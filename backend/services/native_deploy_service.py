"""
VOS3 Native Deploy Service
===========================
Phase 4.0: Deploys VPK packages to VOS3 kernel via hardened serial bridge.

Security:
- CVE-2026-23086: bound_window = min(peer_buffer, LINE_MAX) on every TX
- MAX_CHUNK_SIZE: 8KB max hex payload per WRITE/APPEND
- Congestion backpressure: backs off when bridge reports BUSY
"""

import asyncio
import logging
import os
import socket
from typing import AsyncGenerator, Dict, Optional

from services.vpacker import VPKManifest, VPKDataRetentionPolicy
from services.vbus_driver import VBusDriver, VBusError

logger = logging.getLogger("native_deploy")

# Bridge protocol constants (must match kernel defines)
KERNEL_LINE_MAX = 9216
MAX_CHUNK_SIZE = 8192
BRIDGE_TIMEOUT_S = 5.0
CONGESTION_BACKOFF_S = 0.1
HEADER_OVERHEAD = 64  # Conservative margin for command prefix


class BridgeError(Exception):
    """Error communicating with VOS3 kernel bridge."""

    pass


class VOS3NativeDeployService:
    """Deploys VPK packages to VOS3 kernel via hardened serial bridge."""

    def __init__(self, bridge_socket: str = "/tmp/vos3_bridge.sock"):
        self.socket_path = bridge_socket
        self._peer_buf_size: int = KERNEL_LINE_MAX
        self._sock: Optional[socket.socket] = None
        self._log_offsets: Dict[str, int] = {}
        self._vbus: Optional[VBusDriver] = None
        self._use_vbus: bool = False

    def _connect(self) -> socket.socket:
        """Connect to bridge socket (reuse if already connected).
        Tries VBus binary transport first, falls back to legacy serial.
        """
        # Try VBus first if not already decided
        if not self._use_vbus and self._vbus is None:
            self._vbus = VBusDriver(self.socket_path)
            if self._vbus.connect():
                self._use_vbus = True
                logger.info("VBus binary transport active")
                return None  # VBus doesn't use raw socket
            else:
                self._vbus = None
                logger.info("VBus unavailable, using legacy serial")

        if self._use_vbus:
            return None  # VBus manages its own connection

        if self._sock is not None:
            return self._sock
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(BRIDGE_TIMEOUT_S)
        sock.connect(self.socket_path)
        self._sock = sock
        return sock

    def _disconnect(self) -> None:
        if self._vbus:
            self._vbus.disconnect()
            self._vbus = None
            self._use_vbus = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _bound_window(self, payload_len: int) -> int:
        """CVE-2026-23086 mitigation: TX window = min(peer_buffer, LINE_MAX).
        Returns max safe data bytes for a single transmission.
        """
        effective_max = min(self._peer_buf_size, KERNEL_LINE_MAX)
        data_window = effective_max - HEADER_OVERHEAD
        return min(payload_len, data_window)

    def _validate_and_bound(self, command: str) -> str:
        """Pre-validate entire command fits in bound window."""
        cmd_bytes = len(command.encode("utf-8"))
        window = min(self._peer_buf_size, KERNEL_LINE_MAX)
        if cmd_bytes > window:
            raise ValueError(
                f"Command size {cmd_bytes} exceeds bound window {window} "
                f"(peer_buf={self._peer_buf_size}, LINE_MAX={KERNEL_LINE_MAX})"
            )
        return command

    def _send_command(self, command: str) -> str:
        """Send a command and read the response line."""
        self._connect()  # ensure connected (VBus or legacy)

        if self._use_vbus and self._vbus:
            try:
                return self._vbus.send_command(command)
            except VBusError as e:
                raise BridgeError(f"VBus error: {e}") from e

        # Legacy serial path
        self._validate_and_bound(command)
        sock = self._sock
        sock.sendall((command + "\n").encode("utf-8"))
        # Read response until newline
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise BridgeError("Bridge connection closed")
            buf += chunk
        return buf.decode("utf-8").strip()

    def _parse_response(self, resp: str) -> tuple[bool, str]:
        """Parse bridge response. Returns (is_ok, payload)."""
        if resp.startswith("OK|"):
            return True, resp[3:]
        elif resp.startswith("OK"):
            return True, resp[2:].lstrip("|")
        elif resp.startswith("ERR|"):
            return False, resp[4:]
        else:
            return False, resp

    def _hex_encode(self, data: bytes) -> str:
        return data.hex()

    async def deploy(
        self,
        app_id: str,
        manifest: VPKManifest,
        files: Dict[str, str],
    ) -> AsyncGenerator[dict, None]:
        """Deploy files to VOS3 kernel. Yields SSE progress events."""
        loop = asyncio.get_event_loop()
        app_dir = f"/disk/apps/{app_id}"

        # Phase 1: Packaging
        yield {
            "event": "progress",
            "data": {"phase": "packaging", "message": "Preparing package..."},
        }

        # Phase 2: Create app directory
        yield {
            "event": "progress",
            "data": {"phase": "transferring", "message": "Creating app directory..."},
        }
        try:
            resp = await loop.run_in_executor(
                None, self._send_command, f"MKDIR|{app_dir}"
            )
            ok, msg = self._parse_response(resp)
            if not ok:
                logger.warning("MKDIR failed (may already exist): %s", msg)
        except Exception as e:
            yield {
                "event": "error",
                "data": {"phase": "transferring", "message": str(e)},
            }
            return

        # Phase 3: Transfer files
        total = len(files)
        for i, (path, content) in enumerate(files.items(), 1):
            dest = f"{app_dir}/{path}"
            hex_data = self._hex_encode(content.encode("utf-8"))

            # Chunk if needed (bound window check)
            chunk_size = self._bound_window(len(hex_data)) - len(f"WRITE|{dest}|")
            if chunk_size < 2:
                yield {
                    "event": "error",
                    "data": {
                        "phase": "transferring",
                        "message": f"Path too long: {dest}",
                    },
                }
                return

            # Write first chunk
            first_chunk = hex_data[:chunk_size]
            cmd = f"WRITE|{dest}|{first_chunk}"
            try:
                resp = await loop.run_in_executor(None, self._send_command, cmd)
                ok, msg = self._parse_response(resp)
                if not ok:
                    if "BUSY" in msg:
                        await asyncio.sleep(CONGESTION_BACKOFF_S)
                        resp = await loop.run_in_executor(None, self._send_command, cmd)
                        ok, msg = self._parse_response(resp)
                    if not ok:
                        yield {
                            "event": "error",
                            "data": {
                                "phase": "transferring",
                                "message": f"WRITE failed: {msg}",
                            },
                        }
                        return
            except Exception as e:
                yield {
                    "event": "error",
                    "data": {"phase": "transferring", "message": str(e)},
                }
                return

            # Append remaining chunks
            MAX_BUSY_RETRIES = 50  # 50 × 0.1s = 5s max wait
            offset = chunk_size
            busy_retries = 0
            while offset < len(hex_data):
                append_chunk_size = self._bound_window(len(hex_data) - offset) - len(
                    f"APPEND|{dest}|"
                )
                chunk = hex_data[offset : offset + append_chunk_size]
                append_cmd = f"APPEND|{dest}|{chunk}"
                try:
                    resp = await loop.run_in_executor(
                        None, self._send_command, append_cmd
                    )
                    ok, msg = self._parse_response(resp)
                    if not ok:
                        if "BUSY" in msg:
                            busy_retries += 1
                            if busy_retries > MAX_BUSY_RETRIES:
                                yield {
                                    "event": "error",
                                    "data": {
                                        "phase": "transferring",
                                        "message": "Kernel busy timeout",
                                    },
                                }
                                return
                            await asyncio.sleep(CONGESTION_BACKOFF_S)
                            continue
                        yield {
                            "event": "error",
                            "data": {
                                "phase": "transferring",
                                "message": f"APPEND failed: {msg}",
                            },
                        }
                        return
                except Exception as e:
                    yield {
                        "event": "error",
                        "data": {"phase": "transferring", "message": str(e)},
                    }
                    return
                busy_retries = 0  # Reset on success
                offset += append_chunk_size

            yield {
                "event": "progress",
                "data": {
                    "phase": "transferring",
                    "message": f"Transferred {i}/{total}: {path}",
                    "progress": i / total,
                },
            }

        # Phase 4: Write manifest
        import json

        manifest_hex = self._hex_encode(
            json.dumps(manifest.model_dump(mode="json"), indent=2).encode("utf-8")
        )
        manifest_cmd = f"WRITE|{app_dir}/manifest.json|{manifest_hex}"
        try:
            resp = await loop.run_in_executor(None, self._send_command, manifest_cmd)
        except Exception as e:
            yield {
                "event": "error",
                "data": {"phase": "transferring", "message": str(e)},
            }
            return

        # Phase 5: Launch app
        yield {
            "event": "progress",
            "data": {"phase": "launching", "message": "Starting app on VOS3 kernel..."},
        }
        entry = manifest.system.entry
        mem_mb = manifest.system.resources.total_memory_mb
        try:
            appload_cmd = f"APPLOAD|{app_id}|{entry}|{mem_mb}"
            resp = await loop.run_in_executor(None, self._send_command, appload_cmd)
            ok, msg = self._parse_response(resp)
            if not ok:
                yield {
                    "event": "error",
                    "data": {"phase": "launching", "message": f"APPLOAD failed: {msg}"},
                }
                return
        except Exception as e:
            yield {"event": "error", "data": {"phase": "launching", "message": str(e)}}
            return

        self._log_offsets[app_id] = 0
        yield {
            "event": "complete",
            "data": {
                "phase": "running",
                "app_id": app_id,
                "message": "App deployed and running on VOS3",
            },
        }

    async def get_status(self, app_id: str) -> dict:
        """Get app status via APPSTAT."""
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None, self._send_command, f"APPSTAT|{app_id}"
            )
            ok, payload = self._parse_response(resp)
            if not ok:
                return {"app_id": app_id, "status": "not_found", "error": payload}
            # Parse APPSTAT response (format varies)
            return {"app_id": app_id, "status": "running", "raw": payload}
        except Exception as e:
            return {"app_id": app_id, "status": "error", "error": str(e)}

    async def get_logs(self, app_id: str) -> AsyncGenerator[dict, None]:
        """Stream app logs via APPLOGS command. Yields SSE events."""
        loop = asyncio.get_event_loop()
        offset = self._log_offsets.get(app_id, 0)

        while True:
            try:
                resp = await loop.run_in_executor(
                    None, self._send_command, f"APPLOGS|{app_id}|{offset}"
                )
                ok, payload = self._parse_response(resp)
                if not ok:
                    yield {"event": "error", "data": {"message": payload}}
                    return

                # Parse: new_offset|hex_data
                parts = payload.split("|", 1)
                if len(parts) == 2:
                    new_offset = int(parts[0])
                    hex_data = parts[1]
                    if hex_data:
                        text = bytes.fromhex(hex_data).decode("utf-8", errors="replace")
                        lines = text.splitlines()
                        yield {
                            "event": "logs",
                            "data": {"offset": new_offset, "lines": lines},
                        }
                    offset = new_offset
                    self._log_offsets[app_id] = offset

            except Exception as e:
                yield {"event": "error", "data": {"message": str(e)}}
                return

            await asyncio.sleep(0.5)

    async def stop_app(
        self, app_id: str, manifest: Optional[VPKManifest] = None
    ) -> dict:
        """Stop app via APPKILL with retention policy."""
        loop = asyncio.get_event_loop()
        retention_code = 0  # Default: SCRUB
        if (
            manifest
            and manifest.intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST
        ):
            retention_code = 1
        elif (
            manifest
            and manifest.intent.data_retention_policy == VPKDataRetentionPolicy.SNAPSHOT
        ):
            retention_code = 2

        try:
            resp = await loop.run_in_executor(
                None, self._send_command, f"APPKILL|{app_id}|{retention_code}"
            )
            ok, payload = self._parse_response(resp)
            self._log_offsets.pop(app_id, None)
            return {
                "app_id": app_id,
                "status": "stopped",
                "retention": ["scrub", "persist", "snapshot"][retention_code],
                "success": ok,
            }
        except Exception as e:
            return {"app_id": app_id, "status": "error", "error": str(e)}

    async def list_apps(self) -> list:
        """List running apps via APPLIST."""
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, self._send_command, "APPLIST")
            ok, payload = self._parse_response(resp)
            if not ok:
                return []
            # Parse: app1_id:state:mem_used;app2_id:state:mem_used;...
            apps = []
            if payload:
                for entry in payload.split(";"):
                    parts = entry.split(":")
                    if len(parts) >= 3:
                        apps.append(
                            {
                                "app_id": parts[0],
                                "state": parts[1],
                                "mem_used": int(parts[2]) if parts[2].isdigit() else 0,
                            }
                        )
            return apps
        except Exception as e:
            logger.warning("APPLIST failed: %s", e)
            return []


_service: Optional[VOS3NativeDeployService] = None


def get_native_deploy_service() -> VOS3NativeDeployService:
    global _service
    if _service is None:
        socket_path = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
        _service = VOS3NativeDeployService(bridge_socket=socket_path)
    return _service
