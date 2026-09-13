"""
backend/tests/services/test_sched_inference.py

Sprint 16 / Item J1 — SCHED_INFERENCE class tests.

Covers:
- ReqStatus + SchedError enum values match the kernel header.
- Init defaults; rejects max_requests <= 0.
- Submit: monotonic req_id, FULL rejection at capacity, INVAL on bad time.
- pick_next: EDF order (smallest ttft_deadline first), empty-queue raises.
- complete: SLO violation accounting (past deadline → MISSED_SLO);
  on-time → COMPLETED; latency observed in window.
- Percentile recomputation as window fills.
- gc_completed reclaims terminal slots back to FREE.
- Full submit → pick → complete → gc round-trip.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SI_PATH = _REPO_ROOT / "backend" / "services" / "sched_inference.py"

_spec = importlib.util.spec_from_file_location(
    "vos3_sched_inference_under_test", _SI_PATH
)
si = importlib.util.module_from_spec(_spec)
sys.modules["vos3_sched_inference_under_test"] = si
_spec.loader.exec_module(si)


# ---------------------------------------------------------------------------
# Enum parity
# ---------------------------------------------------------------------------


def test_req_status_values_match_kernel_header():
    assert int(si.ReqStatus.FREE) == 0
    assert int(si.ReqStatus.QUEUED) == 1
    assert int(si.ReqStatus.RUNNING) == 2
    assert int(si.ReqStatus.COMPLETED) == 3
    assert int(si.ReqStatus.MISSED_SLO) == 4


def test_sched_error_values_match_kernel_header():
    assert int(si.SchedError.OK) == 0
    assert int(si.SchedError.INVAL) == -1
    assert int(si.SchedError.FULL) == -2
    assert int(si.SchedError.NOTFOUND) == -3
    assert int(si.SchedError.EMPTY) == -4


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def test_init_defaults():
    q = si.SchedQueue(max_requests=8)
    assert q.max_requests == 8
    assert q.active == 0
    assert q.next_req_id == 1


def test_init_rejects_zero_max():
    with pytest.raises(si.SchedException) as ei:
        si.SchedQueue(max_requests=0)
    assert ei.value.code == si.SchedError.INVAL


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


def test_submit_returns_monotonic_ids():
    q = si.SchedQueue(max_requests=4)
    a = q.submit(arrival_ns=100, ttft_deadline_ns=200, tbt_deadline_ns=300)
    b = q.submit(arrival_ns=100, ttft_deadline_ns=200, tbt_deadline_ns=300)
    assert a == 1
    assert b == 2
    assert q.active == 2
    assert q.stats.total_admitted == 2


def test_submit_full_rejects():
    q = si.SchedQueue(max_requests=2)
    q.submit(arrival_ns=100, ttft_deadline_ns=200, tbt_deadline_ns=300)
    q.submit(arrival_ns=100, ttft_deadline_ns=200, tbt_deadline_ns=300)
    with pytest.raises(si.SchedException) as ei:
        q.submit(arrival_ns=100, ttft_deadline_ns=200, tbt_deadline_ns=300)
    assert ei.value.code == si.SchedError.FULL


def test_submit_ttft_before_arrival_rejects():
    q = si.SchedQueue(max_requests=4)
    with pytest.raises(si.SchedException) as ei:
        q.submit(arrival_ns=500, ttft_deadline_ns=100, tbt_deadline_ns=200)
    assert ei.value.code == si.SchedError.INVAL


def test_submit_negative_time_rejects():
    q = si.SchedQueue(max_requests=4)
    with pytest.raises(si.SchedException) as ei:
        q.submit(arrival_ns=-1, ttft_deadline_ns=100, tbt_deadline_ns=200)
    assert ei.value.code == si.SchedError.INVAL


# ---------------------------------------------------------------------------
# Pick — EDF
# ---------------------------------------------------------------------------


def test_pick_next_picks_earliest_ttft_deadline():
    q = si.SchedQueue(max_requests=4)
    q.submit(arrival_ns=0, ttft_deadline_ns=500, tbt_deadline_ns=600)
    b = q.submit(arrival_ns=0, ttft_deadline_ns=200, tbt_deadline_ns=400)  # earliest
    q.submit(arrival_ns=0, ttft_deadline_ns=300, tbt_deadline_ns=500)
    picked = q.pick_next()
    assert picked.req_id == b


def test_pick_next_flips_status_to_running():
    q = si.SchedQueue(max_requests=4)
    q.submit(arrival_ns=0, ttft_deadline_ns=200, tbt_deadline_ns=300)
    q.pick_next()
    # find the in-queue entry
    in_queue = [r for r in q.requests if r.req_id == 1][0]
    assert in_queue.status == si.ReqStatus.RUNNING


def test_pick_next_skips_running():
    """Once a request is RUNNING, pick_next should skip to the next QUEUED."""
    q = si.SchedQueue(max_requests=4)
    q.submit(arrival_ns=0, ttft_deadline_ns=100, tbt_deadline_ns=200)
    q.submit(arrival_ns=0, ttft_deadline_ns=200, tbt_deadline_ns=300)
    first = q.pick_next()
    second = q.pick_next()
    assert first.req_id == 1
    assert second.req_id == 2


def test_pick_next_empty_raises():
    q = si.SchedQueue(max_requests=4)
    with pytest.raises(si.SchedException) as ei:
        q.pick_next()
    assert ei.value.code == si.SchedError.EMPTY


# ---------------------------------------------------------------------------
# Complete — SLO accounting
# ---------------------------------------------------------------------------


def test_complete_on_time_status_completed():
    q = si.SchedQueue(max_requests=4)
    rid = q.submit(arrival_ns=0, ttft_deadline_ns=1000, tbt_deadline_ns=1500)
    q.pick_next()
    q.complete(rid, completed_ns=900)
    in_queue = [r for r in q.requests if r.req_id == rid][0]
    assert in_queue.status == si.ReqStatus.COMPLETED
    assert q.stats.slo_violations == 0
    assert q.stats.total_completed == 1


def test_complete_past_deadline_bumps_violations():
    q = si.SchedQueue(max_requests=4)
    rid = q.submit(arrival_ns=0, ttft_deadline_ns=500, tbt_deadline_ns=800)
    q.pick_next()
    q.complete(rid, completed_ns=999)
    in_queue = [r for r in q.requests if r.req_id == rid][0]
    assert in_queue.status == si.ReqStatus.MISSED_SLO
    assert q.stats.slo_violations == 1


def test_complete_unknown_req_id_raises_notfound():
    q = si.SchedQueue(max_requests=4)
    with pytest.raises(si.SchedException) as ei:
        q.complete(9999, completed_ns=100)
    assert ei.value.code == si.SchedError.NOTFOUND


# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------


def test_percentiles_grow_as_window_fills():
    q = si.SchedQueue(max_requests=4)
    # submit + complete 100 requests with linearly-increasing latency
    for i in range(100):
        rid = q.submit(arrival_ns=0, ttft_deadline_ns=10000, tbt_deadline_ns=20000)
        q.pick_next()
        q.complete(rid, completed_ns=(i + 1) * 10)
        q.gc_completed()
    s = q.get_stats()
    # P50 should be around 500ns, P95 around 950ns, P99 around 990ns
    assert s.p50_latency_ns > 0
    assert s.p95_latency_ns >= s.p50_latency_ns
    assert s.p99_latency_ns >= s.p95_latency_ns


# ---------------------------------------------------------------------------
# GC + round-trip
# ---------------------------------------------------------------------------


def test_gc_completed_reclaims_terminal_slots():
    q = si.SchedQueue(max_requests=2)
    rid = q.submit(arrival_ns=0, ttft_deadline_ns=100, tbt_deadline_ns=200)
    q.pick_next()
    q.complete(rid, completed_ns=50)
    reclaimed = q.gc_completed()
    assert reclaimed == 1
    # Slot is FREE again; can submit a new request even though max=2 and
    # active just had one terminal entry.
    new_rid = q.submit(arrival_ns=200, ttft_deadline_ns=400, tbt_deadline_ns=500)
    assert new_rid != rid
    # active count should be 1 (the new request).
    assert q.active == 1


def test_full_round_trip():
    q = si.SchedQueue(max_requests=8)
    # 4 requests with different deadlines.
    deadlines = [(0, 500), (0, 200), (0, 800), (0, 300)]  # (arrival, ttft)
    rids = []
    for arr, ttft in deadlines:
        rids.append(
            q.submit(arrival_ns=arr, ttft_deadline_ns=ttft, tbt_deadline_ns=ttft + 100)
        )
    # Pick all four — should come out in EDF order (200, 300, 500, 800).
    picked_order = []
    for _ in range(4):
        picked_order.append(q.pick_next().ttft_deadline_ns)
    assert picked_order == [200, 300, 500, 800]
    # Complete them, one on-time + one late.
    q.complete(rids[1], completed_ns=150)  # on-time (deadline 200)
    q.complete(rids[3], completed_ns=400)  # late (deadline 300)
    q.complete(rids[0], completed_ns=400)  # on-time (deadline 500)
    q.complete(rids[2], completed_ns=750)  # on-time (deadline 800)
    s = q.get_stats()
    assert s.total_admitted == 4
    assert s.total_completed == 4
    assert s.slo_violations == 1
    # GC.
    assert q.gc_completed() == 4
    assert q.active == 0
