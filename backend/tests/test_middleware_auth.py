"""
Tests for middleware/auth.py (security-critical, was previously untested).
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from starlette.requests import Request

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from middleware.auth import (
    AuthenticatedUser,
    verify_auth,
    require_org_membership,
    auth_middleware,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_request(path: str = "/", headers: dict | None = None) -> Request:
    """Create a minimal Starlette Request for testing."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "server": ("localhost", 8000),
    }
    return Request(scope)


def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# AuthenticatedUser.has_permission
# ---------------------------------------------------------------------------


class TestHasPermission:
    def test_exact_permission_match(self):
        user = AuthenticatedUser(id="u1", permissions=["users:read", "users:write"])
        assert user.has_permission("users:read") is True
        assert user.has_permission("users:write") is True

    def test_missing_permission_returns_false(self):
        user = AuthenticatedUser(id="u1", permissions=["users:read"])
        assert user.has_permission("users:delete") is False

    def test_admin_full_grants_any_permission(self):
        user = AuthenticatedUser(id="u1", permissions=["admin:full"])
        assert user.has_permission("users:delete") is True
        assert user.has_permission("billing:manage") is True
        assert user.has_permission("anything:at:all") is True

    def test_empty_permissions_returns_false(self):
        user = AuthenticatedUser(id="u1", permissions=[])
        assert user.has_permission("users:read") is False

    def test_defaults_to_empty_permissions(self):
        user = AuthenticatedUser(id="u1")
        assert user.permissions == []
        assert user.has_permission("any:perm") is False


# ---------------------------------------------------------------------------
# verify_auth — dev mode
# ---------------------------------------------------------------------------


class TestVerifyAuthDevMode:
    def test_dev_mode_no_token_returns_demo_user(self):
        request = make_request("/api/test")
        with patch("middleware.auth.DEV_MODE", True):
            user = run(verify_auth(request))
        assert user is not None
        assert user.id == "dev_seed_user"
        assert user.email == "demo@example.com"
        assert "read" in user.permissions

    def test_dev_mode_demo_user_has_org(self):
        request = make_request("/api/test")
        with patch("middleware.auth.DEV_MODE", True):
            user = run(verify_auth(request))
        assert user.org_id == "org_demo"
        assert user.org_role == "member"

    def test_dev_mode_invalid_token_falls_back_to_demo(self):
        request = make_request(
            "/api/test", headers={"Authorization": "Bearer not-a-real-token"}
        )
        with patch("middleware.auth.DEV_MODE", True):
            user = run(verify_auth(request))
        assert user.id == "dev_seed_user"


# ---------------------------------------------------------------------------
# verify_auth — production mode
# ---------------------------------------------------------------------------


class TestVerifyAuthProductionMode:
    def test_production_missing_token_raises_401(self):
        request = make_request("/api/test")
        with patch("middleware.auth.DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))
        assert exc_info.value.status_code == 401
        assert "Missing authorization token" in exc_info.value.detail

    def test_production_invalid_token_raises_401(self):
        request = make_request(
            "/api/test", headers={"Authorization": "Bearer totally-invalid"}
        )
        with patch("middleware.auth.DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                run(verify_auth(request))
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# require_org_membership
# ---------------------------------------------------------------------------


class TestRequireOrgMembership:
    def _request_with_user(
        self, user: AuthenticatedUser | None, path: str = "/"
    ) -> Request:
        request = make_request(path)
        if user is not None:
            request.state.user = user
        return request

    def test_matching_org_returns_true(self):
        user = AuthenticatedUser(id="u1", org_id="org_123", permissions=[])
        request = self._request_with_user(user)
        with patch("middleware.auth.DEV_MODE", False):
            result = require_org_membership(request, "org_123")
        assert result is True

    def test_mismatched_org_returns_false(self):
        user = AuthenticatedUser(id="u1", org_id="org_123", permissions=[])
        request = self._request_with_user(user)
        with patch("middleware.auth.DEV_MODE", False):
            result = require_org_membership(request, "org_999")
        assert result is False

    def test_admin_full_bypasses_org_check(self):
        user = AuthenticatedUser(id="u1", org_id="org_123", permissions=["admin:full"])
        request = self._request_with_user(user)
        with patch("middleware.auth.DEV_MODE", False):
            result = require_org_membership(request, "org_999")
        assert result is True

    def test_no_user_returns_false(self):
        request = make_request("/")
        with patch("middleware.auth.DEV_MODE", False):
            result = require_org_membership(request, "org_123")
        assert result is False

    def test_dev_mode_always_returns_true(self):
        user = AuthenticatedUser(id="u1", org_id="org_123", permissions=[])
        request = self._request_with_user(user)
        with patch("middleware.auth.DEV_MODE", True):
            result = require_org_membership(request, "completely_different_org")
        assert result is True


# ---------------------------------------------------------------------------
# auth_middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    async def _call_middleware(self, path: str, headers: dict | None = None) -> int:
        """Run auth_middleware and return the response status code."""
        request = make_request(path, headers)
        responses = []

        async def mock_call_next(req):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            responses.append(200)
            return mock_resp

        resp = await auth_middleware(request, mock_call_next)
        return getattr(resp, "status_code", 200)

    def test_public_root_path_skips_auth(self):
        status = run(self._call_middleware("/"))
        assert status == 200

    def test_public_health_path_skips_auth(self):
        status = run(self._call_middleware("/health"))
        assert status == 200

    def test_public_docs_path_skips_auth(self):
        status = run(self._call_middleware("/docs"))
        assert status == 200

    def test_public_webhooks_path_skips_auth(self):
        status = run(self._call_middleware("/api/webhooks/stripe"))
        assert status == 200

    def test_protected_path_calls_next_in_dev_mode(self):
        """In dev mode, protected paths still proceed (demo user injected)."""
        with patch("middleware.auth.DEV_MODE", True):
            status = run(self._call_middleware("/api/users"))
        assert status == 200

    def test_protected_path_raises_in_prod_without_token(self):
        """In production, protected paths without token raise HTTPException."""
        with patch("middleware.auth.DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                run(self._call_middleware("/api/users"))
        assert exc_info.value.status_code == 401
