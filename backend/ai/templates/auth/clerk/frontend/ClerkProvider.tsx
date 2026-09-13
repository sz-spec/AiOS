"use client";

import { ClerkProvider as BaseClerkProvider } from "@clerk/nextjs";

/**
 * ClerkProvider - Wraps the application with Clerk authentication context.
 */
export default function ClerkProvider({ children }: { children: React.ReactNode }) {
  return (
    <BaseClerkProvider>
      {children}
    </BaseClerkProvider>
  );
}
