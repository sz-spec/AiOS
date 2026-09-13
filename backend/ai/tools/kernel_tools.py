"""
VOS3 Kernel Disk Tools
======================

LangChain @tool functions that let AI agents read/write files on the
VOS3 kernel's hardware-persistent VirtIO block device via the serial bridge.

All path operations validate input (no path traversal, no null bytes,
absolute paths only).  The tools are synchronous (required by LangGraph's
create_react_agent) but wrap the async KernelBridgeService using a
thread-pool dispatch when called from inside a running event loop.
"""

import asyncio
import threading
from langchain_core.tools import tool

from kernel_bridge.service import get_bridge_service

# Persistent event loop in a background thread.
# The KernelBridgeService singleton holds an asyncio.Lock and a socket
# connection that are bound to a specific event loop.  Using asyncio.run()
# per-call creates a new loop each time, leaving the Lock orphaned.
# Instead we keep ONE loop alive for all bridge operations.
_bridge_loop: asyncio.AbstractEventLoop | None = None
_bridge_lock = threading.Lock()


def _get_bridge_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent bridge event loop (created on first call)."""
    global _bridge_loop
    if _bridge_loop is not None and _bridge_loop.is_running():
        return _bridge_loop

    with _bridge_lock:
        if _bridge_loop is not None and _bridge_loop.is_running():
            return _bridge_loop
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        _bridge_loop = loop
        return loop


def _run_async(coro):
    """Run an async coroutine on the persistent bridge event loop.

    Thread-safe: works from any sync context (LangGraph tool, FastAPI
    handler, standalone script).
    """
    loop = _get_bridge_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=15.0)


# ------------------------------------------------------------------
# Input Validation (Phase I7)
# ------------------------------------------------------------------

# Allowed path prefixes for kernel VFS operations
_ALLOWED_PREFIXES = ("/disk/", "/disk", "/proc/", "/proc", "/tmp/", "/tmp")


def validate_kernel_path(path: str) -> str | None:
    """Validate a kernel VFS path.

    Rejects path traversal (..), null bytes, non-absolute paths, and
    paths not under allowed prefixes.

    Returns:
        Error string if invalid, None if valid.
    """
    if not path:
        return "Error: path is empty"
    if "\x00" in path:
        return "Error: path contains null byte"
    if not path.startswith("/"):
        return f"Error: path must be absolute (got '{path}')"
    if ".." in path:
        return f"Error: path traversal ('..') not allowed (got '{path}')"
    if not any(path.startswith(p) or path == p.rstrip("/") for p in _ALLOWED_PREFIXES):
        return f"Error: path must start with /disk/, /proc/, or /tmp/ (got '{path}')"
    return None


def sanitize_bridge_args(*args: str) -> str | None:
    """Sanitize bridge command arguments.

    Rejects pipe characters (command injection) and arguments exceeding
    256 bytes.

    Returns:
        Error string if invalid, None if valid.
    """
    for arg in args:
        if "|" in str(arg):
            return "Error: pipe character '|' not allowed in arguments"
        if len(str(arg)) > 256:
            return "Error: argument exceeds 256 byte limit"
    return None


def _validate_path(path: str) -> str | None:
    """Legacy wrapper. Validates path using the new validate_kernel_path."""
    return validate_kernel_path(path)


# ------------------------------------------------------------------
# Original 3 Tools
# ------------------------------------------------------------------


@tool
def list_kernel_disk(path: str = "/disk") -> str:
    """List files on the VOS3 kernel disk.

    Returns comma-separated filenames stored on the hardware-persistent
    VirtIO block device.  Use path='/disk' (default) for the root listing.

    Args:
        path: Directory path on the kernel VFS (must start with /disk)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        files = _run_async(bridge.list_dir(path))
        if not files:
            return f"No files found at {path}"
        return ", ".join(files)
    except Exception as e:
        return f"Error listing {path}: {e}"


@tool
def read_kernel_file(path: str) -> str:
    """Read a file from the VOS3 kernel disk.

    Returns the text content of a file stored on the hardware-persistent
    VirtIO block device.

    Args:
        path: File path on the kernel VFS (must start with /disk/)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        content = _run_async(bridge.read_file(path))
        if content is None:
            return f"Error: could not read {path} (file may not exist)"
        return content
    except Exception as e:
        return f"Error reading {path}: {e}"


@tool
def write_kernel_file(path: str, content: str) -> str:
    """Write a file to the VOS3 kernel disk.

    Stores text content on the hardware-persistent VirtIO block device.
    Files written here survive cold reboots.

    Args:
        path: File path on the kernel VFS (must start with /disk/)
        content: Text content to write
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        resp = _run_async(bridge.write_file(path, content))
        if resp.success:
            return f"Successfully wrote {len(content)} bytes to {path}"
        else:
            return f"Error writing {path}: {resp.error_msg}"
    except Exception as e:
        return f"Error writing {path}: {e}"


