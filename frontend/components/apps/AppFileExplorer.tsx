// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P5.5 — Sandboxed App File Explorer.
//
// Renders the contents of an app's isolated storage directory
// (`{app_data_dir}/apps/{app_id}/storage/`) as a flat list with
// folder/file icons + size + last-modified columns.
//
// Authentication: the dashboard's parent operator session is the
// outer gate; this component additionally sends the app's
// X-App-Id + X-App-Secret headers because /api/apps/{id}/fs/list
// runs under `get_app_context` (P4.2). The component takes these
// in via props rather than fetching the secret itself — secrets
// are write-once and live only in the install response.
//
// Behavior:
//   * Loads on mount + refreshes when `appId` / `secret` change.
//   * Click a folder row to descend; click ".." to ascend.
//   * 403 / 404 / network errors render an inline banner without
//     blowing away the previous listing.

'use client';

import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';

interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size_bytes: number | null;
  modified_at_ms: number | null;
}

interface FsListResponse {
  app_id: string;
  path: string;
  recursive: boolean;
  count: number;
  entries: FsEntry[];
}

interface AppFileExplorerProps {
  appId: string;
  /** Plain-text app secret (from install response). When null/empty
   *  the component renders a stub — the secret is the runtime
   *  authentication for the /fs/list endpoint. */
  secret: string | null;
  /** Optional backend origin. Defaults to relative paths. */
  apiOrigin?: string;
  /** Triggered whenever a file write/upload changes the listing. */
  refreshToken?: number;
}

function formatSize(n: number | null): string {
  if (n === null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatMtime(ms: number | null): string {
  if (!ms) return '—';
  try {
    return new Date(ms).toLocaleString();
  } catch {
    return '—';
  }
}

function FileIcon({ entry }: { entry: FsEntry }): React.ReactElement {
  // Folder → blue chevron-ish square. File → ext-keyed glyph.
  // Avoid any dependency on a heavy icon set — these are inline SVG.
  if (entry.is_dir) {
    return (
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-sm border border-indigo-500/40 bg-indigo-500/20 text-[10px] font-semibold text-indigo-200"
      >
        DIR
      </span>
    );
  }
  const ext = entry.name.split('.').pop()?.toLowerCase() ?? '';
  const palette: Record<string, string> = {
    txt: 'border-slate-500/40 bg-slate-500/15 text-slate-200',
    md: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200',
    json: 'border-amber-500/40 bg-amber-500/15 text-amber-200',
    yml: 'border-amber-500/40 bg-amber-500/15 text-amber-200',
    yaml: 'border-amber-500/40 bg-amber-500/15 text-amber-200',
    py: 'border-sky-500/40 bg-sky-500/15 text-sky-200',
    js: 'border-yellow-500/40 bg-yellow-500/15 text-yellow-200',
    ts: 'border-blue-500/40 bg-blue-500/15 text-blue-200',
    log: 'border-rose-500/40 bg-rose-500/15 text-rose-200',
  };
  const cls = palette[ext] ?? 'border-slate-600 bg-slate-700/30 text-slate-300';
  return (
    <span
      aria-hidden
      className={`inline-flex h-5 w-9 items-center justify-center rounded-sm border text-[10px] font-mono uppercase ${cls}`}
    >
      {ext || 'file'}
    </span>
  );
}

export function AppFileExplorer({
  appId,
  secret,
  apiOrigin = '',
  refreshToken = 0,
}: AppFileExplorerProps): React.ReactElement {
  const [cwd, setCwd] = useState('');                  // relative to sandbox
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const headers = useMemo(
    () => ({
      'X-App-Id': appId,
      'X-App-Secret': secret ?? '',
    }),
    [appId, secret],
  );

  const fetchListing = useCallback(async () => {
    if (!secret) {
      setError('App secret missing — cannot authenticate to /fs/list');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const url = `${apiOrigin}/api/apps/${appId}/fs/list?path=${encodeURIComponent(cwd)}`;
      const res = await fetch(url, { headers });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const code = body?.detail?.error ?? res.status;
        throw new Error(`list failed: ${code}`);
      }
      const body = (await res.json()) as FsListResponse;
      setEntries(body.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'list failed');
    } finally {
      setLoading(false);
    }
  }, [apiOrigin, appId, cwd, headers, secret]);

  useEffect(() => {
    fetchListing();
  }, [fetchListing, refreshToken]);

  const onFolderEnter = useCallback((entry: FsEntry) => {
    setCwd(entry.path);
  }, []);

  const onAscend = useCallback(() => {
    if (!cwd) return;
    const parts = cwd.split('/');
    parts.pop();
    setCwd(parts.join('/'));
  }, [cwd]);

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/30">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2 text-xs text-slate-300">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-200">Storage</span>
          <span className="font-mono text-slate-500">
            /{cwd || ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {cwd ? (
            <button
              type="button"
              onClick={onAscend}
              className="rounded border border-slate-700 px-2 py-0.5 hover:bg-slate-800"
            >
              ↑ up
            </button>
          ) : null}
          <button
            type="button"
            onClick={fetchListing}
            className="rounded border border-slate-700 px-2 py-0.5 hover:bg-slate-800"
          >
            Refresh
          </button>
        </div>
      </div>

      {error ? (
        <div className="border-b border-slate-800 bg-red-500/10 px-4 py-2 text-xs text-red-200">
          {error}
        </div>
      ) : null}

      <ul className="max-h-72 overflow-y-auto text-sm">
        {loading && entries.length === 0 ? (
          <li className="px-4 py-3 text-xs text-slate-500">loading…</li>
        ) : null}
        {entries.length === 0 && !loading ? (
          <li className="px-4 py-6 text-center text-xs text-slate-500">
            empty
          </li>
        ) : null}
        {entries.map((entry) => (
          <li
            key={entry.path}
            className="grid grid-cols-[28px_1fr_80px_140px] items-center gap-3 border-b border-slate-800/50 px-4 py-2 last:border-b-0 hover:bg-slate-800/40"
          >
            <FileIcon entry={entry} />
            {entry.is_dir ? (
              <button
                type="button"
                onClick={() => onFolderEnter(entry)}
                className="truncate text-left text-slate-100 hover:text-indigo-200"
              >
                {entry.name}/
              </button>
            ) : (
              <span className="truncate text-slate-200">{entry.name}</span>
            )}
            <span className="text-right font-mono text-xs text-slate-500">
              {formatSize(entry.size_bytes)}
            </span>
            <span className="text-right text-xs text-slate-500">
              {formatMtime(entry.modified_at_ms)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
