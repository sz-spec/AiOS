"""API tests for wizard_routes — project creation wizard."""


class TestWizardAPI:
    def _create(self, client, **overrides):
        data = {"category": "website", "description": "My portfolio site"}
        data.update(overrides)
        return client.post("/api/v1/projects/wizard", json=data)

    def test_create_wizard_valid(self, client):
        resp = self._create(client)
        assert resp.status_code == 201
        body = resp.json()
        assert "project_id" in body
        assert "name" in body
        assert body["status"] == "building"

    def test_create_wizard_all_categories(self, client):
        for cat in ("website", "ecommerce", "dashboard", "app", "portfolio", "blog"):
            resp = self._create(client, category=cat)
            assert resp.status_code == 201, f"Category '{cat}' failed: {resp.text}"

    def test_create_wizard_invalid_category(self, client):
        resp = self._create(client, category="invalid")
        assert resp.status_code == 422

    def test_create_wizard_empty_description(self, client):
        resp = self._create(client, description="   ")
        assert resp.status_code == 422

    def test_create_wizard_missing_fields(self, client):
        resp = client.post("/api/v1/projects/wizard", json={})
        assert resp.status_code == 422

    def test_create_wizard_with_template(self, client):
        resp = self._create(client, template_id="tmpl-1")
        assert resp.status_code == 201
