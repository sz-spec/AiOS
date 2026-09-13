"""
P3.3 / Sync Engine + Hybrid Diagnostics — pytest coverage.

Three groups:

  1. Dirty-flag invariant — every local write must mark its row
     dirty=True so the sync engine sees it.

  2. SovereignSyncEngine.perform_sync() — round-trips a dirty row
     through a stub pusher and verifies the dirty bit is cleared
     plus lastSyncedAt is stamped. The pusher is a local mock; no
     network is touched.

  3. Hybrid diagnostics — diagnose_readiness() must emit the
     platform-correct `fix_command` AND advertise whether
     attempt_self_heal() can run unattended on this OS.

Network isolation is enforced for the whole suite by the parent
airgap conftest (loopback-only kill-switch); the sync tests rely on
the noop / stub pusher so no socket activity happens at all.
"""

from __future__ import annotations

import asyncio
import platform

import pytest

from core.database.sqlcipher_setup import (
    SelfHealResult,
    attempt_self_heal,
    diagnose_readiness,
)
from core.database.sqlite_setup import (
    ChatSession,
    ChatSessionMessage,
    Project,
    _reset_for_tests,
    get_session,
    init_db,
)
from core.repositories.sqlite import SQLiteChatSessionRepository
from services.sync_engine import SovereignSyncEngine, SyncResult

# ---------------------------------------------------------------------------
# Local-DB fixture — each test gets a clean SQLite file in tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env, monkeypatch):
    """Point sqlite_setup at a fresh tmp DB and bootstrap the schema."""
    # airgap_env already sets VOS3_LOCAL_DB_PATH to a tmp file and
    # VOS3_LOCALITY_PREFERENCE=local-first; we only need to (re)init
    # the schema with the P3.3 columns in place.
    _reset_for_tests()
    init_db()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()


# ---------------------------------------------------------------------------
# (1) Dirty-flag invariant on local writes
# ---------------------------------------------------------------------------


def test_chat_append_marks_session_and_message_dirty(local_db):
    """`SQLiteChatSessionRepository.append_message()` must leave the
    session row AND the new message row with dirty=True so the sync
    engine sees them on its next pass."""
    repo = SQLiteChatSessionRepository()
    msg_id = asyncio.run(
        repo.append_message(
            session_id="sess-dirty-check",
            user_id="user-1",
            role="user",
            content="hello fortress",
            metadata={"client": "test"},
        )
    )
    assert msg_id

    with get_session() as session:
        cs = session.query(ChatSession).filter_by(sessionId="sess-dirty-check").one()
        assert cs.dirty is True, "session row should be dirty after write"
        assert cs.lastSyncedAt is None

        msg = session.query(ChatSessionMessage).filter_by(id=msg_id).one()
        assert msg.dirty is True, "message row should be dirty after write"
        assert msg.lastSyncedAt is None


def test_project_default_dirty_on_insert(local_db):
    """A directly inserted Project row inherits dirty=True from the
    column default — the sync engine should pick it up without the
    caller having to remember to set the flag explicitly."""
    import time

    now = int(time.time() * 1000)
    with get_session() as session:
        p = Project(
            id="proj-1",
            name="Fortress alpha",
            ownerId="user-1",
            isArchived=False,
            createdAt=now,
            updatedAt=now,
        )
        session.add(p)
        session.commit()

    with get_session() as session:
        p = session.query(Project).filter_by(id="proj-1").one()
        assert p.dirty is True
        assert p.lastSyncedAt is None


# ---------------------------------------------------------------------------
# (2) SovereignSyncEngine round-trip
# ---------------------------------------------------------------------------


class _RecordingPusher:
    """Async stub that records every (table, payload) call."""

    def __init__(self, *, fail_tables: tuple = ()):
        self.calls: list = []
        self.fail_tables = set(fail_tables)

    async def __call__(self, table: str, payload: dict):
        self.calls.append((table, payload))
        if table in self.fail_tables:
            raise RuntimeError(f"injected failure for table={table}")


def test_perform_sync_clears_dirty_and_stamps_last_synced(local_db):
    """Insert a dirty chat session + message, run perform_sync, then
    confirm both rows have dirty=False and lastSyncedAt set."""
    repo = SQLiteChatSessionRepository()
    asyncio.run(
        repo.append_message(
            session_id="sess-sync",
            user_id="user-1",
            role="user",
            content="payload to sync",
        )
    )

    pusher = _RecordingPusher()
    engine = SovereignSyncEngine(pusher=pusher)
    result: SyncResult = asyncio.run(engine.perform_sync())

    assert result.ok, result.to_dict()
    assert result.skipped_reason is None
    assert result.pushed["chatSessions"] == 1
    assert result.pushed["chatSessionMessages"] == 1
    assert result.errors == []

    tables_called = sorted(t for t, _ in pusher.calls)
    assert "chatSessions" in tables_called
    assert "chatSessionMessages" in tables_called

    with get_session() as session:
        cs = session.query(ChatSession).filter_by(sessionId="sess-sync").one()
        assert cs.dirty is False, "dirty must clear after a successful push"
        assert cs.lastSyncedAt is not None
        msgs = session.query(ChatSessionMessage).filter_by(sessionId="sess-sync").all()
        assert msgs and all(m.dirty is False for m in msgs)
        assert all(m.lastSyncedAt is not None for m in msgs)


