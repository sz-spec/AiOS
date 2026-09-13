// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// W7.2 — Hardware-aware status badge for model dropdown rows.
//
// Renders one of four badges next to a curated open-weight model option:
//   ● Local Ready          (Green)   — GGUF on disk + size matches
//   ○ Available via LAN    (Grey)    — host can run + LAN peer present
//   ⬇ Download (HTTPS)     (Blue)    — host can run, no peer yet
//   ☁ Cloud Fallback Only  (Orange)  — host VRAM too small, must proxy out
//
// Proprietary cloud models (no `status` field) render no badge.

'use client';

import type React from 'react';

export type ModelStatus =
  | 'local_ready'
  | 'available_lan'
  | 'available_https'
  | 'cloud_fallback_only';

interface ModelStatusBadgeProps {
  status?: ModelStatus;
  size?: 'sm' | 'md';
}

const BADGE_STYLES: Record<ModelStatus, { icon: string; label: string; cls: string }> = {
  local_ready: {
    icon: '●',
    label: 'Local Ready',
    cls: 'text-emerald-300 border-emerald-500/50 bg-emerald-500/10',
  },
  available_lan: {
    icon: '○',
    label: 'Available via LAN',
    cls: 'text-slate-300 border-slate-500/50 bg-slate-500/10',
  },
  available_https: {
    icon: '⬇',
    label: 'Download (HTTPS)',
    cls: 'text-sky-300 border-sky-500/50 bg-sky-500/10',
  },
  cloud_fallback_only: {
    icon: '☁',
    label: 'Cloud Fallback Only',
    cls: 'text-amber-300 border-amber-500/50 bg-amber-500/10',
  },
};

export function ModelStatusBadge({
  status,
  size = 'sm',
}: ModelStatusBadgeProps): React.ReactElement | null {
  if (!status) return null;
  const palette = BADGE_STYLES[status];
  if (!palette) return null;
  const padding = size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs';
  return (
    <span
      data-testid={`model-status-${status}`}
      className={`inline-flex items-center gap-1 rounded-full border font-medium ${padding} ${palette.cls}`}
      title={palette.label}
    >
      <span aria-hidden="true">{palette.icon}</span>
      {palette.label}
    </span>
  );
}

export default ModelStatusBadge;
