'use client';

import * as ContextMenu from '@radix-ui/react-context-menu';
import { Pencil, Trash2 } from 'lucide-react';
import type { ReactNode } from 'react';

interface FileContextMenuProps {
  children: ReactNode;
  onRename: () => void;
  onDelete: () => void;
  disabled?: boolean;
}

const itemStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  padding: '8px 12px',
  borderRadius: 'var(--radius-sm)',
  fontSize: 13,
  color: 'var(--text-primary)',
  cursor: 'pointer',
  outline: 'none',
  userSelect: 'none',
};

export function FileContextMenu({
  children,
  onRename,
  onDelete,
  disabled = false,
}: FileContextMenuProps) {
  if (disabled) return <>{children}</>;

  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>{children}</ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content
          style={{
            minWidth: 180,
            padding: 4,
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-lg)',
            zIndex: 1000,
          }}
        >
          <ContextMenu.Item
            style={itemStyle}
            onSelect={onRename}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            <Pencil size={14} aria-hidden="true" />
            Rename
          </ContextMenu.Item>
          <ContextMenu.Separator
            style={{ height: 1, backgroundColor: 'var(--border-light)', margin: '4px 0' }}
          />
          <ContextMenu.Item
            style={{ ...itemStyle, color: 'var(--error)' }}
            onSelect={onDelete}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            <Trash2 size={14} aria-hidden="true" />
            Delete
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}
