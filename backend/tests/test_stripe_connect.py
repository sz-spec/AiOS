"""Tests for services/stripe_connect.py — Stripe Connect integration."""

import pytest

from services.stripe_connect import StripeConnectService, get_stripe_connect


class TestStripeConnectDevMode:
    """Tests with no STRIPE_SECRET_KEY (dev mode)."""

    def setup_method(self):
        self.svc = StripeConnectService()

    def test_available_false_no_key(self):
        assert self.svc.available is False

    @pytest.mark.asyncio
    async def test_create_express_account_returns_mock(self):
        result = await self.svc.create_express_account("dev@test.com")
        assert result["account_id"] == "acct_dev_mock"
        assert result["mode"] == "dev"

    @pytest.mark.asyncio
    async def test_create_transfer_returns_mock(self):
        result = await self.svc.create_transfer("acct_123", 5000)
        assert result["transfer_id"] == "tr_dev_mock"
        assert result["amount"] == 5000
        assert result["mode"] == "dev"

    @pytest.mark.asyncio
    async def test_get_account_status_returns_mock(self):
        result = await self.svc.get_account_status("acct_123")
        assert result["charges_enabled"] is True
        assert result["payouts_enabled"] is True
        assert result["mode"] == "dev"


class TestGetStripeConnect:
    def test_singleton(self):
        import services.stripe_connect as mod

        mod._service = None
        s1 = get_stripe_connect()
        s2 = get_stripe_connect()
        assert s1 is s2
        mod._service = None

    def test_dev_mode_not_available(self):
        import services.stripe_connect as mod

        mod._service = None
        svc = get_stripe_connect()
        assert svc.available is False
        mod._service = None
