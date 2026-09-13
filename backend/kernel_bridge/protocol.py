"""
VOS3 Kernel Bridge Protocol
============================

Text protocol over QEMU serial (COM2 via Unix domain socket).

Request:  CMD|arg1|arg2|...\n
Response: OK|data\n  or  ERR|code|msg\n

Supports 15 commands: PING, STAT, WRITE, READ, LS, EXEC, MKDIR,
UNLINK, RENAME, SYSINFO, PROCS, HTTPGET, APPLOAD, APPSTAT, APPKILL.
"""

import binascii
from dataclasses import dataclass

# Command constants — original 5
CMD_PING = "PING"
CMD_STAT = "STAT"
CMD_WRITE = "WRITE"
CMD_READ = "READ"
CMD_LS = "LS"

# Command constants — new 7 (Day 13-14)
CMD_EXEC = "EXEC"
CMD_MKDIR = "MKDIR"
CMD_UNLINK = "UNLINK"
CMD_RENAME = "RENAME"
CMD_SYSINFO = "SYSINFO"
CMD_PROCS = "PROCS"
CMD_HTTPGET = "HTTPGET"

# Command constants — Chunked I/O and detailed listing (Phase H)
CMD_READC = "READC"
CMD_APPEND = "APPEND"
CMD_LSM = "LSM"
CMD_RMDIR = "RMDIR"

# Command constants — App Isolation (Phase N)
CMD_APPLOAD = "APPLOAD"
CMD_APPSTAT = "APPSTAT"
CMD_APPKILL = "APPKILL"


@dataclass
class BridgeResponse:
    """Parsed response from the kernel bridge."""

    success: bool
    data: str = ""
    error_code: int = 0
    error_msg: str = ""


def format_command(*parts: str) -> bytes:
    """Format a bridge command as bytes ready to send."""
    line = "|".join(parts) + "\n"
    return line.encode("ascii")


def parse_response(line: str) -> BridgeResponse:
    """Parse a response line from the kernel bridge."""
    line = line.strip()
    if not line:
        return BridgeResponse(success=False, error_code=-1, error_msg="empty response")

    if line.startswith("OK"):
        data = ""
        if "|" in line:
            data = line.split("|", 1)[1]
        return BridgeResponse(success=True, data=data)

    if line.startswith("ERR"):
        parts = line.split("|", 2)
        code = int(parts[1]) if len(parts) > 1 else -1
        msg = parts[2] if len(parts) > 2 else "unknown error"
        return BridgeResponse(success=False, error_code=code, error_msg=msg)

    return BridgeResponse(success=False, error_code=-1, error_msg=f"malformed: {line}")


def parse_kv_response(data: str) -> dict:
    """Parse a key=value comma-separated response string into a dict."""
    result = {}
    for pair in data.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                result[k] = int(v)
            except ValueError:
                result[k] = v
    return result


def parse_procs_response(data: str) -> list[dict]:
    """Parse PROCS response (pid,name,state,ppid;...) into list of dicts."""
    if not data:
        return []
    processes = []
    for row in data.split(";"):
        fields = row.split(",")
        if len(fields) >= 4:
            processes.append(
                {
                    "pid": int(fields[0]),
                    "name": fields[1],
                    "state": fields[2],
                    "ppid": int(fields[3]),
                }
            )
    return processes


def hex_encode(data: bytes) -> str:
    """Encode bytes to hex string for the bridge protocol."""
    return binascii.hexlify(data).decode("ascii")


# Resilience-Matrix F3 — hex relay length cap.
# Defends against DoS via gigabyte-sized hex payloads tunnelling through
# the kernel bridge. 1 MiB == 2 MiB of hex characters, well above any
# legitimate IntentManifest (16 KiB) or file-write payload (100 KiB).
_HEX_DECODE_MAX_BYTES = 1024 * 1024  # 1 MiB decoded


def hex_decode(hex_str: str) -> bytes:
    """Decode hex string from the bridge protocol to bytes.

    Pre-validates length cap (1 MiB) and even-length parity before
    delegating to binascii.unhexlify. Raises ValueError on either
    violation; the calling layer must surface this as a 400/422.
    """
    if len(hex_str) > _HEX_DECODE_MAX_BYTES * 2:
        raise ValueError(
            f"hex_decode payload too large: {len(hex_str)} chars > "
            f"{_HEX_DECODE_MAX_BYTES * 2} cap"
        )
    if len(hex_str) % 2 != 0:
        raise ValueError(f"hex_decode odd-length payload: {len(hex_str)} chars")
    return binascii.unhexlify(hex_str)
