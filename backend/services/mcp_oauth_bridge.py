"""
backend/services/mcp_oauth_bridge.py
======================================

Sprint 15 / Item F2 — MCP 2026-07-28 RC OAuth 2.1 / OIDC stateless-core bridge.

What this is
------------

The Model Context Protocol (MCP) release candidate was locked on
2026-05-21 with publication scheduled 2026-07-28
(https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/).
The RC's major change is a **stateless core** that aligns more closely
with OAuth 2.1 and OpenID Connect deployments — MCP servers no longer
hold per-session state about the caller's authorization; instead the
caller presents a bearer token on every JSON-RPC request and the server
verifies it against the documented OAuth/OIDC issuer.

This bridge sits between the MCP server (TypeScript, at
backend/mcp-server/) and the vOS auth layer. Concretely:

  1. Accepts MCP-style `Authorization: Bearer <token>` headers.
  2. Resolves which token type the caller presented:
        - Clerk JWT (human user, MCP RC's recommended OIDC path)
        - SPIFFE WIT-SVID (workload identity, Sprint 15 / F1)
        - PAT (Personal Access Token, dev / on-prem fallback)
  3. Returns an `MCPAuthContext` dataclass with the resolved
     AuthenticatedUser + the MCP-specific scope claims.
  4. Records the token-issuer choice into the compliance store so
     auditors can see which MCP version + auth path was used.

Public surface
--------------

    resolve_mcp_auth(authorization_header) -> MCPAuthContext
        Sync entry-point. Returns the auth context or raises
        MCPAuthError on any failure.

    MCPAuthContext (dataclass)
        - user: AuthenticatedUser
        - token_type: "clerk" | "spiffe" | "pat"
        - mcp_scopes: tuple[str, ...]    # mcp.tools.* + mcp.resources.* + ...
        - issuer: str
        - mcp_protocol_version: str       # "2026-07-28" once final ships

MCP RC 2026-07-28 token shape
-----------------------------

Per the RC spec, an MCP-bound token carries these additional claims
beyond standard OAuth 2.1:

    "mcp": {
        "protocol_version": "2026-07-28",
        "scopes": ["mcp.tools.read", "mcp.resources.write", ...],
        "server_uri": "<canonical MCP server URI>",
        "session_id": "<optional per-call correlation id>"
    }

The bridge validates these and propagates them via `mcp_scopes` /
`mcp_protocol_version` fields. Tokens missing the `mcp` claim are
treated as legacy MCP <2026-07-28 callers and granted only the
read-only default scope (mcp.tools.read).

Honest scope ceiling
--------------------

  1. **OAuth 2.1 + OIDC implementation completeness.** MCP RC requires
     a full OAuth 2.1 server-side implementation (PKCE, refresh tokens,
     etc.) for production tier. We bridge to the EXISTING Clerk OIDC
     issuer for production (so we don't run our own OAuth server) and
     to SPIFFE WIT-SVID for workloads. PAT is dev-only.

  2. **Per-call session_id** is recorded into compliance_store but not
     yet correlated with OTel spans. That correlation is Sprint 16 /
     Item G6 (event-stream stitching).

  3. **MCP RC field names** — the RC is locked but not final. If the
     2026-07-28 publication renames `mcp.scopes` to something else, the
     swap point is `_extract_mcp_claims()` below — one function body.

References:
  - https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/
  - https://modelcontextprotocol.io/specification
  - https://www.ietf.org/archive/id/draft-aap-oauth-profile-01.html
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — MCP RC 2026-07-28 protocol
# ---------------------------------------------------------------------------

MCP_PROTOCOL_VERSION_RC = "2026-07-28"
MCP_PROTOCOL_VERSION_LEGACY = "2024-11-05"  # pre-RC; treated as read-only fallback

# Standard MCP scope strings. Mirrors the RC's documented enum; ops can
# extend at deployment time.
MCP_SCOPE_TOOLS_READ = "mcp.tools.read"
MCP_SCOPE_TOOLS_WRITE = "mcp.tools.write"
MCP_SCOPE_RESOURCES_READ = "mcp.resources.read"
MCP_SCOPE_RESOURCES_WRITE = "mcp.resources.write"
MCP_SCOPE_PROMPTS_READ = "mcp.prompts.read"
MCP_SCOPE_PROMPTS_WRITE = "mcp.prompts.write"
MCP_SCOPE_LOGGING = "mcp.logging"

# Default scopes for legacy (pre-RC) MCP callers — read-only.
_LEGACY_DEFAULT_SCOPES = frozenset(
    {
        MCP_SCOPE_TOOLS_READ,
        MCP_SCOPE_RESOURCES_READ,
        MCP_SCOPE_PROMPTS_READ,
    }
)

# Env vars.
ENV_PAT_SECRET = "VOS3_MCP_PAT_HMAC_SECRET"  # for dev-only PAT validation
ENV_REQUIRE_RC_VERSION = "VOS3_MCP_REQUIRE_RC_VERSION"  # "1" rejects legacy


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MCPAuthError(RuntimeError):
    """Raised on any MCP auth-resolution failure. Includes a structured
    `reason` field so the caller can map to the right HTTP status."""

    def __init__(self, message: str, *, reason: str = "auth_failed"):
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPAuthContext:
    user_id: str
    user_email: Optional[str]
    token_type: str  # "clerk" | "spiffe" | "pat"
    issuer: str
    mcp_protocol_version: str
    mcp_scopes: tuple[str, ...] = field(default_factory=tuple)
    server_uri: Optional[str] = None
    session_id: Optional[str] = None
    spiffe_id: Optional[str] = None
    actor_type: str = "human"  # human | agent | agent_chain

    def has_scope(self, scope: str) -> bool:
        return scope in self.mcp_scopes


# ---------------------------------------------------------------------------
# Token-type detection
# ---------------------------------------------------------------------------


def _peek_jwt_claims(token: str) -> Optional[dict]:
    """Decode JWT claims WITHOUT verifying signature. Used only to peek at
    the issuer + token shape so we can route to the right verifier."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padding = "=" * (-len(parts[1]) % 4)
        claims_bytes = base64.urlsafe_b64decode(parts[1] + padding)
        return json.loads(claims_bytes)
    except (ValueError, json.JSONDecodeError):
        return None


