/**
 * V Core UI Components
 * 
 * Part 2: Workflow Engine & Mission Control
 * - Workflow cards and builder
 * - Mission Control dashboard
 * - Approvals, Alerts, Metrics
 */

'use client';

import React, { useState } from 'react';
import type { 
  Workflow, WorkflowExecution, 
  Activity, ApprovalRequest, Alert, Metric, DashboardData 
} from '../../hooks/useVCore';

// ============================================
// Workflow Engine Components
// ============================================

interface WorkflowCardProps {
  workflow: Workflow;
  onClick?: () => void;
  onActivate?: () => void;
  onPause?: () => void;
  onRun?: () => void;
}

export function WorkflowCard({ workflow, onClick, onActivate, onPause, onRun }: WorkflowCardProps) {
  const statusColors: Record<string, { bg: string; text: string }> = {
    draft: { bg: '#f3f4f6', text: '#6b7280' },
    active: { bg: '#dcfce7', text: '#16a34a' },
    paused: { bg: '#fef3c7', text: '#d97706' },
    archived: { bg: '#e5e7eb', text: '#4b5563' },
  };

  const successRate = workflow.runCount > 0 
    ? Math.round((workflow.successCount / workflow.runCount) * 100) 
    : 0;

  return (
    <div
      onClick={onClick}
      style={{
        padding: '1.25rem',
        background: 'white',
        border: '1px solid var(--color-border, #e5e5ea)',
        borderRadius: '12px',
        cursor: onClick ? 'pointer' : 'default',
        transition: 'all 0.2s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <span
          style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            background: workflow.color + '20',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '1.25rem',
          }}
        >
          {workflow.icon}
        </span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '1rem' }}>{workflow.name}</div>
          <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
            {workflow.description || `${workflow.nodes.length} steps`}
          </div>
        </div>
        <span
          style={{
            padding: '0.25rem 0.625rem',
            background: statusColors[workflow.status].bg,
            color: statusColors[workflow.status].text,
            borderRadius: '999px',
            fontSize: '0.75rem',
            fontWeight: 500,
            textTransform: 'capitalize',
          }}
        >
          {workflow.status}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', fontSize: '0.8125rem' }}>
        <span style={{ color: 'var(--color-text-secondary, #6e6e73)' }}>
          🔄 {workflow.runCount} runs
        </span>
        <span style={{ color: successRate >= 90 ? '#16a34a' : successRate >= 70 ? '#d97706' : '#dc2626' }}>
          ✓ {successRate}% success
        </span>
        {workflow.lastRunAt && (
          <span style={{ color: 'var(--color-text-tertiary, #86868b)' }}>
            🕐 {new Date(workflow.lastRunAt).toLocaleDateString()}
          </span>
        )}
      </div>

      <div style={{ display: 'flex', gap: '0.5rem' }} onClick={(e) => e.stopPropagation()}>
        {workflow.status === 'draft' || workflow.status === 'paused' ? (
          <button
            onClick={onActivate}
            style={{
              padding: '0.5rem 1rem',
              background: '#dcfce7',
              color: '#16a34a',
              border: 'none',
              borderRadius: '6px',
              fontSize: '0.8125rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            ▶ Activate
          </button>
        ) : workflow.status === 'active' ? (
          <button
            onClick={onPause}
            style={{
              padding: '0.5rem 1rem',
              background: '#fef3c7',
              color: '#d97706',
              border: 'none',
              borderRadius: '6px',
              fontSize: '0.8125rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            ⏸ Pause
          </button>
        ) : null}
        {workflow.status === 'active' && (
          <button
            onClick={onRun}
            style={{
              padding: '0.5rem 1rem',
              background: 'var(--color-accent, #007AFF)',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              fontSize: '0.8125rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            ⚡ Run Now
          </button>
        )}
      </div>
    </div>
  );
}

interface ExecutionListProps {
  executions: WorkflowExecution[];
}

export function ExecutionList({ executions }: ExecutionListProps) {
  const statusIcons: Record<string, string> = {
    pending: '⏳',
    running: '🔄',
    completed: '✅',
    failed: '❌',
    cancelled: '🚫',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {executions.map((exec) => (
        <div
          key={exec.id}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.75rem',
            padding: '0.75rem',
            background: 'var(--color-background-secondary, #f5f5f7)',
            borderRadius: '8px',
          }}
        >
          <span style={{ fontSize: '1.25rem' }}>{statusIcons[exec.status]}</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>
              {exec.id}
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
              {exec.startedAt ? new Date(exec.startedAt).toLocaleString() : 'Not started'}
            </div>
          </div>
          <span
            style={{
              padding: '0.25rem 0.5rem',
              background: exec.status === 'completed' ? '#dcfce7' : exec.status === 'failed' ? '#fee2e2' : '#f3f4f6',
              color: exec.status === 'completed' ? '#16a34a' : exec.status === 'failed' ? '#dc2626' : '#6b7280',
              borderRadius: '6px',
              fontSize: '0.75rem',
              fontWeight: 500,
              textTransform: 'capitalize',
            }}
          >
            {exec.status}
          </span>
        </div>
      ))}
      {executions.length === 0 && (
        <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-tertiary, #86868b)' }}>
          No executions yet
        </div>
      )}
    </div>
  );
}

// ============================================
// Mission Control Components
// ============================================

interface MetricCardProps {
  metric: Metric;
}

export function MetricCard({ metric }: MetricCardProps) {
  const formatValue = (value: number, unit: string) => {
    if (unit === 'USD') return `$${value.toFixed(2)}`;
    if (unit === '%') return `${value.toFixed(1)}%`;
    if (unit === 'ms') return `${value}ms`;
    if (unit === 'GB') return `${value.toFixed(1)} GB`;
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
    return value.toString();
  };

  return (
    <div
      style={{
        padding: '1rem',
        background: 'white',
        border: '1px solid var(--color-border, #e5e5ea)',
        borderRadius: '12px',
      }}
    >
      <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)', marginBottom: '0.5rem' }}>
        {metric.name}
      </div>
      <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>
        {formatValue(metric.value, metric.unit)}
      </div>
    </div>
  );
}

interface ActivityFeedProps {
  activities: Activity[];
}

export function ActivityFeed({ activities }: ActivityFeedProps) {
  const typeIcons: Record<string, string> = {
    agent_action: '🤖',
    workflow_run: '⚡',
    api_call: '🔌',
    user_action: '👤',
    system_event: '⚙️',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {activities.slice(0, 10).map((activity) => (
        <div
          key={activity.id}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.75rem',
            padding: '0.75rem',
            background: 'var(--color-background-secondary, #f5f5f7)',
            borderRadius: '8px',
          }}
        >
          <span style={{ fontSize: '1.25rem' }}>{typeIcons[activity.type] || '📋'}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>
              {activity.sourceName} • {activity.action}
            </div>
            <div
              style={{
                fontSize: '0.8125rem',
                color: 'var(--color-text-secondary, #6e6e73)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {activity.description}
            </div>
          </div>
          <div style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--color-text-tertiary, #86868b)' }}>
            <div>{activity.durationMs}ms</div>
            <div>{new Date(activity.startedAt).toLocaleTimeString()}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

interface ApprovalCardProps {
  approval: ApprovalRequest;
  onApprove?: () => void;
  onReject?: () => void;
}

export function ApprovalCard({ approval, onApprove, onReject }: ApprovalCardProps) {
  const priorityColors: Record<string, { bg: string; text: string }> = {
    low: { bg: '#f3f4f6', text: '#6b7280' },
    normal: { bg: '#dbeafe', text: '#2563eb' },
    high: { bg: '#fef3c7', text: '#d97706' },
    urgent: { bg: '#fee2e2', text: '#dc2626' },
  };

  return (
    <div
      style={{
        padding: '1rem',
        background: 'white',
        border: '1px solid var(--color-border, #e5e5ea)',
        borderRadius: '12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <span style={{ fontSize: '1.5rem' }}>⏳</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '0.9375rem' }}>{approval.title}</div>
          <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
            {approval.description}
          </div>
        </div>
        <span
          style={{
            padding: '0.25rem 0.5rem',
            background: priorityColors[approval.priority].bg,
            color: priorityColors[approval.priority].text,
            borderRadius: '6px',
            fontSize: '0.75rem',
            fontWeight: 500,
            textTransform: 'capitalize',
          }}
        >
          {approval.priority}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
        <span>📁 {approval.category}</span>
        <span>•</span>
        <span>👤 {approval.requestedByName}</span>
        <span>•</span>
        <span>🕐 {new Date(approval.createdAt).toLocaleString()}</span>
      </div>

      {approval.status === 'pending' && (
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            onClick={onApprove}
            style={{
              flex: 1,
              padding: '0.625rem 1rem',
              background: '#dcfce7',
              color: '#16a34a',
              border: 'none',
              borderRadius: '8px',
              fontSize: '0.875rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            ✓ Approve
          </button>
          <button
            onClick={onReject}
            style={{
              flex: 1,
              padding: '0.625rem 1rem',
              background: '#fee2e2',
              color: '#dc2626',
              border: 'none',
              borderRadius: '8px',
              fontSize: '0.875rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            ✗ Reject
          </button>
        </div>
      )}
    </div>
  );
}

interface AlertItemProps {
  alert: Alert;
  onResolve?: () => void;
}

export function AlertItem({ alert, onResolve }: AlertItemProps) {
  const severityConfig: Record<string, { bg: string; border: string; icon: string }> = {
    info: { bg: '#eff6ff', border: '#3b82f6', icon: 'ℹ️' },
    warning: { bg: '#fffbeb', border: '#f59e0b', icon: '⚠️' },
    error: { bg: '#fef2f2', border: '#ef4444', icon: '❌' },
    critical: { bg: '#fef2f2', border: '#dc2626', icon: '🚨' },
  };

  const config = severityConfig[alert.severity];

  return (
    <div
      style={{
        padding: '1rem',
        background: config.bg,
        borderLeft: `4px solid ${config.border}`,
        borderRadius: '0 8px 8px 0',
        opacity: alert.isResolved ? 0.5 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem' }}>
        <span style={{ fontSize: '1.25rem' }}>{config.icon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '0.9375rem' }}>{alert.title}</div>
          <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)', marginTop: '0.25rem' }}>
            {alert.message}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary, #86868b)', marginTop: '0.5rem' }}>
            {new Date(alert.createdAt).toLocaleString()}
          </div>
        </div>
        {!alert.isResolved && onResolve && (
          <button
            onClick={onResolve}
            style={{
              padding: '0.5rem 0.75rem',
              background: 'white',
              border: '1px solid var(--color-border, #e5e5ea)',
              borderRadius: '6px',
              fontSize: '0.75rem',
              cursor: 'pointer',
            }}
          >
            Resolve
          </button>
        )}
      </div>
    </div>
  );
}

interface MissionControlDashboardProps {
  data: DashboardData;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onResolveAlert: (id: string) => void;
}

export function MissionControlDashboard({ data, onApprove, onReject, onResolveAlert }: MissionControlDashboardProps) {
  const [activeTab, setActiveTab] = useState<'overview' | 'activity' | 'approvals' | 'alerts'>('overview');

  const formatMetric = (key: string, value: number) => {
    if (key.includes('rate')) return `${value.toFixed(1)}%`;
    if (key.includes('cost')) return `$${value.toFixed(2)}`;
    if (key.includes('latency')) return `${value}ms`;
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
    return value.toString();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600 }}>🎛️ Mission Control</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {(['overview', 'activity', 'approvals', 'alerts'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: '0.5rem 1rem',
                background: activeTab === tab ? 'var(--color-accent, #007AFF)' : 'transparent',
                color: activeTab === tab ? 'white' : 'var(--color-text-secondary, #6e6e73)',
                border: 'none',
                borderRadius: '8px',
                fontSize: '0.875rem',
                fontWeight: 500,
                cursor: 'pointer',
                textTransform: 'capitalize',
              }}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <>
          {/* Key Metrics Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '1rem' }}>
            {Object.entries(data.metrics).slice(0, 8).map(([key, value]: [string, number]) => (
              <div
                key={key}
                style={{
                  padding: '1rem',
                  background: 'white',
                  border: '1px solid var(--color-border, #e5e5ea)',
                  borderRadius: '12px',
                }}
              >
                <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary, #6e6e73)', marginBottom: '0.25rem' }}>
                  {key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                </div>
                <div style={{ fontSize: '1.375rem', fontWeight: 600 }}>
                  {formatMetric(key, value)}
                </div>
              </div>
            ))}
          </div>

          {/* Activity Summary */}
          <div
            style={{
              padding: '1.25rem',
              background: 'white',
              border: '1px solid var(--color-border, #e5e5ea)',
              borderRadius: '12px',
            }}
          >
            <h3 style={{ margin: '0 0 1rem', fontSize: '1.125rem', fontWeight: 600 }}>Activity Summary (24h)</h3>
            <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>Total</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>{data.activitySummary.total}</div>
              </div>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>Successful</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600, color: '#16a34a' }}>{data.activitySummary.successful}</div>
              </div>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>Failed</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600, color: '#dc2626' }}>{data.activitySummary.failed}</div>
              </div>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>Success Rate</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>{data.activitySummary.successRate.toFixed(1)}%</div>
              </div>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>Tokens Used</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>{(data.activitySummary.totalTokens / 1000).toFixed(1)}K</div>
              </div>
              <div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>Cost</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600 }}>${data.activitySummary.totalCost.toFixed(2)}</div>
              </div>
            </div>
          </div>

          {/* Quick Actions Row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            {/* Pending Approvals */}
            <div style={{ padding: '1.25rem', background: 'white', border: '1px solid var(--color-border, #e5e5ea)', borderRadius: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>⏳ Pending Approvals</h3>
                <span style={{ padding: '0.25rem 0.5rem', background: '#fef3c7', color: '#d97706', borderRadius: '6px', fontSize: '0.75rem', fontWeight: 500 }}>
                  {data.pendingApprovals.length}
                </span>
              </div>
              {data.pendingApprovals.slice(0, 3).map((approval) => (
                <div key={approval.id} style={{ padding: '0.75rem', background: 'var(--color-background-secondary, #f5f5f7)', borderRadius: '8px', marginBottom: '0.5rem' }}>
                  <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>{approval.title}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary, #86868b)' }}>
                    {approval.requestedByName} • {approval.category}
                  </div>
                </div>
              ))}
            </div>

            {/* Alerts */}
            <div style={{ padding: '1.25rem', background: 'white', border: '1px solid var(--color-border, #e5e5ea)', borderRadius: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>🔔 Alerts</h3>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  {data.alerts.critical > 0 && <span style={{ padding: '0.25rem 0.5rem', background: '#fee2e2', color: '#dc2626', borderRadius: '6px', fontSize: '0.75rem' }}>🚨 {data.alerts.critical}</span>}
                  {data.alerts.warning > 0 && <span style={{ padding: '0.25rem 0.5rem', background: '#fef3c7', color: '#d97706', borderRadius: '6px', fontSize: '0.75rem' }}>⚠️ {data.alerts.warning}</span>}
                </div>
              </div>
              {data.recentAlerts.slice(0, 3).map((alert) => (
                <div key={alert.id} style={{ padding: '0.75rem', background: 'var(--color-background-secondary, #f5f5f7)', borderRadius: '8px', marginBottom: '0.5rem' }}>
                  <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>{alert.title}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary, #86868b)' }}>
                    {alert.category} • {alert.severity}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* Activity Tab */}
      {activeTab === 'activity' && (
        <div style={{ padding: '1.25rem', background: 'white', border: '1px solid var(--color-border, #e5e5ea)', borderRadius: '12px' }}>
          <h3 style={{ margin: '0 0 1rem', fontSize: '1.125rem', fontWeight: 600 }}>Recent Activity</h3>
          <ActivityFeed activities={data.recentActivities} />
        </div>
      )}

      {/* Approvals Tab */}
      {activeTab === 'approvals' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {data.pendingApprovals.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onApprove={() => onApprove(approval.id)}
              onReject={() => onReject(approval.id)}
            />
          ))}
          {data.pendingApprovals.length === 0 && (
            <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--color-text-tertiary, #86868b)' }}>
              ✅ No pending approvals
            </div>
          )}
        </div>
      )}

      {/* Alerts Tab */}
      {activeTab === 'alerts' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {data.recentAlerts.map((alert) => (
            <AlertItem
              key={alert.id}
              alert={alert}
              onResolve={() => onResolveAlert(alert.id)}
            />
          ))}
          {data.recentAlerts.length === 0 && (
            <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--color-text-tertiary, #86868b)' }}>
              ✅ No active alerts
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================
// Export
// ============================================

export default {
  WorkflowCard,
  ExecutionList,
  MetricCard,
  ActivityFeed,
  ApprovalCard,
  AlertItem,
  MissionControlDashboard,
};
