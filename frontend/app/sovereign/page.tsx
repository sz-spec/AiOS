'use client';

/**
 * frontend/app/sovereign/page.tsx
 *
 * Stage 10.3 — Sovereign Control Panel.
 *
 * Single-page operator console for the Stage-10 hallucination guardrail.
 * Surfaces:
 *   1. Real-time per-slot confidence gates (live from POLICY_STATUS).
 *   2. Live HALLUCINATION_BLOCK + FORCE_PERMIT_OVERRIDE event feed
 *      (from AUDIT_FAIL_QUOTE polling, deduped by seq).
 *   3. Safe-Rollout (VOS_FORCE_PERMIT) toggle.
 *   4. Per-slot override slider AND a Global Floor slider.
 *   5. Manual ACTION_CHECK_CONFIDENCE fixture so an operator can
 *      verify a gate decision live.
 *
 * Honest scope: the streaming-fidelity sub-1ms verify badge is a
 * separate mechanism (useTokenVerification.ts in 10.3.D) used by the
 * chat stream. This page is the *governance* surface.
 */

import { useMemo, useState } from 'react';

import {
  AUDIT_CAT,
  CATEGORY_LABEL,
  useSovereignPanel,
} from '@/hooks/useSovereignPanel';

const VOS3_INTENT_MAX_CONFIDENCE_SCORE = 1000;

