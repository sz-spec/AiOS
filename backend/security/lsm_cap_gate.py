"""
backend/security/lsm_cap_gate.py
==================================

Sprint 16 / Item B3 — eBPF LSM per-call capability gating model.

What this is
------------

From the 80-problem agent-era catalog, B3:
  "Linux capabilities (CAP_NET_RAW etc.) are all-or-nothing — no per-call
   gating; an agent that needs CAP_NET_BIND_SERVICE once gets it for the
   process lifetime."

Real eBPF LSM programs attached to capability_capable() hooks let the
kernel evaluate a per-call policy before granting the capability.
Sprint 16 Wave 3 ships the **Python policy model**: the data structures
and decision logic that the eBPF program will compile against.

Public surface
--------------

  LinuxCapability — IntEnum mirroring linux/capability.h
  CapGateRule — per-(capability, predicate) policy entry
  CapGate — registry + decision engine
    .add_rule(cap, predicate, decision, reason)
    .check(cap, context) -> CapDecision
    .stats() -> CapGateStats

Predicate model
---------------

Predicates are simple structural matchers over a CapContext:

  - process_match: prefix/glob over comm or exe_path
  - tool_call_match: tool name regex (when invoked from agent)
  - taint_label_at_most: max C7 TaintLabel for this call
  - count_in_window: rate-limit (N calls per window_ms)
  - always: matches everything (fallback rule)

The decision is the first matching rule's `decision` (ALLOW or DENY).
If no rule matches, the gate's default_decision applies.

Honest scope ceiling
--------------------

  - This module is the POLICY model. The eBPF compiler that emits a
    `cap_check.bpf.c` program from this model is the B4 follow-up
    (lands in Sprint 17 / Wave 4 — the model defined here gives B4
    a stable target).
  - Predicates intentionally limited to what an eBPF verifier can
    evaluate (no unbounded loops, no string ops above a 256-byte cap).
  - Rate-limit `count_in_window` is per-process; cross-process
    aggregation needs a kernel-side BPF map (out of scope).
"""

from __future__ import annotations

import enum
import fnmatch
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Linux capabilities — subset commonly relevant to AI agents
# ---------------------------------------------------------------------------


class LinuxCapability(enum.IntEnum):
    """Values match linux/capability.h."""

    CHOWN = 0
    DAC_OVERRIDE = 1
    NET_BIND_SERVICE = 10
    NET_BROADCAST = 11
    NET_ADMIN = 12
    NET_RAW = 13
    IPC_LOCK = 14
    SYS_MODULE = 16
    SYS_RAWIO = 17
    SYS_PTRACE = 19
    SYS_ADMIN = 21
    SYS_NICE = 23
    SYSLOG = 34
    BPF = 39
    PERFMON = 38
    CHECKPOINT_RESTORE = 40


# ---------------------------------------------------------------------------
# Decision + context types
# ---------------------------------------------------------------------------


class CapDecision(enum.IntEnum):
    ALLOW = 0
    DENY = 1


@dataclass(frozen=True)
class CapContext:
    """Inputs the predicate evaluates against. The kernel-side eBPF
    program will populate these from the task_struct + per-fd metadata."""

    capability: LinuxCapability
    process_id: int
    comm: str
    exe_path: str
    tool_call_name: Optional[str] = None
    taint_label: int = 0  # mirror C7 TaintLabel int value
    timestamp_ns: int = 0


@dataclass(frozen=True)
class CapCheckResult:
    decision: CapDecision
    matched_rule_index: Optional[int]
    reason: str


@dataclass
class CapGateStats:
    total_checks: int = 0
    allowed: int = 0
    denied: int = 0
    by_capability: dict[int, int] = field(default_factory=dict)
    rate_limited: int = 0


# ---------------------------------------------------------------------------
# Predicate type
# ---------------------------------------------------------------------------


# A Predicate is a callable returning True/False for a CapContext.
# We build them via factory helpers below so the eBPF compiler has a
# closed set of shapes to translate.

Predicate = Callable[[CapContext], bool]


def predicate_process_match(
    *, comm_glob: Optional[str] = None, exe_prefix: Optional[str] = None
) -> Predicate:
    """Match by process name (fnmatch glob) and/or exe-path prefix."""

    def _p(ctx: CapContext) -> bool:
        if comm_glob is not None and not fnmatch.fnmatchcase(ctx.comm, comm_glob):
            return False
        if exe_prefix is not None and not ctx.exe_path.startswith(exe_prefix):
            return False
        return True

    return _p


def predicate_tool_call_match(*, tool_regex: str) -> Predicate:
    """Match when the call comes from an agent tool whose name matches regex."""
    pat = re.compile(tool_regex)

    def _p(ctx: CapContext) -> bool:
        if ctx.tool_call_name is None:
            return False
        return bool(pat.match(ctx.tool_call_name))

    return _p


