"""Custom JWT verification middleware for FastAPI."""

import os
from typing import Optional
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class AuthUser:
    user_id: str
    email: Optional[str] = None


async def get_current_user(request: Request) -> AuthUser:
    """Verify JWT from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    try:
        import jwt

        secret = os.environ.get("JWT_SECRET", "")
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return AuthUser(
            user_id=payload.get("sub", ""),
            email=payload.get("email"),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
