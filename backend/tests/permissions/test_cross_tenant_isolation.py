"""
Cross-tenant data leakage — workspace_id isolation.

Two installed apps with different workspace_ids must not see each
other's rows when sync surfaces (compute_table_delta, compute_table_summary)
are queried. Additionally, the gate must enforce per-app scope
state — clearing gate state for app A must not perturb app B.
"""

from __future__ import annotations

import pytest

from services.app_sandbox import (
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    ScopeViolation,
)
from services.p2p_sync import compute_table_delta, compute_table_summary
from core.database.sqlite_setup import Project, get_session


def _install(scopes, *, workspace_id, name="x"):
    res = SANDBOX_MANAGER.install(
        {"name": name, "version": "1.0", "scopes": list(scopes)},
        workspace_id=workspace_id,
    )
    return res["app_id"]


def _insert_project(*, app_id_hint: str, workspace_id: str, ts: int = 1):
    with get_session() as s:
        s.add(
            Project(
                id=f"proj_{app_id_hint}",
                name=f"P-{app_id_hint}",
                ownerId="u1",
                organizationId=workspace_id,
                createdAt=ts,
                updatedAt=ts,
                dirty=False,
            )
        )
        s.commit()


# ---------------------------------------------------------------------------
# Workspace-scoped delta — no rows from other workspaces in either direction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ws_a,ws_b",
    [
        ("ws-A", "ws-B"),
        ("acme", "globex"),
        ("tenant-1", "tenant-2"),
        ("ws-alpha", "ws-beta"),
        ("00000000", "ffffffff"),
    ],
)
def test_workspaces_dont_see_each_others_projects(perms_env, ws_a, ws_b):
    _insert_project(app_id_hint=ws_a, workspace_id=ws_a)
    _insert_project(app_id_hint=ws_b, workspace_id=ws_b)
    delta_a = compute_table_delta("projects", workspace_id=ws_a, since_ms=0)
    delta_b = compute_table_delta("projects", workspace_id=ws_b, since_ms=0)
    ids_a = {r["id"] for r in delta_a}
    ids_b = {r["id"] for r in delta_b}
    assert ids_a == {f"proj_{ws_a}"}
    assert ids_b == {f"proj_{ws_b}"}
    assert ids_a.isdisjoint(ids_b)


@pytest.mark.parametrize(
    "ws_a,ws_b",
    [
        ("ws-A", "ws-B"),
        ("acme", "globex"),
    ],
)
def test_workspace_summary_hashes_diverge(perms_env, ws_a, ws_b):
    _insert_project(app_id_hint=ws_a, workspace_id=ws_a)
    _insert_project(app_id_hint=ws_b, workspace_id=ws_b)
    sa = compute_table_summary("projects", workspace_id=ws_a)
    sb = compute_table_summary("projects", workspace_id=ws_b)
    assert sa["row_count"] == 1
    assert sb["row_count"] == 1
    assert sa["hash"] != sb["hash"]


@pytest.mark.parametrize(
    "rogue_ws",
    [
        "",
        "ws-nonexistent",
        "../../etc/passwd",
        "ws-A; DROP TABLE projects",
        "\x00ws-A",
        "ws-A\x00ws-B",
    ],
)
def test_rogue_workspace_id_yields_empty(perms_env, rogue_ws):
    _insert_project(app_id_hint="legit", workspace_id="ws-legit")
    out = compute_table_delta("projects", workspace_id=rogue_ws, since_ms=0)
    assert out == []


# ---------------------------------------------------------------------------
# Gate isolation — app A's clear() must not perturb app B's cache
# ---------------------------------------------------------------------------


def test_clear_one_app_doesnt_affect_other(perms_env):
    a = _install(["filesystem.read"], workspace_id="ws-A", name="appA")
    b = _install(["filesystem.write"], workspace_id="ws-B", name="appB")
    PERMISSION_GATE.check(a, "filesystem.read")
    PERMISSION_GATE.check(b, "filesystem.write")
    assert a in PERMISSION_GATE._entries
    assert b in PERMISSION_GATE._entries
    PERMISSION_GATE._clear(a)
    assert a not in PERMISSION_GATE._entries
    assert b in PERMISSION_GATE._entries  # B untouched


def test_app_a_scope_does_not_grant_app_b(perms_env):
    a = _install(["filesystem.read", "filesystem.write"], workspace_id="ws-A")
    b = _install(["filesystem.read"], workspace_id="ws-B")
    PERMISSION_GATE.check(a, "filesystem.write")
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(b, "filesystem.write")


@pytest.mark.parametrize(
    "scope_a,scope_b,probe",
    [
        ("filesystem.read", "filesystem.write", "filesystem.write"),
        ("llm.cloud", "llm.local", "llm.cloud"),
        ("network.outbound", "kernel.audit", "kernel.audit"),
        ("filesystem", "llm", "llm.cloud"),
    ],
)
def test_app_scope_does_not_cross_pollinate(perms_env, scope_a, scope_b, probe):
    a = _install([scope_a], workspace_id="ws-A")
    _install([scope_b], workspace_id="ws-B")
    if scope_a == probe or probe.startswith(scope_a + "."):
        assert PERMISSION_GATE.check(a, probe) is True
    else:
        with pytest.raises(ScopeViolation):
            PERMISSION_GATE.check(a, probe)


# ---------------------------------------------------------------------------
# App-id uniqueness — every install gets its own opaque id
# ---------------------------------------------------------------------------


def test_install_produces_unique_app_ids_per_workspace(perms_env):
    a = _install(["filesystem.read"], workspace_id="ws-A")
    b = _install(["filesystem.read"], workspace_id="ws-B")
    c = _install(["filesystem.read"], workspace_id="ws-A")
    assert len({a, b, c}) == 3


def test_install_returns_app_id_string(perms_env):
    res = SANDBOX_MANAGER.install(
        {"name": "z", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-perms",
    )
    assert isinstance(res["app_id"], str)
    assert len(res["app_id"]) > 0
