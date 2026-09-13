"""
backend/services/p2p_sync.py — Sovereign Sync Engine (P6.2).

Air-gapped peer-to-peer state synchronization for the vOS mesh.

Trust model
-----------
                ┌────────────────────────────────────────┐
                │ LocalPeerDiscovery already verified    │
                │ the peer's Ed25519 beacon AND its      │
                │ workspace_id matches ours.             │
                └───────────────┬────────────────────────┘
                                │
                                ▼
            ┌─────────────────────────────────────────────┐
            │ establish_session(peer)                     │
            │  1. TCP connect → loopback / LAN address    │
            │  2. ECDH (X25519) ephemeral key exchange    │
            │  3. Mutual `node_id` + `workspace_id` swap  │
            │     (both signed with the SAME P6.1 key     │
            │     used in the beacon, so impersonation    │
            │     attempts at this stage are caught too)  │
            │  4. Workspace mismatch → tear down +        │
            │     local_tampering_blocked audit row.      │
            │  5. Derive symmetric Fernet key from the    │
            │     ECDH shared secret via HKDF-SHA256.     │
            └───────────────┬─────────────────────────────┘
                            │
                            ▼
            ┌─────────────────────────────────────────────┐
            │ SyncSession                                 │
            │  send_encrypted(msg)  / recv_encrypted()    │
            │  state_compare()      / state_delta()       │
            │  close()                                    │
            └─────────────────────────────────────────────┘

What this module does NOT do
----------------------------
* It does not replace the P3.3/P3.4 cloud SyncEngine — that's the
  Convex-bridge path. This module is the airgap-LAN sibling.
* It does not yet wire up real-time gossip. P6.2 ships request/
  response semantics; a future P6.x can add a long-lived pub-sub
  channel over the same encrypted transport.

Protocol envelope (frame on the wire)
-------------------------------------
Every frame is a length-prefixed UTF-8 JSON line:
    [4 bytes BE int — length N][N bytes UTF-8 JSON]

The plaintext handshake messages are sent before key derivation:
    {"op": "hello",
     "node_id": ..., "workspace_id": ...,
     "ephemeral_pk": <X25519 pub hex>,
     "identity_pk":  <Ed25519 pub hex>,
     "signature":    <Ed25519 sig over ephemeral_pk||workspace_id>}

After mutual hello + verification, the post-handshake frames are
Fernet-encrypted JSON payloads.
"""

from __future__ import annotations

import json
import logging
import socket
import socketserver
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path  # noqa: F401 — used as forward-ref type annotation
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wire-protocol constants
# ---------------------------------------------------------------------------


FRAME_LENGTH_PREFIX_BYTES = 4
FRAME_MAX_BYTES = 64 * 1024  # 64 KiB plaintext payload ceiling
HANDSHAKE_TIMEOUT_S = 5.0
SOCKET_RECV_TIMEOUT_S = 10.0


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HandshakeFailed(RuntimeError):
    """Raised when the remote peer fails to complete a clean handshake.

    The caller catches this and may or may not record an audit row;
    `establish_session` already records `malicious_peer_attempt` /
    `local_tampering_blocked` on the workspace/signature mismatch
    paths."""


class SessionClosed(RuntimeError):
    """Raised when send/recv is called on a torn-down session."""


class BlobNotFound(RuntimeError):
    """P7.2 — Peer doesn't hold the requested SHA-256 blob.

    Surfaces a normal "not found" condition (peer is honest, just
    doesn't have the file). Caller should try the next peer or fall
    back to HTTPS."""


# ---------------------------------------------------------------------------
# Frame helpers — length-prefixed JSON over a connected TCP socket
# ---------------------------------------------------------------------------


