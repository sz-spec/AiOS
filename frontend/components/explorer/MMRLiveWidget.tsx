'use client';

import { useState, useEffect, useRef } from 'react';
import { useTransparency } from '@/hooks/useTransparency';

interface HistoryEntry {
  root: string;
  leaves: number;
  ts: Date;
}

export function MMRLiveWidget() {
  const { data, loading, lastUpdated } = useTransparency(3000);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [pulse, setPulse] = useState(false);
  const prevRootRef = useRef<string | null>(null);

  // Track root changes → pulse animation
  useEffect(() => {
    if (!data?.root || !data.fresh) return;
    if (data.root !== prevRootRef.current) {
      prevRootRef.current = data.root;
      setPulse(true);
      setTimeout(() => setPulse(false), 900);
      setHistory((h) => [
        { root: data.root!, leaves: data.leaves ?? 0, ts: new Date() },
        ...h.slice(0, 6),
      ]);
    }
  }, [data?.root, data?.fresh, data?.leaves]);

  const isLive = !!data?.fresh && !!data?.root;

  if (loading) {
    return (
      <div style={{ height: '320px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '14px' }}>
        Connecting to kernel…
      </div>
    );
  }

  return (
    <div>
      {/* Live root hash — the centerpiece */}
      <div
        style={{
          padding: '28px',
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-lg)',
          border: `1px solid ${pulse ? '#16a34a' : isLive ? '#bbf7d0' : 'var(--border-light)'}`,
          marginBottom: '20px',
          transition: 'border-color 0.3s ease',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Pulse glow */}
        {pulse && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: 'radial-gradient(ellipse at center, rgba(22,163,74,0.12) 0%, transparent 70%)',
              animation: 'mmrPulse 0.9s ease-out forwards',
              pointerEvents: 'none',
            }}
          />
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
          <span
            style={{
              display: 'inline-block',
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              backgroundColor: isLive ? '#16a34a' : '#6b7280',
              boxShadow: isLive ? '0 0 8px #16a34a' : 'none',
              animation: isLive ? 'liveDot 2s ease-in-out infinite' : 'none',
            }}
          />
          <span style={{ fontSize: '13px', fontWeight: 600, color: isLive ? '#16a34a' : 'var(--text-tertiary)', letterSpacing: '0.05em' }}>
            {isLive ? 'KERNEL LIVE' : 'KERNEL OFFLINE'}
          </span>
          {lastUpdated && (
            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
              {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>

        <div style={{ marginBottom: '8px', fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          SHA-256 MMR Root Hash
        </div>
        {data?.root ? (
          <div
            style={{
              fontFamily: 'monospace',
              fontSize: '15px',
              color: pulse ? '#4ade80' : '#16a34a',
              letterSpacing: '0.04em',
              wordBreak: 'break-all',
              lineHeight: 1.5,
              transition: 'color 0.3s ease',
            }}
          >
            {data.root}
          </div>
        ) : (
          <div style={{ fontFamily: 'monospace', fontSize: '14px', color: 'var(--text-tertiary)' }}>
            {data?.error ?? '—'}
          </div>
        )}

        <div style={{ marginTop: '16px', display: 'flex', gap: '24px' }}>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>Syscall Events</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
              {data?.leaves != null ? data.leaves.toLocaleString() : '—'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>Algorithm</div>
            <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>SHA-256 MMR · RDSEED-bound</div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>Tamper cost</div>
            <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>2¹²⁸ operations</div>
          </div>
        </div>
      </div>

      {/* EU AI Act compliance strip */}
      <div style={{
        display: 'flex',
        gap: '8px',
        marginBottom: '24px',
        flexWrap: 'wrap',
      }}>
        {[
          'EU AI Act Art. 12 ✓',
          'Automatic logging ✓',
          'Tamper-evident ✓',
          '6-month retention ✓',
          'O(log N) inclusion proof ✓',
        ].map((label) => (
          <span
            key={label}
            style={{
              padding: '4px 10px',
              backgroundColor: 'rgba(22,163,74,0.08)',
              border: '1px solid rgba(22,163,74,0.25)',
              borderRadius: '9999px',
              fontSize: '11px',
              fontWeight: 500,
              color: '#16a34a',
              letterSpacing: '0.02em',
            }}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Root hash history — "the proof it's advancing" */}
      {history.length > 0 && (
        <div>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px', letterSpacing: '0.04em' }}>
            PROOF HISTORY — Root Hash Evolution
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {history.map((entry, i) => (
              <div
                key={entry.ts.getTime()}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  padding: '10px 14px',
                  backgroundColor: i === 0 ? 'rgba(22,163,74,0.06)' : 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${i === 0 ? 'rgba(22,163,74,0.2)' : 'var(--border-light)'}`,
                  opacity: 1 - i * 0.12,
                  animation: i === 0 ? 'slideIn 0.35s ease' : 'none',
                }}
              >
                <span style={{ fontFamily: 'monospace', fontSize: '10px', color: 'var(--text-tertiary)', minWidth: '60px', flexShrink: 0 }}>
                  {entry.ts.toLocaleTimeString()}
                </span>
                <span
                  style={{
                    fontFamily: 'monospace',
                    fontSize: '12px',
                    color: i === 0 ? '#16a34a' : 'var(--text-secondary)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    flex: 1,
                  }}
                >
                  {entry.root}
                </span>
                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                  {entry.leaves.toLocaleString()} events
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {!isLive && !loading && (
        <div style={{
          padding: '20px',
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-light)',
          textAlign: 'center',
          color: 'var(--text-tertiary)',
          fontSize: '14px',
        }}>
          Kernel offline — start QEMU to see live MMR data
        </div>
      )}

      <style>{`
        @keyframes mmrPulse {
          0%   { opacity: 1; transform: scale(1); }
          50%  { opacity: 0.6; }
          100% { opacity: 0; transform: scale(1.05); }
        }
        @keyframes liveDot {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.4; }
        }
        @keyframes slideIn {
          from { opacity: 0; transform: translateY(-6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
