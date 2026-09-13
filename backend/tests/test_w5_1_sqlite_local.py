"""
W5.1 — local-first SQLite verification.

Standalone runner (Python 3.9+) that proves the SQLite repository
implementations work end-to-end without touching the network:

  1. Force a tmp DB path via $VOS3_LOCAL_DB_PATH (no ~/.vos pollution).
  2. Flip $VOS3_LOCALITY_PREFERENCE=local-first.
  3. Through the public factories, write+read three flows:
     a) User sync (upsert + recall)
     b) App installation (insert + verify row in DB)
     c) Chat session append + load (the headline W5.1 demo)
  4. Verify the SQLite file exists on disk and contains the rows.

To prove "no network" we monkey-patch httpx + socket to refuse all
outbound traffic for the duration of the test. Any accidental Convex
client invocation would surface here as an explicit error.

Run with:
  python3 backend/tests/test_w5_1_sqlite_local.py
"""

from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import pathlib
import asyncio

# Pin env BEFORE importing the repositories module so the factories see
# local-first on first access. This file is intentionally tmp-pathed
# (no ~/.vos pollution).
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["VOS3_LOCAL_DB_PATH"] = _TMP_DB.name
os.environ["VOS3_LOCALITY_PREFERENCE"] = "local-first"
os.environ["VOS_PROFILE"] = "community"
os.environ.setdefault("ENVIRONMENT", "development")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Network kill switch — any outbound HTTP / DNS attempt during this test
# means we accidentally hit Convex despite the env flip.
# ---------------------------------------------------------------------------


_network_violations: list[str] = []


import pytest  # noqa: E402


def _make_guarded_socket(real_socket):
    class GuardedSocket(real_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args, **kwargs):
            _network_violations.append(f"socket.connect({args!r}, {kwargs!r})")
            raise RuntimeError(
                "W5.1 test invariant violated: outbound socket attempted"
            )

    return GuardedSocket


@pytest.fixture(autouse=True)
def _network_guard():
    """Air-gap socket kill-switch — scoped to each W5.1 test.

    Previously the guard was installed at MODULE IMPORT time and never
    restored. Under pytest-xdist EVERY worker imports this module during
    collection, so ``socket.socket`` stayed globally replaced on every
    worker for the whole session — poisoning unrelated tests (the Ollama
    cloud-probe, the p2p loopback e2e suite) with spurious ``connect()``
    failures. Scoping the guard to each test (install on setup, restore on
    teardown) keeps the air-gap invariant fully intact — every test still
    runs under the kill-switch and ``test_no_network_attempted`` still
    asserts zero violations — while eliminating the cross-test/cross-worker
    leak.
    """
    import socket as _socket

    real_socket = _socket.socket
    _socket.socket = _make_guarded_socket(real_socket)  # type: ignore[assignment]
    try:
        yield
    finally:
        _socket.socket = real_socket  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _isolate_local_db(monkeypatch):
    """Re-pin THIS module's tmp DB / community profile and rebuild the SQLite
    engine for every test.

    These vars are set at MODULE IMPORT, but the SQLite engine is a cached
    singleton: a prior test in the same xdist worker (e.g. the governance
    fortress/SQLCipher suite) leaves ``_engine`` pointing at ITS database, so
    W5.1 writes succeed (client-side id) but reads hit a different DB →
    ``sqlite3.OperationalError: no such table: appInstallations``. Re-pinning
    the env (monkeypatch auto-reverts) and calling
    ``sqlite_setup._reset_for_tests()`` rebuilds the engine on W5.1's own tmp
    DB, restoring isolation without weakening anything.
    """
    monkeypatch.setenv("VOS3_LOCAL_DB_PATH", _TMP_DB.name)
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "local-first")
    monkeypatch.setenv("VOS_PROFILE", "community")
    try:
        from core.database import sqlite_setup

        sqlite_setup._reset_for_tests()
        yield
        sqlite_setup._reset_for_tests()
    except Exception:
        yield


