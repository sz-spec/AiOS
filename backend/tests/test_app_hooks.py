"""Tests for plugins/app_hooks.py — App lifecycle hooks."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.app_hooks import AppHookManager, AppHookType, get_app_hook_manager


class TestAppHookManager:
    def setup_method(self):
        self.mgr = AppHookManager()

    @pytest.mark.asyncio
    async def test_register_and_execute_sync_hook(self):
        handler = MagicMock(return_value={"status": "ok"})
        self.mgr.register(AppHookType.PRE_INSTALL, handler)
        results = await self.mgr.execute(AppHookType.PRE_INSTALL, {"app_id": "app1"})
        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["data"] == {"status": "ok"}
        handler.assert_called_once_with({"app_id": "app1"})

    @pytest.mark.asyncio
    async def test_register_and_execute_async_hook(self):
        handler = AsyncMock(return_value={"async": True})
        self.mgr.register(AppHookType.POST_INSTALL, handler)
        results = await self.mgr.execute(AppHookType.POST_INSTALL, {"app_id": "app1"})
        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["data"] == {"async": True}

    @pytest.mark.asyncio
    async def test_pre_uninstall_hook(self):
        handler = MagicMock(return_value=None)
        self.mgr.register(AppHookType.PRE_UNINSTALL, handler)
        results = await self.mgr.execute(AppHookType.PRE_UNINSTALL, {"app_id": "app1"})
        assert results[0]["success"] is True

    @pytest.mark.asyncio
    async def test_on_update_hook(self):
        handler = MagicMock(return_value="updated")
        self.mgr.register(AppHookType.ON_UPDATE, handler)
        results = await self.mgr.execute(AppHookType.ON_UPDATE, {"version": "2.0"})
        assert results[0]["data"] == "updated"

    @pytest.mark.asyncio
    async def test_hook_error_doesnt_crash(self):
        handler = MagicMock(side_effect=RuntimeError("plugin crashed"))
        self.mgr.register(AppHookType.PRE_INSTALL, handler)
        results = await self.mgr.execute(AppHookType.PRE_INSTALL, {"app_id": "app1"})
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "plugin crashed" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_multiple_hooks_same_type(self):
        h1 = MagicMock(return_value="first")
        h2 = MagicMock(return_value="second")
        self.mgr.register(AppHookType.POST_INSTALL, h1)
        self.mgr.register(AppHookType.POST_INSTALL, h2)
        results = await self.mgr.execute(AppHookType.POST_INSTALL, {})
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_no_handlers_returns_empty(self):
        results = await self.mgr.execute(AppHookType.ON_ENABLE, {})
        assert results == []


class TestAppHookType:
    def test_hook_types_defined(self):
        assert AppHookType.PRE_INSTALL == "pre_install"
        assert AppHookType.POST_INSTALL == "post_install"
        assert AppHookType.PRE_UNINSTALL == "pre_uninstall"
        assert AppHookType.POST_UNINSTALL == "post_uninstall"
        assert AppHookType.ON_UPDATE == "on_update"
        assert AppHookType.ON_ENABLE == "on_enable"
        assert AppHookType.ON_DISABLE == "on_disable"


class TestGetAppHookManager:
    def test_singleton(self):
        import plugins.app_hooks as mod

        mod._hook_manager = None
        m1 = get_app_hook_manager()
        m2 = get_app_hook_manager()
        assert m1 is m2
        mod._hook_manager = None
