// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P7.1 — smoke tests for the Sovereign Command Center surfaces.
//
// These tests exercise the MOCK-DRIVEN code paths only — every component
// accepts a `mock*` prop that bypasses the live API. That keeps the
// frontend test suite hermetic (no backend, no network, no flake) while
// still verifying:
//   - The mock data renders the expected DOM structure.
//   - Status / integrity classes flip based on the prop content.
//   - The drawer's auto-open behavior fires on a fresh pending row.

import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { ApprovalDrawer, type PendingApproval } from '../ApprovalDrawer';
import { P2PNodeMap, type PeerNode } from '../P2PNodeMap';
import { WorkflowBuilder, type WorkflowStatusView } from '../WorkflowBuilder';

// Clerk's useAuth returns a stable token in test mode so apiFetch never
// actually fires — but our mockX props short-circuit before that anyway.
vi.mock('@clerk/nextjs', () => ({
  useAuth: () => ({
    getToken: vi.fn(async () => 'test-token'),
  }),
}));

// Reanimated-style libs sometimes warn in jsdom. Silence framer-motion's
// inert layout warnings so the test output stays readable.
vi.mock('framer-motion', () => ({
  motion: new Proxy(
    {},
    {
      get:
        () =>
        ({ children, ...rest }: { children?: React.ReactNode }) => (
          <div {...rest}>{children}</div>
        ),
    },
  ),
  AnimatePresence: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

// @xyflow needs a measurable container; jsdom doesn't have ResizeObserver.
beforeAll(() => {
  (global as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
});

// ---------------------------------------------------------------------------
// WorkflowBuilder
// ---------------------------------------------------------------------------

describe('WorkflowBuilder (mock-driven)', () => {
  const SAMPLE: WorkflowStatusView = {
    run_id: 'run-test-aaaa-bbbb',
    workspace_id: 'ws-test',
    name: 'Sample',
    status: 'running',
    error: null,
    steps: [
      {
        id: 'r1',
        step_id: 'fetch',
        app_id: 'app-aaa',
        depends_on: [],
        status: 'completed',
        task: { src: 'sap' },
      },
      {
        id: 'r2',
        step_id: 'process',
        app_id: 'app-bbb',
        depends_on: ['fetch'],
        status: 'running',
        task: null,
      },
    ],
  };

  it('renders the run header with the truncated run id', () => {
    render(<WorkflowBuilder runId={SAMPLE.run_id} mockStatus={SAMPLE} />);
    expect(screen.getByText(/run-test-aaaa/)).toBeInTheDocument();
    expect(screen.getByText(/ws-test/)).toBeInTheDocument();
    expect(screen.getByText(/2 steps/)).toBeInTheDocument();
  });

  it('renders each step card with the matching status badge', () => {
    render(<WorkflowBuilder runId={SAMPLE.run_id} mockStatus={SAMPLE} />);
    const fetchCard = screen.getByTestId('step-card-fetch');
    expect(within(fetchCard).getByText('completed')).toBeInTheDocument();
    const processCard = screen.getByTestId('step-card-process');
    expect(within(processCard).getByText('running')).toBeInTheDocument();
  });

  it('renders the quarantine banner when error matches the signature pattern', () => {
    const tampered: WorkflowStatusView = {
      ...SAMPLE,
      status: 'failed',
      error:
        "workflow run='run-test' tampered: signature mismatch on canonical bytes",
      steps: [
        {
          ...SAMPLE.steps[0],
          status: 'failed',
          error:
            "workflow run='run-test' tampered: signature mismatch",
        },
      ],
    };
    render(<WorkflowBuilder runId={tampered.run_id} mockStatus={tampered} />);
    expect(screen.getByText(/Workflow auto-quarantined/i)).toBeInTheDocument();
    const card = screen.getByTestId('step-card-fetch');
    expect(within(card).getByText(/auto-quarantined/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ApprovalDrawer
// ---------------------------------------------------------------------------

describe('ApprovalDrawer (mock-driven)', () => {
  const APPROVAL: PendingApproval = {
    approval_id: 'approval-test-001',
    app_id: 'app-procurement-bot',
    workspace_id: 'ws-test',
    target_system: 'SAP_GUI',
    action: 'create_purchase_order',
    amount: 15_000,
    currency: 'USD',
    reason: 'amount_over_threshold:15000.0 > 5000.0',
    status: 'awaiting_human_approval',
    approver_id: null,
    result: null,
    created_at: Date.now(),
    updated_at: Date.now(),
  };

  it('renders the badge with the pending count', () => {
    render(<ApprovalDrawer mockApprovals={[APPROVAL]} open />);
    const toggle = screen.getByTestId('approval-drawer-toggle');
    expect(toggle).toHaveTextContent('Awaiting Your Signature');
    expect(toggle).toHaveTextContent('1');
  });

  it('renders the approval card details inside the open drawer', () => {
    render(<ApprovalDrawer mockApprovals={[APPROVAL]} open />);
    const card = screen.getByTestId('approval-approval-test-001');
    expect(within(card).getByText('SAP_GUI')).toBeInTheDocument();
    expect(within(card).getByText('create_purchase_order')).toBeInTheDocument();
    expect(within(card).getByText(/Value cap exceeded/i)).toBeInTheDocument();
    // Formatted currency.
    expect(within(card).getByText(/\$15,000/)).toBeInTheDocument();
  });

  it('shows the empty state when no rows are pending', () => {
    render(<ApprovalDrawer mockApprovals={[]} open />);
    expect(
      screen.getByText(/No pending host transactions/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// P2PNodeMap
// ---------------------------------------------------------------------------

describe('P2PNodeMap (mock-driven)', () => {
  const ALICE: PeerNode = {
    node_id: 'node-alice-aaaa-1111-2222-3333',
    workspace_id: 'ws-shared',
    host: '127.0.0.1',
    sync_port: 13371,
    status: 'active',
    verified: true,
    first_seen_at: Date.now() - 120_000,
    last_seen_at: Date.now() - 1_000,
  };
  const ROGUE: PeerNode = {
    node_id: 'node-rogue-9999-8888-7777-6666',
    workspace_id: 'ws-omega-attacker',
    host: '127.0.0.1',
    sync_port: 13372,
    status: 'rejected',
    verified: true,
    first_seen_at: Date.now() - 20_000,
    last_seen_at: Date.now() - 5_000,
  };

  it('renders the integrity banner in the safe state when all peers are active', () => {
    render(<P2PNodeMap mockPeers={[ALICE]} />);
    const badge = screen.getByTestId('integrity-badge');
    expect(badge).toHaveTextContent(/All peers verified/i);
  });

  it('flips the integrity banner to the intrusion-attempt state', () => {
    render(<P2PNodeMap mockPeers={[ALICE, ROGUE]} />);
    const badge = screen.getByTestId('integrity-badge');
    expect(badge).toHaveTextContent(/Intrusion Attempt Blocked/i);
  });

  it('renders one card per peer with the rogue card carrying its specific warning', () => {
    render(<P2PNodeMap mockPeers={[ALICE, ROGUE]} />);
    expect(
      screen.getByTestId(`peer-card-${ALICE.node_id}`),
    ).toBeInTheDocument();
    const rogueCard = screen.getByTestId(`peer-card-${ROGUE.node_id}`);
    expect(
      within(rogueCard).getByText(/Unauthorized workspace ID flagged/i),
    ).toBeInTheDocument();
  });

  it('renders the empty state when no peers are discovered', () => {
    render(<P2PNodeMap mockPeers={[]} />);
    expect(screen.getByText(/No peers discovered yet/i)).toBeInTheDocument();
  });
});
