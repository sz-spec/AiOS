"""
Stripe Integration Service
===========================
Complete payment processing with Convex.

Features:
- Checkout sessions
- Subscriptions (monthly/yearly)
- AI token credits (hybrid model)
- Webhooks with signature verification
- Customer portal
"""

import os
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum
from dataclasses import dataclass

import stripe
from pydantic import BaseModel

# =============================================================================
# Configuration
# =============================================================================

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")

# Pin the Stripe API version so webhook payload schema stays stable when
# Stripe promotes newer versions. Our handlers are audited against this one.
# Reference: https://docs.stripe.com/upgrades (2026-03-25.dahlia shipped with SDK v15)
stripe.api_version = os.getenv("STRIPE_API_VERSION", "2026-03-25.dahlia")

# Price IDs (create in Stripe Dashboard)
PRICE_IDS = {
    "free": None,
    "pro_monthly": os.getenv("STRIPE_PRICE_PRO_MONTHLY", "price_xxx"),
    "pro_yearly": os.getenv("STRIPE_PRICE_PRO_YEARLY", "price_xxx"),
    "team_monthly": os.getenv("STRIPE_PRICE_TEAM_MONTHLY", "price_xxx"),
    "team_yearly": os.getenv("STRIPE_PRICE_TEAM_YEARLY", "price_xxx"),
    "tokens_1000": os.getenv("STRIPE_PRICE_TOKENS_1000", "price_xxx"),
    "tokens_5000": os.getenv("STRIPE_PRICE_TOKENS_5000", "price_xxx"),
    "tokens_10000": os.getenv("STRIPE_PRICE_TOKENS_10000", "price_xxx"),
}

TOKEN_AMOUNTS = {
    "tokens_1000": 1000,
    "tokens_5000": 5000,
    "tokens_10000": 10000,
}


# =============================================================================
# Models
# =============================================================================


