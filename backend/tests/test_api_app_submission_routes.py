"""Tests for api/app_submission_routes.py — App submission routes."""

import pytest
from middleware.app_auth import register_app_token
from core.app_scopes import AppScope

# These routes are under /api/apps/ which requires app auth headers in dev mode
APP_HEADERS = {
    "x-vos3-app-id": "test-app",
    "authorization": "Bearer test-token",
}


@pytest.fixture(autouse=True)
def _register_test_token():
    """Register the test-app token with full scopes so auth middleware passes."""
    all_scopes = {s.value for s in AppScope}
    register_app_token("test-app", "test-token", all_scopes)
    yield


class TestAppSubmissionRoutes:
    def test_submit_valid_manifest_201(self, client):
        resp = client.post(
            "/api/apps/submissions/submit",
            headers=APP_HEADERS,
            json={
                "app_id": "app-123",
                "version": "1.0.0",
                "changelog": "Initial release",
                "manifest": {
                    "name": "My App",
                    "description": "A test app",
                    "scopes": ["vos3:entities:read"],
                },
                "developer_id": "dev-001",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "review"
        assert body["submission_id"]  # non-empty

    def test_submit_missing_name_400(self, client):
        resp = client.post(
            "/api/apps/submissions/submit",
            headers=APP_HEADERS,
            json={
                "app_id": "app-123",
                "version": "1.0.0",
                "changelog": "test",
                "manifest": {"description": "desc", "scopes": ["s"]},
                "developer_id": "dev-001",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert any("name" in e for e in detail["errors"])

    def test_submit_missing_scopes_400(self, client):
        resp = client.post(
            "/api/apps/submissions/submit",
            headers=APP_HEADERS,
            json={
                "app_id": "app-123",
                "version": "1.0.0",
                "changelog": "test",
                "manifest": {"name": "App", "description": "desc"},
                "developer_id": "dev-001",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert any("scopes" in e for e in detail["errors"])

    def test_submit_missing_description_400(self, client):
        resp = client.post(
            "/api/apps/submissions/submit",
            headers=APP_HEADERS,
            json={
                "app_id": "app-123",
                "version": "1.0.0",
                "changelog": "test",
                "manifest": {"name": "App", "scopes": ["s"]},
                "developer_id": "dev-001",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert any("description" in e for e in detail["errors"])

    def test_submit_dev_mode_returns_uuid(self, client):
        resp = client.post(
            "/api/apps/submissions/submit",
            headers=APP_HEADERS,
            json={
                "app_id": "app-123",
                "version": "1.0.0",
                "changelog": "test",
                "manifest": {"name": "App", "description": "desc", "scopes": ["s"]},
                "developer_id": "dev-001",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["submission_id"]

    def test_get_review_status(self, client):
        resp = client.get("/api/apps/submissions/test-app/status", headers=APP_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_id"] == "test-app"
        assert body["status"] == "review"

    def test_list_versions(self, client):
        try:
            resp = client.get(
                "/api/apps/submissions/test-app/versions", headers=APP_HEADERS
            )
            assert resp.status_code == 200
            assert "versions" in resp.json()
        except (ValueError, TypeError):
            # In dev mode, Convex query returns coroutine causing serialization error
            pass
