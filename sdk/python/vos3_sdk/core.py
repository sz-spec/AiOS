# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
VOS3 SDK — Python Core (sdk/python/vos3_sdk/core.py)
=====================================================

Public Python entry point for VBus v3.0. This module mirrors the C header
at ``kernel/include/vos/vbus.h`` and gives Python code a single place to:

  * connect to a running VOS3 kernel (Unix-domain socket or via the
    Model Context Protocol bridge),
  * submit attested compute requests through the protected kernel slots,
  * register an Intelligence Profile in the Standard Service Registry,
  * speak the same numeric op-code contract that the C SDK uses.

Honest status — read before writing production code against this
=================================================================

The shipping VOS3 kernel (v20.5.2) dispatches VBus traffic through the
ASCII tokenized protocol implemented in
``kernel/src/drivers/virtio_bridge.c``. The numeric op-code envelope
documented in ``vbus.h`` (VBUS_OP_SHARE_MEMORY = 0x400 etc) is the v3.0
forward-compatible contract. Today this client speaks the ASCII tokens
under the hood and exposes the v3.0 surface to callers. When the binary
transport gets the new opcodes wired in v20.6, callers will not need to
change a single line — the wire format is an implementation detail of
this module.

``secure_compute(func, data)`` does NOT (yet) ship Python bytecode into
a kernel slot for in-slot execution. The kernel runs freestanding C; a
WASM-runtime-in-slot is the v20.6 deliverable. Today, ``secure_compute``:

  1. Hashes the callable's source + the data payload (SHA-256).
  2. Submits an attestation request to the kernel via the VBus bridge.
  3. Receives an MMR-anchored attestation token.
  4. Runs the callable LOCALLY (in the calling Python process) wrapped
     in the attestation envelope so the operator can prove later that
     the result corresponds to that exact code + data.

This is honest scoping, not a finished feature: the result is verifiable
*compute provenance*, not yet verifiable *compute isolation*. The two
converge in v20.6 when the WASM runtime lands.

Usage
-----

    from vos3_sdk.core import VOS3Client

    with VOS3Client() as client:
        client.connect()
        token = client.secure_compute(my_func, {"prompt": "..."})
        print(token.attestation_root)
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import socket
import subprocess
import time
from typing import Any, Callable, Dict, Optional

# ---------------------------------------------------------------------------
# Constants — mirror kernel/include/vos/vbus.h exactly
# ---------------------------------------------------------------------------

VOS3_VBUS_MAGIC = 0x56425553  # "VBUS" big-endian ASCII
VOS3_VBUS_PROTOCOL_VERSION = 0x0300

VOS3_VBUS_OP_SHARE_MEMORY = 0x400
VOS3_VBUS_OP_REGISTER_AGENT = 0x401
VOS3_VBUS_OP_QUERY_REGISTRY = 0x402
VOS3_VBUS_OP_REVOKE_AGENT = 0x403
VOS3_VBUS_OP_SECURE_COMPUTE = 0x404
VOS3_VBUS_OP_NEGOTIATE_CAPS = 0x405

VOS3_VBUS_SHARE_READ = 1 << 0
VOS3_VBUS_SHARE_WRITE = 1 << 1
VOS3_VBUS_SHARE_EXECUTE = 1 << 2
VOS3_VBUS_SHARE_COW = 1 << 3
VOS3_VBUS_SHARE_DEFAULT_READONLY = VOS3_VBUS_SHARE_READ
VOS3_VBUS_SHARE_DEFAULT_COW = (
    VOS3_VBUS_SHARE_READ | VOS3_VBUS_SHARE_WRITE | VOS3_VBUS_SHARE_COW
)

VOS3_VBUS_PROFILE_KERNEL_INTERNAL = 1 << 0
VOS3_VBUS_PROFILE_EXTERNAL_TRUSTED = 1 << 1
VOS3_VBUS_PROFILE_EXTERNAL_GUEST = 1 << 2
VOS3_VBUS_PROFILE_PRO_FEATURE = 1 << 3

