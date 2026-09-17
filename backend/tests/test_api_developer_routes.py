"""Tests for api/developer_routes.py — Developer portal routes."""

from unittest.mock import AsyncMock, patch


class TestDeveloperRoutes:
    def test_register_developer_201(self, client):
        resp = client.post(
            "/api/developers/register",
            json={
                "display_name": "Test Dev",
                "email": "dev@test.com",
                "website": "https://test.com",
                "bio": "A developer",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["display_name"] == "Test Dev"
        assert body["email"] == "dev@test.com"
        assert "id" in body

    def test_register_returns_name_and_email(self, client):
        resp = client.post(
            "/api/developers/register",
            json={
                "display_name": "Dev Two",
                "email": "dev2@test.com",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["display_name"] == "Dev Two"
        assert body["email"] == "dev2@test.com"

    def test_register_minimal(self, client):
        resp = client.post(
            "/api/developers/register",
            json={
                "display_name": "Min",
                "email": "min@test.com",
            },
        )
        assert resp.status_code == 201

    def test_get_profile_404(self, client):
        repository = AsyncMock()
        repository.get_profile.return_value = None
        with patch(
            "core.repositories.get_async_developer_repository", return_value=repository
        ):
            resp = client.get("/api/developers/profile/nonexistent-user-xyz")
        repository.get_profile.assert_awaited_once_with(user_id="nonexistent-user-xyz")
        assert resp.status_code == 404

    def test_register_missing_fields_422(self, client):
        resp = client.post("/api/developers/register", json={})
        assert resp.status_code == 422

    def test_register_includes_optional_fields(self, client):
        resp = client.post(
            "/api/developers/register",
            json={
                "display_name": "Full Dev",
                "email": "full@test.com",
                "website": "https://example.com",
                "bio": "A full developer profile",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["website"] == "https://example.com"
        assert body["bio"] == "A full developer profile"
