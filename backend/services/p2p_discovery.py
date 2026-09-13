"""
backend/services/p2p_discovery.py — Local Peer Discovery (P6.2).

Sovereign, air-gapped peer discovery for the vOS mesh.

Design
------
* Pure-Python UDP — no `zeroconf` / `avahi` dependency. The Tauri
  shell and the air-gap harness BOTH refuse non-loopback egress,
  so a third-party mDNS implementation would have to be loopback-
  safe to be usable. Building our own keeps the threat model honest:
  every byte that leaves this process is documented here.

* Beacons are signed with the same Ed25519 keypair P6.1 mints for
  workflow signing. Reusing the keypair makes the node identity
  cryptographically stable across reboots without provisioning
  another secret.

* Receiver verifies the beacon signature BEFORE writing the peer
  to the table. An unverifiable beacon is recorded as a security
  audit event (`kind="malicious_peer_attempt"`) but never persisted
  as a discovered peer.

* A peer whose `workspace_id` differs from the local node's is
  recorded with status='rejected' AND a high-severity audit row
  (`kind="local_tampering_blocked"`). This is the canonical P6.2
  rogue-node defense path.

Beacon envelope (JSON, UTF-8, ≤512 bytes)
-----------------------------------------
  {
    "v": 1,
    "node_id":      "<uuid4>",
    "workspace_id": "<workspace>",
    "sync_port":    13371,
    "ts_ms":        1715600000000,
    "public_key":   "<64 hex chars>",
    "signature":    "<128 hex chars over canonical fields>",
  }

The signature covers a deterministic JSON over
`{node_id, workspace_id, sync_port, ts_ms, public_key}` — the same
canonical-form contract used by P5.2 manifest signatures.

Air-gap discipline
------------------
By default the discovery sends to the IPv4 broadcast address
`255.255.255.255`. Tests override `peer_targets=[("127.0.0.1", 13370)]`
so the loopback-only kill-switch in the airgap harness never sees a
non-loopback `sendto`. Production users on a real LAN keep the
broadcast default; the kill-switch is not installed there.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_DISCOVERY_PORT = 13370
DEFAULT_SYNC_PORT = 13371
BEACON_VERSION = 1
BEACON_MAX_BYTES = 1024
BROADCAST_INTERVAL_S = 2.0
STALE_THRESHOLD_S = 30.0
RECV_BUFFER_BYTES = 2048
RECV_POLL_TIMEOUT_S = 0.5


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass
class PeerRecord:
    """In-memory view of a discovered peer. Mirrors the DB row but
    avoids forcing every caller through SQLAlchemy."""

    node_id: str
    workspace_id: str
    host: str
    sync_port: int
    public_key_hex: str
    status: str
    verified: bool
    first_seen_ms: int
    last_seen_ms: int

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "workspace_id": self.workspace_id,
            "host": self.host,
            "sync_port": self.sync_port,
            "public_key_hex": self.public_key_hex,
            "status": self.status,
            "verified": self.verified,
            "first_seen_ms": self.first_seen_ms,
            "last_seen_ms": self.last_seen_ms,
        }


# ---------------------------------------------------------------------------
# Beacon canonicalization (matches P5.2's sort_keys=True convention)
# ---------------------------------------------------------------------------


def _beacon_canonical_bytes(
    *,
    node_id: str,
    workspace_id: str,
    sync_port: int,
    ts_ms: int,
    public_key_hex: str,
) -> bytes:
    """Deterministic JSON over the fields the signature covers."""
    payload = {
        "node_id": node_id,
        "workspace_id": workspace_id,
        "sync_port": int(sync_port),
        "ts_ms": int(ts_ms),
        "public_key": public_key_hex,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# LocalPeerDiscovery — service core
# ---------------------------------------------------------------------------


class LocalPeerDiscovery:
    """UDP broadcast discovery + peer-table maintenance.

    Public surface:
      start()            — spin up the broadcaster + receiver threads
      stop()             — graceful shutdown
      list_peers(...)    — filter the in-memory cache
      build_beacon()     — produce a signed beacon envelope (also used by tests)
      ingest_beacon()    — receiver-side parse + verify + persist
                           (callable directly from tests)
    """

    def __init__(
        self,
        *,
        node_id: Optional[str] = None,
        workspace_id: str,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        sync_port: int = DEFAULT_SYNC_PORT,
        bind_host: str = "0.0.0.0",
        peer_targets: Optional[list] = None,
        broadcast_interval_s: float = BROADCAST_INTERVAL_S,
        signer: Optional[Callable[[dict], tuple]] = None,
    ):
        """
        Args:
          node_id              — opaque per-node identifier. Defaults
                                 to a new UUID4 (stable for the life
                                 of the process).
          workspace_id         — REQUIRED. Beacons advertise this; the
                                 local rogue-peer guard rejects beacons
                                 whose `workspace_id` differs.
          discovery_port       — UDP port the receiver binds to.
          sync_port            — TCP port the SovereignSyncEngine
                                 listens on (advertised in the beacon).
          bind_host            — "0.0.0.0" by default. Tests override
                                 to "127.0.0.1".
          peer_targets         — explicit unicast targets to send beacons
                                 to. When None, falls back to broadcast
                                 (255.255.255.255). Tests pass
                                 [("127.0.0.1", port)] so the air-gap
                                 kill-switch never sees non-loopback
                                 traffic.
          signer               — optional dependency injection for
                                 tests. Default uses the P6.1
                                 workflow-signing keypair.
        """
        if not workspace_id:
            raise ValueError("workspace_id is required")
        self.node_id = node_id or str(uuid.uuid4())
        self.workspace_id = workspace_id
        self.discovery_port = int(discovery_port)
        self.sync_port = int(sync_port)
        self.bind_host = bind_host
        self.peer_targets = list(peer_targets) if peer_targets else None
        self.broadcast_interval_s = float(broadcast_interval_s)
        self._signer = signer

        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._peers: dict = {}  # node_id -> PeerRecord
        self._rx_socket: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._beacons_sent = 0
        self._beacons_received = 0
        self._beacons_rejected = 0

    # --- public API --------------------------------------------------

    @property
    def public_key_hex(self) -> str:
        """The Ed25519 public key half of this node's identity."""
        _, pub = self._keypair()
        return pub

    def start(self) -> None:
        """Spin up the receiver + broadcaster threads.

        Idempotent: a second call without an intervening stop() is
        a no-op. Threads are daemon so the host process doesn't hang
        if a caller forgets to stop().
        """
        with self._lock:
            if self._rx_thread is not None:
                return
            self._stop.clear()
            self._bind_receiver()
            self._rx_thread = threading.Thread(
                target=self._receiver_loop,
                name=f"vos-discovery-rx-{self.node_id[:8]}",
                daemon=True,
            )
            self._tx_thread = threading.Thread(
                target=self._broadcaster_loop,
                name=f"vos-discovery-tx-{self.node_id[:8]}",
                daemon=True,
            )
            self._rx_thread.start()
            self._tx_thread.start()
            logger.info(
                "[discovery] node=%s ws=%s started on udp=%d sync=%d",
                self.node_id[:12],
                self.workspace_id,
                self.discovery_port,
                self.sync_port,
            )

    def stop(self) -> None:
        """Tear down threads + close the socket. Safe to call twice."""
        self._stop.set()
        sock = self._rx_socket
        self._rx_socket = None
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
        for t in (self._rx_thread, self._tx_thread):
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
        self._rx_thread = None
        self._tx_thread = None

    def list_peers(
        self,
        *,
        verified_only: bool = True,
        active_only: bool = True,
    ) -> list:
        """Snapshot the in-memory peer cache.

        Defaults filter to the SAFE set (verified + active) — the
        rogue-rejected and stale rows are visible only when the caller
        explicitly asks for them (e.g. the security dashboard)."""
        with self._lock:
            out = []
            for p in self._peers.values():
                if verified_only and not p.verified:
                    continue
                if active_only and p.status != "active":
                    continue
                out.append(p)
            return sorted(out, key=lambda r: r.last_seen_ms, reverse=True)

    def stats(self) -> dict:
        """Cheap counters for the dashboard / tests."""
        with self._lock:
            return {
                "node_id": self.node_id,
                "workspace_id": self.workspace_id,
                "beacons_sent": self._beacons_sent,
                "beacons_received": self._beacons_received,
                "beacons_rejected": self._beacons_rejected,
                "peer_count": sum(
                    1
                    for p in self._peers.values()
                    if p.status == "active" and p.verified
                ),
            }

    # --- beacon construction -----------------------------------------

    def build_beacon(self) -> bytes:
        """Mint a fresh signed beacon envelope.

        Returns bytes ready for `socket.sendto`. Exposed publicly so
        tests can inject a beacon into a peer's `ingest_beacon` without
        spinning up the broadcaster thread.
        """
        priv_hex, pub_hex = self._keypair()
        ts_ms = _now_ms()
        canon = _beacon_canonical_bytes(
            node_id=self.node_id,
            workspace_id=self.workspace_id,
            sync_port=self.sync_port,
            ts_ms=ts_ms,
            public_key_hex=pub_hex,
        )
        try:
            from services.app_crypto import sign_manifest

            sig_hex = sign_manifest(
                {
                    "node_id": self.node_id,
                    "workspace_id": self.workspace_id,
                    "sync_port": self.sync_port,
                    "ts_ms": ts_ms,
                    "public_key": pub_hex,
                },
                private_key_hex=priv_hex,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[discovery] sign failed (%s) — emitting unsigned beacon", exc
            )
            sig_hex = ""

        envelope = {
            "v": BEACON_VERSION,
            "node_id": self.node_id,
            "workspace_id": self.workspace_id,
            "sync_port": self.sync_port,
            "ts_ms": ts_ms,
            "public_key": pub_hex,
            "signature": sig_hex,
        }
        # Sanity check — drop on the floor if we ever emit a beacon
        # that wouldn't survive a roundtrip.
        data = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        if len(data) > BEACON_MAX_BYTES:
            raise RuntimeError(
                f"beacon exceeds {BEACON_MAX_BYTES} bytes: {len(data)} — "
                f"workspace_id likely too long"
            )
        del canon  # the canonical form was only needed for the sign call
        return data

    # --- beacon ingestion --------------------------------------------

    def ingest_beacon(
        self,
        raw: bytes,
        *,
        source_host: str,
    ) -> Optional[PeerRecord]:
        """Parse + verify + persist a received beacon.

        Returns the PeerRecord on success, None on rejection. All
        rejection paths land in the security audit log so the
        dashboard surfaces the attempt.
        """
        with self._lock:
            self._beacons_received += 1

        if len(raw) > BEACON_MAX_BYTES:
            self._reject(
                source_host,
                "beacon_oversized",
                {"size": len(raw)},
            )
            return None

        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._reject(
                source_host,
                "beacon_malformed",
                {"reason": str(exc)[:120]},
            )
            return None

        if not isinstance(envelope, dict):
            self._reject(source_host, "beacon_malformed", {})
            return None

        if envelope.get("v") != BEACON_VERSION:
            self._reject(
                source_host,
                "beacon_version",
                {"received_v": envelope.get("v")},
            )
            return None

        node_id = envelope.get("node_id")
        ws_id = envelope.get("workspace_id")
        sync_port = envelope.get("sync_port")
        ts_ms = envelope.get("ts_ms")
        pub_hex = envelope.get("public_key")
        sig_hex = envelope.get("signature")

        if not (
            isinstance(node_id, str)
            and isinstance(ws_id, str)
            and isinstance(sync_port, int)
            and isinstance(ts_ms, int)
            and isinstance(pub_hex, str)
            and isinstance(sig_hex, str)
        ):
            self._reject(source_host, "beacon_malformed_fields", {})
            return None

        # Ignore our own beacon — we should never persist ourselves.
        if node_id == self.node_id:
            return None

        # --- signature verification --------------------------------
        verified = self._verify_beacon(
            node_id=node_id,
            workspace_id=ws_id,
            sync_port=sync_port,
            ts_ms=ts_ms,
            public_key_hex=pub_hex,
            signature_hex=sig_hex,
        )
        if not verified:
            self._reject(
                source_host,
                "malicious_peer_attempt",
                {
                    "node_id": node_id,
                    "workspace_id": ws_id,
                    "reason": "signature_invalid",
                },
                kind="malicious_peer_attempt",
            )
            return None

        # --- workspace mismatch — the canonical rogue-rejection path
        if ws_id != self.workspace_id:
            self._reject(
                source_host,
                "workspace_mismatch",
                {
                    "node_id": node_id,
                    "peer_workspace": ws_id,
                    "local_workspace": self.workspace_id,
                },
                kind="local_tampering_blocked",
            )
            # Still persist as rejected so the dashboard can render
            # the malicious-attempt history.
            return self._upsert_peer(
                node_id=node_id,
                workspace_id=ws_id,
                host=source_host,
                sync_port=sync_port,
                public_key_hex=pub_hex,
                status="rejected",
                verified=True,
            )

        return self._upsert_peer(
            node_id=node_id,
            workspace_id=ws_id,
            host=source_host,
            sync_port=sync_port,
            public_key_hex=pub_hex,
            status="active",
            verified=True,
        )

    # --- helpers -----------------------------------------------------

    def _keypair(self) -> tuple:
        if self._signer is not None:
            return self._signer({})
        from services.crypto_keyring import get_or_mint_workflow_signing_key

        return get_or_mint_workflow_signing_key()

    @staticmethod
    def _verify_beacon(
        *,
        node_id: str,
        workspace_id: str,
        sync_port: int,
        ts_ms: int,
        public_key_hex: str,
        signature_hex: str,
    ) -> bool:
        if not signature_hex or not public_key_hex:
            return False
        try:
            from services.app_crypto import verify_manifest_signature

            canon = _beacon_canonical_bytes(
                node_id=node_id,
                workspace_id=workspace_id,
                sync_port=sync_port,
                ts_ms=ts_ms,
                public_key_hex=public_key_hex,
            )
            return verify_manifest_signature(
                canon,
                signature_hex,
                public_key_hex,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[discovery] verify error: %s", exc)
            return False

    def _upsert_peer(
        self,
        *,
        node_id: str,
        workspace_id: str,
        host: str,
        sync_port: int,
        public_key_hex: str,
        status: str,
        verified: bool,
    ) -> PeerRecord:
        now = _now_ms()
        with self._lock:
            existing = self._peers.get(node_id)
            if existing is None:
                rec = PeerRecord(
                    node_id=node_id,
                    workspace_id=workspace_id,
                    host=host,
                    sync_port=sync_port,
                    public_key_hex=public_key_hex,
                    status=status,
                    verified=verified,
                    first_seen_ms=now,
                    last_seen_ms=now,
                )
                self._peers[node_id] = rec
            else:
                existing.workspace_id = workspace_id
                existing.host = host
                existing.sync_port = sync_port
                existing.public_key_hex = public_key_hex
                existing.status = status
                existing.verified = verified
                existing.last_seen_ms = now
                rec = existing
        self._persist_peer(rec)
        return rec

    def _persist_peer(self, rec: PeerRecord) -> None:
        """Mirror the in-memory record into `discoveredPeers`. Best
        effort — a DB hiccup doesn't tear down the in-memory cache."""
        try:
            from core.database.sqlite_setup import (
                DiscoveredPeer,
                get_session,
                init_db,
            )

            init_db()
            with get_session() as session:
                row = (
                    session.query(DiscoveredPeer)
                    .filter_by(
                        nodeId=rec.node_id,
                    )
                    .one_or_none()
                )
                if row is None:
                    session.add(
                        DiscoveredPeer(
                            id=str(uuid.uuid4()),
                            nodeId=rec.node_id,
                            workspaceId=rec.workspace_id,
                            host=rec.host,
                            syncPort=rec.sync_port,
                            publicKeyHex=rec.public_key_hex,
                            status=rec.status,
                            verified=rec.verified,
                            firstSeenAt=rec.first_seen_ms,
                            lastSeenAt=rec.last_seen_ms,
                        )
                    )
                else:
                    row.workspaceId = rec.workspace_id
                    row.host = rec.host
                    row.syncPort = rec.sync_port
                    row.publicKeyHex = rec.public_key_hex
                    row.status = rec.status
                    row.verified = rec.verified
                    row.lastSeenAt = rec.last_seen_ms
                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[discovery] DB persist failed (%s)", exc)

    def _reject(
        self,
        source_host: str,
        reason: str,
        details: dict,
        *,
        kind: str = "malicious_peer_attempt",
    ) -> None:
        """Record a rejected beacon to securityAuditLog + bump counter."""
        with self._lock:
            self._beacons_rejected += 1
        payload = {"source_host": source_host, "reason": reason, **details}
        try:
            from services.app_sandbox import _record_security_event

            _record_security_event(
                kind=kind,
                reason=f"discovery rejected beacon: {reason}",
                details=payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[discovery] audit failed: %s", exc)
        logger.warning(
            "[discovery] REJECTED beacon from %s: %s details=%s",
            source_host,
            reason,
            payload,
        )

    # --- socket I/O --------------------------------------------------

    def _bind_receiver(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass  # SO_REUSEPORT not on all platforms
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((self.bind_host, self.discovery_port))
        sock.settimeout(RECV_POLL_TIMEOUT_S)
        self._rx_socket = sock

    def _receiver_loop(self) -> None:
        sock = self._rx_socket
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(RECV_BUFFER_BYTES)
            except socket.timeout:
                continue
            except OSError:  # socket closed by stop()
                break
            source_host = addr[0] if isinstance(addr, tuple) else "?"
            try:
                self.ingest_beacon(data, source_host=source_host)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[discovery] ingest exception: %s", exc)

    def _broadcaster_loop(self) -> None:
        # Reuse a tx socket so we don't allocate per beacon. SO_BROADCAST
        # is set so 255.255.255.255 works when no explicit peer_targets
        # are configured.
        try:
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError as exc:
            logger.error("[discovery] tx socket setup failed: %s", exc)
            return
        try:
            while not self._stop.is_set():
                try:
                    self.send_one_beacon(tx)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[discovery] send_one_beacon: %s", exc)
                self._stop.wait(self.broadcast_interval_s)
        finally:
            try:
                tx.close()
            except OSError:
                pass

    def send_one_beacon(self, tx: Optional[socket.socket] = None) -> int:
        """Send one beacon to each configured target.

        Returns the number of `sendto` calls that succeeded. Public
        so tests can drive a single broadcast tick without owning
        the broadcaster thread.
        """
        owns_tx = tx is None
        if owns_tx:
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sent = 0
        try:
            payload = self.build_beacon()
            targets = self.peer_targets or [
                ("255.255.255.255", self.discovery_port),
            ]
            for host, port in targets:
                try:
                    tx.sendto(payload, (host, int(port)))
                    sent += 1
                except OSError as exc:
                    logger.debug(
                        "[discovery] sendto %s:%s failed: %s",
                        host,
                        port,
                        exc,
                    )
        finally:
            if owns_tx:
                try:
                    tx.close()
                except OSError:
                    pass
        with self._lock:
            self._beacons_sent += sent
        return sent


# ---------------------------------------------------------------------------
# Stale eviction — callable from anywhere, e.g. an APScheduler task.
# ---------------------------------------------------------------------------


def mark_stale_peers(threshold_s: float = STALE_THRESHOLD_S) -> int:
    """Flip every active peer not seen in `threshold_s` to status='stale'.

    Operates against the DB directly (no LocalPeerDiscovery instance
    required) so cron-style cleanup can run even when no discovery
    service is bound.
    """
    cutoff = _now_ms() - int(threshold_s * 1000)
    n = 0
    try:
        from core.database.sqlite_setup import (
            DiscoveredPeer,
            get_session,
            init_db,
        )

        init_db()
        with get_session() as session:
            rows = (
                session.query(DiscoveredPeer)
                .filter(
                    DiscoveredPeer.status == "active",
                    DiscoveredPeer.lastSeenAt < cutoff,
                )
                .all()
            )
            for row in rows:
                row.status = "stale"
                n += 1
            if n:
                session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[discovery] stale eviction failed: %s", exc)
    return n


__all__ = [
    "BEACON_MAX_BYTES",
    "BEACON_VERSION",
    "DEFAULT_DISCOVERY_PORT",
    "DEFAULT_SYNC_PORT",
    "LocalPeerDiscovery",
    "PeerRecord",
    "mark_stale_peers",
]
