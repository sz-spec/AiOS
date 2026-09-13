/**
 * AIDebugPanel — Phase 3.0 Subsystem 3.2
 * ========================================
 * Displays AI-powered error analysis results from the debug API.
 * Shows root cause, explanation, suggested fix with diff view,
 * confidence gauge, and "Apply Fix" / "Send to Chat" actions.
 */

'use client';

import { useState, useCallback } from 'react';
import {
  Zap,
  CheckCircle,
  XCircle,
  Loader2,
  Copy,
  MessageSquare,
  AlertCircle,
} from 'lucide-react';
import type { ConsoleEntry } from '@/hooks/useConsoleCapture';

// ─── Types ───────────────────────────────────────────────────────────────────

interface SuggestedFix {
  file: string;
  line: number | null;
  before: string;
  after: string;
}

interface AnalysisResult {
  root_cause: string;
  explanation: string;
  suggested_fix: SuggestedFix | null;
  confidence: number;
  related_issues: string[];
  model_used: string;
  error_category: string;
}

interface AIDebugPanelProps {
  /** The error entry being analyzed. */
  error: ConsoleEntry | null;
  /** Code files for context. */
  projectFiles: Record<string, string>;
  /** Callback when user clicks "Apply Fix". */
  onApplyFix?: (fix: SuggestedFix) => void;
  /** Callback when user clicks "Send to Chat". */
  onSendToChat?: (error: ConsoleEntry, analysis: AnalysisResult) => void;
  /** API base URL. */
  apiUrl?: string;
}

// ─── Confidence Gauge ────────────────────────────────────────────────────────

