'use client';

import { useState, useEffect, useRef } from 'react';
import { CheckCircle, Square, Terminal, Shield, Database, Pause, Play } from 'lucide-react';

interface AppStatusCardProps {
  appId: string;
  onStop?: () => void;
}

interface AppStatus {
  app_id: string;
  status: string;
  raw?: string;
  error?: string;
}

export function AppStatusCard({ appId, onStop }: AppStatusCardProps) {
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [isConsoleOpen, setIsConsoleOpen] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [isStopping, setIsStopping] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const logsContainerRef = useRef<HTMLDivElement>(null);

  // Poll status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(`/api/v1/deploy/native/${appId}/status`);
        if (res.ok) {
          setStatus(await res.json());
        }
      } catch {
        // Ignore fetch errors
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [appId]);

  // Stream logs
  useEffect(() => {
    if (!isConsoleOpen) return;
    let cancelled = false;

    const streamLogs = async () => {
      try {
        const res = await fetch(`/api/v1/deploy/native/${appId}/logs`);
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              const data = event.data || event;
              if (data.lines) {
                setLogs(prev => {
                  const next = [...prev, ...data.lines];
                  return next.length > 100 ? next.slice(-100) : next;
                });
              }
            } catch { /* skip */ }
          }
        }
      } catch { /* stream ended */ }
    };

    streamLogs();
    return () => { cancelled = true; };
  }, [appId, isConsoleOpen]);

  // Auto-scroll logs
  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  const handleStop = async () => {
    setIsStopping(true);
    try {
      await fetch(`/api/v1/deploy/native/${appId}/stop`, { method: 'POST' });
      setStatus(prev => prev ? { ...prev, status: 'stopped' } : null);
      onStop?.();
    } catch { /* ignore */ }
    setIsStopping(false);
  };

  const isRunning = status?.status === 'running';
  const retentionPolicy: 'scrub' | 'persist' = 'scrub'; // Default; would come from manifest in production

  return (
    <div style={{
      borderRadius: 'var(--radius-lg, 12px)',
      border: '1px solid var(--border-light)',
      backgroundColor: 'var(--bg-secondary)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '16px 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: '1px solid var(--border-light)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '10px',
            height: '10px',
            borderRadius: '50%',
            backgroundColor: isRunning ? '#10b981' : '#6b7280',
          }} />
          <div>
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              {appId}
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
              {isRunning ? 'Running on VOS3' : status?.status || 'Unknown'}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* Retention badge */}
          <span style={{
            padding: '2px 8px',
            borderRadius: '10px',
            fontSize: '10px',
            fontWeight: 600,
            textTransform: 'uppercase',
            backgroundColor: (retentionPolicy as string) === 'persist'
              ? 'rgba(59,130,246,0.1)'
              : 'rgba(107,114,128,0.1)',
            color: (retentionPolicy as string) === 'persist' ? '#3b82f6' : '#6b7280',
          }}>
            {retentionPolicy}
          </span>
          {isRunning && (
            <button
              onClick={handleStop}
              disabled={isStopping}
              style={{
                padding: '6px 12px',
                borderRadius: 'var(--radius-sm, 6px)',
                border: '1px solid rgba(239,68,68,0.3)',
                backgroundColor: 'rgba(239,68,68,0.08)',
                fontSize: '12px',
                fontWeight: 500,
                color: '#ef4444',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              <Square size={12} />
              {isStopping ? 'Stopping...' : 'Stop'}
            </button>
          )}
        </div>
      </div>

      {/* Dual Memory Bars */}
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-light)' }}>
        <div style={{ display: 'flex', gap: '16px' }}>
          {/* Inference Memory (Blue - HW Read-Only) */}
          <div style={{ flex: 1 }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '6px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Shield size={12} style={{ color: '#3b82f6' }} />
                <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                  Inference (HW-RO)
                </span>
              </div>
              <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>0 / 8 MB</span>
            </div>
            <div style={{
              height: '6px',
              borderRadius: '3px',
              backgroundColor: 'rgba(59,130,246,0.1)',
              overflow: 'hidden',
            }}>
              <div style={{
                height: '100%',
                width: '0%',
                backgroundColor: '#3b82f6',
                borderRadius: '3px',
                transition: 'width 0.3s',
              }} />
            </div>
          </div>

          {/* Scratchpad Memory (Amber - R/W) */}
          <div style={{ flex: 1 }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '6px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Database size={12} style={{ color: '#f59e0b' }} />
                <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                  Scratchpad (R/W)
                </span>
              </div>
              <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>0 / 12 MB</span>
            </div>
            <div style={{
              height: '6px',
              borderRadius: '3px',
              backgroundColor: 'rgba(245,158,11,0.1)',
              overflow: 'hidden',
            }}>
              <div style={{
                height: '100%',
                width: '0%',
                backgroundColor: '#f59e0b',
                borderRadius: '3px',
                transition: 'width 0.3s',
              }} />
            </div>
          </div>
        </div>
      </div>

      {/* Live Console Toggle */}
      <button
        onClick={() => setIsConsoleOpen(!isConsoleOpen)}
        style={{
          width: '100%',
          padding: '10px 20px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'transparent',
          border: 'none',
          cursor: 'pointer',
          borderBottom: isConsoleOpen ? '1px solid var(--border-light)' : 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Terminal size={14} style={{ color: 'var(--text-secondary)' }} />
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Live Console
          </span>
          {logs.length > 0 && (
            <span style={{
              padding: '1px 6px',
              borderRadius: '10px',
              fontSize: '10px',
              fontWeight: 600,
              backgroundColor: 'rgba(16,185,129,0.1)',
              color: '#10b981',
            }}>
              {logs.length} lines
            </span>
          )}
        </div>
        {isConsoleOpen && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setAutoScroll(!autoScroll);
            }}
            style={{
              padding: '2px 6px',
              borderRadius: '4px',
              border: '1px solid var(--border-light)',
              backgroundColor: 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '10px',
              color: 'var(--text-tertiary)',
            }}
          >
            {autoScroll ? <Pause size={10} /> : <Play size={10} />}
            {autoScroll ? 'Pause' : 'Resume'}
          </button>
        )}
      </button>

      {/* Console Output */}
      {isConsoleOpen && (
        <div
          ref={logsContainerRef}
          style={{
            height: '200px',
            overflow: 'auto',
            backgroundColor: '#1a1a2e',
            fontFamily: 'monospace',
            fontSize: '12px',
            lineHeight: 1.6,
            padding: '12px 16px',
          }}
        >
          {logs.length === 0 ? (
            <div style={{ color: '#6b7280' }}>Waiting for output...</div>
          ) : (
            logs.map((line, i) => (
              <div key={i} style={{ color: '#10b981', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {line}
              </div>
            ))
          )}
          <div ref={logsEndRef} />
        </div>
      )}
    </div>
  );
}
