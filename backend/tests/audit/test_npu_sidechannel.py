"""
Phase 29 — NPU telemetry side-channel (Gap G8, telemetry half).
===============================================================

Validates the read-only NPU telemetry dispatcher:

  - Zero-Stall: under backpressure (ring full) and under lock contention, the
    producer DROPS rather than blocks — record() returns False fast and a tight
    record loop stays well under a hard latency ceiling (no interference with
    NPU processing speed).
  - Correct streaming: utilization stats + per-taint-color frequency counts reach
    the audit sink exactly.
  - Fail-silent: a raising sink is swallowed (telemetry never breaks the caller).
  - INV-1 honesty anchor: the module does NOT call the mm/-resident
    vos3_npu_affinity_pin, and no non-test, non-mm kernel caller wires it either —
    the productive pinning dispatcher stays INV-1-blocked (G8 NOT closed).

Anti-gaming (charter §II): fixed-value asserts on dropped/accepted counts and the
exact per-color histogram; latency asserts use a generous ceiling so they verify
"non-blocking" without being flaky. No mocks of the dispatcher itself.

Run: .venv_p312/bin/python -m pytest tests/audit/test_npu_sidechannel.py -n 0
"""

from __future__ import annotations

import os
import threading
import time

from services.npu_telemetry import (
    NPUTelemetryDispatcher,
    record_npu_sample,
    _reset_singleton_for_tests,
)

PUBLIC, UNTRUSTED, SECRET, TOXIC = 0, 1, 2, 3


# ---------------------------------------------------------------------------
# Zero-Stall: backpressure drops, never blocks.
# ---------------------------------------------------------------------------


def test_backpressure_drops_not_blocks():
    d = NPUTelemetryDispatcher(capacity=8)
    # Fill to capacity.
    for i in range(8):
        assert d.record(npu_id=0, utilization_pct=10.0, taint_color=PUBLIC) is True
    # Every further record is dropped (ring full) — and returns immediately.
    for i in range(1000):
        assert d.record(npu_id=0, utilization_pct=10.0, taint_color=PUBLIC) is False
    assert d.stats.accepted == 8
    assert d.stats.dropped_full == 1000
    assert d.stats.dropped_contention == 0


def test_record_is_nonblocking_under_lock_contention():
    # While another thread holds the internal lock (simulating a drain), record()
    # must DROP immediately (zero-stall), not wait for the lock.
    d = NPUTelemetryDispatcher(capacity=1024)
    held = threading.Event()
    release = threading.Event()

    def _hog():
        with d._lock:  # noqa: SLF001 — deliberately model a long drain holding it
            held.set()
            release.wait(timeout=2.0)

    t = threading.Thread(target=_hog)
    t.start()
    assert held.wait(timeout=2.0)
    start = time.monotonic()
    accepted = d.record(npu_id=1, utilization_pct=50.0, taint_color=UNTRUSTED)
    elapsed = time.monotonic() - start
    release.set()
    t.join(timeout=2.0)

    assert accepted is False  # dropped due to contention
    assert d.stats.dropped_contention == 1
    assert elapsed < 0.25  # returned fast — did NOT block on the held lock


def test_zero_stall_high_volume_latency_ceiling():
    # 50k records must complete well under a generous ceiling — proves the
    # producer path never stalls the (simulated) NPU pipeline.
    d = NPUTelemetryDispatcher(capacity=2048)
    start = time.monotonic()
    for i in range(50_000):
        d.record(npu_id=i % 4, utilization_pct=float(i % 100), taint_color=i % 4)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0  # ~100k+ records/s floor; non-blocking
    # The ring is bounded: accepted never exceeds capacity between drains.
    assert d.stats.accepted <= d.capacity
    assert d.stats.dropped_full > 0


# ---------------------------------------------------------------------------
# Correct streaming to the audit registry.
# ---------------------------------------------------------------------------


