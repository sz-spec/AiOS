// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// v20.6.2 — centralized error bus for the frontend.
//
// Closes docs/audit/300_INTEGRATION_FAILURES_REPORT.md §3.3: dozens of
// `.catch(() => {})` and bare `catch {}` blocks across hooks/* swallowed
// network failures silently. The user saw "loading..." forever or stale
// data with no indication anything went wrong.
//
// Pattern: every hook that wants to surface a recoverable error calls
//   reportError({ scope: 'chat.endSession', error: e, severity: 'warning' })
// instead of console.error or silent swallow. UI subscribes via
// useErrors() and renders toasts / banners as appropriate.
//
// Zero React dependency in the BUS itself — it's a plain pub/sub. The
// useErrors() hook subscribes to it; you can also use it from non-hook
// code (services, fetch wrappers, etc).

export type ErrorSeverity = 'info' | 'warning' | 'error' | 'critical';

export interface VOS3ErrorReport {
  /** Stable identifier for the call site, e.g. 'chat.endSession'. Used for de-dupe + telemetry. */
  scope: string;
  /** The error itself (or string description). */
  error: unknown;
  /** Default 'error'. 'critical' surfaces a blocking modal; 'info' is silent in the toast UI. */
  severity?: ErrorSeverity;
  /** Optional user-facing override; defaults to a sensible message derived from `scope`. */
  userMessage?: string;
  /** When was it raised (ms). */
  timestamp?: number;
}

type Listener = (report: Required<VOS3ErrorReport>) => void;

const listeners = new Set<Listener>();
const recent: Required<VOS3ErrorReport>[] = [];
const RECENT_CAP = 50;
// Per-scope dedupe window: don't refire the same scope within this many ms.
const DEDUPE_WINDOW_MS = 3_000;
const lastFiredByScope = new Map<string, number>();

function _userMessage(scope: string, err: unknown): string {
  const base = scope.split('.')[0];
  switch (base) {
    case 'chat':       return 'Chat sync hit a snag. Retrying.';
    case 'auth':       return 'Authentication issue. Please reload if it persists.';
    case 'memory':     return 'Memory store is temporarily unavailable.';
    case 'bmad':       return 'BMAD workflow lost connection. Reconnecting…';
    case 'codegen':    return 'Code generation request failed.';
    case 'agents':     return 'Agent task failed to dispatch.';
    case 'ws':         return 'Connection interrupted. Reconnecting…';
    case 'convex':     return 'Database sync was interrupted.';
    default:           return 'Something went wrong. The action did not complete.';
  }
}

export function reportError(report: VOS3ErrorReport): void {
  const now = Date.now();
  const last = lastFiredByScope.get(report.scope) ?? 0;
  if (now - last < DEDUPE_WINDOW_MS) {
    return; // suppress duplicate within window
  }
  lastFiredByScope.set(report.scope, now);

  const filled: Required<VOS3ErrorReport> = {
    scope: report.scope,
    error: report.error,
    severity: report.severity ?? 'error',
    userMessage: report.userMessage ?? _userMessage(report.scope, report.error),
    timestamp: report.timestamp ?? now,
  };

  recent.push(filled);
  while (recent.length > RECENT_CAP) recent.shift();

  // Always log to console for dev visibility — but ONCE per dedupe window,
  // not on every event (matches previous console-noise floor).
  console.warn(
    `[vos3] ${filled.severity.toUpperCase()} ${filled.scope}:`,
    filled.error,
  );

  // Fan out to UI subscribers.
  for (const l of listeners) {
    try { l(filled); } catch (e) {
      // Don't let a buggy listener silence other listeners.
      console.error('[vos3] error-bus listener threw:', e);
    }
  }
}

export function subscribeErrors(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function getRecentErrors(): readonly Required<VOS3ErrorReport>[] {
  return recent.slice();
}

export function clearRecentErrors(): void {
  recent.length = 0;
  lastFiredByScope.clear();
}
