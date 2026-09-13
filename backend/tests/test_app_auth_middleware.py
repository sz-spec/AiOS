"""Tests for middleware/app_auth.py — App auth middleware."""

import pytest
import os
from unittest.mock import MagicMock, AsyncMock, patch

from middleware.app_auth import (
    app_auth_middleware,
    register_app_token,
    _extract_app_credentials,
    _app_tokens,
)


class TestExtractAppCredentials:
    def test_extracts_header_and_bearer(self):
        request = MagicMock()
        request.headers = {
            "x-vos3-app-id": "app1",
            "authorization": "Bearer tok123",
        }
        app_id, token = _extract_app_credentials(request)
        assert app_id == "app1"
        assert token == "tok123"

    def test_missing_headers(self):
        request = MagicMock()
        request.headers = {}
        app_id, token = _extract_app_credentials(request)
        assert app_id is None
        assert token is None


class TestRegisterAppToken:
    def test_register_and_lookup(self):
        from middleware.app_auth import _hash_token

        register_app_token("testapp", "testtoken", {"vos3:entities:read"})
        hashed_key = _hash_token("testapp", "testtoken")
        assert hashed_key in _app_tokens
        assert "vos3:entities:read" in _app_tokens[hashed_key]
        # Cleanup
        del _app_tokens[hashed_key]


class TestAppAuthMiddleware:
    @pytest.mark.asyncio
    async def test_non_app_routes_pass_through(self):
        request = MagicMock()
        request.url.path = "/api/chat/send"
        call_next = AsyncMock(return_value="ok")
        result = await app_auth_middleware(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_authorize_route_passes_through(self):
        request = MagicMock()
        request.url.path = "/api/apps/authorize"
        call_next = AsyncMock(return_value="ok")
        result = await app_auth_middleware(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_401(self):
        request = MagicMock()
        request.url.path = "/api/apps/v1/entities"
        request.headers = {}
        call_next = AsyncMock()
        result = await app_auth_middleware(request, call_next)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_dev_mode_accepts_any_token(self):
        request = MagicMock()
        request.url.path = "/api/apps/v1/entities"
        request.headers = {
            "x-vos3-app-id": "dev-app",
            "authorization": "Bearer any-token",
        }
        request.state = MagicMock()
        call_next = AsyncMock(return_value="ok")

        with patch.dict(os.environ, {"DEV_MODE": "true"}):
            result = await app_auth_middleware(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_valid_registered_token(self):
        register_app_token("myapp", "realtoken", {"vos3:entities:read"})
        request = MagicMock()
        request.url.path = "/api/apps/v1/entities"
        request.headers = {
            "x-vos3-app-id": "myapp",
            "authorization": "Bearer realtoken",
        }
        request.state = MagicMock()
        call_next = AsyncMock(return_value="ok")

        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            result = await app_auth_middleware(request, call_next)
        assert result == "ok"
        assert request.state.app_scopes == {"vos3:entities:read"}
        # Cleanup
        from middleware.app_auth import _hash_token

        del _app_tokens[_hash_token("myapp", "realtoken")]

    @pytest.mark.asyncio
    async def test_invalid_token_non_dev_mode_returns_401(self):
        request = MagicMock()
        request.url.path = "/api/apps/v1/entities"
        request.headers = {
            "x-vos3-app-id": "badapp",
            "authorization": "Bearer badtoken",
        }
        call_next = AsyncMock()

        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            result = await app_auth_middleware(request, call_next)
        assert result.status_code == 401
