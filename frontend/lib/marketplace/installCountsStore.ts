'use client';

/**
 * Live install counts for marketplace listings.
 *
 * Two layers:
 *   1. `real`        — authoritative counts fetched from the backend
 *                      (`GET /api/agents/template-stats`). `null` until
 *                      the first successful fetch, after which it's a
 *                      `template_id → count` map.
 *   2. `optimistic`  — per-id deltas applied since the last `setReal`.
 *                      Bumped from the agents page on a successful
 *                      `createAgent` call so the UI reflects new
 *                      installs without waiting for a re-fetch.
 *
 * Display value resolution: `(real[id] ?? baseline) + optimistic[id]`.
 * The `baseline` is each listing's seeded `installs` field — used only
 * before `setReal` lands (cold start) so the UI never shows zero.
 */

import { create } from 'zustand';

interface InstallCountsState {
  real: Record<string, number> | null;
  optimistic: Record<string, number>;
  /** Replace the authoritative map. Clears optimistic deltas — they're
   * baked into the new authoritative value, so we drop them to avoid
   * double-counting an in-flight install on the next render. */
  setReal: (counts: Record<string, number>) => void;
  /** Increment `id`'s optimistic delta by 1. */
  bump: (id: string) => void;
}

export const useInstallCountsStore = create<InstallCountsState>((set) => ({
  real: null,
  optimistic: {},
  setReal: (counts) => set({ real: counts, optimistic: {} }),
  bump: (id) =>
    set((s) => ({
      optimistic: { ...s.optimistic, [id]: (s.optimistic[id] ?? 0) + 1 },
    })),
}));

/**
 * Subscribe to the displayed install count for a single listing.
 *
 *   const installs = useDisplayedInstalls(listing.id, listing.installs);
 *
 * The hook re-renders only when this id's count changes, not on every
 * unrelated bump.
 */
export function useDisplayedInstalls(id: string, baseline: number): number {
  return useInstallCountsStore((s) => {
    const real = s.real?.[id];
    const opt = s.optimistic[id] ?? 0;
    return (real ?? baseline) + opt;
  });
}
