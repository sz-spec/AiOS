'use client';

import { useState } from 'react';
import { CreditCard, Plus, Trash2, DollarSign } from 'lucide-react';

interface Product {
  id: string;
  name: string;
  price: string;
  description: string;
  recurring: boolean;
  interval: 'month' | 'year';
}

interface PaymentSetupWizardProps {
  projectId: string;
  onPaymentsConfigured?: () => void;
}

export function PaymentSetupWizard({ projectId, onPaymentsConfigured }: PaymentSetupWizardProps) {
  const [products, setProducts] = useState<Product[]>([]);
  const [stripeConnected, setStripeConnected] = useState(false);
  const [isApplying, setIsApplying] = useState(false);

  const addProduct = () => {
    setProducts((prev) => [...prev, {
      id: `prod-${Date.now()}`,
      name: '',
      price: '',
      description: '',
      recurring: false,
      interval: 'month',
    }]);
  };

  const updateProduct = (id: string, updates: Partial<Product>) => {
    setProducts((prev) => prev.map((p) => p.id === id ? { ...p, ...updates } : p));
  };

  const removeProduct = (id: string) => {
    setProducts((prev) => prev.filter((p) => p.id !== id));
  };

  const applyPayments = async () => {
    setIsApplying(true);
    try {
      await fetch('/api/v1/payments/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          products,
          stripe_connected: stripeConnected,
        }),
      });
      onPaymentsConfigured?.();
    } catch {
      // ignore
    }
    setIsApplying(false);
  };

  return (
    <div style={{ padding: '24px', maxWidth: '560px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
        <CreditCard size={24} style={{ color: 'var(--accent)' }} />
        <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Add Payments</h3>
      </div>

      {/* Stripe Connection */}
      <div style={{
        padding: '16px 20px',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-light)',
        backgroundColor: 'var(--bg-secondary)',
        marginBottom: '20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: '14px' }}>Stripe</div>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
            {stripeConnected ? 'Connected' : 'Connect to accept payments'}
          </div>
        </div>
        <button
          onClick={() => setStripeConnected(!stripeConnected)}
          style={{
            padding: '8px 16px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: stripeConnected ? 'var(--success)' : '#635bff',
            color: 'white',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          {stripeConnected ? 'Connected' : 'Connect Stripe'}
        </button>
      </div>

      {/* Products */}
      <div style={{ marginBottom: '16px' }}>
        <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '12px' }}>
          Products & Pricing
        </div>

        {products.map((product) => (
          <div key={product.id} style={{
            padding: '16px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-light)',
            marginBottom: '12px',
          }}>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
              <input
                placeholder="Product name"
                value={product.name}
                onChange={(e) => updateProduct(product.id, { name: e.target.value })}
                style={{
                  flex: 1, padding: '8px 12px', borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-light)', fontSize: '14px',
                }}
              />
              <div style={{ position: 'relative' }}>
                <DollarSign size={14} style={{ position: 'absolute', left: '8px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                <input
                  placeholder="29"
                  value={product.price}
                  onChange={(e) => updateProduct(product.id, { price: e.target.value })}
                  style={{
                    width: '100px', padding: '8px 12px 8px 26px', borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)', fontSize: '14px',
                  }}
                />
              </div>
              <button
                onClick={() => removeProduct(product.id)}
                style={{ padding: '8px', color: 'var(--text-tertiary)', backgroundColor: 'transparent' }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            <input
              placeholder="Short description"
              value={product.description}
              onChange={(e) => updateProduct(product.id, { description: e.target.value })}
              style={{
                width: '100%', padding: '8px 12px', borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-light)', fontSize: '13px', marginBottom: '8px',
              }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={product.recurring}
                  onChange={(e) => updateProduct(product.id, { recurring: e.target.checked })}
                  style={{ accentColor: 'var(--accent)' }}
                />
                Recurring
              </label>
              {product.recurring && (
                <select
                  value={product.interval}
                  onChange={(e) => updateProduct(product.id, { interval: e.target.value as 'month' | 'year' })}
                  style={{
                    padding: '4px 8px', borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)', fontSize: '12px',
                  }}
                >
                  <option value="month">Monthly</option>
                  <option value="year">Yearly</option>
                </select>
              )}
            </div>
          </div>
        ))}

        <button
          onClick={addProduct}
          style={{
            width: '100%', padding: '12px',
            borderRadius: 'var(--radius-sm)',
            border: '1px dashed var(--border-medium)',
            fontSize: '13px',
            fontWeight: 500,
            color: 'var(--text-secondary)',
            backgroundColor: 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
          }}
        >
          <Plus size={14} />
          Add Product
        </button>
      </div>

      {/* Generate */}
      {products.length > 0 && (
        <button
          onClick={applyPayments}
          disabled={isApplying || !stripeConnected}
          style={{
            width: '100%',
            padding: '12px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: stripeConnected ? 'var(--accent)' : 'var(--bg-tertiary)',
            color: stripeConnected ? 'white' : 'var(--text-tertiary)',
            fontSize: '14px',
            fontWeight: 500,
            cursor: stripeConnected ? 'pointer' : 'not-allowed',
          }}
        >
          {isApplying ? 'Generating...' : 'Generate Payment Pages'}
        </button>
      )}
    </div>
  );
}
