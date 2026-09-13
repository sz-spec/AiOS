"""
Design System API Routes

REST API for:
- Design system tokens
- CSS generation
- Tailwind config
- Figma import/export
- Component definitions
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser
from design_system.design_system_service import (
    DesignSystemService,
    ColorMode,
    get_design_system_service,
)

router = APIRouter(prefix="/design-system", tags=["design-system"])


class FigmaImportRequest(BaseModel):
    tokens: dict


class GenerateCSSRequest(BaseModel):
    mode: str = "light"  # light, dark, system


@router.get("")
async def get_design_system(
    system_id: Optional[str] = None,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get complete design system."""
    system = service.get_system(system_id)
    if not system:
        raise HTTPException(status_code=404, detail="Design system not found")
    return system.to_dict()


@router.get("/tokens")
async def get_tokens(
    system_id: Optional[str] = None,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get design tokens only."""
    system = service.get_system(system_id)
    if not system:
        raise HTTPException(status_code=404, detail="Design system not found")
    return {
        "colors": [c.to_dict() for c in system.colors],
        "typography": [t.to_dict() for t in system.typography],
        "spacing": [s.to_dict() for s in system.spacing],
        "shadows": [s.to_dict() for s in system.shadows],
        "radii": [r.to_dict() for r in system.radii],
        "animations": [a.to_dict() for a in system.animations],
    }


@router.get("/components")
async def get_components(
    category: Optional[str] = None,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get component definitions."""
    system = service.get_active_system()
    components = system.components
    if category:
        components = [c for c in components if c.category == category]
    return {"components": [c.to_dict() for c in components]}


@router.get("/components/{component_id}")
async def get_component(
    component_id: str,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get specific component definition."""
    system = service.get_active_system()
    for comp in system.components:
        if comp.id == component_id:
            return comp.to_dict()
    raise HTTPException(status_code=404, detail="Component not found")


@router.post("/css")
async def generate_css(
    request: GenerateCSSRequest,
    system_id: Optional[str] = None,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Generate CSS custom properties."""
    mode = ColorMode(request.mode)
    css = service.generate_css_variables(system_id, mode)
    return PlainTextResponse(css, media_type="text/css")


@router.get("/css", response_class=PlainTextResponse)
async def get_css(
    mode: str = "system",
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get CSS custom properties."""
    color_mode = ColorMode(mode)
    return service.generate_css_variables(mode=color_mode)


@router.get("/tailwind")
async def get_tailwind_config(
    system_id: Optional[str] = None,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Generate Tailwind CSS config extension."""
    return service.generate_tailwind_config(system_id)


@router.post("/figma/import")
async def import_figma_tokens(
    request: FigmaImportRequest,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Import tokens from Figma."""
    tokens = service.parse_figma_tokens(request.tokens)
    return {"message": "Tokens imported", "tokens": tokens}


@router.get("/figma/export")
async def export_figma_tokens(
    system_id: Optional[str] = None,
    service: DesignSystemService = Depends(get_design_system_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Export design system to Figma tokens format."""
    return service.export_to_figma_tokens(system_id)


@router.get("/categories")
async def get_component_categories(user: AuthenticatedUser = Depends(get_current_user)):
    """Get component categories."""
    return {
        "categories": [
            {"id": "button", "name": "Buttons", "icon": "👆"},
            {"id": "input", "name": "Inputs", "icon": "📝"},
            {"id": "card", "name": "Cards", "icon": "🃏"},
            {"id": "feedback", "name": "Feedback", "icon": "💬"},
            {"id": "navigation", "name": "Navigation", "icon": "🧭"},
            {"id": "layout", "name": "Layout", "icon": "📐"},
        ]
    }


__all__ = ["router"]
