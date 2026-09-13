"""
Marketplace Routes
==================
API endpoints for the component marketplace.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser
from services.marketplace_service import get_marketplace_service
from services.project_service import get_project_service

router = APIRouter(prefix="/api/v1/marketplace", tags=["Marketplace"])


class InstallRequest(BaseModel):
    project_id: str
    component_id: str


@router.get("/components")
async def list_components(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List marketplace components with optional search/filter."""
    service = get_marketplace_service()
    components = service.search(query=search or "", category=category or "")
    return {
        "components": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "category": c.category,
                "rating": c.rating,
                "downloads": c.downloads,
            }
            for c in components
        ]
    }


@router.post("/install")
async def install_component(
    req: InstallRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Install a marketplace component into a project."""
    marketplace = get_marketplace_service()
    proj_service = get_project_service()

    project = proj_service.get(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    files = marketplace.install(req.component_id)
    if not files:
        raise HTTPException(status_code=404, detail="Component not found")

    proj_service.update_files(req.project_id, files)
    return {"installed": True, "files": list(files.keys())}
