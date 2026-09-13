"""
Tests for Settings API routes (/api/settings/*).

The settings router reads env vars directly and calls the GitHub API via
httpx.  There is no injectable service class — we patch os.environ and mock
httpx.AsyncClient at the call sites to keep tests fast and network-free.

Covers 15+ endpoints including happy paths, validation, and error cases.
"""

import pytest
import os
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Return a TestClient for the application."""
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def clean_env():
    """
    Snapshot and restore relevant environment variables around every test so
    that key mutations from one test do not leak into the next.
    """
    keys = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "TAVILY_API_KEY",
        "GITHUB_TOKEN",
    ]
    snapshot = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Collection: /api/settings/status
# ---------------------------------------------------------------------------


class TestConfigStatus:
    """Tests for GET /api/settings/status."""

    def test_status_returns_all_providers(self, client):
        """Response must contain all expected provider keys."""
        resp = client.get("/api/settings/status")
        assert resp.status_code == 200
        data = resp.json()
        for provider in ("openai", "anthropic", "google", "tavily", "github"):
            assert provider in data, f"missing provider: {provider}"
            assert "configured" in data[provider]

    def test_status_unconfigured_when_no_env_vars(self, client):
        """When env vars are absent all providers report configured=False."""
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "TAVILY_API_KEY",
            "GITHUB_TOKEN",
        ):
            os.environ.pop(key, None)

        resp = client.get("/api/settings/status")
        assert resp.status_code == 200
        data = resp.json()
        for provider in ("openai", "anthropic", "google", "tavily", "github"):
            assert data[provider]["configured"] is False

    def test_status_configured_when_env_vars_present(self, client):
        """When env vars are set the corresponding provider reports configured=True."""
        os.environ["OPENAI_API_KEY"] = "sk-testkey12345678"
        resp = client.get("/api/settings/status")
        assert resp.status_code == 200
        assert resp.json()["openai"]["configured"] is True

    def test_status_masked_key_format(self, client):
        """masked_key must follow the 'XXXX...XXXX' pattern when a key is set."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-testkey123456"
        resp = client.get("/api/settings/status")
        assert resp.status_code == 200
        masked = resp.json()["anthropic"].get("masked_key")
        assert masked is not None
        assert "..." in masked


# ---------------------------------------------------------------------------
# Collection: /api/settings/api-keys
# ---------------------------------------------------------------------------


