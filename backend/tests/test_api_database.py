"""API tests for database_routes — visual database generation."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "dashboard", "description": "test database"},
    )
    return resp.json()["project_id"]


def _sample_tables():
    return [
        {
            "name": "products",
            "columns": [
                {"id": "c1", "name": "name", "type": "text"},
                {"id": "c2", "name": "price", "type": "number"},
            ],
        }
    ]


class TestDatabaseAPI:
    def test_generate_database(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/database/generate",
            json={"project_id": pid, "tables": _sample_tables()},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "ddl" in body
        assert body["files_generated"] > 0

    def test_generate_updates_project_files(self, client):
        pid = _create_project(client)
        client.post(
            "/api/v1/database/generate",
            json={"project_id": pid, "tables": _sample_tables()},
        )
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert any("schema" in k.lower() for k in proj["files"].keys())

    def test_generate_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/database/generate",
            json={"project_id": "fake-id", "tables": _sample_tables()},
        )
        assert resp.status_code == 404

    def test_generate_empty_tables(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/database/generate", json={"project_id": pid, "tables": []}
        )
        assert resp.status_code == 200
        assert resp.json()["files_generated"] == 0
