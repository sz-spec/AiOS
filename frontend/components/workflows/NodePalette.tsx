'use client';

import { NODE_KINDS, type WorkflowNodeKind } from './types';

const CATEGORIES: Array<'Triggers' | 'Actions' | 'Logic'> = ['Triggers', 'Actions', 'Logic'];

export const PALETTE_DATA_TYPE = 'application/vos3-node-kind';

interface NodePaletteProps {
  collapsed: boolean;
  onCollapsedChange: (next: boolean) => void;
}

export function NodePalette({ collapsed, onCollapsedChange }: NodePaletteProps) {
  const onDragStart = (e: React.DragEvent<HTMLButtonElement>, kind: WorkflowNodeKind) => {
    e.dataTransfer.setData(PALETTE_DATA_TYPE, kind);
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <aside
      aria-label="Node palette"
      style={{
        width: collapsed ? 48 : 220,
        flexShrink: 0,
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border-light)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        transition: 'width 160ms ease',
      }}
    >
      <button
        type="button"
        onClick={() => onCollapsedChange(!collapsed)}
        title={collapsed ? 'Expand palette' : 'Collapse palette'}
        aria-label={collapsed ? 'Expand palette' : 'Collapse palette'}
        style={{
          height: 40,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          padding: collapsed ? 0 : '0 14px',
          fontSize: 12,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: 'var(--text-tertiary)',
          backgroundColor: 'transparent',
          border: 'none',
          borderBottom: '1px solid var(--border-light)',
          cursor: 'pointer',
        }}
      >
        {!collapsed && <span>Palette</span>}
        <span aria-hidden="true">{collapsed ? '›' : '‹'}</span>
      </button>

      {!collapsed && (
        <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
          {CATEGORIES.map((cat) => {
            const items = (Object.entries(NODE_KINDS) as Array<[WorkflowNodeKind, (typeof NODE_KINDS)[WorkflowNodeKind]]>).filter(
              ([, m]) => m.category === cat,
            );
            return (
              <div key={cat} style={{ marginBottom: 12 }}>
                <div
                  style={{
                    padding: '6px 8px',
                    fontSize: 10,
                    fontWeight: 700,
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                  }}
                >
                  {cat}
                </div>
                {items.map(([kind, meta]) => (
                  <button
                    key={kind}
                    type="button"
                    draggable
                    onDragStart={(e) => onDragStart(e, kind)}
                    title={meta.description}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      width: '100%',
                      padding: '8px 10px',
                      marginBottom: 4,
                      fontSize: 13,
                      textAlign: 'left',
                      color: 'var(--text-primary)',
                      backgroundColor: 'var(--bg-primary)',
                      border: '1px solid var(--border-light)',
                      borderLeft: `3px solid ${meta.accent}`,
                      borderRadius: 'var(--radius-sm)',
                      cursor: 'grab',
                    }}
                    onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                    onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-primary)')}
                  >
                    <span aria-hidden="true" style={{ fontSize: 16 }}>
                      {meta.icon}
                    </span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span
                        style={{
                          display: 'block',
                          fontWeight: 500,
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {meta.label}
                      </span>
                      <span
                        style={{
                          display: 'block',
                          fontSize: 11,
                          color: 'var(--text-tertiary)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {meta.description}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            );
          })}
          <div
            style={{
              marginTop: 8,
              padding: 10,
              fontSize: 11,
              color: 'var(--text-tertiary)',
              backgroundColor: 'var(--bg-primary)',
              border: '1px dashed var(--border-light)',
              borderRadius: 'var(--radius-sm)',
              lineHeight: 1.4,
            }}
          >
            Drag any node onto the canvas to add it. Connect handles to wire the flow.
          </div>
        </div>
      )}
    </aside>
  );
}
