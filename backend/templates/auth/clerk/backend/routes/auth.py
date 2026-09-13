"""Auth routes for Clerk-authenticated apps."""

from fastapi import APIRouter, Depends
from ..middleware import get_current_user, AuthUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def get_me(user: AuthUser = Depends(get_current_user)):
    """Return current user identity."""
    return {
        "clerk_id": user.clerk_id,
        "email": user.email,
        "org_id": user.org_id,
    }


@router.post("/logout")
async def logout():
    """Client-side logout — server just acknowledges."""
    return {"message": "Logged out"}