class PlanType(str, Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"


class BillingInterval(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"


class CheckoutRequest(BaseModel):
    """Checkout session request."""

    user_id: str
    email: str
    plan: PlanType = PlanType.PRO
    interval: BillingInterval = BillingInterval.MONTHLY
    success_url: str = "/dashboard?success=true"
    cancel_url: str = "/pricing?canceled=true"


class TokenPurchaseRequest(BaseModel):
    """Token purchase request."""

    user_id: str
    email: str
    package: str  # tokens_1000, tokens_5000, tokens_10000
    success_url: str = "/dashboard?tokens=purchased"
    cancel_url: str = "/pricing?canceled=true"


class PortalRequest(BaseModel):
    """Customer portal request."""

    user_id: str
    return_url: str = "/dashboard"


@dataclass
class SubscriptionInfo:
    """Subscription information."""

    id: str
    status: SubscriptionStatus
    plan: PlanType
    interval: BillingInterval
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    stripe_subscription_id: str
    stripe_customer_id: str


# =============================================================================
# Stripe Service
# =============================================================================


class StripeService:
    """
    Complete Stripe integration service backed by Convex.

    Usage:
        service = StripeService(convex_client)

        # Create checkout
        session = await service.create_checkout_session(
            user_id="user_convex_id",
            email="user@example.com",
            plan=PlanType.PRO,
            interval=BillingInterval.MONTHLY
        )
    """

    def __init__(self, convex_client=None):
        self.convex = convex_client

    # -------------------------------------------------------------------------
    # Checkout Sessions
    # -------------------------------------------------------------------------

    async def create_checkout_session(
        self,
        user_id: str,
        email: str,
        plan: PlanType,
        interval: BillingInterval,
        success_url: str = "/dashboard",
        cancel_url: str = "/pricing",
        trial_days: int = 0,
    ) -> Dict[str, Any]:
        """Create a Stripe checkout session for subscription."""
        customer = await self._get_or_create_customer(user_id, email)

        price_key = f"{plan.value}_{interval.value}"
        price_id = PRICE_IDS.get(price_key)

        if not price_id:
            raise ValueError(f"Invalid plan/interval: {price_key}")

        session_params = {
            "customer": customer.id,
            "payment_method_types": ["card"],
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url,
            "client_reference_id": user_id,
            "metadata": {
                "user_id": user_id,
                "plan": plan.value,
                "interval": interval.value,
            },
            "subscription_data": {
                "metadata": {"user_id": user_id, "plan": plan.value},
            },
        }

        if trial_days > 0:
            session_params["subscription_data"]["trial_period_days"] = trial_days

        session = stripe.checkout.Session.create(**session_params)

        return {"session_id": session.id, "url": session.url}

    async def create_token_checkout(
        self,
        user_id: str,
        email: str,
        package: str,
        success_url: str = "/dashboard",
        cancel_url: str = "/pricing",
    ) -> Dict[str, Any]:
        """Create checkout session for token purchase (one-time)."""
        customer = await self._get_or_create_customer(user_id, email)

        price_id = PRICE_IDS.get(package)
        if not price_id:
            raise ValueError(f"Invalid token package: {package}")

        session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=["card"],
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            client_reference_id=user_id,
            metadata={
                "user_id": user_id,
                "package": package,
                "tokens": TOKEN_AMOUNTS.get(package, 0),
                "type": "token_purchase",
            },
        )

        return {"session_id": session.id, "url": session.url}

    # -------------------------------------------------------------------------
    # Customer Portal
    # -------------------------------------------------------------------------

    async def create_portal_session(
        self, user_id: str, return_url: str = "/dashboard"
    ) -> Dict[str, str]:
        """Create a customer portal session for managing subscription."""
        customer_id = await self._get_customer_id(user_id)

        if not customer_id:
            raise ValueError("No subscription found for user")

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )

        return {"url": session.url}

    # -------------------------------------------------------------------------
    # Subscription Management
    # -------------------------------------------------------------------------

    async def get_subscription(self, user_id: str) -> Optional[SubscriptionInfo]:
        """Get user's current subscription from Convex."""
        if not self.convex:
            return None

        data = await self.convex.query("billing:getSubscription", {"userId": user_id})

        if not data:
            return None

        return SubscriptionInfo(
            id=data["_id"],
            status=SubscriptionStatus(data["status"]),
            plan=PlanType(data["plan"]),
            interval=BillingInterval(data["interval"]),
            current_period_start=datetime.fromtimestamp(
                data["currentPeriodStart"] / 1000
            ),
            current_period_end=datetime.fromtimestamp(data["currentPeriodEnd"] / 1000),
            cancel_at_period_end=data["cancelAtPeriodEnd"],
            stripe_subscription_id=data["stripeSubscriptionId"],
            stripe_customer_id=data["stripeCustomerId"],
        )

    async def cancel_subscription(
        self, user_id: str, immediately: bool = False
    ) -> Dict[str, Any]:
        """Cancel subscription."""
        sub = await self.get_subscription(user_id)

        if not sub:
            raise ValueError("No active subscription")

        if immediately:
            stripe.Subscription.delete(sub.stripe_subscription_id)
        else:
            stripe.Subscription.modify(
                sub.stripe_subscription_id, cancel_at_period_end=True
            )

        return {"status": "canceled", "immediately": immediately}

    async def resume_subscription(self, user_id: str) -> Dict[str, Any]:
        """Resume a canceled subscription (before period end)."""
        sub = await self.get_subscription(user_id)

        if not sub:
            raise ValueError("No subscription found")

        stripe.Subscription.modify(
            sub.stripe_subscription_id, cancel_at_period_end=False
        )

        return {"status": "resumed"}

    # -------------------------------------------------------------------------
    # Token/Credits Management
    # -------------------------------------------------------------------------

    async def get_token_balance(self, user_id: str) -> int:
        """Get user's token balance from Convex."""
        if not self.convex:
            return 0

        balance = await self.convex.query("billing:getBalance", {"userId": user_id})
        return balance or 0

    async def add_tokens(
        self, user_id: str, amount: int, reason: str = "purchase"
    ) -> int:
        """Add tokens to user's balance via Convex mutation."""
        if not self.convex:
            return 0

        new_balance = await self.convex.mutation(
            "billing:addCredits",
            {
                "userId": user_id,
                "amount": amount,
                "reason": reason,
            },
        )
        return new_balance or 0

    async def use_tokens(
        self, user_id: str, amount: int, reason: str = "usage"
    ) -> bool:
        """Deduct tokens from user's balance via Convex mutation."""
        if not self.convex:
            return False

        result = await self.convex.mutation(
            "billing:useCredits",
            {
                "userId": user_id,
                "amount": amount,
                "reason": reason,
            },
        )
        return bool(result)

    # -------------------------------------------------------------------------
    # Webhooks
    # -------------------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """
        Handle Stripe webhook with signature verification.

        Stripe SDK v15 compatibility:
        - StripeObject no longer inherits from dict (CHANGELOG 2026-03).
          .get()/.items()/.update() methods were removed.
        - We use attribute access (obj.field) for guaranteed fields and
          _sget()/_smeta() helpers below for optional fields.
        - The __getitem__ operator still works on StripeObject so obj["key"]
          remains valid for explicit key lookups.

        Idempotency:
        - Delegated to Convex via billing:recordWebhookEvent, which inserts
          by event.id and returns duplicate=True if the event was already seen.
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, STRIPE_WEBHOOK_SECRET
            )
        except stripe.SignatureVerificationError:
            raise ValueError("Invalid webhook signature")

        # event.type and event.data.object are stable attributes on Event.
        event_type: str = event.type
        data = event.data.object
        event_id: str = event.id

        if self.convex:
            prior = await self.convex.mutation(
                "billing:recordWebhookEvent",
                {"eventId": event_id, "type": event_type},
            )
            if prior and prior.get("duplicate"):
                return {"event": event_type, "handled": True, "duplicate": True}

        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
            "customer.subscription.created": self._handle_subscription_created,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "invoice.paid": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_payment_failed,
        }

        handler = handlers.get(event_type)
        if handler:
            result = await handler(data)
            return {"event": event_type, "handled": True, "result": result}

        return {"event": event_type, "handled": False}

    # -------------------------------------------------------------------------
    # StripeObject access helpers (v15-safe)
    # -------------------------------------------------------------------------

    @staticmethod
    def _sget(obj, key: str, default=None):
        """
        Safe attribute-or-key lookup on a StripeObject.
        v15 removed .get() — getattr handles attributes, __getitem__ handles
        keys. Returns `default` on either missing.
        """
        if obj is None:
            return default
        try:
            value = getattr(obj, key)
            return value if value is not None else default
        except AttributeError:
            try:
                return obj[key]
            except (KeyError, TypeError):
                return default

    @staticmethod
    def _smeta(obj, key: str, default=None):
        """Safe metadata field lookup — metadata is itself a StripeObject."""
        md = StripeService._sget(obj, "metadata")
        return StripeService._sget(md, key, default)

    # -------------------------------------------------------------------------
    # Event handlers (accept StripeObject; no dict methods)
    # -------------------------------------------------------------------------

    async def _handle_checkout_completed(self, session) -> Dict:
        """Handle successful checkout."""
        user_id = self._sget(session, "client_reference_id") or self._smeta(
            session, "user_id"
        )
        mode = self._sget(session, "mode")

        if mode == "payment":
            tokens = int(self._smeta(session, "tokens", 0) or 0)
            if tokens > 0 and user_id:
                await self.add_tokens(user_id, tokens, "purchase")
                return {"type": "tokens", "amount": tokens}

        return {"type": mode, "user_id": user_id}

    async def _handle_subscription_created(self, subscription) -> Dict:
        """Handle new subscription — upsert in Convex."""
        user_id = self._smeta(subscription, "user_id")
        plan = self._smeta(subscription, "plan", "pro")

        if not self.convex or not user_id:
            return {"error": "Missing data"}

        interval = "monthly"
        items = self._sget(subscription, "items")
        items_data = self._sget(items, "data") if items else None
        if items_data:
            price = self._sget(items_data[0], "price")
            recurring = self._sget(price, "recurring")
            if self._sget(recurring, "interval") == "year":
                interval = "yearly"

        await self.convex.mutation(
            "billing:upsertSubscription",
            {
                "userId": user_id,
                "stripeSubscriptionId": subscription.id,
                "stripeCustomerId": subscription.customer,
                "status": subscription.status,
                "plan": plan,
                "interval": interval,
                "currentPeriodStart": subscription.current_period_start * 1000,
                "currentPeriodEnd": subscription.current_period_end * 1000,
                "cancelAtPeriodEnd": bool(
                    self._sget(subscription, "cancel_at_period_end", False)
                ),
            },
        )

        tokens_per_plan = {"pro": 10000, "team": 50000}
        tokens = tokens_per_plan.get(plan, 0)
        if tokens:
            await self.add_tokens(user_id, tokens, f"subscription_{plan}")

        return {"user_id": user_id, "plan": plan, "tokens_added": tokens}

    async def _handle_subscription_updated(self, subscription) -> Dict:
        """Handle subscription update in Convex."""
        user_id = self._smeta(subscription, "user_id")

        if not self.convex or not user_id:
            return {"error": "Missing data"}

        await self.convex.mutation(
            "billing:updateSubscriptionStatus",
            {
                "stripeSubscriptionId": subscription.id,
                "status": subscription.status,
                "cancelAtPeriodEnd": bool(
                    self._sget(subscription, "cancel_at_period_end", False)
                ),
                "currentPeriodStart": subscription.current_period_start * 1000,
                "currentPeriodEnd": subscription.current_period_end * 1000,
            },
        )

        return {"user_id": user_id, "status": subscription.status}

    async def _handle_subscription_deleted(self, subscription) -> Dict:
        """Handle subscription cancellation in Convex."""
        if not self.convex:
            return {"error": "No database"}

        await self.convex.mutation(
            "billing:cancelSubscriptionByStripeId",
            {
                "stripeSubscriptionId": subscription.id,
            },
        )

        return {"subscription_id": subscription.id, "status": "canceled"}

    async def _handle_invoice_paid(self, invoice) -> Dict:
        """Handle successful payment (renewal) — add tokens."""
        customer_id = invoice.customer

        if not self.convex:
            return {"error": "No database"}

        user = await self.convex.query(
            "billing:getUserByStripeCustomerId",
            {
                "stripeCustomerId": customer_id,
            },
        )

        if user:
            sub = await self.get_subscription(user["_id"])
            plan = sub.plan.value if sub else "pro"
            tokens_per_plan = {"pro": 10000, "team": 50000}
            tokens = tokens_per_plan.get(plan, 0)
            if tokens:
                await self.add_tokens(user["_id"], tokens, f"renewal_{plan}")
            return {"user_id": user["_id"], "tokens_added": tokens}

        return {"customer_id": customer_id}

    async def _handle_payment_failed(self, invoice) -> Dict:
        """Handle failed payment — mark subscription past_due in Convex."""
        customer_id = invoice.customer

        if not self.convex:
            return {"error": "No database"}

        user = await self.convex.query(
            "billing:getUserByStripeCustomerId",
            {
                "stripeCustomerId": customer_id,
            },
        )

        if user:
            sub = await self.get_subscription(user["_id"])
            if sub:
                await self.convex.mutation(
                    "billing:updateSubscriptionStatus",
                    {
                        "stripeSubscriptionId": sub.stripe_subscription_id,
                        "status": "past_due",
                    },
                )

        return {"customer_id": customer_id, "status": "past_due"}

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    async def _get_or_create_customer(
        self, user_id: str, email: str
    ) -> stripe.Customer:
        """Get existing Stripe customer or create new one."""
        customer_id = await self._get_customer_id(user_id)

        if customer_id:
            return stripe.Customer.retrieve(customer_id)

        customer = stripe.Customer.create(
            email=email,
            metadata={"user_id": user_id},
        )

        if self.convex:
            await self.convex.mutation(
                "billing:getOrCreateStripeCustomer",
                {
                    "userId": user_id,
                    "stripeCustomerId": customer.id,
                },
            )

        return customer

    async def _get_customer_id(self, user_id: str) -> Optional[str]:
        """Get Stripe customer ID from Convex."""
        if not self.convex:
            return None

        return await self.convex.query(
            "billing:getStripeCustomerId", {"userId": user_id}
        )


# =============================================================================
# FastAPI Integration
# =============================================================================

_stripe_service: Optional[StripeService] = None


def get_stripe_service() -> StripeService:
    """Get Stripe service singleton."""
    global _stripe_service
    if _stripe_service is None:
        _stripe_service = StripeService()
    return _stripe_service


def init_stripe_with_convex(convex_client) -> StripeService:
    """Initialize Stripe service with Convex client."""
    global _stripe_service
    _stripe_service = StripeService(convex_client)
    return _stripe_service
