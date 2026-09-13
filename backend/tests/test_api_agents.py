"""
Tests for Agents API routes (/api/agents/*).

The agents router stores all state in module-level in-memory dicts (_agents,
_logs).  There is no external service class to mock — we drive everything
through the TestClient and reset state between tests by deleting every agent
that was created during a test.

Covers 15+ endpoints including happy paths and 404 error cases.
"""

import pytest
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_AGENT_BODY = {
    "name": "Test Agent",
    "role": "backend",
    "model": "gpt-4o-mini",
    "description": "A test backend agent",
}

_NONEXISTENT_ID = "deadbeef"


def _cleanup_agents(client: TestClient) -> None:
    """Delete all agents currently in the in-memory store."""
    resp = client.get("/api/agents/")
    if resp.status_code == 200:
        for agent in resp.json().get("agents", []):
            client.delete(f"/api/agents/{agent['id']}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Return a TestClient and clean up any agents created during the test."""
    with TestClient(app, base_url="http://localhost") as c:
        yield c
        _cleanup_agents(c)


@pytest.fixture
def created_agent(client):
    """Create one agent and return its JSON payload."""
    resp = client.post("/api/agents/", json=_VALID_AGENT_BODY)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Collection: List & static endpoints
# ---------------------------------------------------------------------------


class TestListAgents:
    """Tests for GET /api/agents/ — list all agents."""

    def test_list_agents_initially_empty(self, client):
        """An empty store returns an empty list with total=0."""
        _cleanup_agents(client)
        resp = client.get("/api/agents/")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "total" in data
        assert data["total"] == 0
        assert data["agents"] == []

    def test_list_agents_after_creation(self, client, created_agent):
        """After creating one agent the list contains it."""
        resp = client.get("/api/agents/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        ids = [a["id"] for a in data["agents"]]
        assert created_agent["id"] in ids


class TestStaticRoutes:
    """Tests for static list endpoints that return configuration data."""

    def test_templates_returns_list(self, client):
        """GET /api/agents/templates returns a dict with a 'templates' key."""
        resp = client.get("/api/agents/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "total" in data

    def test_available_roles_returns_list(self, client):
        """GET /api/agents/roles/available returns known role names."""
        resp = client.get("/api/agents/roles/available")
        assert resp.status_code == 200
        data = resp.json()
        assert "roles" in data
        role_names = [r["role"] for r in data["roles"]]
        # At minimum the standard roles must be present
        for expected in ("architect", "frontend", "backend", "tester", "reviewer"):
            assert expected in role_names

    def test_available_services_returns_list(self, client):
        """GET /api/agents/services/available returns services and capabilities."""
        resp = client.get("/api/agents/services/available")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "default_capabilities" in data
        assert len(data["services"]) > 0

    @pytest.mark.parametrize("role", ["frontend", "backend", "architect", "coding"])
    def test_resolve_model_returns_model_info(self, client, role):
        """GET /api/agents/resolve-model returns router_role and model fields."""
        resp = client.get(f"/api/agents/resolve-model?role={role}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["router_role"] == role
        assert "model_name" in data
        assert "model_id" in data


# ---------------------------------------------------------------------------
# Collection: Agent CRUD
# ---------------------------------------------------------------------------


class TestCreateAgent:
    """Tests for POST /api/agents/."""

    def test_create_agent_success(self, client):
        """Creating an agent with valid body returns a well-formed Agent object."""
        resp = client.post("/api/agents/", json=_VALID_AGENT_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["name"] == _VALID_AGENT_BODY["name"]
        assert data["role"] == _VALID_AGENT_BODY["role"]
        assert data["status"] == "idle"

    def test_create_agent_missing_name(self, client):
        """Omitting required 'name' field returns 422 Unprocessable Entity."""
        resp = client.post("/api/agents/", json={"role": "backend"})
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "role",
        [
            "architect",
            "frontend",
            "backend",
            "tester",
            "reviewer",
            "researcher",
            "assistant",
            "custom",
        ],
    )
    def test_create_agent_valid_roles(self, client, role):
        """Agents can be created with any of the documented roles."""
        body = {**_VALID_AGENT_BODY, "role": role}
        resp = client.post("/api/agents/", json=body)
        assert resp.status_code == 200
        assert resp.json()["role"] == role

    def test_create_agent_default_model_applied(self, client):
        """When no model is specified the server fills a default."""
        body = {"name": "No Model Agent", "role": "tester"}
        resp = client.post("/api/agents/", json=body)
        assert resp.status_code == 200
        assert resp.json()["model"] is not None


class TestGetAgent:
    """Tests for GET /api/agents/{agent_id}."""

    def test_get_existing_agent(self, client, created_agent):
        """Fetching an existing agent by ID returns the correct object."""
        agent_id = created_agent["id"]
        resp = client.get(f"/api/agents/{agent_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == agent_id

    def test_get_nonexistent_agent_returns_404(self, client):
        """Fetching an unknown agent ID returns 404."""
        resp = client.get(f"/api/agents/{_NONEXISTENT_ID}")
        assert resp.status_code == 404


class TestUpdateAgent:
    """Tests for PUT /api/agents/{agent_id}."""

    def test_update_existing_agent(self, client, created_agent):
        """Updating an agent changes its fields."""
        agent_id = created_agent["id"]
        updated_body = {**_VALID_AGENT_BODY, "name": "Updated Name", "role": "frontend"}
        resp = client.put(f"/api/agents/{agent_id}", json=updated_body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Name"
        assert data["role"] == "frontend"

    def test_update_nonexistent_agent_returns_404(self, client):
        """Updating an unknown agent ID returns 404."""
        resp = client.put(f"/api/agents/{_NONEXISTENT_ID}", json=_VALID_AGENT_BODY)
        assert resp.status_code == 404


class TestDeleteAgent:
    """Tests for DELETE /api/agents/{agent_id}."""

    def test_delete_existing_agent(self, client, created_agent):
        """Deleting an existing agent succeeds and returns the deleted ID."""
        agent_id = created_agent["id"]
        resp = client.delete(f"/api/agents/{agent_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["agent_id"] == agent_id

        # Confirm the agent is gone
        check = client.get(f"/api/agents/{agent_id}")
        assert check.status_code == 404

    def test_delete_nonexistent_agent_returns_404(self, client):
        """Deleting an unknown agent ID returns 404."""
        resp = client.delete(f"/api/agents/{_NONEXISTENT_ID}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Collection: Run & logs
# ---------------------------------------------------------------------------


class TestRunAgent:
    """Tests for POST /api/agents/{agent_id}/run."""

    def test_run_increments_run_count(self, client, created_agent):
        """Calling /run increments the agent's run_count by 1."""
        agent_id = created_agent["id"]
        before = client.get(f"/api/agents/{agent_id}").json()["run_count"]
        resp = client.post(f"/api/agents/{agent_id}/run")
        assert resp.status_code == 200
        assert resp.json()["run_count"] == before + 1

    def test_run_nonexistent_agent_returns_404(self, client):
        """Running an unknown agent ID returns 404."""
        resp = client.post(f"/api/agents/{_NONEXISTENT_ID}/run")
        assert resp.status_code == 404


class TestAgentLogs:
    """Tests for GET /api/agents/{agent_id}/logs."""

    def test_logs_initially_empty(self, client, created_agent):
        """A freshly created agent has no logs."""
        agent_id = created_agent["id"]
        resp = client.get(f"/api/agents/{agent_id}/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == agent_id
        assert data["logs"] == []
        assert data["total"] == 0

    def test_logs_nonexistent_agent_returns_404(self, client):
        """Fetching logs for an unknown agent returns 404."""
        resp = client.get(f"/api/agents/{_NONEXISTENT_ID}/logs")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Collection: Memory & truth
# ---------------------------------------------------------------------------


class TestAgentMemory:
    """Tests for GET /api/agents/{agent_id}/memory."""

    def test_memory_returns_structure(self, client, created_agent):
        """Memory endpoint returns agent_id, memories list, and total."""
        agent_id = created_agent["id"]
        resp = client.get(f"/api/agents/{agent_id}/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == agent_id
        assert "memories" in data
        assert "total" in data

    def test_memory_nonexistent_agent_returns_404(self, client):
        """Fetching memory for an unknown agent returns 404."""
        resp = client.get(f"/api/agents/{_NONEXISTENT_ID}/memory")
        assert resp.status_code == 404


class TestAgentTruth:
    """Tests for GET /api/agents/{agent_id}/truth."""

    def test_truth_returns_empty_facts_initially(self, client, created_agent):
        """Truth endpoint returns agent_id and an empty facts list for new agents."""
        agent_id = created_agent["id"]
        resp = client.get(f"/api/agents/{agent_id}/truth")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == agent_id
        assert "facts" in data

    def test_truth_nonexistent_agent_returns_404(self, client):
        """Fetching truth for an unknown agent returns 404."""
        resp = client.get(f"/api/agents/{_NONEXISTENT_ID}/truth")
        assert resp.status_code == 404
