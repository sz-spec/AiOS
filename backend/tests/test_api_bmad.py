"""
Tests for BMAD API Routes
==========================

Covers: POST/GET/DELETE/PATCH /api/bmad/sessions,
        POST clone, toggle-active,
        GET /api/bmad/agents, GET /api/bmad/agents/{role},
        GET /api/bmad/sessions/{id}/approvals,
        GET /api/bmad/sessions/{id}/artifacts,
        GET /api/bmad/phases
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Guard: skip entire module if bmad dependencies are missing
try:
    from bmad.session import (
        create_session,
        get_session,
        list_sessions,
        delete_session,
        BMADMode,
        _sessions,
    )

    BMAD_AVAILABLE = True
except ImportError:
    BMAD_AVAILABLE = False

pytestmark = pytest.mark.skipif(not BMAD_AVAILABLE, reason="bmad module not available")

from main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient with localhost base URL."""
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def patch_bmad_singletons():
    """Patch module-level singletons to prevent real file/LLM operations."""
    with patch("api.bmad_routes._project_manager", None), patch(
        "api.bmad_routes._llm_bridge", None
    ):
        yield


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear the in-memory session store before and after each test."""
    _sessions.clear()
    yield
    _sessions.clear()


def _create_payload(**overrides):
    """Build a CreateSessionRequest payload with sensible defaults."""
    payload = {
        "project_name": "Test Project",
        "description": "A test project",
        "mode": "guided",
        "auto_commit": True,
        "metadata": {},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# POST /api/bmad/sessions
# ---------------------------------------------------------------------------


class TestSessionCreate:
    def test_create_session_returns_200(self, client):
        response = client.post("/api/bmad/sessions", json=_create_payload())
        assert response.status_code == 200

    def test_create_session_returns_id(self, client):
        data = client.post("/api/bmad/sessions", json=_create_payload()).json()
        assert "id" in data
        assert isinstance(data["id"], str)
        assert len(data["id"]) > 0

    def test_create_session_returns_project_name(self, client):
        data = client.post(
            "/api/bmad/sessions", json=_create_payload(project_name="My BMAD Project")
        ).json()
        assert data["project_name"] == "My BMAD Project"

    def test_create_session_returns_description(self, client):
        data = client.post(
            "/api/bmad/sessions", json=_create_payload(description="A great project")
        ).json()
        assert data["description"] == "A great project"

    def test_create_session_guided_mode(self, client):
        data = client.post(
            "/api/bmad/sessions", json=_create_payload(mode="guided")
        ).json()
        assert data["mode"] == "guided"
        assert data["current_phase"] == "ideation"

    def test_create_session_simple_mode_starts_at_development(self, client):
        data = client.post(
            "/api/bmad/sessions", json=_create_payload(mode="simple")
        ).json()
        assert data["mode"] == "simple"
        assert data["current_phase"] == "development"

    def test_create_session_expert_mode(self, client):
        data = client.post(
            "/api/bmad/sessions", json=_create_payload(mode="expert")
        ).json()
        assert data["mode"] == "expert"

    def test_create_session_party_mode(self, client):
        data = client.post(
            "/api/bmad/sessions", json=_create_payload(mode="party")
        ).json()
        assert data["mode"] == "party"

    def test_create_session_invalid_mode_returns_400(self, client):
        response = client.post(
            "/api/bmad/sessions", json=_create_payload(mode="nonexistent")
        )
        assert response.status_code == 400
        assert "Invalid mode" in response.json()["detail"]

    def test_create_session_is_active_by_default(self, client):
        data = client.post("/api/bmad/sessions", json=_create_payload()).json()
        assert data["is_active"] is True

    def test_create_session_has_phases_dict(self, client):
        data = client.post("/api/bmad/sessions", json=_create_payload()).json()
        assert isinstance(data["phases"], dict)
        assert len(data["phases"]) > 0


# ---------------------------------------------------------------------------
# GET /api/bmad/sessions
# ---------------------------------------------------------------------------


class TestSessionList:
    def test_list_sessions_empty(self, client):
        response = client.get("/api/bmad/sessions")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_sessions_returns_created_sessions(self, client):
        client.post(
            "/api/bmad/sessions", json=_create_payload(project_name="Project A")
        )
        client.post(
            "/api/bmad/sessions", json=_create_payload(project_name="Project B")
        )

        data = client.get("/api/bmad/sessions").json()
        assert len(data) == 2
        names = {s["project_name"] for s in data}
        assert names == {"Project A", "Project B"}

    def test_list_sessions_returns_only_own_sessions(self, client):
        """
        After the April 2026 IDOR fix removed the ?user_id query parameter,
        the endpoint always filters to the authenticated caller's sessions.
        A user_id=1 query param is now ignored (not declared on the route).
        """
        client.post("/api/bmad/sessions", json=_create_payload())
        data = client.get("/api/bmad/sessions").json()
        # The session was created with user.id (from auth) — the list
        # returns sessions owned by the same authenticated user, so exactly 1.
        assert len(data) == 1

    def test_list_sessions_query_user_id_is_ignored(self, client):
        """
        Regression: before the IDOR fix, ?user_id=nonexistent returned an
        empty list because the server blindly filtered by the query param.
        After the fix, the query param is not declared on the endpoint and
        FastAPI ignores it — results are always scoped to the authenticated
        user. This test documents that the previous enumeration vector is
        closed.
        """
        client.post("/api/bmad/sessions", json=_create_payload())
        # Unknown query params are silently ignored by FastAPI; no filter.
        data = client.get(
            "/api/bmad/sessions",
            params={"user_id": "nonexistent"},
        ).json()
        assert len(data) == 1


# ---------------------------------------------------------------------------
# GET /api/bmad/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestSessionGet:
    def test_get_session_returns_200(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.get(f"/api/bmad/sessions/{created['id']}")
        assert response.status_code == 200

    def test_get_session_returns_correct_data(self, client):
        created = client.post(
            "/api/bmad/sessions", json=_create_payload(project_name="Fetched Project")
        ).json()
        data = client.get(f"/api/bmad/sessions/{created['id']}").json()
        assert data["id"] == created["id"]
        assert data["project_name"] == "Fetched Project"

    def test_get_session_not_found_returns_404(self, client):
        response = client.get("/api/bmad/sessions/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# DELETE /api/bmad/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestSessionDelete:
    def test_delete_session_returns_200(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.delete(f"/api/bmad/sessions/{created['id']}")
        assert response.status_code == 200

    def test_delete_session_returns_status(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        data = client.delete(f"/api/bmad/sessions/{created['id']}").json()
        assert data["status"] == "deleted"
        assert data["session_id"] == created["id"]

    def test_delete_session_removes_from_store(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        client.delete(f"/api/bmad/sessions/{created['id']}")
        response = client.get(f"/api/bmad/sessions/{created['id']}")
        assert response.status_code == 404

    def test_delete_session_not_found_returns_404(self, client):
        response = client.delete("/api/bmad/sessions/nonexistent-id")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/bmad/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestSessionUpdate:
    def test_update_project_name(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.patch(
            f"/api/bmad/sessions/{created['id']}",
            json={"project_name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["project_name"] == "Updated Name"

    def test_update_description(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.patch(
            f"/api/bmad/sessions/{created['id']}",
            json={"description": "Updated description"},
        )
        assert response.status_code == 200
        assert response.json()["description"] == "Updated description"

    def test_update_both_fields(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.patch(
            f"/api/bmad/sessions/{created['id']}",
            json={"project_name": "New Name", "description": "New desc"},
        )
        data = response.json()
        assert data["project_name"] == "New Name"
        assert data["description"] == "New desc"

    def test_update_not_found_returns_404(self, client):
        response = client.patch(
            "/api/bmad/sessions/nonexistent-id",
            json={"project_name": "X"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/bmad/sessions/{session_id}/clone
# ---------------------------------------------------------------------------


class TestSessionClone:
    def test_clone_session_returns_200(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.post(
            f"/api/bmad/sessions/{created['id']}/clone",
            json={"project_name": "Cloned Project"},
        )
        assert response.status_code == 200

    def test_clone_session_creates_new_id(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        cloned = client.post(
            f"/api/bmad/sessions/{created['id']}/clone",
            json={"project_name": "Cloned Project"},
        ).json()
        assert cloned["id"] != created["id"]

    def test_clone_session_uses_new_project_name(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        cloned = client.post(
            f"/api/bmad/sessions/{created['id']}/clone",
            json={"project_name": "Cloned Project"},
        ).json()
        assert cloned["project_name"] == "Cloned Project"

    def test_clone_session_inherits_mode(self, client):
        created = client.post(
            "/api/bmad/sessions", json=_create_payload(mode="expert")
        ).json()
        cloned = client.post(
            f"/api/bmad/sessions/{created['id']}/clone",
            json={"project_name": "Cloned Expert"},
        ).json()
        assert cloned["mode"] == "expert"

    def test_clone_session_custom_description(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        cloned = client.post(
            f"/api/bmad/sessions/{created['id']}/clone",
            json={"project_name": "Clone", "description": "Clone desc"},
        ).json()
        assert cloned["description"] == "Clone desc"

    def test_clone_session_not_found_returns_404(self, client):
        response = client.post(
            "/api/bmad/sessions/nonexistent-id/clone",
            json={"project_name": "Clone"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/bmad/sessions/{session_id}/toggle-active
# ---------------------------------------------------------------------------


class TestSessionToggle:
    def test_toggle_active_flips_to_false(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        assert created["is_active"] is True

        toggled = client.post(
            f"/api/bmad/sessions/{created['id']}/toggle-active"
        ).json()
        assert toggled["is_active"] is False

    def test_toggle_active_flips_back_to_true(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()

        # First toggle: True -> False
        client.post(f"/api/bmad/sessions/{created['id']}/toggle-active")
        # Second toggle: False -> True
        toggled = client.post(
            f"/api/bmad/sessions/{created['id']}/toggle-active"
        ).json()
        assert toggled["is_active"] is True

    def test_toggle_not_found_returns_404(self, client):
        response = client.post("/api/bmad/sessions/nonexistent-id/toggle-active")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/bmad/agents
# ---------------------------------------------------------------------------


class TestAgents:
    def test_list_agents_returns_200(self, client):
        response = client.get("/api/bmad/agents")
        assert response.status_code == 200

    def test_list_agents_has_agents_key(self, client):
        data = client.get("/api/bmad/agents").json()
        assert "agents" in data

    def test_list_agents_nonempty(self, client):
        data = client.get("/api/bmad/agents").json()
        assert len(data["agents"]) > 0

    def test_list_agents_has_phases_mapping(self, client):
        data = client.get("/api/bmad/agents").json()
        assert "phases" in data
        assert isinstance(data["phases"], dict)
        assert len(data["phases"]) > 0

    def test_list_agents_each_has_role(self, client):
        data = client.get("/api/bmad/agents").json()
        for agent in data["agents"]:
            assert "role" in agent
            assert "name" in agent

    def test_get_agent_by_role_returns_200(self, client):
        response = client.get("/api/bmad/agents/product_manager")
        assert response.status_code == 200

    def test_get_agent_by_role_returns_correct_data(self, client):
        data = client.get("/api/bmad/agents/product_manager").json()
        assert data["role"] == "product_manager"
        assert data["name"] == "Product Manager"

    def test_get_agent_invalid_role_returns_404(self, client):
        response = client.get("/api/bmad/agents/nonexistent_role")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/bmad/sessions/{session_id}/approvals
# ---------------------------------------------------------------------------


class TestApprovals:
    def test_list_approvals_returns_200(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.get(f"/api/bmad/sessions/{created['id']}/approvals")
        assert response.status_code == 200

    def test_list_approvals_empty_for_new_session(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        data = client.get(f"/api/bmad/sessions/{created['id']}/approvals").json()
        assert data["session_id"] == created["id"]
        assert data["approvals"] == []

    def test_list_approvals_not_found_returns_404(self, client):
        response = client.get("/api/bmad/sessions/nonexistent-id/approvals")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/bmad/sessions/{session_id}/artifacts
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_list_artifacts_returns_200(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        response = client.get(f"/api/bmad/sessions/{created['id']}/artifacts")
        assert response.status_code == 200

    def test_list_artifacts_empty_for_new_session(self, client):
        created = client.post("/api/bmad/sessions", json=_create_payload()).json()
        data = client.get(f"/api/bmad/sessions/{created['id']}/artifacts").json()
        assert data["session_id"] == created["id"]
        assert data["artifacts"] == []

    def test_list_artifacts_not_found_returns_404(self, client):
        response = client.get("/api/bmad/sessions/nonexistent-id/artifacts")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/bmad/phases
# ---------------------------------------------------------------------------


class TestPhases:
    def test_list_phases_returns_200(self, client):
        response = client.get("/api/bmad/phases")
        assert response.status_code == 200

    def test_list_phases_has_phases_key(self, client):
        data = client.get("/api/bmad/phases").json()
        assert "phases" in data

    def test_list_phases_nonempty(self, client):
        data = client.get("/api/bmad/phases").json()
        assert len(data["phases"]) > 0

    def test_list_phases_each_has_required_fields(self, client):
        data = client.get("/api/bmad/phases").json()
        required = {
            "phase",
            "agents",
            "entry_criteria",
            "exit_criteria",
            "commit_message",
        }
        for phase in data["phases"]:
            assert required.issubset(
                phase.keys()
            ), f"Phase {phase.get('phase')} missing fields"

    def test_list_phases_contains_ideation(self, client):
        data = client.get("/api/bmad/phases").json()
        phase_names = [p["phase"] for p in data["phases"]]
        assert "ideation" in phase_names

    def test_list_phases_contains_deployment(self, client):
        data = client.get("/api/bmad/phases").json()
        phase_names = [p["phase"] for p in data["phases"]]
        assert "deployment" in phase_names
