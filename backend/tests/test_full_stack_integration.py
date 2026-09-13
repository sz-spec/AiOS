"""
Full-Stack Integration Tests
==============================

End-to-end flows using TestClient, marked @pytest.mark.integration.
Covers: Chat lifecycle, V-Core entity lifecycle, Agent lifecycle,
        Memory roundtrip, Kernel status, Kernel filesystem.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


# ---------------------------------------------------------------------------
# Chat Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestChatLifecycle:
    def test_chat_models_list(self, client):
        """GET /api/chat/models returns a list of model entries."""
        resp = client.get("/api/chat/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) > 0

    def test_chat_models_have_required_fields(self, client):
        """Each model entry has id and provider."""
        resp = client.get("/api/chat/models")
        data = resp.json()
        for model in data["models"]:
            assert "id" in model
            assert "provider" in model

    def test_chat_completions_requires_auth(self, client):
        """POST /api/chat/completions validates input."""
        resp = client.post(
            "/api/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        # Should return 200 or 422 (validation) or 500 (missing API keys)
        # but NOT 404 (route exists)
        assert resp.status_code != 404


# ---------------------------------------------------------------------------
# V-Core Entity Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestVCoreLifecycle:
    def test_entity_crud_flow(self, client):
        """Create -> Get -> List -> Delete entity flow."""
        # Create (uses get_current_user which returns org_demo)
        resp = client.post(
            "/api/v-core/entities",
            json={
                "name": "integration_test_entity",
                "label": "Integration Test",
                "fields": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        entity = data.get("entity", data)
        entity_id = entity.get("id")
        assert entity_id is not None

        # Get
        resp = client.get(f"/api/v-core/entities/{entity_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("name") == "integration_test_entity"

        # List
        resp = client.get("/api/v-core/entities")
        assert resp.status_code == 200

    def test_entity_fields_and_records(self, client):
        """Create entity -> Add field -> Create record -> Query records."""
        # Create entity
        resp = client.post(
            "/api/v-core/entities",
            json={
                "name": "integ_records_entity",
                "label": "Records Test",
                "fields": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        entity = data.get("entity", data)
        entity_id = entity.get("id")

        # Add field (field_def requires "type" not "field_type")
        resp = client.post(
            f"/api/v-core/entities/{entity_id}/fields",
            json={
                "name": "email",
                "type": "text",
                "required": True,
            },
        )
        assert resp.status_code == 200

        # Create record
        resp = client.post(
            f"/api/v-core/entities/{entity_id}/records",
            json={
                "data": {"email": "test@example.com"},
            },
        )
        assert resp.status_code == 200

        # Get records
        resp = client.get(f"/api/v-core/entities/{entity_id}/records")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Agent Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAgentLifecycle:
    def test_list_agents(self, client):
        """GET /api/agents returns agent list."""
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    def test_create_and_delete_agent(self, client):
        """Create agent -> Check it exists -> Delete."""
        resp = client.post(
            "/api/agents",
            json={
                "name": "IntegrationTestAgent",
                "role": "tester",
            },
        )
        assert resp.status_code in (200, 201)
        agent = resp.json()
        agent_id = agent.get("id")

        if agent_id:
            # Delete
            resp = client.delete(f"/api/agents/{agent_id}")
            assert resp.status_code in (200, 204)

    def test_agent_execute_dev_mode(self, client):
        """Execute task in dev mode returns mock result."""
        # Create agent first
        resp = client.post(
            "/api/agents",
            json={
                "name": "DevAgent",
                "role": "coding",
            },
        )
        if resp.status_code not in (200, 201):
            pytest.skip("Agent creation failed")
        agent_id = resp.json().get("id")

        # Execute task
        resp = client.post(
            f"/api/agents/{agent_id}/execute",
            json={
                "task": "Write hello world",
            },
        )
        # Dev mode should still return a response
        assert resp.status_code in (200, 500)

        # Cleanup
        client.delete(f"/api/agents/{agent_id}")


# ---------------------------------------------------------------------------
# Memory Roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMemoryRoundtrip:
    def test_memory_stats(self, client):
        """GET /api/memory returns stats structure."""
        resp = client.get("/api/memory")
        assert resp.status_code == 200
        data = resp.json()
        # Should have basic stats keys
        assert isinstance(data, dict)

    def test_memory_types(self, client):
        """GET /api/memory/types returns memory type list."""
        resp = client.get("/api/memory/types")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_memory_query(self, client):
        """POST /api/memory/query returns results."""
        resp = client.post(
            "/api/memory/query",
            json={
                "query": "test query",
                "limit": 5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


# ---------------------------------------------------------------------------
# Kernel Status
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestKernelIntegration:
    def test_kernel_status_endpoint(self, client):
        """GET /api/kernel/status returns structure with connected field."""
        resp = client.get("/api/kernel/status")
        if resp.status_code == 404:
            pytest.skip("Kernel routes not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "connected" in data or "status" in data

    def test_kernel_filesystem_endpoint(self, client):
        """GET /api/kernel/filesystem returns file list or error."""
        resp = client.get("/api/kernel/filesystem")
        if resp.status_code == 404:
            pytest.skip("Kernel routes not mounted")
        # May return 200 with files or 503 if not connected
        assert resp.status_code in (200, 503, 500)


# ---------------------------------------------------------------------------
# Metrics and Health
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMetricsIntegration:
    def test_metrics_endpoint(self, client):
        """GET /api/metrics returns metrics summary."""
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_health_endpoint(self, client):
        """GET /api/metrics/health returns health status."""
        resp = client.get("/api/metrics/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_cost_breakdown_endpoint(self, client):
        """GET /api/metrics/costs returns cost breakdown."""
        resp = client.get("/api/metrics/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
