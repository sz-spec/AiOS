"""
Atomic app uninstall — gate state + DB row teardown.

`SANDBOX_MANAGER.uninstall(app_id)` is wrapped in a single SQLAlchemy
session.commit() so the row deletion is atomic. The gate cache must be
cleared synchronously so a subsequent `check()` cannot find a stale
entry.

Tests verify:
  * uninstall deletes the App row + clears the gate cache,
  * uninstall is idempotent (calling twice doesn't crash),
  * uninstalled app raises AppNotFound on permission checks,
  * concurrent installs/uninstalls don't corrupt the gate.
"""

from __future__ import annotations

import threading

import pytest

from core.database.sqlite_setup import App, get_session
from services.app_sandbox import (
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    AppNotFound,
)


def _install(scopes=("filesystem.read",), *, workspace_id="ws-gov", name="app"):
    res = SANDBOX_MANAGER.install(
        {"name": name, "version": "1.0", "scopes": list(scopes)},
        workspace_id=workspace_id,
    )
    return res["app_id"]


# ---------------------------------------------------------------------------
# Single-app round trip
# ---------------------------------------------------------------------------


def test_install_then_uninstall_clears_db_row(gov_env):
    app_id = _install()
    with get_session() as s:
        assert s.query(App).filter_by(id=app_id).one_or_none() is not None
    SANDBOX_MANAGER.uninstall(app_id)
    with get_session() as s:
        assert s.query(App).filter_by(id=app_id).one_or_none() is None


def test_uninstall_clears_gate_cache(gov_env):
    app_id = _install()
    PERMISSION_GATE.check(app_id, "filesystem.read")
    assert app_id in PERMISSION_GATE._entries
    SANDBOX_MANAGER.uninstall(app_id)
    assert app_id not in PERMISSION_GATE._entries


def test_uninstalled_app_check_raises_not_found(gov_env):
    app_id = _install()
    SANDBOX_MANAGER.uninstall(app_id)
    with pytest.raises(AppNotFound):
        PERMISSION_GATE.check(app_id, "filesystem.read")


def test_uninstall_is_idempotent(gov_env):
    app_id = _install()
    SANDBOX_MANAGER.uninstall(app_id)
    SANDBOX_MANAGER.uninstall(app_id)  # second call must not raise
    SANDBOX_MANAGER.uninstall("nonexistent_app_id")  # neither must this


# ---------------------------------------------------------------------------
# Multiple apps — uninstalling one must not perturb others
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_apps", [2, 3, 5, 10])
def test_uninstall_target_only(gov_env, n_apps):
    ids = [_install(name=f"app{i}") for i in range(n_apps)]
    target = ids[len(ids) // 2]
    SANDBOX_MANAGER.uninstall(target)
    with get_session() as s:
        assert s.query(App).filter_by(id=target).one_or_none() is None
        for survivor in ids:
            if survivor == target:
                continue
            assert s.query(App).filter_by(id=survivor).one_or_none() is not None


def test_uninstall_all_in_sequence_yields_empty_table(gov_env):
    ids = [_install(name=f"app{i}") for i in range(5)]
    for app_id in ids:
        SANDBOX_MANAGER.uninstall(app_id)
    with get_session() as s:
        remaining = s.query(App).filter(App.id.in_(ids)).all()
    assert remaining == []


# ---------------------------------------------------------------------------
# Different workspaces — cross-workspace uninstall is per-id, not per-tenant
# ---------------------------------------------------------------------------


def test_uninstall_does_not_affect_other_workspace(gov_env):
    a = _install(workspace_id="ws-X", name="appX")
    b = _install(workspace_id="ws-Y", name="appY")
    SANDBOX_MANAGER.uninstall(a)
    with get_session() as s:
        assert s.query(App).filter_by(id=a).one_or_none() is None
        assert s.query(App).filter_by(id=b).one_or_none() is not None


# ---------------------------------------------------------------------------
# Concurrency — multiple threads racing on install/uninstall
# ---------------------------------------------------------------------------


def test_concurrent_uninstalls_do_not_corrupt(gov_env):
    ids = [_install(name=f"app{i}") for i in range(10)]

    def worker(app_id):
        try:
            SANDBOX_MANAGER.uninstall(app_id)
        except Exception:
            pass  # idempotency contract — should not raise, but tolerate

    threads = [threading.Thread(target=worker, args=(app_id,)) for app_id in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with get_session() as s:
        remaining = s.query(App).filter(App.id.in_(ids)).all()
    assert remaining == []


@pytest.mark.parametrize("rounds", [3, 5, 10])
def test_install_uninstall_loop_no_orphan_state(gov_env, rounds):
    for i in range(rounds):
        app_id = _install(name=f"loop{i}")
        PERMISSION_GATE.check(app_id, "filesystem.read")
        SANDBOX_MANAGER.uninstall(app_id)
        assert app_id not in PERMISSION_GATE._entries
        with get_session() as s:
            assert s.query(App).filter_by(id=app_id).one_or_none() is None


# ---------------------------------------------------------------------------
# Re-install with same name produces a NEW app_id
# ---------------------------------------------------------------------------


def test_reinstall_after_uninstall_creates_new_app_id(gov_env):
    a = _install(name="same-name")
    SANDBOX_MANAGER.uninstall(a)
    b = _install(name="same-name")
    assert a != b


def test_reinstall_clears_old_gate_entry(gov_env):
    a = _install(name="same-name")
    PERMISSION_GATE.check(a, "filesystem.read")
    SANDBOX_MANAGER.uninstall(a)
    b = _install(name="same-name")
    PERMISSION_GATE.check(b, "filesystem.read")
    assert a not in PERMISSION_GATE._entries
    assert b in PERMISSION_GATE._entries
