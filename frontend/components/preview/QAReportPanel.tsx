'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, AlertCircle, AlertTriangle, CheckCircle, Info, RefreshCw } from 'lucide-react';

interface QAIssue {
  id: string;
  title: string;
  description: string;
  severity: 'critical' | 'high' | 'medium' | 'info';
  viewport: string;
  route: string;
  location_hint: string;
  fix_suggestion: string;
}

interface QAReportPanelProps {
  issues: QAIssue[];
  summary: { pass: number; warn: number; fail: number; a11y_issues?: number };
  onRecheck?: () => void;
}

const SEVERITY_STYLES: Record<string, { color: string; bg: string; icon: typeof AlertCircle }> = {
  critical: { color: '#ef4444', bg: 'rgba(239,68,68,0.08)', icon: AlertCircle },
  high:     { color: '#f97316', bg: 'rgba(249,115,22,0.08)', icon: AlertTriangle },
  medium:   { color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', icon: AlertTriangle },
  info:     { color: '#3b82f6', bg: 'rgba(59,130,246,0.08)', icon: Info },
};

export function QAReportPanel({ issues, summary, onRecheck }: QAReportPanelProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [expandedIssues, setExpandedIssues] = useState<Set<string>>(new Set());

  const toggleIssue = (id: string) => {
    setExpandedIssues(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const criticalCount = issues.filter(i => i.severity === 'critical').length;
  const highCount = issues.filter(i => i.severity === 'high').length;
  const a11yCount = summary.a11y_issues || 0;

  return (
    <div style={{
      borderTop: '1px solid var(--border-light)',
      backgroundColor: 'var(--bg-secondary)',
    }}>
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          backgroundColor: 'transparent',
          border: 'none',
          cursor: 'pointer',
          fontSize: '13px',
          color: 'var(--text-primary)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span style={{ fontWeight: 600 }}>Visual QA</span>

          {/* Badge counts */}
          <div style={{ display: 'flex', gap: '4px' }}>
            {summary.pass > 0 && (
              <span style={{
                padding: '1px 6px',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: 600,
                backgroundColor: 'rgba(16,185,129,0.1)',
                color: '#10b981',
              }}>
                {summary.pass} pass
              </span>
            )}
            {summary.warn > 0 && (
              <span style={{
                padding: '1px 6px',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: 600,
                backgroundColor: 'rgba(245,158,11,0.1)',
                color: '#f59e0b',
              }}>
                {summary.warn} warn
              </span>
            )}
            {summary.fail > 0 && (
              <span style={{
                padding: '1px 6px',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: 600,
                backgroundColor: 'rgba(239,68,68,0.1)',
                color: '#ef4444',
              }}>
                {summary.fail} fail
              </span>
            )}
          </div>
        </div>

        {onRecheck && (
          <div
            onClick={(e) => { e.stopPropagation(); onRecheck(); }}
            style={{
              padding: '4px 8px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-light)',
              fontSize: '11px',
              color: 'var(--text-secondary)',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              cursor: 'pointer',
            }}
          >
            <RefreshCw size={11} />
            Re-check
          </div>
        )}
      </button>

      {/* Issue list */}
      {isExpanded && (
        <div style={{
          maxHeight: '300px',
          overflow: 'auto',
          borderTop: '1px solid var(--border-light)',
        }}>
          {issues.length === 0 ? (
            <div style={{
              padding: '24px',
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: '13px',
            }}>
              <CheckCircle size={20} style={{ color: '#10b981', marginBottom: '8px' }} />
              <div>No visual issues detected</div>
            </div>
          ) : (
            <>
              {/* A11y section header */}
              {a11yCount > 0 && (
                <div style={{
                  padding: '6px 12px',
                  fontSize: '11px',
                  fontWeight: 600,
                  color: 'var(--text-tertiary)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  backgroundColor: 'var(--bg-tertiary)',
                  borderBottom: '1px solid var(--border-light)',
                }}>
                  Accessibility ({a11yCount} issues)
                </div>
              )}
              {issues.map((issue) => {
                const style = SEVERITY_STYLES[issue.severity] || SEVERITY_STYLES.info;
                const Icon = style.icon;
                const isOpen = expandedIssues.has(issue.id);

                return (
                  <div
                    key={issue.id}
                    style={{
                      borderBottom: '1px solid var(--border-light)',
                      backgroundColor: isOpen ? style.bg : 'transparent',
                    }}
                  >
                    <button
                      onClick={() => toggleIssue(issue.id)}
                      style={{
                        width: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '8px 12px',
                        backgroundColor: 'transparent',
                        border: 'none',
                        cursor: 'pointer',
                        textAlign: 'left',
                      }}
                    >
                      <Icon size={14} style={{ color: style.color, flexShrink: 0 }} />
                      <span style={{ fontSize: '12px', color: 'var(--text-primary)', flex: 1 }}>
                        {issue.title}
                      </span>
                      <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                        {issue.viewport}
                      </span>
                    </button>
                    {isOpen && (
                      <div style={{ padding: '0 12px 10px 34px', fontSize: '11px' }}>
                        <p style={{ color: 'var(--text-secondary)', marginBottom: '6px', lineHeight: '1.5' }}>
                          {issue.description}
                        </p>
                        {issue.fix_suggestion && (
                          <p style={{ color: style.color, fontWeight: 500 }}>
                            Fix: {issue.fix_suggestion}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}
    </div>
  );
}
