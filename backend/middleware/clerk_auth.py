"""
Clerk Auth — re-exports from auth.py for TIS compliance.

All Clerk JWT validation, JWKS caching, Convex user resolution,
and FastAPI dependencies are implemented in middleware/auth.py.

Usage:
    from middleware.clerk_auth import get_current_user, get_optional_user, AuthUser

    @router.get("/protected")
    async def protected(user: AuthUser = Depends(get_current_user)):
        return {"clerk_id": user.id, "convex_id": user.convex_user_id}
"""

from middleware.auth import (
    AuthenticatedUser as AuthUser,
    get_current_user,
    verify_auth,
    require_permission,
    require_org_membership,
    auth_middleware,
    DEV_MODE,
)
from fastapi import Request, HTTPException


# TIS Section 1.3.3: get_optional_user — returns None instead of 401
async def get_optional_user(request: Request) -> AuthUser | None:
    """Same as get_current_user but returns None for unauthenticated requests."""
    try:
        return await verify_auth(request)
    except HTTPException:
        return None


__all__ = [
    "AuthUser",
    "get_current_user",
    "get_optional_user",
    "verify_auth",
    "require_permission",
    "require_org_membership",
    "auth_middleware",
    "DEV_MODE",
]
