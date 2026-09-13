// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P5.1 — Sovereign App Dashboard.
//
// Operator-facing surface for managing third-party apps installed
// on this device:
//   - Lists every row in the `apps` SQLite table.
//   - Shows a status badge (active / isolated / disabled).
//   - Lets the operator isolate or reactivate an app inline.
//   - Surfaces the per-app PermissionControls and the global
//     SovereignAuditTrail panel.
//
// Authentication: the operator is signed in via Clerk (the bearer
// token reaches the backend through `apiFetch`). The audit panel's
// poll endpoint is public-shaped today; a tighter gate lands in a
// future P5.x patch.

'use client';

import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '@clerk/nextjs';

import { apiFetch } from '@/lib/api-client';
import { AppPermissionControls } from '@/components/apps/AppPermissionControls';
import { AppFileExplorer } from '@/components/apps/AppFileExplorer';
import { AppFileUploadZone } from '@/components/apps/AppFileUploadZone';
import { SovereignAuditTrail } from '@/components/apps/SovereignAuditTrail';

interface AppRecord {
  id: string;
  name: string;
  version: string;
  status: 'active' | 'isolated' | 'disabled' | string;
  manifest: {
    name: string;
    version: string;
    scopes: string[];
    restrictions: string[];
    config?: Record<string, unknown> | null;
  };
  createdAt: number;
  updatedAt: number;
}

interface AppsListResponse {
  apps: AppRecord[];
  count: number;
}

