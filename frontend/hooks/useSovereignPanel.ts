/**
 * frontend/hooks/useSovereignPanel.ts
 *
 * Stage 10.3 — Sovereign Control Panel state hook.
 *
 * Wraps the Tauri policy bridge calls (tauri-bridge.ts) into a single
 * React hook the dashboard page consumes. Polls the kernel for the
 * confidence-event feed and the per-slot status; exposes mutators
 * (toggle Safe-Rollout, set per-slot threshold, set global floor) that
 * call the kernel synchronously and refresh state on success.
 *
 * Polling cadence: 2 seconds. The kernel ring is bounded at 64 entries
 * (VOS3_AUDIT_RING_SIZE in audit_ring.h); a 2-second cadence keeps the
 * dashboard live without losing events to wraparound under any
 * realistic block rate. The streaming-fidelity sub-1ms verify path is
 * a *separate* mechanism (see useTokenVerification.ts in 10.3.D), not
 * this dashboard's responsibility.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  isTauri,
  tauriPolicyStatus,
  tauriPolicySetForcePermit,
  tauriPolicyOverrideSlot,
  tauriPolicyDrainAudit,
  tauriPolicyCheckConfidence,
} from '@/lib/tauri-bridge';

// Match the kernel's audit_ring.h enum values verbatim.
export const AUDIT_CAT = {
  NONE: 0,
  INTENT_REJECT: 1,
  TEE_BIND_FAIL: 2,
  HALLUCINATION_BLOCK: 3,
  FORCE_PERMIT_OVERRIDE: 4,
} as const;

export const CATEGORY_LABEL: Record<number, string> = {
  [AUDIT_CAT.NONE]: 'none',
  [AUDIT_CAT.INTENT_REJECT]: 'intent reject',
  [AUDIT_CAT.TEE_BIND_FAIL]: 'tee bind fail',
  [AUDIT_CAT.HALLUCINATION_BLOCK]: 'hallucination block',
  [AUDIT_CAT.FORCE_PERMIT_OVERRIDE]: 'force-permit override',
};

export interface PolicyStatus {
  forcePermit: boolean;
  /** Per-slot gate values, indexed 0..N-1. The kernel reports 4 today. */
  gates: number[];
}

export interface AuditEvent {
  seq: number;
  category: number;
  rc: number;
  slotId: number;
  digestPrefixHex: string;
}

export interface AuditQuoteState {
  /** kernel's monotonic emit count (-1 if unread) */
  total: number;
  /** entries currently buffered in the ring */
  fill: number;
  /** parsed events, newest-first; deduped by seq across polls */
  events: AuditEvent[];
}

const POLL_INTERVAL_MS = 2000;
const MAX_EVENTS_BUFFERED = 256;

// ---------------------------------------------------------------------------
// Wire-format parsers — match cmd_policy_status / cmd_audit_fail_quote
// ---------------------------------------------------------------------------

/**
 * Parse "POLICY|force_permit=N|gates=g0,g1,g2,g3".
 * Tolerates the leading "OK " status word the VBus driver may prepend.
 */
export function parsePolicyStatus(reply: string): PolicyStatus | null {
  const idx = reply.indexOf('POLICY|');
  if (idx < 0) return null;
  const body = reply.slice(idx);
  const parts = body.split('|');
  // parts[0]="POLICY", parts[1]="force_permit=N", parts[2]="gates=g0,…"
  if (parts.length < 3) return null;
  const fp = parts[1].split('=')[1];
  const gatesPart = parts[2].split('=')[1] ?? '';
  const gates = gatesPart === ''
    ? []
    : gatesPart.split(',').map((s) => Number(s)).filter((n) => Number.isFinite(n));
  return { forcePermit: fp === '1', gates };
}

/**
 * Parse "AUDIT_FAIL|total=N|fill=M|<seq>:<cat>:<rc>:<slot>:<digest>|…".
 */
