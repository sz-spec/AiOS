# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
backend/middleware/csrf.py — W3.3 Tauri-IPC CSRF firewall.

Two-layer defense for the local FastAPI ↔ Tauri shell channel:

  1. **Handshake** — `/api/auth/csrf` is the only endpoint that hands out
     the per-process CSRF token. To obtain it, the caller must present
     an `X-Tauri-Handshake` header whose value matches the secret in
     `VOS3_TAURI_IPC_SECRET`. The Tauri Rust shell embeds this secret
     at compile time (or reads it from `~/.vos3/ipc.secret` on first
     run); a stray web browser running on the same machine cannot
     read it, so it cannot complete the handshake.

  2. **Mutation gate** — for every POST/PUT/PATCH/DELETE on `/api/*`
     the middleware compares `X-CSRF-Token` against the volatile
     server token using `hmac.compare_digest`. Mismatch → 403.

The CSRF token itself is generated **once at process start** and held
in module-scope. It is intentionally NOT persisted: a backend restart
forces the Tauri shell to re-handshake, which is the cheap+correct
recovery path.

The complementary `frontend/middleware.ts` double-submit cookie pattern
is preserved unchanged — that layer defends against browser-mediated
CSRF on the Next.js side. This file adds the backend gate so a request
that bypasses Next.js (direct curl on :8000) is still rejected.

Public path exemptions (no CSRF required):
  - GET requests of any kind (CSRF is for state-changing methods)
  - `/api/auth/csrf`        (the handshake itself)
  - `/api/webhooks/*`       (Clerk/Stripe — signed externally)
  - `/api/billing/webhook`  (Stripe — signed externally)
  - `/health`, `/docs`, `/openapi.json`, `/redoc`
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tokens — generated once, never reissued during this process lifetime.
# ---------------------------------------------------------------------------

# 32 bytes of cryptographic randomness → 43-char URL-safe string.
_CSRF_TOKEN: str = secrets.token_urlsafe(32)
_CSRF_TOKEN_BYTES: bytes = _CSRF_TOKEN.encode("utf-8")


def _resolve_handshake_secret() -> str:
    """Read the Tauri-IPC handshake secret.

    Precedence:
      1. `VOS3_TAURI_IPC_SECRET` env var (canonical — set by Tauri Rust
         shell or the operator before starting the backend)
      2. A securely-generated dev fallback that is regenerated on every
         backend start. Developers needing an explicit handshake value
         should provide the environment variable; secrets are not logged.

    Production refusal: if `ENVIRONMENT=production` and no env var is set,
    we refuse to operate (don't silently fall back to a known value the
    way the old `change_me` pattern did).
    """
    explicit = os.getenv("VOS3_TAURI_IPC_SECRET", "").strip()
    if explicit:
        return explicit

    if os.getenv("ENVIRONMENT", "development") == "production":
        raise RuntimeError(
            "VOS3_TAURI_IPC_SECRET must be set in production. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )

    # Dev fallback remains random per process and is never written to logs.
    fallback = secrets.token_urlsafe(32)
    logger.warning(
        "VOS3_TAURI_IPC_SECRET not set; generated an ephemeral development secret. "
        "Set the environment variable for an explicit local handshake value."
    )
    return fallback


_HANDSHAKE_SECRET: str = _resolve_handshake_secret()
_HANDSHAKE_SECRET_BYTES: bytes = _HANDSHAKE_SECRET.encode("utf-8")


# ---------------------------------------------------------------------------
# Public surface — handshake router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/csrf")
async def get_csrf_token(request: Request) -> JSONResponse:
    """Return the per-process CSRF token if the caller has the IPC secret.

    Tauri shell flow:
      GET /api/auth/csrf
      X-Tauri-Handshake: <VOS3_TAURI_IPC_SECRET>

      → 200 { "csrf_token": "<token>" }
    """
    presented = request.headers.get("X-Tauri-Handshake", "").encode("utf-8")
    if not hmac.compare_digest(presented, _HANDSHAKE_SECRET_BYTES):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Tauri-Handshake header",
        )
    return JSONResponse({"csrf_token": _CSRF_TOKEN})


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

_MUTABLE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths exempt from CSRF — either non-state-changing or signed externally.
#
# W4.2 — split into exact-match vs prefix to close the path-prefix bypass.
# Prior implementation used `startswith` for every entry, so a route like
# `/api/auth/csrf-rotate` or `/api/billing/webhook-drain` would silently
# match the exempt-list and skip the CSRF gate. Latent today (no such
# routes exist), but a foot-gun for the next dev who adds one.
#
# Rule of thumb:
#   - Use _EXEMPT_PATHS for any single concrete endpoint.
#   - Use _EXEMPT_PREFIXES only for *directory-shaped* exempts (must end
#     with "/", e.g. "/api/webhooks/" matches "/api/webhooks/clerk" but
#     NOT "/api/webhooksXYZ").
_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/api/auth/csrf",  # handshake itself; gated by X-Tauri-Handshake
        "/api/billing/webhook",  # Stripe — signed externally
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)

_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/webhooks/",  # Clerk / Svix — signed by HMAC at /webhooks/clerk
)


def _is_exempt(path: str) -> bool:
    """Return True iff `path` is whitelisted from the CSRF gate.

    Non-`/api/*` paths are always exempt (static, WebSocket, etc.).
    `/api/*` paths must match either an exact entry in _EXEMPT_PATHS or
    a directory prefix in _EXEMPT_PREFIXES. Bare-startswith matching is
    intentionally NOT used to avoid the W4.2 path-prefix bypass.
    """
    if not path.startswith("/api"):
        return True
    if path in _EXEMPT_PATHS:
        return True
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


async def csrf_middleware(request: Request, call_next):
    """Reject mutating /api/* requests that lack a matching X-CSRF-Token.

    Order: this middleware is registered AFTER content-size/security-headers
    so it runs late in the inbound chain. It only enforces on mutating
    methods; GET/HEAD/OPTIONS pass through untouched.
    """
    if request.method in _MUTABLE_METHODS and not _is_exempt(request.url.path):
        presented = request.headers.get("X-CSRF-Token", "").encode("utf-8")
        if not hmac.compare_digest(presented, _CSRF_TOKEN_BYTES):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "CSRF_VIOLATION",
                        "message": (
                            "Missing or invalid X-CSRF-Token. Complete the "
                            "handshake at GET /api/auth/csrf."
                        ),
                    }
                },
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Test helpers (do NOT use in production code paths)
# ---------------------------------------------------------------------------


def _peek_csrf_token_for_tests() -> str:
    """Test-only accessor for the volatile CSRF token."""
    return _CSRF_TOKEN


def _peek_handshake_secret_for_tests() -> str:
    """Test-only accessor for the Tauri handshake secret."""
    return _HANDSHAKE_SECRET


__all__ = [
    "router",
    "csrf_middleware",
    "_peek_csrf_token_for_tests",
    "_peek_handshake_secret_for_tests",
]
