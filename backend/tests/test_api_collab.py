"""API tests for collab_routes — real-time collaboration."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "app", "description": "test collab"},
    )
    return resp.json()["project_id"]


class TestCollabAPI:
    def test_invite_collaborator(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/collab/invite",
            json={"project_id": pid, "email": "dev@example.com", "role": "editor"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "dev@example.com"
        assert body["role"] == "editor"
        assert "id" in body

    def test_invite_default_role(self, client):
        """Invite without role should default to 'editor'."""
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/collab/invite",
            json={"project_id": pid, "email": "dev@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "editor"

    def test_list_collaborators(self, client):
        pid = _create_project(client)
        client.post(
            "/api/v1/collab/invite",
            json={"project_id": pid, "email": "a@test.com", "role": "editor"},
        )
        client.post(
            "/api/v1/collab/invite",
            json={"project_id": pid, "email": "b@test.com", "role": "viewer"},
        )
        resp = client.get(f"/api/v1/collab/{pid}/collaborators")
        assert resp.status_code == 200
        collabs = resp.json()["collaborators"]
        assert len(collabs) == 2

    def test_list_empty(self, client):
        pid = _create_project(client)
        resp = client.get(f"/api/v1/collab/{pid}/collaborators")
        assert resp.status_code == 200
        assert resp.json()["collaborators"] == []

    def test_remove_collaborator(self, client):
        pid = _create_project(client)
        invite_resp = client.post(
            "/api/v1/collab/invite",
            json={"project_id": pid, "email": "remove@test.com", "role": "editor"},
        )
        cid = invite_resp.json()["id"]
        resp = client.delete(f"/api/v1/collab/{pid}/collaborators/{cid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
        # Verify removal
        list_resp = client.get(f"/api/v1/collab/{pid}/collaborators")
        assert len(list_resp.json()["collaborators"]) == 0

    def test_remove_nonexistent(self, client):
        pid = _create_project(client)
        resp = client.delete(f"/api/v1/collab/{pid}/collaborators/fake-id")
        assert resp.status_code == 404
