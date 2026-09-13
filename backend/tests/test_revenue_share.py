"""Tests for services/revenue_share.py — Revenue Share Engine."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.revenue_share import (
    RevenueShareEngine,
    get_revenue_engine,
    DEFAULT_DEVELOPER_PERCENT,
    DEFAULT_PLATFORM_PERCENT,
)


class TestRevenueShareEngine:
    def setup_method(self):
        self.engine = RevenueShareEngine()

    # --- calculate_split ---

    def test_default_70_30_split(self):
        result = self.engine.calculate_split("app1", 10000)
        assert result["total"] == 10000
        assert result["developer_amount"] == 7000
        assert result["platform_amount"] == 3000
        assert result["developer_percent"] == 70
        assert result["platform_percent"] == 30

    def test_split_with_override(self):
        self.engine.set_override("app2", 80)
        result = self.engine.calculate_split("app2", 10000)
        assert result["developer_amount"] == 8000
        assert result["platform_amount"] == 2000
        assert result["developer_percent"] == 80
        assert result["platform_percent"] == 20

    def test_split_with_zero_amount(self):
        result = self.engine.calculate_split("app1", 0)
        assert result["total"] == 0
        assert result["developer_amount"] == 0
        assert result["platform_amount"] == 0

    def test_split_odd_cents_remainder_to_platform(self):
        # 70% of 999 = 699.3 -> int(699.3) = 699, platform gets 300
        result = self.engine.calculate_split("app1", 999)
        assert result["developer_amount"] == 699
        assert result["platform_amount"] == 300
        assert result["developer_amount"] + result["platform_amount"] == 999

    def test_split_single_cent(self):
        result = self.engine.calculate_split("app1", 1)
        assert result["developer_amount"] == 0
        assert result["platform_amount"] == 1

    # --- set_override ---

    def test_set_override_reject_negative(self):
        with pytest.raises(ValueError, match="0-100"):
            self.engine.set_override("app1", -1)

    def test_set_override_reject_over_100(self):
        with pytest.raises(ValueError, match="0-100"):
            self.engine.set_override("app1", 101)

    def test_set_override_boundary_0(self):
        self.engine.set_override("app1", 0)
        result = self.engine.calculate_split("app1", 1000)
        assert result["developer_amount"] == 0
        assert result["platform_amount"] == 1000

    def test_set_override_boundary_100(self):
        self.engine.set_override("app1", 100)
        result = self.engine.calculate_split("app1", 1000)
        assert result["developer_amount"] == 1000
        assert result["platform_amount"] == 0

    # --- process_purchase ---

    @pytest.mark.asyncio
    async def test_process_purchase_with_mocked_stripe(self):
        mock_stripe = MagicMock()
        mock_stripe.create_transfer = AsyncMock(
            return_value={"transfer_id": "tr_123", "amount": 7000}
        )
        with patch(
            "services.stripe_connect.get_stripe_connect", return_value=mock_stripe
        ), patch("db.convex.get_convex_client", side_effect=Exception("no convex")):
            result = await self.engine.process_purchase(
                "app1", "acct_dev", 10000, "buyer1"
            )
        assert result["developer_amount"] == 7000
        assert result["transfer"]["transfer_id"] == "tr_123"
        mock_stripe.create_transfer.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_purchase_missing_stripe_account(self):
        with patch("db.convex.get_convex_client", side_effect=Exception("no convex")):
            result = await self.engine.process_purchase("app1", "", 10000, "buyer1")
        assert result["developer_amount"] == 7000
        assert "transfer" not in result

    @pytest.mark.asyncio
    async def test_process_purchase_records_payout(self):
        mock_db = MagicMock()
        mock_stripe = MagicMock()
        mock_stripe.create_transfer = AsyncMock(return_value={"transfer_id": "tr_456"})
        with patch(
            "services.stripe_connect.get_stripe_connect", return_value=mock_stripe
        ), patch("db.convex.get_convex_client", return_value=mock_db):
            await self.engine.process_purchase("app1", "acct_dev", 10000, "buyer1")
        mock_db.mutation.assert_called_once()
        call_args = mock_db.mutation.call_args
        assert call_args[0][0] == "developerPayouts:create"


class TestGetRevenueEngine:
    def test_singleton(self):
        import services.revenue_share as mod

        mod._engine = None
        e1 = get_revenue_engine()
        e2 = get_revenue_engine()
        assert e1 is e2
        mod._engine = None

    def test_defaults(self):
        assert DEFAULT_DEVELOPER_PERCENT == 70
        assert DEFAULT_PLATFORM_PERCENT == 30
