"""
App Authentication Middleware
==============================
Validates app OAuth tokens, extracts granted scopes,
and enforces scope requirements per endpoint.

Tokens are stored as SHA-256 hashes to prevent plaintext leakage
in the event of a memory dump or database exposure.
"""

import hashlib
import os
from typing import Optional, Set

from fastapi import Request
from fastapi.responses import JSONResponse

# App tokens are stored as: sha256(app_id:token) -> granted_scopes
# In production, this would be backed by Redis or Convex
_app_tokens: dict[str, Set[str]] = {}


def _hash_token(app_id: str, token: str) -> str:
    """Compute SHA-256 hash of app_id:token for storage/lookup."""
    return hashlib.sha256(f"{app_id}:{token}".encode()).hexdigest()


def register_app_token(app_id: str, token: str, scopes: Set[str]):
    """Register an app token with granted scopes (called during consent flow).

    The raw token is hashed before storage — only the hash is kept in memory.
    """
    key = _hash_token(app_id, token)
    _app_tokens[key] = scopes


def _extract_app_credentials(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Extract app ID and token from request headers."""
    app_id = request.headers.get("x-vos3-app-id")
    auth = request.headers.get("authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = auth[7:]
    return app_id, token


async def app_auth_middleware(request: Request, call_next):
    """Authenticate app requests and attach granted scopes to request state."""
    path = request.url.path

    # Only apply to app API routes
    if not path.startswith("/api/apps/"):
        return await call_next(request)

    # Skip auth endpoints themselves
    if path.startswith("/api/apps/authorize") or path.startswith("/api/apps/register"):
        return await call_next(request)

    app_id, token = _extract_app_credentials(request)

    if not app_id or not token:
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Missing app credentials. Provide X-VOS3-App-Id header and Bearer token."
            },
        )

    # Validate token by comparing hashes
    key = _hash_token(app_id, token)
    scopes = _app_tokens.get(key)

    # Dev mode: accept any token with all scopes
    dev_mode = os.environ.get("DEV_MODE", "false").lower() == "true"
    if scopes is None and dev_mode:
        from core.app_scopes import AppScope

        scopes = {s.value for s in AppScope}

    if scopes is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid app token."},
        )

    # Attach scopes to request state
    request.state.app_id = app_id
    request.state.app_scopes = scopes

    return await call_next(request)
