"""
Plugin API Routes

REST API for plugin management:
- List/search plugins
- Install/uninstall
- Enable/disable
- Configure
- Execute hooks
- Marketplace
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

logger = logging.getLogger(__name__)

from plugins.plugin_system import (
    PluginManager,
    PluginManifest,
    PluginType,
    PluginStatus,
    HookType,
    get_plugin_manager,
)

router = APIRouter(prefix="/plugins", tags=["plugins"])


# ============================================
# Request/Response Models
# ============================================


class PluginManifestRequest(BaseModel):
    """Plugin manifest for installation."""

    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    type: str = "integration"
    main: str = "main.py"
    dependencies: list[str] = []
    python_dependencies: list[str] = []
    permissions: list[str] = []
    config_schema: dict = {}
    hooks: list[str] = []


class PluginConfigRequest(BaseModel):
    """Plugin configuration update."""

    settings: dict = {}
    enabled: bool = True


class ExecuteHookRequest(BaseModel):
    """Execute hook request."""

    hook_type: str
    data: dict = {}
    project_id: Optional[str] = None


class PluginResponse(BaseModel):
    """Plugin info response."""

    id: str
    name: str
    version: str
    description: str
    author: str
    type: str
    status: str
    enabled: bool
    loaded_at: Optional[str]
    error: Optional[str]
    config: Optional[dict]
    permissions: list[str]
    hooks: list[str]


class PluginListResponse(BaseModel):
    """List of plugins."""

    plugins: list[PluginResponse]
    total: int


class HookResultResponse(BaseModel):
    """Hook execution result."""

    plugin_id: str
    success: bool
    data: Optional[dict]
    error: Optional[str]
    duration_ms: float


class ExecuteHookResponse(BaseModel):
    """Execute hook response."""

    hook_type: str
    results: list[HookResultResponse]
    total_duration_ms: float


class MarketplacePlugin(BaseModel):
    """Marketplace plugin info."""

    id: str
    name: str
    version: str
    description: str
    author: str
    type: str
    category: str
    tags: list[str]
    downloads: int
    rating: float
    price: float
    icon: Optional[str]
    homepage: Optional[str]


# ============================================
# Helper Functions
# ============================================


def plugin_to_response(instance) -> PluginResponse:
    """Convert plugin instance to response."""
    return PluginResponse(
        id=instance.manifest.id,
        name=instance.manifest.name,
        version=instance.manifest.version,
        description=instance.manifest.description,
        author=instance.manifest.author,
        type=instance.manifest.type.value,
        status=instance.status.value,
        enabled=instance.status == PluginStatus.ACTIVE,
        loaded_at=instance.loaded_at.isoformat() if instance.loaded_at else None,
        error=instance.error,
        config=instance.config.settings if instance.config else None,
        permissions=instance.manifest.permissions,
        hooks=instance.manifest.hooks,
    )


# ============================================
# Plugin CRUD Endpoints
# ============================================


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    type: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """List all plugins with optional filters."""
    plugin_type = PluginType(type) if type else None
    plugin_status = PluginStatus(status) if status else None

    plugins = manager.list_plugins(
        plugin_type=plugin_type,
        status=plugin_status,
    )

    # Search filter
    if search:
        search_lower = search.lower()
        plugins = [
            p
            for p in plugins
            if search_lower in p.manifest.name.lower()
            or search_lower in p.manifest.description.lower()
        ]

    return PluginListResponse(
        plugins=[plugin_to_response(p) for p in plugins],
        total=len(plugins),
    )


def _get_type_description(plugin_type: PluginType) -> str:
    """Get description for plugin type."""
    descriptions = {
        PluginType.INTEGRATION: "External service integrations (APIs, webhooks)",
        PluginType.UI_EXTENSION: "Frontend UI additions and modifications",
        PluginType.CODE_GENERATOR: "Custom code generation templates",
        PluginType.AI_PROVIDER: "AI model providers and adapters",
        PluginType.WORKFLOW: "Custom workflow automations",
        PluginType.THEME: "Visual theme plugins",
        PluginType.ANALYTICS: "Analytics and tracking integrations",
        PluginType.AUTH: "Authentication providers",
        PluginType.STORAGE: "File storage providers",
        PluginType.DATABASE: "Database adapters",
        PluginType.DEPLOYMENT: "Deployment target providers",
        PluginType.NOTIFICATION: "Notification channel integrations",
    }
    return descriptions.get(plugin_type, "")


# Mock marketplace data
MARKETPLACE_PLUGINS = [
    MarketplacePlugin(
        id="slack-notifications",
        name="Slack Notifications",
        version="1.2.0",
        description="Send build and deploy notifications to Slack channels",
        author="VBuilder Team",
        type="notification",
        category="Communication",
        tags=["slack", "notifications", "webhook"],
        downloads=5420,
        rating=4.8,
        price=0,
        icon="🔔",
        homepage="https://github.com/vbuilder/plugin-slack",
    ),
    MarketplacePlugin(
        id="github-deploy",
        name="GitHub Auto Deploy",
        version="2.0.1",
        description="Automatically deploy to GitHub Pages on build",
        author="VBuilder Team",
        type="deployment",
        category="Deployment",
        tags=["github", "deploy", "ci/cd"],
        downloads=8932,
        rating=4.9,
        price=0,
        icon="🚀",
        homepage="https://github.com/vbuilder/plugin-github-deploy",
    ),
    MarketplacePlugin(
        id="openai-provider",
        name="OpenAI Provider",
        version="1.5.0",
        description="Use OpenAI GPT models for code generation",
        author="VBuilder Team",
        type="ai_provider",
        category="AI",
        tags=["openai", "gpt", "ai"],
        downloads=12450,
        rating=4.7,
        price=0,
        icon="🤖",
        homepage="https://github.com/vbuilder/plugin-openai",
    ),
    MarketplacePlugin(
        id="tailwind-generator",
        name="Tailwind Component Generator",
        version="1.0.0",
        description="Generate Tailwind CSS components from designs",
        author="Community",
        type="code_generator",
        category="Code Generation",
        tags=["tailwind", "css", "components"],
        downloads=3210,
        rating=4.5,
        price=9.99,
        icon="🎨",
        homepage=None,
    ),
    MarketplacePlugin(
        id="vercel-deploy",
        name="Vercel Deployment",
        version="1.3.0",
        description="One-click deployment to Vercel",
        author="VBuilder Team",
        type="deployment",
        category="Deployment",
        tags=["vercel", "deploy", "serverless"],
        downloads=7654,
        rating=4.9,
        price=0,
        icon="▲",
        homepage="https://github.com/vbuilder/plugin-vercel",
    ),
]


# ============================================
# Plugin Types & Categories
# ============================================


@router.get("/types")
async def list_plugin_types(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all plugin types."""
    return {
        "types": [
            {"name": t.value, "description": _get_type_description(t)}
            for t in PluginType
        ]
    }


