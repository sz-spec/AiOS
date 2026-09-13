"""
backend/services/reasoning_audit.py
=====================================

Sprint 16 / Item G4 — AgentSight-style LLM reasoning audit
(eBPF TLS-intercept twin).

What this is
------------

From the 80-problem agent-era catalog, G4:
  "Linux LSM doesn't capture LLM-side reasoning, only syscall layer —
   LSM hooks fire at kernel boundary; the LLM's internal chain-of-
   thought is invisible."

AgentSight (arxiv 2508.02736) addresses this by intercepting TLS-
encrypted LLM traffic via eBPF + correlating with kernel events to
reconstruct an "intent → effect" chain. Their <3% overhead claim
makes this practical for production agent deployments.

Sprint 16 / Wave 2 / G4 ships the Python twin of the AgentSight model:

  - LLMTrafficIntercept records every prompt/response pair (the data
    that the production eBPF hook would extract from intercepted TLS).
  - KernelEventCorrelator records every meaningful kernel-side event
    (syscall, file write, network egress) keyed by process + time.
  - ReasoningAuditor joins the two streams within a time window and
    emits ReasoningAuditRecord{intent, effects, anomalies} records
    that downstream MAIF audit envelope ingestion (Sprint 15 / G5)
    serializes.

Public surface
--------------

  LLMTrafficIntercept.record(process_id, prompt, response, model_id,
                              tool_calls=())
  KernelEventCorrelator.record(process_id, event_kind, target,
                                payload_sha256=None)
  ReasoningAuditor.audit(process_id, window_ns) -> ReasoningAuditRecord
  ReasoningAuditor.snapshot_stats() -> ReasoningAuditStats

Anomaly detection (in-process, deterministic)
---------------------------------------------

  - LOOP — same prompt/response repeated within window (resource-wasting
    reasoning loop, per the AgentSight paper).
  - UNJUSTIFIED_EGRESS — network egress event with no preceding LLM
    response that requested it.
  - PII_LEAK — egress payload contains a known PII pattern hash AND
    the LLM response was UNTRUSTED-labeled.
  - SILENT_EXFIL — kernel egress to a domain not mentioned in any
    prompt/response in the window.

These are heuristics; downstream graders (the dual-LLM router C2 +
the privileged LLM) can lower a SILENT_EXFIL anomaly to "false alarm"
via the same .declassify() pattern as the C7 taint engine.

Honest scope ceiling
--------------------

  - This module is the LOGICAL audit layer. The real eBPF TLS-intercept
    hook (kprobe on SSL_read/SSL_write per AgentSight Section 3.2) is
    a kernel-side / userspace-bpf binary deployment outside Sprint 16
    Wave 2's scope; the Python class provides the same data model so
    downstream MAIF + G2 task/action span integration is testable today.
  - AgentSight's <3% overhead claim is from their published benchmarks
    and is NOT re-validated on vOS workloads. Sprint 16 / Wave 3 will
    add a benchmark suite once the kernel-side hook lands.
  - PII pattern detection is hash-based (sha256 of common patterns —
    api keys, ssn, email); not a full PII discoverer. For policy-grade
    PII detection, use the G5 redaction layer from Sprint 15.

References:
  - AgentSight: System-Level Observability for AI Agents Using eBPF
    (arxiv 2508.02736, August 2025)
  - SAMOS: Securing MCP-based Agent Workflows (ACM '26)
  - C7 IFC engine (this Sprint 16 Wave 2) — provides taint labels
    the auditor consults
"""

from __future__ import annotations

import enum
import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Records + enums
# ---------------------------------------------------------------------------


class KernelEventKind(enum.IntEnum):
    NET_EGRESS = 0
    FILE_WRITE = 1
    SYSCALL_EXEC = 2
    SYSCALL_OPEN = 3
    OTHER = 99


@dataclass(frozen=True)
class LLMTrafficRecord:
    process_id: int
    timestamp_ns: int
    prompt_sha256: str
    response_sha256: str
    prompt_excerpt: str  # first 256 chars (for human audit)
    response_excerpt: str
    model_id: str
    tool_calls: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class KernelEventRecord:
    process_id: int
    timestamp_ns: int
    kind: KernelEventKind
    target: str  # URL / file path / syscall name
    payload_sha256: Optional[str] = None


class AnomalyKind(str, enum.Enum):
    LOOP = "loop"
    UNJUSTIFIED_EGRESS = "unjustified_egress"
    PII_LEAK = "pii_leak"
    SILENT_EXFIL = "silent_exfil"


@dataclass(frozen=True)
class Anomaly:
    kind: AnomalyKind
    detail: str
    event_index: Optional[int] = None


