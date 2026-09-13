"use client";

import { SessionProvider as NextAuthSessionProvider } from "next-auth/react";

/**
 * SessionProvider - Wraps the application with NextAuth session context.
 */
export default function SessionProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextAuthSessionProvider>
      {children}
    </NextAuthSessionProvider>
  );
}
