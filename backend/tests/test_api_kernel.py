"""
Tests for VOS3 Kernel API Routes
==================================

HTTP tests for all /api/kernel/* endpoints with mocked bridge service.
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from kernel_bridge.protocol import BridgeResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


def _mock_bridge(**overrides):
    """Create a fully-mocked bridge service with sensible defaults."""
    svc = AsyncMock()
    svc.connected = True

    # Defaults
    svc.ping = AsyncMock(return_value=BridgeResponse(success=True, data="PONG"))
    svc.sysinfo = AsyncMock(
        return_value={
            "uptime_ms": 60000,
            "mem_total_kb": 524288,
            "mem_free_kb": 480000,
            "tasks": 3,
        }
    )
    svc.procs = AsyncMock(
        return_value=[
            {"pid": 1, "name": "init", "state": "RUNNING", "ppid": 0},
            {"pid": 2, "name": "bridge", "state": "READY", "ppid": 1},
        ]
    )
    svc.list_dir = AsyncMock(return_value=["hello.txt", "test", "dir1"])
    svc.stat = AsyncMock(
        return_value={
            "total_blocks": 1024,
            "free_blocks": 512,
            "block_size": 4096,
        }
    )
    svc.read_file = AsyncMock(return_value="file content here")
    svc.write_file = AsyncMock(return_value=BridgeResponse(success=True, data="17"))
    svc.unlink = AsyncMock(return_value=BridgeResponse(success=True))
    svc.mkdir = AsyncMock(return_value=BridgeResponse(success=True))
    svc.rename = AsyncMock(return_value=BridgeResponse(success=True))
    svc.exec_program = AsyncMock(return_value={"exit_code": 0, "output": "hello world"})
    svc.http_get = AsyncMock(return_value="<html>test</html>")

    # Apply overrides
    for k, v in overrides.items():
        setattr(svc, k, v)

    return svc


# ---------------------------------------------------------------------------
# GET /api/kernel/status
# ---------------------------------------------------------------------------


class TestKernelStatus:
    def test_status_connected(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["ping"] is True
        assert "sysinfo" in data

    def test_status_disconnected(self, client):
        svc = _mock_bridge(
            ping=AsyncMock(
                return_value=BridgeResponse(success=False, error_msg="timeout")
            ),
        )
        svc.connected = False
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ping"] is False


# ---------------------------------------------------------------------------
# POST /api/kernel/ping
# ---------------------------------------------------------------------------


class TestKernelPing:
    def test_ping_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post("/api/kernel/ping")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["response"] == "PONG"

    def test_ping_failure(self, client):
        svc = _mock_bridge(
            ping=AsyncMock(
                return_value=BridgeResponse(success=False, error_msg="timeout")
            ),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post("/api/kernel/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# GET /api/kernel/sysinfo
# ---------------------------------------------------------------------------


class TestKernelSysinfo:
    def test_sysinfo_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/sysinfo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uptime_ms"] == 60000
        assert data["tasks"] == 3

    def test_sysinfo_error(self, client):
        svc = _mock_bridge(
            sysinfo=AsyncMock(return_value={"error": "bridge timeout"}),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/sysinfo")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /api/kernel/processes
# ---------------------------------------------------------------------------


class TestKernelProcesses:
    def test_processes_returns_list(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["processes"]) == 2
        assert data["processes"][0]["name"] == "init"

    def test_processes_empty(self, client):
        svc = _mock_bridge(procs=AsyncMock(return_value=[]))
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/processes")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/kernel/execute
# ---------------------------------------------------------------------------


class TestKernelExecute:
    def test_execute_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/execute",
                json={
                    "path": "/disk/hello",
                    "args": "",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert data["output"] == "hello world"

    def test_execute_with_args(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/execute",
                json={
                    "path": "/disk/prog",
                    "args": "arg1 arg2",
                },
            )
        assert resp.status_code == 200
        svc.exec_program.assert_called_with("/disk/prog", "arg1 arg2")

    def test_execute_error(self, client):
        svc = _mock_bridge(
            exec_program=AsyncMock(
                return_value={"error": "not found", "exit_code": -1}
            ),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/execute",
                json={
                    "path": "/disk/missing",
                },
            )
        assert resp.status_code == 502

    def test_execute_invalid_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/execute",
                json={
                    "path": "../etc/passwd",
                },
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/kernel/filesystem
# ---------------------------------------------------------------------------


class TestKernelFilesystem:
    def test_filesystem_default_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/filesystem")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/disk"
        assert data["count"] == 3

    def test_filesystem_custom_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/filesystem?path=/disk/subdir")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/disk/subdir"

    def test_filesystem_invalid_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/filesystem?path=../../etc")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/kernel/file  (read)
# ---------------------------------------------------------------------------


class TestKernelReadFile:
    def test_read_file_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/file?path=/disk/test.txt")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "file content here"
        assert data["path"] == "/disk/test.txt"
        assert data["size"] == len("file content here")

    def test_read_file_not_found(self, client):
        svc = _mock_bridge(read_file=AsyncMock(return_value=None))
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/file?path=/disk/missing.txt")
        assert resp.status_code == 404

    def test_read_file_no_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/file")
        assert resp.status_code == 422  # Missing required query param


# ---------------------------------------------------------------------------
# POST /api/kernel/file  (write)
# ---------------------------------------------------------------------------


class TestKernelWriteFile:
    def test_write_file_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/file",
                json={
                    "path": "/disk/new.txt",
                    "content": "hello world",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["bytes_written"] == 17

    def test_write_file_error(self, client):
        svc = _mock_bridge(
            write_file=AsyncMock(
                return_value=BridgeResponse(success=False, error_msg="disk full")
            ),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/file",
                json={
                    "path": "/disk/big.txt",
                    "content": "data",
                },
            )
        assert resp.status_code == 502

    def test_write_file_invalid_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/file",
                json={
                    "path": "/disk/../../../etc/passwd",
                    "content": "hack",
                },
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/kernel/file
# ---------------------------------------------------------------------------


class TestKernelDeleteFile:
    def test_delete_file_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.delete("/api/kernel/file?path=/disk/old.txt")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_file_error(self, client):
        svc = _mock_bridge(
            unlink=AsyncMock(
                return_value=BridgeResponse(success=False, error_msg="not found")
            ),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.delete("/api/kernel/file?path=/disk/ghost.txt")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST /api/kernel/mkdir
# ---------------------------------------------------------------------------


class TestKernelMkdir:
    def test_mkdir_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post("/api/kernel/mkdir?path=/disk/newdir")
        assert resp.status_code == 200
        assert resp.json()["created"] is True

    def test_mkdir_error(self, client):
        svc = _mock_bridge(
            mkdir=AsyncMock(
                return_value=BridgeResponse(success=False, error_msg="already exists")
            ),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post("/api/kernel/mkdir?path=/disk/existing")
        assert resp.status_code == 502

    def test_mkdir_invalid_path(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post("/api/kernel/mkdir?path=no_leading_slash")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/kernel/rename
# ---------------------------------------------------------------------------


class TestKernelRename:
    def test_rename_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/rename",
                json={
                    "old_path": "/disk/a.txt",
                    "new_path": "/disk/b.txt",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["renamed"] is True

    def test_rename_error(self, client):
        svc = _mock_bridge(
            rename=AsyncMock(
                return_value=BridgeResponse(success=False, error_msg="source not found")
            ),
        )
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/rename",
                json={
                    "old_path": "/disk/missing",
                    "new_path": "/disk/new",
                },
            )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /api/kernel/http-proxy
# ---------------------------------------------------------------------------


class TestKernelHttpProxy:
    def test_http_proxy_success(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/http-proxy?url=https://example.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://example.com"
        assert data["content"] == "<html>test</html>"

    def test_http_proxy_failure(self, client):
        svc = _mock_bridge(http_get=AsyncMock(return_value=None))
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/http-proxy?url=https://nonexistent.invalid")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Legacy /api/kernel/disk/* routes
# ---------------------------------------------------------------------------


class TestLegacyDiskRoutes:
    def test_disk_stat(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/disk/stat")
        assert resp.status_code == 200
        assert "total_blocks" in resp.json()

    def test_disk_ls(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/disk/ls")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_disk_read(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get("/api/kernel/disk/read?path=/disk/test.txt")
        assert resp.status_code == 200

    def test_disk_write(self, client):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.post(
                "/api/kernel/disk/write",
                json={
                    "path": "/disk/file.txt",
                    "content": "data",
                },
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Path validation (security)
# ---------------------------------------------------------------------------


class TestPathValidation:
    @pytest.mark.parametrize(
        "bad_path",
        [
            "../etc/passwd",
            "/disk/../../etc/shadow",
            "",
            "/disk/file with spaces",
            "/disk/file;rm -rf /",
            "/disk/$HOME",
            "/disk/`whoami`",
        ],
    )
    def test_rejects_unsafe_paths(self, client, bad_path):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get(f"/api/kernel/file?path={bad_path}")
        assert resp.status_code in (400, 422), f"Path '{bad_path}' should be rejected"

    @pytest.mark.parametrize(
        "good_path",
        [
            "/disk/test.txt",
            "/disk/dir/subdir/file.c",
            "/proc/1/status",
            "/disk/my_program",
            "/disk/file-name.ext",
        ],
    )
    def test_accepts_safe_paths(self, client, good_path):
        svc = _mock_bridge()
        with patch("api.kernel_routes.get_bridge_service", return_value=svc):
            resp = client.get(f"/api/kernel/file?path={good_path}")
        # Should not be 400 (validation error)
        assert resp.status_code != 400, f"Path '{good_path}' should be accepted"


# ---------------------------------------------------------------------------
# POST /api/kernel/compile-and-run
# ---------------------------------------------------------------------------


class TestCompileAndRun:
    def test_compile_and_run_success(self, client):
        mock_result = {
            "success": True,
            "compile_error": "",
            "output": "Hello from VOS3!",
            "exit_code": 0,
            "path": "/disk/agent_prog",
            "size": 8192,
        }
        with patch(
            "kernel_bridge.compiler.compile_upload_and_run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/kernel/compile-and-run",
                json={
                    "source_code": '#include <stdio.h>\nint main() { puts("Hello"); return 0; }',
                    "program_name": "agent_prog",
                    "args": "",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["exit_code"] == 0

    def test_compile_and_run_compile_error(self, client):
        mock_result = {
            "success": False,
            "compile_error": "error: expected ';' before '}' token",
            "output": "",
            "exit_code": -1,
        }
        with patch(
            "kernel_bridge.compiler.compile_upload_and_run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/kernel/compile-and-run",
                json={
                    "source_code": "int main() { return 0 }",  # missing semicolon
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "compile_error" in data

    def test_compile_and_run_missing_source(self, client):
        resp = client.post("/api/kernel/compile-and-run", json={})
        assert resp.status_code == 422  # Pydantic validation error
