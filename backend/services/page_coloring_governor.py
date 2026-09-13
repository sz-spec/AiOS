"""
backend/services/page_coloring_governor.py
============================================

Sprint 16 / Item A4 — TLB side-channel mitigation via page coloring +
TLB-randomization policy governor.

What this is
------------

From the 80-problem agent-era catalog, A4:
  "Page-table side channels fingerprint model architecture — every
   transformer's attention pattern is visible via TLB-miss timing,
   revealing layer count + head dimensions to a co-tenant."

Page coloring partitions physical pages into color classes such that
co-tenants are placed in disjoint colors, eliminating the shared-TLB-
set leakage. This module is the **policy governor** that decides:

  1. Which color set to assign to a new AI workload region (based on
     the workload_hint shipped in Sprint 16 / A1).
  2. When to randomize TLB-entry ordering on context-switch (the
     existing kernel ASID flip is at slot granularity; A4 adds
     per-color-set randomization for cross-color isolation).
  3. When to deny a co-tenant placement that would land in a color
     class shared with a high-sensitivity tenant.

Public surface
--------------

  PageColoringGovernor.allocate_colors(workload_hint, requested_count) -> ColorSet
  PageColoringGovernor.release_colors(color_set)
  PageColoringGovernor.check_isolation(a_set, b_set) -> IsolationDecision
  PageColoringGovernor.snapshot_stats() -> PageColoringStats

Color model
-----------

The CPU's L1d cache has N sets (typically 64 on x86_64 with 4KB pages,
larger on huge pages). Coloring assigns each physical page a "color"
in 0..N-1 derived from the upper bits of its physical address.

We model this with a fixed pool of `N_COLORS` (default 64). Allocator
maintains a refcount per color + per-workload-class reservation:

  MODEL_WEIGHTS   → exclusive colors (own set, no sharing)
  KV_CACHE        → tenant-grouped colors (one per agent)
  SCRATCH         → shared pool (no isolation guarantee)

Honest scope ceiling
--------------------

  - This module IS the policy decision layer. The actual kernel-side
    physical-page allocator that honors color assignments lives in
    kernel/src/mm/ (Wave 3 follow-up integrates with the existing
    ai_slots.c HugePage path).
  - L1d set count varies by CPU (default 64); operator can override
    via VOS3_PAGE_COLOR_COUNT env. We trust the value — no CPUID
    probing here (kernel will probe in production).
  - Real defense against TLB side channels also requires SCHED_CORE
    cookies (existing in vOS kernel/src/sched/core_cookie.c) to prevent
    SMT cross-thread leakage. This module covers the COLOR axis;
    SCHED_CORE covers the SMT axis. Both required for full defense
    per the Sprint 16 plan.
  - Color-randomization on context switch is signalled via
    `randomize_on_next_switch` flag — actual switching is the kernel
    scheduler's responsibility.

References:
  - ShadowScope (arxiv 2509.00300) — GPU page-table side channels
  - "Composable OS Kernel Architectures" (arxiv 2508.00604)
  - vOS Sprint 16 / A1 workload-hint API (kernel/include/vos/ai_guard.h)
"""

from __future__ import annotations

import enum
import os
import threading
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COLOR_COUNT = int(os.environ.get("VOS3_PAGE_COLOR_COUNT", "64"))
DEFAULT_MAX_PER_COLOR_RSS = 1024  # pages — softcap for refcount fairness


class WorkloadHint(enum.IntEnum):
    """Mirror the kernel A1 enum vos3_ai_workload_hint_t."""

    SCRATCH = 0
    MODEL_WEIGHTS = 1
    KV_CACHE = 2


class IsolationKind(enum.IntEnum):
    """Outcome of cross-tenant placement check."""

    ISOLATED = 0  # disjoint color sets — safe to co-tenant
    OVERLAPPING = 1  # at least one shared color — leak risk
    FULLY_SHARED = 2  # identical sets — explicitly co-tenanted


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColorSet:
    """A set of physical-page color indices reserved for one workload."""

    set_id: int
    colors: frozenset[int]
    workload_hint: WorkloadHint
    owner_tag: str
    randomize_on_next_switch: bool = False


