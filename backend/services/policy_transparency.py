"""
backend/services/policy_transparency.py — Phase 26 (Gap G3)
============================================================

A LIVE append-only, tamper-evident transparency log for policy-integrity
events (privacy-routing / data-sovereignty / compliance-validation decisions),
plus dynamic cryptographic inclusion-proof generation and a signed tree head
(STH) over the current root.

Honest scope
============

* The Merkle primitive here is an **RFC-6962 binary Merkle transparency log**
  (SHA-256, domain-separated leaf/node hashing, bottom-up audit paths). The
  four hashing/proof functions below are **byte-faithful vendored copies** of
  the proven implementation in ``infra/security/rekor_v2_log.py`` (lines
  82-167) — ``infra`` is not on the backend import path, so they are inlined
  here rather than path-hacked. ``test_phase26_attestation_proofs.py`` contains
  a cross-check that asserts these vendored functions produce output identical
  to the ``infra`` originals, so a future divergence is itself a test failure.

* The project's broader vocabulary calls the **kernel-side** ledger an "MMR"
  (Merkle Mountain Range), reached via the (still no-op on dev) ``MMR_RECORD_EVENT``
  VBus command. This module is the **userspace** transparency ledger; it is NOT
  that kernel MMR. Both are append-only Merkle structures; we do not claim this
  one is the kernel MMR.

* The STH is a genuine ECDSA-P256 signature over ``tree_size || root_hash``. The
  signing key is resolved from ``VOS3_TRANSPARENCY_STH_KEY_PEM`` (a persistent
  PEM private key) when set; otherwise a **process-ephemeral** keypair is
  generated and a warning is logged (the public key is served at
  ``/api/v1/transparency/root`` so any STH is verifiable within its key epoch).

Nothing here advances the moat tally — that is gated exclusively on an
independent third-party external audit.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

logger = logging.getLogger("vos3.security.transparency")

# ---------------------------------------------------------------------------
# RFC-6962 §2.1 Merkle hashing — VENDORED byte-for-byte from
# infra/security/rekor_v2_log.py:78-167. Domain separation:
#   leaf hash:  SHA-256(0x00 || data)
#   node hash:  SHA-256(0x01 || left || right)
# ---------------------------------------------------------------------------

LEAF_DOMAIN = b"\x00"
NODE_DOMAIN = b"\x01"


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(LEAF_DOMAIN + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_DOMAIN + left + right).digest()


def compute_root(leaves: List[bytes]) -> bytes:
    """Merkle root over a list of LEAF HASHES (already domain-sep'd)."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return leaves[0]
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return node_hash(compute_root(leaves[:k]), compute_root(leaves[k:]))


def compute_inclusion_path(leaves: List[bytes], idx: int) -> List[bytes]:
    """RFC-6962 §2.1.1 audit path, BOTTOM-UP order."""
    n = len(leaves)
    if idx < 0 or idx >= n:
        raise IndexError(f"leaf index {idx} out of range [0, {n})")
    if n == 1:
        return []
    k = 1
    while k * 2 < n:
        k *= 2
    if idx < k:
        return compute_inclusion_path(leaves[:k], idx) + [compute_root(leaves[k:])]
    return compute_inclusion_path(leaves[k:], idx - k) + [compute_root(leaves[:k])]


def verify_inclusion(
    *,
    leaf_data: bytes,
    leaf_index: int,
    tree_size: int,
    audit_path: List[bytes],
    expected_root: bytes,
) -> bool:
    """Verify a bottom-up audit path against the claimed root (RFC-6962 §2.1.1)."""
    if leaf_index < 0 or leaf_index >= tree_size:
        return False
    fn = leaf_index
    sn = tree_size - 1
    r = leaf_hash(leaf_data)
    for sibling in audit_path:
        if sn == 0:
            return False
        if (fn & 1) == 1 or fn == sn:
            r = node_hash(sibling, r)
            while (fn & 1) == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, sibling)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


# ---------------------------------------------------------------------------
# Signed Tree Head (STH) — ECDSA P-256 over tree_size || root_hash.
# ---------------------------------------------------------------------------


def _sth_message(tree_size: int, root_hash_hex: str) -> bytes:
    # Domain-separated, unambiguous: prefix + decimal size + ':' + 32-byte root.
    return (
        b"vos3-sth-v1:" + str(tree_size).encode() + b":" + bytes.fromhex(root_hash_hex)
    )


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

DEFAULT_LOG_PATH = "infra/security/policy_transparency.jsonl"


