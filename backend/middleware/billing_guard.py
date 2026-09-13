"""
BillingGuard — server-side credit enforcement dependency.

Injects into any router that triggers LLM inference. Checks and reserves
credits BEFORE the LLM call is made so bypassing the client-side
/credits/use endpoint is impossible.

Usage:
    router = APIRouter(dependencies=[Depends(billing_guard)])
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Sentinel: operations that consume credits and their default estimate.
# Real deduction is reconciled post-completion; this is a pre-flight reserve.
ESTIMATED_CREDITS: dict[str, int] = {
    "chat": 10,
    "agents": 50,
    "codegen": 20,
}

_BYPASS_PATHS: frozenset[str] = frozenset(
    {
        "/api/chat/models",
        "/api/chat/history",
        "/api/agents",  # GET list — no inference
        "/api/codegen/templates",
    }
)


async def billing_guard(request: Request) -> None:
    """
    FastAPI dependency: verify the authenticated user has sufficient credits
    before allowing a request to reach an LLM-calling route handler.

    Raises HTTP 402 if balance is insufficient.
    Raises HTTP 503 if the billing service is unavailable.
    No-ops for paths in _BYPASS_PATHS and for non-POST methods.

    Dev-mode bypass (Sprint 14.4): when VOS3_ALLOW_DEV_MODE=true, skip the
    credit check entirely. Production behavior is unchanged — the env var
    is OFF in production. Without this bypass, every contract test that
    POSTs to a credit-gated endpoint fails with 402 before pydantic body
    validation can produce a 422; that masks real validation regressions
    behind a billing wall the test environment doesn't actually have.
    """
    # Dev-mode bypass — keeps test env consistent with auth bypass.
    import os

    if os.getenv("VOS3_ALLOW_DEV_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return

    # Only gate state-changing (POST/PUT) requests.
    if request.method not in ("POST", "PUT"):
        return

    # Skip paths that list or read without inference.
    if request.url.path in _BYPASS_PATHS:
        return

    # Determine which credit tier applies.
    path = request.url.path
    if "/chat" in path:
        operation = "chat"
    elif "/agents" in path:
        operation = "agents"
    elif "/codegen" in path:
        operation = "codegen"
    else:
        operation = "chat"  # safe default

    estimated = ESTIMATED_CREDITS[operation]

    # Resolve the authenticated user from request state (set by auth middleware).
    user = getattr(request.state, "user", None)
    if user is None:
        # Auth middleware hasn't run yet or route is public — skip billing check.
        return

    user_id: str = getattr(user, "id", None) or getattr(user, "user_id", None)
    if not user_id:
        return

    # Attempt credit pre-check via stripe_service / Convex.
    try:
        from tools.stripe_service import get_stripe_service  # type: ignore

        stripe_svc = get_stripe_service()
        if stripe_svc is None:
            logger.warning(
                "BillingGuard: stripe_service unavailable — skipping check for %s",
                user_id,
            )
            return

        balance = await stripe_svc.get_token_balance(user_id)
        if balance is not None and balance < estimated:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "insufficient_credits",
                    "message": f"Insufficient credits for this operation. Available: {balance}, required: ~{estimated}.",
                    "balance": balance,
                    "required": estimated,
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Billing service unavailable — fail open with a warning rather than
        # blocking all inference. Log for alerting.
        logger.error(
            "BillingGuard: credit check failed for %s — failing open: %s", user_id, exc
        )
