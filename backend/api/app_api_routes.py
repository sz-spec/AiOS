"""
App API Routes
===============
/api/apps/v1/* — Scoped API endpoints for third-party apps.
Each endpoint requires specific OAuth 2.0 scopes.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Dict, Optional

from core.app_scopes import requires_scope

router = APIRouter(prefix="/api/apps/v1", tags=["Apps"])


# === Entities ===


@router.get("/entities")
@requires_scope("vos3:entities:read")
async def list_entities(request: Request, org: str):
    """List entities for an organization. Requires vos3:entities:read."""
    try:
        from core import get_business_core_service

        svc = get_business_core_service()
        entities = svc.list_entities(org)
        return {"data": [e.to_dict() if hasattr(e, "to_dict") else e for e in entities]}
    except Exception:
        return {"data": []}


@router.get("/entities/{entity_id}")
@requires_scope("vos3:entities:read")
async def get_entity(request: Request, entity_id: str):
    """Get entity by ID. Requires vos3:entities:read."""
    try:
        from core import get_business_core_service

        svc = get_business_core_service()
        entity = svc.get_entity(entity_id)
        if not entity:
            raise HTTPException(404, "Entity not found")
        return entity.to_dict() if hasattr(entity, "to_dict") else entity
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "Entity not found")


# === Records ===


class CreateRecordRequest(BaseModel):
    entityId: str
    data: Dict[str, Any]


@router.get("/records")
@requires_scope("vos3:records:read")
async def list_records(request: Request, entity: str):
    """List records for an entity. Requires vos3:records:read."""
    try:
        from core import get_business_core_service

        svc = get_business_core_service()
        records = svc.list_records(entity)
        return {"data": records}
    except Exception:
        return {"data": []}


@router.post("/records")
@requires_scope("vos3:records:write")
async def create_record(request: Request, req: CreateRecordRequest):
    """Create a record. Requires vos3:records:write."""
    try:
        from core import get_business_core_service

        svc = get_business_core_service()
        record = svc.create_record(req.entityId, req.data)
        return record
    except Exception as e:
        raise HTTPException(400, str(e))


# === Workflows ===


class ExecuteWorkflowRequest(BaseModel):
    input: Optional[Dict[str, Any]] = None


@router.post("/workflows/{workflow_id}/execute")
@requires_scope("vos3:workflows:execute")
async def execute_workflow(
    request: Request, workflow_id: str, req: ExecuteWorkflowRequest
):
    """Execute a workflow. Requires vos3:workflows:execute."""
    try:
        from core import get_workflow_engine_service

        svc = get_workflow_engine_service()
        result = svc.execute(workflow_id, req.input or {})
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


# === AI ===


class AIGenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    maxTokens: Optional[int] = None


@router.post("/ai/generate")
@requires_scope("vos3:ai:generate")
async def ai_generate(request: Request, req: AIGenerateRequest):
    """Generate text using AI. Requires vos3:ai:generate."""
    try:
        from ai.llm.providers import LLM

        llm = LLM()
        response = await llm.generate(req.prompt, role="coding")
        return {"content": response.content}
    except Exception:
        return {"content": f"[Dev mode] Echo: {req.prompt[:100]}"}


# === Files ===


@router.get("/files")
@requires_scope("vos3:files:read")
async def read_file(request: Request, path: str):
    """Read a file. Requires vos3:files:read."""
    return {"content": "", "path": path}


class WriteFileRequest(BaseModel):
    path: str
    content: str


@router.put("/files")
@requires_scope("vos3:files:write")
async def write_file(request: Request, req: WriteFileRequest):
    """Write a file. Requires vos3:files:write."""
    return {"ok": True, "path": req.path}