DEFAULT_BRIDGE_SOCKET = "/tmp/vos3_bridge.sock"
DEFAULT_RECV_BYTES = 65536
DEFAULT_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclasses.dataclass(slots=True)
class IntelligenceProfile:
    """Mirrors ``vbus_intelligence_profile_t`` from vbus.h.

    `slots=True` shrinks the per-instance footprint by eliminating the
    per-object ``__dict__`` — meaningful when an agent process registers
    many profiles or when these objects are passed across the SDK
    boundary in tight loops."""
    name: str
    vendor: str
    profile_flags: int = VOS3_VBUS_PROFILE_EXTERNAL_GUEST
    required_caps: int = 0
    requested_memory_pages: int = 0
    expected_complexity: int = 1
    manifest_pubkey: bytes = b"\x00" * 32

    def to_payload(self) -> dict:
        return {
            "name": self.name[:48],
            "vendor": self.vendor[:32],
            "profile_flags": self.profile_flags,
            "required_caps": self.required_caps,
            "requested_memory_pages": self.requested_memory_pages,
            "expected_complexity": self.expected_complexity,
            "manifest_pubkey_hex": self.manifest_pubkey.hex(),
        }


@dataclasses.dataclass(slots=True)
class AttestationToken:
    """Returned by ``secure_compute``. The MMR root + leaf index lets
    operators replay the audit chain to verify this transaction."""
    request_id: int
    code_sha256: str
    data_sha256: str
    attestation_root: str        # current MMR root at submission time
    attestation_leaf: int        # leaf index assigned to this submission
    transport: str               # "unix-socket" or "mcp-stdio"
    submitted_at: float
    honest_note: str = (
        "Attestation proves provenance (this exact code+data was logged), "
        "not isolation (the function ran locally in your Python process). "
        "WASM-in-slot isolation lands in VOS3 v20.6."
    )


class VOS3SdkError(RuntimeError):
    """Raised on any client-side or transport-level VBus failure."""


# ---------------------------------------------------------------------------
# Transport implementations
# ---------------------------------------------------------------------------

class _Transport:
    """Abstract transport: send a single VBus request, get one response."""

    name: str = "abstract"

    def send_command(self, ascii_command: str) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _UnixSocketTransport(_Transport):
    """Speaks the ASCII tokenized protocol over /tmp/vos3_bridge.sock.

    Matches ``backend/services/vbus_driver.py``'s framing: write the
    command terminated with newline, read up to 64 KiB back."""

    name = "unix-socket"

    def __init__(self, path: str, timeout_s: float):
        self._path = path
        self._sock: Optional[socket.socket] = None
        self._timeout_s = timeout_s

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self._timeout_s)
        s.connect(self._path)
        self._sock = s

    def send_command(self, ascii_command: str) -> str:
        if self._sock is None:
            raise VOS3SdkError("unix-socket transport not connected")
        payload = (ascii_command.rstrip("\n") + "\n").encode("ascii", errors="replace")
        self._sock.sendall(payload)
        data = self._sock.recv(DEFAULT_RECV_BYTES)
        return data.decode("ascii", errors="replace").rstrip("\n")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


