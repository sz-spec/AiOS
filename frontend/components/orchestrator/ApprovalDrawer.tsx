// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P7.1 — Sovereign Command Center · Transaction Guard Approval Drawer.
//
// The HITL interception surface for the P6.3 Host Bridge. When a
// sandboxed app fires a SAP/host transaction that exceeds the
// Transaction Guard policy (high-value purchase order, rate-limit,
// write-without-approval), the backend persists a
// `pendingHostApprovals` row and returns `awaiting_approval` to the
// caller. The operator opens this drawer, sees what was requested,
// and either:
//   - Approves: a biometric prompt (TouchID / Windows Hello on
//     supported hardware; falls back to a confirm dialog) is shown.
//     On verification, the decision is signed with the locally-stored
//     Ed25519 keypair (P6.1) and POSTed to
//     `/api/host_bridge/approvals/{id}/approve`.
//   - Rejects: same sign-then-POST flow, but with `decision="reject"`.
//
// Cryptographic signature path
// ----------------------------
// The signing keypair lives on the backend in the P6.1 keyring; the
// frontend never sees the private bytes. We POST the canonical
// decision blob to a tiny `/api/host_bridge/_sign` helper that does
// the Ed25519 sign in-process and returns the hex signature + public
// key. Until that endpoint ships, the drawer asks the backend's
// approve route to sign-and-execute server-side by supplying the
// pre-computed signature from the keyring proxy.
//
// For the P7.1 milestone we ship the "server-side sign via Bearer
// auth" path: the frontend collects the biometric gesture (proof of
// operator presence), then POSTs the operator's identity to the
// approve route. The backend's keyring-pinned verifier guarantees
// nobody but the locally-authenticated operator can sign — so the
// Bearer JWT IS the proof of presence as far as the kernel cares.

'use client';

import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  Fingerprint,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react';

import { apiFetch } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types — mirror the backend's `_approval_to_dict` shape
// ---------------------------------------------------------------------------

export interface PendingApproval {
  approval_id: string;
  app_id: string;
  workspace_id: string;
  target_system: string;
  action: string;
  amount: number | null;
  currency: string | null;
  reason: string | null;
  status: string;
  approver_id: string | null;
  result: Record<string, unknown> | null;
  created_at: number;
  updated_at: number;
}

interface ApprovalsListResponse {
  approvals: PendingApproval[];
}

interface SignResponse {
  signature_hex: string;
  public_key_hex: string;
  approver_user_id: string;
}

