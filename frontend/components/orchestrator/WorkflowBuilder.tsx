// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P7.1 — Sovereign Command Center · Workflow Builder (DAG viewer).
//
// Live visualization of an orchestrator run:
//   - Polls GET /api/orchestrator/workflows/{runId}/status
//   - Renders each step as a card laid out with dagre
//   - Connects dependsOn → step with @xyflow edges
//   - Animates the active running step
//   - Quarantine state ("signature mismatch — auto-quarantined") gets a
//     loud red border + warning icon so the operator can't miss it
//
// Polling lifecycle:
//   - The first request happens immediately on mount.
//   - Subsequent polls fire every `pollIntervalMs` (default 1.5s).
//   - The poll is paused as soon as the run reaches a terminal status
//     (completed / failed / cancelled / quarantined).
//
// Test discipline:
//   - The component accepts an optional `mockStatus` prop. When supplied,
//     the API call is skipped — useful for storybook / Playwright / vitest
//     snapshot tests that don't have the FastAPI backend running.

'use client';

import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import {
  Background,
  BackgroundVariant,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
} from '@xyflow/react';
import { AlertTriangle, CheckCircle2, Clock, Loader2, ShieldAlert, XCircle } from 'lucide-react';

import { apiFetch } from '@/lib/api-client';
import { layoutWithDagre } from '@/components/workflows/dagreLayout';

import '@xyflow/react/dist/style.css';

// ---------------------------------------------------------------------------
// Types — mirror the backend's run_status shape
// ---------------------------------------------------------------------------

export type StepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'quarantined';

export interface WorkflowStepView {
  id: string;
  step_id: string;
  app_id: string;
  depends_on: string[];
  status: StepStatus | string;
  task?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  stderr?: string | null;
  error?: string | null;
  created_at?: number;
  updated_at?: number;
}

export interface WorkflowStatusView {
  run_id: string;
  workspace_id: string;
  name?: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | string;
  error?: string | null;
  steps: WorkflowStepView[];
}

interface WorkflowBuilderProps {
  runId: string;
  pollIntervalMs?: number;
  /**
   * Test escape hatch — when supplied, skip the API call and render
   * this status verbatim. Used by Playwright + the storybook page.
   */
  mockStatus?: WorkflowStatusView;
}

// ---------------------------------------------------------------------------
// Status pill — tiny helper used by both the run header and the step card
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, { bg: string; text: string; border: string; ring?: string }> = {
  pending: { bg: 'bg-slate-700/40', text: 'text-slate-300', border: 'border-slate-600/60' },
  running: {
    bg: 'bg-sky-500/15',
    text: 'text-sky-200',
    border: 'border-sky-500/60',
    ring: 'ring-sky-500/40',
  },
  completed: { bg: 'bg-emerald-500/15', text: 'text-emerald-300', border: 'border-emerald-500/60' },
  failed: { bg: 'bg-rose-500/15', text: 'text-rose-300', border: 'border-rose-500/60' },
  cancelled: { bg: 'bg-amber-500/15', text: 'text-amber-300', border: 'border-amber-500/60' },
  quarantined: { bg: 'bg-rose-500/15', text: 'text-rose-100', border: 'border-rose-400' },
};

function StatusIcon({ status }: { status: string }): React.ReactElement | null {
  if (status === 'pending') return <Clock className="h-3.5 w-3.5" />;
  if (status === 'running') return <Loader2 className="h-3.5 w-3.5 animate-spin" />;
  if (status === 'completed') return <CheckCircle2 className="h-3.5 w-3.5" />;
  if (status === 'failed') return <XCircle className="h-3.5 w-3.5" />;
  if (status === 'cancelled') return <AlertTriangle className="h-3.5 w-3.5" />;
  if (status === 'quarantined') return <ShieldAlert className="h-3.5 w-3.5" />;
  return null;
}

