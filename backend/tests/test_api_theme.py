"""
API tests for theme_routes — theme management.

Router:  api/theme_routes.py  (prefix "/themes")
         NOT mounted in main.py, so we mount it at "/api/themes" for tests.
Service: ThemeService (tools.themes)
         injected via Depends(get_theme_service).

The dependency is overridden via app.dependency_overrides.
Note: the router is included once at module import time so tests share the
      same app instance that conftest.py's `client` fixture also uses.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from api.theme_routes import router as theme_router
from tools.themes import get_theme_service

# Mount the theme router once at the "/api/themes" prefix for this test module.
# Guard prevents double-mounting if pytest re-imports the module.
_THEME_PREFIX = "/api/themes"
_already_mounted = any(
    getattr(r, "path", "").startswith(_THEME_PREFIX) for r in app.routes
)
if not _already_mounted:
    app.include_router(theme_router, prefix="/api", tags=["Themes-Test"])


# =============================================================================
# Shared mock data
# =============================================================================

MOCK_THEME = {
    "id": "theme1",
    "name": "Test Theme",
    "description": "A test theme",
    "mode": "dark",
    "category": "custom",
    "base_theme_id": None,
    "colors": {"primary": "#007AFF", "background": "#000000"},
    "typography": {},
    "author_id": "user_123",
    "author_name": "Test User",
    "organization_id": None,
    "is_public": True,
    "downloads": 0,
    "tags": ["minimal"],
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
    "preview_url": None,
    "is_preset": False,
}


def _make_theme_obj(data=None) -> MagicMock:
    """Return a MagicMock that behaves like a Theme domain object."""
    d = {**MOCK_THEME, **(data or {})}
    obj = MagicMock()
    obj.to_dict.return_value = d
    obj.to_css.return_value = ":root { --primary: #007AFF; }"
    obj.id = d["id"]
    for k, v in d.items():
        setattr(obj, k, v)
    return obj


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _override_user():
    """Override get_current_user so user.id == 'user_123' (matches MOCK_THEME author_id)."""
    from middleware.auth import get_current_user, AuthenticatedUser

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user_123", email="test@test.com"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_themes():
    svc = MagicMock()

    theme_obj = _make_theme_obj()
    theme_copy_obj = _make_theme_obj({"id": "theme2", "name": "Test Theme Copy"})

    svc.list_themes = MagicMock(return_value=[theme_obj])
    svc.get_theme = MagicMock(return_value=theme_obj)
    svc.create_theme = MagicMock(return_value=theme_obj)
    svc.update_theme = MagicMock(return_value=theme_obj)
    svc.delete_theme = MagicMock(return_value=True)
    svc.duplicate_theme = MagicMock(return_value=theme_copy_obj)
    svc.get_user_theme = MagicMock(return_value=theme_obj)
    svc.set_user_theme = MagicMock(return_value=True)
    svc.export_theme = MagicMock(return_value='{"id": "theme1"}')
    svc.import_theme = MagicMock(return_value=theme_obj)
    svc.generate_css = MagicMock(return_value=":root { --primary: #007AFF; }")

    app.dependency_overrides[get_theme_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_theme_service, None)


# =============================================================================
# List themes
# =============================================================================


class TestListThemes:
    def test_list_themes(self, client, mock_themes):
        resp = client.get("/api/themes")
        assert resp.status_code == 200
        body = resp.json()
        assert "themes" in body
        assert "total" in body
        assert body["total"] == 1
        assert body["themes"][0]["id"] == "theme1"

    def test_list_themes_with_mode_filter(self, client, mock_themes):
        resp = client.get("/api/themes?mode=dark")
        assert resp.status_code == 200

    def test_list_themes_with_search(self, client, mock_themes):
        resp = client.get("/api/themes?search=test")
        assert resp.status_code == 200

    def test_list_themes_returns_list_shape(self, client, mock_themes):
        body = client.get("/api/themes").json()
        theme = body["themes"][0]
        for field in (
            "id",
            "name",
            "mode",
            "colors",
            "typography",
            "is_public",
            "tags",
        ):
            assert field in theme, f"Missing field: {field}"


# =============================================================================
# Preset themes
# =============================================================================


class TestPresetThemes:
    def test_list_presets(self, client, mock_themes):
        resp = client.get("/api/themes/presets")
        assert resp.status_code == 200
        body = resp.json()
        assert "themes" in body
        assert "total" in body

    def test_list_presets_calls_service_with_official_category(
        self, client, mock_themes
    ):
        client.get("/api/themes/presets")
        mock_themes.list_themes.assert_called()
        call_kwargs = mock_themes.list_themes.call_args[1]
        # The route passes category=ThemeCategory.OFFICIAL
        assert call_kwargs.get("category") is not None


# =============================================================================
# Get theme by ID
# =============================================================================


class TestGetTheme:
    def test_get_theme_success(self, client, mock_themes):
        resp = client.get("/api/themes/theme1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "theme1"

    def test_get_theme_not_found(self, client, mock_themes):
        mock_themes.get_theme = MagicMock(return_value=None)
        resp = client.get("/api/themes/nonexistent")
        assert resp.status_code == 404

    def test_get_theme_response_has_required_fields(self, client, mock_themes):
        body = client.get("/api/themes/theme1").json()
        for field in ("id", "name", "description", "mode", "colors", "is_public"):
            assert field in body


# =============================================================================
# Create theme
# =============================================================================


class TestCreateTheme:
    def test_create_theme_success(self, client, mock_themes):
        resp = client.post(
            "/api/themes",
            json={"name": "My Theme", "mode": "dark", "colors": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "theme1"

    def test_create_theme_calls_service(self, client, mock_themes):
        client.post(
            "/api/themes",
            json={"name": "New Theme", "mode": "light"},
        )
        mock_themes.create_theme.assert_called_once()
        kwargs = mock_themes.create_theme.call_args[1]
        assert kwargs["name"] == "New Theme"
        assert kwargs["user_id"] == "user_123"

    def test_create_theme_missing_name(self, client, mock_themes):
        resp = client.post("/api/themes", json={"mode": "dark"})
        assert resp.status_code == 422

    def test_create_theme_empty_name_rejected(self, client, mock_themes):
        resp = client.post("/api/themes", json={"name": ""})
        assert resp.status_code == 422


# =============================================================================
# Update theme
# =============================================================================


class TestUpdateTheme:
    def test_update_theme_success(self, client, mock_themes):
        resp = client.patch(
            "/api/themes/theme1",
            json={"name": "Renamed Theme"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "theme1"

    def test_update_theme_not_found(self, client, mock_themes):
        mock_themes.update_theme = MagicMock(return_value=None)
        resp = client.patch("/api/themes/missing", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_theme_partial(self, client, mock_themes):
        resp = client.patch(
            "/api/themes/theme1",
            json={"is_public": False},
        )
        assert resp.status_code == 200


# =============================================================================
# Delete theme
# =============================================================================


class TestDeleteTheme:
    def test_delete_theme_success(self, client, mock_themes):
        resp = client.delete("/api/themes/theme1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["theme_id"] == "theme1"

    def test_delete_theme_not_found(self, client, mock_themes):
        mock_themes.delete_theme = MagicMock(return_value=False)
        resp = client.delete("/api/themes/unknown")
        assert resp.status_code == 404


# =============================================================================
# Duplicate theme
# =============================================================================


class TestDuplicateTheme:
    def test_duplicate_theme_success(self, client, mock_themes):
        resp = client.post("/api/themes/theme1/duplicate?new_name=Copy")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "theme2"
        assert body["name"] == "Test Theme Copy"

    def test_duplicate_theme_not_found(self, client, mock_themes):
        mock_themes.duplicate_theme = MagicMock(return_value=None)
        resp = client.post("/api/themes/missing/duplicate")
        assert resp.status_code == 404

    def test_duplicate_theme_calls_service(self, client, mock_themes):
        client.post("/api/themes/theme1/duplicate?new_name=NewCopy")
        mock_themes.duplicate_theme.assert_called_once()
        kwargs = mock_themes.duplicate_theme.call_args[1]
        assert kwargs["theme_id"] == "theme1"
        assert kwargs["new_name"] == "NewCopy"


# =============================================================================
# User preferences
# =============================================================================


class TestUserTheme:
    def test_get_user_current_theme(self, client, mock_themes):
        resp = client.get("/api/themes/user/current")
        assert resp.status_code == 200
        assert resp.json()["id"] == "theme1"

    def test_set_user_theme_success(self, client, mock_themes):
        resp = client.post(
            "/api/themes/user/current",
            json={"theme_id": "theme1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "updated"
        assert body["theme_id"] == "theme1"

    def test_set_user_theme_invalid(self, client, mock_themes):
        mock_themes.set_user_theme = MagicMock(return_value=False)
        resp = client.post(
            "/api/themes/user/current",
            json={"theme_id": "bad-theme"},
        )
        assert resp.status_code == 400

    def test_set_user_theme_missing_theme_id(self, client, mock_themes):
        resp = client.post("/api/themes/user/current", json={})
        assert resp.status_code == 422


# =============================================================================
# Export / Import
# =============================================================================


class TestExportImport:
    def test_export_theme_success(self, client, mock_themes):
        resp = client.get("/api/themes/theme1/export")
        assert resp.status_code == 200
        body = resp.json()
        assert body["theme_id"] == "theme1"
        assert "json" in body

    def test_export_theme_not_found(self, client, mock_themes):
        mock_themes.export_theme = MagicMock(return_value=None)
        resp = client.get("/api/themes/unknown/export")
        assert resp.status_code == 404

    def test_import_theme_success(self, client, mock_themes):
        resp = client.post(
            "/api/themes/import",
            json={"json_data": '{"id": "theme1"}'},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "theme1"

    def test_import_theme_invalid_data(self, client, mock_themes):
        mock_themes.import_theme = MagicMock(return_value=None)
        resp = client.post("/api/themes/import", json={"json_data": "{}"})
        assert resp.status_code == 400

    def test_import_theme_missing_body(self, client, mock_themes):
        resp = client.post("/api/themes/import", json={})
        assert resp.status_code == 422


# =============================================================================
# CSS endpoints
# =============================================================================


class TestCSSEndpoints:
    def test_get_all_css(self, client, mock_themes):
        resp = client.get("/api/themes/css/all")
        assert resp.status_code == 200
        body = resp.json()
        assert "css" in body
        assert "theme_ids" in body
        assert isinstance(body["theme_ids"], list)

    def test_get_user_css(self, client, mock_themes):
        resp = client.get("/api/themes/css/user")
        assert resp.status_code == 200
        body = resp.json()
        assert "css" in body
        assert "theme_ids" in body

    def test_get_theme_css(self, client, mock_themes):
        resp = client.get("/api/themes/theme1/css")
        assert resp.status_code == 200
        body = resp.json()
        assert "css" in body
        assert body["theme_id"] == "theme1"

    def test_get_theme_css_not_found(self, client, mock_themes):
        mock_themes.get_theme = MagicMock(return_value=None)
        resp = client.get("/api/themes/nonexistent/css")
        assert resp.status_code == 404


# =============================================================================
# Preview CSS
# =============================================================================


class TestPreviewCSS:
    def test_preview_css_success(self, client, mock_themes):
        resp = client.post(
            "/api/themes/preview/css",
            json={"mode": "dark", "colors": {}},
        )
        # preview/css constructs a Theme inline — status 200 expected
        assert resp.status_code == 200
        body = resp.json()
        assert "css" in body

    def test_preview_css_empty_colors(self, client, mock_themes):
        resp = client.post(
            "/api/themes/preview/css",
            json={"colors": {}},
        )
        assert resp.status_code == 200


# =============================================================================
# Parametrized: GET endpoints return 200
# =============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/api/themes",
        "/api/themes/presets",
        "/api/themes/theme1",
        "/api/themes/user/current",
        "/api/themes/theme1/export",
        "/api/themes/css/all",
        "/api/themes/css/user",
        "/api/themes/theme1/css",
    ],
)
def test_get_endpoints_return_200(client, mock_themes, path):
    resp = client.get(path)
    assert (
        resp.status_code == 200
    ), f"GET {path} returned {resp.status_code}: {resp.text}"


# =============================================================================
# Parametrized: invalid theme ID returns 404 when service returns None
# =============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/api/themes/bad-id",
        "/api/themes/bad-id/css",
        "/api/themes/bad-id/export",
    ],
)
def test_unknown_theme_id_returns_404(client, mock_themes, path):
    mock_themes.get_theme = MagicMock(return_value=None)
    mock_themes.export_theme = MagicMock(return_value=None)
    resp = client.get(path)
    assert (
        resp.status_code == 404
    ), f"GET {path} should be 404 for unknown theme, got {resp.status_code}"
