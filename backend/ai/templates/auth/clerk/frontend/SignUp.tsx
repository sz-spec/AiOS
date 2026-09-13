"use client";

import { SignUp as ClerkSignUp } from "@clerk/nextjs";

/**
 * SignUp - Clerk-powered sign-up page component.
 * Renders the Clerk SignUp widget with redirect configuration.
 */
export default function SignUp() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <ClerkSignUp
        appearance={{
          elements: {
            rootBox: "mx-auto",
            card: "shadow-lg",
          },
        }}
        afterSignUpUrl="/dashboard"
        signInUrl="/sign-in"
      />
    </div>
  );
}
