"""
B3 Phase-20 race-under-load stress suite (TEST_PLAN_300 §B3 / Phase 20).
=========================================================================

SCOPE — read before trusting the numbers:
  * This exercises the **MOCK** ``KernelGateConnector`` (force_mock=True): its
    thread-safety under 1000+ concurrent mark/push/evict/decide ops, and that
    the per-fd decision is CONSISTENT under concurrent map mutation — i.e. a
    marked-no-entry fd ALWAYS denies and an unmarked fd ALWAYS allows, with no
    interleaving that flips a decision.
  * It does NOT exercise the live-kernel TOCTOU (no kernel here). The real
    write()-vs-mark race is verified empirically on a live 6.12 BPF-LSM kernel
    in infra/runners (Docker, privileged) — see the ledger Phase-20 entry. A
    Python stress test can only show "no inconsistency observed over N"; it is
    not a mathematical proof of race-freedom.

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_b3_stress_race.py -n 0
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from security import kernel_gate_connector as kgc
from security.kernel_gate_connector import (
    DecisionKind,
    KernelGateConnector,
    SinkKind,
    TaintLabel,
)

_N = 1200  # concurrent iterations per test
_WORKERS = 16


def _conn() -> KernelGateConnector:
    return KernelGateConnector(force_mock=True)


def _arg(fd: int, colors: bytes, *, claimed=int(TaintLabel.SECRET)) -> bytes:
    return kgc._pack_mark_push_arg(
        fd=fd,
        colors=colors,
        sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=claimed,
    )


def _spray(fn, n=_N, workers=_WORKERS):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [f.result() for f in [ex.submit(fn, i) for i in range(n)]]


# ---------------------------------------------------------------------------
# 1. A marked-no-entry fd ALWAYS denies, under heavy concurrent simulate load.
# ---------------------------------------------------------------------------
def test_marked_no_entry_fd_always_denies_under_load():
    c = _conn()
    c.mark_push(_arg(5, bytes([2])), pid=10)
    c.gc_entry(pid=10, fd=5)  # marked, entry GC'd -> must fail closed every time
    results = _spray(lambda _i: c.simulate_write(fd=5, pid=10, length=1).decision)
    denies = sum(r == DecisionKind.DENY for r in results)
    assert denies == _N, f"LEAK: {_N - denies}/{_N} marked writes were ALLOWED"


# ---------------------------------------------------------------------------
# 2. An unmarked fd ALWAYS allows (no false-positive denial under load).
# ---------------------------------------------------------------------------
def test_unmarked_fd_always_allows_under_load():
    c = _conn()
    results = _spray(lambda _i: c.simulate_write(fd=42, pid=10, length=1).decision)
    assert all(r == DecisionKind.ALLOW for r in results)


# ---------------------------------------------------------------------------
# 3. Per-fd isolation under interleaved load: marked fd denies, sibling allows,
#    simultaneously, with zero cross-fd bleed.
# ---------------------------------------------------------------------------
def test_per_fd_isolation_under_concurrent_load():
    c = _conn()
    c.mark_push(_arg(7, bytes([2])), pid=10)
    c.gc_entry(pid=10, fd=7)  # fd 7 marked-no-entry -> deny; fd 8 untouched -> allow

    def one(i):
        if i % 2 == 0:
            return ("m", c.simulate_write(fd=7, pid=10).decision)
        return ("u", c.simulate_write(fd=8, pid=10).decision)

    for kind, dec in _spray(one):
        if kind == "m":
            assert dec == DecisionKind.DENY, "marked fd 7 leaked an ALLOW"
        else:
            assert dec == DecisionKind.ALLOW, "unmarked fd 8 falsely DENIED"


# ---------------------------------------------------------------------------
# 4. Concurrent mark_push / gc_entry / evict / simulate never corrupts state
#    or raises — the connector lock must serialize all mutations.
# ---------------------------------------------------------------------------
def test_concurrent_mutation_and_decide_is_consistent():
    c = _conn()
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def marker():
        barrier.wait()
        try:
            for fd in range(_N):
                c.mark_push(_arg(fd % 64, bytes([2])), pid=99)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def gcer():
        barrier.wait()
        try:
            for fd in range(_N):
                c.gc_entry(pid=99, fd=fd % 64)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def evicter():
        barrier.wait()
        try:
            for fd in range(_N):
                c.evict(pid=99, fd=fd % 64)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def decider():
        barrier.wait()
        try:
            for fd in range(_N):
                d = c.simulate_write(fd=fd % 64, pid=99).decision
                # Decision must always be a valid enum value — never garbage.
                assert d in (DecisionKind.ALLOW, DecisionKind.DENY)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=t) for t in (marker, gcer, evicter, decider)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, f"concurrency errors: {errors[:3]}"


# ---------------------------------------------------------------------------
# 5. Concurrent identical mark_push converges to a single consistent entry
#    (atomic-commit invariant: marked AND entry present, never half).
# ---------------------------------------------------------------------------
def test_concurrent_identical_mark_push_converges():
    c = _conn()
    arg = _arg(3, bytes([0, 0, 2, 0]))  # real max SECRET via re-derive
    _spray(lambda _i: c.mark_push(arg, pid=10))
    assert c.is_marked(pid=10, fd=3)
    entry = c.peek_entry(pid=10, fd=3)
    assert entry is not None
    assert entry["max_color"] == int(TaintLabel.SECRET)  # re-derived, not claimed
    assert c.simulate_write(fd=3, pid=10).decision == DecisionKind.DENY


# ---------------------------------------------------------------------------
# 6/7. Phase-20/21 flag-gate: the live LSM egress gate is OFF by default
#      (dev/CI unaffected) and only activates under VOS3_ENABLE_LIVE_LSM_GATE.
# ---------------------------------------------------------------------------
def test_egress_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VOS3_ENABLE_LIVE_LSM_GATE", raising=False)
    assert kgc.egress_gate_enabled() is False
    assert kgc.init_egress_gate_if_enabled() is None


def test_egress_gate_flag_activates_mock_on_dev(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_LSM_GATE", "1")
    assert kgc.egress_gate_enabled() is True
    gate = kgc.init_egress_gate_if_enabled()
    assert gate is not None
    # On a dev host the gate is MOCK (no kernel) — enabling the flag must NOT
    # claim live enforcement; it just records.
    assert gate.mode == kgc.GateMode.MOCK
