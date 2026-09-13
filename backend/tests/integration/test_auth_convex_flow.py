"""
Auth + Convex Integration Flow Tests
======================================
Tests the full auth pipeline: JWT verification, Convex user ID resolution,
caching, dev-mode fallback, and JWKS lifecycle.

Run:
    pytest tests/integration/test_auth_convex_flow.py -v
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We need PyJWT for creating test tokens
jwt_lib = pytest.importorskip("jwt", reason="PyJWT required for auth tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hs256_token(payload: dict, secret: str = "test_secret") -> str:
    """Create a HS256-signed JWT with the given payload."""
    return jwt_lib.encode(payload, secret, algorithm="HS256")


def _make_expired_token(secret: str = "test_secret") -> str:
    """Create a HS256 JWT whose exp is in the past."""
    return jwt_lib.encode(
        {"sub": "expired_user", "exp": int(time.time()) - 3600},
        secret,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthConvexFlow:
    """Integration tests for the auth -> Convex user resolution pipeline."""

    # 1. _resolve_convex_user_id cached entry --------------------------------
    @pytest.mark.asyncio
    async def test_resolve_cached_entry(self):
        """Cached clerk_id returns immediately without DB call."""
        from middleware.auth import _resolve_convex_user_id, _user_id_cache, _cache_set

        original = dict(_user_id_cache)
        try:
            _cache_set("clerk_cached", "convex_cached_id")

            with patch("db.convex.get_convex_db") as mock_db:
                result = await _resolve_convex_user_id("clerk_cached")

            assert result == "convex_cached_id"
            mock_db.assert_not_called()
        finally:
            _user_id_cache.clear()
            _user_id_cache.update(original)

    # 2. _resolve_convex_user_id cache miss → calls Convex --------------------
    @pytest.mark.asyncio
    async def test_resolve_cache_miss_calls_convex(self):
        """On cache miss, Convex query is called and result cached."""
        from middleware.auth import _resolve_convex_user_id, _user_id_cache, _cache_get

        original = dict(_user_id_cache)
        try:
            _user_id_cache.pop("clerk_new", None)

            mock_db_instance = MagicMock()
            mock_db_instance.dev_mode = False
            mock_db_instance.query = AsyncMock(
                return_value={"_id": "convex_new_id", "name": "Test"}
            )

            with patch("db.convex.get_convex_db", return_value=mock_db_instance):
                result = await _resolve_convex_user_id("clerk_new")

            assert result == "convex_new_id"
            assert _cache_get("clerk_new") == "convex_new_id"
            mock_db_instance.query.assert_awaited_once_with(
                "users:getByClerkId", {"clerkId": "clerk_new"}
            )
        finally:
            _user_id_cache.clear()
            _user_id_cache.update(original)

    # 3. _resolve_convex_user_id with failed Convex → returns None ------------
    @pytest.mark.asyncio
    async def test_resolve_convex_failure_returns_none(self):
        """When Convex throws, _resolve_convex_user_id returns None gracefully."""
        from middleware.auth import _resolve_convex_user_id, _user_id_cache

        original = dict(_user_id_cache)
        try:
            _user_id_cache.pop("clerk_fail", None)

            with patch(
                "db.convex.get_convex_db",
                side_effect=RuntimeError("Convex unreachable"),
            ):
                result = await _resolve_convex_user_id("clerk_fail")

            assert result is None
            assert "clerk_fail" not in _user_id_cache
        finally:
            _user_id_cache.clear()
            _user_id_cache.update(original)

    # 4. _user_id_cache populated after successful resolution ----------------
    @pytest.mark.asyncio
    async def test_cache_populated_after_success(self):
        """Successful resolution populates _user_id_cache for next call."""
        from middleware.auth import _resolve_convex_user_id, _user_id_cache, _cache_get

        original = dict(_user_id_cache)
        try:
            _user_id_cache.pop("clerk_populate", None)

            mock_db = MagicMock()
            mock_db.dev_mode = False
            mock_db.query = AsyncMock(return_value={"_id": "cvx_pop"})

            with patch("db.convex.get_convex_db", return_value=mock_db):
                first = await _resolve_convex_user_id("clerk_populate")

            assert first == "cvx_pop"
            assert _cache_get("clerk_populate") == "cvx_pop"

            # Second call should hit cache (patch no longer active)
            second = await _resolve_convex_user_id("clerk_populate")
            assert second == "cvx_pop"
        finally:
            _user_id_cache.clear()
            _user_id_cache.update(original)

    # 5. verify_auth dev mode with no token ----------------------------------
    @pytest.mark.asyncio
    async def test_verify_auth_dev_mode_no_token(self):
        """In dev mode with no token, verify_auth returns dev user."""
        mock_request = MagicMock()
        mock_headers = MagicMock()
        mock_headers.get.return_value = ""
        mock_request.headers = mock_headers
        mock_request.url.path = "/api/test"

        with patch("middleware.auth.DEV_MODE", True):
            from middleware.auth import verify_auth

            user = await verify_auth(mock_request)

        assert user is not None
        assert user.id == "dev_seed_user"
        assert user.metadata.get("dev_mode") is True

    # 6. verify_auth dev mode with invalid token → fallback ------------------
    @pytest.mark.asyncio
    async def test_verify_auth_dev_mode_invalid_token(self):
        """In dev mode with an invalid token, falls back to dev user."""
        mock_request = MagicMock()
        mock_headers = MagicMock()
        mock_headers.get.side_effect = lambda key, default="": (
            "Bearer bad_token_xyz" if key == "Authorization" else default
        )
        mock_request.headers = mock_headers
        mock_request.url.path = "/api/test"

        with patch("middleware.auth.DEV_MODE", True), patch(
            "middleware.auth.CLERK_ISSUER_URL", ""
        ), patch("middleware.auth.CLERK_SECRET_KEY", ""):
            from middleware.auth import verify_auth

            user = await verify_auth(mock_request)

        assert user is not None
        assert user.id == "dev_seed_user"

    # 7. verify_auth without dev mode, no token → raises 401 -----------------
    @pytest.mark.asyncio
    async def test_verify_auth_no_dev_no_token_raises_401(self):
        """Without dev mode and no token, verify_auth raises 401."""
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_headers = MagicMock()
        mock_headers.get.return_value = ""
        mock_request.headers = mock_headers
        mock_request.url.path = "/api/test"

        with patch("middleware.auth.DEV_MODE", False):
            from middleware.auth import verify_auth

            with pytest.raises(HTTPException) as exc_info:
                await verify_auth(mock_request)

        assert exc_info.value.status_code == 401
        assert "Missing authorization token" in str(exc_info.value.detail)

    # 8. _verify_token with valid HS256 JWT ----------------------------------
    @pytest.mark.asyncio
    async def test_verify_token_valid_hs256(self):
        """Valid HS256 JWT with sub claim returns AuthenticatedUser."""
        secret = "test_hs256_secret"
        token = _make_hs256_token(
            {
                "sub": "user_123",
                "email": "test@example.com",
                "org_id": "org_abc",
                "permissions": ["read", "write"],
                "exp": int(time.time()) + 3600,
            },
            secret=secret,
        )

        with patch("middleware.auth.CLERK_ISSUER_URL", ""), patch(
            "middleware.auth.CLERK_SECRET_KEY", secret
        ), patch("middleware.auth.get_clerk_client", return_value=None), patch(
            "middleware.auth._resolve_convex_user_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from middleware.auth import _verify_token

            user = await _verify_token(token)

        assert user.id == "user_123"
        assert user.email == "test@example.com"
        assert user.org_id == "org_abc"

    # 9. _verify_token with expired JWT → raises 401 -------------------------
    @pytest.mark.asyncio
    async def test_verify_token_expired_raises_401(self):
        """Expired JWT raises HTTPException 401."""
        from fastapi import HTTPException

        secret = "test_secret_exp"
        token = _make_expired_token(secret=secret)

        with patch("middleware.auth.CLERK_ISSUER_URL", ""), patch(
            "middleware.auth.CLERK_SECRET_KEY", secret
        ), patch("middleware.auth.get_clerk_client", return_value=None):
            from middleware.auth import _verify_token

            with pytest.raises(HTTPException) as exc_info:
                await _verify_token(token)

        assert exc_info.value.status_code == 401

    # 10. _verify_token with empty sub claim → raises 401 --------------------
    @pytest.mark.asyncio
    async def test_verify_token_empty_sub_raises_401(self):
        """JWT with empty sub claim raises 401."""
        from fastapi import HTTPException

        secret = "test_secret_empty"
        token = _make_hs256_token(
            {"exp": int(time.time()) + 3600},  # No 'sub' or 'user_id'
            secret=secret,
        )

        with patch("middleware.auth.CLERK_ISSUER_URL", ""), patch(
            "middleware.auth.CLERK_SECRET_KEY", secret
        ), patch("middleware.auth.get_clerk_client", return_value=None):
            from middleware.auth import _verify_token

            with pytest.raises(HTTPException) as exc_info:
                await _verify_token(token)

        assert exc_info.value.status_code == 401

    # 11. get_current_user stores user in request.state ----------------------
    @pytest.mark.asyncio
    async def test_get_current_user_stores_in_request_state(self):
        """get_current_user attaches the user to request.state."""
        from middleware.auth import AuthenticatedUser

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_headers = MagicMock()
        mock_headers.get.side_effect = lambda key, default="": (
            "Bearer ignored" if key == "Authorization" else default
        )
        mock_request.headers = mock_headers
        mock_request.url.path = "/api/test"

        dev_user = AuthenticatedUser(id="dev_seed_user", email="dev@test.com")

        with patch("middleware.auth.DEV_MODE", True), patch(
            "middleware.auth.verify_auth", new_callable=AsyncMock, return_value=dev_user
        ):
            from middleware.auth import get_current_user

            # get_current_user expects Depends-injected credentials; pass None
            user = await get_current_user(mock_request, credentials=None)

        assert user.id == "dev_seed_user"
        assert mock_request.state.user == user

    # 12. JWKS cache TTL: after TTL, fresh fetch triggered -------------------
    @pytest.mark.asyncio
    async def test_jwks_cache_ttl_triggers_refresh(self):
        """After JWKS cache TTL expires, _get_jwks triggers rotation."""
        import middleware.auth as auth_mod

        original_cache = auth_mod._jwks_cache
        original_time = auth_mod._jwks_cache_time
        original_issuer = auth_mod.CLERK_ISSUER_URL

        try:
            # Set cache with an expired timestamp
            auth_mod._jwks_cache = {"keys": [{"kid": "old"}]}
            auth_mod._jwks_cache_time = time.time() - (auth_mod._JWKS_CACHE_TTL + 100)
            auth_mod.CLERK_ISSUER_URL = "https://test.clerk.accounts.dev"

            with patch.object(
                auth_mod, "_rotate_jwks", new_callable=AsyncMock
            ) as mock_rotate:
                mock_rotate.return_value = {"keys": [{"kid": "new"}]}
                result = await auth_mod._get_jwks()

            mock_rotate.assert_awaited_once()
            assert result == {"keys": [{"kid": "new"}]}
        finally:
            auth_mod._jwks_cache = original_cache
            auth_mod._jwks_cache_time = original_time
            auth_mod.CLERK_ISSUER_URL = original_issuer
