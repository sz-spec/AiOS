"""
Phase 33 — cross-process taint propagation (G5 propagation feature).
====================================================================

Exercises the REAL ``TaintInheritanceBroker`` over the REAL ``KernelGateConnector``
(taint_colors, MOCK on dev) + the REAL Phase-26 ``PolicyTransparencyLedger``:

  TC1  map Source(TOXIC) -> Target(CLEAN): target becomes TOXIC AND a provenance
       event is recorded (and is cryptographically provable).
  TC2  map Source(CLEAN) -> Target(CLEAN): no taint mutation.
  TC3  after IPC close: the taint maps are pruned (entry gone).
  + monotonic invariant: TOXIC is never scrubbed during inheritance.
  + a re-map that DOWNGRADES color -> PROVENANCE_VIOLATION + force-close.

Anti-gaming (charter §II): fixed-value asserts on the exact resulting colors /
mutated flag / pruned state; the colors are read back from the REAL connector
(peek_entry.max_color), and the provenance event is verified through the REAL
ledger inclusion proof — no mocked verdicts.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase33_taint_propagation.py -n 0
"""

from __future__ import annotations

import pytest

from security.kernel_gate_connector import KernelGateConnector, TaintLabel
from services.ipc_broker import ProvenanceViolation, TaintInheritanceBroker
from services.policy_transparency import PolicyTransparencyLedger, verify_proof

PUBLIC = int(TaintLabel.PUBLIC)  # 0 (clean)
UNTRUSTED = int(TaintLabel.UNTRUSTED)  # 1
SECRET = int(TaintLabel.SECRET)  # 2
TOXIC = int(TaintLabel.TOXIC)  # 3


def _broker(tmp_path):
    conn = KernelGateConnector(force_mock=True)
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "prov.jsonl"))
    return TaintInheritanceBroker(gate_connector=conn, ledger=ledger), conn, ledger


# ---------------------------------------------------------------------------
# TC1 — Source(TOXIC) -> Target(CLEAN): target becomes TOXIC + provenance logged.
# ---------------------------------------------------------------------------


def test_tc1_toxic_source_taints_clean_target_and_logs(tmp_path):
    broker, conn, ledger = _broker(tmp_path)
    seg, src_pid, tgt_pid = 4096, 5001, 5002
    broker.register_segment(seg_id=seg, owner_pid=src_pid, color=TOXIC)
    # Target starts clean (no entry -> PUBLIC).
    assert broker.recorded_color(seg_id=seg, pid=tgt_pid) == PUBLIC

    before_size = ledger.tree_size()
    result = broker.map_segment(seg_id=seg, source_pid=src_pid, target_pid=tgt_pid)

    # Target is now TOXIC in the kernel taint_colors map.
    assert result.target_color_after == TOXIC
    assert result.mutated is True
    assert conn.peek_entry(fd=seg, pid=tgt_pid)["max_color"] == TOXIC
    # A provenance event was recorded AND is cryptographically provable.
    assert ledger.tree_size() == before_size + 1
    assert result.provenance_event_id is not None
    proof = ledger.generate_proof(result.provenance_event_id)
    assert verify_proof(proof) is True
    assert proof["event_type"] == "ipc_taint_inheritance"


# ---------------------------------------------------------------------------
# TC2 — Source(CLEAN) -> Target(CLEAN): no mutation.
# ---------------------------------------------------------------------------


def test_tc2_clean_to_clean_no_mutation(tmp_path):
    broker, conn, _ = _broker(tmp_path)
    seg, src_pid, tgt_pid = 4097, 6001, 6002
    broker.register_segment(seg_id=seg, owner_pid=src_pid, color=PUBLIC)
    result = broker.map_segment(seg_id=seg, source_pid=src_pid, target_pid=tgt_pid)
    assert result.target_color_after == PUBLIC
    assert result.mutated is False
    # No toxic/elevated entry was written to the target.
    assert broker.recorded_color(seg_id=seg, pid=tgt_pid) == PUBLIC


