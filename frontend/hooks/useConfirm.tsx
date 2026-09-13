'use client';

import { useCallback, useState } from 'react';
import { ConfirmDialog } from '@/components/files/FileDialog';

interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  destructive?: boolean;
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (value: boolean) => void;
}

/**
 * Promise-based confirm dialog. Drop-in replacement for `window.confirm`,
 * styled with the project's ConfirmDialog so it matches the rest of the app.
 *
 *   const { confirm, ConfirmDialog: Mount } = useConfirm();
 *   ...
 *   if (await confirm({ title: 'Delete?', destructive: true })) doIt();
 *   ...
 *   return <>{...}<Mount /></>;
 */
export function useConfirm() {
  const [pending, setPending] = useState<PendingConfirm | null>(null);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setPending({ ...options, resolve });
    });
  }, []);

  const close = useCallback(
    (value: boolean) => {
      setPending((current) => {
        current?.resolve(value);
        return null;
      });
    },
    [],
  );

  const Mount = useCallback(() => {
    if (!pending) return null;
    return (
      <ConfirmDialog
        open
        title={pending.title}
        description={pending.description}
        confirmLabel={pending.confirmLabel}
        destructive={pending.destructive}
        onConfirm={() => close(true)}
        onClose={() => close(false)}
      />
    );
  }, [pending, close]);

  return { confirm, ConfirmDialog: Mount };
}
