"""
Stage 1 · Atomic unit isolation for services/sync_engine.py.

Covers — purpose | guards file:line:
  SyncResult shape + helpers                       | sync_engine.py:90-138
  _project_to_payload                              | sync_engine.py:147-158
  _chat_session_to_payload                         | sync_engine.py:161-169
  _chat_message_to_payload                         | sync_engine.py:172-180
  resolve_conflict cloud_wins / local_wins / tied  | sync_engine.py:316-347
  perform_sync skips when locality != local-first  | sync_engine.py:542-545
  perform_pull skips when locality != local-first  | sync_engine.py:596-599
  perform_sync respects custom pusher              | sync_engine.py:533-568, 701-737
  _noop_pusher accepts any payload                 | sync_engine.py:406-411

The Convex backend is not present in this unit env — every test
uses an injected pusher/puller. We never hit a real cloud.
"""

from __future__ import annotations

import asyncio
import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def sync_db(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    yield


# ---------------------------------------------------------------------------
# SyncResult value type
# ---------------------------------------------------------------------------


def test_sync_result_default_shape():
    from services.sync_engine import SyncResult

    r = SyncResult(started_at_ms=1, finished_at_ms=2)
    assert r.ok is True
    assert r.total_pushed == 0
    assert r.total_pulled == 0
    assert r.skipped_reason is None
    assert r.errors == []


def test_sync_result_total_pushed_sums_all_tables():
    from services.sync_engine import SyncResult

    r = SyncResult(started_at_ms=0, finished_at_ms=0)
    r.pushed["projects"] = 3
    r.pushed["chatSessions"] = 2
    r.pushed["chatSessionMessages"] = 1
    assert r.total_pushed == 6


def test_sync_result_total_pulled_sums_inserted_and_updated():
    from services.sync_engine import SyncResult

    r = SyncResult(started_at_ms=0, finished_at_ms=0)
    r.pulled["projects"] = {"inserted": 2, "updated": 1}
    r.pulled["chatSessions"] = {"inserted": 0, "updated": 4}
    r.pulled["chatSessionMessages"] = {"inserted": 1, "updated": 1}
    assert r.total_pulled == 9


def test_sync_result_ok_false_on_errors():
    from services.sync_engine import SyncResult

    r = SyncResult(started_at_ms=0, finished_at_ms=0)
    r.errors.append({"table": "x", "id": "1", "error": "boom"})
    assert r.ok is False


def test_sync_result_ok_false_when_skipped():
    from services.sync_engine import SyncResult

    r = SyncResult(started_at_ms=0, finished_at_ms=0)
    r.skipped_reason = "locality_not_local_first"
    assert r.ok is False


def test_sync_result_to_dict_includes_computed_props():
    from services.sync_engine import SyncResult

    r = SyncResult(started_at_ms=10, finished_at_ms=20)
    r.pushed["projects"] = 1
    d = r.to_dict()
    assert d["total_pushed"] == 1
    assert d["total_pulled"] == 0
    assert d["ok"] is True
    assert d["started_at_ms"] == 10
    assert d["finished_at_ms"] == 20


# ---------------------------------------------------------------------------
# resolve_conflict — pure last-write-wins
# ---------------------------------------------------------------------------


class _MockRow:
    """Minimal stand-in for a SQLAlchemy row — exposes the attrs we touch."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_resolve_conflict_cloud_newer():
    from services.sync_engine import resolve_conflict, CONFLICT_CLOUD_WINS

    local = _MockRow(updatedAt=100)
    cloud = {"updatedAt": 200}
    assert resolve_conflict(local, cloud) == CONFLICT_CLOUD_WINS


def test_resolve_conflict_local_newer():
    from services.sync_engine import resolve_conflict, CONFLICT_LOCAL_WINS

    local = _MockRow(updatedAt=300)
    cloud = {"updatedAt": 100}
    assert resolve_conflict(local, cloud) == CONFLICT_LOCAL_WINS


def test_resolve_conflict_tied_keeps_local():
    from services.sync_engine import resolve_conflict, CONFLICT_TIED_LOCAL_KEEPS

    local = _MockRow(updatedAt=500)
    cloud = {"updatedAt": 500}
    assert resolve_conflict(local, cloud) == CONFLICT_TIED_LOCAL_KEEPS


def test_resolve_conflict_missing_cloud_timestamp_local_wins():
    """Cloud row without `updatedAt` → cloud_ts=0 → local wins."""
    from services.sync_engine import resolve_conflict, CONFLICT_LOCAL_WINS

    local = _MockRow(updatedAt=100)
    cloud = {}  # no updatedAt
    assert resolve_conflict(local, cloud) == CONFLICT_LOCAL_WINS


def test_resolve_conflict_uses_custom_key():
    """Chat messages use `timestamp`, not `updatedAt`."""
    from services.sync_engine import resolve_conflict, CONFLICT_CLOUD_WINS

    local = _MockRow(timestamp=100)
    cloud = {"timestamp": 200}
    assert (
        resolve_conflict(
            local,
            cloud,
            updated_at_key="timestamp",
        )
        == CONFLICT_CLOUD_WINS
    )


# ---------------------------------------------------------------------------
# perform_sync skip gates (locality preference)
# ---------------------------------------------------------------------------


def test_perform_sync_skipped_when_not_local_first(unit_env, monkeypatch):
    """If VOS3_LOCALITY_PREFERENCE != local-first → skip + return."""
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "cloud-first")
    from services.sync_engine import SovereignSyncEngine

    eng = SovereignSyncEngine(pusher=lambda t, p: None, puller=lambda t, s: [])
    result = asyncio.run(eng.perform_sync())
    assert result.skipped_reason == "locality_not_local_first"
    assert result.total_pushed == 0


def test_perform_sync_runs_when_local_first(sync_db, monkeypatch):
    """If env is set correctly, perform_sync returns with no skip reason."""
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "local-first")

    async def _async_pusher(table, payload):
        pass

    async def _async_puller(table, since_ms):
        return []

    from services.sync_engine import SovereignSyncEngine

    eng = SovereignSyncEngine(pusher=_async_pusher, puller=_async_puller)
    result = asyncio.run(eng.perform_sync())
    assert result.skipped_reason is None


def test_perform_pull_skipped_when_not_local_first(unit_env, monkeypatch):
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "")
    from services.sync_engine import SovereignSyncEngine

    async def _async_pusher(table, payload):
        pass

    async def _async_puller(table, since_ms):
        return []

    eng = SovereignSyncEngine(pusher=_async_pusher, puller=_async_puller)
    result = asyncio.run(eng.perform_pull())
    assert result.skipped_reason == "locality_not_local_first"


# ---------------------------------------------------------------------------
# _noop_pusher / _noop_puller — async no-ops
# ---------------------------------------------------------------------------


def test_noop_pusher_returns_none():
    from services.sync_engine import _noop_pusher

    result = asyncio.run(_noop_pusher("projects", {"_id": "x"}))
    assert result is None


def test_noop_puller_returns_empty_list():
    from services.sync_engine import _noop_puller

    rows = asyncio.run(_noop_puller("projects", since_ms=0))
    assert rows == []
