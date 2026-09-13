"""
Phase 1.0 Audit — Verification against TIS Acceptance Criteria
===============================================================

Maps tests directly to AC numbers from docs/TECHNICAL_IMPLEMENTATION_SPEC.md §1.8.
Run:  cd backend && python -m pytest tests/phase_1_audit.py -v

Subsystems covered:
  AC-1  Authentication & Authorization  (Clerk middleware)
  AC-2  Convex Client Hardening         (ConvexDB mode switching, WriteBuffer)
  AC-6  Deployment & Infrastructure      (railway.toml, vercel.json)
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure imports resolve from backend/
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))


# ===========================================================================
#  HELPERS
# ===========================================================================


def run(coro):
    """Run an async coroutine synchronously (same helper used across test suite)."""
    return asyncio.run(coro)


def make_request(path: str = "/", headers: dict | None = None):
    """Create a minimal Starlette Request for testing middleware."""
    from starlette.requests import Request

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "server": ("localhost", 8000),
    }
    return Request(scope)


# ===========================================================================
#  AC-2: CONVEX CLIENT HARDENING
# ===========================================================================


class TestConvexDBModeSwitch:
    """AC-2.1 / AC-2.2 / AC-2.3 — ConvexDB routes to Convex in prod, in-memory in dev."""

    # ---- AC-2.3: Dev mode fallback preserved ----

    def test_ac_2_3_no_credentials_is_dev_mode(self):
        """ConvexDB() with no URL/key must enter dev mode."""
        from db.convex import ConvexDB

        db = ConvexDB(url="", deploy_key="")
        assert db.dev_mode is True
        assert db.get_mode() == "dev"

    def test_ac_2_3_missing_deploy_key_is_dev_mode(self):
        """URL alone is insufficient — both url AND deploy_key required for prod."""
        from db.convex import ConvexDB

        db = ConvexDB(url="https://example.convex.cloud", deploy_key="")
        assert db.dev_mode is True

    def test_ac_2_3_missing_url_is_dev_mode(self):
        """deploy_key alone is insufficient."""
        from db.convex import ConvexDB

        db = ConvexDB(url="", deploy_key="prod:secret123")
        assert db.dev_mode is True

    def test_ac_2_3_dev_mode_insert_uses_in_memory(self):
        """In dev mode, insert() writes to self._db dict, NOT Convex."""
        from db.convex import ConvexDB

        db = ConvexDB(url="", deploy_key="")
        doc_id = run(db.insert("projects", {"name": "Test"}))

        assert doc_id is not None
        assert len(doc_id) > 0
        # Data must be in the in-memory dict
        assert "projects" in db._db
        assert doc_id in db._db["projects"]
        assert db._db["projects"][doc_id]["name"] == "Test"

    def test_ac_2_3_dev_mode_get_reads_from_memory(self):
        """In dev mode, get() returns from self._db."""
        from db.convex import ConvexDB

        db = ConvexDB()
        doc_id = run(db.insert("things", {"color": "blue"}))
        doc = run(db.get("things", doc_id))

        assert doc is not None
        assert doc["color"] == "blue"
        assert doc["_id"] == doc_id

    def test_ac_2_3_dev_mode_list_returns_all(self):
        """In dev mode, list() returns all docs in the in-memory table."""
        from db.convex import ConvexDB

        db = ConvexDB()
        run(db.insert("items", {"x": 1}))
        run(db.insert("items", {"x": 2}))

        docs = run(db.list("items"))
        assert len(docs) == 2

    def test_ac_2_3_dev_mode_update_modifies_in_memory(self):
        """In dev mode, update() patches the in-memory doc."""
        from db.convex import ConvexDB

        db = ConvexDB()
        doc_id = run(db.insert("t", {"a": 1, "b": 2}))
        updated = run(db.update("t", doc_id, {"b": 99}))

        assert updated["b"] == 99
        assert updated["a"] == 1  # unchanged

    def test_ac_2_3_dev_mode_delete_removes_from_memory(self):
        """In dev mode, delete() removes from self._db."""
        from db.convex import ConvexDB

        db = ConvexDB()
        doc_id = run(db.insert("t", {"a": 1}))
        assert run(db.delete("t", doc_id)) is True
        assert run(db.get("t", doc_id)) is None
        assert run(db.delete("t", doc_id)) is False  # already gone

    # ---- AC-2.1 / AC-2.2: Production mode routes through Convex HTTP ----

    def test_ac_2_1_both_credentials_enters_prod_mode(self):
        """ConvexDB with BOTH url and deploy_key must be in production mode."""
        from db.convex import ConvexDB

        db = ConvexDB(
            url="https://my-deploy.convex.cloud",
            deploy_key="prod:deploy_key_abc",
        )
        assert db.dev_mode is False
        assert db.get_mode() == "production"

    def test_ac_2_1_prod_insert_calls_mutation(self):
        """In prod mode, insert('projects', data) calls mutation('projects:create', data)."""
        from db.convex import ConvexDB

        db = ConvexDB(
            url="https://x.convex.cloud",
            deploy_key="prod:key",
        )
        db.mutation = AsyncMock(return_value="id_from_convex")

        result = run(db.insert("projects", {"name": "Test"}))

        assert result == "id_from_convex"
        db.mutation.assert_awaited_once_with("projects:create", {"name": "Test"})
        # In-memory dict must remain empty
        assert db._db == {}

    def test_ac_2_2_prod_get_calls_query(self):
        """In prod mode, get('projects', id) calls query('projects:getById', {id})."""
        from db.convex import ConvexDB

        db = ConvexDB(url="https://x.convex.cloud", deploy_key="prod:key")
        db.query = AsyncMock(return_value={"_id": "abc", "name": "Test"})

        doc = run(db.get("projects", "abc"))

        assert doc["name"] == "Test"
        db.query.assert_awaited_once_with("projects:getById", {"id": "abc"})

    def test_prod_update_calls_mutation(self):
        """In prod mode, update() calls mutation('table:update', {id, ...data})."""
        from db.convex import ConvexDB

        db = ConvexDB(url="https://x.convex.cloud", deploy_key="prod:key")
        db.mutation = AsyncMock(return_value={"_id": "abc", "name": "Updated"})

        run(db.update("projects", "abc", {"name": "Updated"}))

        db.mutation.assert_awaited_once_with(
            "projects:update", {"id": "abc", "name": "Updated"}
        )

    def test_prod_delete_calls_mutation(self):
        """In prod mode, delete() calls mutation('table:remove', {id})."""
        from db.convex import ConvexDB

        db = ConvexDB(url="https://x.convex.cloud", deploy_key="prod:key")
        db.mutation = AsyncMock(return_value=None)

        result = run(db.delete("projects", "abc"))

        assert result is True
        db.mutation.assert_awaited_once_with("projects:remove", {"id": "abc"})

    def test_prod_list_calls_query(self):
        """In prod mode, list() calls query('table:list', {})."""
        from db.convex import ConvexDB

        db = ConvexDB(url="https://x.convex.cloud", deploy_key="prod:key")
        db.query = AsyncMock(return_value=[{"_id": "a"}, {"_id": "b"}])

        docs = run(db.list("projects"))

        assert len(docs) == 2
        db.query.assert_awaited_once_with("projects:list", {})


# ===========================================================================
#  AC-1: AUTHENTICATION & AUTHORIZATION
# ===========================================================================


class TestClerkMiddlewareDevMode:
    """AC-1.x — verify_auth behavior in DEV MODE (no CLERK_SECRET_KEY)."""

    def test_ac_1_dev_no_token_returns_mock_user(self):
        """Dev mode: missing token returns demo user (not 401)."""
        from middleware.auth import verify_auth

        request = make_request("/api/projects")
        with patch("middleware.auth.DEV_MODE", True):
            user = run(verify_auth(request))

        assert user is not None
        assert user.id == "dev_seed_user"
        assert user.email == "demo@example.com"
        assert "read" in user.permissions
        assert user.metadata.get("dev_mode") is True

    def test_dev_mode_invalid_token_returns_mock_user(self):
        """Dev mode: garbage token falls back to demo user instead of 401."""
        from middleware.auth import verify_auth

        request = make_request(
            "/api/projects",
            headers={"Authorization": "Bearer garbage.token.here"},
        )
        with patch("middleware.auth.DEV_MODE", True):
            user = run(verify_auth(request))

        assert user.id == "dev_seed_user"


class TestClerkMiddlewareProdMode:
    """AC-1.1 / AC-1.2 / AC-1.3 — verify_auth behavior in PRODUCTION mode."""

    def test_ac_1_1_missing_token_raises_401(self):
        """AC-1.1: Request without Authorization header → 401."""
        from fastapi import HTTPException
        from middleware.auth import verify_auth

        request = make_request("/api/projects")
        with patch("middleware.auth.DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))

        assert exc_info.value.status_code == 401
        assert "Missing authorization token" in exc_info.value.detail

    def test_ac_1_1_empty_bearer_raises_401(self):
        """AC-1.1: 'Authorization: Bearer ' with empty token → 401."""
        from fastapi import HTTPException
        from middleware.auth import verify_auth

        request = make_request("/api/projects", headers={"Authorization": "Bearer "})
        with patch("middleware.auth.DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))

        assert exc_info.value.status_code == 401

    def test_ac_1_1_invalid_token_raises_401(self):
        """AC-1.1: Completely invalid JWT string → 401."""
        from fastapi import HTTPException
        from middleware.auth import verify_auth

        request = make_request(
            "/api/projects",
            headers={"Authorization": "Bearer not.a.jwt"},
        )
        with patch("middleware.auth.DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))

        assert exc_info.value.status_code == 401

    def test_ac_1_3_expired_jwt_raises_401_with_expired_detail(self):
        """AC-1.3: Expired JWT raises 401 with 'Token expired' detail."""
        import jwt as pyjwt
        from fastapi import HTTPException
        from middleware.auth import verify_auth

        # Build a HS256 JWT that expired 1 hour ago
        secret = "test-secret-key"
        expired_token = pyjwt.encode(
            {
                "sub": "user_expired",
                "exp": int(time.time()) - 3600,
                "iat": int(time.time()) - 7200,
            },
            secret,
            algorithm="HS256",
        )

        request = make_request(
            "/api/test",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        with (
            patch("middleware.auth.DEV_MODE", False),
            patch("middleware.auth.CLERK_SECRET_KEY", secret),
            patch("middleware.auth.CLERK_ISSUER_URL", ""),
            patch("middleware.auth.HAS_CLERK", False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))

        assert exc_info.value.status_code == 401
        assert "Token expired" in exc_info.value.detail

    def test_ac_1_2_valid_hs256_jwt_returns_user(self):
        """AC-1.2: Valid HS256 JWT with 'sub' claim returns AuthenticatedUser."""
        import jwt as pyjwt
        from middleware.auth import verify_auth

        secret = "test-secret-key"
        valid_token = pyjwt.encode(
            {
                "sub": "user_clerk_123",
                "email": "test@example.com",
                "org_id": "org_abc",
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
            },
            secret,
            algorithm="HS256",
        )

        request = make_request(
            "/api/projects",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        with (
            patch("middleware.auth.DEV_MODE", False),
            patch("middleware.auth.CLERK_SECRET_KEY", secret),
            patch("middleware.auth.CLERK_ISSUER_URL", ""),
            patch("middleware.auth.HAS_CLERK", False),
            patch(
                "middleware.auth._resolve_convex_user_id",
                new=AsyncMock(return_value="convex_id_xyz"),
            ),
        ):
            user = run(verify_auth(request))

        assert user.id == "user_clerk_123"
        assert user.email == "test@example.com"
        assert user.org_id == "org_abc"

    def test_ac_1_5_convex_user_id_resolved(self):
        """AC-1.5: After JWT validation, convex_user_id is resolved via cache/query."""
        import jwt as pyjwt
        from middleware.auth import verify_auth

        secret = "test-secret-key"
        token = pyjwt.encode(
            {
                "sub": "user_abc",
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
            },
            secret,
            algorithm="HS256",
        )

        request = make_request(
            "/api/test",
            headers={"Authorization": f"Bearer {token}"},
        )
        with (
            patch("middleware.auth.DEV_MODE", False),
            patch("middleware.auth.CLERK_SECRET_KEY", secret),
            patch("middleware.auth.CLERK_ISSUER_URL", ""),
            patch("middleware.auth.HAS_CLERK", False),
            patch(
                "middleware.auth._resolve_convex_user_id",
                new=AsyncMock(return_value="convex_user_777"),
            ),
        ):
            user = run(verify_auth(request))

        assert user.convex_user_id == "convex_user_777"

    def test_jwt_without_sub_claim_raises_401(self):
        """JWT that decodes but has no 'sub' or 'user_id' → 401."""
        import jwt as pyjwt
        from fastapi import HTTPException
        from middleware.auth import verify_auth

        secret = "test-secret-key"
        token = pyjwt.encode(
            {
                "email": "nosub@example.com",
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
            },
            secret,
            algorithm="HS256",
        )

        request = make_request(
            "/api/test",
            headers={"Authorization": f"Bearer {token}"},
        )
        with (
            patch("middleware.auth.DEV_MODE", False),
            patch("middleware.auth.CLERK_SECRET_KEY", secret),
            patch("middleware.auth.CLERK_ISSUER_URL", ""),
            patch("middleware.auth.HAS_CLERK", False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))

        assert exc_info.value.status_code == 401
        assert "Missing or invalid" in exc_info.value.detail


# ===========================================================================
#  AC-2.4 / AC-2.5 / AC-2.6: CONVEX WRITE BUFFER
# ===========================================================================


class TestConvexWriteBuffer:
    """AC-2.4 / AC-2.5 / AC-2.6 — ConvexWriteBuffer coalescing and flushing."""

    def _make_buffer(self, flush_interval_ms=50000, max_batch_size=100):
        """Create a buffer with a mock client. High flush interval prevents auto-flush."""
        from db.convex import ConvexDB, ConvexWriteBuffer

        client = ConvexDB()  # dev mode, mutations go to memory
        client.mutation = AsyncMock(return_value="mock_id")
        buffer = ConvexWriteBuffer(
            client,
            flush_interval_ms=flush_interval_ms,
            max_batch_size=max_batch_size,
        )
        return buffer, client

    def test_ac_2_5_ten_updates_same_key_coalesced_to_one(self):
        """
        AC-2.5: Enqueue 10 rapid updates for the same (buildId, agentName).
        After flush_now(), only 1 mutation must be sent — the last-write-wins.
        """
        buffer, client = self._make_buffer()

        async def _test():
            for i in range(10):
                await buffer.enqueue(
                    "agentStatus:upsert",
                    {
                        "buildId": "build_1",
                        "agentName": "architect",
                        "status": "running",
                        "progressPercent": (i + 1) * 10,
                        "progressMessage": f"Step {i + 1}",
                    },
                )
            await buffer.flush_now()

        run(_test())

        # Only 1 mutation should have been fired
        assert client.mutation.await_count == 1
        # The winning mutation must carry the LAST state
        sent_args = client.mutation.await_args_list[0]
        assert sent_args[0][0] == "agentStatus:upsert"
        assert sent_args[0][1]["progressPercent"] == 100
        assert sent_args[0][1]["progressMessage"] == "Step 10"

    def test_ac_2_5_different_agents_not_coalesced(self):
        """
        Different (buildId, agentName) keys must NOT coalesce — each
        agent's state is independent.
        """
        buffer, client = self._make_buffer()

        async def _test():
            agents = ["architect", "frontend", "backend", "tester", "reviewer"]
            for name in agents:
                await buffer.enqueue(
                    "agentStatus:upsert",
                    {
                        "buildId": "build_1",
                        "agentName": name,
                        "status": "running",
                        "progressPercent": 50,
                    },
                )
            await buffer.flush_now()

        run(_test())

        # 5 different agents → 5 separate mutations
        assert client.mutation.await_count == 5

    def test_ac_2_5_different_builds_not_coalesced(self):
        """Updates for different buildIds must NOT coalesce."""
        buffer, client = self._make_buffer()

        async def _test():
            for build_num in range(3):
                await buffer.enqueue(
                    "agentStatus:upsert",
                    {
                        "buildId": f"build_{build_num}",
                        "agentName": "architect",
                        "status": "running",
                    },
                )
            await buffer.flush_now()

        run(_test())

        assert client.mutation.await_count == 3

    def test_ac_2_6_flush_now_delivers_final_state(self):
        """
        AC-2.6: flush_now() must deliver ALL pending writes.
        Queue must be empty afterward.
        """
        buffer, client = self._make_buffer()

        async def _test():
            # Enqueue 3 different agents
            for name in ["architect", "frontend", "backend"]:
                await buffer.enqueue(
                    "agentStatus:upsert",
                    {
                        "buildId": "build_final",
                        "agentName": name,
                        "status": "completed",
                        "progressPercent": 100,
                    },
                )

            await buffer.flush_now()

            # Verify queue is empty after flush
            assert len(buffer._queue) == 0

            # Enqueue 1 more — should not duplicate
            await buffer.enqueue(
                "agentStatus:upsert",
                {
                    "buildId": "build_final",
                    "agentName": "reviewer",
                    "status": "completed",
                },
            )
            await buffer.flush_now()

        run(_test())

        # 3 from first flush + 1 from second flush = 4 total
        assert client.mutation.await_count == 4

    def test_ac_2_4_mixed_rapid_updates_produce_correct_count(self):
        """
        AC-2.4: Simulate 5 builds × 9 agents → 45 unique keys.
        Enqueue 3 updates each (135 enqueues).
        After flush_now(), exactly 45 mutations must fire (one per unique key).
        """
        buffer, client = self._make_buffer()

        async def _test():
            agents = [
                "architect",
                "expander",
                "dispatcher",
                "frontend",
                "backend",
                "aggregator",
                "tester",
                "reviewer",
                "finalize",
            ]
            for build_num in range(5):
                for agent in agents:
                    # 3 rapid updates per agent per build
                    for attempt in range(3):
                        await buffer.enqueue(
                            "agentStatus:upsert",
                            {
                                "buildId": f"build_{build_num}",
                                "agentName": agent,
                                "status": "running",
                                "progressPercent": (attempt + 1) * 33,
                            },
                        )
            await buffer.flush_now()

        run(_test())

        # 5 builds × 9 agents = 45 unique keys, coalesced from 135 enqueues
        assert client.mutation.await_count == 45

        # Verify every flushed mutation carries the LAST state (progressPercent=99)
        for call_obj in client.mutation.await_args_list:
            args_dict = call_obj[0][1]
            assert args_dict["progressPercent"] == 99  # 3rd attempt: 3*33=99

    def test_auto_flush_on_max_batch_size(self):
        """Buffer auto-flushes when queue reaches max_batch_size."""
        buffer, client = self._make_buffer(
            flush_interval_ms=999999,  # effectively disabled
            max_batch_size=3,
        )

        async def _test():
            # Enqueue 3 DIFFERENT keys to hit batch limit
            await buffer.enqueue("fn", {"buildId": "b1", "agentName": "a1"})
            await buffer.enqueue("fn", {"buildId": "b1", "agentName": "a2"})
            await buffer.enqueue("fn", {"buildId": "b1", "agentName": "a3"})
            # Give the event loop a tick for the flush to execute
            await asyncio.sleep(0.01)

        run(_test())

        # Auto-flush should have fired when queue hit 3
        assert client.mutation.await_count == 3

    def test_empty_flush_is_noop(self):
        """flush_now() on empty queue must not call any mutations."""
        buffer, client = self._make_buffer()

        run(buffer.flush_now())

        assert client.mutation.await_count == 0

    def test_coalescing_preserves_function_name(self):
        """Coalescing key includes function_name — different functions don't merge."""
        buffer, client = self._make_buffer()

        async def _test():
            await buffer.enqueue(
                "agentStatus:upsert",
                {"buildId": "b1", "agentName": "arch", "status": "running"},
            )
            await buffer.enqueue(
                "builds:updateCost",
                {"buildId": "b1", "agentName": "arch", "totalCost": 0.05},
            )
            await buffer.flush_now()

        run(_test())

        # Two different function names → 2 mutations
        assert client.mutation.await_count == 2


