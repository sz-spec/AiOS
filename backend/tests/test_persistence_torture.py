"""
Persistence Torture Test
========================
Proves the write-through pattern works for ChatService, ProjectService,
and PromptHistoryService by hammering each with high-volume operations,
clearing in-memory caches, and verifying 100% recovery from the backing
store (Convex in production, dict-backed mock in tests).

Each test is fully independent: unique user_ids, isolated mock stores,
no shared mutable state between test functions.

Run:
    pytest tests/test_persistence_torture.py -v
"""

import os
import sys
import copy
import uuid
import pytest
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import patch

# Ensure backend root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Mock Convex Store — dict-backed, simulates Convex query/mutation semantics
# =============================================================================


class MockConvexStore:
    """In-memory store that mimics the subset of Convex operations used by the
    three repository classes: ConvexChatSessionRepository,
    ConvexProjectRepository, and ConvexPromptHistoryRepository.

    All data is stored in ``self.tables[table_name]`` as a list of dicts.
    Each dict gets a unique ``_id`` on insert.
    """

    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def _gen_id(self) -> str:
        return f"mock_{uuid.uuid4().hex[:16]}"

    # -- Low-level table ops (async — _run() calls asyncio.run() on these) --

    async def insert(self, table: str, doc: dict) -> str:
        doc_id = self._gen_id()
        row = {
            **doc,
            "_id": doc_id,
            "_creationTime": datetime.now(timezone.utc).isoformat(),
        }
        self.tables[table].append(row)
        return doc_id

    async def get(self, table: str, doc_id: str) -> Optional[dict]:
        for row in self.tables[table]:
            if row.get("_id") == doc_id:
                return copy.deepcopy(row)
        return None

    async def update(self, table: str, doc_id: str, updates: dict) -> Optional[dict]:
        for row in self.tables[table]:
            if row.get("_id") == doc_id:
                row.update(updates)
                return copy.deepcopy(row)
        return None

    async def delete(self, table: str, doc_id: str) -> bool:
        before = len(self.tables[table])
        self.tables[table] = [r for r in self.tables[table] if r.get("_id") != doc_id]
        return len(self.tables[table]) < before

    async def find(self, table: str, filters: dict) -> List[dict]:
        results = []
        for row in self.tables[table]:
            if all(row.get(k) == v for k, v in filters.items()):
                results.append(copy.deepcopy(row))
        return results

    async def list(self, table: str, **filters) -> List[dict]:
        if not filters:
            return [copy.deepcopy(r) for r in self.tables[table]]
        return await self.find(table, filters)

    # -- Chat session mutations/queries (named endpoints) --

    async def mutation(self, endpoint: str, args: dict):
        if endpoint == "chatSessions:save":
            return self._chat_save(args)
        if endpoint == "chatSessions:remove":
            return self._chat_remove(args)
        raise ValueError(f"Unknown mutation: {endpoint}")

    async def query(self, endpoint: str, args: dict):
        if endpoint == "chatSessions:load":
            return self._chat_load(args)
        if endpoint == "chatSessions:list":
            return self._chat_list(args)
        raise ValueError(f"Unknown query: {endpoint}")

    def _chat_save(self, args: dict) -> str:
        user_id = args["userId"]
        session_id = args["sessionId"]
        messages = args.get("messages", [])
        # Upsert: update if exists, insert if not
        for row in self.tables["chatSessions"]:
            if row.get("sessionId") == session_id:
                row["messages"] = messages
                row["userId"] = user_id
                return row["_id"]
        doc_id = self._gen_id()
        self.tables["chatSessions"].append(
            {
                "_id": doc_id,
                "userId": user_id,
                "sessionId": session_id,
                "messages": messages,
            }
        )
        return doc_id

    def _chat_load(self, args: dict) -> Optional[dict]:
        session_id = args["sessionId"]
        for row in self.tables["chatSessions"]:
            if row.get("sessionId") == session_id:
                return copy.deepcopy(row)
        return None

    def _chat_list(self, args: dict) -> List[dict]:
        user_id = args["userId"]
        return [
            copy.deepcopy(r)
            for r in self.tables["chatSessions"]
            if r.get("userId") == user_id
        ]

    def _chat_remove(self, args: dict) -> bool:
        session_id = args["sessionId"]
        before = len(self.tables["chatSessions"])
        self.tables["chatSessions"] = [
            r for r in self.tables["chatSessions"] if r.get("sessionId") != session_id
        ]
        return len(self.tables["chatSessions"]) < before


