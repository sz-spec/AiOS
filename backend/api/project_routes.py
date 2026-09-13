"""
Project API Routes
==================
CRUD endpoints for V Creator projects.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Union

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ProjectCreateRequest(BaseModel):
    name: Optional[str] = None
    description: str
    category: str
    template_id: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    status: str
    template_id: Optional[str]
    files: Dict[str, str]
    settings: dict
    created_at: Union[str, int, float]
    updated_at: Union[str, int, float]


class ProjectListResponse(BaseModel):
    projects: List[ProjectResponse]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=ProjectListResponse)
async def list_projects(user: AuthenticatedUser = Depends(get_current_user)):
    """List all projects for the current user."""
    service = get_project_service()
    projects = service.list_all()
    return ProjectListResponse(
        projects=[ProjectResponse(**p.to_dict()) for p in projects]
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get a single project by ID."""
    service = get_project_service()
    project = service.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.to_dict())


@router.delete("/{project_id}")
async def delete_project(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Delete a project."""
    service = get_project_service()
    project = service.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Ownership check: only the project owner can delete
    if project.user_id != user.id and not user.has_permission("admin:full"):
        raise HTTPException(
            status_code=403, detail="Not authorized to delete this project"
        )
    if not service.delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "deleted"}
