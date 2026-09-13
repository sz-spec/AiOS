'use client';

import { useState, useEffect } from 'react';

export default function AnalyticsPage() {
  const [period, setPeriod] = useState<'7d' | '30d' | '90d'>('30d');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Simulate loading
    setTimeout(() => setLoading(false), 500);
  }, []);

  const metrics = [
    { label: 'Total Users', value: '1,234', change: '+12%', positive: true },
    { label: 'Active Sessions', value: '567', change: '+8%', positive: true },
    { label: 'AI Requests', value: '45,678', change: '+23%', positive: true },
    { label: 'Avg Response Time', value: '1.2s', change: '-15%', positive: true },
  ];

  const topEntities = [
    { name: 'Contacts', records: 1250, change: '+5%' },
    { name: 'Deals', records: 890, change: '+12%' },
    { name: 'Tasks', records: 2340, change: '+8%' },
    { name: 'Appointments', records: 456, change: '+3%' },
  ];

  const recentActivity = [
    { action: 'New user signup', time: '2 minutes ago', icon: '👤' },
    { action: 'Workflow executed', time: '5 minutes ago', icon: '⚡' },
    { action: 'Deal closed', time: '12 minutes ago', icon: '💰' },
    { action: 'Contact created', time: '18 minutes ago', icon: '📇' },
    { action: 'AI request completed', time: '25 minutes ago', icon: '🤖' },
  ];

  return (
    <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '4px' }}>Analytics</h1>
          <p style={{ color: 'var(--text-secondary)' }}>Track your business metrics and AI usage</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          {(['7d', '30d', '90d'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              style={{
                padding: '8px 16px',
                backgroundColor: period === p ? 'var(--accent-primary)' : 'var(--bg-secondary)',
                color: period === p ? 'white' : 'var(--text-primary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
                fontWeight: 500,
              }}
            >
              {p === '7d' ? '7 Days' : p === '30d' ? '30 Days' : '90 Days'}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '48px', color: 'var(--text-secondary)' }}>Loading analytics...</div>
      ) : (
        <>
          {/* Key Metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', marginBottom: '24px' }}>
            {metrics.map((metric) => (
              <div
                key={metric.label}
                style={{
                  padding: '20px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-light)',
                }}
              >
                <div style={{ color: 'var(--text-secondary)', fontSize: '14px', marginBottom: '8px' }}>
                  {metric.label}
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px' }}>
                  <span style={{ fontSize: '28px', fontWeight: 600 }}>{metric.value}</span>
                  <span style={{ color: metric.positive ? '#22c55e' : '#ef4444', fontSize: '14px', fontWeight: 500 }}>
                    {metric.change}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Charts and Tables */}
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '24px' }}>
            {/* Chart Placeholder */}
            <div
              style={{
                padding: '20px',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
              }}
            >
              <h3 style={{ marginBottom: '16px', fontWeight: 600 }}>AI Usage Over Time</h3>
              <div
                style={{
                  height: '300px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--text-secondary)',
                }}
              >
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '48px', marginBottom: '8px' }}>📊</div>
                  <div>Chart visualization</div>
                  <div style={{ fontSize: '12px' }}>Connect PostHog for detailed analytics</div>
                </div>
              </div>
            </div>

            {/* Recent Activity */}
            <div
              style={{
                padding: '20px',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
              }}
            >
              <h3 style={{ marginBottom: '16px', fontWeight: 600 }}>Recent Activity</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {recentActivity.map((activity, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{ fontSize: '20px' }}>{activity.icon}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '14px' }}>{activity.action}</div>
                      <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{activity.time}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Entity Stats */}
          <div
            style={{
              marginTop: '24px',
              padding: '20px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-light)',
            }}
          >
            <h3 style={{ marginBottom: '16px', fontWeight: 600 }}>Records by Entity</h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
              {topEntities.map((entity) => (
                <div
                  key={entity.name}
                  style={{
                    padding: '16px',
                    backgroundColor: 'var(--bg-primary)',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  <div style={{ fontWeight: 500, marginBottom: '4px' }}>{entity.name}</div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                    <span style={{ fontSize: '20px', fontWeight: 600 }}>{entity.records.toLocaleString()}</span>
                    <span style={{ color: '#22c55e', fontSize: '12px' }}>{entity.change}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
