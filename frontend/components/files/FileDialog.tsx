'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

// ---------------------------------------------------------------------------
// Shared chrome
// ---------------------------------------------------------------------------

function DialogShell({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.45)',
            backdropFilter: 'blur(2px)',
            zIndex: 1000,
            animation: 'vos-fade-in 120ms ease-out',
          }}
        />
        <Dialog.Content
          style={{
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: '90vw',
            maxWidth: 440,
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-lg)',
            boxShadow: 'var(--shadow-lg)',
            padding: 24,
            zIndex: 1001,
            color: 'var(--text-primary)',
            animation: 'vos-zoom-in 140ms ease-out',
          }}
        >
          <style>{`
            @keyframes vos-fade-in { from { opacity: 0 } to { opacity: 1 } }
            @keyframes vos-zoom-in {
              from { opacity: 0; transform: translate(-50%, -50%) scale(0.96); }
              to   { opacity: 1; transform: translate(-50%, -50%) scale(1); }
            }
          `}</style>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <Dialog.Title
                style={{ margin: 0, fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}
              >
                {title}
              </Dialog.Title>
              {description && (
                <Dialog.Description
                  style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}
                >
                  {description}
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                style={{
                  width: 28,
                  height: 28,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--text-secondary)',
                  backgroundColor: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                }}
                onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>
          <div style={{ marginTop: 18 }}>{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------------------
// Prompt dialog (rename / create)
// ---------------------------------------------------------------------------

interface PromptDialogProps {
  open: boolean;
  title: string;
  description?: string;
  initialValue?: string;
  confirmLabel?: string;
  placeholder?: string;
  onConfirm: (value: string) => void | Promise<void>;
  onClose: () => void;
}

export function PromptDialog({
  open,
  title,
  description,
  initialValue = '',
  confirmLabel = 'Save',
  placeholder,
  onConfirm,
  onClose,
}: PromptDialogProps) {
  const [value, setValue] = useState(initialValue);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setValue(initialValue);
      // Defer focus until after dialog content mounts.
      const id = setTimeout(() => inputRef.current?.select(), 50);
      return () => clearTimeout(id);
    }
  }, [open, initialValue]);

  const trimmed = value.trim();
  const disabled = submitting || trimmed.length === 0 || trimmed === initialValue;

  const handleSubmit = async () => {
    if (disabled) return;
    setSubmitting(true);
    try {
      await onConfirm(trimmed);
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogShell
      open={open}
      onOpenChange={(next) => (next ? null : onClose())}
      title={title}
      description={description}
    >
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        onKeyDown={(e) => {
          if (e.key === 'Enter') handleSubmit();
          if (e.key === 'Escape') onClose();
        }}
        style={{
          width: '100%',
          padding: '10px 12px',
          fontSize: 14,
          color: 'var(--text-primary)',
          backgroundColor: 'var(--bg-input)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-sm)',
          outline: 'none',
        }}
        onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
        onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border-light)')}
      />
      <DialogFooter
        onCancel={onClose}
        onConfirm={handleSubmit}
        confirmLabel={submitting ? 'Working…' : confirmLabel}
        confirmDisabled={disabled}
      />
    </DialogShell>
  );
}

// ---------------------------------------------------------------------------
// Confirm dialog (delete)
// ---------------------------------------------------------------------------

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  destructive = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await onConfirm();
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogShell
      open={open}
      onOpenChange={(next) => (next ? null : onClose())}
      title={title}
      description={description}
    >
      <DialogFooter
        onCancel={onClose}
        onConfirm={handleSubmit}
        confirmLabel={submitting ? 'Working…' : confirmLabel}
        destructive={destructive}
        confirmDisabled={submitting}
      />
    </DialogShell>
  );
}

// ---------------------------------------------------------------------------
// Footer (shared)
// ---------------------------------------------------------------------------

function DialogFooter({
  onCancel,
  onConfirm,
  confirmLabel,
  destructive = false,
  confirmDisabled = false,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  confirmLabel: string;
  destructive?: boolean;
  confirmDisabled?: boolean;
}) {
  const confirmBg = destructive ? 'var(--error)' : 'var(--accent)';
  const confirmHover = destructive ? '#b91c1c' : 'var(--accent-hover)';

  return (
    <div style={{ marginTop: 20, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
      <button
        type="button"
        onClick={onCancel}
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
        onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
        onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={onConfirm}
        disabled={confirmDisabled}
        style={{
          padding: '8px 14px',
          fontSize: 13,
          fontWeight: 500,
          color: 'var(--text-inverse)',
          backgroundColor: confirmBg,
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          cursor: confirmDisabled ? 'not-allowed' : 'pointer',
          opacity: confirmDisabled ? 0.6 : 1,
        }}
        onMouseOver={(e) => {
          if (!confirmDisabled) e.currentTarget.style.backgroundColor = confirmHover;
        }}
        onMouseOut={(e) => {
          if (!confirmDisabled) e.currentTarget.style.backgroundColor = confirmBg;
        }}
      >
        {confirmLabel}
      </button>
    </div>
  );
}
