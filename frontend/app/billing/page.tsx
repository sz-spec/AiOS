'use client';

import { useState, useEffect } from 'react';
import { useLocality } from '@/context/LocalityContext';

interface Plan {
  id: string;
  name: string;
  price: number;
  yearlyPrice?: number;
  description: string;
  features: string[];
  highlighted?: boolean;
}

interface Subscription {
  plan: string;
  status: string;
  tokens: number;
  current_period_end?: string;
}

export default function BillingPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [billingInterval, setBillingInterval] = useState<'monthly' | 'yearly'>('monthly');
  const [loading, setLoading] = useState(true);
  // W6.3 — gate Stripe-bound flows when the backend is local-first.
  // We do not allow subscribe / token-purchase actions to fire under
  // sovereign profiles since (a) there is no path to Stripe by design
  // and (b) the user's expectation is "no cloud egress at all". The
  // page still renders for browsing parity with the cloud experience.
  const { isLocalFirst } = useLocality();

  useEffect(() => {
    fetchBillingData();
  }, []);

  const fetchBillingData = async () => {
    try {
      // Fetch plans
      const plansRes = await fetch('/api/billing/plans');
      if (plansRes.ok) {
        const data = await plansRes.json();
        setPlans(data.plans || []);
      }

      // Fetch subscription
      const subRes = await fetch('/api/billing/subscription', {
        headers: { Authorization: 'Bearer demo' },
      });
      if (subRes.ok) {
        const data = await subRes.json();
        setSubscription(data);
      }
    } catch (error) {
      console.error('Failed to fetch billing data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubscribe = async (planId: string) => {
    try {
      const res = await fetch('/api/billing/checkout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer demo',
        },
        body: JSON.stringify({
          plan: planId,
          interval: billingInterval,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.url) {
          window.location.href = data.url;
        }
      }
    } catch (error) {
      console.error('Failed to create checkout:', error);
    }
  };

  const getPrice = (plan: Plan) => {
    if (billingInterval === 'yearly' && plan.yearlyPrice) {
      return Math.round(plan.yearlyPrice / 12);
    }
    return plan.price;
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: '32px', textAlign: 'center' }}>
        <h1 style={{ fontSize: '28px', fontWeight: 600, marginBottom: '8px' }}>Plans & Billing</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Choose the plan that works best for you</p>
      </div>

      {/* W6.3 — sovereign-mode banner. Surfaces WHY the purchase buttons
          are disabled so the user understands the gate isn't a bug. */}
      {isLocalFirst && (
        <div
          role="status"
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
            padding: '14px 18px',
            marginBottom: '24px',
            background: 'rgba(16, 185, 129, 0.06)',
            border: '1px solid rgba(110, 231, 183, 0.28)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
          }}
        >
          <span style={{ fontSize: '18px', lineHeight: 1 }}>🛡️</span>
          <div>
            <div style={{ fontWeight: 600, marginBottom: '4px' }}>
              Sovereign mode active — billing is disabled.
            </div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '13px', lineHeight: 1.5 }}>
              Plan upgrades and token packages route through Stripe in the
              cloud. Under <code>VOS3_LOCALITY_PREFERENCE=local-first</code> the
              backend intentionally refuses cloud egress. Switch to the
              cloud / enterprise profile to manage your subscription.
            </div>
          </div>
        </div>
      )}

      {/* Current Subscription */}
      {subscription && subscription.plan !== 'free' && (
        <div
          style={{
            padding: '20px',
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-light)',
            marginBottom: '32px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 600, marginBottom: '4px' }}>Current Plan: {subscription.plan}</div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
                Status: {subscription.status} | Tokens: {subscription.tokens?.toLocaleString() || 0}
              </div>
            </div>
            <button
              style={{
                padding: '8px 16px',
                backgroundColor: 'var(--bg-primary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
              }}
            >
              Manage Subscription
            </button>
          </div>
        </div>
      )}

      {/* Billing Interval Toggle */}
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '32px' }}>
        <div
          style={{
            display: 'inline-flex',
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-md)',
            padding: '4px',
          }}
        >
          <button
            onClick={() => setBillingInterval('monthly')}
            style={{
              padding: '8px 20px',
              backgroundColor: billingInterval === 'monthly' ? 'var(--bg-primary)' : 'transparent',
              color: billingInterval === 'monthly' ? 'var(--text-primary)' : 'var(--text-secondary)',
              borderRadius: 'var(--radius-sm)',
              fontWeight: 500,
              border: 'none',
            }}
          >
            Monthly
          </button>
          <button
            onClick={() => setBillingInterval('yearly')}
            style={{
              padding: '8px 20px',
              backgroundColor: billingInterval === 'yearly' ? 'var(--bg-primary)' : 'transparent',
              color: billingInterval === 'yearly' ? 'var(--text-primary)' : 'var(--text-secondary)',
              borderRadius: 'var(--radius-sm)',
              fontWeight: 500,
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            Yearly
            <span
              style={{
                backgroundColor: '#22c55e',
                color: 'white',
                padding: '2px 6px',
                borderRadius: 'var(--radius-sm)',
                fontSize: '11px',
                fontWeight: 600,
              }}
            >
              Save 20%
            </span>
          </button>
        </div>
      </div>

      {/* Plans Grid */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '48px', color: 'var(--text-secondary)' }}>Loading plans...</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '24px' }}>
          {plans.map((plan) => (
            <div
              key={plan.id}
              style={{
                padding: '24px',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-lg)',
                border: plan.highlighted ? '2px solid var(--accent-primary)' : '1px solid var(--border-light)',
                position: 'relative',
              }}
            >
              {plan.highlighted && (
                <div
                  style={{
                    position: 'absolute',
                    top: '-12px',
                    left: '50%',
                    transform: 'translateX(-50%)',
                    backgroundColor: 'var(--accent-primary)',
                    color: 'white',
                    padding: '4px 12px',
                    borderRadius: 'var(--radius-full)',
                    fontSize: '12px',
                    fontWeight: 600,
                  }}
                >
                  Most Popular
                </div>
              )}

              <div style={{ marginBottom: '16px' }}>
                <h3 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '4px' }}>{plan.name}</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>{plan.description}</p>
              </div>

              <div style={{ marginBottom: '24px' }}>
                <span style={{ fontSize: '36px', fontWeight: 700 }}>${getPrice(plan)}</span>
                <span style={{ color: 'var(--text-secondary)' }}>/month</span>
                {billingInterval === 'yearly' && plan.yearlyPrice && (
                  <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                    Billed ${plan.yearlyPrice}/year
                  </div>
                )}
              </div>

              <button
                onClick={() => handleSubscribe(plan.id)}
                disabled={subscription?.plan === plan.id || isLocalFirst}
                title={
                  isLocalFirst
                    ? 'Disabled under sovereign mode — billing requires cloud connectivity.'
                    : undefined
                }
                style={{
                  width: '100%',
                  padding: '12px',
                  backgroundColor:
                    isLocalFirst
                      ? 'var(--bg-primary)'
                      : subscription?.plan === plan.id
                        ? 'var(--bg-primary)'
                        : plan.highlighted
                          ? 'var(--accent-primary)'
                          : 'var(--bg-primary)',
                  color:
                    isLocalFirst || subscription?.plan === plan.id
                      ? 'var(--text-tertiary)'
                      : plan.highlighted
                        ? 'white'
                        : 'var(--text-primary)',
                  border: plan.highlighted && !isLocalFirst ? 'none' : '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-md)',
                  fontWeight: 600,
                  marginBottom: '24px',
                  cursor:
                    subscription?.plan === plan.id || isLocalFirst
                      ? 'not-allowed'
                      : 'pointer',
                  opacity: isLocalFirst ? 0.55 : 1,
                }}
              >
                {subscription?.plan === plan.id
                  ? 'Current Plan'
                  : isLocalFirst
                    ? 'Sovereign mode'
                    : plan.price === 0
                      ? 'Get Started'
                      : 'Subscribe'}
              </button>

              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {plan.features.map((feature, i) => (
                  <li
                    key={i}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '8px',
                      marginBottom: '8px',
                      fontSize: '14px',
                    }}
                  >
                    <span style={{ color: '#22c55e' }}>&#x2713;</span>
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {/* Token Packages */}
      <div style={{ marginTop: '48px' }}>
        <h2 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '16px', textAlign: 'center' }}>
          Need More AI Tokens?
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px', maxWidth: '800px', margin: '0 auto' }}>
          {[
            { tokens: 1000, price: 10 },
            { tokens: 5000, price: 40 },
            { tokens: 10000, price: 70 },
          ].map((pkg) => (
            <div
              key={pkg.tokens}
              style={{
                padding: '20px',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
                textAlign: 'center',
              }}
            >
              <div style={{ fontSize: '24px', fontWeight: 700 }}>{pkg.tokens.toLocaleString()}</div>
              <div style={{ color: 'var(--text-secondary)', marginBottom: '12px' }}>tokens</div>
              <button
                disabled={isLocalFirst}
                title={
                  isLocalFirst
                    ? 'Disabled under sovereign mode — token purchases route through Stripe.'
                    : undefined
                }
                style={{
                  padding: '8px 24px',
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-md)',
                  color: isLocalFirst ? 'var(--text-tertiary)' : 'var(--text-primary)',
                  fontWeight: 500,
                  cursor: isLocalFirst ? 'not-allowed' : 'pointer',
                  opacity: isLocalFirst ? 0.55 : 1,
                }}
              >
                {isLocalFirst ? '—' : `$${pkg.price}`}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
