"""
VOS3 Native Deploy Routes
==========================
Phase 4.0: Deploy, status, stop, and log streaming for VOS3 kernel apps.
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser
from middleware.ownership import require_ownership

from services.native_deploy_service import get_native_deploy_service
from services.vpacker import VPKManifest

router = APIRouter(prefix="/api/v1/deploy/native", tags=["Native Deploy"])


class NativeDeployRequest(BaseModel):
    project_id: str
    app_name: Optional[str] = None
    manifest_override: Optional[dict] = None


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/{project_id}")
@require_ownership("project", id_param="project_id")
async def deploy_native(
    project_id: str,
    req: Optional[NativeDeployRequest] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Deploy a built project to VOS3 kernel. Returns SSE stream.

    v21.3.1 (Track A): @require_ownership decorator applied. Project
    owner resolver must be registered at startup (backend/startup.py)
    before this endpoint can be invoked under non-dev auth.
    """
    from services.project_service import get_project_service

    proj_service = get_project_service()
    project = proj_service.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.files:
        raise HTTPException(status_code=400, detail="Project has no files to deploy")

    # Get VPK manifest from project or build one
    manifest_dict = getattr(project, "vpk_manifest", None)
    if req and req.manifest_override:
        manifest_dict = req.manifest_override
    if manifest_dict:
        manifest = VPKManifest(**manifest_dict)
    else:
        from services.vpacker import VPKSystemManifest, VPKIntentManifest
        import re

        name = req.app_name if req and req.app_name else project_id[:32]
        safe_name = re.sub(r"[^a-z0-9_-]", "-", name.lower())[:64] or "app"
        manifest = VPKManifest(
            system=VPKSystemManifest(
                name=safe_name,
                version="1.0.0",
                entry=f"/disk/apps/{safe_name}/index.js",
            ),
            intent=VPKIntentManifest(),
        )

    app_id = manifest.system.name
    deploy_service = get_native_deploy_service()

    async def event_stream():
        async for event in deploy_service.deploy(app_id, manifest, project.files):
            yield _sse_event(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{app_id}/status")
async def app_status(app_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    """Get running app status from kernel via APPSTAT."""
    deploy_service = get_native_deploy_service()
    return await deploy_service.get_status(app_id)


@router.get("/{app_id}/logs")
async def app_logs(app_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    """Stream app logs via APPLOGS. Returns SSE stream."""
    deploy_service = get_native_deploy_service()

    async def event_stream():
        async for event in deploy_service.get_logs(app_id):
            yield _sse_event(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{app_id}/stop")
async def stop_app(app_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    """Stop app via APPKILL. Retention policy determines inference_memory fate."""
    deploy_service = get_native_deploy_service()
    return await deploy_service.stop_app(app_id)


@router.get("/apps")
async def list_apps(user: AuthenticatedUser = Depends(get_current_user)):
    """List all running apps on VOS3 kernel."""
    deploy_service = get_native_deploy_service()
    return {"apps": await deploy_service.list_apps()}
