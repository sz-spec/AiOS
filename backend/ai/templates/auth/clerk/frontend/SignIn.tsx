"use client";

import { SignIn as ClerkSignIn } from "@clerk/nextjs";

/**
 * SignIn - Clerk-powered sign-in page component.
 * Renders the Clerk SignIn widget with redirect configuration.
 */
export default function SignIn() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <ClerkSignIn
        appearance={{
          elements: {
            rootBox: "mx-auto",
            card: "shadow-lg",
          },
        }}
        afterSignInUrl="/dashboard"
        signUpUrl="/sign-up"
      />
    </div>
  );
}
