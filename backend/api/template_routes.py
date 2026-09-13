"""
Template API Routes
====================
API endpoints for template library.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser
from tools.templates import (
    get_template_service,
    TemplateCategory,
    TemplateFramework,
)

router = APIRouter(prefix="/api/templates", tags=["Templates"])


# =============================================================================
# Response Models
# =============================================================================


class TemplatePreview(BaseModel):
    """Template preview (without files)."""

    id: str
    name: str
    description: str
    category: str
    framework: str
    difficulty: str
    thumbnail: str
    tags: List[str]
    features: List[str]
    is_premium: bool
    is_new: bool
    is_popular: bool
    downloads: int


class TemplateDetail(TemplatePreview):
    """Template with files."""

    files: List[dict]
    dependencies: dict
    preview_url: Optional[str]


class CategoryInfo(BaseModel):
    """Category with count."""

    name: str
    count: int
    icon: str


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=List[TemplatePreview])
async def get_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
    framework: Optional[str] = Query(None, description="Filter by framework"),
    search: Optional[str] = Query(None, description="Search query"),
    include_premium: bool = Query(True, description="Include premium templates"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Get all templates with optional filters.
    """
    service = get_template_service()

    if search:
        templates = service.search(search)
    elif category:
        try:
            cat = TemplateCategory(category)
            templates = service.get_by_category(cat)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    else:
        fw = None
        if framework:
            try:
                fw = TemplateFramework(framework)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"Invalid framework: {framework}"
                )

        templates = service.get_all(include_premium=include_premium, framework=fw)

    return [
        TemplatePreview(
            id=t.id,
            name=t.name,
            description=t.description,
            category=t.category.value,
            framework=t.framework.value,
            difficulty=t.difficulty.value,
            thumbnail=t.thumbnail,
            tags=t.tags,
            features=t.features,
            is_premium=t.is_premium,
            is_new=t.is_new,
            is_popular=t.is_popular,
            downloads=t.downloads,
        )
        for t in templates
    ]


@router.get("/categories", response_model=List[CategoryInfo])
async def get_categories(user: AuthenticatedUser = Depends(get_current_user)):
    """
    Get all template categories with counts.
    """
    service = get_template_service()
    return service.get_categories()


@router.get("/popular", response_model=List[TemplatePreview])
async def get_popular_templates(
    limit: int = Query(6, ge=1, le=20),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Get most popular templates.
    """
    service = get_template_service()
    templates = service.get_popular(limit=limit)

    return [
        TemplatePreview(
            id=t.id,
            name=t.name,
            description=t.description,
            category=t.category.value,
            framework=t.framework.value,
            difficulty=t.difficulty.value,
            thumbnail=t.thumbnail,
            tags=t.tags,
            features=t.features,
            is_premium=t.is_premium,
            is_new=t.is_new,
            is_popular=t.is_popular,
            downloads=t.downloads,
        )
        for t in templates
    ]


@router.get("/new", response_model=List[TemplatePreview])
async def get_new_templates(
    limit: int = Query(6, ge=1, le=20),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Get newest templates.
    """
    service = get_template_service()
    templates = service.get_new(limit=limit)

    return [
        TemplatePreview(
            id=t.id,
            name=t.name,
            description=t.description,
            category=t.category.value,
            framework=t.framework.value,
            difficulty=t.difficulty.value,
            thumbnail=t.thumbnail,
            tags=t.tags,
            features=t.features,
            is_premium=t.is_premium,
            is_new=t.is_new,
            is_popular=t.is_popular,
            downloads=t.downloads,
        )
        for t in templates
    ]


@router.get("/{template_id}", response_model=TemplateDetail)
async def get_template(
    template_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Get template details including files.
    """
    service = get_template_service()
    template = service.get_by_id(template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return TemplateDetail(
        id=template.id,
        name=template.name,
        description=template.description,
        category=template.category.value,
        framework=template.framework.value,
        difficulty=template.difficulty.value,
        thumbnail=template.thumbnail,
        preview_url=template.preview_url,
        tags=template.tags,
        features=template.features,
        is_premium=template.is_premium,
        is_new=template.is_new,
        is_popular=template.is_popular,
        downloads=template.downloads,
        files=[
            {"path": f.path, "content": f.content, "language": f.language}
            for f in template.files
        ],
        dependencies=template.dependencies,
    )


@router.post("/{template_id}/use")
async def use_template(
    template_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Mark template as used (increment downloads).
    Returns template files for project creation.
    """
    service = get_template_service()
    template = service.get_by_id(template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Increment downloads
    await service.increment_downloads(template_id)

    return {
        "template_id": template_id,
        "name": template.name,
        "framework": template.framework.value,
        "files": [
            {"path": f.path, "content": f.content, "language": f.language}
            for f in template.files
        ],
        "dependencies": template.dependencies,
    }