export default function SovereignPanelPage() {
  const {
    policy,
    audit,
    error,
    refresh,
    setForcePermit,
    setSlotThreshold,
    setGlobalFloor,
    checkConfidence,
  } = useSovereignPanel();

  const [globalFloorDraft, setGlobalFloorDraft] = useState(0);
  const [fixtureSlot, setFixtureSlot] = useState(0);
  const [fixtureScore, setFixtureScore] = useState(500);
  const [fixtureReply, setFixtureReply] = useState<string | null>(null);

  // Block-rate summary over the buffered events.
  const summary = useMemo(() => {
    const counts: Record<number, number> = {};
    for (const e of audit.events) {
      counts[e.category] = (counts[e.category] ?? 0) + 1;
    }
    return counts;
  }, [audit.events]);

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100 p-6">
      <header className="mb-6 border-b border-zinc-800 pb-4">
        <h1 className="text-2xl font-semibold">Sovereign Control Panel</h1>
        <p className="text-sm text-zinc-400">
          Stage 10 hallucination-guardrail governance. Every change is
          single-frame, reversible, and recorded in the audit ring.
        </p>
        {error && (
          <p className="mt-2 text-sm text-red-400" data-testid="error-banner">
            ⚠ {error}
          </p>
        )}
      </header>

      {/* ----------------------------------------------------------- */}
      {/* Force-permit toggle (Safe-Rollout)                         */}
      {/* ----------------------------------------------------------- */}
      <section className="mb-6 rounded border border-zinc-800 bg-zinc-900 p-4">
        <h2 className="mb-2 text-lg font-semibold">Safe-Rollout</h2>
        <p className="mb-3 text-sm text-zinc-400">
          When ON: the kernel still emits an audit-ring entry for every
          would-block, but the action proceeds. Used to gather
          telemetry on what WOULD be blocked before flipping enforcement
          on. Reversible single-frame; never persists across kernel
          reboots.
        </p>
        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={!!policy?.forcePermit}
            onChange={(e) => setForcePermit(e.target.checked)}
            className="h-5 w-5"
            data-testid="force-permit-toggle"
          />
          <span className="text-sm">
            VOS_FORCE_PERMIT ={' '}
            <strong>{policy?.forcePermit ? 'ON (observe-only)' : 'OFF (enforce)'}</strong>
          </span>
        </label>
      </section>

      {/* ----------------------------------------------------------- */}
      {/* Per-slot gates + Global Floor                              */}
      {/* ----------------------------------------------------------- */}
      <section className="mb-6 rounded border border-zinc-800 bg-zinc-900 p-4">
        <h2 className="mb-2 text-lg font-semibold">Per-slot confidence gates</h2>
        <p className="mb-3 text-sm text-zinc-400">
          0 = no guardrail. Higher = more restrictive. Direct override
          path; does NOT touch the TEE measurement chain (RTMR[1] is
          unchanged), so you can dial without invalidating attestation.
        </p>

        <div className="space-y-3">
          {(policy?.gates ?? []).map((score, slotId) => (
            <div key={slotId} className="flex items-center gap-3">
              <span className="w-16 text-xs text-zinc-500">slot {slotId}</span>
              <input
                type="range"
                min={0}
                max={VOS3_INTENT_MAX_CONFIDENCE_SCORE}
                step={50}
                value={score}
                onChange={(e) =>
                  setSlotThreshold(slotId, Number(e.target.value))
                }
                className="flex-1"
                data-testid={`slot-${slotId}-slider`}
              />
              <span className="w-16 text-right text-sm tabular-nums">
                {score}
              </span>
            </div>
          ))}
          {(!policy || policy.gates.length === 0) && (
            <p className="text-sm text-zinc-500">
              Waiting for kernel POLICY_STATUS reply…
            </p>
          )}
        </div>

        <div className="mt-4 flex items-center gap-3 border-t border-zinc-800 pt-3">
          <span className="w-16 text-xs text-zinc-500">global</span>
          <input
            type="range"
            min={0}
            max={VOS3_INTENT_MAX_CONFIDENCE_SCORE}
            step={50}
            value={globalFloorDraft}
            onChange={(e) => setGlobalFloorDraft(Number(e.target.value))}
            className="flex-1"
            data-testid="global-floor-slider"
          />
          <span className="w-16 text-right text-sm tabular-nums">
            {globalFloorDraft}
          </span>
          <button
            type="button"
            onClick={() => setGlobalFloor(globalFloorDraft)}
            className="rounded bg-zinc-700 px-3 py-1 text-sm hover:bg-zinc-600"
            data-testid="apply-global-floor"
          >
            Apply to all
          </button>
        </div>
      </section>

      {/* ----------------------------------------------------------- */}
      {/* Live event feed                                            */}
      {/* ----------------------------------------------------------- */}
      <section className="mb-6 rounded border border-zinc-800 bg-zinc-900 p-4">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">
            Compliance event feed
            <span className="ml-2 text-xs text-zinc-500">
              kernel total {audit.total >= 0 ? audit.total : '—'} · ring fill {audit.fill}
            </span>
          </h2>
          <button
            type="button"
            onClick={refresh}
            className="rounded bg-zinc-800 px-2 py-1 text-xs hover:bg-zinc-700"
          >
            Refresh
          </button>
        </div>

        <div className="mb-3 flex flex-wrap gap-3 text-xs text-zinc-400">
          {[
            AUDIT_CAT.INTENT_REJECT,
            AUDIT_CAT.TEE_BIND_FAIL,
            AUDIT_CAT.HALLUCINATION_BLOCK,
            AUDIT_CAT.FORCE_PERMIT_OVERRIDE,
          ].map((cat) => (
            <span key={cat} className="rounded bg-zinc-800 px-2 py-0.5">
              {CATEGORY_LABEL[cat]}: <strong>{summary[cat] ?? 0}</strong>
            </span>
          ))}
        </div>

        <table className="w-full text-left text-sm tabular-nums" data-testid="audit-feed">
          <thead className="text-xs text-zinc-500">
            <tr>
              <th className="py-1 pr-3">seq</th>
              <th className="py-1 pr-3">category</th>
              <th className="py-1 pr-3">slot</th>
              <th className="py-1 pr-3">rc</th>
              <th className="py-1">digest prefix</th>
            </tr>
          </thead>
          <tbody>
            {audit.events.length === 0 && (
              <tr>
                <td className="py-2 text-zinc-500" colSpan={5}>
                  No events buffered yet — the panel polls every 2s.
                </td>
              </tr>
            )}
            {audit.events.map((e) => (
              <tr key={e.seq} className="border-t border-zinc-800/60">
                <td className="py-1 pr-3 text-zinc-400">{e.seq}</td>
                <td className="py-1 pr-3">{CATEGORY_LABEL[e.category] ?? e.category}</td>
                <td className="py-1 pr-3">{e.slotId === 255 ? '—' : e.slotId}</td>
                <td className="py-1 pr-3">{e.rc}</td>
                <td className="py-1 font-mono text-xs text-zinc-500">{e.digestPrefixHex}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* ----------------------------------------------------------- */}
      {/* Manual fixture                                              */}
      {/* ----------------------------------------------------------- */}
      <section className="rounded border border-zinc-800 bg-zinc-900 p-4">
        <h2 className="mb-2 text-lg font-semibold">Test fixture</h2>
        <p className="mb-3 text-sm text-zinc-400">
          Manually exercise an agent's gate. Issues a single
          ACTION_CHECK_CONFIDENCE frame against the kernel; the reply is
          the literal kernel response (CONF_OK or CONF_BLOCK).
        </p>
        <div className="flex items-center gap-3">
          <label className="text-xs text-zinc-500">slot</label>
          <input
            type="number"
            min={0}
            max={(policy?.gates.length ?? 4) - 1}
            value={fixtureSlot}
            onChange={(e) => setFixtureSlot(Number(e.target.value))}
            className="w-16 rounded bg-zinc-800 p-1 text-sm"
          />
          <label className="text-xs text-zinc-500">observed</label>
          <input
            type="number"
            min={0}
            max={VOS3_INTENT_MAX_CONFIDENCE_SCORE}
            value={fixtureScore}
            onChange={(e) => setFixtureScore(Number(e.target.value))}
            className="w-20 rounded bg-zinc-800 p-1 text-sm"
          />
          <button
            type="button"
            onClick={async () => {
              const reply = await checkConfidence(fixtureSlot, fixtureScore);
              setFixtureReply(reply ?? 'no reply');
            }}
            className="rounded bg-zinc-700 px-3 py-1 text-sm hover:bg-zinc-600"
            data-testid="run-fixture"
          >
            Check
          </button>
        </div>
        {fixtureReply && (
          <p className="mt-3 font-mono text-xs text-zinc-300" data-testid="fixture-reply">
            ↪ {fixtureReply}
          </p>
        )}
      </section>
    </main>
  );
}
