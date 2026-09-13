"""
backend/core/security/history_validator.py
===========================================

Sprint 23 (DEPTH) — Agent Execution-History Policy Gate.

Accounting note (READ FIRST — no count inflation)
-------------------------------------------------

This module is **DEPTH**, not a new catalog row. The moat stays 49/80. It
is the real-time, *blocking* counterpart to G4
(``backend/services/reasoning_audit.py``), which only *detects* anomalies
*after the fact*, and it adds the temporal/multi-step dimension that B1
(``agent_capability_table.py``, single-call capabilities) and B6
(``ibct_engine.py``, cross-agent delegation) do not model. It does NOT
claim to close B5/F5 (already closed) or any new row.

What this is
------------

Each tool-call an agent makes can be individually permitted yet form a
forbidden *sequence*: the classic exfiltration chain is "read SECRET data"
→ "send to an external destination" — each step is fine alone, the
sequence is not. This gate keeps a bounded per-agent ring buffer of recent
operations and, before each new tool-call, evaluates the (history + pending
op) against a policy matrix. If a forbidden sequence would be completed it
**fail-closes** by raising ``ContextViolation``; otherwise it records the
op and allows it.

Enforcement contract
--------------------

    v = AgentExecutionHistoryValidator()          # ships DEFAULT_POLICY
    v.require_operation("agent-7",
        Operation(tool="fs.read", labels={"SECRET"}))            # allowed (recorded)
    v.require_operation("agent-7",
        Operation(tool="net.send", destination="https://evil.example/"))
        # raises ContextViolation — SECRET was read, now an external send

A send to an operator-declared *trusted* destination is allowed even after
a SECRET read (the data stays inside the trust boundary). The policy matrix
is data: operators add/replace ``SequenceRule`` entries.

Honest scope ceiling
--------------------

  - Policy/decision layer. Like B1/B6, it only governs calls that route
    through ``require_operation`` — it does not stop code that bypasses the
    gate. Pair with the kernel capability gate for unbypassable
    enforcement.
  - Label strings ("SECRET"/"TOXIC"/"UNTRUSTED"/"PUBLIC") are the canonical
    names from the C7 ``TaintLabel`` enum, used here as plain strings to
    keep this module dependency-free (matching the self-contained
    ``agent_capability_table.py`` / ``ibct_engine.py`` siblings).
  - Dev escape hatch ``VOS3_HISTORY_DEV_OVERRIDE=1`` downgrades a block to a
    loud WARNING + allow. Never set it in production.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_HISTORY_DEV_OVERRIDE"

DEFAULT_RING_SIZE = 256
DEFAULT_WINDOW_SECONDS = 60.0

# Canonical sensitivity label strings (mirror C7 TaintLabel names).
SECRET_LABELS = frozenset({"SECRET", "TOXIC"})

# Tool names that constitute an outbound/egress action. Operators extend.
DEFAULT_EGRESS_TOOLS = frozenset(
    {
        "net.send",
        "http.post",
        "http.put",
        "http.request",
        "webhook",
        "email.send",
        "dns.query",
        "socket.send",
    }
)


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class ClockProtocol(Protocol):
    def time(self) -> float: ...


class SystemClock:
    def time(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Operation + errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    tool: str
    destination: str = ""
    labels: frozenset = field(default_factory=frozenset)
    resource: str = ""
    timestamp: float = 0.0  # filled in by the validator on record

    def with_timestamp(self, ts: float) -> "Operation":
        return Operation(
            tool=self.tool,
            destination=self.destination,
            labels=self.labels,
            resource=self.resource,
            timestamp=ts,
        )

    @property
    def carries_secret(self) -> bool:
        return bool(set(self.labels) & SECRET_LABELS)


class ContextViolation(Exception):
    """Raised by require_operation() when the pending op would complete a
    forbidden multi-step sequence. Fail-closed."""

    def __init__(self, rule_name: str, reason: str):
        self.rule_name = rule_name
        self.reason = reason
        super().__init__(f"[{rule_name}] {reason}")


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleContext:
    history: tuple  # tuple[Operation, ...] (oldest → newest)
    pending: Operation
    now: float
    trusted_destinations: frozenset
    window_seconds: float
    egress_tools: frozenset

    def is_external_egress(self, op: Operation) -> bool:
        """An op is external egress if it uses an egress tool OR names a
        destination, and that destination is not operator-trusted."""
        looks_egress = op.tool in self.egress_tools or bool(op.destination)
        if not looks_egress:
            return False
        return not _is_trusted(op.destination, self.trusted_destinations)


class SequenceRule(Protocol):
    name: str

    def evaluate(self, ctx: RuleContext) -> Optional[str]:
        """Return a reason string to BLOCK, or None to allow."""
        ...


@dataclass(frozen=True)
class SecretThenEgressRule:
    """Block an external egress when a SECRET/TOXIC op is in recent history."""

    name: str = "secret-then-external-egress"

    def evaluate(self, ctx: RuleContext) -> Optional[str]:
        if not ctx.is_external_egress(ctx.pending):
            return None
        for op in ctx.history:
            if op.carries_secret and (ctx.now - op.timestamp) <= ctx.window_seconds:
                return (
                    f"external egress to {ctx.pending.destination or ctx.pending.tool!r} "
                    f"after a {sorted(set(op.labels) & SECRET_LABELS)} op "
                    f"({op.tool!r}) within {ctx.window_seconds:.0f}s — exfil chain"
                )
        return None


@dataclass(frozen=True)
class ToolLoopRule:
    """Block when the same (tool, destination) repeats too often in-window."""

    name: str = "tool-call-loop"
    max_repeats: int = 5

    def evaluate(self, ctx: RuleContext) -> Optional[str]:
        key = (ctx.pending.tool, ctx.pending.destination)
        count = 1  # the pending one
        for op in ctx.history:
            if (op.tool, op.destination) == key and (
                ctx.now - op.timestamp
            ) <= ctx.window_seconds:
                count += 1
        if count > self.max_repeats:
            return (
                f"tool {ctx.pending.tool!r} repeated {count}x within "
                f"{ctx.window_seconds:.0f}s (max {self.max_repeats}) — loop"
            )
        return None


@dataclass(frozen=True)
class RateCeilingRule:
    """Block when an agent exceeds a total op rate in-window."""

    name: str = "rate-ceiling"
    max_ops: int = 100

    def evaluate(self, ctx: RuleContext) -> Optional[str]:
        recent = 1 + sum(
            1 for op in ctx.history if (ctx.now - op.timestamp) <= ctx.window_seconds
        )
        if recent > self.max_ops:
            return (
                f"{recent} ops within {ctx.window_seconds:.0f}s exceeds rate "
                f"ceiling {self.max_ops}"
            )
        return None


@dataclass(frozen=True)
class PredicateRule:
    """Operator-defined rule from a simple callable, for custom matrices."""

    name: str
    fn: Callable[[RuleContext], Optional[str]]

    def evaluate(self, ctx: RuleContext) -> Optional[str]:
        return self.fn(ctx)


def default_policy() -> tuple:
    return (SecretThenEgressRule(), ToolLoopRule(), RateCeilingRule())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class HistoryStats:
    operations: int = 0
    allowed: int = 0
    blocked: int = 0
    dev_overrides_used: int = 0
    blocked_by_rule: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


@dataclass
class AgentExecutionHistoryValidator:
    """Fail-closed real-time gate over a bounded per-agent operation history."""

    policy: tuple = field(default_factory=default_policy)
    ring_size: int = DEFAULT_RING_SIZE
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    trusted_destinations: frozenset = field(default_factory=frozenset)
    egress_tools: frozenset = field(default_factory=lambda: DEFAULT_EGRESS_TOOLS)
    clock: ClockProtocol = field(default_factory=SystemClock)
    stats: HistoryStats = field(default_factory=HistoryStats)
    _histories: dict = field(default_factory=dict)  # agent_id -> deque[Operation]
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _history_for(self, agent_id: str) -> deque:
        dq = self._histories.get(agent_id)
        if dq is None:
            dq = deque(maxlen=self.ring_size)
            self._histories[agent_id] = dq
        return dq

    def history(self, agent_id: str) -> tuple:
        with self._lock:
            return tuple(self._histories.get(agent_id, ()))

    def require_operation(self, agent_id: str, operation: Operation) -> Operation:
        """Evaluate the pending operation against the agent's history + the
        policy matrix. On the first rule that fires, raise ContextViolation
        (fail-closed). Otherwise record the op (timestamped) and return it.
        Dev override downgrades a block to a loud WARNING + allow."""
        if not agent_id:
            raise ValueError("agent_id is required")
        if not isinstance(operation, Operation):
            raise TypeError("operation must be an Operation")

        now = self.clock.time()
        stamped = operation.with_timestamp(now)
        with self._lock:
            self.stats.operations += 1
            hist = tuple(self._history_for(agent_id))

        ctx = RuleContext(
            history=hist,
            pending=stamped,
            now=now,
            trusted_destinations=self.trusted_destinations,
            window_seconds=self.window_seconds,
            egress_tools=self.egress_tools,
        )

        for rule in self.policy:
            reason = rule.evaluate(ctx)
            if reason:
                return self._handle_block(agent_id, stamped, rule.name, reason)

        # Allowed → record.
        with self._lock:
            self._history_for(agent_id).append(stamped)
            self.stats.allowed += 1
        return stamped

    def _handle_block(
        self, agent_id: str, op: Operation, rule_name: str, reason: str
    ) -> Operation:
        if self._env_true(ENV_DEV_OVERRIDE):
            with self._lock:
                self.stats.dev_overrides_used += 1
                self.stats.allowed += 1
                self._history_for(agent_id).append(op)
            logger.warning(
                "[history_validator] agent=%s op=%s would be BLOCKED by %s "
                "(%s) but allowed by DEV OVERRIDE (%s). NEVER set in production.",
                agent_id,
                op.tool,
                rule_name,
                reason,
                ENV_DEV_OVERRIDE,
            )
            return op
        with self._lock:
            self.stats.blocked += 1
            self.stats.blocked_by_rule[rule_name] = (
                self.stats.blocked_by_rule.get(rule_name, 0) + 1
            )
        logger.error(
            "[history_validator] agent=%s op=%s REFUSED (fail-closed) by %s: %s",
            agent_id,
            op.tool,
            rule_name,
            reason,
        )
        raise ContextViolation(rule_name, reason)

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


def _is_trusted(destination: str, trusted: frozenset) -> bool:
    if not destination:
        return False
    dest = destination.strip().lower()
    host = dest.split("://", 1)[-1].split("/", 1)[0].split("@")[-1].split(":", 1)[0]
    for t in trusted:
        t = t.strip().lower()
        if not t:
            continue
        if dest == t or host == t:
            return True
        if t.startswith(".") and host.endswith(t):
            return True
        if host == t.lstrip("*."):
            return True
    return False


__all__ = [
    "Operation",
    "ContextViolation",
    "RuleContext",
    "SequenceRule",
    "SecretThenEgressRule",
    "ToolLoopRule",
    "RateCeilingRule",
    "PredicateRule",
    "default_policy",
    "HistoryStats",
    "AgentExecutionHistoryValidator",
    "ClockProtocol",
    "SystemClock",
    "SECRET_LABELS",
    "DEFAULT_EGRESS_TOOLS",
    "ENV_DEV_OVERRIDE",
]
