'use client';

import React from 'react';
import type { BMADPhase, PhaseStatus, PhaseState } from '@/hooks/useBMAD';

interface PhaseTimelineProps {
  phases: Record<BMADPhase, PhaseState>;
  currentPhase: BMADPhase;
  onPhaseClick?: (phase: BMADPhase) => void;
}

const PHASE_ORDER: BMADPhase[] = [
  'ideation',
  'discovery',
  'planning',
  'design',
  'development',
  'testing',
  'review',
  'deployment',
  'operations',
];

const PHASE_LABELS: Record<BMADPhase, string> = {
  ideation: 'Ideation',
  discovery: 'Discovery',
  planning: 'Planning',
  design: 'Design',
  development: 'Development',
  testing: 'Testing',
  review: 'Review',
  deployment: 'Deploy',
  operations: 'Operations',
};

const PHASE_ICONS: Record<BMADPhase, string> = {
  ideation: '\u{1F4A1}',
  discovery: '\u{1F50D}',
  planning: '\u{1F4CB}',
  design: '\u{1F3A8}',
  development: '\u{26A1}',
  testing: '\u{1F9EA}',
  review: '\u{1F440}',
  deployment: '\u{1F680}',
  operations: '\u{1F4E1}',
};

const STATUS_COLORS: Record<PhaseStatus, { bg: string; glow: string; text: string }> = {
  pending: { bg: 'var(--bg-tertiary)', glow: 'none', text: 'var(--text-tertiary)' },
  in_progress: { bg: '#3b82f6', glow: '0 0 12px rgba(59,130,246,0.4)', text: '#3b82f6' },
  awaiting_approval: { bg: '#f59e0b', glow: '0 0 12px rgba(245,158,11,0.4)', text: '#f59e0b' },
  approved: { bg: '#10b981', glow: '0 0 12px rgba(16,185,129,0.3)', text: '#10b981' },
  rejected: { bg: '#ef4444', glow: '0 0 12px rgba(239,68,68,0.3)', text: '#ef4444' },
  completed: { bg: '#10b981', glow: '0 0 12px rgba(16,185,129,0.3)', text: '#10b981' },
  skipped: { bg: 'var(--bg-tertiary)', glow: 'none', text: 'var(--text-tertiary)' },
};

export function PhaseTimeline({ phases, currentPhase, onPhaseClick }: PhaseTimelineProps) {
  const currentIndex = PHASE_ORDER.indexOf(currentPhase);
  const progressPercent = (currentIndex / (PHASE_ORDER.length - 1)) * 100;

  return (
    <div style={{ width: '100%', padding: '8px 0' }}>
      {/* Desktop Timeline */}
      <div style={{ position: 'relative' }}>
        {/* Progress Track */}
        <div style={{
          position: 'absolute',
          top: '19px',
          left: '5%',
          right: '5%',
          height: '3px',
          backgroundColor: 'var(--border-light)',
          borderRadius: '2px',
          zIndex: 0,
        }}>
          <div style={{
            height: '100%',
            width: `${progressPercent}%`,
            background: 'linear-gradient(90deg, #3b82f6, #8b5cf6, #d97706)',
            borderRadius: '2px',
            transition: 'width 0.7s ease-out',
          }} />
        </div>

        {/* Phase Nodes */}
        <div style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
        }}>
          {PHASE_ORDER.map((phase, index) => {
            const phaseState = phases[phase];
            const status = phaseState?.status || 'pending';
            const colors = STATUS_COLORS[status];
            const isActive = phase === currentPhase;
            const isPast = index < currentIndex;
            const isCompleted = status === 'completed' || isPast;
            const isAwaiting = status === 'awaiting_approval';

            const circleBg = isCompleted
              ? '#10b981'
              : isActive
                ? '#3b82f6'
                : isAwaiting
                  ? '#f59e0b'
                  : 'var(--bg-secondary)';

            const circleBorder = isCompleted || isActive || isAwaiting
              ? 'none'
              : '2px solid var(--border-light)';

            const circleGlow = isActive
              ? '0 0 0 4px rgba(59,130,246,0.15), 0 4px 12px rgba(59,130,246,0.3)'
              : isAwaiting
                ? '0 0 0 4px rgba(245,158,11,0.15), 0 4px 12px rgba(245,158,11,0.3)'
                : isCompleted
                  ? '0 2px 8px rgba(16,185,129,0.2)'
                  : 'none';

            const labelColor = isActive
              ? '#3b82f6'
              : isCompleted
                ? '#10b981'
                : 'var(--text-tertiary)';

            return (
              <div
                key={phase}
                onClick={() => onPhaseClick?.(phase)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  cursor: onPhaseClick ? 'pointer' : 'default',
                  width: `${100 / PHASE_ORDER.length}%`,
                }}
              >
                {/* Phase Circle */}
                <div style={{
                  position: 'relative',
                  width: '38px',
                  height: '38px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: circleBg,
                  border: circleBorder,
                  boxShadow: circleGlow,
                  zIndex: 1,
                  transform: isActive ? 'scale(1.15)' : 'scale(1)',
                  transition: 'all 0.3s ease',
                }}>
                  {isCompleted ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <span style={{
                      fontSize: '14px',
                      filter: (!isActive && !isAwaiting) ? 'grayscale(1) opacity(0.5)' : 'none',
                    }}>
                      {PHASE_ICONS[phase]}
                    </span>
                  )}

                  {/* Awaiting Approval Badge */}
                  {isAwaiting && (
                    <span style={{
                      position: 'absolute',
                      top: '-2px',
                      right: '-2px',
                      width: '14px',
                      height: '14px',
                      backgroundColor: '#f59e0b',
                      borderRadius: '50%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '8px',
                      fontWeight: 700,
                      color: 'white',
                      border: '2px solid var(--bg-primary)',
                    }}>!</span>
                  )}
                </div>

                {/* Phase Label */}
                <span style={{
                  marginTop: '8px',
                  fontSize: '11px',
                  fontWeight: isActive ? 600 : 500,
                  color: labelColor,
                  textAlign: 'center',
                  lineHeight: 1.2,
                  transition: 'color 0.2s',
                }}>
                  {PHASE_LABELS[phase]}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Pulse animation for awaiting_approval */}
      <style>{`
        @keyframes pulse-glow {
          0%, 100% { box-shadow: 0 0 0 4px rgba(245,158,11,0.15); }
          50% { box-shadow: 0 0 0 8px rgba(245,158,11,0.08); }
        }
      `}</style>
    </div>
  );
}

export default PhaseTimeline;
