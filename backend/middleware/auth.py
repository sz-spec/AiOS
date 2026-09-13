"""
Clerk Authentication Middleware
================================
JWT verification for Clerk authentication.

Environment Variables:
- CLERK_SECRET_KEY: Clerk secret key for JWT verification
- CLERK_PUBLISHABLE_KEY: Clerk publishable key (optional, for client-side)

Usage:
    from middleware.auth import get_current_user, require_permission

    @router.get("/protected")
    async def protected_endpoint(user: AuthenticatedUser = Depends(get_current_user)):
        return {"user_id": user.id}

    @router.get("/admin")
    async def admin_endpoint(user: AuthenticatedUser = Depends(require_permission("admin:full"))):
        return {"admin": True}
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# JWT verification
try:
    import jwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    jwt = None

# Clerk SDK (optional)
try:
    from clerk_backend_api import Clerk

    HAS_CLERK = True
except ImportError:
    HAS_CLERK = False
    Clerk = None


# Configuration
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "")
CLERK_PUBLISHABLE_KEY = os.getenv("CLERK_PUBLISHABLE_KEY", "")
# Dev mode requires BOTH: no clerk key AND explicit opt-in via env var
_allow_dev_mode = os.getenv("VOS3_ALLOW_DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_MODE = not CLERK_SECRET_KEY and _allow_dev_mode

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
if ENVIRONMENT == "production" and not CLERK_SECRET_KEY:
    raise RuntimeError(
        "FATAL: CLERK_SECRET_KEY is required when ENVIRONMENT=production. "
        "Refusing to start with authentication disabled."
    )

if not CLERK_SECRET_KEY and not _allow_dev_mode:
    # Neither auth configured nor dev mode opted-in; auth will reject all requests
    print(
        "AUTH: WARNING — No CLERK_SECRET_KEY and VOS3_ALLOW_DEV_MODE not set. "
        "All requests will require auth (which will fail). "
        "Set VOS3_ALLOW_DEV_MODE=true for local development."
    )
    DEV_MODE = False

if DEV_MODE:
    print("AUTH: Running in dev mode (VOS3_ALLOW_DEV_MODE=true, no authentication)")


# =========================================================================
# W5.3 — Offline local-first auth fallback
# =========================================================================
#
# When the backend is running under a community / local-first profile, it
# cannot reach Clerk to fetch JWKS or validate signatures. We expose a
# narrowly-scoped fallback that:
#
#   1. Decodes the Bearer token without verifying its signature
#   2. Resolves the caller to a LOCAL user row from the W5.1 SQLite store
#   3. Builds an AuthenticatedUser from the claims + local row
#
# This is STRICTLY GATED — all of the following must hold for the
# fallback to engage:
#
#   - VOS3_LOCALITY_PREFERENCE == "local-first"
#   - VOS_PROFILE in {"community", "local"}        (or VOS3_OFFLINE_AUTH=true)
#   - ENVIRONMENT != "production"
#
# Misconfiguring any one of these to enable the fallback in a production
# environment would silently degrade JWT auth to a no-op signature check.
# The production-environment guard (ENVIRONMENT == "production") is a
# hard refusal, regardless of the other env values. Boot-time logging
# announces when the fallback is active.

_OFFLINE_AUTH_ENV = os.getenv("VOS3_OFFLINE_AUTH", "").lower() in ("true", "1", "yes")
_VOS_PROFILE = os.getenv("VOS_PROFILE", "").lower()
_LOCALITY_PREFERENCE = os.getenv("VOS3_LOCALITY_PREFERENCE", "").lower()


def _offline_auth_active() -> bool:
    """Return True iff the local-first offline-auth fallback should engage.

    Re-reads env on every call so tests + dynamic configs can flip the
    behaviour without forcing a module reimport.
    """
    env = os.getenv("ENVIRONMENT", "development")
    if env == "production":
        # Hard refusal — production NEVER takes the offline fallback,
        # regardless of any other env values.
        return False
    locality = os.getenv("VOS3_LOCALITY_PREFERENCE", "").lower()
    if locality != "local-first":
        return False
    profile = os.getenv("VOS_PROFILE", "").lower()
    explicit = os.getenv("VOS3_OFFLINE_AUTH", "").lower() in ("true", "1", "yes")
    return explicit or profile in ("community", "local")


# Loud boot-time signal so an operator can spot misconfiguration before
# the first request lands.
if _offline_auth_active():
    print(
        "AUTH: W5.3 OFFLINE FALLBACK ACTIVE — "
        "Bearer tokens are decoded without signature verification and "
        "users are resolved from the local SQLite store. "
        "Production refusal: ENVIRONMENT=production disables this."
    )


# Security scheme
security = HTTPBearer(auto_error=False)


# =========================================================================
# [RESTORED-FROM-LOGS] EU AI Act Article 12 — Region Detection
# Source: 2026-04-25 forensic logs (Phase 12 commit 40f50ef)
# =========================================================================
# 27 EU member states + EEA (NO, IS, LI). When a user's IP country falls in
# this set, EU AI Act compliance forces local-only inference unless the user
# has explicitly opted into "Global Cloud Processing" via Clerk metadata.

_EU_COUNTRY_CODES: frozenset[str] = frozenset(
    {
        # EU 27
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        # EEA
        "NO",
        "IS",
        "LI",
    }
)

_REGION_HEADER_PRIORITY: tuple[str, ...] = (
    "cf-ipcountry",  # Cloudflare
    "x-vercel-ip-country",  # Vercel
    "x-appengine-country",  # Google App Engine
    "x-region",  # Internal proxy override
)


def _detect_region_code(request: "Request") -> Optional[str]:
    """[RESTORED-FROM-LOGS] Read the user's region code from edge-proxy headers.

    Priority order is fixed. Returns ISO 3166-1 alpha-2 country code,
    upper-cased, or None if no header is present. Two-letter only —
    longer values (e.g., "United States") are rejected as non-conforming.
    """
    for hdr in _REGION_HEADER_PRIORITY:
        val = request.headers.get(hdr, "").strip().upper()
        if len(val) == 2 and val.isalpha():
            return val
    return None


def _is_eu_region(country_code: Optional[str]) -> bool:
    """[RESTORED-FROM-LOGS] Return True if the country code is an EU/EEA member."""
    return country_code in _EU_COUNTRY_CODES if country_code else False


@dataclass
class AuthenticatedUser:
    """Authenticated user from JWT token."""

    id: str  # Clerk user ID (sub claim)
    email: Optional[str] = None
    org_id: Optional[str] = None
    org_role: Optional[str] = None
    permissions: list[str] = None
    metadata: dict = None
    convex_user_id: Optional[str] = None  # Resolved Convex _id for this user

    # [RESTORED-FROM-LOGS] EU AI Act Article 12 — populated by `verify_auth()`
    # from edge headers. `is_eu_region` is True when the request's IP country
    # is in the EU/EEA. `global_cloud_consent` is True when the user has
    # explicitly opted in to non-local inference (read from Clerk metadata).
    region_code: Optional[str] = None
    is_eu_region: bool = False
    global_cloud_consent: bool = False

    # Sprint 15 / Item F1 — SPIFFE WIT-SVID workload identity. When the
    # caller presents a workload-identity token (via the Sprint 15 SPIFFE
    # path in clerk_auth.py), this field carries the SPIFFE ID URI
    # (spiffe://<trust-domain>/<workload-path>). None when the caller used
    # the human-user Clerk JWT path. See backend/core/security/
    # spiffe_workload_identity.py for the verifier.
    spiffe_id: Optional[str] = None
    spiffe_trust_domain: Optional[str] = None

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = []
        if self.metadata is None:
            self.metadata = {}

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        if "admin:full" in self.permissions:
            return True
        return permission in self.permissions

    def requires_local_inference(self) -> bool:
        """[RESTORED-FROM-LOGS] EU AI Act Article 12: True when this user must be
        served by a local / sovereign-cloud model only. Returns False if the
        user has explicit opt-in consent for global cloud processing."""
        return self.is_eu_region and not self.global_cloud_consent


# Clerk client (lazy initialization)
_clerk_client: Optional[Clerk] = None


def get_clerk_client() -> Optional[Clerk]:
    """Get Clerk client (lazy initialization)."""
    global _clerk_client
    if _clerk_client is None and HAS_CLERK and CLERK_SECRET_KEY:
        _clerk_client = Clerk(bearer_auth=CLERK_SECRET_KEY)
    return _clerk_client


# =========================================================================
# JWKS Cache — for RS256 Clerk JWT validation
# =========================================================================

CLERK_ISSUER_URL = os.getenv("CLERK_ISSUER_URL", "")

# Primary and previous JWKS key sets
_jwks_cache: dict = {}
_jwks_previous: dict = {}
_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 3600  # 1 hour
_JWKS_REFRESH_INTERVAL = 3000  # 50 minutes — proactive refresh before TTL
_JWKS_GRACE_PERIOD = 300  # 5 minutes — keep old keys for in-flight tokens
_JWKS_FORCE_FETCH_COOLDOWN = 30  # Rate-limit forced fetches to once per 30s
_JWKS_RETRY_MAX = 3
_JWKS_RETRY_BACKOFF_BASE = 1  # Exponential backoff: 1s, 2s, 4s

_jwks_previous_time: float = 0  # When _jwks_previous was demoted
_jwks_last_force_fetch: float = 0  # Last forced fetch timestamp

# Connection pooling — module-level singleton httpx.AsyncClient
_http_client: Optional["httpx.AsyncClient"] = (
    None  # noqa: F821 - httpx imported lazily inside _get_http_client
)
_http_client_lock: Optional[asyncio.Lock] = None

# Background refresh task handle
_jwks_refresh_task: Optional[asyncio.Task] = None

# B-HIGH-15: Bounded Convex user ID resolution cache with TTL.
# Prevents unbounded memory growth over extended operation.
_USER_ID_CACHE_MAX = 10000
_USER_ID_CACHE_TTL = 3600  # seconds
_user_id_cache: dict[str, tuple[str, float]] = {}  # clerk_id -> (convex_id, timestamp)


# Resilience-Matrix F8 — JTI replay protection cache.
# Maps jti -> expiry epoch. We keep at most _JTI_CACHE_MAX entries; on
# overflow, the oldest entry is evicted regardless of expiry. Each
# successful presentation registers the jti; a re-presentation hits the
# cache and is rejected. The cache is in-process; a multi-worker
# deployment should swap this for a Redis-backed implementation in the
# same shape.
_JTI_CACHE_MAX = 50_000
_jti_cache: dict[str, float] = {}


def _jti_register(jti: str, exp: Optional[int]) -> bool:
    """Register a JWT id; return False if it has been seen before.

    `exp` is the JWT's expiry (epoch seconds). The entry self-evicts
    after exp; if `exp` is missing, we keep the entry for 24h as a
    conservative replay window.
    """
    if not isinstance(jti, str) or not jti:
        # Defensive — non-string jti: skip the gate, do not raise.
        return True
    now = time.time()
    # Lazy expiry sweep — drop expired entries on each call.
    if _jti_cache:
        # Walking the dict is bounded; the cache is capped.
        expired = [k for k, exp_ts in _jti_cache.items() if exp_ts <= now]
        for k in expired:
            del _jti_cache[k]
    if jti in _jti_cache:
        return False
    if len(_jti_cache) >= _JTI_CACHE_MAX:
        # Evict the oldest by insertion order (dict preserves it).
        oldest = next(iter(_jti_cache))
        del _jti_cache[oldest]
    expiry = float(exp) if exp is not None else now + 86400.0
    _jti_cache[jti] = expiry
    return True


def _cache_get(clerk_id: str) -> Optional[str]:
    """Get a cached user ID, respecting TTL."""
    entry = _user_id_cache.get(clerk_id)
    if entry is None:
        return None
    convex_id, ts = entry
    if time.time() - ts > _USER_ID_CACHE_TTL:
        del _user_id_cache[clerk_id]
        return None
    return convex_id


def _cache_set(clerk_id: str, convex_id: str) -> None:
    """Cache a user ID with eviction when over capacity."""
    if len(_user_id_cache) >= _USER_ID_CACHE_MAX:
        # Evict oldest entry
        oldest_key = next(iter(_user_id_cache))
        del _user_id_cache[oldest_key]
    _user_id_cache[clerk_id] = (convex_id, time.time())


def _get_http_lock() -> asyncio.Lock:
    """Get or create the asyncio lock (must be called within an event loop)."""
    global _http_client_lock
    if _http_client_lock is None:
        _http_client_lock = asyncio.Lock()
    return _http_client_lock


async def _get_http_client() -> (
    "httpx.AsyncClient"
):  # noqa: F821 - httpx imported lazily inside _get_http_client
    """Lazy-initialize and return a singleton httpx.AsyncClient with connection pooling."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    lock = _get_http_lock()
    async with lock:
        # Double-check after acquiring lock
        if _http_client is not None and not _http_client.is_closed:
            return _http_client
        import httpx

        _http_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        return _http_client


