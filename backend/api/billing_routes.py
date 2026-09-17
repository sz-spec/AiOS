"""
Stripe API Routes
==================
FastAPI routes for payment processing.
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from tools.stripe_service import (
    StripeService,
    get_stripe_service,
    PlanType,
    BillingInterval,
)

from api.deps import get_current_user, AuthenticatedUser

import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Router
# =============================================================================

router = APIRouter(prefix="/api/billing", tags=["Billing"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateCheckoutRequest(BaseModel):
    plan: str = "pro"
    interval: str = "monthly"
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CreateTokenCheckoutRequest(BaseModel):
    package: str  # tokens_1000, tokens_5000, tokens_10000
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PortalSessionRequest(BaseModel):
    return_url: Optional[str] = None


class UseCreditsRequest(BaseModel):
    """Request to use/deduct credits."""

    amount: int
    reason: str = "ai_generation"
    reference_id: Optional[str] = None


class AddCreditsRequest(BaseModel):
    """Request to add credits (admin only)."""

    user_id: str
    amount: int
    reason: str = "admin_adjustment"
    transaction_type: str = "bonus"  # bonus, admin_adjustment, refund


class RefundCreditsRequest(BaseModel):
    """Request to refund credits for failed operation."""

    amount: int
    reason: str = "generation_failed"
    reference_id: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/checkout")
async def create_checkout_session(
    checkout_req: CreateCheckoutRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Create a Stripe checkout session for subscription.

    Returns:
        session_id and checkout URL
    """
    user_id = user.id
    email = user.email or f"{user_id}@example.com"

    try:
        plan = PlanType(checkout_req.plan)
        interval = BillingInterval(checkout_req.interval)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan or interval")

    try:
        result = await stripe_service.create_checkout_session(
            user_id=user_id,
            email=email,
            plan=plan,
            interval=interval,
            success_url=checkout_req.success_url or "/dashboard?success=true",
            cancel_url=checkout_req.cancel_url or "/pricing?canceled=true",
            trial_days=7 if plan == PlanType.PRO else 0,
        )
        return result
    except Exception as e:
        logger.error("Checkout session creation failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Operation failed")


