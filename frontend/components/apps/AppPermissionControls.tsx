// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P5.1 — Per-app permission editor.
//
// Renders the app's current granted scopes (and explicit
// restrictions) as interactive badges. Each granted scope has a
// checkbox; toggling it calls
// POST /api/apps/{id}/scopes/toggle which:
//   1. Edits the manifest's scopes list in SQLite.
//   2. Evicts the gate cache so the change takes effect on the
//      next PermissionGate.check() — no server restart.
//
// New scopes can be granted via the inline "Add scope" form. The
// add-scope input accepts the same dot-namespaced grammar the
// backend uses (e.g. "llm.local", "events.subscribe:chat",
// "rpc.call:*").

'use client';

import type React from 'react';
import { useCallback, useState } from 'react';
import { useAuth } from '@clerk/nextjs';

import { apiFetch } from '@/lib/api-client';

interface AppRecord {
  id: string;
  manifest: {
    scopes: string[];
    restrictions: string[];
  };
  status: string;
}

interface AppPermissionControlsProps {
  app: AppRecord;
  onUpdated?: () => void;
}

const COMMON_SCOPES = [
  'filesystem.read',
  'filesystem.write',
  'llm.local',
  'llm.cloud',
  'network.outbound',
  'rag.read',
  'rag.write',
  'process.execute',
  'rpc.expose',
  'app.state.read',
  'app.state.write',
];

export function AppPermissionControls({
  app,
  onUpdated,
}: AppPermissionControlsProps): React.ReactElement {
  const { getToken } = useAuth();
  const [pendingScope, setPendingScope] = useState<string | null>(null);
  const [newScope, setNewScope] = useState('');
  const [error, setError] = useState<string | null>(null);

  const toggle = useCallback(
    async (scope: string, granted: boolean) => {
      setPendingScope(scope);
      setError(null);
      try {
        const res = await apiFetch(
          getToken,
          `/api/apps/${app.id}/scopes/toggle`,
          {
            method: 'POST',
            body: JSON.stringify({ scope, granted }),
          },
        );
        if (!res.ok) {
          const detail = await res.text();
          throw new Error(`toggle failed: ${res.status} ${detail}`);
        }
        onUpdated?.();
      } catch (err) {
        setError(err instanceof Error ? err.message : 'toggle failed');
      } finally {
        setPendingScope(null);
      }
    },
    [getToken, app.id, onUpdated],
  );

  const grantNew = useCallback(async () => {
    const scope = newScope.trim();
    if (!scope) return;
    await toggle(scope, true);
    setNewScope('');
  }, [newScope, toggle]);

  const grantedSet = new Set(app.manifest.scopes);
  const isolated = app.status !== 'active';

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
          Granted scopes
        </div>
        {app.manifest.scopes.length === 0 ? (
          <div className="text-sm text-slate-500">
            No scopes granted. The app cannot reach any vOS capability.
          </div>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {app.manifest.scopes.map((scope) => (
              <li key={scope}>
                <label
                  className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs ${
                    isolated
                      ? 'border-slate-800 bg-slate-900/60 text-slate-500'
                      : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                  } ${pendingScope === scope ? 'opacity-60' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked
                    disabled={isolated || pendingScope === scope}
                    onChange={() => toggle(scope, false)}
                    className="h-3.5 w-3.5"
                    aria-label={`Revoke ${scope}`}
                  />
                  <code className="font-mono">{scope}</code>
                </label>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
          Suggested common scopes
        </div>
        <ul className="flex flex-wrap gap-2">
          {COMMON_SCOPES.filter((s) => !grantedSet.has(s)).map((scope) => (
            <li key={scope}>
              <button
                type="button"
                disabled={isolated || pendingScope === scope}
                onClick={() => toggle(scope, true)}
                className="rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-500 hover:bg-slate-700/70 disabled:opacity-50"
              >
                <code className="font-mono">+ {scope}</code>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {app.manifest.restrictions.length > 0 ? (
        <div>
          <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            Restrictions (hard blocks)
          </div>
          <ul className="flex flex-wrap gap-2">
            {app.manifest.restrictions.map((r) => (
              <li
                key={r}
                className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs text-red-200"
              >
                <code className="font-mono">{r}</code>
              </li>
            ))}
          </ul>
          <div className="mt-1 text-xs text-slate-500">
            Restrictions beat any matching grant — they cannot be
            edited inline. Re-install the app to change them.
          </div>
        </div>
      ) : null}

      <div className="flex items-center gap-2 pt-2">
        <input
          type="text"
          value={newScope}
          placeholder="custom.scope.path"
          onChange={(e) => setNewScope(e.target.value)}
          disabled={isolated}
          className="flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-slate-500 focus:outline-none"
        />
        <button
          type="button"
          onClick={grantNew}
          disabled={isolated || !newScope.trim()}
          className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:border-slate-600 hover:bg-slate-700 disabled:opacity-50"
        >
          Grant scope
        </button>
      </div>

      {error ? (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">
          {error}
        </div>
      ) : null}
    </div>
  );
}
