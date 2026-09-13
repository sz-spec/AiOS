"""
Extended API tests for analytics_routes — edge cases and low-coverage areas.

Covers 10 areas NOT in test_api_analytics.py:
  1. Timeseries filtering (event_type, interval variants, days boundaries)
  2. Top pages boundary limits (limit=1, limit=100, days=1)
  3. AI usage user_id filter
  4. AI usage by_model breakdown
  5. Funnel edge cases (single step, many steps, empty steps)
  6. Realtime events limit boundaries
  7. Realtime active users format (timestamp ISO, active_now int)
  8. CSV export with filters (event_type, days, combined)
  9. Dashboard with organization_id
  10. Track event with deeply nested / complex properties

Uses a locally-created FastAPI app with the analytics router mounted directly,
bypassing the production middleware stack (api_key, auth) that requires
env vars only available in deployment.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.analytics_routes import router as analytics_router
from analytics.analytics_service import get_analytics_service
from api.deps import get_current_user, AuthenticatedUser

# ---------------------------------------------------------------------------
# Build a minimal app with just the analytics router -- no production middleware.
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(analytics_router, prefix="/api")

_DEV_USER = AuthenticatedUser(
    id="test_user",
    email="test@example.com",
    org_id="org_test",
    permissions=["read", "write"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dashboard_metrics(**overrides):
    """Build a MagicMock that satisfies DashboardResponse field access."""
    defaults = dict(
        total_users=10,
        active_users_today=5,
        active_users_week=20,
        active_users_month=80,
        new_users_today=2,
        new_users_week=10,
        total_projects=20,
        projects_created_today=1,
        projects_created_week=5,
        total_ai_generations=100,
        ai_generations_today=10,
        total_tokens_used=50000,
        tokens_used_today=1000,
        total_deployments=5,
        deployments_today=1,
        deployment_success_rate=0.95,
        errors_today=2,
        error_rate=0.01,
        avg_response_time=200.0,
        p99_response_time=800.0,
    )
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _make_tracked_event(event_id="ext_evt1"):
    ev = MagicMock()
    ev.id = event_id
    return ev


def _make_realtime_event(event_type="page_view", ts=None):
    """Build a mock event suitable for the realtime /events endpoint."""
    ev = MagicMock()
    ev.event_type = event_type
    ev.timestamp = ts or datetime.now(timezone.utc)
    ev.to_dict.return_value = {
        "id": "evt_rt",
        "event_type": event_type,
        "timestamp": ev.timestamp.isoformat(),
    }
    return ev


def _make_csv_event(event_type="page_view", page_url="/home", user_id="u1", ts=None):
    """Build a mock event used by the CSV export endpoint."""
    ev = MagicMock()
    ev.event_type = event_type
    ev.timestamp = ts or datetime.now(timezone.utc)
    ev.user_id = user_id
    ev.page_url = page_url
    ev.properties = {"source": "test"}
    return ev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(
        _test_app, base_url="http://localhost", raise_server_exceptions=False
    )


@pytest.fixture
def mock_analytics():
    """Override AnalyticsService with a MagicMock with sensible defaults."""
    svc = MagicMock()

    # track_* defaults
    svc.track_event.return_value = _make_tracked_event("ext_evt1")
    svc.track_page_view.return_value = _make_tracked_event("ext_evt2")
    svc.track_ai_generation.return_value = _make_tracked_event("ext_evt3")

    # dashboard
    svc.get_dashboard_metrics.return_value = _make_dashboard_metrics()

    # timeseries
    svc.get_events_over_time.return_value = [{"date": "2025-01-01", "count": 3}]

    # top pages
    svc.get_top_pages.return_value = [{"page": "/home", "views": 42}]

    # AI usage
    svc.get_ai_usage_stats.return_value = {
        "total_generations": 50,
        "successful": 48,
        "failed": 2,
        "success_rate": 96.0,
        "total_tokens": 25000,
        "avg_tokens_per_generation": 500.0,
        "total_duration_ms": 6000,
        "avg_duration_ms": 120.0,
        "by_model": {},
    }

    # funnel
    svc.get_funnel_data.return_value = [
        {"step": "signup", "step_number": 1, "users": 100, "conversion_rate": 100.0},
    ]

    # user metrics — None triggers 404
    svc.get_user_metrics.return_value = None

    # realtime _events list
    svc._events = []

    _test_app.dependency_overrides[get_analytics_service] = lambda: svc
    _test_app.dependency_overrides[get_current_user] = lambda: _DEV_USER
    yield svc
    _test_app.dependency_overrides.pop(get_analytics_service, None)
    _test_app.dependency_overrides.pop(get_current_user, None)


# ===========================================================================
# 1. Timeseries filtering
# ===========================================================================


class TestTimeseriesFiltering:
    """GET /api/analytics/events/timeseries with various query params."""

    def test_timeseries_filter_by_event_type(self, client, mock_analytics):
        resp = client.get("/api/analytics/events/timeseries?event_type=button_click")
        assert resp.status_code == 200
        call_kwargs = mock_analytics.get_events_over_time.call_args
        assert "button_click" in str(call_kwargs)

    def test_timeseries_interval_hour(self, client, mock_analytics):
        resp = client.get("/api/analytics/events/timeseries?interval=hour")
        assert resp.status_code == 200
        assert "hour" in str(mock_analytics.get_events_over_time.call_args)

    def test_timeseries_interval_week(self, client, mock_analytics):
        resp = client.get("/api/analytics/events/timeseries?interval=week")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_timeseries_days_minimum_one(self, client, mock_analytics):
        """days=1 is the minimum allowed (ge=1)."""
        resp = client.get("/api/analytics/events/timeseries?days=1")
        assert resp.status_code == 200

    def test_timeseries_days_maximum_365(self, client, mock_analytics):
        """days=365 is the maximum allowed (le=365)."""
        resp = client.get("/api/analytics/events/timeseries?days=365")
        assert resp.status_code == 200

    def test_timeseries_days_over_max_rejected(self, client, mock_analytics):
        """days=366 exceeds le=365 — FastAPI validation rejects it."""
        resp = client.get("/api/analytics/events/timeseries?days=366")
        assert resp.status_code == 422

    def test_timeseries_days_zero_rejected(self, client, mock_analytics):
        """days=0 is below ge=1."""
        resp = client.get("/api/analytics/events/timeseries?days=0")
        assert resp.status_code == 422

    def test_timeseries_invalid_interval_rejected(self, client, mock_analytics):
        """interval must match ^(hour|day|week)$."""
        resp = client.get("/api/analytics/events/timeseries?interval=month")
        assert resp.status_code == 422


# ===========================================================================
# 2. Top pages boundary limits
# ===========================================================================


class TestTopPagesBoundaries:
    """GET /api/analytics/pages/top with edge-case limit and days values."""

    def test_top_pages_limit_one(self, client, mock_analytics):
        resp = client.get("/api/analytics/pages/top?limit=1")
        assert resp.status_code == 200
        assert "1" in str(mock_analytics.get_top_pages.call_args)

    def test_top_pages_limit_max_100(self, client, mock_analytics):
        resp = client.get("/api/analytics/pages/top?limit=100")
        assert resp.status_code == 200

    def test_top_pages_limit_over_max_rejected(self, client, mock_analytics):
        """limit=101 exceeds le=100."""
        resp = client.get("/api/analytics/pages/top?limit=101")
        assert resp.status_code == 422

    def test_top_pages_limit_zero_rejected(self, client, mock_analytics):
        """limit=0 is below ge=1."""
        resp = client.get("/api/analytics/pages/top?limit=0")
        assert resp.status_code == 422

    def test_top_pages_days_one(self, client, mock_analytics):
        resp = client.get("/api/analytics/pages/top?days=1")
        assert resp.status_code == 200


# ===========================================================================
# 3. AI usage user filter
# ===========================================================================


class TestAIUsageUserFilter:
    """GET /api/analytics/ai/usage with ?user_id=... filter."""

    def test_ai_usage_with_specific_user_id(self, client, mock_analytics):
        resp = client.get("/api/analytics/ai/usage?user_id=specific_user")
        assert resp.status_code == 200
        call_kwargs = mock_analytics.get_ai_usage_stats.call_args
        assert "specific_user" in str(call_kwargs)

    def test_ai_usage_without_user_id_defaults(self, client, mock_analytics):
        """When no user_id query param, the route still calls the service successfully."""
        resp = client.get("/api/analytics/ai/usage")
        assert resp.status_code == 200
        assert mock_analytics.get_ai_usage_stats.called


# ===========================================================================
# 4. AI usage by_model breakdown
# ===========================================================================


class TestAIUsageByModel:
    """Verify the by_model dict is returned correctly in the response."""

    def test_by_model_empty_dict(self, client, mock_analytics):
        resp = client.get("/api/analytics/ai/usage")
        body = resp.json()
        assert "by_model" in body
        assert body["by_model"] == {}

    def test_by_model_with_multiple_models(self, client, mock_analytics):
        mock_analytics.get_ai_usage_stats.return_value = {
            "total_generations": 30,
            "successful": 28,
            "failed": 2,
            "success_rate": 93.3,
            "total_tokens": 15000,
            "avg_tokens_per_generation": 500.0,
            "total_duration_ms": 4000,
            "avg_duration_ms": 133.3,
            "by_model": {
                "gpt-4": {"count": 20, "tokens": 10000},
                "claude-sonnet": {"count": 10, "tokens": 5000},
            },
        }
        resp = client.get("/api/analytics/ai/usage")
        body = resp.json()
        assert len(body["by_model"]) == 2
        assert body["by_model"]["gpt-4"]["count"] == 20
        assert body["by_model"]["claude-sonnet"]["tokens"] == 5000


# ===========================================================================
# 5. Funnel edge cases
# ===========================================================================


class TestFunnelEdgeCases:
    """POST /api/analytics/funnel with varying step counts."""

    def test_funnel_single_step(self, client, mock_analytics):
        mock_analytics.get_funnel_data.return_value = [
            {"step": "signup", "step_number": 1, "users": 50, "conversion_rate": 100.0},
        ]
        resp = client.post(
            "/api/analytics/funnel",
            json={"steps": ["signup"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["step"] == "signup"
        assert body[0]["conversion_rate"] == 100.0

    def test_funnel_many_steps(self, client, mock_analytics):
        steps = [
            "visit",
            "signup",
            "onboard",
            "first_project",
            "first_deploy",
            "invite_team",
            "upgrade",
        ]
        mock_return = [
            {
                "step": s,
                "step_number": i + 1,
                "users": max(100 - i * 15, 5),
                "conversion_rate": (
                    round(max(100 - i * 15, 5) / max(100 - (i - 1) * 15, 5) * 100, 1)
                    if i > 0
                    else 100.0
                ),
            }
            for i, s in enumerate(steps)
        ]
        mock_analytics.get_funnel_data.return_value = mock_return
        resp = client.post(
            "/api/analytics/funnel",
            json={"steps": steps},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 7
        assert body[0]["step"] == "visit"
        assert body[-1]["step"] == "upgrade"

    def test_funnel_empty_steps_returns_empty(self, client, mock_analytics):
        """An empty steps list is accepted (list[str] allows []) and returns []."""
        mock_analytics.get_funnel_data.return_value = []
        resp = client.post(
            "/api/analytics/funnel",
            json={"steps": []},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_funnel_custom_days_param(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/funnel",
            json={"steps": ["a", "b"], "days": 7},
        )
        assert resp.status_code == 200
        assert mock_analytics.get_funnel_data.called


# ===========================================================================
# 6. Realtime events limit boundaries
# ===========================================================================


class TestRealtimeEventsLimit:
    """GET /api/analytics/realtime/events with ?limit=... boundaries."""

    def test_realtime_events_limit_one(self, client, mock_analytics):
        mock_analytics._events = [_make_realtime_event() for _ in range(5)]
        resp = client.get("/api/analytics/realtime/events?limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] <= 1

    def test_realtime_events_limit_max_100(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/events?limit=100")
        assert resp.status_code == 200

    def test_realtime_events_limit_over_max_rejected(self, client, mock_analytics):
        """limit=101 exceeds le=100."""
        resp = client.get("/api/analytics/realtime/events?limit=101")
        assert resp.status_code == 422

    def test_realtime_events_limit_zero_rejected(self, client, mock_analytics):
        """limit=0 is below ge=1."""
        resp = client.get("/api/analytics/realtime/events?limit=0")
        assert resp.status_code == 422

    def test_realtime_events_count_matches_list_length(self, client, mock_analytics):
        mock_analytics._events = [_make_realtime_event() for _ in range(3)]
        resp = client.get("/api/analytics/realtime/events?limit=20")
        body = resp.json()
        assert body["count"] == len(body["events"])


# ===========================================================================
# 7. Realtime active users format
# ===========================================================================


class TestRealtimeActiveUsersFormat:
    """GET /api/analytics/realtime/active-users — verify response shape."""

    def test_active_now_is_integer(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/active-users")
        body = resp.json()
        assert isinstance(body["active_now"], int)

    def test_timestamp_is_iso_format(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/active-users")
        body = resp.json()
        ts = body["timestamp"]
        # Must parse as ISO 8601 without error
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    def test_active_5min_present_and_is_int(self, client, mock_analytics):
        resp = client.get("/api/analytics/realtime/active-users")
        body = resp.json()
        assert "active_5min" in body
        assert isinstance(body["active_5min"], int)


# ===========================================================================
# 8. CSV export with filters
# ===========================================================================


class TestCSVExportFilters:
    """GET /api/analytics/export/csv with ?event_type and ?days filters."""

    def test_csv_export_filter_by_event_type(self, client, mock_analytics):
        now = datetime.now(timezone.utc)
        mock_analytics._events = [
            _make_csv_event(event_type="page_view", ts=now),
            _make_csv_event(event_type="button_click", ts=now),
            _make_csv_event(event_type="page_view", ts=now),
        ]
        resp = client.get("/api/analytics/export/csv?event_type=page_view")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        for row in body["rows"]:
            assert row["event_type"] == "page_view"

    def test_csv_export_filter_by_days(self, client, mock_analytics):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=60)
        mock_analytics._events = [
            _make_csv_event(event_type="page_view", ts=now),
            _make_csv_event(event_type="page_view", ts=old),
        ]
        resp = client.get("/api/analytics/export/csv?days=7")
        assert resp.status_code == 200
        body = resp.json()
        # Only the recent event should pass the 7-day window
        assert body["count"] == 1

    def test_csv_export_combined_event_type_and_days(self, client, mock_analytics):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=60)
        mock_analytics._events = [
            _make_csv_event(event_type="page_view", ts=now),
            _make_csv_event(event_type="button_click", ts=now),
            _make_csv_event(event_type="page_view", ts=old),
        ]
        resp = client.get("/api/analytics/export/csv?event_type=page_view&days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["rows"][0]["event_type"] == "page_view"

    def test_csv_export_has_properties_column(self, client, mock_analytics):
        now = datetime.now(timezone.utc)
        mock_analytics._events = [_make_csv_event(ts=now)]
        resp = client.get("/api/analytics/export/csv")
        body = resp.json()
        assert "properties" in body["headers"]
        assert len(body["rows"]) == 1
        assert "source" in body["rows"][0]["properties"]


# ===========================================================================
# 9. Dashboard with organization_id
# ===========================================================================


class TestDashboardOrgId:
    """GET /api/analytics/dashboard with ?organization_id query param."""

    def test_dashboard_with_org_id_passes_to_service(self, client, mock_analytics):
        resp = client.get("/api/analytics/dashboard?organization_id=org_abc")
        assert resp.status_code == 200
        call_kwargs = mock_analytics.get_dashboard_metrics.call_args
        assert "org_abc" in str(call_kwargs)

    def test_dashboard_without_org_id(self, client, mock_analytics):
        """Without organization_id, the route falls back to user.org_id."""
        resp = client.get("/api/analytics/dashboard")
        assert resp.status_code == 200
        assert mock_analytics.get_dashboard_metrics.called

    def test_dashboard_returns_all_metric_sections(self, client, mock_analytics):
        resp = client.get("/api/analytics/dashboard")
        body = resp.json()
        expected = [
            "total_users",
            "active_users_today",
            "active_users_week",
            "active_users_month",
            "new_users_today",
            "new_users_week",
            "total_projects",
            "projects_created_today",
            "projects_created_week",
            "total_ai_generations",
            "ai_generations_today",
            "total_tokens_used",
            "tokens_used_today",
            "total_deployments",
            "deployments_today",
            "deployment_success_rate",
            "errors_today",
            "error_rate",
            "avg_response_time",
            "p99_response_time",
        ]
        for field_name in expected:
            assert field_name in body, f"Missing dashboard field: {field_name}"


# ===========================================================================
# 10. Track event with complex nested properties
# ===========================================================================


class TestTrackEventComplexProperties:
    """POST /api/analytics/track/event with deeply nested properties dict."""

    def test_deeply_nested_properties(self, client, mock_analytics):
        nested = {
            "level1": {
                "level2": {
                    "level3": {
                        "value": 42,
                        "tags": ["a", "b", "c"],
                    }
                }
            },
            "metadata": {
                "experiment": "variant_a",
                "flags": {"dark_mode": True, "beta": False},
            },
        }
        resp = client.post(
            "/api/analytics/track/event",
            json={"event_type": "experiment_trigger", "properties": nested},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"
        # Verify the nested properties were passed through to the service
        call_kwargs = mock_analytics.track_event.call_args
        passed = call_kwargs.kwargs.get("properties", {})
        assert passed["level1"]["level2"]["level3"]["value"] == 42

    def test_properties_with_special_characters(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={
                "event_type": "form_submit",
                "properties": {
                    "input_value": "Hello <script>alert('xss')</script>",
                    "unicode_field": "Caf\u00e9 \u2603",
                    "empty_nested": {},
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"

    def test_properties_with_large_list(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={
                "event_type": "batch_action",
                "properties": {
                    "item_ids": list(range(100)),
                    "summary": {"count": 100, "action": "archive"},
                },
            },
        )
        assert resp.status_code == 200
        call_kwargs = mock_analytics.track_event.call_args
        passed = call_kwargs.kwargs.get("properties", {})
        assert len(passed["item_ids"]) == 100

    def test_empty_properties_accepted(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={"event_type": "ping", "properties": {}},
        )
        assert resp.status_code == 200

    def test_track_event_with_all_optional_fields(self, client, mock_analytics):
        resp = client.post(
            "/api/analytics/track/event",
            json={
                "event_type": "full_event",
                "properties": {"key": "val"},
                "page_url": "https://app.example.com/dashboard",
                "referrer": "https://google.com",
                "session_id": "sess_abc123",
                "project_id": "proj_xyz",
            },
        )
        assert resp.status_code == 200
        call_kwargs = mock_analytics.track_event.call_args
        assert call_kwargs.kwargs.get("page_url") == "https://app.example.com/dashboard"
        assert call_kwargs.kwargs.get("referrer") == "https://google.com"
        assert call_kwargs.kwargs.get("session_id") == "sess_abc123"
        assert call_kwargs.kwargs.get("project_id") == "proj_xyz"
