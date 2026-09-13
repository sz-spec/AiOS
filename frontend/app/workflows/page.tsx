'use client';

import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  type OnConnect,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  reconnectEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTheme } from 'next-themes';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { toast } from 'sonner';
import {
  FolderOpen,
  LayoutGrid,
  Plus,
  Save,
  Trash2,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { ConfirmDialog, PromptDialog } from '@/components/files/FileDialog';
import { NodePalette, PALETTE_DATA_TYPE } from '@/components/workflows/NodePalette';
import { WorkflowNode } from '@/components/workflows/WorkflowNode';
import { layoutWithDagre } from '@/components/workflows/dagreLayout';
import { NODE_KINDS, type WorkflowNodeData, type WorkflowNodeKind } from '@/components/workflows/types';
import { SkeletonBlock, SkeletonRegion } from '@/components/shared/Skeleton';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { usePlatform } from '@/lib/hooks/usePlatform';
import { workflowsDirFor, WORKFLOWS_BASE_DIR } from '@/lib/marketplace/paths';
import type { VerticalId } from '@/lib/marketplace/verticals';

const API_BASE = '';
const SAVE_VERSION = 1;
const MOBILE_BREAKPOINT = 768;

interface OpenEntry {
  name: string;
  /** Absolute kernel-VFS path to the source file. */
  sourcePath: string;
  /** True when the file lives in the unscoped legacy root and the
   *  current platform is not 'all'. Triggers the Legacy badge in the
   *  open dialog and the auto-migrate-on-save behavior. */
  isLegacy: boolean;
}

type SerializedWorkflow = {
  version: number;
  name?: string;
  nodes: Node[];
  edges: Edge[];
  exported_at: string;
  platform_tag?: VerticalId;
};

type DialogState =
  | { kind: 'closed' }
  | { kind: 'save' }
  | { kind: 'open'; entries: OpenEntry[] }
  | { kind: 'clearConfirm' };

const NODE_TYPES = { workflow: WorkflowNode } as const;

const DEFAULT_EDGE_OPTIONS: Partial<Edge> = {
  type: 'smoothstep',
  animated: true,
  style: { strokeWidth: 1.6, stroke: 'var(--accent)' },
  markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--accent)' },
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkflowBuilder />
    </ReactFlowProvider>
  );
}

