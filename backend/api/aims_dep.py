"""
Phase 25 (Gap G2) — flag-gated AIMS / OAuth perimeter auth dependency.
======================================================================

Brings the dormant `services.mcp_oauth_bridge.resolve_mcp_auth()` (Clerk JWT /
SPIFFE WIT-SVID / dev-PAT resolver, 49 tests, previously ZERO request-path
callers) live as a FastAPI router-level dependency.

Behaviour:
  - `VOS3_ENABLE_LIVE_AIMS_AUTH` OFF (default, dev/CI) -> dependency is a NO-OP
    (returns None); existing auth (`get_current_user` / `billing_guard`) is
    unchanged and the 439-test baseline is unaffected.
  - ON -> every guarded request must carry a resolvable
    `Authorization: Bearer <token>`. FAIL-CLOSED: missing/empty/non-bearer ->
    HTTP 401; unclassifiable/invalid/expired/wrong-protocol -> HTTP 403. No
    backend model interaction happens past a rejection.

HONEST SCOPE: this wires `resolve_mcp_auth` (the OAuth/token side). The separate
`services.aims_envelope` (IETF agent-identity envelope) primitive is NOT exercised
by this resolver and remains unwired — a distinct follow-up, not closed here.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger("vos3.security.aims")


def aims_auth_enabled() -> bool:
    """True iff the operator opted the live AIMS/OAuth perimeter gate ON."""
    return os.environ.get("VOS3_ENABLE_LIVE_AIMS_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def aims_auth_dependency(request: Request):
    """Router-level perimeter auth. Returns the resolved MCPAuthContext when the
    gate is ON and the token is valid; returns None when the gate is OFF
    (legacy access). Raises HTTP 401/403 (fail-closed) on any auth failure."""
    if not aims_auth_enabled():
        return None
    # Phase 31 (G7→G2 integration): require a hardware-attested platform before
    # resolving any session identity. No-op unless VOS3_ENABLE_TPM_ATTESTATION is
    # set; when set, an unattested/PCR-mismatched host raises AttestationDenied
    # (→ 403) so no token-backed identity is established on an untrusted platform.
    from services.tpm_attestation import attested_token_guard

    attested_token_guard()
    # Deferred import: keep the bridge off the hot import path when gate is OFF.
    from services.mcp_oauth_bridge import MCPAuthError, resolve_mcp_auth

    try:
        return resolve_mcp_auth(request.headers.get("authorization"))
    except MCPAuthError as exc:
        reason = getattr(exc, "reason", "bad_token")
        # missing/absent credential -> 401; present-but-invalid -> 403.
        status = 401 if reason == "missing_auth" else 403
        logger.warning(
            "[SECURITY][aims-auth] reject reason=%s status=%d path=%s",
            reason,
            status,
            getattr(getattr(request, "url", None), "path", "<unknown>"),
        )
        raise HTTPException(
            status_code=status,
            detail="Unauthorized" if status == 401 else "Forbidden",
        ) from exc


__all__ = ["aims_auth_enabled", "aims_auth_dependency"]
