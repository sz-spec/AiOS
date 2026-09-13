"""
Checkpoint API Routes
=====================
Version history endpoints for project checkpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service
from services.checkpoint_service import get_checkpoint_service

router = APIRouter(prefix="/api/v1/projects", tags=["Checkpoints"])


class CheckpointResponse(BaseModel):
    id: str
    description: str
    created_at: str


class CheckpointListResponse(BaseModel):
    checkpoints: List[CheckpointResponse]


class CreateCheckpointRequest(BaseModel):
    description: str = ""


@router.get("/{project_id}/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """List all checkpoints for a project."""
    service = get_checkpoint_service()
    checkpoints = service.list_by_project(project_id)
    return CheckpointListResponse(
        checkpoints=[
            CheckpointResponse(
                id=cp.id, description=cp.description, created_at=cp.created_at
            )
            for cp in checkpoints
        ]
    )


@router.post(
    "/{project_id}/checkpoints", response_model=CheckpointResponse, status_code=201
)
async def create_checkpoint(
    project_id: str,
    req: CreateCheckpointRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a checkpoint from current project state."""
    proj_service = get_project_service()
    project = proj_service.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    cp_service = get_checkpoint_service()
    cp = cp_service.create(project_id, project.files, req.description)
    return CheckpointResponse(
        id=cp.id, description=cp.description, created_at=cp.created_at
    )


@router.post("/{project_id}/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(
    project_id: str,
    checkpoint_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Restore a project to a checkpoint state."""
    proj_service = get_project_service()
    cp_service = get_checkpoint_service()

    files = cp_service.restore(project_id, checkpoint_id)
    if files is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")

    # Create a new checkpoint of current state before restoring
    project = proj_service.get(project_id)
    if project and project.files:
        cp_service.create(project_id, project.files, "Auto-save before restore")

    # Restore files
    proj_service.update(project_id, files=files)
    return {"status": "restored", "files_count": len(files)}
