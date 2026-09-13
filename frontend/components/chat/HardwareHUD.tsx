'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';

interface PressureData {
  fresh: boolean;
  pressure: number;
  congested: number;
  hp_used: number;
  hp_total: number;
  timeouts: number;
}

const ENTER_THRESHOLD = 0.85;
const EXIT_THRESHOLD  = 0.75;
const ALPHA           = 0.30;

export function HardwareHUD() {
  const { getToken } = useAuth();
  const [data, setData]       = useState<PressureData | null>(null);
  const [ewma, setEwma]       = useState(0);
  const [degraded, setDegraded] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const prevDegradedRef = useRef(false);
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const poll = useCallback(async () => {
    try {
      const token = await getTokenRef.current();
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await window.fetch('/api/kernel/hardware/pressure', { headers });
      if (!res.ok) return;
      const json: PressureData = await res.json();
      setData(json);

      // EWMA update + hysteresis — mirrors backend/src/efficiency/router.py
      setEwma((prev) => {
        const next = ALPHA * (json.pressure ?? 0) + (1 - ALPHA) * prev;
        setDegraded((prevDeg) => {
          const entering = !prevDeg && next > ENTER_THRESHOLD;
          const exiting  = prevDeg && next < EXIT_THRESHOLD;
          if (entering || exiting) {
            setTransitioning(true);
            setTimeout(() => setTransitioning(false), 600);
          }
          if (entering) return true;
          if (exiting)  return false;
          return prevDeg;
        });
        return next;
      });
    } catch {
      // Kernel offline — silent
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [poll]);

  // Don't render if kernel offline or still loading
  if (!data?.fresh) return null;

  const pct    = Math.round((data.pressure ?? 0) * 100);
  const barPct = Math.min(100, pct);
  const barColor = degraded
    ? '#d97706'
    : pct > 70 ? '#eab308'
    : '#16a34a';

  return (
    <div
      style={{
        position: 'fixed',
        bottom: '96px',
        left: '24px',
        zIndex: 30,
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        opacity: transitioning ? 0.7 : 1,
        transition: 'opacity 0.3s ease',
        pointerEvents: 'none',
      }}
      aria-label="Hardware pressure HUD"
    >
      {/* Efficiency Mode badge — slides in/out smoothly */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 10px',
          backgroundColor: degraded ? 'rgba(217,119,6,0.15)' : 'rgba(22,163,74,0.1)',
          border: `1px solid ${degraded ? 'rgba(217,119,6,0.4)' : 'rgba(22,163,74,0.25)'}`,
          borderRadius: '9999px',
          backdropFilter: 'blur(8px)',
          transition: 'all 0.5s ease',
          maxWidth: transitioning ? '160px' : '160px',
          overflow: 'hidden',
        }}
      >
        <span
          style={{
            width: '5px',
            height: '5px',
            borderRadius: '50%',
            backgroundColor: degraded ? '#d97706' : '#16a34a',
            flexShrink: 0,
            transition: 'background-color 0.5s ease',
            animation: 'hudDot 2s ease-in-out infinite',
          }}
        />
        <span
          style={{
            fontSize: '10px',
            fontWeight: 600,
            color: degraded ? '#d97706' : '#16a34a',
            letterSpacing: '0.06em',
            whiteSpace: 'nowrap',
            transition: 'color 0.5s ease',
          }}
        >
          {degraded ? 'EFFICIENCY MODE' : 'FULL QUALITY'}
        </span>
      </div>

      {/* Pressure gauge widget */}
      <div
        style={{
          padding: '8px 12px',
          backgroundColor: 'rgba(0,0,0,0.55)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: '10px',
          backdropFilter: 'blur(12px)',
          minWidth: '140px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '5px' }}>
          <span style={{ fontSize: '9px', fontWeight: 600, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.08em' }}>
            KERNEL PRESSURE
          </span>
          <span style={{ fontSize: '11px', fontWeight: 700, color: barColor, fontVariantNumeric: 'tabular-nums', transition: 'color 0.4s ease' }}>
            {pct}%
          </span>
        </div>

        {/* Bar */}
        <div style={{ height: '3px', backgroundColor: 'rgba(255,255,255,0.1)', borderRadius: '2px', overflow: 'hidden' }}>
          <div
            style={{
              height: '100%',
              width: `${barPct}%`,
              backgroundColor: barColor,
              borderRadius: '2px',
              transition: 'width 0.8s ease, background-color 0.5s ease',
            }}
          />
        </div>

        {/* EWMA + model */}
        <div style={{ marginTop: '5px', display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '9px', color: 'rgba(255,255,255,0.3)' }}>
            EWMA {(ewma * 100).toFixed(0)}%
          </span>
          <span style={{ fontSize: '9px', color: 'rgba(255,255,255,0.3)' }}>
            {degraded ? 'haiku' : 'opus/sonnet'}
          </span>
        </div>
      </div>

      <style>{`
        @keyframes hudDot {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