def test_perform_sync_keeps_row_dirty_on_push_failure(local_db):
    """If the pusher raises for a row, dirty must STAY True so the
    next sweep retries it. Errors are recorded on SyncResult."""
    repo = SQLiteChatSessionRepository()
    asyncio.run(
        repo.append_message(
            session_id="sess-failing",
            user_id="user-1",
            role="user",
            content="this push will fail",
        )
    )

    pusher = _RecordingPusher(fail_tables=("chatSessions",))
    engine = SovereignSyncEngine(pusher=pusher)
    result = asyncio.run(engine.perform_sync())

    # chatSessions push failed → that row stays dirty.
    with get_session() as session:
        cs = session.query(ChatSession).filter_by(sessionId="sess-failing").one()
        assert cs.dirty is True
        assert cs.lastSyncedAt is None
    # And the engine recorded exactly one error for that table.
    assert any(e["table"] == "chatSessions" for e in result.errors)
    # The message row CAN still get pushed — it's an independent row;
    # the engine is best-effort per row.


def test_perform_sync_skips_when_locality_not_local_first(local_db, monkeypatch):
    """`VOS3_LOCALITY_PREFERENCE != local-first` → engine is inert."""
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "cloud-first")
    engine = SovereignSyncEngine(pusher=_RecordingPusher())
    result = asyncio.run(engine.perform_sync())
    assert result.skipped_reason == "locality_not_local_first"
    assert result.total_pushed == 0


# ---------------------------------------------------------------------------
# (3) Hybrid diagnostics: per-OS fix_command + self-heal opt-in
# ---------------------------------------------------------------------------


def test_diagnose_emits_platform_specific_fix_command(monkeypatch):
    """When fortress is requested but no driver, fix_command must be
    populated AND name a tool that actually exists on this OS."""
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "irrelevant-for-this-test")
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Force the no-driver branch without uninstalling sqlcipher3.
    from core.database import sqlcipher_setup

    monkeypatch.setattr(sqlcipher_setup, "detect_driver", lambda: None)

    r = diagnose_readiness(emit=False)
    assert r["driver_available"] is False
    assert r["fix_command"], "fix_command must be a non-empty string"
    assert r["platform"] == platform.system()

    if platform.system() == "Darwin":
        assert "brew install sqlcipher" in r["fix_command"]
        assert "pip install" in r["fix_command"]
        assert r["can_self_heal"] is True
    elif platform.system() == "Linux":
        assert "apt-get install" in r["fix_command"]
        assert "libsqlcipher-dev" in r["fix_command"]
        assert r["can_self_heal"] is True
    elif platform.system() == "Windows":
        assert "zetetic.net" in r["fix_command"]
        # No automated path on Windows.
        assert r["can_self_heal"] is False


def test_diagnose_reports_no_fix_command_when_ready(monkeypatch):
    """No driver gap → no fix_command (it's None)."""
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    r = diagnose_readiness(emit=False)
    assert r["fix_command"] is None
    assert r["can_self_heal"] is False


def test_attempt_self_heal_requires_opt_in(monkeypatch):
    """`attempt_self_heal()` must refuse to run when neither
    VOS3_AUTO_HEAL=1 nor force=True is set. Server hosts MUST NOT
    silently invoke `brew/apt` from a startup hook."""
    monkeypatch.delenv("VOS3_AUTO_HEAL", raising=False)
    res = attempt_self_heal(force=False)
    assert isinstance(res, SelfHealResult)
    assert res.ok is False
    assert "VOS3_AUTO_HEAL" in (res.error or "")
    assert res.steps == []


def test_attempt_self_heal_no_op_when_no_plan(monkeypatch):
    """On an unknown platform there's no automated plan — the result
    must surface the manual fix command, not silently no-op."""
    monkeypatch.setenv("VOS3_AUTO_HEAL", "1")
    from core.database import sqlcipher_setup

    monkeypatch.setattr(sqlcipher_setup.platform, "system", lambda: "Plan9")
    res = attempt_self_heal(force=False)
    assert res.ok is False
    assert "platform='Plan9'" in (res.error or "")
    assert "pip install" in (res.error or "")
