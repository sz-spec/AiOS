"""
P4.1 — Sovereign App Runtime sandbox tests.

Three groups:

  1. Manifest validation — InvalidManifest fires on every malformed
     shape the install route would otherwise let through.

  2. PermissionGate semantics — the load-bearing logic. Includes the
     directive's worked example: `restrictions: ["network.blocked"]`
     hard-blocks any `network.*` scope including the cloud LLM
     path that requires `network.outbound`.

  3. HTTP install endpoint — POST /api/apps/install behind a Bearer
     auth gate, persists the row, returns the assigned id.

The full airgap loopback-only kill-switch is active for every test;
none of the assertions need network egress.
"""

from __future__ import annotations

import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    App,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_sandbox import (
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    AppIsolated,
    AppManifest,
    AppNotFound,
    InvalidManifest,
    ScopeViolation,
    _reset_gate_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    """Fresh SQLite + empty in-memory PermissionGate."""
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


# ---------------------------------------------------------------------------
# (1) Manifest validation
# ---------------------------------------------------------------------------


def test_manifest_requires_name_and_version():
    with pytest.raises(InvalidManifest, match="name"):
        AppManifest.from_dict({"version": "1.0.0"})
    with pytest.raises(InvalidManifest, match="version"):
        AppManifest.from_dict({"name": "x"})


def test_manifest_rejects_non_object_root():
    with pytest.raises(InvalidManifest):
        AppManifest.from_dict("not-a-dict")  # type: ignore[arg-type]


def test_manifest_rejects_scopes_with_whitespace():
    with pytest.raises(InvalidManifest, match="whitespace"):
        AppManifest.from_dict(
            {
                "name": "x",
                "version": "1.0",
                "scopes": ["llm.local", "bad scope"],
            }
        )


def test_manifest_rejects_non_list_restrictions():
    with pytest.raises(InvalidManifest, match="restrictions"):
        AppManifest.from_dict(
            {
                "name": "x",
                "version": "1.0",
                "restrictions": "network.blocked",  # must be list
            }
        )


def test_manifest_accepts_minimal_well_formed():
    m = AppManifest.from_dict(
        {
            "name": "Notes",
            "version": "0.1.0",
            "scopes": ["filesystem.read"],
            "restrictions": ["network.blocked"],
        }
    )
    assert m.name == "Notes"
    assert m.version == "0.1.0"
    assert m.scopes == ("filesystem.read",)
    assert m.restrictions == ("network.blocked",)


# ---------------------------------------------------------------------------
# (2) PermissionGate semantics
# ---------------------------------------------------------------------------


def _install_local_llm_app(scopes, restrictions=()) -> str:
    """Install a fresh app under the module-level SANDBOX_MANAGER
    with the requested manifest. Returns the new app_id."""
    result = SANDBOX_MANAGER.install(
        {
            "name": "test-app",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return result["app_id"]


def test_gate_allows_granted_scope(local_db):
    app_id = _install_local_llm_app(["filesystem.read", "llm.local"])
    assert PERMISSION_GATE.check(app_id, "filesystem.read") is True
    assert PERMISSION_GATE.check(app_id, "llm.local") is True


def test_gate_denies_ungranted_scope(local_db):
    app_id = _install_local_llm_app(["filesystem.read"])
    with pytest.raises(ScopeViolation, match="no granting scope"):
        PERMISSION_GATE.check(app_id, "llm.cloud")


def test_gate_hierarchical_scope_implication(local_db):
    """A grant of 'llm' covers 'llm.local' AND 'llm.cloud'."""
    app_id = _install_local_llm_app(["llm"])
    assert PERMISSION_GATE.check(app_id, "llm.local") is True
    assert PERMISSION_GATE.check(app_id, "llm.cloud") is True


def test_gate_narrow_grant_does_not_imply_sibling(local_db):
    """Grant of 'llm.local' does NOT cover 'llm.cloud'."""
    app_id = _install_local_llm_app(["llm.local"])
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, "llm.cloud")


def test_gate_network_blocked_kills_cloud_llm(local_db):
    """Directive's worked example: `restrictions: ["network.blocked"]`
    hard-blocks the cloud LLM route (which the dispatcher checks
    with the joint scope ["llm.cloud", "network.outbound"])."""
    app_id = _install_local_llm_app(
        scopes=["llm", "network.outbound"],  # broad grant
        restrictions=["network.blocked"],  # but blocked
    )
    # Local LLM still works — `network.blocked` doesn't touch llm.local.
    assert PERMISSION_GATE.check(app_id, "llm.local") is True
    # Cloud LLM route — denied at the network.outbound subcheck.
    with pytest.raises(ScopeViolation) as exc:
        PERMISSION_GATE.check(app_id, "llm.cloud", "network.outbound")
    assert exc.value.scope == "network.outbound"
    assert "network.blocked" in exc.value.reason


def test_gate_restriction_beats_explicit_grant(local_db):
    """Asymmetric semantics — an explicit grant of a blocked scope
    does NOT override a matching restriction. This is what makes
    `restrictions` durable against a sloppy manifest."""
    app_id = _install_local_llm_app(
        scopes=["network.outbound"],
        restrictions=["network.blocked"],
    )
    with pytest.raises(ScopeViolation, match="network.blocked"):
        PERMISSION_GATE.check(app_id, "network.outbound")


def test_gate_isolated_app_denies_everything(local_db):
    """`isolate()` flips status to 'isolated'; every gate check raises
    AppIsolated until reactivate()."""
    app_id = _install_local_llm_app(["llm.local", "filesystem.read"])
    SANDBOX_MANAGER.isolate(app_id, reason="contained suspicious behavior")
    with pytest.raises(AppIsolated):
        PERMISSION_GATE.check(app_id, "llm.local")
    SANDBOX_MANAGER.reactivate(app_id)
    assert PERMISSION_GATE.check(app_id, "llm.local") is True


def test_gate_unknown_app_raises_not_found(local_db):
    with pytest.raises(AppNotFound):
        PERMISSION_GATE.check("ghost-app-id", "llm.local")


def test_gate_empty_scope_request_is_an_error(local_db):
    """Calling check() with no scopes is a contract bug — silently
    returning True would bypass the gate."""
    app_id = _install_local_llm_app(["llm.local"])
    with pytest.raises(ScopeViolation, match="no scopes requested"):
        PERMISSION_GATE.check(app_id)


def test_can_predicate_returns_false_instead_of_raising(local_db):
    app_id = _install_local_llm_app(["llm.local"])
    assert PERMISSION_GATE.can(app_id, "llm.local") is True
    assert PERMISSION_GATE.can(app_id, "llm.cloud") is False
    assert PERMISSION_GATE.can("nope-not-installed", "llm.local") is False


# ---------------------------------------------------------------------------
# (3) Manager persistence + lifecycle
# ---------------------------------------------------------------------------


def test_install_persists_manifest_to_apps_table(local_db):
    result = SANDBOX_MANAGER.install(
        {
            "name": "Persisted",
            "version": "2.3.4",
            "scopes": ["filesystem.read", "rag.search"],
            "restrictions": ["network.blocked"],
        }
    )
    app_id = result["app_id"]
    assert result["secret"], "install() must return a fresh plaintext secret"
    with get_session() as session:
        row = session.query(App).filter_by(id=app_id).one()
        assert row.name == "Persisted"
        assert row.version == "2.3.4"
        assert row.status == "active"
        manifest = json.loads(row.manifest_json)
        assert manifest["scopes"] == ["filesystem.read", "rag.search"]
        assert manifest["restrictions"] == ["network.blocked"]
        assert row.dirty is True  # P3.3 sync journal


def test_reinstall_same_id_updates_in_place(local_db):
    first = SANDBOX_MANAGER.install(
        {"name": "v1", "version": "1.0.0", "scopes": ["llm.local"]},
        app_id="fixed-id",
    )
    second = SANDBOX_MANAGER.install(
        {"name": "v2", "version": "2.0.0", "scopes": ["llm.local", "rag.search"]},
        app_id="fixed-id",
    )
    assert second["app_id"] == first["app_id"]
    record = SANDBOX_MANAGER.get(first["app_id"])
    assert record["name"] == "v2"
    assert record["version"] == "2.0.0"
    assert "rag.search" in record["manifest"]["scopes"]


def test_uninstall_drops_row_and_gate_entry(local_db):
    app_id = _install_local_llm_app(["llm.local"])
    SANDBOX_MANAGER.uninstall(app_id)
    assert SANDBOX_MANAGER.get(app_id) is None
    with pytest.raises(AppNotFound):
        PERMISSION_GATE.check(app_id, "llm.local")


# ---------------------------------------------------------------------------
# (4) HTTP install endpoint behind Bearer auth
# ---------------------------------------------------------------------------


def _build_apps_test_app() -> FastAPI:
    """Stand up a minimal FastAPI app with just /api/apps mounted.

    We avoid the full backend factory (which pulls in heavy deps that
    aren't installed in the airgap harness) by re-importing the
    router and replacing the auth dependency with a stub that maps
    `Authorization: Bearer <clerk_id>` directly to an AuthenticatedUser.
    """
    from api.app_routes import router as apps_router

    app = FastAPI()

    # Override the bearer-token dependency with the airgap stub —
    # the airgap harness already sets VOS3_OFFLINE_AUTH=true, and
    # `make_offline_token` produces JWTs the real dependency accepts.
    app.include_router(apps_router)
    return app


def test_install_endpoint_persists_manifest_and_returns_id(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p41_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)

    client = TestClient(_build_apps_test_app())
    r = client.post(
        "/api/apps/install",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "manifest": {
                "name": "Quotes",
                "version": "0.1.0",
                "scopes": ["filesystem.read", "llm.local"],
                "restrictions": ["network.blocked"],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["name"] == "Quotes"
    assert body["version"] == "0.1.0"
    new_id = body["app_id"]
    assert new_id

    # Row landed in `apps`, gate is primed.
    with get_session() as session:
        row = session.query(App).filter_by(id=new_id).one()
        assert row.name == "Quotes"
    assert PERMISSION_GATE.can(new_id, "llm.local") is True
    assert PERMISSION_GATE.can(new_id, "network.outbound") is False


def test_install_endpoint_rejects_malformed_manifest(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p41_bob"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)

    client = TestClient(_build_apps_test_app())
    r = client.post(
        "/api/apps/install",
        headers={"Authorization": f"Bearer {token}"},
        json={"manifest": {"name": "no-version-set"}},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "invalid_manifest"


def test_install_endpoint_requires_auth(local_db, network_guard):
    client = TestClient(_build_apps_test_app())
    r = client.post(
        "/api/apps/install",
        json={"manifest": {"name": "x", "version": "1.0", "scopes": []}},
    )
    # No Bearer header → 401 (or 403 depending on middleware order).
    assert r.status_code in (401, 403)


# ===========================================================================
# P4.2 — Runtime hardening: dispatcher + RAG repo + middleware
# ===========================================================================


# ---------------------------------------------------------------------------
# (5) LLMRequestContext.app_id flows through to PermissionGate
# ---------------------------------------------------------------------------


def test_local_first_router_denies_app_without_llm_local_scope(
    local_db,
    mock_ollama,
    airgap_env,
):
    """An app lacking `llm.local` cannot resolve the local Ollama
    LLM — the gate trips inside LocalFirstRouter.resolve() and the
    dispatcher raises HTTPException(403)."""
    from fastapi import HTTPException

    from services.llm_dispatcher import LLMRequestContext, LocalFirstRouter

    app_id = _install_local_llm_app(["filesystem.read"])  # no llm.* grant

    router = LocalFirstRouter()
    with pytest.raises(HTTPException) as exc:
        router.resolve(LLMRequestContext(role="coding", app_id=app_id))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "scope_violation"
    assert exc.value.detail["scope"] == "llm.local"


def test_local_first_router_allows_app_with_llm_local_scope(
    local_db,
    mock_ollama,
    airgap_env,
    monkeypatch,
):
    """An app holding `llm.local` resolves the Ollama LLM normally."""
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    from services.llm_dispatcher import LLMRequestContext, LocalFirstRouter

    app_id = _install_local_llm_app(["llm.local"])
    router = LocalFirstRouter()
    resolution = router.resolve(LLMRequestContext(role="coding", app_id=app_id))
    assert resolution.provider == "ollama"
    assert resolution.model_id == "llama3"


def test_cloud_router_denies_app_with_network_blocked(local_db, airgap_env):
    """The directive's worked example. App has broad `llm` grant but
    `restrictions: ["network.blocked"]` → cloud LLM is hard-blocked at
    the `network.outbound` sub-check inside CloudRouter.resolve()."""
    from fastapi import HTTPException

    from services.llm_dispatcher import CloudRouter, LLMRequestContext

    app_id = _install_local_llm_app(
        scopes=["llm"],  # umbrella grant
        restrictions=["network.blocked"],  # but no network
    )
    router = CloudRouter()
    with pytest.raises(HTTPException) as exc:
        router.resolve(LLMRequestContext(role="coding", app_id=app_id))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "scope_violation"
    # First failing scope — order is llm.cloud then network.outbound.
    # llm.cloud passes (granted via "llm"); network.outbound is
    # blocked. So the detail.scope must be the second one.
    assert exc.value.detail["scope"] == "network.outbound"
    assert "network.blocked" in exc.value.detail["reason"]


def test_dispatcher_no_app_id_skips_gate(local_db, mock_ollama, airgap_env):
    """First-party request (app_id=None) bypasses the gate entirely —
    existing W6.* behavior is preserved."""
    from services.llm_dispatcher import LLMRequestContext, LocalFirstRouter

    router = LocalFirstRouter()
    # Even with NO app installed at all, the resolver succeeds when
    # app_id is unset.
    resolution = router.resolve(LLMRequestContext(role="coding"))
    assert resolution.provider == "ollama"


# ---------------------------------------------------------------------------
# (6) RAG repository — rag.read / rag.write enforcement
# ---------------------------------------------------------------------------


class _FakeMemoryRepo:
    """Drop-in test double for LocalChromaMemoryRepository.

    The real repo needs ChromaDB; we exercise ONLY the gate-check
    wrapper here. Mirrors the public method signatures including
    the P4.2 `app_id` kwarg + the `_enforce_rag_scope` call site.
    """

    def __init__(self):
        self.added: list = []
        self.queried: list = []

    def add_memory(self, project_id, text, metadata=None, *, app_id=None):
        from core.repositories.sqlite import _enforce_rag_scope

        _enforce_rag_scope(app_id, "rag.write")
        self.added.append((project_id, text))
        return "doc-id-stub"

    def query_memory(self, project_id, query_text, limit=5, *, app_id=None):
        from core.repositories.sqlite import _enforce_rag_scope

        _enforce_rag_scope(app_id, "rag.read")
        self.queried.append((project_id, query_text))
        return []


def test_rag_write_denied_without_scope(local_db):
    """An app without `rag.write` cannot add memories."""
    from fastapi import HTTPException

    app_id = _install_local_llm_app(["rag.read"])  # read-only
    repo = _FakeMemoryRepo()
    with pytest.raises(HTTPException) as exc:
        repo.add_memory("project-1", "secret text", app_id=app_id)
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "rag.write"
    assert repo.added == [], "write must NOT proceed when gate denies"


def test_rag_read_denied_without_scope(local_db):
    from fastapi import HTTPException

    app_id = _install_local_llm_app(["llm.local"])  # no rag.*
    repo = _FakeMemoryRepo()
    with pytest.raises(HTTPException) as exc:
        repo.query_memory("project-1", "find me", app_id=app_id)
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "rag.read"


def test_rag_write_allowed_with_scope(local_db):
    app_id = _install_local_llm_app(["rag.write"])
    repo = _FakeMemoryRepo()
    doc_id = repo.add_memory("project-1", "ok to write", app_id=app_id)
    assert doc_id == "doc-id-stub"
    assert repo.added == [("project-1", "ok to write")]


def test_rag_first_party_call_skips_gate(local_db):
    """app_id=None → no gate, no scope, no exception."""
    repo = _FakeMemoryRepo()
    repo.add_memory("project-1", "internal write")
    repo.query_memory("project-1", "internal query")
    assert repo.added == [("project-1", "internal write")]
    assert repo.queried == [("project-1", "internal query")]


# ---------------------------------------------------------------------------
# (7) get_app_context middleware dependency
# ---------------------------------------------------------------------------


def _build_app_context_test_app() -> FastAPI:
    """Mount a single test endpoint that returns the validated app_id."""
    from middleware.auth import get_app_context

    app = FastAPI()

    @app.get("/_test/app-context")
    async def endpoint(app_id: str | None = Depends(get_app_context)):
        return {"app_id": app_id}

    return app


def test_app_context_no_headers_returns_none(local_db, network_guard):
    client = TestClient(_build_app_context_test_app())
    r = client.get("/_test/app-context")
    assert r.status_code == 200
    assert r.json() == {"app_id": None}


def test_app_context_validates_secret(local_db, network_guard):
    """X-App-Id + correct X-App-Secret resolves to the app_id."""
    result = SANDBOX_MANAGER.install(
        {
            "name": "ctx-app",
            "version": "1.0",
            "scopes": ["llm.local"],
        }
    )
    app_id = result["app_id"]
    secret = result["secret"]
    assert secret

    client = TestClient(_build_app_context_test_app())
    r = client.get(
        "/_test/app-context",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
    )
    assert r.status_code == 200
    assert r.json() == {"app_id": app_id}


def test_app_context_rejects_wrong_secret(local_db, network_guard):
    result = SANDBOX_MANAGER.install(
        {
            "name": "ctx-app",
            "version": "1.0",
            "scopes": ["llm.local"],
        }
    )
    client = TestClient(_build_app_context_test_app())
    r = client.get(
        "/_test/app-context",
        headers={"X-App-Id": result["app_id"], "X-App-Secret": "wrong-secret"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "app_unauthorized"


def test_app_context_rejects_partial_headers(local_db, network_guard):
    client = TestClient(_build_app_context_test_app())
    # Just the id, no secret.
    r = client.get(
        "/_test/app-context",
        headers={"X-App-Id": "some-id"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "app_context_incomplete"


def test_app_context_rejects_isolated_app(local_db, network_guard):
    """Even with a valid secret, an isolated app is unauthorized."""
    result = SANDBOX_MANAGER.install(
        {
            "name": "ctx-app",
            "version": "1.0",
            "scopes": ["llm.local"],
        }
    )
    SANDBOX_MANAGER.isolate(result["app_id"], reason="test-freeze")
    client = TestClient(_build_app_context_test_app())
    r = client.get(
        "/_test/app-context",
        headers={
            "X-App-Id": result["app_id"],
            "X-App-Secret": result["secret"],
        },
    )
    assert r.status_code == 403
