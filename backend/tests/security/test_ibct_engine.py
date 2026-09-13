"""
Tests for Sprint 21 / Item B6 (+F6) — IBCT engine + delegation-graph
Datalog policy (backend/core/security/ibct_engine.py).

Pins: authority flows along delegation edges and attenuates; cross-agent
invokes outside the proven graph are refused (fail-closed); tokens are
invocation-bound (no replay across invocations).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.security.ibct_engine import (  # noqa: E402
    CrossAgentInvokeDenied,
    IbctEngine,
    TokenVerifyError,
)


def test_root_grant_authorized():
    eng = IbctEngine()
    eng.grant_root("orchestrator", {"web.fetch", "fs.read"})
    assert eng.query("orchestrator", "web.fetch")
    assert not eng.query("orchestrator", "admin.delete")


def test_transitive_authorization_along_edges():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch", "fs.read"})
    eng.delegate("A", "B", {"web.fetch"})
    eng.delegate("B", "C", {"web.fetch"})
    assert eng.query("C", "web.fetch")  # A→B→C transitively
    assert not eng.query("C", "fs.read")  # never delegated down the chain


def test_attenuation_limits_delegated_scope():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch", "fs.read"})
    eng.delegate("A", "B", {"web.fetch"})  # only web.fetch passed
    assert eng.query("B", "web.fetch")
    assert not eng.query("B", "fs.read")


def test_delegation_without_held_authority_confers_nothing():
    """An edge from an agent that doesn't hold the tool confers nothing."""
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    eng.delegate("B", "C", {"fs.read"})  # B never had fs.read
    assert not eng.query("C", "fs.read")


def test_cross_agent_invoke_allowed_in_graph():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    eng.delegate("A", "B", {"web.fetch"})
    token = eng.require_cross_agent_invoke(
        "A", "B", "web.fetch", invocation_id="task-1"
    )
    assert token.agent_id == "B"
    assert eng.stats.invokes_allowed == 1


def test_cross_agent_invoke_refused_without_edge():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    with pytest.raises(CrossAgentInvokeDenied):
        eng.require_cross_agent_invoke("A", "B", "web.fetch", invocation_id="task-1")


def test_cross_agent_invoke_refused_when_caller_unauthorized():
    eng = IbctEngine()
    # B has an edge to C but B itself was never authorized for the tool.
    eng.delegate("B", "C", {"fs.read"})
    with pytest.raises(CrossAgentInvokeDenied):
        eng.require_cross_agent_invoke("B", "C", "fs.read", invocation_id="task-1")


def test_ibct_verifies_for_matching_invocation():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    eng.delegate("A", "B", {"web.fetch"})
    token = eng.require_cross_agent_invoke(
        "A", "B", "web.fetch", invocation_id="task-42"
    )
    assert eng.verify_token(token, invocation_id="task-42")
    assert eng.stats.tokens_verified == 1


def test_ibct_replay_in_other_invocation_refused():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    eng.delegate("A", "B", {"web.fetch"})
    token = eng.require_cross_agent_invoke(
        "A", "B", "web.fetch", invocation_id="task-42"
    )
    with pytest.raises(TokenVerifyError):
        eng.verify_token(token, invocation_id="task-99")


def test_ibct_tampered_mac_refused():
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    eng.delegate("A", "B", {"web.fetch"})
    token = eng.require_cross_agent_invoke(
        "A", "B", "web.fetch", invocation_id="task-42"
    )
    forged = type(token)(**{**token.__dict__, "mac": b"\x00" * 32})
    with pytest.raises(TokenVerifyError):
        eng.verify_token(forged, invocation_id="task-42")


def test_delegation_cycle_does_not_escalate():
    """A→B→A cycle must not invent authority neither side holds."""
    eng = IbctEngine()
    eng.grant_root("A", {"web.fetch"})
    eng.delegate("A", "B", {"web.fetch"})
    eng.delegate("B", "A", {"web.fetch"})
    assert eng.query("B", "web.fetch")
    assert not eng.query("A", "fs.read")
    assert not eng.query("B", "fs.read")
