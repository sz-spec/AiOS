"""
Tests for prompt_routes — Prompt History API (/api/prompts/*).

The router uses PromptHistoryService injected via
Depends(get_prompt_history_service).  All service methods are mocked via
app.dependency_overrides.

The routes call entry_to_response() which accesses PromptEntry attributes
directly, so mocks return objects whose attributes match PromptEntry.
For simplicity, mock methods that return collections return plain dicts
that the route then passes through (export, stats, types) while methods
that go through entry_to_response() return MagicMock objects that expose
the required attribute surface.

Covers 13+ endpoints: CRUD, search, export, stats, rate, favorite, tags.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from api.prompt_routes import router as _prompt_router

from prompts.prompt_history import get_prompt_history_service

# Mount the prompt router (not mounted in main.py for production yet)
_PROMPT_PREFIX = "/api/prompts"
_mounted_prompt = False
for _route in app.routes:
    if hasattr(_route, "path") and _route.path.startswith(_PROMPT_PREFIX):
        _mounted_prompt = True
        break
if not _mounted_prompt:
    app.include_router(_prompt_router, prefix="/api", tags=["Prompts-Test"])


# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

MOCK_PROMPT = {
    "id": "prompt1",
    "input_prompt": "Write a Python function",
    "prompt_type": "code",
    "status": "completed",
    "project_id": "proj1",
    "session_id": "sess1",
    "model": "gpt-4",
    "provider": "openai",
    "output_response": "def hello(): pass",
    "prompt_tokens": 20,
    "completion_tokens": 10,
    "total_tokens": 30,
    "duration_ms": 500,
    "rating": None,
    "is_favorite": False,
    "tags": ["python"],
    "created_at": "2024-01-01T00:00:00",
}


def _make_prompt_entry(data: dict = None) -> MagicMock:
    """
    Build a MagicMock that matches the PromptEntry attribute interface
    required by entry_to_response() inside the route.
    """
    d = data or MOCK_PROMPT
    entry = MagicMock()
    entry.id = d["id"]
    entry.user_id = "user_123"
    entry.project_id = d.get("project_id")
    entry.prompt_type = MagicMock(value=d["prompt_type"])
    entry.status = MagicMock(value=d["status"])
    entry.input_prompt = d["input_prompt"]
    entry.output_response = d["output_response"]
    entry.generated_code = []
    entry.model = d["model"]
    entry.provider = d["provider"]
    entry.prompt_tokens = d["prompt_tokens"]
    entry.completion_tokens = d["completion_tokens"]
    entry.total_tokens = d["total_tokens"]
    entry.duration_ms = d["duration_ms"]
    entry.error = None
    entry.tags = d["tags"]
    entry.rating = d["rating"]
    entry.feedback = None
    entry.is_favorite = d["is_favorite"]
    # datetime-like objects
    created = MagicMock()
    created.isoformat.return_value = d["created_at"]
    entry.created_at = created
    entry.completed_at = None
    return entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_user():
    """
    Override get_current_user so user.id == 'user_123', matching the
    MOCK_PROMPT entry.user_id ownership. Required after the April 2026 IDOR
    fix added `_require_owner` on all /{prompt_id} paths — without the
    override the dev-mode user ('dev_seed_user') would mismatch the mock's
    'user_123' owner and the ownership check would 404.
    """
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
def mock_prompt_svc():
    svc = MagicMock()
    entry = _make_prompt_entry()

    # Methods used by the routes
    svc.create_prompt.return_value = entry
    svc.list_prompts.return_value = ([entry], 1)
    svc.search_prompts.return_value = [entry]
    svc.get_prompt.return_value = entry
    svc.update_prompt.return_value = entry
    svc.delete_prompt.return_value = True
    svc.rate_prompt.return_value = True
    svc.toggle_favorite.return_value = True
    svc.add_tags.return_value = True

    # Stats — route constructs PromptStatsResponse from the returned object's attributes
    stats_obj = MagicMock()
    stats_obj.total_prompts = 10
    stats_obj.successful_prompts = 8
    stats_obj.failed_prompts = 2
    stats_obj.total_tokens = 500
    stats_obj.avg_tokens_per_prompt = 50.0
    stats_obj.total_duration_ms = 5000
    stats_obj.avg_duration_ms = 500.0
    stats_obj.by_type = {"code": 4, "chat": 6}
    stats_obj.by_model = {"gpt-4": 10}
    stats_obj.by_day = {}
    stats_obj.most_used_tags = [("python", 3)]
    svc.get_stats.return_value = stats_obj

    # Export — route unwraps data["rows"] for CSV, returns dict directly for JSON
    svc.export_prompts.return_value = {
        "rows": [
            {
                "id": "prompt1",
                "type": "chat",
                "input_prompt": "Write a Python function",
                "status": "completed",
                "model": "gpt-4",
                "provider": "openai",
                "tags": ["python"],
                "rating": None,
                "is_favorite": False,
                "created_at": "2024-01-01T00:00:00",
            }
        ],
        "total": 1,
    }

    app.dependency_overrides[get_prompt_history_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_prompt_history_service, None)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreatePrompt:
    def test_returns_200_with_prompt_object(self, client, mock_prompt_svc):
        resp = client.post(
            "/api/prompts",
            json={
                "input_prompt": "Write a Python fn",
                "prompt_type": "code_generation",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "prompt1"
        assert data["input_prompt"] == "Write a Python function"

    def test_create_calls_service(self, client, mock_prompt_svc):
        client.post("/api/prompts", json={"input_prompt": "Test"})
        mock_prompt_svc.create_prompt.assert_called_once()

    def test_missing_input_prompt_returns_422(self, client, mock_prompt_svc):
        resp = client.post("/api/prompts", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListPrompts:
    def test_returns_200_with_prompts_list(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data
        assert "total" in data
        assert data["total"] == 1

    def test_list_includes_limit_and_offset(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts?limit=10&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 10
        assert data["offset"] == 0

    def test_list_calls_service(self, client, mock_prompt_svc):
        client.get("/api/prompts")
        mock_prompt_svc.list_prompts.assert_called_once()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearchPrompts:
    def test_returns_200_with_search_results(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/search?q=python")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data
        assert "query" in data
        assert data["query"] == "python"

    def test_search_count_matches_results(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/search?q=python")
        data = resp.json()
        assert data["count"] == len(data["prompts"])

    def test_search_calls_service_with_query(self, client, mock_prompt_svc):
        client.get("/api/prompts/search?q=python")
        mock_prompt_svc.search_prompts.assert_called_once()

    def test_search_without_q_returns_422(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/search")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_returns_200_with_stats(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_prompts" in data
        assert data["total_prompts"] == 10

    def test_stats_contain_all_required_fields(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/stats")
        data = resp.json()
        for field in (
            "total_prompts",
            "successful_prompts",
            "failed_prompts",
            "total_tokens",
            "avg_tokens_per_prompt",
            "by_type",
            "by_model",
        ):
            assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExportJson:
    def test_returns_200_with_json_export(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/export/json")
        assert resp.status_code == 200

    def test_export_json_calls_service(self, client, mock_prompt_svc):
        client.get("/api/prompts/export/json")
        mock_prompt_svc.export_prompts.assert_called_once()


class TestExportCsv:
    def test_returns_200_for_csv_export(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/export/csv")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class TestGetPromptTypes:
    def test_returns_200_with_types_and_statuses(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/types/all")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        assert "statuses" in data

    def test_types_are_lists(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/types/all")
        data = resp.json()
        assert isinstance(data["types"], list)
        assert isinstance(data["statuses"], list)


# ---------------------------------------------------------------------------
# Get single prompt
# ---------------------------------------------------------------------------


class TestGetPrompt:
    def test_returns_200_for_existing_prompt(self, client, mock_prompt_svc):
        resp = client.get("/api/prompts/prompt1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "prompt1"

    def test_returns_404_when_prompt_not_found(self, client, mock_prompt_svc):
        mock_prompt_svc.get_prompt.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/prompts/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestUpdatePrompt:
    def test_patch_returns_200_with_updated_prompt(self, client, mock_prompt_svc):
        resp = client.patch("/api/prompts/prompt1", json={"status": "completed"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "prompt1"

    def test_patch_returns_404_when_prompt_not_found(self, client, mock_prompt_svc):
        mock_prompt_svc.get_prompt.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).patch("/api/prompts/missing", json={"status": "completed"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeletePrompt:
    def test_delete_returns_200_with_status(self, client, mock_prompt_svc):
        resp = client.delete("/api/prompts/prompt1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["prompt_id"] == "prompt1"

    def test_delete_returns_404_when_not_found(self, client, mock_prompt_svc):
        mock_prompt_svc.delete_prompt.return_value = False
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).delete("/api/prompts/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Rate
# ---------------------------------------------------------------------------


class TestRatePrompt:
    def test_rate_returns_200_with_rating(self, client, mock_prompt_svc):
        resp = client.post(
            "/api/prompts/prompt1/rate", json={"rating": 5, "feedback": "Great!"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rated"
        assert data["rating"] == 5

    def test_rate_returns_404_when_not_found(self, client, mock_prompt_svc):
        mock_prompt_svc.rate_prompt.return_value = False
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/prompts/missing/rate", json={"rating": 3})
        assert resp.status_code == 404

    def test_rate_missing_rating_returns_422(self, client, mock_prompt_svc):
        resp = client.post("/api/prompts/prompt1/rate", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Favorite
# ---------------------------------------------------------------------------


class TestToggleFavorite:
    def test_favorite_returns_200_with_is_favorite(self, client, mock_prompt_svc):
        resp = client.post("/api/prompts/prompt1/favorite")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert "is_favorite" in data

    def test_favorite_returns_404_when_not_found(self, client, mock_prompt_svc):
        mock_prompt_svc.toggle_favorite.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/prompts/missing/favorite")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Add Tags
# ---------------------------------------------------------------------------


class TestAddTags:
    def test_add_tags_returns_200_with_tags(self, client, mock_prompt_svc):
        resp = client.post(
            "/api/prompts/prompt1/tags", json={"tags": ["python", "function"]}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert "tags" in data
        assert "python" in data["tags"]

    def test_add_tags_returns_404_when_not_found(self, client, mock_prompt_svc):
        mock_prompt_svc.add_tags.return_value = False
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/prompts/missing/tags", json={"tags": ["test"]})
        assert resp.status_code == 404

    def test_add_tags_calls_service(self, client, mock_prompt_svc):
        client.post("/api/prompts/prompt1/tags", json={"tags": ["python", "function"]})
        mock_prompt_svc.add_tags.assert_called_once()