def test_drain_streams_correct_utilization_and_color_histogram():
    captured = {}
    d = NPUTelemetryDispatcher(capacity=64, audit_sink=captured.update)
    # 2 PUBLIC, 1 UNTRUSTED, 3 SECRET, 1 TOXIC; utilizations 10..70.
    samples = [
        (10.0, PUBLIC),
        (20.0, PUBLIC),
        (30.0, UNTRUSTED),
        (40.0, SECRET),
        (50.0, SECRET),
        (60.0, SECRET),
        (70.0, TOXIC),
    ]
    for util, color in samples:
        assert d.record(npu_id=2, utilization_pct=util, taint_color=color) is True

    summary = d.drain()
    assert summary["samples"] == 7
    assert summary["utilization_max"] == 70.0
    assert abs(summary["utilization_mean"] - (280.0 / 7)) < 1e-9
    assert summary["taint_color_freq"] == {
        "PUBLIC": 2,
        "UNTRUSTED": 1,
        "SECRET": 3,
        "TOXIC": 1,
    }
    # The same summary was exported to the audit sink.
    assert captured["samples"] == 7
    assert captured["taint_color_freq"]["SECRET"] == 3
    # Draining empties the ring.
    assert d.drain()["samples"] == 0


def test_drain_empty_is_safe_zeroed():
    d = NPUTelemetryDispatcher(capacity=16)
    s = d.drain()
    assert s["samples"] == 0
    assert s["utilization_mean"] == 0.0
    assert s["taint_color_freq"] == {
        "PUBLIC": 0,
        "UNTRUSTED": 0,
        "SECRET": 0,
        "TOXIC": 0,
    }


# ---------------------------------------------------------------------------
# Fail-silent.
# ---------------------------------------------------------------------------


def test_failsilent_sink_exception_is_swallowed():
    def _bad_sink(_summary):
        raise RuntimeError("audit registry unreachable")

    d = NPUTelemetryDispatcher(capacity=16, audit_sink=_bad_sink)
    d.record(npu_id=0, utilization_pct=5.0, taint_color=PUBLIC)
    # drain must NOT propagate the sink error.
    summary = d.drain()
    assert summary["samples"] == 1
    assert d.stats.sink_errors == 1


def test_record_never_raises_on_bad_input():
    d = NPUTelemetryDispatcher(capacity=4)
    assert d.record(npu_id="x", utilization_pct="y", taint_color=None) is False  # type: ignore[arg-type]


def test_singleton_producer_convenience():
    _reset_singleton_for_tests()
    try:
        assert (
            record_npu_sample(npu_id=0, utilization_pct=1.0, taint_color=PUBLIC) is True
        )
    finally:
        _reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# INV-1 honesty anchor: the telemetry side-channel did NOT wire the mm/ pin.
# ---------------------------------------------------------------------------


def test_module_does_not_call_inv1_blocked_pin():
    import services.npu_telemetry as mod

    src = open(mod.__file__, encoding="utf-8").read()
    # The module may MENTION the symbol in its honest-scope docstring, but must
    # never CALL it. Assert there is no call-shaped occurrence.
    assert "vos3_npu_affinity_pin(" not in src


def test_no_nontest_nonmm_kernel_caller_of_pin_exists():
    # G8 / AG-2: the productive pinning dispatcher stays INV-1-blocked. There must
    # be NO caller of vos3_npu_affinity_pin( outside kernel/src/mm and tests. If
    # this ever fails, a real (or stubbed) caller was added — investigate, do not
    # silence.
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    ksrc = os.path.join(repo_root, "kernel", "src")
    offenders = []
    for dirpath, _dirs, files in os.walk(ksrc):
        if os.sep + "mm" in dirpath:
            continue  # the declaration + its home live in mm/ (INV-1) — allowed
        if os.sep + "tests" in dirpath:
            continue  # test callers are allowed/expected (AG-2 asserts NON-test)
        for fn in files:
            if not fn.endswith((".c", ".h")) or fn.startswith("test_"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if "vos3_npu_affinity_pin(" in txt:
                offenders.append(os.path.relpath(p, repo_root))
    assert offenders == [], f"unexpected pin caller(s) outside mm/: {offenders}"
