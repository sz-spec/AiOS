"""
backend/tests/services/test_page_coloring_governor.py

Sprint 16 / Item A4 — TLB side-channel mitigation policy tests.

Covers:
- WorkloadHint enum values match kernel A1 enum.
- Init validates color_count + max_per_color_rss.
- allocate_colors:
    * MODEL_WEIGHTS → exclusive grant (no other workload can reuse).
    * KV_CACHE → tenant-grouped (same owner_tag gets same colors).
    * SCRATCH → shared pool, no isolation.
- Rejection: invalid hint type, zero count, count > capacity, empty owner.
- Capacity exhaustion: MODEL_WEIGHTS request beyond available exclusive
  colors raises RuntimeError + bumps allocation_failures stat.
- release_colors decrements refcount + clears exclusive_owner.
- check_isolation: ISOLATED for disjoint sets, FULLY_SHARED for identical,
  OVERLAPPING for partial — overlap counter bumped.
- color_refcount returns current count; raises IndexError out-of-range.
- Stats counters: total_allocations, exclusive_grants, tenant_grouped_grants,
  shared_grants, allocation_failures, overlap_warnings_issued.
- Same KV_CACHE owner gets identical color set across multiple allocate calls
  (tenant-group dedup).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PCG_PATH = _REPO_ROOT / "backend" / "services" / "page_coloring_governor.py"
_spec = importlib.util.spec_from_file_location("vos3_pcg_under_test", _PCG_PATH)
pcg = importlib.util.module_from_spec(_spec)
sys.modules["vos3_pcg_under_test"] = pcg
_spec.loader.exec_module(pcg)


# ---------------------------------------------------------------------------
# Enum + init
# ---------------------------------------------------------------------------


def test_workload_hint_values_match_kernel_a1():
    assert int(pcg.WorkloadHint.SCRATCH) == 0
    assert int(pcg.WorkloadHint.MODEL_WEIGHTS) == 1
    assert int(pcg.WorkloadHint.KV_CACHE) == 2


def test_init_rejects_zero_color_count():
    with pytest.raises(ValueError):
        pcg.PageColoringGovernor(color_count=0)


def test_init_rejects_zero_max_per_color_rss():
    with pytest.raises(ValueError):
        pcg.PageColoringGovernor(color_count=16, max_per_color_rss=0)


# ---------------------------------------------------------------------------
# MODEL_WEIGHTS — exclusive grant
# ---------------------------------------------------------------------------


def test_model_weights_exclusive_grant():
    gov = pcg.PageColoringGovernor(color_count=16)
    cs = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="llama-3-70b"
    )
    assert len(cs.colors) == 4
    assert cs.workload_hint == pcg.WorkloadHint.MODEL_WEIGHTS
    assert cs.owner_tag == "llama-3-70b"
    # Each color should be exclusively owned.
    for c in cs.colors:
        assert gov.color_refcount(c) == 1


def test_model_weights_second_request_uses_different_colors():
    gov = pcg.PageColoringGovernor(color_count=16)
    cs1 = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-A"
    )
    cs2 = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-B"
    )
    # Different owners — disjoint exclusive grants.
    assert not (cs1.colors & cs2.colors)


def test_model_weights_capacity_exhausted_raises():
    gov = pcg.PageColoringGovernor(color_count=4)
    gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-A"
    )
    # Only 4 colors total, all exclusive — next request fails.
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        gov.allocate_colors(
            pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=1, owner_tag="model-B"
        )
    stats = gov.snapshot_stats()
    assert stats.allocation_failures == 1


# ---------------------------------------------------------------------------
# KV_CACHE — tenant-grouped
# ---------------------------------------------------------------------------


def test_kv_cache_same_owner_gets_same_colors():
    gov = pcg.PageColoringGovernor(color_count=16)
    cs1 = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-7"
    )
    cs2 = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-7"
    )
    # Same owner → same colors (tenant-grouped dedup).
    assert cs1.colors == cs2.colors


def test_kv_cache_different_owners_get_different_colors():
    gov = pcg.PageColoringGovernor(color_count=16)
    cs1 = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-1"
    )
    cs2 = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-2"
    )
    # Different owners → at least one different color (sets are picked
    # to spread; some overlap allowed under high contention).
    assert cs1.colors != cs2.colors


def test_kv_cache_signals_randomize_on_switch():
    gov = pcg.PageColoringGovernor(color_count=16)
    cs = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-1"
    )
    assert cs.randomize_on_next_switch is True


# ---------------------------------------------------------------------------
# SCRATCH — shared pool
# ---------------------------------------------------------------------------


def test_scratch_no_exclusivity():
    gov = pcg.PageColoringGovernor(color_count=8)
    cs1 = gov.allocate_colors(
        pcg.WorkloadHint.SCRATCH, requested_count=4, owner_tag="job-A"
    )
    gov.allocate_colors(pcg.WorkloadHint.SCRATCH, requested_count=4, owner_tag="job-B")
    # SCRATCH allows overlap.
    assert cs1.workload_hint == pcg.WorkloadHint.SCRATCH


def test_scratch_does_not_set_randomize():
    gov = pcg.PageColoringGovernor(color_count=8)
    cs = gov.allocate_colors(
        pcg.WorkloadHint.SCRATCH, requested_count=2, owner_tag="job-A"
    )
    assert cs.randomize_on_next_switch is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_allocate_rejects_bad_hint_type():
    gov = pcg.PageColoringGovernor(color_count=8)
    with pytest.raises(TypeError):
        gov.allocate_colors("invalid", requested_count=1, owner_tag="x")  # type: ignore[arg-type]


def test_allocate_rejects_zero_count():
    gov = pcg.PageColoringGovernor(color_count=8)
    with pytest.raises(ValueError):
        gov.allocate_colors(pcg.WorkloadHint.SCRATCH, requested_count=0, owner_tag="x")


def test_allocate_rejects_count_over_capacity():
    gov = pcg.PageColoringGovernor(color_count=8)
    with pytest.raises(ValueError):
        gov.allocate_colors(pcg.WorkloadHint.SCRATCH, requested_count=9, owner_tag="x")


def test_allocate_rejects_empty_owner_tag():
    gov = pcg.PageColoringGovernor(color_count=8)
    with pytest.raises(ValueError):
        gov.allocate_colors(pcg.WorkloadHint.SCRATCH, requested_count=1, owner_tag="")


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def test_release_decrements_refcount():
    gov = pcg.PageColoringGovernor(color_count=8)
    cs = gov.allocate_colors(
        pcg.WorkloadHint.SCRATCH, requested_count=2, owner_tag="job-A"
    )
    for c in cs.colors:
        assert gov.color_refcount(c) >= 1
    gov.release_colors(cs)
    for c in cs.colors:
        assert gov.color_refcount(c) == 0


def test_release_clears_exclusive_owner():
    """After releasing MODEL_WEIGHTS colors, a new MODEL_WEIGHTS request
    from a different owner can re-claim them."""
    gov = pcg.PageColoringGovernor(color_count=4)
    cs = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-A"
    )
    gov.release_colors(cs)
    cs2 = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-B"
    )
    assert len(cs2.colors) == 4


def test_release_rejects_non_colorset():
    gov = pcg.PageColoringGovernor(color_count=8)
    with pytest.raises(TypeError):
        gov.release_colors("not-a-color-set")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Isolation check
# ---------------------------------------------------------------------------


def test_check_isolation_isolated():
    gov = pcg.PageColoringGovernor(color_count=16)
    a = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-A"
    )
    b = gov.allocate_colors(
        pcg.WorkloadHint.MODEL_WEIGHTS, requested_count=4, owner_tag="model-B"
    )
    decision = gov.check_isolation(a, b)
    assert decision.kind == pcg.IsolationKind.ISOLATED
    assert decision.shared_colors == ()


def test_check_isolation_fully_shared():
    gov = pcg.PageColoringGovernor(color_count=16)
    a = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-1"
    )
    b = gov.allocate_colors(
        pcg.WorkloadHint.KV_CACHE, requested_count=4, owner_tag="agent-1"
    )
    decision = gov.check_isolation(a, b)
    assert decision.kind == pcg.IsolationKind.FULLY_SHARED
    assert len(decision.shared_colors) == 4


def test_check_isolation_overlapping_bumps_warning():
    """Manually construct overlapping ColorSets to exercise the
    OVERLAPPING path + the overlap_warnings_issued stat."""
    gov = pcg.PageColoringGovernor(color_count=16)
    a = pcg.ColorSet(
        set_id=1,
        colors=frozenset({1, 2, 3}),
        workload_hint=pcg.WorkloadHint.SCRATCH,
        owner_tag="x",
    )
    b = pcg.ColorSet(
        set_id=2,
        colors=frozenset({3, 4, 5}),
        workload_hint=pcg.WorkloadHint.SCRATCH,
        owner_tag="y",
    )
    decision = gov.check_isolation(a, b)
    assert decision.kind == pcg.IsolationKind.OVERLAPPING
    assert decision.shared_colors == (3,)
    stats = gov.snapshot_stats()
    assert stats.overlap_warnings_issued == 1


def test_check_isolation_rejects_non_colorset():
    gov = pcg.PageColoringGovernor(color_count=8)
    a = gov.allocate_colors(pcg.WorkloadHint.SCRATCH, requested_count=1, owner_tag="x")
    with pytest.raises(TypeError):
        gov.check_isolation(a, "not-a-color-set")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Stats + ref count
# ---------------------------------------------------------------------------


def test_color_refcount_out_of_range_raises():
    gov = pcg.PageColoringGovernor(color_count=8)
    with pytest.raises(IndexError):
        gov.color_refcount(100)
    with pytest.raises(IndexError):
        gov.color_refcount(-1)


def test_stats_counters():
    gov = pcg.PageColoringGovernor(color_count=16)
    gov.allocate_colors(pcg.WorkloadHint.MODEL_WEIGHTS, 2, "model-A")
    gov.allocate_colors(pcg.WorkloadHint.KV_CACHE, 2, "agent-1")
    gov.allocate_colors(pcg.WorkloadHint.SCRATCH, 2, "job-X")
    stats = gov.snapshot_stats()
    assert stats.total_allocations == 3
    assert stats.exclusive_grants == 1
    assert stats.tenant_grouped_grants == 1
    assert stats.shared_grants == 1
    assert stats.color_count == 16


def test_snapshot_stats_returns_copy():
    gov = pcg.PageColoringGovernor(color_count=8)
    s1 = gov.snapshot_stats()
    gov.allocate_colors(pcg.WorkloadHint.SCRATCH, 2, "x")
    s2 = gov.snapshot_stats()
    assert s1.total_allocations == 0
    assert s2.total_allocations == 1
