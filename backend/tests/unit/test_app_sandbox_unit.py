"""
Stage 1 · Atomic unit isolation for services/app_sandbox.py.

Covers — purpose | guards file:line:
  scope grammar (_scope_implies, _restriction_blocks)  | app_sandbox.py:368-390
  AppManifest.from_dict                                 | app_sandbox.py:393-465
  PermissionGate.check + cache eviction                | app_sandbox.py:495-570
  SANDBOX_MANAGER install/isolate/reactivate           | app_sandbox.py:641-880
"""

from __future__ import annotations

import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def sb_env(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    yield


# ---------------------------------------------------------------------------
# Scope grammar — hierarchical implies
# ---------------------------------------------------------------------------


def test_scope_implies_identity():
    from services.app_sandbox import _scope_implies

    assert _scope_implies("llm.local", "llm.local") is True


def test_scope_implies_hierarchical():
    from services.app_sandbox import _scope_implies

    assert _scope_implies("llm", "llm.cloud") is True
    assert _scope_implies("llm", "llm.local") is True


def test_scope_implies_not_reverse():
    from services.app_sandbox import _scope_implies

    # Narrow grant does NOT imply broad request.
    assert _scope_implies("llm.local", "llm.cloud") is False
    assert _scope_implies("llm.local", "llm") is False


def test_scope_implies_unrelated_namespaces():
    from services.app_sandbox import _scope_implies

    assert _scope_implies("llm", "filesystem.read") is False
    assert _scope_implies("filesystem.read", "filesystem.write") is False


def test_scope_implies_prefix_not_substring():
    """`granted='llm'` does NOT imply `'llmoasis.foo'` — it's a path
    prefix, not a string prefix."""
    from services.app_sandbox import _scope_implies

    assert _scope_implies("llm", "llmoasis.foo") is False


# ---------------------------------------------------------------------------
# Scope grammar — restrictions
# ---------------------------------------------------------------------------


def test_restriction_blocks_exact_match():
    from services.app_sandbox import _restriction_blocks

    assert _restriction_blocks("network.outbound", "network.outbound") is True


def test_restriction_blocks_parent_namespace():
    """restriction='network.blocked' blocks ANY 'network.*' request."""
    from services.app_sandbox import _restriction_blocks

    assert _restriction_blocks("network.blocked", "network.outbound") is True
    assert _restriction_blocks("network.blocked", "network.fetch") is True
    assert _restriction_blocks("network.blocked", "network") is True


def test_restriction_does_not_block_unrelated():
    from services.app_sandbox import _restriction_blocks

    assert _restriction_blocks("network.blocked", "filesystem.read") is False


# ---------------------------------------------------------------------------
# AppManifest.from_dict — strict shape enforcement
# ---------------------------------------------------------------------------


def test_manifest_rejects_missing_name():
    from services.app_sandbox import AppManifest, InvalidManifest

    with pytest.raises(InvalidManifest):
        AppManifest.from_dict({"version": "1.0", "scopes": []})


def test_manifest_rejects_missing_version():
    from services.app_sandbox import AppManifest, InvalidManifest

    with pytest.raises(InvalidManifest):
        AppManifest.from_dict({"name": "x", "scopes": []})


def test_manifest_rejects_empty_name():
    from services.app_sandbox import AppManifest, InvalidManifest

    with pytest.raises(InvalidManifest):
        AppManifest.from_dict({"name": "", "version": "1.0"})


def test_manifest_rejects_non_list_scopes():
    from services.app_sandbox import AppManifest, InvalidManifest

    with pytest.raises(InvalidManifest):
        AppManifest.from_dict({"name": "x", "version": "1.0", "scopes": "llm"})


def test_manifest_accepts_empty_scopes():
    """An app with NO scopes is legal (gets nothing); rejecting it
    would break first-party dev-mode installs."""
    from services.app_sandbox import AppManifest

    m = AppManifest.from_dict({"name": "x", "version": "1.0", "scopes": []})
    assert m.scopes == ()


def test_manifest_strips_whitespace_from_name():
    from services.app_sandbox import AppManifest

    m = AppManifest.from_dict({"name": "  hello  ", "version": "1.0"})
    assert m.name == "hello"


# ---------------------------------------------------------------------------
# SANDBOX_MANAGER install + lifecycle
# ---------------------------------------------------------------------------


def test_install_returns_app_id_and_secret(sb_env):
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": ["llm.local"]},
        workspace_id="ws",
    )
    assert isinstance(res["app_id"], str) and len(res["app_id"]) > 0
    # Default install mints a secret.
    assert isinstance(res["secret"], str)
    assert len(res["secret"]) > 0


def test_install_with_generate_secret_false_no_secret(sb_env):
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": []},
        workspace_id="ws",
        generate_secret=False,
    )
    assert res.get("secret") is None


def test_isolate_reactivate_cycle(sb_env):
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": []},
        workspace_id="ws",
    )
    SANDBOX_MANAGER.isolate(res["app_id"], reason="test")
    assert SANDBOX_MANAGER.get(res["app_id"])["status"] == "isolated"
    SANDBOX_MANAGER.reactivate(res["app_id"])
    assert SANDBOX_MANAGER.get(res["app_id"])["status"] == "active"


# ---------------------------------------------------------------------------
# PermissionGate.check — happy/deny paths
# ---------------------------------------------------------------------------


def test_gate_check_happy_path(sb_env):
    from services.app_sandbox import PERMISSION_GATE, SANDBOX_MANAGER

    app_id = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws",
    )["app_id"]
    assert PERMISSION_GATE.check(app_id, "filesystem.read") is True


def test_gate_check_denies_missing_scope(sb_env):
    from services.app_sandbox import (
        PERMISSION_GATE,
        SANDBOX_MANAGER,
        ScopeViolation,
    )

    app_id = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws",
    )["app_id"]
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, "host.automation")


def test_gate_check_denies_isolated_app(sb_env):
    from services.app_sandbox import (
        AppIsolated,
        PERMISSION_GATE,
        SANDBOX_MANAGER,
    )

    app_id = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": ["llm.local"]},
        workspace_id="ws",
    )["app_id"]
    SANDBOX_MANAGER.isolate(app_id)
    with pytest.raises(AppIsolated):
        PERMISSION_GATE.check(app_id, "llm.local")


def test_gate_check_denies_unknown_app(sb_env):
    from services.app_sandbox import AppNotFound, PERMISSION_GATE

    with pytest.raises(AppNotFound):
        PERMISSION_GATE.check("does-not-exist", "llm.local")


def test_gate_restrictions_beat_grants(sb_env):
    """`scopes=['network']` + `restrictions=['network.blocked']` → all
    network.* requests denied. Restrictions win over hierarchical grants."""
    from services.app_sandbox import (
        PERMISSION_GATE,
        SANDBOX_MANAGER,
        ScopeViolation,
    )

    app_id = SANDBOX_MANAGER.install(
        {
            "name": "x",
            "version": "1.0",
            "scopes": ["network"],
            "restrictions": ["network.blocked"],
        },
        workspace_id="ws",
    )["app_id"]
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, "network.outbound")