def _send_frame(sock: socket.socket, payload: dict) -> None:
    """Length-prefix + send `payload` as a single UTF-8 JSON frame."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > FRAME_MAX_BYTES:
        raise ValueError(f"frame body {len(body)} exceeds {FRAME_MAX_BYTES}")
    sock.sendall(struct.pack(">I", len(body)) + body)


def _recv_n(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise HandshakeFailed("peer closed during recv")
        buf += chunk
    return buf


def _recv_frame(sock: socket.socket) -> dict:
    """Block until the next length-prefixed JSON frame is fully read."""
    hdr = _recv_n(sock, FRAME_LENGTH_PREFIX_BYTES)
    (length,) = struct.unpack(">I", hdr)
    if length <= 0 or length > FRAME_MAX_BYTES:
        raise HandshakeFailed(f"invalid frame length {length}")
    body = _recv_n(sock, length)
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandshakeFailed(f"frame decode failed: {exc}") from exc
    if not isinstance(envelope, dict):
        raise HandshakeFailed("frame body not a JSON object")
    return envelope


# ---------------------------------------------------------------------------
# Diffie-Hellman / ECDH (X25519) helpers
# ---------------------------------------------------------------------------


def _generate_ephemeral_x25519() -> tuple:
    """Return `(priv_obj, pub_hex)` for a fresh X25519 keypair."""
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives import serialization

    priv = x25519.X25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, pub_raw.hex()


def _derive_shared_secret(priv_obj, peer_pub_hex: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import x25519

    peer_pub = x25519.X25519PublicKey.from_public_bytes(
        bytes.fromhex(peer_pub_hex),
    )
    return priv_obj.exchange(peer_pub)


def _derive_fernet_key(shared_secret: bytes, *, transcript: bytes) -> bytes:
    """HKDF-SHA256 → 32 raw bytes → urlsafe base64 → Fernet-ready key.

    The transcript (concatenation of both ephemeral public keys, in
    canonical lexicographic order) is mixed in so a MITM that reuses
    half of a previous handshake's ephemeral can't replay derived
    keys."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"vOS3.P6.2.p2p_sync.v1",
        info=b"vOS3-p2p-symmetric-key|" + transcript,
    ).derive(shared_secret)
    return base64.urlsafe_b64encode(raw)


# ---------------------------------------------------------------------------
# SyncSession — wraps a connected TCP socket with the post-handshake key.
# ---------------------------------------------------------------------------


@dataclass
class SessionMetadata:
    local_node_id: str
    local_workspace_id: str
    peer_node_id: str
    peer_workspace_id: str
    peer_identity_pk: str
    started_at_ms: int