# =============================================================================
# Synchronous runner shim — mirrors _run() in convex.py
# =============================================================================


def _sync_run(coro):
    """Run an async coroutine synchronously, matching the convex.py pattern."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
            return loop.run_until_complete(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_store():
    """Fresh MockConvexStore for each test."""
    return MockConvexStore()


@pytest.fixture
def patched_chat_service(mock_store):
    """ChatService with Convex client replaced by mock_store."""
    with patch("core.repositories.convex._get_convex", return_value=mock_store), patch(
        "core.repositories.convex._run", side_effect=_sync_run
    ):
        from services.chat_service import ChatService

        svc = ChatService()
        yield svc


@pytest.fixture
def patched_project_service(mock_store):
    """ProjectService with Convex client replaced by mock_store."""
    with patch("core.repositories.convex._get_convex", return_value=mock_store), patch(
        "core.repositories.convex._run", side_effect=_sync_run
    ):
        from services.project_service import ProjectService

        svc = ProjectService()
        yield svc


@pytest.fixture
def patched_prompt_service(mock_store):
    """PromptHistoryService with Convex client replaced by mock_store."""
    with patch("core.repositories.convex._get_convex", return_value=mock_store), patch(
        "core.repositories.convex._run", side_effect=_sync_run
    ):
        from services.prompt_history import PromptHistoryService

        svc = PromptHistoryService()
        yield svc


# =============================================================================
# Helpers
# =============================================================================


def _add_message(service, session_id: str, role: str, content: str):
    """Append a message to a cached session and persist via save_session.

    ChatService has no dedicated add_message(); in production the chat route
    appends to session["messages"] then saves.  We replicate that here.
    """
    session = service._cache.get(session_id)
    assert session is not None, f"Session {session_id} not in cache"
    msg = {"role": role, "content": content}
    session["messages"].append(msg)
    # Write-through: persist full message list to Convex
    from services.chat_service import _get_repo

    try:
        _get_repo().save_session(
            session["user_id"],
            session_id,
            messages=session["messages"],
        )
    except Exception:
        pass  # Graceful degradation mirrors service pattern


def _unique_user() -> str:
    """Generate a unique user ID for test isolation."""
    return f"torture_user_{uuid.uuid4().hex[:10]}"


# =============================================================================
# Test 1: Rapid-Fire Chat Sessions
# =============================================================================


class TestChatPersistenceRapidFire:
    """Create 100 chat sessions with messages, nuke cache, recover all."""

    def test_chat_persistence_rapid_fire(self, patched_chat_service):
        svc = patched_chat_service
        user_id = _unique_user()
        session_ids = []

        # Phase 1: Create 100 sessions, each with 2-3 messages
        for i in range(100):
            sid = f"sess_{i}_{uuid.uuid4().hex[:6]}"
            session_ids.append(sid)
            svc.create_session(session_id=sid, user_id=user_id)
            _add_message(svc, sid, "user", f"Hello from session {i}")
            _add_message(svc, sid, "assistant", f"Response to session {i}")
            if i % 3 == 0:
                _add_message(svc, sid, "user", f"Follow-up in session {i}")

        # Sanity: cache should have all 100
        assert len(svc._cache) == 100, f"Cache has {len(svc._cache)}, expected 100"

        # Phase 2: Nuke the in-memory cache
        svc._cache = {}
        assert len(svc._cache) == 0, "Cache should be empty after nuke"

        # Phase 3: Recover every session from mock Convex
        recovered = 0
        message_mismatch = []
        for i, sid in enumerate(session_ids):
            session = svc.get_session(sid)
            assert session is not None, f"Session {sid} not recovered from Convex"
            recovered += 1

            # Verify message count: 2 messages normally, 3 for every 3rd session
            expected_msgs = 3 if i % 3 == 0 else 2
            actual_msgs = len(session.get("messages", []))
            if actual_msgs != expected_msgs:
                message_mismatch.append(
                    f"Session {sid}: expected {expected_msgs} msgs, got {actual_msgs}"
                )

        assert recovered == 100, f"Only recovered {recovered}/100 sessions"
        assert (
            len(message_mismatch) == 0
        ), f"{len(message_mismatch)} sessions have wrong message count:\n" + "\n".join(
            message_mismatch[:10]
        )


# =============================================================================
# Test 2: Project CRUD Stress
# =============================================================================


class TestProjectPersistenceCrudStress:
    """Create 50, update 25, delete 10 -- nuke cache -- verify via list_by_user."""

    def test_project_persistence_crud_stress(self, patched_project_service):
        svc = patched_project_service
        user_id = _unique_user()
        project_ids = []

        # Phase 1: Create 50 projects
        for i in range(50):
            p = svc.create(
                user_id=user_id,
                name=f"Project-{i}",
                description=f"Description for project {i}",
                category="test",
            )
            project_ids.append(p.id)

        assert len(svc._cache) == 50, f"Cache has {len(svc._cache)}, expected 50"

        # Phase 2: Update first 25 with new names
        updated_ids = set()
        for i in range(25):
            pid = project_ids[i]
            svc.update(
                pid, name=f"Updated-Project-{i}", description=f"Updated desc {i}"
            )
            updated_ids.add(pid)

        # Phase 3: Delete last 10
        deleted_ids = set()
        for i in range(40, 50):
            pid = project_ids[i]
            svc.delete(pid)
            deleted_ids.add(pid)

        # Phase 4: Nuke cache
        svc._cache = {}
        assert len(svc._cache) == 0

        # Phase 5: Recover via list_by_user (forces Convex read)
        projects = svc.list_by_user(user_id)

        # ConvexProjectRepository.delete() does soft-delete (isArchived=True)
        # list_by_user filters out archived, so we should get 40
        assert (
            len(projects) == 40
        ), f"Expected 40 projects (50 - 10 deleted), got {len(projects)}"

        # Verify the 25 updated projects have new names
        recovered_names = {p.id: p.name for p in projects}
        updated_count = 0
        for pid in updated_ids:
            if pid in recovered_names:
                if recovered_names[pid].startswith("Updated-Project-"):
                    updated_count += 1
        assert (
            updated_count == 25
        ), f"Expected 25 updated projects, found {updated_count}"

        # Verify none of the deleted IDs are present
        recovered_ids = {p.id for p in projects}
        leaked_deletes = deleted_ids & recovered_ids
        assert (
            len(leaked_deletes) == 0
        ), f"Deleted projects leaked through: {leaked_deletes}"


# =============================================================================
# Test 3: Prompt History Write-Through
# =============================================================================


class TestPromptHistoryWriteThrough:
    """Create 50 prompts, toggle favorites on 10, add tags to 15, nuke caches,
    verify full recovery via search_prompts (which triggers hydration)."""

    def test_prompt_history_write_through(self, patched_prompt_service):
        svc = patched_prompt_service
        user_id = _unique_user()
        prompt_ids = []

        # Phase 1: Create 50 prompts
        for i in range(50):
            entry = svc.create_prompt(
                user_id=user_id,
                prompt=f"Test prompt number {i}: explain concept {i}",
                model="claude-sonnet",
                tags=[],
            )
            prompt_ids.append(entry.id)

        assert len(svc._cache) == 50, f"Cache has {len(svc._cache)}, expected 50"

        # Phase 2: Toggle favorite on first 10
        favorited_ids = set()
        for i in range(10):
            result = svc.toggle_favorite(prompt_ids[i], user_id)
            assert result is True, f"toggle_favorite returned {result} for prompt {i}"
            favorited_ids.add(prompt_ids[i])

        # Phase 3: Add tags to prompts 10-24 (15 prompts)
        tagged_ids = set()
        for i in range(10, 25):
            result = svc.add_tags(prompt_ids[i], user_id, ["torture-test", f"tag-{i}"])
            assert result is not None, f"add_tags returned None for prompt {i}"
            tagged_ids.add(prompt_ids[i])

        # Phase 4: Nuke ALL in-memory caches
        svc._cache = {}
        svc._user_index = defaultdict(list)
        svc._project_index = defaultdict(list)

        assert len(svc._cache) == 0
        assert len(svc._user_index) == 0

        # Phase 5: Search prompts — triggers _hydrate_user → Convex read
        results, total = svc.search_prompts(user_id)
        assert total == 50, f"Expected 50 prompts, hydrated {total}"

        # Verify favorites
        favorites, fav_total = svc.search_prompts(user_id, favorites_only=True)
        assert fav_total == 10, f"Expected 10 favorited prompts, got {fav_total}"
        fav_ids = {p.id for p in favorites}
        assert (
            fav_ids == favorited_ids
        ), f"Favorited IDs mismatch: expected {favorited_ids}, got {fav_ids}"

        # Verify tags
        tagged, tag_total = svc.search_prompts(user_id, tags=["torture-test"])
        assert tag_total == 15, f"Expected 15 tagged prompts, got {tag_total}"
        tag_ids = {p.id for p in tagged}
        assert (
            tag_ids == tagged_ids
        ), f"Tagged IDs mismatch: expected {tagged_ids}, got {tag_ids}"


# =============================================================================
# Test 4: Simulated SIGKILL Recovery
# =============================================================================


class TestSigkillRecoverySimulation:
    """Create data across all three services, destroy service objects entirely,
    instantiate fresh services, and prove 100% recovery from Convex."""

    def test_sigkill_recovery_simulation(self, mock_store):
        """Full lifecycle: create -> kill -> recover -> assert."""

        with patch(
            "core.repositories.convex._get_convex", return_value=mock_store
        ), patch("core.repositories.convex._run", side_effect=_sync_run):

            user_id = _unique_user()

            # ---- Phase 1: Create data ----

            # 20 chat sessions
            from services.chat_service import ChatService

            chat_svc = ChatService()
            chat_session_ids = []
            for i in range(20):
                sid = f"sigkill_sess_{i}_{uuid.uuid4().hex[:6]}"
                chat_session_ids.append(sid)
                chat_svc.create_session(session_id=sid, user_id=user_id)
                _add_message(chat_svc, sid, "user", f"SIGKILL test msg {i}")

            # 20 projects
            from services.project_service import ProjectService

            proj_svc = ProjectService()
            project_ids = []
            for i in range(20):
                p = proj_svc.create(
                    user_id=user_id,
                    name=f"SigkillProject-{i}",
                    description=f"Desc {i}",
                    category="sigkill-test",
                )
                project_ids.append(p.id)

            # 20 prompts
            from services.prompt_history import PromptHistoryService

            prompt_svc = PromptHistoryService()
            prompt_ids = []
            for i in range(20):
                entry = prompt_svc.create_prompt(
                    user_id=user_id,
                    prompt=f"SIGKILL prompt {i}",
                    model="test-model",
                )
                prompt_ids.append(entry.id)

            # Sanity check: all data in caches
            assert len(chat_svc._cache) == 20
            assert len(proj_svc._cache) == 20
            assert len(prompt_svc._cache) == 20

            # ---- Phase 2: Simulate SIGKILL (destroy all service objects) ----
            del chat_svc
            del proj_svc
            del prompt_svc

            # ---- Phase 3: Fresh service instances (empty caches) ----
            chat_svc2 = ChatService()
            proj_svc2 = ProjectService()
            prompt_svc2 = PromptHistoryService()

            assert len(chat_svc2._cache) == 0
            assert len(proj_svc2._cache) == 0
            assert len(prompt_svc2._cache) == 0

            # ---- Phase 4: Recover and assert ----

            # Chat sessions: recover each individually
            chat_recovered = 0
            for sid in chat_session_ids:
                session = chat_svc2.get_session(sid)
                if session is not None:
                    chat_recovered += 1
                    assert (
                        len(session.get("messages", [])) >= 1
                    ), f"Session {sid} recovered but messages missing"
            assert chat_recovered == 20, f"Chat: recovered {chat_recovered}/20 sessions"

            # Projects: recover via list_by_user
            projects = proj_svc2.list_by_user(user_id)
            assert len(projects) == 20, f"Projects: recovered {len(projects)}/20"
            recovered_proj_ids = {p.id for p in projects}
            for pid in project_ids:
                assert (
                    pid in recovered_proj_ids
                ), f"Project {pid} not recovered after SIGKILL"

            # Prompts: recover via search_prompts (triggers hydration)
            prompts, total = prompt_svc2.search_prompts(user_id)
            assert total == 20, f"Prompts: recovered {total}/20"
            recovered_prompt_ids = {p.id for p in prompts}
            for pid in prompt_ids:
                assert (
                    pid in recovered_prompt_ids
                ), f"Prompt {pid} not recovered after SIGKILL"


# =============================================================================
# Test 5: Interleaved Operations (bonus stress)
# =============================================================================


class TestInterleavedOperations:
    """Rapidly alternate between create/read/update across services to flush
    out ordering bugs in the write-through path."""

    def test_interleaved_create_read_update(self, mock_store):
        """Create-read-update cycle across all three services, interleaved."""

        with patch(
            "core.repositories.convex._get_convex", return_value=mock_store
        ), patch("core.repositories.convex._run", side_effect=_sync_run):

            from services.chat_service import ChatService
            from services.project_service import ProjectService
            from services.prompt_history import PromptHistoryService

            chat_svc = ChatService()
            proj_svc = ProjectService()
            prompt_svc = PromptHistoryService()

            user_id = _unique_user()
            artifacts = {"sessions": [], "projects": [], "prompts": []}

            # Interleaved creation: 30 of each, round-robin
            for i in range(30):
                # Chat
                sid = f"interleave_sess_{i}"
                chat_svc.create_session(session_id=sid, user_id=user_id)
                _add_message(chat_svc, sid, "user", f"Interleaved msg {i}")
                artifacts["sessions"].append(sid)

                # Project
                p = proj_svc.create(
                    user_id=user_id,
                    name=f"Interleaved-{i}",
                    description=f"Desc-{i}",
                    category="interleave",
                )
                artifacts["projects"].append(p.id)

                # Prompt
                entry = prompt_svc.create_prompt(
                    user_id=user_id,
                    prompt=f"Interleaved prompt {i}",
                )
                artifacts["prompts"].append(entry.id)

            # Update some projects mid-stream
            for i in range(0, 30, 2):
                proj_svc.update(
                    artifacts["projects"][i],
                    name=f"Interleaved-Updated-{i}",
                )

            # Nuke all caches
            chat_svc._cache = {}
            proj_svc._cache = {}
            prompt_svc._cache = {}
            prompt_svc._user_index = defaultdict(list)
            prompt_svc._project_index = defaultdict(list)

            # Recover
            for sid in artifacts["sessions"]:
                s = chat_svc.get_session(sid)
                assert s is not None, f"Session {sid} lost"

            projects = proj_svc.list_by_user(user_id)
            assert len(projects) == 30, f"Projects: {len(projects)}/30"
            # Check updates persisted
            updated_count = sum(
                1 for p in projects if p.name.startswith("Interleaved-Updated-")
            )
            assert (
                updated_count == 15
            ), f"Expected 15 updated projects, got {updated_count}"

            prompts, total = prompt_svc.search_prompts(user_id)
            assert total == 30, f"Prompts: {total}/30"


# =============================================================================
# Test 6: Cache Nuke Idempotency
# =============================================================================


class TestCacheNukeIdempotency:
    """Nuke and recover multiple times to prove reads are truly from Convex,
    not from some residual reference."""

    def test_repeated_nuke_and_recover(self, patched_project_service):
        svc = patched_project_service
        user_id = _unique_user()

        # Create 10 projects
        ids = []
        for i in range(10):
            p = svc.create(
                user_id=user_id,
                name=f"Idempotent-{i}",
                description=f"Desc-{i}",
                category="nuke-test",
            )
            ids.append(p.id)

        # Nuke and recover 5 times
        for cycle in range(5):
            svc._cache = {}
            assert len(svc._cache) == 0, f"Cycle {cycle}: cache not empty after nuke"

            projects = svc.list_by_user(user_id)
            assert (
                len(projects) == 10
            ), f"Cycle {cycle}: expected 10 projects, got {len(projects)}"

            recovered_ids = {p.id for p in projects}
            for pid in ids:
                assert (
                    pid in recovered_ids
                ), f"Cycle {cycle}: project {pid} missing after nuke"
