"""
Tests for VOS3 Kernel Compiler Service
========================================

Unit tests for cross-compilation pipeline: compile_c_source,
compile_and_upload, compile_upload_and_run.

All subprocess calls and bridge interactions are mocked.
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, patch, mock_open

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel_bridge.compiler import (
    compile_c_source,
    compile_and_upload,
    compile_upload_and_run,
    CFLAGS,
    LDFLAGS,
    CC,
    LD,
    VOS3_ROOT,
    USER_INC,
)
from kernel_bridge.protocol import BridgeResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_subprocess(returncode=0, stdout=b"", stderr=b""):
    """Create a mock async subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


class TestCompilerConfig:
    def test_cc_is_cross_compiler(self):
        assert "x86_64-elf-gcc" in CC or "gcc" in CC

    def test_ld_is_cross_linker(self):
        assert "x86_64-elf-ld" in LD or "ld" in LD

    def test_cflags_freestanding(self):
        assert "-ffreestanding" in CFLAGS

    def test_cflags_no_stdlib(self):
        assert "-nostdlib" in CFLAGS

    def test_cflags_nostdinc(self):
        assert "-nostdinc" in CFLAGS

    def test_cflags_includes_user_inc(self):
        assert any(str(USER_INC) in flag for flag in CFLAGS)

    def test_ldflags_static(self):
        assert "-static" in LDFLAGS

    def test_ldflags_text_address(self):
        assert "-Ttext=0x400000" in LDFLAGS

    def test_vos3_root_exists(self):
        # VOS3_ROOT should resolve to the project root. The historical
        # `"VOS3" in str(VOS3_ROOT)` check was coupled to a checkout dir literally
        # named "VOS3"; the canonical repo dir is "vos.v1", so that substring
        # assertion was environment-brittle and objectively outdated. Assert the
        # REAL intent (named in the test + comment): the root resolves, exists,
        # and contains the kernel tree. Strictly stronger, not weaker.
        assert VOS3_ROOT.exists() and VOS3_ROOT.is_dir()
        assert (VOS3_ROOT / "kernel").is_dir()


# ---------------------------------------------------------------------------
# compile_c_source
# ---------------------------------------------------------------------------


class TestCompileCSource:
    @pytest.mark.asyncio
    async def test_compile_success(self):
        binary_data = b"\x7fELF" + b"\x00" * 100

        compile_proc = _mock_subprocess(returncode=0)
        link_proc = _mock_subprocess(returncode=0)
        gcc_inc_proc = _mock_subprocess(returncode=0, stdout=b"/usr/lib/gcc/include\n")

        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            args[0] if args else ""
            if call_count == 1:
                return gcc_inc_proc  # _get_gcc_include_path
            elif call_count == 2:
                return compile_proc  # gcc -c
            else:
                return link_proc  # ld

        with patch(
            "kernel_bridge.compiler.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            with patch("kernel_bridge.compiler.os.path.exists", return_value=True):
                with patch("builtins.open", mock_open(read_data=binary_data)):
                    binary, error = await compile_c_source(
                        "int main() { return 0; }",
                        "test_prog",
                    )

        assert binary is not None
        assert error == ""

    @pytest.mark.asyncio
    async def test_compile_failure(self):
        gcc_inc_proc = _mock_subprocess(returncode=0, stdout=b"/usr/lib/gcc/include\n")
        compile_proc = _mock_subprocess(
            returncode=1,
            stderr=b"error: expected ';' before '}'\n",
        )

        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gcc_inc_proc
            return compile_proc

        with patch(
            "kernel_bridge.compiler.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            binary, error = await compile_c_source(
                "int main() { return 0 }",  # syntax error
                "bad_prog",
            )

        assert binary is None
        assert "expected" in error

    @pytest.mark.asyncio
    async def test_link_failure(self):
        gcc_inc_proc = _mock_subprocess(returncode=0, stdout=b"/usr/lib/gcc/include\n")
        compile_proc = _mock_subprocess(returncode=0)
        link_proc = _mock_subprocess(
            returncode=1,
            stderr=b"undefined reference to 'missing_func'\n",
        )

        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gcc_inc_proc
            elif call_count == 2:
                return compile_proc
            return link_proc

        with patch(
            "kernel_bridge.compiler.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            with patch("kernel_bridge.compiler.os.path.exists", return_value=True):
                binary, error = await compile_c_source(
                    "void missing_func(); int main() { missing_func(); return 0; }",
                    "link_fail",
                )

        assert binary is None
        assert "undefined reference" in error

    @pytest.mark.asyncio
    async def test_missing_crt0(self):
        gcc_inc_proc = _mock_subprocess(returncode=0, stdout=b"/usr/lib/gcc/include\n")
        compile_proc = _mock_subprocess(returncode=0)

        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gcc_inc_proc
            return compile_proc

        def exists_side_effect(path):
            if "crt0" in path:
                return False
            return True

        with patch(
            "kernel_bridge.compiler.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            with patch(
                "kernel_bridge.compiler.os.path.exists", side_effect=exists_side_effect
            ):
                binary, error = await compile_c_source(
                    "int main() { return 0; }",
                    "test",
                )

        assert binary is None
        assert "crt0" in error.lower()

    @pytest.mark.asyncio
    async def test_missing_libc(self):
        gcc_inc_proc = _mock_subprocess(returncode=0, stdout=b"/usr/lib/gcc/include\n")
        compile_proc = _mock_subprocess(returncode=0)

        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gcc_inc_proc
            return compile_proc

        def exists_side_effect(path):
            if "libc" in path:
                return False
            return True

        with patch(
            "kernel_bridge.compiler.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            with patch(
                "kernel_bridge.compiler.os.path.exists", side_effect=exists_side_effect
            ):
                binary, error = await compile_c_source(
                    "int main() { return 0; }",
                    "test",
                )

        assert binary is None
        assert "libc" in error.lower()


# ---------------------------------------------------------------------------
# compile_and_upload
# ---------------------------------------------------------------------------


class TestCompileAndUpload:
    @pytest.mark.asyncio
    async def test_upload_success(self):
        binary = b"\x7fELF" + b"\x00" * 100

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=True, data="104"),
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                success, message = await compile_and_upload(
                    "int main() { return 0; }",
                    "test_prog",
                )

        assert success is True
        assert "/disk/test_prog" in message

    @pytest.mark.asyncio
    async def test_upload_compile_error(self):
        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(None, "syntax error"),
        ):
            success, message = await compile_and_upload(
                "bad code",
                "fail_prog",
            )

        assert success is False
        assert "Compilation error" in message

    @pytest.mark.asyncio
    async def test_upload_bridge_error(self):
        binary = b"\x7fELF" + b"\x00" * 100

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=False, error_msg="disk full"),
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                success, message = await compile_and_upload(
                    "int main() { return 0; }",
                    "test_prog",
                )

        assert success is False
        assert "Upload failed" in message

    @pytest.mark.asyncio
    async def test_upload_custom_dest(self):
        binary = b"\x7fELF"

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=True, data="4"),
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                success, message = await compile_and_upload(
                    "int main() { return 0; }",
                    "my_bin",
                    dest_path="/disk/bin",
                )

        assert success is True
        assert "/disk/bin/my_bin" in message