# ===========================================================================
#  AC-6: DEPLOYMENT & INFRASTRUCTURE (static file checks)
# ===========================================================================


class TestDeploymentConfigs:
    """AC-6.3 — railway.toml and vercel.json correctness."""

    def test_ac_6_3_railway_start_command_is_main_app(self):
        """AC-6.3: railway.toml startCommand must reference 'main:app'."""
        railway_path = Path(__file__).parent.parent / "railway.toml"
        assert railway_path.exists(), f"railway.toml not found at {railway_path}"

        content = railway_path.read_text()
        assert "main:app" in content, (
            f"railway.toml startCommand does not reference 'main:app'. "
            f"Content: {content[:200]}"
        )
        assert (
            "server_enhanced" not in content
        ), "railway.toml still references the old 'server_enhanced' module"

    def test_vercel_json_exists_and_valid(self):
        """vercel.json must exist at project root with correct framework."""
        # Check project root (one level above backend/)
        vercel_path = Path(__file__).parent.parent.parent / "vercel.json"
        assert vercel_path.exists(), f"vercel.json not found at {vercel_path}"

        data = json.loads(vercel_path.read_text())
        assert data.get("framework") == "nextjs"
        assert "frontend" in data.get("buildCommand", "")

    def test_railway_has_health_check(self):
        """railway.toml should define a healthcheckPath."""
        railway_path = Path(__file__).parent.parent / "railway.toml"
        content = railway_path.read_text()
        assert (
            "healthcheck" in content.lower()
        ), "railway.toml should define a healthcheck path"


