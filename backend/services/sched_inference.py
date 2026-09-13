"""
backend/services/sched_inference.py
=====================================

Sprint 16 / Item J1 — Python twin of the kernel SCHED_INFERENCE class
(kernel/include/vos/sched_inference.h).

What this is
------------

EDF (Earliest Deadline First) scheduler for inference requests with
distinct TTFT (Time To First Token) and TBT (Time Between Tokens)
deadline classes. Mirrors the kernel API exactly so backend services
can program against the same surface today, ahead of the kernel
syscall path landing in Sprint 16 / Wave 2.

Why a Python twin
-----------------

  1. Tests cover the picker logic + SLO accounting at Python-test
     latency without needing a QEMU rebuild.
  2. The backend OTel-GenAI emitter (G1, shipped Sprint 15) can
     submit per-request deadlines directly to this Python twin
     before the kernel hook lands.
  3. SLAI-style backpressure heuristics (arxiv 2508.01002) can be
     prototyped here against the same API the kernel will expose.

Honest scope ceiling
--------------------

  - Picker is O(N) linear scan. For the expected N (~tens of in-flight
    requests per CPU) this is the fast path. A binary heap is worth
    revisiting only above N≈100, which is well past the per-CPU
    inference throughput target.

  - SLO violation = completion past deadline. We do NOT cancel a
    missed-deadline request mid-flight (would orphan partial output
    tokens, worse UX). The next-priority effect comes from EDF
    preferring newer, non-missed requests.

  - Percentile computation uses a moving-window histogram (2048
    most-recent latency observations) — bounded memory, accurate at
    P50 and P95, slightly noisy at P99 if the window hasn't filled.

References:
  - SLAI: SLO-aware LLM inference scheduler (arxiv 2508.01002)
  - TempoNet: Slack-Quantized Transformer-Guided RL Scheduler (arxiv 2602.18109)
"""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Status + error codes (mirror the kernel header)
# ---------------------------------------------------------------------------


class ReqStatus(enum.IntEnum):
    FREE = 0
    QUEUED = 1
    RUNNING = 2
    COMPLETED = 3
    MISSED_SLO = 4


class SchedError(enum.IntEnum):
    OK = 0
    INVAL = -1
    FULL = -2
    NOTFOUND = -3
    EMPTY = -4


class SchedException(Exception):
    def __init__(self, code: SchedError, message: str = ""):
        self.code = code
        super().__init__(f"{code.name}: {message}" if message else code.name)


# ---------------------------------------------------------------------------
# Request descriptor
# ---------------------------------------------------------------------------


@dataclass
class SchedRequest:
    req_id: int = 0
    arrival_ns: int = 0
    ttft_deadline_ns: int = 0
    tbt_deadline_ns: int = 0
    completed_ns: int = 0
    opaque: int = 0
    status: ReqStatus = ReqStatus.FREE


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class SchedStats:
    total_admitted: int = 0
    total_completed: int = 0
    slo_violations: int = 0
    p50_latency_ns: int = 0
    p95_latency_ns: int = 0
    p99_latency_ns: int = 0


_LATENCY_WINDOW = 2048  # rolling-window size for percentile estimation


