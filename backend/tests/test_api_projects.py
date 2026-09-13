"""API tests for project_routes — project CRUD."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard", json={"category": "website", "description": "test"}
    )
    return resp.json()["project_id"]


class TestProjectsAPI:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert resp.json()["projects"] == []

    def test_list_after_create(self, client):
        _create_project(client)
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert len(resp.json()["projects"]) == 1

    def test_get_project(self, client):
        pid = _create_project(client)
        resp = client.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == pid

    def test_get_nonexistent(self, client):
        resp = client.get("/api/v1/projects/fake-id")
        assert resp.status_code == 404

    def test_delete_project(self, client):
        pid = _create_project(client)
        resp = client.delete(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/v1/projects/fake-id")
        assert resp.status_code == 404

    def test_project_has_files_field(self, client):
        pid = _create_project(client)
        resp = client.get(f"/api/v1/projects/{pid}")
        assert "files" in resp.json()
