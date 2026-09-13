"""
Payment Builder Service
=======================
Generates Stripe payment integration code for user-created apps.
"""

from typing import Dict, List


def generate_payment_code(products: List[Dict]) -> Dict[str, str]:
    """
    Generate payment-related code for a project.

    Args:
        products: List of product definitions with name, price, recurring, interval

    Returns:
        Dict of file path -> file content
    """
    files = {}

    files["src/lib/stripe.ts"] = _generate_stripe_lib()
    files["src/pages/Pricing.tsx"] = _generate_pricing_page(products)
    files["src/api/checkout.ts"] = _generate_checkout_api(products)

    return files


def _generate_stripe_lib() -> str:
    return """import { loadStripe } from '@stripe/stripe-js';

const stripePromise = loadStripe(import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY);

export async function createCheckoutSession(priceId: string) {
  const response = await fetch('/api/checkout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ priceId }),
  });

  const { sessionId } = await response.json();
  const stripe = await stripePromise;
  await stripe?.redirectToCheckout({ sessionId });
}

export { stripePromise };
"""


def _generate_pricing_page(products: List[Dict]) -> str:
    product_cards = ""
    for p in products:
        price_display = f"${p.get('price', '0')}"
        if p.get("recurring"):
            price_display += f"/{p.get('interval', 'month')}"
        product_cards += f"""
      <div style={{ border: '1px solid #eee', borderRadius: '12px', padding: '24px', textAlign: 'center' }}>
        <h3>{p.get('name', 'Product')}</h3>
        <p style={{ fontSize: '32px', fontWeight: 700 }}>{price_display}</p>
        <p>{p.get('description', '')}</p>
        <button onClick={{() => createCheckoutSession('{p.get('name', '').lower().replace(' ', '_')}')}}
          style={{ padding: '12px 32px', borderRadius: '8px', background: '#635bff', color: 'white', border: 'none', cursor: 'pointer' }}>
          Get Started
        </button>
      </div>"""

    return f"""import React from 'react';
import {{ createCheckoutSession }} from '../lib/stripe';

export default function Pricing() {{
  return (
    <div style={{ maxWidth: '960px', margin: '0 auto', padding: '48px 24px' }}>
      <h1 style={{ textAlign: 'center' }}>Pricing</h1>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '24px', marginTop: '32px' }}>
{product_cards}
      </div>
    </div>
  );
}}
"""


def _generate_checkout_api(products: List[Dict]) -> str:
    return """import Stripe from 'stripe';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(request: Request) {
  const { priceId } = await request.json();

  const session = await stripe.checkout.sessions.create({
    payment_method_types: ['card'],
    line_items: [{ price: priceId, quantity: 1 }],
    mode: 'payment',
    success_url: `${process.env.APP_URL}/success`,
    cancel_url: `${process.env.APP_URL}/pricing`,
  });

  return Response.json({ sessionId: session.id });
}
"""
