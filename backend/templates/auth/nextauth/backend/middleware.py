"""NextAuth session verification middleware for FastAPI."""

import os
from typing import Optional
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class AuthUser:
    user_id: str
    email: Optional[str] = None


async def get_current_user(request: Request) -> AuthUser:
    """Verify NextAuth JWT from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    try:
        import jwt

        secret = os.environ.get("NEXTAUTH_SECRET", "")
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return AuthUser(
            user_id=payload.get("sub", payload.get("id", "")),
            email=payload.get("email"),
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid session: {e}")
