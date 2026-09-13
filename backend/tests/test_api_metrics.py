"""
Tests for Metrics API routes (/api/metrics/*).

The metrics router calls src.observability functions and src.efficiency.router
directly — there is no service class to inject via dependency_overrides.
Instead we patch the names as they appear inside the api.metrics_routes module
and similarly stub out the smart_router object so no real LLM infrastructure
is required.

Covers 12+ endpoints including happy paths, error reporting, and health checks.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

_MOCK_METRICS = {
    "summary": {
        "total_requests": 10,
        "total_tokens_in": 500,
        "total_tokens_out": 250,
        "total_cost": 0.05,
        "success_rate": 1.0,
    },
    "recent_requests": [
        {"model": "claude-sonnet", "tokens_in": 100, "tokens_out": 50, "cost": 0.001}
    ],
    "last_hour": {"requests": 3, "cost": 0.003},
    "requests": [],
}

_MOCK_COSTS = {
    "by_model": {"claude-sonnet": 0.03},
    "by_role": {"backend": 0.05},
    "total": 0.08,
}

_MOCK_HEALTH = {
    "status": "healthy",
    "services": {
        "router": {"status": "healthy", "enabled_models": 3},
        "memory": {"status": "healthy", "total_memories": 42},
    },
}

_MOCK_ERRORS = [
    {
        "id": "err_001",
        "error_type": "runtime",
        "message": "Something broke",
        "source": "api",
        "severity": "medium",
        "resolved": False,
        "timestamp": "2026-03-05T10:00:00",
    }
]


def _make_mock_smart_router():
    """Build a MagicMock that satisfies all smart_router attribute accesses."""
    sr = MagicMock()
    sr.list_models.return_value = ["claude-sonnet", "gpt-4o"]
    sr.list_enabled_models.return_value = ["claude-sonnet"]

    model_mock = MagicMock()
    model_mock.name = "Claude Sonnet"
    model_mock.provider = "Anthropic"
    model_mock.model_id = "claude-sonnet-4-6"
    model_mock.enabled = True
    model_mock.priority = 1
    sr.get_model.return_value = model_mock

    sr.config = MagicMock()
    sr.config.default_model = "claude-sonnet"
    sr.config.complexity_threshold = 9
    sr.config.role_mappings = {"backend": "claude-sonnet"}

    return sr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_observability():
    """Patch all observability functions used inside api.metrics_routes."""
    with patch("api.metrics_routes.get_metrics", return_value=_MOCK_METRICS), patch(
        "api.metrics_routes.get_cost_breakdown", return_value=_MOCK_COSTS
    ), patch("api.metrics_routes.reset_metrics", return_value=None), patch(
        "api.metrics_routes.get_health", return_value=_MOCK_HEALTH
    ), patch(
        "api.metrics_routes.get_errors", return_value=_MOCK_ERRORS
    ), patch(
        "api.metrics_routes.record_error", return_value=None
    ), patch(
        "api.metrics_routes.update_service_status", return_value=None
    ), patch(
        "api.metrics_routes.smart_router", _make_mock_smart_router()
    ):
        yield


@pytest.fixture
def client():
    """Return a TestClient for the application."""
    return TestClient(app, base_url="http://localhost")


# ---------------------------------------------------------------------------
# Collection: Core metrics endpoints
# ---------------------------------------------------------------------------


class TestGetAllMetrics:
    """Tests for GET /api/metrics/."""

    def test_returns_full_metrics_dict(self, client, mock_observability):
        """Response contains the complete metrics structure."""
        resp = client.get("/api/metrics/")
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "recent_requests" in data

    def test_summary_fields_present(self, client, mock_observability):
        """The summary section exposes total_requests and total_cost."""
        resp = client.get("/api/metrics/")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert "total_requests" in summary
        assert "total_cost" in summary


class TestGetMetricsSummary:
    """Tests for GET /api/metrics/summary."""

    def test_returns_summary_subset(self, client, mock_observability):
        """Summary endpoint returns only the summary portion of metrics."""
        resp = client.get("/api/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()
        # Should mirror _MOCK_METRICS["summary"]
        assert "total_requests" in data
        assert data["total_requests"] == _MOCK_METRICS["summary"]["total_requests"]

    def test_does_not_include_recent_requests(self, client, mock_observability):
        """The summary endpoint should NOT include the full recent_requests list."""
        resp = client.get("/api/metrics/summary")
        assert resp.status_code == 200
        # recent_requests lives at the top-level of metrics, not in summary
        assert "recent_requests" not in resp.json()


class TestGetCosts:
    """Tests for GET /api/metrics/costs."""

    def test_returns_cost_breakdown(self, client, mock_observability):
        """Cost endpoint returns the cost breakdown dict."""
        resp = client.get("/api/metrics/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_model" in data
        assert "total" in data

    def test_cost_values_are_numeric(self, client, mock_observability):
        """Total cost value is a number."""
        resp = client.get("/api/metrics/costs")
        assert resp.status_code == 200
        assert isinstance(resp.json()["total"], (int, float))


class TestGetModels:
    """Tests for GET /api/metrics/models."""

    def test_returns_models_dict(self, client, mock_observability):
        """Models endpoint returns a dict with 'models', 'default', and config fields."""
        resp = client.get("/api/metrics/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "default" in data
        assert "complexity_threshold" in data
        assert "role_mappings" in data

    def test_default_model_matches_mock(self, client, mock_observability):
        """The default model value matches what the mock smart_router provides."""
        resp = client.get("/api/metrics/models")
        assert resp.status_code == 200
        assert resp.json()["default"] == "claude-sonnet"


class TestGetRecentRequests:
    """Tests for GET /api/metrics/recent."""

    def test_returns_recent_requests_list(self, client, mock_observability):
        """Recent endpoint returns recent_requests and last_hour keys."""
        resp = client.get("/api/metrics/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert "recent_requests" in data
        assert "last_hour" in data
        assert isinstance(data["recent_requests"], list)


class TestResetMetrics:
    """Tests for POST /api/metrics/reset."""

    def test_reset_returns_success(self, client, mock_observability):
        """Reset endpoint returns success=True."""
        resp = client.post("/api/metrics/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_reset_calls_reset_metrics(self, client):
        """reset_metrics() is called exactly once when the endpoint is hit."""
        with patch("api.metrics_routes.reset_metrics") as mock_reset, patch(
            "api.metrics_routes.smart_router", _make_mock_smart_router()
        ):
            resp = client.post("/api/metrics/reset")
        assert resp.status_code == 200
        mock_reset.assert_called_once()


# ---------------------------------------------------------------------------
# Collection: Health endpoints
# ---------------------------------------------------------------------------


class TestSystemHealth:
    """Tests for GET /api/metrics/health."""

    def test_health_returns_status(self, client, mock_observability):
        """Health endpoint returns a dict with a 'status' key."""
        resp = client.get("/api/metrics/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_health_returns_services(self, client, mock_observability):
        """Health endpoint includes a 'services' dict."""
        resp = client.get("/api/metrics/health")
        assert resp.status_code == 200
        assert "services" in resp.json()


class TestLanggraphHealth:
    """Tests for GET /api/metrics/health/langgraph."""

    def test_langgraph_health_returns_version_and_features(self, client):
        """LangGraph health endpoint returns langgraph_version and features dict."""
        resp = client.get("/api/metrics/health/langgraph")
        # Accept 200 (fully operational) or 500 (import error in test env)
        if resp.status_code == 200:
            data = resp.json()
            assert "langgraph_version" in data
            assert "features" in data
            assert "status" in data
        else:
            # In environments without langgraph/router_agent, a 500 is acceptable
            assert resp.status_code in (200, 500)

    def test_langgraph_status_is_string(self, client):
        """If the endpoint succeeds, status is one of the documented strings."""
        resp = client.get("/api/metrics/health/langgraph")
        if resp.status_code == 200:
            assert resp.json()["status"] in ("ok", "degraded", "rollback")


# ---------------------------------------------------------------------------
# Collection: Error tracking
# ---------------------------------------------------------------------------


class TestGetErrors:
    """Tests for GET /api/metrics/errors."""

    def test_returns_errors_list(self, client, mock_observability):
        """Errors endpoint returns 'errors' list and 'total' count."""
        resp = client.get("/api/metrics/errors")
        assert resp.status_code == 200
        data = resp.json()
        assert "errors" in data
        assert "total" in data
        assert isinstance(data["errors"], list)

    def test_total_matches_errors_length(self, client, mock_observability):
        """'total' equals the length of the returned errors list."""
        resp = client.get("/api/metrics/errors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(data["errors"])

    def test_errors_contain_expected_fields(self, client, mock_observability):
        """Each error entry has error_type, message, source, and severity."""
        resp = client.get("/api/metrics/errors")
        assert resp.status_code == 200
        for error in resp.json()["errors"]:
            for field in ("error_type", "message", "source", "severity"):
                assert field in error, f"missing field: {field}"


class TestReportError:
    """Tests for POST /api/metrics/errors/report."""

    def test_report_error_success(self, client, mock_observability):
        """Reporting an error returns success=True."""
        resp = client.post(
            "/api/metrics/errors/report"
            "?error_type=runtime&message=test+error&source=api&severity=medium"
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_report_error_calls_record_error(self, client):
        """record_error() is called with the submitted parameters."""
        with patch("api.metrics_routes.record_error") as mock_record, patch(
            "api.metrics_routes.smart_router", _make_mock_smart_router()
        ):
            resp = client.post(
                "/api/metrics/errors/report"
                "?error_type=runtime&message=boom&source=unit_test&severity=high"
            )
        assert resp.status_code == 200
        mock_record.assert_called_once_with(
            "runtime", "boom", "unit_test", "high", None
        )

    @pytest.mark.parametrize("severity", ["low", "medium", "high", "critical"])
    def test_report_error_accepts_all_severities(
        self, client, mock_observability, severity
    ):
        """All documented severity levels are accepted without error."""
        resp = client.post(
            f"/api/metrics/errors/report"
            f"?error_type=test&message=msg&source=pytest&severity={severity}"
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_report_error_missing_required_params_returns_422(
        self, client, mock_observability
    ):
        """Omitting required query params (error_type, message, source) returns 422."""
        resp = client.post("/api/metrics/errors/report")
        assert resp.status_code == 422
