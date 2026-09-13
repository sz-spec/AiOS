"""
backend/services/rapl_batch_bridge.py
=======================================

Sprint 16 / Item J3 — RAPL batch-aware power capping bridge.

Why this exists
---------------

From the 80-problem agent-era catalog, J3:
  "Power capping unaware of inference batch sizes — RAPL/Intel SST
   policies don't know about transformer batch sizes; power throttling
   kicks in mid-batch."

When the RAPL governor caps package power because thermal headroom is
running out, it cuts current uniformly across all running tasks. For an
inference workload mid-batch this is exactly the worst time — the
batch is committed (KV cache populated, queue building up), so the
power cap collapses TBT (time-between-tokens) for everyone in the batch.

The fix: extend the RAPL governor with telemetry from the OTel-GenAI
emitter (G1, shipped Sprint 15) so the policy can:

  1. Defer power cap to the END of an in-flight batch when possible.
  2. Pre-emptively shrink the NEXT batch when thermal headroom is low,
     instead of throttling the current one.
  3. Expose a metric (`rapl.deferred_cap_count`) so operators can see
     how often deferral kicked in.

Public surface
--------------

  RaplBatchBridge.register_request(req_id, batch_size, gen_ai_span_attrs)
  RaplBatchBridge.update_thermal(package_temp_c, package_power_w)
  RaplBatchBridge.should_apply_cap(current_cap_w) -> CapDecision
  RaplBatchBridge.complete_request(req_id)
  RaplBatchBridge.snapshot() -> RaplBatchStats

Honest scope ceiling
--------------------

  - This module IS the policy decision layer. The actual RAPL MSR
    write (or equivalent on AMD platforms) is the kernel's job —
    this module returns a decision, not a side effect.
  - "Defer" decisions are bounded: if the current batch has been
    running for > max_defer_ms, we let the cap apply. Default 50ms;
    operator-tunable via VOS3_RAPL_MAX_DEFER_MS env.
  - The cooperative model assumes the OTel emitter actually reports
    batch_size for every in-flight inference request. If batch_size
    is missing, we treat the request as size 1 (worst case for our
    deferral logic, which is the safe default).
  - This module does not enforce thermal limits. Hardware (TJMax)
    + the kernel RAPL governor + the underlying voltage regulator
    will throttle independently if we defer too long. The bridge
    is an optimization layer, not a safety layer.

References:
  - RAPID: Power-Aware Disaggregated Inference (arxiv 2601.12241)
  - Argo NRM + Intel RAPL co-control (LLNL/Argonne 2023+)
  - Intel SST documentation (community.intel.com)
"""

from __future__ import annotations

import enum
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Decision enum + dataclasses
# ---------------------------------------------------------------------------


class CapDecisionKind(enum.IntEnum):
    APPLY = 0  # apply the cap as-is (no in-flight batches to protect)
    DEFER = 1  # defer; wait for in-flight batch to drain
    SHRINK_NEXT = 2  # apply cap but signal scheduler to shrink next batch


@dataclass(frozen=True)
class CapDecision:
    kind: CapDecisionKind
    target_cap_w: float
    reason: str
    deferred_ms: float = 0.0
    in_flight_batches: int = 0


@dataclass
class _InFlightRequest:
    req_id: str
    batch_size: int
    started_ns: int
    span_attrs: dict


@dataclass
class RaplBatchStats:
    total_cap_decisions: int = 0
    deferred_cap_count: int = 0
    shrink_next_count: int = 0
    apply_count: int = 0
    in_flight_requests: int = 0
    last_package_temp_c: float = 0.0
    last_package_power_w: float = 0.0
    last_decision_kind: Optional[CapDecisionKind] = None


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


