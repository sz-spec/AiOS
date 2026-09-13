"""API tests for marketplace_routes — component marketplace."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "test marketplace"},
    )
    return resp.json()["project_id"]


class TestMarketplaceAPI:
    def test_list_components(self, client):
        resp = client.get("/api/v1/marketplace/components")
        assert resp.status_code == 200
        components = resp.json()["components"]
        assert len(components) >= 3  # 3 built-in components
        assert "id" in components[0]
        assert "name" in components[0]

    def test_search_by_query(self, client):
        resp = client.get("/api/v1/marketplace/components?search=navbar")
        assert resp.status_code == 200
        components = resp.json()["components"]
        assert len(components) >= 1
        assert any("navbar" in c["name"].lower() for c in components)

    def test_search_by_category(self, client):
        resp = client.get("/api/v1/marketplace/components?category=navigation")
        assert resp.status_code == 200
        components = resp.json()["components"]
        assert all(c["category"] == "navigation" for c in components)

    def test_search_no_results(self, client):
        resp = client.get("/api/v1/marketplace/components?search=nonexistent_xyz")
        assert resp.status_code == 200
        assert resp.json()["components"] == []

    def test_install_component(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/marketplace/install",
            json={"project_id": pid, "component_id": "hero-section"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["installed"] is True
        assert len(body["files"]) > 0

    def test_install_nonexistent_component(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/marketplace/install",
            json={"project_id": pid, "component_id": "fake-component"},
        )
        assert resp.status_code == 404

    def test_install_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/marketplace/install",
            json={"project_id": "fake-id", "component_id": "hero-section"},
        )
        assert resp.status_code == 404

    def test_install_persists_files(self, client):
        """Verify installed component files appear in the project."""
        pid = _create_project(client)
        client.post(
            "/api/v1/marketplace/install",
            json={"project_id": pid, "component_id": "contact-form"},
        )
        proj = client.get(f"/api/v1/projects/{pid}").json()
        file_keys = list(proj["files"].keys())
        assert any("ContactForm" in k for k in file_keys)
