"""
Tests for Industry Blueprints API routes (/api/blueprints/*).

Covers all 7 endpoints in api/blueprints_routes.py using a mocked BlueprintsService.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from blueprints.blueprints_service import get_blueprints_service
from api.blueprints_routes import router as _blueprints_router

# Mount blueprints router (not mounted in main.py for production yet)
_BP_PREFIX = "/api/blueprints"
_mounted_bp = False
for _route in app.routes:
    if hasattr(_route, "path") and _route.path.startswith(_BP_PREFIX):
        _mounted_bp = True
        break
if not _mounted_bp:
    app.include_router(_blueprints_router, prefix="/api", tags=["Blueprints-Test"])


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_blueprint():
    bp = MagicMock()
    bp.id = "bp_healthcare_1"
    bp.name = "Healthcare Clinic Starter"
    bp.industry = "healthcare"
    bp.icon = "🏥"
    bp.to_summary.return_value = {
        "id": "bp_healthcare_1",
        "name": "Healthcare Clinic Starter",
        "industry": "healthcare",
        "status": "available",
        "description": "Pre-built for healthcare clinics",
    }
    bp.to_dict.return_value = {
        "id": "bp_healthcare_1",
        "name": "Healthcare Clinic Starter",
        "industry": "healthcare",
        "status": "available",
        "description": "Pre-built for healthcare clinics",
        "agents": [],
        "workflows": [],
    }
    return bp


@pytest.fixture
def mock_deployment():
    dep = MagicMock()
    dep.id = "dep_test1"
    dep.blueprint_id = "bp_healthcare_1"
    dep.user_id = "user_123"
    dep.status = "active"
    dep.enabled_agents = []
    dep.enabled_workflows = []
    dep.customizations = {}
    dep.to_dict.return_value = {
        "id": "dep_test1",
        "blueprint_id": "bp_healthcare_1",
        "user_id": "user_123",
        "status": "active",
        "enabled_agents": [],
        "enabled_workflows": [],
        "customizations": {},
    }
    return dep


@pytest.fixture(autouse=True)
def mock_blueprints(mock_blueprint, mock_deployment):
    """Inject a mock BlueprintsService for every test in this module."""
    svc = MagicMock()
    svc.get_blueprints.return_value = [mock_blueprint]
    svc.get_blueprint.return_value = mock_blueprint
    svc.get_industries.return_value = [
        {"industry": "healthcare", "name": "Healthcare", "count": 2},
        {"industry": "retail", "name": "Retail", "count": 1},
    ]
    svc.deploy_blueprint.return_value = mock_deployment
    svc.get_user_deployments.return_value = [mock_deployment]
    svc.get_deployment.return_value = mock_deployment

    app.dependency_overrides[get_blueprints_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_blueprints_service, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


# =============================================================================
# List blueprints
# =============================================================================


def test_list_blueprints(client, mock_blueprints):
    resp = client.get("/api/blueprints/")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "blueprints" in data
    assert data["count"] == 1
    mock_blueprints.get_blueprints.assert_called_once()


def test_list_blueprints_with_industry_filter(client, mock_blueprints):
    resp = client.get("/api/blueprints/?industry=healthcare")
    assert resp.status_code == 200
    # Called with the Industry enum, not raw string
    call_kwargs = mock_blueprints.get_blueprints.call_args.kwargs
    assert call_kwargs["industry"].value == "healthcare"
    assert call_kwargs["status"] is None


@pytest.mark.parametrize("invalid_val", ["INVALID", "xyz123", "123"])
def test_list_blueprints_invalid_industry(invalid_val):
    """Invalid industry values return 400 with a descriptive error."""
    with TestClient(app, base_url="http://localhost") as c:
        resp = c.get(f"/api/blueprints/?industry={invalid_val}")
    assert resp.status_code == 400


# =============================================================================
# Industries
# =============================================================================


def test_list_industries(client, mock_blueprints):
    resp = client.get("/api/blueprints/industries")
    assert resp.status_code == 200
    data = resp.json()
    assert "industries" in data
    assert len(data["industries"]) >= 1
    mock_blueprints.get_industries.assert_called_once()


# =============================================================================
# Get blueprint by ID
# =============================================================================


def test_get_blueprint_found(client, mock_blueprints):
    resp = client.get("/api/blueprints/bp_healthcare_1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "bp_healthcare_1"


def test_get_blueprint_not_found(client, mock_blueprints):
    mock_blueprints.get_blueprint.return_value = None
    resp = client.get("/api/blueprints/nonexistent")
    assert resp.status_code == 404


# =============================================================================
# Deploy blueprint
# =============================================================================


def test_deploy_blueprint_success(client, mock_blueprints):
    resp = client.post(
        "/api/blueprints/deploy",
        json={
            "blueprint_id": "bp_healthcare_1",
            "customizations": {"clinic_name": "City Clinic"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "deployment" in data
    mock_blueprints.deploy_blueprint.assert_called_once()


def test_deploy_blueprint_not_found(client, mock_blueprints):
    mock_blueprints.deploy_blueprint.return_value = None
    resp = client.post(
        "/api/blueprints/deploy",
        json={
            "blueprint_id": "nonexistent_bp",
            "customizations": {},
        },
    )
    assert resp.status_code == 404


# =============================================================================
# List deployments
# =============================================================================


def test_list_deployments(client, mock_blueprints, mock_blueprint):
    resp = client.get("/api/blueprints/deployments")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "deployments" in data
    assert data["count"] == 1
    mock_blueprints.get_user_deployments.assert_called_once()


# =============================================================================
# Get deployment by ID
# =============================================================================


def test_get_deployment_found(client, mock_blueprints):
    resp = client.get("/api/blueprints/deployments/dep_test1")
    assert resp.status_code == 200
    data = resp.json()
    assert "deployment" in data
    assert "blueprint" in data


def test_get_deployment_not_found(client, mock_blueprints):
    mock_blueprints.get_deployment.return_value = None
    resp = client.get("/api/blueprints/deployments/nonexistent")
    assert resp.status_code == 404


# =============================================================================
# Update deployment
# =============================================================================


@pytest.mark.parametrize("status", ["active", "paused", "archived"])
def test_update_deployment_status(client, mock_blueprints, mock_deployment, status):
    resp = client.patch(
        "/api/blueprints/deployments/dep_test1", json={"status": status}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "deployment" in data
    # Status was updated in-place on the mock object
    assert mock_deployment.status == status


def test_update_deployment_not_found(client, mock_blueprints):
    mock_blueprints.get_deployment.return_value = None
    resp = client.patch(
        "/api/blueprints/deployments/nonexistent", json={"status": "paused"}
    )
    assert resp.status_code == 404
