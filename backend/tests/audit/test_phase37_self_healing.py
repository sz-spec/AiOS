"""
Phase 37 — self-healing policy re-sync (Gap G14).
=================================================

Exercises the REAL ``PolicySelfHealingBroker`` over the REAL Phase-33 taint
broker / Phase-26 ledger / Phase-35 TPM checkpoint / Phase-32 Safe-Lock:

  Recovery-from-ledger (the headline TC): corrupt the kernel taint_colors map ->
  replay the anchored ledger -> verify the taint state is restored + Safe-Lock
  cleared.
  + refuse to heal when NO checkpoint verifies (stay locked).

Anti-gaming (charter §II): fixed-value asserts; the taint state is read back from
the REAL connector (peek_entry.max_color), recovery uses the REAL EK-signed
checkpoint + Merkle re-derivation, and a tampered/absent anchor blocks healing.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase37_self_healing.py -n 0
"""

from __future__ import annotations

from security.kernel_gate_connector import KernelGateConnector, TaintLabel
from services.audit_anchor import AuditCheckpointAnchor
from services.integrity_watchdog import RuntimeIntegrityWatchdog
from services.ipc_broker import TaintInheritanceBroker
from services.policy_self_healing import PolicySelfHealingBroker
from services.policy_transparency import PolicyTransparencyLedger
from services.tpm_attestation import LocalSoftTPM, PlatformAttestationService

TOXIC = int(TaintLabel.TOXIC)
SECRET = int(TaintLabel.SECRET)
PUBLIC = int(TaintLabel.PUBLIC)
_GOLD = {0: "aa" * 32, 1: "bb" * 32, 7: "cc" * 32, 11: "dd" * 32}


def _stack(tmp_path):
    conn = KernelGateConnector(force_mock=True)
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    svc = PlatformAttestationService(
        LocalSoftTPM(present=True, pcrs=dict(_GOLD)),
        authorized_pcrs=dict(_GOLD),
        audit_sink=lambda r: None,
    )
    anchor = AuditCheckpointAnchor(
        ledger, svc, interval=1, nvram_path=str(tmp_path / "nv.jsonl")
    )
    wd = RuntimeIntegrityWatchdog(text_source=lambda: b"k", audit_sink=lambda r: None)
    taint = TaintInheritanceBroker(gate_connector=conn, ledger=ledger)
    return conn, ledger, svc, anchor, wd, taint


# ---------------------------------------------------------------------------
# Recovery-from-ledger: corrupt taint -> replay -> state restored + unlock.
# ---------------------------------------------------------------------------


def test_recovery_from_ledger_restores_taint_and_clears_safe_lock(tmp_path):
    conn, ledger, svc, anchor, wd, taint = _stack(tmp_path)

    # Establish taint state: a TOXIC source maps into a target (records an
    # ipc_taint_inheritance event in the ledger AND pushes the color to the map).
    seg, src_pid, tgt_pid = 4096, 5001, 5002
    taint.register_segment(seg_id=seg, owner_pid=src_pid, color=TOXIC)
    result = taint.map_segment(seg_id=seg, source_pid=src_pid, target_pid=tgt_pid)
    assert result.target_color_after == TOXIC
    assert conn.peek_entry(fd=seg, pid=tgt_pid)["max_color"] == TOXIC

    # Anchor the ledger (TPM-EK-signed Merkle checkpoint).
    anchor.checkpoint()

    # SIMULATE transient memory corruption of the kernel map + a detected
    # violation engaging Safe-Lock.
    conn.evict(fd=seg, pid=tgt_pid)
    assert conn.peek_entry(fd=seg, pid=tgt_pid) is None  # corrupted/lost
    wd.engage_safe_lock("LEDGER_COMPROMISE_CRITICAL: simulated")
    assert wd.is_safe_locked() is True

    # HEAL: replay the anchored ledger back into the map.
    broker = PolicySelfHealingBroker(
        ledger=ledger, anchor_mgr=anchor, connector=conn, watchdog=wd
    )
    report = broker.heal()

    assert report.healed is True
    assert report.colors_restored >= 1
    # The TOXIC taint state is restored in the kernel map.
    assert conn.peek_entry(fd=seg, pid=tgt_pid)["max_color"] == TOXIC
    # Safe-Lock cleared by the verified recovery.
    assert wd.is_safe_locked() is False


def test_heal_recovers_to_last_valid_checkpoint(tmp_path):
    conn, ledger, svc, anchor, wd, taint = _stack(tmp_path)
    seg, src, tgt = 7000, 8001, 8002
    taint.register_segment(seg_id=seg, owner_pid=src, color=SECRET)
    taint.map_segment(seg_id=seg, source_pid=src, target_pid=tgt)
    anchor.checkpoint()
    cp = PolicySelfHealingBroker(
        ledger=ledger, anchor_mgr=anchor, connector=conn
    ).last_valid_checkpoint()
    assert cp is not None
    assert cp.tree_size == ledger.tree_size()


# ---------------------------------------------------------------------------
# Refuse to heal when there is no valid checkpoint (stay locked).
# ---------------------------------------------------------------------------


def test_no_valid_checkpoint_refuses_to_heal_and_stays_locked(tmp_path):
    conn, ledger, svc, anchor, wd, taint = _stack(tmp_path)
    taint.register_segment(seg_id=10, owner_pid=1, color=TOXIC)
    taint.map_segment(seg_id=10, source_pid=1, target_pid=2)
    # NO checkpoint taken -> nothing anchored.
    wd.engage_safe_lock("violation")

    broker = PolicySelfHealingBroker(
        ledger=ledger, anchor_mgr=anchor, connector=conn, watchdog=wd
    )
    report = broker.heal()
    assert report.healed is False
    assert report.recovered_to_tree_size == 0
    # Without a trusted recovery point the system stays in Safe-Lock.
    assert wd.is_safe_locked() is True


def test_tampered_checkpoint_root_is_not_a_valid_recovery_point(tmp_path):
    conn, ledger, svc, anchor, wd, taint = _stack(tmp_path)
    taint.register_segment(seg_id=11, owner_pid=1, color=TOXIC)
    taint.map_segment(seg_id=11, source_pid=1, target_pid=2)
    anchor.checkpoint()
    # Corrupt the only anchor's recorded root in NVRAM (now the EK sig won't cover
    # it / the root won't match) -> not a valid recovery point.
    from dataclasses import replace

    store = anchor.nvram
    store._mem[0] = replace(store._mem[0], root_hex="11" * 32)  # noqa: SLF001

    broker = PolicySelfHealingBroker(
        ledger=ledger, anchor_mgr=anchor, connector=conn, watchdog=wd
    )
    assert broker.last_valid_checkpoint() is None
    wd.engage_safe_lock("violation")
    assert broker.heal().healed is False
    assert wd.is_safe_locked() is True
