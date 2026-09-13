"""
Stage 4 · Cross-workspace data isolation breakout attempts.

Each test simulates an attacker trying to cross the workspace
boundary — the core multi-tenant boundary of vOS. Every breakout
attempt must be rejected by the responsible gate AND record an
audit row.

Surfaces covered:
  * P2P sync handshake — workspace_id mismatch
  * P2P discovery — beacon workspace_id mismatch
  * App install — workspace tenancy preserved across operations
  * Workflow row — workspace_id is part of the signed canonical form
"""

from __future__ import annotations

import pytest

from services.p2p_discovery import LocalPeerDiscovery

# ---------------------------------------------------------------------------
# P2P discovery — every workspace mismatch produces a 'rejected' record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rogue_ws,local_ws",
    [
        ("ws-org-A", "ws-org-B"),
        ("public-tenant", "private-tenant"),
        ("ws-1", "ws-2"),
        ("evil-corp", "good-corp"),
        ("workspace-uuid-aaa", "workspace-uuid-bbb"),
    ],
)
def test_p2p_discovery_workspace_mismatch_rejected(adv_env, rogue_ws, local_ws):
    me = LocalPeerDiscovery(
        workspace_id=local_ws,
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13391)],
    )
    rogue = LocalPeerDiscovery(
        workspace_id=rogue_ws,
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13391)],
    )
    rec = me.ingest_beacon(rogue.build_beacon(), source_host="10.0.0.5")
    # Record exists (audit trail) but status='rejected'.
    assert rec is not None
    assert rec.status == "rejected"
    assert rec.workspace_id == rogue_ws


# ---------------------------------------------------------------------------
# Each cross-workspace beacon produces local_tampering_blocked audit
# ---------------------------------------------------------------------------


def test_cross_workspace_writes_audit(adv_env):
    from core.database.sqlite_setup import SecurityAuditLog, get_session

    me = LocalPeerDiscovery(
        workspace_id="ws-mine",
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13392)],
    )
    rogue = LocalPeerDiscovery(
        workspace_id="ws-theirs",
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13392)],
    )
    me.ingest_beacon(rogue.build_beacon(), source_host="10.0.0.1")
    with get_session() as s:
        rows = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# App install — workspace_id is stored on the app record
# ---------------------------------------------------------------------------


def test_apps_in_different_workspaces_are_isolated(adv_env):
    """Two apps in different workspaces have separate sandbox roots."""
    from services.app_sandbox import SANDBOX_MANAGER, app_storage_root

    a1 = SANDBOX_MANAGER.install(
        {"name": "app1", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-tenant-A",
    )["app_id"]
    a2 = SANDBOX_MANAGER.install(
        {"name": "app2", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-tenant-B",
    )["app_id"]
    root_a = str(app_storage_root(a1))
    root_b = str(app_storage_root(a2))
    assert root_a != root_b
    # Neither root contains the other.
    assert not root_a.startswith(root_b)
    assert not root_b.startswith(root_a)


def test_sandbox_manager_get_returns_workspace_id(adv_env):
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "x", "version": "1.0", "scopes": []},
        workspace_id="ws-tenant-Z",
    )
    record = SANDBOX_MANAGER.get(res["app_id"])
    assert record["workspace_id"] == "ws-tenant-Z"


# ---------------------------------------------------------------------------
# Cross-workspace file access — app A cannot resolve to app B's sandbox
# ---------------------------------------------------------------------------


def test_cross_workspace_filesystem_isolated(adv_env):
    """App A in workspace A cannot resolve a path into app B's sandbox."""
    from services.app_filesystem import (
        _resolve_inside_sandbox,
        PathTraversalAttempt,
    )
    from services.app_sandbox import SANDBOX_MANAGER, app_storage_root

    a = SANDBOX_MANAGER.install(
        {"name": "a", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-A",
    )["app_id"]
    b = SANDBOX_MANAGER.install(
        {"name": "b", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-B",
    )["app_id"]
    b_root = str(app_storage_root(b))

    # Even if app A names its target with a relative path, it can't reach b_root.
    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(a, b_root)


# ---------------------------------------------------------------------------
# Workflow row — workspace_id is in the signed canonical form
# ---------------------------------------------------------------------------


def test_workflow_workspace_id_in_signature(adv_env):
    """Mutating workspace_id after signing → verifier returns False."""
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r",
        workspace_id="ws-original",
        manifest_json="{}",
        created_at_ms=1000,
    )

    class _Row:
        id = "r"
        workspaceId = "ws-tampered"  # attacker changes workspace
        manifest_json = "{}"
        createdAt = 1000
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_Row()) is False


# ---------------------------------------------------------------------------
# P2P sync — workspace switching during handshake
# ---------------------------------------------------------------------------


def test_p2p_sync_workspace_mismatch_storm(adv_env):
    """Hammer the sync engine with 10 rogue handshake attempts —
    each rejected, each audited."""
    import uuid
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from services.p2p_sync import HandshakeFailed, SovereignSyncEngine

    server_eng = SovereignSyncEngine(
        node_id=str(uuid.uuid4()),
        workspace_id="ws-home",
    )

    from services.p2p_sync import SovereignSyncServer

    server = SovereignSyncServer(server_eng, host="127.0.0.1", port=0)
    server.start_in_thread()
    try:
        for _ in range(10):
            rogue = SovereignSyncEngine(
                node_id=str(uuid.uuid4()),
                workspace_id="ws-FOREIGN",
            )
            with pytest.raises(HandshakeFailed):
                rogue.establish_session(
                    "127.0.0.1",
                    server.port,
                    expected_workspace_id="ws-FOREIGN",
                )
        with get_session() as s:
            rows = (
                s.query(SecurityAuditLog)
                .filter_by(
                    kind="local_tampering_blocked",
                )
                .all()
            )
            assert len(rows) >= 10
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Workspace ID injection — special characters in workspace_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil_ws",
    [
        "../ws-other",  # path traversal attempt
        "ws-other; DROP TABLE",  # sql injection
        "ws-\x00null",  # nul byte
        "ws-' OR 1=1 --",  # sqli classic
    ],
)
def test_evil_workspace_id_does_not_cross_boundary(adv_env, evil_ws):
    """Even bizarre workspace_id strings remain isolated — they don't
    accidentally collide with another workspace."""
    me = LocalPeerDiscovery(
        workspace_id="ws-clean",
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13393)],
    )
    rogue = LocalPeerDiscovery(
        workspace_id=evil_ws,
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13393)],
    )
    rec = me.ingest_beacon(rogue.build_beacon(), source_host="10.0.0.5")
    # Either rejected or recorded as 'rejected' — never 'active'.
    if rec is not None:
        assert rec.status == "rejected"


# ---------------------------------------------------------------------------
# Workspace_id reuse across nodes is fine — same workspace can have many peers
# ---------------------------------------------------------------------------


def test_same_workspace_many_peers_allowed(adv_env):
    me = LocalPeerDiscovery(
        workspace_id="ws-multi",
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13394)],
    )
    peers = [
        LocalPeerDiscovery(
            workspace_id="ws-multi",
            bind_host="127.0.0.1",
            peer_targets=[("127.0.0.1", 13394)],
        )
        for _ in range(5)
    ]
    for p in peers:
        rec = me.ingest_beacon(p.build_beacon(), source_host="10.0.0.1")
        assert rec is not None
        assert rec.status == "active"
    assert len(me.list_peers()) == 5
