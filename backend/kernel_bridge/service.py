"""
VOS3 Kernel Bridge Service
===========================

Async service that communicates with the VOS3 kernel over a Unix domain socket
connected to QEMU's COM2 serial port.

Critical: The socket connection must be PERSISTENT. QEMU drops data when
no client is connected (chardev shows "disconnected"). Connect once, hold
the connection open, and only reconnect on error.

Supports 19 commands: PING, STAT, WRITE, READ, READC, APPEND, LS, LSM,
EXEC, MKDIR, RMDIR, UNLINK, RENAME, SYSINFO, PROCS, HTTPGET,
APPLOAD, APPSTAT, APPKILL.
"""

import asyncio
import ipaddress
import logging
import os
import socket
from typing import Optional
from urllib.parse import urlparse

from .circuit_breaker import CircuitBreaker
from .protocol import (
    CMD_PING,
    CMD_STAT,
    CMD_WRITE,
    CMD_READ,
    CMD_LS,
    CMD_EXEC,
    CMD_MKDIR,
    CMD_UNLINK,
    CMD_RENAME,
    CMD_SYSINFO,
    CMD_PROCS,
    CMD_READC,
    CMD_APPEND,
    CMD_LSM,
    CMD_RMDIR,
    CMD_APPLOAD,
    CMD_APPSTAT,
    CMD_APPKILL,
    BridgeResponse,
    format_command,
    parse_response,
    parse_kv_response,
    parse_procs_response,
    hex_encode,
    hex_decode,
)

logger = logging.getLogger("vos3.bridge")

SOCKET_PATH = "/tmp/vos3_bridge.sock"

_VERIFY_TLS = os.getenv("VOS3_BRIDGE_VERIFY_TLS", "true").lower() != "false"