export function parseAuditQuote(reply: string): AuditQuoteState | null {
  const idx = reply.indexOf('AUDIT_FAIL|');
  if (idx < 0) return null;
  const body = reply.slice(idx);
  const parts = body.split('|');
  if (parts.length < 3) return null;
  const total = Number(parts[1].split('=')[1]);
  const fill = Number(parts[2].split('=')[1]);
  if (!Number.isFinite(total) || !Number.isFinite(fill)) return null;
  const events: AuditEvent[] = [];
  for (let i = 3; i < parts.length; i++) {
    const fields = parts[i].split(':');
    if (fields.length !== 5) continue;
    const seq = Number(fields[0]);
    const category = Number(fields[1]);
    const rc = Number(fields[2]);
    const slotId = Number(fields[3]);
    const digestPrefixHex = fields[4];
    if (![seq, category, rc, slotId].every(Number.isFinite)) continue;
    events.push({ seq, category, rc, slotId, digestPrefixHex });
  }
  return { total, fill, events };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSovereignPanel() {
  const [policy, setPolicy] = useState<PolicyStatus | null>(null);
  const [audit, setAudit] = useState<AuditQuoteState>({
    total: -1,
    fill: 0,
    events: [],
  });
  const [error, setError] = useState<string | null>(null);

  // De-dup events by seq across polls. The kernel keeps the last 64 in
  // the ring; once an event scrolls off the kernel ring, we still want
  // the dashboard's UI buffer to retain it (up to MAX_EVENTS_BUFFERED).
  const seenSeqs = useRef<Set<number>>(new Set());

  // Refresh policy + audit. Every call is safe to run concurrently with
  // mutations because the kernel is single-frame-at-a-time.
  const refresh = useCallback(async () => {
    if (!isTauri()) {
      // Browser mode: dashboard is a viewer-only stub. Useful for
      // frontend dev without a running kernel.
      return;
    }
    try {
      const [statusReply, auditReply] = await Promise.all([
        tauriPolicyStatus(),
        tauriPolicyDrainAudit(),
      ]);
      const ps = parsePolicyStatus(statusReply);
      if (ps) setPolicy(ps);

      const aq = parseAuditQuote(auditReply);
      if (aq) {
        setAudit((prev) => {
          const merged: AuditEvent[] = [];
          for (const e of aq.events) {
            if (!seenSeqs.current.has(e.seq)) {
              seenSeqs.current.add(e.seq);
              merged.push(e);
            }
          }
          // Newest first; cap the buffer.
          const next = [...merged.reverse(), ...prev.events].slice(
            0,
            MAX_EVENTS_BUFFERED,
          );
          return { total: aq.total, fill: aq.fill, events: next };
        });
      }
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    }
  }, []);

  // Poll loop.
  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await refresh();
    };
    void tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [refresh]);

  // ---------------- Mutators ----------------

  const setForcePermit = useCallback(
    async (enabled: boolean) => {
      if (!isTauri()) return;
      try {
        await tauriPolicySetForcePermit(enabled);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [refresh],
  );

  const setSlotThreshold = useCallback(
    async (slotId: number, score: number) => {
      if (!isTauri()) return;
      try {
        await tauriPolicyOverrideSlot(slotId, score);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [refresh],
  );

  /** Apply the same threshold to every slot in one transaction. */
  const setGlobalFloor = useCallback(
    async (score: number) => {
      if (!isTauri() || !policy) return;
      try {
        await Promise.all(
          policy.gates.map((_g, slotId) =>
            tauriPolicyOverrideSlot(slotId, score),
          ),
        );
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [policy, refresh],
  );

  /** Manual gate exercise from the dashboard fixture. */
  const checkConfidence = useCallback(
    async (slotId: number, score: number) => {
      if (!isTauri()) return null;
      try {
        return await tauriPolicyCheckConfidence(slotId, score);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      }
    },
    [],
  );

  return {
    policy,
    audit,
    error,
    refresh,
    setForcePermit,
    setSlotThreshold,
    setGlobalFloor,
    checkConfidence,
  };
}
