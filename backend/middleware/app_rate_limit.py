"""
Per-App Rate Limiting
======================
Rate limits per app based on tier: free (100 RPM), paid (1000 RPM).
"""

import time
from typing import Dict, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

# Per-app rate limits (requests per minute)
TIER_LIMITS = {
    "free": 100,
    "paid": 1000,
    "enterprise": 10000,
}

# App tier registry (in production, loaded from Convex)
_app_tiers: Dict[str, str] = {}


class AppRateLimiter:
    """Per-app token bucket rate limiter."""

    def __init__(self):
        self._buckets: Dict[str, Tuple[float, float]] = {}

    def allow(self, app_id: str, tier: str = "free") -> bool:
        limit = TIER_LIMITS.get(tier, 100)
        rate = limit / 60.0  # Convert RPM to per-second
        burst = limit

        now = time.monotonic()
        tokens, last_time = self._buckets.get(app_id, (burst, now))
        elapsed = now - last_time
        tokens = min(burst, tokens + elapsed * rate)

        if tokens >= 1.0:
            self._buckets[app_id] = (tokens - 1.0, now)
            return True
        self._buckets[app_id] = (tokens, now)
        return False


_limiter = AppRateLimiter()


async def app_rate_limit_middleware(request: Request, call_next):
    """Per-app rate limiting middleware."""
    if not request.url.path.startswith("/api/apps/"):
        return await call_next(request)

    app_id = getattr(request.state, "app_id", None)
    if not app_id:
        return await call_next(request)

    tier = _app_tiers.get(app_id, "free")

    if not _limiter.allow(app_id, tier):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded for app '{app_id}'. Tier: {tier}"},
            headers={"Retry-After": "1"},
        )

    return await call_next(request)
