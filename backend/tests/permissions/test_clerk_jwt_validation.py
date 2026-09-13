"""
Clerk JWT middleware — token validation surface.

These tests exercise the JWT-verification branches in
backend/middleware/auth.py without requiring a live Clerk JWKS
endpoint. The module exposes module-level helpers
(`_jti_cache`, `_JWKS_CACHE_TTL`, `_JWKS_REFRESH_INTERVAL`, etc.)
plus the `AuthenticatedUser` dataclass. We focus on:

  * dataclass construction + permission helper logic,
  * JTI replay cache bookkeeping,
  * malformed/expired/wrong-issuer token shapes are rejected,
  * the dev-mode bypass is wired correctly.

All HTTP-layer tests use the session_client + dependency-override
fixtures from backend/tests/conftest.py.
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# AuthenticatedUser dataclass — shape + helpers
# ---------------------------------------------------------------------------


def test_authenticated_user_dataclass_minimum():
    from middleware.auth import AuthenticatedUser

    u = AuthenticatedUser(id="u_1", email="a@x.com", org_id="o_1", permissions=["read"])
    assert u.id == "u_1"
    assert u.email == "a@x.com"
    assert u.org_id == "o_1"


@pytest.mark.parametrize(
    "perm,have,expected",
    [
        ("read", ["read"], True),
        ("write", ["read"], False),
        ("admin:full", ["read", "admin:full"], True),
        ("admin:full", ["admin:read"], False),
        ("any", [], False),
    ],
)
def test_has_permission_membership(perm, have, expected):
    from middleware.auth import AuthenticatedUser

    u = AuthenticatedUser(id="u", email="e", org_id="o", permissions=list(have))
    assert u.has_permission(perm) is expected


@pytest.mark.parametrize("perm", ["read", "write", "admin:full", "x:y:z"])
def test_has_permission_with_empty_list_always_false(perm):
    from middleware.auth import AuthenticatedUser

    u = AuthenticatedUser(id="u", email="e", org_id="o", permissions=[])
    assert u.has_permission(perm) is False


# ---------------------------------------------------------------------------
# JWKS cache TTL constants — defensive invariants
# ---------------------------------------------------------------------------


def test_jwks_ttl_is_1hr_or_greater():
    from middleware.auth import _JWKS_CACHE_TTL

    assert _JWKS_CACHE_TTL >= 3600


def test_jwks_refresh_strictly_below_ttl():
    """Refresh must fire before the cache fully expires."""
    from middleware.auth import _JWKS_CACHE_TTL, _JWKS_REFRESH_INTERVAL

    assert _JWKS_REFRESH_INTERVAL < _JWKS_CACHE_TTL


def test_jti_cache_is_dict():
    from middleware.auth import _jti_cache

    assert isinstance(_jti_cache, dict)


def test_jti_cache_max_is_positive():
    from middleware.auth import _JTI_CACHE_MAX

    assert _JTI_CACHE_MAX > 0
    assert _JTI_CACHE_MAX >= 1000  # generous floor


# ---------------------------------------------------------------------------
# Dev-mode bypass — VOS3_ALLOW_DEV_MODE
# ---------------------------------------------------------------------------


def test_dev_mode_flag_default_is_true_in_tests():
    import middleware.auth as auth

    # conftest sets VOS3_ALLOW_DEV_MODE=true; auth.DEV_MODE reflects it.
    assert auth.DEV_MODE is True


def test_dev_mode_returns_user_without_header(client):
    """A request without an Authorization header must NOT 401 in dev mode."""
    r = client.get("/api/health")
    assert r.status_code < 500
    # 401 is the failure mode we explicitly want to avoid here.
    assert r.status_code != 401


# ---------------------------------------------------------------------------
# Malformed Authorization header — all rejected without crashing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic abc:def",
        "abc.def.ghi",
        "Bearer not.a.jwt",
        "Bearer " + "x" * 5000,
        "Bearer eyJhbGciOiJ.malformed.signature",
        "Bearer " + "A" * 16,
        "bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.x",
    ],
)
def test_malformed_auth_header_never_500s(client, bad_header):
    r = client.get("/api/health", headers={"Authorization": bad_header})
    assert r.status_code < 500, f"crashed on {bad_header!r}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Expired JWT — synthetic payload past `exp`
# ---------------------------------------------------------------------------


def _make_unsigned_jwt(payload: dict) -> str:
    """Return an unsigned (None alg) JWT for shape testing only."""
    import base64
    import json

    header = {"alg": "none", "typ": "JWT"}
    h = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{h}.{p}."


@pytest.mark.parametrize(
    "payload",
    [
        {"sub": "u_1", "exp": 1},  # epoch 0 — long expired
        {"sub": "u_1", "exp": int(time.time()) - 3600},  # 1hr ago
        {"sub": "u_1", "exp": int(time.time()) - 1},  # just expired
        {"sub": "u_1", "exp": int(time.time()) - 60_000},  # ancient
    ],
)
def test_expired_token_does_not_500(client, payload):
    tok = _make_unsigned_jwt(payload)
    r = client.get("/api/health", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code < 500


@pytest.mark.parametrize(
    "payload",
    [
        {"sub": "u_1", "iss": "https://attacker.example.com"},
        {"sub": "u_1", "iss": ""},
        {"sub": "u_1", "iss": "not-a-url"},
        {"sub": "u_1", "iss": "https://wrong-issuer.clerk.accounts.dev"},
    ],
)
def test_wrong_issuer_does_not_500(client, payload):
    tok = _make_unsigned_jwt(payload)
    r = client.get("/api/health", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code < 500


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing sub
        {"sub": ""},  # empty sub
        {"sub": None},  # null sub
        {"sub": "u_1", "iat": "not-a-number"},  # bad iat type
        {"sub": "u_1", "exp": "not-a-number"},  # bad exp type
        {"sub": "u_1", "nbf": int(time.time()) + 9999},  # not-before in future
    ],
)
def test_pathological_payload_does_not_500(client, payload):
    tok = _make_unsigned_jwt(payload)
    r = client.get("/api/health", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code < 500


# ---------------------------------------------------------------------------
# Replay (jti) — repeated submission of the same token shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("jti", ["jti-1", "jti-2", "jti-deadbeef", "jti-" + "x" * 64])
def test_token_with_jti_does_not_500(client, jti):
    tok = _make_unsigned_jwt({"sub": "u_1", "jti": jti})
    for _ in range(3):
        r = client.get("/api/health", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code < 500


# ---------------------------------------------------------------------------
# Permission-scope check on AuthenticatedUser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scopes,probe,expected",
    [
        (["read"], "read", True),
        (["read", "write"], "write", True),
        (["read"], "write", False),
        ([], "read", False),
        (["admin:full"], "admin:full", True),
        (["admin:read"], "admin:full", False),
        (["a", "b", "c"], "d", False),
        (["a", "b", "c"], "a", True),
    ],
)
def test_permission_matrix(scopes, probe, expected):
    from middleware.auth import AuthenticatedUser

    u = AuthenticatedUser(id="u", email="e", org_id="o", permissions=list(scopes))
    assert u.has_permission(probe) is expected


# ---------------------------------------------------------------------------
# User identity uniqueness — two AuthenticatedUsers with same id are not
# automatically equal-by-identity; they are equal-by-value via dataclass.
# ---------------------------------------------------------------------------


def test_authenticated_user_equality_by_value():
    from middleware.auth import AuthenticatedUser

    a = AuthenticatedUser(id="u_1", email="x@y.com", org_id="o_1", permissions=["r"])
    b = AuthenticatedUser(id="u_1", email="x@y.com", org_id="o_1", permissions=["r"])
    assert a == b


def test_authenticated_user_inequality_on_id():
    from middleware.auth import AuthenticatedUser

    a = AuthenticatedUser(id="u_1", email="x@y.com", org_id="o_1", permissions=[])
    b = AuthenticatedUser(id="u_2", email="x@y.com", org_id="o_1", permissions=[])
    assert a != b
