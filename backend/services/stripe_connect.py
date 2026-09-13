"""
Stripe Connect Integration
============================
Developer onboarding (Express accounts) and payout routing
for the VOS3 app marketplace.
"""

import os
from typing import Dict, Optional


class StripeConnectService:
    """Manages Stripe Connect for developer revenue sharing."""

    def __init__(self):
        self._stripe_key = os.environ.get("STRIPE_SECRET_KEY")
        self._stripe = None
        if self._stripe_key:
            try:
                import stripe

                stripe.api_key = self._stripe_key
                self._stripe = stripe
            except ImportError:
                pass

    @property
    def available(self) -> bool:
        return self._stripe is not None

    async def create_express_account(
        self, email: str, country: str = "US"
    ) -> Optional[Dict]:
        """Create a Stripe Express account for a developer."""
        if not self._stripe:
            return {"account_id": "acct_dev_mock", "onboarding_url": "#", "mode": "dev"}

        account = self._stripe.Account.create(
            type="express",
            email=email,
            country=country,
            capabilities={
                "transfers": {"requested": True},
            },
        )

        link = self._stripe.AccountLink.create(
            account=account.id,
            refresh_url=os.environ.get("APP_URL", "http://localhost:3000")
            + "/developer/stripe/refresh",
            return_url=os.environ.get("APP_URL", "http://localhost:3000")
            + "/developer/stripe/return",
            type="account_onboarding",
        )

        return {
            "account_id": account.id,
            "onboarding_url": link.url,
        }

    async def create_transfer(
        self, destination_account: str, amount_cents: int, description: str = ""
    ) -> Optional[Dict]:
        """Transfer funds to a developer's Connect account."""
        if not self._stripe:
            return {"transfer_id": "tr_dev_mock", "amount": amount_cents, "mode": "dev"}

        transfer = self._stripe.Transfer.create(
            amount=amount_cents,
            currency="usd",
            destination=destination_account,
            description=description or "VOS3 App Revenue",
        )

        return {
            "transfer_id": transfer.id,
            "amount": amount_cents,
        }

    async def get_account_status(self, account_id: str) -> Dict:
        """Check if a Connect account is ready for payouts."""
        if not self._stripe:
            return {"charges_enabled": True, "payouts_enabled": True, "mode": "dev"}

        account = self._stripe.Account.retrieve(account_id)
        return {
            "charges_enabled": account.charges_enabled,
            "payouts_enabled": account.payouts_enabled,
            "details_submitted": account.details_submitted,
        }


_service: Optional[StripeConnectService] = None


def get_stripe_connect() -> StripeConnectService:
    global _service
    if _service is None:
        _service = StripeConnectService()
    return _service