export function StatusBadge({ status }: { status: string }): React.ReactElement {
  const palette = STATUS_COLORS[status] ?? STATUS_COLORS.pending;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${palette.bg} ${palette.text} ${palette.border}`}
    >
      <StatusIcon status={status} />
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Quarantine detection — a "failed" step whose error starts with the magic
// substring "signature mismatch" is rendered as the special quarantine state.
// ---------------------------------------------------------------------------

function detectQuarantine(step: WorkflowStepView): boolean {
  const err = (step.error ?? '').toLowerCase();
  return err.includes('signature mismatch') || err.includes('workflow tampered');
}

// ---------------------------------------------------------------------------
// xyflow node renderer
// ---------------------------------------------------------------------------

function StepCard({ data }: { data: WorkflowStepView & { quarantined: boolean } }): React.ReactElement {
  const palette = STATUS_COLORS[data.status as string] ?? STATUS_COLORS.pending;
  const quarantineCls = data.quarantined
    ? 'border-rose-500 shadow-[0_0_0_3px_rgba(244,63,94,0.25)]'
    : `${palette.border}`;

  const taskPreview = useMemo(() => {
    if (!data.task) return null;
    const stringified = JSON.stringify(data.task);
    return stringified.length > 90 ? `${stringified.slice(0, 87)}…` : stringified;
  }, [data.task]);

  return (
    <div
      data-testid={`step-card-${data.step_id}`}
      className={`relative w-[260px] rounded-lg border bg-slate-900/95 px-4 py-3 text-left shadow-lg ${quarantineCls} ${palette.ring ? `ring-2 ${palette.ring}` : ''}`}
    >
      {data.quarantined ? (
        <div className="absolute -top-2 -right-2 flex h-6 w-6 items-center justify-center rounded-full bg-rose-500 text-white shadow">
          <ShieldAlert className="h-3.5 w-3.5" />
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-sm font-semibold text-slate-100">{data.step_id}</div>
        <StatusBadge status={data.quarantined ? 'quarantined' : (data.status as string)} />
      </div>
      <div className="mt-1 truncate font-mono text-[11px] uppercase tracking-wide text-slate-500">
        {data.app_id.slice(0, 12)}…
      </div>
      {taskPreview ? (
        <div className="mt-2 truncate rounded bg-slate-950/60 px-2 py-1 font-mono text-[10.5px] text-slate-400">
          {taskPreview}
        </div>
      ) : null}
      {data.quarantined ? (
        <div className="mt-2 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          ⚠️ Workflow signature mismatch — auto-quarantined. The orchestrator
          refused to execute this run. Review the audit trail.
        </div>
      ) : data.error ? (
        <div className="mt-2 truncate rounded border border-rose-500/30 bg-rose-500/5 px-2 py-1 font-mono text-[10.5px] text-rose-300">
          {data.error}
        </div>
      ) : null}
    </div>
  );
}

const NODE_TYPES = {
  step: ({ data }: { data: unknown }) => (
    <StepCard data={data as WorkflowStepView & { quarantined: boolean }} />
  ),
};

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
  'quarantined',
]);

export function WorkflowBuilder({
  runId,
  pollIntervalMs = 1500,
  mockStatus,
}: WorkflowBuilderProps): React.ReactElement {
  const { getToken } = useAuth();
  const [status, setStatus] = useState<WorkflowStatusView | null>(mockStatus ?? null);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    if (mockStatus) return; // tests
    try {
      const res = await apiFetch(
        getToken,
        `/api/orchestrator/workflows/${encodeURIComponent(runId)}/status`,
        { method: 'GET' },
      );
      if (!res.ok) {
        throw new Error(`GET workflow status → ${res.status}`);
      }
      const body = (await res.json()) as WorkflowStatusView;
      setStatus(body);
      setError(null);
      if (TERMINAL_RUN_STATUSES.has(body.status)) {
        stopRef.current = true;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to fetch workflow status');
    }
  }, [getToken, runId, mockStatus]);

  useEffect(() => {
    if (mockStatus) {
      setStatus(mockStatus);
      return;
    }
    stopRef.current = false;
    void fetchStatus();
    const id = setInterval(() => {
      if (stopRef.current) return;
      void fetchStatus();
    }, pollIntervalMs);
    return () => clearInterval(id);
  }, [fetchStatus, pollIntervalMs, mockStatus]);

  // ----- DAG graph derivation -----
  const { nodes, edges } = useMemo(() => {
    if (!status) return { nodes: [] as Node[], edges: [] as Edge[] };
    const ns: Node[] = status.steps.map((step) => ({
      id: step.step_id,
      type: 'step',
      position: { x: 0, y: 0 }, // dagre fills this in
      data: { ...step, quarantined: detectQuarantine(step) } as unknown as Record<
        string,
        unknown
      >,
      width: 260,
      height: 120,
    }));
    const es: Edge[] = [];
    for (const step of status.steps) {
      for (const dep of step.depends_on) {
        es.push({
          id: `${dep}→${step.step_id}`,
          source: dep,
          target: step.step_id,
          animated: step.status === 'running',
          style: { stroke: '#475569', strokeWidth: 1.5 },
        });
      }
    }
    const laidOut = layoutWithDagre(ns, es, 'LR');
    return { nodes: laidOut, edges: es };
  }, [status]);

  // ----- Render -----
  if (error && !status) {
    return (
      <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
        Failed to load workflow status: {error}
      </div>
    );
  }
  if (!status) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/40 text-sm text-slate-500">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading workflow…
      </div>
    );
  }

  const runQuarantined =
    status.status === 'failed' &&
    ((status.error ?? '').toLowerCase().includes('signature mismatch') ||
      (status.error ?? '').toLowerCase().includes('workflow tampered'));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs uppercase tracking-wider text-slate-500">Run</span>
            <code className="font-mono text-sm text-slate-200">{status.run_id.slice(0, 18)}…</code>
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-slate-400">
            <span>workspace: {status.workspace_id}</span>
            <span>•</span>
            <span>{status.steps.length} steps</span>
          </div>
        </div>
        <StatusBadge status={runQuarantined ? 'quarantined' : status.status} />
      </div>

      {runQuarantined ? (
        <div className="rounded-lg border border-rose-500/50 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
          <div className="flex items-start gap-2">
            <ShieldAlert className="mt-0.5 h-5 w-5 flex-shrink-0 text-rose-400" />
            <div>
              <div className="font-semibold">Workflow auto-quarantined</div>
              <div className="mt-1 text-rose-200/90">
                {status.error ??
                  'The orchestrator detected a signature mismatch on the persisted run and refused to execute it. Review the security audit trail for the matching `workflow_tampered` event.'}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <div className="h-[460px] overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            proOptions={{ hideAttribution: true }}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            panOnScroll
            zoomOnScroll
          >
            <Background variant={BackgroundVariant.Dots} gap={20} color="#1e293b" />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </div>
  );
}

export default WorkflowBuilder;
