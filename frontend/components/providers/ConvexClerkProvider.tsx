"use client";

import { useEffect, useRef } from "react";
import { ClerkProvider, useAuth } from "@clerk/nextjs";
import { ConvexProviderWithClerk } from "convex/react-clerk";
import { ConvexReactClient } from "convex/react";

/**
 * Composite provider for Convex + Clerk that degrades gracefully when
 * either dependency's environment variable is missing.
 *
 * Failure modes handled:
 *
 *  1. NEXT_PUBLIC_CONVEX_URL absent
 *     → Skip the `<ConvexProviderWithClerk>` wrapper. The app still
 *       loads inside `<ClerkProvider>`. Pages that call `useQuery` /
 *       `useMutation` from convex/react will throw, but every
 *       REST-driven surface (dashboard, files, marketplace, workflows,
 *       agents) keeps working.
 *
 *  2. NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY absent
 *     → Render a friendly configuration screen. We cannot stub Clerk
 *       cleanly because `useAuth()` is consumed in many places at the
 *       top of render and throws synchronously when called outside
 *       `<ClerkProvider>`. A clear setup screen is more honest than a
 *       cascade of "useAuth must be used inside ClerkProvider" errors.
 *
 *  3. Both present
 *     → Normal operation: Clerk wraps Convex wraps children.
 *
 * The Convex client is created once and memoized in a ref so HMR
 * doesn't leak websocket connections across reloads.
 */
const CONVEX_URL = process.env.NEXT_PUBLIC_CONVEX_URL;
const CLERK_PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export function ConvexClerkProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const convexRef = useRef<ConvexReactClient | null>(null);
  if (CONVEX_URL && !convexRef.current) {
    convexRef.current = new ConvexReactClient(CONVEX_URL);
  }

  // One-time dev warnings so missing config is loud in the console
  // without spamming on every render.
  useEffect(() => {
    if (!CONVEX_URL) {
      // eslint-disable-next-line no-console
      console.warn(
        "[VOS3] NEXT_PUBLIC_CONVEX_URL not set — running without Convex. " +
          "REST-driven pages will work; Convex hooks (useQuery/useMutation) " +
          "will throw if invoked.",
      );
    }
    if (!CLERK_PUBLISHABLE_KEY) {
      // eslint-disable-next-line no-console
      console.warn(
        "[VOS3] NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY not set — auth disabled. " +
          "Showing configuration screen.",
      );
    }
  }, []);

  if (!CLERK_PUBLISHABLE_KEY) {
    return <ConfigurationRequiredScreen />;
  }

  const inner = convexRef.current ? (
    <ConvexProviderWithClerk client={convexRef.current} useAuth={useAuth}>
      {children}
    </ConvexProviderWithClerk>
  ) : (
    children
  );

  return (
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY}>{inner}</ClerkProvider>
  );
}

// ---------------------------------------------------------------------------
// Configuration-required screen
// ---------------------------------------------------------------------------

function ConfigurationRequiredScreen() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 32,
        backgroundColor: "var(--bg-primary)",
        color: "var(--text-primary)",
        fontFamily:
          "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >
      <div style={{ maxWidth: 560, width: "100%" }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 12,
            padding: "4px 12px",
            fontSize: 11,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "#d97706",
            backgroundColor: "rgba(217, 119, 6, 0.12)",
            border: "1px solid rgba(217, 119, 6, 0.32)",
            borderRadius: 9999,
            marginBottom: 16,
          }}
        >
          Configuration required
        </div>
        <h1
          style={{
            margin: 0,
            fontSize: 26,
            fontWeight: 700,
            letterSpacing: "-0.02em",
          }}
        >
          Set up Clerk to load VOS3
        </h1>
        <p
          style={{
            margin: "10px 0 24px",
            fontSize: 14,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}
        >
          The frontend cannot render without a Clerk publishable key — every
          page calls <code>useAuth()</code> for token retrieval. Add the value
          below to <code>frontend/.env.local</code> and restart the dev server.
        </p>

        <Step
          n={1}
          title="Get a Clerk publishable key"
          body={
            <>
              Open{" "}
              <a
                href="https://dashboard.clerk.com"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent)" }}
              >
                dashboard.clerk.com
              </a>{" "}
              → API Keys → copy the value starting with <code>pk_test_</code>{" "}
              or <code>pk_live_</code>.
            </>
          }
        />
        <Step
          n={2}
          title="Add it to .env.local"
          body={
            <pre
              style={{
                margin: "8px 0 0",
                padding: 12,
                fontSize: 12,
                fontFamily: "monospace",
                color: "var(--text-primary)",
                backgroundColor: "var(--bg-secondary)",
                border: "1px solid var(--border-light)",
                borderRadius: 8,
                overflow: "auto",
              }}
            >
              {`# frontend/.env.local
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
NEXT_PUBLIC_CONVEX_URL=https://your-deployment.convex.cloud`}
            </pre>
          }
        />
        <Step
          n={3}
          title="Restart the dev server"
          body={
            <pre
              style={{
                margin: "8px 0 0",
                padding: 12,
                fontSize: 12,
                fontFamily: "monospace",
                color: "var(--text-primary)",
                backgroundColor: "var(--bg-secondary)",
                border: "1px solid var(--border-light)",
                borderRadius: 8,
              }}
            >
              {`npm run dev`}
            </pre>
          }
        />

        <div
          style={{
            marginTop: 28,
            padding: 14,
            fontSize: 12,
            color: "var(--text-tertiary)",
            backgroundColor: "var(--bg-secondary)",
            border: "1px dashed var(--border-light)",
            borderRadius: 8,
            lineHeight: 1.6,
          }}
        >
          <strong style={{ color: "var(--text-secondary)" }}>Note:</strong>{" "}
          <code>NEXT_PUBLIC_CONVEX_URL</code> is optional in dev. If absent,
          REST-driven pages (dashboard, files, marketplace, workflows) still
          work; only Convex hooks (<code>useQuery</code>, <code>useMutation</code>)
          will throw at call sites that exist in <code>components/build/*</code>.
        </div>
      </div>
    </div>
  );
}

function Step({
  n,
  title,
  body,
}: {
  n: number;
  title: string;
  body: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: 14,
        padding: "12px 0",
        borderTop: "1px solid var(--border-light)",
      }}
    >
      <div
        aria-hidden="true"
        style={{
          flexShrink: 0,
          width: 24,
          height: 24,
          borderRadius: "50%",
          backgroundColor: "var(--bg-hover)",
          color: "var(--text-secondary)",
          fontSize: 12,
          fontWeight: 700,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {n}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
        <div
          style={{
            marginTop: 4,
            fontSize: 13,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}
        >
          {body}
        </div>
      </div>
    </div>
  );
}
