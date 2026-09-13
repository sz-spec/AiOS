// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P7.1 — Sovereign Command Center · dashboard portal.
//
// One page unifies the three operator-facing surfaces:
//   1. WorkflowBuilder  — live DAG for the selected orchestrator run
//   2. ApprovalDrawer   — slide-out HITL panel for Transaction Guard holds
//   3. P2PNodeMap       — live LAN mesh + air-gap integrity status
//
// Run selection
// -------------
// The page accepts `?run=<run_id>` to pin a particular workflow. Without
// that query param, it ships with a curated mock so the page never
// flashes a "no run" empty state in browser-dev (the backend's GET
// /api/orchestrator/workflows is a different surface that the operator
// uses via the bundled WorkflowBuilder).

'use client';

import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Activity, Cpu, ShieldCheck } from 'lucide-react';

import { ApprovalDrawer, type PendingApproval } from '@/components/orchestrator/ApprovalDrawer';
import { P2PNodeMap, type PeerNode } from '@/components/orchestrator/P2PNodeMap';
import {
  WorkflowBuilder,
  type WorkflowStatusView,
} from '@/components/orchestrator/WorkflowBuilder';

// ---------------------------------------------------------------------------
// Mock data — used when no `?run=` query is supplied. Keeps the dashboard
// rendering in storybook + Playwright without depending on a live backend.
// ---------------------------------------------------------------------------

const MOCK_WORKFLOW: WorkflowStatusView = {
  run_id: 'run-demo-0001-ffea',
  workspace_id: 'ws-tahoe-demo',
  name: 'Vendor reconciliation',
  status: 'running',
  error: null,
  steps: [
    {
      id: 'step-1',
      step_id: 'extract',
      app_id: 'app-extract-sap',
      depends_on: [],
      status: 'completed',
      task: { source: 'SAP_S4HANA', vendor_id: 'V100' },
    },
    {
      id: 'step-2',
      step_id: 'normalize',
      app_id: 'app-normalize',
      depends_on: ['extract'],
      status: 'running',
      task: { mode: 'usd-convert' },
    },
    {
      id: 'step-3',
      step_id: 'reconcile',
      app_id: 'app-recon',
      depends_on: ['normalize'],
      status: 'pending',
      task: { target: 'ledger' },
    },
    {
      id: 'step-4',
      step_id: 'notify',
      app_id: 'app-notify',
      depends_on: ['reconcile'],
      status: 'pending',
      task: { channel: 'inbox' },
    },
  ],
};

const MOCK_APPROVALS: PendingApproval[] = [
  {
    approval_id: 'approval-demo-0001',
    app_id: 'app-procurement-bot',
    workspace_id: 'ws-tahoe-demo',
    target_system: 'SAP_GUI',
    action: 'create_purchase_order',
    amount: 15_000,
    currency: 'USD',
    reason: 'amount_over_threshold:15000.0 > 5000.0',
    status: 'awaiting_human_approval',
    approver_id: null,
    result: null,
    created_at: Date.now() - 25_000,
    updated_at: Date.now() - 25_000,
  },
];

const MOCK_PEERS: PeerNode[] = [
  {
    node_id: 'node-alpha-7a3f1b29c40d',
    workspace_id: 'ws-tahoe-demo',
    host: '127.0.0.1',
    sync_port: 13371,
    status: 'active',
    verified: true,
    first_seen_at: Date.now() - 240_000,
    last_seen_at: Date.now() - 2_000,
  },
  {
    node_id: 'node-bravo-9e22f4d68a17',
    workspace_id: 'ws-tahoe-demo',
    host: '127.0.0.1',
    sync_port: 13372,
    status: 'active',
    verified: true,
    first_seen_at: Date.now() - 180_000,
    last_seen_at: Date.now() - 6_000,
  },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function useQueryParam(name: string): string | null {
  const [value, setValue] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    setValue(params.get(name));
  }, [name]);
  return value;
}

export default function CommandCenterPage(): React.ReactElement {
  const runId = useQueryParam('run');
  const useMocks = !runId;

  const headerSubtitle = useMemo(
    () =>
      useMocks
        ? 'Operator preview — pass ?run=<run_id> to bind to a live workflow.'
        : 'Live workflow status · ApprovalDrawer auto-pops on any Transaction Guard hold.',
    [useMocks],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 px-8 py-6">
        <div>
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-500">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
            Sovereign Command Center
          </div>
          <h1 className="mt-1 text-2xl font-semibold">Workflow Operations</h1>
          <p className="mt-1 text-sm text-slate-400">{headerSubtitle}</p>
        </div>
        <ApprovalDrawer mockApprovals={useMocks ? MOCK_APPROVALS : undefined} />
      </header>

      <main className="grid grid-cols-1 gap-6 p-8 xl:grid-cols-[1.6fr_1fr]">
        <section className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <Activity className="h-4 w-4 text-sky-400" />
            Active workflow
          </div>
          {useMocks ? (
            <WorkflowBuilder runId={MOCK_WORKFLOW.run_id} mockStatus={MOCK_WORKFLOW} />
          ) : (
            <WorkflowBuilder runId={runId!} />
          )}
        </section>

        <section className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <Cpu className="h-4 w-4 text-emerald-400" />
            Local mesh peers
          </div>
          <P2PNodeMap mockPeers={useMocks ? MOCK_PEERS : undefined} />
        </section>
      </main>
    </div>
  );
}
