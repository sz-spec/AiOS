"""
backend/services/audit_anchor.py — Phase 35 (Gap G12)
======================================================

Immutable audit ledger: periodically Merkle-checkpoints the Phase-26
``PolicyTransparencyLedger`` and anchors each checkpoint root with a TPM
Endorsement-Key signature (Phase-31) into an append-only NVRAM index. On boot the
``AuditIntegrityChecker`` re-derives the ledger's Merkle root and verifies every
anchor's EK signature; any tamper triggers ``LEDGER_COMPROMISE_CRITICAL`` and a
permanent Safe-Lock (Phase-32).

Honest scope (read before citing G12 as closed)
================================================

The crypto is real: RFC-6962 Merkle roots (Phase 26) + ECDSA-P256 EK signatures
(Phase 31). What is MOCK on dev:
  - the **hardware root** — a real TPM 2.0 EK + manufacturer cert chain. Dev uses
    the software ``LocalSoftTPM`` EK (INSECURE; no hardware binding); the anchor
    fails closed if no TPM is present.
  - the **NVRAM index** — a real TPM NV index / secure partition is the production
    store. Dev uses an append-only JSONL file (``_NVRAMStore``): the engine only
    ever appends and never rewrites prior anchors, modelling NVRAM monotonicity.
The integrity check does NOT trust the ledger's cached leaf hashes — it
RE-DERIVES each leaf from its stored canonical payload, so a tampered past entry
changes the recomputed root and is caught.

This advances no moat tally (INV-6: the anchor is evidence, not an external audit).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from services.policy_transparency import compute_root, leaf_hash

logger = logging.getLogger("vos3.security.audit_anchor")

DEFAULT_CHECKPOINT_INTERVAL = 100
_ANCHOR_DOMAIN = b"vos-ckpt-v1:"


def _anchor_message(tree_size: int, root_hex: str) -> bytes:
    return _ANCHOR_DOMAIN + str(tree_size).encode() + b":" + bytes.fromhex(root_hex)


@dataclass(frozen=True)
class CheckpointAnchor:
    tree_size: int
    root_hex: str
    nonce_b64: str
    sig_b64: str
    ek_public_pem: str
    alg: str = "ecdsa-p256-sha256"

    def to_dict(self) -> dict:
        return {
            "tree_size": self.tree_size,
            "root_hex": self.root_hex,
            "nonce_b64": self.nonce_b64,
            "sig_b64": self.sig_b64,
            "ek_public_pem": self.ek_public_pem,
            "alg": self.alg,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CheckpointAnchor":
        return cls(
            tree_size=int(d["tree_size"]),
            root_hex=str(d["root_hex"]),
            nonce_b64=str(d["nonce_b64"]),
            sig_b64=str(d["sig_b64"]),
            ek_public_pem=str(d["ek_public_pem"]),
            alg=str(d.get("alg", "ecdsa-p256-sha256")),
        )


class _NVRAMStore:
    """Append-only anchor index. In-memory by default; a file path models a
    persistent NVRAM partition that survives a soft reboot. Append-only: prior
    anchors are never rewritten (NVRAM monotonicity)."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._mem: List[CheckpointAnchor] = []
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._mem.append(CheckpointAnchor.from_dict(json.loads(line)))

    def append(self, anchor: CheckpointAnchor) -> None:
        with self._lock:
            self._mem.append(anchor)
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(anchor.to_dict()) + "\n")
                    f.flush()

    def read_all(self) -> List[CheckpointAnchor]:
        with self._lock:
            return list(self._mem)


def _rederive_root(ledger, tree_size: int) -> str:
    """Recompute the Merkle root over the FIRST ``tree_size`` leaves, re-deriving
    each leaf hash from its stored canonical payload (NOT the cached leaf_hash) so
    a tampered past entry is detected."""
    leaves = []
    for rec in ledger._records[:tree_size]:  # noqa: SLF001 — integrity introspection
        payload = base64.b64decode(rec["payload_b64"])
        leaves.append(leaf_hash(payload))
    return compute_root(leaves).hex()