function StatusBadge({ status }: { status: string }): React.ReactElement {
  // Color choices match the directive — green/orange/neutral.
  const cls = useMemo(() => {
    if (status === 'active') {
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40';
    }
    if (status === 'isolated') {
      return 'bg-amber-500/15 text-amber-300 border-amber-500/40';
    }
    return 'bg-slate-500/15 text-slate-300 border-slate-500/40';
  }, [status]);
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${cls}`}
    >
      {status}
    </span>
  );
}

export default function AppsDashboardPage(): React.ReactElement {
  const { getToken } = useAuth();
  const [apps, setApps] = useState<AppRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const fetchApps = useCallback(async () => {
    setError(null);
    try {
      const res = await apiFetch(getToken, '/api/apps', { method: 'GET' });
      if (!res.ok) {
        throw new Error(`GET /api/apps → ${res.status}`);
      }
      const body = (await res.json()) as AppsListResponse;
      setApps(body.apps ?? []);
      // Keep the current selection if it still exists, else pick the first row.
      if (body.apps.length > 0) {
        setSelectedId((prev) => {
          if (prev && body.apps.some((a) => a.id === prev)) {
            return prev;
          }
          return body.apps[0].id;
        });
      } else {
        setSelectedId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to fetch apps');
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchApps();
  }, [fetchApps]);

  const toggleIsolation = useCallback(
    async (app: AppRecord) => {
      const path =
        app.status === 'active'
          ? `/api/apps/${app.id}/isolate`
          : `/api/apps/${app.id}/reactivate`;
      const res = await apiFetch(getToken, path, {
        method: 'POST',
        body: JSON.stringify({ reason: 'operator-toggled-via-dashboard' }),
      });
      if (!res.ok) {
        setError(`POST ${path} → ${res.status}`);
        return;
      }
      // Re-fetch the list so the badge + manifest reflect the change.
      await fetchApps();
    },
    [getToken, fetchApps],
  );

  const selectedApp = useMemo(
    () => apps.find((a) => a.id === selectedId) ?? null,
    [apps, selectedId],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-8 py-5">
        <h1 className="text-2xl font-semibold">Sovereign App Dashboard</h1>
        <p className="mt-1 text-sm text-slate-400">
          Manage third-party apps installed on this device. Toggle
          permission scopes live — changes take effect on the next
          gate check, no restart required.
        </p>
      </header>

      {error ? (
        <div className="mx-8 mt-6 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      ) : null}

      <main className="grid grid-cols-12 gap-6 p-8">
        {/* Left rail — list of apps */}
        <section className="col-span-4 rounded-lg border border-slate-800 bg-slate-900/40">
          <div className="border-b border-slate-800 px-4 py-3 text-sm font-medium text-slate-300">
            Installed apps {loading ? '…' : `(${apps.length})`}
          </div>
          <ul>
            {apps.map((app) => (
              <li key={app.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(app.id)}
                  className={`flex w-full items-center justify-between border-b border-slate-800/60 px-4 py-3 text-left transition hover:bg-slate-800/60 ${
                    selectedId === app.id ? 'bg-slate-800/80' : ''
                  }`}
                >
                  <div>
                    <div className="text-sm font-medium text-slate-100">
                      {app.name}
                    </div>
                    <div className="text-xs text-slate-500">v{app.version}</div>
                  </div>
                  <StatusBadge status={app.status} />
                </button>
              </li>
            ))}
            {!loading && apps.length === 0 ? (
              <li className="px-4 py-6 text-center text-sm text-slate-500">
                No apps installed yet.
              </li>
            ) : null}
          </ul>
        </section>

        {/* Right pane — detail + audit trail */}
        <section className="col-span-8 space-y-6">
          {selectedApp ? (
            <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <div className="text-lg font-semibold text-slate-100">
                    {selectedApp.name}
                  </div>
                  <div className="text-xs text-slate-500">
                    v{selectedApp.version} · id {selectedApp.id}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={selectedApp.status} />
                  <button
                    type="button"
                    onClick={() => toggleIsolation(selectedApp)}
                    className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:border-slate-600 hover:bg-slate-700"
                  >
                    {selectedApp.status === 'active'
                      ? 'Isolate app'
                      : 'Reactivate app'}
                  </button>
                </div>
              </div>
              {selectedApp.status === 'isolated' ? (
                // P5.4 — Resource Guard banner. Shown whenever an
                // app sits in the 'isolated' state. The directive's
                // wording assumes the common cause (CPU/memory
                // overage); when the cache reflects a manual
                // isolation, the operator can still click
                // Re-Activate so the same affordance covers both.
                <div className="mb-4 rounded-md border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold">
                        ⚠️ Security Shield Active
                      </div>
                      <div className="mt-1 text-amber-200/80">
                        This app has been automatically isolated due to
                        excessive CPU or Memory usage. All API and
                        process calls are denied until you reactivate it.
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => toggleIsolation(selectedApp)}
                      className="shrink-0 rounded-md border border-amber-400/60 bg-amber-500/20 px-3 py-1.5 text-xs font-medium text-amber-50 hover:bg-amber-500/30"
                    >
                      Re-Activate & Reset Quota
                    </button>
                  </div>
                </div>
              ) : null}
              <AppPermissionControls
                app={selectedApp}
                onUpdated={fetchApps}
              />

              {/* W7.2 — Sovereign App Runtime: per-app sandboxed
                  storage tree. The upload zone stages files into
                  apps/<id>/storage/ via POST /api/apps/{id}/fs/write;
                  the explorer renders the listing from GET
                  /api/apps/{id}/fs/list. Both routes are
                  PermissionGate-checked + path-confined under
                  _resolve_inside_sandbox.

                  Note: secret is the X-App-Secret returned ONCE at
                  install. The dashboard does not cache it (operator-
                  recoverable only via re-install with secret rotation).
                  Components render a stub when secret is null. */}
              <div className="mt-6 space-y-4">
                <div className="text-xs uppercase tracking-wider text-slate-500">
                  Sandboxed storage
                </div>
                <AppFileUploadZone
                  appId={selectedApp.id}
                  secret={null}
                  onUploaded={fetchApps}
                />
                <AppFileExplorer
                  appId={selectedApp.id}
                  secret={null}
                />
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-5 py-10 text-center text-sm text-slate-500">
              Select an app on the left to manage its permissions.
            </div>
          )}

          <SovereignAuditTrail appId={selectedApp?.id ?? null} />
        </section>
      </main>
    </div>
  );
}
