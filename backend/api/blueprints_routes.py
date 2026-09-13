"""
Industry Blueprints API Routes
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser
from blueprints.blueprints_service import (
    BlueprintsService,
    Industry,
    BlueprintStatus,
    get_blueprints_service,
)

router = APIRouter(prefix="/blueprints", tags=["blueprints"])


class DeployRequest(BaseModel):
    blueprint_id: str
    customizations: dict = {}


class UpdateDeploymentRequest(BaseModel):
    enabled_agents: Optional[list[str]] = None
    enabled_workflows: Optional[list[str]] = None
    customizations: Optional[dict] = None
    status: Optional[str] = None


@router.get("")
async def list_blueprints(
    industry: Optional[str] = None,
    status: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    try:
        ind = Industry(industry) if industry else None
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid industry: {industry}")
    try:
        st = BlueprintStatus(status) if status else None
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    blueprints = service.get_blueprints(industry=ind, status=st)
    return {
        "count": len(blueprints),
        "blueprints": [bp.to_summary() for bp in blueprints],
    }


@router.get("/industries")
async def list_industries(
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    return {"industries": service.get_industries()}


@router.post("/deploy")
async def deploy_blueprint(
    request: DeployRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    deployment = service.deploy_blueprint(
        blueprint_id=request.blueprint_id,
        user_id=user.id,
        organization_id=user.org_id,
        customizations=request.customizations,
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return {
        "message": "Blueprint deployed successfully",
        "deployment": deployment.to_dict(),
    }


@router.get("/deployments")
async def list_deployments(
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    deployments = service.get_user_deployments(user.id)
    result = []
    for dep in deployments:
        bp = service.get_blueprint(dep.blueprint_id)
        result.append(
            {
                **dep.to_dict(),
                "blueprint_name": bp.name if bp else "Unknown",
                "blueprint_icon": bp.icon if bp else "",
            }
        )
    return {"count": len(result), "deployments": result}


@router.get("/deployments/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    blueprint = service.get_blueprint(deployment.blueprint_id)
    return {
        "deployment": deployment.to_dict(),
        "blueprint": blueprint.to_dict() if blueprint else None,
    }


@router.patch("/deployments/{deployment_id}")
async def update_deployment(
    deployment_id: str,
    request: UpdateDeploymentRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if request.enabled_agents is not None:
        deployment.enabled_agents = request.enabled_agents
    if request.enabled_workflows is not None:
        deployment.enabled_workflows = request.enabled_workflows
    if request.customizations is not None:
        deployment.customizations.update(request.customizations)
    if request.status is not None:
        deployment.status = request.status
    return {"message": "Deployment updated", "deployment": deployment.to_dict()}


@router.get("/{blueprint_id}")
async def get_blueprint(
    blueprint_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BlueprintsService = Depends(get_blueprints_service),
):
    blueprint = service.get_blueprint(blueprint_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return blueprint.to_dict()


__all__ = ["router"]
