"""
Tests for design_system_routes — Design System API (/api/design-system/*).

The router uses DesignSystemService injected via
Depends(get_design_system_service).  All service methods are mocked via
app.dependency_overrides.

Key routing nuances from the actual route implementation:
- GET  /design-system        → service.get_system() → system.to_dict()
- GET  /design-system/tokens → service.get_system() → iterates system.colors,
                               system.typography, ... calling .to_dict()
- GET  /design-system/components   → service.get_active_system() → system.components
- GET  /design-system/components/{id} → iterates components for matching comp.id
- POST /design-system/css    → service.generate_css_variables() → PlainTextResponse
- GET  /design-system/css    → service.generate_css_variables() → PlainTextResponse
- GET  /design-system/tailwind → service.generate_tailwind_config()
- POST /design-system/figma/import → service.parse_figma_tokens() → tokens dict
- GET  /design-system/figma/export → service.export_to_figma_tokens()
- GET  /design-system/categories   → static response (no service call)

Covers 10+ endpoints.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from api.design_system_routes import router as _ds_router

from design_system.design_system_service import get_design_system_service

# Mount the design system router (not mounted in main.py for production yet)
_DS_PREFIX = "/api/design-system"
_mounted_ds = False
for route in app.routes:
    if hasattr(route, "path") and route.path.startswith(_DS_PREFIX):
        _mounted_ds = True
        break
if not _mounted_ds:
    app.include_router(_ds_router, prefix="/api", tags=["DesignSystem-Test"])


# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

MOCK_DS = {
    "id": "ds1",
    "name": "VOS Design System",
    "version": "1.0.0",
    "tokens": {
        "colors": {"primary": "#007AFF"},
        "typography": {},
        "spacing": {},
        "shadows": {},
        "radii": {},
        "animations": {},
    },
    "components": [],
}
MOCK_COMPONENT = {
    "id": "button",
    "name": "Button",
    "category": "interactive",
    "description": "A button component",
    "variants": [],
    "props": [],
    "code": "<button>Click me</button>",
}

CSS_SNIPPET = ":root { --primary: #007AFF; }"
TAILWIND_CONFIG = {"extend": {"colors": {"primary": "#007AFF"}}}


def _make_token_obj(data: dict) -> MagicMock:
    obj = MagicMock()
    obj.to_dict.return_value = data
    return obj


def _make_component_obj(data: dict) -> MagicMock:
    obj = MagicMock()
    obj.id = data["id"]
    obj.category = data["category"]
    obj.to_dict.return_value = data
    return obj


def _make_system_obj() -> MagicMock:
    """Build a system mock matching the attribute access pattern in the routes."""
    system = MagicMock()
    system.to_dict.return_value = MOCK_DS

    # Token collections — each item has a .to_dict() method
    color_tok = _make_token_obj(
        {"name": "primary", "value": "#007AFF", "type": "color"}
    )
    system.colors = [color_tok]
    system.typography = []
    system.spacing = []
    system.shadows = []
    system.radii = []
    system.animations = []

    # Component list
    comp = _make_component_obj(MOCK_COMPONENT)
    system.components = [comp]

    return system


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_ds_svc():
    svc = MagicMock()
    system = _make_system_obj()

    svc.get_system.return_value = system
    svc.get_active_system.return_value = system
    svc.generate_css_variables.return_value = CSS_SNIPPET
    svc.generate_tailwind_config.return_value = TAILWIND_CONFIG
    svc.parse_figma_tokens.return_value = {"colors": {"primary": "#007AFF"}}
    svc.export_to_figma_tokens.return_value = {"tokens": {}}

    app.dependency_overrides[get_design_system_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_design_system_service, None)


# ---------------------------------------------------------------------------
# Get design system
# ---------------------------------------------------------------------------


class TestGetDesignSystem:
    def test_returns_200_with_design_system(self, client, mock_ds_svc):
        resp = client.get("/api/design-system")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "ds1"
        assert data["name"] == "VOS Design System"

    def test_design_system_has_version(self, client, mock_ds_svc):
        resp = client.get("/api/design-system")
        assert resp.json()["version"] == "1.0.0"

    def test_returns_404_when_system_not_found(self, client, mock_ds_svc):
        mock_ds_svc.get_system.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/design-system")
        assert resp.status_code == 404

    def test_calls_service_get_system(self, client, mock_ds_svc):
        client.get("/api/design-system")
        mock_ds_svc.get_system.assert_called_once()


# ---------------------------------------------------------------------------
# Get tokens
# ---------------------------------------------------------------------------


class TestGetTokens:
    def test_returns_200_with_token_categories(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/tokens")
        assert resp.status_code == 200
        data = resp.json()
        for key in (
            "colors",
            "typography",
            "spacing",
            "shadows",
            "radii",
            "animations",
        ):
            assert key in data, f"Missing token category: {key}"

    def test_colors_token_is_list(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/tokens")
        assert isinstance(resp.json()["colors"], list)

    def test_colors_contain_primary_token(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/tokens")
        colors = resp.json()["colors"]
        assert len(colors) == 1
        assert colors[0]["name"] == "primary"

    def test_returns_404_when_system_not_found(self, client, mock_ds_svc):
        mock_ds_svc.get_system.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/design-system/tokens")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Get components
# ---------------------------------------------------------------------------


class TestGetComponents:
    def test_returns_200_with_components_list(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/components")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert isinstance(data["components"], list)
        assert len(data["components"]) == 1

    def test_component_contains_expected_fields(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/components")
        comp = resp.json()["components"][0]
        assert comp["id"] == "button"
        assert comp["name"] == "Button"
        assert comp["category"] == "interactive"

    def test_category_filter_returns_matching_components(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/components?category=interactive")
        assert resp.status_code == 200
        components = resp.json()["components"]
        assert all(c["category"] == "interactive" for c in components)

    def test_category_filter_excludes_non_matching(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/components?category=layout")
        assert resp.status_code == 200
        # No layout components in our mock — list should be empty
        assert resp.json()["components"] == []


# ---------------------------------------------------------------------------
# Get single component
# ---------------------------------------------------------------------------


class TestGetComponent:
    def test_returns_200_for_existing_component(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/components/button")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "button"
        assert data["name"] == "Button"

    def test_returns_404_for_missing_component(self, client, mock_ds_svc):
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/design-system/components/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Generate CSS (POST)
# ---------------------------------------------------------------------------


class TestGenerateCssPost:
    def test_returns_200_with_css_content(self, client, mock_ds_svc):
        resp = client.post("/api/design-system/css", json={"mode": "light"})
        assert resp.status_code == 200
        assert ":root" in resp.text

    def test_css_media_type_is_text(self, client, mock_ds_svc):
        resp = client.post("/api/design-system/css", json={"mode": "light"})
        assert "text" in resp.headers.get("content-type", "")

    def test_calls_generate_css_variables(self, client, mock_ds_svc):
        client.post("/api/design-system/css", json={"mode": "light"})
        mock_ds_svc.generate_css_variables.assert_called()


# ---------------------------------------------------------------------------
# Get CSS (GET)
# ---------------------------------------------------------------------------


class TestGetCssGet:
    def test_get_returns_200_with_css(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/css?mode=dark")
        assert resp.status_code == 200
        assert ":root" in resp.text

    def test_get_css_calls_service(self, client, mock_ds_svc):
        client.get("/api/design-system/css")
        mock_ds_svc.generate_css_variables.assert_called()


# ---------------------------------------------------------------------------
# Tailwind config
# ---------------------------------------------------------------------------


class TestGetTailwindConfig:
    def test_returns_200_with_tailwind_config(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/tailwind")
        assert resp.status_code == 200
        data = resp.json()
        assert "extend" in data

    def test_tailwind_has_colors_extension(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/tailwind")
        assert "colors" in resp.json()["extend"]

    def test_calls_generate_tailwind_config(self, client, mock_ds_svc):
        client.get("/api/design-system/tailwind")
        mock_ds_svc.generate_tailwind_config.assert_called_once()


# ---------------------------------------------------------------------------
# Figma import
# ---------------------------------------------------------------------------


class TestFigmaImport:
    def test_returns_200_with_imported_tokens(self, client, mock_ds_svc):
        resp = client.post("/api/design-system/figma/import", json={"tokens": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert "tokens" in data
        assert "message" in data

    def test_import_calls_parse_figma_tokens(self, client, mock_ds_svc):
        client.post("/api/design-system/figma/import", json={"tokens": {}})
        mock_ds_svc.parse_figma_tokens.assert_called_once_with({})

    def test_missing_tokens_body_returns_422(self, client, mock_ds_svc):
        resp = client.post("/api/design-system/figma/import", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Figma export
# ---------------------------------------------------------------------------


class TestFigmaExport:
    def test_returns_200_with_export_data(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/figma/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "tokens" in data

    def test_export_calls_service(self, client, mock_ds_svc):
        client.get("/api/design-system/figma/export")
        mock_ds_svc.export_to_figma_tokens.assert_called_once()


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class TestGetComponentCategories:
    def test_returns_200_with_categories(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data

    def test_categories_is_list_with_id_and_name(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/categories")
        categories = resp.json()["categories"]
        assert isinstance(categories, list)
        assert len(categories) > 0
        for cat in categories:
            assert "id" in cat
            assert "name" in cat

    def test_button_category_is_present(self, client, mock_ds_svc):
        resp = client.get("/api/design-system/categories")
        ids = [c["id"] for c in resp.json()["categories"]]
        assert "button" in ids
