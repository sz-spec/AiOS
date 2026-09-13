"""
Developer Analytics Routes
============================
Downloads, revenue, ratings, usage metrics by app.
"""

from fastapi import APIRouter, Depends
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser

router = APIRouter(prefix="/api/developers/analytics", tags=["Developer Analytics"])


@router.get("/{developer_id}/overview")
async def get_analytics_overview(
    developer_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get analytics overview for a developer."""
    try:
        from core.repositories import get_async_developer_repository

        apps = await get_async_developer_repository().list_apps(
            developer_id=developer_id
        )
        total_downloads = sum(a.get("downloads", 0) for a in apps)
        ratings = [a.get("avgRating", 0) for a in apps if a.get("avgRating", 0) > 0]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0
        return {
            "developer_id": developer_id,
            "total_apps": len(apps),
            "total_downloads": total_downloads,
            "average_rating": round(avg_rating, 1),
        }
    except Exception:
        return {
            "developer_id": developer_id,
            "total_apps": 0,
            "total_downloads": 0,
            "average_rating": 0.0,
        }


@router.get("/{developer_id}/earnings")
async def get_earnings(
    developer_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get earnings data for a developer."""
    try:
        from core.repositories import get_async_developer_repository

        profile = await get_async_developer_repository().get_profile(
            user_id=developer_id
        )
        return {
            "total_earnings": profile.get("totalEarnings", 0) if profile else 0,
            "pending_payout": 0,
            "last_payout": None,
        }
    except Exception:
        return {"total_earnings": 0, "pending_payout": 0, "last_payout": None}


@router.get("/{developer_id}/apps/{app_id}/metrics")
async def get_app_metrics(
    developer_id: str,
    app_id: str,
    period: Optional[str] = "30d",
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get metrics for a specific app."""
    return {
        "app_id": app_id,
        "period": period,
        "downloads": 0,
        "active_installations": 0,
        "api_calls": 0,
        "avg_rating": 0.0,
        "revenue": 0.0,
    }