function WorkflowBuilder() {
  const { resolvedTheme } = useTheme();
  const { getToken } = useAuth();
  const { screenToFlowPosition, fitView } = useReactFlow();
  const isMobile = useMediaQuery(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
  const { platform } = usePlatform();
  const workflowsDir = workflowsDirFor(platform);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);
  const [dialog, setDialog] = useState<DialogState>({ kind: 'closed' });
  const [currentName, setCurrentName] = useState<string | null>(null);
  /** Absolute path the active document was last read from / written to.
   *  Used to detect "legacy" (unscoped) origins so a save under a
   *  specific vertical can re-route the file and delete the original. */
  const [currentSourcePath, setCurrentSourcePath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const closeDialog = useCallback(() => setDialog({ kind: 'closed' }), []);

  // Auto-collapse palette below the mobile breakpoint.
  useEffect(() => {
    setPaletteCollapsed(isMobile);
  }, [isMobile]);

  const authHeaders = useCallback(
    async (extra?: Record<string, string>) => {
      const token = await getToken();
      return {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...extra,
      };
    },
    [getToken],
  );

  // -------------------------------------------------------------------------
  // Drag-and-drop from palette
  // -------------------------------------------------------------------------

  const onDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const kind = e.dataTransfer.getData(PALETTE_DATA_TYPE) as WorkflowNodeKind;
      if (!kind || !NODE_KINDS[kind]) return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const meta = NODE_KINDS[kind];
      const newNode: Node = {
        id: `${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        type: 'workflow',
        position,
        data: { kind, label: meta.label } satisfies WorkflowNodeData,
      };
      setNodes((current) => current.concat(newNode));
    },
    [screenToFlowPosition, setNodes],
  );

  // -------------------------------------------------------------------------
  // Connect / reconnect
  // -------------------------------------------------------------------------

  const onConnect: OnConnect = useCallback(
    (params) => setEdges((current) => addEdge(params, current)),
    [setEdges],
  );

  const onReconnect = useCallback(
    (oldEdge: Edge, newConnection: Parameters<typeof reconnectEdge>[1]) => {
      setEdges((current) => reconnectEdge(oldEdge, newConnection, current));
    },
    [setEdges],
  );

  // -------------------------------------------------------------------------
  // Auto-layout
  // -------------------------------------------------------------------------

  const handleAutoLayout = useCallback(() => {
    setNodes((current) => layoutWithDagre(current, edges, 'LR'));
    // Fit after layout settles next frame.
    requestAnimationFrame(() => fitView({ padding: 0.2, duration: 200 }));
  }, [edges, fitView, setNodes]);

  // -------------------------------------------------------------------------
  // Persistence (/api/v1/files)
  // -------------------------------------------------------------------------

  const performSave = useCallback(
    async (name: string) => {
      const safeName = name.replace(/[^a-zA-Z0-9_-]/g, '_');
      const filename = safeName.endsWith('.json') ? safeName : `${safeName}.json`;
      const path = `${workflowsDir}/${filename}`;
      const payload: SerializedWorkflow = {
        version: SAVE_VERSION,
        name: safeName,
        nodes,
        edges,
        exported_at: new Date().toISOString(),
        platform_tag: platform,
      };
      try {
        // Ensure target dir exists (idempotent — 502 if already there is fine).
        await fetch(`${API_BASE}/api/v1/files/mkdir`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ path: workflowsDir }),
        }).catch(() => {});

        const res = await fetch(`${API_BASE}/api/v1/files/`, {
          method: 'POST',
          headers: await authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            path,
            content: JSON.stringify(payload, null, 2),
            overwrite: true,
          }),
        });
        if (!res.ok) {
          const detail = await res.json().then((b) => b?.detail).catch(() => res.statusText);
          throw new Error(String(detail));
        }

        // Auto-migration: if this document originated in a different
        // directory (i.e. the unscoped legacy root) and the user is now
        // saving under a vertical, delete the original after the write
        // succeeded so we don't leave a duplicate behind. Failure to
        // delete is non-fatal — the new copy is already persisted.
        const migratedFromLegacy =
          !!currentSourcePath &&
          currentSourcePath !== path &&
          platform !== 'all';
        if (migratedFromLegacy && currentSourcePath) {
          try {
            await fetch(
              `${API_BASE}/api/v1/files/?path=${encodeURIComponent(currentSourcePath)}`,
              { method: 'DELETE', headers: await authHeaders() },
            );
          } catch {
            /* swallow — the user has the new copy either way */
          }
        }

        setCurrentName(filename);
        setCurrentSourcePath(path);
        if (migratedFromLegacy) {
          toast.success(`Migrated ${filename} → ${platform}`, {
            description: `Moved from ${WORKFLOWS_BASE_DIR} to ${workflowsDir}.`,
          });
        } else {
          toast.success(`Saved ${filename}`);
        }
      } catch (err) {
        toast.error('Save failed', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [authHeaders, edges, nodes],
  );

  const openSaveDialog = useCallback(() => {
    setDialog({ kind: 'save' });
  }, []);

  const openLoadDialog = useCallback(async () => {
    setLoading(true);
    try {
      const headers = await authHeaders();

      // Always list the (vertically) scoped directory.
      const scopedReq = fetch(
        `${API_BASE}/api/v1/files/?path=${encodeURIComponent(workflowsDir)}`,
        { headers },
      );

      // When a specific vertical is active, also peek into the unscoped
      // root so workflows saved before vertical-scoping landed remain
      // discoverable. Skipped on 'all' since `workflowsDir` already IS
      // the root and we'd be listing the same path twice.
      const includeLegacy = platform !== 'all';
      const legacyReq = includeLegacy
        ? fetch(
            `${API_BASE}/api/v1/files/?path=${encodeURIComponent(WORKFLOWS_BASE_DIR)}`,
            { headers },
          )
        : Promise.resolve(null);

      const [scopedRes, legacyRes] = await Promise.all([scopedReq, legacyReq]);

      // 404 on the scoped dir is a fresh-vertical state; don't bail —
      // legacy entries (if any) might still surface.
      if (scopedRes.status !== 404 && !scopedRes.ok) {
        const detail = await scopedRes
          .json()
          .then((b) => b?.detail)
          .catch(() => scopedRes.statusText);
        throw new Error(String(detail));
      }

      const scopedData =
        scopedRes.ok ? await scopedRes.json() : { entries: [] };
      const scopedNames = new Set<string>();
      const out: OpenEntry[] = [];

      for (const e of (scopedData.entries ?? []) as { name: string; type: string }[]) {
        if (e.type === 'dir' || !e.name.endsWith('.json')) continue;
        scopedNames.add(e.name);
        out.push({
          name: e.name,
          sourcePath: `${workflowsDir}/${e.name}`,
          isLegacy: false,
        });
      }

      // Merge legacy entries — but skip any name that already exists in
      // the scoped dir (scoped wins; we don't surface duplicates).
      // Also skip a legacy entry whose path equals a scoped vertical
      // subdirectory (those are sibling verticals, not files).
      if (legacyRes && legacyRes.ok) {
        const legacyData = await legacyRes.json();
        for (const e of (legacyData.entries ?? []) as { name: string; type: string }[]) {
          if (e.type === 'dir' || !e.name.endsWith('.json')) continue;
          if (scopedNames.has(e.name)) continue;
          out.push({
            name: e.name,
            sourcePath: `${WORKFLOWS_BASE_DIR}/${e.name}`,
            isLegacy: true,
          });
        }
      }

      if (out.length === 0) {
        toast.info('No workflows saved yet', {
          description:
            platform === 'all'
              ? `Save your first workflow to populate ${WORKFLOWS_BASE_DIR}.`
              : `Save your first workflow to populate ${workflowsDir}.`,
        });
        return;
      }

      setDialog({ kind: 'open', entries: out });
    } catch (err) {
      toast.error('Could not list workflows', {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setLoading(false);
    }
  }, [authHeaders, platform, workflowsDir]);

  const performLoad = useCallback(
    async (entry: OpenEntry) => {
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/files/content?path=${encodeURIComponent(entry.sourcePath)}`,
          { headers: await authHeaders() },
        );
        if (!res.ok) {
          const detail = await res.json().then((b) => b?.detail).catch(() => res.statusText);
          throw new Error(String(detail));
        }
        const body = await res.json();
        const parsed: SerializedWorkflow = JSON.parse(body.content);
        if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
          throw new Error('Invalid workflow file: missing nodes/edges');
        }
        setNodes(parsed.nodes);
        setEdges(parsed.edges);
        setCurrentName(entry.name);
        setCurrentSourcePath(entry.sourcePath);
        toast.success(
          entry.isLegacy
            ? `Loaded legacy ${entry.name} — saving will migrate it to ${platform}`
            : `Loaded ${entry.name}`,
        );
        requestAnimationFrame(() => fitView({ padding: 0.2, duration: 200 }));
      } catch (err) {
        toast.error('Load failed', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [authHeaders, fitView, platform, setEdges, setNodes],
  );

  const handleClear = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setCurrentSourcePath(null);
    setCurrentName(null);
  }, [setEdges, setNodes]);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const reactFlowColorMode = useMemo<'light' | 'dark' | 'system'>(() => {
    if (resolvedTheme === 'dark') return 'dark';
    if (resolvedTheme === 'light') return 'light';
    return 'system';
  }, [resolvedTheme]);

  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <div
      ref={containerRef}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        paddingTop: isMobile ? 60 : 0,
        backgroundColor: 'var(--bg-primary)',
        color: 'var(--text-primary)',
      }}
    >
      <Toolbar
        currentName={currentName}
        nodeCount={nodes.length}
        onSave={openSaveDialog}
        onOpen={openLoadDialog}
        onAutoLayout={handleAutoLayout}
        onClear={() => setDialog({ kind: 'clearConfirm' })}
        canClear={nodes.length > 0 || edges.length > 0}
        loading={loading}
      />

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <NodePalette collapsed={paletteCollapsed} onCollapsedChange={setPaletteCollapsed} />

        <div
          onDragOver={onDragOver}
          onDrop={onDrop}
          style={{ flex: 1, position: 'relative', minWidth: 0 }}
        >
          {loading ? (
            <CanvasSkeleton />
          ) : nodes.length === 0 ? (
            <CanvasEmpty />
          ) : null}

          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onReconnect={onReconnect}
            nodeTypes={NODE_TYPES}
            defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
            colorMode={reactFlowColorMode}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
            <Controls position="bottom-left" />
            <MiniMap
              position="bottom-right"
              pannable
              zoomable
              nodeStrokeWidth={2}
              nodeColor={(n) => {
                const data = n.data as WorkflowNodeData | undefined;
                return data ? NODE_KINDS[data.kind]?.accent ?? 'var(--accent)' : 'var(--accent)';
              }}
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-sm)',
              }}
            />
            <Panel position="top-right">
              <CountsBadge nodes={nodes.length} edges={edges.length} />
            </Panel>
          </ReactFlow>
        </div>
      </div>

      {/* Dialogs */}
      <PromptDialog
        open={dialog.kind === 'save'}
        title="Save workflow"
        description={`Saved as JSON to ${workflowsDir}/<name>.json`}
        initialValue={currentName?.replace(/\.json$/, '') ?? ''}
        placeholder="my_workflow"
        confirmLabel="Save"
        onConfirm={(name) => performSave(name)}
        onClose={closeDialog}
      />

      <ConfirmDialog
        open={dialog.kind === 'clearConfirm'}
        title="Clear canvas"
        description="All nodes and edges will be removed from the canvas. Saved files are not affected."
        confirmLabel="Clear"
        destructive
        onConfirm={() => handleClear()}
        onClose={closeDialog}
      />

      {dialog.kind === 'open' && (
        <OpenDialog
          entries={dialog.entries}
          workflowsDir={workflowsDir}
          platform={platform}
          onSelect={(entry) => {
            closeDialog();
            performLoad(entry);
          }}
          onClose={closeDialog}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toolbar
// ---------------------------------------------------------------------------

function Toolbar({
  currentName,
  nodeCount,
  onSave,
  onOpen,
  onAutoLayout,
  onClear,
  canClear,
  loading,
}: {
  currentName: string | null;
  nodeCount: number;
  onSave: () => void;
  onOpen: () => void;
  onAutoLayout: () => void;
  onClear: () => void;
  canClear: boolean;
  loading: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: '10px 16px',
        borderBottom: '1px solid var(--border-light)',
        backgroundColor: 'var(--bg-secondary)',
        flexWrap: 'wrap',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <WorkflowIcon size={18} aria-hidden="true" style={{ color: 'var(--accent)', flexShrink: 0 }} />
        <h1 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Workflow Builder</h1>
        <span
          style={{
            fontSize: 12,
            color: 'var(--text-tertiary)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            minWidth: 0,
          }}
        >
          {currentName ? `editing ${currentName}` : 'untitled'} · {nodeCount} {nodeCount === 1 ? 'node' : 'nodes'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <ToolbarButton onClick={onOpen} disabled={loading}>
          <FolderOpen size={14} /> Open
        </ToolbarButton>
        <ToolbarButton onClick={onAutoLayout} disabled={nodeCount === 0}>
          <LayoutGrid size={14} /> Auto-layout
        </ToolbarButton>
        <ToolbarButton onClick={onClear} disabled={!canClear} destructive>
          <Trash2 size={14} /> Clear
        </ToolbarButton>
        <ToolbarButton onClick={onSave} primary disabled={nodeCount === 0}>
          <Save size={14} /> Save
        </ToolbarButton>
      </div>
    </div>
  );
}

function ToolbarButton({
  onClick,
  children,
  primary = false,
  destructive = false,
  disabled = false,
}: {
  onClick: () => void;
  children: React.ReactNode;
  primary?: boolean;
  destructive?: boolean;
  disabled?: boolean;
}) {
  const bg = primary
    ? 'var(--accent)'
    : destructive
      ? 'transparent'
      : 'var(--bg-primary)';
  const color = primary ? 'var(--text-inverse)' : destructive ? 'var(--error)' : 'var(--text-primary)';
  const border = primary ? 'none' : '1px solid var(--border-light)';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 12px',
        fontSize: 13,
        fontWeight: 500,
        color,
        backgroundColor: bg,
        border,
        borderRadius: 'var(--radius-sm)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Open dialog (custom — file picker)
// ---------------------------------------------------------------------------

function OpenDialog({
  entries,
  workflowsDir,
  platform,
  onSelect,
  onClose,
}: {
  entries: OpenEntry[];
  workflowsDir: string;
  platform: VerticalId;
  onSelect: (entry: OpenEntry) => void;
  onClose: () => void;
}) {
  const hasLegacy = entries.some((e) => e.isLegacy);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Open workflow"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.45)',
        backdropFilter: 'blur(2px)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '90vw',
          maxWidth: 460,
          backgroundColor: 'var(--bg-primary)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          padding: 20,
          color: 'var(--text-primary)',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Open workflow</h2>
        <p style={{ margin: '6px 0 14px', fontSize: 13, color: 'var(--text-secondary)' }}>
          Select a saved workflow from {workflowsDir}.
          {hasLegacy && platform !== 'all' && (
            <>
              {' '}
              <strong style={{ color: 'var(--text-primary)' }}>Legacy</strong> entries
              live in {WORKFLOWS_BASE_DIR} from before vertical scoping —
              opening one and saving will migrate it here automatically.
            </>
          )}
        </p>
        {entries.length === 0 ? (
          <div
            style={{
              padding: 24,
              textAlign: 'center',
              fontSize: 13,
              color: 'var(--text-tertiary)',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            No saved workflows yet.
          </div>
        ) : (
          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
            {entries.map((e) => (
              <button
                key={e.sourcePath}
                type="button"
                onClick={() => onSelect(e)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                  padding: '10px 12px',
                  marginBottom: 4,
                  textAlign: 'left',
                  fontSize: 13,
                  color: 'var(--text-primary)',
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                }}
                onMouseOver={(ev) => (ev.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                onMouseOut={(ev) => (ev.currentTarget.style.backgroundColor = 'var(--bg-secondary)')}
              >
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {e.name}
                </span>
                {e.isLegacy && (
                  <span
                    style={{
                      flexShrink: 0,
                      padding: '1px 8px',
                      fontSize: 10,
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                      color: '#d97706',
                      backgroundColor: 'rgba(217, 119, 6, 0.12)',
                      border: '1px solid rgba(217, 119, 6, 0.32)',
                      borderRadius: 'var(--radius-full)',
                    }}
                    title={`Stored at ${e.sourcePath} — saving will migrate to ${workflowsDir}`}
                  >
                    Legacy
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
        <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '8px 14px',
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--text-secondary)',
              backgroundColor: 'transparent',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty / loading / counts
// ---------------------------------------------------------------------------

function CanvasEmpty() {
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
        zIndex: 1,
      }}
    >
      <div
        style={{
          textAlign: 'center',
          padding: 24,
          backgroundColor: 'var(--bg-secondary)',
          border: '1px dashed var(--border-light)',
          borderRadius: 'var(--radius-lg)',
          maxWidth: 320,
        }}
      >
        <div
          style={{
            width: 56,
            height: 56,
            margin: '0 auto 12px',
            borderRadius: 'var(--radius-full)',
            backgroundColor: 'var(--bg-hover)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
          }}
        >
          <Plus size={28} aria-hidden="true" />
        </div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Start your workflow</div>
        <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>
          Drag a node from the palette onto the canvas. Connect node handles to wire the flow.
        </div>
      </div>
    </div>
  );
}

function CanvasSkeleton() {
  return (
    <div style={{ position: 'absolute', inset: 0, padding: 32, zIndex: 5, pointerEvents: 'none' }}>
      <SkeletonRegion label="Loading workflow…">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
              <SkeletonBlock width={200} height={64} radius={12} delayMs={i * 120} />
              <SkeletonBlock width={80} height={2} delayMs={i * 120} />
              <SkeletonBlock width={200} height={64} radius={12} delayMs={i * 120 + 60} />
            </div>
          ))}
        </div>
      </SkeletonRegion>
    </div>
  );
}

function CountsBadge({ nodes, edges }: { nodes: number; edges: number }) {
  return (
    <div
      style={{
        padding: '4px 10px',
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        color: 'var(--text-secondary)',
        backgroundColor: 'var(--bg-secondary)',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-full)',
      }}
    >
      {nodes} {nodes === 1 ? 'node' : 'nodes'} · {edges} {edges === 1 ? 'edge' : 'edges'}
    </div>
  );
}