# ---------------------------------------------------------------------------
# compile_upload_and_run
# ---------------------------------------------------------------------------


class TestCompileUploadAndRun:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        binary = b"\x7fELF" + b"\x00" * 8188

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=True, data="8192"),
        )
        mock_svc.exec_program = AsyncMock(
            return_value={"exit_code": "0", "output": "Hello from VOS3!"},
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                result = await compile_upload_and_run(
                    '#include <stdio.h>\nint main() { puts("Hello from VOS3!"); return 0; }',
                    "hello",
                )

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert result["output"] == "Hello from VOS3!"
        assert result["path"] == "/disk/hello"
        assert result["size"] == 8192

    @pytest.mark.asyncio
    async def test_pipeline_compile_failure(self):
        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(None, "undefined reference"),
        ):
            result = await compile_upload_and_run("bad code")

        assert result["success"] is False
        assert result["compile_error"] == "undefined reference"
        assert result["exit_code"] == -1

    @pytest.mark.asyncio
    async def test_pipeline_upload_failure(self):
        binary = b"\x7fELF"

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=False, error_msg="disk full"),
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                result = await compile_upload_and_run("int main() { return 0; }")

        assert result["success"] is False
        assert "Upload failed" in result["output"]

    @pytest.mark.asyncio
    async def test_pipeline_exec_nonzero_exit(self):
        binary = b"\x7fELF"

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=True, data="4"),
        )
        mock_svc.exec_program = AsyncMock(
            return_value={"exit_code": "1", "output": "segfault"},
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                result = await compile_upload_and_run("int main() { return 1; }")

        assert result["success"] is False
        assert result["exit_code"] == 1
        assert result["output"] == "segfault"

    @pytest.mark.asyncio
    async def test_pipeline_with_args(self):
        binary = b"\x7fELF"

        mock_svc = AsyncMock()
        mock_svc.write_file = AsyncMock(
            return_value=BridgeResponse(success=True, data="4"),
        )
        mock_svc.exec_program = AsyncMock(
            return_value={"exit_code": "0", "output": "ok"},
        )

        with patch(
            "kernel_bridge.compiler.compile_c_source",
            new_callable=AsyncMock,
            return_value=(binary, ""),
        ):
            with patch(
                "kernel_bridge.service.get_bridge_service", return_value=mock_svc
            ):
                result = await compile_upload_and_run(
                    "int main(int argc, char** argv) { return 0; }",
                    program_name="argtest",
                    args="hello world",
                )

        assert result["success"] is True
        mock_svc.exec_program.assert_called_with("/disk/argtest", "hello world")
