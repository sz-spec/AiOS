"""
VOS3 Files API
==============

REST-style facade over the kernel VBus filesystem commands
(LS / LSM / STAT / UNLINK / RENAME / MKDIR), intended for the
File Explorer UI at the frontend.

All endpoints require an authenticated user (`get_current_user`)
and validate the kernel VFS path against a strict allowlist
regex to block path traversal.

Mounted under prefix `/api/v1/files` by `router_registry.py`.

Underlying transport: shared `kernel_bridge.service.get_bridge_service()`
(Unix-socket VBus client). Logic intentionally delegates instead of
duplicating the older `/api/kernel/...` routes.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import AuthenticatedUser, get_current_user
from kernel_bridge.service import get_bridge_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Path validation — mirrors api/kernel_routes.py
# ---------------------------------------------------------------------------

_SAFE_PATH_RE = re.compile(r"^/[a-zA-Z0-9_./-]+$")


def _validate_path(path: str) -> str:
    """Validate a VFS path. Rejects empty paths, `..` traversal, and any
    character outside the kernel-safe allowlist. Raises 400 on failure."""
    if not path or ".." in path or not _SAFE_PATH_RE.match(path):
        raise HTTPException(status_code=400, detail=f"Invalid path: {path}")
    return path


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RenameRequest(BaseModel):
    old_path: str = Field(..., max_length=256)
    new_path: str = Field(..., max_length=256)


class MkdirRequest(BaseModel):
    path: str = Field(..., max_length=256)


class CreateFileRequest(BaseModel):
    """Create a new (empty by default) file at `path`.

    Refusing to overwrite an existing file is opt-in via `overwrite=false`,
    which is the safer default for a UI flow."""

    path: str = Field(..., max_length=256)
    content: str = Field("", max_length=100_000)
    overwrite: bool = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
async def list_files(
    path: str = Query("/disk", max_length=256, description="Absolute kernel VFS path"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List entries in a directory on the kernel VFS.

    Returns each entry with `name`, `size`, and `type` (file/dir/unknown)
    via the kernel's LSM command.
    """
    _validate_path(path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")
    entries = await svc.list_dir_detailed(path)
    return {
        "path": path,
        "entries": entries,
        "count": len(entries),
    }


@router.get("/info")
async def file_info(
    path: str = Query(..., max_length=256),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get filesystem metadata for a single path (kernel STAT)."""
    _validate_path(path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")
    info = await svc.stat(path)
    if "error" in info:
        raise HTTPException(status_code=404, detail=info["error"])
    return {"path": path, **info}


@router.get("/content")
async def file_content(
    path: str = Query(..., max_length=256),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Read the UTF-8 content of a file from the kernel VFS (READ).

    Intended for small, structured payloads (JSON workflows, configs, etc.).
    Larger reads should use the chunked legacy endpoint.
    """
    _validate_path(path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")
    content = await svc.read_file(path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Cannot read {path}")
    return {"path": path, "content": content, "size": len(content)}


@router.delete("")
@router.delete("/")
async def delete_file(
    path: str = Query(..., max_length=256),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a file from the kernel VFS (UNLINK)."""
    _validate_path(path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")
    resp = await svc.unlink(path)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {"path": path, "deleted": True}


@router.patch("")
@router.patch("/")
async def rename_file(
    req: RenameRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Rename or move a file on the kernel VFS (RENAME)."""
    _validate_path(req.old_path)
    _validate_path(req.new_path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")
    resp = await svc.rename(req.old_path, req.new_path)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {"old_path": req.old_path, "new_path": req.new_path, "renamed": True}


@router.post("/mkdir")
async def create_directory(
    req: MkdirRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a directory on the kernel VFS (MKDIR)."""
    _validate_path(req.path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")
    resp = await svc.mkdir(req.path)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)
    return {"path": req.path, "created": True}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_file(
    req: CreateFileRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a new file on the kernel VFS (WRITE).

    Default behavior refuses to overwrite an existing file (returns 409).
    Pass `overwrite=true` to force-write. The kernel bridge has no native
    O_CREAT|O_EXCL semantics, so existence is checked via STAT before WRITE.
    """
    _validate_path(req.path)
    svc = get_bridge_service()
    if not svc.connected:
        raise HTTPException(status_code=503, detail="Kernel bridge not connected")

    if not req.overwrite:
        info = await svc.stat(req.path)
        if "error" not in info:
            raise HTTPException(
                status_code=409, detail=f"File already exists: {req.path}"
            )

    resp = await svc.write_file(req.path, req.content)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.error_msg)

    bytes_written = (
        int(resp.data) if resp.data and resp.data.isdigit() else len(req.content)
    )
    return {
        "path": req.path,
        "created": True,
        "bytes_written": bytes_written,
    }
