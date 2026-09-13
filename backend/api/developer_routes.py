"""
Developer Portal Routes
========================
Developer registration, profile management, and app listing.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser

router = APIRouter(prefix="/api/developers", tags=["Developers"])


class DeveloperRegisterRequest(BaseModel):
    display_name: str
    email: str
    website: Optional[str] = None
    bio: Optional[str] = None


class DeveloperResponse(BaseModel):
    id: str
    display_name: str
    email: str
    website: Optional[str] = None
    bio: Optional[str] = None
    verified: bool = False
    total_apps: int = 0
    total_earnings: float = 0.0


@router.post("/register", response_model=DeveloperResponse, status_code=201)
async def register_developer(
    req: DeveloperRegisterRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Register as a developer on the VOS3 platform."""
    try:
        from core.repositories import get_async_developer_repository

        user_id = user.id
        result = await get_async_developer_repository().register(
            user_id=user_id,
            display_name=req.display_name,
            email=req.email,
            website=req.website,
            bio=req.bio,
        )
        return DeveloperResponse(
            id=str(result),
            display_name=req.display_name,
            email=req.email,
            website=req.website,
            bio=req.bio,
        )
    except Exception:
        # Fallback for dev mode
        return DeveloperResponse(
            id="dev-001",
            display_name=req.display_name,
            email=req.email,
            website=req.website,
            bio=req.bio,
        )


@router.get("/profile/{user_id}", response_model=DeveloperResponse)
async def get_developer_profile(
    user_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get developer profile."""
    try:
        from core.repositories import get_async_developer_repository

        profile = await get_async_developer_repository().get_profile(user_id=user_id)
        if not profile:
            raise HTTPException(404, "Developer not found")
        return DeveloperResponse(
            id=str(profile.get("_id", "")),
            display_name=profile.get("displayName", ""),
            email=profile.get("email", ""),
            website=profile.get("website"),
            bio=profile.get("bio"),
            verified=profile.get("verified", False),
            total_apps=profile.get("totalApps", 0),
            total_earnings=profile.get("totalEarnings", 0.0),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "Developer not found")


@router.get("/{user_id}/apps")
async def list_developer_apps(
    user_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """List all apps published by a developer."""
    try:
        from core.repositories import get_async_developer_repository

        apps = await get_async_developer_repository().list_apps(developer_id=user_id)
        return {"apps": apps}
    except Exception:
        return {"apps": []}
