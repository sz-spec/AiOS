// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// W3.3 — Tauri-IPC api-client wrapper.
//
// Single chokepoint for every authenticated request from React hooks to
// the local FastAPI backend at :8000. Replaces raw `fetch()` calls so
// the three security headers (Authorization, X-CSRF-Token, and the
// initial X-Tauri-Handshake) are attached in one place.
//
// Headers attached automatically:
//   - `Authorization: Bearer <clerk token>` — via auth-token.ts coalesced
//     fetcher (preserves the 401-retry + request-coalescing behavior)
//   - `X-CSRF-Token: <per-process token>` — for POST/PUT/PATCH/DELETE
//   - `Content-Type: application/json` — only when body is present and
//     not already set by the caller
//
// CSRF handshake lifecycle:
//   - The token is fetched lazily on the first mutating call.
//   - It's cached in a module-scope variable for the lifetime of the
//     browser/Tauri WebView session.
//   - If the backend returns 403 CSRF_VIOLATION (e.g. backend restarted
//     and reissued its volatile token), we drop the cached token, redo
//     the handshake once, and retry the failed call.
//
// Handshake secret source (in order of precedence):
//   1. `window.__VOS3_TAURI_HANDSHAKE__` — injected by the Tauri Rust
//      shell at WebView creation time (production path).
//   2. `NEXT_PUBLIC_VOS3_TAURI_IPC_SECRET` env var — set in
//      `.env.local` for browser-dev workflows.
//
// If neither is available, the client logs a warning and skips
// CSRF attachment; the backend will then 403 the request — making
// the config gap visible at first use rather than silently dropping.

import { authedFetch, getCoalescedToken, invalidateCache } from './auth-token'

type TokenFetcher = () => Promise<string | null>

const MUTABLE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

// ---------------------------------------------------------------------------
// CSRF token cache — per browser session.
// ---------------------------------------------------------------------------

let csrfToken: string | null = null
let csrfHandshakeInflight: Promise<string | null> | null = null

function getHandshakeSecret(): string | null {
  // Production: Tauri shell injects it at WebView creation.
  if (typeof window !== 'undefined') {
    const fromWindow = (window as unknown as { __VOS3_TAURI_HANDSHAKE__?: string }).__VOS3_TAURI_HANDSHAKE__
    if (fromWindow) return fromWindow
  }
  // Dev: NEXT_PUBLIC env var. Note this gets bundled into the JS — fine
  // for the local-only dev workflow but DO NOT use this for production
  // builds; the Tauri shell should inject via window in that case.
  if (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_VOS3_TAURI_IPC_SECRET) {
    return process.env.NEXT_PUBLIC_VOS3_TAURI_IPC_SECRET
  }
  return null
}

/**
 * W6.4 — under the Tauri desktop shell the handshake secret is held by
 * the Rust sidecar manager (see desktop/src-tauri/src/sidecar.rs). Pull
 * it via the `get_handshake` invoke command instead of relying on a
 * window-injected global or a NEXT_PUBLIC env var. We resolve the
 * dynamic `@tauri-apps/api/core` import lazily so:
 *   - The browser build (no Tauri) doesn't break on missing module.
 *   - The first call eagerly populates the in-memory cache so
 *     subsequent fetches don't pay the invoke round-trip.
 */
async function getHandshakeSecretFromTauri(): Promise<string | null> {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { __TAURI_INTERNALS__?: unknown }
  if (!w.__TAURI_INTERNALS__) return null
  try {
    const { invoke } = (await import('@tauri-apps/api/core')) as {
      invoke: <T>(cmd: string) => Promise<T>
    }
    const secret = await invoke<string>('get_handshake')
    return typeof secret === 'string' && secret.length > 0 ? secret : null
  } catch {
    return null
  }
}

async function performHandshake(): Promise<string | null> {
  // W6.4 — prefer the Tauri invoke bridge when available (production
  // desktop runs). Falls back to window-global / NEXT_PUBLIC env for
  // browser-dev workflows.
  let secret = await getHandshakeSecretFromTauri()
  if (!secret) {
    secret = getHandshakeSecret()
  }
  if (!secret) {
    console.warn(
      '[api-client] X-Tauri-Handshake secret not available. ' +
        'Set NEXT_PUBLIC_VOS3_TAURI_IPC_SECRET (dev) or rely on the Tauri shell injection. ' +
        'Mutating requests will be rejected with 403 until the handshake is configured.'
    )
    return null
  }
  try {
    const res = await fetch('/api/auth/csrf', {
      method: 'GET',
      headers: { 'X-Tauri-Handshake': secret },
    })
    if (!res.ok) {
      console.warn(`[api-client] CSRF handshake failed with status ${res.status}`)
      return null
    }
    const data = (await res.json()) as { csrf_token?: string }
    return data.csrf_token ?? null
  } catch (err) {
    console.warn('[api-client] CSRF handshake error:', err)
    return null
  }
}

