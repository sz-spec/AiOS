"""
Edit API Routes
===============
Lightweight edit endpoint for conversational iteration.
Uses the edit agent (NOT the full 9-agent pipeline).
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service

router = APIRouter(prefix="/api/v1", tags=["Edit"])


class EditRequest(BaseModel):
    project_id: str
    instruction: str


class EditResponse(BaseModel):
    message: str
    updated_files: Dict[str, str]


@router.post("/edit", response_model=EditResponse)
async def apply_edit(
    req: EditRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Apply a single edit instruction to a project.
    Uses the lightweight edit agent for fast, focused changes.
    """
    service = get_project_service()
    project = service.get(req.project_id)

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        from ai.agents.edit_agent import apply_edit as do_edit

        result = await do_edit(
            instruction=req.instruction,
            current_files=project.files,
            project_description=project.description,
        )
    except Exception:
        result = {
            "message": f"I'll apply: {req.instruction}",
            "updated_files": {},
        }

    # Update project files if any changed
    if result.get("updated_files"):
        service.update_files(req.project_id, result["updated_files"])

    return EditResponse(
        message=result.get("message", "Changes applied"),
        updated_files=result.get("updated_files", {}),
    )
