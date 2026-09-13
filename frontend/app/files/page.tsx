'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { toast } from 'sonner';
import {
  ChevronRight,
  File as FileIcon,
  Folder,
  FolderOpen,
  Home,
  Plus,
  RefreshCw,
  PlugZap,
} from 'lucide-react';
import { FileContextMenu } from '@/components/files/FileContextMenu';
import { ConfirmDialog, PromptDialog } from '@/components/files/FileDialog';
import { SkeletonBlock, SkeletonRegion } from '@/components/shared/Skeleton';
import { useMediaQuery } from '@/hooks/useMediaQuery';

const API_BASE = '';
const ROOT_PATH = '/disk';

type EntryType = 'file' | 'dir' | 'unknown' | string;

interface FileEntry {
  name: string;
  size: number;
  type: EntryType;
  /**
   * Inode modification time as a uint64 emitted by the kernel LSM
   * command. Seconds since the Unix epoch on vos3fs, seconds since
   * uptime on RAMFS. Zero means "unknown" (older kernels or
   * filesystems that don't track it).
   */
  mtime: number;
}

interface ListResponse {
  path: string;
  entries: FileEntry[];
  count: number;
}

type ViewState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; entries: FileEntry[] }
  | { kind: 'kernelOffline'; message: string }
  | { kind: 'error'; status: number; message: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log10(bytes) / 3), units.length - 1);
  const v = bytes / Math.pow(1000, i);
  return `${v >= 10 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

/**
 * Format a kernel mtime (seconds since epoch) for display.
 *
 * Heuristics:
 *  - 0 / non-finite → em-dash (filesystem doesn't track or older
 *    kernel didn't emit it).
 *  - Implausibly small values (< 1e9 seconds, ~Sept 2001) are likely
 *    RAMFS uptime-relative timestamps, not Unix epoch — render as
 *    "uptime+Ns" so the user knows it's not a real wall-clock.
 *  - Within the last 7 days → relative ("just now", "5m ago", "3h
 *    ago", "2d ago").
 *  - Older → absolute short date ("May 7", "Jan 22, 2025").
 */
function formatMtime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  if (seconds < 1_000_000_000) return `uptime+${Math.floor(seconds)}s`;
  const ms = seconds * 1000;
  const now = Date.now();
  const diff = now - ms;
  if (diff < 0) return new Date(ms).toLocaleDateString();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  const d = new Date(ms);
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  return sameYear
    ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function joinPath(base: string, name: string): string {
  const sep = base.endsWith('/') ? '' : '/';
  return `${base}${sep}${name}`;
}

function parentPath(path: string): string | null {
  if (path === '/' || path === ROOT_PATH) return null;
  const idx = path.lastIndexOf('/');
  if (idx <= 0) return ROOT_PATH;
  return path.slice(0, idx) || ROOT_PATH;
}

function pathSegments(path: string): { label: string; href: string }[] {
  const parts = path.split('/').filter(Boolean);
  const acc: { label: string; href: string }[] = [];
  let cur = '';
  for (const p of parts) {
    cur += '/' + p;
    acc.push({ label: p, href: cur });
  }
  return acc;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type DialogState =
  | { kind: 'closed' }
  | { kind: 'create' }
  | { kind: 'rename'; entry: FileEntry }
  | { kind: 'delete'; entry: FileEntry };

export default function FilesPage() {
  const { getToken } = useAuth();
  const [path, setPath] = useState<string>(ROOT_PATH);
  const [view, setView] = useState<ViewState>({ kind: 'idle' });
  const [dialog, setDialog] = useState<DialogState>({ kind: 'closed' });
  const closeDialog = useCallback(() => setDialog({ kind: 'closed' }), []);
  const isMobile = useMediaQuery('(max-width: 767px)');

  const authHeaders = useCallback(async (extra?: Record<string, string>) => {
    const token = await getToken();
    return {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extra,
    };
  }, [getToken]);

  const fetchList = useCallback(
    async (target: string) => {
      setView({ kind: 'loading' });
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/files/?path=${encodeURIComponent(target)}`,
          { headers: await authHeaders() },
        );
        if (!res.ok) {
          let detail = res.statusText;
          try {
            const body = await res.json();
            if (body?.detail) detail = String(body.detail);
          } catch {
            /* ignore — non-JSON body */
          }
          if (res.status === 503) {
            setView({ kind: 'kernelOffline', message: detail });
            toast.error('Kernel disconnected', { description: detail });
            return;
          }
          setView({ kind: 'error', status: res.status, message: detail });
          toast.error(`Failed to load ${target}`, { description: `${res.status}: ${detail}` });
          return;
        }
        const data: ListResponse = await res.json();
        setView({ kind: 'ready', entries: data.entries ?? [] });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setView({ kind: 'error', status: 0, message: msg });
        toast.error('Network error', { description: msg });
      }
    },
    [authHeaders],
  );

  useEffect(() => {
    fetchList(path);
  }, [path, fetchList]);

  // -------------------------------------------------------------------------
  // Mutations: rename + delete
  // -------------------------------------------------------------------------

  const performRename = useCallback(
    async (entry: FileEntry, nextName: string) => {
      const oldPath = joinPath(path, entry.name);
      const newPath = joinPath(path, nextName);
      try {
        const res = await fetch(`${API_BASE}/api/v1/files/`, {
          method: 'PATCH',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
        });
        if (!res.ok) {
          const detail = await res
            .json()
            .then((b) => b?.detail)
            .catch(() => res.statusText);
          throw new Error(String(detail));
        }
        toast.success(`Renamed to ${nextName}`);
        fetchList(path);
      } catch (err) {
        toast.error('Rename failed', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [authHeaders, fetchList, path],
  );

  const performDelete = useCallback(
    async (entry: FileEntry) => {
      const target = joinPath(path, entry.name);
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/files/?path=${encodeURIComponent(target)}`,
          { method: 'DELETE', headers: await authHeaders() },
        );
        if (!res.ok) {
          const detail = await res
            .json()
            .then((b) => b?.detail)
            .catch(() => res.statusText);
          throw new Error(String(detail));
        }
        toast.success(`Deleted ${entry.name}`);
        fetchList(path);
      } catch (err) {
        toast.error('Delete failed', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [authHeaders, fetchList, path],
  );

  const performCreate = useCallback(
    async (name: string) => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/files/`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ path: joinPath(path, name), content: '' }),
        });
        if (!res.ok) {
          const detail = await res
            .json()
            .then((b) => b?.detail)
            .catch(() => res.statusText);
          throw new Error(String(detail));
        }
        toast.success(`Created ${name}`);
        fetchList(path);
      } catch (err) {
        toast.error('Create failed', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [authHeaders, fetchList, path],
  );

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const segments = useMemo(() => pathSegments(path), [path]);
  const parent = useMemo(() => parentPath(path), [path]);

  return (
    <div
      style={{
        padding: 32,
        paddingTop: isMobile ? 76 : 32,
        maxWidth: 1100,
        margin: '0 auto',
      }}
    >
      <Header
        path={path}
        onRefresh={() => fetchList(path)}
        onCreate={() => setDialog({ kind: 'create' })}
      />

      <Breadcrumbs
        segments={segments}
        onNavigate={setPath}
        canGoUp={parent !== null}
        onGoUp={() => parent && setPath(parent)}
      />

      <div
        style={{
          marginTop: 16,
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-lg)',
          overflow: 'hidden',
        }}
      >
        {view.kind === 'loading' && <FileSkeleton rows={5} />}

        {view.kind === 'kernelOffline' && <KernelDisconnected message={view.message} onRetry={() => fetchList(path)} />}

        {view.kind === 'error' && (
          <ErrorBlock
            status={view.status}
            message={view.message}
            onRetry={() => fetchList(path)}
          />
        )}

        {view.kind === 'ready' && view.entries.length === 0 && (
          <EmptyState onCreate={() => setDialog({ kind: 'create' })} />
        )}

        {view.kind === 'ready' && view.entries.length > 0 && (
          <FileTable
            entries={view.entries}
            isMobile={isMobile}
            onOpen={(entry) => {
              if (entry.type === 'dir') setPath(joinPath(path, entry.name));
            }}
            onRename={(entry) => setDialog({ kind: 'rename', entry })}
            onDelete={(entry) => setDialog({ kind: 'delete', entry })}
          />
        )}
      </div>

      <PromptDialog
        open={dialog.kind === 'create'}
        title="Create new file"
        description={`A new empty file will be created in ${path}.`}
        placeholder="example.txt"
        confirmLabel="Create"
        onConfirm={(name) => performCreate(name)}
        onClose={closeDialog}
      />

      <PromptDialog
        open={dialog.kind === 'rename'}
        title="Rename file"
        description={dialog.kind === 'rename' ? `Enter a new name for "${dialog.entry.name}".` : undefined}
        initialValue={dialog.kind === 'rename' ? dialog.entry.name : ''}
        confirmLabel="Rename"
        onConfirm={(name) => {
          if (dialog.kind !== 'rename') return;
          return performRename(dialog.entry, name);
        }}
        onClose={closeDialog}
      />

      <ConfirmDialog
        open={dialog.kind === 'delete'}
        title="Delete file"
        description={
          dialog.kind === 'delete'
            ? `"${dialog.entry.name}" will be permanently deleted from the kernel VFS. This cannot be undone.`
            : undefined
        }
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          if (dialog.kind !== 'delete') return;
          return performDelete(dialog.entry);
        }}
        onClose={closeDialog}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Header({
  path,
  onRefresh,
  onCreate,
}: {
  path: string;
  onRefresh: () => void;
  onCreate: () => void;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
      <div>
        <h1
          style={{
            margin: 0,
            fontSize: 26,
            fontWeight: 700,
            letterSpacing: '-0.02em',
            color: 'var(--text-primary)',
          }}
        >
          Files
        </h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-tertiary)' }}>
          Browse the kernel VFS at <code style={{ fontFamily: 'monospace' }}>{path}</code>
        </p>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <IconButton onClick={onRefresh} label="Refresh">
          <RefreshCw size={16} />
        </IconButton>
        <PrimaryButton onClick={onCreate}>
          <Plus size={14} />
          New file
        </PrimaryButton>
      </div>
    </div>
  );
}

