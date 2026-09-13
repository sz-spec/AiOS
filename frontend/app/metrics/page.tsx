'use client';

import { useState, useEffect } from 'react';
import { VosActivityPanel } from '@/components/admin/VosActivityPanel';
import { useConfirm } from '@/hooks/useConfirm';

interface MetricsSummary {
  total_requests: number;
  total_cost: number;
  total_tokens_in: number;
  total_tokens_out: number;
  avg_response_time_ms: number;
  avg_complexity: number;
  error_rate: number;
  fallback_rate: number;
}

interface RecentRequest {
  timestamp: string;
  role: string;
  complexity: number;
  model: string;
  response_time_ms: number;
  cost: number;
  success: boolean;
}

interface MetricsData {
  summary: MetricsSummary;
  by_model: Record<string, number>;
  by_role: Record<string, number>;
  last_hour: { requests: number; cost: number };
  recent_requests: RecentRequest[];
}

interface ModelsInfo {
  models: Record<string, { name: string; provider: string; model_id: string; enabled: boolean; priority: number }>;
  default: string;
  complexity_threshold: number;
  role_mappings: Record<string, string>;
  enabled_models: string[];
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

export default function MetricsPage() {
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();

  const fetchMetrics = async () => {
    try {
      const [metricsRes, modelsRes] = await Promise.all([
        fetch(`${API_URL}/api/metrics/`),
        fetch(`${API_URL}/api/metrics/models`),
      ]);

      if (metricsRes.ok) {
        setMetrics(await metricsRes.json());
      }
      if (modelsRes.ok) {
        setModels(await modelsRes.json());
      }
      setError(null);
    } catch (err) {
      setError('Failed to fetch metrics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();

    if (autoRefresh) {
      const interval = setInterval(fetchMetrics, 5000);
      return () => clearInterval(interval);
    }
  }, [autoRefresh]);

  const handleReset = async () => {
    const ok = await confirm({
      title: 'Reset all metrics?',
      description: 'All accumulated request, cost, and error metrics will be cleared. This cannot be undone.',
      confirmLabel: 'Reset',
      destructive: true,
    });
    if (ok) {
      await fetch(`${API_URL}/api/metrics/reset`, { method: 'POST' });
      fetchMetrics();
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading metrics...
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>
          Router Metrics
        </h1>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          <button
            onClick={fetchMetrics}
            style={{
              padding: '8px 16px',
              backgroundColor: 'var(--bg-hover)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
            }}
          >
            Refresh
          </button>
          <button
            onClick={handleReset}
            style={{
              padding: '8px 16px',
              backgroundColor: '#fee2e2',
              border: '1px solid #fecaca',
              borderRadius: 'var(--radius-sm)',
              color: '#dc2626',
              cursor: 'pointer',
            }}
          >
            Reset
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '12px', backgroundColor: '#fee2e2', color: '#dc2626', borderRadius: 'var(--radius-sm)', marginBottom: '24px' }}>
          {error}
        </div>
      )}

