"use client";

import { UserButton as ClerkUserButton } from "@clerk/nextjs";

/**
 * UserButton - Clerk user profile button component.
 * Shows avatar, manages sign-out, and profile settings.
 */
export default function UserButton() {
  return (
    <ClerkUserButton
      afterSignOutUrl="/"
      appearance={{
        elements: {
          avatarBox: "w-8 h-8",
        },
      }}
    />
  );
}
