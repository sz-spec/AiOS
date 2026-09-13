"""
Figma Import Routes
===================
API endpoints for Figma design import.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from api.deps import get_current_user, AuthenticatedUser
from services.figma_service import get_figma_service
from services.project_service import get_project_service

router = APIRouter(prefix="/api/v1/figma", tags=["Figma"])


class FetchFramesRequest(BaseModel):
    url: str


class FrameResponse(BaseModel):
    id: str
    name: str
    thumbnail: str


class ConvertRequest(BaseModel):
    project_id: str
    url: str
    frame_ids: List[str]


@router.post("/frames")
async def fetch_frames(
    req: FetchFramesRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Fetch available frames from a Figma file URL."""
    service = get_figma_service()
    frames = await service.get_frames(req.url)
    return {"frames": frames}


@router.post("/convert")
async def convert_frames(
    req: ConvertRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Convert selected Figma frames to React/Tailwind code."""
    figma_service = get_figma_service()
    proj_service = get_project_service()

    project = proj_service.get(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    files = await figma_service.convert_frames(req.url, req.frame_ids)
    proj_service.update_files(req.project_id, files)

    return {"files": files, "files_count": len(files)}
