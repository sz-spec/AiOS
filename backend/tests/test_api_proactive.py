"""
Tests for proactive_routes — Proactive AI API (/api/proactive/*).

The router uses ProactiveAIService injected via Depends(get_proactive_service).
All service methods are mocked via app.dependency_overrides.

Route-level details from the actual implementation:
- POST /analyze                → service.run_analysis() returns a dict
                                 {total, counts, insights: [obj_with_to_dict]}
- POST /analyze/{type}         → service.analyze_*() returns list of objects
                                 with .to_dict()
- GET  /insights               → service.get_insights() returns list of objects
- GET  /insights/{id}          → service.get_insight() returns obj or None
- POST /insights/{id}/view     → service.mark_viewed() returns obj; uses
                                 obj.status.value in response
- POST /insights/{id}/dismiss  → service.dismiss_insight() returns obj; uses
                                 obj.status.value in response
- POST /insights/{id}/action   → service.execute_action() returns dict with
                                 "success" key; await call
- POST /reports/generate       → service.generate_weekly_report() returns obj
                                 with .to_dict(); await call
- GET  /reports                → service.get_reports() returns list of objects
- GET  /reports/{id}           → service.get_report() returns obj or None
- GET  /settings               → service.get_settings() returns obj with .to_dict()
- PATCH /settings              → service.update_settings() returns obj with .to_dict()
- GET  /stats                  → service.get_stats() returns plain dict
- GET  /types                  → static response (no service call)

Covers 16+ endpoints.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from api.proactive_routes import router as _proactive_router

from proactive.proactive_service import get_proactive_service

# Mount the proactive router (not mounted in main.py for production yet)
_PROACTIVE_PREFIX = "/api/proactive"
_mounted_proactive = False
for route in app.routes:
    if hasattr(route, "path") and route.path.startswith(_PROACTIVE_PREFIX):
        _mounted_proactive = True
        break
if not _mounted_proactive:
    app.include_router(_proactive_router, prefix="/api", tags=["Proactive-Test"])


# ---------------------------------------------------------------------------
# Fixtures — auth override
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_user_admin():
    """
    Override get_current_user so user.id == 'admin_user' with admin:full
    permissions. The April 2026 security fix gates PATCH /proactive/settings
    on admin:full because settings are tenant-global; dev-mode user only
    has ['read'] permission and would 403.
    """
    from middleware.auth import get_current_user, AuthenticatedUser

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="admin_user", email="admin@test.com", permissions=["admin:full"]
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

MOCK_INSIGHT = {
    "id": "ins1",
    "insight_type": "opportunity",
    "title": "Upsell opportunity",
    "description": "User ready for upgrade",
    "priority": "high",
    "status": "pending",
    "confidence": 0.85,
    "impact_score": 0.9,
    "suggested_actions": [
        {"id": "act1", "label": "Send upgrade email", "action_type": "email"}
    ],
    "data": {},
    "created_at": "2024-01-01T00:00:00",
    "viewed_at": None,
    "dismissed_at": None,
    "user_id": "admin_user",
}
MOCK_REPORT = {
    "id": "report1",
    "period_start": "2024-01-01",
    "period_end": "2024-01-07",
    "summary": "Strong week",
    "insights": [],
    "metrics": {},
    "created_at": "2024-01-01T00:00:00",
    "user_id": "admin_user",
}
MOCK_SETTINGS = {
    "revenue_protection": True,
    "churn_prevention": True,
    "opportunity_detection": True,
    "schedule_optimization": False,
    "risk_alerts": True,
    "weekly_insights": True,
    "notify_email": True,
    "notify_push": False,
    "notify_sms": False,
    "min_confidence": 0.7,
    "min_impact_score": 0.5,
    "quiet_hours_start": None,
    "quiet_hours_end": None,
}


def _make_insight_obj(data: dict = None) -> MagicMock:
    """Build an insight MagicMock matching the attribute access pattern in routes."""
    d = data or MOCK_INSIGHT
    obj = MagicMock()
    obj.to_dict.return_value = d
    obj.status = MagicMock(value=d["status"])
    obj.user_id = d.get("user_id", "admin_user")
    return obj


def _make_report_obj(data: dict = None) -> MagicMock:
    d = data or MOCK_REPORT
    obj = MagicMock()
    obj.to_dict.return_value = d
    obj.user_id = d.get("user_id", "admin_user")
    return obj


def _make_settings_obj(data: dict = None) -> MagicMock:
    d = data or MOCK_SETTINGS
    obj = MagicMock()
    obj.to_dict.return_value = d
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_proactive():
    svc = MagicMock()
    insight = _make_insight_obj()
    viewed_insight = _make_insight_obj(
        {**MOCK_INSIGHT, "viewed_at": "2024-01-01T12:00:00", "status": "viewed"}
    )
    dismissed_insight = _make_insight_obj({**MOCK_INSIGHT, "status": "dismissed"})
    report = _make_report_obj()
    settings = _make_settings_obj()

    # Analysis endpoints — all async in the route
    svc.run_analysis = AsyncMock(
        return_value={
            "total": 1,
            "counts": {"opportunity": 1},
            "insights": [insight],
        }
    )
    svc.analyze_revenue_protection = AsyncMock(return_value=[insight])
    svc.analyze_churn_risk = AsyncMock(return_value=[])
    svc.detect_opportunities = AsyncMock(return_value=[insight])
    svc.optimize_schedule = AsyncMock(return_value=[])
    svc.detect_risks = AsyncMock(return_value=[])

    # Insight CRUD — synchronous
    svc.get_insights.return_value = [insight]
    svc.get_insight.return_value = insight
    svc.mark_viewed.return_value = viewed_insight
    svc.dismiss_insight.return_value = dismissed_insight

    # Action execution — async
    svc.execute_action = AsyncMock(
        return_value={"success": True, "action_id": "act1", "result": {}}
    )

    # Reports — generate is async, others are sync
    svc.generate_weekly_report = AsyncMock(return_value=report)
    svc.get_reports.return_value = [report]
    svc.get_report.return_value = report

    # Settings
    svc.get_settings.return_value = settings
    svc.update_settings.return_value = settings

    # Stats
    svc.get_stats.return_value = {"total_insights": 10, "by_type": {"opportunity": 5}}

    app.dependency_overrides[get_proactive_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_proactive_service, None)


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------


class TestRunAllAnalysis:
    def test_returns_200_with_total_and_insights(self, client, mock_proactive):
        resp = client.post("/api/proactive/analyze")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "insights" in data
        assert data["total"] == 1

    def test_insights_list_has_expected_length(self, client, mock_proactive):
        resp = client.post("/api/proactive/analyze")
        assert len(resp.json()["insights"]) == 1

    def test_calls_service_run_analysis(self, client, mock_proactive):
        client.post("/api/proactive/analyze")
        mock_proactive.run_analysis.assert_called_once()


class TestAnalyzeRevenue:
    def test_returns_200_with_insights(self, client, mock_proactive):
        resp = client.post("/api/proactive/analyze/revenue")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "insights" in data
        assert data["count"] == 1

    def test_calls_analyze_revenue_protection(self, client, mock_proactive):
        client.post("/api/proactive/analyze/revenue")
        mock_proactive.analyze_revenue_protection.assert_called_once()


class TestAnalyzeChurn:
    def test_returns_200_with_empty_insights_when_no_risk(self, client, mock_proactive):
        resp = client.post("/api/proactive/analyze/churn")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["insights"] == []

    def test_calls_analyze_churn_risk(self, client, mock_proactive):
        client.post("/api/proactive/analyze/churn")
        mock_proactive.analyze_churn_risk.assert_called_once()


class TestAnalyzeOpportunities:
    def test_returns_200_with_opportunities(self, client, mock_proactive):
        resp = client.post("/api/proactive/analyze/opportunities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert len(data["insights"]) == 1

    def test_calls_detect_opportunities(self, client, mock_proactive):
        client.post("/api/proactive/analyze/opportunities")
        mock_proactive.detect_opportunities.assert_called_once()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


class TestGetInsights:
    def test_returns_200_with_insights_list(self, client, mock_proactive):
        resp = client.get("/api/proactive/insights")
        assert resp.status_code == 200
        data = resp.json()
        assert "insights" in data
        assert "count" in data
        assert data["count"] == 1

    def test_insight_contains_expected_fields(self, client, mock_proactive):
        resp = client.get("/api/proactive/insights")
        insight = resp.json()["insights"][0]
        assert insight["id"] == "ins1"
        assert insight["insight_type"] == "opportunity"
        assert insight["priority"] == "high"

    def test_calls_service_get_insights(self, client, mock_proactive):
        client.get("/api/proactive/insights")
        mock_proactive.get_insights.assert_called_once()


class TestGetInsight:
    def test_returns_200_for_existing_insight(self, client, mock_proactive):
        resp = client.get("/api/proactive/insights/ins1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "ins1"
        assert data["title"] == "Upsell opportunity"

    def test_returns_404_when_insight_not_found(self, client, mock_proactive):
        mock_proactive.get_insight.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/proactive/insights/missing")
        assert resp.status_code == 404


class TestMarkViewed:
    def test_returns_200_with_viewed_status(self, client, mock_proactive):
        resp = client.post("/api/proactive/insights/ins1/view")
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "status" in data
        assert data["status"] == "viewed"

    def test_returns_404_when_insight_not_found(self, client, mock_proactive):
        mock_proactive.mark_viewed.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/proactive/insights/missing/view")
        assert resp.status_code == 404

    def test_calls_service_mark_viewed(self, client, mock_proactive):
        client.post("/api/proactive/insights/ins1/view")
        mock_proactive.mark_viewed.assert_called_once_with("ins1")


class TestDismissInsight:
    def test_returns_200_with_dismissed_status(self, client, mock_proactive):
        resp = client.post("/api/proactive/insights/ins1/dismiss")
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert data["status"] == "dismissed"

    def test_returns_404_when_insight_not_found(self, client, mock_proactive):
        mock_proactive.dismiss_insight.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/proactive/insights/missing/dismiss")
        assert resp.status_code == 404


class TestExecuteAction:
    def test_returns_200_with_success_result(self, client, mock_proactive):
        resp = client.post(
            "/api/proactive/insights/ins1/action", json={"action_id": "act1"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["action_id"] == "act1"

    def test_returns_400_when_action_fails(self, client, mock_proactive):
        mock_proactive.execute_action = AsyncMock(
            return_value={"success": False, "error": "Action unavailable"}
        )
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/proactive/insights/ins1/action", json={"action_id": "bad_action"})
        assert resp.status_code == 400

    def test_calls_service_execute_action(self, client, mock_proactive):
        client.post("/api/proactive/insights/ins1/action", json={"action_id": "act1"})
        mock_proactive.execute_action.assert_called_once_with("ins1", "act1")

    def test_missing_action_id_returns_422(self, client, mock_proactive):
        resp = client.post("/api/proactive/insights/ins1/action", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class TestGenerateWeeklyReport:
    def test_returns_200_with_report(self, client, mock_proactive):
        resp = client.post("/api/proactive/reports/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "report1"
        assert data["summary"] == "Strong week"

    def test_calls_generate_weekly_report(self, client, mock_proactive):
        client.post("/api/proactive/reports/generate")
        mock_proactive.generate_weekly_report.assert_called_once()


class TestGetReports:
    def test_returns_200_with_reports_list(self, client, mock_proactive):
        resp = client.get("/api/proactive/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert "reports" in data
        assert "count" in data
        assert data["count"] == 1

    def test_report_contains_expected_fields(self, client, mock_proactive):
        resp = client.get("/api/proactive/reports")
        report = resp.json()["reports"][0]
        assert report["id"] == "report1"
        assert report["period_start"] == "2024-01-01"
        assert report["period_end"] == "2024-01-07"

    def test_calls_service_get_reports(self, client, mock_proactive):
        client.get("/api/proactive/reports")
        mock_proactive.get_reports.assert_called_once()


class TestGetReport:
    def test_returns_200_for_existing_report(self, client, mock_proactive):
        resp = client.get("/api/proactive/reports/report1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "report1"

    def test_returns_404_when_report_not_found(self, client, mock_proactive):
        mock_proactive.get_report.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/proactive/reports/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_returns_200_with_settings(self, client, mock_proactive):
        resp = client.get("/api/proactive/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["revenue_protection"] is True
        assert data["min_confidence"] == 0.7

    def test_settings_contain_all_keys(self, client, mock_proactive):
        resp = client.get("/api/proactive/settings")
        data = resp.json()
        for key in (
            "revenue_protection",
            "churn_prevention",
            "opportunity_detection",
            "notify_email",
            "notify_push",
            "min_confidence",
            "min_impact_score",
        ):
            assert key in data, f"Missing settings key: {key}"

    def test_calls_service_get_settings(self, client, mock_proactive):
        client.get("/api/proactive/settings")
        mock_proactive.get_settings.assert_called_once()


class TestUpdateSettings:
    def test_returns_200_with_updated_settings(self, client, mock_proactive):
        resp = client.patch("/api/proactive/settings", json={"min_confidence": 0.8})
        assert resp.status_code == 200
        data = resp.json()
        assert "min_confidence" in data

    def test_calls_service_update_settings(self, client, mock_proactive):
        client.patch("/api/proactive/settings", json={"min_confidence": 0.8})
        mock_proactive.update_settings.assert_called_once()

    def test_empty_patch_still_returns_200(self, client, mock_proactive):
        resp = client.patch("/api/proactive/settings", json={})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_returns_200_with_stats(self, client, mock_proactive):
        resp = client.get("/api/proactive/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_insights" in data
        assert data["total_insights"] == 10

    def test_stats_have_by_type_breakdown(self, client, mock_proactive):
        resp = client.get("/api/proactive/stats")
        data = resp.json()
        assert "by_type" in data
        assert isinstance(data["by_type"], dict)

    def test_calls_service_get_stats(self, client, mock_proactive):
        client.get("/api/proactive/stats")
        mock_proactive.get_stats.assert_called_once()


# ---------------------------------------------------------------------------
# Types (static endpoint)
# ---------------------------------------------------------------------------


class TestGetInsightTypes:
    def test_returns_200_with_types_priorities_statuses(self, client, mock_proactive):
        resp = client.get("/api/proactive/types")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        assert "priorities" in data
        assert "statuses" in data

    def test_types_is_list_with_id_and_name(self, client, mock_proactive):
        resp = client.get("/api/proactive/types")
        types = resp.json()["types"]
        assert isinstance(types, list)
        assert len(types) > 0
        for t in types:
            assert "id" in t
            assert "name" in t

    def test_priorities_contain_high(self, client, mock_proactive):
        resp = client.get("/api/proactive/types")
        priority_ids = [p["id"] for p in resp.json()["priorities"]]
        assert "high" in priority_ids

    def test_statuses_contain_new(self, client, mock_proactive):
        resp = client.get("/api/proactive/types")
        status_ids = [s["id"] for s in resp.json()["statuses"]]
        assert "new" in status_ids