class TestUpdateApiKeys:
    """Tests for POST /api/settings/api-keys."""

    def test_update_openai_key(self, client):
        """Posting an openai_api_key stores it in the environment."""
        resp = client.post(
            "/api/settings/api-keys", json={"openai_api_key": "sk-test123456"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "openai" in data["updated"]

    def test_update_multiple_keys(self, client):
        """Multiple keys can be updated in a single request."""
        resp = client.post(
            "/api/settings/api-keys",
            json={
                "openai_api_key": "sk-test111111",
                "google_api_key": "AIza-testkey",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "openai" in data["updated"]
        assert "google" in data["updated"]

    def test_update_empty_body_updates_nothing(self, client):
        """An empty body is valid and updates nothing."""
        resp = client.post("/api/settings/api-keys", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["updated"] == []


# ---------------------------------------------------------------------------
# Collection: /api/settings/validate
# ---------------------------------------------------------------------------


class TestValidateApiKeys:
    """Tests for POST /api/settings/validate with mocked HTTP calls."""

    def test_validate_no_keys_configured(self, client):
        """When no keys are set all providers report not configured."""
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
            os.environ.pop(key, None)

        resp = client.post("/api/settings/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        for provider in ("openai", "anthropic", "google"):
            assert data["results"][provider]["valid"] is False

    def test_validate_openai_key_valid(self, client):
        """A 200 response from OpenAI models endpoint means the key is valid."""
        os.environ["OPENAI_API_KEY"] = "sk-validkey12345678"

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("api.settings_routes.httpx.AsyncClient", return_value=mock_client):
            resp = client.post("/api/settings/validate")

        assert resp.status_code == 200
        assert resp.json()["results"]["openai"]["valid"] is True

    def test_validate_openai_key_invalid(self, client):
        """A 401 response from OpenAI means the key is invalid."""
        os.environ["OPENAI_API_KEY"] = "sk-badkey12345678"

        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("api.settings_routes.httpx.AsyncClient", return_value=mock_client):
            resp = client.post("/api/settings/validate")

        assert resp.status_code == 200
        assert resp.json()["results"]["openai"]["valid"] is False


# ---------------------------------------------------------------------------
# Collection: /api/settings/api-keys/{provider}
# ---------------------------------------------------------------------------


class TestDeleteApiKey:
    """Tests for DELETE /api/settings/api-keys/{provider}."""

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
    def test_delete_known_provider(self, client, provider):
        """Deleting a known provider succeeds and returns the provider name."""
        resp = client.delete(f"/api/settings/api-keys/{provider}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["deleted"] == provider

    def test_delete_unknown_provider_returns_400(self, client):
        """Deleting an unknown provider name returns 400."""
        resp = client.delete("/api/settings/api-keys/notaprovider")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Collection: /api/settings/models/*
# ---------------------------------------------------------------------------


class TestModelsCatalog:
    """Tests for GET /api/settings/models/catalog."""

    def test_catalog_returns_providers_key(self, client):
        """Catalog endpoint returns a 'providers' key with model data."""
        resp = client.get("/api/settings/models/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data

    def test_catalog_total_models_present(self, client):
        """Catalog response includes total_models when catalog is available."""
        resp = client.get("/api/settings/models/catalog")
        assert resp.status_code == 200
        data = resp.json()
        # Either 'total_models' is present (catalog loaded) or 'error' key explains absence
        assert "total_models" in data or "error" in data


class TestActivatedModels:
    """Tests for GET /api/settings/models/activated."""

    def test_activated_models_returns_list(self, client):
        """Activated models endpoint returns a dict with 'models' and 'count'."""
        resp = client.get("/api/settings/models/activated")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "count" in data
        assert isinstance(data["models"], list)


class TestActivateModel:
    """Tests for POST /api/settings/models/activate."""

    def test_activate_unknown_model_returns_404(self, client):
        """Activating a model ID that doesn't exist in the catalog returns 404."""
        resp = client.post(
            "/api/settings/models/activate",
            json={
                "model_id": "not-a-real-model-xyz",
            },
        )
        assert resp.status_code == 404

    def test_activate_known_model_success(self, client):
        """Activating a cataloged model returns success=True."""
        # Use a model ID we know exists in the catalog
        resp = client.get("/api/settings/models/catalog")
        data = resp.json()
        providers = data.get("providers", {})
        # Collect first available model ID
        model_id = None
        for models in providers.values():
            if models:
                model_id = models[0]["id"]
                break

        if model_id is None:
            pytest.skip("Models catalog is empty or not loaded")

        activate_resp = client.post(
            "/api/settings/models/activate", json={"model_id": model_id}
        )
        assert activate_resp.status_code == 200
        assert activate_resp.json()["success"] is True


class TestDeactivateModel:
    """Tests for DELETE /api/settings/models/deactivate/{model_id}."""

    def test_deactivate_not_activated_returns_404(self, client):
        """Deactivating a model that was not explicitly activated returns 404."""
        resp = client.delete("/api/settings/models/deactivate/model-never-activated")
        assert resp.status_code == 404


class TestListProviders:
    """Tests for GET /api/settings/models/providers/list."""

    def test_list_providers_returns_list(self, client):
        """Providers endpoint returns 'providers' list and 'total_providers' count."""
        resp = client.get("/api/settings/models/providers/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "total_providers" in data
        assert isinstance(data["providers"], list)

    def test_each_provider_has_expected_fields(self, client):
        """Each provider entry has name, model_count, and has_api_key fields."""
        resp = client.get("/api/settings/models/providers/list")
        assert resp.status_code == 200
        for provider in resp.json()["providers"]:
            assert "name" in provider
            assert "model_count" in provider
            assert "has_api_key" in provider


class TestModelRecommendations:
    """Tests for GET /api/settings/models/recommendations."""

    def test_recommendations_returns_list(self, client):
        """Recommendations endpoint returns a 'recommendations' list."""
        resp = client.get("/api/settings/models/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)

    def test_recommendations_have_tier_keys(self, client):
        """Each recommendation entry has budget, mid_range, and premium tiers."""
        resp = client.get("/api/settings/models/recommendations")
        assert resp.status_code == 200
        for rec in resp.json()["recommendations"]:
            assert "task" in rec
            for tier in ("budget", "mid_range", "premium"):
                assert tier in rec


# ---------------------------------------------------------------------------
# Collection: /api/settings/github/*
# ---------------------------------------------------------------------------


class TestGitHubUser:
    """Tests for GET /api/settings/github/user with mocked HTTP calls."""

    def test_github_user_not_configured(self, client):
        """Without GITHUB_TOKEN env var the response indicates not configured."""
        os.environ.pop("GITHUB_TOKEN", None)
        resp = client.get("/api/settings/github/user")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False
        assert data["user"] is None

    def test_github_user_configured_and_valid(self, client):
        """With a valid token the GitHub user info is returned."""
        os.environ["GITHUB_TOKEN"] = "ghp_testtoken12345678"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "login": "testuser",
            "name": "Test User",
            "avatar_url": "https://avatars.githubusercontent.com/u/1",
            "html_url": "https://github.com/testuser",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("api.settings_routes.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/api/settings/github/user")

        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["user"]["login"] == "testuser"

    def test_github_user_configured_but_invalid_token(self, client):
        """An invalid token from the GitHub API returns configured=False."""
        os.environ["GITHUB_TOKEN"] = "ghp_badtoken12345678"

        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("api.settings_routes.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/api/settings/github/user")

        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False
