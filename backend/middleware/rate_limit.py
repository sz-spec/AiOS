"""
Rate Limiting Middleware
========================
Token-bucket rate limiter per IP address.
Uses in-memory storage by default, Redis-backed when available.

Includes a dedicated AuthRateLimiter for identity-sensitive paths
(login, webhook, token exchange) with stricter thresholds.

Resilience-Matrix F9 — adds an account-lockout layer on top of the
per-IP token bucket. After N failed identifications from the same IP
(default 10) within the lockout window, the IP is hard-blocked for
LOCKOUT_DURATION_SEC. The fail counter increments on auth-path
responses with status >= 400, observed by `record_auth_failure()`
(called by the auth middleware on any 401/403).
"""

import time
from typing import Dict, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

# F9 — account-lockout configuration. Conservative defaults: 10 fails
# in any 15-minute window triggers a 24-hour block. The values are
# tunable via env so security/ops can ratchet them per profile.
import os as _os

LOCKOUT_FAIL_THRESHOLD = int(_os.environ.get("VOS3_AUTH_LOCKOUT_THRESHOLD", "10"))
LOCKOUT_FAIL_WINDOW_SEC = int(
    _os.environ.get("VOS3_AUTH_LOCKOUT_WINDOW", "900")
)  # 15 min
LOCKOUT_DURATION_SEC = int(
    _os.environ.get("VOS3_AUTH_LOCKOUT_DURATION", "86400")
)  # 24 h
_LOCKOUT_CACHE_MAX = 50_000

# key -> list[fail_timestamp]; trimmed to LOCKOUT_FAIL_WINDOW_SEC.
_fail_history: Dict[str, list] = {}
# key -> unlock_at_epoch.
_lockouts: Dict[str, float] = {}


def _evict_lockout_caches():
    """Drop entries that have expired or have unbounded growth."""
    now = time.time()
    if _lockouts:
        expired = [k for k, t in _lockouts.items() if t <= now]
        for k in expired:
            del _lockouts[k]
    if len(_fail_history) > _LOCKOUT_CACHE_MAX:
        # Trim oldest by first-key (insertion order).
        for k in list(_fail_history)[: len(_fail_history) - _LOCKOUT_CACHE_MAX]:
            del _fail_history[k]


def is_locked_out(key: str) -> Tuple[bool, float]:
    """Return (locked, unlock_at_epoch). unlock_at_epoch is 0 when unlocked."""
    now = time.time()
    until = _lockouts.get(key, 0.0)
    if until > now:
        return True, until
    if until and until <= now:
        # Expired lockout — clean up.
        del _lockouts[key]
    return False, 0.0


def record_auth_failure(key: str) -> bool:
    """Record an auth failure for `key`. Returns True if the key is now
    locked out. Should be called from the auth middleware on any 401/403."""
    now = time.time()
    history = _fail_history.setdefault(key, [])
    cutoff = now - LOCKOUT_FAIL_WINDOW_SEC
    # Drop fails outside the window.
    while history and history[0] < cutoff:
        history.pop(0)
    history.append(now)
    if len(history) >= LOCKOUT_FAIL_THRESHOLD:
        _lockouts[key] = now + LOCKOUT_DURATION_SEC
        # Reset the rolling window — they get a fresh count after the lockout expires.
        _fail_history[key] = []
        return True
    return False


def reset_auth_failures(key: str) -> None:
    """Clear the fail counter on a successful auth (called by auth.py)."""
    _fail_history.pop(key, None)


class TokenBucket:
    """Per-IP token bucket rate limiter."""

    def __init__(self, rate: float = 60.0, burst: int = 120):
        """
        Args:
            rate: Tokens replenished per second (default: 60 req/s)
            burst: Maximum tokens (burst capacity)
        """
        self.rate = rate
        self.burst = burst
        self._buckets: Dict[str, Tuple[float, float]] = {}  # ip -> (tokens, last_time)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last_time = self._buckets.get(key, (self.burst, now))

        # Replenish tokens
        elapsed = now - last_time
        tokens = min(self.burst, tokens + elapsed * self.rate)

        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True

        self._buckets[key] = (tokens, now)
        return False

    def cleanup(self, max_age: float = 300.0):
        """Remove stale entries older than max_age seconds."""
        now = time.monotonic()
        stale = [k for k, (_, t) in self._buckets.items() if now - t > max_age]
        for k in stale:
            del self._buckets[k]


# Global rate limiter (standard API: 60 req/min burst 120)
_limiter = TokenBucket(rate=1.0, burst=120)

# Auth rate limiter (strict: 5 req/min burst 5 for identity paths)
_auth_limiter = TokenBucket(rate=5.0 / 60.0, burst=5)

_cleanup_counter = 0

# Paths subject to the stricter auth rate limit
_AUTH_PREFIXES = (
    "/api/auth/",
    "/api/clerk/",
    "/api/webhooks/clerk",
    "/api/apps/authorize",
    "/api/apps/register",
    "/api/terminal/",
)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from TCP peer address.

    Uses request.client.host (the actual TCP connection source) to prevent
    IP spoofing via X-Forwarded-For.  If the app is behind a trusted reverse
    proxy (Railway, Vercel, etc.), the proxy strips/overwrites XFF before it
    reaches uvicorn, so client.host already reflects the real remote IP.
    """
    return request.client.host if request.client else "unknown"


def _is_auth_path(path: str) -> bool:
    """Check if the request path is an identity-sensitive route."""
    return any(path.startswith(prefix) for prefix in _AUTH_PREFIXES)


async def rate_limit_middleware(request: Request, call_next):
    """Rate limit middleware for FastAPI."""
    global _cleanup_counter

    # Skip rate limiting for health checks
    if request.url.path in ("/health", "/"):
        return await call_next(request)

    ip = _get_client_ip(request)
    path = request.url.path

    # F9 — hard lockout precedes the soft rate limit. If the IP has
    # crossed the fail threshold, every request is rejected for the
    # lockout duration regardless of bucket state.
    if _is_auth_path(path):
        locked, until = is_locked_out(ip)
        if locked:
            retry = max(1, int(until - time.time()))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Authentication endpoint locked after "
                        f"{LOCKOUT_FAIL_THRESHOLD} failed attempts. "
                        f"Retry after {retry}s."
                    )
                },
                headers={"Retry-After": str(retry)},
            )

    # Apply stricter limit to auth/identity paths
    if _is_auth_path(path):
        if not _auth_limiter.allow(ip):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many authentication requests. Please try again later."
                },
                headers={"Retry-After": "60"},
            )
    else:
        if not _limiter.allow(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": "1"},
            )

    response = await call_next(request)

    # F9 — observe response status for auth paths and feed the fail counter.
    if _is_auth_path(path) and response.status_code in (401, 403):
        record_auth_failure(ip)

    # Periodic cleanup
    _cleanup_counter += 1
    if _cleanup_counter >= 1000:
        _cleanup_counter = 0
        _limiter.cleanup()
        _auth_limiter.cleanup()
        _evict_lockout_caches()

    return response