@router.post("/checkout/tokens")
async def create_token_checkout(
    token_req: CreateTokenCheckoutRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Create checkout session for token purchase.
    """
    user_id = user.id
    email = user.email or f"{user_id}@example.com"

    try:
        result = await stripe_service.create_token_checkout(
            user_id=user_id,
            email=email,
            package=token_req.package,
            success_url=token_req.success_url or "/dashboard?tokens=purchased",
            cancel_url=token_req.cancel_url or "/pricing?canceled=true",
        )
        return result
    except ValueError as e:
        logger.error("Token checkout validation failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=400, detail="Invalid token package")
    except Exception as e:
        logger.error("Token checkout creation failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Operation failed")


@router.post("/portal")
async def create_portal_session(
    portal_req: PortalSessionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Create a customer portal session for managing subscription.
    """
    user_id = user.id

    try:
        result = await stripe_service.create_portal_session(
            user_id=user_id,
            return_url=portal_req.return_url or "/dashboard",
        )
        return result
    except ValueError as e:
        logger.error(
            "Portal session creation failed — customer not found for user %s: %s",
            user_id,
            e,
        )
        raise HTTPException(status_code=404, detail="Customer not found")
    except Exception as e:
        logger.error("Portal session creation failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Operation failed")


@router.get("/subscription")
async def get_subscription(
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Get current user's subscription status.
    """
    user_id = user.id

    subscription = await stripe_service.get_subscription(user_id)

    if not subscription:
        return {
            "plan": "free",
            "status": "none",
            "tokens": await stripe_service.get_token_balance(user_id),
        }

    return {
        "plan": subscription.plan.value,
        "status": subscription.status.value,
        "interval": subscription.interval.value,
        "current_period_end": subscription.current_period_end.isoformat(),
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "tokens": await stripe_service.get_token_balance(user_id),
    }


@router.post("/subscription/cancel")
async def cancel_subscription(
    immediately: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Cancel subscription.
    """
    user_id = user.id

    try:
        result = await stripe_service.cancel_subscription(user_id, immediately)
        return result
    except ValueError as e:
        logger.error(
            "Subscription cancellation failed — not found for user %s: %s", user_id, e
        )
        raise HTTPException(status_code=404, detail="Subscription not found")


@router.post("/subscription/resume")
async def resume_subscription(
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Resume a canceled subscription (before period ends).
    """
    user_id = user.id

    try:
        result = await stripe_service.resume_subscription(user_id)
        return result
    except ValueError as e:
        logger.error(
            "Subscription resume failed — not found for user %s: %s", user_id, e
        )
        raise HTTPException(status_code=404, detail="Subscription not found")


@router.get("/tokens")
async def get_token_balance(
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Get current token balance.
    """
    user_id = user.id
    balance = await stripe_service.get_token_balance(user_id)

    return {"balance": balance}


# =============================================================================
# Credit System Endpoints
# =============================================================================


@router.get("/credits")
async def get_credits(
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Get current credit balance with details.

    Returns balance, lifetime stats, and next refill info.
    """
    user_id = user.id

    balance = await stripe_service.get_token_balance(user_id)

    # Get detailed credit info if available
    credit_info = {
        "balance": balance,
        "user_id": user_id,
    }

    # Get transaction history for lifetime stats if Convex is available
    if stripe_service.convex:
        try:
            txns = (
                await stripe_service.convex.query(
                    "billing:getTransactions", {"userId": user_id, "limit": 200}
                )
                or []
            )
            lifetime_earned = sum(t["amount"] for t in txns if t.get("amount", 0) > 0)
            lifetime_spent = abs(
                sum(t["amount"] for t in txns if t.get("amount", 0) < 0)
            )
            credit_info.update(
                {
                    "lifetime_earned": lifetime_earned,
                    "lifetime_spent": lifetime_spent,
                }
            )
        except Exception:
            pass  # Fall back to basic info

    return credit_info


@router.post("/credits/use")
async def use_credits(
    credit_req: UseCreditsRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Use/deduct credits from user's balance.

    The deduction is performed atomically inside a single Convex mutation
    (billing:useCredits) which checks the balance and deducts in one
    transaction — eliminating the TOCTOU race that a read-then-write pattern
    would introduce.

    Returns success status and new balance.
    """
    user_id = user.id

    if credit_req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Single atomic call — no separate balance pre-check.
    # billing:useCredits reads, validates, and deducts in one Convex transaction.
    # It raises ConvexError("insufficient_credits") if balance would go negative.
    try:
        deducted = await stripe_service.use_tokens(
            user_id=user_id,
            amount=credit_req.amount,
            reason=credit_req.reason,
        )
    except Exception as e:
        err_str = str(e)
        if "insufficient_credits" in err_str:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "insufficient_credits",
                    "message": "Insufficient credits for this operation.",
                    "required": credit_req.amount,
                },
            )
        logger.error("Credit deduction failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to deduct credits")

    if not deducted:
        raise HTTPException(status_code=402, detail="Failed to deduct credits")

    new_balance = await stripe_service.get_token_balance(user_id)

    return {
        "success": True,
        "amount_used": credit_req.amount,
        "balance": new_balance,
        "reason": credit_req.reason,
    }


@router.post("/credits/refund")
async def refund_credits(
    refund_req: RefundCreditsRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Refund credits for a failed operation.

    Use this when an AI generation fails after credits were deducted.
    A refund is only permitted when a prior deduction transaction matching
    this user and amount exists in the credit transaction history.  If no
    such record is found, the request is rejected with 403 to prevent
    users from manufacturing arbitrary credit increases via this endpoint.
    """
    user_id = user.id

    if refund_req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Guard: verify a prior deduction exists for this user+amount before refunding.
    if not stripe_service.convex:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — cannot verify prior deduction",
        )

    try:
        # Fetch recent transactions and look for a matching deduction.
        # We check the last 200 transactions to bound the query cost.
        txns = (
            await stripe_service.convex.query(
                "billing:getTransactions", {"userId": user_id, "limit": 200}
            )
            or []
        )

        # A deduction is stored with a negative amount equal to -refund_req.amount.
        target_amount = -refund_req.amount
        has_prior_deduction = any(t.get("amount") == target_amount for t in txns)

        if not has_prior_deduction:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "no_prior_deduction",
                    "message": "No matching deduction found for this refund amount.",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to verify prior deduction for user %s: %s", user_id, e)
        raise HTTPException(
            status_code=500,
            detail="Failed to verify prior deduction",
        )

    new_balance = await stripe_service.add_tokens(
        user_id=user_id,
        amount=refund_req.amount,
        reason=f"refund: {refund_req.reason}",
    )

    return {
        "success": True,
        "amount_refunded": refund_req.amount,
        "balance": new_balance,
        "reason": refund_req.reason,
    }


@router.post("/credits/add")
async def add_credits_admin(
    add_req: AddCreditsRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Add credits to a user's account (admin only).

    Used for:
    - Promotional credits
    - Customer support adjustments
    - Manual refunds
    """

    # Admin permission check: require admin:full permission
    if not current_user.has_permission("admin:full"):
        raise HTTPException(status_code=403, detail="Admin access required")

    if add_req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    if add_req.transaction_type not in ["bonus", "admin_adjustment", "refund"]:
        raise HTTPException(status_code=400, detail="Invalid transaction type")

    new_balance = await stripe_service.add_tokens(
        user_id=add_req.user_id,
        amount=add_req.amount,
        reason=add_req.reason,
    )

    return {
        "success": True,
        "user_id": add_req.user_id,
        "amount_added": add_req.amount,
        "balance": new_balance,
        "reason": add_req.reason,
        "transaction_type": add_req.transaction_type,
    }


@router.get("/credits/transactions")
async def get_credit_transactions(
    limit: int = 50,
    offset: int = 0,
    transaction_type: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Get credit transaction history for the current user.

    Returns list of all credit additions/deductions with timestamps.
    """
    user_id = user.id

    if not stripe_service.convex:
        return {"transactions": [], "total": 0, "message": "Database not configured"}

    try:
        txns = (
            await stripe_service.convex.query(
                "billing:getTransactions", {"userId": user_id, "limit": offset + limit}
            )
            or []
        )

        # Client-side filter by transaction_type if provided
        if transaction_type:
            txns = [t for t in txns if t.get("reason") == transaction_type]

        total = len(txns)
        page = txns[offset : offset + limit]

        return {
            "transactions": page,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error("Failed to fetch transactions: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch transactions")


# =============================================================================
# Quota Management Endpoints
# =============================================================================


@router.get("/quota")
async def get_quota(user: AuthenticatedUser = Depends(get_current_user)):
    """
    Get current user's quota usage and limits.

    Returns daily/monthly token usage with tier information.
    """
    user_id = user.id

    try:
        from ai.quotas import get_quota_manager

        quota = get_quota_manager()
        usage = await quota.get_usage(user_id)

        return {
            "tier": usage.tier.value,
            "daily": {
                "used": usage.daily_tokens_used,
                "limit": usage.daily_tokens_limit,
                "remaining": usage.daily_remaining,
                "usage_pct": round(usage.daily_usage_pct, 1),
                "reset_at": usage.reset_at,
            },
            "monthly": {
                "used": usage.monthly_tokens_used,
                "limit": usage.monthly_tokens_limit,
                "remaining": usage.monthly_remaining,
                "usage_pct": round(usage.monthly_usage_pct, 1),
                "reset_at": usage.monthly_reset_at,
            },
            "requests_today": usage.requests_today,
            "last_request_at": usage.last_request_at,
        }
    except ImportError:
        return {"error": "Quota system not available", "tier": "free"}
    except Exception as e:
        logger.error("Failed to get quota: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get quota")


@router.post("/quota/check")
async def check_quota(
    estimated_tokens: int = 1000,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Check if user can make a request with estimated token usage.

    Use this before starting an LLM generation.
    """
    user_id = user.id

    try:
        from ai.quotas import get_quota_manager

        quota = get_quota_manager()
        allowed, status = await quota.check_quota(user_id, estimated_tokens)

        return {
            "allowed": allowed,
            "message": status.message,
            "alert_level": status.alert_level,
            "estimated_tokens": estimated_tokens,
            "usage": status.usage.to_dict() if status.usage else None,
        }
    except ImportError:
        return {"allowed": True, "message": "Quota system not available"}
    except Exception as e:
        logger.error("Failed to check quota: %s", e)
        raise HTTPException(status_code=500, detail="Failed to check quota")


@router.post("/quota/record")
async def record_quota_usage(
    tokens_used: int,
    model: str = "unknown",
    request_type: str = "generation",
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Record token usage after an LLM call.

    Called automatically by the LLM factory, but available for manual use.
    """
    user_id = user.id

    if tokens_used <= 0:
        raise HTTPException(status_code=400, detail="tokens_used must be positive")

    try:
        from ai.quotas import get_quota_manager

        quota = get_quota_manager()
        usage = await quota.record_usage(
            user_id=user_id,
            tokens_used=tokens_used,
            model=model,
            request_type=request_type,
        )

        return {
            "success": True,
            "tokens_recorded": tokens_used,
            "daily_total": usage.daily_tokens_used,
            "monthly_total": usage.monthly_tokens_used,
            "daily_remaining": usage.daily_remaining,
        }
    except ImportError:
        return {"success": True, "message": "Quota system not available"}
    except Exception as e:
        logger.error("Failed to record usage: %s", e)
        raise HTTPException(status_code=500, detail="Failed to record usage")


@router.get("/quota/alerts")
async def get_quota_alerts(
    limit: int = 20,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Get recent quota alerts for the current user.

    Returns threshold crossing events (50%, 80%, 95%, 100%).
    """
    user_id = user.id

    try:
        from ai.quotas import get_quota_manager

        quota = get_quota_manager()
        alerts = await quota.get_alerts(user_id=user_id, limit=limit)

        return {
            "alerts": [a.to_dict() for a in alerts],
            "count": len(alerts),
        }
    except ImportError:
        return {"alerts": [], "count": 0}
    except Exception as e:
        logger.error("Failed to get alerts: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get alerts")


@router.get("/quota/tiers")
async def get_quota_tiers(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Get all available quota tiers and their limits.

    Used for pricing page and upgrade prompts.
    """
    try:
        from ai.quotas import TIER_LIMITS, QuotaTier

        return {
            "tiers": {
                "free": {
                    "name": "Free",
                    "daily_tokens": TIER_LIMITS[QuotaTier.FREE]["daily_tokens"],
                    "monthly_tokens": TIER_LIMITS[QuotaTier.FREE]["monthly_tokens"],
                    "max_tokens_per_request": TIER_LIMITS[QuotaTier.FREE][
                        "max_tokens_per_request"
                    ],
                    "rate_limit_rpm": TIER_LIMITS[QuotaTier.FREE]["rate_limit_rpm"],
                },
                "pro": {
                    "name": "Pro",
                    "daily_tokens": TIER_LIMITS[QuotaTier.PRO]["daily_tokens"],
                    "monthly_tokens": TIER_LIMITS[QuotaTier.PRO]["monthly_tokens"],
                    "max_tokens_per_request": TIER_LIMITS[QuotaTier.PRO][
                        "max_tokens_per_request"
                    ],
                    "rate_limit_rpm": TIER_LIMITS[QuotaTier.PRO]["rate_limit_rpm"],
                },
                "enterprise": {
                    "name": "Enterprise",
                    "daily_tokens": TIER_LIMITS[QuotaTier.ENTERPRISE]["daily_tokens"],
                    "monthly_tokens": TIER_LIMITS[QuotaTier.ENTERPRISE][
                        "monthly_tokens"
                    ],
                    "max_tokens_per_request": TIER_LIMITS[QuotaTier.ENTERPRISE][
                        "max_tokens_per_request"
                    ],
                    "rate_limit_rpm": TIER_LIMITS[QuotaTier.ENTERPRISE][
                        "rate_limit_rpm"
                    ],
                },
            },
            "alert_thresholds": [50, 80, 95, 100],
        }
    except ImportError:
        return {
            "tiers": {
                "free": {"daily_tokens": 50000, "monthly_tokens": 1500000},
                "pro": {"daily_tokens": 1000000, "monthly_tokens": 30000000},
                "enterprise": {"daily_tokens": 10000000, "monthly_tokens": 300000000},
            }
        }


# =============================================================================
# Webhook Endpoint
# =============================================================================


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Handle Stripe webhooks with signature verification.

    SECURITY MODEL:
    Stripe webhooks do NOT carry a user Bearer token — they are authenticated
    via the `Stripe-Signature` header, which is an HMAC-SHA256 over the raw
    request body signed with STRIPE_WEBHOOK_SECRET. Adding Depends(get_current_user)
    here silently rejects every legitimate Stripe event with 401, effectively
    disabling billing. Authentication must rely exclusively on construct_event().

    Reference: https://docs.stripe.com/webhooks/signature

    Configure webhook in Stripe Dashboard:
    - URL: https://your-domain/api/billing/webhook
    - Events to enable:
        - checkout.session.completed
        - customer.subscription.created
        - customer.subscription.updated
        - customer.subscription.deleted
        - invoice.paid
        - invoice.payment_failed
        - customer.created
        - customer.updated

    Webhook Secret: Set STRIPE_WEBHOOK_SECRET in environment variables.

    Returns:
        - 200: Event processed successfully (ACK fast, even duplicates)
        - 400: Invalid signature or payload
        - 500: Internal processing error
    """
    payload = (
        await request.body()
    )  # RAW bytes — must not be parsed before construct_event
    signature = request.headers.get("stripe-signature", "")

    if not signature:
        logger.warning("Stripe webhook received without signature header")
        raise HTTPException(status_code=400, detail="Missing Stripe signature")

    try:
        result = await stripe_service.handle_webhook(payload, signature)

        event_type = result.get("event", "unknown")
        handled = result.get("handled", False)
        logger.info("Stripe webhook processed: %s (handled=%s)", event_type, handled)

        return {
            "success": True,
            "event": event_type,
            "handled": handled,
            "result": result.get("result") if handled else None,
        }

    except ValueError as e:
        logger.error("Stripe webhook validation failed: %s", e)
        raise HTTPException(status_code=400, detail="Webhook validation failed")

    except Exception as e:
        logger.exception("Stripe webhook processing error: %s", e)

        try:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
        except ImportError:
            pass

        raise HTTPException(status_code=500, detail="Webhook processing failed")


# =============================================================================
# Plans & Pricing Info
# =============================================================================


@router.get("/plans")
async def get_plans(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Get available subscription plans.
    """
    return {
        "plans": [
            {
                "id": "free",
                "name": "Free",
                "description": "Get started with AI app building",
                "price_monthly": 0,
                "price_yearly": 0,
                "features": [
                    "3 projects",
                    "100 AI generations/month",
                    "Basic templates",
                    "Community support",
                ],
                "tokens_monthly": 100,
            },
            {
                "id": "pro",
                "name": "Pro",
                "description": "For serious builders",
                "price_monthly": 29,
                "price_yearly": 290,
                "features": [
                    "Unlimited projects",
                    "10,000 AI generations/month",
                    "All templates",
                    "GitHub sync",
                    "Priority support",
                    "Custom domains",
                ],
                "tokens_monthly": 10000,
                "popular": True,
            },
            {
                "id": "team",
                "name": "Team",
                "description": "For teams and agencies",
                "price_monthly": 99,
                "price_yearly": 990,
                "features": [
                    "Everything in Pro",
                    "50,000 AI generations/month",
                    "5 team members",
                    "Team collaboration",
                    "API access",
                    "SLA support",
                    "White-label option",
                ],
                "tokens_monthly": 50000,
            },
        ],
        "token_packages": [
            {"id": "tokens_1000", "tokens": 1000, "price": 10},
            {"id": "tokens_5000", "tokens": 5000, "price": 40},
            {"id": "tokens_10000", "tokens": 10000, "price": 70},
        ],
    }
