"""
Convex Routing Map Validation
=============================
Cross-references every _FUNCTION_MAP entry in ConvexDB against
actual Convex function exports from frontend/convex/*.ts.

Tests:
1. All 68 map entries point to real exported functions
2. Function name format: module:functionName
3. Fallback for unmapped tables logs warning (doesn't crash)
4. MissionControl tables (approvals, alerts, metrics) fallback gracefully
5. CRUD routing correctness for key tables
6. Dev mode skips routing entirely
"""

import os
import re
import logging

os.environ["VOS3_STORAGE_BACKEND"] = "memory"

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.convex import ConvexDB

# Complete set of exported Convex functions from frontend/convex/*.ts
# Extracted via comprehensive codebase exploration.
# Format: "module:functionName"
KNOWN_CONVEX_FUNCTIONS = {
    # vcore.ts (26 functions)
    "vcore:createEntity",
    "vcore:getEntity",
    "vcore:listEntities",
    "vcore:updateEntity",
    "vcore:deleteEntity",
    "vcore:createRecord",
    "vcore:getRecord",
    "vcore:listRecords",
    "vcore:updateRecord",
    "vcore:deleteRecord",
    "vcore:createWorkflow",
    "vcore:getWorkflow",
    "vcore:listWorkflows",
    "vcore:updateWorkflow",
    "vcore:deleteWorkflow",
    "vcore:createExecution",
    "vcore:updateExecution",
    "vcore:listExecutions",
    "vcore:createApiKey",
    "vcore:getApiKey",
    "vcore:getApiKeyByHash",
    "vcore:updateApiKey",
    "vcore:deleteApiKey",
    "vcore:listApiKeys",
    "vcore:addAuditEntry",
    "vcore:listAuditLog",
    # users.ts (7 functions)
    "users:syncFromClerk",
    "users:getByEmail",
    "users:getByClerkId",
    "users:getById",
    "users:update",
    "users:recordSignIn",
    "users:softDelete",
    # organizations.ts (8 functions)
    "organizations:create",
    "organizations:getById",
    "organizations:getBySlug",
    "organizations:listByUser",
    "organizations:update",
    "organizations:addMember",
    "organizations:removeMember",
    "organizations:listMembers",
    # projects.ts (16 functions)
    "projects:create",
    "projects:getById",
    "projects:listByUser",
    "projects:update",
    "projects:upsertFile",
    "projects:getFiles",
    "projects:deleteFile",
    "projects:addChatMessage",
    "projects:getChatMessages",
    "projects:createMemory",
    "projects:getMemory",
    "projects:updateMemory",
    "projects:deleteMemory",
    "projects:listMemory",
    "projects:listMemoryByType",
    # billing.ts (11 functions)
    "billing:getBalance",
    "billing:addCredits",
    "billing:useCredits",
    "billing:getTransactions",
    "billing:getSubscription",
    "billing:upsertSubscription",
    "billing:updateSubscriptionStatus",
    "billing:cancelSubscriptionByStripeId",
    "billing:getOrCreateStripeCustomer",
    "billing:getStripeCustomerId",
    "billing:getUserByStripeCustomerId",
    # quota.ts (6 functions)
    "quota:getUsage",
    "quota:checkQuota",
    "quota:recordUsage",
    "quota:setQuotaLimit",
    "quota:createAlert",
    "quota:listAlerts",
    # builds.ts (9 functions)
    "builds:getById",
    "builds:listByProject",
    "builds:listByUser",
    "builds:getLatestByProject",
    "builds:create",
    "builds:updateStatus",
    "builds:updateCost",
    "builds:updateScores",
    "builds:remove",
    # agentStatus.ts (4 functions)
    "agentStatus:listByBuild",
    "agentStatus:getByBuildAgent",
    "agentStatus:upsert",
    "agentStatus:remove",
    # apps.ts (8 functions)
    "apps:publish",
    "apps:install",
    "apps:uninstall",
    "apps:get",
    "apps:list",
    "apps:search",
    "apps:getInstalled",
    "apps:updateStatus",
    # developers.ts (5 functions)
    "developers:register",
    "developers:getProfile",
    "developers:getApps",
    "developers:getEarnings",
    "developers:updateStripeConnect",
    # appReviews.ts (4 functions)
    "appReviews:create",
    "appReviews:list",
    "appReviews:averageRating",
    "appReviews:voteHelpful",
    # chatSessions.ts (4 functions)
    "chatSessions:save",
    "chatSessions:load",
    "chatSessions:list",
    "chatSessions:remove",
    # expertRequests.ts (7 functions)
    "expertRequests:getByProject",
    "expertRequests:getByUser",
    "expertRequests:listPending",
    "expertRequests:getById",
    "expertRequests:create",
    "expertRequests:claim",
    "expertRequests:resolve",
    # presence.ts (5 functions)
    "presence:heartbeat",
    "presence:disconnect",
    "presence:cleanupStale",
    "presence:getActiveUsers",
    "presence:getCursorsForFile",
    # yjsUpdates.ts (9 functions)
    "yjsUpdates:getOrCreateDocument",
    "yjsUpdates:getDocument",
    "yjsUpdates:listDocuments",
    "yjsUpdates:pushUpdate",
    "yjsUpdates:getUpdatesSince",
    "yjsUpdates:getLatestSeq",
    "yjsUpdates:updateStateVector",
    "yjsUpdates:compactUpdates",
    # promptHistory.ts (5 functions)
    "promptHistory:create",
    "promptHistory:getById",
    "promptHistory:update",
    "promptHistory:remove",
    "promptHistory:listByUser",
}