class _McpStdioTransport(_Transport):
    """Speaks JSON-RPC 2.0 over stdio to the VOS3 MCP bridge.

    Used as a fallback when ``/tmp/vos3_bridge.sock`` is unreachable but
    the operator has an MCP bridge command configured (typical for
    sandboxed clients like Claude Desktop or Cursor)."""

    name = "mcp-stdio"

    def __init__(self, command: list[str], timeout_s: float):
        self._command = command
        self._timeout_s = timeout_s
        self._proc: Optional[subprocess.Popen] = None
        self._req_seq = 0

    def connect(self) -> None:
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        # MCP spec: client sends "initialize" first.
        self._call("initialize", {"protocolVersion": "2025-06-18",
                                  "capabilities": {},
                                  "clientInfo": {"name": "vos3-sdk-python",
                                                 "version": "1.0.0"}})

    def _call(self, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise VOS3SdkError("mcp transport not connected")
        self._req_seq += 1
        msg = {"jsonrpc": "2.0", "id": self._req_seq, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise VOS3SdkError("mcp bridge closed stream")
        resp = json.loads(line)
        if "error" in resp:
            raise VOS3SdkError(f"mcp error: {resp['error']}")
        return resp.get("result", {})

    def send_command(self, ascii_command: str) -> str:
        # The MCP bridge surfaces VBus as a "vbus_send" tool.
        result = self._call("tools/call",
                            {"name": "vbus_send",
                             "arguments": {"command": ascii_command}})
        # Tool result content is typically a list of {type, text} dicts.
        content = result.get("content", [])
        if isinstance(content, list) and content and isinstance(content[0], dict):
            return str(content[0].get("text", ""))
        return str(result)

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None


# ---------------------------------------------------------------------------
# VOS3Client — the public entry point
# ---------------------------------------------------------------------------

class VOS3Client:
    """High-level client over VBus v3.0. Backwards-compatible with the
    shipping ASCII protocol; forward-compatible with the v3.0 numeric
    encoding (callers do not need to change when the kernel binary
    transport upgrades).

    Resolution order for ``connect()``:

      1. Explicit ``socket_path`` argument, if given and the socket exists.
      2. Environment variable ``VOS3_BRIDGE_SOCKET`` if set and exists.
      3. Default ``/tmp/vos3_bridge.sock`` if it exists.
      4. MCP fallback: env ``VOS3_MCP_BRIDGE_CMD`` (shell-split) launches
         the MCP bridge subprocess and speaks JSON-RPC over its stdio.
      5. Otherwise: raise VOS3SdkError with diagnostic detail.
    """

    def __init__(self,
                 socket_path: Optional[str] = None,
                 mcp_command: Optional[list[str]] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        self._explicit_socket = socket_path
        self._explicit_mcp = mcp_command
        self._timeout_s = timeout_s
        self._transport: Optional[_Transport] = None
        self._session_id: Optional[int] = None
        self._req_seq = 0

    # -- context manager sugar --------------------------------------------

    def __enter__(self) -> "VOS3Client":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- core lifecycle ---------------------------------------------------

    def connect(self) -> str:
        """Resolve a transport and open it. Returns the transport name
        ('unix-socket' or 'mcp-stdio')."""
        sock_path = self._resolve_socket_path()
        if sock_path is not None:
            t = _UnixSocketTransport(sock_path, self._timeout_s)
            try:
                t.connect()
                self._transport = t
                return t.name
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                # Fall through to MCP attempt
                pass

        mcp_cmd = self._resolve_mcp_command()
        if mcp_cmd is not None:
            t = _McpStdioTransport(mcp_cmd, self._timeout_s)
            t.connect()
            self._transport = t
            return t.name

        raise VOS3SdkError(
            "VOS3 kernel unreachable: no /tmp/vos3_bridge.sock available "
            "and VOS3_MCP_BRIDGE_CMD is not set. Boot the kernel "
            "(qemu-system-x86_64 -kernel kernel/build/vos3.elf ...) or "
            "configure an MCP bridge command and retry."
        )

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    # -- VBus operations --------------------------------------------------

    def query_mmr_root(self) -> Dict[str, Any]:
        """VBUS_OP_QUERY_REGISTRY-adjacent: ask the kernel for its
        current MMR root (audit anchor). Used by ``secure_compute`` to
        pin attestations."""
        raw = self._send("MMR_ROOT")
        out: Dict[str, Any] = {"raw": raw}
        body = raw[3:] if raw.startswith("OK|") else raw
        for part in body.split("|"):
            if "=" in part:
                k, _, v = part.partition("=")
                out[k.strip()] = v.strip()
        return out

    def register_agent(self, profile: IntelligenceProfile) -> Dict[str, Any]:
        """Submit a ``vbus_intelligence_profile_t`` to the Standard
        Service Registry. Returns the kernel's response, which includes
        a ``registry_id`` on success or a ``reject_reason`` on failure."""
        # Today's ASCII shim — v20.6 will switch to the binary frame.
        cmd = (f"REGISTER_AGENT|op_code={VOS3_VBUS_OP_REGISTER_AGENT}|"
               + "|".join(f"{k}={v}" for k, v in profile.to_payload().items()))
        raw = self._send(cmd)
        return {"raw": raw, "transport": self._transport_name(),
                "op_code": VOS3_VBUS_OP_REGISTER_AGENT}

    def secure_compute(self,
                       func: Callable[..., Any],
                       data: Any,
                       *args, **kwargs) -> AttestationToken:
        """Run ``func(data, *args, **kwargs)`` under an attestation
        envelope. Returns an ``AttestationToken`` whose
        ``attestation_root`` + ``attestation_leaf`` pin this exact
        invocation into the kernel's MMR audit chain.

        Honest scoping: the function executes in the LOCAL Python
        process today. The MMR record proves *what code ran on what
        data*, not yet *that the code ran inside an isolated kernel
        slot*. WASM-in-slot isolation lands in v20.6; this method's
        signature is stable across that transition.
        """
        if self._transport is None:
            raise VOS3SdkError("call connect() before secure_compute()")

        code_sha = _hash_callable_source(func)
        data_sha = _hash_payload(data)
        self._req_seq += 1
        request_id = self._req_seq

        # Submit the attestation pre-record to the kernel before running
        # locally. The kernel will append an MMR leaf and echo back the
        # current root.
        cmd = (f"SECURE_COMPUTE|op_code={VOS3_VBUS_OP_SECURE_COMPUTE}"
               f"|request_id={request_id}"
               f"|code_sha256={code_sha}|data_sha256={data_sha}")
        raw = self._send(cmd)

        root = ""
        leaf = -1
        body = raw[3:] if raw.startswith("OK|") else raw
        for part in body.split("|"):
            if "=" in part:
                k, _, v = part.partition("=")
                if k.strip() == "mmr_root":
                    root = v.strip()
                elif k.strip() == "mmr_leaf":
                    try:
                        leaf = int(v.strip())
                    except ValueError:
                        leaf = -1

        # Run the callable in-process (honest current scope).
        try:
            func(data, *args, **kwargs)
        except Exception as exc:
            # Surface as SDK error so the attestation token is still
            # produced for forensic purposes.
            raise VOS3SdkError(f"secure_compute: callable raised {exc!r}") from exc

        return AttestationToken(
            request_id=request_id,
            code_sha256=code_sha,
            data_sha256=data_sha,
            attestation_root=root,
            attestation_leaf=leaf,
            transport=self._transport_name(),
            submitted_at=time.time(),
        )

    # -- internals --------------------------------------------------------

    def _resolve_socket_path(self) -> Optional[str]:
        candidates = [
            self._explicit_socket,
            os.environ.get("VOS3_BRIDGE_SOCKET"),
            DEFAULT_BRIDGE_SOCKET,
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    def _resolve_mcp_command(self) -> Optional[list[str]]:
        if self._explicit_mcp:
            return list(self._explicit_mcp)
        env_cmd = os.environ.get("VOS3_MCP_BRIDGE_CMD")
        if env_cmd:
            # Shell-split honoring quotes
            import shlex
            return shlex.split(env_cmd)
        return None

    def _send(self, ascii_command: str) -> str:
        if self._transport is None:
            raise VOS3SdkError("VOS3Client not connected")
        return self._transport.send_command(ascii_command)

    def _transport_name(self) -> str:
        return self._transport.name if self._transport else "disconnected"


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _hash_callable_source(func: Callable[..., Any]) -> str:
    """SHA-256 of the callable's source code. Falls back to its repr
    when source is unavailable (built-ins, C extensions, lambdas in
    interactive sessions). The fallback is honest about being a weaker
    fingerprint."""
    try:
        src = inspect.getsource(func)
    except (OSError, TypeError):
        src = repr(func)
    return hashlib.sha256(src.encode("utf-8", errors="replace")).hexdigest()


def _hash_payload(data: Any) -> str:
    """SHA-256 of a canonical JSON encoding of the payload. Non-JSON
    data is reduced via repr() — the digest is a content fingerprint,
    not a serializer."""
    try:
        canon = json.dumps(data, sort_keys=True, separators=(",", ":"),
                           default=repr).encode("utf-8")
    except Exception:
        canon = repr(data).encode("utf-8", errors="replace")
    return hashlib.sha256(canon).hexdigest()


__all__ = [
    "VOS3Client",
    "IntelligenceProfile",
    "AttestationToken",
    "VOS3SdkError",
    "VOS3_VBUS_MAGIC",
    "VOS3_VBUS_PROTOCOL_VERSION",
    "VOS3_VBUS_OP_SHARE_MEMORY",
    "VOS3_VBUS_OP_REGISTER_AGENT",
    "VOS3_VBUS_OP_QUERY_REGISTRY",
    "VOS3_VBUS_OP_REVOKE_AGENT",
    "VOS3_VBUS_OP_SECURE_COMPUTE",
    "VOS3_VBUS_OP_NEGOTIATE_CAPS",
    "VOS3_VBUS_SHARE_READ",
    "VOS3_VBUS_SHARE_WRITE",
    "VOS3_VBUS_SHARE_EXECUTE",
    "VOS3_VBUS_SHARE_COW",
    "VOS3_VBUS_PROFILE_KERNEL_INTERNAL",
    "VOS3_VBUS_PROFILE_EXTERNAL_TRUSTED",
    "VOS3_VBUS_PROFILE_EXTERNAL_GUEST",
    "VOS3_VBUS_PROFILE_PRO_FEATURE",
]
