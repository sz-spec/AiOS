"""API tests for deploy_routes — one-click deployment."""

from services.project_service import get_project_service


def _create_project_with_files(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "test deploy"},
    )
    pid = resp.json()["project_id"]
    svc = get_project_service()
    svc.update_files(pid, {"index.html": "<h1>Live</h1>", "app.tsx": "code"})
    return pid


class TestDeployAPI:
    def test_deploy_project(self, client):
        pid = _create_project_with_files(client)
        resp = client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "test-app"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "test-app.vcreator.app" in body["url"]
        assert body["status"] == "live"

    def test_deploy_no_files(self, client):
        resp = client.post(
            "/api/v1/projects/wizard",
            json={"category": "website", "description": "empty"},
        )
        pid = resp.json()["project_id"]
        resp = client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "empty-app"}
        )
        assert resp.status_code == 400

    def test_deploy_no_subdomain_first(self, client):
        pid = _create_project_with_files(client)
        resp = client.post("/api/v1/deploy", json={"project_id": pid})
        assert resp.status_code == 400

    def test_deploy_status(self, client):
        pid = _create_project_with_files(client)
        client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "status-test"}
        )
        resp = client.get(f"/api/v1/deploy/{pid}/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "live"

    def test_deploy_status_not_deployed(self, client):
        pid = _create_project_with_files(client)
        resp = client.get(f"/api/v1/deploy/{pid}/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_deployed"

    def test_redeploy(self, client):
        pid = _create_project_with_files(client)
        client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "redeploy-test"}
        )
        resp = client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "redeploy-test"}
        )
        assert resp.status_code == 200

    def test_deploy_updates_project_status(self, client):
        pid = _create_project_with_files(client)
        client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "status-update"}
        )
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert proj["status"] == "deployed"

    def test_redeploy_without_subdomain(self, client):
        """Second deploy without subdomain should reuse existing subdomain."""
        pid = _create_project_with_files(client)
        client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "reuse-test"}
        )
        resp = client.post("/api/v1/deploy", json={"project_id": pid})
        assert resp.status_code == 200
        assert "reuse-test.vcreator.app" in resp.json()["url"]
