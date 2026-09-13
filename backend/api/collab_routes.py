"""
Collaboration Routes
====================
API endpoints for real-time collaboration.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser
from services.collab_service import get_collab_service

router = APIRouter(prefix="/api/v1/collab", tags=["Collaboration"])


class InviteRequest(BaseModel):
    project_id: str
    email: str
    role: str = "editor"


@router.post("/invite")
async def invite_collaborator(
    req: InviteRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Invite a collaborator to a project."""
    service = get_collab_service()
    collab = service.invite(req.project_id, req.email, req.role)
    return {"id": collab.id, "email": collab.user_email, "role": collab.role}


@router.get("/{project_id}/collaborators")
async def list_collaborators(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """List collaborators for a project."""
    service = get_collab_service()
    collabs = service.list_collaborators(project_id)
    return {
        "collaborators": [
            {"id": c.id, "email": c.user_email, "role": c.role, "accepted": c.accepted}
            for c in collabs
        ]
    }


@router.delete("/{project_id}/collaborators/{collaborator_id}")
async def remove_collaborator(
    project_id: str,
    collaborator_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Remove a collaborator from a project."""
    service = get_collab_service()
    if not service.remove(project_id, collaborator_id):
        raise HTTPException(status_code=404, detail="Collaborator not found")
    return {"status": "removed"}
