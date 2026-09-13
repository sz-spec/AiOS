"""
Deploy Routes
=============
API endpoints for one-click deployment.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service
from services.deploy_service import get_deploy_service

router = APIRouter(prefix="/api/v1/deploy", tags=["Deploy"])


class DeployRequest(BaseModel):
    project_id: str
    subdomain: Optional[str] = None


class DeployResponse(BaseModel):
    deployment_id: str
    url: str
    status: str


@router.post("", response_model=DeployResponse)
async def deploy_project(
    req: DeployRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Deploy a project. First deploy requires subdomain, subsequent deploys reuse it."""
    proj_service = get_project_service()
    project = proj_service.get(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.files:
        raise HTTPException(status_code=400, detail="Project has no files to deploy")

    deploy_service = get_deploy_service()

    # Check for existing deployment
    existing = deploy_service.get_latest(req.project_id)
    subdomain = req.subdomain or (existing.subdomain if existing else None)

    if not subdomain:
        raise HTTPException(
            status_code=400, detail="Subdomain is required for first deployment"
        )

    deployment = await deploy_service.deploy_frontend(
        project_id=req.project_id,
        files=project.files,
        subdomain=subdomain,
    )

    # Update project status
    proj_service.update(req.project_id, status="deployed")

    return DeployResponse(
        deployment_id=deployment.id,
        url=deployment.url,
        status=deployment.status,
    )


@router.get("/{project_id}/status")
async def get_deploy_status(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get latest deployment status for a project."""
    deploy_service = get_deploy_service()
    deployment = deploy_service.get_latest(project_id)
    if not deployment:
        return {"status": "not_deployed"}
    return deployment.to_dict()
