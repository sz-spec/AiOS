# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
backend/middleware/security_headers.py — Resilience-Matrix F7.

Sets Content-Security-Policy + sibling response headers on every API
response. Resolves the gap surfaced in the CEO Audit Report (R2 §51 —
"CSP `unsafe-eval` regression") and the spec's §3.3 hardening claim.

The policy is intentionally strict for /api/ JSON responses (which
should not load any third-party scripts). Frontend HTML is served
separately by Next.js and gets its own per-page CSP via Next's headers
config.
"""

from __future__ import annotations

from fastapi import Request

# Locked to `'self'` only — no inline scripts, no unsafe-eval, no
# remote origins. The /api/ surface returns JSON; nothing should embed
# script. Adjust only with a documented exception in this file.
_CSP_DIRECTIVES = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'"
)


async def security_headers_middleware(request: Request, call_next):
    """Append CSP + adjacent hardening headers to every response."""
    response = await call_next(request)

    # CSP — primary defense.
    response.headers["Content-Security-Policy"] = _CSP_DIRECTIVES

    # Defense-in-depth siblings.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # HSTS only when the request was over HTTPS — avoid pinning HSTS
    # for local-dev plaintext.
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )

    return response