# ---------------------------------------------------------------------------
# TC3 — after IPC close, taint maps are pruned.
# ---------------------------------------------------------------------------


def test_tc3_close_prunes_taint_maps(tmp_path):
    broker, conn, _ = _broker(tmp_path)
    seg, src_pid, tgt_pid = 4098, 7001, 7002
    broker.register_segment(seg_id=seg, owner_pid=src_pid, color=SECRET)
    broker.map_segment(seg_id=seg, source_pid=src_pid, target_pid=tgt_pid)
    assert conn.peek_entry(fd=seg, pid=tgt_pid) is not None  # present pre-close

    removed = broker.close_segment(seg_id=seg, pid=tgt_pid)
    assert removed is True
    assert conn.peek_entry(fd=seg, pid=tgt_pid) is None  # pruned
    assert broker.recorded_color(seg_id=seg, pid=tgt_pid) == PUBLIC  # registry cleared


# ---------------------------------------------------------------------------
# Monotonic invariant: TOXIC never scrubbed during inheritance (directional).
# ---------------------------------------------------------------------------


def test_toxic_target_not_scrubbed_by_clean_source(tmp_path):
    broker, conn, _ = _broker(tmp_path)
    seg, src_pid, tgt_pid = 4099, 8001, 8002
    # Target is already TOXIC; a CLEAN source maps in.
    broker.register_segment(seg_id=seg, owner_pid=src_pid, color=PUBLIC)
    broker.register_segment(seg_id=seg, owner_pid=tgt_pid, color=TOXIC)
    result = broker.map_segment(seg_id=seg, source_pid=src_pid, target_pid=tgt_pid)
    # max(PUBLIC, TOXIC) == TOXIC -> never scrubbed down.
    assert result.target_color_after == TOXIC
    assert result.mutated is False
    assert conn.peek_entry(fd=seg, pid=tgt_pid)["max_color"] == TOXIC


def test_elevating_source_raises_target_color(tmp_path):
    broker, conn, _ = _broker(tmp_path)
    seg, src_pid, tgt_pid = 4100, 9001, 9002
    broker.register_segment(seg_id=seg, owner_pid=src_pid, color=SECRET)
    broker.register_segment(seg_id=seg, owner_pid=tgt_pid, color=UNTRUSTED)
    result = broker.map_segment(seg_id=seg, source_pid=src_pid, target_pid=tgt_pid)
    assert result.target_color_after == SECRET  # max(SECRET, UNTRUSTED)
    assert result.mutated is True


# ---------------------------------------------------------------------------
# PROVENANCE_VIOLATION: a downgrade re-map force-closes the handle.
# ---------------------------------------------------------------------------


def test_downgrade_remap_triggers_provenance_violation_and_force_close(tmp_path):
    broker, conn, _ = _broker(tmp_path)
    seg, pid = 4101, 10001
    broker.register_segment(seg_id=seg, owner_pid=pid, color=TOXIC)
    assert conn.peek_entry(fd=seg, pid=pid) is not None

    # Attempt to re-map the TOXIC segment as PUBLIC (a scrub) -> violation.
    with pytest.raises(ProvenanceViolation) as ei:
        broker.remap_segment(seg_id=seg, pid=pid, claimed_color=PUBLIC)
    assert ei.value.seg_id == seg
    # The handle was force-closed (taint entry pruned) before the raise.
    assert conn.peek_entry(fd=seg, pid=pid) is None


def test_monotonic_remap_upward_is_allowed(tmp_path):
    broker, conn, _ = _broker(tmp_path)
    seg, pid = 4102, 11001
    broker.register_segment(seg_id=seg, owner_pid=pid, color=UNTRUSTED)
    eff = broker.remap_segment(seg_id=seg, pid=pid, claimed_color=TOXIC)
    assert eff == TOXIC
    assert conn.peek_entry(fd=seg, pid=pid)["max_color"] == TOXIC
