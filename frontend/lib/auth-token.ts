// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// v20.6.2 — request-coalesced token resolver.
//
// Closes docs/audit/300_INTEGRATION_FAILURES_REPORT.md §2.5: under
// concurrent request bursts that hit a token-expiry boundary, every
// caller would independently invoke `getToken()`. Some saw the still-
// expired token (cache hit), some saw the freshly refreshed token,
// producing 401 stampedes and intermittent UX breakage.
//
// This module provides a SHARED in-flight refresh — the first concurrent
// call kicks off `getToken()`; everyone after waits on the same Promise.
// Resolves once and feeds the same value to all waiters. After resolution
// the singleton is cleared so the next call refreshes fresh.
//
// Usage:
//   const token = await getCoalescedToken(getTokenFromClerk);
//
// Or inside a hook:
//   const { getToken } = useAuth();
//   const tokenFn = useCallback(() => getCoalescedToken(getToken), [getToken]);

type TokenFetcher = () => Promise<string | null>;

let inflight: Promise<string | null> | null = null;
let lastToken: string | null = null;
let lastFetchedAt: number = 0;

// Treat tokens as "fresh enough" for this many ms after fetch — avoids a
// hot loop of refreshes for back-to-back calls within the same event tick.
// Clerk tokens already have 60s validity windows; 5s is a conservative
// caching window that won't outrun token expiry.
const FRESH_WINDOW_MS = 5_000;

/**
 * Fetches an auth token, sharing a single in-flight refresh across
 * concurrent callers within the same JS tick window.
 *
 * Returns null if the underlying fetcher returns null (unauthenticated).
 * Re-raises errors from the fetcher.
 */
export async function getCoalescedToken(fetcher: TokenFetcher): Promise<string | null> {
  // Fast path: a recent fetch resolved within the freshness window.
  const now = Date.now();
  if (lastToken !== null && now - lastFetchedAt < FRESH_WINDOW_MS) {
    return lastToken;
  }

  // If a refresh is already in flight, wait on it.
  if (inflight !== null) {
    return inflight;
  }

  // Start a new refresh. Multiple concurrent callers within this tick
  // will see this same Promise.
  inflight = (async () => {
    try {
      const t = await fetcher();
      lastToken = t;
      lastFetchedAt = Date.now();
      return t;
    } finally {
      // Clear AFTER the resolution so any stragglers in the same tick
      // still see the in-flight promise (not a race with the cache).
      // Microtask delay is sufficient.
      setTimeout(() => { inflight = null; }, 0);
    }
  })();

  return inflight;
}

/**
 * Helper that wraps a fetch() call with a single 401-retry using a
 * freshly-coalesced token. Use for any high-frequency authenticated
 * fetch from the hooks layer:
 *
 *   const res = await authedFetch(getToken, '/api/agents', { method: 'POST', body: ... });
 */
export async function authedFetch(
  tokenFetcher: TokenFetcher,
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const buildHeaders = (token: string | null): Headers => {
    const h = new Headers(init.headers || {});
    if (token) h.set('Authorization', `Bearer ${token}`);
    if (init.body && !h.has('Content-Type')) h.set('Content-Type', 'application/json');
    return h;
  };

  let token = await getCoalescedToken(tokenFetcher);
  let res = await fetch(url, { ...init, headers: buildHeaders(token) });

  if (res.status === 401) {
    // Force a fresh token outside the freshness window and retry once.
    invalidateCache();
    token = await getCoalescedToken(tokenFetcher);
    res = await fetch(url, { ...init, headers: buildHeaders(token) });
  }
  return res;
}

/** Manually invalidate the freshness cache (e.g. on user sign-out). */
export function invalidateCache(): void {
  lastToken = null;
  lastFetchedAt = 0;
}