def predicate_taint_at_most(*, max_label: int) -> Predicate:
    """Match when the request's taint label is at most `max_label`."""

    def _p(ctx: CapContext) -> bool:
        return ctx.taint_label <= max_label

    return _p


def predicate_always() -> Predicate:
    return lambda _ctx: True


# ---------------------------------------------------------------------------
# Rate-limit predicate (stateful — keeps a per-cap deque of timestamps)
# ---------------------------------------------------------------------------


def predicate_rate_limit(
    *,
    max_calls: int,
    window_ms: int,
    scope_key_fn: Optional[Callable[[CapContext], str]] = None,
) -> Predicate:
    """True iff the call falls WITHIN the rate budget. Once budget is
    exhausted, the predicate returns False (typically paired with a
    DENY decision).

    Args:
        max_calls: budget per window per scope_key.
        window_ms: rolling-window size.
        scope_key_fn: function mapping ctx → string key. Default
                      is (capability, process_id).
    """
    if max_calls <= 0:
        raise ValueError("max_calls must be positive")
    if window_ms <= 0:
        raise ValueError("window_ms must be positive")
    window_ns = window_ms * 1_000_000
    history: dict[str, deque] = {}
    lock = threading.Lock()
    default_key = lambda ctx: f"{ctx.capability.value}:{ctx.process_id}"
    key_fn = scope_key_fn or default_key

    def _p(ctx: CapContext) -> bool:
        now = ctx.timestamp_ns or time.time_ns()
        key = key_fn(ctx)
        with lock:
            dq = history.get(key)
            if dq is None:
                dq = deque()
                history[key] = dq
            # Drop expired entries.
            cutoff = now - window_ns
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= max_calls:
                return False
            dq.append(now)
            return True

    return _p


# ---------------------------------------------------------------------------
# Rule + Gate
# ---------------------------------------------------------------------------


@dataclass
class CapGateRule:
    capability: LinuxCapability
    predicate: Predicate
    decision: CapDecision
    reason: str
    description: str = ""


class CapGate:
    """Per-host capability decision engine. Production wiring will
    install an eBPF LSM program compiled from these rules; today this
    module runs the rules in-process for backend services that mediate
    capability requests on behalf of agents."""

    def __init__(self, default_decision: CapDecision = CapDecision.DENY):
        self._rules: list[CapGateRule] = []
        self._default = default_decision
        self._lock = threading.Lock()
        self._stats = CapGateStats()

    def add_rule(self, rule: CapGateRule) -> int:
        """Returns the rule's index (rules evaluated in insertion order)."""
        if not isinstance(rule, CapGateRule):
            raise TypeError("rule must be CapGateRule")
        if not isinstance(rule.capability, LinuxCapability):
            raise TypeError("rule.capability must be LinuxCapability")
        with self._lock:
            self._rules.append(rule)
            return len(self._rules) - 1

    def add_rules(self, rules: Iterable[CapGateRule]) -> list[int]:
        return [self.add_rule(r) for r in rules]

    def check(self, ctx: CapContext) -> CapCheckResult:
        if not isinstance(ctx, CapContext):
            raise TypeError("ctx must be CapContext")
        with self._lock:
            self._stats.total_checks += 1
            self._stats.by_capability[ctx.capability.value] = (
                self._stats.by_capability.get(ctx.capability.value, 0) + 1
            )
            for idx, rule in enumerate(self._rules):
                if rule.capability != ctx.capability:
                    continue
                if not rule.predicate(ctx):
                    continue
                if rule.decision == CapDecision.ALLOW:
                    self._stats.allowed += 1
                else:
                    self._stats.denied += 1
                return CapCheckResult(
                    decision=rule.decision,
                    matched_rule_index=idx,
                    reason=rule.reason,
                )
            # No rule matched — apply default.
            if self._default == CapDecision.ALLOW:
                self._stats.allowed += 1
                reason = "default_allow"
            else:
                self._stats.denied += 1
                reason = "default_deny_no_matching_rule"
            return CapCheckResult(
                decision=self._default,
                matched_rule_index=None,
                reason=reason,
            )

    def rule_count(self) -> int:
        with self._lock:
            return len(self._rules)

    def snapshot_stats(self) -> CapGateStats:
        with self._lock:
            return CapGateStats(
                total_checks=self._stats.total_checks,
                allowed=self._stats.allowed,
                denied=self._stats.denied,
                by_capability=dict(self._stats.by_capability),
                rate_limited=self._stats.rate_limited,
            )


__all__ = [
    "LinuxCapability",
    "CapDecision",
    "CapContext",
    "CapCheckResult",
    "CapGateStats",
    "CapGateRule",
    "CapGate",
    "predicate_process_match",
    "predicate_tool_call_match",
    "predicate_taint_at_most",
    "predicate_always",
    "predicate_rate_limit",
]
