"""
VOS3 Cross-Compiler Service
=============================

Cross-compiles C source code to x86_64-elf binaries for execution
on the VOS3 kernel. Uses the same toolchain as user/Makefile.

Pipeline: source code -> temp file -> x86_64-elf-gcc -> binary bytes
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vos3.compiler")

# Paths
VOS3_ROOT = Path(__file__).parent.parent.parent
USER_DIR = VOS3_ROOT / "user"
USER_INC = USER_DIR / "include"
USER_LIB = USER_DIR / "build" / "libc.a"
USER_CRT0 = USER_DIR / "build" / "lib" / "crt0.o"

# Cross-compiler tools
CROSS = os.environ.get("VOS3_CROSS_PREFIX", "x86_64-elf-")
CC = f"{CROSS}gcc"
LD = f"{CROSS}ld"

# Compiler flags matching user/Makefile
CFLAGS = [
    "-std=c11",
    "-ffreestanding",
    "-fno-stack-protector",
    "-fno-pic",
    "-fno-pie",
    "-mno-red-zone",
    "-mno-sse",
    "-mno-sse2",
    "-mno-mmx",
    "-mno-80387",
    "-nostdinc",
    "-nostdlib",
    f"-I{USER_INC}",
    "-Wall",
    "-Wextra",
    "-Wpedantic",
    "-Wno-unused-parameter",
    "-O2",
]

LDFLAGS = [
    "-nostdlib",
    "-static",
    "-no-pie",
    "-z",
    "max-page-size=0x1000",
    "-Ttext=0x400000",
]


async def compile_c_source(
    source_code: str,
    program_name: str = "agent_prog",
) -> tuple[Optional[bytes], str]:
    """Compile C source code to a VOS3 x86_64-elf binary.

    Args:
        source_code: C source code string
        program_name: Name for the output binary

    Returns:
        Tuple of (binary_bytes, error_message).
        binary_bytes is None on compilation failure.
    """
    with tempfile.TemporaryDirectory(prefix="vos3_compile_") as tmpdir:
        src_path = os.path.join(tmpdir, f"{program_name}.c")
        obj_path = os.path.join(tmpdir, f"{program_name}.o")
        bin_path = os.path.join(tmpdir, program_name)

        # Write source
        with open(src_path, "w") as f:
            f.write(source_code)

        # Compile
        gcc_inc = await _get_gcc_include_path()
        cflags = CFLAGS.copy()
        if gcc_inc:
            cflags.extend(["-isystem", gcc_inc])

        compile_cmd = [CC] + cflags + ["-c", src_path, "-o", obj_path]
        logger.info("Compiling: %s", " ".join(compile_cmd))

        proc = await asyncio.create_subprocess_exec(
            *compile_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            logger.warning("Compilation failed: %s", error)
            return None, error

        # Link
        crt0 = str(USER_CRT0)
        libc = str(USER_LIB)

        # Check that crt0 and libc exist
        if not os.path.exists(crt0):
            return None, f"crt0.o not found at {crt0}. Build user/ first."
        if not os.path.exists(libc):
            return None, f"libc.a not found at {libc}. Build user/ first."

        link_cmd = [LD] + LDFLAGS + ["-o", bin_path, crt0, obj_path, libc]
        logger.info("Linking: %s", " ".join(link_cmd))

        proc = await asyncio.create_subprocess_exec(
            *link_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            logger.warning("Linking failed: %s", error)
            return None, error

        # Read binary
        with open(bin_path, "rb") as f:
            binary = f.read()

        logger.info("Compiled %s: %d bytes", program_name, len(binary))
        return binary, ""


async def _get_gcc_include_path() -> Optional[str]:
    """Get the GCC builtin include path."""
    try:
        proc = await asyncio.create_subprocess_exec(
            CC,
            "-print-file-name=include",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            path = stdout.decode().strip()
            if os.path.isdir(path):
                return path
    except Exception:
        pass
    return None


async def compile_and_upload(
    source_code: str,
    program_name: str = "agent_prog",
    dest_path: str = "/disk",
) -> tuple[bool, str]:
    """Compile source and upload binary to kernel via bridge.

    Args:
        source_code: C source code
        program_name: Binary name
        dest_path: Destination directory on kernel VFS

    Returns:
        Tuple of (success, message)
    """
    from .service import get_bridge_service

    # Compile
    binary, error = await compile_c_source(source_code, program_name)
    if binary is None:
        return False, f"Compilation error:\n{error}"

    # Upload via bridge WRITE
    svc = get_bridge_service()
    file_path = f"{dest_path}/{program_name}"
    resp = await svc.write_file(file_path, binary)
    if not resp.success:
        return False, f"Upload failed: {resp.error_msg}"

    return True, file_path


async def compile_upload_and_run(
    source_code: str,
    program_name: str = "agent_prog",
    args: str = "",
) -> dict:
    """Full pipeline: compile -> upload -> execute on kernel.

    Returns dict with: success, output, exit_code, compile_error, path
    """
    from .service import get_bridge_service

    # Step 1: Compile
    binary, compile_error = await compile_c_source(source_code, program_name)
    if binary is None:
        return {
            "success": False,
            "compile_error": compile_error,
            "output": "",
            "exit_code": -1,
        }

    # Step 2: Upload
    svc = get_bridge_service()
    file_path = f"/disk/{program_name}"
    resp = await svc.write_file(file_path, binary)
    if not resp.success:
        return {
            "success": False,
            "compile_error": "",
            "output": f"Upload failed: {resp.error_msg}",
            "exit_code": -1,
        }

    # Step 3: Execute
    result = await svc.exec_program(file_path, args)
    exit_code = result.get("exit_code", -1)
    if isinstance(exit_code, str):
        exit_code = int(exit_code)

    return {
        "success": exit_code == 0,
        "compile_error": "",
        "output": result.get("output", ""),
        "exit_code": exit_code,
        "path": file_path,
        "size": len(binary),
    }
