"""API tests for build_routes — SSE build progress streaming."""

from unittest.mock import patch


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "test build"},
    )
    return resp.json()["project_id"]


def _instant_sleep(*args, **kwargs):
    """Replace asyncio.sleep with instant return."""
    import asyncio

    f = asyncio.Future()
    f.set_result(None)
    return f


class TestBuildAPI:
    def test_stream_build(self, client):
        pid = _create_project(client)
        with patch("asyncio.sleep", new=_instant_sleep):
            resp = client.get(f"/api/v1/build/{pid}/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_stream_has_events(self, client):
        pid = _create_project(client)
        with patch("asyncio.sleep", new=_instant_sleep):
            resp = client.get(f"/api/v1/build/{pid}/stream")
        body = resp.text
        assert "node_start" in body
        assert "node_complete" in body
        assert "build_complete" in body

    def test_stream_nonexistent_sends_error_event(self, client):
        """Nonexistent project returns 200 with SSE error event, NOT 404."""
        with patch("asyncio.sleep", new=_instant_sleep):
            resp = client.get("/api/v1/build/fake-id/stream")
        assert resp.status_code == 200
        assert "node_error" in resp.text

    def test_stream_sets_project_ready(self, client):
        pid = _create_project(client)
        with patch("asyncio.sleep", new=_instant_sleep):
            client.get(f"/api/v1/build/{pid}/stream")
        proj_resp = client.get(f"/api/v1/projects/{pid}")
        proj = proj_resp.json()
        assert proj["status"] == "ready"
        assert len(proj["files"]) > 0