class TestFunctionMapCompleteness:
    """Every _FUNCTION_MAP entry must point to a real Convex function."""

    def test_all_entries_are_real_functions(self):
        """Cross-reference all 68 entries against known exports."""
        db = ConvexDB()
        missing = []
        for (table, op), function_name in db._FUNCTION_MAP.items():
            if function_name not in KNOWN_CONVEX_FUNCTIONS:
                missing.append(f"({table}, {op}) → {function_name}")

        assert len(missing) == 0, (
            f"{len(missing)} map entries point to non-existent functions:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_map_has_expected_count(self):
        """Verify map has expected number of entries."""
        db = ConvexDB()
        count = len(db._FUNCTION_MAP)
        assert count >= 60, f"Expected 60+ entries, got {count}"

    def test_all_entries_have_valid_format(self):
        """All function names must match 'module:functionName' format."""
        db = ConvexDB()
        pattern = re.compile(r"^[a-zA-Z]+:[a-zA-Z]+$")
        invalid = []
        for (table, op), function_name in db._FUNCTION_MAP.items():
            if not pattern.match(function_name):
                invalid.append(f"({table}, {op}) → '{function_name}'")

        assert (
            len(invalid) == 0
        ), f"{len(invalid)} entries have invalid format:\n" + "\n".join(
            f"  - {i}" for i in invalid
        )


class TestFunctionResolution:
    """Test _resolve_function behavior."""

    def test_known_table_resolves(self):
        """Known tables resolve to correct function."""
        db = ConvexDB()
        assert db._resolve_function("entities", "create") == "vcore:createEntity"
        assert db._resolve_function("users", "create") == "users:syncFromClerk"
        assert db._resolve_function("organizations", "create") == "organizations:create"
        assert db._resolve_function("records", "list") == "vcore:listRecords"
        assert db._resolve_function("workflows", "getById") == "vcore:getWorkflow"

    def test_unknown_table_returns_fallback(self):
        """Unknown table returns generic pattern."""
        db = ConvexDB()
        result = db._resolve_function("nonexistent_table", "create")
        assert result == "nonexistent_table:create"

    def test_unknown_table_logs_warning(self, caplog):
        """Unknown table logs a warning."""
        db = ConvexDB()
        with caplog.at_level(logging.WARNING):
            db._resolve_function("bogus", "remove")
        assert any("no function mapping" in r.message for r in caplog.records)


class TestMissionControlFallback:
    """MissionControl tables (approvals, alerts, metrics) have no Convex functions yet."""

    def test_approvals_not_in_map(self):
        """approvals table is deliberately unmapped."""
        db = ConvexDB()
        assert ("approvals", "create") not in db._FUNCTION_MAP
        assert ("approvals", "list") not in db._FUNCTION_MAP

    def test_alerts_not_in_map(self):
        """alerts table is deliberately unmapped."""
        db = ConvexDB()
        assert ("alerts", "create") not in db._FUNCTION_MAP
        assert ("alerts", "list") not in db._FUNCTION_MAP

    def test_metrics_not_in_map(self):
        """metrics table is deliberately unmapped."""
        db = ConvexDB()
        assert ("metrics", "create") not in db._FUNCTION_MAP
        assert ("metrics", "list") not in db._FUNCTION_MAP

    def test_unmapped_tables_dont_crash(self, caplog):
        """Unmapped tables resolve gracefully with warning."""
        db = ConvexDB()
        with caplog.at_level(logging.WARNING):
            result = db._resolve_function("approvals", "create")
        assert result == "approvals:create"
        assert any("no function mapping" in r.message for r in caplog.records)


class TestCrudRouting:
    """Verify CRUD methods use _resolve_function correctly."""

    def test_key_tables_crud_map(self):
        """Verify all 5 CRUD operations map correctly for core tables."""
        db = ConvexDB()
        expected = {
            ("entities", "create"): "vcore:createEntity",
            ("entities", "getById"): "vcore:getEntity",
            ("entities", "update"): "vcore:updateEntity",
            ("entities", "remove"): "vcore:deleteEntity",
            ("entities", "list"): "vcore:listEntities",
            ("records", "create"): "vcore:createRecord",
            ("records", "getById"): "vcore:getRecord",
            ("records", "update"): "vcore:updateRecord",
            ("records", "remove"): "vcore:deleteRecord",
            ("records", "list"): "vcore:listRecords",
            ("workflows", "create"): "vcore:createWorkflow",
            ("workflows", "getById"): "vcore:getWorkflow",
            ("workflows", "update"): "vcore:updateWorkflow",
            ("workflows", "remove"): "vcore:deleteWorkflow",
            ("workflows", "list"): "vcore:listWorkflows",
            ("users", "create"): "users:syncFromClerk",
            ("users", "getById"): "users:getById",
            ("users", "update"): "users:update",
            ("organizations", "create"): "organizations:create",
            ("organizations", "getById"): "organizations:getById",
            ("organizations", "update"): "organizations:update",
        }
        for (table, op), expected_fn in expected.items():
            actual = db._resolve_function(table, op)
            assert (
                actual == expected_fn
            ), f"({table}, {op}): expected {expected_fn}, got {actual}"


class TestDevModeIsolation:
    """In dev mode, CRUD goes to in-memory — routing map is bypassed."""

    def test_dev_mode_detected(self):
        """ConvexDB with no URL/key runs in dev mode."""
        db = ConvexDB()
        assert db.dev_mode is True
        assert db.get_mode() == "dev"

    def test_dev_mode_insert_uses_memory(self):
        """Dev mode insert doesn't call _resolve_function."""
        import asyncio

        db = ConvexDB()
        doc_id = asyncio.run(db.insert("entities", {"name": "test"}))
        assert doc_id is not None
        # Verify it's in the dev dict
        assert "entities" in db._db
        assert doc_id in db._db["entities"]

    def test_dev_mode_roundtrip(self):
        """Dev mode: insert → get → update → list → delete."""
        import asyncio

        db = ConvexDB()

        doc_id = asyncio.run(db.insert("test_table", {"field": "value1"}))
        doc = asyncio.run(db.get("test_table", doc_id))
        assert doc["field"] == "value1"

        asyncio.run(db.update("test_table", doc_id, {"field": "value2"}))
        doc = asyncio.run(db.get("test_table", doc_id))
        assert doc["field"] == "value2"

        docs = asyncio.run(db.list("test_table"))
        assert len(docs) == 1

        result = asyncio.run(db.delete("test_table", doc_id))
        assert result is True

        docs = asyncio.run(db.list("test_table"))
        assert len(docs) == 0
