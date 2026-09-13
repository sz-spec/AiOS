"""Auth routes for NextAuth-authenticated apps."""

from fastapi import APIRouter, Depends
from ..middleware import get_current_user, AuthUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/session")
async def get_session(user: AuthUser = Depends(get_current_user)):
    """Return current session info."""
    return {
        "user_id": user.user_id,
        "email": user.email,
    }


@router.get("/csrf")
async def csrf_token():
    """Return a CSRF token for form submissions."""
    import secrets

    return {"csrfToken": secrets.token_urlsafe(32)}