# ------------------------------------------------------------------
# SSRF Protection (Pass 7)
# ------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / IMDS
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.ip_network("0.0.0.0/8"),  # "This" network
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def validate_url(url: str) -> tuple:
    """Validate a URL against SSRF attacks with DNS pinning.

    Phase v17 (B-H1): Returns (resolved_ip, original_host) tuple so callers
    can connect directly to the resolved IP with a Host header, defeating
    DNS rebinding attacks (TOCTOU between resolve and connect).

    Raises ValueError if the URL targets a blocked network or scheme.
    """
    parsed = urlparse(url)

    # Scheme whitelist (Resilience-Matrix F11 — HTTPS-only outbound under
    # any non-community profile; community keeps `http` permitted to ease
    # local development against plain test fixtures).
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    if parsed.scheme == "http":
        try:
            from vos_profile import is_community

            if not is_community():
                raise ValueError(
                    "Blocked scheme http:// outside community profile "
                    "(VOS_PROFILE != community); use https://"
                )
        except ImportError:
            # vos_profile unavailable — default-deny http:// for safety.
            raise ValueError("Blocked scheme http:// (default-deny)")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("No hostname in URL")

    # Resolve hostname to IP — pin the result for subsequent connection
    try:
        infos = socket.getaddrinfo(
            hostname, parsed.port or 443, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed: {e}") from e

    resolved_ip = None
    for family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise ValueError(f"Blocked private/reserved IP: {ip}")
        if resolved_ip is None:
            resolved_ip = str(ip)

    if resolved_ip is None:
        raise ValueError("DNS resolution returned no addresses")

    return (resolved_ip, hostname)


RESPONSE_TIMEOUT = 5.0  # seconds
POST_CONNECT_DELAY = 1.0  # seconds — let QEMU wire the chardev


class KernelBridgeService:
    """Async client for the VOS3 kernel serial bridge.

    Maintains a persistent connection to the QEMU unix socket.
    The kernel's bridge task busy-polls COM2, so commands get immediate
    response once the socket is wired.
    """

    def __init__(self, socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._connected = False
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

    async def connect(self) -> bool:
        """Connect to the kernel bridge socket.

        Waits POST_CONNECT_DELAY after connecting to let QEMU fully
        wire the chardev to the guest UART before sending any data.
        """
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self.socket_path
            )
            self._connected = True
            logger.info("Connected to kernel bridge at %s", self.socket_path)

            # Critical: wait for QEMU to wire the chardev backend to the
            # guest serial device. Without this delay, early commands are
            # silently dropped.
            await asyncio.sleep(POST_CONNECT_DELAY)
            logger.info("Post-connect delay complete, bridge ready")

            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            logger.warning("Cannot connect to kernel bridge: %s", e)
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from the kernel bridge socket."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def circuit_state(self) -> str:
        """Current circuit breaker state: CLOSED, OPEN, or HALF_OPEN."""
        return self._circuit_breaker.state

    async def _send_command(self, *parts: str) -> BridgeResponse:
        """Send a command and wait for response.

        Auto-reconnects on failure. The connection is kept open between
        commands (persistent) to avoid QEMU chardev disconnect.

        Circuit breaker: rejects calls when OPEN (too many transport
        failures). Kernel ERR responses do NOT trip the breaker —
        only transport errors (timeout, disconnect, broken pipe) do.
        """
        # Circuit breaker fast-reject (outside lock to avoid holding it)
        if self._circuit_breaker.is_open:
            import time

            elapsed = time.time() - (self._circuit_breaker.last_failure_time or 0)
            if elapsed < self._circuit_breaker.recovery_timeout:
                return BridgeResponse(
                    success=False, error_code=-2, error_msg="circuit breaker OPEN"
                )
            # Timeout elapsed — transition to HALF_OPEN, allow one probe
            self._circuit_breaker.state = "HALF_OPEN"
            self._circuit_breaker.half_open_successes = 0

        async with self._lock:
            # Reconnect if not connected
            if not self._connected:
                if not await self.connect():
                    self._circuit_breaker._on_failure()
                    return BridgeResponse(
                        success=False, error_code=-1, error_msg="bridge not connected"
                    )

            try:
                cmd = format_command(*parts)
                self._writer.write(cmd)
                await self._writer.drain()

                # Read response line with timeout
                raw = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=RESPONSE_TIMEOUT,
                )
                line = raw.decode("ascii", errors="replace")
                resp = parse_response(line)

                if resp.success:
                    self._circuit_breaker._on_success()
                # Kernel ERR responses are protocol-level, not transport
                # failures — don't trip the circuit breaker.

                return resp

            except (
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
                BrokenPipeError,
            ) as e:
                logger.warning("Bridge communication error: %s", e)
                self._circuit_breaker._on_failure()
                await self.disconnect()
                return BridgeResponse(
                    success=False, error_code=-1, error_msg=f"communication error: {e}"
                )

    # ------------------------------------------------------------------
    # High-level API — Original 5 commands
    # ------------------------------------------------------------------

    async def ping(self) -> BridgeResponse:
        """Send PING, expect PONG."""
        return await self._send_command(CMD_PING)

    async def stat(self, path: str = "/disk") -> dict:
        """Get filesystem statistics. Returns dict of key=value pairs."""
        resp = await self._send_command(CMD_STAT, path)
        if not resp.success:
            return {"error": resp.error_msg}
        return parse_kv_response(resp.data)

    async def write_file(self, path: str, content: str | bytes) -> BridgeResponse:
        """Write content to a file on the kernel VFS."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        hex_data = hex_encode(content)
        return await self._send_command(CMD_WRITE, path, hex_data)

    async def read_file(self, path: str) -> Optional[str]:
        """Read a file from the kernel VFS. Returns content string or None."""
        resp = await self._send_command(CMD_READ, path)
        if not resp.success:
            return None
        try:
            return hex_decode(resp.data).decode("utf-8", errors="replace")
        except Exception:
            return None

    async def list_dir(self, path: str = "/disk") -> list[str]:
        """List files in a directory. Returns list of filenames."""
        resp = await self._send_command(CMD_LS, path)
        if not resp.success:
            return []
        if not resp.data:
            return []
        return [f for f in resp.data.split(",") if f]

    # ------------------------------------------------------------------
    # High-level API — New 7 commands (Day 13-14)
    # ------------------------------------------------------------------

    async def exec_program(self, path: str, args: str = "") -> dict:
        """Execute a binary on the kernel. Returns dict with exit_code."""
        resp = await self._send_command(CMD_EXEC, path, args)
        if not resp.success:
            return {"error": resp.error_msg, "exit_code": -1}
        return parse_kv_response(resp.data)

    async def mkdir(self, path: str) -> BridgeResponse:
        """Create a directory on the kernel VFS."""
        return await self._send_command(CMD_MKDIR, path)

    async def unlink(self, path: str) -> BridgeResponse:
        """Delete a file on the kernel VFS."""
        return await self._send_command(CMD_UNLINK, path)

    async def rename(self, oldpath: str, newpath: str) -> BridgeResponse:
        """Rename/move a file on the kernel VFS."""
        return await self._send_command(CMD_RENAME, oldpath, newpath)

    async def sysinfo(self) -> dict:
        """Get system information (uptime, memory, task count)."""
        resp = await self._send_command(CMD_SYSINFO)
        if not resp.success:
            return {"error": resp.error_msg}
        return parse_kv_response(resp.data)

    async def procs(self) -> list[dict]:
        """Get list of running processes."""
        resp = await self._send_command(CMD_PROCS)
        if not resp.success:
            return []
        return parse_procs_response(resp.data)

    async def http_get(self, url: str) -> Optional[str]:
        """Fetch a URL via host-side HTTP proxy.

        This command is intercepted by the Python bridge service itself
        (not sent to the kernel). The kernel returns ERR if it receives
        this command directly.

        SSRF protection: validates URL scheme and resolved IP before
        connecting. Blocks private/reserved networks and non-HTTP schemes.
        """
        try:
            resolved_ip, original_host = validate_url(url)
        except ValueError as e:
            logger.warning("SSRF blocked for %s: %s", url, e)
            return None

        try:
            import httpx

            # Phase v17 (B-H1): Connect to pinned IP with Host header to
            # prevent DNS rebinding between validation and connection.
            parsed = urlparse(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            pinned_url = f"{parsed.scheme}://{resolved_ip}:{port}{parsed.path or '/'}"
            if parsed.query:
                pinned_url += f"?{parsed.query}"
            headers = {"Host": original_host}
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, read=10.0), verify=_VERIFY_TLS
            ) as client:
                response = await client.get(pinned_url, headers=headers)
                return response.text
        except Exception as e:
            logger.warning("HTTP GET failed for %s: %s", url, e)
            return None

    # ------------------------------------------------------------------
    # High-level API — Chunked I/O and detailed listing (Phase H)
    # ------------------------------------------------------------------

    async def read_file_chunked(
        self, path: str, max_size: int = 70000
    ) -> Optional[str]:
        """Read a file in chunks using READC with offset.

        Sends multiple READC commands with increasing offsets to assemble
        the full file content, up to max_size bytes.
        """
        CHUNK_SIZE = 4096
        assembled = b""
        offset = 0

        while offset < max_size:
            resp = await self._send_command(
                CMD_READC, path, str(offset), str(CHUNK_SIZE)
            )
            if not resp.success:
                if offset == 0:
                    return None
                break
            try:
                chunk = hex_decode(resp.data)
            except Exception:
                break
            if not chunk:
                break
            assembled += chunk
            offset += len(chunk)
            if len(chunk) < CHUNK_SIZE:
                break

        try:
            return assembled.decode("utf-8", errors="replace")
        except Exception:
            return None

    async def write_file_chunked(
        self, path: str, content: str | bytes
    ) -> BridgeResponse:
        """Write content in chunks: WRITE for first 4096 bytes, APPEND for rest."""
        if isinstance(content, str):
            content = content.encode("utf-8")

        CHUNK_SIZE = 4096
        first_chunk = content[:CHUNK_SIZE]
        hex_data = hex_encode(first_chunk)
        resp = await self._send_command(CMD_WRITE, path, hex_data)
        if not resp.success:
            return resp

        offset = CHUNK_SIZE
        while offset < len(content):
            chunk = content[offset : offset + CHUNK_SIZE]
            hex_data = hex_encode(chunk)
            resp = await self._send_command(CMD_APPEND, path, hex_data)
            if not resp.success:
                return resp
            offset += CHUNK_SIZE

        return BridgeResponse(success=True, data=f"wrote {len(content)} bytes")

    async def rmdir(self, path: str) -> BridgeResponse:
        """Remove a directory on the kernel VFS."""
        return await self._send_command(CMD_RMDIR, path)

    async def list_dir_detailed(self, path: str = "/disk") -> list[dict]:
        """List directory with details (name, size, type, mtime).

        Sends LSM command. Modern kernels emit four colon-separated
        fields per entry (`name:size:type:mtime`); pre-mtime kernels
        emit three (`name:size:type`) and we default `mtime` to 0 so
        old kernels still parse cleanly. `mtime` is an opaque uint64
        (seconds since uptime on RAMFS, since Unix epoch on vos3fs);
        the API surfaces it as `mtime` in the listing response so the
        host UI can sort by recency without per-file stat calls.

        Returns list of dicts with 'name', 'size', 'type', 'mtime' keys.
        """
        resp = await self._send_command(CMD_LSM, path)
        if not resp.success:
            return []
        if not resp.data:
            return []
        entries = []
        for entry in resp.data.split(","):
            parts = entry.split(":")
            if len(parts) >= 3:
                # mtime is a new field — defaults to 0 if absent so we
                # remain wire-compatible with kernels that predate the
                # mtime emission patch.
                mtime_raw = parts[3] if len(parts) >= 4 else "0"
                entries.append(
                    {
                        "name": parts[0],
                        "size": int(parts[1]) if parts[1].isdigit() else 0,
                        "type": parts[2],
                        "mtime": int(mtime_raw) if mtime_raw.isdigit() else 0,
                    }
                )
            elif parts[0]:
                entries.append(
                    {"name": parts[0], "size": 0, "type": "unknown", "mtime": 0}
                )
        return entries

    # ------------------------------------------------------------------
    # High-level API — App Isolation (Phase N)
    # ------------------------------------------------------------------

    async def appload(self, app_id: str, binary_path: str) -> BridgeResponse:
        """Load an application binary into the kernel's app sandbox."""
        return await self._send_command(CMD_APPLOAD, app_id, binary_path)

    async def appstat(self, app_id: str) -> dict:
        """Get status of a loaded application."""
        resp = await self._send_command(CMD_APPSTAT, app_id)
        if not resp.success:
            return {"error": resp.error_msg}
        return parse_kv_response(resp.data)

    async def appkill(self, app_id: str) -> BridgeResponse:
        """Kill a running application."""
        return await self._send_command(CMD_APPKILL, app_id)


