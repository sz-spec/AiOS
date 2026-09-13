"""
Wizard API Routes
=================
Project creation wizard endpoint that creates a project
and triggers the multi-agent build pipeline.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service

router = APIRouter(prefix="/api/v1/projects", tags=["Wizard"])


# =============================================================================
# Request/Response Models
# =============================================================================


class WizardRequest(BaseModel):
    category: str
    description: str
    template_id: Optional[str] = None

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Description cannot be empty")
        return v

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str) -> str:
        valid = {"website", "ecommerce", "dashboard", "app", "portfolio", "blog"}
        if v not in valid:
            raise ValueError(f"Invalid category. Must be one of: {', '.join(valid)}")
        return v


class WizardResponse(BaseModel):
    project_id: str
    name: str
    status: str


# =============================================================================
# Category to project name mapping
# =============================================================================

CATEGORY_NAMES = {
    "website": "My Website",
    "ecommerce": "My Store",
    "dashboard": "My Dashboard",
    "app": "My App",
    "portfolio": "My Portfolio",
    "blog": "My Blog",
}


# =============================================================================
# Endpoint
# =============================================================================


@router.post("/wizard", response_model=WizardResponse, status_code=201)
async def create_from_wizard(
    req: WizardRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Create a project from the wizard and start the build pipeline.

    The project is created with status='building'. The frontend subscribes
    to the SSE build stream to track progress. The multi-agent pipeline
    generates files asynchronously.
    """
    service = get_project_service()

    # Generate a default name from category
    name = CATEGORY_NAMES.get(req.category, "My Project")

    user_id = user.id

    project = service.create(
        user_id=user_id,
        name=name,
        description=req.description,
        category=req.category,
        template_id=req.template_id,
    )

    # Trigger async multi-agent pipeline
    from fastapi import BackgroundTasks

    try:
        from ai.agents.multi_agent import MultiAgentBuilder

        background = BackgroundTasks()
        background.add_task(_run_pipeline, project.id, req.description, req.category)
    except ImportError:
        pass  # AI agents not available — SSE endpoint will simulate progress

    return WizardResponse(
        project_id=project.id,
        name=project.name,
        status=project.status,
    )


async def _run_pipeline(project_id: str, description: str, category: str):
    """Background task: run multi-agent build pipeline."""
    try:
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder()
        result = await builder.build(
            project_id=project_id,
            description=description,
            category=category,
        )
        # Update project with generated files
        from services.project_service import get_project_service

        service = get_project_service()
        if result and result.get("files"):
            service.update_files(project_id, result["files"])
            service.update(project_id, status="ready")
        else:
            service.update(project_id, status="error")
    except Exception:
        from services.project_service import get_project_service

        service = get_project_service()
        service.update(project_id, status="error")