# ===========================================================================
#  AC SUMMARY — Schema / Structure Validation
# ===========================================================================


class TestSchemaExtension:
    """AC-3.1 / AC-3.2 / AC-3.3 — Verify schema files define required tables."""

    def _read_schema(self, path: str) -> str:
        schema_file = Path(__file__).parent.parent.parent / path
        assert schema_file.exists(), f"Schema file not found: {schema_file}"
        return schema_file.read_text()

    def test_ac_3_1_builds_table_in_schema(self):
        """AC-3.1: frontend/convex/schema.ts (SSoT) defines 'builds' table with required indexes."""
        content = self._read_schema("frontend/convex/schema.ts")
        assert "builds:" in content or "builds :" in content
        assert "by_project" in content
        assert "by_user" in content
        assert "by_status" in content
        assert "by_project_status" in content

    def test_ac_3_2_agent_status_table_in_schema(self):
        """AC-3.2: frontend/convex/schema.ts (SSoT) defines 'agentStatus' with vos3Metadata."""
        content = self._read_schema("frontend/convex/schema.ts")
        assert "agentStatus:" in content or "agentStatus :" in content
        assert "by_build" in content
        assert "by_build_agent" in content
        assert "vos3Metadata" in content

    def test_ac_3_3_context_snapshots_in_schema(self):
        """AC-3.3: frontend/convex/schema.ts (SSoT) defines 'contextSnapshots'."""
        content = self._read_schema("frontend/convex/schema.ts")
        assert "contextSnapshots" in content
        assert "serializedState" in content
        assert "decisionLog" in content
        assert "taskProgress" in content

    def test_ac_3_1_builds_table_in_frontend_schema(self):
        """AC-3.1: frontend/convex/schema.ts also defines 'builds' (canonical deployment)."""
        content = self._read_schema("frontend/convex/schema.ts")
        assert "builds:" in content or "builds :" in content
        assert "by_project" in content
        assert "vos3Metadata" in content

    def test_ac_3_2_agent_status_in_frontend_schema(self):
        """AC-3.2: frontend/convex/schema.ts defines 'agentStatus'."""
        content = self._read_schema("frontend/convex/schema.ts")
        assert "agentStatus:" in content or "agentStatus :" in content
        assert "by_build_agent" in content

    def test_frontend_convex_functions_exist(self):
        """Frontend must have builds.ts and agentStatus.ts for useQuery subscriptions."""
        builds_ts = Path(__file__).parent.parent.parent / "frontend/convex/builds.ts"
        agent_ts = (
            Path(__file__).parent.parent.parent / "frontend/convex/agentStatus.ts"
        )
        assert builds_ts.exists(), "frontend/convex/builds.ts missing"
        assert agent_ts.exists(), "frontend/convex/agentStatus.ts missing"


