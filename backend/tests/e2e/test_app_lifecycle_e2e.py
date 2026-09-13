"""
Stage 5 · App lifecycle E2E.

install → permission-check → file resolve → isolate → reactivate
and every cross-step audit row written along the way.
"""

from __future__ import annotations

import pytest


def test_full_install_check_isolate_reactivate(e2e_env):
    """Install an app, exercise the gate, isolate, reactivate."""
    from services.app_sandbox import (
        AppIsolated,
        PERMISSION_GATE,
        SANDBOX_MANAGER,
        ScopeViolation,
    )

    res = SANDBOX_MANAGER.install(
        {
            "name": "lifecycle",
            "version": "1.0",
            "scopes": ["filesystem.read", "llm.local"],
        },
        workspace_id="ws-lifecycle",
    )
    app_id = res["app_id"]
    # Step 1: gate accepts in-scope check.
    assert PERMISSION_GATE.check(app_id, "filesystem.read") is True
    # Step 2: gate rejects out-of-scope.
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, "host.automation")
    # Step 3: isolate.
    SANDBOX_MANAGER.isolate(app_id, reason="lifecycle-test")
    assert SANDBOX_MANAGER.get(app_id)["status"] == "isolated"
    # Step 4: gate refuses after isolation.
    with pytest.raises(AppIsolated):
        PERMISSION_GATE.check(app_id, "filesystem.read")
    # Step 5: reactivate.
    SANDBOX_MANAGER.reactivate(app_id)
    assert SANDBOX_MANAGER.get(app_id)["status"] == "active"
    # Step 6: gate works again.
    assert PERMISSION_GATE.check(app_id, "filesystem.read") is True


def test_install_with_restrictions_blocks_explicitly(e2e_env):
    """Install with restrictions=['network.blocked'] → all network.* denied."""
    from services.app_sandbox import (
        PERMISSION_GATE,
        SANDBOX_MANAGER,
        ScopeViolation,
    )

    res = SANDBOX_MANAGER.install(
        {
            "name": "restricted",
            "version": "1.0",
            "scopes": ["network"],
            "restrictions": ["network.blocked"],
        },
        workspace_id="ws-r",
    )
    aid = res["app_id"]
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(aid, "network.outbound")
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(aid, "network.fetch")


def test_path_traversal_auto_isolates_then_recover(e2e_env):
    """A traversal attempt flips the app to isolated; reactivate restores."""
    from services.app_filesystem import (
        PathTraversalAttempt,
        _resolve_inside_sandbox,
    )
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "traversal", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-t",
    )
    aid = res["app_id"]
    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(aid, "../../etc/passwd")
    assert SANDBOX_MANAGER.get(aid)["status"] == "isolated"
    SANDBOX_MANAGER.reactivate(aid)
    assert SANDBOX_MANAGER.get(aid)["status"] == "active"


def test_install_persists_across_gate_reset(e2e_env):
    """The PermissionGate hydrates from the DB after _reset_gate_for_tests."""
    from services.app_sandbox import (
        PERMISSION_GATE,
        SANDBOX_MANAGER,
        _reset_gate_for_tests,
    )

    res = SANDBOX_MANAGER.install(
        {"name": "persist", "version": "1.0", "scopes": ["llm.local"]},
        workspace_id="ws-p",
    )
    aid = res["app_id"]
    _reset_gate_for_tests()
    # After reset, the gate re-loads from DB → check still works.
    assert PERMISSION_GATE.check(aid, "llm.local") is True


def test_two_apps_audit_rows_distinct(e2e_env):
    """Two apps installed; audit rows track each correctly."""
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from services.app_filesystem import (
        PathTraversalAttempt,
        _resolve_inside_sandbox,
    )
    from services.app_sandbox import SANDBOX_MANAGER

    a1 = SANDBOX_MANAGER.install(
        {"name": "a", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-1",
    )["app_id"]
    a2 = SANDBOX_MANAGER.install(
        {"name": "b", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-2",
    )["app_id"]

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(a1, "../boom")
    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(a2, "../boom2")

    with get_session() as s:
        rows = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="path_traversal_attempt",
            )
            .all()
        )
        app_ids = {r.appId for r in rows}
        assert a1 in app_ids
        assert a2 in app_ids


