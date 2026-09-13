"""
backend/tests/services/test_rapl_batch_bridge.py

Sprint 16 / Item J3 — RAPL batch-aware power-cap bridge tests.

Covers the cap-decision policy:
- No in-flight → APPLY (no benefit from deferral).
- Apply-threshold thermal → APPLY (safety).
- Shrink-threshold thermal → SHRINK_NEXT.
- Old in-flight (> max_defer_ms) → APPLY (don't starve cap).
- In-flight + thermal OK + within defer window → DEFER.
- Statistics counters track all four decision kinds.
- Thread-safety: parallel register + decision don't corrupt state.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RB_PATH = _REPO_ROOT / "backend" / "services" / "rapl_batch_bridge.py"
_spec = importlib.util.spec_from_file_location("vos3_rapl_batch_under_test", _RB_PATH)
rb = importlib.util.module_from_spec(_spec)
sys.modules["vos3_rapl_batch_under_test"] = rb
_spec.loader.exec_module(rb)


def _new_bridge(max_defer_ms: float = 50.0) -> "rb.RaplBatchBridge":
    return rb.RaplBatchBridge(
        max_defer_ms=max_defer_ms,
        shrink_threshold_temp_c=90.0,
        apply_threshold_temp_c=95.0,
    )


# ---------------------------------------------------------------------------
# No in-flight
# ---------------------------------------------------------------------------


def test_no_inflight_applies_cap():
    bridge = _new_bridge()
    bridge.update_thermal(80.0, 100.0)
    decision = bridge.should_apply_cap(current_cap_w=120.0)
    assert decision.kind == rb.CapDecisionKind.APPLY
    assert decision.reason == "no_inflight_batches"
    assert decision.in_flight_batches == 0


# ---------------------------------------------------------------------------
# Thermal thresholds
# ---------------------------------------------------------------------------


def test_apply_threshold_overrides_defer():
    bridge = _new_bridge()
    bridge.register_request("req-1", batch_size=4)
    bridge.update_thermal(96.0, 130.0)  # >= apply_threshold
    decision = bridge.should_apply_cap(current_cap_w=125.0)
    assert decision.kind == rb.CapDecisionKind.APPLY
    assert decision.reason == "thermal_at_apply_threshold"


def test_shrink_threshold_returns_shrink_next():
    bridge = _new_bridge()
    bridge.register_request("req-1", batch_size=8)
    bridge.update_thermal(92.0, 120.0)  # >= shrink, < apply
    decision = bridge.should_apply_cap(current_cap_w=125.0)
    assert decision.kind == rb.CapDecisionKind.SHRINK_NEXT
    assert decision.reason == "thermal_at_shrink_threshold"


# ---------------------------------------------------------------------------
# Defer + max-defer escape valve
# ---------------------------------------------------------------------------


def test_inflight_within_defer_window_defers():
    bridge = _new_bridge(max_defer_ms=100.0)
    bridge.register_request("req-1", batch_size=4)
    bridge.update_thermal(75.0, 110.0)
    decision = bridge.should_apply_cap(current_cap_w=125.0)
    assert decision.kind == rb.CapDecisionKind.DEFER
    assert decision.in_flight_batches == 1


def test_inflight_past_max_defer_applies_cap():
    """If the in-flight batch has been running > max_defer_ms,
    the bridge stops deferring and lets the cap apply."""
    bridge = _new_bridge(max_defer_ms=5.0)  # 5 ms max-defer
    bridge.register_request("req-1", batch_size=4)
    time.sleep(0.020)  # 20 ms — past max_defer
    bridge.update_thermal(75.0, 110.0)
    decision = bridge.should_apply_cap(current_cap_w=125.0)
    assert decision.kind == rb.CapDecisionKind.APPLY
    assert decision.reason == "max_defer_exceeded"
    assert decision.deferred_ms > 5.0


# ---------------------------------------------------------------------------
# Lifecycle: register + complete
# ---------------------------------------------------------------------------


def test_register_and_complete_lifecycle():
    bridge = _new_bridge()
    bridge.register_request("req-1", batch_size=2)
    bridge.register_request("req-2", batch_size=3)
    snap1 = bridge.snapshot()
    assert snap1.in_flight_requests == 2
    bridge.complete_request("req-1")
    snap2 = bridge.snapshot()
    assert snap2.in_flight_requests == 1
    bridge.complete_request("req-2")
    snap3 = bridge.snapshot()
    assert snap3.in_flight_requests == 0


def test_complete_unknown_id_is_noop():
    bridge = _new_bridge()
    # Should not raise.
    bridge.complete_request("never-registered")
    assert bridge.snapshot().in_flight_requests == 0


def test_register_invalid_batch_size_defaults_to_one():
    """Telemetry might pass batch_size=0 (e.g. missing attribute);
    safe default is 1 (treat as worst-case-deferral)."""
    bridge = _new_bridge()
    bridge.register_request("req-1", batch_size=0)
    # No raise — registration accepted.
    assert bridge.snapshot().in_flight_requests == 1


def test_register_empty_req_id_raises():
    bridge = _new_bridge()
    with pytest.raises(ValueError):
        bridge.register_request("", batch_size=1)


# ---------------------------------------------------------------------------
# Stats counters
# ---------------------------------------------------------------------------


def test_stats_track_decision_kinds():
    bridge = _new_bridge(max_defer_ms=1000.0)
    # 1 APPLY (no inflight)
    bridge.update_thermal(80.0, 100.0)
    bridge.should_apply_cap(current_cap_w=125.0)
    # Register a request, drive 1 DEFER, 1 SHRINK_NEXT, 1 APPLY-via-thermal
    bridge.register_request("req-1", batch_size=4)
    bridge.update_thermal(80.0, 100.0)
    bridge.should_apply_cap(current_cap_w=125.0)  # DEFER
    bridge.update_thermal(92.0, 120.0)
    bridge.should_apply_cap(current_cap_w=125.0)  # SHRINK_NEXT
    bridge.update_thermal(96.0, 130.0)
    bridge.should_apply_cap(current_cap_w=125.0)  # APPLY (thermal)

    snap = bridge.snapshot()
    assert snap.total_cap_decisions == 4
    assert snap.deferred_cap_count == 1
    assert snap.shrink_next_count == 1
    assert snap.apply_count == 2


def test_snapshot_returns_copy_not_reference():
    bridge = _new_bridge()
    bridge.update_thermal(80.0, 100.0)
    snap1 = bridge.snapshot()
    bridge.update_thermal(95.0, 130.0)
    snap2 = bridge.snapshot()
    # snap1 unchanged.
    assert snap1.last_package_temp_c == 80.0
    assert snap2.last_package_temp_c == 95.0


# ---------------------------------------------------------------------------
# Thread-safety smoke
# ---------------------------------------------------------------------------


def test_concurrent_register_and_decide_no_corruption():
    bridge = _new_bridge(max_defer_ms=1000.0)
    bridge.update_thermal(80.0, 100.0)
    errors = []

    def register_loop():
        try:
            for i in range(100):
                bridge.register_request(f"req-{i}", batch_size=1)
                bridge.complete_request(f"req-{i}")
        except Exception as exc:
            errors.append(exc)

    def decide_loop():
        try:
            for _ in range(100):
                bridge.should_apply_cap(current_cap_w=120.0)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=register_loop),
        threading.Thread(target=decide_loop),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        if t.is_alive():
            pytest.fail("thread did not terminate — possible deadlock")
    assert not errors, f"unexpected exceptions: {errors}"
    snap = bridge.snapshot()
    assert snap.total_cap_decisions == 100
    assert snap.in_flight_requests == 0


# ---------------------------------------------------------------------------
# Env-var configuration
# ---------------------------------------------------------------------------


def test_env_var_controls_max_defer_ms(monkeypatch):
    monkeypatch.setenv("VOS3_RAPL_MAX_DEFER_MS", "200")
    # Re-load the module with the env var set — using a FRESH spec since
    # importlib.reload doesn't work on dynamically-loaded specs.
    fresh_spec = importlib.util.spec_from_file_location(
        "vos3_rapl_batch_envtest", _RB_PATH
    )
    fresh = importlib.util.module_from_spec(fresh_spec)
    sys.modules["vos3_rapl_batch_envtest"] = fresh
    fresh_spec.loader.exec_module(fresh)
    bridge = fresh.RaplBatchBridge()
    assert bridge.max_defer_ms == 200.0
