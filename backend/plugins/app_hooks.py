"""
App Lifecycle Hooks
====================
pre_install, post_install, pre_uninstall, post_uninstall, on_update hooks
for app lifecycle management.
"""

from typing import Any, Callable, Dict, List, Optional
import asyncio


class AppHookType:
    PRE_INSTALL = "pre_install"
    POST_INSTALL = "post_install"
    PRE_UNINSTALL = "pre_uninstall"
    POST_UNINSTALL = "post_uninstall"
    ON_UPDATE = "on_update"
    ON_ENABLE = "on_enable"
    ON_DISABLE = "on_disable"


class AppHookManager:
    """Manages lifecycle hooks for app installations."""

    def __init__(self):
        self._hooks: Dict[str, List[Callable]] = {}

    def register(self, hook_type: str, handler: Callable):
        """Register a hook handler."""
        if hook_type not in self._hooks:
            self._hooks[hook_type] = []
        self._hooks[hook_type].append(handler)

    async def execute(self, hook_type: str, context: Dict[str, Any]) -> List[Dict]:
        """Execute all handlers for a hook type."""
        handlers = self._hooks.get(hook_type, [])
        results = []
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(context)
                else:
                    result = handler(context)
                results.append({"success": True, "data": result})
            except Exception as e:
                results.append({"success": False, "error": str(e)})
        return results


_hook_manager: Optional[AppHookManager] = None


def get_app_hook_manager() -> AppHookManager:
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = AppHookManager()
    return _hook_manager