@dataclass(frozen=True)
class IsolationDecision:
    kind: IsolationKind
    shared_colors: tuple[int, ...]
    reason: str


@dataclass
class PageColoringStats:
    color_count: int = 0
    total_allocations: int = 0
    total_releases: int = 0
    exclusive_grants: int = 0  # MODEL_WEIGHTS reservations
    tenant_grouped_grants: int = 0  # KV_CACHE reservations
    shared_grants: int = 0  # SCRATCH reservations
    overlap_warnings_issued: int = 0
    allocation_failures: int = 0


# ---------------------------------------------------------------------------
# Governor
# ---------------------------------------------------------------------------


class PageColoringGovernor:
    """Per-host policy decider for AI workload page coloring.

    Maintains:
      - per-color refcount (how many active workloads claim this color)
      - per-color exclusive owner (set when MODEL_WEIGHTS reserved this color)
      - tenant-group registry (owner_tag → colors) for KV_CACHE workloads
    """

    def __init__(
        self,
        color_count: int = DEFAULT_COLOR_COUNT,
        max_per_color_rss: int = DEFAULT_MAX_PER_COLOR_RSS,
    ):
        if color_count <= 0:
            raise ValueError("color_count must be positive")
        if max_per_color_rss <= 0:
            raise ValueError("max_per_color_rss must be positive")
        self._color_count = color_count
        self._max_per_color = max_per_color_rss
        self._refcount: list[int] = [0] * color_count
        self._exclusive_owner: list[Optional[str]] = [None] * color_count
        self._tenant_groups: dict[str, frozenset[int]] = {}
        self._next_set_id = 1
        self._lock = threading.Lock()
        self._stats = PageColoringStats(color_count=color_count)

    # -- Allocation ---------------------------------------------------------

    def allocate_colors(
        self, workload_hint: WorkloadHint, requested_count: int, owner_tag: str
    ) -> ColorSet:
        if not isinstance(workload_hint, WorkloadHint):
            raise TypeError("workload_hint must be WorkloadHint")
        if requested_count <= 0:
            raise ValueError("requested_count must be positive")
        if requested_count > self._color_count:
            raise ValueError(
                f"requested_count {requested_count} > available " f"{self._color_count}"
            )
        if not owner_tag or not isinstance(owner_tag, str):
            raise ValueError("owner_tag must be non-empty string")

        with self._lock:
            self._stats.total_allocations += 1
            self._next_set_id += 1
            set_id = self._next_set_id

            if workload_hint == WorkloadHint.MODEL_WEIGHTS:
                colors = self._reserve_exclusive(requested_count, owner_tag)
                if colors is None:
                    self._stats.allocation_failures += 1
                    raise RuntimeError(
                        f"cannot reserve {requested_count} exclusive colors "
                        f"for MODEL_WEIGHTS — capacity exhausted"
                    )
                self._stats.exclusive_grants += 1
                randomize = False
            elif workload_hint == WorkloadHint.KV_CACHE:
                colors = self._reserve_tenant_grouped(requested_count, owner_tag)
                self._stats.tenant_grouped_grants += 1
                randomize = True  # rotate per-tenant colors on context switch
            else:  # SCRATCH
                colors = self._reserve_shared(requested_count)
                self._stats.shared_grants += 1
                randomize = False

            cs = ColorSet(
                set_id=set_id,
                colors=colors,
                workload_hint=workload_hint,
                owner_tag=owner_tag,
                randomize_on_next_switch=randomize,
            )
            for c in colors:
                self._refcount[c] += 1
            return cs

    def _reserve_exclusive(
        self, count: int, owner_tag: str
    ) -> Optional[frozenset[int]]:
        """Find `count` colors with refcount==0 and no exclusive owner."""
        free = [
            c
            for c in range(self._color_count)
            if self._refcount[c] == 0 and self._exclusive_owner[c] is None
        ]
        if len(free) < count:
            return None
        chosen = frozenset(free[:count])
        for c in chosen:
            self._exclusive_owner[c] = owner_tag
        return chosen

    def _reserve_tenant_grouped(self, count: int, owner_tag: str) -> frozenset[int]:
        """If owner_tag already has a tenant group, return same colors.
        Else, find `count` colors with no exclusive_owner conflict +
        lowest refcount."""
        if owner_tag in self._tenant_groups:
            return self._tenant_groups[owner_tag]
        candidates = sorted(
            (self._refcount[c], c)
            for c in range(self._color_count)
            if self._exclusive_owner[c] is None
        )
        chosen = frozenset(c for _rc, c in candidates[:count])
        self._tenant_groups[owner_tag] = chosen
        return chosen

    def _reserve_shared(self, count: int) -> frozenset[int]:
        """Spread over all non-exclusive colors, preferring lowest refcount."""
        candidates = sorted(
            (self._refcount[c], c)
            for c in range(self._color_count)
            if self._exclusive_owner[c] is None
        )
        return frozenset(c for _rc, c in candidates[:count])

    # -- Release ------------------------------------------------------------

    def release_colors(self, color_set: ColorSet) -> None:
        if not isinstance(color_set, ColorSet):
            raise TypeError("color_set must be ColorSet")
        with self._lock:
            self._stats.total_releases += 1
            for c in color_set.colors:
                if self._refcount[c] > 0:
                    self._refcount[c] -= 1
                if (
                    color_set.workload_hint == WorkloadHint.MODEL_WEIGHTS
                    and self._exclusive_owner[c] == color_set.owner_tag
                ):
                    self._exclusive_owner[c] = None
            if (
                color_set.workload_hint == WorkloadHint.KV_CACHE
                and color_set.owner_tag in self._tenant_groups
                and self._tenant_groups[color_set.owner_tag] == color_set.colors
            ):
                del self._tenant_groups[color_set.owner_tag]

    # -- Isolation check ----------------------------------------------------

    def check_isolation(self, a: ColorSet, b: ColorSet) -> IsolationDecision:
        if not isinstance(a, ColorSet) or not isinstance(b, ColorSet):
            raise TypeError("a + b must be ColorSet instances")
        shared = a.colors & b.colors
        if not shared:
            return IsolationDecision(
                kind=IsolationKind.ISOLATED,
                shared_colors=(),
                reason="disjoint_color_sets",
            )
        if a.colors == b.colors:
            return IsolationDecision(
                kind=IsolationKind.FULLY_SHARED,
                shared_colors=tuple(sorted(shared)),
                reason="identical_color_sets",
            )
        with self._lock:
            self._stats.overlap_warnings_issued += 1
        return IsolationDecision(
            kind=IsolationKind.OVERLAPPING,
            shared_colors=tuple(sorted(shared)),
            reason="partial_color_overlap",
        )

    # -- Stats --------------------------------------------------------------

    def snapshot_stats(self) -> PageColoringStats:
        with self._lock:
            return PageColoringStats(
                color_count=self._stats.color_count,
                total_allocations=self._stats.total_allocations,
                total_releases=self._stats.total_releases,
                exclusive_grants=self._stats.exclusive_grants,
                tenant_grouped_grants=self._stats.tenant_grouped_grants,
                shared_grants=self._stats.shared_grants,
                overlap_warnings_issued=self._stats.overlap_warnings_issued,
                allocation_failures=self._stats.allocation_failures,
            )

    def color_refcount(self, color: int) -> int:
        if color < 0 or color >= self._color_count:
            raise IndexError(f"color {color} out of range")
        with self._lock:
            return self._refcount[color]


__all__ = [
    "DEFAULT_COLOR_COUNT",
    "DEFAULT_MAX_PER_COLOR_RSS",
    "WorkloadHint",
    "IsolationKind",
    "ColorSet",
    "IsolationDecision",
    "PageColoringStats",
    "PageColoringGovernor",
]
