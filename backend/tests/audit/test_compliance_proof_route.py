"""
Phase-22 compliance-proof endpoint test (TEST_PLAN_300 §B3 / Phase 22).

Exercises GET /api/compliance/audit/compliance-proof in isolation (minimal app,
auth dependency overridden) so it does not depend on the full app boot. Asserts
the endpoint serves the self-collected evidence bundle AND that the bundle is
HONESTLY labelled (external audit pending, moat impact none) — i.e. the API
surfaces evidence, it does not assert a passed audit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import compliance_routes
from api.deps import get_current_user

# tests/audit/<file> -> parents: audit, tests, backend, REPO_ROOT
_PAYLOAD = (
    Path(__file__).resolve().parents[3] / "docs" / "audit" / "compliance_payload.json"
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(compliance_routes.router, prefix="/api/compliance")
    # Bypass Clerk auth for the isolated test (the route still DECLARES the dep).
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app)


@pytest.mark.skipif(not _PAYLOAD.is_file(), reason="bundle not generated on this host")
def test_compliance_proof_served_and_honestly_labelled():
    r = _client().get("/api/compliance/audit/compliance-proof")
    assert r.status_code == 200
    body = r.json()
    # The bundle must NOT claim more than it is.
    assert str(body.get("external_audit_status", "")).upper().startswith("PENDING")
    assert str(body.get("moat_impact", "")).lower().startswith("none")
    assert "_disclaimer" in body  # the self-cert disclaimer is present


def test_compliance_route_declares_auth_dependency():
    # The endpoint must require get_current_user (no anonymous access).
    src = Path(compliance_routes.__file__).read_text()
    idx = src.find("async def get_compliance_proof")
    assert idx != -1, "handler missing"
    # the handler signature (within the next ~300 chars) carries the auth dep
    assert "Depends(get_current_user)" in src[idx : idx + 300]


def test_compliance_bundle_is_valid_json_when_present():
    if not _PAYLOAD.is_file():
        pytest.skip("bundle not generated")
    data = json.loads(_PAYLOAD.read_text())
    assert isinstance(data, dict) and data.get("moat_impact")