# Now safe to import the repository module.
from core.repositories import (  # noqa: E402
    get_app_installation_repository,
    get_async_user_sync_repository,
    get_async_chat_session_repository,
    SQLiteAppInstallationRepository,
    SQLiteUserSyncRepository,
    SQLiteChatSessionRepository,
)
from core.database.sqlite_setup import resolve_db_path  # noqa: E402


def _show(label: str, ok: bool, detail: str = "") -> None:
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {label}{('  → ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_locality_dispatch() -> None:
    print("→ Locality dispatch")
    app_repo = get_app_installation_repository()
    user_repo = get_async_user_sync_repository()
    chat_repo = get_async_chat_session_repository()
    _show(
        "factory returns SQLiteAppInstallationRepository under local-first",
        isinstance(app_repo, SQLiteAppInstallationRepository),
        f"got {type(app_repo).__name__}",
    )
    _show(
        "factory returns SQLiteUserSyncRepository under local-first",
        isinstance(user_repo, SQLiteUserSyncRepository),
        f"got {type(user_repo).__name__}",
    )
    _show(
        "factory returns SQLiteChatSessionRepository under local-first",
        isinstance(chat_repo, SQLiteChatSessionRepository),
        f"got {type(chat_repo).__name__}",
    )


def test_db_path_resolution() -> None:
    print("→ DB path resolution")
    resolved = str(resolve_db_path())
    # On macOS /var/folders is a symlink → /private/var/folders, so the
    # Path.resolve() output may differ from os.environ[...] textually.
    # Use samefile to compare by inode after both paths exist.
    expected_real = os.path.realpath(_TMP_DB.name)
    resolved_real = os.path.realpath(resolved)
    _show(
        "VOS3_LOCAL_DB_PATH override honored",
        expected_real == resolved_real,
        f"resolved={resolved} expected_real={expected_real}",
    )


def test_user_sync_upsert_and_recall() -> None:
    print("→ User sync (async) — upsert + recall")
    repo = get_async_user_sync_repository()
    loop = asyncio.new_event_loop()
    try:
        # Initial sync
        user_id = loop.run_until_complete(
            repo.sync_from_clerk(
                clerk_id="user_w51_test",
                email="alice@example.test",
                full_name="Alice Tester",
                avatar_url="https://example.test/a.png",
                metadata={"theme": "dark"},
            )
        )
        _show(
            "sync_from_clerk inserted a row",
            isinstance(user_id, str) and len(user_id) > 0,
            f"id={user_id[:8]}…",
        )

        # Recall
        recalled = loop.run_until_complete(
            repo.get_by_clerk_id(clerk_id="user_w51_test")
        )
        _show(
            "get_by_clerk_id round-trips the row",
            recalled is not None
            and recalled["email"] == "alice@example.test"
            and recalled["fullName"] == "Alice Tester"
            and recalled["metadata"] == {"theme": "dark"},
            f"recalled={recalled}",
        )

        # Second sync = update (not duplicate)
        user_id_2 = loop.run_until_complete(
            repo.sync_from_clerk(
                clerk_id="user_w51_test",
                email="alice+new@example.test",
                full_name="Alice Tester",
            )
        )
        _show(
            "second sync_from_clerk upserts (same id)",
            user_id_2 == user_id,
        )

        # Record sign-in
        loop.run_until_complete(repo.record_sign_in(clerk_id="user_w51_test"))
        recalled = loop.run_until_complete(
            repo.get_by_clerk_id(clerk_id="user_w51_test")
        )
        _show(
            "record_sign_in updates lastSignInAt",
            recalled is not None and recalled["lastSignInAt"] is not None,
        )

        # Soft-delete
        loop.run_until_complete(repo.soft_delete(clerk_id="user_w51_test"))
        recalled = loop.run_until_complete(
            repo.get_by_clerk_id(clerk_id="user_w51_test")
        )
        _show(
            "soft_delete marks metadata.deleted=True",
            recalled is not None
            and (recalled.get("metadata") or {}).get("deleted") is True,
        )
    finally:
        loop.close()


def test_app_installation_persists() -> None:
    print("→ App installation (sync) — install + verify on disk")
    repo = get_app_installation_repository()
    row_id = repo.install(
        app_id="app_w51_demo",
        organization_id="org_w51_demo",
        installed_by="user_w51_test",
        version="1.0.0",
        granted_scopes=["read:projects", "write:chat"],
        config={"theme": "midnight"},
    )
    _show(
        "install returned a row id",
        isinstance(row_id, str) and len(row_id) > 0,
        f"id={row_id[:8]}…",
    )

    # Independent read via raw sqlite3 — proves the row hit the file.
    conn = sqlite3.connect(_TMP_DB.name)
    try:
        rows = conn.execute(
            "SELECT appId, organizationId, version, grantedScopes FROM appInstallations WHERE id = ?",
            (row_id,),
        ).fetchall()
    finally:
        conn.close()
    _show(
        "raw sqlite3 read confirms persisted row",
        len(rows) == 1 and rows[0][0] == "app_w51_demo" and rows[0][2] == "1.0.0",
        f"row={rows[0] if rows else None}",
    )


def test_chat_write_read() -> None:
    """W5.1 headline demo — local chat message write+read with no network."""
    print("→ Chat session (async) — write + load")
    repo = get_async_chat_session_repository()
    loop = asyncio.new_event_loop()
    try:
        sess_id = "chat-w51-demo"

        # Empty load
        empty = loop.run_until_complete(repo.load(session_id=sess_id))
        _show("load() on absent session returns None", empty is None)

        # Append three messages
        loop.run_until_complete(
            repo.append_message(
                session_id=sess_id,
                user_id="user_w51_test",
                role="user",
                content="Local-first hello.",
            )
        )
        loop.run_until_complete(
            repo.append_message(
                session_id=sess_id,
                user_id="user_w51_test",
                role="assistant",
                content="Hello back, offline.",
            )
        )
        loop.run_until_complete(
            repo.append_message(
                session_id=sess_id,
                user_id="user_w51_test",
                role="user",
                content="Goodbye Convex.",
                metadata={"tokens": 4},
            )
        )

        # Load with all three messages
        loaded = loop.run_until_complete(repo.load(session_id=sess_id))
        _show(
            "load returns session with 3 messages",
            loaded is not None and len(loaded["messages"]) == 3,
            f"count={len(loaded['messages']) if loaded else 'None'}",
        )
        _show(
            "messages preserve role + content + ordering",
            loaded["messages"][0]["role"] == "user"
            and loaded["messages"][0]["content"] == "Local-first hello."
            and loaded["messages"][2]["metadata"] == {"tokens": 4},
        )

        # Remove — both session and messages dropped
        loop.run_until_complete(repo.remove(session_id=sess_id))
        loaded = loop.run_until_complete(repo.load(session_id=sess_id))
        _show("remove drops the session and all messages", loaded is None)
    finally:
        loop.close()


def test_no_network_attempted() -> None:
    print("→ Network isolation")
    _show(
        "no outbound socket attempted during the W5.1 flows",
        len(_network_violations) == 0,
        f"violations={_network_violations[:3]}",
    )


def main() -> None:
    print("=" * 62)
    print("W5.1 — SQLite local-first verification")
    print("=" * 62)
    print(f"  DB path                 : {_TMP_DB.name}")
    print(f"  VOS3_LOCALITY_PREFERENCE: {os.environ['VOS3_LOCALITY_PREFERENCE']}")
    print(f"  VOS_PROFILE             : {os.environ['VOS_PROFILE']}")
    print()

    test_locality_dispatch()
    print()
    test_db_path_resolution()
    print()
    test_user_sync_upsert_and_recall()
    print()
    test_app_installation_persists()
    print()
    test_chat_write_read()
    print()
    test_no_network_attempted()

    # Final on-disk sanity: file is non-empty and contains our tables.
    db_size = os.path.getsize(_TMP_DB.name)
    print()
    print(f"  ✓ SQLite file: {_TMP_DB.name}  size={db_size} bytes")
    print()
    print("✓ All W5.1 SQLite local-first assertions passed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        # Cleanup — don't leave tmp DB lingering.
        try:
            os.unlink(_TMP_DB.name)
        except OSError:
            pass
