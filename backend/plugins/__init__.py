"""
Plugin System Module

Extensible plugin architecture for custom integrations,
AI providers, code generators, and more.
"""

from plugins.plugin_system import (
    # Enums
    PluginType,
    PluginStatus,
    HookType,
    # Data models
    PluginManifest,
    PluginConfig,
    PluginInstance,
    HookContext,
    HookResult,
    # Base class
    BasePlugin,
    # Core classes
    PluginRegistry,
    PluginLoader,
    PluginManager,
    # Singleton
    get_plugin_manager,
    # Decorators
    hook,
    requires_permission,
)

from plugins.builtin_plugins import (
    SlackNotificationPlugin,
    GitHubDeployPlugin,
    OpenAIProviderPlugin,
    WebhookPlugin,
    BUILTIN_MANIFESTS,
    PLUGIN_CLASSES,
)

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
    # Built-in plugins
    "SlackNotificationPlugin",
    "GitHubDeployPlugin",
    "OpenAIProviderPlugin",
    "WebhookPlugin",
    "BUILTIN_MANIFESTS",
    "PLUGIN_CLASSES",
]
