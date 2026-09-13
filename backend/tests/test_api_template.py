"""
Tests for template_routes — Template Library API (/api/templates/*).

The router calls get_template_service() directly (no FastAPI Depends), so
the service factory is patched with monkeypatch on the module attribute
'api.template_routes.get_template_service'.

The route builds TemplatePreview / TemplateDetail Pydantic models from the
returned objects by accessing their attributes directly, so mocks must expose
the correct attribute surface (category.value, framework.value,
difficulty.value, files list, etc.).

Covers 8+ endpoints: list, categories, popular, new, get by id, use.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

MOCK_TEMPLATE = {
    "id": "nextjs-saas",
    "name": "Next.js SaaS Starter",
    "description": "Full-stack SaaS with auth, billing, and dashboard",
    "category": "saas",
    "framework": "nextjs",
    "difficulty": "intermediate",
    "thumbnail": "/thumbnails/nextjs-saas.png",
    "tags": ["saas", "nextjs", "typescript"],
    "features": ["Authentication", "Billing", "Dashboard"],
    "is_premium": False,
    "is_new": True,
    "is_popular": True,
    "downloads": 500,
}
MOCK_TEMPLATE_DETAIL = {
    **MOCK_TEMPLATE,
    "files": [
        {"path": "package.json", "content": '{"name": "app"}', "language": "json"}
    ],
    "dependencies": {"next": "^14.0.0"},
    "preview_url": None,
}


def _make_template_obj(data: dict = None, detail: bool = False) -> MagicMock:
    """
    Build a MagicMock that satisfies all template attribute accesses in the route.
    Both TemplatePreview and TemplateDetail code paths are covered.
    """
    d = data or (MOCK_TEMPLATE_DETAIL if detail else MOCK_TEMPLATE)
    t = MagicMock()
    t.id = d["id"]
    t.name = d["name"]
    t.description = d["description"]
    t.category = MagicMock(value=d["category"])
    t.framework = MagicMock(value=d["framework"])
    t.difficulty = MagicMock(value=d["difficulty"])
    t.thumbnail = d["thumbnail"]
    t.tags = d["tags"]
    t.features = d["features"]
    t.is_premium = d["is_premium"]
    t.is_new = d["is_new"]
    t.is_popular = d["is_popular"]
    t.downloads = d["downloads"]

    if detail:
        # Build file objects as the route accesses f.path / f.content / f.language
        file_obj = MagicMock()
        file_obj.path = d["files"][0]["path"]
        file_obj.content = d["files"][0]["content"]
        file_obj.language = d["files"][0]["language"]
        t.files = [file_obj]
        t.dependencies = d["dependencies"]
        t.preview_url = d["preview_url"]

    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def mock_template_svc(monkeypatch):
    svc = MagicMock()

    template_obj = _make_template_obj()
    detail_obj = _make_template_obj(detail=True)

    svc.get_all.return_value = [template_obj]
    svc.search.return_value = [template_obj]
    svc.get_by_category.return_value = [template_obj]
    svc.get_categories.return_value = [{"name": "saas", "count": 5, "icon": "layers"}]
    svc.get_popular.return_value = [template_obj]
    svc.get_new.return_value = [template_obj]
    svc.get_by_id.return_value = detail_obj
    svc.increment_downloads = MagicMock()

    # increment_downloads is awaited inside use_template (AsyncMock needed)
    from unittest.mock import AsyncMock

    svc.increment_downloads = AsyncMock(return_value=None)

    monkeypatch.setattr("api.template_routes.get_template_service", lambda: svc)
    return svc


# ---------------------------------------------------------------------------
# List templates
# ---------------------------------------------------------------------------


class TestListTemplates:
    def test_returns_200_with_template_list(self, client, mock_template_svc):
        resp = client.get("/api/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_template_contains_expected_fields(self, client, mock_template_svc):
        resp = client.get("/api/templates")
        t = resp.json()[0]
        for field in (
            "id",
            "name",
            "description",
            "category",
            "framework",
            "difficulty",
            "tags",
            "features",
            "is_premium",
            "is_new",
            "is_popular",
            "downloads",
        ):
            assert field in t, f"Missing field: {field}"

    def test_template_id_matches_mock(self, client, mock_template_svc):
        resp = client.get("/api/templates")
        assert resp.json()[0]["id"] == "nextjs-saas"

    def test_list_calls_service_get_all(self, client, mock_template_svc):
        client.get("/api/templates")
        mock_template_svc.get_all.assert_called_once()

    def test_search_query_uses_service_search(self, client, mock_template_svc):
        resp = client.get("/api/templates?search=saas")
        assert resp.status_code == 200
        mock_template_svc.search.assert_called_once_with("saas")

    def test_category_filter_uses_service_get_by_category(
        self, client, mock_template_svc
    ):
        resp = client.get("/api/templates?category=saas")
        assert resp.status_code == 200
        mock_template_svc.get_by_category.assert_called_once()


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class TestGetCategories:
    def test_returns_200_with_categories_list(self, client, mock_template_svc):
        resp = client.get("/api/templates/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_category_contains_name_count_icon(self, client, mock_template_svc):
        resp = client.get("/api/templates/categories")
        cat = resp.json()[0]
        assert cat["name"] == "saas"
        assert cat["count"] == 5
        assert "icon" in cat

    def test_categories_calls_service(self, client, mock_template_svc):
        client.get("/api/templates/categories")
        mock_template_svc.get_categories.assert_called_once()


# ---------------------------------------------------------------------------
# Popular templates
# ---------------------------------------------------------------------------


class TestGetPopularTemplates:
    def test_returns_200_with_popular_list(self, client, mock_template_svc):
        resp = client.get("/api/templates/popular?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_popular_calls_service_with_limit(self, client, mock_template_svc):
        client.get("/api/templates/popular?limit=5")
        mock_template_svc.get_popular.assert_called_once_with(limit=5)

    def test_popular_template_is_popular_flag_true(self, client, mock_template_svc):
        resp = client.get("/api/templates/popular")
        assert resp.json()[0]["is_popular"] is True


# ---------------------------------------------------------------------------
# New templates
# ---------------------------------------------------------------------------


class TestGetNewTemplates:
    def test_returns_200_with_new_list(self, client, mock_template_svc):
        resp = client.get("/api/templates/new?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_new_calls_service_with_limit(self, client, mock_template_svc):
        client.get("/api/templates/new?limit=5")
        mock_template_svc.get_new.assert_called_once_with(limit=5)

    def test_new_template_is_new_flag_true(self, client, mock_template_svc):
        resp = client.get("/api/templates/new")
        assert resp.json()[0]["is_new"] is True


# ---------------------------------------------------------------------------
# Get template by ID
# ---------------------------------------------------------------------------


class TestGetTemplate:
    def test_returns_200_with_template_detail(self, client, mock_template_svc):
        resp = client.get("/api/templates/nextjs-saas")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "nextjs-saas"
        assert "files" in data
        assert "dependencies" in data

    def test_template_detail_has_files_list(self, client, mock_template_svc):
        resp = client.get("/api/templates/nextjs-saas")
        files = resp.json()["files"]
        assert isinstance(files, list)
        assert len(files) == 1
        assert files[0]["path"] == "package.json"

    def test_returns_404_when_template_not_found(self, client, mock_template_svc):
        mock_template_svc.get_by_id.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/templates/nonexistent")
        assert resp.status_code == 404

    def test_get_calls_service_get_by_id(self, client, mock_template_svc):
        client.get("/api/templates/nextjs-saas")
        mock_template_svc.get_by_id.assert_called_with("nextjs-saas")


# ---------------------------------------------------------------------------
# Use template
# ---------------------------------------------------------------------------


class TestUseTemplate:
    def test_returns_200_with_template_files(self, client, mock_template_svc):
        resp = client.post("/api/templates/nextjs-saas/use")
        assert resp.status_code == 200
        data = resp.json()
        assert data["template_id"] == "nextjs-saas"
        assert "files" in data
        assert "dependencies" in data

    def test_use_increments_downloads(self, client, mock_template_svc):
        client.post("/api/templates/nextjs-saas/use")
        mock_template_svc.increment_downloads.assert_called_once_with("nextjs-saas")

    def test_use_returns_404_when_template_not_found(self, client, mock_template_svc):
        mock_template_svc.get_by_id.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/templates/nonexistent/use")
        assert resp.status_code == 404

    def test_use_response_contains_framework(self, client, mock_template_svc):
        resp = client.post("/api/templates/nextjs-saas/use")
        data = resp.json()
        assert data["framework"] == "nextjs"
        assert data["name"] == "Next.js SaaS Starter"
