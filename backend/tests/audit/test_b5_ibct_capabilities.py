"""
B5 — IBCT + agent capability-table boundary tests (TEST_PLAN_300 §B5)
=====================================================================

Adversarial boundary sweep over the two B5 capability primitives:

  * ``core/security/agent_capability_table.py`` — Ed25519-signed,
    fail-closed agent tool-call capabilities (Sprint 21 / B1).
  * ``core/security/ibct_engine.py`` — Invocation-Bound Capability Tokens
    with a Datalog delegation-graph fixpoint (Sprint 21 / B6+F6).

Goal: prove an unauthorized component cannot cross a capability boundary —
uncapped calls, over-scoped resources, permission escalation, expired or
forged tokens, cross-invocation replay, and authority amplification through
delegation are all refused (fail-closed).

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_b5_ibct_capabilities.py -v
"""
from __future__ import annotations

import dataclasses

import pytest

from core.security.agent_capability_table import (
    AgentCapabilityGate,
    CapabilityDenied,
    ToolPerm,
)
from core.security.ibct_engine import (
    CrossAgentInvokeDenied,
    IbctEngine,
    IbctError,
    TokenVerifyError,
)


class FakeClock:
    def __init__(self, t: float = 1_000.0):
        self._t = t

    def time(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


# ===========================================================================
# B5a — AgentCapabilityGate (signed tool-call capabilities)
# ===========================================================================


def _gate(clock=None):
    return AgentCapabilityGate.generate(clock=clock)


def test_b5_valid_capability_allows_in_scope():
    gate = _gate()
    gate.grant(agent_id="a7", tool="web.fetch",
               scope_prefix="https://corp.example/", perms=ToolPerm.RO)
    cap = gate.require_capability("a7", "web.fetch",
                                  resource="https://corp.example/data")
    assert cap.agent_id == "a7"


def test_b5_uncapped_call_is_refused():
    gate = _gate()
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch", resource="https://corp.example/x")


def test_b5_out_of_scope_resource_is_refused():
    gate = _gate()
    gate.grant(agent_id="a7", tool="web.fetch",
               scope_prefix="https://corp.example/", perms=ToolPerm.RO)
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch",
                                resource="https://evil.example/")


def test_b5_permission_escalation_is_refused():
    """A read-only capability must not satisfy a WRITE perm request."""
    gate = _gate()
    gate.grant(agent_id="a7", tool="fs", scope_prefix="/data/",
               perms=ToolPerm.RO)
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "fs", resource="/data/x", perm=ToolPerm.WRITE)


def test_b5_expired_capability_is_refused():
    clk = FakeClock()
    gate = _gate(clock=clk)
    gate.grant(agent_id="a7", tool="web.fetch",
               scope_prefix="https://corp.example/", ttl_seconds=60)
    clk.advance(61)
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch",
                                resource="https://corp.example/x")


def test_b5_forged_signature_is_refused():
    """Tampering the signature (forgery) must fail verification."""
    gate = _gate()
    cap = gate.grant(agent_id="a7", tool="web.fetch",
                     scope_prefix="https://corp.example/")
    forged = dataclasses.replace(cap, signature=b"\x00" * len(cap.signature))
    gate.revoke(cap.cap_id)
    gate.install(forged)
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch",
                                resource="https://corp.example/x")


def test_b5_capability_from_a_different_issuer_is_refused():
    """A cap signed by another gate's key must not be honoured (issuer pin)."""
    gate_a = _gate()
    gate_b = _gate()
    cap_b = gate_b.grant(agent_id="a7", tool="web.fetch",
                         scope_prefix="https://corp.example/")
    gate_a.install(cap_b)  # foreign-issued token planted into gate_a's table
    with pytest.raises(CapabilityDenied):
        gate_a.require_capability("a7", "web.fetch",
                                  resource="https://corp.example/x")


def test_b5_tampered_scope_is_refused():
    """Widening the scope after signing must break the signature."""
    gate = _gate()
    cap = gate.grant(agent_id="a7", tool="web.fetch",
                     scope_prefix="https://corp.example/")
    widened = dataclasses.replace(cap, scope_prefix="https://")  # widen scope
    gate.revoke(cap.cap_id)
    gate.install(widened)
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch", resource="https://evil.example/")


def test_b5_revoke_removes_authority():
    gate = _gate()
    cap = gate.grant(agent_id="a7", tool="web.fetch",
                     scope_prefix="https://corp.example/")
    gate.require_capability("a7", "web.fetch", resource="https://corp.example/x")
    assert gate.revoke(cap.cap_id) is True
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch", resource="https://corp.example/x")


def test_b5_prefix_scope_with_delimiter_blocks_sibling_host():
    """SAFE pattern: a trailing-'/' scope blocks a look-alike sibling host."""
    gate = _gate()
    gate.grant(agent_id="a7", tool="web.fetch",
               scope_prefix="https://corp.example/")
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a7", "web.fetch",
                                resource="https://corp.example.evil.com/steal")


