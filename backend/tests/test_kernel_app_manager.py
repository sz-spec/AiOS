"""Tests for services/kernel_app_manager.py — Kernel App Manager."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from services.kernel_app_manager import KernelAppManager, get_kernel_app_manager


def _make_bridge_response(success=True, data="", error_msg="", error_code=0):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error_msg = error_msg
    resp.error_code = error_code
    return resp


class TestKernelAppManager:
    @patch("services.kernel_app_manager.get_bridge_service")
    def setup_method(self, _method, mock_bridge_factory):
        self.mock_bridge = MagicMock()
        mock_bridge_factory.return_value = self.mock_bridge
        self.mgr = KernelAppManager()

    @pytest.mark.asyncio
    async def test_load_app_sends_command(self):
        self.mock_bridge._send_command = AsyncMock(
            return_value=_make_bridge_response(data="app_id=1|status=loaded")
        )
        with patch(
            "services.kernel_app_manager.parse_kv_response",
            return_value={"app_id": "1", "status": "loaded"},
        ):
            result = await self.mgr.load_app(1, "/bin/test")
        self.mock_bridge._send_command.assert_called_once_with(
            "APPLOAD", "1", "/bin/test"
        )
        assert result["status"] == "loaded"

    @pytest.mark.asyncio
    async def test_load_app_no_binary(self):
        self.mock_bridge._send_command = AsyncMock(
            return_value=_make_bridge_response(data="app_id=1|status=loaded")
        )
        with patch(
            "services.kernel_app_manager.parse_kv_response",
            return_value={"app_id": "1"},
        ):
            await self.mgr.load_app(1)
        self.mock_bridge._send_command.assert_called_once_with("APPLOAD", "1")

    @pytest.mark.asyncio
    async def test_load_app_invalid_id(self):
        result = await self.mgr.load_app(8)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_app_status(self):
        self.mock_bridge._send_command = AsyncMock(
            return_value=_make_bridge_response(data="app_id=1|mem_used=4096")
        )
        with patch(
            "services.kernel_app_manager.parse_kv_response",
            return_value={"app_id": "1", "mem_used": "4096"},
        ):
            result = await self.mgr.get_app_status(1)
        self.mock_bridge._send_command.assert_called_once_with("APPSTAT", "1")
        assert result["mem_used"] == "4096"

    @pytest.mark.asyncio
    async def test_kill_app(self):
        self.mock_bridge._send_command = AsyncMock(
            return_value=_make_bridge_response(data="app_id=1|killed=2")
        )
        with patch(
            "services.kernel_app_manager.parse_kv_response",
            return_value={"app_id": "1", "killed": "2"},
        ):
            result = await self.mgr.kill_app(1)
        self.mock_bridge._send_command.assert_called_once_with("APPKILL", "1")
        assert result["killed"] == "2"

    @pytest.mark.asyncio
    async def test_bridge_error(self):
        self.mock_bridge._send_command = AsyncMock(
            return_value=_make_bridge_response(
                success=False, error_msg="not found", error_code=404
            )
        )
        result = await self.mgr.load_app(1)
        assert result["error"] == "not found"
        assert result["error_code"] == 404


class TestGetKernelAppManager:
    def test_singleton(self):
        import services.kernel_app_manager as mod

        mod._manager = None
        with patch("services.kernel_app_manager.get_bridge_service"):
            m1 = get_kernel_app_manager()
            m2 = get_kernel_app_manager()
        assert m1 is m2
        mod._manager = None