function ConfidenceGauge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const color = confidence >= 0.8 ? '#22c55e'
    : confidence >= 0.5 ? '#f59e0b'
    : '#ef4444';
  const label = confidence >= 0.8 ? 'High'
    : confidence >= 0.5 ? 'Medium'
    : 'Low';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <div style={{
        width: 60,
        height: 6,
        backgroundColor: '#333',
        borderRadius: 3,
        overflow: 'hidden',
      }}>
        <div style={{
          width: `${pct}%`,
          height: '100%',
          backgroundColor: color,
          borderRadius: 3,
          transition: 'width 0.3s ease',
        }} />
      </div>
      <span style={{ color, fontSize: '12px', fontWeight: 600 }}>
        {pct}% {label}
      </span>
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export function AIDebugPanel({
  error,
  projectFiles,
  onApplyFix,
  onSendToChat,
  apiUrl = '/api',
}: AIDebugPanelProps) {
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [fixApplied, setFixApplied] = useState(false);

  const analyzeError = useCallback(async () => {
    if (!error) return;

    setLoading(true);
    setApiError(null);
    setAnalysis(null);
    setFixApplied(false);

    // Gather relevant code files (files mentioned in error + active files)
    const relevantCode: Record<string, string> = {};
    if (error.file && projectFiles[error.file]) {
      relevantCode[error.file] = projectFiles[error.file];
    }
    // Include up to 4 more files that might be related
    const otherFiles = Object.entries(projectFiles)
      .filter(([path]) => path !== error.file)
      .slice(0, 4);
    for (const [path, content] of otherFiles) {
      relevantCode[path] = content;
    }

    try {
      const res = await fetch(`${apiUrl}/v1/debug/analyze-error`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          error_message: error.message,
          stack_trace: error.stackTrace || '',
          relevant_code: relevantCode,
        }),
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`API error ${res.status}: ${detail}`);
      }

      const data: AnalysisResult = await res.json();
      setAnalysis(data);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Analysis failed');
    } finally {
      setLoading(false);
    }
  }, [error, projectFiles, apiUrl]);

  const handleApplyFix = useCallback(() => {
    if (analysis?.suggested_fix && onApplyFix) {
      onApplyFix(analysis.suggested_fix);
      setFixApplied(true);
    }
  }, [analysis, onApplyFix]);

  const handleSendToChat = useCallback(() => {
    if (error && analysis && onSendToChat) {
      onSendToChat(error, analysis);
    }
  }, [error, analysis, onSendToChat]);

  if (!error) {
    return (
      <div style={{
        padding: '24px',
        textAlign: 'center',
        color: '#666',
        fontSize: '13px',
        backgroundColor: '#1e1e1e',
        borderTop: '1px solid #333',
      }}>
        <Zap size={24} color="#4b5563" style={{ marginBottom: 8 }} />
        <div>Click "AI Fix" on a console error to analyze it</div>
      </div>
    );
  }

  return (
    <div style={{
      backgroundColor: '#1e1e1e',
      borderTop: '1px solid #333',
      fontSize: '13px',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 12px',
        borderBottom: '1px solid #333',
        backgroundColor: '#252526',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Zap size={14} color="#f59e0b" />
          <span style={{ color: '#cccccc', fontWeight: 600 }}>AI Debug</span>
          {analysis && (
            <span style={{
              padding: '1px 6px',
              borderRadius: '3px',
              backgroundColor: '#1e3a5f',
              color: '#60a5fa',
              fontSize: '11px',
            }}>
              {analysis.model_used || 'gemini-pro'}
            </span>
          )}
        </div>
        {!analysis && !loading && (
          <button
            onClick={analyzeError}
            style={{
              padding: '4px 12px',
              borderRadius: '4px',
              backgroundColor: '#2563eb',
              color: '#ffffff',
              fontSize: '12px',
              fontWeight: 500,
            }}
          >
            Analyze Error
          </button>
        )}
      </div>

      {/* Error summary */}
      <div style={{
        padding: '8px 12px',
        backgroundColor: 'rgba(239,68,68,0.08)',
        borderBottom: '1px solid #333',
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
          <AlertCircle size={14} color="#ef4444" style={{ marginTop: 2, flexShrink: 0 }} />
          <span style={{ color: '#fca5a5', wordBreak: 'break-word' }}>
            {error.message}
          </span>
        </div>
        {error.file && (
          <div style={{ color: '#666', fontSize: '11px', marginTop: 4, marginLeft: 20 }}>
            {error.file}{error.line ? `:${error.line}` : ''}
          </div>
        )}
      </div>

      {/* Loading state */}
      {loading && (
        <div style={{
          padding: '24px',
          textAlign: 'center',
          color: '#9ca3af',
        }}>
          <Loader2
            size={20}
            style={{ animation: 'spin 1s linear infinite', marginBottom: 8 }}
          />
          <div>Analyzing error with AI...</div>
        </div>
      )}

      {/* API error */}
      {apiError && (
        <div style={{
          padding: '12px',
          margin: '8px 12px',
          backgroundColor: 'rgba(239,68,68,0.12)',
          borderRadius: '4px',
          color: '#fca5a5',
        }}>
          {apiError}
        </div>
      )}

      {/* Analysis results */}
      {analysis && (
        <div style={{ padding: '12px' }}>
          {/* Confidence */}
          <div style={{ marginBottom: 12 }}>
            <ConfidenceGauge confidence={analysis.confidence} />
          </div>

          {/* Root Cause */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ color: '#9ca3af', fontSize: '11px', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Root Cause
            </div>
            <div style={{ color: '#e5e7eb', lineHeight: '20px' }}>
              {analysis.root_cause}
            </div>
          </div>

          {/* Explanation */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ color: '#9ca3af', fontSize: '11px', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Explanation
            </div>
            <div style={{ color: '#d1d5db', lineHeight: '20px' }}>
              {analysis.explanation}
            </div>
          </div>

          {/* Suggested Fix */}
          {analysis.suggested_fix && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ color: '#9ca3af', fontSize: '11px', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Suggested Fix — {analysis.suggested_fix.file}
                {analysis.suggested_fix.line && `:${analysis.suggested_fix.line}`}
              </div>
              <div style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '12px',
                borderRadius: '4px',
                overflow: 'hidden',
              }}>
                {analysis.suggested_fix.before && (
                  <div style={{ padding: '6px 10px', backgroundColor: 'rgba(239,68,68,0.12)', color: '#fca5a5' }}>
                    - {analysis.suggested_fix.before}
                  </div>
                )}
                {analysis.suggested_fix.after && (
                  <div style={{ padding: '6px 10px', backgroundColor: 'rgba(34,197,94,0.12)', color: '#86efac' }}>
                    + {analysis.suggested_fix.after}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Related Issues */}
          {analysis.related_issues.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ color: '#9ca3af', fontSize: '11px', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Related Issues
              </div>
              <ul style={{ margin: 0, paddingLeft: 16, color: '#d1d5db' }}>
                {analysis.related_issues.map((issue, i) => (
                  <li key={i} style={{ marginBottom: 2, lineHeight: '18px' }}>{issue}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: '8px', marginTop: 16 }}>
            {analysis.suggested_fix && (
              <button
                onClick={handleApplyFix}
                disabled={fixApplied}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  padding: '6px 14px',
                  borderRadius: '4px',
                  backgroundColor: fixApplied ? '#166534' : '#22c55e',
                  color: '#ffffff',
                  fontSize: '12px',
                  fontWeight: 600,
                  opacity: fixApplied ? 0.7 : 1,
                }}
              >
                {fixApplied
                  ? <><CheckCircle size={13} /> Applied</>
                  : <><Zap size={13} /> Apply Fix</>
                }
              </button>
            )}
            <button
              onClick={handleSendToChat}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '6px 14px',
                borderRadius: '4px',
                backgroundColor: '#374151',
                color: '#d1d5db',
                fontSize: '12px',
                fontWeight: 500,
              }}
            >
              <MessageSquare size={13} /> Send to Chat
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