@dataclass
class RaplBatchBridge:
    """Single chokepoint for power-cap decisions that need to be aware
    of in-flight inference batches.

    Thread-safe — holds an internal Lock around state updates so the
    OTel emitter thread and the kernel-poll thread don't race.
    """

    max_defer_ms: float = float(os.environ.get("VOS3_RAPL_MAX_DEFER_MS", "50"))
    shrink_threshold_temp_c: float = 90.0
    apply_threshold_temp_c: float = 95.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _in_flight: dict[str, _InFlightRequest] = field(default_factory=dict)
    _stats: RaplBatchStats = field(default_factory=RaplBatchStats)
    _last_thermal_update_ns: int = 0

    @staticmethod
    def _now_ns() -> int:
        return time.time_ns()

    def register_request(
        self, req_id: str, batch_size: int, gen_ai_span_attrs: Optional[dict] = None
    ) -> None:
        if not req_id or not isinstance(req_id, str):
            raise ValueError("req_id must be a non-empty string")
        if batch_size <= 0:
            batch_size = 1  # safe default if telemetry was missing
        with self._lock:
            self._in_flight[req_id] = _InFlightRequest(
                req_id=req_id,
                batch_size=batch_size,
                started_ns=self._now_ns(),
                span_attrs=dict(gen_ai_span_attrs or {}),
            )
            self._stats.in_flight_requests = len(self._in_flight)

    def complete_request(self, req_id: str) -> None:
        with self._lock:
            self._in_flight.pop(req_id, None)
            self._stats.in_flight_requests = len(self._in_flight)

    def update_thermal(self, package_temp_c: float, package_power_w: float) -> None:
        with self._lock:
            self._stats.last_package_temp_c = float(package_temp_c)
            self._stats.last_package_power_w = float(package_power_w)
            self._last_thermal_update_ns = self._now_ns()

    def should_apply_cap(self, current_cap_w: float) -> CapDecision:
        """Policy.

        - If no in-flight batches: APPLY (no deferral benefit).
        - If thermal is at apply_threshold or above: APPLY (safety;
          deferral would risk hardware throttling anyway).
        - If thermal is at shrink_threshold or above (but below apply):
          SHRINK_NEXT (cap now, signal scheduler to shrink the next batch).
        - Else if any in-flight batch has been running > max_defer_ms:
          APPLY (don't starve the cap further).
        - Else: DEFER.
        """
        with self._lock:
            self._stats.total_cap_decisions += 1
            now = self._now_ns()
            in_flight = len(self._in_flight)
            temp = self._stats.last_package_temp_c
            if in_flight == 0:
                self._stats.apply_count += 1
                decision = CapDecision(
                    kind=CapDecisionKind.APPLY,
                    target_cap_w=current_cap_w,
                    reason="no_inflight_batches",
                    in_flight_batches=0,
                )
                self._stats.last_decision_kind = decision.kind
                return decision

            if temp >= self.apply_threshold_temp_c:
                self._stats.apply_count += 1
                decision = CapDecision(
                    kind=CapDecisionKind.APPLY,
                    target_cap_w=current_cap_w,
                    reason="thermal_at_apply_threshold",
                    in_flight_batches=in_flight,
                )
                self._stats.last_decision_kind = decision.kind
                return decision

            if temp >= self.shrink_threshold_temp_c:
                self._stats.shrink_next_count += 1
                decision = CapDecision(
                    kind=CapDecisionKind.SHRINK_NEXT,
                    target_cap_w=current_cap_w,
                    reason="thermal_at_shrink_threshold",
                    in_flight_batches=in_flight,
                )
                self._stats.last_decision_kind = decision.kind
                return decision

            # Check oldest in-flight age — if > max_defer_ms, stop deferring.
            oldest_age_ms = 0.0
            if self._in_flight:
                oldest_started = min(r.started_ns for r in self._in_flight.values())
                oldest_age_ms = (now - oldest_started) / 1_000_000.0
            if oldest_age_ms > self.max_defer_ms:
                self._stats.apply_count += 1
                decision = CapDecision(
                    kind=CapDecisionKind.APPLY,
                    target_cap_w=current_cap_w,
                    reason="max_defer_exceeded",
                    deferred_ms=oldest_age_ms,
                    in_flight_batches=in_flight,
                )
                self._stats.last_decision_kind = decision.kind
                return decision

            self._stats.deferred_cap_count += 1
            decision = CapDecision(
                kind=CapDecisionKind.DEFER,
                target_cap_w=current_cap_w,
                reason="batch_inflight_within_defer_window",
                deferred_ms=oldest_age_ms,
                in_flight_batches=in_flight,
            )
            self._stats.last_decision_kind = decision.kind
            return decision

    def snapshot(self) -> RaplBatchStats:
        with self._lock:
            return RaplBatchStats(
                total_cap_decisions=self._stats.total_cap_decisions,
                deferred_cap_count=self._stats.deferred_cap_count,
                shrink_next_count=self._stats.shrink_next_count,
                apply_count=self._stats.apply_count,
                in_flight_requests=self._stats.in_flight_requests,
                last_package_temp_c=self._stats.last_package_temp_c,
                last_package_power_w=self._stats.last_package_power_w,
                last_decision_kind=self._stats.last_decision_kind,
            )


__all__ = [
    "CapDecisionKind",
    "CapDecision",
    "RaplBatchStats",
    "RaplBatchBridge",
]