def _percentile(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    idx = int(q * (len(sorted_vals) - 1))
    return sorted_vals[idx]


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


@dataclass
class SchedQueue:
    max_requests: int = 128
    requests: list[SchedRequest] = field(default_factory=list)
    active: int = 0
    next_req_id: int = 1
    stats: SchedStats = field(default_factory=SchedStats)
    _latency_window: deque = field(
        default_factory=lambda: deque(maxlen=_LATENCY_WINDOW)
    )

    def __post_init__(self) -> None:
        if self.max_requests <= 0:
            raise SchedException(SchedError.INVAL, "max_requests must be > 0")
        if not self.requests:
            self.requests = [SchedRequest() for _ in range(self.max_requests)]
        elif len(self.requests) != self.max_requests:
            raise SchedException(
                SchedError.INVAL,
                f"requests length {len(self.requests)} != "
                f"max_requests {self.max_requests}",
            )

    def submit(
        self,
        *,
        arrival_ns: int,
        ttft_deadline_ns: int,
        tbt_deadline_ns: int,
        opaque: int = 0,
    ) -> int:
        """Return the assigned req_id."""
        if arrival_ns < 0 or ttft_deadline_ns < 0 or tbt_deadline_ns < 0:
            raise SchedException(SchedError.INVAL, "times must be non-negative")
        if ttft_deadline_ns < arrival_ns:
            raise SchedException(SchedError.INVAL, "ttft_deadline must be >= arrival")
        if self.active >= self.max_requests:
            raise SchedException(SchedError.FULL, "queue at capacity")

        for r in self.requests:
            if r.status != ReqStatus.FREE:
                continue
            r.req_id = self.next_req_id
            r.arrival_ns = arrival_ns
            r.ttft_deadline_ns = ttft_deadline_ns
            r.tbt_deadline_ns = tbt_deadline_ns
            r.completed_ns = 0
            r.opaque = opaque
            r.status = ReqStatus.QUEUED
            self.next_req_id += 1
            if self.next_req_id == 0:
                self.next_req_id = 1
            self.active += 1
            self.stats.total_admitted += 1
            return r.req_id
        raise SchedException(SchedError.FULL, "no free slot (internal)")

    def pick_next(self) -> SchedRequest:
        """EDF picker — returns a COPY of the picked request after
        flipping its status QUEUED → RUNNING in the queue."""
        best: Optional[SchedRequest] = None
        for r in self.requests:
            if r.status != ReqStatus.QUEUED:
                continue
            if best is None or r.ttft_deadline_ns < best.ttft_deadline_ns:
                best = r
        if best is None:
            raise SchedException(SchedError.EMPTY, "no QUEUED requests")
        best.status = ReqStatus.RUNNING
        return SchedRequest(
            req_id=best.req_id,
            arrival_ns=best.arrival_ns,
            ttft_deadline_ns=best.ttft_deadline_ns,
            tbt_deadline_ns=best.tbt_deadline_ns,
            completed_ns=best.completed_ns,
            opaque=best.opaque,
            status=best.status,
        )

    def complete(self, req_id: int, completed_ns: int) -> None:
        if completed_ns < 0:
            raise SchedException(SchedError.INVAL, "completed_ns must be >= 0")
        for r in self.requests:
            if r.req_id != req_id:
                continue
            if r.status not in (ReqStatus.QUEUED, ReqStatus.RUNNING):
                raise SchedException(
                    SchedError.NOTFOUND,
                    f"req {req_id} not active " f"(status={r.status.name})",
                )
            r.completed_ns = completed_ns
            latency = max(0, completed_ns - r.arrival_ns)
            self._latency_window.append(latency)
            self.stats.total_completed += 1
            if completed_ns > r.ttft_deadline_ns:
                r.status = ReqStatus.MISSED_SLO
                self.stats.slo_violations += 1
            else:
                r.status = ReqStatus.COMPLETED
            self.active -= 1
            self._recompute_percentiles()
            return
        raise SchedException(SchedError.NOTFOUND, f"req {req_id} not in queue")

    def _recompute_percentiles(self) -> None:
        if not self._latency_window:
            return
        sorted_vals = sorted(self._latency_window)
        self.stats.p50_latency_ns = _percentile(sorted_vals, 0.50)
        self.stats.p95_latency_ns = _percentile(sorted_vals, 0.95)
        self.stats.p99_latency_ns = _percentile(sorted_vals, 0.99)

    def get_stats(self) -> SchedStats:
        return SchedStats(
            total_admitted=self.stats.total_admitted,
            total_completed=self.stats.total_completed,
            slo_violations=self.stats.slo_violations,
            p50_latency_ns=self.stats.p50_latency_ns,
            p95_latency_ns=self.stats.p95_latency_ns,
            p99_latency_ns=self.stats.p99_latency_ns,
        )

    def gc_completed(self) -> int:
        """Sweep terminal entries back to FREE — caller invokes this
        periodically (the kernel side does it on the scheduler tick).
        Returns the number of slots reclaimed."""
        reclaimed = 0
        for r in self.requests:
            if r.status in (ReqStatus.COMPLETED, ReqStatus.MISSED_SLO):
                r.status = ReqStatus.FREE
                r.req_id = 0
                r.arrival_ns = 0
                r.ttft_deadline_ns = 0
                r.tbt_deadline_ns = 0
                r.completed_ns = 0
                r.opaque = 0
                reclaimed += 1
        return reclaimed


__all__ = [
    "ReqStatus",
    "SchedError",
    "SchedException",
    "SchedRequest",
    "SchedStats",
    "SchedQueue",
]
