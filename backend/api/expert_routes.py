"""
Expert-in-the-Loop API Routes
==============================
REST endpoints for the SOS expert request system.

Routes:
    POST /request          — Create a new expert help request
    POST /requests/{id}/claim   — Claim a request as an expert
    POST /requests/{id}/resolve — Resolve a request with patches
    GET  /requests/pending      — List pending requests
    GET  /requests/mine         — List current user's requests
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, Dict, Optional

from api.deps import get_current_user, AuthenticatedUser
from services.expert_pool import (
    get_expert_service,
    package_context,
)

router = APIRouter(tags=["Expert"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateExpertRequestBody(BaseModel):
    project_id: str
    description: str = ""
    build_state: Optional[Dict[str, Any]] = None


class ClaimRequestBody(BaseModel):
    pass


class ResolveRequestBody(BaseModel):
    patches: Dict[str, str] = {}
    summary: str = ""


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/request")
async def create_expert_request(
    body: CreateExpertRequestBody,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a new SOS expert help request."""
    svc = get_expert_service()

    # Package context from build state if provided
    state = body.build_state or {}
    ctx = package_context(state, user_description=body.description)

    req = svc.create_request(
        user_id=user.id,
        project_id=body.project_id,
        context=ctx,
    )

    return {
        "status": "ok",
        "request": {
            "id": req.id,
            "user_id": req.user_id,
            "project_id": req.project_id,
            "status": req.status,
            "has_context": req.context is not None,
            "created_at": req.created_at,
        },
    }


@router.post("/requests/{request_id}/claim")
async def claim_expert_request(
    request_id: str,
    body: ClaimRequestBody,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Claim a pending expert request."""
    svc = get_expert_service()
    claimed = svc.claim_request(request_id, expert_id=user.id)

    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail="Request not found or already claimed",
        )

    return {
        "status": "ok",
        "request": {
            "id": claimed.id,
            "status": claimed.status,
            "claimed_by": claimed.claimed_by,
            "claimed_at": claimed.claimed_at,
        },
    }


@router.post("/requests/{request_id}/resolve")
async def resolve_expert_request(
    request_id: str,
    body: ResolveRequestBody,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Resolve an expert request with patches and summary."""
    svc = get_expert_service()

    resolution = {
        "patches": body.patches,
        "summary": body.summary,
    }

    resolved = svc.resolve_request(request_id, resolution)

    if resolved is None:
        raise HTTPException(
            status_code=409,
            detail="Request not found or already resolved",
        )

    return {
        "status": "ok",
        "request": {
            "id": resolved.id,
            "status": resolved.status,
            "resolved_at": resolved.resolved_at,
        },
    }


@router.get("/requests/pending")
async def list_pending_requests(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all pending expert requests."""
    svc = get_expert_service()
    pending = svc.list_pending()
    return {
        "requests": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "project_id": r.project_id,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in pending
        ]
    }


@router.get("/requests/mine")
async def list_my_requests(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List requests created by the current user."""
    svc = get_expert_service()
    reqs = svc.list_by_user(user.id)
    return {
        "requests": [
            {
                "id": r.id,
                "project_id": r.project_id,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in reqs
        ]
    }
