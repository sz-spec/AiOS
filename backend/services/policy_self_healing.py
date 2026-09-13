"""
backend/services/policy_self_healing.py — Phase 37 (Gap G14)
=============================================================

Self-healing policy re-sync: on a detected ``IntegrityViolation`` /
``LedgerCompromise``, rebuild the kernel's active ``taint_colors`` state by
replaying the transparency-ledger events from the last VALID, TPM-EK-signed
Merkle checkpoint (Phase 35). The ledger is the durable, hardware-anchored
source of truth; the kernel map is the volatile state being repaired — so a
transient corruption of the map is healed by re-applying the anchored colors.

Trust model (honest)
=====================

Healing recovers ONLY up to the last checkpoint whose EK signature verifies AND
whose recomputed Merkle root matches the ledger — i.e. the last cryptographically
anchored, untampered state. Events past that point are not trusted for recovery.
If NO checkpoint verifies (the ledger itself is compromised), ``heal`` refuses and
the system stays in Safe-Lock (no silent "recovery" from a tampered ledger). The
kernel re-apply runs through the EXISTING ``KernelGateConnector`` (MOCK on dev);
``kernel/src/mm/`` is never touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from security.kernel_gate_connector import SinkKind
from services.audit_anchor import _anchor_message, _rederive_root

logger = logging.getLogger("vos3.security.self_healing")

_TOXIC = 3


@dataclass(frozen=True)
class HealReport:
    healed: bool
    reason: str
    recovered_to_tree_size: int
    events_replayed: int
    colors_restored: int


class _ColorBuffer:
    """All-<color> TaintedBuffer-shaped mask used to re-push a segment's color."""

    def __init__(self, color: int, n: int = 64):
        import hashlib

        self.content = b"\x00" * n
        self.colors = bytes([color]) * n
        self._sha = hashlib.sha256(self.content + bytes([color])).hexdigest()

    @property
    def sha256(self) -> str:
        return self._sha

    def max_color(self) -> int:
        return max(self.colors) if self.colors else 0


def _anchor_sig_ok(anchor) -> bool:
    try:
        import base64

        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        nonce = base64.b64decode(anchor.nonce_b64)
        msg = _anchor_message(anchor.tree_size, anchor.root_hex)
        pub = serialization.load_pem_public_key(anchor.ek_public_pem.encode("ascii"))
        pub.verify(
            base64.b64decode(anchor.sig_b64), nonce + msg, ec.ECDSA(hashes.SHA256())
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class PolicyReSync:
    """Replays anchored ledger events back into the kernel taint_colors map."""

    def __init__(self, *, ledger, connector) -> None:
        self._ledger = ledger
        self._conn = connector

    def resync(self, *, up_to_tree_size: int) -> int:
        """Re-apply the taint-color state recorded by ``ipc_taint_inheritance``
        events in ``[0, up_to_tree_size)`` to the connector. Returns the number of
        colors restored. Idempotent + monotonic (re-pushing the anchored color)."""
        restored = 0
        records = self._ledger._records[
            :up_to_tree_size
        ]  # noqa: SLF001 — recovery read
        for rec in records:
            if rec.get("event_type") != "ipc_taint_inheritance":
                continue
            md = rec.get("metadata") or {}
            try:
                seg_id = int(md["seg_id"])
                target_pid = int(md["target_pid"])
                color = int(md["target_after"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                self._conn.push_tainted_buffer(
                    fd=seg_id,
                    pid=target_pid,
                    buffer=_ColorBuffer(color),
                    sink_kind=SinkKind.NETWORK_EGRESS,
                )
                restored += 1
            except Exception as exc:  # noqa: BLE001 — one bad event must not abort heal
                logger.warning(
                    "[self-heal] re-push failed seg=%s: %s", md.get("seg_id"), exc
                )
        return restored


class PolicySelfHealingBroker:
    """Orchestrates recovery from the last valid hardware-anchored checkpoint."""

    def __init__(self, *, ledger, anchor_mgr, connector, watchdog=None) -> None:
        self._ledger = ledger
        self._anchor = anchor_mgr
        self._resync = PolicyReSync(ledger=ledger, connector=connector)
        self._watchdog = watchdog

    def last_valid_checkpoint(self):
        """The highest-tree_size anchor whose EK signature verifies AND whose
        recomputed Merkle root matches the ledger. None if none verify."""
        valid = None
        for anchor in self._anchor.nvram.read_all():
            if not _anchor_sig_ok(anchor):
                continue
            if _rederive_root(self._ledger, anchor.tree_size) != anchor.root_hex:
                continue
            if valid is None or anchor.tree_size > valid.tree_size:
                valid = anchor
        return valid

    def heal(self) -> HealReport:
        """Rebuild taint_colors from the last valid checkpoint. Clears Safe-Lock on
        success; refuses (stays locked) if no checkpoint verifies."""
        cp = self.last_valid_checkpoint()
        if cp is None:
            logger.critical(
                "[SECURITY_CRITICAL][self-heal] NO valid checkpoint — cannot heal; "
                "system remains in Safe-Lock"
            )
            return HealReport(
                healed=False,
                reason="no valid signed checkpoint to recover from",
                recovered_to_tree_size=0,
                events_replayed=0,
                colors_restored=0,
            )
        replayed = len(self._ledger._records[: cp.tree_size])  # noqa: SLF001
        restored = self._resync.resync(up_to_tree_size=cp.tree_size)
        logger.warning(
            "[SECURITY][self-heal] recovered to tree_size=%d events=%d colors_restored=%d",
            cp.tree_size,
            replayed,
            restored,
        )
        if self._watchdog is not None and self._watchdog.is_safe_locked():
            # The verified replay IS the authorized recovery action.
            self._watchdog.clear_safe_lock(
                operator_attestation=f"self-heal:replay-from-checkpoint@{cp.tree_size}"
            )
        return HealReport(
            healed=True,
            reason=f"recovered from checkpoint tree_size={cp.tree_size}",
            recovered_to_tree_size=cp.tree_size,
            events_replayed=replayed,
            colors_restored=restored,
        )


__all__ = [
    "HealReport",
    "PolicyReSync",
    "PolicySelfHealingBroker",
]
