"""
Phase 25 — AIMS/OAuth perimeter auth integration tests (Gap G2).
================================================================

Exercises the REAL `api.aims_dep.aims_auth_dependency` (which calls the REAL
`services.mcp_oauth_bridge.resolve_mcp_auth`) through a TestClient, and confirms
the dependency is actually wired onto the chat + codegen perimeter routers.

Anti-gaming (charter §II): fixed-value status asserts (== 401 / == 403 / == 200);
the 200 case uses a GENUINELY-valid PAT minted with the real HMAC contract — the
resolver is NOT mocked. Flag OFF must be a true no-op (legacy access unchanged).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.aims_dep import aims_auth_dependency


def _mint_valid_pat(secret: str, pat_id: str = "a" * 32) -> str:
    """Build a vos3pat_<id>_<b64url(HMAC-SHA256(id, secret))> token exactly as
    services.mcp_oauth_bridge._resolve_pat validates it."""
    mac = hmac.new(secret.encode(), pat_id.encode("ascii"), hashlib.sha256).digest()
    mac_b64 = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    return f"vos3pat_{pat_id}_{mac_b64}"


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/guarded", dependencies=[Depends(aims_auth_dependency)])
    async def _g():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_case1_flag_on_missing_envelope_returns_exactly_401(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_AIMS_AUTH", "1")
    monkeypatch.delenv("VOS3_MCP_REQUIRE_RC_VERSION", raising=False)
    r = _client().get("/guarded")  # no Authorization header
    assert r.status_code == 401


def test_case1b_flag_on_malformed_token_returns_exactly_403(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_AIMS_AUTH", "1")
    r = _client().get("/guarded", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 403


def test_case2_flag_on_valid_pat_returns_exactly_200(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_AIMS_AUTH", "1")
    monkeypatch.setenv("VOS3_MCP_PAT_HMAC_SECRET", "phase25-test-secret")
    monkeypatch.delenv("VOS3_MCP_REQUIRE_RC_VERSION", raising=False)
    tok = _mint_valid_pat("phase25-test-secret")
    r = _client().get("/guarded", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_case3_flag_off_legacy_access_unregressed_returns_200(monkeypatch):
    # Flag OFF (dev/CI default): the gate is a no-op; no Authorization required.
    monkeypatch.delenv("VOS3_ENABLE_LIVE_AIMS_AUTH", raising=False)
    r = _client().get("/guarded")
    assert r.status_code == 200


def test_dependency_is_actually_wired_onto_perimeter_routers():
    # Prove the wiring is real (not just the helper): chat + codegen routers must
    # carry aims_auth_dependency in their router-level dependency list.
    from api import chat_routes, codegen_routes

    for mod in (chat_routes, codegen_routes):
        deps = [getattr(d, "dependency", None) for d in mod.router.dependencies]
        assert aims_auth_dependency in deps, f"{mod.__name__} missing AIMS dependency"
