"""
backend/services/npu_telemetry.py — Phase 29 (Gap G8, telemetry side-channel)
==============================================================================

NPU telemetry side-channel: a fail-silent, zero-stall collector that aggregates
NPU utilization + per-taint-color frequency counts and exports them to the audit
registry.

Honest scope (read this before citing G8 as closed)
===================================================

G8 has two halves:

  1. The **productive PINNING dispatcher** — a non-test scheduler path that calls
     the mm-resident ``npu_affinity.c`` pin routine. That function
     lives **inside ``kernel/src/mm/``** and is therefore **BLOCKED by INV-1**
     (the untouchable-mm invariant). Per the remediation playbook (G8 / AG-2) we
     **do not** stub a fake caller to make a structural test pass — that is the
     exact Phase-23 gaming. This module does NOT touch ``mm/`` and does NOT call
     ``vos3_npu_affinity_pin``; the pinning dispatcher stays an explicit
     architecture decision (lift INV-1 or relocate the hook out of ``mm/``).

  2. The **telemetry side-channel** — read-only export of "what the NPU did"
     (utilization + taint-color frequency) to the audit registry. That is what
     this module lands. It is the userspace collector with the same contract a
     BPF ring buffer gives: a bounded ring whose producer NEVER blocks — when the
     ring is full or a drain holds it, the sample is DROPPED (the BPF
     ``ringbuf_reserve`` "no space -> NULL -> drop" semantic), never stalling the
     caller (Zero-Stall Policy). A kernel-side ``perf_event_open`` / BPF-ringbuf
     producer feeding real ``npu_ops.c`` counters is the Linux follow-up; on
     non-Linux dev the producers push samples in directly.

So Phase 29 moves G8 from "no productive dispatcher AND no telemetry export" to
"telemetry side-channel landed; productive pinning dispatcher still INV-1
blocked." It does NOT close G8 and advances no moat tally.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("vos3.security.npu_telemetry")

# Taint labels mirror enum vos3_taint_label (taint_maps.h): 0..3.
_TAINT_LABEL_NAMES = {0: "PUBLIC", 1: "UNTRUSTED", 2: "SECRET", 3: "TOXIC"}

DEFAULT_RING_CAPACITY = 4096


@dataclass(frozen=True)
class NPUTelemetrySample:
    """One read-only observation of NPU activity. Carries NO buffer contents —
    only the metadata the audit registry needs (the SHA-correlation lives in the
    kernel audit ring, per the taint_maps.h contract)."""

    npu_id: int
    utilization_pct: float  # 0.0 .. 100.0
    taint_color: int  # enum vos3_taint_label 0..3
    timestamp_ns: int


@dataclass
class NPUTelemetryStats:
    accepted: int = 0
    dropped_full: int = 0  # ring at capacity (backpressure)
    dropped_contention: int = 0  # a drain held the lock (zero-stall drop)
    drains: int = 0
    sink_errors: int = 0


def _default_audit_sink(summary: Dict[str, object]) -> None:
    """Default audit-registry export: a structured ``[AUDIT][npu-telemetry]``
    log line (the structured log IS the audit record on dev, matching the
    project-wide convention)."""
    logger.info(
        "[AUDIT][npu-telemetry] samples=%s util_mean=%.2f util_max=%.2f "
        "colors=%s dropped_full=%s dropped_contention=%s",
        summary.get("samples"),
        summary.get("utilization_mean", 0.0),
        summary.get("utilization_max", 0.0),
        summary.get("taint_color_freq"),
        summary.get("dropped_full"),
        summary.get("dropped_contention"),
    )


class NPUTelemetryDispatcher:
    """Bounded, non-blocking NPU telemetry collector.

    The producer hot path (``record``) takes the lock with ``blocking=False``: if
    a drain holds it OR the ring is full, the sample is dropped and a counter is
    bumped — the caller is NEVER stalled (Zero-Stall Policy). ``drain`` empties
    the ring, aggregates utilization + per-color frequency, and exports the
    summary to the audit sink (fail-silent: a raising sink is logged, not
    propagated)."""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_RING_CAPACITY,
        audit_sink: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._ring: List[NPUTelemetrySample] = []
        self._lock = threading.Lock()
        self._sink = audit_sink or _default_audit_sink
        self.stats = NPUTelemetryStats()

    @property
    def capacity(self) -> int:
        return self._capacity

    def record(
        self,
        *,
        npu_id: int,
        utilization_pct: float,
        taint_color: int,
        timestamp_ns: Optional[int] = None,
    ) -> bool:
        """Non-blocking enqueue. Returns True if the sample was accepted, False if
        it was dropped (ring full or drain in progress). NEVER blocks; NEVER
        raises (a malformed sample is dropped, not surfaced)."""
        try:
            ts = timestamp_ns if timestamp_ns is not None else time.monotonic_ns()
            sample = NPUTelemetrySample(
                npu_id=int(npu_id),
                utilization_pct=float(utilization_pct),
                taint_color=int(taint_color),
                timestamp_ns=int(ts),
            )
        except (TypeError, ValueError):
            return False

        # Zero-stall: a contended lock means a drain is running -> DROP, never wait.
        if not self._lock.acquire(blocking=False):
            self.stats.dropped_contention += 1
            return False
        try:
            if len(self._ring) >= self._capacity:
                self.stats.dropped_full += 1  # backpressure -> drop, never block
                return False
            self._ring.append(sample)
            self.stats.accepted += 1
            return True
        finally:
            self._lock.release()

    def drain(self) -> Dict[str, object]:
        """Pull the buffered samples, aggregate, and export to the audit sink.
        Returns the summary dict. Fail-silent on sink errors."""
        with self._lock:
            batch = self._ring
            self._ring = []
            self.stats.drains += 1
            dropped_full = self.stats.dropped_full
            dropped_contention = self.stats.dropped_contention

        n = len(batch)
        if n:
            utils = [s.utilization_pct for s in batch]
            util_mean = sum(utils) / n
            util_max = max(utils)
        else:
            util_mean = 0.0
            util_max = 0.0
        color_counter: Counter = Counter(s.taint_color for s in batch)
        # Stable, named histogram (every known label present, zero-filled).
        taint_color_freq = {
            _TAINT_LABEL_NAMES.get(c, f"LABEL_{c}"): color_counter.get(c, 0)
            for c in sorted(set(_TAINT_LABEL_NAMES) | set(color_counter))
        }

        summary: Dict[str, object] = {
            "samples": n,
            "utilization_mean": util_mean,
            "utilization_max": util_max,
            "taint_color_freq": taint_color_freq,
            "dropped_full": dropped_full,
            "dropped_contention": dropped_contention,
        }

        try:
            self._sink(summary)
        except Exception as exc:  # noqa: BLE001 — fail-silent export
            self.stats.sink_errors += 1
            logger.warning("[npu-telemetry] audit sink raised (dropped): %s", exc)
        return summary


# ---------------------------------------------------------------------------
# Process-wide singleton + producer convenience
# ---------------------------------------------------------------------------

_DISPATCHER_SINGLETON: Optional[NPUTelemetryDispatcher] = None
_SINGLETON_LOCK = threading.Lock()


def get_npu_telemetry_dispatcher() -> NPUTelemetryDispatcher:
    global _DISPATCHER_SINGLETON
    if _DISPATCHER_SINGLETON is None:
        with _SINGLETON_LOCK:
            if _DISPATCHER_SINGLETON is None:
                _DISPATCHER_SINGLETON = NPUTelemetryDispatcher()
    return _DISPATCHER_SINGLETON


def record_npu_sample(*, npu_id: int, utilization_pct: float, taint_color: int) -> bool:
    """Producer convenience: push one NPU telemetry sample to the process-wide
    side-channel. Non-blocking + fail-silent — safe to call from any hot path."""
    return get_npu_telemetry_dispatcher().record(
        npu_id=npu_id, utilization_pct=utilization_pct, taint_color=taint_color
    )


def _reset_singleton_for_tests() -> None:
    global _DISPATCHER_SINGLETON
    with _SINGLETON_LOCK:
        _DISPATCHER_SINGLETON = None


__all__ = [
    "NPUTelemetrySample",
    "NPUTelemetryStats",
    "NPUTelemetryDispatcher",
    "get_npu_telemetry_dispatcher",
    "record_npu_sample",
]
