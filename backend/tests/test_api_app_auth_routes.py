"""Tests for api/app_auth_routes.py — OAuth consent flow."""


class TestAppAuthRoutes:
    def test_authorize_returns_scopes(self, client):
        resp = client.post(
            "/api/apps/authorize",
            json={
                "app_id": "app-123",
                "redirect_uri": "https://example.com/callback",
                "scopes": ["vos3:entities:read", "vos3:records:write"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_id"] == "app-123"
        assert len(body["scopes"]) == 2
        assert body["scopes"][0]["scope"] == "vos3:entities:read"
        assert "description" in body["scopes"][0]

    def test_authorize_invalid_scope(self, client):
        resp = client.post(
            "/api/apps/authorize",
            json={
                "app_id": "app-123",
                "redirect_uri": "https://example.com/callback",
                "scopes": ["vos3:bogus:scope"],
            },
        )
        assert resp.status_code == 400

    def test_grant_consent(self, client):
        resp = client.post(
            "/api/apps/authorize/grant",
            json={
                "app_id": "app-123",
                "granted_scopes": ["vos3:entities:read"],
                "user_id": "user-1",
                "organization_id": "org-1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["token_type"] == "Bearer"
        assert "vos3:entities:read" in body["scopes"]
        assert body["expires_in"] == 3600

    def test_grant_invalid_scope(self, client):
        resp = client.post(
            "/api/apps/authorize/grant",
            json={
                "app_id": "app-123",
                "granted_scopes": ["vos3:invalid:scope"],
                "user_id": "user-1",
                "organization_id": "org-1",
            },
        )
        assert resp.status_code == 400

    def test_revoke_consent(self, client):
        resp = client.post(
            "/api/apps/authorize/revoke?app_id=app-123&organization_id=org-1"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["revoked"] is True
        assert body["app_id"] == "app-123"

    def test_authorize_empty_scopes(self, client):
        resp = client.post(
            "/api/apps/authorize",
            json={
                "app_id": "app-123",
                "redirect_uri": "https://example.com/callback",
                "scopes": [],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["scopes"] == []