async def _fetch_jwks_with_retry() -> Optional[dict]:
    """Fetch JWKS from Clerk with retry logic (3 attempts, exponential backoff).

    Returns the parsed JSON response, or None on total failure.
    """
    if not CLERK_ISSUER_URL:
        return None

    url = f"{CLERK_ISSUER_URL}/.well-known/jwks.json"
    client = await _get_http_client()

    for attempt in range(_JWKS_RETRY_MAX):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            wait = _JWKS_RETRY_BACKOFF_BASE * (2**attempt)
            if attempt < _JWKS_RETRY_MAX - 1:
                logger.warning(
                    "JWKS fetch attempt %d/%d failed (%s), retrying in %ds",
                    attempt + 1,
                    _JWKS_RETRY_MAX,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "JWKS fetch failed after %d attempts: %s",
                    _JWKS_RETRY_MAX,
                    exc,
                )
    return None


async def _rotate_jwks() -> dict:
    """Fetch fresh JWKS and rotate caches (current -> previous)."""
    global _jwks_cache, _jwks_previous, _jwks_cache_time, _jwks_previous_time

    new_data = await _fetch_jwks_with_retry()
    if new_data is None:
        # Fetch failed entirely — keep stale cache
        return _jwks_cache

    now = time.time()
    # Promote current to previous for graceful rollover
    if _jwks_cache:
        _jwks_previous = _jwks_cache
        _jwks_previous_time = now
    _jwks_cache = new_data
    _jwks_cache_time = now
    logger.info("JWKS rotated successfully at %.0f", now)
    return _jwks_cache


