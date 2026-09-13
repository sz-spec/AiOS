"""Tests for payment_builder — Stripe payment code generation."""

from services.payment_builder import generate_payment_code


def _sample_products():
    return [
        {
            "name": "Pro Plan",
            "price": "29",
            "description": "Full access",
            "recurring": True,
            "interval": "month",
        }
    ]


class TestPaymentBuilder:
    def test_generate_basic(self):
        files = generate_payment_code(_sample_products())
        assert len(files) == 3

    def test_pricing_page_has_products(self):
        files = generate_payment_code(_sample_products())
        pricing = [
            v for k, v in files.items() if "Pricing" in k or "pricing" in k.lower()
        ]
        assert len(pricing) > 0
        assert "Pro Plan" in pricing[0] or "pro_plan" in pricing[0]

    def test_stripe_lib(self):
        files = generate_payment_code(_sample_products())
        assert any("stripe" in k.lower() for k in files.keys())
        stripe_file = [v for k, v in files.items() if "stripe" in k.lower()][0]
        assert "loadStripe" in stripe_file or "Stripe" in stripe_file

    def test_checkout_api(self):
        files = generate_payment_code(_sample_products())
        assert any("checkout" in k.lower() for k in files.keys())

    def test_recurring_product(self):
        files = generate_payment_code(_sample_products())
        pricing = [
            v for k, v in files.items() if "Pricing" in k or "pricing" in k.lower()
        ]
        assert len(pricing) > 0
        content = pricing[0]
        assert (
            "month" in content.lower()
            or "recurring" in content.lower()
            or "/mo" in content
        )
