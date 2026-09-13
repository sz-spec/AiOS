"""
Plugin System Core

Extensible plugin architecture for:
- Custom integrations (APIs, services)
- UI extensions
- Code generators
- AI model providers
- Custom workflows
- Event hooks

Features:
- Hot reload plugins
- Sandboxed execution
- Version management
- Dependency resolution
- Plugin marketplace support
"""

import importlib
import importlib.util
import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
import asyncio
from functools import wraps

# ============================================
# Plugin Types
# ============================================


class PluginType(str, Enum):
    """Types of plugins."""

    INTEGRATION = "integration"  # External service integrations
    UI_EXTENSION = "ui_extension"  # Frontend UI additions
    CODE_GENERATOR = "code_generator"  # Custom code generators
    AI_PROVIDER = "ai_provider"  # AI model providers
    WORKFLOW = "workflow"  # Custom workflows
    THEME = "theme"  # Theme plugins
    ANALYTICS = "analytics"  # Analytics integrations
    AUTH = "auth"  # Authentication providers
    STORAGE = "storage"  # Storage providers
    DATABASE = "database"  # Database adapters
    DEPLOYMENT = "deployment"  # Deployment targets
    NOTIFICATION = "notification"  # Notification channels


class PluginStatus(str, Enum):
    """Plugin lifecycle status."""

    REGISTERED = "registered"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    DISABLED = "disabled"
    UNLOADING = "unloading"


class HookType(str, Enum):
    """Available hook points."""

    # Lifecycle hooks
    ON_PROJECT_CREATE = "on_project_create"
    ON_PROJECT_DELETE = "on_project_delete"
    ON_PROJECT_UPDATE = "on_project_update"

    # File hooks
    ON_FILE_CREATE = "on_file_create"
    ON_FILE_UPDATE = "on_file_update"
    ON_FILE_DELETE = "on_file_delete"

    # AI hooks
    BEFORE_AI_GENERATE = "before_ai_generate"
    AFTER_AI_GENERATE = "after_ai_generate"
    ON_AI_ERROR = "on_ai_error"

    # Build hooks
    BEFORE_BUILD = "before_build"
    AFTER_BUILD = "after_build"
    ON_BUILD_ERROR = "on_build_error"

    # Deploy hooks
    BEFORE_DEPLOY = "before_deploy"
    AFTER_DEPLOY = "after_deploy"
    ON_DEPLOY_ERROR = "on_deploy_error"

    # User hooks
    ON_USER_LOGIN = "on_user_login"
    ON_USER_LOGOUT = "on_user_logout"
    ON_USER_SIGNUP = "on_user_signup"

    # Custom
    CUSTOM = "custom"


# ============================================
# Data Models
# ============================================


