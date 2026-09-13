import type { Metadata } from 'next';
import { headers } from 'next/headers';
import { ClientLayout } from '@/components/shared/ClientLayout';
import { ConvexClerkProvider } from '@/components/providers/ConvexClerkProvider';
import { ThemeProvider } from '@/components/providers/ThemeProvider';
import './globals.css';

export const metadata: Metadata = {
  title: 'VOS3 - AI Operating System',
  description: 'AI Operating System for Business',
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const headersList = await headers();
  const nonce = headersList.get('x-nonce') ?? '';

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="icon" href="/favicon.ico" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
          nonce={nonce}
        />
      </head>
      <body>
        <ThemeProvider>
          <ConvexClerkProvider>
            <ClientLayout>{children}</ClientLayout>
          </ConvexClerkProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
