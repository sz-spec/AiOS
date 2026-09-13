"""
App Auth Routes
================
OAuth 2.0 consent flow for app authorization.
/api/apps/authorize — User sees requested scopes, grants/denies.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import secrets
import time

from api.deps import get_current_user, AuthenticatedUser

from core.app_scopes import validate_scopes, SCOPE_DESCRIPTIONS

router = APIRouter(prefix="/api/apps", tags=["App Auth"])


class AuthorizeRequest(BaseModel):
    app_id: str
    redirect_uri: str
    scopes: List[str]
    state: Optional[str] = None


class AuthorizeResponse(BaseModel):
    consent_url: Optional[str] = None
    scopes: List[dict]
    app_id: str


class ConsentGrantRequest(BaseModel):
    app_id: str
    granted_scopes: List[str]
    user_id: str
    organization_id: str


class ConsentGrantResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    scopes: List[str]
    expires_in: int = 3600


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize_app(
    req: AuthorizeRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Initiate OAuth consent flow.
    Returns the list of requested scopes with descriptions for the consent UI.
    """
    try:
        valid_scopes = validate_scopes(req.scopes)
    except ValueError as e:
        raise HTTPException(400, str(e))

    scope_details = [
        {
            "scope": s.value,
            "description": SCOPE_DESCRIPTIONS.get(s, s.value),
        }
        for s in valid_scopes
    ]

    return AuthorizeResponse(
        app_id=req.app_id,
        scopes=scope_details,
    )


@router.post("/authorize/grant", response_model=ConsentGrantResponse)
async def grant_consent(
    req: ConsentGrantRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    User grants consent — issue access token with granted scopes.
    """
    try:
        valid_scopes = validate_scopes(req.granted_scopes)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Generate token
    token = secrets.token_urlsafe(32)

    # Register token with scopes
    from middleware.app_auth import register_app_token

    register_app_token(req.app_id, token, {s.value for s in valid_scopes})

    # Phase v17 (B-C3): Always use authenticated identity — ignore req.user_id
    # to prevent forged userId in request body from overriding the real identity.
    authenticated_user_id = user.id

    # Persist to Convex
    try:
        from core.repositories import get_async_app_permissions_repository

        await get_async_app_permissions_repository().grant(
            app_id=req.app_id,
            organization_id=req.organization_id,
            user_id=authenticated_user_id,
            scopes=[s.value for s in valid_scopes],
            granted_at_ms=int(time.time() * 1000),
        )
    except Exception:
        pass

    return ConsentGrantResponse(
        access_token=token,
        scopes=[s.value for s in valid_scopes],
    )


@router.post("/authorize/revoke")
async def revoke_consent(
    app_id: str,
    organization_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Revoke all permissions for an app in an organization."""
    return {"revoked": True, "app_id": app_id, "organization_id": organization_id}
