// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// P5.5 — Sandboxed app file uploader.
//
// Drag-and-drop area that uploads the dropped file's contents
// through POST /api/apps/{id}/fs/write. Authenticates with the
// app's X-App-Id + X-App-Secret headers (same pattern as the
// explorer).
//
// Behavior:
//   * Accepts a single file at a time. Multi-file uploads queue
//     sequentially so a failure on file N doesn't poison file N+1.
//   * Reads as UTF-8 text (sufficient for the directive's POST
//     /fs/write contract, which is JSON-encoded). Binary support
//     lands in a future P5.x via the /state/file endpoint shape
//     once that endpoint accepts base64.
//   * Real-time feedback per upload:
//       green check on success
//       amber warning when the route returns 403 / scope_violation
//       red error on network / 500 failures
//   * Calls `onUploaded()` after each successful write so the
//     explorer can refresh.

'use client';

import type React from 'react';
import {
  type DragEvent,
  useCallback,
  useMemo,
  useRef,
  useState,
} from 'react';

interface UploadEvent {
  id: string;
  filename: string;
  size_bytes: number;
  status: 'pending' | 'success' | 'denied' | 'error';
  message?: string;
}

interface AppFileUploadZoneProps {
  appId: string;
  secret: string | null;
  apiOrigin?: string;
  /** Fires after every successful write — the explorer should
   *  re-list. */
  onUploaded?: () => void;
}

export function AppFileUploadZone({
  appId,
  secret,
  apiOrigin = '',
  onUploaded,
}: AppFileUploadZoneProps): React.ReactElement {
  const [events, setEvents] = useState<UploadEvent[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const headers = useMemo(
    () => ({
      'X-App-Id': appId,
      'X-App-Secret': secret ?? '',
      'Content-Type': 'application/json',
    }),
    [appId, secret],
  );

  const uploadOne = useCallback(
    async (file: File) => {
      const eventId = `${file.name}-${Date.now()}-${Math.random()}`;
      const seed: UploadEvent = {
        id: eventId,
        filename: file.name,
        size_bytes: file.size,
        status: 'pending',
      };
      setEvents((prev) => [seed, ...prev].slice(0, 8));
      try {
        const text = await file.text();
        const url = `${apiOrigin}/api/apps/${appId}/fs/write`;
        const res = await fetch(url, {
          method: 'POST',
          headers,
          body: JSON.stringify({ path: file.name, content: text }),
        });
        if (res.ok) {
          setEvents((prev) =>
            prev.map((e) =>
              e.id === eventId
                ? { ...e, status: 'success', message: 'written' }
                : e,
            ),
          );
          onUploaded?.();
          return;
        }
        const body = await res.json().catch(() => ({}));
        const detail = body?.detail ?? {};
        if (
          res.status === 403
          && (detail.scope === 'filesystem.write'
              || detail.error === 'scope_violation'
              || detail.error === 'path_traversal_attempt')
        ) {
          setEvents((prev) =>
            prev.map((e) =>
              e.id === eventId
                ? {
                    ...e,
                    status: 'denied',
                    message:
                      detail.error === 'path_traversal_attempt'
                        ? 'path traversal blocked — app auto-isolated'
                        : 'filesystem.write denied by manifest',
                  }
                : e,
            ),
          );
          return;
        }
        setEvents((prev) =>
          prev.map((e) =>
            e.id === eventId
              ? {
                  ...e,
                  status: 'error',
                  message: `HTTP ${res.status} ${detail.error ?? ''}`,
                }
              : e,
          ),
        );
      } catch (err) {
        setEvents((prev) =>
          prev.map((e) =>
            e.id === eventId
              ? {
                  ...e,
                  status: 'error',
                  message:
                    err instanceof Error ? err.message : 'upload failed',
                }
              : e,
          ),
        );
      }
    },
    [apiOrigin, appId, headers, onUploaded],
  );

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      // Sequential to keep per-file events ordered + bounded.
      for (let i = 0; i < files.length; i += 1) {
        // eslint-disable-next-line no-await-in-loop
        await uploadOne(files[i]);
      }
    },
    [uploadOne],
  );

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      void handleFiles(e.dataTransfer?.files ?? null);
    },
    [handleFiles],
  );

  const onDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  return (
    <div className="space-y-3">
      <div
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => inputRef.current?.click()}
        className={`flex cursor-pointer items-center justify-center rounded-lg border-2 border-dashed px-4 py-8 text-sm transition ${
          dragging
            ? 'border-indigo-400 bg-indigo-500/10 text-indigo-100'
            : 'border-slate-700 bg-slate-900/40 text-slate-400 hover:border-slate-500'
        }`}
        role="button"
        aria-label="Drag files here to upload"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            void handleFiles(e.target.files);
            // Reset so the same file can be re-uploaded.
            e.target.value = '';
          }}
        />
        {dragging ? 'Drop to upload' : 'Drag a file here, or click to select'}
      </div>

      {events.length > 0 ? (
        <ul className="space-y-1">
          {events.map((e) => {
            const palette: Record<UploadEvent['status'], string> = {
              pending: 'border-slate-700 bg-slate-800/50 text-slate-300',
              success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
              denied: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
              error: 'border-red-500/40 bg-red-500/10 text-red-200',
            };
            const icon: Record<UploadEvent['status'], string> = {
              pending: '⏳',
              success: '✓',
              denied: '⚠️',
              error: '✗',
            };
            return (
              <li
                key={e.id}
                className={`flex items-center justify-between rounded-md border px-3 py-1.5 text-xs ${palette[e.status]}`}
              >
                <div className="flex items-center gap-2">
                  <span>{icon[e.status]}</span>
                  <span className="truncate">{e.filename}</span>
                  <span className="text-slate-500">
                    ({e.size_bytes} B)
                  </span>
                </div>
                <span className="truncate text-right text-[11px] opacity-80">
                  {e.message ?? e.status}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