      {/* Summary Cards */}
      {metrics?.summary && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '24px' }}>
          <MetricCard title="Total Requests" value={metrics.summary.total_requests} />
          <MetricCard title="Total Cost" value={`$${metrics.summary.total_cost.toFixed(4)}`} />
          <MetricCard title="Avg Response Time" value={`${metrics.summary.avg_response_time_ms.toFixed(0)}ms`} />
          <MetricCard title="Avg Complexity" value={metrics.summary.avg_complexity.toFixed(1)} />
          <MetricCard title="Error Rate" value={`${metrics.summary.error_rate}%`} color={metrics.summary.error_rate > 5 ? '#dc2626' : undefined} />
          <MetricCard title="Fallback Rate" value={`${metrics.summary.fallback_rate}%`} />
        </div>
      )}

      {/* Two Column Layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginBottom: '24px' }}>
        {/* By Model */}
        <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '20px', border: '1px solid var(--border-light)' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '16px', color: 'var(--text-primary)' }}>
            Requests by Model
          </h2>
          {metrics?.by_model && Object.entries(metrics.by_model).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {Object.entries(metrics.by_model).map(([model, count]) => (
                <div key={model} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{model}</span>
                  <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{count}</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>No data yet</div>
          )}
        </div>

        {/* By Role */}
        <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '20px', border: '1px solid var(--border-light)' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '16px', color: 'var(--text-primary)' }}>
            Requests by Role
          </h2>
          {metrics?.by_role && Object.entries(metrics.by_role).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {Object.entries(metrics.by_role).map(([role, count]) => (
                <div key={role} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{role}</span>
                  <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{count}</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>No data yet</div>
          )}
        </div>
      </div>

      {/* Router Configuration */}
      {models && (
        <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '20px', border: '1px solid var(--border-light)', marginBottom: '24px' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '16px', color: 'var(--text-primary)' }}>
            Router Configuration
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 500, marginBottom: '8px', color: 'var(--text-secondary)' }}>Role Mappings</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {Object.entries(models.role_mappings).map(([role, model]) => (
                  <div key={role} style={{ fontSize: '14px' }}>
                    <span style={{ color: 'var(--text-tertiary)' }}>{role}</span>
                    <span style={{ color: 'var(--text-tertiary)' }}> → </span>
                    <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{model}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 500, marginBottom: '8px', color: 'var(--text-secondary)' }}>Settings</h3>
              <div style={{ fontSize: '14px', color: 'var(--text-primary)' }}>
                <div>Default: <strong>{models.default}</strong></div>
                <div>Complexity Threshold: <strong>{models.complexity_threshold}</strong></div>
                <div>Enabled Models: <strong>{models.enabled_models.join(', ')}</strong></div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* VOS Activity */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '16px', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            backgroundColor: '#8B5CF6',
            display: 'inline-block',
          }} />
          VOS Activity
        </h2>
        <VosActivityPanel />
      </div>

      {/* Recent Requests */}
      <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '20px', border: '1px solid var(--border-light)' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '16px', color: 'var(--text-primary)' }}>
          Recent Requests
        </h2>
        {metrics?.recent_requests && metrics.recent_requests.length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '14px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-light)' }}>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Time</th>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Role</th>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Complexity</th>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Model</th>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Response Time</th>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Cost</th>
                  <th style={{ textAlign: 'left', padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {metrics.recent_requests.slice().reverse().map((req, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid var(--border-light)' }}>
                    <td style={{ padding: '8px', color: 'var(--text-tertiary)' }}>
                      {new Date(req.timestamp).toLocaleTimeString()}
                    </td>
                    <td style={{ padding: '8px', color: 'var(--text-primary)' }}>{req.role}</td>
                    <td style={{ padding: '8px', color: 'var(--text-primary)' }}>{req.complexity}</td>
                    <td style={{ padding: '8px', color: 'var(--text-primary)', fontWeight: 500 }}>{req.model}</td>
                    <td style={{ padding: '8px', color: 'var(--text-primary)' }}>{req.response_time_ms.toFixed(0)}ms</td>
                    <td style={{ padding: '8px', color: 'var(--text-primary)' }}>${req.cost.toFixed(4)}</td>
                    <td style={{ padding: '8px' }}>
                      <span style={{
                        padding: '2px 8px',
                        borderRadius: '4px',
                        fontSize: '12px',
                        backgroundColor: req.success ? '#dcfce7' : '#fee2e2',
                        color: req.success ? '#16a34a' : '#dc2626',
                      }}>
                        {req.success ? 'OK' : 'Error'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>No requests yet</div>
        )}
      </div>
      <ConfirmMount />
    </div>
  );
}

function MetricCard({ title, value, color }: { title: string; value: string | number; color?: string }) {
  return (
    <div style={{
      backgroundColor: 'var(--bg-secondary)',
      borderRadius: 'var(--radius-md)',
      padding: '16px',
      border: '1px solid var(--border-light)',
    }}>
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{title}</div>
      <div style={{ fontSize: '24px', fontWeight: 600, color: color || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}