async def _jwks_background_refresh_loop() -> None:
    """Background loop that proactively refreshes JWKS before TTL expires."""
    while True:
        try:
            await asyncio.sleep(_JWKS_REFRESH_INTERVAL)
            logger.info("JWKS background refresh triggered")
            await _rotate_jwks()
        except asyncio.CancelledError:
            logger.info("JWKS background refresh task cancelled")
            break
        except Exception as exc:
            logger.warning("JWKS background refresh error: %s", exc)
            # Continue loop — next interval will retry


async def init_jwks_cache() -> None:
    """Pre-warm JWKS cache and start background refresh task.

    Call during application startup (e.g., in FastAPI lifespan):
        await init_jwks_cache()
    """
    global _jwks_refresh_task

    if not CLERK_ISSUER_URL:
        logger.info("JWKS init skipped: CLERK_ISSUER_URL not set")
        return

    # Warm the cache immediately
    await _rotate_jwks()
    logger.info("JWKS cache warmed on startup")

    # Start background refresh (idempotent — only one task)
    if _jwks_refresh_task is None or _jwks_refresh_task.done():
        _jwks_refresh_task = asyncio.create_task(_jwks_background_refresh_loop())
        _jwks_refresh_task.add_done_callback(
            lambda t: (
                logger.error("JWKS refresh task crashed: %s", t.exception())
                if not t.cancelled() and t.exception()
                else None
            )
        )
        logger.info(
            "JWKS background refresh task started (interval=%ds)",
            _JWKS_REFRESH_INTERVAL,
        )


