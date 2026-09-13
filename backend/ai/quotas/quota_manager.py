"""
Quota Manager - Token Usage Limits by Subscription Tier
========================================================
Implements VBuilder-style quota management with daily/monthly limits.

Tier Limits:
- Free: 50,000 tokens/day (1.5M/month)
- Pro: 1,000,000 tokens/day (30M/month)
- Enterprise: 10,000,000 tokens/day (300M/month)

Features:
- Real-time token tracking
- Daily and monthly limits
- Alert thresholds (50%, 80%, 95%, 100%)
- Integration with credit system
- Automatic daily reset

Usage:
    from ai.quotas import get_quota_manager

    quota = get_quota_manager()

    # Check before LLM call
    can_use, status = await quota.check_quota(user_id, estimated_tokens=1000)
    if not can_use:
        raise QuotaExceededError(status.message)

    # Record usage after LLM call
    await quota.record_usage(user_id, tokens_used=850, model="claude-opus")

    # Get usage stats
    usage = await quota.get_usage(user_id)
"""

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


# =============================================================================
# Tier Configuration (VBuilder Pattern)
# =============================================================================


class QuotaTier(str, Enum):
    """Subscription tiers with quota limits."""

    FREE = "free"
    PRO = "pro"
    TEAM = "team"  # Alias for Pro
    ENTERPRISE = "enterprise"


# Daily token limits by tier
TIER_LIMITS: Dict[QuotaTier, Dict[str, int]] = {
    QuotaTier.FREE: {
        "daily_tokens": 50_000,  # 50K/day
        "monthly_tokens": 1_500_000,  # 1.5M/month
        "max_tokens_per_request": 4_000,  # 4K per request
        "rate_limit_rpm": 20,  # 20 requests/minute
    },
    QuotaTier.PRO: {
        "daily_tokens": 1_000_000,  # 1M/day
        "monthly_tokens": 30_000_000,  # 30M/month
        "max_tokens_per_request": 16_000,  # 16K per request
        "rate_limit_rpm": 100,  # 100 requests/minute
    },
    QuotaTier.TEAM: {
        "daily_tokens": 1_000_000,  # Same as Pro
        "monthly_tokens": 30_000_000,
        "max_tokens_per_request": 16_000,
        "rate_limit_rpm": 100,
    },
    QuotaTier.ENTERPRISE: {
        "daily_tokens": 10_000_000,  # 10M/day
        "monthly_tokens": 300_000_000,  # 300M/month
        "max_tokens_per_request": 32_000,  # 32K per request
        "rate_limit_rpm": 500,  # 500 requests/minute
    },
}

# Alert thresholds (percentage of limit)
ALERT_THRESHOLDS = [50, 80, 95, 100]


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class QuotaUsage:
    """User's quota usage statistics."""

    user_id: str
    tier: QuotaTier
    daily_tokens_used: int = 0
    daily_tokens_limit: int = 0
    monthly_tokens_used: int = 0
    monthly_tokens_limit: int = 0
    requests_today: int = 0
    last_request_at: Optional[str] = None
    reset_at: Optional[str] = None
    monthly_reset_at: Optional[str] = None

    @property
    def daily_usage_pct(self) -> float:
        if self.daily_tokens_limit == 0:
            return 0.0
        return (self.daily_tokens_used / self.daily_tokens_limit) * 100

    @property
    def monthly_usage_pct(self) -> float:
        if self.monthly_tokens_limit == 0:
            return 0.0
        return (self.monthly_tokens_used / self.monthly_tokens_limit) * 100

    @property
    def daily_remaining(self) -> int:
        return max(0, self.daily_tokens_limit - self.daily_tokens_used)

    @property
    def monthly_remaining(self) -> int:
        return max(0, self.monthly_tokens_limit - self.monthly_tokens_used)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "tier": self.tier.value,
            "daily_usage_pct": round(self.daily_usage_pct, 1),
            "monthly_usage_pct": round(self.monthly_usage_pct, 1),
            "daily_remaining": self.daily_remaining,
            "monthly_remaining": self.monthly_remaining,
        }