class PolicyTransparencyLedger:
    """Append-only Merkle transparency log for policy-integrity events.

    Persisted as JSON-Lines (one full record per line, including the canonical
    leaf payload) so proofs can be regenerated against the CURRENT root for any
    historical event — not just the root at append time.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(
            path or os.environ.get("VOS3_POLICY_TRANSPARENCY_LOG") or DEFAULT_LOG_PATH
        )
        self._lock = threading.RLock()
        self._records: List[Dict[str, Any]] = []
        self._leaves: List[bytes] = []
        self._by_id: Dict[str, int] = {}
        self._priv: Optional[ec.EllipticCurvePrivateKey] = None
        self._ephemeral_key = False
        self._init_signing_key()
        self._load_existing()

    # -- signing key -------------------------------------------------------

    def _init_signing_key(self) -> None:
        pem_path = os.environ.get("VOS3_TRANSPARENCY_STH_KEY_PEM", "").strip()
        if pem_path and Path(pem_path).is_file():
            try:
                self._priv = serialization.load_pem_private_key(
                    Path(pem_path).read_bytes(), password=None
                )
                if not isinstance(self._priv, ec.EllipticCurvePrivateKey):
                    raise TypeError("STH key must be an EC private key")
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transparency] STH key %s unloadable (%s); using ephemeral",
                    pem_path,
                    exc,
                )
        # Process-ephemeral fallback (dev): STH verifiable within this epoch.
        self._priv = ec.generate_private_key(ec.SECP256R1())
        self._ephemeral_key = True
        logger.warning(
            "[transparency] STH signing key is PROCESS-EPHEMERAL "
            "(set VOS3_TRANSPARENCY_STH_KEY_PEM for a persistent signed tree head)"
        )

    def public_key_pem(self) -> str:
        assert self._priv is not None
        return (
            self._priv.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )

    # -- load --------------------------------------------------------------

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        with self._lock:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        # Truncated final line from a crashed append — stop.
                        break
                    self._records.append(rec)
                    self._leaves.append(bytes.fromhex(rec["leaf_hash"]))
                    self._by_id[rec["event_id"]] = rec["leaf_index"]

    # -- canonicalization --------------------------------------------------

    @staticmethod
    def _canonical_payload(
        *,
        event_type: str,
        schema_hash: str,
        seq: int,
        appended_at: str,
        metadata: Dict[str, Any],
    ) -> bytes:
        return json.dumps(
            {
                "v": 1,
                "event_type": event_type,
                "schema_hash": schema_hash,
                "seq": seq,
                "appended_at": appended_at,
                "metadata": metadata,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    # -- append ------------------------------------------------------------

    def record(
        self,
        *,
        event_type: str,
        schema_hash: str,
        metadata: Optional[Dict[str, Any]] = None,
        appended_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one policy event; return its record (incl. event_id).

        ``appended_at`` may be supplied for determinism in tests; otherwise a
        UTC ISO-8601 timestamp is stamped. Append-only and tamper-evident: each
        event becomes a new leaf, the root advances, and the full canonical
        payload is persisted so a proof can be regenerated later.
        """
        from datetime import datetime, timezone

        meta = dict(metadata or {})
        with self._lock:
            seq = len(self._records)
            ts = appended_at or datetime.now(timezone.utc).isoformat()
            payload = self._canonical_payload(
                event_type=event_type,
                schema_hash=schema_hash,
                seq=seq,
                appended_at=ts,
                metadata=meta,
            )
            leaf = leaf_hash(payload)
            # Deterministic, collision-resistant handle (payload carries seq+ts).
            event_id = "pte_%d_%s" % (seq, hashlib.sha256(payload).hexdigest()[:16])
            self._leaves.append(leaf)
            idx = seq
            rec = {
                "event_id": event_id,
                "event_type": event_type,
                "schema_hash": schema_hash,
                "seq": seq,
                "appended_at": ts,
                "leaf_index": idx,
                "leaf_hash": leaf.hex(),
                "payload_b64": base64.b64encode(payload).decode("ascii"),
                "metadata": meta,
            }
            self._records.append(rec)
            self._by_id[event_id] = idx
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, sort_keys=True) + "\n")
                    f.flush()
            except OSError as exc:
                logger.warning("[transparency] persist failed: %s", exc)
            return rec

    # -- proof -------------------------------------------------------------

    def latest_root(self) -> str:
        with self._lock:
            return compute_root(self._leaves).hex()

    def tree_size(self) -> int:
        with self._lock:
            return len(self._leaves)

    def signed_tree_head(self) -> Dict[str, Any]:
        with self._lock:
            size = len(self._leaves)
            root_hex = compute_root(self._leaves).hex()
        assert self._priv is not None
        sig = self._priv.sign(_sth_message(size, root_hex), ec.ECDSA(hashes.SHA256()))
        return {
            "tree_size": size,
            "root_hash": root_hex,
            "sth_signature_b64": base64.b64encode(sig).decode("ascii"),
            "sth_algorithm": "ecdsa-p256-sha256",
            "public_key_pem": self.public_key_pem(),
            "ephemeral_key": self._ephemeral_key,
            "log_algorithm": "rfc6962-sha256",
        }

    def generate_proof(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Return a live inclusion proof against the CURRENT root, or None if
        the event id is unknown."""
        with self._lock:
            idx = self._by_id.get(event_id)
            if idx is None:
                return None
            rec = self._records[idx]
            audit_path = compute_inclusion_path(self._leaves, idx)
            root_hex = compute_root(self._leaves).hex()
            size = len(self._leaves)
        assert self._priv is not None
        sth_sig = self._priv.sign(
            _sth_message(size, root_hex), ec.ECDSA(hashes.SHA256())
        )
        return {
            "event_id": event_id,
            "event_type": rec["event_type"],
            "schema_hash": rec["schema_hash"],
            "appended_at": rec["appended_at"],
            "leaf_index": idx,
            "tree_size": size,
            "root_hash": root_hex,
            "leaf_hash": rec["leaf_hash"],
            "leaf_payload_b64": rec["payload_b64"],
            "audit_path": [h.hex() for h in audit_path],
            "algorithm": "rfc6962-sha256",
            "sth_signature_b64": base64.b64encode(sth_sig).decode("ascii"),
            "sth_algorithm": "ecdsa-p256-sha256",
            "public_key_pem": self.public_key_pem(),
            "ephemeral_key": self._ephemeral_key,
        }


# ---------------------------------------------------------------------------
# Stand-alone verification helpers (for clients / tests / auditors)
# ---------------------------------------------------------------------------


def verify_proof(proof: Dict[str, Any]) -> bool:
    """Full client-side verification: STH signature over the root AND the
    inclusion path of the leaf payload against that same root. Returns False
    (never raises) on any structural or cryptographic failure."""
    try:
        root = bytes.fromhex(proof["root_hash"])
        payload = base64.b64decode(proof["leaf_payload_b64"])
        path = [bytes.fromhex(h) for h in proof["audit_path"]]
        # 1) inclusion against the claimed root
        if not verify_inclusion(
            leaf_data=payload,
            leaf_index=int(proof["leaf_index"]),
            tree_size=int(proof["tree_size"]),
            audit_path=path,
            expected_root=root,
        ):
            return False
        # 2) the claimed root is the legitimate signed tree head
        pub = serialization.load_pem_public_key(proof["public_key_pem"].encode("ascii"))
        pub.verify(
            base64.b64decode(proof["sth_signature_b64"]),
            _sth_message(int(proof["tree_size"]), proof["root_hash"]),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Process-wide singleton + the policy-event dispatcher
# ---------------------------------------------------------------------------

_LEDGER_SINGLETON: Optional[PolicyTransparencyLedger] = None
_SINGLETON_LOCK = threading.Lock()


def get_policy_transparency_ledger() -> PolicyTransparencyLedger:
    global _LEDGER_SINGLETON
    if _LEDGER_SINGLETON is None:
        with _SINGLETON_LOCK:
            if _LEDGER_SINGLETON is None:
                _LEDGER_SINGLETON = PolicyTransparencyLedger()
    return _LEDGER_SINGLETON


def _reset_singleton_for_tests(ledger: Optional[PolicyTransparencyLedger]) -> None:
    """Test seam: install a ledger bound to a tmp path (or clear it)."""
    global _LEDGER_SINGLETON
    with _SINGLETON_LOCK:
        _LEDGER_SINGLETON = ledger


def transparency_feed_enabled() -> bool:
    """True iff the operator opted the live policy->transparency feed ON.

    Default OFF so dev/CI request flows do not write a ledger file and the
    existing test baseline is unaffected. The endpoint + engine are always
    live; only the automatic feed from the policy engines is gated."""
    return os.environ.get("VOS3_POLICY_TRANSPARENCY_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def record_policy_event(
    *,
    event_type: str,
    schema_hash: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Best-effort dispatcher used by the policy engines. Returns the event_id
    on success, or None when the feed is OFF or any error occurs. NEVER raises —
    transparency logging must not affect a privacy-routing decision's flow."""
    if not transparency_feed_enabled():
        return None
    try:
        rec = get_policy_transparency_ledger().record(
            event_type=event_type, schema_hash=schema_hash, metadata=metadata
        )
        logger.info(
            "[transparency] recorded event_type=%s event_id=%s leaf_index=%d",
            event_type,
            rec["event_id"],
            rec["leaf_index"],
        )
        return rec["event_id"]
    except Exception as exc:  # noqa: BLE001 — must never raise to the policy path
        logger.warning("[transparency] record_policy_event failed: %s", exc)
        return None


__all__ = [
    "PolicyTransparencyLedger",
    "get_policy_transparency_ledger",
    "record_policy_event",
    "transparency_feed_enabled",
    "verify_proof",
    "verify_inclusion",
    "compute_root",
    "compute_inclusion_path",
    "leaf_hash",
    "node_hash",
    "_reset_singleton_for_tests",
]