async def shutdown_jwks_cache() -> None:
    """Cancel background refresh and close HTTP client.

    Call during application shutdown (e.g., in FastAPI lifespan):
        await shutdown_jwks_cache()
    """
    global _jwks_refresh_task, _http_client

    if _jwks_refresh_task is not None and not _jwks_refresh_task.done():
        _jwks_refresh_task.cancel()
        try:
            await _jwks_refresh_task
        except asyncio.CancelledError:
            pass
        _jwks_refresh_task = None
        logger.info("JWKS background refresh task stopped")

    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None
        logger.info("JWKS HTTP client closed")


async def _get_jwks() -> dict:
    """Return cached JWKS data, triggering rotation if TTL exceeded."""
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache

    if not CLERK_ISSUER_URL:
        return {}

    # TTL expired (background task may have been delayed or not started)
    return await _rotate_jwks()


async def _force_fetch_jwks_if_allowed() -> Optional[dict]:
    """Force a fresh JWKS fetch, rate-limited to once per 30 seconds.

    Returns fresh JWKS data if fetched, or None if rate-limited or failed.
    """
    global _jwks_last_force_fetch
    now = time.time()
    if (now - _jwks_last_force_fetch) < _JWKS_FORCE_FETCH_COOLDOWN:
        logger.info(
            "JWKS force-fetch rate-limited (last=%.0fs ago)",
            now - _jwks_last_force_fetch,
        )
        return None
    _jwks_last_force_fetch = now
    logger.info("JWKS force-fetch triggered for unknown kid")
    return await _rotate_jwks()