@dataclass
class QuotaStatus:
    """Result of quota check."""

    allowed: bool
    message: str
    usage: Optional[QuotaUsage] = None
    alert_level: Optional[int] = None  # 50, 80, 95, 100 if threshold crossed
    estimated_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "allowed": self.allowed,
            "message": self.message,
            "alert_level": self.alert_level,
            "estimated_tokens": self.estimated_tokens,
        }
        if self.usage:
            result["usage"] = self.usage.to_dict()
        return result


@dataclass
class QuotaAlert:
    """Quota threshold alert."""

    user_id: str
    tier: QuotaTier
    threshold: int  # 50, 80, 95, 100
    current_usage_pct: float
    limit_type: str  # "daily" or "monthly"
    tokens_used: int
    tokens_limit: int
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "tier": self.tier.value,
        }


# =============================================================================
# Quota Manager
# =============================================================================


class QuotaManager:
    """
    Manages token quotas for users based on subscription tier.

    Integrates with:
    - Credit system (Day 5) for billing
    - In-memory storage for development
    """

    def __init__(self):
        """
        Initialize quota manager.
        """
        # In-memory storage
        self._usage: Dict[str, Dict[str, Any]] = {}
        self._alerts: List[QuotaAlert] = []
        self._lock = asyncio.Lock()

        # Alert callbacks
        self._alert_callbacks: List[callable] = []

        logger.info("QuotaManager initialized")

    def register_alert_callback(self, callback: callable):
        """Register callback for quota alerts."""
        self._alert_callbacks.append(callback)

    async def get_user_tier(self, user_id: str) -> QuotaTier:
        """
        Get user's subscription tier.

        Checks subscription status or defaults to FREE.
        """
        return QuotaTier.FREE

    async def get_usage(self, user_id: str) -> QuotaUsage:
        """
        Get user's current quota usage.

        Returns usage stats including daily/monthly tokens used.
        """
        tier = await self.get_user_tier(user_id)
        limits = TIER_LIMITS[tier]

        # In-memory storage
        async with self._lock:
            if user_id not in self._usage:
                self._usage[user_id] = {
                    "daily_tokens_used": 0,
                    "monthly_tokens_used": 0,
                    "requests_today": 0,
                    "last_reset": datetime.now(timezone.utc).date().isoformat(),
                    "month_start": datetime.now(timezone.utc)
                    .replace(day=1)
                    .date()
                    .isoformat(),
                }

            usage_data = self._usage[user_id]

            # Check for daily reset
            today = datetime.now(timezone.utc).date().isoformat()
            if usage_data.get("last_reset") != today:
                usage_data["daily_tokens_used"] = 0
                usage_data["requests_today"] = 0
                usage_data["last_reset"] = today

            # Check for monthly reset
            month_start = datetime.now(timezone.utc).replace(day=1).date().isoformat()
            if usage_data.get("month_start") != month_start:
                usage_data["monthly_tokens_used"] = 0
                usage_data["month_start"] = month_start

            return QuotaUsage(
                user_id=user_id,
                tier=tier,
                daily_tokens_used=usage_data.get("daily_tokens_used", 0),
                daily_tokens_limit=limits["daily_tokens"],
                monthly_tokens_used=usage_data.get("monthly_tokens_used", 0),
                monthly_tokens_limit=limits["monthly_tokens"],
                requests_today=usage_data.get("requests_today", 0),
                reset_at=self._get_daily_reset_time(),
                monthly_reset_at=self._get_monthly_reset_time(),
            )

    async def check_quota(
        self, user_id: str, estimated_tokens: int = 0
    ) -> Tuple[bool, QuotaStatus]:
        """
        Check if user can make a request with estimated token usage.

        Args:
            user_id: User identifier
            estimated_tokens: Estimated tokens for the request

        Returns:
            Tuple of (allowed: bool, status: QuotaStatus)
        """
        usage = await self.get_usage(user_id)
        limits = TIER_LIMITS[usage.tier]

        # Check per-request limit
        if estimated_tokens > limits["max_tokens_per_request"]:
            return False, QuotaStatus(
                allowed=False,
                message=f"Request exceeds max tokens per request ({limits['max_tokens_per_request']:,})",
                usage=usage,
                estimated_tokens=estimated_tokens,
            )

        # Check daily limit
        if usage.daily_tokens_used + estimated_tokens > usage.daily_tokens_limit:
            return False, QuotaStatus(
                allowed=False,
                message=f"Daily quota exceeded. Resets at {usage.reset_at}",
                usage=usage,
                alert_level=100,
                estimated_tokens=estimated_tokens,
            )

        # Check monthly limit
        if usage.monthly_tokens_used + estimated_tokens > usage.monthly_tokens_limit:
            return False, QuotaStatus(
                allowed=False,
                message=f"Monthly quota exceeded. Resets at {usage.monthly_reset_at}",
                usage=usage,
                alert_level=100,
                estimated_tokens=estimated_tokens,
            )

        # Check alert thresholds
        alert_level = None
        projected_usage_pct = (
            (usage.daily_tokens_used + estimated_tokens) / usage.daily_tokens_limit
        ) * 100

        for threshold in ALERT_THRESHOLDS:
            if projected_usage_pct >= threshold and usage.daily_usage_pct < threshold:
                alert_level = threshold
                break

        return True, QuotaStatus(
            allowed=True,
            message="OK",
            usage=usage,
            alert_level=alert_level,
            estimated_tokens=estimated_tokens,
        )

    async def record_usage(
        self,
        user_id: str,
        tokens_used: int,
        model: str = "unknown",
        request_type: str = "generation",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QuotaUsage:
        """
        Record token usage after an LLM call.

        Args:
            user_id: User identifier
            tokens_used: Actual tokens consumed
            model: Model used
            request_type: Type of request (generation, embedding, etc.)
            metadata: Additional metadata

        Returns:
            Updated QuotaUsage
        """
        usage = await self.get_usage(user_id)
        now = datetime.now(timezone.utc)

        # Calculate new usage
        new_daily = usage.daily_tokens_used + tokens_used
        new_monthly = usage.monthly_tokens_used + tokens_used
        new_requests = usage.requests_today + 1

        # Check for alert thresholds
        old_daily_pct = usage.daily_usage_pct
        new_daily_pct = (new_daily / usage.daily_tokens_limit) * 100

        for threshold in ALERT_THRESHOLDS:
            if new_daily_pct >= threshold > old_daily_pct:
                alert = QuotaAlert(
                    user_id=user_id,
                    tier=usage.tier,
                    threshold=threshold,
                    current_usage_pct=new_daily_pct,
                    limit_type="daily",
                    tokens_used=new_daily,
                    tokens_limit=usage.daily_tokens_limit,
                )
                self._alerts.append(alert)
                await self._trigger_alert(alert)
                break

        # Update in-memory
        async with self._lock:
            if user_id not in self._usage:
                self._usage[user_id] = {}

            self._usage[user_id].update(
                {
                    "daily_tokens_used": new_daily,
                    "monthly_tokens_used": new_monthly,
                    "requests_today": new_requests,
                    "last_request_at": now.isoformat(),
                }
            )

        # Return updated usage
        usage.daily_tokens_used = new_daily
        usage.monthly_tokens_used = new_monthly
        usage.requests_today = new_requests
        usage.last_request_at = now.isoformat()

        return usage

    async def _trigger_alert(self, alert: QuotaAlert):
        """Trigger alert callbacks."""
        logger.warning(
            f"Quota alert: user={alert.user_id} tier={alert.tier.value} "
            f"threshold={alert.threshold}% type={alert.limit_type}"
        )

        for callback in self._alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

    def _get_daily_reset_time(self) -> str:
        """Get next daily reset time (midnight UTC)."""
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        return tomorrow.isoformat()

    def _get_monthly_reset_time(self) -> str:
        """Get next monthly reset time (1st of next month)."""
        now = datetime.now(timezone.utc)
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1)
        else:
            next_month = now.replace(month=now.month + 1, day=1)
        return next_month.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    async def get_alerts(
        self, user_id: Optional[str] = None, limit: int = 50
    ) -> List[QuotaAlert]:
        """Get recent quota alerts."""
        alerts = self._alerts[-limit:]
        if user_id:
            alerts = [a for a in alerts if a.user_id == user_id]
        return alerts

    async def upgrade_tier(self, user_id: str, new_tier: QuotaTier) -> QuotaUsage:
        """
        Upgrade user's quota tier.

        Note: This should be called by the billing system after subscription change.
        """
        logger.info(f"Upgrading user {user_id} to tier {new_tier.value}")

        return await self.get_usage(user_id)

    def get_tier_limits(self, tier: QuotaTier) -> Dict[str, int]:
        """Get limits for a specific tier."""
        return TIER_LIMITS.get(tier, TIER_LIMITS[QuotaTier.FREE])

    async def get_all_tier_limits(self) -> Dict[str, Dict[str, int]]:
        """Get limits for all tiers (for pricing page)."""
        return {tier.value: limits for tier, limits in TIER_LIMITS.items()}


