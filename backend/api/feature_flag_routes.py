"""
Feature Flag API Routes
=======================
Admin endpoints for managing feature flags at runtime.
All write endpoints require admin permission.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser
from services.feature_flags import get_feature_flags, FeatureFlagService

router = APIRouter(prefix="/api/feature-flags", tags=["Feature Flags"])


class UpsertFlagRequest(BaseModel):
    enabled: Optional[bool] = None
    rollout_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    description: Optional[str] = None
    metadata: Optional[dict] = None


class EvaluateFlagRequest(BaseModel):
    flag_name: str
    user_id: Optional[str] = ""


@router.get("/")
async def list_flags(
    user: AuthenticatedUser = Depends(get_current_user),
    flags: FeatureFlagService = Depends(get_feature_flags),
):
    """List all feature flags (admin only)."""
    if "admin:full" not in user.permissions:
        raise HTTPException(status_code=403, detail="Admin permission required")
    return {"flags": flags.list_flags()}


@router.get("/{flag_name}")
async def get_flag(
    flag_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
    flags: FeatureFlagService = Depends(get_feature_flags),
):
    """Get a single flag definition."""
    if "admin:full" not in user.permissions:
        raise HTTPException(status_code=403, detail="Admin permission required")
    flag = flags.get_flag(flag_name)
    if not flag:
        raise HTTPException(status_code=404, detail=f"Flag {flag_name!r} not found")
    return flag.to_dict()


@router.put("/{flag_name}")
async def upsert_flag(
    flag_name: str,
    body: UpsertFlagRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    flags: FeatureFlagService = Depends(get_feature_flags),
):
    """Create or update a feature flag (admin only)."""
    if "admin:full" not in user.permissions:
        raise HTTPException(status_code=403, detail="Admin permission required")
    flag = await flags.upsert_flag(
        name=flag_name,
        enabled=body.enabled,
        rollout_pct=body.rollout_pct,
        description=body.description,
        metadata=body.metadata,
    )
    return {"flag": flag.to_dict(), "message": f"Flag {flag_name!r} updated"}


@router.delete("/{flag_name}")
async def delete_flag(
    flag_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
    flags: FeatureFlagService = Depends(get_feature_flags),
):
    """Delete a custom flag (cannot delete built-in flags)."""
    if "admin:full" not in user.permissions:
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        removed = await flags.delete_flag(flag_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Flag {flag_name!r} not found")
    return {"message": f"Flag {flag_name!r} deleted"}


@router.post("/evaluate")
async def evaluate_flag(
    body: EvaluateFlagRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    flags: FeatureFlagService = Depends(get_feature_flags),
):
    """Evaluate a flag for a specific user_id (uses caller's user_id if not provided)."""
    uid = body.user_id or user.id
    enabled = await flags.is_enabled(body.flag_name, user_id=uid)
    return {
        "flag_name": body.flag_name,
        "user_id": uid,
        "enabled": enabled,
    }
