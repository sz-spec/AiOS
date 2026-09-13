"""
Theme API Routes

Endpoints for theme management:
- List themes (with filters)
- Get theme details
- Create custom theme
- Update theme
- Delete theme
- Duplicate theme
- User preferences
- Export/Import
- Generate CSS
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from api.deps import get_current_user, AuthenticatedUser

from tools.themes import (
    Theme,
    ThemeMode,
    ThemeCategory,
    ThemeService,
    get_theme_service,
)

router = APIRouter(prefix="/themes", tags=["themes"])


# ============================================
# Request/Response Models
# ============================================


class ColorPaletteUpdate(BaseModel):
    """Color palette update."""

    primary: Optional[str] = None
    primary_foreground: Optional[str] = None
    primary_hover: Optional[str] = None
    secondary: Optional[str] = None
    accent: Optional[str] = None
    background: Optional[str] = None
    background_secondary: Optional[str] = None
    foreground: Optional[str] = None
    foreground_secondary: Optional[str] = None
    border: Optional[str] = None
    success: Optional[str] = None
    warning: Optional[str] = None
    error: Optional[str] = None
    info: Optional[str] = None
    editor_background: Optional[str] = None
    editor_foreground: Optional[str] = None
    syntax_keyword: Optional[str] = None
    syntax_string: Optional[str] = None
    syntax_comment: Optional[str] = None
    syntax_function: Optional[str] = None


class TypographyUpdate(BaseModel):
    """Typography update."""

    font_sans: Optional[str] = None
    font_mono: Optional[str] = None
    font_heading: Optional[str] = None
    font_size_base: Optional[str] = None


class CreateThemeRequest(BaseModel):
    """Create theme request."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    mode: str = "light"
    base_theme_id: Optional[str] = None
    colors: Optional[dict] = None
    typography: Optional[dict] = None
    is_public: bool = False
    tags: list[str] = []


class UpdateThemeRequest(BaseModel):
    """Update theme request."""

    name: Optional[str] = None
    description: Optional[str] = None
    colors: Optional[dict] = None
    typography: Optional[dict] = None
    is_public: Optional[bool] = None
    tags: Optional[list[str]] = None


class ImportThemeRequest(BaseModel):
    """Import theme request."""

    json_data: str


class SetUserThemeRequest(BaseModel):
    """Set user theme request."""

    theme_id: str


class ThemeResponse(BaseModel):
    """Theme response."""

    id: str
    name: str
    description: str
    mode: str
    category: str
    colors: dict
    typography: dict
    author_id: Optional[str]
    author_name: Optional[str]
    organization_id: Optional[str]
    is_public: bool
    downloads: int
    created_at: str
    updated_at: str
    preview_url: Optional[str]
    tags: list[str]

    @classmethod
    def from_theme(cls, theme: Theme) -> "ThemeResponse":
        """Create from Theme."""
        data = theme.to_dict()
        return cls(**data)


class ThemeListResponse(BaseModel):
    """Theme list response."""

    themes: list[ThemeResponse]
    total: int


class CSSResponse(BaseModel):
    """CSS response."""

    css: str
    theme_ids: list[str]


# ============================================
# Theme Endpoints
# ============================================


