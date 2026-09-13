"""API tests for checkpoint_routes — version checkpoint management."""

from services.project_service import get_project_service


def _create_project_with_files(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "test checkpoints"},
    )
    pid = resp.json()["project_id"]
    svc = get_project_service()
    svc.update_files(pid, {"app.tsx": "v1 code"})
    return pid


class TestCheckpointsAPI:
    def test_create_checkpoint(self, client):
        pid = _create_project_with_files(client)
        resp = client.post(
            f"/api/v1/projects/{pid}/checkpoints", json={"description": "v1"}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["description"] == "v1"

    def test_list_checkpoints(self, client):
        pid = _create_project_with_files(client)
        client.post(f"/api/v1/projects/{pid}/checkpoints", json={"description": "v1"})
        client.post(f"/api/v1/projects/{pid}/checkpoints", json={"description": "v2"})
        resp = client.get(f"/api/v1/projects/{pid}/checkpoints")
        assert resp.status_code == 200
        assert len(resp.json()["checkpoints"]) == 2

    def test_list_empty(self, client):
        pid = _create_project_with_files(client)
        resp = client.get(f"/api/v1/projects/{pid}/checkpoints")
        assert resp.status_code == 200
        assert resp.json()["checkpoints"] == []

    def test_restore_checkpoint(self, client):
        pid = _create_project_with_files(client)
        cp_resp = client.post(
            f"/api/v1/projects/{pid}/checkpoints", json={"description": "v1"}
        )
        cp_id = cp_resp.json()["id"]
        resp = client.post(f"/api/v1/projects/{pid}/checkpoints/{cp_id}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

    def test_restore_nonexistent(self, client):
        pid = _create_project_with_files(client)
        resp = client.post(f"/api/v1/projects/{pid}/checkpoints/fake-id/restore")
        assert resp.status_code == 404

    def test_checkpoint_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/projects/fake-id/checkpoints", json={"description": "v1"}
        )
        assert resp.status_code == 404

    def test_restore_changes_project_files(self, client):
        pid = _create_project_with_files(client)
        # Create checkpoint with v1
        cp_resp = client.post(
            f"/api/v1/projects/{pid}/checkpoints", json={"description": "v1"}
        )
        cp_id = cp_resp.json()["id"]
        # Modify files to v2
        svc = get_project_service()
        svc.update_files(pid, {"app.tsx": "v2 code"})
        # Restore to v1
        client.post(f"/api/v1/projects/{pid}/checkpoints/{cp_id}/restore")
        # Verify files are back to v1
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert proj["files"]["app.tsx"] == "v1 code"

    def test_checkpoint_empty_project(self, client):
        """Checkpoint for project with no files should still work."""
        resp = client.post(
            "/api/v1/projects/wizard",
            json={"category": "website", "description": "empty"},
        )
        pid = resp.json()["project_id"]
        resp = client.post(
            f"/api/v1/projects/{pid}/checkpoints",
            json={"description": "empty snapshot"},
        )
        assert resp.status_code == 201
