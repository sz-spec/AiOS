"""
Phase 35 — immutable hardware-anchored audit ledger (Gap G12).
==============================================================

Exercises the REAL Phase-26 ``PolicyTransparencyLedger`` + REAL Phase-31 TPM EK
(``LocalSoftTPM``) + REAL Phase-32 Safe-Lock, composed by the Phase-35 anchor:

  TC1  append events -> checkpoint -> the TPM EK anchor signature verifies and the
       anchored root equals the ledger's recomputed Merkle root.
  TC2  tamper a past log entry -> re-verify -> IntegrityCheckFailed +
       LEDGER_COMPROMISE_CRITICAL + permanent Safe-Lock engaged.
  TC3  hardware anchor persists across a "soft reboot" (NVRAM file reloaded by a
       fresh checker instance) -> still verifies.

Anti-gaming (charter §II): fixed-value asserts; the anchor is a genuine ECDSA EK
signature, the tamper is a real byte change to a past leaf payload (caught by
re-deriving the Merkle root, not trusting cached leaf hashes), and no TPM ->
fail-closed.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase35_audit_immutability.py -n 0
"""

from __future__ import annotations

import base64

import pytest

from services.audit_anchor import (
    AuditCheckpointAnchor,
    AuditIntegrityChecker,
    DEFAULT_CHECKPOINT_INTERVAL,
    LedgerCompromise,
)
from services.integrity_watchdog import RuntimeIntegrityWatchdog
from services.policy_transparency import PolicyTransparencyLedger
from services.tpm_attestation import (
    AttestationDenied,
    LocalSoftTPM,
    PlatformAttestationService,
)

_GOLD = {0: "aa" * 32, 1: "bb" * 32, 7: "cc" * 32, 11: "dd" * 32}


def _attestation(present=True):
    return PlatformAttestationService(
        LocalSoftTPM(present=present, pcrs=dict(_GOLD)),
        authorized_pcrs=dict(_GOLD),
        audit_sink=lambda r: None,
    )


def _append(ledger, n, prefix="evt"):
    for i in range(n):
        ledger.record(event_type=prefix, schema_hash=f"{i:064x}", metadata={"i": i})


# ---------------------------------------------------------------------------
# TC1 — append -> checkpoint -> EK anchor verifies against the Merkle root.
# ---------------------------------------------------------------------------


def test_tc1_checkpoint_ek_anchor_verifies(tmp_path):
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    svc = _attestation()
    anchor_mgr = AuditCheckpointAnchor(ledger, svc, interval=100)

    _append(ledger, 100)
    anchor = anchor_mgr.maybe_checkpoint()  # crossed the 100-event boundary
    assert anchor is not None
    assert anchor.tree_size == 100
    # The anchored root equals the ledger's current Merkle root.
    assert anchor.root_hex == ledger.latest_root()

    # The EK signature over the checkpoint verifies; the integrity check passes.
    checker = AuditIntegrityChecker(ledger)
    assert checker.verify([anchor]) is True


def test_tc1b_no_tpm_anchor_is_failclosed(tmp_path):
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    svc = _attestation(present=False)  # no TPM
    anchor_mgr = AuditCheckpointAnchor(ledger, svc, interval=10)
    _append(ledger, 10)
    with pytest.raises(AttestationDenied):
        anchor_mgr.checkpoint()  # cannot EK-anchor without hardware root


def test_default_interval_is_100():
    assert DEFAULT_CHECKPOINT_INTERVAL == 100


# ---------------------------------------------------------------------------
# TC2 — tamper a past entry -> IntegrityCheckFailed + permanent Safe-Lock.
# ---------------------------------------------------------------------------


def test_tc2_tamper_detected_and_safe_lock(tmp_path):
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    svc = _attestation()
    anchor_mgr = AuditCheckpointAnchor(ledger, svc, interval=50)
    _append(ledger, 50)
    anchor = anchor_mgr.checkpoint()

    wd = RuntimeIntegrityWatchdog(text_source=lambda: b"k", audit_sink=lambda r: None)
    audit: list = []
    checker = AuditIntegrityChecker(ledger, watchdog=wd, audit_sink=audit.append)
    assert checker.verify([anchor]) is True  # clean baseline

    # Tamper a PAST leaf's canonical payload (re-derivation will catch it).
    ledger._records[10]["payload_b64"] = base64.b64encode(
        b"TAMPERED"
    ).decode()  # noqa: SLF001

    with pytest.raises(LedgerCompromise):
        checker.verify([anchor])
    assert "LEDGER_COMPROMISE_CRITICAL" in [a["marker"] for a in audit]
    # Permanent Safe-Lock engaged (clearable only by operator attestation).
    assert wd.is_safe_locked() is True
    with pytest.raises(ValueError):
        wd.clear_safe_lock(operator_attestation="")


def test_tc2b_tampered_anchor_signature_detected(tmp_path):
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    svc = _attestation()
    anchor_mgr = AuditCheckpointAnchor(ledger, svc, interval=10)
    _append(ledger, 10)
    anchor = anchor_mgr.checkpoint()

    # Forge the anchored root (signature no longer covers it) -> EK verify fails.
    from dataclasses import replace

    forged = replace(anchor, root_hex=("11" * 32))
    checker = AuditIntegrityChecker(ledger)
    with pytest.raises(LedgerCompromise):
        checker.verify([forged])


# ---------------------------------------------------------------------------
# TC3 — hardware anchor persistence across a soft reboot (NVRAM reload).
# ---------------------------------------------------------------------------


def test_tc3_anchor_persists_across_soft_reboot(tmp_path):
    nvram = str(tmp_path / "nvram.jsonl")
    ledger_path = str(tmp_path / "l.jsonl")

    # Pre-reboot: append + checkpoint, anchor written to the NVRAM file.
    ledger1 = PolicyTransparencyLedger(path=ledger_path)
    svc1 = _attestation()
    mgr1 = AuditCheckpointAnchor(ledger1, svc1, interval=20, nvram_path=nvram)
    _append(ledger1, 20)
    mgr1.checkpoint()
    assert len(mgr1.nvram.read_all()) == 1

    # "Soft reboot": fresh ledger (reloads the SAME JSONL) + fresh anchor manager
    # (reloads the SAME NVRAM file). The hardware anchor survived.
    ledger2 = PolicyTransparencyLedger(path=ledger_path)
    svc2 = _attestation()
    mgr2 = AuditCheckpointAnchor(ledger2, svc2, interval=20, nvram_path=nvram)
    persisted = mgr2.nvram.read_all()
    assert len(persisted) == 1
    assert persisted[0].tree_size == 20

    # The persisted anchor still verifies against the reloaded ledger.
    checker = AuditIntegrityChecker(ledger2)
    assert checker.verify(persisted) is True


def test_nvram_is_append_only(tmp_path):
    # Two checkpoints append two anchors; the store only ever grows.
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    svc = _attestation()
    mgr = AuditCheckpointAnchor(
        ledger, svc, interval=5, nvram_path=str(tmp_path / "nv.jsonl")
    )
    _append(ledger, 5)
    mgr.checkpoint()
    _append(ledger, 5)
    mgr.checkpoint()
    sizes = [a.tree_size for a in mgr.nvram.read_all()]
    assert sizes == [5, 10]  # monotonic, append-only