/**
 * Lazily fetch + cache the CSRF token. Concurrent callers share the
 * same in-flight promise so we never run two handshakes.
 */
async function getCsrfToken(): Promise<string | null> {
  if (csrfToken !== null) return csrfToken
  if (csrfHandshakeInflight !== null) return csrfHandshakeInflight
  csrfHandshakeInflight = (async () => {
    try {
      csrfToken = await performHandshake()
      return csrfToken
    } finally {
      // Clear the in-flight reference on the next tick so concurrent
      // waiters still see the same promise.
      setTimeout(() => {
        csrfHandshakeInflight = null
      }, 0)
    }
  })()
  return csrfHandshakeInflight
}

/**
 * Drop the cached CSRF token. Called after a 403 CSRF_VIOLATION so the
 * next call re-handshakes. Also exposed for the sign-out flow.
 */
export function invalidateCsrfCache(): void {
  csrfToken = null
}

// ---------------------------------------------------------------------------
// apiFetch — single drop-in replacement for raw fetch().
// ---------------------------------------------------------------------------

/**
 * Authenticated + CSRF-protected fetch.
 *
 * Behaviour:
 *  - Attaches `Authorization: Bearer <token>` via the coalesced token fetcher.
 *  - For mutating methods, attaches `X-CSRF-Token` (handshaking lazily once).
 *  - On 401, retries once with a refreshed auth token (via authedFetch).
 *  - On 403 CSRF_VIOLATION, drops the cached CSRF token, re-handshakes,
 *    and retries the call exactly once.
 *
 * Drop-in usage:
 *   const res = await apiFetch(getToken, '/api/agents', { method: 'POST', body: JSON.stringify(...) })
 */
export async function apiFetch(
  tokenFetcher: TokenFetcher,
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const needsCsrf = MUTABLE_METHODS.has(method) && url.startsWith('/api/')

  const buildInit = async (): Promise<RequestInit> => {
    const headers = new Headers(init.headers || {})
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    if (needsCsrf) {
      const csrf = await getCsrfToken()
      if (csrf) headers.set('X-CSRF-Token', csrf)
    }
    return { ...init, method, headers }
  }

  // First attempt — authedFetch handles Authorization injection + the
  // 401-retry behavior already.
  let res = await authedFetch(tokenFetcher, url, await buildInit())

  // CSRF self-heal — if the backend restarted and rotated its token,
  // our cached one is stale. Drop, re-handshake, retry once.
  if (res.status === 403 && needsCsrf) {
    let body: { error?: { code?: string } } | null = null
    try {
      body = (await res.clone().json()) as { error?: { code?: string } }
    } catch {
      // Non-JSON 403 — not our CSRF flow. Return as-is.
    }
    if (body?.error?.code === 'CSRF_VIOLATION') {
      invalidateCsrfCache()
      res = await authedFetch(tokenFetcher, url, await buildInit())
    }
  }
  return res
}

// ---------------------------------------------------------------------------
// Convenience methods — typed helpers for JSON-in / JSON-out endpoints.
// ---------------------------------------------------------------------------

/**
 * GET helper. Returns parsed JSON or throws on non-2xx.
 */
export async function apiGet<T = unknown>(
  tokenFetcher: TokenFetcher,
  url: string,
): Promise<T> {
  const res = await apiFetch(tokenFetcher, url, { method: 'GET' })
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`)
  return (await res.json()) as T
}

/**
 * POST helper. Body is JSON-stringified automatically.
 */
export async function apiPost<T = unknown>(
  tokenFetcher: TokenFetcher,
  url: string,
  body?: unknown,
): Promise<T> {
  const res = await apiFetch(tokenFetcher, url, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`POST ${url} → ${res.status}`)
  return (await res.json()) as T
}

/**
 * PUT helper.
 */
export async function apiPut<T = unknown>(
  tokenFetcher: TokenFetcher,
  url: string,
  body?: unknown,
): Promise<T> {
  const res = await apiFetch(tokenFetcher, url, {
    method: 'PUT',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`PUT ${url} → ${res.status}`)
  return (await res.json()) as T
}

/**
 * DELETE helper.
 */
export async function apiDelete<T = unknown>(
  tokenFetcher: TokenFetcher,
  url: string,
): Promise<T> {
  const res = await apiFetch(tokenFetcher, url, { method: 'DELETE' })
  if (!res.ok) throw new Error(`DELETE ${url} → ${res.status}`)
  return (await res.json()) as T
}

// ---------------------------------------------------------------------------
// Re-exports for legacy callers that imported from this module.
// ---------------------------------------------------------------------------

export { authedFetch, getCoalescedToken, invalidateCache as invalidateAuthCache }
