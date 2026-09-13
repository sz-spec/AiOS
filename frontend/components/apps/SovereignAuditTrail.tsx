// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P5.1 — Sovereign Audit Trail panel.
//
// Terminal-styled live view of the `securityAuditLog` SQLite
// table. Polls `/api/system/audit-logs` every few seconds and
// highlights blocked hacks:
//   - path_traversal_attempt  → crimson
//   - scope_violation         → amber
//   - app_isolated            → orange
//   - app_status_change       → neutral
//   - scope_toggled           → indigo (operator-driven, not a hack)
//
// The directive's worked example renders as e.g.:
//   "App 'Notes' attempted to read '/etc/passwd' — BLOCKED"
// (We replace the literal app id with the friendly name when we
// can resolve it from a small in-memory map populated by the
// dashboard page.)

'use client';

import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '@clerk/nextjs';

import { apiFetch } from '@/lib/api-client';

interface AuditRow {
  id: string;
  timestamp: number;
  kind: string;
  app_id: string | null;
  scope: string | null;
  reason: string | null;
  details: Record<string, unknown>;
}

interface AuditResponse {
  rows: AuditRow[];
  count: number;
  limit: number;
  offset: number;
  filters: { app_id: string | null; kind: string | null };
}

interface SovereignAuditTrailProps {
  /** Optional filter — limit rows to one app id. */
  appId: string | null;
  /** Polling interval (ms). Default 4000. */
  intervalMs?: number;
}

const KIND_PALETTE: Record<
  string,
  { label: string; cls: string }
> = {
  path_traversal_attempt: {
    label: 'TRAVERSAL',
    cls: 'border-red-500/50 bg-red-500/10 text-red-300',
  },
  scope_violation: {
    label: 'SCOPE DENIED',
    cls: 'border-amber-500/50 bg-amber-500/10 text-amber-300',
  },
  app_isolated: {
    label: 'ISOLATED',
    cls: 'border-orange-500/50 bg-orange-500/10 text-orange-300',
  },
  app_not_found: {
    label: 'UNKNOWN APP',
    cls: 'border-rose-500/50 bg-rose-500/10 text-rose-300',
  },
  app_status_change: {
    label: 'STATUS',
    cls: 'border-slate-500/50 bg-slate-500/10 text-slate-300',
  },
  scope_toggled: {
    label: 'TOGGLED',
    cls: 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300',
  },
};

function formatTimestamp(ms: number): string {
  try {
    return new Date(ms).toISOString().replace('T', ' ').slice(0, 19);
  } catch {
    return String(ms);
  }
}

function renderLine(row: AuditRow): string {
  // Build a human-readable line; mirrors the directive's example
  // shape "App 'Notes' attempted to read '/etc/passwd' — BLOCKED".
  const app = row.app_id ?? 'unknown-app';
  switch (row.kind) {
    case 'path_traversal_attempt': {
      const path =
        (row.details?.requested_path as string | undefined) ??
        (row.details?.resolved_path as string | undefined) ??
        '<unknown>';
      return `App ${app} attempted to access "${path}" — BLOCKED (${row.reason ?? 'no-reason'})`;
    }
    case 'scope_violation':
      return `App ${app} requested scope "${row.scope}" — DENIED (${row.reason ?? 'no-reason'})`;
    case 'app_isolated':
      return `App ${app} is isolated — gate denying all checks (${row.reason ?? 'no-reason'})`;
    case 'app_not_found':
      return `Unknown app id "${app}" tried to use scope "${row.scope}"`;
    case 'app_status_change':
      return `App ${app} status → ${
        (row.details?.new_status as string | undefined) ?? '?'
      } (${row.reason ?? 'operator'})`;
    case 'scope_toggled':
      return `Operator toggled "${row.scope}" on app ${app} (granted=${
        (row.details?.granted as boolean | undefined) ?? '?'
      })`;
    default:
      return `${row.kind} ${app} scope=${row.scope ?? '-'} reason=${row.reason ?? '-'}`;
  }
}

export function SovereignAuditTrail({
  appId,
  intervalMs = 4000,
}: SovereignAuditTrailProps): React.ReactElement {
  const { getToken } = useAuth();
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchRows = useCallback(async () => {
    setError(null);
    try {
      const qs = new URLSearchParams({ limit: '100' });
      if (appId) qs.set('app_id', appId);
      const res = await apiFetch(
        getToken,
        `/api/system/audit-logs?${qs.toString()}`,
        { method: 'GET' },
      );
      if (!res.ok) {
        throw new Error(`GET /api/system/audit-logs → ${res.status}`);
      }
      const body = (await res.json()) as AuditResponse;
      setRows(body.rows ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'fetch failed');
    }
  }, [getToken, appId]);

  useEffect(() => {
    fetchRows();
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }
    if (!paused) {
      intervalRef.current = setInterval(fetchRows, intervalMs);
    }
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchRows, intervalMs, paused]);

  return (
    <div className="rounded-lg border border-slate-800 bg-black/60 font-mono text-xs">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <div className="flex items-center gap-2 text-slate-300">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />
          Sovereign Audit Trail
          <span className="text-slate-500">
            {appId ? `(filtered to app ${appId.slice(0, 8)}…)` : '(all apps)'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-slate-800"
          >
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            type="button"
            onClick={fetchRows}
            className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-slate-800"
          >
            Refresh
          </button>
        </div>
      </div>

      {error ? (
        <div className="border-b border-slate-800 px-4 py-2 text-red-300">
          {error}
        </div>
      ) : null}

      <div className="max-h-80 overflow-y-auto px-4 py-3">
        {rows.length === 0 ? (
          <div className="text-slate-500">
            No security events recorded yet.
          </div>
        ) : (
          <ul className="space-y-1">
            {rows.map((row) => {
              const palette =
                KIND_PALETTE[row.kind] ?? {
                  label: row.kind.toUpperCase(),
                  cls: 'border-slate-600/50 bg-slate-500/10 text-slate-300',
                };
              return (
                <li key={row.id} className="flex items-start gap-3">
                  <span className="shrink-0 text-slate-500">
                    {formatTimestamp(row.timestamp)}
                  </span>
                  <span
                    className={`shrink-0 rounded border px-1.5 py-0 text-[10px] uppercase tracking-wide ${palette.cls}`}
                  >
                    {palette.label}
                  </span>
                  <span className="text-slate-200">{renderLine(row)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
