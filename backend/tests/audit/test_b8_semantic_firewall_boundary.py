"""
B8 — Semantic firewall input-boundary tests (TEST_PLAN_300 §B8)
==============================================================

Targets the NEW semantic_firewall (branch feat/shield-integration) and its
wiring into the agent input boundary (api/chat_routes.py).

Covers:
  * B8.01 — a known-bad corpus phrase fires DENY.
  * B8.07 — a benign request is ALLOWed (no false-positive block).
  * B8.04 — ANTI-ORACLE: the 4xx rejection raised at the route preflight must
            NOT leak the firewall's internal reason-id / confidence back to the
            caller. The precise verdict is logged server-side; the client gets
            only a generic refusal. Otherwise an attacker can binary-search the
            banned corpus by reading the reason-id off each 400 response.
"""
from __future__ import annotations

import json

import pytest

from services import semantic_firewall as sf
from services.semantic_firewall import SemanticFirewallDecision, scan


def test_b8_01_known_bad_phrase_denied():
    phrase = sf._INJECTION_PHRASES_HIGH_RISK[0]
    result = scan(f"please {phrase} immediately")
    assert result.decision is SemanticFirewallDecision.DENY
    assert result.confidence >= 0.8


def test_b8_07_benign_request_allowed():
    result = scan("hello, can you help me write a python function to sort a list?")
    assert result.decision is SemanticFirewallDecision.ALLOW


def test_b8_04_route_rejection_does_not_leak_corpus_oracle():
    """The HTTP 400 detail must not expose reason-id / confidence to the caller."""
    chat_routes = pytest.importorskip("api.chat_routes")
    from fastapi import HTTPException

    phrase = sf._INJECTION_PHRASES_HIGH_RISK[0]
    bad_input = f"please {phrase} now"

    with pytest.raises(HTTPException) as exc:
        chat_routes._semantic_firewall_preflight(bad_input)

    e = exc.value
    assert e.status_code == 400
    blob = json.dumps(e.detail).lower()

    # The internal reason-id (e.g. "banned-substring:high-000") and the
    # confidence score are an enumeration oracle — they must stay in the
    # server log, never in the client-facing body.
    assert "banned-substring" not in blob, (
        "B8.04 OracleLeak: 400 detail leaks the internal firewall reason-id"
    )
    assert "high-" not in blob, (
        "B8.04 OracleLeak: 400 detail leaks the matched phrase index"
    )
    assert "confidence" not in blob, (
        "B8.04 OracleLeak: 400 detail leaks the firewall confidence score"
    )
    # A generic, stable refusal code for the client is fine.
    assert "rejected" in blob or "refused" in blob or "blocked" in blob