function Breadcrumbs({
  segments,
  onNavigate,
  canGoUp,
  onGoUp,
}: {
  segments: { label: string; href: string }[];
  onNavigate: (path: string) => void;
  canGoUp: boolean;
  onGoUp: () => void;
}) {
  return (
    <nav
      aria-label="Breadcrumb"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        padding: '8px 12px',
        backgroundColor: 'var(--bg-secondary)',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-md)',
        fontSize: 13,
        flexWrap: 'wrap',
      }}
    >
      <BreadcrumbButton onClick={() => onNavigate('/')} active={segments.length === 0}>
        <Home size={14} />
        Root
      </BreadcrumbButton>
      {segments.map((s, idx) => {
        const isLast = idx === segments.length - 1;
        return (
          <span key={s.href} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <ChevronRight size={12} style={{ color: 'var(--text-tertiary)' }} aria-hidden="true" />
            <BreadcrumbButton onClick={() => onNavigate(s.href)} active={isLast}>
              {s.label}
            </BreadcrumbButton>
          </span>
        );
      })}
      {canGoUp && (
        <button
          type="button"
          onClick={onGoUp}
          style={{
            marginLeft: 'auto',
            padding: '4px 10px',
            fontSize: 12,
            color: 'var(--text-secondary)',
            backgroundColor: 'transparent',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
          }}
        >
          ↑ Up
        </button>
      )}
    </nav>
  );
}

