"""
backend/services/audit_actor_provenance.py
============================================

Sprint 15 / Item F3 — Audit-record actor_type provenance.

What this is
------------

Per the May-2026 review at https://arxiv.org/html/2501.09674v1
("Authenticated Delegation and Authorized AI Agents"), regulator-grade
audit logs MUST be able to answer one question after the fact:

  "Was this action initiated by the human, by an autonomous agent acting
   on the human's behalf, or by an agent-to-agent chain?"

Today's syslog / journald / SaaS audit logs record the USER (the principal
the request was authenticated as) but not the ACTOR TYPE. This module
adds the field via a single chokepoint so every emitter records the same
shape:

    {
      ...event payload...,
      "vos3.actor.type": "human" | "agent" | "agent_chain",
      "vos3.actor.id":   "<stable ID of the actor>",
      "vos3.actor.chain": ["agent:planner", "agent:web_searcher"],
      "vos3.actor.confidence": 0.0..1.0,
    }

Why a single chokepoint
-----------------------

If every audit-emitter independently decides what "actor_type" means,
the field will drift and become useless for compliance reporting under
EU AI Act Annex IV §9 (which requires a tamper-evident log of what the
AI system did and who initiated each action).

`tag_audit_event()` is the only sanctioned API for adding actor
provenance to an audit record. Cross-links:
  - F2 (mcp_oauth_bridge.MCPAuthContext.actor_type) populates actor_type
    at the MCP boundary.
  - G1 (telemetry_genai.AgentSpanData.actor_type) populates actor_type
    on every gen_ai.agent.* span.
  - F3 (this module) is the audit-log emitter side.

Public surface
--------------

    tag_audit_event(event, *, actor_type, actor_id,
                    actor_chain=None, confidence=None) -> dict
        Returns a new dict (does not mutate input) with the four
        actor-provenance fields added.

    derive_actor_type(*, mcp_context=None, user_session=None) -> str
        Derives actor_type from request context using the precedence:
        1. mcp_context.actor_type if MCP-routed
        2. user_session.actor_type if direct user session
        3. default "human"

    is_agent_chain(actor_chain) -> bool
        Convenience: returns True if actor_chain contains ≥2 agent steps.

    HUMAN / AGENT / AGENT_CHAIN — actor_type constants.

Honest scope ceiling
--------------------

  - This module RECORDS provenance. It does not VERIFY the claim that
    a given event is "human-initiated"; it trusts the upstream context
    (Clerk session ⇒ human; MCP token ⇒ per token's actor_type claim;
    SPIFFE workload ⇒ agent). Verification of the claim is the
    authentication layer's job.

  - The 0.0..1.0 confidence score is informational. It captures how
    sure we are about the actor_type — e.g. a MCP token whose
    actor_type was inferred from absence of mcp.* claims gets a
    lower confidence than one with an explicit mcp.actor_type claim.

  - "agent_chain" is detected automatically when actor_chain has ≥2
    entries; callers don't need to set actor_type explicitly to
    "agent_chain" — they pass the chain and the module derives the type.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

# Canonical actor-type values (must match values in F2 + G1).
HUMAN = "human"
AGENT = "agent"
AGENT_CHAIN = "agent_chain"

_VALID_ACTOR_TYPES = frozenset({HUMAN, AGENT, AGENT_CHAIN})

# Field-name constants (must match telemetry_genai's ATTR_VOS_ACTOR_* and
# the EU AI Act Annex IV §9 schema vOS produces).
FIELD_ACTOR_TYPE = "vos3.actor.type"
FIELD_ACTOR_ID = "vos3.actor.id"
FIELD_ACTOR_CHAIN = "vos3.actor.chain"
FIELD_ACTOR_CONFIDENCE = "vos3.actor.confidence"


def tag_audit_event(
    event: dict[str, Any],
    *,
    actor_type: str,
    actor_id: str,
    actor_chain: Optional[Sequence[str]] = None,
    confidence: Optional[float] = None,
) -> dict[str, Any]:
    """Return a NEW dict (no mutation) with the four actor-provenance
    fields added.

    Auto-upgrades actor_type to AGENT_CHAIN when actor_chain has ≥2
    entries — even if the caller passed AGENT. This guarantees the
    field accurately reflects the actor topology.
    """
    if not isinstance(event, dict):
        raise TypeError(f"event must be dict, got {type(event).__name__}")
    if actor_type not in _VALID_ACTOR_TYPES:
        raise ValueError(
            f"actor_type must be one of {sorted(_VALID_ACTOR_TYPES)}, "
            f"got {actor_type!r}"
        )
    if not actor_id or not isinstance(actor_id, str):
        raise ValueError("actor_id must be a non-empty string")

    chain_list: list[str] = list(actor_chain) if actor_chain else []
    # Auto-upgrade to AGENT_CHAIN when ≥2 chain entries.
    if len(chain_list) >= 2 and actor_type == AGENT:
        actor_type = AGENT_CHAIN

    # Confidence default: 1.0 if explicit, 0.7 if inferred (no chain
    # info), capped to [0.0, 1.0].
    if confidence is None:
        conf_value = 1.0 if chain_list else 0.7
    else:
        conf_value = max(0.0, min(1.0, float(confidence)))

    new_event = dict(event)
    new_event[FIELD_ACTOR_TYPE] = actor_type
    new_event[FIELD_ACTOR_ID] = actor_id
    new_event[FIELD_ACTOR_CHAIN] = chain_list
    new_event[FIELD_ACTOR_CONFIDENCE] = conf_value
    return new_event


def derive_actor_type(
    *,
    mcp_context: Any = None,
    user_session: Any = None,
) -> str:
    """Derive actor_type from request context.

    Precedence:
      1. mcp_context.actor_type if an MCPAuthContext was passed
      2. user_session.actor_type if a session object was passed
      3. default HUMAN
    """
    if mcp_context is not None:
        candidate = getattr(mcp_context, "actor_type", None)
        if candidate in _VALID_ACTOR_TYPES:
            return candidate
    if user_session is not None:
        candidate = getattr(user_session, "actor_type", None)
        if candidate in _VALID_ACTOR_TYPES:
            return candidate
    return HUMAN


def is_agent_chain(actor_chain: Optional[Sequence[str]]) -> bool:
    """Convenience predicate — True iff chain has ≥2 entries."""
    if not actor_chain:
        return False
    return len(list(actor_chain)) >= 2


__all__ = [
    "HUMAN",
    "AGENT",
    "AGENT_CHAIN",
    "FIELD_ACTOR_TYPE",
    "FIELD_ACTOR_ID",
    "FIELD_ACTOR_CHAIN",
    "FIELD_ACTOR_CONFIDENCE",
    "tag_audit_event",
    "derive_actor_type",
    "is_agent_chain",
]