class SyncSession:
    """Encrypted message channel over a connected TCP socket.

    Public surface:
      send_encrypted(payload: dict)
      recv_encrypted()           -> dict
      compare_state(table, ...)  -> dict
      request_delta(table, ...)  -> list
      close()
    """

    def __init__(
        self,
        *,
        sock: socket.socket,
        fernet_key: bytes,
        metadata: SessionMetadata,
    ):
        from cryptography.fernet import Fernet

        self._sock = sock
        self._fernet = Fernet(fernet_key)
        self.metadata = metadata
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def send_encrypted(self, payload: dict) -> None:
        if self._closed:
            raise SessionClosed("send on closed session")
        plain = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        token = self._fernet.encrypt(plain)
        _send_frame(self._sock, {"enc": token.decode("utf-8")})

    def recv_encrypted(self, *, timeout_s: float = SOCKET_RECV_TIMEOUT_S) -> dict:
        if self._closed:
            raise SessionClosed("recv on closed session")
        old_to = self._sock.gettimeout()
        try:
            self._sock.settimeout(timeout_s)
            envelope = _recv_frame(self._sock)
        finally:
            try:
                self._sock.settimeout(old_to)
            except Exception:  # noqa: BLE001
                pass
        token = envelope.get("enc")
        if not isinstance(token, str):
            raise HandshakeFailed("missing enc field on post-handshake frame")
        plain = self._fernet.decrypt(token.encode("utf-8"))
        result = json.loads(plain.decode("utf-8"))
        if not isinstance(result, dict):
            raise HandshakeFailed("decrypted payload not a JSON object")
        return result

    # --- high-level RPC verbs ----------------------------------------

    def compare_state(self, table: str, fields: Optional[list] = None) -> dict:
        self.send_encrypted(
            {
                "op": "state.compare",
                "table": table,
                "fields": fields or [],
                "request_id": str(uuid.uuid4()),
            }
        )
        return self.recv_encrypted()

    def request_delta(self, table: str, since_ms: int = 0) -> dict:
        self.send_encrypted(
            {
                "op": "state.delta",
                "table": table,
                "since_ms": int(since_ms),
                "request_id": str(uuid.uuid4()),
            }
        )
        return self.recv_encrypted()

    # P7.2 — blob fetch for model GGUF distribution over the mesh ----

    def request_blob(self, sha256: str, *, chunk_timeout_s: float = 30.0) -> bytes:
        """Pull a binary blob (e.g. a GGUF) from the peer by SHA-256.

        The peer responds either with a `blob.notfound` frame (raises
        BlobNotFound here) or with one or more `blob.chunk` frames
        terminated by `blob.eof`. All frames carry the request_id so
        out-of-order responses from a future async handler still match.

        Returns the assembled bytes. The caller is expected to verify
        the SHA-256 a second time before persisting — defense in depth
        against a malicious peer that lies about its own digest.

        Wire shape (all encrypted within Fernet frames):
            req:  {op:"blob.request", sha256:<hex>, request_id:<uuid>}
            ack:  {op:"blob.found", request_id, size_bytes, chunks}
            ...   N chunks: {op:"blob.chunk", request_id, idx, data_b64}
            end:  {op:"blob.eof", request_id, sha256}
                OR
            err:  {op:"blob.notfound", request_id}
        """
        request_id = str(uuid.uuid4())
        self.send_encrypted(
            {
                "op": "blob.request",
                "sha256": sha256,
                "request_id": request_id,
            }
        )

        # 1. Read the header frame.
        header = self.recv_encrypted(timeout_s=chunk_timeout_s)
        if header.get("op") == "blob.notfound":
            raise BlobNotFound(f"peer does not hold blob sha256={sha256[:16]}…")
        if header.get("op") != "blob.found":
            raise HandshakeFailed(f"unexpected blob response op={header.get('op')!r}")
        expected_chunks = int(header.get("chunks") or 0)
        if expected_chunks <= 0:
            raise HandshakeFailed("peer reported zero-chunk blob")

        # 2. Drain chunks in order.
        import base64

        buf = bytearray()
        seen_indices = set()
        for _ in range(expected_chunks):
            frame = self.recv_encrypted(timeout_s=chunk_timeout_s)
            op = frame.get("op")
            if op != "blob.chunk":
                raise HandshakeFailed(f"unexpected chunk op={op!r}")
            idx = frame.get("idx")
            data_b64 = frame.get("data_b64")
            if not isinstance(idx, int) or not isinstance(data_b64, str):
                raise HandshakeFailed("malformed blob.chunk frame")
            if idx in seen_indices:
                raise HandshakeFailed(f"duplicate chunk idx={idx}")
            if idx != len(seen_indices):
                raise HandshakeFailed(
                    f"out-of-order chunk idx={idx} (expected " f"{len(seen_indices)})"
                )
            seen_indices.add(idx)
            try:
                buf.extend(base64.b64decode(data_b64, validate=True))
            except Exception as exc:
                raise HandshakeFailed(f"chunk b64 decode failed: {exc}") from exc

        # 3. Final eof frame.
        eof = self.recv_encrypted(timeout_s=chunk_timeout_s)
        if eof.get("op") != "blob.eof":
            raise HandshakeFailed(f"expected blob.eof, got op={eof.get('op')!r}")

        return bytes(buf)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# SovereignSyncEngine — public facade
# ---------------------------------------------------------------------------


