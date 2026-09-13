'use client';

import type { CSSProperties, ReactNode } from 'react';

/**
 * Small skeleton primitives for loading states.
 * Uses the project's CSS variables so it adapts to dark mode automatically.
 */

export function SkeletonBlock({
  width,
  height,
  radius = 4,
  delayMs = 0,
  style,
}: {
  width: number | string;
  height: number | string;
  radius?: number | string;
  delayMs?: number;
  style?: CSSProperties;
}) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-block',
        width,
        height,
        backgroundColor: 'var(--bg-hover)',
        borderRadius: radius,
        animation: 'vos-skeleton-pulse 1.4s ease-in-out infinite',
        animationDelay: `${delayMs}ms`,
        ...style,
      }}
    />
  );
}

export function SkeletonStyles() {
  return (
    <style>{`
      @keyframes vos-skeleton-pulse {
        0%, 100% { opacity: 0.55; }
        50% { opacity: 1; }
      }
    `}</style>
  );
}

export function SkeletonRegion({
  label = 'Loading…',
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <div role="status" aria-label={label} aria-live="polite">
      <SkeletonStyles />
      {children}
      <span style={{ position: 'absolute', left: -9999 }}>{label}</span>
    </div>
  );
}
