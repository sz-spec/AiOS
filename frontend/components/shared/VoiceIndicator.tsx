'use client';

import React from 'react';

interface VoiceIndicatorProps {
  isActive: boolean;
  size?: 'small' | 'medium' | 'large';
  variant?: 'waveform' | 'pulse' | 'dots';
  color?: string;
  className?: string;
}

export function VoiceIndicator({
  isActive,
  size = 'medium',
  variant = 'waveform',
  color = 'var(--error, #dc2626)',
  className = '',
}: VoiceIndicatorProps) {
  if (!isActive) return null;

  const sizeConfig = {
    small: { height: 16, barWidth: 2, gap: 2 },
    medium: { height: 24, barWidth: 3, gap: 3 },
    large: { height: 32, barWidth: 4, gap: 4 },
  };

  const config = sizeConfig[size];

  if (variant === 'waveform') {
    return (
      <div
        className={className}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: `${config.gap}px`,
          height: config.height,
        }}
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <div
            key={i}
            style={{
              width: config.barWidth,
              height: '40%',
              backgroundColor: color,
              borderRadius: config.barWidth / 2,
              animation: `voice-bar ${0.6 + i * 0.1}s ease-in-out infinite`,
              animationDelay: `${i * 0.1}s`,
            }}
          />
        ))}
      </div>
    );
  }

  if (variant === 'pulse') {
    return (
      <div
        className={className}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          width: config.height,
          height: config.height,
        }}
      >
        <div
          style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            borderRadius: '50%',
            backgroundColor: color,
            opacity: 0.3,
            animation: 'voice-pulse-ring 1.5s ease-in-out infinite',
          }}
        />
        <div
          style={{
            width: '50%',
            height: '50%',
            borderRadius: '50%',
            backgroundColor: color,
          }}
        />
      </div>
    );
  }

  if (variant === 'dots') {
    return (
      <div
        className={className}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: `${config.gap}px`,
        }}
      >
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              width: config.barWidth * 2,
              height: config.barWidth * 2,
              borderRadius: '50%',
              backgroundColor: color,
              animation: 'voice-dot 1s ease-in-out infinite',
              animationDelay: `${i * 0.2}s`,
            }}
          />
        ))}
      </div>
    );
  }

  return null;
}

// Compact listening indicator with label
interface VoiceListeningBadgeProps {
  isListening: boolean;
  language?: string;
  onStop?: () => void;
}

export function VoiceListeningBadge({
  isListening,
  language,
  onStop,
}: VoiceListeningBadgeProps) {
  if (!isListening) return null;

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        padding: '6px 12px',
        backgroundColor: 'rgba(220, 38, 38, 0.1)',
        border: '1px solid rgba(220, 38, 38, 0.3)',
        borderRadius: '20px',
        fontSize: '13px',
        color: 'var(--error, #dc2626)',
      }}
    >
      <VoiceIndicator isActive size="small" variant="dots" />
      <span>Listening{language ? ` (${language})` : ''}...</span>
      {onStop && (
        <button
          type="button"
          onClick={onStop}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 18,
            height: 18,
            border: 'none',
            borderRadius: '50%',
            backgroundColor: 'var(--error, #dc2626)',
            color: 'white',
            cursor: 'pointer',
            fontSize: '10px',
            lineHeight: 1,
          }}
        >
          <svg width={10} height={10} viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="1" />
          </svg>
        </button>
      )}
    </div>
  );
}

export default VoiceIndicator;
