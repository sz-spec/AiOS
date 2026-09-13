"""
P5.1 — Sovereign App Dashboard backend tests.

Coverage groups:

  1. `toggle_scope()` core invariant — the directive's load-bearing
     guarantee: revoking a scope at runtime takes effect on the
     NEXT gate check, without a server restart. Verified by
     reading the SQLite row, calling the toggle, then re-invoking
     LocalFirstRouter.resolve() and asserting the previously-
     working call now raises HTTPException(403).

  2. HTTP toggle endpoint — POST /api/apps/{id}/scopes/toggle. Body
     validation, granted=True path (adds), granted=False path
     (removes), idempotent on no-op, 404 for unknown app.

  3. Audit log surface — GET /api/system/audit-logs returns the
     rows the gate persisted, supports pagination + filtering by
     app_id + kind. Penetration attempts AND legitimate operator
     toggles both land in the same table.

The full airgap loopback-only kill-switch is active for every test.
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    _reset_for_tests,
    init_db,
)
from services.app_filesystem import (
    FILESYSTEM_MANAGER,
    PathTraversalAttempt,
)
from services.app_sandbox import (
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    AppNotFound,
    ScopeViolation,
    _reset_gate_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


def _install(scopes, restrictions=()) -> tuple:
    res = SANDBOX_MANAGER.install(
        {
            "name": "p51-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


# ---------------------------------------------------------------------------
# (1) toggle_scope — the load-bearing invariant
# ---------------------------------------------------------------------------


def test_toggle_revokes_scope_and_evicts_cache(local_db, mock_ollama, airgap_env):
    """Directive's golden-path scenario.

    1. Install an app with `llm.local`.
    2. Confirm `LocalFirstRouter.resolve()` succeeds for it
       (proves the gate currently grants the scope).
    3. Toggle `llm.local` to `granted=False` via the manager.
    4. Confirm a fresh `resolve()` now raises HTTPException(403)
       with `scope_violation` — proves the cache was evicted and
       the new manifest took effect on the very next check."""
    import os

    os.environ["VOS3_OLLAMA_MODEL"] = "llama3"
    from services.llm_dispatcher import LLMRequestContext, LocalFirstRouter

    app_id, _ = _install(["llm.local"])
    router = LocalFirstRouter()

    # (a) Currently granted — resolve() succeeds.
    resolution = router.resolve(LLMRequestContext(role="coding", app_id=app_id))
    assert resolution.provider == "ollama"

    # (b) Confirm the gate cache has the entry — proves we'll be
    # exercising the eviction path, not just a cold-load.
    assert app_id in PERMISSION_GATE._entries

    # (c) Toggle off — manager-side API.
    result = SANDBOX_MANAGER.toggle_scope(
        app_id,
        scope="llm.local",
        granted=False,
    )
    assert result["granted"] is False
    assert "llm.local" not in result["manifest"]["scopes"]

    # (d) Cache MUST have been cleared. The next check rehydrates
    # from the freshly-edited manifest.
    assert app_id not in PERMISSION_GATE._entries

    # (e) Same router call — must now raise 403 ScopeViolation.
    with pytest.raises(HTTPException) as exc:
        router.resolve(LLMRequestContext(role="coding", app_id=app_id))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "scope_violation"
    assert exc.value.detail["scope"] == "llm.local"


def test_toggle_adds_new_scope(local_db):
    """Granted=True with a scope NOT in the manifest must add it
    and clear the cache so the new grant is honored immediately."""
    app_id, _ = _install(["filesystem.read"])
    # Pre-warm the cache.
    assert PERMISSION_GATE.can(app_id, "filesystem.read") is True
    assert PERMISSION_GATE.can(app_id, "llm.local") is False

    SANDBOX_MANAGER.toggle_scope(app_id, scope="llm.local", granted=True)

    # Cache evicted; next call hydrates with the new scope.
    assert PERMISSION_GATE.can(app_id, "llm.local") is True


def test_toggle_idempotent_on_redundant_grant(local_db):
    """Granting an already-granted scope is a no-op but still
    evicts the cache + records an audit row."""
    app_id, _ = _install(["llm.local"])
    pre = len(SANDBOX_MANAGER.get(app_id)["manifest"]["scopes"])

    SANDBOX_MANAGER.toggle_scope(app_id, scope="llm.local", granted=True)
    post = SANDBOX_MANAGER.get(app_id)["manifest"]["scopes"]
    assert len(post) == pre
    assert post.count("llm.local") == 1


def test_toggle_rejects_unknown_app(local_db):
    with pytest.raises(AppNotFound):
        SANDBOX_MANAGER.toggle_scope(
            "ghost-id",
            scope="llm.local",
            granted=True,
        )


def test_toggle_validates_scope_string(local_db):
    app_id, _ = _install([])
    with pytest.raises(ValueError, match="scope"):
        SANDBOX_MANAGER.toggle_scope(app_id, scope="", granted=True)
    with pytest.raises(ValueError, match="whitespace"):
        SANDBOX_MANAGER.toggle_scope(app_id, scope="has space", granted=True)


# ---------------------------------------------------------------------------
# (2) HTTP /scopes/toggle endpoint
# ---------------------------------------------------------------------------


def _build_apps_http() -> FastAPI:
    from api.app_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_http_toggle_endpoint_revokes_and_evicts(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    """The HTTP route mirrors the manager API and clears the cache."""
    clerk_id = "user_p51_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)

    app_id, _ = _install(["llm.local"])
    # Warm the gate cache.
    PERMISSION_GATE.check(app_id, "llm.local")
    assert app_id in PERMISSION_GATE._entries

    client = TestClient(_build_apps_http())
    r = client.post(
        f"/api/apps/{app_id}/scopes/toggle",
        headers={"Authorization": f"Bearer {token}"},
        json={"scope": "llm.local", "granted": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["granted"] is False
    assert "llm.local" not in body["manifest"]["scopes"]
    # Cache evicted.
    assert app_id not in PERMISSION_GATE._entries


def test_http_toggle_rejects_malformed_body(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p51_bob"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)

    app_id, _ = _install([])
    client = TestClient(_build_apps_http())

    r = client.post(
        f"/api/apps/{app_id}/scopes/toggle",
        headers={"Authorization": f"Bearer {token}"},
        json={"scope": "llm.local"},  # missing `granted`
    )
    assert r.status_code == 400

    r = client.post(
        f"/api/apps/{app_id}/scopes/toggle",
        headers={"Authorization": f"Bearer {token}"},
        json={"scope": "", "granted": True},
    )
    assert r.status_code == 400


def test_http_toggle_unknown_app_returns_404(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p51_carol"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    client = TestClient(_build_apps_http())
    r = client.post(
        "/api/apps/ghost-app-id/scopes/toggle",
        headers={"Authorization": f"Bearer {token}"},
        json={"scope": "llm.local", "granted": True},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (3) /api/system/audit-logs endpoint
# ---------------------------------------------------------------------------


def _build_system_http() -> FastAPI:
    from api.system_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_audit_log_captures_scope_violation(local_db, network_guard):
    """A failed gate check appends a row visible via the endpoint."""
    app_id, _ = _install(["filesystem.read"])
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, "llm.cloud")

    client = TestClient(_build_system_http())
    r = client.get("/api/system/audit-logs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    matching = [
        row
        for row in body["rows"]
        if row["kind"] == "scope_violation"
        and row["app_id"] == app_id
        and row["scope"] == "llm.cloud"
    ]
    assert matching, f"no scope_violation row found, got {body}"
    # The row carries the reason string we use in the dashboard.
    assert "no granting scope" in (matching[0]["reason"] or "")


def test_audit_log_captures_path_traversal(local_db, network_guard):
    """The filesystem manager's traversal raise must also leave a row."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    with pytest.raises(PathTraversalAttempt):
        FILESYSTEM_MANAGER.read_app_file(app_id, "../../etc/passwd")

    client = TestClient(_build_system_http())
    r = client.get(
        "/api/system/audit-logs",
        params={"kind": "path_traversal_attempt"},
    )
    body = r.json()
    assert body["count"] >= 1
    row = body["rows"][0]
    assert row["kind"] == "path_traversal_attempt"
    assert row["app_id"] == app_id
    assert row["details"]["requested_path"] == "../../etc/passwd"