function BreadcrumbButton({
  onClick,
  active,
  children,
}: {
  onClick: () => void;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 8px',
        borderRadius: 'var(--radius-sm)',
        fontSize: 13,
        fontWeight: active ? 600 : 400,
        color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        backgroundColor: 'transparent',
        cursor: active ? 'default' : 'pointer',
      }}
      onMouseOver={(e) => {
        if (!active) e.currentTarget.style.backgroundColor = 'var(--bg-hover)';
      }}
      onMouseOut={(e) => {
        if (!active) e.currentTarget.style.backgroundColor = 'transparent';
      }}
      aria-current={active ? 'page' : undefined}
    >
      {children}
    </button>
  );
}

function FileTable({
  entries,
  isMobile,
  onOpen,
  onRename,
  onDelete,
}: {
  entries: FileEntry[];
  isMobile: boolean;
  onOpen: (entry: FileEntry) => void;
  onRename: (entry: FileEntry) => void;
  onDelete: (entry: FileEntry) => void;
}) {
  const sorted = useMemo(
    () =>
      [...entries].sort((a, b) => {
        if ((a.type === 'dir') !== (b.type === 'dir')) return a.type === 'dir' ? -1 : 1;
        return a.name.localeCompare(b.name);
      }),
    [entries],
  );

  if (isMobile) {
    return (
      <div role="list" aria-label="Files" style={{ padding: 12, display: 'grid', gap: 8 }}>
        {sorted.map((entry) => {
          const isDir = entry.type === 'dir';
          return (
            <FileContextMenu
              key={entry.name}
              onRename={() => onRename(entry)}
              onDelete={() => onDelete(entry)}
            >
              <button
                type="button"
                role="listitem"
                onClick={() => onOpen(entry)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  width: '100%',
                  padding: 14,
                  textAlign: 'left',
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-md)',
                  fontSize: 14,
                  color: 'var(--text-primary)',
                  cursor: isDir ? 'pointer' : 'default',
                }}
              >
                <div
                  style={{
                    width: 36,
                    height: 36,
                    flexShrink: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--bg-hover)',
                    color: isDir ? 'var(--accent)' : 'var(--text-secondary)',
                  }}
                >
                  {isDir ? <Folder size={18} aria-hidden="true" /> : <FileIcon size={18} aria-hidden="true" />}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontWeight: 500,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {entry.name}
                  </div>
                  <div style={{ marginTop: 2, fontSize: 12, color: 'var(--text-tertiary)' }}>
                    {entry.type} · {isDir ? '—' : formatSize(entry.size)}
                    {entry.mtime > 0 && ` · ${formatMtime(entry.mtime)}`}
                  </div>
                </div>
              </button>
            </FileContextMenu>
          );
        })}
      </div>
    );
  }

  return (
    <div role="table" aria-label="Files">
      <div
        role="row"
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 100px 110px 80px',
          gap: 16,
          padding: '10px 16px',
          fontSize: 11,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: 'var(--text-tertiary)',
          borderBottom: '1px solid var(--border-light)',
        }}
      >
        <span>Name</span>
        <span style={{ textAlign: 'right' }}>Size</span>
        <span>Modified</span>
        <span>Type</span>
      </div>
      {sorted.map((entry) => {
        const isDir = entry.type === 'dir';
        return (
          <FileContextMenu
            key={entry.name}
            onRename={() => onRename(entry)}
            onDelete={() => onDelete(entry)}
          >
            <button
              type="button"
              role="row"
              onClick={() => onOpen(entry)}
              onDoubleClick={() => onOpen(entry)}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 100px 110px 80px',
                gap: 16,
                width: '100%',
                padding: '10px 16px',
                textAlign: 'left',
                fontSize: 13,
                color: 'var(--text-primary)',
                backgroundColor: 'transparent',
                border: 'none',
                borderBottom: '1px solid var(--border-light)',
                cursor: isDir ? 'pointer' : 'default',
              }}
              onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
              onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                {isDir ? (
                  <Folder size={16} style={{ color: 'var(--accent)' }} aria-hidden="true" />
                ) : (
                  <FileIcon size={16} style={{ color: 'var(--text-tertiary)' }} aria-hidden="true" />
                )}
                {entry.name}
              </span>
              <span
                style={{
                  textAlign: 'right',
                  fontVariantNumeric: 'tabular-nums',
                  color: 'var(--text-secondary)',
                }}
              >
                {isDir ? '—' : formatSize(entry.size)}
              </span>
              <span
                style={{
                  color: 'var(--text-secondary)',
                  fontVariantNumeric: 'tabular-nums',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
                title={
                  entry.mtime > 0
                    ? new Date(entry.mtime * 1000).toISOString()
                    : 'Modification time not tracked'
                }
              >
                {formatMtime(entry.mtime)}
              </span>
              <span style={{ color: 'var(--text-tertiary)', textTransform: 'capitalize' }}>
                {entry.type}
              </span>
            </button>
          </FileContextMenu>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// State views
// ---------------------------------------------------------------------------

function FileSkeleton({ rows }: { rows: number }) {
  return (
    <SkeletonRegion label="Loading files…">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 100px 80px',
            gap: 16,
            padding: '12px 16px',
            borderBottom: '1px solid var(--border-light)',
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <SkeletonBlock width={16} height={16} delayMs={i * 100} />
            <SkeletonBlock width={`${50 + ((i * 13) % 30)}%`} height={12} delayMs={i * 100} />
          </span>
          <SkeletonBlock width={60} height={12} delayMs={i * 100} style={{ justifySelf: 'end' }} />
          <SkeletonBlock width={48} height={12} delayMs={i * 100} />
        </div>
      ))}
    </SkeletonRegion>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div
      style={{
        padding: '64px 24px',
        textAlign: 'center',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 16,
      }}
    >
      <div
        style={{
          width: 72,
          height: 72,
          borderRadius: 'var(--radius-full)',
          backgroundColor: 'var(--bg-hover)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary)',
        }}
      >
        <FolderOpen size={36} aria-hidden="true" />
      </div>
      <div>
        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
          This folder is empty
        </div>
        <div style={{ marginTop: 4, fontSize: 13, color: 'var(--text-tertiary)' }}>
          Create your first file to get started.
        </div>
      </div>
      <PrimaryButton onClick={onCreate}>
        <Plus size={14} />
        Create file
      </PrimaryButton>
    </div>
  );
}

function KernelDisconnected({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      style={{
        padding: '64px 24px',
        textAlign: 'center',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 16,
      }}
    >
      <div
        style={{
          width: 72,
          height: 72,
          borderRadius: 'var(--radius-full)',
          backgroundColor: 'rgba(220, 38, 38, 0.1)',
          color: 'var(--error)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <PlugZap size={36} aria-hidden="true" />
      </div>
      <div>
        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
          Kernel Disconnected
        </div>
        <div style={{ marginTop: 4, fontSize: 13, color: 'var(--text-tertiary)', maxWidth: 420 }}>
          {message || 'The VOS3 kernel bridge is not currently reachable. Start the kernel and try again.'}
        </div>
      </div>
      <PrimaryButton onClick={onRetry}>
        <RefreshCw size={14} />
        Retry
      </PrimaryButton>
    </div>
  );
}

function ErrorBlock({
  status,
  message,
  onRetry,
}: {
  status: number;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div style={{ padding: 32, textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--error)' }}>
        {status ? `${status} —` : 'Error —'} {message || 'Unknown error'}
      </div>
      <button
        type="button"
        onClick={onRetry}
        style={{
          marginTop: 12,
          padding: '6px 12px',
          fontSize: 13,
          backgroundColor: 'var(--bg-hover)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--text-primary)',
          cursor: 'pointer',
        }}
      >
        Retry
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

function IconButton({
  onClick,
  label,
  children,
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      style={{
        width: 36,
        height: 36,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 'var(--radius-sm)',
        backgroundColor: 'transparent',
        border: '1px solid var(--border-light)',
        color: 'var(--text-secondary)',
        cursor: 'pointer',
      }}
      onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
      onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
    >
      {children}
    </button>
  );
}

function PrimaryButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '8px 14px',
        fontSize: 13,
        fontWeight: 500,
        color: 'var(--text-inverse)',
        backgroundColor: 'var(--accent)',
        border: 'none',
        borderRadius: 'var(--radius-sm)',
        cursor: 'pointer',
      }}
      onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--accent-hover)')}
      onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'var(--accent)')}
    >
      {children}
    </button>
  );
}
