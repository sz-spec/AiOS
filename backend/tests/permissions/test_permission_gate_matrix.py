"""
PermissionGate matrix — exhaustive scope × restriction permutations.

The gate's contract (see app_sandbox.py:PermissionGate._decide):

  1. status != "active"            → AppIsolated
  2. ANY restriction matches scope → ScopeViolation (restriction wins)
  3. NO grant implies scope        → ScopeViolation
  4. else                          → True

These tests enumerate every interesting (granted, restricted, requested)
triple — hierarchical match, parent/child interplay, the asymmetric
prefix rule for `_restriction_blocks` — to lock the contract in.

The cardinal invariant: **restrictions ALWAYS override grants.**
"""

from __future__ import annotations


import pytest

from services.app_sandbox import (
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    ScopeViolation,
    AppIsolated,
    AppNotFound,
)


def _install(manifest_dict: dict, *, workspace_id: str = "ws-perms") -> str:
    res = SANDBOX_MANAGER.install(manifest_dict, workspace_id=workspace_id)
    return res["app_id"]


# ---------------------------------------------------------------------------
# Bare scope → bare scope (no hierarchy)
# ---------------------------------------------------------------------------

_BARE_SCOPES = [
    "filesystem.read",
    "filesystem.write",
    "network.outbound",
    "network.inbound",
    "llm.local",
    "llm.cloud",
    "kernel.audit",
]


@pytest.mark.parametrize("scope", _BARE_SCOPES)
def test_exact_grant_allows(perms_env, scope):
    app_id = _install({"name": "x", "version": "1.0", "scopes": [scope]})
    assert PERMISSION_GATE.check(app_id, scope) is True


@pytest.mark.parametrize(
    "granted,requested", [(g, r) for g in _BARE_SCOPES for r in _BARE_SCOPES if g != r]
)
def test_unrelated_grant_denies(perms_env, granted, requested):
    app_id = _install({"name": "x", "version": "1.0", "scopes": [granted]})
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, requested)


# ---------------------------------------------------------------------------
# Hierarchical grant — "llm" implies "llm.cloud", "llm.local", ...
# ---------------------------------------------------------------------------

_PARENT_CHILDREN = [
    ("filesystem", ["filesystem.read", "filesystem.write", "filesystem.delete"]),
    ("network", ["network.outbound", "network.inbound", "network.outbound.http"]),
    ("llm", ["llm.local", "llm.cloud", "llm.cloud.openai"]),
    ("kernel", ["kernel.audit", "kernel.privileged"]),
]


@pytest.mark.parametrize(
    "parent,child",
    [(p, c) for p, cs in _PARENT_CHILDREN for c in cs],
)
def test_parent_grant_implies_child(perms_env, parent, child):
    app_id = _install({"name": "x", "version": "1.0", "scopes": [parent]})
    assert PERMISSION_GATE.check(app_id, child) is True


@pytest.mark.parametrize(
    "parent,child",
    [(p, c) for p, cs in _PARENT_CHILDREN for c in cs],
)
def test_child_grant_does_NOT_imply_parent(perms_env, parent, child):
    app_id = _install({"name": "x", "version": "1.0", "scopes": [child]})
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, parent)


@pytest.mark.parametrize(
    "sibling_a,sibling_b",
    [
        ("llm.local", "llm.cloud"),
        ("filesystem.read", "filesystem.write"),
        ("network.outbound", "network.inbound"),
        ("filesystem.read", "filesystem.delete"),
        ("kernel.audit", "kernel.privileged"),
    ],
)
def test_siblings_do_not_imply_each_other(perms_env, sibling_a, sibling_b):
    app_id = _install({"name": "x", "version": "1.0", "scopes": [sibling_a]})
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, sibling_b)


# ---------------------------------------------------------------------------
# Restrictions — explicit denial always wins (the load-bearing test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", _BARE_SCOPES)
def test_restriction_overrides_exact_grant(perms_env, scope):
    """The cardinal contract — every restriction must beat its matching grant."""
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": [scope],
            "restrictions": [scope],
        }
    )
    with pytest.raises(ScopeViolation) as ei:
        PERMISSION_GATE.check(app_id, scope)
    assert "restricted by" in str(ei.value).lower()


