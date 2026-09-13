"""
Feature Flags Service
=====================
Deterministic, hash-based feature flag evaluation for A/B testing and
progressive rollouts — no redeploy required.

Architecture (Opus 4.7 design):
  Flags are evaluated deterministically per user_id using HMAC-SHA256 so
  the same user always gets the same experience. Rollout percentages can be
  changed at runtime via the admin API or environment variables.

  Redis-backed persistence: flag state survives restarts.
  In-memory fallback: works without Redis (per-process only).

Usage:
    flags = get_feature_flags()
    if await flags.is_enabled("proactive_analysis", user_id="usr_xyz"):
        # 5% of users get proactive analysis
        ...

Cascade failure simulation (circuit breaker pattern):
    - Redis unavailable → fall back to in-memory state
    - Flag evaluation never raises — returns default_enabled on error
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HMAC_KEY = os.getenv("FEATURE_FLAG_HMAC_KEY", "vos3-feature-flag-salt-2026").encode()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FeatureFlag:
    """A single feature flag definition."""

    name: str
    enabled: bool = False
    rollout_pct: float = 0.0  # 0.0–100.0; evaluated per user_id
    description: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Flag evaluator — deterministic hash-based bucket assignment
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=2048)
def _user_bucket(flag_name: str, user_id: str) -> float:
    """Return a stable float in [0, 100) for (flag_name, user_id).

    Uses HMAC-SHA256 so the same user always lands in the same bucket for
    a given flag. Different flags use different buckets (flag_name in key).
    Result is memoized — repeated calls for the same (flag, user) pair skip
    the HMAC computation entirely (O(1) after first call).
    """
    digest = hmac.new(
        _HMAC_KEY,
        f"{flag_name}:{user_id}".encode(),
        hashlib.sha256,
    ).digest()
    # Take first 4 bytes as uint32, map to [0, 100)
    value = int.from_bytes(digest[:4], "big")
    return (value / 0xFFFFFFFF) * 100.0


# ---------------------------------------------------------------------------
# In-memory store (used as primary or fallback)
# ---------------------------------------------------------------------------

_DEFAULT_FLAGS: dict[str, FeatureFlag] = {
    "proactive_analysis": FeatureFlag(
        name="proactive_analysis",
        enabled=True,
        rollout_pct=5.0,
        description="Proactive AI analysis engine — enabled for 5% of users",
    ),
    "streaming_codegen": FeatureFlag(
        name="streaming_codegen",
        enabled=True,
        rollout_pct=100.0,
        description="Streaming code generation responses",
    ),
    "haiku_fallback": FeatureFlag(
        name="haiku_fallback",
        enabled=True,
        rollout_pct=100.0,
        description="Auto-downgrade to Haiku 4.5 under driver pressure",
    ),
    "new_billing_portal": FeatureFlag(
        name="new_billing_portal",
        enabled=False,
        rollout_pct=0.0,
        description="New self-serve billing portal (Stripe Customer Portal v2)",
    ),
}


class FeatureFlagService:
    """
    Feature flag service with deterministic per-user rollout.

    Designed for zero-downtime flag changes at 1M-user scale:
    - Flag evaluation is O(1) and never blocks
    - Redis writes are fire-and-forget (failures don't affect reads)
    - All evaluation errors return `default_enabled` (fail-open)
    """

    def __init__(self, redis_url: Optional[str] = None):
        self._flags: dict[str, FeatureFlag] = dict(_DEFAULT_FLAGS)
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._redis: Any = None
        self._redis_key = "vos3:feature_flags"

    async def _try_load_redis(self) -> None:
        """Attempt to load flag state from Redis (non-fatal if unavailable)."""
        if not self._redis_url:
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            raw = await self._redis.get(self._redis_key)
            if raw:
                data = json.loads(raw)
                for name, flag_data in data.items():
                    self._flags[name] = FeatureFlag(**flag_data)
                logger.info("FeatureFlagService: loaded %d flags from Redis", len(data))
        except Exception as e:
            logger.warning(
                "FeatureFlagService: Redis load failed (using defaults): %s", e
            )
            self._redis = None

    async def _try_save_redis(self) -> None:
        """Persist current flags to Redis (non-fatal)."""
        if not self._redis:
            return
        try:
            payload = {name: flag.to_dict() for name, flag in self._flags.items()}
            await self._redis.set(self._redis_key, json.dumps(payload))
        except Exception as e:
            logger.warning("FeatureFlagService: Redis save failed: %s", e)

    async def initialize(self) -> None:
        """Load persisted state from Redis on startup."""
        await self._try_load_redis()

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    async def is_enabled(
        self,
        flag_name: str,
        user_id: str = "",
        default_enabled: bool = False,
    ) -> bool:
        """Evaluate a flag for a specific user.

        Returns True if:
          1. The flag exists and `enabled` is True, AND
          2. The user falls within the `rollout_pct` bucket (or rollout_pct == 100)

        Always returns `default_enabled` on any error.
        """
        try:
            flag = self._flags.get(flag_name)
            if flag is None:
                return default_enabled
            if not flag.enabled:
                return False
            if flag.rollout_pct >= 100.0:
                return True
            if flag.rollout_pct <= 0.0:
                return False
            if not user_id:
                return False
            bucket = _user_bucket(flag_name, user_id)
            return bucket < flag.rollout_pct
        except Exception as e:
            logger.warning("FeatureFlagService.is_enabled(%s) error: %s", flag_name, e)
            return default_enabled

    def is_enabled_sync(
        self,
        flag_name: str,
        user_id: str = "",
        default_enabled: bool = False,
    ) -> bool:
        """Synchronous version for use in non-async contexts."""
        try:
            flag = self._flags.get(flag_name)
            if flag is None:
                return default_enabled
            if not flag.enabled:
                return False
            if flag.rollout_pct >= 100.0:
                return True
            if flag.rollout_pct <= 0.0:
                return False
            if not user_id:
                return False
            return _user_bucket(flag_name, user_id) < flag.rollout_pct
        except Exception:
            return default_enabled

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def list_flags(self) -> list[dict]:
        return [f.to_dict() for f in self._flags.values()]

    def get_flag(self, name: str) -> Optional[FeatureFlag]:
        return self._flags.get(name)

    async def upsert_flag(
        self,
        name: str,
        enabled: Optional[bool] = None,
        rollout_pct: Optional[float] = None,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> FeatureFlag:
        """Create or update a flag. Persists to Redis."""
        flag = self._flags.get(name) or FeatureFlag(name=name)
        if enabled is not None:
            flag.enabled = enabled
        if rollout_pct is not None:
            flag.rollout_pct = max(0.0, min(100.0, rollout_pct))
        if description is not None:
            flag.description = description
        if metadata is not None:
            flag.metadata.update(metadata)
        flag.updated_at = time.time()
        self._flags[name] = flag
        await self._try_save_redis()
        logger.info(
            "FeatureFlagService: upserted flag %r (enabled=%s, pct=%.1f%%)",
            name,
            flag.enabled,
            flag.rollout_pct,
        )
        return flag

    async def delete_flag(self, name: str) -> bool:
        if name in _DEFAULT_FLAGS:
            raise ValueError(f"Cannot delete built-in flag {name!r}")
        removed = self._flags.pop(name, None) is not None
        if removed:
            await self._try_save_redis()
        return removed


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: Optional[FeatureFlagService] = None


def get_feature_flags() -> FeatureFlagService:
    global _service
    if _service is None:
        _service = FeatureFlagService()
    return _service
