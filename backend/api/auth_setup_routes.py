"""
Auth Setup Routes
=================
API endpoints for one-click authentication setup.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service
from services.auth_builder import generate_auth_code

router = APIRouter(prefix="/api/v1/auth", tags=["Auth Setup"])


class AuthSetupRequest(BaseModel):
    project_id: str
    enabled: bool
    providers: List[str] = ["email"]


class AuthSetupResponse(BaseModel):
    enabled: bool
    files_generated: int


@router.post("/setup", response_model=AuthSetupResponse)
async def setup_auth(
    req: AuthSetupRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Enable or disable authentication for a project."""
    service = get_project_service()
    project = service.get(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not req.enabled:
        # Remove auth files
        return AuthSetupResponse(enabled=False, files_generated=0)

    files = generate_auth_code(req.providers)
    service.update_files(req.project_id, files)

    return AuthSetupResponse(enabled=True, files_generated=len(files))
