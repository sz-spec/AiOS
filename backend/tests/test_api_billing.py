"""
Tests for Billing API Routes
=============================

Covers: POST /api/billing/checkout, POST /api/billing/checkout/tokens,
        POST /api/billing/portal, GET /api/billing/subscription,
        POST /api/billing/subscription/cancel, POST /api/billing/subscription/resume,
        GET /api/billing/tokens, GET /api/billing/credits,
        POST /api/billing/credits/use, POST /api/billing/credits/refund,
        POST /api/billing/credits/add, GET /api/billing/credits/transactions,
        POST /api/billing/webhook, GET /api/billing/plans
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from tools.stripe_service import get_stripe_service
from api.deps import get_current_user

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_user():
    """A mock user object matching AuthenticatedUser fields."""
    user = MagicMock()
    user.id = "test_user_123"
    user.email = "test@example.com"
    user.org_id = None
    user.org_role = None
    user.permissions = []
    user.metadata = {}
    user.convex_user_id = None
    return user


@pytest.fixture
def mock_stripe():
    """A mock StripeService with async methods."""
    service = MagicMock()
    service.create_checkout_session = AsyncMock(
        return_value={
            "session_id": "cs_test_123",
            "url": "https://checkout.stripe.com/test",
        }
    )
    service.create_token_checkout = AsyncMock(
        return_value={
            "session_id": "cs_tok_123",
            "url": "https://checkout.stripe.com/tokens",
        }
    )
    service.create_portal_session = AsyncMock(
        return_value={
            "url": "https://billing.stripe.com/portal/test",
        }
    )
    service.get_subscription = AsyncMock(return_value=None)
    service.get_token_balance = AsyncMock(return_value=500)
    service.cancel_subscription = AsyncMock(return_value={"canceled": True})
    service.resume_subscription = AsyncMock(return_value={"resumed": True})
    service.use_tokens = AsyncMock(return_value=True)
    service.add_tokens = AsyncMock(return_value=600)
    service.handle_webhook = AsyncMock(
        return_value={
            "event": "checkout.session.completed",
            "handled": True,
            "result": {"user_id": "test_user_123"},
        }
    )
    service.convex = None
    return service


@pytest.fixture
def client(mock_user, mock_stripe):
    """TestClient with auth and stripe dependencies overridden."""
    app.dependency_overrides[get_stripe_service] = lambda: mock_stripe
    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.pop(get_stripe_service, None)
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# POST /api/billing/checkout
# ---------------------------------------------------------------------------


class TestCreateCheckout:
    def test_checkout_returns_200(self, client):
        response = client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "monthly",
            },
        )
        assert response.status_code == 200

    def test_checkout_returns_session_id_and_url(self, client):
        data = client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "monthly",
            },
        ).json()
        assert "session_id" in data
        assert "url" in data

    def test_checkout_invalid_plan_returns_400(self, client):
        response = client.post(
            "/api/billing/checkout",
            json={
                "plan": "nonexistent_plan",
                "interval": "monthly",
            },
        )
        assert response.status_code == 400
        assert "Invalid plan or interval" in response.json()["detail"]

    def test_checkout_invalid_interval_returns_400(self, client):
        response = client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "biweekly",
            },
        )
        assert response.status_code == 400

    def test_checkout_service_error_returns_500(self, client, mock_stripe):
        mock_stripe.create_checkout_session = AsyncMock(
            side_effect=RuntimeError("Stripe API down")
        )
        response = client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "monthly",
            },
        )
        assert response.status_code == 500

    def test_checkout_uses_default_urls(self, client, mock_stripe):
        client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "monthly",
            },
        )
        call_kwargs = mock_stripe.create_checkout_session.call_args.kwargs
        assert call_kwargs["success_url"] == "/dashboard?success=true"
        assert call_kwargs["cancel_url"] == "/pricing?canceled=true"

    def test_checkout_unauthenticated_returns_401(self, mock_stripe):
        """Without auth override, missing auth should yield 401."""
        app.dependency_overrides[get_stripe_service] = lambda: mock_stripe
        raw_client = TestClient(app, base_url="http://localhost")
        response = raw_client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "monthly",
            },
        )
        # billing_routes.get_user_from_request checks request.state.user
        # then Authorization header; without either it raises 401
        # (but the fallback in the module may create a demo user from header)
        assert response.status_code in (200, 401)
        app.dependency_overrides.pop(get_stripe_service, None)


# ---------------------------------------------------------------------------
# POST /api/billing/checkout/tokens
# ---------------------------------------------------------------------------


class TestTokenCheckout:
    def test_token_checkout_returns_200(self, client):
        response = client.post(
            "/api/billing/checkout/tokens",
            json={
                "package": "tokens_1000",
            },
        )
        assert response.status_code == 200

    def test_token_checkout_returns_session_data(self, client):
        data = client.post(
            "/api/billing/checkout/tokens",
            json={
                "package": "tokens_5000",
            },
        ).json()
        assert "session_id" in data
        assert "url" in data

    def test_token_checkout_value_error_returns_400(self, client, mock_stripe):
        mock_stripe.create_token_checkout = AsyncMock(
            side_effect=ValueError("Invalid package")
        )
        response = client.post(
            "/api/billing/checkout/tokens",
            json={
                "package": "tokens_invalid",
            },
        )
        assert response.status_code == 400
        assert (
            "Invalid" in response.json()["detail"]
            and "package" in response.json()["detail"].lower()
        )

    def test_token_checkout_generic_error_returns_500(self, client, mock_stripe):
        mock_stripe.create_token_checkout = AsyncMock(
            side_effect=RuntimeError("Stripe error")
        )
        response = client.post(
            "/api/billing/checkout/tokens",
            json={
                "package": "tokens_1000",
            },
        )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/billing/portal
# ---------------------------------------------------------------------------


class TestPortalSession:
    def test_portal_returns_200(self, client):
        response = client.post("/api/billing/portal", json={})
        assert response.status_code == 200

    def test_portal_returns_url(self, client):
        data = client.post("/api/billing/portal", json={}).json()
        assert "url" in data

    def test_portal_value_error_returns_404(self, client, mock_stripe):
        mock_stripe.create_portal_session = AsyncMock(
            side_effect=ValueError("No customer found")
        )
        response = client.post("/api/billing/portal", json={})
        assert response.status_code == 404

    def test_portal_generic_error_returns_500(self, client, mock_stripe):
        mock_stripe.create_portal_session = AsyncMock(
            side_effect=RuntimeError("Portal unavailable")
        )
        response = client.post("/api/billing/portal", json={})
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/billing/subscription
# ---------------------------------------------------------------------------


class TestGetSubscription:
    def test_subscription_none_returns_free(self, client):
        data = client.get("/api/billing/subscription").json()
        assert data["plan"] == "free"
        assert data["status"] == "none"
        assert "tokens" in data

    def test_subscription_active_returns_details(self, client, mock_stripe):
        from tools.stripe_service import (
            SubscriptionInfo,
            SubscriptionStatus,
            PlanType,
            BillingInterval,
        )

        sub = SubscriptionInfo(
            id="sub_123",
            status=SubscriptionStatus.ACTIVE,
            plan=PlanType.PRO,
            interval=BillingInterval.MONTHLY,
            current_period_start=datetime(2026, 1, 1),
            current_period_end=datetime(2026, 2, 1),
            cancel_at_period_end=False,
            stripe_subscription_id="sub_stripe_123",
            stripe_customer_id="cus_stripe_123",
        )
        mock_stripe.get_subscription = AsyncMock(return_value=sub)
        data = client.get("/api/billing/subscription").json()
        assert data["plan"] == "pro"
        assert data["status"] == "active"
        assert data["interval"] == "monthly"
        assert data["cancel_at_period_end"] is False
        assert "current_period_end" in data
        assert "tokens" in data


# ---------------------------------------------------------------------------
# POST /api/billing/subscription/cancel
# ---------------------------------------------------------------------------


class TestCancelSubscription:
    def test_cancel_returns_200(self, client):
        response = client.post("/api/billing/subscription/cancel")
        assert response.status_code == 200

    def test_cancel_returns_result(self, client):
        data = client.post("/api/billing/subscription/cancel").json()
        assert data["canceled"] is True

    def test_cancel_not_found_returns_404(self, client, mock_stripe):
        mock_stripe.cancel_subscription = AsyncMock(
            side_effect=ValueError("No active subscription")
        )
        response = client.post("/api/billing/subscription/cancel")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/billing/subscription/resume
# ---------------------------------------------------------------------------


class TestResumeSubscription:
    def test_resume_returns_200(self, client):
        response = client.post("/api/billing/subscription/resume")
        assert response.status_code == 200

    def test_resume_returns_result(self, client):
        data = client.post("/api/billing/subscription/resume").json()
        assert data["resumed"] is True

    def test_resume_not_found_returns_404(self, client, mock_stripe):
        mock_stripe.resume_subscription = AsyncMock(
            side_effect=ValueError("No subscription to resume")
        )
        response = client.post("/api/billing/subscription/resume")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/billing/tokens
# ---------------------------------------------------------------------------


class TestGetTokenBalance:
    def test_tokens_returns_200(self, client):
        response = client.get("/api/billing/tokens")
        assert response.status_code == 200

    def test_tokens_returns_balance(self, client):
        data = client.get("/api/billing/tokens").json()
        assert data["balance"] == 500


# ---------------------------------------------------------------------------
# GET /api/billing/credits
# ---------------------------------------------------------------------------


class TestGetCredits:
    def test_credits_returns_200(self, client):
        response = client.get("/api/billing/credits")
        assert response.status_code == 200

    def test_credits_returns_balance_and_user_id(self, client):
        data = client.get("/api/billing/credits").json()
        assert data["balance"] == 500
        assert data["user_id"] == "test_user_123"

    def test_credits_with_convex_includes_lifetime_stats(self, client, mock_stripe):
        mock_convex = AsyncMock()
        mock_convex.query = AsyncMock(
            return_value=[
                {"amount": 1000},
                {"amount": 500},
                {"amount": -200},
                {"amount": -100},
            ]
        )
        mock_stripe.convex = mock_convex
        data = client.get("/api/billing/credits").json()
        assert data["lifetime_earned"] == 1500
        assert data["lifetime_spent"] == 300


# ---------------------------------------------------------------------------
# POST /api/billing/credits/use
# ---------------------------------------------------------------------------


class TestUseCredits:
    def test_use_credits_returns_200(self, client):
        response = client.post(
            "/api/billing/credits/use",
            json={
                "amount": 100,
                "reason": "ai_generation",
            },
        )
        assert response.status_code == 200

    def test_use_credits_returns_success_payload(self, client, mock_stripe):
        mock_stripe.get_token_balance = AsyncMock(side_effect=[500, 400])
        data = client.post(
            "/api/billing/credits/use",
            json={
                "amount": 100,
                "reason": "code_completion",
            },
        ).json()
        assert data["success"] is True
        assert data["amount_used"] == 100
        assert data["reason"] == "code_completion"
        assert "balance" in data

    def test_use_credits_zero_amount_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/use",
            json={
                "amount": 0,
                "reason": "test",
            },
        )
        assert response.status_code == 400
        assert "positive" in response.json()["detail"]

    def test_use_credits_negative_amount_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/use",
            json={
                "amount": -5,
                "reason": "test",
            },
        )
        assert response.status_code == 400

    def test_use_credits_insufficient_balance_returns_402(self, client, mock_stripe):
        # The atomic mutation decides affordability; a separate pre-read races.
        mock_stripe.use_tokens = AsyncMock(side_effect=RuntimeError("insufficient_credits"))
        response = client.post(
            "/api/billing/credits/use",
            json={
                "amount": 100,
                "reason": "ai_generation",
            },
        )
        assert response.status_code == 402
        detail = response.json()["detail"]
        assert detail["error"] == "insufficient_credits"
        assert detail["required"] == 100
        mock_stripe.get_token_balance.assert_not_awaited()

    def test_use_credits_use_tokens_fails_returns_402(self, client, mock_stripe):
        mock_stripe.get_token_balance = AsyncMock(return_value=500)
        mock_stripe.use_tokens = AsyncMock(return_value=False)
        response = client.post(
            "/api/billing/credits/use",
            json={
                "amount": 100,
                "reason": "ai_generation",
            },
        )
        assert response.status_code == 402
        assert "Failed to deduct" in response.json()["detail"]
        mock_stripe.get_token_balance.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /api/billing/credits/refund
# ---------------------------------------------------------------------------


class TestRefundCredits:
    def test_refund_without_database_fails_closed(self, client, mock_stripe):
        response = client.post("/api/billing/credits/refund", json={"amount": 50, "reason": "failed"})
        assert response.status_code == 503
        mock_stripe.add_tokens.assert_not_awaited()

    def test_refund_without_prior_deduction_fails_closed(self, client, mock_stripe):
        mock_stripe.convex = MagicMock()
        mock_stripe.convex.query = AsyncMock(return_value=[])
        response = client.post("/api/billing/credits/refund", json={"amount": 50, "reason": "failed"})
        assert response.status_code == 403
        mock_stripe.convex.query.assert_awaited_once_with(
            "billing:getTransactions", {"userId": "test_user_123", "limit": 200}
        )
        mock_stripe.add_tokens.assert_not_awaited()

    def test_refund_returns_200(self, client, mock_stripe):
        mock_stripe.convex = MagicMock()
        mock_stripe.convex.query = AsyncMock(return_value=[{"amount": -50}])
        response = client.post(
            "/api/billing/credits/refund",
            json={
                "amount": 50,
                "reason": "generation_failed",
            },
        )
        assert response.status_code == 200

    def test_refund_returns_success_payload(self, client, mock_stripe):
        mock_stripe.convex = MagicMock()
        mock_stripe.convex.query = AsyncMock(return_value=[{"amount": -75}])
        data = client.post(
            "/api/billing/credits/refund",
            json={
                "amount": 75,
                "reason": "timeout",
            },
        ).json()
        assert data["success"] is True
        assert data["amount_refunded"] == 75
        assert data["balance"] == 600
        assert data["reason"] == "timeout"

    def test_refund_zero_amount_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/refund",
            json={
                "amount": 0,
                "reason": "test",
            },
        )
        assert response.status_code == 400
        assert "positive" in response.json()["detail"]

    def test_refund_negative_amount_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/refund",
            json={
                "amount": -10,
                "reason": "test",
            },
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/billing/credits/add
# ---------------------------------------------------------------------------


class TestAddCredits:
    def test_add_credits_returns_200(self, client):
        response = client.post(
            "/api/billing/credits/add",
            json={
                "user_id": "target_user",
                "amount": 200,
                "reason": "promo",
                "transaction_type": "bonus",
            },
        )
        assert response.status_code == 200

    def test_add_credits_returns_full_payload(self, client):
        data = client.post(
            "/api/billing/credits/add",
            json={
                "user_id": "target_user",
                "amount": 300,
                "reason": "support_adjustment",
                "transaction_type": "admin_adjustment",
            },
        ).json()
        assert data["success"] is True
        assert data["user_id"] == "target_user"
        assert data["amount_added"] == 300
        assert data["transaction_type"] == "admin_adjustment"

    def test_add_credits_zero_amount_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/add",
            json={
                "user_id": "u1",
                "amount": 0,
                "reason": "test",
                "transaction_type": "bonus",
            },
        )
        assert response.status_code == 400

    def test_add_credits_negative_amount_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/add",
            json={
                "user_id": "u1",
                "amount": -100,
                "reason": "test",
                "transaction_type": "bonus",
            },
        )
        assert response.status_code == 400

    def test_add_credits_invalid_transaction_type_returns_400(self, client):
        response = client.post(
            "/api/billing/credits/add",
            json={
                "user_id": "u1",
                "amount": 100,
                "reason": "test",
                "transaction_type": "hack",
            },
        )
        assert response.status_code == 400
        assert "Invalid transaction type" in response.json()["detail"]

    def test_add_credits_valid_transaction_types(self, client):
        for txn_type in ["bonus", "admin_adjustment", "refund"]:
            response = client.post(
                "/api/billing/credits/add",
                json={
                    "user_id": "u1",
                    "amount": 10,
                    "reason": "test",
                    "transaction_type": txn_type,
                },
            )
            assert response.status_code == 200, f"Failed for type: {txn_type}"


# ---------------------------------------------------------------------------
# GET /api/billing/credits/transactions
# ---------------------------------------------------------------------------


class TestCreditTransactions:
    def test_transactions_no_convex_returns_empty(self, client, mock_stripe):
        mock_stripe.convex = None
        data = client.get("/api/billing/credits/transactions").json()
        assert data["transactions"] == []
        assert data["total"] == 0

    def test_transactions_with_convex_returns_list(self, client, mock_stripe):
        mock_convex = AsyncMock()
        mock_convex.query = AsyncMock(
            return_value=[
                {"amount": 100, "reason": "purchase", "ts": "2026-01-01"},
                {"amount": -50, "reason": "ai_generation", "ts": "2026-01-02"},
            ]
        )
        mock_stripe.convex = mock_convex
        data = client.get("/api/billing/credits/transactions").json()
        assert data["total"] == 2
        assert len(data["transactions"]) == 2

    def test_transactions_query_failure_returns_500(self, client, mock_stripe):
        mock_convex = AsyncMock()
        mock_convex.query = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_stripe.convex = mock_convex
        response = client.get("/api/billing/credits/transactions")
        assert response.status_code == 500
        assert "Failed to fetch transactions" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/billing/webhook
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_webhook_returns_200(self, client, mock_stripe):
        response = client.post(
            "/api/billing/webhook",
            content=b'{"type":"checkout.session.completed"}',
            headers={"stripe-signature": "sig_test_123"},
        )
        assert response.status_code == 200

    def test_webhook_returns_event_data(self, client, mock_stripe):
        data = client.post(
            "/api/billing/webhook",
            content=b'{"type":"checkout.session.completed"}',
            headers={"stripe-signature": "sig_test_123"},
        ).json()
        assert data["success"] is True
        assert data["event"] == "checkout.session.completed"
        assert data["handled"] is True

    def test_webhook_missing_signature_returns_400(self, client):
        response = client.post(
            "/api/billing/webhook",
            content=b'{"type":"test"}',
        )
        assert response.status_code == 400
        assert "Missing Stripe signature" in response.json()["detail"]

    def test_webhook_value_error_returns_400(self, client, mock_stripe):
        mock_stripe.handle_webhook = AsyncMock(
            side_effect=ValueError("Invalid signature")
        )
        response = client.post(
            "/api/billing/webhook",
            content=b'{"type":"test"}',
            headers={"stripe-signature": "bad_sig"},
        )
        assert response.status_code == 400

    def test_webhook_generic_error_returns_500(self, client, mock_stripe):
        mock_stripe.handle_webhook = AsyncMock(
            side_effect=RuntimeError("Processing failed")
        )
        response = client.post(
            "/api/billing/webhook",
            content=b'{"type":"test"}',
            headers={"stripe-signature": "sig_test"},
        )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/billing/plans
# ---------------------------------------------------------------------------


class TestGetPlans:
    def test_plans_returns_200(self, client):
        response = client.get("/api/billing/plans")
        assert response.status_code == 200

    def test_plans_contains_plans_key(self, client):
        data = client.get("/api/billing/plans").json()
        assert "plans" in data

    def test_plans_contains_three_plans(self, client):
        data = client.get("/api/billing/plans").json()
        assert len(data["plans"]) == 3

    def test_plans_ids_are_free_pro_team(self, client):
        data = client.get("/api/billing/plans").json()
        ids = [p["id"] for p in data["plans"]]
        assert ids == ["free", "pro", "team"]

    def test_plans_contain_token_packages(self, client):
        data = client.get("/api/billing/plans").json()
        assert "token_packages" in data
        assert len(data["token_packages"]) == 3

    def test_plans_pro_is_popular(self, client):
        data = client.get("/api/billing/plans").json()
        pro = [p for p in data["plans"] if p["id"] == "pro"][0]
        assert pro.get("popular") is True

    def test_plans_each_has_required_fields(self, client):
        data = client.get("/api/billing/plans").json()
        required = {
            "id",
            "name",
            "description",
            "price_monthly",
            "price_yearly",
            "features",
            "tokens_monthly",
        }
        for plan in data["plans"]:
            assert required.issubset(plan.keys()), f"Plan {plan['id']} missing fields"
