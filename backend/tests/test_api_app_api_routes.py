"""Tests for api/app_api_routes.py — Scoped app API routes."""

import pytest
from middleware.app_auth import register_app_token
from core.app_scopes import AppScope

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


class TestAppApiRoutes:
    def test_list_entities(self, client):
        resp = client.get("/api/apps/v1/entities?org=org1", headers=APP_HEADERS)
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_get_entity_not_found(self, client):
        resp = client.get("/api/apps/v1/entities/nonexistent", headers=APP_HEADERS)
        assert resp.status_code == 404

    def test_create_record(self, client):
        resp = client.post(
            "/api/apps/v1/records",
            headers=APP_HEADERS,
            json={
                "entityId": "entity1",
                "data": {"name": "Test"},
            },
        )
        # May return 200 or 400 depending on entity state
        assert resp.status_code in (200, 400)

    def test_list_records(self, client):
        resp = client.get("/api/apps/v1/records?entity=entity1", headers=APP_HEADERS)
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_execute_workflow(self, client):
        resp = client.post(
            "/api/apps/v1/workflows/wf1/execute",
            headers=APP_HEADERS,
            json={
                "input": {"key": "value"},
            },
        )
        assert resp.status_code in (200, 400)

    def test_ai_generate(self, client):
        resp = client.post(
            "/api/apps/v1/ai/generate",
            headers=APP_HEADERS,
            json={
                "prompt": "Hello world",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "content" in body

    def test_read_file(self, client):
        resp = client.get("/api/apps/v1/files?path=test.txt", headers=APP_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "test.txt"

    def test_write_file(self, client):
        resp = client.put(
            "/api/apps/v1/files",
            headers=APP_HEADERS,
            json={
                "path": "test.txt",
                "content": "hello",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_missing_auth_header_returns_401(self, client):
        resp = client.get("/api/apps/v1/entities?org=org1")
        assert resp.status_code == 401
