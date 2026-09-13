// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P7.1 — Sovereign Command Center · P2P Mesh Node Map.
//
// Visualizes every peer the local node has discovered on the LAN.
// Two-pane layout:
//   - Header: aggregate health (active count, last-rogue-attempt, integrity
//     status).
//   - Grid: one card per peer with status dot + workspace + host:port +
//     last-seen freshness + verified badge.
//
// The "Air-Gap Integrity Status" badge flips to a red intrusion-attempt
// state whenever any peer is in `status='rejected'`. Operators see a
// clear, plain-English warning that an unauthorized workspace_id was
// flagged.

'use client';

import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import {
  AlertOctagon,
  Cpu,
  Network,
  RadioTower,
  ShieldCheck,
  Wifi,
  WifiOff,
} from 'lucide-react';

import { apiFetch } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types — mirror /api/p2p/peers response
// ---------------------------------------------------------------------------

export interface PeerNode {
  node_id: string;
  workspace_id: string;
  host: string;
  sync_port: number;
  status: 'active' | 'rejected' | 'stale' | string;
  verified: boolean;
  first_seen_at: number;
  last_seen_at: number;
}

interface PeersResponse {
  peers: PeerNode[];
}

interface P2PNodeMapProps {
  mockPeers?: PeerNode[];
  pollIntervalMs?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(ms: number): string {
  const delta = Math.max(0, Date.now() - ms);
  if (delta < 5000) return 'just now';
  if (delta < 60_000) return `${Math.floor(delta / 1000)}s ago`;
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  return `${Math.floor(delta / 3_600_000)}h ago`;
}

function statusDot(status: string, verified: boolean): string {
  if (!verified) return 'bg-slate-500';
  if (status === 'active') return 'bg-emerald-400 shadow-[0_0_0_3px_rgba(52,211,153,0.25)]';
  if (status === 'stale') return 'bg-amber-400';
  if (status === 'rejected') return 'bg-rose-500 shadow-[0_0_0_3px_rgba(244,63,94,0.3)]';
  return 'bg-slate-500';
}

function statusLabel(status: string): string {
  if (status === 'active') return 'Synced';
  if (status === 'stale') return 'Idle';
  if (status === 'rejected') return 'Blocked';
  return status;
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function P2PNodeMap({
  mockPeers,
  pollIntervalMs = 5000,
}: P2PNodeMapProps): React.ReactElement {
  const { getToken } = useAuth();
  const [peers, setPeers] = useState<PeerNode[]>(mockPeers ?? []);
  const [loading, setLoading] = useState(!mockPeers);
  const [error, setError] = useState<string | null>(null);

  const fetchPeers = useCallback(async () => {
    if (mockPeers) return;
    try {
      const res = await apiFetch(getToken, '/api/p2p/peers', { method: 'GET' });
      if (!res.ok) throw new Error(`GET /api/p2p/peers → ${res.status}`);
      const body = (await res.json()) as PeersResponse;
      setPeers(body.peers ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to fetch peers');
    } finally {
      setLoading(false);
    }
  }, [getToken, mockPeers]);

  useEffect(() => {
    if (mockPeers) {
      setPeers(mockPeers);
      setLoading(false);
      return;
    }
    void fetchPeers();
    const id = setInterval(() => void fetchPeers(), pollIntervalMs);
    return () => clearInterval(id);
  }, [fetchPeers, mockPeers, pollIntervalMs]);

  // ----- Aggregates -----
  const aggregate = useMemo(() => {
    const active = peers.filter((p) => p.status === 'active' && p.verified).length;
    const stale = peers.filter((p) => p.status === 'stale').length;
    const rejected = peers.filter((p) => p.status === 'rejected').length;
    return { active, stale, rejected, total: peers.length };
  }, [peers]);

  const intrusion = aggregate.rejected > 0;

  // ----- Render -----
  return (
    <div className="space-y-4">
      <div
        className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 ${
          intrusion
            ? 'border-rose-500/60 bg-rose-500/10'
            : 'border-emerald-500/40 bg-emerald-500/5'
        }`}
        data-testid="integrity-badge"
      >
        <div className="flex items-center gap-3">
          {intrusion ? (
            <AlertOctagon className="h-6 w-6 text-rose-400" />
          ) : (
            <ShieldCheck className="h-6 w-6 text-emerald-400" />
          )}
          <div>
            <div className="text-xs uppercase tracking-wider text-slate-400">
              Air-Gap Integrity Status
            </div>
            <div
              className={`text-sm font-semibold ${
                intrusion ? 'text-rose-100' : 'text-emerald-100'
              }`}
            >
              {intrusion
                ? `⚠️ Intrusion Attempt Blocked — ${aggregate.rejected} unauthorized workspace ID${aggregate.rejected === 1 ? '' : 's'} flagged.`
                : 'All peers verified · No rogue node attempts detected.'}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3 text-center text-xs">
          <div>
            <div className="text-lg font-semibold text-emerald-300">{aggregate.active}</div>
            <div className="uppercase tracking-wider text-slate-500">Active</div>
          </div>
          <div>
            <div className="text-lg font-semibold text-amber-300">{aggregate.stale}</div>
            <div className="uppercase tracking-wider text-slate-500">Idle</div>
          </div>
          <div>
            <div className="text-lg font-semibold text-rose-300">{aggregate.rejected}</div>
            <div className="uppercase tracking-wider text-slate-500">Blocked</div>
          </div>
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-10 text-center text-sm text-slate-500">
          Probing the mesh…
        </div>
      ) : peers.length === 0 ? (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-12 text-center">
          <RadioTower className="mx-auto h-8 w-8 text-slate-500" />
          <div className="mt-3 text-sm font-medium text-slate-300">
            No peers discovered yet.
          </div>
          <div className="mt-1 text-xs text-slate-500">
            The local node is broadcasting; peers will appear as soon as they
            answer with a verified beacon.
          </div>
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {peers.map((peer) => {
            const rejected = peer.status === 'rejected';
            return (
              <li
                key={peer.node_id}
                data-testid={`peer-card-${peer.node_id}`}
                className={`rounded-lg border bg-slate-900/60 p-4 ${
                  rejected
                    ? 'border-rose-500/60 bg-rose-500/5'
                    : 'border-slate-800'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className={`inline-flex h-3 w-3 rounded-full ${statusDot(
                        peer.status,
                        peer.verified,
                      )}`}
                    />
                    <div className="text-sm font-semibold text-slate-100">
                      {statusLabel(peer.status)}
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-950/40 px-2 py-0.5 text-[10.5px] uppercase tracking-wider text-slate-400">
                    {peer.verified ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                    {peer.verified ? 'verified' : 'unsigned'}
                  </span>
                </div>

                <div className="mt-3 flex items-start gap-2 text-xs">
                  <Cpu className="mt-0.5 h-3.5 w-3.5 text-slate-500" />
                  <div className="flex-1">
                    <div className="font-mono text-[11px] text-slate-300">
                      {peer.node_id.slice(0, 18)}…
                    </div>
                    <div className="mt-0.5 text-[10.5px] uppercase tracking-wider text-slate-500">
                      Node ID
                    </div>
                  </div>
                </div>

                <div className="mt-2 flex items-start gap-2 text-xs">
                  <Network className="mt-0.5 h-3.5 w-3.5 text-slate-500" />
                  <div className="flex-1">
                    <div className="font-mono text-[11px] text-slate-300">
                      {peer.host}:{peer.sync_port}
                    </div>
                    <div className="mt-0.5 text-[10.5px] uppercase tracking-wider text-slate-500">
                      Address
                    </div>
                  </div>
                </div>

                <div
                  className={`mt-3 rounded border px-2 py-1 text-[11px] ${
                    rejected
                      ? 'border-rose-500/40 bg-rose-500/10 text-rose-200'
                      : 'border-slate-700/60 bg-slate-950/40 text-slate-300'
                  }`}
                >
                  <span className="uppercase tracking-wider text-slate-500">
                    Workspace:{' '}
                  </span>
                  <span className="font-mono">{peer.workspace_id}</span>
                </div>

                {rejected ? (
                  <div className="mt-3 rounded-md border border-rose-500/50 bg-rose-500/15 px-2 py-1.5 text-[11px] text-rose-100">
                    Unauthorized workspace ID flagged. The local sovereign
                    guard blocked this peer from joining the mesh.
                  </div>
                ) : null}

                <div className="mt-3 flex items-center justify-between text-[11px] text-slate-500">
                  <span>last seen {timeAgo(peer.last_seen_at)}</span>
                  <span>first seen {timeAgo(peer.first_seen_at)}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default P2PNodeMap;
