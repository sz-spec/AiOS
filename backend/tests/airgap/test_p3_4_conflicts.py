"""
P3.4 — bidirectional sync with conflict resolution.

Coverage groups:

  1. resolve_conflict() — pure helper; LWW semantics + tie-breaker.

  2. SovereignSyncEngine.perform_pull() — four flow shapes:
       a. cloud row absent locally → INSERT
       b. cloud row present + local clean → UPDATE
       c. conflict + cloud newer → cloud wins, dirty clears
       d. conflict + local newer → local stays dirty, no overwrite

  3. /api/system/sync/status — returns the watermark + dirty counts
     from a freshly-mutated local DB.

All tests stay on the air-gap loopback kill-switch; no socket
traffic is generated — the puller is always a local stub.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    Project,
    _reset_for_tests,
    get_session,
    init_db,
)
from core.repositories.sqlite import SQLiteChatSessionRepository
from services.sync_engine import (
    CONFLICT_CLOUD_WINS,
    CONFLICT_LOCAL_WINS,
    CONFLICT_TIED_LOCAL_KEEPS,
    SovereignSyncEngine,
    resolve_conflict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    """Fresh SQLite under the airgap-pinned env."""
    _reset_for_tests()
    init_db()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()


class _ScriptedPuller:
    """Async stub puller that replays a per-table scripted response.

    `script` is a dict[table_name, list[row_dict]]; the puller
    returns the configured list verbatim and ignores `since_ms`.
    Tests use this to inject specific "what the cloud has" snapshots
    without touching Convex.
    """

    def __init__(self, script: dict):
        self.script = script
        self.calls: list = []

    async def __call__(self, table: str, since_ms):
        self.calls.append((table, since_ms))
        return list(self.script.get(table, []))


# ---------------------------------------------------------------------------
# (1) resolve_conflict() — pure helper semantics
# ---------------------------------------------------------------------------


class _RowStub:
    """Stand-in for an ORM row in resolve_conflict() unit tests —
    the helper only reads `updatedAt` / `timestamp` via getattr."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_resolve_conflict_cloud_wins_when_newer():
    local = _RowStub(updatedAt=1000)
    cloud = {"updatedAt": 2000, "_id": "x"}
    assert resolve_conflict(local, cloud) == CONFLICT_CLOUD_WINS


def test_resolve_conflict_local_wins_when_newer():
    local = _RowStub(updatedAt=5000)
    cloud = {"updatedAt": 1000, "_id": "x"}
    assert resolve_conflict(local, cloud) == CONFLICT_LOCAL_WINS


def test_resolve_conflict_tie_keeps_local():
    """A tie is rare but possible (same clock ms). Prefer keeping
    local dirty so the next push reconciles authoritatively."""
    local = _RowStub(updatedAt=1234)
    cloud = {"updatedAt": 1234, "_id": "x"}
    assert resolve_conflict(local, cloud) == CONFLICT_TIED_LOCAL_KEEPS


def test_resolve_conflict_missing_cloud_timestamp_defaults_to_local_wins():
    """A cloud row without updatedAt is treated as ts=0 — we never
    overwrite a dirty local edit with a row that can't prove it's newer."""
    local = _RowStub(updatedAt=500)
    cloud = {"_id": "x"}  # no updatedAt
    assert resolve_conflict(local, cloud) == CONFLICT_LOCAL_WINS


def test_resolve_conflict_honors_custom_key_for_messages():
    """ChatSessionMessage uses `timestamp` as the LWW key, not updatedAt."""
    local = _RowStub(timestamp=900)
    cloud = {"timestamp": 1800, "_id": "msg-1"}
    assert (
        resolve_conflict(local, cloud, updated_at_key="timestamp")
        == CONFLICT_CLOUD_WINS
    )


# ---------------------------------------------------------------------------
# (2) perform_pull — full flow integration
# ---------------------------------------------------------------------------