def test_isolate_uninstall_lifecycle(e2e_env):
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "uninstall", "version": "1.0", "scopes": []},
        workspace_id="ws-u",
    )
    aid = res["app_id"]
    SANDBOX_MANAGER.isolate(aid)
    SANDBOX_MANAGER.uninstall(aid)
    # After uninstall, get returns None — or raises depending on impl.
    record = SANDBOX_MANAGER.get(aid)
    assert record is None


def test_install_with_no_scopes_succeeds(e2e_env):
    """Installing with empty scopes is legal (first-party dev mode)."""
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "no-scopes", "version": "1.0", "scopes": []},
        workspace_id="ws-empty",
    )
    aid = res["app_id"]
    assert SANDBOX_MANAGER.get(aid)["status"] == "active"


def test_install_returns_secret_unique(e2e_env):
    """Each install mints a fresh secret."""
    from services.app_sandbox import SANDBOX_MANAGER

    r1 = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": []},
        workspace_id="ws-sec",
    )
    r2 = SANDBOX_MANAGER.install(
        {"name": "y", "version": "1.0", "scopes": []},
        workspace_id="ws-sec",
    )
    assert r1["secret"] != r2["secret"]


def test_install_workspace_isolation_full(e2e_env):
    """Sandbox roots, app IDs, secrets — all unique across workspaces."""
    from services.app_sandbox import SANDBOX_MANAGER, app_storage_root

    r1 = SANDBOX_MANAGER.install(
        {"name": "same-name", "version": "1.0", "scopes": []},
        workspace_id="ws-A",
    )
    r2 = SANDBOX_MANAGER.install(
        {"name": "same-name", "version": "1.0", "scopes": []},
        workspace_id="ws-B",
    )
    # Different app IDs even though name + version match.
    assert r1["app_id"] != r2["app_id"]
    assert r1["secret"] != r2["secret"]
    assert str(app_storage_root(r1["app_id"])) != str(app_storage_root(r2["app_id"]))


def test_manifest_signed_install_lifecycle(e2e_env):
    """Installation persists the manifest and version intact."""
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {
            "name": "signed",
            "version": "2.3.4",
            "scopes": ["llm.local", "filesystem.read"],
        },
        workspace_id="ws-sig",
    )
    record = SANDBOX_MANAGER.get(res["app_id"])
    assert record["version"] == "2.3.4"
    assert record["name"] == "signed"
    assert set(record["manifest"]["scopes"]) == {"llm.local", "filesystem.read"}


def test_listapps_filters_by_workspace(e2e_env):
    """list returns ALL apps; consumer filters by workspace."""
    from services.app_sandbox import SANDBOX_MANAGER

    for ws in ("ws-X", "ws-X", "ws-Y"):
        SANDBOX_MANAGER.install(
            {"name": "x", "version": "1.0", "scopes": []},
            workspace_id=ws,
        )
    all_apps = SANDBOX_MANAGER.list_apps()
    x_apps = [a for a in all_apps if a["workspace_id"] == "ws-X"]
    y_apps = [a for a in all_apps if a["workspace_id"] == "ws-Y"]
    assert len(x_apps) == 2
    assert len(y_apps) == 1


def test_isolate_idempotent(e2e_env):
    """Isolate twice doesn't double-write or raise."""
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "idem", "version": "1.0", "scopes": []},
        workspace_id="ws-i",
    )
    aid = res["app_id"]
    SANDBOX_MANAGER.isolate(aid)
    SANDBOX_MANAGER.isolate(aid)  # idempotent
    assert SANDBOX_MANAGER.get(aid)["status"] == "isolated"
