// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// W6.3 — SovereignStatus badge.
//
// Subtle Tahoe-style indicator that surfaces the backend's locality
// posture in the navigation chrome. Three visual states:
//
//   1. Loading           — skeleton dot, no label
//   2. Sovereign         — 🛡️ Sovereign Mode  (local-first + air-gap)
//   3. Local-only        — 🛡️ Local Mode      (local-first + cloud keys present)
//   4. Cloud             — ☁ Cloud Mode       (any non-local-first)
//
// "Sovereign" and "Local Mode" differ deliberately:
//   - Sovereign: operator pinned local-first AND no cloud creds set.
//     This is the "100% air-gapped" claim.
//   - Local Mode: local-first preference but cloud keys exist on disk.
//     Egress could happen if a future config change flips the dispatcher;
//     showing a distinct label avoids overstating sovereignty.
//
// Glassmorphic Tahoe styling: translucent surface + soft border, no
// hard color, hover reveals tooltip with database + llm_provider.

'use client';

import { useLocality } from '@/context/LocalityContext';

const SOVEREIGN_TOOLTIP =
  'Backend is running in sovereign air-gap mode. ' +
  'All data stays on this machine; no cloud egress.';
const LOCAL_TOOLTIP =
  'Backend pinned to local-first preference, but cloud credentials are ' +
  'configured. Requests route locally unless the dispatcher changes.';
const CLOUD_TOOLTIP =
  'Backend running in cloud-first / auto mode. Requests route to ' +
  'remote providers (Convex, OpenAI, Anthropic, ...) as the SmartRouter ' +
  'selects.';

export function SovereignStatus() {
  const { status, isLoading, isLocalFirst, isSovereign } = useLocality();

  // Loading: render a low-noise skeleton so the layout doesn't shift
  // when the first fetch resolves.
  if (isLoading && status === null) {
    return (
      <div
        aria-label="System status loading"
        title="Checking system locality…"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 10px',
          borderRadius: '999px',
          fontSize: '11px',
          fontWeight: 500,
          letterSpacing: '0.02em',
          color: 'var(--text-secondary, #888)',
          background: 'rgba(255, 255, 255, 0.04)',
          border: '1px solid rgba(255, 255, 255, 0.08)',
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
        }}
      >
        <span
          style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            background: 'var(--text-secondary, #888)',
            opacity: 0.5,
          }}
        />
        <span>…</span>
      </div>
    );
  }

  // Decide the visual state.
  let icon: string;
  let label: string;
  let tooltip: string;
  let accent: { fg: string; bg: string; border: string; glow: string };

  if (isSovereign) {
    icon = '🛡️';
    label = 'Sovereign Mode';
    tooltip = SOVEREIGN_TOOLTIP;
    accent = {
      // Subtle emerald for the active "fortress" state.
      fg: 'rgba(110, 231, 183, 0.95)',
      bg: 'rgba(16, 185, 129, 0.08)',
      border: 'rgba(110, 231, 183, 0.28)',
      glow: '0 0 10px rgba(16, 185, 129, 0.15)',
    };
  } else if (isLocalFirst) {
    icon = '🛡️';
    label = 'Local Mode';
    tooltip = LOCAL_TOOLTIP;
    accent = {
      fg: 'rgba(186, 230, 253, 0.95)',
      bg: 'rgba(56, 189, 248, 0.08)',
      border: 'rgba(186, 230, 253, 0.28)',
      glow: 'none',
    };
  } else {
    icon = '☁';
    label = 'Cloud Mode';
    tooltip = CLOUD_TOOLTIP;
    accent = {
      fg: 'var(--text-secondary, #aaa)',
      bg: 'rgba(255, 255, 255, 0.04)',
      border: 'rgba(255, 255, 255, 0.08)',
      glow: 'none',
    };
  }

  const fullTooltip = status
    ? `${tooltip}\n\nDatabase: ${status.database}\nLLM provider: ${status.llm_provider}\nVersion: ${status.version}`
    : tooltip;

  return (
    <div
      role="status"
      aria-label={label}
      title={fullTooltip}
      data-locality={status?.locality ?? 'unknown'}
      data-sovereign={isSovereign ? '1' : '0'}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        padding: '4px 10px',
        borderRadius: '999px',
        fontSize: '11px',
        fontWeight: 500,
        letterSpacing: '0.02em',
        color: accent.fg,
        background: accent.bg,
        border: `1px solid ${accent.border}`,
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        boxShadow: accent.glow,
        userSelect: 'none',
        whiteSpace: 'nowrap',
        cursor: 'default',
      }}
    >
      <span style={{ fontSize: '12px', lineHeight: 1 }}>{icon}</span>
      <span>{label}</span>
    </div>
  );
}
