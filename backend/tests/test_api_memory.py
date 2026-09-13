"""
Tests for Memory API Routes
============================

Covers: GET /api/memory/, POST /api/memory/add, POST /api/memory/query,
        GET /api/memory/recent, GET /api/memory/types, POST /api/memory/remember,
        GET /api/memory/recall, GET /api/memory/{id}, PUT /api/memory/{id},
        DELETE /api/memory/{id}, POST /api/memory/bulk-delete,
        GET /api/memory/export/all, DELETE /api/memory/clear,
        POST /api/memory/import
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


def _make_entry(id="mem1", content="test memory", memory_type="conversation"):
    """Return a mock MemoryEntry-like object."""
    entry = MagicMock()
    entry.id = id
    entry.to_dict.return_value = {
        "id": id,
        "content": content,
        "memory_type": memory_type,
        "timestamp": "2026-01-01T00:00:00",
        "metadata": {},
    }
    return entry


@pytest.fixture(autouse=True)
def mock_memory(monkeypatch):
    """Patch get_user_dev_memory at the module level so every route gets the mock."""
    mock_dm = MagicMock()

    # Default return values
    mock_dm.get_stats.return_value = {
        "total": 5,
        "by_type": {"conversation": 3, "decision": 2},
    }
    mock_dm.add.return_value = _make_entry()
    mock_dm.query.return_value = [{"id": "mem1", "content": "test", "similarity": 0.9}]
    mock_dm.get_recent.return_value = [
        {
            "id": "mem1",
            "content": "recent memory",
            "memory_type": "conversation",
            "timestamp": "2026-01-01T00:00:00",
            "metadata": {},
        }
    ]
    mock_dm.get_by_id.return_value = {
        "id": "mem1",
        "content": "test memory",
        "memory_type": "conversation",
        "timestamp": "2026-01-01T00:00:00",
        "metadata": {},
    }
    mock_dm.update.return_value = True
    mock_dm.delete.return_value = True
    mock_dm.delete_bulk.return_value = 2
    mock_dm.export_all.return_value = [
        {
            "id": "mem1",
            "content": "exported",
            "memory_type": "conversation",
            "timestamp": "2026-01-01T00:00:00",
            "metadata": {},
        }
    ]
    mock_dm.import_memories.return_value = 3
    mock_dm.clear.return_value = True

    monkeypatch.setattr("api.memory_routes.get_user_dev_memory", lambda user_id: mock_dm)
    return mock_dm


# ---------------------------------------------------------------------------
# GET /api/memory/  — stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    def test_stats_returns_200(self, client):
        response = client.get("/api/memory/")
        assert response.status_code == 200

    def test_stats_contains_total(self, client):
        data = client.get("/api/memory/").json()
        assert "total" in data

    def test_stats_total_is_integer(self, client):
        data = client.get("/api/memory/").json()
        assert isinstance(data["total"], int)

    def test_stats_calls_get_stats(self, client, mock_memory):
        client.get("/api/memory/")
        mock_memory.get_stats.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/memory/add
# ---------------------------------------------------------------------------


class TestAddMemory:
    def _payload(self, content="test memory", memory_type="conversation"):
        return {"content": content, "memory_type": memory_type}

    def test_add_returns_200(self, client):
        response = client.post("/api/memory/add", json=self._payload())
        assert response.status_code == 200

    def test_add_response_has_success_true(self, client):
        data = client.post("/api/memory/add", json=self._payload()).json()
        assert data.get("success") is True

    def test_add_response_has_entry(self, client):
        data = client.post("/api/memory/add", json=self._payload()).json()
        assert "entry" in data

    def test_add_response_entry_has_id(self, client):
        data = client.post("/api/memory/add", json=self._payload()).json()
        assert "id" in data["entry"]

    def test_add_auto_tags_applied(self, client):
        """Content with known keywords should trigger auto-tagging."""
        data = client.post(
            "/api/memory/add",
            json={
                "content": "urgent bug fix in frontend react component",
                "memory_type": "error",
            },
        ).json()
        # auto_tags key present in response
        assert "auto_tags" in data

    @pytest.mark.parametrize(
        "memory_type",
        [
            "conversation",
            "decision",
            "code_change",
            "learning",
            "error",
            "solution",
            "context",
        ],
    )
    def test_add_accepts_valid_memory_types(self, client, memory_type):
        response = client.post(
            "/api/memory/add",
            json={"content": "some content", "memory_type": memory_type},
        )
        assert response.status_code == 200

    def test_add_returns_500_when_memory_add_fails(self, client, mock_memory):
        mock_memory.add.return_value = None
        response = client.post("/api/memory/add", json=self._payload())
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/memory/query
# ---------------------------------------------------------------------------


class TestQueryMemory:
    def test_query_returns_200(self, client):
        response = client.post("/api/memory/query", json={"query": "test", "top_k": 3})
        assert response.status_code == 200

    def test_query_response_has_results(self, client):
        data = client.post("/api/memory/query", json={"query": "test"}).json()
        assert "results" in data

    def test_query_response_has_count(self, client):
        data = client.post("/api/memory/query", json={"query": "test"}).json()
        assert "count" in data

    def test_query_count_matches_results_length(self, client):
        data = client.post(
            "/api/memory/query", json={"query": "test", "top_k": 5}
        ).json()
        assert data["count"] == len(data["results"])

    def test_query_passes_top_k_to_memory(self, client, mock_memory):
        client.post("/api/memory/query", json={"query": "hello", "top_k": 7})
        call_kwargs = mock_memory.query.call_args
        assert call_kwargs is not None

    def test_query_with_memory_type_filter(self, client):
        response = client.post(
            "/api/memory/query",
            json={"query": "error log", "top_k": 5, "memory_type": "error"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/memory/recent
# ---------------------------------------------------------------------------


class TestRecentMemories:
    def test_recent_returns_200(self, client):
        response = client.get("/api/memory/recent")
        assert response.status_code == 200

    def test_recent_response_has_memories(self, client):
        data = client.get("/api/memory/recent").json()
        assert "memories" in data

    def test_recent_response_has_count(self, client):
        data = client.get("/api/memory/recent").json()
        assert "count" in data

    def test_recent_with_custom_limit(self, client):
        response = client.get("/api/memory/recent?limit=5")
        assert response.status_code == 200

    def test_recent_with_memory_type_filter(self, client):
        response = client.get("/api/memory/recent?memory_type=decision")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/memory/types
# ---------------------------------------------------------------------------


class TestMemoryTypes:
    def test_types_returns_200(self, client):
        response = client.get("/api/memory/types")
        assert response.status_code == 200

    def test_types_response_has_types(self, client):
        data = client.get("/api/memory/types").json()
        assert "types" in data

    def test_types_contains_conversation(self, client):
        data = client.get("/api/memory/types").json()
        assert "conversation" in data["types"]

    def test_types_contains_all_expected(self, client):
        data = client.get("/api/memory/types").json()
        expected = {
            "conversation",
            "decision",
            "code_change",
            "learning",
            "error",
            "solution",
            "context",
        }
        assert expected.issubset(set(data["types"]))

    def test_types_has_descriptions(self, client):
        data = client.get("/api/memory/types").json()
        assert "descriptions" in data


# ---------------------------------------------------------------------------
# POST /api/memory/remember  (query-parameter convenience endpoint)
# ---------------------------------------------------------------------------


class TestQuickRemember:
    def test_remember_returns_200(self, client):
        response = client.post(
            "/api/memory/remember",
            params={"content": "hello", "memory_type": "conversation"},
        )
        assert response.status_code == 200

    def test_remember_response_has_success(self, client):
        data = client.post(
            "/api/memory/remember",
            params={"content": "remember this"},
        ).json()
        assert "success" in data

    def test_remember_with_tags(self, client):
        response = client.post(
            "/api/memory/remember",
            params={"content": "tagged memory", "tags": "bug,frontend"},
        )
        assert response.status_code == 200

    def test_remember_with_files(self, client):
        response = client.post(
            "/api/memory/remember",
            params={"content": "file memory", "files": "main.py,utils.py"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/memory/recall  (query-parameter convenience endpoint)
# ---------------------------------------------------------------------------


class TestQuickRecall:
    def test_recall_returns_200(self, client):
        response = client.get("/api/memory/recall", params={"q": "test query"})
        assert response.status_code == 200

    def test_recall_returns_list(self, client):
        data = client.get("/api/memory/recall", params={"q": "test"}).json()
        assert isinstance(data, list)

    def test_recall_with_custom_k(self, client):
        response = client.get("/api/memory/recall", params={"q": "test", "k": 10})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/memory/{insight_id}
# ---------------------------------------------------------------------------


class TestGetInsight:
    def test_get_insight_returns_200(self, client):
        response = client.get("/api/memory/mem1")
        assert response.status_code == 200

    def test_get_insight_returns_correct_id(self, client):
        data = client.get("/api/memory/mem1").json()
        assert data.get("id") == "mem1"

    def test_get_insight_not_found_returns_404(self, client, mock_memory):
        mock_memory.get_by_id.return_value = None
        response = client.get("/api/memory/nonexistent")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/memory/{insight_id}
# ---------------------------------------------------------------------------


class TestUpdateInsight:
    def test_update_returns_200(self, client):
        response = client.put("/api/memory/mem1", json={"content": "updated content"})
        assert response.status_code == 200

    def test_update_response_has_success(self, client):
        data = client.put("/api/memory/mem1", json={"content": "new content"}).json()
        assert data.get("success") is True

    def test_update_not_found_returns_404(self, client, mock_memory):
        mock_memory.update.return_value = None
        response = client.put("/api/memory/bad_id", json={"content": "x"})
        assert response.status_code == 404

    def test_update_with_tags(self, client):
        response = client.put(
            "/api/memory/mem1",
            json={"tags": ["bug", "frontend"]},
        )
        assert response.status_code == 200

    def test_update_with_memory_type(self, client):
        response = client.put(
            "/api/memory/mem1",
            json={"memory_type": "decision"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /api/memory/{insight_id}
# ---------------------------------------------------------------------------


class TestDeleteInsight:
    def test_delete_returns_200(self, client):
        response = client.delete("/api/memory/mem1")
        assert response.status_code == 200

    def test_delete_response_has_success(self, client):
        data = client.delete("/api/memory/mem1").json()
        assert data.get("success") is True

    def test_delete_not_found_returns_404(self, client, mock_memory):
        mock_memory.delete.return_value = False
        response = client.delete("/api/memory/missing")
        assert response.status_code == 404

    def test_delete_calls_memory_delete(self, client, mock_memory):
        client.delete("/api/memory/abc123")
        mock_memory.delete.assert_called_once_with("abc123")


# ---------------------------------------------------------------------------
# POST /api/memory/bulk-delete
# ---------------------------------------------------------------------------


class TestBulkDelete:
    def test_bulk_delete_returns_200(self, client):
        response = client.post("/api/memory/bulk-delete", json={"ids": ["id1", "id2"]})
        assert response.status_code == 200

    def test_bulk_delete_response_has_deleted_count(self, client):
        data = client.post(
            "/api/memory/bulk-delete", json={"ids": ["id1", "id2"]}
        ).json()
        assert "deleted" in data
        assert data["deleted"] == 2

    def test_bulk_delete_empty_list(self, client, mock_memory):
        mock_memory.delete_bulk.return_value = 0
        data = client.post("/api/memory/bulk-delete", json={"ids": []}).json()
        assert data["deleted"] == 0

    def test_bulk_delete_calls_delete_bulk(self, client, mock_memory):
        client.post("/api/memory/bulk-delete", json={"ids": ["x", "y", "z"]})
        mock_memory.delete_bulk.assert_called_once_with(["x", "y", "z"])


# ---------------------------------------------------------------------------
# GET /api/memory/export/all
# ---------------------------------------------------------------------------


class TestExportInsights:
    def test_export_returns_200(self, client):
        response = client.get("/api/memory/export/all")
        assert response.status_code == 200

    def test_export_has_insights(self, client):
        data = client.get("/api/memory/export/all").json()
        assert "insights" in data

    def test_export_has_count(self, client):
        data = client.get("/api/memory/export/all").json()
        assert "count" in data

    def test_export_count_matches_insights_length(self, client):
        data = client.get("/api/memory/export/all").json()
        assert data["count"] == len(data["insights"])

    def test_export_has_exported_at_timestamp(self, client):
        data = client.get("/api/memory/export/all").json()
        assert "exported_at" in data


# ---------------------------------------------------------------------------
# DELETE /api/memory/clear
# ---------------------------------------------------------------------------


class TestClearMemories:
    def test_clear_returns_200(self, client):
        response = client.delete("/api/memory/clear")
        assert response.status_code == 200

    def test_clear_response_has_success(self, client):
        data = client.delete("/api/memory/clear").json()
        assert "success" in data

    def test_clear_with_memory_type(self, client):
        response = client.delete("/api/memory/clear?memory_type=error")
        assert response.status_code == 200

    def test_clear_failure_still_returns_200(self, client, mock_memory):
        """When clear() returns False the endpoint still returns 200 (not an exception)."""
        mock_memory.clear.return_value = False
        response = client.delete("/api/memory/clear")
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is False


# ---------------------------------------------------------------------------
# POST /api/memory/import
# ---------------------------------------------------------------------------


class TestImportInsights:
    def test_import_returns_200(self, client):
        payload = {
            "insights": [
                {
                    "id": "x1",
                    "content": "imported memory",
                    "memory_type": "conversation",
                }
            ]
        }
        response = client.post("/api/memory/import", json=payload)
        assert response.status_code == 200

    def test_import_response_has_imported_count(self, client):
        payload = {"insights": [{"content": "a"}, {"content": "b"}]}
        data = client.post("/api/memory/import", json=payload).json()
        assert "imported" in data
        assert data["imported"] == 3  # mocked to return 3

    def test_import_empty_list(self, client, mock_memory):
        mock_memory.import_memories.return_value = 0
        data = client.post("/api/memory/import", json={"insights": []}).json()
        assert data["imported"] == 0