class LedgerCompromise(Exception):
    """Raised on a detected audit-ledger tamper (root mismatch or anchor signature
    failure). ``.reason`` carries the audit cause."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Backwards-friendly alias matching the brief's name.
IntegrityCheckFailed = LedgerCompromise


class AuditCheckpointAnchor:
    """Periodically Merkle-checkpoints the ledger and EK-anchors the root."""

    def __init__(
        self,
        ledger,
        attestation_service,
        *,
        interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        nvram_path: Optional[str] = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._ledger = ledger
        self._svc = attestation_service
        self._interval = interval
        self._nvram = _NVRAMStore(nvram_path)
        self._lock = threading.RLock()
        self._last_anchored_size = 0

    @property
    def nvram(self) -> _NVRAMStore:
        return self._nvram

    def checkpoint(self) -> CheckpointAnchor:
        """Force a checkpoint at the current tree size: EK-sign the Merkle root and
        append the anchor to NVRAM. Fail-closed if no TPM (AttestationDenied)."""
        with self._lock:
            tree_size = self._ledger.tree_size()
            root_hex = _rederive_root(self._ledger, tree_size)
            nonce, quote = self._svc.ek_sign_digest(
                _anchor_message(tree_size, root_hex)
            )
            anchor = CheckpointAnchor(
                tree_size=tree_size,
                root_hex=root_hex,
                nonce_b64=base64.b64encode(nonce).decode("ascii"),
                sig_b64=quote.signature_b64,
                ek_public_pem=quote.ek_public_pem,
            )
            self._nvram.append(anchor)
            self._last_anchored_size = tree_size
            logger.info(
                "[SECURITY][audit-anchor] checkpoint tree_size=%d root=%s EK-anchored",
                tree_size,
                root_hex[:16],
            )
            return anchor

    def maybe_checkpoint(self) -> Optional[CheckpointAnchor]:
        """Checkpoint iff the ledger has crossed a new multiple of ``interval``
        since the last anchor. Returns the new anchor or None."""
        with self._lock:
            size = self._ledger.tree_size()
            if (
                size // self._interval > self._last_anchored_size // self._interval
                and size > 0
            ):
                return self.checkpoint()
            return None


class AuditIntegrityChecker:
    """Boot-time tamper-evident verification of the anchored ledger."""

    def __init__(self, ledger, *, watchdog=None, audit_sink=None) -> None:
        self._ledger = ledger
        self._watchdog = watchdog
        self._sink = audit_sink

    def _verify_anchor_sig(self, anchor: CheckpointAnchor) -> bool:
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            nonce = base64.b64decode(anchor.nonce_b64)
            msg = _anchor_message(anchor.tree_size, anchor.root_hex)
            pub = serialization.load_pem_public_key(
                anchor.ek_public_pem.encode("ascii")
            )
            pub.verify(
                base64.b64decode(anchor.sig_b64), nonce + msg, ec.ECDSA(hashes.SHA256())
            )
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    def verify(self, anchors) -> bool:
        """Re-derive the ledger root at each anchor's tree size, compare to the
        anchored root, and verify the EK signature. On ANY mismatch: emit
        LEDGER_COMPROMISE_CRITICAL, engage permanent Safe-Lock, raise
        LedgerCompromise. Returns True iff all anchors verify."""
        anchors = list(anchors)
        for anchor in anchors:
            # 1) the anchor's EK signature must be authentic.
            if not self._verify_anchor_sig(anchor):
                return self._compromise(
                    f"EK signature invalid for anchor tree_size={anchor.tree_size}"
                )
            # 2) the ledger's recomputed root at tree_size must equal the anchored
            #    root — a tampered PAST entry changes this and is caught.
            recomputed = _rederive_root(self._ledger, anchor.tree_size)
            if recomputed != anchor.root_hex:
                return self._compromise(
                    f"root mismatch at tree_size={anchor.tree_size}: "
                    f"recomputed={recomputed[:16]} anchored={anchor.root_hex[:16]}"
                )
        return True

    def _compromise(self, reason: str) -> bool:
        logger.critical(
            "[SECURITY_CRITICAL][audit-anchor] LEDGER_COMPROMISE_CRITICAL %s", reason
        )
        if self._sink is not None:
            try:
                self._sink({"marker": "LEDGER_COMPROMISE_CRITICAL", "reason": reason})
            except Exception:  # noqa: BLE001
                pass
        if self._watchdog is not None:
            # Permanent Safe-Lock: clearing requires an operator attestation.
            self._watchdog.engage_safe_lock(f"LEDGER_COMPROMISE_CRITICAL: {reason}")
        raise LedgerCompromise(reason)


__all__ = [
    "DEFAULT_CHECKPOINT_INTERVAL",
    "CheckpointAnchor",
    "AuditCheckpointAnchor",
    "AuditIntegrityChecker",
    "LedgerCompromise",
    "IntegrityCheckFailed",
]
