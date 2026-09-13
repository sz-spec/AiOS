"""
Phase 2 · KIM (Kernel Inference Manager) — HugePage exhaustion + graceful refusal.

Honest-scope ceiling
--------------------
Actual HugePage exhaustion requires kernel runtime (PMM allocator
state). From host pytest we model the **allocator state machine**:

  * a fixed-capacity HugePage pool with L3-cache-coloring rotation,
  * SLOT_START requests pick a free HugePage with the appropriate color,
  * SLOT_FINISH returns the page to the pool,
  * when the pool is exhausted, SLOT_START returns an explicit error
    rather than panic / null-deref.

The test exercises this state machine across 25 scenarios — saturating
the pool with sequential allocations, mixing alloc+free, verifying
color rotation, etc.
"""

from __future__ import annotations

import pytest


class _HugePagePool:
    """Pure-Python KIM allocator model."""

    POOL_SIZE = 8  # 8 HugePages (matches CLAUDE.md "8 model slots")
    L3_COLORS = 4  # 4-way L3 cache coloring

    class Exhausted(RuntimeError):
        pass

    def __init__(self):
        self._free = list(range(self.POOL_SIZE))
        self._allocated: dict[int, int] = {}  # slot_id → color

    def alloc(self, slot_id: int) -> int:
        """Return the L3 color assigned to this slot."""
        if slot_id in self._allocated:
            raise ValueError(f"slot {slot_id} already allocated")
        if not self._free:
            raise self.Exhausted("HugePage pool exhausted")
        page = self._free.pop(0)
        color = page % self.L3_COLORS
        self._allocated[slot_id] = color
        return color

    def free(self, slot_id: int) -> None:
        if slot_id not in self._allocated:
            raise ValueError(f"slot {slot_id} not allocated")
        self._allocated.pop(slot_id)
        # Return any free page; we lost the exact id but for the model
        # any free page suffices.
        self._free.append(len(self._free) + len(self._allocated))

    def free_count(self) -> int:
        return len(self._free)


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------


def test_alloc_returns_color_in_range():
    p = _HugePagePool()
    color = p.alloc(0)
    assert 0 <= color < _HugePagePool.L3_COLORS


def test_free_reduces_allocated_count():
    p = _HugePagePool()
    p.alloc(0)
    p.alloc(1)
    p.free(0)
    assert 1 in p._allocated
    assert 0 not in p._allocated


# ---------------------------------------------------------------------------
# Exhaustion — POOL_SIZE+1 allocs must raise, NOT panic / silent OK
# ---------------------------------------------------------------------------


def test_exhaustion_raises():
    p = _HugePagePool()
    for i in range(_HugePagePool.POOL_SIZE):
        p.alloc(i)
    with pytest.raises(_HugePagePool.Exhausted):
        p.alloc(_HugePagePool.POOL_SIZE)


@pytest.mark.parametrize("n_extra", [1, 2, 5, 10, 100])
def test_overflow_attempts_all_raise(n_extra):
    p = _HugePagePool()
    for i in range(_HugePagePool.POOL_SIZE):
        p.alloc(i)
    for j in range(n_extra):
        with pytest.raises(_HugePagePool.Exhausted):
            p.alloc(_HugePagePool.POOL_SIZE + j)


# ---------------------------------------------------------------------------
# Mixed alloc/free — freeing recycles capacity
# ---------------------------------------------------------------------------


def test_alloc_after_free_succeeds():
    p = _HugePagePool()
    for i in range(_HugePagePool.POOL_SIZE):
        p.alloc(i)
    p.free(0)
    # Now there's room again.
    p.alloc(_HugePagePool.POOL_SIZE)


@pytest.mark.parametrize("n_rounds", [1, 5, 10, 50, 100])
def test_alloc_free_cycle_no_leak(n_rounds):
    p = _HugePagePool()
    for round_ in range(n_rounds):
        p.alloc(round_)
        p.free(round_)
    assert p.free_count() == _HugePagePool.POOL_SIZE


# ---------------------------------------------------------------------------
# Color rotation — consecutive allocs must hit different L3 colors
# ---------------------------------------------------------------------------


def test_colors_rotate_within_first_four_allocs():
    p = _HugePagePool()
    colors = {p.alloc(i) for i in range(_HugePagePool.L3_COLORS)}
    assert colors == set(range(_HugePagePool.L3_COLORS))


def test_color_collision_acceptable_after_full_rotation():
    p = _HugePagePool()
    for i in range(_HugePagePool.POOL_SIZE):
        p.alloc(i)
    # 8 allocs over 4 colors → at least one color used twice.
    assert len(p._allocated) == _HugePagePool.POOL_SIZE


# ---------------------------------------------------------------------------
# Duplicate slot_id — must reject
# ---------------------------------------------------------------------------


def test_double_alloc_same_slot_rejects():
    p = _HugePagePool()
    p.alloc(0)
    with pytest.raises(ValueError):
        p.alloc(0)


def test_free_unknown_slot_rejects():
    p = _HugePagePool()
    with pytest.raises(ValueError):
        p.free(99)


# ---------------------------------------------------------------------------
# Allocator is graceful — no Python-level memoryerror or recursion
# ---------------------------------------------------------------------------


def test_allocator_doesnt_panic_under_burst():
    p = _HugePagePool()
    refusal_count = 0
    for i in range(100):
        try:
            p.alloc(i)
        except _HugePagePool.Exhausted:
            refusal_count += 1
    # 8 successful + 92 refusals.
    assert refusal_count == 100 - _HugePagePool.POOL_SIZE


# ---------------------------------------------------------------------------
# State invariants
# ---------------------------------------------------------------------------


def test_freshly_initialized_pool_has_full_capacity():
    p = _HugePagePool()
    assert p.free_count() == _HugePagePool.POOL_SIZE


@pytest.mark.parametrize("n_alloc", [1, 2, 4, 8])
def test_free_count_decreases_by_one_per_alloc(n_alloc):
    p = _HugePagePool()
    pre = p.free_count()
    for i in range(n_alloc):
        p.alloc(i)
    assert p.free_count() == pre - n_alloc


# ---------------------------------------------------------------------------
# Slow — 1000 round-trip cycles
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_1000_round_trip_no_state_leak():
    p = _HugePagePool()
    for i in range(1000):
        p.alloc(i)
        p.free(i)
    assert p.free_count() == _HugePagePool.POOL_SIZE
    assert len(p._allocated) == 0
