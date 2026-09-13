"""
Tests for Sprint 21 / Item B1 — agent tool-call capability table
(backend/core/security/agent_capability_table.py).

Pins the fail-closed, unforgeable contract: a tool call is allowed only
under a valid, signed, unexpired, in-scope capability; uncapped /
out-of-scope / expired / forged calls are refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.security.agent_capability_table import (  # noqa: E402
    AgentCapability,
    AgentCapabilityGate,
    CapabilityDenied,
    ToolPerm,
    generate_keypair,
)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


def test_grant_then_require_allows_in_scope():
    gate = AgentCapabilityGate.generate()
    gate.grant(
        agent_id="agent-7",
        tool="web.fetch",
        scope_prefix="https://corp.example/",
        perms=ToolPerm.RO,
    )
    cap = gate.require_capability(
        "agent-7", "web.fetch", resource="https://corp.example/data"
    )
    assert cap.agent_id == "agent-7"
    assert gate.stats.allowed == 1


def test_uncapped_call_refused():
    gate = AgentCapabilityGate.generate()
    with pytest.raises(CapabilityDenied):
        gate.require_capability("agent-7", "web.fetch")


def test_out_of_scope_resource_refused():
    gate = AgentCapabilityGate.generate()
    gate.grant(
        agent_id="agent-7", tool="web.fetch", scope_prefix="https://corp.example/"
    )
    with pytest.raises(CapabilityDenied):
        gate.require_capability(
            "agent-7", "web.fetch", resource="https://evil.example/"
        )


def test_perm_not_granted_refused():
    gate = AgentCapabilityGate.generate()
    gate.grant(agent_id="agent-7", tool="fs", scope_prefix="/data/", perms=ToolPerm.RO)
    # RO does not include WRITE.
    with pytest.raises(CapabilityDenied):
        gate.require_capability(
            "agent-7", "fs", resource="/data/x", perm=ToolPerm.WRITE
        )


def test_wrong_agent_refused():
    gate = AgentCapabilityGate.generate()
    gate.grant(agent_id="agent-7", tool="web.fetch", scope_prefix="")
    with pytest.raises(CapabilityDenied):
        gate.require_capability("agent-8", "web.fetch")


def test_expired_capability_refused():
    clock = FakeClock()
    gate = AgentCapabilityGate.generate(clock=clock)
    gate.grant(agent_id="a", tool="t", scope_prefix="", ttl_seconds=60)
    assert gate.require_capability("a", "t")
    clock.now += 61
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a", "t")


def test_forged_signature_refused():
    gate = AgentCapabilityGate.generate()
    real = gate.grant(agent_id="a", tool="t", scope_prefix="")
    forged = AgentCapability(
        **{**real.__dict__, "perms": int(ToolPerm.RW), "signature": b"\x00" * 64}
    )
    gate2 = AgentCapabilityGate(
        issuer_private=gate.issuer_private, issuer_public=gate.issuer_public
    )
    gate2.install(forged)
    with pytest.raises(CapabilityDenied):
        gate2.require_capability("a", "t", perm=ToolPerm.WRITE)
    assert gate2.stats.bad_signature >= 1


def test_capability_from_foreign_issuer_refused():
    gate = AgentCapabilityGate.generate()
    foreign_priv, foreign_pub = generate_keypair()
    # A cap properly signed by a DIFFERENT issuer the gate doesn't trust.
    foreign_gate = AgentCapabilityGate(
        issuer_private=foreign_priv, issuer_public=foreign_pub
    )
    foreign_cap = foreign_gate.grant(agent_id="a", tool="t", scope_prefix="")
    gate.install(foreign_cap)
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a", "t")


def test_revoke_removes_capability():
    gate = AgentCapabilityGate.generate()
    cap = gate.grant(agent_id="a", tool="t", scope_prefix="")
    assert gate.require_capability("a", "t")
    assert gate.revoke(cap.cap_id) is True
    with pytest.raises(CapabilityDenied):
        gate.require_capability("a", "t")
