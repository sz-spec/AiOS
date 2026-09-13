'use client';

import dynamic from 'next/dynamic';
import { motion } from 'framer-motion';
import { usePathname } from 'next/navigation';
import { Toaster } from 'sonner';
import { useTheme } from 'next-themes';
import { Navigation } from '@/components/shared/Navigation';
import { ErrorBoundary } from '@/components/shared/ErrorBoundary';
import { CommandPalette } from '@/components/shared/CommandPalette';
import { LocalityProvider } from '@/context/LocalityContext';
import { SovereignStatus } from '@/components/layout/SovereignStatus';

const VoiceController = dynamic(
  () => import('@/components/shared/VoiceController').then((mod) => mod.VoiceController),
  { ssr: false }
);

export function ClientLayout({ children }: { children: React.ReactNode }) {
  const { resolvedTheme } = useTheme();
  const pathname = usePathname();

  return (
    <ErrorBoundary>
      {/* W6.3 — locality context mounted near the root so every client
          component can call useLocality() without a separate boot fetch. */}
      <LocalityProvider>
        <div style={{ display: 'flex', minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
          <Navigation />
          <motion.main
            key={pathname}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            style={{ flex: 1, overflow: 'auto', minWidth: 0 }}
          >
            {children}
          </motion.main>
          <VoiceController />
          <CommandPalette />
          {/* W6.3 — sovereign-mode status badge pinned to top-right.
              Sits above the Toaster so a fresh announcement stack
              doesn't obscure the air-gap indicator. */}
          <div
            aria-hidden={false}
            style={{
              position: 'fixed',
              top: 12,
              right: 16,
              zIndex: 50,
              pointerEvents: 'auto',
            }}
          >
            <SovereignStatus />
          </div>
          <Toaster
            position="top-right"
            theme={(resolvedTheme as 'light' | 'dark' | undefined) ?? 'system'}
            richColors
            closeButton
          />
        </div>
      </LocalityProvider>
    </ErrorBoundary>
  );
}