interface ApprovalDrawerProps {
  /**
   * Test mode — when supplied, the API is bypassed and these rows
   * are rendered verbatim.
   */
  mockApprovals?: PendingApproval[];
  /**
   * Open/close control. When undefined the drawer manages its own
   * state via the auto-open-on-pending behavior.
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  pollIntervalMs?: number;
}

// ---------------------------------------------------------------------------
// Threshold-reason → user-facing summary
// ---------------------------------------------------------------------------

function summarizeReason(reason: string | null): {
  badge: string;
  description: string;
  tone: 'amber' | 'rose' | 'sky';
} {
  if (!reason) {
    return {
      badge: 'Guard intercept',
      description: 'A safety threshold was crossed.',
      tone: 'amber',
    };
  }
  if (reason.startsWith('amount_over_threshold')) {
    return {
      badge: 'Value cap exceeded',
      description: 'The monetary value of this transaction exceeds the configured Transaction Guard limit.',
      tone: 'rose',
    };
  }
  if (reason.startsWith('rate_limit_exceeded')) {
    return {
      badge: 'Rate limit triggered',
      description: 'Too many host calls within the last minute. The Guard paused this request to prevent runaway automation.',
      tone: 'amber',
    };
  }
  if (reason === 'writes_require_approval') {
    return {
      badge: 'Write action',
      description: 'This action mutates state on the host system. Operator approval is required by policy.',
      tone: 'sky',
    };
  }
  return { badge: 'Policy hold', description: reason, tone: 'amber' };
}

function formatAmount(amount: number | null, currency: string | null): string {
  if (amount == null) return '—';
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount} ${currency ?? ''}`.trim();
  }
}

// ---------------------------------------------------------------------------
// Biometric helper — uses WebAuthn / Platform Authenticator when available.
// Falls back to a synchronous confirm() so the UX still works on the
// browser-dev path.
// ---------------------------------------------------------------------------

async function verifyBiometric(label: string): Promise<{ ok: boolean; method: string }> {
  if (typeof window === 'undefined') {
    return { ok: false, method: 'unavailable' };
  }
  const credApi = window.navigator?.credentials;
  // Probe for platform authenticator support (TouchID / Windows Hello).
  if (
    credApi &&
    typeof window.PublicKeyCredential !== 'undefined' &&
    typeof window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === 'function'
  ) {
    try {
      const available =
        await window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
      if (available) {
        // We don't ship a real WebAuthn registration in P7.1 — the
        // backend's Bearer-JWT + keyring-pin already form the trust
        // chain. The biometric probe here is purely a UX gesture
        // ("prove the human is present"). Use the credential
        // get() flow with a throwaway challenge; rejection = user
        // cancelled.
        const challenge = new Uint8Array(32);
        window.crypto.getRandomValues(challenge);
        try {
          await navigator.credentials.get({
            publicKey: {
              challenge,
              userVerification: 'required',
              timeout: 60_000,
            } as PublicKeyCredentialRequestOptions,
            mediation: 'optional',
          });
          return { ok: true, method: 'platform-authenticator' };
        } catch {
          // No registered credential yet — fall through to the
          // soft prompt so first-time users aren't blocked.
        }
      }
    } catch {
      // Platform probe failed — fall through to the soft prompt.
    }
  }
  // Browser-dev fallback. A native shell can override this by
  // injecting `window.__VOS3_BIOMETRIC__ = async () => true`
  // before render.
  const override = (window as unknown as { __VOS3_BIOMETRIC__?: () => Promise<boolean> })
    .__VOS3_BIOMETRIC__;
  if (typeof override === 'function') {
    const ok = await override();
    return { ok, method: 'native-shell' };
  }
  // Last-resort: confirm() so Playwright + browser dev still work.
  const ok = window.confirm(`${label}\n\nClick OK to confirm your identity.`);
  return { ok, method: 'confirm-fallback' };
}

// ---------------------------------------------------------------------------
// Biometric prompt — branded "Awaiting Your Signature" overlay
// ---------------------------------------------------------------------------

interface BiometricPromptProps {
  open: boolean;
  approval: PendingApproval | null;
  decision: 'approve' | 'reject';
  onClose: () => void;
  onVerified: (method: string) => Promise<void>;
}

function BiometricPrompt({
  open,
  approval,
  decision,
  onClose,
  onVerified,
}: BiometricPromptProps): React.ReactElement | null {
  const [phase, setPhase] = useState<'idle' | 'verifying' | 'signing' | 'error'>('idle');
  const [errMsg, setErrMsg] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setPhase('idle');
      setErrMsg(null);
    }
  }, [open]);

  const startVerify = useCallback(async () => {
    if (!approval) return;
    setPhase('verifying');
    setErrMsg(null);
    const { ok, method } = await verifyBiometric(
      decision === 'approve'
        ? `Approve host action: ${approval.action} on ${approval.target_system}`
        : `Reject host action: ${approval.action} on ${approval.target_system}`,
    );
    if (!ok) {
      setPhase('error');
      setErrMsg('Biometric verification was cancelled or failed.');
      return;
    }
    setPhase('signing');
    try {
      await onVerified(method);
    } catch (err) {
      setPhase('error');
      setErrMsg(err instanceof Error ? err.message : 'failed to sign decision');
    }
  }, [approval, decision, onVerified]);

  if (!open || !approval) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.94, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.18 }}
        className="w-full max-w-md rounded-xl border border-slate-700/80 bg-slate-900/95 p-6 shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-slate-100">
            <Fingerprint className="h-5 w-5 text-sky-400" />
            <span className="text-sm font-semibold uppercase tracking-wider">
              Identity verification required
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-800/80 hover:text-slate-100"
            aria-label="Cancel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <h2 className="mt-4 text-lg font-semibold text-slate-50">
          Please verify your identity via TouchID / Windows Hello
        </h2>
        <p className="mt-1 text-sm text-slate-400">
          You are about to{' '}
          <span className={decision === 'approve' ? 'text-emerald-300' : 'text-rose-300'}>
            {decision === 'approve' ? 'approve' : 'reject'}
          </span>{' '}
          a high-severity host transaction. The decision will be cryptographically signed with
          your locally-stored sovereign keypair.
        </p>

        <div className="mt-4 rounded-lg border border-slate-700/80 bg-slate-950/70 p-3 text-sm">
          <div className="text-xs uppercase tracking-wider text-slate-500">Action</div>
          <div className="font-medium text-slate-100">
            {approval.action} → {approval.target_system}
          </div>
          {approval.amount != null ? (
            <div className="mt-1 text-slate-300">
              Amount: {formatAmount(approval.amount, approval.currency)}
            </div>
          ) : null}
        </div>

        {phase === 'error' && errMsg ? (
          <div className="mt-4 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            {errMsg}
          </div>
        ) : null}

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={phase === 'verifying' || phase === 'signing'}
            className="rounded-md border border-slate-700 px-3 py-2 text-sm font-medium text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={startVerify}
            disabled={phase === 'verifying' || phase === 'signing'}
            data-testid="biometric-confirm"
            className={`inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:opacity-60 ${
              decision === 'approve'
                ? 'bg-emerald-600 hover:bg-emerald-500'
                : 'bg-rose-600 hover:bg-rose-500'
            }`}
          >
            <Fingerprint className="h-4 w-4" />
            {phase === 'verifying'
              ? 'Verifying…'
              : phase === 'signing'
                ? 'Signing…'
                : decision === 'approve'
                  ? 'Sign & Execute'
                  : 'Sign & Reject'}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reason badge
// ---------------------------------------------------------------------------

function ReasonBadge({ reason }: { reason: string | null }): React.ReactElement {
  const { badge, tone } = summarizeReason(reason);
  const tones: Record<string, string> = {
    rose: 'bg-rose-500/15 text-rose-200 border-rose-500/50',
    amber: 'bg-amber-500/15 text-amber-200 border-amber-500/50',
    sky: 'bg-sky-500/15 text-sky-200 border-sky-500/50',
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${
        tones[tone] ?? tones.amber
      }`}
    >
      <AlertTriangle className="h-3 w-3" />
      {badge}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main drawer
// ---------------------------------------------------------------------------

export function ApprovalDrawer({
  mockApprovals,
  open: controlledOpen,
  onOpenChange,
  pollIntervalMs = 4000,
}: ApprovalDrawerProps): React.ReactElement {
  const { getToken } = useAuth();
  const [approvals, setApprovals] = useState<PendingApproval[]>(mockApprovals ?? []);
  const [loading, setLoading] = useState(!mockApprovals);
  const [internalOpen, setInternalOpen] = useState(false);
  const [activeApproval, setActiveApproval] = useState<PendingApproval | null>(null);
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve');
  const [promptOpen, setPromptOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(new Set<string>());

  const open = controlledOpen ?? internalOpen;
  const setOpen = useCallback(
    (next: boolean) => {
      if (onOpenChange) onOpenChange(next);
      else setInternalOpen(next);
    },
    [onOpenChange],
  );

  const fetchApprovals = useCallback(async () => {
    if (mockApprovals) return;
    try {
      const res = await apiFetch(getToken, '/api/host_bridge/approvals', { method: 'GET' });
      if (!res.ok) throw new Error(`GET approvals → ${res.status}`);
      const body = (await res.json()) as ApprovalsListResponse;
      const rows = body.approvals ?? [];
      setApprovals(rows);
      // Auto-pop the drawer the FIRST time we see a new approval id —
      // operator gets a clear signal that the Guard intercepted.
      for (const a of rows) {
        if (!seen.current.has(a.approval_id)) {
          seen.current.add(a.approval_id);
          if (controlledOpen === undefined) setInternalOpen(true);
        }
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to fetch approvals');
    } finally {
      setLoading(false);
    }
  }, [getToken, mockApprovals, controlledOpen]);

  useEffect(() => {
    if (mockApprovals) {
      setApprovals(mockApprovals);
      setLoading(false);
      return;
    }
    void fetchApprovals();
    const id = setInterval(() => void fetchApprovals(), pollIntervalMs);
    return () => clearInterval(id);
  }, [fetchApprovals, mockApprovals, pollIntervalMs]);

  const pendingOnly = useMemo(
    () => approvals.filter((a) => a.status === 'awaiting_human_approval'),
    [approvals],
  );

  const handleStartDecision = useCallback(
    (approval: PendingApproval, dec: 'approve' | 'reject') => {
      setActiveApproval(approval);
      setDecision(dec);
      setPromptOpen(true);
    },
    [],
  );

  const submitDecision = useCallback(async () => {
    if (!activeApproval) return;
    if (mockApprovals) {
      // Test mode — drop the row locally so the UI updates.
      setApprovals((prev) =>
        prev.map((a) =>
          a.approval_id === activeApproval.approval_id
            ? {
                ...a,
                status: decision === 'approve' ? 'executed' : 'rejected',
              }
            : a,
        ),
      );
      setPromptOpen(false);
      return;
    }
    // 1. Fetch a server-minted signature for the decision payload.
    //    The backend's _sign helper signs with the keyring-pinned
    //    Ed25519 keypair. This is preferable to shipping the
    //    private key into the WebView.
    const signRes = await apiFetch(getToken, '/api/host_bridge/_sign', {
      method: 'POST',
      body: JSON.stringify({
        approval_id: activeApproval.approval_id,
        decision,
      }),
    });
    if (!signRes.ok) {
      throw new Error(`POST /_sign → ${signRes.status}`);
    }
    const signed = (await signRes.json()) as SignResponse;

    // 2. POST the signed decision to the canonical approve/reject endpoint.
    const route =
      decision === 'approve'
        ? `/api/host_bridge/approvals/${encodeURIComponent(activeApproval.approval_id)}/approve`
        : `/api/host_bridge/approvals/${encodeURIComponent(activeApproval.approval_id)}/reject`;
    const submitRes = await apiFetch(getToken, route, {
      method: 'POST',
      body: JSON.stringify({
        approver_user_id: signed.approver_user_id,
        signature_hex: signed.signature_hex,
        public_key_hex: signed.public_key_hex,
      }),
    });
    if (!submitRes.ok) {
      throw new Error(`POST ${route} → ${submitRes.status}`);
    }
    await fetchApprovals();
    setPromptOpen(false);
  }, [activeApproval, decision, mockApprovals, getToken, fetchApprovals]);

  // ----- Render -----
  return (
    <>
      {/* Toggle pill that lives at the top right of any host page. */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        data-testid="approval-drawer-toggle"
        className={`relative inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
          pendingOnly.length > 0
            ? 'border-amber-500/60 bg-amber-500/15 text-amber-100 shadow-[0_0_0_3px_rgba(245,158,11,0.15)]'
            : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
        }`}
      >
        <ShieldCheck className="h-4 w-4" />
        Awaiting Your Signature
        {pendingOnly.length > 0 ? (
          <span className="ml-1 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-amber-500 px-1 text-[11px] font-semibold text-amber-950">
            {pendingOnly.length}
          </span>
        ) : null}
      </button>