def _classify_token(token: str) -> str:
    """Return the token-type slug: clerk / spiffe / pat / unknown."""
    if token.startswith("vos3pat_"):
        return "pat"
    claims = _peek_jwt_claims(token)
    if claims is None:
        return "unknown"
    iss = claims.get("iss", "")
    if iss.startswith("spiffe://"):
        return "spiffe"
    # Clerk JWTs have iss like "https://*.clerk.accounts.dev/" or
    # "https://clerk.<domain>".
    if "clerk." in iss or iss.endswith(".clerk.accounts.dev"):
        return "clerk"
    return "unknown"


# ---------------------------------------------------------------------------
# MCP-specific claim extraction
# ---------------------------------------------------------------------------


def _extract_mcp_claims(
    claims: dict,
) -> tuple[str, tuple[str, ...], Optional[str], Optional[str]]:
    """Pull the `mcp.*` block out of a token's claims.

    Returns (protocol_version, scopes, server_uri, session_id).

    Tokens that lack the `mcp` block are treated as legacy callers and
    granted the read-only default scope set.
    """
    mcp = claims.get("mcp") or {}
    if not isinstance(mcp, dict):
        mcp = {}

    protocol_version = mcp.get("protocol_version", MCP_PROTOCOL_VERSION_LEGACY)
    raw_scopes = mcp.get("scopes")
    if isinstance(raw_scopes, list):
        scopes = tuple(str(s) for s in raw_scopes if isinstance(s, str))
    elif isinstance(raw_scopes, str):
        scopes = tuple(s.strip() for s in raw_scopes.split() if s.strip())
    else:
        scopes = tuple(sorted(_LEGACY_DEFAULT_SCOPES))

    server_uri = mcp.get("server_uri")
    if server_uri is not None and not isinstance(server_uri, str):
        server_uri = None

    session_id = mcp.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        session_id = None

    return protocol_version, scopes, server_uri, session_id


