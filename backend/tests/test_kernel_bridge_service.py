"""
Tests for VOS3 Kernel Bridge Service
======================================

Unit tests for KernelBridgeService using mocked async streams.
Each test patches the socket connection so no actual QEMU is needed.
"""

import pytest
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel_bridge.service import KernelBridgeService, get_bridge_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reader(lines: list[str]) -> AsyncMock:
    """Create a mock StreamReader that yields the given lines."""
    reader = AsyncMock()
    responses = [line.encode("ascii") for line in lines]
    reader.readline = AsyncMock(side_effect=responses)
    return reader


def _make_writer() -> MagicMock:
    """Create a mock StreamWriter."""
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def _make_service(lines: list[str]) -> KernelBridgeService:
    """Create a service with pre-wired mock reader/writer."""
    svc = KernelBridgeService(socket_path="/tmp/test.sock")
    svc._reader = _make_reader(lines)
    svc._writer = _make_writer()
    svc._connected = True
    return svc


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class TestConnection:
    @pytest.mark.asyncio
    async def test_connect_success(self):
        svc = KernelBridgeService(socket_path="/tmp/test.sock")
        mock_reader = _make_reader([])
        mock_writer = _make_writer()

        with patch(
            "kernel_bridge.service.asyncio.open_unix_connection",
            new_callable=AsyncMock,
            return_value=(mock_reader, mock_writer),
        ):
            with patch("kernel_bridge.service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.connect()

        assert result is True
        assert svc.connected is True

    @pytest.mark.asyncio
    async def test_connect_failure_file_not_found(self):
        svc = KernelBridgeService(socket_path="/nonexistent/path.sock")

        with patch(
            "kernel_bridge.service.asyncio.open_unix_connection",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("No such file"),
        ):
            result = await svc.connect()

        assert result is False
        assert svc.connected is False

    @pytest.mark.asyncio
    async def test_connect_failure_connection_refused(self):
        svc = KernelBridgeService(socket_path="/tmp/test.sock")

        with patch(
            "kernel_bridge.service.asyncio.open_unix_connection",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError(),
        ):
            result = await svc.connect()

        assert result is False

    @pytest.mark.asyncio
    async def test_disconnect(self):
        svc = _make_service([])
        assert svc.connected is True

        await svc.disconnect()

        assert svc.connected is False
        assert svc._reader is None
        assert svc._writer is None

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        svc = KernelBridgeService()
        await svc.disconnect()  # Should not raise
        assert svc.connected is False


# ---------------------------------------------------------------------------
# PING
# ---------------------------------------------------------------------------


class TestPing:
    @pytest.mark.asyncio
    async def test_ping_success(self):
        svc = _make_service(["OK|PONG\n"])
        resp = await svc.ping()
        assert resp.success is True
        assert resp.data == "PONG"

    @pytest.mark.asyncio
    async def test_ping_error(self):
        svc = _make_service(["ERR|1|timeout\n"])
        resp = await svc.ping()
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_ping_sends_correct_command(self):
        svc = _make_service(["OK|PONG\n"])
        await svc.ping()
        svc._writer.write.assert_called_with(b"PING\n")


# ---------------------------------------------------------------------------
# STAT
# ---------------------------------------------------------------------------


class TestStat:
    @pytest.mark.asyncio
    async def test_stat_success(self):
        svc = _make_service(["OK|total_blocks=1024,free_blocks=512,block_size=4096\n"])
        result = await svc.stat("/disk")
        assert result["total_blocks"] == 1024
        assert result["free_blocks"] == 512
        assert result["block_size"] == 4096

    @pytest.mark.asyncio
    async def test_stat_error(self):
        svc = _make_service(["ERR|2|path not found\n"])
        result = await svc.stat("/nonexistent")
        assert "error" in result


# ---------------------------------------------------------------------------
# WRITE / READ
# ---------------------------------------------------------------------------


class TestWriteRead:
    @pytest.mark.asyncio
    async def test_write_file_string(self):
        svc = _make_service(["OK|5\n"])
        resp = await svc.write_file("/disk/test.txt", "hello")
        assert resp.success is True
        assert resp.data == "5"

    @pytest.mark.asyncio
    async def test_write_file_bytes(self):
        svc = _make_service(["OK|3\n"])
        resp = await svc.write_file("/disk/bin", b"\x00\x01\x02")
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_write_file_error(self):
        svc = _make_service(["ERR|5|disk full\n"])
        resp = await svc.write_file("/disk/big", "x" * 10000)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_read_file_success(self):
        # "hello" = 68656c6c6f
        svc = _make_service(["OK|68656c6c6f\n"])
        content = await svc.read_file("/disk/test.txt")
        assert content == "hello"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        svc = _make_service(["ERR|2|not found\n"])
        content = await svc.read_file("/disk/missing")
        assert content is None


# ---------------------------------------------------------------------------
# LS
# ---------------------------------------------------------------------------


class TestListDir:
    @pytest.mark.asyncio
    async def test_list_dir_with_files(self):
        svc = _make_service(["OK|test.txt,hello,dir1\n"])
        files = await svc.list_dir("/disk")
        assert files == ["test.txt", "hello", "dir1"]

    @pytest.mark.asyncio
    async def test_list_dir_empty(self):
        svc = _make_service(["OK|\n"])
        files = await svc.list_dir("/disk/empty")
        assert files == []

    @pytest.mark.asyncio
    async def test_list_dir_error(self):
        svc = _make_service(["ERR|2|not a directory\n"])
        files = await svc.list_dir("/disk/file.txt")
        assert files == []


# ---------------------------------------------------------------------------
# EXEC
# ---------------------------------------------------------------------------


class TestExecProgram:
    @pytest.mark.asyncio
    async def test_exec_success(self):
        svc = _make_service(["OK|exit_code=0,output=hello world\n"])
        result = await svc.exec_program("/disk/hello")
        assert result["exit_code"] == 0
        assert result["output"] == "hello world"

    @pytest.mark.asyncio
    async def test_exec_nonzero_exit(self):
        svc = _make_service(["OK|exit_code=1,output=error\n"])
        result = await svc.exec_program("/disk/fail")
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_exec_with_args(self):
        svc = _make_service(["OK|exit_code=0\n"])
        await svc.exec_program("/disk/prog", "arg1 arg2")
        svc._writer.write.assert_called_with(b"EXEC|/disk/prog|arg1 arg2\n")

    @pytest.mark.asyncio
    async def test_exec_error(self):
        svc = _make_service(["ERR|2|file not found\n"])
        result = await svc.exec_program("/disk/missing")
        assert "error" in result
        assert result["exit_code"] == -1


# ---------------------------------------------------------------------------
# MKDIR / UNLINK / RENAME
# ---------------------------------------------------------------------------


class TestFilesystemOps:
    @pytest.mark.asyncio
    async def test_mkdir_success(self):
        svc = _make_service(["OK\n"])
        resp = await svc.mkdir("/disk/newdir")
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_mkdir_error(self):
        svc = _make_service(["ERR|17|already exists\n"])
        resp = await svc.mkdir("/disk/existing")
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_unlink_success(self):
        svc = _make_service(["OK\n"])
        resp = await svc.unlink("/disk/old.txt")
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_unlink_error(self):
        svc = _make_service(["ERR|2|not found\n"])
        resp = await svc.unlink("/disk/ghost")
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_rename_success(self):
        svc = _make_service(["OK\n"])
        resp = await svc.rename("/disk/a.txt", "/disk/b.txt")
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_rename_sends_both_paths(self):
        svc = _make_service(["OK\n"])
        await svc.rename("/disk/old", "/disk/new")
        svc._writer.write.assert_called_with(b"RENAME|/disk/old|/disk/new\n")


# ---------------------------------------------------------------------------
# SYSINFO
# ---------------------------------------------------------------------------


class TestSysinfo:
    @pytest.mark.asyncio
    async def test_sysinfo_success(self):
        svc = _make_service(
            ["OK|uptime_ms=60000,mem_total_kb=524288,mem_free_kb=500000,tasks=3\n"]
        )
        info = await svc.sysinfo()
        assert info["uptime_ms"] == 60000
        assert info["mem_total_kb"] == 524288
        assert info["mem_free_kb"] == 500000
        assert info["tasks"] == 3

    @pytest.mark.asyncio
    async def test_sysinfo_error(self):
        svc = _make_service(["ERR|1|timeout\n"])
        info = await svc.sysinfo()
        assert "error" in info


# ---------------------------------------------------------------------------
# PROCS
# ---------------------------------------------------------------------------


class TestProcs:
    @pytest.mark.asyncio
    async def test_procs_success(self):
        svc = _make_service(["OK|1,init,RUNNING,0;2,bridge,READY,1\n"])
        procs = await svc.procs()
        assert len(procs) == 2
        assert procs[0]["pid"] == 1
        assert procs[0]["name"] == "init"
        assert procs[1]["state"] == "READY"

    @pytest.mark.asyncio
    async def test_procs_empty(self):
        svc = _make_service(["OK|\n"])
        procs = await svc.procs()
        assert procs == []

    @pytest.mark.asyncio
    async def test_procs_error(self):
        svc = _make_service(["ERR|1|not available\n"])
        procs = await svc.procs()
        assert procs == []


# ---------------------------------------------------------------------------
# HTTP GET (host-side proxy)
# ---------------------------------------------------------------------------


class TestHttpGet:
    @pytest.mark.asyncio
    async def test_http_get_success(self):
        svc = _make_service([])
        mock_response = MagicMock()
        mock_response.text = "<html>hello</html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "kernel_bridge.service.validate_url",
            return_value=("93.184.216.34", "example.com"),
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                content = await svc.http_get("https://example.com")

        assert content == "<html>hello</html>"

    @pytest.mark.asyncio
    async def test_http_get_failure(self):
        svc = _make_service([])

        with patch(
            "kernel_bridge.service.validate_url",
            return_value=("93.184.216.34", "nonexistent.invalid"),
        ):
            with patch("httpx.AsyncClient", side_effect=Exception("network error")):
                content = await svc.http_get("https://nonexistent.invalid")

        assert content is None


# ---------------------------------------------------------------------------
# Auto-reconnect
# ---------------------------------------------------------------------------


class TestAutoReconnect:
    @pytest.mark.asyncio
    async def test_send_command_reconnects_when_disconnected(self):
        svc = KernelBridgeService(socket_path="/tmp/test.sock")
        assert svc.connected is False

        mock_reader = _make_reader(["OK|PONG\n"])
        mock_writer = _make_writer()

        with patch(
            "kernel_bridge.service.asyncio.open_unix_connection",
            new_callable=AsyncMock,
            return_value=(mock_reader, mock_writer),
        ):
            with patch("kernel_bridge.service.asyncio.sleep", new_callable=AsyncMock):
                resp = await svc.ping()

        assert resp.success is True
        assert svc.connected is True

    @pytest.mark.asyncio
    async def test_send_command_returns_error_when_cannot_reconnect(self):
        svc = KernelBridgeService(socket_path="/nonexistent.sock")

        with patch(
            "kernel_bridge.service.asyncio.open_unix_connection",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError(),
        ):
            resp = await svc.ping()

        assert resp.success is False
        assert "not connected" in resp.error_msg

    @pytest.mark.asyncio
    async def test_communication_error_disconnects(self):
        svc = _make_service([])
        svc._reader.readline = AsyncMock(side_effect=ConnectionError("broken pipe"))

        resp = await svc.ping()

        assert resp.success is False
        assert svc.connected is False

    @pytest.mark.asyncio
    async def test_timeout_disconnects(self):
        svc = _make_service([])
        svc._reader.readline = AsyncMock(side_effect=asyncio.TimeoutError())

        resp = await svc.ping()

        assert resp.success is False
        assert svc.connected is False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_bridge_service_returns_same_instance(self):
        import kernel_bridge.service as mod

        old = mod._service
        try:
            mod._service = None
            svc1 = get_bridge_service()
            svc2 = get_bridge_service()
            assert svc1 is svc2
        finally:
            mod._service = old

    def test_get_bridge_service_default_socket_path(self):
        import kernel_bridge.service as mod

        old = mod._service
        try:
            mod._service = None
            svc = get_bridge_service()
            assert svc.socket_path == "/tmp/vos3_bridge.sock"
        finally:
            mod._service = old
