"""
VOS3 Kernel App Manager
========================

Manages kernel app lifecycle (load, status, kill) via the serial bridge.
Wraps the APPLOAD, APPSTAT, APPKILL bridge commands.

Phase N: Kernel App Runtime & Isolation.
"""

import logging
from typing import Optional

from kernel_bridge.service import get_bridge_service
from kernel_bridge.protocol import (
    parse_kv_response,
)

logger = logging.getLogger("vos3.kernel_app_manager")


class KernelAppManager:
    """Manages kernel-side app contexts via the serial bridge.

    Uses the bridge commands:
      - APPLOAD|app_id|binary_path  -> create context + (stub) load ELF
      - APPSTAT|app_id              -> get memory/CPU/status info
      - APPKILL|app_id              -> terminate tasks + destroy context

    PIDs 0-7 are managed here (kernel bridge). PIDs 8-255 are managed by
    the AgentRegistry in vos/engine.py for dynamic AI agent allocation.
    """

    KERNEL_MAX_ID = 7

    def __init__(self):
        self._bridge = get_bridge_service()

    async def load_app(self, app_id: int, binary_path: str = "") -> dict:
        """Load an app into a kernel app context.

        Creates the per-app AI guard context and (in future) loads the
        ELF binary. Currently a stub that just creates the context.

        Args:
            app_id: Application ID (0-7).
            binary_path: Path to binary on the kernel VFS (optional).

        Returns:
            dict with app_id, status, and binary info, or error.
        """
        if not 0 <= app_id <= 7:
            return {"error": "app_id must be 0-7"}

        args = [str(app_id)]
        if binary_path:
            args.append(binary_path)

        resp = await self._bridge._send_command("APPLOAD", *args)
        if not resp.success:
            return {"error": resp.error_msg, "error_code": resp.error_code}
        return parse_kv_response(resp.data)

    async def get_app_status(self, app_id: int) -> dict:
        """Get status information for a kernel app.

        Returns memory usage, region count, CPU ticks, task count,
        and whether the app context is currently active.

        Args:
            app_id: Application ID (0-7).

        Returns:
            dict with app_id, mem_used, regions, cpu_ticks, tasks, active.
        """
        if not 0 <= app_id <= 7:
            return {"error": "app_id must be 0-7"}

        resp = await self._bridge._send_command("APPSTAT", str(app_id))
        if not resp.success:
            return {"error": resp.error_msg, "error_code": resp.error_code}
        return parse_kv_response(resp.data)

    async def kill_app(self, app_id: int) -> dict:
        """Kill a kernel app and destroy its context.

        Terminates all tasks with the given app_id and destroys the
        per-app AI guard context, freeing all associated memory.

        Args:
            app_id: Application ID (0-7).

        Returns:
            dict with app_id, killed (task count), ctx_destroyed.
        """
        if not 0 <= app_id <= 7:
            return {"error": "app_id must be 0-7"}

        resp = await self._bridge._send_command("APPKILL", str(app_id))
        if not resp.success:
            return {"error": resp.error_msg, "error_code": resp.error_code}
        return parse_kv_response(resp.data)


# Singleton
_manager: Optional[KernelAppManager] = None


def get_kernel_app_manager() -> KernelAppManager:
    """Get or create the singleton kernel app manager."""
    global _manager
    if _manager is None:
        _manager = KernelAppManager()
    return _manager
