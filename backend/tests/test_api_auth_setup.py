"""API tests for auth_setup_routes — authentication setup."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard", json={"category": "app", "description": "test auth"}
    )
    return resp.json()["project_id"]


class TestAuthSetupAPI:
    def test_setup_auth_enabled(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/auth/setup",
            json={"project_id": pid, "enabled": True, "providers": ["email"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["files_generated"] == 4

    def test_setup_auth_disabled(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/auth/setup", json={"project_id": pid, "enabled": False}
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["files_generated"] == 0

    def test_setup_auth_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/auth/setup",
            json={"project_id": "fake-id", "enabled": True, "providers": ["email"]},
        )
        assert resp.status_code == 404

    def test_auth_files_in_project(self, client):
        pid = _create_project(client)
        client.post(
            "/api/v1/auth/setup",
            json={"project_id": pid, "enabled": True, "providers": ["email"]},
        )
        proj = client.get(f"/api/v1/projects/{pid}").json()
        file_keys = " ".join(proj["files"].keys()).lower()
        assert "login" in file_keys
        assert "auth" in file_keys
