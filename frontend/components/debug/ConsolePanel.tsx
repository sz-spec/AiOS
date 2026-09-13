/**
 * ConsolePanel — Phase 3.0 Subsystem 3.2
 * ========================================
 * CLI-style log viewer that displays console output captured from the
 * Sandpack preview iframe. Supports log/error/warn/info with type-based
 * coloring, file:line click navigation, and a filter toolbar.
 */

'use client';

import { useState, useRef, useEffect } from 'react';
import { Trash2, Filter, ChevronDown, ChevronRight, AlertCircle, AlertTriangle, Info, Terminal } from 'lucide-react';
import type { ConsoleEntry } from '@/hooks/useConsoleCapture';

interface ConsolePanelProps {
  entries: ConsoleEntry[];
  onClear: () => void;
  onEntryClick?: (entry: ConsoleEntry) => void;
  onSendToAI?: (entry: ConsoleEntry) => void;
  maxHeight?: number;
}

type FilterType = 'all' | 'error' | 'warn' | 'log' | 'info';

const TYPE_STYLES: Record<string, { color: string; bg: string; icon: typeof AlertCircle }> = {
  error: { color: '#ef4444', bg: 'rgba(239,68,68,0.08)', icon: AlertCircle },
  warn:  { color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', icon: AlertTriangle },
  info:  { color: '#3b82f6', bg: 'rgba(59,130,246,0.08)', icon: Info },
  log:   { color: '#9ca3af', bg: 'transparent', icon: Terminal },
};

export function ConsolePanel({
  entries,
  onClear,
  onEntryClick,
  onSendToAI,
  maxHeight = 300,
}: ConsolePanelProps) {
  const [filter, setFilter] = useState<FilterType>('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries.length]);

  const filtered = filter === 'all'
    ? entries
    : entries.filter((e) => e.type === filter);

  const errorCount = entries.filter((e) => e.type === 'error').length;
  const warnCount = entries.filter((e) => e.type === 'warn').length;

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      borderTop: '1px solid var(--border-light)',
      backgroundColor: '#1e1e1e',
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
      fontSize: '12px',
    }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '4px 8px',
        borderBottom: '1px solid #333',
        backgroundColor: '#252526',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Terminal size={13} color="#9ca3af" />
          <span style={{ color: '#cccccc', fontWeight: 600 }}>Console</span>

          {/* Filter buttons */}
          {(['all', 'error', 'warn', 'log'] as FilterType[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: '2px 8px',
                borderRadius: '3px',
                fontSize: '11px',
                backgroundColor: filter === f ? '#3c3c3c' : 'transparent',
                color: filter === f ? '#ffffff' : '#808080',
                border: filter === f ? '1px solid #555' : '1px solid transparent',
              }}
            >
              {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
              {f === 'error' && errorCount > 0 && (
                <span style={{ marginLeft: 4, color: '#ef4444' }}>{errorCount}</span>
              )}
              {f === 'warn' && warnCount > 0 && (
                <span style={{ marginLeft: 4, color: '#f59e0b' }}>{warnCount}</span>
              )}
            </button>
          ))}
        </div>

        <button
          onClick={onClear}
          title="Clear console"
          style={{
            padding: '4px',
            color: '#808080',
            backgroundColor: 'transparent',
          }}
        >
          <Trash2 size={13} />
        </button>
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        style={{
          maxHeight: `${maxHeight}px`,
          overflowY: 'auto',
          padding: '4px 0',
        }}
      >
        {filtered.length === 0 && (
          <div style={{ padding: '16px', color: '#666', textAlign: 'center' }}>
            No console output
          </div>
        )}

        {filtered.map((entry) => {
          const style = TYPE_STYLES[entry.type] || TYPE_STYLES.log;
          const Icon = style.icon;
          const isExpanded = expanded.has(entry.id);
          const hasStack = !!entry.stackTrace;
          const hasLocation = !!entry.file;

          return (
            <div
              key={entry.id}
              style={{
                padding: '3px 8px',
                borderBottom: '1px solid #2a2a2a',
                backgroundColor: style.bg,
              }}
            >
              <div style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '6px',
              }}>
                {/* Expand arrow (if has stack trace) */}
                {hasStack ? (
                  <button
                    onClick={() => toggleExpand(entry.id)}
                    style={{
                      padding: 0,
                      marginTop: 1,
                      color: '#808080',
                      backgroundColor: 'transparent',
                      flexShrink: 0,
                    }}
                  >
                    {isExpanded
                      ? <ChevronDown size={12} />
                      : <ChevronRight size={12} />
                    }
                  </button>
                ) : (
                  <span style={{ width: 12, flexShrink: 0 }} />
                )}

                {/* Type icon */}
                <Icon size={13} color={style.color} style={{ marginTop: 1, flexShrink: 0 }} />

                {/* Message */}
                <span
                  style={{
                    color: style.color,
                    wordBreak: 'break-word',
                    flex: 1,
                    lineHeight: '18px',
                  }}
                >
                  {entry.message}
                </span>

                {/* File:line link */}
                {hasLocation && (
                  <button
                    onClick={() => onEntryClick?.(entry)}
                    style={{
                      color: '#4fc1ff',
                      backgroundColor: 'transparent',
                      fontSize: '11px',
                      flexShrink: 0,
                      textDecoration: 'underline',
                    }}
                  >
                    {entry.file}:{entry.line}
                  </button>
                )}

                {/* Send to AI button for errors */}
                {entry.type === 'error' && onSendToAI && (
                  <button
                    onClick={() => onSendToAI(entry)}
                    title="Send to AI for analysis"
                    style={{
                      padding: '1px 6px',
                      fontSize: '10px',
                      borderRadius: '3px',
                      backgroundColor: '#2563eb',
                      color: '#ffffff',
                      flexShrink: 0,
                    }}
                  >
                    AI Fix
                  </button>
                )}
              </div>

              {/* Expanded stack trace */}
              {isExpanded && entry.stackTrace && (
                <pre style={{
                  margin: '4px 0 2px 30px',
                  padding: '4px 8px',
                  backgroundColor: '#1a1a1a',
                  borderRadius: '3px',
                  color: '#888',
                  fontSize: '11px',
                  whiteSpace: 'pre-wrap',
                  lineHeight: '16px',
                }}>
                  {entry.stackTrace}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
