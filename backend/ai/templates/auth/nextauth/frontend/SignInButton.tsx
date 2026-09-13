"use client";

import { signIn, signOut, useSession } from "next-auth/react";

/**
 * SignInButton - NextAuth sign-in/sign-out button component.
 */
export default function SignInButton() {
  const { data: session } = useSession();

  if (session) {
    return (
      <button onClick={() => signOut()} className="px-4 py-2 rounded bg-red-500 text-white">
        Sign Out
      </button>
    );
  }

  return (
    <button onClick={() => signIn()} className="px-4 py-2 rounded bg-blue-600 text-white">
      Sign In
    </button>
  );
}
