"""
API tests for analytics_routes — event tracking, dashboard metrics, AI usage,
funnel analysis, real-time endpoints, and CSV export.

The AnalyticsService is injected via Depends(get_analytics_service), so we use
app.dependency_overrides to swap it out.

Important method-name notes from analytics_routes.py:
  - dashboard  → service.get_dashboard_metrics()
  - timeseries → service.get_events_over_time()
  - top pages  → service.get_top_pages()
  - AI usage   → service.get_ai_usage_stats()
  - funnel     → service.get_funnel_data()
  - user       → service.get_user_metrics()
  - track event    → service.track_event()      (returns object with .id)
  - track pageview → service.track_page_view()  (returns object with .id)
  - track AI gen   → service.track_ai_generation() (returns object with .id)
  - realtime   → service.get_dashboard_metrics() (reuses dashboard method)
  - export csv → service._events (list, iterated directly)
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from analytics.analytics_service import get_analytics_service


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


def _make_dashboard_metrics():
    """Build a MagicMock that satisfies DashboardResponse field access."""
    m = MagicMock()
    m.total_users = 10
    m.active_users_today = 5
    m.active_users_week = 20
    m.active_users_month = 80
    m.new_users_today = 2
    m.new_users_week = 10
    m.total_projects = 20
    m.projects_created_today = 1
    m.projects_created_week = 5
    m.total_ai_generations = 100
    m.ai_generations_today = 10
    m.total_tokens_used = 50000
    m.tokens_used_today = 1000
    m.total_deployments = 5
    m.deployments_today = 1
    m.deployment_success_rate = 0.95
    m.errors_today = 2
    m.error_rate = 0.01
    m.avg_response_time = 200.0
    m.p99_response_time = 800.0
    return m


def _make_tracked_event(event_id="evt1"):
    """Return a mock event object whose .id is set."""
    ev = MagicMock()
    ev.id = event_id
    return ev


@pytest.fixture
def mock_analytics():
    svc = MagicMock()

    # track_* — return objects with .id
    svc.track_event.return_value = _make_tracked_event("evt1")
    svc.track_page_view.return_value = _make_tracked_event("evt2")
    svc.track_ai_generation.return_value = _make_tracked_event("evt3")

    # dashboard — returns a metrics object with individual attribute access
    svc.get_dashboard_metrics.return_value = _make_dashboard_metrics()

    # events over time
    svc.get_events_over_time.return_value = [{"date": "2024-01-01", "count": 5}]

    # top pages
    svc.get_top_pages.return_value = [{"page": "/dashboard", "views": 100}]

    # AI usage — returns a dict that gets unpacked into AIUsageStats(**data)
    svc.get_ai_usage_stats.return_value = {
        "total_generations": 100,
        "successful": 95,
        "failed": 5,
        "success_rate": 0.95,
        "total_tokens": 50000,
        "avg_tokens_per_generation": 500.0,
        "total_duration_ms": 10000,
        "avg_duration_ms": 100.0,
        "by_model": {},
    }

    # funnel — returns a list of dicts unpacked into FunnelStep(**step)
    svc.get_funnel_data.return_value = [
        {"step": "signup", "step_number": 1, "users": 100, "conversion_rate": 1.0}
    ]

    # user metrics — None triggers 404
    svc.get_user_metrics.return_value = None

    # realtime events list (accessed as service._events)
    svc._events = []

    app.dependency_overrides[get_analytics_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_analytics_service, None)


# ============================================================================
# Event Tracking
# ============================================================================


class TestTrackEvent:
    def test_track_event_returns_200(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={"event_type": "page_view"},
        )
        assert resp.status_code == 200

    def test_track_event_returns_tracked_status(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={"event_type": "page_view"},
        )
        body = resp.json()
        assert body["status"] == "tracked"

    def test_track_event_returns_event_id(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={"event_type": "page_view"},
        )
        body = resp.json()
        assert body["event_id"] == "evt1"

    def test_track_event_with_properties(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={
                "event_type": "button_click",
                "properties": {"button": "signup"},
                "page_url": "/home",
                "session_id": "ses123",
            },
        )
        assert resp.status_code == 200
        mock_analytics.track_event.assert_called_once()

    def test_track_event_calls_service(self, client, mock_analytics):
        client.post(
            "/api/analytics/track/event",
            json={"event_type": "sign_up"},
        )
        assert mock_analytics.track_event.called


class TestTrackPageView:
    def test_track_pageview_returns_200(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/pageview",
            json={"page_url": "/dashboard"},
        )
        assert resp.status_code == 200

    def test_track_pageview_returns_tracked_status(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/pageview",
            json={"page_url": "/dashboard"},
        )
        assert resp.json()["status"] == "tracked"

    def test_track_pageview_returns_event_id(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/pageview",
            json={"page_url": "/dashboard"},
        )
        assert resp.json()["event_id"] == "evt2"

    def test_track_pageview_calls_service(self, client, mock_analytics):
        client.post(
            "/api/analytics/track/pageview",
            json={"page_url": "/landing"},
        )
        assert mock_analytics.track_page_view.called


class TestTrackAIGeneration:
    def test_track_ai_generation_returns_200(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/ai-generation",
            json={
                "model": "gpt-4",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "duration_ms": 1200,
                "success": True,
            },
        )
        assert resp.status_code == 200

    def test_track_ai_generation_returns_tracked(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/ai-generation",
            json={"model": "claude-sonnet"},
        )
        assert resp.json()["status"] == "tracked"

    def test_track_ai_generation_returns_event_id(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/ai-generation",
            json={"model": "claude-sonnet"},
        )
        assert resp.json()["event_id"] == "evt3"


# ============================================================================
# Dashboard
# ============================================================================


class TestDashboard:
    def test_dashboard_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/dashboard")
        assert resp.status_code == 200

    def test_dashboard_has_user_metrics(self, client, mock_analytics):
        resp = client.get("/api/analytics/dashboard")
        body = resp.json()
        assert "total_users" in body
        assert body["total_users"] == 10

    def test_dashboard_has_project_metrics(self, client, mock_analytics):
        resp = client.get("/api/analytics/dashboard")
        body = resp.json()
        assert "total_projects" in body
        assert body["total_projects"] == 20

    def test_dashboard_has_ai_metrics(self, client, mock_analytics):
        resp = client.get("/api/analytics/dashboard")
        body = resp.json()
        assert "total_ai_generations" in body
        assert body["total_ai_generations"] == 100

    def test_dashboard_calls_service(self, client, mock_analytics):
        client.get("/api/analytics/dashboard")
        assert mock_analytics.get_dashboard_metrics.called


# ============================================================================
# Time Series & Pages
# ============================================================================


class TestTimeSeries:
    def test_events_timeseries_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/events/timeseries?event_type=click&days=7")
        assert resp.status_code == 200

    def test_events_timeseries_returns_list(self, client, mock_analytics):
        resp = client.get("/api/analytics/events/timeseries")
        body = resp.json()
        assert isinstance(body, list)

    def test_events_timeseries_list_has_date_count(self, client, mock_analytics):
        resp = client.get("/api/analytics/events/timeseries")
        body = resp.json()
        assert len(body) == 1
        assert body[0]["date"] == "2024-01-01"
        assert body[0]["count"] == 5


class TestTopPages:
    def test_top_pages_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/pages/top?limit=5")
        assert resp.status_code == 200

    def test_top_pages_returns_list(self, client, mock_analytics):
        resp = client.get("/api/analytics/pages/top")
        assert isinstance(resp.json(), list)

    def test_top_pages_has_page_and_views(self, client, mock_analytics):
        resp = client.get("/api/analytics/pages/top")
        first = resp.json()[0]
        assert first["page"] == "/dashboard"
        assert first["views"] == 100


# ============================================================================
# AI Usage
# ============================================================================


class TestAIUsage:
    def test_ai_usage_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/ai/usage")
        assert resp.status_code == 200

    def test_ai_usage_has_expected_fields(self, client, mock_analytics):
        resp = client.get("/api/analytics/ai/usage")
        body = resp.json()
        assert "total_generations" in body
        assert "success_rate" in body
        assert "total_tokens" in body
        assert body["total_generations"] == 100


# ============================================================================
# Funnel
# ============================================================================


class TestFunnel:
    def test_funnel_returns_200(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/funnel",
            json={"steps": ["signup", "onboard", "first_project"]},
        )
        assert resp.status_code == 200

    def test_funnel_returns_steps(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/funnel",
            json={"steps": ["signup", "onboard"]},
        )
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["step"] == "signup"

    def test_funnel_calls_service_with_steps(self, client, mock_analytics):
        client.post(
            "/api/analytics/funnel",
            json={"steps": ["a", "b", "c"]},
        )
        assert mock_analytics.get_funnel_data.called


# ============================================================================
# User Analytics
# ============================================================================


class TestUserAnalytics:
    def test_get_user_analytics_not_found_returns_404(self, client, mock_analytics):
        # mock returns None → 404
        resp = client.get("/api/analytics/users/unknown")
        assert resp.status_code == 404

    def test_get_user_analytics_success(self, client, mock_analytics):
        user_metrics = MagicMock()
        user_metrics.user_id = "user_123"
        user_metrics.total_sessions = 10
        user_metrics.total_page_views = 50
        user_metrics.total_actions = 200
        user_metrics.total_prompts = 30
        user_metrics.total_tokens_used = 15000
        user_metrics.total_projects = 3
        user_metrics.total_deployments = 2
        user_metrics.first_seen = None
        user_metrics.last_seen = None
        mock_analytics.get_user_metrics.return_value = user_metrics

        resp = client.get("/api/analytics/users/user_123")
        assert resp.status_code == 200

    def test_get_user_analytics_returns_user_id(self, client, mock_analytics):
        user_metrics = MagicMock()
        user_metrics.user_id = "user_123"
        user_metrics.total_sessions = 5
        user_metrics.total_page_views = 20
        user_metrics.total_actions = 80
        user_metrics.total_prompts = 10
        user_metrics.total_tokens_used = 5000
        user_metrics.total_projects = 1
        user_metrics.total_deployments = 1
        user_metrics.first_seen = None
        user_metrics.last_seen = None
        mock_analytics.get_user_metrics.return_value = user_metrics

        resp = client.get("/api/analytics/users/user_123")
        assert resp.json()["user_id"] == "user_123"


# ============================================================================
# Real-time
# ============================================================================


class TestRealtime:
    def test_realtime_active_users_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/active-users")
        assert resp.status_code == 200

    def test_realtime_active_users_has_active_now(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/active-users")
        body = resp.json()
        assert "active_now" in body
        assert "timestamp" in body

    def test_realtime_events_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/events")
        assert resp.status_code == 200

    def test_realtime_events_has_events_list(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/events")
        body = resp.json()
        assert "events" in body
        assert isinstance(body["events"], list)

    def test_realtime_events_has_count(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/events")
        assert "count" in resp.json()


# ============================================================================
# Export
# ============================================================================


class TestExportCSV:
    def test_export_csv_returns_200(self, client, mock_analytics):
        resp = client.get("/api/analytics/export/csv")
        assert resp.status_code == 200

    def test_export_csv_has_headers_rows_count(self, client, mock_analytics):
        resp = client.get("/api/analytics/export/csv")
        body = resp.json()
        assert "headers" in body
        assert "rows" in body
        assert "count" in body

    def test_export_csv_correct_headers(self, client, mock_analytics):
        resp = client.get("/api/analytics/export/csv")
        headers = resp.json()["headers"]
        for expected in ["timestamp", "event_type", "user_id"]:
            assert expected in headers

    def test_export_csv_empty_events_gives_zero_count(self, client, mock_analytics):
        # _events is already [] in fixture
        resp = client.get("/api/analytics/export/csv")
        assert resp.json()["count"] == 0