def _find_key_in_jwks(jwks_data: dict, kid: str) -> Optional[dict]:
    """Find a JWK by kid in a JWKS key set."""
    if not jwks_data or "keys" not in jwks_data:
        return None
    for key_data in jwks_data["keys"]:
        if key_data.get("kid") == kid:
            return key_data
    return None


async def _resolve_jwk_for_kid(kid: str) -> Optional[dict]:
    """Resolve a JWK for the given kid with graceful fallback.

    Lookup order:
    1. Current JWKS cache
    2. Previous JWKS cache (within grace period)
    3. Force-fetch fresh JWKS (rate-limited)
    """
    # 1. Check current cache
    jwks_data = await _get_jwks()
    key = _find_key_in_jwks(jwks_data, kid)
    if key is not None:
        return key

    # 2. Check previous cache (within grace period)
    now = time.time()
    if _jwks_previous and (now - _jwks_previous_time) < _JWKS_GRACE_PERIOD:
        key = _find_key_in_jwks(_jwks_previous, kid)
        if key is not None:
            logger.info("kid=%s resolved from previous JWKS (grace period)", kid)
            return key

    # 3. Force-fetch (rate-limited)
    fresh_data = await _force_fetch_jwks_if_allowed()
    if fresh_data is not None:
        key = _find_key_in_jwks(fresh_data, kid)
        if key is not None:
            return key

    return None


async def _resolve_convex_user_id(clerk_id: str) -> Optional[str]:
    """Resolve Clerk user ID to Convex document _id. Results cached in-memory."""
    cached = _cache_get(clerk_id)
    if cached is not None:
        return cached

    try:
        from db.convex import get_convex_db

        db = get_convex_db()
        if db.dev_mode:
            return None
        result = await db.query("users:getByClerkId", {"clerkId": clerk_id})
        if result and isinstance(result, dict):
            convex_id = result.get("_id")
            if convex_id:
                _cache_set(clerk_id, convex_id)
            return convex_id
    except Exception:
        pass
    return None