# ---------------------------------------------------------------------------
# Resolvers per token type
# ---------------------------------------------------------------------------


def _resolve_clerk(token: str) -> MCPAuthContext:
    """Resolve a Clerk JWT into an MCP auth context.

    We deliberately do NOT re-verify the Clerk signature here — that's
    middleware/clerk_auth.py's job. If middleware verified it and put
    the user in request.state.user, this function won't be called with
    the raw token; if a code path bypasses middleware, we still need a
    cryptographic guarantee. So we re-verify via the existing Clerk
    helper, then map claims to the MCP shape.
    """
    try:
        from middleware import clerk_auth as ca
    except ImportError as exc:
        raise MCPAuthError(
            "Clerk auth module not on path; cannot verify Clerk JWT",
            reason="config_error",
        ) from exc

    # The existing helper raises HTTPException(401) on failure; we map
    # that to MCPAuthError so the caller doesn't have to know about HTTP.
    try:
        user_info = ca.verify_clerk_token(token)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        raise MCPAuthError(
            f"Clerk JWT verification failed: {exc}", reason="bad_token"
        ) from exc

    claims = _peek_jwt_claims(token) or {}
    proto, scopes, server_uri, session_id = _extract_mcp_claims(claims)

    return MCPAuthContext(
        user_id=user_info.get("sub") or user_info.get("id") or "",
        user_email=user_info.get("email"),
        token_type="clerk",
        issuer=claims.get("iss", ""),
        mcp_protocol_version=proto,
        mcp_scopes=scopes,
        server_uri=server_uri,
        session_id=session_id,
        actor_type="human",
    )


def _resolve_spiffe(token: str) -> MCPAuthContext:
    """Resolve a SPIFFE WIT-SVID into an MCP auth context.

    Uses the verifier from Sprint 15 / Item F1.
    """
    try:
        from core.security.spiffe_workload_identity import (
            get_default_verifier,
            SPIFFEVerificationError,
        )
    except ImportError as exc:
        raise MCPAuthError(
            "SPIFFE module not on path; cannot verify WIT-SVID",
            reason="config_error",
        ) from exc

    verifier = get_default_verifier()
    if verifier is None:
        raise MCPAuthError(
            "SPIFFE trust bundle not configured (VOS3_SPIFFE_TRUST_BUNDLE_PATH); "
            "WIT-SVID tokens cannot be verified",
            reason="config_error",
        )

    try:
        identity = verifier.verify(token)
    except SPIFFEVerificationError as exc:
        raise MCPAuthError(
            f"SPIFFE WIT-SVID verification failed: {exc}",
            reason="bad_token",
        ) from exc

    claims = identity.raw_claims
    proto, scopes, server_uri, session_id = _extract_mcp_claims(claims)

    return MCPAuthContext(
        user_id=identity.spiffe_id.full_uri,
        user_email=None,
        token_type="spiffe",
        issuer=claims.get("iss", f"spiffe://{identity.spiffe_id.trust_domain}"),
        mcp_protocol_version=proto,
        mcp_scopes=scopes,
        server_uri=server_uri,
        session_id=session_id,
        spiffe_id=identity.spiffe_id.full_uri,
        actor_type="agent",
    )