def test_pull_inserts_row_absent_locally(local_db):
    """Cloud has a project the local DB has never seen → INSERT."""
    puller = _ScriptedPuller(
        {
            "projects": [
                {
                    "_id": "proj-new-from-cloud",
                    "name": "cloud-only project",
                    "ownerId": "user-1",
                    "createdAt": 1000,
                    "updatedAt": 2000,
                    "isArchived": False,
                }
            ],
        }
    )
    engine = SovereignSyncEngine(pusher=_noop_pusher, puller=puller)
    result = asyncio.run(engine.perform_pull())

    assert result.ok, result.to_dict()
    assert result.pulled["projects"]["inserted"] == 1
    assert result.pulled["projects"]["updated"] == 0
    assert result.conflicts == []
    with get_session() as session:
        p = session.query(Project).filter_by(id="proj-new-from-cloud").one()
        assert p.name == "cloud-only project"
        assert p.dirty is False, "cloud-inserted row must NOT be dirty"
        assert p.lastSyncedAt is not None


def test_pull_updates_local_clean_row(local_db):
    """Local row exists, dirty=False (already in sync). Cloud sends a
    newer version → UPDATE without consulting the conflict resolver."""
    now = int(time.time() * 1000)
    with get_session() as session:
        session.add(
            Project(
                id="proj-clean",
                name="old-name",
                ownerId="user-1",
                isArchived=False,
                createdAt=now,
                updatedAt=now,
                dirty=False,
                lastSyncedAt=now,
            )
        )
        session.commit()

    puller = _ScriptedPuller(
        {
            "projects": [
                {
                    "_id": "proj-clean",
                    "name": "new-name-from-cloud",
                    "ownerId": "user-1",
                    "createdAt": now,
                    "updatedAt": now + 5000,
                    "isArchived": False,
                }
            ],
        }
    )
    engine = SovereignSyncEngine(pusher=_noop_pusher, puller=puller)
    result = asyncio.run(engine.perform_pull())

    assert result.pulled["projects"]["updated"] == 1
    assert result.pulled["projects"]["inserted"] == 0
    assert result.conflicts == []
    with get_session() as session:
        p = session.query(Project).filter_by(id="proj-clean").one()
        assert p.name == "new-name-from-cloud"
        assert p.dirty is False


def test_pull_conflict_cloud_newer_wins(local_db):
    """Local row is dirty (pending push). Cloud has an even-newer
    version → conflict resolved with cloud wins; local dirty clears."""
    now = int(time.time() * 1000)
    with get_session() as session:
        session.add(
            Project(
                id="proj-conflict-cloud-wins",
                name="local-pending-edit",
                ownerId="user-1",
                isArchived=False,
                createdAt=now,
                updatedAt=now + 1000,  # local touched at t+1
                dirty=True,
                lastSyncedAt=None,
            )
        )
        session.commit()

    puller = _ScriptedPuller(
        {
            "projects": [
                {
                    "_id": "proj-conflict-cloud-wins",
                    "name": "newer-cloud-edit",
                    "ownerId": "user-1",
                    "createdAt": now,
                    "updatedAt": now + 5000,  # cloud is t+5 — newer
                    "isArchived": False,
                }
            ],
        }
    )
    engine = SovereignSyncEngine(pusher=_noop_pusher, puller=puller)
    result = asyncio.run(engine.perform_pull())

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict["table"] == "projects"
    assert conflict["id"] == "proj-conflict-cloud-wins"
    assert conflict["decision"] == CONFLICT_CLOUD_WINS

    with get_session() as session:
        p = session.query(Project).filter_by(id="proj-conflict-cloud-wins").one()
        assert p.name == "newer-cloud-edit"
        assert p.dirty is False, "cloud-wins must clear dirty"
        assert p.lastSyncedAt is not None
    assert result.pulled["projects"]["updated"] == 1