class SovereignSyncEngine:
    """Public entry point for the P2P sync layer.

    Stateless aside from the local node-id + workspace cache. Each
    `establish_session` call spawns a fresh handshake; there is no
    global connection pool yet (sessions are short-lived in P6.2).
    """

    def __init__(
        self,
        *,
        node_id: str,
        workspace_id: str,
        identity_signer: Optional[Callable[[bytes], tuple]] = None,
    ):
        if not workspace_id:
            raise ValueError("workspace_id required")
        self.node_id = node_id
        self.workspace_id = workspace_id
        self._signer = identity_signer

    # --- outbound: connect + handshake -------------------------------

    def establish_session(
        self,
        peer_host: str,
        peer_port: int,
        *,
        expected_workspace_id: Optional[str] = None,
        expected_peer_pk: Optional[str] = None,
    ) -> SyncSession:
        """Connect to a peer, run the handshake, return a SyncSession.

        Raises:
          HandshakeFailed — on transport error, signature mismatch,
                            workspace mismatch, or protocol violation.
                            Workspace mismatch ALSO emits
                            `local_tampering_blocked` audit; signature
                            mismatch emits `malicious_peer_attempt`.
        """
        sock = socket.create_connection(
            (peer_host, int(peer_port)),
            timeout=HANDSHAKE_TIMEOUT_S,
        )
        try:
            return self._client_handshake(
                sock,
                expected_workspace_id=expected_workspace_id or self.workspace_id,
                expected_peer_pk=expected_peer_pk,
            )
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise

    # --- inbound: server-side handshake helper -----------------------

    def serve_handshake(self, sock: socket.socket) -> SyncSession:
        """Drive the handshake from the server side on an already-accepted
        socket. Used by the inline TCP server in tests + by Tauri's
        sidecar in production."""
        return self._server_handshake(sock)

    # --- shared internals --------------------------------------------

    def _identity_keypair(self) -> tuple:
        """Return `(priv_hex, pub_hex)` for the Ed25519 identity key."""
        if self._signer is not None:
            return self._signer(b"")
        from services.crypto_keyring import get_or_mint_workflow_signing_key

        return get_or_mint_workflow_signing_key()

    def _sign_handshake(self, ephemeral_pk_hex: str) -> str:
        """Bind the ephemeral key to our identity by signing it +
        the workspace_id. A MITM that swaps the ephemeral key will
        fail this check."""
        priv_hex, _ = self._identity_keypair()
        from services.app_crypto import sign_manifest

        return sign_manifest(
            {
                "ephemeral_pk": ephemeral_pk_hex,
                "node_id": self.node_id,
                "workspace_id": self.workspace_id,
            },
            private_key_hex=priv_hex,
        )

    @staticmethod
    def _verify_handshake(
        *,
        ephemeral_pk_hex: str,
        node_id: str,
        workspace_id: str,
        signature_hex: str,
        identity_pk_hex: str,
    ) -> bool:
        from services.app_crypto import verify_manifest_signature
        from services.app_crypto import canonical_manifest_bytes

        canon = canonical_manifest_bytes(
            {
                "ephemeral_pk": ephemeral_pk_hex,
                "node_id": node_id,
                "workspace_id": workspace_id,
            }
        )
        return verify_manifest_signature(canon, signature_hex, identity_pk_hex)

    def _client_handshake(
        self,
        sock: socket.socket,
        *,
        expected_workspace_id: str,
        expected_peer_pk: Optional[str],
    ) -> SyncSession:
        priv_eph, pub_eph_hex = _generate_ephemeral_x25519()
        _, identity_pub = self._identity_keypair()
        signature = self._sign_handshake(pub_eph_hex)
        _send_frame(
            sock,
            {
                "op": "hello",
                "node_id": self.node_id,
                "workspace_id": self.workspace_id,
                "ephemeral_pk": pub_eph_hex,
                "identity_pk": identity_pub,
                "signature": signature,
            },
        )
        try:
            peer_hello = _recv_frame(sock)
        except HandshakeFailed:
            raise
        return self._complete_handshake(
            sock,
            local_priv_eph=priv_eph,
            local_pub_eph_hex=pub_eph_hex,
            peer_hello=peer_hello,
            expected_workspace_id=expected_workspace_id,
            expected_peer_pk=expected_peer_pk,
            initiator=True,
        )

    def _server_handshake(self, sock: socket.socket) -> SyncSession:
        try:
            sock.settimeout(HANDSHAKE_TIMEOUT_S)
            client_hello = _recv_frame(sock)
        except HandshakeFailed:
            raise
        priv_eph, pub_eph_hex = _generate_ephemeral_x25519()
        _, identity_pub = self._identity_keypair()
        signature = self._sign_handshake(pub_eph_hex)
        _send_frame(
            sock,
            {
                "op": "hello",
                "node_id": self.node_id,
                "workspace_id": self.workspace_id,
                "ephemeral_pk": pub_eph_hex,
                "identity_pk": identity_pub,
                "signature": signature,
            },
        )
        return self._complete_handshake(
            sock,
            local_priv_eph=priv_eph,
            local_pub_eph_hex=pub_eph_hex,
            peer_hello=client_hello,
            expected_workspace_id=self.workspace_id,
            expected_peer_pk=None,
            initiator=False,
        )

    def _complete_handshake(
        self,
        sock: socket.socket,
        *,
        local_priv_eph,
        local_pub_eph_hex: str,
        peer_hello: dict,
        expected_workspace_id: str,
        expected_peer_pk: Optional[str],
        initiator: bool,
    ) -> SyncSession:
        op = peer_hello.get("op")
        if op != "hello":
            raise HandshakeFailed(f"unexpected peer op {op!r}")

        peer_node_id = peer_hello.get("node_id")
        peer_workspace = peer_hello.get("workspace_id")
        peer_eph_hex = peer_hello.get("ephemeral_pk")
        peer_identity_pk = peer_hello.get("identity_pk")
        peer_signature = peer_hello.get("signature")

        if not all(
            isinstance(x, str) and x
            for x in (
                peer_node_id,
                peer_workspace,
                peer_eph_hex,
                peer_identity_pk,
                peer_signature,
            )
        ):
            raise HandshakeFailed("peer hello missing required fields")

        # ---- pinned-identity check (optional) ----------------------
        if expected_peer_pk is not None and expected_peer_pk != peer_identity_pk:
            self._audit_blocked(
                kind="malicious_peer_attempt",
                reason="identity_pk_pinning_mismatch",
                details={
                    "peer_node_id": peer_node_id,
                    "expected_identity_pk": expected_peer_pk,
                    "received_identity_pk": peer_identity_pk,
                },
            )
            raise HandshakeFailed("peer identity key did not match pin")

        # ---- signature check ---------------------------------------
        if not self._verify_handshake(
            ephemeral_pk_hex=peer_eph_hex,
            node_id=peer_node_id,
            workspace_id=peer_workspace,
            signature_hex=peer_signature,
            identity_pk_hex=peer_identity_pk,
        ):
            self._audit_blocked(
                kind="malicious_peer_attempt",
                reason="handshake_signature_invalid",
                details={
                    "peer_node_id": peer_node_id,
                    "peer_workspace": peer_workspace,
                },
            )
            raise HandshakeFailed("peer handshake signature did not verify")

        # ---- workspace_id mismatch — canonical rogue-rejection -----
        if peer_workspace != expected_workspace_id:
            self._audit_blocked(
                kind="local_tampering_blocked",
                reason="workspace_id_mismatch",
                details={
                    "peer_node_id": peer_node_id,
                    "peer_workspace": peer_workspace,
                    "local_workspace": expected_workspace_id,
                    "initiator": initiator,
                },
            )
            raise HandshakeFailed(
                "peer workspace_id %s does not match local %s"
                % (peer_workspace, expected_workspace_id)
            )

        # ---- ECDH + HKDF -------------------------------------------
        try:
            shared = _derive_shared_secret(local_priv_eph, peer_eph_hex)
        except Exception as exc:  # noqa: BLE001
            raise HandshakeFailed(f"ECDH failure: {exc}") from exc

        # Transcript binds the symmetric key to BOTH ephemeral pubs in
        # a canonical (sorted) order — neither side can lie about
        # which key they sent.
        a, b = sorted([local_pub_eph_hex, peer_eph_hex])
        transcript = (a + "|" + b).encode("utf-8")
        fernet_key = _derive_fernet_key(shared, transcript=transcript)

        return SyncSession(
            sock=sock,
            fernet_key=fernet_key,
            metadata=SessionMetadata(
                local_node_id=self.node_id,
                local_workspace_id=self.workspace_id,
                peer_node_id=peer_node_id,
                peer_workspace_id=peer_workspace,
                peer_identity_pk=peer_identity_pk,
                started_at_ms=_now_ms(),
            ),
        )

    # --- audit helper -----------------------------------------------

    @staticmethod
    def _audit_blocked(*, kind: str, reason: str, details: dict) -> None:
        try:
            from services.app_sandbox import _record_security_event

            _record_security_event(
                kind=kind,
                reason=f"p2p sync rejected: {reason}",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[p2p_sync] audit failed: %s", exc)
        logger.warning(
            "[p2p_sync] BLOCKED handshake reason=%s details=%s",
            reason,
            details,
        )


# ---------------------------------------------------------------------------
# Threaded TCP server — drives _server_handshake on every connection
# ---------------------------------------------------------------------------


class _SyncRequestHandler(socketserver.BaseRequestHandler):
    """Per-connection handler — invokes the server-side handshake then
    dispatches the resulting `SyncSession` to a user-provided callback.

    The callback contract: `callback(session)` is called once with the
    open SyncSession; the handler closes the socket when callback
    returns or raises.
    """

    server: "SovereignSyncServer"  # filled in by ThreadingTCPServer

    def handle(self) -> None:
        sock = self.request
        sock.settimeout(SOCKET_RECV_TIMEOUT_S)
        try:
            session = self.server.engine.serve_handshake(sock)
        except HandshakeFailed as exc:
            logger.warning(
                "[p2p_sync] handshake from %s failed: %s",
                self.client_address,
                exc,
            )
            return
        try:
            self.server.handle_session(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[p2p_sync] session handler raised: %s", exc)
        finally:
            session.close()


class SovereignSyncServer(socketserver.ThreadingTCPServer):
    """A minimal threaded TCP server that runs the P6.2 sync handshake."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        engine: SovereignSyncEngine,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        session_callback: Optional[Callable[[SyncSession], None]] = None,
    ):
        super().__init__((host, int(port)), _SyncRequestHandler)
        self.engine = engine
        self.session_callback = session_callback or default_session_handler

    def handle_session(self, session: SyncSession) -> None:
        if self.session_callback is not None:
            self.session_callback(session)

    def start_in_thread(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.serve_forever,
            name="vos-p2p-sync-server",
            daemon=True,
        )
        thread.start()
        return thread

    @property
    def port(self) -> int:
        return self.server_address[1]


# ---------------------------------------------------------------------------
# Default session handler — implements compare + delta over LOCAL state
# ---------------------------------------------------------------------------


def default_session_handler(session: SyncSession) -> None:
    """Server-side responder for the compare/delta verbs.

    Reads requests off the session and replies with locally-computed
    state. Workspace tenancy is already verified by the handshake —
    a peer reaching this function is, by construction, in the SAME
    workspace, so the responder freely returns rows.
    """
    try:
        while not session.closed:
            try:
                request = session.recv_encrypted()
            except (SessionClosed, OSError):
                break
            op = request.get("op")
            if op == "state.compare":
                table = request.get("table") or ""
                fields = request.get("fields") or []
                session.send_encrypted(
                    {
                        "op": "state.compare.ack",
                        "request_id": request.get("request_id"),
                        "table": table,
                        "summary": compute_table_summary(
                            table,
                            workspace_id=session.metadata.local_workspace_id,
                            fields=fields,
                        ),
                    }
                )
            elif op == "state.delta":
                table = request.get("table") or ""
                since_ms = int(request.get("since_ms") or 0)
                session.send_encrypted(
                    {
                        "op": "state.delta.ack",
                        "request_id": request.get("request_id"),
                        "table": table,
                        "rows": compute_table_delta(
                            table,
                            workspace_id=session.metadata.local_workspace_id,
                            since_ms=since_ms,
                        ),
                    }
                )
            elif op == "blob.request":
                # P7.2 — model GGUF distribution. Lookup is constrained
                # to entries in the curated catalog whose SHA-256 pin
                # matches the requested hex AND whose file is locally
                # present. Any miss returns blob.notfound with no
                # filesystem leak.
                _serve_blob_request(session, request)
            elif op == "session.close":
                break
            else:
                session.send_encrypted(
                    {
                        "op": "error",
                        "request_id": request.get("request_id"),
                        "reason": f"unknown_op:{op!r}",
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p_sync] default handler error: %s", exc)


# ---------------------------------------------------------------------------
# Local-state helpers — used by both the inline TCP server AND the HTTP routes
# ---------------------------------------------------------------------------


def _table_model(table_name: str):
    """Return the SQLAlchemy model class for `table_name`, or None
    when the table is not exposed to P2P sync."""
    from core.database.sqlite_setup import (
        Project,
        ChatSession,
        ChatSessionMessage,
        AppState,
        App,
    )

    mapping = {
        "projects": Project,
        "chatSessions": ChatSession,
        "chatSessionMessages": ChatSessionMessage,
        "appState": AppState,
        "apps": App,
    }
    return mapping.get(table_name)


def _row_workspace(row, model) -> Optional[str]:
    """Best-effort workspace extraction. P3.3 tables don't all have
    a workspace column yet — for those we report the global pool."""
    for attr in ("workspaceId", "organizationId"):
        val = getattr(row, attr, None)
        if isinstance(val, str) and val:
            return val
    return None


def _row_updated_at(row) -> int:
    for attr in ("updatedAt", "lastSyncedAt", "timestamp", "createdAt"):
        val = getattr(row, attr, None)
        if isinstance(val, int):
            return val
    return 0


def _row_to_dict(row) -> dict:
    out = {}
    for col in row.__table__.columns:
        v = getattr(row, col.name, None)
        if isinstance(v, bytes):
            v = v.decode("utf-8", errors="replace")
        out[col.name] = v
    return out


def compute_table_summary(
    table: str,
    *,
    workspace_id: str,
    fields: Optional[list] = None,
) -> dict:
    """Return a deterministic summary of the local state for `table`.

    Shape: `{"row_count", "latest_updated_at", "hash"}`. `hash` is
    SHA-256 over the concatenation of `(id, updatedAt)` pairs in
    sorted-id order — a cheap fingerprint that flips whenever any
    row in the workspace changes.
    """
    import hashlib

    model = _table_model(table)
    if model is None:
        return {"error": f"unknown_table:{table}"}

    try:
        from core.database.sqlite_setup import get_session, init_db

        init_db()
        with get_session() as session:
            rows = session.query(model).all()
            scoped = [r for r in rows if _row_workspace(r, model) == workspace_id]
            scoped.sort(key=lambda r: getattr(r, "id", ""))
            digest = hashlib.sha256()
            latest = 0
            for r in scoped:
                rid = getattr(r, "id", None) or ""
                ts = _row_updated_at(r)
                latest = max(latest, ts)
                digest.update(f"{rid}|{ts}\n".encode("utf-8"))
            return {
                "row_count": len(scoped),
                "latest_updated_at": latest,
                "hash": digest.hexdigest(),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p_sync] summary failed: %s", exc)
        return {"error": str(exc)[:200]}


def compute_table_delta(
    table: str,
    *,
    workspace_id: str,
    since_ms: int = 0,
) -> list:
    """Return rows of `table` in `workspace_id` updated since `since_ms`."""
    model = _table_model(table)
    if model is None:
        return []
    try:
        from core.database.sqlite_setup import get_session, init_db

        init_db()
        with get_session() as session:
            rows = session.query(model).all()
            out = []
            for r in rows:
                if _row_workspace(r, model) != workspace_id:
                    continue
                ts = _row_updated_at(r)
                if ts >= since_ms:
                    out.append(_row_to_dict(r))
            out.sort(key=lambda d: d.get("id") or "")
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p_sync] delta failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# P7.2 — Blob serving over the encrypted session
# ---------------------------------------------------------------------------


# Max plaintext bytes per blob.chunk frame. Conservative: well below
# the Fernet/JSON frame ceiling so the b64-encoded payload + envelope
# still fits inside FRAME_MAX_BYTES.
BLOB_CHUNK_SIZE = 32 * 1024  # 32 KiB plaintext per chunk


def _serve_blob_request(session: SyncSession, request: dict) -> None:
    """Server-side handler for `blob.request`. Streams a curated GGUF
    file to the peer in 32 KiB chunks IF the SHA-256 matches a local
    file. Otherwise responds with `blob.notfound`.

    Security envelope:
      * The encrypted Fernet frame is the authenticator — both sides
        derived the symmetric key from the same X25519+HKDF transcript
        and verified each other's Ed25519 identity at handshake. By
        the time we're serving bytes, the peer's workspace_id has
        already matched ours.
      * SHA-256 acts as the only file selector. Asking for a SHA that
        doesn't match any catalog entry returns notfound — no path
        leak, no directory enumeration.
    """
    import base64

    request_id = request.get("request_id") or str(uuid.uuid4())
    requested_sha = request.get("sha256")
    if not isinstance(requested_sha, str) or len(requested_sha) != 64:
        session.send_encrypted(
            {
                "op": "blob.notfound",
                "request_id": request_id,
                "reason": "malformed_sha256",
            }
        )
        return

    blob_path = _locate_blob_by_sha256(requested_sha)
    if blob_path is None:
        session.send_encrypted(
            {
                "op": "blob.notfound",
                "request_id": request_id,
            }
        )
        return

    size = blob_path.stat().st_size
    n_chunks = (size + BLOB_CHUNK_SIZE - 1) // BLOB_CHUNK_SIZE
    session.send_encrypted(
        {
            "op": "blob.found",
            "request_id": request_id,
            "size_bytes": size,
            "chunks": n_chunks,
            "sha256": requested_sha,
        }
    )

    try:
        with blob_path.open("rb") as fh:
            for idx in range(n_chunks):
                chunk = fh.read(BLOB_CHUNK_SIZE)
                if not chunk:
                    break
                session.send_encrypted(
                    {
                        "op": "blob.chunk",
                        "request_id": request_id,
                        "idx": idx,
                        "data_b64": base64.b64encode(chunk).decode("ascii"),
                    }
                )
    except OSError as exc:
        logger.warning("[p2p_sync] blob read failed: %s", exc)
        session.send_encrypted(
            {
                "op": "blob.eof",
                "request_id": request_id,
                "sha256": requested_sha,
                "truncated": True,
            }
        )
        return

    session.send_encrypted(
        {
            "op": "blob.eof",
            "request_id": request_id,
            "sha256": requested_sha,
        }
    )


def _locate_blob_by_sha256(requested_sha: str) -> Optional["Path"]:
    """Return the local file path whose SHA-256 matches `requested_sha`,
    OR None if no curated catalog entry holds that hash on disk.

    The lookup is constrained to the curated catalog — operators don't
    expose arbitrary disk content via the mesh. The catalog entry's
    `id` determines the filename in `$VOS3_MODEL_DIR`."""
    try:
        # Late import to avoid circulars; model_manager has the
        # catalog + model_dir helpers.
        from services.model_manager import (
            load_curated_catalog,
            _resolve_model_dir,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p_sync] catalog import failed: %s", exc)
        return None
    try:
        catalog = load_curated_catalog()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p_sync] catalog load failed: %s", exc)
        return None
    md = _resolve_model_dir()
    target = requested_sha.lower()
    for entry in catalog.get("models", []):
        if (entry.get("sha256") or "").lower() != target:
            continue
        candidate = md / f"{entry['id']}.gguf"
        if candidate.exists():
            return candidate
    return None


__all__ = [
    "BLOB_CHUNK_SIZE",
    "BlobNotFound",
    "HANDSHAKE_TIMEOUT_S",
    "HandshakeFailed",
    "SessionClosed",
    "SessionMetadata",
    "SovereignSyncEngine",
    "SovereignSyncServer",
    "SyncSession",
    "compute_table_delta",
    "compute_table_summary",
    "default_session_handler",
]