# ============================================
# Marketplace Endpoints
# ============================================


@router.get("/marketplace", response_model=list[MarketplacePlugin])
async def marketplace_list(
    category: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = Query("downloads", pattern="^(downloads|rating|name)$"),
    free_only: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List marketplace plugins."""
    plugins = MARKETPLACE_PLUGINS.copy()

    if category:
        plugins = [p for p in plugins if p.category.lower() == category.lower()]

    if search:
        search_lower = search.lower()
        plugins = [
            p
            for p in plugins
            if search_lower in p.name.lower()
            or search_lower in p.description.lower()
            or any(search_lower in tag for tag in p.tags)
        ]

    if free_only:
        plugins = [p for p in plugins if p.price == 0]

    if sort == "downloads":
        plugins.sort(key=lambda p: p.downloads, reverse=True)
    elif sort == "rating":
        plugins.sort(key=lambda p: p.rating, reverse=True)
    elif sort == "name":
        plugins.sort(key=lambda p: p.name)

    return plugins


@router.get("/marketplace/{plugin_id}", response_model=MarketplacePlugin)
async def marketplace_get(
    plugin_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get marketplace plugin details."""
    for plugin in MARKETPLACE_PLUGINS:
        if plugin.id == plugin_id:
            return plugin
    raise HTTPException(status_code=404, detail="Plugin not found in marketplace")


@router.post("/marketplace/{plugin_id}/install", response_model=PluginResponse)
async def marketplace_install(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Install a plugin from marketplace."""
    marketplace_plugin = None
    for p in MARKETPLACE_PLUGINS:
        if p.id == plugin_id:
            marketplace_plugin = p
            break

    if not marketplace_plugin:
        raise HTTPException(status_code=404, detail="Plugin not found in marketplace")

    manifest = PluginManifest(
        id=marketplace_plugin.id,
        name=marketplace_plugin.name,
        version=marketplace_plugin.version,
        description=marketplace_plugin.description,
        author=marketplace_plugin.author,
        type=PluginType(marketplace_plugin.type),
        category=marketplace_plugin.category,
        tags=marketplace_plugin.tags,
        price=marketplace_plugin.price,
    )

    try:
        instance = await manager.install_plugin(manifest)
        return plugin_to_response(instance)
    except Exception as e:
        logger.error("Marketplace plugin install failed: %s", e)
        raise HTTPException(status_code=500, detail="Plugin installation failed")


@router.get("/{plugin_id}", response_model=PluginResponse)
async def get_plugin(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Get a specific plugin."""
    instance = manager.get_plugin(plugin_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Plugin not found")

    return plugin_to_response(instance)


@router.post("", response_model=PluginResponse)
async def install_plugin(
    request: PluginManifestRequest,
    config: Optional[dict] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Install a new plugin."""
    try:
        manifest = PluginManifest(
            id=request.id,
            name=request.name,
            version=request.version,
            description=request.description,
            author=request.author,
            type=PluginType(request.type),
            main=request.main,
            dependencies=request.dependencies,
            python_dependencies=request.python_dependencies,
            permissions=request.permissions,
            config_schema=request.config_schema,
            hooks=request.hooks,
        )

        instance = await manager.install_plugin(manifest, config)
        return plugin_to_response(instance)

    except ValueError as e:
        logger.error("Plugin install validation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid plugin configuration")
    except Exception as e:
        logger.error("Plugin install failed: %s", e)
        raise HTTPException(status_code=500, detail="Plugin installation failed")


@router.delete("/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Uninstall a plugin."""
    success = await manager.uninstall_plugin(plugin_id)
    if not success:
        raise HTTPException(status_code=404, detail="Plugin not found")

    return {"status": "uninstalled", "plugin_id": plugin_id}


# ============================================
# Plugin Lifecycle Endpoints
# ============================================


@router.post("/{plugin_id}/enable", response_model=PluginResponse)
async def enable_plugin(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Enable a disabled plugin."""
    try:
        instance = await manager.enable_plugin(plugin_id)
        return plugin_to_response(instance)
    except ValueError as e:
        logger.error("Plugin enable failed — not found: %s", e)
        raise HTTPException(status_code=404, detail="Plugin not found")
    except Exception as e:
        logger.error("Plugin enable failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to enable plugin")


@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Disable an active plugin."""
    success = await manager.disable_plugin(plugin_id)
    if not success:
        raise HTTPException(status_code=404, detail="Plugin not found")

    return {"status": "disabled", "plugin_id": plugin_id}


@router.post("/{plugin_id}/reload", response_model=PluginResponse)
async def reload_plugin(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Reload a plugin (hot reload)."""
    try:
        instance = await manager.loader.reload_plugin(plugin_id)
        return plugin_to_response(instance)
    except ValueError as e:
        logger.error("Plugin reload failed — not found: %s", e)
        raise HTTPException(status_code=404, detail="Plugin not found")
    except Exception as e:
        logger.error("Plugin reload failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to reload plugin")


# ============================================
# Plugin Configuration Endpoints
# ============================================


@router.get("/{plugin_id}/config")
async def get_plugin_config(
    plugin_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Get plugin configuration."""
    instance = manager.get_plugin(plugin_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Plugin not found")

    return {
        "plugin_id": plugin_id,
        "settings": instance.config.settings if instance.config else {},
        "schema": instance.manifest.config_schema,
        "updated_at": (
            instance.config.updated_at.isoformat() if instance.config else None
        ),
    }


@router.put("/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    request: PluginConfigRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Update plugin configuration."""
    success = manager.update_config(plugin_id, request.settings)
    if not success:
        raise HTTPException(status_code=404, detail="Plugin not found")

    return {
        "status": "updated",
        "plugin_id": plugin_id,
        "settings": request.settings,
    }


# ============================================
# Hook Endpoints
# ============================================


@router.post("/hooks/execute", response_model=ExecuteHookResponse)
async def execute_hook(
    request: ExecuteHookRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    manager: PluginManager = Depends(get_plugin_manager),
):
    """Execute a hook across all active plugins."""
    try:
        hook_type = HookType(request.hook_type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid hook type: {request.hook_type}"
        )

    start_time = datetime.now(timezone.utc)

    results = await manager.execute_hook(
        hook_type=hook_type,
        data=request.data,
        user_id=user.id,
        project_id=request.project_id,
    )

    total_duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

    return ExecuteHookResponse(
        hook_type=request.hook_type,
        results=[
            HookResultResponse(
                plugin_id=r.plugin_id,
                success=r.success,
                data=r.data if isinstance(r.data, dict) else {"result": r.data},
                error=r.error,
                duration_ms=r.duration_ms,
            )
            for r in results
        ],
        total_duration_ms=total_duration,
    )


@router.get("/hooks/types")
async def list_hook_types(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all available hook types."""
    return {
        "hook_types": [
            {
                "name": hook.value,
                "category": hook.value.split("_")[0],
            }
            for hook in HookType
        ]
    }


__all__ = ["router"]
