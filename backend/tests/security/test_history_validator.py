"""
Tests for Sprint 23 (DEPTH) — Agent Execution-History Policy Gate
(backend/core/security/history_validator.py).

Pins the fail-closed contract: a pending tool-call that would complete a
forbidden multi-step sequence (read SECRET → external send, loop, rate) is
refused with ContextViolation; benign sequences and sends to trusted
destinations are allowed. Pure logic — 100% on macOS via pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.security.history_validator import (  # noqa: E402
    AgentExecutionHistoryValidator,
    ContextViolation,
    ENV_DEV_OVERRIDE,
    Operation,
    PredicateRule,
    RateCeilingRule,
    ToolLoopRule,
)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


def test_benign_sequence_allowed():
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={"PUBLIC"}))
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://x.example/")
    )
    assert out.tool == "net.send"
    assert v.stats.allowed == 2
    assert v.stats.blocked == 0


def test_secret_then_external_send_blocked():
    v = AgentExecutionHistoryValidator()
    v.require_operation(
        "a", Operation(tool="fs.read", labels={"SECRET"}, resource="/vault/key")
    )
    with pytest.raises(ContextViolation) as exc:
        v.require_operation(
            "a", Operation(tool="net.send", destination="https://evil.example/")
        )
    assert exc.value.rule_name == "secret-then-external-egress"
    assert v.stats.blocked == 1


def test_secret_then_send_to_trusted_dest_allowed():
    v = AgentExecutionHistoryValidator(
        trusted_destinations=frozenset({"internal.corp"})
    )
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://internal.corp/sink")
    )
    assert out.destination == "https://internal.corp/sink"
    assert v.stats.blocked == 0


def test_ring_buffer_bound_evicts_secret_then_allows():
    """Once the SECRET op falls out of the bounded ring, the external send
    is no longer gated by it."""
    v = AgentExecutionHistoryValidator(ring_size=3)
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    # Push enough benign ops to evict the SECRET op (ring_size=3).
    for _ in range(3):
        v.require_operation("a", Operation(tool="compute"))
    # SECRET op evicted from the ring → external send allowed.
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://evil.example/")
    )
    assert out.tool == "net.send"


def test_per_agent_isolation():
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    # Agent B never read a secret → its send is allowed.
    out = v.require_operation(
        "b", Operation(tool="net.send", destination="https://x.example/")
    )
    assert out.tool == "net.send"
    # Agent A is still gated.
    with pytest.raises(ContextViolation):
        v.require_operation(
            "a", Operation(tool="net.send", destination="https://x.example/")
        )


def test_tool_loop_rule_fires():
    v = AgentExecutionHistoryValidator(policy=(ToolLoopRule(max_repeats=3),))
    for _ in range(3):
        v.require_operation("a", Operation(tool="poll", destination="d"))
    with pytest.raises(ContextViolation) as exc:
        v.require_operation("a", Operation(tool="poll", destination="d"))
    assert exc.value.rule_name == "tool-call-loop"


def test_window_expiry_lets_secret_egress_through():
    clock = FakeClock()
    v = AgentExecutionHistoryValidator(window_seconds=60.0, clock=clock)
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    clock.now += 61  # SECRET read now outside the window
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://evil.example/")
    )
    assert out.tool == "net.send"


def test_window_within_still_blocks():
    clock = FakeClock()
    v = AgentExecutionHistoryValidator(window_seconds=60.0, clock=clock)
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    clock.now += 30  # still within window
    with pytest.raises(ContextViolation):
        v.require_operation(
            "a", Operation(tool="net.send", destination="https://evil.example/")
        )


def test_rate_ceiling_rule():
    v = AgentExecutionHistoryValidator(policy=(RateCeilingRule(max_ops=5),))
    for _ in range(5):
        v.require_operation("a", Operation(tool="op"))
    with pytest.raises(ContextViolation) as exc:
        v.require_operation("a", Operation(tool="op"))
    assert exc.value.rule_name == "rate-ceiling"


def test_custom_predicate_rule():
    def no_delete(ctx):
        if ctx.pending.tool == "fs.delete":
            return "deletes are forbidden for this agent class"
        return None

    v = AgentExecutionHistoryValidator(policy=(PredicateRule("no-delete", no_delete),))
    v.require_operation("a", Operation(tool="fs.read"))
    with pytest.raises(ContextViolation) as exc:
        v.require_operation("a", Operation(tool="fs.delete"))
    assert exc.value.rule_name == "no-delete"


def test_dev_override_downgrades_block(monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://evil.example/")
    )
    assert out.tool == "net.send"
    assert v.stats.dev_overrides_used == 1


def test_stats_blocked_by_rule():
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={"TOXIC"}))
    with pytest.raises(ContextViolation):
        v.require_operation(
            "a", Operation(tool="http.post", destination="https://evil.example/")
        )
    assert v.stats.blocked_by_rule.get("secret-then-external-egress") == 1


def test_empty_history_allows_and_input_validation():
    v = AgentExecutionHistoryValidator()
    assert v.require_operation(
        "a", Operation(tool="net.send", destination="https://x.example/")
    )
    with pytest.raises(ValueError):
        v.require_operation("", Operation(tool="x"))
    with pytest.raises(TypeError):
        v.require_operation("a", "not-an-operation")