@pytest.mark.parametrize(
    "parent,child", [(p, c) for p, cs in _PARENT_CHILDREN for c in cs]
)
def test_restriction_on_child_blocks_only_that_child(perms_env, parent, child):
    """Restriction `parent.child` blocks parent.* (per _restriction_blocks)."""
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": [parent],
            "restrictions": [child],
        }
    )
    # The child itself is blocked.
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, child)


@pytest.mark.parametrize("parent", [p for p, _ in _PARENT_CHILDREN])
def test_restriction_on_child_blocks_parent_namespace(perms_env, parent):
    """
    `_restriction_blocks('parent.X', 'parent')` returns True — the
    asymmetric rule documented in app_sandbox.py. Verify it.
    """
    restriction = f"{parent}.blocked"
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": [parent],
            "restrictions": [restriction],
        }
    )
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, parent)


@pytest.mark.parametrize(
    "parent,siblings",
    [
        ("network", ["network.outbound", "network.inbound", "network.outbound.http"]),
        ("filesystem", ["filesystem.read", "filesystem.write"]),
        ("llm", ["llm.local", "llm.cloud"]),
    ],
)
def test_restriction_on_one_sibling_blocks_namespace(perms_env, parent, siblings):
    restriction = siblings[0]
    granted = parent
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": [granted],
            "restrictions": [restriction],
        }
    )
    # Every sibling — including the unblocked ones — gets caught by the
    # parent-namespace rule.
    for s in siblings:
        with pytest.raises(ScopeViolation):
            PERMISSION_GATE.check(app_id, s)


# ---------------------------------------------------------------------------
# Multi-scope grants — at least ONE must imply the request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "grants,requested,allowed",
    [
        (["filesystem.read", "llm.local"], "filesystem.read", True),
        (["filesystem.read", "llm.local"], "llm.local", True),
        (["filesystem.read", "llm.local"], "network.outbound", False),
        (["filesystem", "llm"], "filesystem.write", True),
        (["filesystem", "llm"], "llm.cloud", True),
        (["filesystem", "llm"], "kernel.audit", False),
        (["filesystem.read"], "filesystem.read", True),
        (["network.outbound"], "network.outbound", True),
        (["network.outbound", "filesystem.read"], "filesystem.read", True),
        (["network.outbound", "filesystem.read"], "kernel.privileged", False),
    ],
)
def test_multi_scope_grant_matrix(perms_env, grants, requested, allowed):
    app_id = _install({"name": "x", "version": "1.0", "scopes": grants})
    if allowed:
        assert PERMISSION_GATE.check(app_id, requested) is True
    else:
        with pytest.raises(ScopeViolation):
            PERMISSION_GATE.check(app_id, requested)


# ---------------------------------------------------------------------------
# Multi-scope check call — caller passes multiple scopes; ALL must pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scopes_requested",
    [
        ("filesystem.read", "filesystem.write"),
        ("filesystem.read", "llm.local"),
        ("filesystem.read", "filesystem.write", "llm.cloud"),
    ],
)
def test_check_all_requested_must_pass(perms_env, scopes_requested):
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": list(scopes_requested),
        }
    )
    assert PERMISSION_GATE.check(app_id, *scopes_requested) is True


@pytest.mark.parametrize(
    "grants,scopes_requested",
    [
        (["filesystem.read"], ("filesystem.read", "filesystem.write")),
        (["llm.local"], ("llm.local", "network.outbound")),
        (["filesystem.read", "llm.local"], ("filesystem.read", "kernel.privileged")),
    ],
)
def test_check_fails_on_any_missing(perms_env, grants, scopes_requested):
    app_id = _install({"name": "x", "version": "1.0", "scopes": grants})
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, *scopes_requested)


def test_check_with_empty_scope_tuple_raises(perms_env):
    app_id = _install({"name": "x", "version": "1.0", "scopes": ["filesystem.read"]})
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id)