# ------------------------------------------------------------------
# New 8 Tools (Phase H2.3)
# ------------------------------------------------------------------


@tool
def stat_kernel_disk(path: str) -> str:
    """Get file/directory statistics from the VOS3 kernel disk.

    Returns metadata (size, type, permissions) for a file or directory.

    Args:
        path: Path on the kernel VFS (must start with /disk/)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        info = _run_async(bridge.stat(path))
        if "error" in info:
            return f"Error getting stat for {path}: {info['error']}"
        parts = [f"{k}={v}" for k, v in info.items()]
        return f"Stat for {path}: {', '.join(parts)}"
    except Exception as e:
        return f"Error getting stat for {path}: {e}"


@tool
def mkdir_kernel_disk(path: str) -> str:
    """Create a directory on the VOS3 kernel disk.

    Creates the specified directory on the hardware-persistent VirtIO
    block device.

    Args:
        path: Directory path to create (must start with /disk/)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        resp = _run_async(bridge.mkdir(path))
        if resp.success:
            return f"Successfully created directory {path}"
        else:
            return f"Error creating directory {path}: {resp.error_msg}"
    except Exception as e:
        return f"Error creating directory {path}: {e}"


@tool
def rmdir_kernel_disk(path: str) -> str:
    """Remove a directory from the VOS3 kernel disk.

    Removes the specified directory. The directory must be empty.

    Args:
        path: Directory path to remove (must start with /disk/)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        resp = _run_async(bridge.rmdir(path))
        if resp.success:
            return f"Successfully removed directory {path}"
        else:
            return f"Error removing directory {path}: {resp.error_msg}"
    except Exception as e:
        return f"Error removing directory {path}: {e}"


@tool
def delete_kernel_file(path: str) -> str:
    """Delete a file from the VOS3 kernel disk.

    Permanently removes a file from the hardware-persistent VirtIO
    block device.

    Args:
        path: File path to delete (must start with /disk/)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        resp = _run_async(bridge.unlink(path))
        if resp.success:
            return f"Successfully deleted {path}"
        else:
            return f"Error deleting {path}: {resp.error_msg}"
    except Exception as e:
        return f"Error deleting {path}: {e}"


@tool
def rename_kernel_file(old_path: str, new_path: str) -> str:
    """Rename or move a file on the VOS3 kernel disk.

    Renames or moves a file within the hardware-persistent VirtIO
    block device.

    Args:
        old_path: Current file path (must start with /disk/)
        new_path: New file path (must start with /disk/)
    """
    err = _validate_path(old_path)
    if err:
        return err
    err = _validate_path(new_path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        resp = _run_async(bridge.rename(old_path, new_path))
        if resp.success:
            return f"Successfully renamed {old_path} -> {new_path}"
        else:
            return f"Error renaming {old_path}: {resp.error_msg}"
    except Exception as e:
        return f"Error renaming {old_path}: {e}"


@tool
def list_kernel_disk_detailed(path: str = "/disk") -> str:
    """List files on the VOS3 kernel disk with detailed info.

    Returns file listing with name, size, and type for each entry.

    Args:
        path: Directory path on the kernel VFS (must start with /disk)
    """
    err = _validate_path(path)
    if err:
        return err

    try:
        bridge = get_bridge_service()
        entries = _run_async(bridge.list_dir_detailed(path))
        if not entries:
            return f"No files found at {path}"
        lines = []
        for e in entries:
            lines.append(f"  {e['name']}  (size={e['size']}, type={e['type']})")
        return f"Contents of {path}:\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing {path}: {e}"


@tool
def get_kernel_sysinfo() -> str:
    """Get VOS3 kernel system information.

    Returns system metrics including uptime, memory usage, and task count.
    """
    try:
        bridge = get_bridge_service()
        info = _run_async(bridge.sysinfo())
        if "error" in info:
            return f"Error getting sysinfo: {info['error']}"
        parts = [f"{k}={v}" for k, v in info.items()]
        return f"VOS3 System Info: {', '.join(parts)}"
    except Exception as e:
        return f"Error getting sysinfo: {e}"


@tool
def get_kernel_processes() -> str:
    """Get list of running processes on the VOS3 kernel.

    Returns PID, name, state, and parent PID for each running process.
    """
    try:
        bridge = get_bridge_service()
        procs = _run_async(bridge.procs())
        if not procs:
            return "No processes found (or bridge not connected)"
        lines = []
        for p in procs:
            lines.append(
                f"  PID={p['pid']} name={p['name']} "
                f"state={p['state']} ppid={p['ppid']}"
            )
        return f"Running processes ({len(procs)}):\n" + "\n".join(lines)
    except Exception as e:
        return f"Error getting processes: {e}"