async def _verify_token_offline(token: str) -> AuthenticatedUser:
    """W5.3 — local-first offline token verifier.

    Decodes the JWT WITHOUT signature verification and resolves the user
    against the local SQLite store. Gated behind `_offline_auth_active()`;
    the caller MUST check that gate before invoking this function. The
    gate is intentionally re-checked here so that even a direct call
    from outside `verify_auth` can't bypass it.
    """
    if not _offline_auth_active():
        raise HTTPException(
            status_code=500,
            detail="Offline auth gate not satisfied",
        )
    if not HAS_JWT:
        raise HTTPException(status_code=500, detail="JWT library not installed")

    # Decode without verifying signature or 'aud' — we explicitly accept
    # any well-formed JWT in this mode. `exp` is still enforced so an
    # ancient token doesn't grant indefinite access.
    try:
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": True,
            },
        )
    except Exception as exc:  # malformed or expired
        raise HTTPException(
            status_code=401,
            detail=f"Offline auth: token decode failed ({exc.__class__.__name__})",
        )

    clerk_id = payload.get("sub") or payload.get("user_id")
    if not clerk_id:
        raise HTTPException(
            status_code=401,
            detail="Offline auth: token missing 'sub' claim",
        )

    # Resolve the user from the local SQLite repo (W5.1).
    try:
        from core.repositories.sqlite import get_sqlite_user_sync_repository

        repo = get_sqlite_user_sync_repository()
        row = await repo.get_by_clerk_id(clerk_id=clerk_id)
    except Exception as exc:
        logger.warning("Offline auth: SQLite lookup failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Offline auth: local user store unavailable",
        )

    if row is None:
        # Fail gracefully — caller may want to provision the user via
        # /users/sync first. Surface as 401 with a specific reason so
        # the client can decide whether to retry the sync.
        raise HTTPException(
            status_code=401,
            detail=f"Offline auth: user '{clerk_id}' not provisioned locally",
        )

    user = AuthenticatedUser(
        id=clerk_id,
        email=row.get("email") or payload.get("email"),
        org_id=payload.get("org_id"),
        org_role=payload.get("org_role"),
        permissions=payload.get("permissions") or [],
        metadata={
            **(row.get("metadata") or {}),
            "session_id": payload.get("sid"),
            "exp": payload.get("exp"),
            "iat": payload.get("iat"),
            "offline_auth": True,
        },
    )
    # Convex ID is irrelevant offline — point at the local SQLite _id so
    # downstream ownership checks have a stable handle.
    user.convex_user_id = row.get("_id")
    return user


async def verify_auth(request: Request) -> Optional[AuthenticatedUser]:
    """
    Verify authentication from request.

    Checks Authorization header for Bearer token and validates with Clerk.
    In dev mode, allows unauthenticated access with a mock user.

    W5.3 — under VOS3_LOCALITY_PREFERENCE=local-first (community profile),
    a narrowly-scoped offline fallback decodes the token locally and
    resolves the user from the W5.1 SQLite store. Strictly disabled in
    production by `_offline_auth_active()`.

    Returns:
        AuthenticatedUser if authenticated, None if unauthenticated in dev mode

    Raises:
        HTTPException: If authentication fails in production mode
    """
    # Get token from header
    auth_header = request.headers.get("Authorization", "")
    token = (
        auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    )

    # W5.3 — offline local-first fallback BEFORE the production path so
    # the JWKS fetch can't be invoked when we're air-gapped. Note this
    # path requires a token; we don't auto-grant a mock identity here.
    if _offline_auth_active():
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Offline auth: missing authorization token",
            )
        user = await _verify_token_offline(token)
        _attach_region(user, request)
        return user

    # Dev mode: return mock user if no token (read-only permissions only)
    if DEV_MODE:
        _dev_user = AuthenticatedUser(
            id="dev_seed_user",
            email="demo@example.com",
            org_id="org_demo",
            org_role="member",
            permissions=["read"],
            metadata={"dev_mode": True},
        )
        if not token:
            logger.warning(
                "DEV_MODE: auth fallback (no token) for %s", request.url.path
            )
            _attach_region(_dev_user, request)
            return _dev_user
        # In dev mode, try to decode token but don't fail if invalid
        try:
            user = await _verify_token(token)
            _attach_region(user, request)
            return user
        except Exception:
            logger.warning(
                "DEV_MODE: auth fallback (invalid token) for %s", request.url.path
            )
            _attach_region(_dev_user, request)
            return _dev_user

    # Production mode: require valid token
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    try:
        user = await _verify_token(token)
        _attach_region(user, request)
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _attach_region(user: AuthenticatedUser, request: "Request") -> None:
    """[RESTORED-FROM-LOGS] Populate `region_code`, `is_eu_region`,
    `global_cloud_consent` on the user from request headers and Clerk metadata.
    Called once per request, immediately after token verification."""
    code = _detect_region_code(request)
    user.region_code = code
    user.is_eu_region = _is_eu_region(code)

    # Opt-in consent comes from Clerk public metadata. Either the JWT payload
    # carried it (preferred — no extra call) or it was set on AuthenticatedUser
    # directly by an earlier middleware. Default: False.
    consent_raw = (
        user.metadata.get("global_cloud_processing_consent")
        if isinstance(user.metadata, dict)
        else None
    )
    user.global_cloud_consent = bool(consent_raw) if consent_raw is not None else False


