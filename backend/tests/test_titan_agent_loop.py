"""
VOS3 v20.5 — TITAN Agent-Loop Sovereignty Tests
================================================

Verifies that when `VOS3_DEFAULT_LOCAL_FIRST=true` (or LOCAL_ONLY is
asserted), a full multi-agent relay (Architect → Developer → Reviewer)
runs entirely on local-titan and **never touches** Anthropic or OpenAI
provider modules. Cloud client instantiation is the failure signal: if
ChatAnthropic.__init__ or ChatOpenAI.__init__ runs during a TITAN-only
loop, the test fails loudly.

Also exercises:
  - Privacy mandate + TITAN-down → HTTP 503 + Retry-After: 30
  - VOS3_DEFAULT_LOCAL_FIRST + TITAN-up → local-titan for all non-critical
  - X-VOS3-Attestation header generation
  - LOCAL_EXECUTION audit event (vos3.agent_orchestration logger)

Source-shape contract — does not require torch / unsloth / ML deps.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: assert no cloud client gets instantiated
# ---------------------------------------------------------------------------


class _CloudClientCounter:
    """Tracks every attempt to instantiate ChatAnthropic / ChatOpenAI.
    Test asserts the counter stays at 0 throughout the agent loop."""

    def __init__(self):
        self.anthropic_inits = 0
        self.openai_inits = 0
        self.gemini_inits = 0

    def crash_anthropic(self, *args, **kwargs):
        self.anthropic_inits += 1
        raise AssertionError(
            "ChatAnthropic instantiated during TITAN-only loop — "
            "sovereignty contract violation"
        )

    def crash_openai(self, *args, **kwargs):
        self.openai_inits += 1
        raise AssertionError(
            "ChatOpenAI instantiated during TITAN-only loop — "
            "sovereignty contract violation"
        )

    def crash_gemini(self, *args, **kwargs):
        self.gemini_inits += 1
        raise AssertionError(
            "ChatGoogleGenerativeAI instantiated during TITAN-only loop — "
            "sovereignty contract violation"
        )


# ---------------------------------------------------------------------------
# Test 1: Architect → Developer → Reviewer relay routes to local-titan
# ---------------------------------------------------------------------------


def test_full_agent_relay_routes_to_local_titan():
    """Simulate an 11-agent pipeline (3-agent subset: Architect plans →
    Developer writes → Reviewer audits). With VOS3_DEFAULT_LOCAL_FIRST=true
    AND TITAN healthy, every non-critical-role assignment must return
    local-titan. Critical roles (architect, reviewer) remain on cloud
    UNLESS local_only is asserted.

    NB: 'architect' and 'reviewer' are critical roles per
    src.efficiency.router._CRITICAL_ROLES. The pipeline simulation
    distinguishes the role-routing-policy answer from the cloud-bypass
    answer: critical roles default to cloud, but TITAN-only requests
    force local even for critical roles.
    """
    counter = _CloudClientCounter()
    from src.efficiency.router import get_optimal_model

    pipeline = [
        ("architect", 5),
        ("developer", 5),
        ("reviewer", 5),
    ]

    with patch.dict(
        os.environ, {"VOS3_DEFAULT_LOCAL_FIRST": "true"}, clear=False
    ), patch("src.efficiency.router._ollama_up_safe", return_value=True), patch(
        "langchain_anthropic.ChatAnthropic",
        side_effect=counter.crash_anthropic,
        create=True,
    ), patch(
        "langchain_openai.ChatOpenAI", side_effect=counter.crash_openai, create=True
    ):

        # Default-local-first WITHOUT explicit local_only — critical roles
        # still go to cloud for quality preservation.
        chosen_default = {
            role: get_optimal_model(role, comp) for role, comp in pipeline
        }
        assert chosen_default["developer"] == "local-titan", (
            f"Developer must route to local-titan under default-local-first, "
            f"got {chosen_default['developer']!r}"
        )
        # Critical roles preserve cloud unless explicitly forbidden.
        assert chosen_default["architect"] != "local-titan"
        assert chosen_default["reviewer"] != "local-titan"

        # Now force local_only — even critical roles must route to TITAN.
        chosen_local_only = {
            role: get_optimal_model(role, comp, local_only=True)
            for role, comp in pipeline
        }
        for role, model in chosen_local_only.items():
            assert (
                model == "local-titan"
            ), f"local_only=True must force {role} to local-titan, got {model!r}"

    # Sovereignty assertion — no cloud client was constructed.
    assert counter.anthropic_inits == 0, (
        f"ChatAnthropic was instantiated {counter.anthropic_inits} times "
        f"during TITAN-only loop"
    )
    assert counter.openai_inits == 0, (
        f"ChatOpenAI was instantiated {counter.openai_inits} times "
        f"during TITAN-only loop"
    )


# ---------------------------------------------------------------------------
# Test 2: TITAN handshake — health check passes
# ---------------------------------------------------------------------------


def test_titan_health_check_uses_titan_endpoint():
    from services.agent_orchestration import titan_health_check, _titan_endpoint

    # When OLLAMA_TITAN_ENDPOINT is unset, falls back to OLLAMA_BASE_URL
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OLLAMA_TITAN_ENDPOINT", None)
        os.environ["OLLAMA_BASE_URL"] = "http://example.invalid:99999"
        assert _titan_endpoint() == "http://example.invalid:99999"

    # When OLLAMA_TITAN_ENDPOINT is set, takes precedence
    with patch.dict(
        os.environ,
        {
            "OLLAMA_TITAN_ENDPOINT": "http://titan.local:11434",
            "OLLAMA_BASE_URL": "http://other.local:11434",
        },
    ):
        assert _titan_endpoint() == "http://titan.local:11434"

    # When both unreachable, health check returns False
    with patch.dict(
        os.environ,
        {
            "OLLAMA_TITAN_ENDPOINT": "http://does-not-resolve.invalid:11434",
        },
    ):
        assert titan_health_check() is False


# ---------------------------------------------------------------------------
# Test 3: Privacy mandate + TITAN-down → 503 (NOT cloud fallback)
# ---------------------------------------------------------------------------


def test_privacy_mandate_with_titan_down_raises_503():
    """When the EU AI Act gate fires AND TITAN is unreachable AND no
    sovereign cloud is configured, the orchestrator MUST raise 503
    with Retry-After: 30. Silent cloud fallback is a compliance violation."""
    from services.agent_orchestration import orchestrate_pre_flight
    from services.request_manifest import LocalInferenceUnavailableError
    from middleware.auth import AuthenticatedUser

    eu_user = AuthenticatedUser(
        id="test_eu_titan_down",
        region_code="DE",
        is_eu_region=True,
        global_cloud_consent=False,
    )

    with patch(
        "services.agent_orchestration.titan_health_check", return_value=False
    ), patch(
        "services.regional_policy._check_ollama_available", return_value=False
    ), patch.dict(
        os.environ, {"VOS3_EU_SOVEREIGN_CLOUD_URL": ""}, clear=False
    ):
        try:
            orchestrate_pre_flight(
                role="developer",
                complexity=5,
                user=eu_user,
            )
            pytest.fail(
                "Expected LocalInferenceUnavailableError when TITAN down + "
                "privacy mandate active"
            )
        except LocalInferenceUnavailableError as e:
            assert e.status_code == 503
            assert e.headers.get("Retry-After") == "30"
            # Detail body must EXPLICITLY forbid cloud fallback (rather than
            # silently substituting it). The error message must contain a
            # negation phrase about cloud routing.
            detail_str = str(e.detail).lower()
            assert any(
                phrase in detail_str
                for phrase in (
                    "cloud fallback is not permitted",
                    "not permitted",
                    "non-sovereign cloud",
                )
            ), f"503 detail must explicitly forbid cloud fallback; got: {detail_str}"


# ---------------------------------------------------------------------------
# Test 4: X-VOS3-Attestation header is generated for every routing decision
# ---------------------------------------------------------------------------


def test_attestation_header_format():
    from services.agent_orchestration import orchestrate_pre_flight

    with patch.dict(
        os.environ, {"VOS3_DEFAULT_LOCAL_FIRST": "true"}, clear=False
    ), patch(
        "services.agent_orchestration.titan_health_check", return_value=True
    ), patch(
        "src.efficiency.router._ollama_up_safe", return_value=True
    ):
        decision = orchestrate_pre_flight(role="developer", complexity=5)
        header = decision.to_attestation_header()

    # Header must be parseable as pipe-delimited key=value pairs.
    parts = dict(p.split("=", 1) for p in header.split("|") if "=" in p)
    assert parts.get("model") == "local-titan"
    assert parts.get("is_local") == "true"
    assert parts.get("is_titan") == "true"
    assert parts.get("reason") == "default_local_first"
    assert "ts" in parts
    # Local-titan executions carry the LOCAL_EXECUTION label hash.
    assert "label_hash" in parts
    assert len(parts["label_hash"]) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# Test 5: LOCAL_EXECUTION event is emitted by record_local_execution
# ---------------------------------------------------------------------------


def test_local_execution_event_logged():
    from services.agent_orchestration import (
        record_local_execution,
        AgentRoutingDecision,
        LOCAL_EXECUTION_LABEL_HASH,
    )

    decision = AgentRoutingDecision(
        role="developer",
        chosen_model="local-titan",
        is_local=True,
        is_titan=True,
        titan_endpoint="http://localhost:11434",
        health_check_passed=True,
        decision_reason="default_local_first",
        timestamp_ms=1714000000000,
    )

    # Capture the structured log entry.
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    log = logging.getLogger("vos3.agent_orchestration")
    handler = _Capture()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        result = record_local_execution(decision, user_id="test_user")
        assert result is True

        # The label hash + role + model must appear in the log.
        joined = " ".join(captured)
        assert "OP_LOCAL_EXECUTION" in joined
        assert LOCAL_EXECUTION_LABEL_HASH in joined
        assert "developer" in joined
        assert "local-titan" in joined
    finally:
        log.removeHandler(handler)


# ---------------------------------------------------------------------------
# Test 6: Cloud-routed decision does NOT log LOCAL_EXECUTION
# ---------------------------------------------------------------------------


def test_local_execution_not_logged_for_cloud_path():
    """record_local_execution must early-return False for cloud routing
    so the MMR ledger is not polluted with non-local events."""
    from services.agent_orchestration import (
        record_local_execution,
        AgentRoutingDecision,
    )

    cloud_decision = AgentRoutingDecision(
        role="architect",
        chosen_model="claude-sonnet",
        is_local=False,
        is_titan=False,
        titan_endpoint=None,
        health_check_passed=True,
        decision_reason="cloud_standard",
        timestamp_ms=1714000000000,
    )
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    log = logging.getLogger("vos3.agent_orchestration")
    handler = _Capture()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        result = record_local_execution(cloud_decision, user_id="test_user")
        assert result is False
        assert not any(
            "OP_LOCAL_EXECUTION" in m for m in captured
        ), "Cloud-routed decision must NOT emit LOCAL_EXECUTION audit event"
    finally:
        log.removeHandler(handler)


# ---------------------------------------------------------------------------
# Test 7: VOS3_DEFAULT_LOCAL_FIRST disabled → standard cloud routing
# ---------------------------------------------------------------------------


def test_default_local_first_disabled_uses_cloud():
    """When VOS3_DEFAULT_LOCAL_FIRST is unset, non-critical roles must
    route via the standard cloud-first path (existing v20.4 behavior)."""
    from src.efficiency.router import get_optimal_model

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VOS3_DEFAULT_LOCAL_FIRST", None)
        with patch("src.efficiency.router._ollama_up_safe", return_value=True):
            chosen = get_optimal_model(role="developer", complexity=5)
            assert chosen != "local-titan", (
                f"Without VOS3_DEFAULT_LOCAL_FIRST, must NOT default to "
                f"local-titan; got {chosen!r}"
            )


# ---------------------------------------------------------------------------
# Test 8: router.yaml has the local-titan slot
# ---------------------------------------------------------------------------


def test_router_yaml_has_local_titan_slot():
    """Verifies the v20.5 local-titan slot is registered in router.yaml."""
    import yaml

    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "router.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    assert (
        "local-titan" in cfg["models"]
    ), "router.yaml must register the local-titan slot for v20.5"
    titan = cfg["models"]["local-titan"]
    assert titan["enabled"] is True
    assert titan["provider"] == "ollama"
    assert titan["api_key_env"] == "OLLAMA_TITAN_ENDPOINT"
