'use client';

import { Command } from 'cmdk';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTheme } from 'next-themes';
import {
  FileText,
  Folder,
  MessageSquare,
  Moon,
  Settings,
  ShoppingBag,
  Sun,
  Workflow,
} from 'lucide-react';

interface CommandItem {
  id: string;
  label: string;
  shortcut?: string;
  icon: React.ReactNode;
  onSelect: () => void;
  group: 'Navigation' | 'Preferences';
}

export function CommandPalette() {
  const router = useRouter();
  const { setTheme, resolvedTheme } = useTheme();
  const [open, setOpen] = useState(false);

  // Cmd+K / Ctrl+K toggles the palette globally.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      if (key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
      if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open]);

  const close = () => setOpen(false);

  const run = (fn: () => void) => () => {
    close();
    fn();
  };

  const items: CommandItem[] = [
    {
      id: 'nav.chat',
      label: 'Go to Chat',
      icon: <MessageSquare size={16} />,
      group: 'Navigation',
      onSelect: run(() => router.push('/chat')),
    },
    {
      id: 'nav.files',
      label: 'Go to Files',
      icon: <Folder size={16} />,
      group: 'Navigation',
      onSelect: run(() => router.push('/files')),
    },
    {
      id: 'nav.workflows',
      label: 'Go to Workflows',
      icon: <Workflow size={16} />,
      group: 'Navigation',
      onSelect: run(() => router.push('/workflows')),
    },
    {
      id: 'nav.marketplace',
      label: 'Go to Marketplace',
      icon: <ShoppingBag size={16} />,
      group: 'Navigation',
      onSelect: run(() => router.push('/marketplace')),
    },
    {
      id: 'nav.settings',
      label: 'Go to Settings',
      icon: <Settings size={16} />,
      group: 'Navigation',
      onSelect: run(() => router.push('/settings')),
    },
    {
      id: 'nav.audit',
      label: 'Go to Audit Log',
      icon: <FileText size={16} />,
      group: 'Navigation',
      onSelect: run(() => router.push('/explorer')),
    },
    {
      id: 'pref.theme',
      label: resolvedTheme === 'dark' ? 'Toggle Light Mode' : 'Toggle Dark Mode',
      shortcut: '⇧⌘L',
      icon: resolvedTheme === 'dark' ? <Sun size={16} /> : <Moon size={16} />,
      group: 'Preferences',
      onSelect: run(() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')),
    },
  ];

  if (!open) return null;

  const groups = ['Navigation', 'Preferences'] as const;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onClick={close}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.45)',
        backdropFilter: 'blur(2px)',
        zIndex: 1100,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '14vh',
        animation: 'vos-fade-in 120ms ease-out',
      }}
    >
      <style>{`
        @keyframes vos-fade-in { from { opacity: 0 } to { opacity: 1 } }
        @keyframes vos-cmdk-zoom {
          from { opacity: 0; transform: translateY(-8px) scale(0.98); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        [cmdk-input] {
          width: 100%;
          padding: 14px 16px;
          font-size: 15px;
          color: var(--text-primary);
          background-color: transparent;
          border: none;
          outline: none;
          font-family: inherit;
        }
        [cmdk-list] {
          max-height: 360px;
          overflow-y: auto;
          padding: 6px;
        }
        [cmdk-group-heading] {
          padding: 8px 10px 4px;
          font-size: 11px;
          font-weight: 700;
          color: var(--text-tertiary);
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        [cmdk-item] {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 10px 12px;
          font-size: 13px;
          color: var(--text-primary);
          border-radius: var(--radius-sm);
          cursor: pointer;
          user-select: none;
        }
        [cmdk-item][data-selected="true"] {
          background-color: var(--bg-hover);
        }
        [cmdk-empty] {
          padding: 24px;
          text-align: center;
          font-size: 13px;
          color: var(--text-tertiary);
        }
      `}</style>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '90vw',
          maxWidth: 560,
          backgroundColor: 'var(--bg-primary)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          overflow: 'hidden',
          animation: 'vos-cmdk-zoom 140ms ease-out',
        }}
      >
        <Command label="Command palette" loop>
          <Command.Input placeholder="Type a command or search…" autoFocus />
          <Command.List>
            <Command.Empty>No results found.</Command.Empty>
            {groups.map((group) => {
              const groupItems = items.filter((i) => i.group === group);
              if (groupItems.length === 0) return null;
              return (
                <Command.Group key={group} heading={group}>
                  {groupItems.map((item) => (
                    <Command.Item
                      key={item.id}
                      value={item.label}
                      onSelect={item.onSelect}
                    >
                      <span style={{ color: 'var(--text-secondary)', display: 'inline-flex' }}>
                        {item.icon}
                      </span>
                      <span style={{ flex: 1 }}>{item.label}</span>
                      {item.shortcut && (
                        <kbd
                          style={{
                            fontSize: 11,
                            fontFamily: 'inherit',
                            color: 'var(--text-tertiary)',
                            padding: '2px 6px',
                            border: '1px solid var(--border-light)',
                            borderRadius: 4,
                          }}
                        >
                          {item.shortcut}
                        </kbd>
                      )}
                    </Command.Item>
                  ))}
                </Command.Group>
              );
            })}
          </Command.List>
        </Command>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            padding: '8px 14px',
            fontSize: 11,
            color: 'var(--text-tertiary)',
            borderTop: '1px solid var(--border-light)',
            backgroundColor: 'var(--bg-secondary)',
          }}
        >
          <span>↑↓ navigate</span>
          <span>↵ select</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}
