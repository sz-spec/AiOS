"""
NextAuth Configuration - Server-side NextAuth session validation.
Validates NextAuth JWTs for API route protection.
"""

import os

NEXTAUTH_SECRET = os.getenv("NEXTAUTH_SECRET", "")
NEXTAUTH_URL = os.getenv("NEXTAUTH_URL", "http://localhost:3000")


def get_nextauth_config() -> dict:
    """Return NextAuth configuration for server-side validation."""
    return {
        "secret": NEXTAUTH_SECRET,
        "url": NEXTAUTH_URL,
    }
