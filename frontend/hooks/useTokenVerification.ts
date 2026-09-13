/**
 * frontend/hooks/useTokenVerification.ts
 *
 * Stage 10.3 — Asynchronous Integrity Streaming consumer hook.
 *
 * Pairs with backend/services/integrity_worker.py. The chat stream
 * renders tokens optimistically (immediate, with a "pending" marker);
 * this hook subscribes to verified/revoked events and promotes the
 * UI marker to "verified" or redacts the token when the worker reports.
 *
 * Event contract
 * ==============
 *
 *   chat:token-verified    payload = { tokenId: number, actualDigestHex: string }
 *   chat:token-revoked     payload = { tokenId: number, reason: string,
 *                                       actualDigestHex: string,
 *                                       expectedDigestHex: string }
 *
 * In Tauri mode, events arrive via the @tauri-apps/api/event listener
 * (existing onKernelEvent in lib/tauri-bridge.ts). In browser mode the
 * hook is a no-op — there is no kernel to verify against — and tokens
 * stay in "pending" state forever.
 *
 * Performance contract (the "sub-1ms" claim from the Stage-10 plan)
 * =================================================================
 *
 * What this hook DOES guarantee:
 *   - Producer → optimistic render is one setState in the chat hook,
 *     not gated on hash verification. The user sees the token at
 *     network latency + React render budget — typically a few ms.
 *   - Consumer → verified-badge promotion is one Map.set + one
 *     setState. The badge transition cost is bounded by React's
 *     reconciliation, NOT by SHA-384 cost (the hash ran in the
 *     backend integrity worker, off the critical path).
 *
 * What it does NOT guarantee, and we are honest about:
 *   - The end-to-end "token-emit → verified-badge-shown" latency is
 *     a system property, not a hook property. The plan target is
 *     ≤ 1 ms UI jitter on the optimistic-render path; the
 *     verification badge itself is allowed to lag (~50-100 ms) without
 *     hurting the user experience.
 *   - We have NOT measured these latencies on a real workload. The
 *     benchmark for it is `backend/tests/benchmarks/test_async_integrity_jitter.py`,
 *     a Stage-10 missing-files port that has not yet shipped. When
 *     it ships, this contract is what it asserts against.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { isTauri, onKernelEvent } from '@/lib/tauri-bridge';

export type VerificationState =
  | 'pending'    // optimistic render — verification has not completed yet
  | 'verified'   // backend integrity worker confirmed the SHA-384
  | 'revoked';   // hash mismatch OR worker failed; token must be redacted

export interface VerifiedEventPayload {
  tokenId: number;
  actualDigestHex: string;
}

export interface RevokedEventPayload {
  tokenId: number;
  actualDigestHex: string;
  expectedDigestHex: string;
  reason: string;
}

const EVT_VERIFIED = 'chat:token-verified';
const EVT_REVOKED = 'chat:token-revoked';

/**
 * Track the verification state of a stream of tokens identified by
 * monotonically-increasing tokenIds.
 *
 * @returns
 *  - states: Record<tokenId, VerificationState>
 *  - markPending(id): called by the chat producer when emitting
 *    optimistically; ensures the hook treats the id as known.
 *  - lastRevocation: most-recent revocation reason for surfacing
 *    a banner to the user.
 */
export function useTokenVerification() {
  const [states, setStates] = useState<Record<number, VerificationState>>({});
  const [lastRevocation, setLastRevocation] = useState<RevokedEventPayload | null>(
    null,
  );

  // Refs to avoid setState-in-effect ping-pong on the listener side.
  const statesRef = useRef(states);
  statesRef.current = states;

  useEffect(() => {
    if (!isTauri()) return;

    let unlistenVerified: (() => void) | null = null;
    let unlistenRevoked: (() => void) | null = null;
    let cancelled = false;

    void (async () => {
      unlistenVerified = await onKernelEvent<VerifiedEventPayload>(
        EVT_VERIFIED,
        (payload) => {
          if (cancelled) return;
          setStates((prev) => ({ ...prev, [payload.tokenId]: 'verified' }));
        },
      );
      unlistenRevoked = await onKernelEvent<RevokedEventPayload>(
        EVT_REVOKED,
        (payload) => {
          if (cancelled) return;
          setStates((prev) => ({ ...prev, [payload.tokenId]: 'revoked' }));
          setLastRevocation(payload);
        },
      );
    })();

    return () => {
      cancelled = true;
      if (unlistenVerified) unlistenVerified();
      if (unlistenRevoked) unlistenRevoked();
    };
  }, []);

  const markPending = useCallback((tokenId: number) => {
    setStates((prev) =>
      // Idempotent: do not regress a verified/revoked token to pending.
      prev[tokenId] !== undefined ? prev : { ...prev, [tokenId]: 'pending' },
    );
  }, []);

  const reset = useCallback(() => {
    setStates({});
    setLastRevocation(null);
  }, []);

  return { states, lastRevocation, markPending, reset };
}
