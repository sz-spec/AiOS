"""
VOS3 Kernel Bridge API Routes
===============================

REST endpoints for communicating with the VOS3 kernel
over the serial bridge (COM2 via Unix domain socket).

Supports all 15 bridge commands: PING, STAT, WRITE, READ, LS,
EXEC, MKDIR, UNLINK, RENAME, SYSINFO, PROCS, HTTPGET,
APPLOAD, APPSTAT, APPKILL.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
import re

from api.deps import get_current_user, AuthenticatedUser
from kernel_bridge.service import get_bridge_service

router = APIRouter()

# ============================================================================
# REQUEST MODELS
# ============================================================================


class WriteRequest(BaseModel):
    path: str = Field(..., max_length=256)
    content: str = Field(..., max_length=100_000)


class ExecRequest(BaseModel):
    path: str = Field(..., max_length=256)
    args: str = Field("", max_length=4096)


class RenameRequest(BaseModel):
    old_path: str = Field(..., max_length=256)
    new_path: str = Field(..., max_length=256)


class CompileAndRunRequest(BaseModel):
    """Compile C source code and execute on the kernel."""

    source_code: str = Field(..., max_length=100_000)
    program_name: str = Field("agent_prog", max_length=64)
    args: str = Field("", max_length=4096)


class AppLoadRequest(BaseModel):
    """Load an app into a kernel app context."""

    app_id: int = Field(..., ge=0, le=7)
    binary_path: str = Field("", max_length=256)


# ============================================================================
# PATH VALIDATION
# ============================================================================

_SAFE_PATH_RE = re.compile(r"^/[a-zA-Z0-9_./-]+$")


def _validate_path(path: str) -> str:
    """Validate a VFS path to prevent path traversal."""
    if not path or ".." in path or not _SAFE_PATH_RE.match(path):
        raise HTTPException(status_code=400, detail=f"Invalid path: {path}")
    return path


# ============================================================================
# SYSTEM STATUS
# ============================================================================


@router.get("/status")
async def kernel_status(user: AuthenticatedUser = Depends(get_current_user)):
    """Check kernel bridge connectivity and get system info."""
    svc = get_bridge_service()
    resp = await svc.ping()
    info = {}
    if resp.success:
        info = await svc.sysinfo()
    return {
        "connected": svc.connected,
        "ping": resp.success,
        "data": resp.data if resp.success else resp.error_msg,
        "sysinfo": info,
    }


@router.post("/ping")
async def kernel_ping(user: AuthenticatedUser = Depends(get_current_user)):
    """Send PING to kernel, expect PONG."""
    svc = get_bridge_service()
    resp = await svc.ping()
    if not resp.success:
        return {"success": False, "error": resp.error_msg}
    return {"success": True, "response": resp.data}


@router.get("/sysinfo")
async def kernel_sysinfo(user: AuthenticatedUser = Depends(get_current_user)):
    """Get system information (uptime, memory, task count)."""
    svc = get_bridge_service()
    info = await svc.sysinfo()
    if "error" in info:
        raise HTTPException(status_code=502, detail=info["error"])
    return info


# ============================================================================
# PROCESS MANAGEMENT
# ============================================================================


@router.get("/processes")
async def kernel_processes(user: AuthenticatedUser = Depends(get_current_user)):
    """Get list of running processes."""
    svc = get_bridge_service()
    procs = await svc.procs()
    return {"processes": procs, "count": len(procs)}


@router.post("/execute")
async def kernel_execute(
    req: ExecRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Execute a binary on the kernel."""
    _validate_path(req.path)
    svc = get_bridge_service()
    result = await svc.exec_program(req.path, req.args)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


# ============================================================================
# FILESYSTEM
# ============================================================================


@router.get("/filesystem")
async def kernel_filesystem(
    path: str = Query("/disk"), user: AuthenticatedUser = Depends(get_current_user)
):
    """Get filesystem listing and statistics."""
    _validate_path(path)
    svc = get_bridge_service()
    files = await svc.list_dir(path)
    stat = await svc.stat(path)
    return {
        "path": path,
        "files": files,
        "count": len(files),
        "stat": stat if "error" not in stat else None,
    }