async def _verify_token(token: str) -> AuthenticatedUser:
    """
    Verify JWT token and extract user information.

    Validation order:
    1. JWKS-based RS256 verification (if CLERK_ISSUER_URL set)
    2. Clerk SDK verification (if clerk_backend_api installed)
    3. Manual HS256/RS256 with CLERK_SECRET_KEY
    """
    if not HAS_JWT:
        raise HTTPException(status_code=500, detail="JWT library not installed")

    payload = None

    # Strategy 1: JWKS-based RS256 verification (preferred for Clerk)
    if CLERK_ISSUER_URL:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if kid:
                key_data = await _resolve_jwk_for_kid(kid)
                if key_data is not None:
                    from jwt import algorithms

                    public_key = algorithms.RSAAlgorithm.from_jwk(key_data)
                    _clerk_audience = os.getenv("CLERK_AUDIENCE")
                    payload = jwt.decode(
                        token,
                        public_key,
                        algorithms=["RS256"],
                        issuer=CLERK_ISSUER_URL,
                        audience=_clerk_audience,
                        options={"verify_aud": bool(_clerk_audience)},
                    )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except Exception:
            pass  # Fall through to other strategies

    # Strategy 2: Clerk SDK
    if payload is None:
        clerk = get_clerk_client()
        if clerk and HAS_CLERK:
            try:
                session = clerk.sessions.verify_token(token)
                user = AuthenticatedUser(
                    id=session.user_id,
                    org_id=getattr(session, "org_id", None),
                    metadata={"session_id": session.id},
                )
                user.convex_user_id = await _resolve_convex_user_id(user.id)
                return user
            except Exception:
                pass

    # Strategy 3: Manual JWT with CLERK_SECRET_KEY
    if payload is None:
        try:
            _clerk_audience = os.getenv("CLERK_AUDIENCE")
            _allowed_algorithms = ["RS256"]
            if ENVIRONMENT != "production":
                _allowed_algorithms.append("HS256")
            payload = jwt.decode(
                token,
                CLERK_SECRET_KEY,
                algorithms=_allowed_algorithms,
                audience=_clerk_audience,
                options={"verify_aud": bool(_clerk_audience)},
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

    if payload is None:
        raise HTTPException(
            status_code=401, detail="Missing or invalid authentication token"
        )

    # Resilience-Matrix F8 — JTI replay protection.
    # If the token carries a `jti` claim, reject it if we've seen the
    # same JTI from any prior request (within the JTI cache's TTL).
    # Tokens without `jti` flow through unchanged — we cannot detect
    # replay on issuers that don't emit unique IDs, but Clerk does.
    _jti = payload.get("jti")
    if _jti is not None and not _jti_register(_jti, payload.get("exp")):
        raise HTTPException(
            status_code=401,
            detail="Token replay detected (jti already presented)",
        )

    # Extract user info from claims
    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Missing or invalid authentication token"
        )

    user = AuthenticatedUser(
        id=user_id,
        email=payload.get("email"),
        org_id=payload.get("org_id"),
        org_role=payload.get("org_role"),
        permissions=payload.get("permissions", []),
        metadata={
            "session_id": payload.get("sid"),
            "exp": payload.get("exp"),
            "iat": payload.get("iat"),
        },
    )
    # Resolve Convex user ID (non-blocking, cached)
    user.convex_user_id = await _resolve_convex_user_id(user.id)
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> AuthenticatedUser:
    """
    Dependency to get the current authenticated user.

    Usage:
        @router.get("/me")
        async def get_me(user: AuthenticatedUser = Depends(get_current_user)):
            return {"user_id": user.id}
    """
    user = await verify_auth(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Store user in request state for later use
    request.state.user = user
    return user


def require_permission(permission: str):
    """
    Dependency factory to require a specific permission.

    Usage:
        @router.delete("/users/{id}")
        async def delete_user(
            user: AuthenticatedUser = Depends(require_permission("users:delete"))
        ):
            ...
    """

    async def permission_checker(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission: {permission}",
            )
        return user

    return permission_checker


def require_org_membership(request: Request, org_id: str):
    """
    Verify user is a member of the specified organization.

    Usage in route:
        if not require_org_membership(request, org_id):
            raise HTTPException(403, "Not a member of this organization")
    """
    user: AuthenticatedUser = getattr(request.state, "user", None)
    if not user:
        return False

    # In dev mode, allow all
    if DEV_MODE:
        return True

    # Check org membership
    return user.org_id == org_id or user.has_permission("admin:full")


# Middleware for protected routes
async def auth_middleware(request: Request, call_next):
    """
    Authentication middleware for FastAPI.

    Add to app:
        app.middleware("http")(auth_middleware)

    Or selectively:
        @app.middleware("http")
        async def auth_middleware_wrapper(request, call_next):
            if should_protect(request.url.path):
                return await auth_middleware(request, call_next)
            return await call_next(request)
    """
    # Public paths that don't require auth
    public_paths = [
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/webhooks",
    ]

    # Check if path is public
    path = request.url.path
    is_public = any(path == p or path.startswith(f"{p}/") for p in public_paths)

    if not is_public:
        try:
            user = await verify_auth(request)
            request.state.user = user
        except HTTPException:
            if not DEV_MODE:
                raise

    return await call_next(request)


# ---------------------------------------------------------------------------
# P4.2 — App context dependency.
#
# Reads X-App-Id + X-App-Secret from the request, validates them
# against the SANDBOX_MANAGER registry, and attaches the validated
# app_id to `request.state.app_id` for downstream consumers (LLM
# dispatcher, RAG repos, …).
#
# Failure modes:
#   * Headers absent           → returns None. The caller stays a
#                                "user-only" request — vOS core
#                                services skip the gate check.
#   * Header present but bad   → HTTPException(403). We intentionally
#                                conflate "unknown app", "wrong
#                                secret", and "isolated app" into a
#                                single 403 to avoid enumeration.
# ---------------------------------------------------------------------------


async def get_app_context(request: Request) -> Optional[str]:
    """Validate X-App-Id / X-App-Secret and return the app_id.

    Returns:
      str   — the app exists, status='active', secret matches.
      None  — neither header was sent (this is NOT an authenticated
              app call; user-only routes proceed normally).

    Raises:
      HTTPException(403) — header(s) present but validation failed.

    Also sets `request.state.app_id` to the validated id (or None)
    so route handlers can access it without re-running the lookup.
    """
    app_id_header = request.headers.get("x-app-id")
    app_secret_header = request.headers.get("x-app-secret")

    # Both absent → not an app call, user-only path.
    if not app_id_header and not app_secret_header:
        request.state.app_id = None
        return None

    if not app_id_header or not app_secret_header:
        # One but not the other — partial creds are always 403.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "app_context_incomplete",
                "reason": "both X-App-Id and X-App-Secret are required",
            },
        )

    # Defer the import so middleware/auth.py stays usable when the
    # sandbox manager isn't on sys.path (e.g. legacy boots).
    from services.app_sandbox import SANDBOX_MANAGER

    record = SANDBOX_MANAGER.get(app_id_header)
    if record is None or record.get("status") != "active":
        # Unknown OR not active — same 403 either way (no enumeration).
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized"},
        )
    if not SANDBOX_MANAGER.verify_secret(app_id_header, app_secret_header):
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized"},
        )

    request.state.app_id = app_id_header
    return app_id_header


__all__ = [
    "verify_auth",
    "get_current_user",
    "get_app_context",
    "require_permission",
    "require_org_membership",
    "auth_middleware",
    "AuthenticatedUser",
    "DEV_MODE",
    "init_jwks_cache",
    "shutdown_jwks_cache",
    # [RESTORED-FROM-LOGS] EU AI Act Article 12 helpers (exposed for tests / direct use)
    "_detect_region_code",
    "_is_eu_region",
    "_EU_COUNTRY_CODES",
]
