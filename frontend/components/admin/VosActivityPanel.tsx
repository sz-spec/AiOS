'use client';

import { useState, useEffect } from 'react';

interface VosAction {
  timestamp: string;
  intent: string;
  tools: string[];
  success: boolean;
  summary: string;
  source: string;
  source_id?: string;
}

interface VosStats {
  total: number;
  by_intent: Record<string, number>;
  by_source: Record<string, number>;
  by_tool: Record<string, number>;
}

const INTENT_COLORS: Record<string, string> = {
  agent_management: '#8B5CF6',
  memory_management: '#3B82F6',
  vcore_management: '#10B981',
  system_query: '#F59E0B',
  settings_management: '#6366F1',
  codegen: '#EC4899',
};

export function VosActivityPanel() {
  const [actions, setActions] = useState<VosAction[]>([]);
  const [stats, setStats] = useState<VosStats | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const [actionsRes, statsRes] = await Promise.all([
        fetch('/api/vos/actions?limit=30'),
        fetch('/api/vos/actions/stats'),
      ]);
      if (actionsRes.ok) {
        const data = await actionsRes.json();
        setActions(data.actions || []);
      }
      if (statsRes.ok) {
        setStats(await statsRes.json());
      }
    } catch {
      // Silently fail — VOS may not be running
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div style={{ padding: '20px', color: 'var(--text-tertiary)', fontSize: '14px' }}>
        Loading VOS activity...
      </div>
    );
  }

  const maxIntentCount = stats ? Math.max(...Object.values(stats.by_intent), 1) : 1;
  const maxToolCount = stats ? Math.max(...Object.values(stats.by_tool), 1) : 1;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        {/* Intent Distribution */}
        <div style={{
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          padding: '16px',
          border: '1px solid var(--border-light)',
        }}>
          <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Intent Distribution
          </h3>
          {stats && Object.keys(stats.by_intent).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {Object.entries(stats.by_intent)
                .sort(([, a], [, b]) => b - a)
                .map(([intent, count]) => (
                  <div key={intent}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '2px' }}>
                      <span style={{ color: INTENT_COLORS[intent] || 'var(--text-secondary)' }}>
                        {intent.replace('_', ' ')}
                      </span>
                      <span style={{ color: 'var(--text-tertiary)' }}>{count}</span>
                    </div>
                    <div style={{
                      height: '4px',
                      backgroundColor: 'var(--bg-tertiary)',
                      borderRadius: '2px',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        width: `${(count / maxIntentCount) * 100}%`,
                        height: '100%',
                        backgroundColor: INTENT_COLORS[intent] || '#6B7280',
                        borderRadius: '2px',
                      }} />
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>No data yet</div>
          )}
        </div>

        {/* Source Breakdown */}
        <div style={{
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          padding: '16px',
          border: '1px solid var(--border-light)',
        }}>
          <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Source Breakdown
          </h3>
          {stats && Object.keys(stats.by_source).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {Object.entries(stats.by_source)
                .sort(([, a], [, b]) => b - a)
                .map(([source, count]) => (
                  <div key={source} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{
                      padding: '2px 8px',
                      backgroundColor: source === 'chat' ? 'rgba(59, 130, 246, 0.1)' : source === 'agent' ? 'rgba(139, 92, 246, 0.1)' : 'var(--bg-tertiary)',
                      border: `1px solid ${source === 'chat' ? 'rgba(59, 130, 246, 0.2)' : source === 'agent' ? 'rgba(139, 92, 246, 0.2)' : 'var(--border-light)'}`,
                      borderRadius: 'var(--radius-full)',
                      fontSize: '11px',
                      fontWeight: 500,
                      color: source === 'chat' ? '#3B82F6' : source === 'agent' ? '#8B5CF6' : 'var(--text-secondary)',
                    }}>
                      {source}
                    </span>
                    <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{count}</span>
                  </div>
                ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>No data yet</div>
          )}
        </div>

        {/* Tool Usage */}
        <div style={{
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          padding: '16px',
          border: '1px solid var(--border-light)',
        }}>
          <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Tool Usage
          </h3>
          {stats && Object.keys(stats.by_tool).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {Object.entries(stats.by_tool)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 8)
                .map(([tool, count]) => (
                  <div key={tool}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '2px' }}>
                      <span style={{ color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: '11px' }}>
                        {tool}
                      </span>
                      <span style={{ color: 'var(--text-tertiary)' }}>{count}</span>
                    </div>
                    <div style={{
                      height: '4px',
                      backgroundColor: 'var(--bg-tertiary)',
                      borderRadius: '2px',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        width: `${(count / maxToolCount) * 100}%`,
                        height: '100%',
                        backgroundColor: '#6366F1',
                        borderRadius: '2px',
                      }} />
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>No data yet</div>
          )}
        </div>
      </div>

      {/* Recent Actions Timeline */}
      <div style={{
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-md)',
        padding: '16px',
        border: '1px solid var(--border-light)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Recent Actions
          </h3>
          <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
            {stats?.total || 0} total actions
          </span>
        </div>

        {actions.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '400px', overflow: 'auto' }}>
            {actions.map((action, idx) => {
              const intentColor = INTENT_COLORS[action.intent] || '#6B7280';
              return (
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    gap: '10px',
                    padding: '8px 10px',
                    backgroundColor: action.success ? 'transparent' : 'rgba(239, 68, 68, 0.05)',
                    borderRadius: 'var(--radius-sm)',
                    borderLeft: `3px solid ${intentColor}`,
                  }}
                >
                  {/* Timestamp */}
                  <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap', minWidth: '60px' }}>
                    {new Date(action.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>

                  {/* Intent badge */}
                  <span style={{
                    padding: '1px 6px',
                    backgroundColor: `${intentColor}15`,
                    border: `1px solid ${intentColor}30`,
                    borderRadius: 'var(--radius-full)',
                    fontSize: '10px',
                    fontWeight: 600,
                    color: intentColor,
                    whiteSpace: 'nowrap',
                  }}>
                    {action.intent.replace('_', ' ')}
                  </span>

                  {/* Tools used */}
                  {action.tools.length > 0 && (
                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                      {action.tools.join(', ')}
                    </span>
                  )}

                  {/* Summary */}
                  <span style={{
                    flex: 1,
                    fontSize: '12px',
                    color: 'var(--text-primary)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}>
                    {action.summary}
                  </span>

                  {/* Source */}
                  <span style={{
                    fontSize: '10px',
                    color: 'var(--text-tertiary)',
                    whiteSpace: 'nowrap',
                  }}>
                    {action.source}
                  </span>

                  {/* Status */}
                  <span style={{
                    width: '8px',
                    height: '8px',
                    borderRadius: '50%',
                    backgroundColor: action.success ? '#22C55E' : '#EF4444',
                    flexShrink: 0,
                    alignSelf: 'center',
                  }} />
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px' }}>
            No VOS actions recorded yet. Send a system-level command in chat to see activity here.
          </div>
        )}
      </div>
    </div>
  );
}