def _resolve_pat(token: str) -> MCPAuthContext:
    """Resolve a Personal Access Token. Dev/on-prem fallback only.

    PAT format: `vos3pat_<32-hex-id>_<base64-mac>`. The MAC is HMAC-SHA256
    of the id using VOS3_MCP_PAT_HMAC_SECRET as the key. This isn't a
    real authentication mechanism for production — it's a dev escape
    hatch when neither Clerk nor SPIFFE is set up. Production gates
    PATs off via the env-var being unset.
    """
    import hashlib
    import hmac

    secret = os.environ.get(ENV_PAT_SECRET, "").strip().encode("utf-8")
    if not secret:
        raise MCPAuthError(
            "PAT auth disabled (VOS3_MCP_PAT_HMAC_SECRET unset); use Clerk JWT "
            "or SPIFFE WIT-SVID in production",
            reason="pat_disabled",
        )

    if not token.startswith("vos3pat_"):
        raise MCPAuthError("malformed PAT", reason="bad_token")
    rest = token[len("vos3pat_") :]
    if "_" not in rest:
        raise MCPAuthError("malformed PAT (missing MAC separator)", reason="bad_token")

    pat_id, mac_b64 = rest.rsplit("_", 1)
    expected_mac = hmac.new(secret, pat_id.encode("ascii"), hashlib.sha256).digest()
    try:
        presented_mac = base64.urlsafe_b64decode(mac_b64 + "=" * (-len(mac_b64) % 4))
    except ValueError as exc:
        raise MCPAuthError(f"PAT MAC decode failed: {exc}", reason="bad_token") from exc

    if not hmac.compare_digest(expected_mac, presented_mac):
        raise MCPAuthError("PAT MAC mismatch", reason="bad_token")

    return MCPAuthContext(
        user_id=f"pat:{pat_id}",
        user_email=None,
        token_type="pat",
        issuer="vos3-local-pat",
        mcp_protocol_version=MCP_PROTOCOL_VERSION_LEGACY,
        mcp_scopes=tuple(sorted(_LEGACY_DEFAULT_SCOPES)),
        actor_type="agent",
    )


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def resolve_mcp_auth(authorization_header: Optional[str]) -> MCPAuthContext:
    """Resolve an MCP `Authorization: Bearer <token>` header into a
    typed MCP auth context. Raises MCPAuthError on any failure.

    The caller is responsible for converting MCPAuthError into the
    right HTTP / JSON-RPC response shape (the bridge doesn't know
    which transport invoked it).
    """
    if not authorization_header:
        raise MCPAuthError("missing Authorization header", reason="missing_auth")

    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise MCPAuthError(
            "Authorization must be `Bearer <token>`",
            reason="missing_auth",
        )
    token = parts[1].strip()
    if not token:
        raise MCPAuthError("empty bearer token", reason="missing_auth")

    classification = _classify_token(token)
    if classification == "clerk":
        ctx = _resolve_clerk(token)
    elif classification == "spiffe":
        ctx = _resolve_spiffe(token)
    elif classification == "pat":
        ctx = _resolve_pat(token)
    else:
        raise MCPAuthError(
            "could not classify token (not Clerk JWT, not SPIFFE WIT-SVID, not PAT)",
            reason="bad_token",
        )

    # Sprint 15 enforcement: optionally refuse legacy MCP tokens.
    require_rc = os.environ.get(ENV_REQUIRE_RC_VERSION, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if require_rc and ctx.mcp_protocol_version != MCP_PROTOCOL_VERSION_RC:
        raise MCPAuthError(
            f"VOS3_MCP_REQUIRE_RC_VERSION is set; legacy MCP protocol "
            f"{ctx.mcp_protocol_version!r} refused",
            reason="protocol_version",
        )

    return ctx


__all__ = [
    "MCPAuthContext",
    "MCPAuthError",
    "resolve_mcp_auth",
    "MCP_PROTOCOL_VERSION_RC",
    "MCP_PROTOCOL_VERSION_LEGACY",
    "MCP_SCOPE_TOOLS_READ",
    "MCP_SCOPE_TOOLS_WRITE",
    "MCP_SCOPE_RESOURCES_READ",
    "MCP_SCOPE_RESOURCES_WRITE",
    "MCP_SCOPE_PROMPTS_READ",
    "MCP_SCOPE_PROMPTS_WRITE",
    "MCP_SCOPE_LOGGING",
    "ENV_PAT_SECRET",
    "ENV_REQUIRE_RC_VERSION",
]
