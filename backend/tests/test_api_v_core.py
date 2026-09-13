"""
API tests for v_core_routes — Control Plane, Business Core, Workflow Engine, Mission Control.

Services are called directly inside handlers (not via Depends), so we patch them
with monkeypatch on the module-level factory functions.

All service return values that have .to_dict() called on them are MagicMock objects
— MagicMock auto-generates attribute access, so .to_dict() returns another MagicMock
which FastAPI will serialise as a dict.  Where the route returns the raw value or
iterates over it we set the return_value to an empty list [] or a plain dict {}.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app


@pytest.fixture(autouse=True)
def _override_user():
    """Override dev user so org membership checks pass (org_id=None skips check)."""
    from middleware.auth import get_current_user, AuthenticatedUser

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user_123", email="test@test.com", org_id=None
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def mock_services(monkeypatch):
    cp = MagicMock()
    bc = MagicMock()
    we = MagicMock()
    mc = MagicMock()

    # ---- Control Plane ----
    cp.list_user_organizations.return_value = []
    cp.create_organization.return_value = MagicMock()
    cp.create_organization.return_value.to_dict.return_value = {
        "id": "org1",
        "name": "Test Org",
        "plan": "free",
    }
    cp.get_organization.return_value = MagicMock()
    cp.get_organization.return_value.to_dict.return_value = {
        "id": "org1",
        "name": "Test Org",
    }
    cp.list_members.return_value = []
    cp.create_invitation.return_value = MagicMock()
    cp.create_invitation.return_value.to_dict.return_value = {
        "id": "inv1",
        "email": "test@test.com",
    }
    cp.list_roles.return_value = []
    cp.get_audit_logs.return_value = []

    # ---- Business Core ----
    bc.list_entities.return_value = []
    bc.create_entity.return_value = MagicMock()
    bc.create_entity.return_value.to_dict.return_value = {
        "id": "ent1",
        "name": "TestEntity",
        "label": "Test",
    }
    bc.get_entity.return_value = MagicMock()
    bc.get_entity.return_value.to_dict.return_value = {
        "id": "ent1",
        "name": "TestEntity",
    }
    bc.add_field.return_value = MagicMock()
    bc.add_field.return_value.to_dict.return_value = {
        "id": "fld1",
        "name": "test_field",
    }
    bc.list_records.return_value = []
    bc.count_records.return_value = 0
    bc.create_record.return_value = MagicMock()
    bc.create_record.return_value.to_dict.return_value = {
        "id": "rec1",
        "data": {"name": "test"},
    }
    bc.get_record.return_value = MagicMock()
    bc.get_record.return_value.to_dict.return_value = {"id": "rec1", "data": {}}
    bc.update_record.return_value = MagicMock()
    bc.update_record.return_value.to_dict.return_value = {
        "id": "rec1",
        "data": {"updated": True},
    }
    bc.delete_record.return_value = True
    bc.get_record_history.return_value = []

    # ---- Workflow Engine ----
    we.list_workflows.return_value = []
    we.create_workflow.return_value = MagicMock()
    we.create_workflow.return_value.to_dict.return_value = {
        "id": "wf1",
        "name": "Test Workflow",
    }
    we.get_workflow.return_value = MagicMock()
    we.get_workflow.return_value.to_dict.return_value = {
        "id": "wf1",
        "name": "Test Workflow",
        "status": "draft",
    }
    we.add_node.return_value = MagicMock()
    we.add_node.return_value.to_dict.return_value = {"id": "node1"}
    we.activate_workflow.return_value = MagicMock()
    we.activate_workflow.return_value.to_dict.return_value = {
        "id": "wf1",
        "status": "active",
    }
    we.pause_workflow.return_value = MagicMock()
    we.pause_workflow.return_value.to_dict.return_value = {
        "id": "wf1",
        "status": "paused",
    }
    # execute_workflow is awaited in the handler
    exec_mock = MagicMock()
    exec_mock.to_dict.return_value = {"execution_id": "exec1"}
    we.execute_workflow = AsyncMock(return_value=exec_mock)
    we.list_executions.return_value = []
    we.get_workflow_stats.return_value = {}
    we.get_metric_definitions = MagicMock(return_value=[])

    # ---- Mission Control ----
    mc.get_dashboard_data.return_value = {"metrics": {}}
    mc.get_activities.return_value = []
    mc.get_activity_summary.return_value = {}
    mc.list_approvals.return_value = []
    mc.create_approval.return_value = MagicMock()
    mc.create_approval.return_value.to_dict.return_value = {"id": "appr1"}
    mc.approve.return_value = MagicMock()
    mc.approve.return_value.to_dict.return_value = {"id": "appr1", "status": "approved"}
    mc.reject.return_value = MagicMock()
    mc.reject.return_value.to_dict.return_value = {"id": "appr1", "status": "rejected"}
    mc.get_alerts.return_value = []
    mc.get_alert_counts.return_value = {}
    mc.mark_alert_read.return_value = True
    mc.resolve_alert.return_value = True
    mc.get_metric_definitions.return_value = []
    mc.get_metrics.return_value = {}
    mc.get_metric_history.return_value = []
    mc.get_agent_performance.return_value = {}

    monkeypatch.setattr("api.v_core_routes.get_control_plane_service", lambda: cp)
    monkeypatch.setattr("api.v_core_routes.get_business_core_service", lambda: bc)
    monkeypatch.setattr("api.v_core_routes.get_workflow_engine_service", lambda: we)
    monkeypatch.setattr("api.v_core_routes.get_mission_control_service", lambda: mc)
    return cp, bc, we, mc


# ============================================================================
# Control Plane
# ============================================================================


class TestOrganizations:
    def test_list_organizations_returns_200(self, client):
        resp = client.get("/api/v-core/organizations")
        assert resp.status_code == 200

    def test_list_organizations_returns_list(self, client):
        resp = client.get("/api/v-core/organizations")
        body = resp.json()
        assert "organizations" in body
        assert isinstance(body["organizations"], list)

    def test_create_organization_returns_201_or_200(self, client):
        resp = client.post("/api/v-core/organizations", json={"name": "Test Org"})
        assert resp.status_code in (200, 201)

    def test_create_organization_returns_org_data(self, client):
        resp = client.post("/api/v-core/organizations", json={"name": "Test Org"})
        body = resp.json()
        assert "organization" in body
        assert body["organization"]["id"] == "org1"
        assert body["organization"]["name"] == "Test Org"

    def test_get_organization_returns_200(self, client):
        resp = client.get("/api/v-core/organizations/org1")
        assert resp.status_code == 200

    def test_get_organization_returns_org_fields(self, client):
        resp = client.get("/api/v-core/organizations/org1")
        body = resp.json()
        assert body["id"] == "org1"
        assert body["name"] == "Test Org"

    def test_get_organization_not_found(self, client, mock_services):
        cp = mock_services[0]
        cp.get_organization.return_value = None
        resp = client.get("/api/v-core/organizations/nonexistent")
        assert resp.status_code == 404

    def test_list_members_returns_200(self, client):
        resp = client.get("/api/v-core/organizations/org1/members")
        assert resp.status_code == 200
        assert "members" in resp.json()

    def test_invite_member_returns_invitation(self, client):
        resp = client.post(
            "/api/v-core/organizations/org1/invitations",
            json={"email": "test@test.com", "role_id": "role1"},
        )
        assert resp.status_code == 200
        assert resp.json()["invitation"]["id"] == "inv1"


class TestRolesAndPermissions:
    def test_list_roles_returns_200(self, client):
        resp = client.get("/api/v-core/roles")
        assert resp.status_code == 200
        assert "roles" in resp.json()

    def test_list_permissions_returns_200(self, client):
        resp = client.get("/api/v-core/permissions")
        assert resp.status_code == 200

    def test_list_permissions_returns_list(self, client):
        resp = client.get("/api/v-core/permissions")
        body = resp.json()
        assert "permissions" in body
        assert isinstance(body["permissions"], list)
        # At least one permission should be present from the Permission enum
        assert len(body["permissions"]) > 0


# ============================================================================
# Business Core
# ============================================================================


class TestEntities:
    def test_list_entities_returns_200(self, client):
        resp = client.get("/api/v-core/entities")
        assert resp.status_code == 200
        assert "entities" in resp.json()

    def test_create_entity_returns_200(self, client):
        resp = client.post(
            "/api/v-core/entities",
            json={"name": "Contact", "label": "Contact"},
        )
        assert resp.status_code == 200

    def test_create_entity_returns_entity_data(self, client):
        resp = client.post(
            "/api/v-core/entities",
            json={"name": "Contact", "label": "Contact"},
        )
        body = resp.json()
        assert "entity" in body
        assert body["entity"]["id"] == "ent1"

    def test_get_entity_not_found(self, client, mock_services):
        bc = mock_services[1]
        bc.get_entity.return_value = None
        resp = client.get("/api/v-core/entities/nonexistent")
        assert resp.status_code == 404


class TestRecords:
    def test_create_record_returns_200(self, client):
        resp = client.post(
            "/api/v-core/entities/ent1/records",
            json={"data": {"name": "Alice"}},
        )
        assert resp.status_code == 200

    def test_create_record_returns_record_data(self, client):
        resp = client.post(
            "/api/v-core/entities/ent1/records",
            json={"data": {"name": "Alice"}},
        )
        body = resp.json()
        assert "record" in body
        assert body["record"]["id"] == "rec1"

    def test_get_record_returns_200(self, client):
        resp = client.get("/api/v-core/records/rec1")
        assert resp.status_code == 200

    def test_get_record_not_found(self, client, mock_services):
        bc = mock_services[1]
        bc.get_record.return_value = None
        resp = client.get("/api/v-core/records/nonexistent")
        assert resp.status_code == 404

    def test_patch_record_returns_200(self, client):
        resp = client.patch(
            "/api/v-core/records/rec1",
            json={"data": {"name": "Bob"}},
        )
        assert resp.status_code == 200

    def test_patch_record_returns_updated_data(self, client):
        resp = client.patch(
            "/api/v-core/records/rec1",
            json={"data": {"name": "Bob"}},
        )
        body = resp.json()
        assert "record" in body
        assert body["record"]["data"]["updated"] is True

    def test_patch_record_not_found(self, client, mock_services):
        bc = mock_services[1]
        bc.update_record.return_value = None
        resp = client.patch(
            "/api/v-core/records/nonexistent",
            json={"data": {}},
        )
        assert resp.status_code == 404

    def test_delete_record_returns_200(self, client):
        resp = client.delete("/api/v-core/records/rec1")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_record_not_found(self, client, mock_services):
        bc = mock_services[1]
        bc.delete_record.return_value = False
        resp = client.delete("/api/v-core/records/nonexistent")
        assert resp.status_code == 404


# ============================================================================
# Workflow Engine
# ============================================================================


class TestWorkflows:
    def test_list_workflows_returns_200(self, client):
        resp = client.get("/api/v-core/workflows")
        assert resp.status_code == 200
        assert "workflows" in resp.json()

    def test_create_workflow_returns_200(self, client):
        resp = client.post(
            "/api/v-core/workflows",
            json={"name": "My Workflow"},
        )
        assert resp.status_code == 200

    def test_create_workflow_returns_workflow_data(self, client):
        resp = client.post(
            "/api/v-core/workflows",
            json={"name": "My Workflow"},
        )
        body = resp.json()
        assert "workflow" in body
        assert body["workflow"]["id"] == "wf1"

    def test_get_workflow_not_found(self, client, mock_services):
        we = mock_services[2]
        we.get_workflow.return_value = None
        resp = client.get("/api/v-core/workflows/nonexistent")
        assert resp.status_code == 404

    def test_activate_workflow_returns_200(self, client):
        resp = client.post("/api/v-core/workflows/wf1/activate")
        assert resp.status_code == 200

    def test_activate_workflow_status_active(self, client):
        resp = client.post("/api/v-core/workflows/wf1/activate")
        body = resp.json()
        assert body["workflow"]["status"] == "active"

    def test_activate_workflow_not_found(self, client, mock_services):
        we = mock_services[2]
        we.activate_workflow.return_value = None
        resp = client.post("/api/v-core/workflows/nonexistent/activate")
        assert resp.status_code == 404

    def test_execute_workflow_returns_200(self, client):
        resp = client.post(
            "/api/v-core/workflows/wf1/execute",
            json={"trigger_data": {}},
        )
        assert resp.status_code == 200

    def test_execute_workflow_returns_execution_id(self, client):
        resp = client.post(
            "/api/v-core/workflows/wf1/execute",
            json={"trigger_data": {}},
        )
        body = resp.json()
        assert "execution" in body

    def test_list_workflow_actions_returns_200(self, client):
        resp = client.get("/api/v-core/workflow-actions")
        assert resp.status_code == 200
        assert "actions" in resp.json()

    def test_list_workflow_triggers_returns_200(self, client):
        resp = client.get("/api/v-core/workflow-triggers")
        assert resp.status_code == 200
        assert "triggers" in resp.json()

    def test_workflow_actions_have_id_and_name(self, client):
        resp = client.get("/api/v-core/workflow-actions")
        actions = resp.json()["actions"]
        assert len(actions) > 0
        for action in actions:
            assert "id" in action
            assert "name" in action

    def test_workflow_triggers_have_id_and_name(self, client):
        resp = client.get("/api/v-core/workflow-triggers")
        triggers = resp.json()["triggers"]
        assert len(triggers) > 0
        for trigger in triggers:
            assert "id" in trigger
            assert "name" in trigger


# ============================================================================
# Mission Control
# ============================================================================


class TestDashboard:
    def test_get_dashboard_returns_200(self, client):
        resp = client.get("/api/v-core/dashboard")
        assert resp.status_code == 200

    def test_get_dashboard_has_metrics_key(self, client):
        resp = client.get("/api/v-core/dashboard")
        assert "metrics" in resp.json()


class TestApprovals:
    def test_list_approvals_returns_200(self, client):
        resp = client.get("/api/v-core/approvals")
        assert resp.status_code == 200
        assert "approvals" in resp.json()

    def test_create_approval_returns_200(self, client):
        resp = client.post(
            "/api/v-core/approvals",
            json={
                "title": "Test",
                "category": "general",
                "action_type": "deploy",
                "action_data": {},
            },
        )
        assert resp.status_code == 200

    def test_create_approval_returns_approval_data(self, client):
        resp = client.post(
            "/api/v-core/approvals",
            json={
                "title": "Test",
                "category": "general",
                "action_type": "deploy",
                "action_data": {},
            },
        )
        body = resp.json()
        assert "approval" in body
        assert body["approval"]["id"] == "appr1"

    def test_approve_request_returns_200(self, client):
        resp = client.post("/api/v-core/approvals/appr1/approve")
        assert resp.status_code == 200

    def test_approve_not_found(self, client, mock_services):
        mc = mock_services[3]
        mc.approve.return_value = None
        resp = client.post("/api/v-core/approvals/nonexistent/approve")
        assert resp.status_code == 404

    def test_reject_request_returns_200(self, client):
        resp = client.post("/api/v-core/approvals/appr1/reject")
        assert resp.status_code == 200

    def test_reject_not_found(self, client, mock_services):
        mc = mock_services[3]
        mc.reject.return_value = None
        resp = client.post("/api/v-core/approvals/nonexistent/reject")
        assert resp.status_code == 404


class TestAlerts:
    def test_list_alerts_returns_200(self, client):
        resp = client.get("/api/v-core/alerts")
        assert resp.status_code == 200
        assert "alerts" in resp.json()

    def test_list_alerts_empty_by_default(self, client):
        resp = client.get("/api/v-core/alerts")
        assert resp.json()["alerts"] == []

    def test_mark_alert_read_returns_200(self, client):
        resp = client.post("/api/v-core/alerts/alert1/read")
        assert resp.status_code == 200
        assert resp.json()["read"] is True

    def test_mark_alert_read_not_found(self, client, mock_services):
        mc = mock_services[3]
        mc.mark_alert_read.return_value = False
        resp = client.post("/api/v-core/alerts/nonexistent/read")
        assert resp.status_code == 404

    def test_resolve_alert_returns_200(self, client):
        resp = client.post("/api/v-core/alerts/alert1/resolve")
        assert resp.status_code == 200
        assert resp.json()["resolved"] is True

    def test_resolve_alert_not_found(self, client, mock_services):
        mc = mock_services[3]
        mc.resolve_alert.return_value = False
        resp = client.post("/api/v-core/alerts/nonexistent/resolve")
        assert resp.status_code == 404


class TestMetrics:
    def test_get_metrics_returns_200(self, client):
        resp = client.get("/api/v-core/metrics")
        assert resp.status_code == 200

    def test_get_metrics_has_metrics_key(self, client):
        resp = client.get("/api/v-core/metrics")
        assert "metrics" in resp.json()

    def test_get_metrics_list_is_iterable(self, client):
        resp = client.get("/api/v-core/metrics")
        assert isinstance(resp.json()["metrics"], list)
