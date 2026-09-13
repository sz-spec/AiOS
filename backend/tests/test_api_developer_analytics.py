"""Tests for api/developer_analytics_routes.py — Analytics routes."""


class TestDeveloperAnalyticsRoutes:
    def test_overview_fallback(self, client):
        resp = client.get("/api/developers/analytics/dev-001/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["developer_id"] == "dev-001"
        assert body["total_apps"] == 0
        assert body["total_downloads"] == 0
        assert body["average_rating"] == 0.0

    def test_earnings_fallback(self, client):
        resp = client.get("/api/developers/analytics/dev-001/earnings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_earnings"] == 0
        assert body["pending_payout"] == 0
        assert body["last_payout"] is None

    def test_app_metrics_with_period(self, client):
        resp = client.get(
            "/api/developers/analytics/dev-001/apps/app-123/metrics?period=7d"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_id"] == "app-123"
        assert body["period"] == "7d"
        assert body["downloads"] == 0
        assert body["revenue"] == 0.0

    def test_app_metrics_default_period(self, client):
        resp = client.get("/api/developers/analytics/dev-001/apps/app-123/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["period"] == "30d"

    def test_overview_returns_structure(self, client):
        resp = client.get("/api/developers/analytics/unknown-dev/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {
            "developer_id",
            "total_apps",
            "total_downloads",
            "average_rating",
        }