# =============================================================================
# Singleton Instance
# =============================================================================

_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """
    Get the singleton quota manager instance.

    Returns:
        QuotaManager instance
    """
    global _quota_manager

    if _quota_manager is None:
        _quota_manager = QuotaManager()

    return _quota_manager


def reset_quota_manager():
    """Reset the singleton instance (for testing)."""
    global _quota_manager
    _quota_manager = None


# =============================================================================
# Integration with Credit System
# =============================================================================


async def check_and_record_usage(
    user_id: str, tokens_used: int, model: str = "unknown", deduct_credits: bool = True
) -> Tuple[bool, QuotaStatus]:
    """
    Combined quota check and credit deduction.

    Integrates with the credit system from Day 5.
    First checks quota, then deducts credits if enabled.

    Args:
        user_id: User identifier
        tokens_used: Tokens consumed
        model: Model used
        deduct_credits: Whether to deduct from credit balance

    Returns:
        Tuple of (success, QuotaStatus)
    """
    quota = get_quota_manager()

    # Check quota first
    allowed, status = await quota.check_quota(user_id, tokens_used)

    if not allowed:
        return False, status

    # Deduct credits if enabled
    if deduct_credits:
        try:
            from integrations.stripe_service import get_stripe_service

            stripe = get_stripe_service()

            # Convert tokens to credits (1 credit = 1000 tokens)
            credits_to_deduct = max(1, tokens_used // 1000)
            success = await stripe.use_tokens(
                user_id, credits_to_deduct, f"quota:{model}"
            )

            if not success:
                # Check if user has credits at all
                balance = await stripe.get_token_balance(user_id)
                if balance <= 0:
                    return False, QuotaStatus(
                        allowed=False,
                        message="Insufficient credits. Please purchase more.",
                        usage=status.usage,
                    )
        except ImportError:
            logger.debug("Credit system not available, skipping deduction")
        except Exception as e:
            logger.warning(f"Credit deduction failed: {e}")

    # Record usage
    await quota.record_usage(user_id, tokens_used, model)

    return True, status


__all__ = [
    "QuotaManager",
    "get_quota_manager",
    "reset_quota_manager",
    "QuotaTier",
    "QuotaUsage",
    "QuotaStatus",
    "QuotaAlert",
    "TIER_LIMITS",
    "ALERT_THRESHOLDS",
    "check_and_record_usage",
]