def test_b5_prefix_confusion_without_delimiter_now_blocked():
    """FINDING B5-1 — NOW PATCHED (was S3 sharp-edge).

    A scope_prefix WITHOUT a trailing delimiter used to be escapable by a
    look-alike host because covers() did a bare str.startswith(). The fix
    (delimiter-aware _scope_prefix_covers) requires the match to end on a
    '/'/':' boundary, so a delimiter-less prefix no longer covers a rogue
    sibling host."""
    gate = _gate()
    gate.grant(agent_id="a7", tool="web.fetch",
               scope_prefix="https://corp.example")  # NO trailing slash
    with pytest.raises(CapabilityDenied):
        gate.require_capability(
            "a7", "web.fetch", resource="https://corp.example.evil.com/steal")


def test_b5_delimiterless_prefix_still_covers_real_boundary():
    """The B5-1 fix must NOT over-block: a delimiter-less prefix still covers
    a resource whose junction falls on a real '/' boundary."""
    gate = _gate()
    gate.grant(agent_id="a7", tool="web.fetch",
               scope_prefix="https://corp.example")  # NO trailing slash
    cap = gate.require_capability(
        "a7", "web.fetch", resource="https://corp.example/data")
    assert cap is not None
    # exact-match resource is also covered
    assert gate.require_capability(
        "a7", "web.fetch", resource="https://corp.example") is not None


# ===========================================================================
# B5b — IbctEngine (delegation graph + invocation-bound tokens)
# ===========================================================================


def test_b5_ibct_root_grant_is_authorized():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch", "fs.read"})
    assert eng.query("orch", "web.fetch") is True
    assert eng.query("orch", "fs.write") is False


def test_b5_ibct_delegated_tool_is_authorized_attenuated():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch", "fs.read"})
    eng.delegate("orch", "researcher", {"web.fetch"})
    assert eng.query("researcher", "web.fetch") is True
    # attenuation: fs.read was NOT delegated, so researcher does not hold it.
    assert eng.query("researcher", "fs.read") is False


def test_b5_ibct_cross_agent_invoke_success_returns_token():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch"})
    eng.delegate("orch", "researcher", {"web.fetch"})
    tok = eng.require_cross_agent_invoke(
        "orch", "researcher", "web.fetch", invocation_id="task-1")
    assert eng.verify_token(tok, invocation_id="task-1") is tok


def test_b5_ibct_caller_without_authority_is_refused():
    eng = IbctEngine()
    eng.delegate("orch", "researcher", {"web.fetch"})  # edge but no root grant
    with pytest.raises(CrossAgentInvokeDenied):
        eng.require_cross_agent_invoke(
            "orch", "researcher", "web.fetch", invocation_id="task-1")


def test_b5_ibct_invoke_outside_graph_is_refused():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch"})
    # no delegation edge orch->stranger
    with pytest.raises(CrossAgentInvokeDenied):
        eng.require_cross_agent_invoke(
            "orch", "stranger", "web.fetch", invocation_id="task-1")


def test_b5_ibct_delegation_cannot_amplify_authority():
    """A delegate cannot pass on a tool the delegator never held."""
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch"})
    eng.delegate("orch", "researcher", {"fs.write"})  # orch doesn't hold fs.write
    assert eng.query("researcher", "fs.write") is False
    with pytest.raises(CrossAgentInvokeDenied):
        eng.require_cross_agent_invoke(
            "orch", "researcher", "fs.write", invocation_id="t")


def test_b5_ibct_token_replay_across_invocation_is_refused():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch"})
    eng.delegate("orch", "researcher", {"web.fetch"})
    tok = eng.require_cross_agent_invoke(
        "orch", "researcher", "web.fetch", invocation_id="task-A")
    with pytest.raises(TokenVerifyError):
        eng.verify_token(tok, invocation_id="task-B")  # replay → refused


def test_b5_ibct_forged_token_mac_is_refused():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch"})
    eng.delegate("orch", "researcher", {"web.fetch"})
    tok = eng.require_cross_agent_invoke(
        "orch", "researcher", "web.fetch", invocation_id="task-A")
    forged = dataclasses.replace(tok, mac=b"\x00" * len(tok.mac))
    with pytest.raises(TokenVerifyError):
        eng.verify_token(forged, invocation_id="task-A")


def test_b5_ibct_delegation_cycle_terminates_and_adds_nothing():
    """A delegation cycle must not loop forever nor escalate authority."""
    eng = IbctEngine()
    eng.grant_root("a", {"t"})
    eng.delegate("a", "b", {"t"})
    eng.delegate("b", "a", {"t"})  # cycle a<->b
    # fixpoint terminates; both hold t, nobody gains a new tool.
    assert eng.query("a", "t") is True
    assert eng.query("b", "t") is True
    assert eng.query("a", "other") is False


def test_b5_ibct_self_delegation_and_missing_invocation_id_rejected():
    eng = IbctEngine()
    eng.grant_root("orch", {"web.fetch"})
    with pytest.raises(IbctError):
        eng.delegate("orch", "orch", {"web.fetch"})  # self-delegation
    eng.delegate("orch", "researcher", {"web.fetch"})
    with pytest.raises(IbctError):
        eng.require_cross_agent_invoke(
            "orch", "researcher", "web.fetch", invocation_id="")  # no invocation