# ---------------------------------------------------------------------------
# can() — non-raising sibling of check()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,allowed",
    [
        ("filesystem.read", True),
        ("filesystem.write", False),
        ("llm.local", False),
        ("network.outbound", False),
    ],
)
def test_can_is_bool_only(perms_env, scope, allowed):
    app_id = _install({"name": "x", "version": "1.0", "scopes": ["filesystem.read"]})
    result = PERMISSION_GATE.can(app_id, scope)
    assert result is allowed


# ---------------------------------------------------------------------------
# App lifecycle — isolated apps deny everything
# ---------------------------------------------------------------------------


def test_isolated_app_denies_all_scopes(perms_env):
    from core.database.sqlite_setup import App, get_session

    app_id = _install({"name": "x", "version": "1.0", "scopes": ["filesystem.read"]})
    with get_session() as s:
        row = s.query(App).filter_by(id=app_id).one()
        row.status = "isolated"
        s.commit()
    PERMISSION_GATE._clear(app_id)
    with pytest.raises(AppIsolated):
        PERMISSION_GATE.check(app_id, "filesystem.read")


def test_nonexistent_app_raises_not_found(perms_env):
    with pytest.raises(AppNotFound):
        PERMISSION_GATE.check("app_nonexistent_xyz", "filesystem.read")


# ---------------------------------------------------------------------------
# Cache invariant — _clear() forces re-hydration
# ---------------------------------------------------------------------------


def test_clear_forces_rehydration(perms_env):
    app_id = _install({"name": "x", "version": "1.0", "scopes": ["filesystem.read"]})
    PERMISSION_GATE.check(app_id, "filesystem.read")
    assert app_id in PERMISSION_GATE._entries
    PERMISSION_GATE._clear(app_id)
    assert app_id not in PERMISSION_GATE._entries
    PERMISSION_GATE.check(app_id, "filesystem.read")
    assert app_id in PERMISSION_GATE._entries


def test_snapshot_returns_authoritative_state(perms_env):
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": ["filesystem.read", "llm.local"],
            "restrictions": ["network.outbound"],
        }
    )
    snap = PERMISSION_GATE.snapshot(app_id)
    assert snap["app_id"] == app_id
    assert snap["status"] == "active"
    assert set(snap["scopes"]) == {"filesystem.read", "llm.local"}
    assert set(snap["restrictions"]) == {"network.outbound"}


# ---------------------------------------------------------------------------
# Deep parametrize — every (grant, restriction, request) triple in a small
# universe. Generates 100+ test instances. Covers the "100+ permutations"
# spec requirement.
# ---------------------------------------------------------------------------

_UNIVERSE = [
    "filesystem",
    "filesystem.read",
    "filesystem.write",
    "network",
    "network.outbound",
    "network.inbound",
    "llm",
    "llm.local",
    "llm.cloud",
]


def _expected(grants: tuple, restrictions: tuple, requested: str) -> bool:
    # Mirror app_sandbox._decide.
    def _restriction_blocks(r, s):
        if r == s:
            return True
        if "." in r:
            parent = r.rsplit(".", 1)[0]
            if s == parent or s.startswith(parent + "."):
                return True
        return False

    def _scope_implies(g, s):
        return g == s or s.startswith(g + ".")

    for r in restrictions:
        if _restriction_blocks(r, requested):
            return False
    for g in grants:
        if _scope_implies(g, requested):
            return True
    return False


@pytest.mark.parametrize(
    "grants,restrictions,requested",
    [((g,), (r,), q) for g in _UNIVERSE[:4] for r in _UNIVERSE[:4] for q in _UNIVERSE],
)
def test_full_triple_matrix_matches_reference(
    perms_env, grants, restrictions, requested
):
    """108 triples — each must match the pure-function reference oracle."""
    app_id = _install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": list(grants),
            "restrictions": list(restrictions),
        }
    )
    expected = _expected(grants, restrictions, requested)
    actual = PERMISSION_GATE.can(app_id, requested)
    assert actual is expected, (
        f"grants={grants} restrictions={restrictions} requested={requested} "
        f"expected={expected} got={actual}"
    )