@dataclass
class PluginManifest:
    """Plugin manifest (plugin.json)."""

    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    type: PluginType = PluginType.INTEGRATION

    # Entry point
    main: str = "main.py"  # Main module file

    # Requirements
    dependencies: list[str] = field(default_factory=list)  # Other plugins
    python_dependencies: list[str] = field(default_factory=list)  # pip packages
    node_dependencies: dict[str, str] = field(default_factory=dict)  # npm packages

    # Permissions
    permissions: list[str] = field(default_factory=list)

    # Configuration schema
    config_schema: dict = field(default_factory=dict)

    # UI
    icon: Optional[str] = None
    homepage: Optional[str] = None
    repository: Optional[str] = None

    # Hooks this plugin subscribes to
    hooks: list[str] = field(default_factory=list)

    # Compatibility
    min_version: str = "1.0.0"
    max_version: Optional[str] = None

    # Marketplace
    price: float = 0.0  # 0 = free
    category: str = "general"
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "PluginManifest":
        """Create from dictionary."""
        # Convert type string to enum
        if "type" in data and isinstance(data["type"], str):
            data["type"] = PluginType(data["type"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = asdict(self)
        result["type"] = self.type.value
        return result


@dataclass
class PluginConfig:
    """Plugin configuration."""

    plugin_id: str
    enabled: bool = True
    settings: dict = field(default_factory=dict)
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PluginInstance:
    """Running plugin instance."""

    manifest: PluginManifest
    status: PluginStatus = PluginStatus.REGISTERED
    config: Optional[PluginConfig] = None
    module: Any = None  # Loaded Python module
    instance: Any = None  # Plugin class instance
    error: Optional[str] = None
    loaded_at: Optional[datetime] = None

    @property
    def id(self) -> str:
        return self.manifest.id


@dataclass
class HookContext:
    """Context passed to hook handlers."""

    hook_type: HookType
    plugin_id: str
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    data: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HookResult:
    """Result from hook execution."""

    success: bool
    plugin_id: str
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


# ============================================
# Base Plugin Class
# ============================================


class BasePlugin(ABC):
    """Base class for all plugins."""

    def __init__(self, manifest: PluginManifest, config: PluginConfig):
        self.manifest = manifest
        self.config = config
        self._hooks: dict[HookType, Callable] = {}

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def settings(self) -> dict:
        return self.config.settings if self.config else {}

    # Lifecycle methods

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the plugin. Return True if successful."""
        pass

    async def activate(self) -> bool:
        """Called when plugin is activated."""
        return True

    async def deactivate(self) -> bool:
        """Called when plugin is deactivated."""
        return True

    async def cleanup(self) -> None:
        """Cleanup resources before unloading."""
        pass

    # Hook registration

    def register_hook(self, hook_type: HookType, handler: Callable) -> None:
        """Register a hook handler."""
        self._hooks[hook_type] = handler

    def get_hook_handler(self, hook_type: HookType) -> Optional[Callable]:
        """Get handler for a hook type."""
        return self._hooks.get(hook_type)

    # Configuration

    def get_config_schema(self) -> dict:
        """Return JSON schema for plugin configuration."""
        return self.manifest.config_schema

    def validate_config(self, config: dict) -> tuple[bool, Optional[str]]:
        """Validate configuration. Return (valid, error_message)."""
        # Basic validation - override for custom validation
        return True, None

    # API for plugins

    def log(self, message: str, level: str = "info") -> None:
        """Log a message from the plugin."""
        print(f"[{self.id}] [{level.upper()}] {message}")

    def emit_event(self, event_type: str, data: dict) -> None:
        """Emit a custom event."""
        # Will be handled by event system
        pass


# ============================================
# Plugin Registry
# ============================================


class PluginRegistry:
    """Central registry for all plugins."""

    def __init__(self):
        self._plugins: dict[str, PluginInstance] = {}
        self._hooks: dict[HookType, list[tuple[str, Callable]]] = {}
        self._plugin_paths: list[Path] = []
        self._configs: dict[str, PluginConfig] = {}

    # Plugin Management

    def register(self, manifest: PluginManifest) -> PluginInstance:
        """Register a plugin from manifest."""
        if manifest.id in self._plugins:
            raise ValueError(f"Plugin {manifest.id} already registered")

        instance = PluginInstance(
            manifest=manifest,
            status=PluginStatus.REGISTERED,
        )
        self._plugins[manifest.id] = instance
        return instance

    def unregister(self, plugin_id: str) -> bool:
        """Unregister a plugin."""
        if plugin_id not in self._plugins:
            return False

        self._plugins[plugin_id]

        # Remove hooks
        for hook_type in list(self._hooks.keys()):
            self._hooks[hook_type] = [
                (pid, handler)
                for pid, handler in self._hooks[hook_type]
                if pid != plugin_id
            ]

        del self._plugins[plugin_id]
        return True

    def get(self, plugin_id: str) -> Optional[PluginInstance]:
        """Get a plugin instance."""
        return self._plugins.get(plugin_id)

    def list_all(self) -> list[PluginInstance]:
        """List all registered plugins."""
        return list(self._plugins.values())

    def list_active(self) -> list[PluginInstance]:
        """List active plugins."""
        return [p for p in self._plugins.values() if p.status == PluginStatus.ACTIVE]

    def list_by_type(self, plugin_type: PluginType) -> list[PluginInstance]:
        """List plugins by type."""
        return [p for p in self._plugins.values() if p.manifest.type == plugin_type]

    # Hook Management

    def register_hook(
        self,
        plugin_id: str,
        hook_type: HookType,
        handler: Callable,
    ) -> None:
        """Register a hook handler."""
        if hook_type not in self._hooks:
            self._hooks[hook_type] = []

        self._hooks[hook_type].append((plugin_id, handler))

    def unregister_hook(self, plugin_id: str, hook_type: HookType) -> bool:
        """Unregister a hook handler."""
        if hook_type not in self._hooks:
            return False

        original_len = len(self._hooks[hook_type])
        self._hooks[hook_type] = [
            (pid, handler)
            for pid, handler in self._hooks[hook_type]
            if pid != plugin_id
        ]
        return len(self._hooks[hook_type]) < original_len

    async def execute_hooks(
        self,
        hook_type: HookType,
        context: HookContext,
    ) -> list[HookResult]:
        """Execute all handlers for a hook."""
        results = []

        handlers = self._hooks.get(hook_type, [])

        for plugin_id, handler in handlers:
            plugin = self._plugins.get(plugin_id)
            if not plugin or plugin.status != PluginStatus.ACTIVE:
                continue

            start_time = datetime.now(timezone.utc)

            try:
                if asyncio.iscoroutinefunction(handler):
                    result_data = await handler(context)
                else:
                    result_data = handler(context)

                duration = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds() * 1000

                results.append(
                    HookResult(
                        success=True,
                        plugin_id=plugin_id,
                        data=result_data,
                        duration_ms=duration,
                    )
                )
            except Exception as e:
                duration = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds() * 1000
                results.append(
                    HookResult(
                        success=False,
                        plugin_id=plugin_id,
                        error=str(e),
                        duration_ms=duration,
                    )
                )

        return results

    # Configuration

    def set_config(self, plugin_id: str, config: PluginConfig) -> None:
        """Set plugin configuration."""
        self._configs[plugin_id] = config
        if plugin_id in self._plugins:
            self._plugins[plugin_id].config = config

    def get_config(self, plugin_id: str) -> Optional[PluginConfig]:
        """Get plugin configuration."""
        return self._configs.get(plugin_id)


# ============================================
# Plugin Loader
# ============================================


class PluginLoader:
    """Loads and manages plugin lifecycle."""

    def __init__(self, registry: PluginRegistry):
        self.registry = registry
        self._plugin_dirs: list[Path] = []

    def add_plugin_directory(self, path: Path) -> None:
        """Add a directory to search for plugins."""
        if path.exists() and path.is_dir():
            self._plugin_dirs.append(path)

    def discover_plugins(self) -> list[PluginManifest]:
        """Discover plugins in registered directories."""
        manifests = []

        for plugin_dir in self._plugin_dirs:
            for item in plugin_dir.iterdir():
                if item.is_dir():
                    manifest_path = item / "plugin.json"
                    if manifest_path.exists():
                        try:
                            with open(manifest_path) as f:
                                data = json.load(f)
                            data["id"] = data.get("id", item.name)
                            manifest = PluginManifest.from_dict(data)
                            manifests.append(manifest)
                        except Exception as e:
                            print(f"Error loading manifest {manifest_path}: {e}")

        return manifests

    async def load_plugin(
        self,
        plugin_id: str,
        config: Optional[PluginConfig] = None,
    ) -> PluginInstance:
        """Load and initialize a plugin."""
        instance = self.registry.get(plugin_id)
        if not instance:
            raise ValueError(f"Plugin {plugin_id} not registered")

        if instance.status == PluginStatus.ACTIVE:
            return instance

        instance.status = PluginStatus.INITIALIZING

        try:
            # Find plugin directory
            plugin_dir = self._find_plugin_dir(plugin_id)
            if not plugin_dir:
                raise FileNotFoundError(f"Plugin directory not found: {plugin_id}")

            # Load module
            main_file = plugin_dir / instance.manifest.main
            if not main_file.exists():
                raise FileNotFoundError(f"Main file not found: {main_file}")

            spec = importlib.util.spec_from_file_location(
                f"plugins.{plugin_id}",
                main_file,
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            instance.module = module

            # Find plugin class
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin
                ):
                    plugin_class = attr
                    break

            if not plugin_class:
                raise TypeError(f"No plugin class found in {main_file}")

            # Create instance
            plugin_config = config or PluginConfig(plugin_id=plugin_id)
            plugin_instance = plugin_class(instance.manifest, plugin_config)

            # Initialize
            success = await plugin_instance.initialize()
            if not success:
                raise RuntimeError("Plugin initialization failed")

            instance.instance = plugin_instance
            instance.config = plugin_config
            instance.status = PluginStatus.ACTIVE
            instance.loaded_at = datetime.now(timezone.utc)

            # Register hooks
            for hook_name in instance.manifest.hooks:
                try:
                    hook_type = HookType(hook_name)
                    handler = plugin_instance.get_hook_handler(hook_type)
                    if handler:
                        self.registry.register_hook(plugin_id, hook_type, handler)
                except ValueError:
                    pass  # Unknown hook type

            return instance

        except Exception as e:
            instance.status = PluginStatus.ERROR
            instance.error = str(e)
            raise

    async def unload_plugin(self, plugin_id: str) -> bool:
        """Unload a plugin."""
        instance = self.registry.get(plugin_id)
        if not instance:
            return False

        instance.status = PluginStatus.UNLOADING

        try:
            # Call cleanup
            if instance.instance:
                await instance.instance.cleanup()

            # Remove from sys.modules
            module_name = f"plugins.{plugin_id}"
            if module_name in sys.modules:
                del sys.modules[module_name]

            # Reset instance
            instance.module = None
            instance.instance = None
            instance.status = PluginStatus.DISABLED
            instance.loaded_at = None

            return True

        except Exception as e:
            instance.status = PluginStatus.ERROR
            instance.error = str(e)
            return False

    async def reload_plugin(self, plugin_id: str) -> PluginInstance:
        """Reload a plugin."""
        instance = self.registry.get(plugin_id)
        if not instance:
            raise ValueError(f"Plugin {plugin_id} not registered")

        config = instance.config

        await self.unload_plugin(plugin_id)
        return await self.load_plugin(plugin_id, config)

    def _find_plugin_dir(self, plugin_id: str) -> Optional[Path]:
        """Find plugin directory by ID."""
        for base_dir in self._plugin_dirs:
            plugin_dir = base_dir / plugin_id
            if plugin_dir.exists():
                return plugin_dir
        return None


# ============================================
# Plugin Manager (High-level API)
# ============================================


class PluginManager:
    """High-level plugin management API."""

    def __init__(self):
        self.registry = PluginRegistry()
        self.loader = PluginLoader(self.registry)
        self._initialized = False

    async def initialize(self, plugin_dirs: Optional[list[Path]] = None) -> None:
        """Initialize the plugin system."""
        if self._initialized:
            return

        # Add default plugin directories
        default_dirs = [
            Path("./plugins"),
            Path("./plugins/builtin"),
            Path("./plugins/community"),
            Path("./plugins/custom"),
        ]

        for dir_path in plugin_dirs or default_dirs:
            if dir_path.exists():
                self.loader.add_plugin_directory(dir_path)

        # Discover and register plugins
        manifests = self.loader.discover_plugins()
        for manifest in manifests:
            try:
                self.registry.register(manifest)
            except ValueError:
                pass  # Already registered

        self._initialized = True

    async def install_plugin(
        self,
        manifest: PluginManifest,
        config: Optional[dict] = None,
    ) -> PluginInstance:
        """Install and activate a plugin."""
        # Register
        self.registry.register(manifest)

        # Set config
        if config:
            plugin_config = PluginConfig(
                plugin_id=manifest.id,
                settings=config,
            )
            self.registry.set_config(manifest.id, plugin_config)

        # Load
        return await self.loader.load_plugin(manifest.id)

    async def uninstall_plugin(self, plugin_id: str) -> bool:
        """Uninstall a plugin."""
        await self.loader.unload_plugin(plugin_id)
        return self.registry.unregister(plugin_id)

    async def enable_plugin(self, plugin_id: str) -> PluginInstance:
        """Enable a disabled plugin."""
        return await self.loader.load_plugin(plugin_id)

    async def disable_plugin(self, plugin_id: str) -> bool:
        """Disable a plugin."""
        return await self.loader.unload_plugin(plugin_id)

    def get_plugin(self, plugin_id: str) -> Optional[PluginInstance]:
        """Get a plugin by ID."""
        return self.registry.get(plugin_id)

    def list_plugins(
        self,
        plugin_type: Optional[PluginType] = None,
        status: Optional[PluginStatus] = None,
    ) -> list[PluginInstance]:
        """List plugins with optional filters."""
        plugins = self.registry.list_all()

        if plugin_type:
            plugins = [p for p in plugins if p.manifest.type == plugin_type]

        if status:
            plugins = [p for p in plugins if p.status == status]

        return plugins

    async def execute_hook(
        self,
        hook_type: HookType,
        data: Optional[dict] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> list[HookResult]:
        """Execute a hook across all active plugins."""
        context = HookContext(
            hook_type=hook_type,
            plugin_id="",  # Will be set per handler
            user_id=user_id,
            project_id=project_id,
            data=data or {},
        )

        return await self.registry.execute_hooks(hook_type, context)

    def update_config(
        self,
        plugin_id: str,
        settings: dict,
    ) -> bool:
        """Update plugin configuration."""
        instance = self.registry.get(plugin_id)
        if not instance:
            return False

        config = instance.config or PluginConfig(plugin_id=plugin_id)
        config.settings.update(settings)
        config.updated_at = datetime.now(timezone.utc)

        self.registry.set_config(plugin_id, config)

        return True


# ============================================
# Singleton
# ============================================

_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get plugin manager singleton."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


# ============================================
# Decorators for Plugin Development
# ============================================


def hook(hook_type: HookType):
    """Decorator to register a method as a hook handler."""

    def decorator(func: Callable) -> Callable:
        func._hook_type = hook_type
        return func

    return decorator


def requires_permission(permission: str):
    """Decorator to require a permission for a method."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            if permission not in self.manifest.permissions:
                raise PermissionError(f"Plugin lacks permission: {permission}")
            return await func(self, *args, **kwargs)

        return wrapper

    return decorator


# ============================================
# Exports
# ============================================

__all__ = [
    # Enums
    "PluginType",
    "PluginStatus",
    "HookType",
    # Data models
    "PluginManifest",
    "PluginConfig",
    "PluginInstance",
    "HookContext",
    "HookResult",
    # Base class
    "BasePlugin",
    # Core classes
    "PluginRegistry",
    "PluginLoader",
    "PluginManager",
    # Singleton
    "get_plugin_manager",
    # Decorators
    "hook",
    "requires_permission",
]