@router.get("", response_model=ThemeListResponse)
async def list_themes(
    category: Optional[str] = None,
    mode: Optional[str] = None,
    search: Optional[str] = None,
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """List available themes."""
    themes = service.list_themes(
        category=ThemeCategory(category) if category else None,
        mode=ThemeMode(mode) if mode else None,
        search=search,
        tags=tags.split(",") if tags else None,
        user_id=user.id,
        organization_id=user.org_id,
    )

    return ThemeListResponse(
        themes=[ThemeResponse.from_theme(t) for t in themes],
        total=len(themes),
    )


@router.get("/presets", response_model=ThemeListResponse)
async def list_preset_themes(
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """List official preset themes."""
    themes = service.list_themes(category=ThemeCategory.OFFICIAL)
    return ThemeListResponse(
        themes=[ThemeResponse.from_theme(t) for t in themes],
        total=len(themes),
    )


@router.get("/{theme_id}", response_model=ThemeResponse)
async def get_theme(
    theme_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Get theme by ID."""
    theme = service.get_theme(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    return ThemeResponse.from_theme(theme)


@router.post("", response_model=ThemeResponse)
async def create_theme(
    request: CreateThemeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Create a custom theme."""
    theme = service.create_theme(
        name=request.name,
        description=request.description,
        mode=ThemeMode(request.mode),
        base_theme_id=request.base_theme_id,
        colors=request.colors,
        typography=request.typography,
        user_id=user.id,
        user_name=user.email,
        organization_id=user.org_id,
        is_public=request.is_public,
        tags=request.tags,
    )
    return ThemeResponse.from_theme(theme)


@router.patch("/{theme_id}", response_model=ThemeResponse)
async def update_theme(
    theme_id: str,
    request: UpdateThemeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Update a theme."""
    updates = request.dict(exclude_none=True)
    theme = service.update_theme(theme_id, user.id, updates)

    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found or access denied")

    return ThemeResponse.from_theme(theme)


@router.delete("/{theme_id}")
async def delete_theme(
    theme_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Delete a theme."""
    success = service.delete_theme(theme_id, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Theme not found or access denied")
    return {"status": "deleted", "theme_id": theme_id}


@router.post("/{theme_id}/duplicate", response_model=ThemeResponse)
async def duplicate_theme(
    theme_id: str,
    new_name: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Duplicate a theme."""
    theme = service.duplicate_theme(
        theme_id=theme_id,
        user_id=user.id,
        user_name=user.email,
        new_name=new_name,
    )

    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")

    return ThemeResponse.from_theme(theme)


# ============================================
# User Preferences
# ============================================


@router.get("/user/current", response_model=ThemeResponse)
async def get_user_theme(
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Get user's current theme."""
    theme = service.get_user_theme(user.id)
    return ThemeResponse.from_theme(theme)


@router.post("/user/current")
async def set_user_theme(
    request: SetUserThemeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Set user's theme."""
    success = service.set_user_theme(user.id, request.theme_id)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid theme ID")
    return {"status": "updated", "theme_id": request.theme_id}


# ============================================
# Export/Import
# ============================================


@router.get("/{theme_id}/export")
async def export_theme(
    theme_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Export theme as JSON."""
    json_data = service.export_theme(theme_id)
    if not json_data:
        raise HTTPException(status_code=404, detail="Theme not found")

    return {
        "theme_id": theme_id,
        "json": json_data,
    }


@router.post("/import", response_model=ThemeResponse)
async def import_theme(
    request: ImportThemeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Import theme from JSON."""
    theme = service.import_theme(
        json_data=request.json_data,
        user_id=user.id,
        user_name=user.email,
    )

    if not theme:
        raise HTTPException(status_code=400, detail="Invalid theme data")

    return ThemeResponse.from_theme(theme)


# ============================================
# CSS Generation
# ============================================


@router.get("/css/all", response_model=CSSResponse)
async def get_all_css(
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Get CSS for all themes."""
    themes = service.list_themes()
    css = service.generate_css()

    return CSSResponse(
        css=css,
        theme_ids=[t.id for t in themes],
    )


@router.get("/css/user", response_model=CSSResponse)
async def get_user_css(
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Get CSS for user's active theme."""
    theme = service.get_user_theme(user.id)
    css = theme.to_css()

    return CSSResponse(
        css=css,
        theme_ids=[theme.id],
    )


@router.get("/{theme_id}/css")
async def get_theme_css(
    theme_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ThemeService = Depends(get_theme_service),
):
    """Get CSS for specific theme."""
    theme = service.get_theme(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")

    return {
        "theme_id": theme_id,
        "css": theme.to_css(),
    }


# ============================================
# Preview Colors
# ============================================


@router.post("/preview/css")
async def preview_theme_css(
    colors: dict,
    typography: Optional[dict] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Generate preview CSS from colors without saving."""
    from tools.themes import ColorPalette, Typography, Theme

    preview_theme = Theme(
        id="preview",
        name="Preview",
        colors=ColorPalette(**colors),
        typography=Typography(**(typography or {})),
    )

    return {
        "css": preview_theme.to_css(),
    }


__all__ = ["router"]
