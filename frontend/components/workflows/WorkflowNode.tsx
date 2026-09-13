'use client';

import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import { NODE_KINDS, type WorkflowNodeData } from './types';

/**
 * Custom node component for the workflow builder.
 * Styled to match the VOS3 aesthetic via CSS variables so it works in both
 * light and dark mode without a separate theme branch.
 */
export function WorkflowNode({ data, selected }: NodeProps) {
  const nodeData = data as unknown as WorkflowNodeData;
  const meta = NODE_KINDS[nodeData.kind];
  if (!meta) return null;

  return (
    <div
      style={{
        minWidth: 200,
        padding: 12,
        backgroundColor: 'var(--bg-primary)',
        border: `1px solid ${selected ? meta.accent : 'var(--border-light)'}`,
        borderLeft: `4px solid ${meta.accent}`,
        borderRadius: 'var(--radius-md)',
        boxShadow: selected ? `0 0 0 3px ${meta.accent}30` : 'var(--shadow-sm)',
        fontSize: 13,
        color: 'var(--text-primary)',
        transition: 'box-shadow 120ms ease, border-color 120ms ease',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: meta.accent, border: 'none', width: 8, height: 8 }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span aria-hidden="true" style={{ fontSize: 18, lineHeight: 1 }}>
          {meta.icon}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontWeight: 600,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {nodeData.label || meta.label}
          </div>
          <div
            style={{
              fontSize: 11,
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}
          >
            {meta.category}
          </div>
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        style={{ background: meta.accent, border: 'none', width: 8, height: 8 }}
      />
    </div>
  );
}
