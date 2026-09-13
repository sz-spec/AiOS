"""
backend/tests/red_team/test_red_team_regression.py

Sprint 15 / Item F3 — Red-team regression sweep against the dual-LLM
defense (C2) and the actor-type provenance tagger.

Every fixture in fixtures.py is sent through the DualLLMRouter and must
be flagged. This is the standing regression suite — when we add a new
defense capability we add fixtures here; when an attack class is
disclosed in the wild we add it here before shipping the next release.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module(slug: str, rel_path: str):
    abs_path = _REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(f"vos3_red_team_{slug}", abs_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"vos3_red_team_{slug}"] = mod
    spec.loader.exec_module(mod)
    return mod


ql = _load_module("ql", "backend/services/quarantined_llm.py")
dr = _load_module("dr", "backend/ai/agents/dual_llm_router.py")
ap = _load_module("ap", "backend/services/audit_actor_provenance.py")
fixtures_mod = _load_module("fixtures", "backend/tests/red_team/fixtures.py")


class StubLLM:
    """Returns a benign summary regardless of input. The defense must
    flag attacks based on the INPUT pattern (not the output)."""

    def generate(self, prompt: str, *, max_tokens: int) -> str:
        return "benign summary"


def _make_router() -> "dr.DualLLMRouter":
    return dr.DualLLMRouter(quarantined_llm=ql.QuarantinedLLM(llm=StubLLM()))


# ---------------------------------------------------------------------------
# Red-team fixture regression sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    fixtures_mod.RED_TEAM_CORPUS,
    ids=[f.name for f in fixtures_mod.RED_TEAM_CORPUS],
)
def test_fixture_detector_flags_expected_indicators(fixture):
    """Detector must surface every expected indicator for each fixture.
    Regression guard against silent-bypass: if the detector regresses
    on a known attack class, this test fails immediately."""
    found = set(ql.detect_injection_indicators(fixture.payload))
    expected = set(fixture.expected_indicators)
    missing = expected - found
    assert not missing, (
        f"Fixture {fixture.name!r} ({fixture.source}) lost defense coverage: "
        f"detector failed to flag {missing!r} — found {found!r}"
    )


@pytest.mark.parametrize(
    "fixture",
    fixtures_mod.RED_TEAM_CORPUS,
    ids=[f.name for f in fixtures_mod.RED_TEAM_CORPUS],
)
def test_fixture_end_to_end_blocked_by_dual_llm_router(fixture):
    """End-to-end: every red-team fixture fed through the router must
    come back was_blocked=True."""
    router = _make_router()
    pkt = router.route_tool_result(
        tool_name="fetch_url",
        raw_output=fixture.payload,
    )
    assert pkt.was_blocked is True, (
        f"Fixture {fixture.name!r} ({fixture.source}) got through dual-LLM "
        f"defense — indicators were {pkt.blocked_indicators!r}. "
        f"Severity: {fixture.severity}, class: {fixture.class_id}"
    )


def test_critical_fixtures_are_all_blocked():
    """Sanity guard: every fixture marked severity=critical must be
    blocked. If a critical attack class slips through, the build fails."""
    router = _make_router()
    leaked = []
    for f in fixtures_mod.RED_TEAM_CORPUS:
        if f.severity != "critical":
            continue
        pkt = router.route_tool_result(tool_name="fetch_url", raw_output=f.payload)
        if not pkt.was_blocked:
            leaked.append(f.name)
    assert leaked == [], f"CRITICAL fixtures bypassed defense: {leaked}"


def test_fixture_lookup_helpers():
    f = fixtures_mod.fixture_by_name("echoleak_markdown_image")
    assert f.severity == "critical"
    assert "EchoLeak" in f.description

    with pytest.raises(KeyError):
        fixtures_mod.fixture_by_name("does_not_exist")

    by_class = fixtures_mod.fixtures_by_class("OWASP-A1: Prompt Injection")
    assert len(by_class) >= 3
    for fx in by_class:
        assert fx.class_id == "OWASP-A1: Prompt Injection"


# ---------------------------------------------------------------------------
# Bypass test (validates the router's escape hatch works as documented)
# ---------------------------------------------------------------------------


def test_bypass_context_manager_lets_attack_through():
    """When the test bypass is engaged, the router MUST return the
    raw output untouched — this confirms the bypass is functional so
    red-team tests can run pre-defense behavior on demand."""
    router = _make_router()
    attack = fixtures_mod.fixture_by_name("classic_ignore_previous").payload
    with dr.bypass_for_test():
        pkt = router.route_tool_result(tool_name="fetch_url", raw_output=attack)
    assert pkt.bypassed is True
    assert pkt.was_blocked is False
    assert pkt.safe_summary == attack


# ---------------------------------------------------------------------------
# Actor-type provenance tagging tests
# ---------------------------------------------------------------------------


def test_tag_audit_event_human():
    base = {"endpoint": "/api/chat", "ts": 1234567890}
    out = ap.tag_audit_event(base, actor_type=ap.HUMAN, actor_id="user_abc")
    assert out[ap.FIELD_ACTOR_TYPE] == "human"
    assert out[ap.FIELD_ACTOR_ID] == "user_abc"
    assert out[ap.FIELD_ACTOR_CHAIN] == []
    assert out[ap.FIELD_ACTOR_CONFIDENCE] == 0.7
    # Original dict not mutated.
    assert ap.FIELD_ACTOR_TYPE not in base


def test_tag_audit_event_agent_with_chain():
    base = {"endpoint": "/api/tool"}
    chain = ["agent:planner", "agent:web_searcher", "agent:summarizer"]
    out = ap.tag_audit_event(
        base,
        actor_type=ap.AGENT,
        actor_id="agent_planner_v3",
        actor_chain=chain,
    )
    # Auto-upgraded to agent_chain because chain has ≥2 entries.
    assert out[ap.FIELD_ACTOR_TYPE] == "agent_chain"
    assert out[ap.FIELD_ACTOR_CHAIN] == chain
    assert out[ap.FIELD_ACTOR_CONFIDENCE] == 1.0


def test_tag_audit_event_invalid_actor_type():
    with pytest.raises(ValueError, match="actor_type must be one of"):
        ap.tag_audit_event({}, actor_type="robot", actor_id="x")


def test_tag_audit_event_empty_actor_id():
    with pytest.raises(ValueError, match="actor_id must be a non-empty string"):
        ap.tag_audit_event({}, actor_type=ap.HUMAN, actor_id="")


def test_tag_audit_event_non_dict():
    with pytest.raises(TypeError, match="event must be dict"):
        ap.tag_audit_event("not a dict", actor_type=ap.HUMAN, actor_id="u")  # type: ignore[arg-type]


def test_tag_audit_event_confidence_clamped():
    out_high = ap.tag_audit_event(
        {}, actor_type=ap.HUMAN, actor_id="u", confidence=99.0
    )
    assert out_high[ap.FIELD_ACTOR_CONFIDENCE] == 1.0
    out_low = ap.tag_audit_event({}, actor_type=ap.HUMAN, actor_id="u", confidence=-5.0)
    assert out_low[ap.FIELD_ACTOR_CONFIDENCE] == 0.0


def test_derive_actor_type_from_mcp_context():
    class MockMCPCtx:
        actor_type = "agent"

    assert ap.derive_actor_type(mcp_context=MockMCPCtx()) == "agent"


def test_derive_actor_type_from_session_when_no_mcp():
    class MockSession:
        actor_type = "agent_chain"

    assert ap.derive_actor_type(user_session=MockSession()) == "agent_chain"


def test_derive_actor_type_defaults_human():
    assert ap.derive_actor_type() == "human"


def test_derive_actor_type_ignores_invalid_candidate():
    class MockCtx:
        actor_type = "robot"

    assert ap.derive_actor_type(mcp_context=MockCtx()) == "human"


def test_is_agent_chain():
    assert ap.is_agent_chain(["a", "b"]) is True
    assert ap.is_agent_chain(["a"]) is False
    assert ap.is_agent_chain([]) is False
    assert ap.is_agent_chain(None) is False
