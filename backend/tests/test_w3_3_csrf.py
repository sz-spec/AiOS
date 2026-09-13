"""
W3.3 — CSRF + CORS verification tests.

Lives in backend/tests/ (where the rest of the test suite is) but uses
TestClient against the full app factory so middleware runs end-to-end.

Coverage:
  1. Unauthorized origin cannot complete the CSRF handshake (401).
  2. Mutating /api/* request without X-CSRF-Token is rejected (403).
  3. Mutating /api/* with a wrong X-CSRF-Token is rejected (403).
  4. Mutating /api/* with the correct token passes the CSRF middleware
     (it may still 401/403 downstream for auth reasons — what matters is
     the CSRF gate doesn't block).
  5. GET requests pass through without a CSRF token.
  6. Handshake exemption is consistent across both auth and CSRF gates.
"""

from __future__ import annotations

import os

# Pin the handshake secret BEFORE importing the app so the middleware
# initializes with a known value.
os.environ.setdefault("VOS3_TAURI_IPC_SECRET", "test-handshake-secret-for-pytest")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import create_app  # noqa: E402
from middleware.csrf import (  # noqa: E402
    _peek_csrf_token_for_tests,
    _peek_handshake_secret_for_tests,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def test_handshake_rejects_missing_header(client: TestClient) -> None:
    """No X-Tauri-Handshake → 401 unconditionally."""
    res = client.get("/api/auth/csrf")
    assert res.status_code == 401
    assert "X-Tauri-Handshake" in res.json()["detail"]


def test_handshake_rejects_bad_secret(client: TestClient) -> None:
    """Wrong handshake secret → 401."""
    res = client.get(
        "/api/auth/csrf",
        headers={"X-Tauri-Handshake": "wrong-secret"},
    )
    assert res.status_code == 401


def test_handshake_accepts_correct_secret(client: TestClient) -> None:
    """Correct handshake secret → 200 + csrf_token in body."""
    res = client.get(
        "/api/auth/csrf",
        headers={"X-Tauri-Handshake": _peek_handshake_secret_for_tests()},
    )
    assert res.status_code == 200
    body = res.json()
    assert "csrf_token" in body
    assert body["csrf_token"] == _peek_csrf_token_for_tests()


def test_unauthorized_origin_handshake_blocked(client: TestClient) -> None:
    """The user's smoke-test scenario — an evil-tracker.com origin
    attempting to fetch the CSRF token without the handshake secret
    is rejected before any token leak."""
    res = client.get(
        "/api/auth/csrf",
        headers={"Origin": "http://evil-tracker.com"},
    )
    # CORS layer may strip the Origin header on response, but the
    # handshake itself rejects on the missing X-Tauri-Handshake.
    assert res.status_code == 401


def test_mutating_request_without_csrf_token_blocked(client: TestClient) -> None:
    """POST to a non-public /api/* path without X-CSRF-Token → 403 CSRF_VIOLATION."""
    res = client.post(
        "/api/agents",
        json={"name": "x", "role": "tester"},
    )
    assert res.status_code == 403
    body = res.json()
    assert body.get("error", {}).get("code") == "CSRF_VIOLATION"


def test_mutating_request_with_wrong_csrf_token_blocked(client: TestClient) -> None:
    """POST with mismatched X-CSRF-Token → 403."""
    res = client.post(
        "/api/agents",
        headers={"X-CSRF-Token": "not-the-real-token"},
        json={"name": "x", "role": "tester"},
    )
    assert res.status_code == 403
    assert res.json().get("error", {}).get("code") == "CSRF_VIOLATION"


def test_mutating_request_with_correct_csrf_token_passes_gate(
    client: TestClient,
) -> None:
    """POST with the correct X-CSRF-Token passes the CSRF middleware.

    The request may still be rejected downstream (auth, validation, etc.)
    but the response code should NOT be CSRF_VIOLATION. We assert on the
    error code shape, not the HTTP status, because downstream auth/CORS
    may set 401 or 403 for unrelated reasons.
    """
    res = client.post(
        "/api/agents",
        headers={"X-CSRF-Token": _peek_csrf_token_for_tests()},
        json={"name": "x", "role": "tester"},
    )
    # Whatever the status, the CSRF middleware did not generate it.
    body = (
        res.json()
        if res.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if isinstance(body, dict):
        assert body.get("error", {}).get("code") != "CSRF_VIOLATION"


def test_get_request_passes_without_csrf(client: TestClient) -> None:
    """GET /api/* without a token must NOT be CSRF-blocked.

    CSRF is for state-changing methods only. Auth/Clerk may still 401,
    but we should never see CSRF_VIOLATION for a GET.
    """
    res = client.get("/api/agents")
    body = (
        res.json()
        if res.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if isinstance(body, dict):
        assert body.get("error", {}).get("code") != "CSRF_VIOLATION"


def test_webhook_path_exempt_from_csrf(client: TestClient) -> None:
    """Webhook callbacks (Clerk/Stripe) are signed externally —
    they must NOT require X-CSRF-Token."""
    # Even an obviously-malformed payload should bypass CSRF.
    res = client.post(
        "/api/webhooks/clerk",
        json={"type": "fake.event", "data": {}},
    )
    body = (
        res.json()
        if res.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if isinstance(body, dict):
        assert body.get("error", {}).get("code") != "CSRF_VIOLATION"