# ===========================================================================
#  AC SUMMARY — Frontend Component Existence
# ===========================================================================


class TestFrontendComponents:
    """Verify required frontend components exist per TIS §1.5.3."""

    def _component_exists(self, relative_path: str) -> bool:
        full = Path(__file__).parent.parent.parent / relative_path
        return full.exists()

    def test_build_status_bar_exists(self):
        assert self._component_exists(
            "frontend/components/build/BuildStatusBar.tsx"
        ), "BuildStatusBar.tsx missing"

    def test_agent_status_cards_exists(self):
        assert self._component_exists(
            "frontend/components/build/AgentStatusCards.tsx"
        ), "AgentStatusCards.tsx missing"

    def test_build_history_exists(self):
        assert self._component_exists(
            "frontend/components/build/BuildHistory.tsx"
        ), "BuildHistory.tsx missing"

    def test_build_progress_panel_still_exists(self):
        """Existing BuildProgressPanel must not have been deleted."""
        assert self._component_exists(
            "frontend/components/build/BuildProgressPanel.tsx"
        ), "BuildProgressPanel.tsx was removed — should be preserved"

    def test_live_preview_still_exists(self):
        """LivePreview.tsx must not have been deleted or broken."""
        assert self._component_exists(
            "frontend/components/preview/LivePreview.tsx"
        ), "LivePreview.tsx was removed"