def test_audit_log_filter_by_app_id(local_db, network_guard):
    a, _ = _install([])
    b, _ = _install([])
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(a, "x.y")
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(b, "p.q")

    client = TestClient(_build_system_http())
    r = client.get("/api/system/audit-logs", params={"app_id": a})
    body = r.json()
    for row in body["rows"]:
        assert row["app_id"] == a


def test_audit_log_pagination(local_db, network_guard):
    """Limit + offset shape works against multi-row data."""
    app_id, _ = _install([])
    # Generate a few rows.
    for s in ("a", "b", "c", "d", "e"):
        try:
            PERMISSION_GATE.check(app_id, s)
        except ScopeViolation:
            pass

    client = TestClient(_build_system_http())
    page1 = client.get(
        "/api/system/audit-logs",
        params={"limit": 2, "offset": 0},
    ).json()
    page2 = client.get(
        "/api/system/audit-logs",
        params={"limit": 2, "offset": 2},
    ).json()
    assert page1["count"] == 2
    assert page2["count"] == 2
    ids1 = {r["id"] for r in page1["rows"]}
    ids2 = {r["id"] for r in page2["rows"]}
    assert ids1.isdisjoint(ids2)


def test_audit_log_records_scope_toggle(local_db, network_guard):
    """Operator-driven toggles also land in the audit log so the
    dashboard can render a unified history."""
    app_id, _ = _install([])
    SANDBOX_MANAGER.toggle_scope(app_id, scope="llm.local", granted=True)

    client = TestClient(_build_system_http())
    body = client.get(
        "/api/system/audit-logs",
        params={"kind": "scope_toggled"},
    ).json()
    matching = [
        r
        for r in body["rows"]
        if r["kind"] == "scope_toggled"
        and r["app_id"] == app_id
        and r["scope"] == "llm.local"
    ]
    assert matching
    assert matching[0]["details"]["granted"] is True
