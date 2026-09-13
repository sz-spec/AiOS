"""
Revenue Share Engine
=====================
Calculate revenue split between developers and VOS3 platform.
Default: 70% developer / 30% VOS3. Per-app overrides supported.
"""

from typing import Dict, Optional

# Default split: 70% developer, 30% platform
DEFAULT_DEVELOPER_PERCENT = 70
DEFAULT_PLATFORM_PERCENT = 30


class RevenueShareEngine:
    """Calculate and execute revenue splits for app purchases."""

    def __init__(self):
        self._overrides: Dict[str, int] = {}  # app_id -> developer_percent

    def set_override(self, app_id: str, developer_percent: int):
        """Set a custom revenue split for a specific app."""
        if developer_percent < 0 or developer_percent > 100:
            raise ValueError("Developer percent must be 0-100")
        self._overrides[app_id] = developer_percent

    def calculate_split(self, app_id: str, amount_cents: int) -> Dict:
        """Calculate the revenue split for a purchase.

        Returns:
            {
                "total": amount_cents,
                "developer_amount": ...,
                "platform_amount": ...,
                "developer_percent": ...,
                "platform_percent": ...,
            }
        """
        dev_percent = self._overrides.get(app_id, DEFAULT_DEVELOPER_PERCENT)
        platform_percent = 100 - dev_percent

        developer_amount = int(amount_cents * dev_percent / 100)
        platform_amount = amount_cents - developer_amount  # Remainder to platform

        return {
            "total": amount_cents,
            "developer_amount": developer_amount,
            "platform_amount": platform_amount,
            "developer_percent": dev_percent,
            "platform_percent": platform_percent,
        }

    async def process_purchase(
        self,
        app_id: str,
        developer_stripe_account: str,
        amount_cents: int,
        buyer_id: str,
    ) -> Dict:
        """Process a purchase: calculate split and transfer to developer."""
        split = self.calculate_split(app_id, amount_cents)

        # Transfer developer's share via Stripe Connect
        if split["developer_amount"] > 0 and developer_stripe_account:
            try:
                from services.stripe_connect import get_stripe_connect

                stripe_connect = get_stripe_connect()
                transfer = await stripe_connect.create_transfer(
                    destination_account=developer_stripe_account,
                    amount_cents=split["developer_amount"],
                    description=f"VOS3 App Revenue: {app_id}",
                )
                split["transfer"] = transfer
            except Exception as e:
                split["transfer_error"] = str(e)

        # Record payout in Convex via the repositories layer.
        try:
            from core.repositories import get_developer_payout_repository

            get_developer_payout_repository().record(
                developer_id=developer_stripe_account,
                amount=split["developer_amount"] / 100,
                currency="usd",
                status="completed" if "transfer" in split else "pending",
                period_start_ms=0,
                period_end_ms=0,
            )
        except Exception:
            pass

        return split


_engine: Optional[RevenueShareEngine] = None


def get_revenue_engine() -> RevenueShareEngine:
    global _engine
    if _engine is None:
        _engine = RevenueShareEngine()
    return _engine