@router.get("/file")
async def kernel_read_file(
    path: str = Query(...), user: AuthenticatedUser = Depends(get_current_user)
):
    """Read a file from the kernel VFS."""
    _validate_path(path)
    svc = get_bridge_service()
    content = await svc.read_file(path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Cannot read {path}")
    return {"path": path, "content": content, "size": len(content)}


@router.post("/file")
async def kernel_write_file(
    req: WriteRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Write content to a file on the kernel VFS."""
    _validate_path(req.path)
    svc = get_bridge_service()
    resp = await svc.write_file(req.path, req.content)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {
        "path": req.path,
        "bytes_written": int(resp.data) if resp.data.isdigit() else 0,
        "success": True,
    }


@router.delete("/file")
async def kernel_delete_file(
    path: str = Query(...), user: AuthenticatedUser = Depends(get_current_user)
):
    """Delete a file from the kernel VFS."""
    _validate_path(path)
    svc = get_bridge_service()
    resp = await svc.unlink(path)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {"path": path, "deleted": True}


@router.post("/mkdir")
async def kernel_mkdir(
    path: str = Query(...), user: AuthenticatedUser = Depends(get_current_user)
):
    """Create a directory on the kernel VFS."""
    _validate_path(path)
    svc = get_bridge_service()
    resp = await svc.mkdir(path)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {"path": path, "created": True}


@router.post("/rename")
async def kernel_rename(
    req: RenameRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Rename/move a file on the kernel VFS."""
    _validate_path(req.old_path)
    _validate_path(req.new_path)
    svc = get_bridge_service()
    resp = await svc.rename(req.old_path, req.new_path)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {"old_path": req.old_path, "new_path": req.new_path, "renamed": True}


# ============================================================================
# LEGACY DISK ROUTES (backward compatibility)
# ============================================================================


@router.get("/disk/stat")
async def disk_stat(
    path: str = "/disk", user: AuthenticatedUser = Depends(get_current_user)
):
    """Get filesystem statistics for a mount point."""
    _validate_path(path)
    svc = get_bridge_service()
    result = await svc.stat(path)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/disk/ls")
async def disk_ls(
    path: str = "/disk", user: AuthenticatedUser = Depends(get_current_user)
):
    """List files in a directory on the kernel VFS."""
    _validate_path(path)
    svc = get_bridge_service()
    files = await svc.list_dir(path)
    return {"path": path, "files": files, "count": len(files)}


@router.get("/disk/read")
async def disk_read(
    path: str = Query(...), user: AuthenticatedUser = Depends(get_current_user)
):
    """Read a file from the kernel VFS."""
    _validate_path(path)
    svc = get_bridge_service()
    content = await svc.read_file(path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Cannot read {path}")
    return {"path": path, "content": content, "size": len(content)}


@router.post("/disk/write")
async def disk_write(
    req: WriteRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Write content to a file on the kernel VFS."""
    _validate_path(req.path)
    svc = get_bridge_service()
    resp = await svc.write_file(req.path, req.content)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {
        "path": req.path,
        "bytes_written": int(resp.data) if resp.data.isdigit() else 0,
        "success": True,
    }


# ============================================================================
# COMPILE AND RUN
# ============================================================================


@router.post("/compile-and-run")
async def kernel_compile_and_run(
    req: CompileAndRunRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Compile C source code and execute on the VOS3 kernel.

    Full pipeline: compile (host) -> upload (bridge WRITE) -> execute (bridge EXEC).
    """
    try:
        from kernel_bridge.compiler import compile_upload_and_run
    except ImportError:
        raise HTTPException(status_code=501, detail="Compiler not available")

    result = await compile_upload_and_run(
        source_code=req.source_code,
        program_name=req.program_name,
        args=req.args,
    )
    return result


# ============================================================================
# KERNEL APP MANAGEMENT (Phase N)
# ============================================================================


@router.post("/apps/load")
async def kernel_app_load(
    req: AppLoadRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Load an app into a kernel app context.

    Creates a per-app AI guard context and (in future) loads the ELF binary.
    """
    from services.kernel_app_manager import get_kernel_app_manager

    mgr = get_kernel_app_manager()
    result = await mgr.load_app(req.app_id, req.binary_path)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/apps/{app_id}/status")
async def kernel_app_status(
    app_id: int, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get status information for a kernel app."""
    if not 0 <= app_id <= 7:
        raise HTTPException(status_code=400, detail="app_id must be 0-7")
    from services.kernel_app_manager import get_kernel_app_manager

    mgr = get_kernel_app_manager()
    result = await mgr.get_app_status(app_id)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.post("/apps/{app_id}/kill")
async def kernel_app_kill(
    app_id: int, user: AuthenticatedUser = Depends(get_current_user)
):
    """Kill a kernel app and destroy its context."""
    if not 0 <= app_id <= 7:
        raise HTTPException(status_code=400, detail="app_id must be 0-7")
    from services.kernel_app_manager import get_kernel_app_manager

    mgr = get_kernel_app_manager()
    result = await mgr.kill_app(app_id)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


# ============================================================================
# HTTP PROXY
# ============================================================================


@router.get("/http-proxy")
async def kernel_http_proxy(
    url: str = Query(...), user: AuthenticatedUser = Depends(get_current_user)
):
    """Fetch a URL via host-side HTTP proxy."""
    svc = get_bridge_service()
    content = await svc.http_get(url)
    if content is None:
        raise HTTPException(status_code=502, detail=f"Failed to fetch {url}")
    return {"url": url, "content": content, "size": len(content)}
