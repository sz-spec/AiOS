"""
Tests for AIVA API routes (/api/aiva/*).

Covers all 18+ endpoints in api/aiva_routes.py using a mocked AIVAService.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from api.aiva_routes import router as _aiva_router
from aiva.agent_builder import (
    AIAgent,
    AgentType,
    AgentArchitecture,
    AgentMode,
    AgentStatus,
    get_aiva_service,
)

# Mount AIVA router (not mounted in main.py for production yet)
_AIVA_PREFIX = "/api/aiva"
_mounted_aiva = False
for _route in app.routes:
    if hasattr(_route, "path") and _route.path.startswith(_AIVA_PREFIX):
        _mounted_aiva = True
        break
if not _mounted_aiva:
    app.include_router(_aiva_router, prefix="/api", tags=["AIVA-Test"])


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_agent():
    """A real AIAgent instance with deterministic IDs/timestamps."""
    agent = AIAgent()
    agent.id = "agent_test123"
    agent.name = "Test Agent"
    agent.description = "A test agent"
    agent.user_id = "user_123"
    agent.agent_type = AgentType.CUSTOM
    agent.architecture = AgentArchitecture.REACT
    agent.mode = AgentMode.ASSIST
    agent.status = AgentStatus.ACTIVE
    agent.tools = []
    agent.capabilities = []
    agent.model = "gpt-4"
    agent.system_prompt = ""
    agent.temperature = 0.7
    agent.created_at = datetime(2024, 1, 1)
    agent.updated_at = datetime(2024, 1, 1)
    agent.deployed_at = datetime(2024, 1, 1)
    return agent


@pytest.fixture(autouse=True)
def _override_user():
    """
    Override get_current_user so user.id == 'user_123', matching the
    mock_agent.user_id ownership. Required after the April 2026 IDOR fix
    added `_require_agent_owner` on per-agent endpoints — without the
    override the dev-mode user ('dev_seed_user') would mismatch.
    """
    from middleware.auth import get_current_user, AuthenticatedUser

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user_123", email="test@test.com"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def mock_aiva(mock_agent):
    """Inject a mock AIVAService for every test in this module."""
    svc = MagicMock()

    # Sync service methods
    svc.get_templates.return_value = [
        {
            "type": "custom",
            "name": "Custom",
            "description": "",
            "icon": "",
            "architecture": "react",
            "mode": "assist",
            "tools": [],
            "capabilities": [],
        }
    ]
    svc.get_available_tools.return_value = []
    svc.get_tools_by_category.return_value = []
    svc.create_from_description.return_value = mock_agent
    svc.create_from_template.return_value = mock_agent
    svc.set_architecture.return_value = mock_agent
    svc.set_tools.return_value = mock_agent
    svc.configure_security.return_value = mock_agent
    svc.add_escalation_rule.return_value = mock_agent
    svc.set_mode.return_value = mock_agent
    svc.deploy_agent.return_value = mock_agent
    svc.pause_agent.return_value = mock_agent
    svc.resume_agent.return_value = mock_agent
    svc.list_agents.return_value = [mock_agent]
    svc.get_agent.return_value = mock_agent
    svc.update_agent.return_value = mock_agent
    svc.delete_agent.return_value = True
    svc.get_test_conversation.return_value = []
    svc.clear_test_conversation.return_value = None

    # Async service methods
    svc.test_agent = AsyncMock(
        return_value={"message": "Hello!", "agent_id": "agent_test123"}
    )

    app.dependency_overrides[get_aiva_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_aiva_service, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


# =============================================================================
# Static list endpoints (no service call)
# =============================================================================


def test_list_architectures(client):
    resp = client.get("/api/aiva/architectures")
    assert resp.status_code == 200
    data = resp.json()
    assert "architectures" in data
    arch = data["architectures"]
    assert len(arch) > 0
    assert all("id" in a and "name" in a and "description" in a for a in arch)


def test_list_modes(client):
    resp = client.get("/api/aiva/modes")
    assert resp.status_code == 200
    data = resp.json()
    assert "modes" in data
    modes = data["modes"]
    assert len(modes) > 0
    assert all("id" in m and "name" in m and "use_case" in m for m in modes)


def test_list_agent_types(client):
    resp = client.get("/api/aiva/agent-types")
    assert resp.status_code == 200
    data = resp.json()
    assert "types" in data
    assert len(data["types"]) > 0
    assert all("id" in t for t in data["types"])


# =============================================================================
# Template & tool endpoints
# =============================================================================


def test_list_templates(client, mock_aiva):
    resp = client.get("/api/aiva/templates")
    assert resp.status_code == 200
    mock_aiva.get_templates.assert_called_once()
    assert "templates" in resp.json()


def test_list_tools_no_category(client, mock_aiva):
    resp = client.get("/api/aiva/tools")
    assert resp.status_code == 200
    mock_aiva.get_available_tools.assert_called_once()
    assert "tools" in resp.json()


def test_list_tools_with_valid_category(client, mock_aiva):
    resp = client.get("/api/aiva/tools?category=crm")
    assert resp.status_code == 200
    mock_aiva.get_tools_by_category.assert_called_once()


def test_list_tools_with_unknown_category_falls_back(client, mock_aiva):
    """Unknown category falls back to get_available_tools."""
    resp = client.get("/api/aiva/tools?category=UNKNOWN_CAT")
    assert resp.status_code == 200
    mock_aiva.get_available_tools.assert_called_once()


# =============================================================================
# Agent creation
# =============================================================================


def test_create_from_description_success(client, mock_aiva):
    resp = client.post(
        "/api/aiva/agents/from-description",
        json={
            "description": "A sales assistant for our CRM",
            "organization_id": "org_1",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "step" in data
    assert data["step"] == 1
    assert "agent" in data


def test_create_from_description_missing_body(client):
    resp = client.post("/api/aiva/agents/from-description", json={})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "agent_type", ["custom", "sales_assistant", "support_agent", "scheduler"]
)
def test_create_from_template_valid_type(client, mock_aiva, agent_type):
    resp = client.post(
        "/api/aiva/agents/from-template",
        json={
            "agent_type": agent_type,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["step"] == 1
    mock_aiva.create_from_template.assert_called()


def test_create_from_template_invalid_type(client):
    resp = client.post(
        "/api/aiva/agents/from-template",
        json={
            "agent_type": "not_a_real_type",
        },
    )
    assert resp.status_code == 400


# =============================================================================
# Agent CRUD
# =============================================================================


def test_list_agents(client, mock_aiva):
    resp = client.get("/api/aiva/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    assert data["total"] == 1
    mock_aiva.list_agents.assert_called_once()


def test_get_agent_found(client, mock_aiva):
    resp = client.get("/api/aiva/agents/agent_test123")
    assert resp.status_code == 200
    assert resp.json()["id"] == "agent_test123"


def test_get_agent_not_found(client, mock_aiva):
    mock_aiva.get_agent.return_value = None
    resp = client.get("/api/aiva/agents/nonexistent")
    assert resp.status_code == 404


def test_update_agent(client, mock_aiva):
    resp = client.patch("/api/aiva/agents/agent_test123", json={"name": "Updated Name"})
    assert resp.status_code == 200
    # Ownership check must have run (get_agent called before update_agent)
    mock_aiva.get_agent.assert_called()
    mock_aiva.update_agent.assert_called_once()


def test_update_agent_forbidden_field_rejected(client, mock_aiva):
    """Sending a non-allowlisted field (e.g. user_id) must be rejected with 422."""
    resp = client.patch("/api/aiva/agents/agent_test123", json={"user_id": "evil_user"})
    assert resp.status_code == 422
    mock_aiva.update_agent.assert_not_called()


def test_update_agent_temperature_out_of_range(client, mock_aiva):
    """temperature must be in [0.0, 2.0]; values outside that range yield 422."""
    resp = client.patch("/api/aiva/agents/agent_test123", json={"temperature": 5.0})
    assert resp.status_code == 422
    mock_aiva.update_agent.assert_not_called()


def test_delete_agent(client, mock_aiva):
    resp = client.delete("/api/aiva/agents/agent_test123")
    assert resp.status_code == 200
    data = resp.json()
    assert "agent_id" in data
    assert data["agent_id"] == "agent_test123"


def test_delete_agent_not_found(client, mock_aiva):
    mock_aiva.delete_agent.return_value = False
    resp = client.delete("/api/aiva/agents/nonexistent")
    assert resp.status_code == 404


# =============================================================================
# Configuration steps
# =============================================================================


@pytest.mark.parametrize(
    "architecture", ["simple", "react", "plan_execute", "multi_agent", "hierarchical"]
)
def test_set_architecture_valid(client, mock_aiva, architecture):
    resp = client.post(
        "/api/aiva/agents/agent_test123/architecture",
        json={"architecture": architecture},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["step"] == 2
    mock_aiva.set_architecture.assert_called()


def test_set_architecture_invalid(client):
    resp = client.post(
        "/api/aiva/agents/agent_test123/architecture",
        json={"architecture": "invalid_arch"},
    )
    assert resp.status_code == 400


def test_set_tools(client, mock_aiva):
    resp = client.post(
        "/api/aiva/agents/agent_test123/tools", json={"tool_ids": ["tool1", "tool2"]}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["step"] == 3
    mock_aiva.set_tools.assert_called_once_with("agent_test123", ["tool1", "tool2"])


def test_configure_security(client, mock_aiva):
    resp = client.post(
        "/api/aiva/agents/agent_test123/security",
        json={
            "require_approval_for": ["delete"],
            "blocked_topics": ["politics"],
            "pii_redaction": True,
            "max_actions_per_minute": 5,
            "human_in_loop_threshold": 0.8,
            "audit_all_actions": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["step"] == 5
    mock_aiva.configure_security.assert_called_once()


@pytest.mark.parametrize("mode", ["suggest", "assist", "act"])
def test_set_mode_valid(client, mock_aiva, mode):
    resp = client.post("/api/aiva/agents/agent_test123/mode", json={"mode": mode})
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert data["mode"] == mode


def test_set_mode_invalid(client):
    resp = client.post("/api/aiva/agents/agent_test123/mode", json={"mode": "fly"})
    assert resp.status_code == 400


# =============================================================================
# Testing endpoints
# =============================================================================


def test_test_agent(client, mock_aiva):
    resp = client.post("/api/aiva/agents/agent_test123/test", json={"message": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert "step" in data
    assert data["step"] == 4
    mock_aiva.test_agent.assert_awaited_once()


def test_get_test_conversation(client, mock_aiva):
    resp = client.get("/api/aiva/agents/agent_test123/test/conversation")
    assert resp.status_code == 200
    assert "conversation" in resp.json()
    mock_aiva.get_test_conversation.assert_called_once_with("agent_test123")


def test_clear_test_conversation(client, mock_aiva):
    resp = client.delete("/api/aiva/agents/agent_test123/test/conversation")
    assert resp.status_code == 200
    mock_aiva.clear_test_conversation.assert_called_once_with("agent_test123")


# =============================================================================
# Lifecycle endpoints
# =============================================================================


def test_deploy_agent(client, mock_aiva):
    resp = client.post("/api/aiva/agents/agent_test123/deploy")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["step"] == 6


def test_pause_agent(client, mock_aiva):
    resp = client.post("/api/aiva/agents/agent_test123/pause")
    assert resp.status_code == 200
    assert "status" in resp.json()
    mock_aiva.pause_agent.assert_called_once_with("agent_test123")


def test_resume_agent(client, mock_aiva):
    resp = client.post("/api/aiva/agents/agent_test123/resume")
    assert resp.status_code == 200
    assert "status" in resp.json()
    mock_aiva.resume_agent.assert_called_once_with("agent_test123")
    mock_aiva.deploy_agent.assert_not_called()


# =============================================================================
# Escalation
# =============================================================================


def test_add_escalation_rule(client, mock_aiva):
    mock_aiva.mock_agent_with_rules = MagicMock()
    # Return agent with one escalation rule
    from aiva.agent_builder import EscalationRule

    rule = EscalationRule(
        condition="user is angry", action="notify", target="manager", priority="high"
    )
    mock_aiva.mock_agent_with_rules.escalation_rules = [rule]
    mock_aiva.add_escalation_rule.return_value = mock_aiva.mock_agent_with_rules

    resp = client.post(
        "/api/aiva/agents/agent_test123/escalation-rules",
        json={
            "condition": "user is angry",
            "action": "notify",
            "target": "manager",
            "priority": "high",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    mock_aiva.add_escalation_rule.assert_called_once()


# =============================================================================
# Coverage: 404 paths
# =============================================================================


def test_pause_agent_not_found(client, mock_aiva):
    mock_aiva.pause_agent.return_value = None
    resp = client.post("/api/aiva/agents/nonexistent/pause")
    assert resp.status_code == 404


def test_deploy_agent_not_found(client, mock_aiva):
    mock_aiva.deploy_agent.return_value = None
    resp = client.post("/api/aiva/agents/nonexistent/deploy")
    assert resp.status_code == 404


def test_resume_agent_not_found(client, mock_aiva):
    mock_aiva.resume_agent.return_value = None
    resp = client.post("/api/aiva/agents/nonexistent/resume")
    assert resp.status_code == 404
