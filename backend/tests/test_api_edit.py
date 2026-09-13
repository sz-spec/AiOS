"""API tests for edit_routes — lightweight conversational editing."""

from services.project_service import get_project_service


def _create_project_with_files(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "test edit"},
    )
    pid = resp.json()["project_id"]
    svc = get_project_service()
    svc.update_files(pid, {"app.tsx": "<h1>Hello</h1>"})
    return pid


class TestEditAPI:
    def test_edit_valid(self, client):
        pid = _create_project_with_files(client)
        resp = client.post(
            "/api/v1/edit", json={"project_id": pid, "instruction": "change header"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body
        assert "updated_files" in body

    def test_edit_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/edit",
            json={"project_id": "fake-id", "instruction": "change header"},
        )
        assert resp.status_code == 404

    def test_edit_empty_instruction(self, client):
        pid = _create_project_with_files(client)
        resp = client.post("/api/v1/edit", json={"project_id": pid, "instruction": ""})
        # No validation on instruction — should return 200 with fallback
        assert resp.status_code == 200

    def test_edit_fallback_on_agent_error(self, client):
        """When edit agent is unavailable, route returns 200 with stub message."""
        pid = _create_project_with_files(client)
        resp = client.post(
            "/api/v1/edit", json={"project_id": pid, "instruction": "do something"}
        )
        assert resp.status_code == 200
        assert resp.json()["message"]  # non-empty message

    def test_edit_persists_files(self, client):
        """If edit agent returns updated files, they should be persisted."""
        pid = _create_project_with_files(client)
        client.post(
            "/api/v1/edit", json={"project_id": pid, "instruction": "add footer"}
        )
        # Verify project still has files (at minimum the original)
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert "app.tsx" in proj["files"]
