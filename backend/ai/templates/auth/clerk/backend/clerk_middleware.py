"""
Clerk Middleware - JWT verification for Clerk authentication.
Validates Clerk-issued JWT tokens on protected API routes.
"""

import os

import jwt
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "")

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Extract and validate user from Clerk JWT token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    try:
        payload = jwt.decode(
            credentials.credentials,
            CLERK_SECRET_KEY,
            algorithms=["RS256", "HS256"],
            options={"verify_aud": False},
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        return {"id": user_id, "email": payload.get("email", "")}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
