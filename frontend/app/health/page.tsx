'use client';

import { useState, useEffect } from 'react';

interface ServiceStatus {
  status: string;
  last_check: string | null;
  [key: string]: any;
}

interface HealthData {
  status: string;
  timestamp: string;
  services: Record<string, ServiceStatus>;
  errors_last_hour: {
    critical: number;
    error: number;
    warning: number;
  };
  total_errors: number;
  unresolved_errors: number;
}

interface ErrorRecord {
  timestamp: string;
  error_type: string;
  message: string;
  source: string;
  severity: string;
  stack_trace: string | null;
  resolved: boolean;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export default function HealthPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [errors, setErrors] = useState<ErrorRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showResolved, setShowResolved] = useState(false);

  const fetchHealth = async () => {
    try {
      const [healthRes, errorsRes] = await Promise.all([
        fetch(`${API_URL}/api/metrics/health`),
        fetch(`${API_URL}/api/metrics/errors?include_resolved=${showResolved}`),
      ]);

      if (healthRes.ok) {
        setHealth(await healthRes.json());
      }
      if (errorsRes.ok) {
        const data = await errorsRes.json();
        setErrors(data.errors || []);
      }
    } catch (err) {
      console.error('Failed to fetch health data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();

    if (autoRefresh) {
      const interval = setInterval(fetchHealth, 5000);
      return () => clearInterval(interval);
    }
  }, [autoRefresh, showResolved]);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'healthy':
        return { bg: '#dcfce7', color: '#16a34a' };
      case 'degraded':
      case 'warning':
        return { bg: '#fef3c7', color: '#d97706' };
      case 'critical':
      case 'error':
        return { bg: '#fee2e2', color: '#dc2626' };
      default:
        return { bg: '#f3f4f6', color: '#6b7280' };
    }
  };

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'critical':
        return { bg: '#fee2e2', color: '#dc2626' };
      case 'error':
        return { bg: '#ffedd5', color: '#ea580c' };
      case 'warning':
        return { bg: '#fef3c7', color: '#d97706' };
      default:
        return { bg: '#f3f4f6', color: '#6b7280' };
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading health status...
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <h1 style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>
            System Health
          </h1>
          {health && (
            <span
              style={{
                padding: '4px 12px',
                borderRadius: '9999px',
                fontSize: '14px',
                fontWeight: 500,
                ...getStatusColor(health.status),
              }}
            >
              {health.status.toUpperCase()}
            </span>
          )}
        </div>
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
            onClick={fetchHealth}
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
        </div>
      </div>

      {/* Services Grid */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '12px', color: 'var(--text-primary)' }}>
          Services
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '16px' }}>
          {health?.services && Object.entries(health.services).map(([name, service]) => {
            const statusStyle = getStatusColor(service.status);
            return (
              <div
                key={name}
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-md)',
                  padding: '16px',
                  border: '1px solid var(--border-light)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <span style={{ fontSize: '16px', fontWeight: 500, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                    {name}
                  </span>
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: '4px',
                      fontSize: '12px',
                      fontWeight: 500,
                      ...statusStyle,
                    }}
                  >
                    {service.status}
                  </span>
                </div>
                {service.last_check && (
                  <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                    Last check: {new Date(service.last_check).toLocaleTimeString()}
                  </div>
                )}
                {Object.entries(service).filter(([k]) => !['status', 'last_check'].includes(k)).map(([key, value]) => (
                  <div key={key} style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                    {key}: <span style={{ color: 'var(--text-primary)' }}>{String(value)}</span>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>

      {/* Kernel Disk Health (Day 10) */}
      <DiskHealthWidget apiUrl={API_URL} autoRefresh={autoRefresh} />

      {/* Error Summary */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px', marginBottom: '24px' }}>
        <ErrorCountCard
          title="Critical"
          count={health?.errors_last_hour.critical || 0}
          color="#dc2626"
          bgColor="#fee2e2"
        />
        <ErrorCountCard
          title="Errors"
          count={health?.errors_last_hour.error || 0}
          color="#ea580c"
          bgColor="#ffedd5"
        />
        <ErrorCountCard
          title="Warnings"
          count={health?.errors_last_hour.warning || 0}
          color="#d97706"
          bgColor="#fef3c7"
        />
        <ErrorCountCard
          title="Unresolved"
          count={health?.unresolved_errors || 0}
          color="#6b7280"
          bgColor="#f3f4f6"
        />
      </div>

      {/* Error List */}
      <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '20px', border: '1px solid var(--border-light)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
            Recent Errors & Bugs
          </h2>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>
            <input
              type="checkbox"
              checked={showResolved}
              onChange={(e) => setShowResolved(e.target.checked)}
            />
            Show resolved
          </label>
        </div>

        {errors.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {errors.slice().reverse().map((error, idx) => {
              const severityStyle = getSeverityColor(error.severity);
              return (
                <div
                  key={idx}
                  style={{
                    padding: '12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)',
                    backgroundColor: error.resolved ? 'var(--bg-primary)' : 'var(--bg-secondary)',
                    opacity: error.resolved ? 0.7 : 1,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span
                        style={{
                          padding: '2px 8px',
                          borderRadius: '4px',
                          fontSize: '11px',
                          fontWeight: 500,
                          textTransform: 'uppercase',
                          ...severityStyle,
                        }}
                      >
                        {error.severity}
                      </span>
                      <span style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-primary)' }}>
                        {error.error_type}
                      </span>
                      <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        in {error.source}
                      </span>
                    </div>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                      {new Date(error.timestamp).toLocaleString()}
                    </span>
                  </div>
                  <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
                    {error.message}
                  </div>
                  {error.stack_trace && (
                    <pre
                      style={{
                        marginTop: '8px',
                        padding: '8px',
                        backgroundColor: 'var(--bg-primary)',
                        borderRadius: '4px',
                        fontSize: '12px',
                        color: 'var(--text-tertiary)',
                        overflow: 'auto',
                        maxHeight: '100px',
                      }}
                    >
                      {error.stack_trace}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
            No errors recorded
          </div>
        )}
      </div>
    </div>
  );
}

function DiskHealthWidget({ apiUrl, autoRefresh }: { apiUrl: string; autoRefresh: boolean }) {
  const [stat, setStat] = useState<Record<string, number> | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [available, setAvailable] = useState(true);

  useEffect(() => {
    const fetchDisk = async () => {
      try {
        const [statRes, lsRes] = await Promise.all([
          fetch(`${apiUrl}/api/kernel/disk/stat`),
          fetch(`${apiUrl}/api/kernel/disk/ls`),
        ]);
        if (statRes.ok) {
          setStat(await statRes.json());
          setAvailable(true);
        } else {
          setAvailable(false);
        }
        if (lsRes.ok) {
          const data = await lsRes.json();
          setFiles(data.files || []);
        }
      } catch {
        setAvailable(false);
      }
    };

    fetchDisk();
    if (autoRefresh) {
      const interval = setInterval(fetchDisk, 10000);
      return () => clearInterval(interval);
    }
  }, [apiUrl, autoRefresh]);

  if (!available || !stat) return null;

  const totalBlocks = stat.total_blocks || 1;
  const freeBlocks = stat.free_blocks || 0;
  const usedPercent = Math.round(((totalBlocks - freeBlocks) / totalBlocks) * 100);
  const barColor = usedPercent > 90 ? '#dc2626' : usedPercent > 70 ? '#d97706' : '#16a34a';

  return (
    <div style={{ marginBottom: '24px' }}>
      <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '12px', color: 'var(--text-primary)' }}>
        Kernel Disk (/disk)
      </h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
        <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '16px', border: '1px solid var(--border-light)' }}>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Mount Count</div>
          <div style={{ fontSize: '28px', fontWeight: 600, color: 'var(--text-primary)' }}>{stat.mount_count ?? '-'}</div>
        </div>
        <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '16px', border: '1px solid var(--border-light)' }}>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>Disk Usage</div>
          <div style={{ fontSize: '22px', fontWeight: 600, color: barColor, marginBottom: '8px' }}>{usedPercent}%</div>
          <div style={{ height: '6px', backgroundColor: 'var(--bg-primary)', borderRadius: '3px', overflow: 'hidden' }}>
            <div style={{ width: `${usedPercent}%`, height: '100%', backgroundColor: barColor, borderRadius: '3px' }} />
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
            {freeBlocks} / {totalBlocks} blocks free
          </div>
        </div>
        <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '16px', border: '1px solid var(--border-light)' }}>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Files on /disk</div>
          <div style={{ fontSize: '28px', fontWeight: 600, color: 'var(--text-primary)' }}>{files.length}</div>
          {files.length > 0 && (
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
              {files.slice(0, 5).join(', ')}{files.length > 5 ? `, +${files.length - 5} more` : ''}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ErrorCountCard({ title, count, color, bgColor }: { title: string; count: number; color: string; bgColor: string }) {
  return (
    <div
      style={{
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-md)',
        padding: '16px',
        border: '1px solid var(--border-light)',
      }}
    >
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{title} (last hour)</div>
      <div style={{ fontSize: '28px', fontWeight: 600, color: count > 0 ? color : 'var(--text-primary)' }}>
        {count}
      </div>
    </div>
  );
}