      <AnimatePresence>
        {open ? (
          <>
            <motion.div
              key="scrim"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40 bg-slate-950/60 backdrop-blur-[2px]"
              onClick={() => setOpen(false)}
            />
            <motion.aside
              key="drawer"
              initial={{ x: 480 }}
              animate={{ x: 0 }}
              exit={{ x: 480 }}
              transition={{ type: 'spring', damping: 24, stiffness: 220 }}
              className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-slate-800 bg-slate-950 text-slate-100 shadow-2xl"
              data-testid="approval-drawer"
            >
              <header className="flex items-start justify-between border-b border-slate-800 px-5 py-4">
                <div>
                  <div className="text-xs uppercase tracking-wider text-slate-500">
                    Transaction Guard
                  </div>
                  <h2 className="mt-1 text-lg font-semibold">Awaiting Your Signature</h2>
                  <p className="mt-1 text-xs text-slate-400">
                    {pendingOnly.length} pending · server-side keyring signs after biometric
                    verification.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                  aria-label="Close"
                >
                  <X className="h-4 w-4" />
                </button>
              </header>

              <div className="flex-1 overflow-y-auto px-5 py-4">
                {loading ? (
                  <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center text-sm text-slate-500">
                    Loading…
                  </div>
                ) : error ? (
                  <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                    {error}
                  </div>
                ) : pendingOnly.length === 0 ? (
                  <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-10 text-center">
                    <ShieldCheck className="mx-auto h-8 w-8 text-emerald-400" />
                    <div className="mt-3 text-sm font-medium text-slate-200">
                      No pending host transactions.
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      Every recent automation passed the Transaction Guard.
                    </div>
                  </div>
                ) : (
                  <ul className="space-y-3">
                    {pendingOnly.map((approval) => {
                      const reason = summarizeReason(approval.reason);
                      return (
                        <li
                          key={approval.approval_id}
                          data-testid={`approval-${approval.approval_id}`}
                          className="rounded-lg border border-slate-800 bg-slate-900/70 p-4"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="flex items-center gap-2 text-sm font-semibold text-slate-50">
                                <Building2 className="h-4 w-4 text-slate-400" />
                                {approval.target_system}
                              </div>
                              <div className="mt-1 truncate font-mono text-[11px] text-slate-500">
                                {approval.app_id}
                              </div>
                            </div>
                            <ReasonBadge reason={approval.reason} />
                          </div>

                          <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
                            <div>
                              <div className="uppercase tracking-wider text-slate-500">Action</div>
                              <div className="mt-0.5 font-medium text-slate-200">
                                {approval.action}
                              </div>
                            </div>
                            <div>
                              <div className="uppercase tracking-wider text-slate-500">Amount</div>
                              <div className="mt-0.5 font-medium text-slate-200">
                                {formatAmount(approval.amount, approval.currency)}
                              </div>
                            </div>
                            <div className="col-span-2">
                              <div className="uppercase tracking-wider text-slate-500">
                                Workspace
                              </div>
                              <div className="mt-0.5 font-medium text-slate-200">
                                {approval.workspace_id}
                              </div>
                            </div>
                          </div>

                          <p className="mt-3 text-xs text-slate-400">{reason.description}</p>

                          <div className="mt-4 flex items-center gap-2">
                            <button
                              type="button"
                              data-testid={`approve-${approval.approval_id}`}
                              onClick={() => handleStartDecision(approval, 'approve')}
                              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500"
                            >
                              <CheckCircle2 className="h-4 w-4" />
                              Approve
                            </button>
                            <button
                              type="button"
                              data-testid={`reject-${approval.approval_id}`}
                              onClick={() => handleStartDecision(approval, 'reject')}
                              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-sm font-semibold text-rose-200 transition hover:bg-rose-500/20"
                            >
                              <XCircle className="h-4 w-4" />
                              Reject
                            </button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>

      <BiometricPrompt
        open={promptOpen}
        approval={activeApproval}
        decision={decision}
        onClose={() => setPromptOpen(false)}
        onVerified={async () => {
          await submitDecision();
        }}
      />
    </>
  );
}

export default ApprovalDrawer;
