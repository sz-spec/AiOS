"""
backend/services/remote_attestation.py — Phase 36 (Gap G13)
============================================================

Self-attesting compliance gateway: assembles a single, OFFLINE-VERIFIABLE bundle
that lets an external compliance auditor verify vOS system state without internal
access. The bundle composes:
  - the current transparency-ledger signed tree head (Phase 26 STH),
  - the latest TPM-EK-signed Merkle checkpoint (Phase 35 hardware anchor),
  - inclusion proofs for the last N transactions, and
  - the current system taint status (taint_colors decision tally + Safe-Lock).

Fail-closed: when the system is in Safe-Lock (a prior LedgerCompromise /
IntegrityViolation), the gateway refuses to attest (the endpoint returns 403 +
``X-Compliance-Status: CRITICAL_FAILURE``).

Honest scope (read before citing G13 as closed)
================================================

The bundle's crypto is real and externally verifiable: RFC-6962 inclusion proofs
+ ECDSA-P256 STH (Phase 26) + ECDSA-P256 EK checkpoint signature (Phase 31/35).
What is MOCK on dev: the EK is the software ``LocalSoftTPM`` key (no real TPM /
manufacturer cert chain), so on a host with no ``/dev/tpmrm0`` the TPM checkpoint
is ``null`` and the bundle carries the ledger STH + proofs + taint status only
(it never fakes a hardware signature). INV-6: this is auditor INPUT, not an
external audit — it advances no moat tally.
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("vos3.security.remote_attestation")

BUNDLE_SCHEMA = "vos-attest-v1"


class RemoteAttestationProvider:
    """Builds the self-attesting compliance bundle from the live ledger + anchor."""

    def __init__(
        self,
        *,
        ledger,
        anchor_mgr,
        safe_lock_fn: Optional[Callable[[], Tuple[bool, str]]] = None,
        gate=None,
    ) -> None:
        self._ledger = ledger
        self._anchor = anchor_mgr
        self._safe_lock_fn = safe_lock_fn
        self._gate = gate

    # -- status ------------------------------------------------------------

    def compliance_status(self) -> Tuple[bool, str]:
        """(safe_locked, reason). Defaults to the Phase-32 egress Safe-Lock read."""
        if self._safe_lock_fn is not None:
            return self._safe_lock_fn()
        from services.integrity_watchdog import egress_safe_locked

        return egress_safe_locked()

    def taint_status(self) -> Dict[str, object]:
        locked, reason = self.compliance_status()
        status: Dict[str, object] = {
            "safe_locked": locked,
            "safe_lock_reason": reason,
        }
        gate = self._gate
        if gate is None:
            try:
                from security.kernel_gate_connector import get_kernel_gate

                gate = get_kernel_gate()
            except Exception:  # noqa: BLE001
                gate = None
        if gate is not None:
            try:
                status["egress_gate_mode"] = gate.mode.name
                status["decisions_allow"] = int(gate.stats.decisions_allow)
                status["decisions_deny"] = int(gate.stats.decisions_deny)
            except Exception:  # noqa: BLE001
                pass
        return status

    # -- bundle ------------------------------------------------------------

    def _latest_tpm_checkpoint(self) -> Optional[dict]:
        """Return the latest TPM-EK checkpoint anchor as a dict, minting one if the
        ledger has crossed a checkpoint boundary. Fail-closed: returns None if no
        TPM is present (never fakes a hardware signature)."""
        from services.tpm_attestation import AttestationDenied

        try:
            self._anchor.maybe_checkpoint()
        except AttestationDenied:
            pass  # no TPM -> no checkpoint; reflected truthfully as null below
        anchors = self._anchor.nvram.read_all()
        if not anchors:
            # No boundary crossed yet — force one if a TPM is available.
            try:
                self._anchor.checkpoint()
                anchors = self._anchor.nvram.read_all()
            except AttestationDenied:
                return None
        return anchors[-1].to_dict() if anchors else None

    def _last_n_inclusion_proofs(self, last_n: int) -> List[dict]:
        records = self._ledger._records  # noqa: SLF001 — read-only introspection
        proofs: List[dict] = []
        for rec in records[-last_n:]:
            p = self._ledger.generate_proof(rec["event_id"])
            if p is not None:
                proofs.append(p)
        return proofs

    def build_bundle(self, *, last_n: int = 100) -> Dict[str, object]:
        sth = self._ledger.signed_tree_head()
        tpm_checkpoint = self._latest_tpm_checkpoint()
        proofs = self._last_n_inclusion_proofs(last_n)
        return {
            "schema": BUNDLE_SCHEMA,
            "tree_size": self._ledger.tree_size(),
            "ledger_sth": sth,
            "tpm_checkpoint": tpm_checkpoint,
            "inclusion_proofs": proofs,
            "taint_status": self.taint_status(),
        }


# ---------------------------------------------------------------------------
# External verifier — runs with NO internal access, only the bundle + EK pub.
# ---------------------------------------------------------------------------


def verify_attestation_bundle(bundle: Dict[str, object]) -> Dict[str, object]:
    """Offline verification an external auditor performs. Verifies:
      - every inclusion proof (RFC-6962 inclusion + STH signature, Phase 26),
      - the TPM-EK checkpoint signature over its Merkle root (Phase 35), if present.
    Returns a report dict; ``all_ok`` is the conjunction. Never raises."""
    from services.policy_transparency import verify_proof

    report: Dict[str, object] = {}

    proofs = bundle.get("inclusion_proofs") or []
    proofs_ok = all(verify_proof(p) for p in proofs) if proofs else True
    report["inclusion_proofs_ok"] = proofs_ok
    report["proof_count"] = len(proofs)

    # STH self-consistency: each proof's root must equal the bundle's ledger root.
    sth = bundle.get("ledger_sth") or {}
    sth_root = sth.get("root_hash")
    sth_ok = bool(sth_root) and all(p.get("root_hash") == sth_root for p in proofs)
    report["ledger_sth_ok"] = sth_ok if proofs else bool(sth_root)

    tpm = bundle.get("tpm_checkpoint")
    if tpm is None:
        report["tpm_checkpoint_present"] = False
        report["tpm_checkpoint_ok"] = False
    else:
        report["tpm_checkpoint_present"] = True
        report["tpm_checkpoint_ok"] = _verify_tpm_checkpoint(tpm)

    report["all_ok"] = bool(
        report["inclusion_proofs_ok"]
        and report["ledger_sth_ok"]
        and report["tpm_checkpoint_present"]
        and report["tpm_checkpoint_ok"]
    )
    return report


def _verify_tpm_checkpoint(tpm: dict) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        from services.audit_anchor import _anchor_message

        nonce = base64.b64decode(tpm["nonce_b64"])
        msg = _anchor_message(int(tpm["tree_size"]), str(tpm["root_hex"]))
        pub = serialization.load_pem_public_key(tpm["ek_public_pem"].encode("ascii"))
        pub.verify(
            base64.b64decode(tpm["sig_b64"]), nonce + msg, ec.ECDSA(hashes.SHA256())
        )
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Process-wide provider (the endpoint uses this; tests can override it).
# ---------------------------------------------------------------------------

_PROVIDER: Optional[RemoteAttestationProvider] = None
_LOCK = threading.Lock()


def _build_default_provider() -> RemoteAttestationProvider:
    from services.audit_anchor import AuditCheckpointAnchor
    from services.policy_transparency import get_policy_transparency_ledger
    from services.tpm_attestation import get_platform_attestation_service

    ledger = get_policy_transparency_ledger()
    anchor = AuditCheckpointAnchor(ledger, get_platform_attestation_service())
    return RemoteAttestationProvider(ledger=ledger, anchor_mgr=anchor)


def get_remote_attestation_provider() -> RemoteAttestationProvider:
    global _PROVIDER
    if _PROVIDER is None:
        with _LOCK:
            if _PROVIDER is None:
                _PROVIDER = _build_default_provider()
    return _PROVIDER


def set_remote_attestation_provider(
    provider: Optional[RemoteAttestationProvider],
) -> None:
    """Test/integration seam: install (or clear) the process provider."""
    global _PROVIDER
    with _LOCK:
        _PROVIDER = provider


__all__ = [
    "BUNDLE_SCHEMA",
    "RemoteAttestationProvider",
    "verify_attestation_bundle",
    "get_remote_attestation_provider",
    "set_remote_attestation_provider",
]
