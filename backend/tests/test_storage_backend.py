"""
Tests for the Convex storage backend (db/convex.py).

These tests cover:
- Dev mode activation when URL/key are missing
- Production mode when URL + key present
- Dev mode CRUD operations on the in-memory _dev_storage dict
- Singleton behavior of get_convex_client()
"""

import asyncio

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.convex import ConvexDB, get_convex_client, get_convex_db


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------


class TestDevModeDetection:
    def test_empty_url_triggers_dev_mode(self):
        db = ConvexDB(url="", deploy_key="")
        assert db.dev_mode is True

    def test_missing_url_triggers_dev_mode(self):
        db = ConvexDB(url=None, deploy_key="some-key")
        assert db.dev_mode is True

    def test_missing_key_triggers_dev_mode(self):
        db = ConvexDB(url="https://example.convex.cloud", deploy_key="")
        assert db.dev_mode is True

    def test_both_present_disables_dev_mode(self):
        db = ConvexDB(url="https://example.convex.cloud", deploy_key="prod-key")
        assert db.dev_mode is False

    def test_get_mode_returns_dev_string(self):
        db = ConvexDB(url="", deploy_key="")
        assert db.get_mode() == "dev"

    def test_get_mode_returns_production_string(self):
        db = ConvexDB(url="https://example.convex.cloud", deploy_key="key")
        assert db.get_mode() == "production"

    def test_is_connected_true_in_dev_mode(self):
        db = ConvexDB(url="", deploy_key="")
        assert db.is_connected() is True

    def test_is_connected_true_in_production_mode(self):
        db = ConvexDB(url="https://example.convex.cloud", deploy_key="key")
        assert db.is_connected() is True


# ---------------------------------------------------------------------------
# Dev mode CRUD
# ---------------------------------------------------------------------------


class TestDevModeCRUD:
    def setup_method(self):
        """Fresh DB instance for each test."""
        self.db = ConvexDB(url="", deploy_key="")

    def test_insert_returns_string_id(self):
        doc_id = run(self.db.insert("users", {"name": "Alice", "role": "admin"}))
        assert isinstance(doc_id, str)
        assert len(doc_id) > 0

    def test_get_returns_inserted_document(self):
        doc_id = run(self.db.insert("users", {"name": "Bob", "age": 30}))
        doc = run(self.db.get("users", doc_id))
        assert doc is not None
        assert doc["name"] == "Bob"
        assert doc["age"] == 30

    def test_get_nonexistent_returns_none(self):
        doc = run(self.db.get("users", "nonexistent-id"))
        assert doc is None

    def test_update_changes_fields(self):
        doc_id = run(self.db.insert("users", {"name": "Charlie", "score": 10}))
        updated = run(self.db.update("users", doc_id, {"score": 99}))
        assert updated["score"] == 99
        assert updated["name"] == "Charlie"

    def test_update_reflects_in_get(self):
        doc_id = run(self.db.insert("items", {"value": "old"}))
        run(self.db.update("items", doc_id, {"value": "new"}))
        doc = run(self.db.get("items", doc_id))
        assert doc["value"] == "new"

    def test_delete_removes_document(self):
        doc_id = run(self.db.insert("posts", {"title": "Hello"}))
        result = run(self.db.delete("posts", doc_id))
        assert result is True
        doc = run(self.db.get("posts", doc_id))
        assert doc is None

    def test_list_empty_table_returns_empty_list(self):
        result = run(self.db.list("empty_table"))
        assert result == []

    def test_list_returns_all_documents(self):
        run(self.db.insert("products", {"name": "X"}))
        run(self.db.insert("products", {"name": "Y"}))
        run(self.db.insert("products", {"name": "Z"}))
        products = run(self.db.list("products"))
        assert len(products) == 3

    def test_find_filters_by_field(self):
        run(self.db.insert("users", {"name": "Alice", "role": "admin"}))
        run(self.db.insert("users", {"name": "Bob", "role": "user"}))
        run(self.db.insert("users", {"name": "Carol", "role": "admin"}))
        admins = run(self.db.find("users", {"role": "admin"}))
        assert len(admins) == 2
        names = {d["name"] for d in admins}
        assert names == {"Alice", "Carol"}

    def test_find_no_matches_returns_empty_list(self):
        run(self.db.insert("users", {"name": "Alice", "role": "admin"}))
        result = run(self.db.find("users", {"role": "superuser"}))
        assert result == []

    def test_inserted_document_has_timestamps(self):
        doc_id = run(self.db.insert("events", {"type": "click"}))
        doc = run(self.db.get("events", doc_id))
        assert "createdAt" in doc
        assert "updatedAt" in doc
        assert "_creationTime" in doc

    def test_inserted_document_has_id_field(self):
        doc_id = run(self.db.insert("things", {"x": 1}))
        doc = run(self.db.get("things", doc_id))
        assert doc["_id"] == doc_id

    def test_multiple_tables_are_independent(self):
        run(self.db.insert("table_a", {"val": 1}))
        run(self.db.insert("table_b", {"val": 2}))
        a = run(self.db.list("table_a"))
        b = run(self.db.list("table_b"))
        assert len(a) == 1
        assert len(b) == 1

    def test_dev_action_returns_dev_mode_status(self):
        result = run(self.db.action("emails:send", {"to": "test@example.com"}))
        assert result["status"] == "dev_mode"
        assert result["function"] == "emails:send"


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_convex_client_returns_same_instance(self):
        import db.convex as convex_module

        # Reset singleton
        convex_module._convex_db = None
        db1 = get_convex_client()
        db2 = get_convex_client()
        assert db1 is db2
        convex_module._convex_db = None  # cleanup

    def test_get_convex_db_alias_works(self):
        import db.convex as convex_module

        convex_module._convex_db = None
        db1 = get_convex_client()
        db2 = get_convex_db()
        assert db1 is db2
        convex_module._convex_db = None  # cleanup
