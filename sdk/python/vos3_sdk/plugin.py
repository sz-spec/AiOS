"""VOS3 Plugin — Python base class for backend app extensions.

Extends the existing BasePlugin from backend/plugins/plugin_system.py
with app platform features: OAuth scopes, stateless pattern, API client.
"""

from typing import Any, Dict, List, Optional


class VOS3Plugin:
    """Base class for VOS3 platform plugins.

    Follows the stateless app pattern (Shopify model):
    apps don't store state internally, derive context from API sessions.

    Usage:
        class MyPlugin(VOS3Plugin):
            name = "my-plugin"
            scopes = ["vos3:records:read", "vos3:records:write"]

            async def on_install(self, context):
                # Called when the app is installed
                pass

            async def on_hook(self, hook_name, data):
                # Called for registered hooks
                pass
    """

    name: str = ""
    version: str = "0.1.0"
    scopes: List[str] = []
    description: str = ""
    category: str = "general"

    def __init__(self, app_id: str, api_key: str, base_url: str = "https://api.vos3.app"):
        self.app_id = app_id
        self._api_key = api_key
        self._base_url = base_url
        self._client: Optional[Any] = None

    @property
    def client(self):
        """Lazy-initialized API client."""
        if self._client is None:
            from .client import VOS3APIClient
            self._client = VOS3APIClient(
                app_id=self.app_id,
                api_key=self._api_key,
                base_url=self._base_url,
                version=self.version,
            )
        return self._client

    # --- Lifecycle hooks (override in subclass) ---

    async def on_install(self, context: Dict) -> None:
        """Called when the app is installed in an organization."""
        pass

    async def on_uninstall(self, context: Dict) -> None:
        """Called when the app is uninstalled."""
        pass

    async def on_enable(self, context: Dict) -> None:
        """Called when the app is enabled after being disabled."""
        pass

    async def on_disable(self, context: Dict) -> None:
        """Called when the app is disabled."""
        pass

    async def on_hook(self, hook_name: str, data: Dict) -> Optional[Dict]:
        """Called for registered event hooks. Return data to modify the event."""
        return None

    # --- Utility ---

    def get_manifest(self) -> Dict:
        """Generate app manifest from class attributes."""
        return {
            "id": self.app_id,
            "name": self.name or self.app_id,
            "version": self.version,
            "description": self.description,
            "scopes": self.scopes,
            "category": self.category,
            "type": "plugin",
        }