def test_pull_conflict_local_newer_keeps_dirty(local_db):
    """Local row is dirty AND newer than cloud → keep local, dirty
    stays True so the next perform_sync() pushes it authoritatively."""
    now = int(time.time() * 1000)
    with get_session() as session:
        session.add(
            Project(
                id="proj-conflict-local-wins",
                name="local-newer-edit",
                ownerId="user-1",
                isArchived=False,
                createdAt=now,
                updatedAt=now + 9000,  # local at t+9
                dirty=True,
                lastSyncedAt=None,
            )
        )
        session.commit()

    puller = _ScriptedPuller(
        {
            "projects": [
                {
                    "_id": "proj-conflict-local-wins",
                    "name": "stale-cloud-edit",
                    "ownerId": "user-1",
                    "createdAt": now,
                    "updatedAt": now + 2000,  # cloud at t+2 — older
                    "isArchived": False,
                }
            ],
        }
    )
    engine = SovereignSyncEngine(pusher=_noop_pusher, puller=puller)
    result = asyncio.run(engine.perform_pull())

    assert len(result.conflicts) == 1
    assert result.conflicts[0]["decision"] == CONFLICT_LOCAL_WINS
    # local_wins → no local overwrite, dirty unchanged.
    assert result.pulled["projects"]["updated"] == 0
    assert result.pulled["projects"]["inserted"] == 0

    with get_session() as session:
        p = session.query(Project).filter_by(id="proj-conflict-local-wins").one()
        assert p.name == "local-newer-edit", "local row must be preserved"
        assert p.dirty is True, "local-wins must KEEP dirty for next push"


def test_pull_skips_when_locality_not_local_first(local_db, monkeypatch):
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "cloud-first")
    engine = SovereignSyncEngine(pusher=_noop_pusher, puller=_ScriptedPuller({}))
    result = asyncio.run(engine.perform_pull())
    assert result.skipped_reason == "locality_not_local_first"
    assert result.total_pulled == 0


# ---------------------------------------------------------------------------
# (3) /api/system/sync/status — endpoint shape + counts
# ---------------------------------------------------------------------------


def test_sync_status_endpoint_reports_dirty_count(local_db):
    """After a local write, the status endpoint must report dirty_count >= 1."""
    repo = SQLiteChatSessionRepository()
    asyncio.run(
        repo.append_message(
            session_id="sess-status",
            user_id="user-1",
            role="user",
            content="dirty after write",
        )
    )

    from api.system_routes import router as system_router

    app = FastAPI()
    app.include_router(system_router)
    client = TestClient(app)

    resp = client.get("/api/system/sync/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dirty_count"] >= 2  # one ChatSession + one message
    assert "tables" in body
    assert body["tables"]["chatSessions"]["dirty"] >= 1
    assert body["tables"]["chatSessionMessages"]["dirty"] >= 1
    # No sync has happened yet — watermark is None.
    assert body["last_synced_at_ms"] is None


def test_sync_status_endpoint_updates_after_sync(local_db):
    """Run perform_sync(), then check the endpoint reflects the new
    watermark + zero dirty count."""
    repo = SQLiteChatSessionRepository()
    asyncio.run(
        repo.append_message(
            session_id="sess-after-sync",
            user_id="user-1",
            role="user",
            content="will sync",
        )
    )

    async def _noop(table, payload):
        return None

    engine = SovereignSyncEngine(pusher=_noop, puller=_ScriptedPuller({}))
    asyncio.run(engine.perform_sync())

    from api.system_routes import router as system_router

    app = FastAPI()
    app.include_router(system_router)
    client = TestClient(app)
    body = client.get("/api/system/sync/status").json()

    assert body["dirty_count"] == 0
    assert body["last_synced_at_ms"] is not None
    assert body["tables"]["chatSessions"]["dirty"] == 0
    assert body["tables"]["chatSessionMessages"]["dirty"] == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_pusher(table: str, payload: dict):
    """Shared async stub — perform_pull() doesn't use the pusher, but
    the engine still needs one at construction time."""
    return None