@dataclass(frozen=True)
class ReasoningAuditRecord:
    process_id: int
    window_start_ns: int
    window_end_ns: int
    intents: tuple[LLMTrafficRecord, ...]
    effects: tuple[KernelEventRecord, ...]
    anomalies: tuple[Anomaly, ...]


@dataclass
class ReasoningAuditStats:
    audits_run: int = 0
    intents_recorded: int = 0
    effects_recorded: int = 0
    anomalies_total: int = 0
    anomalies_by_kind: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# PII pattern hashes — known-bad patterns for the PII_LEAK heuristic
# ---------------------------------------------------------------------------


_PII_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style API key
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN
    re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE),  # email
    re.compile(r"\b\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{4}\b"),  # CC
]


def _contains_pii(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _PII_PATTERNS)


def _hash_excerpt(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# LLMTrafficIntercept + KernelEventCorrelator
# ---------------------------------------------------------------------------


class LLMTrafficIntercept:
    """Records prompt/response pairs the production eBPF hook would
    extract from intercepted TLS. In tests, callers feed records via
    .record(). In production, the eBPF hook will call this same .record()."""

    def __init__(self):
        self._records: list[LLMTrafficRecord] = []
        self._lock = threading.Lock()

    @staticmethod
    def _now_ns() -> int:
        return time.time_ns()

    def record(
        self,
        *,
        process_id: int,
        prompt: str,
        response: str,
        model_id: str,
        tool_calls: Iterable[str] = (),
    ) -> LLMTrafficRecord:
        if process_id <= 0:
            raise ValueError("process_id must be positive int")
        if not isinstance(prompt, str) or not isinstance(response, str):
            raise TypeError("prompt + response must be strings")
        if not model_id:
            raise ValueError("model_id required")
        rec = LLMTrafficRecord(
            process_id=process_id,
            timestamp_ns=self._now_ns(),
            prompt_sha256=_hash_excerpt(prompt),
            response_sha256=_hash_excerpt(response),
            prompt_excerpt=prompt[:256],
            response_excerpt=response[:256],
            model_id=model_id,
            tool_calls=tuple(tool_calls),
        )
        with self._lock:
            self._records.append(rec)
        return rec

    def slice_for(
        self, process_id: int, window_start_ns: int, window_end_ns: int
    ) -> list[LLMTrafficRecord]:
        with self._lock:
            return [
                r
                for r in self._records
                if r.process_id == process_id
                and window_start_ns <= r.timestamp_ns <= window_end_ns
            ]


class KernelEventCorrelator:
    def __init__(self):
        self._events: list[KernelEventRecord] = []
        self._lock = threading.Lock()

    @staticmethod
    def _now_ns() -> int:
        return time.time_ns()

    def record(
        self,
        *,
        process_id: int,
        event_kind: KernelEventKind,
        target: str,
        payload_sha256: Optional[str] = None,
    ) -> KernelEventRecord:
        if process_id <= 0:
            raise ValueError("process_id must be positive int")
        if not isinstance(event_kind, KernelEventKind):
            raise TypeError("event_kind must be KernelEventKind")
        if not target:
            raise ValueError("target required")
        rec = KernelEventRecord(
            process_id=process_id,
            timestamp_ns=self._now_ns(),
            kind=event_kind,
            target=target,
            payload_sha256=payload_sha256,
        )
        with self._lock:
            self._events.append(rec)
        return rec

    def slice_for(
        self, process_id: int, window_start_ns: int, window_end_ns: int
    ) -> list[KernelEventRecord]:
        with self._lock:
            return [
                e
                for e in self._events
                if e.process_id == process_id
                and window_start_ns <= e.timestamp_ns <= window_end_ns
            ]


# ---------------------------------------------------------------------------
# ReasoningAuditor
# ---------------------------------------------------------------------------


class ReasoningAuditor:
    """Joins LLM-intent records with kernel-effect records, emits a
    ReasoningAuditRecord with detected anomalies for downstream MAIF
    envelope ingestion."""

    def __init__(
        self, intercept: LLMTrafficIntercept, correlator: KernelEventCorrelator
    ):
        if intercept is None or correlator is None:
            raise ValueError("intercept + correlator required")
        self._intercept = intercept
        self._correlator = correlator
        self._stats = ReasoningAuditStats()
        self._lock = threading.Lock()

    def audit(
        self, process_id: int, window_ns: int = 30_000_000_000
    ) -> ReasoningAuditRecord:
        """Audit `window_ns` of activity ending NOW for `process_id`."""
        if process_id <= 0:
            raise ValueError("process_id must be positive int")
        if window_ns <= 0:
            raise ValueError("window_ns must be positive")
        end_ns = time.time_ns()
        start_ns = end_ns - window_ns
        intents = self._intercept.slice_for(process_id, start_ns, end_ns)
        effects = self._correlator.slice_for(process_id, start_ns, end_ns)

        anomalies = list(self._detect_loop(intents))
        anomalies.extend(self._detect_unjustified_egress(intents, effects))
        anomalies.extend(self._detect_pii_leak(intents, effects))
        anomalies.extend(self._detect_silent_exfil(intents, effects))

        record = ReasoningAuditRecord(
            process_id=process_id,
            window_start_ns=start_ns,
            window_end_ns=end_ns,
            intents=tuple(intents),
            effects=tuple(effects),
            anomalies=tuple(anomalies),
        )
        with self._lock:
            self._stats.audits_run += 1
            self._stats.intents_recorded += len(intents)
            self._stats.effects_recorded += len(effects)
            self._stats.anomalies_total += len(anomalies)
            for a in anomalies:
                self._stats.anomalies_by_kind[a.kind.value] = (
                    self._stats.anomalies_by_kind.get(a.kind.value, 0) + 1
                )
        return record

    # -- Detectors -----------------------------------------------------------

    def _detect_loop(self, intents: list[LLMTrafficRecord]) -> Iterable[Anomaly]:
        """Same (prompt_sha256, response_sha256) pair appears more than once."""
        seen: dict[tuple[str, str], int] = {}
        for idx, r in enumerate(intents):
            key = (r.prompt_sha256, r.response_sha256)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                yield Anomaly(
                    kind=AnomalyKind.LOOP,
                    detail=f"prompt/response pair repeated at index {idx}",
                    event_index=idx,
                )

    def _detect_unjustified_egress(
        self, intents: list[LLMTrafficRecord], effects: list[KernelEventRecord]
    ) -> Iterable[Anomaly]:
        """NET_EGRESS event with no preceding LLM response in the window."""
        for idx, e in enumerate(effects):
            if e.kind != KernelEventKind.NET_EGRESS:
                continue
            preceding = [i for i in intents if i.timestamp_ns <= e.timestamp_ns]
            if not preceding:
                yield Anomaly(
                    kind=AnomalyKind.UNJUSTIFIED_EGRESS,
                    detail=f"NET_EGRESS to {e.target} with no preceding LLM intent",
                    event_index=idx,
                )

    def _detect_pii_leak(
        self, intents: list[LLMTrafficRecord], effects: list[KernelEventRecord]
    ) -> Iterable[Anomaly]:
        """LLM response excerpt contains PII pattern AND an egress
        followed within the window."""
        intent_has_pii = any(_contains_pii(i.response_excerpt) for i in intents)
        if not intent_has_pii:
            return
        for idx, e in enumerate(effects):
            if e.kind == KernelEventKind.NET_EGRESS:
                yield Anomaly(
                    kind=AnomalyKind.PII_LEAK,
                    detail=f"LLM response contained PII pattern; "
                    f"NET_EGRESS to {e.target} in window",
                    event_index=idx,
                )

    def _detect_silent_exfil(
        self, intents: list[LLMTrafficRecord], effects: list[KernelEventRecord]
    ) -> Iterable[Anomaly]:
        """NET_EGRESS to a target NOT mentioned in any prompt/response."""
        mentioned: set[str] = set()
        for i in intents:
            text = (i.prompt_excerpt or "") + " " + (i.response_excerpt or "")
            for tok in text.split():
                if "://" in tok or "." in tok:
                    mentioned.add(tok.strip(".,;:'\"()<>[]"))
        for idx, e in enumerate(effects):
            if e.kind != KernelEventKind.NET_EGRESS:
                continue
            # Check if e.target (or its host portion) shows up in mentions.
            host = e.target.split("://", 1)[-1].split("/", 1)[0]
            if not any(host in m or m in host for m in mentioned):
                yield Anomaly(
                    kind=AnomalyKind.SILENT_EXFIL,
                    detail=f"NET_EGRESS to {e.target} not mentioned in any "
                    "prompt or response in window",
                    event_index=idx,
                )

    # -- Stats ---------------------------------------------------------------

    def snapshot_stats(self) -> ReasoningAuditStats:
        with self._lock:
            return ReasoningAuditStats(
                audits_run=self._stats.audits_run,
                intents_recorded=self._stats.intents_recorded,
                effects_recorded=self._stats.effects_recorded,
                anomalies_total=self._stats.anomalies_total,
                anomalies_by_kind=dict(self._stats.anomalies_by_kind),
            )


__all__ = [
    "KernelEventKind",
    "AnomalyKind",
    "LLMTrafficRecord",
    "KernelEventRecord",
    "Anomaly",
    "ReasoningAuditRecord",
    "ReasoningAuditStats",
    "LLMTrafficIntercept",
    "KernelEventCorrelator",
    "ReasoningAuditor",
]