# ------------------------------------------------------------------
# Idempotency Store (Phase I3)
# ------------------------------------------------------------------

import hashlib
import time as _time


class IdempotencyStore:
    """Cache for idempotent write operations.

    Keyed on sha256(command + args), stores results with TTL.
    Prevents duplicate writes to the kernel bridge.
    """

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._store: dict[str, tuple[float, BridgeResponse]] = {}

    def _key(self, *parts: str) -> str:
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("ascii", errors="replace")).hexdigest()

    def get(self, *parts: str) -> Optional[BridgeResponse]:
        key = self._key(*parts)
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, resp = entry
        if _time.time() - ts > self.ttl:
            del self._store[key]
            return None
        return resp

    def put(self, resp: BridgeResponse, *parts: str) -> None:
        key = self._key(*parts)
        self._store[key] = (_time.time(), resp)

    def prune(self) -> int:
        """Remove expired entries. Returns count of pruned entries."""
        now = _time.time()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self.ttl]
        for k in expired:
            del self._store[k]
        return len(expired)


# Write commands that should use idempotency
_IDEMPOTENT_COMMANDS = {"WRITE", "APPEND", "MKDIR", "RMDIR", "UNLINK", "RENAME"}


# Singleton
_service: Optional[KernelBridgeService] = None


def get_bridge_service() -> KernelBridgeService:
    """Get or create the singleton bridge service."""
    global _service
    if _service is None:
        _service = KernelBridgeService()
    return _service
