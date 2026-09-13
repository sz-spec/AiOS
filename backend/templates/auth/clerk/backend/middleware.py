"""Clerk JWT verification middleware for FastAPI."""

import os
from typing import Optional
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class AuthUser:
    clerk_id: str
    email: Optional[str] = None
    org_id: Optional[str] = None


async def get_current_user(request: Request) -> AuthUser:
    """Extract and verify Clerk JWT from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    try:
        import jwt

        issuer_url = os.environ.get("CLERK_ISSUER_URL", "")
        secret = os.environ.get("CLERK_SECRET_KEY", "")

        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer_url,
            options={"verify_aud": False},
        )
        return AuthUser(
            clerk_id=payload.get("sub", ""),
            email=payload.get("email"),
            org_id=payload.get("org_id"),
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
