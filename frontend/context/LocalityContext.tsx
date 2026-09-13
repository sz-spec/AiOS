// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// W6.3 — locality context + useLocality() hook.
//
// On mount, fetches `/api/system/status` (public, no auth required)
// and provides the backend's locality posture to every component via
// a React context. The two convenience predicates the UI cares about:
//
//   isLocalFirst  — backend pinned VOS3_LOCALITY_PREFERENCE=local-first
//   isSovereign   — local-first AND no cloud keys configured
//                   (full air-gap; differs from the policy-only mode)
//
// Component contracts:
//   - LocalityProvider mounted near the root (see ClientLayout.tsx).
//   - useLocality() returns { status, isLoading, error, isLocalFirst,
//                              isSovereign, refresh }.
//   - During the initial fetch isLoading=true; consumers should treat
//     locality-gated UIs as "unknown" until the first fetch resolves
//     (don't optimistically render the cloud experience).
//
// Failure mode: if /api/system/status is unreachable (e.g. backend
// boot race), `error` is set and isLocalFirst defaults to FALSE — i.e.
// we fall back to the cloud experience rather than silently locking
// the user out of cloud-only flows. The sovereign badge simply doesn't
// render until the call succeeds.

'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

export type Locality = 'local-first' | 'cloud-first' | 'auto';
export type Database = 'SQLite' | 'Convex';

export interface SystemStatus {
  locality: Locality;
  database: Database;
  llm_provider: string;
  is_airgapped: boolean;
  version: string;
}

// P3.4 — sync-engine surface for the UI's "synced N seconds ago"
// pill. Polled separately from the immutable system posture so we
// can refresh sync state at a faster cadence without re-doing the
// llm-provider probe.
export interface SyncTableStatus {
  dirty: number;
  last_synced_at_ms: number | null;
}

export interface SyncStatus {
  last_synced_at_ms: number | null;
  dirty_count: number;
  tables: Record<string, SyncTableStatus>;
}

interface LocalityContextValue {
  status: SystemStatus | null;
  syncStatus: SyncStatus | null;
  isLoading: boolean;
  error: string | null;
  isLocalFirst: boolean;
  isSovereign: boolean;
  refresh: () => Promise<void>;
  refreshSync: () => Promise<void>;
}

const LocalityContext = createContext<LocalityContextValue | null>(null);

const STATUS_URL = '/api/system/status';
const SYNC_STATUS_URL = '/api/system/sync/status';

export function LocalityProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<Promise<void> | null>(null);
  const syncInflight = useRef<Promise<void> | null>(null);

  const fetchStatus = useCallback(async (): Promise<void> => {
    // Coalesce concurrent refresh() calls.
    if (inflight.current !== null) {
      await inflight.current;
      return;
    }
    inflight.current = (async () => {
      try {
        setError(null);
        // The endpoint is public on the backend (in the auth public-paths
        // list); apiFetch will just omit Authorization when getToken
        // returns null and the request still succeeds.
        const res = await apiFetch(getToken, STATUS_URL, { method: 'GET' });
        if (!res.ok) {
          throw new Error(`GET ${STATUS_URL} → ${res.status}`);
        }
        const data = (await res.json()) as SystemStatus;
        setStatus(data);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'status fetch failed';
        setError(msg);
        // Keep the previous status if any; don't blow away a successful
        // earlier read on a transient refresh failure.
      } finally {
        setIsLoading(false);
        inflight.current = null;
      }
    })();
    await inflight.current;
  }, [getToken]);

  const fetchSyncStatus = useCallback(async (): Promise<void> => {
    // Independent inflight guard — a slow /sync/status fetch must
    // not block /status, and vice-versa.
    if (syncInflight.current !== null) {
      await syncInflight.current;
      return;
    }
    syncInflight.current = (async () => {
      try {
        const res = await apiFetch(getToken, SYNC_STATUS_URL, { method: 'GET' });
        if (!res.ok) {
          // Non-fatal: the sync pill just stays at its last value.
          return;
        }
        const data = (await res.json()) as SyncStatus;
        setSyncStatus(data);
      } catch {
        // Swallow — sync status is best-effort UI sugar, never a
        // hard requirement for the rest of the app.
      } finally {
        syncInflight.current = null;
      }
    })();
    await syncInflight.current;
  }, [getToken]);

  useEffect(() => {
    fetchStatus();
    fetchSyncStatus();
  }, [fetchStatus, fetchSyncStatus]);

  const value = useMemo<LocalityContextValue>(
    () => ({
      status,
      syncStatus,
      isLoading,
      error,
      isLocalFirst: status?.locality === 'local-first',
      isSovereign:
        status?.locality === 'local-first' && status.is_airgapped === true,
      refresh: fetchStatus,
      refreshSync: fetchSyncStatus,
    }),
    [status, syncStatus, isLoading, error, fetchStatus, fetchSyncStatus],
  );

  return (
    <LocalityContext.Provider value={value}>{children}</LocalityContext.Provider>
  );
}

/**
 * Access the backend's locality posture from any client component.
 *
 * Returns a stable shape — `isLocalFirst` and `isSovereign` default to
 * FALSE until the first /api/system/status fetch resolves. Code that
 * needs to wait for a definitive answer should branch on
 * `status === null && isLoading`.
 */
export function useLocality(): LocalityContextValue {
  const ctx = useContext(LocalityContext);
  if (!ctx) {
    // Throwing here would crash any client component that imports
    // useLocality outside the provider. Instead, return a stable
    // "unknown / cloud-default" snapshot so the app doesn't go down
    // if the provider tree was forgotten on a sub-route. Logs a
    // single console warning so the gap is visible during dev.
    if (typeof window !== 'undefined') {
      console.warn(
        '[useLocality] called outside <LocalityProvider>. Defaulting to cloud-first; '
          + 'wrap your tree with <LocalityProvider> to enable sovereign-mode UI.',
      );
    }
    return {
      status: null,
      syncStatus: null,
      isLoading: false,
      error: null,
      isLocalFirst: false,
      isSovereign: false,
      refresh: async () => {},
      refreshSync: async () => {},
    };
  }
  return ctx;
}