# ===========================================================================
#  REPORT — Print AC summary (runs as final test)
# ===========================================================================


class TestACReport:
    """Generate a human-readable acceptance criteria report."""

    def test_print_ac_summary(self, request):
        """
        This test always passes — it prints the AC mapping for review.
        Run with -v to see the full report.
        """
        report = """
╔══════════════════════════════════════════════════════════════════════════╗
║                    PHASE 1.0 — ACCEPTANCE CRITERIA AUDIT               ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║  AC-1: Authentication & Authorization                                  ║
║  ───────────────────────────────────                                   ║
║  AC-1.1  Missing token → 401 ........... TestClerkMiddlewareProdMode   ║
║  AC-1.2  Valid JWT → AuthUser ........... test_ac_1_2_valid_hs256_jwt  ║
║  AC-1.3  Expired JWT → 401 "expired" ... test_ac_1_3_expired_jwt      ║
║  AC-1.5  Convex user ID resolved ....... test_ac_1_5_convex_user_id   ║
║  AC-1.6  WebSocket auth ................ (requires running server)     ║
║                                                                        ║
║  AC-2: Convex Client Hardening                                         ║
║  ─────────────────────────────                                         ║
║  AC-2.1  Prod insert → mutation() ...... test_ac_2_1_prod_insert      ║
║  AC-2.2  Prod get → query() ............ test_ac_2_2_prod_get         ║
║  AC-2.3  Dev mode uses in-memory ....... TestConvexDBModeSwitch (6)    ║
║  AC-2.4  5×9 agents coalesced .......... test_ac_2_4_mixed_rapid      ║
║  AC-2.5  10 same-key → 1 mutation ...... test_ac_2_5_ten_updates      ║
║  AC-2.6  flush_now() delivers all ...... test_ac_2_6_flush_now        ║
║                                                                        ║
║  AC-3: Schema & Data Layer                                             ║
║  ─────────────────────────                                             ║
║  AC-3.1  builds table + indexes ........ TestSchemaExtension           ║
║  AC-3.2  agentStatus + vos3Metadata .... TestSchemaExtension           ║
║  AC-3.3  contextSnapshots .............. TestSchemaExtension           ║
║  AC-3.4  vos3Metadata accepts null ..... (runtime, needs Convex)       ║
║  AC-3.5  vos3Metadata stores data ...... (runtime, needs Convex)       ║
║                                                                        ║
║  AC-6: Deployment                                                      ║
║  ────────────────                                                      ║
║  AC-6.1  Railway health ................ (requires deployment)         ║
║  AC-6.2  Vercel loads .................. (requires deployment)         ║
║  AC-6.3  railway.toml fixed ............ test_ac_6_3_railway           ║
║  AC-6.4  E2E flow ...................... (requires deployment)         ║
║                                                                        ║
║  Legend: (runtime) = requires live Convex/Clerk/Railway                 ║
║          All other criteria are tested in this audit.                   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
        print(report)
