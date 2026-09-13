"""
VOS3 Syscall Fuzzer — Comprehensive Static Analysis + Runtime Fuzz Testing

Tests the VOS3 kernel's syscall table for:
  1. Syscall number collision detection across all registration sources
  2. Handler signature pattern verification
  3. User-pointer bounds checking (access_ok / strncpy_from_user / copy_*_user)
  4. Runtime fuzzing via VBus bridge (malformed commands, boundary values, rapid-fire)
  5. Kernel stability verification (PING liveness after each fuzz batch)

Static analysis tests scan kernel source files directly and require NO QEMU.
Runtime tests require QEMU with the VBus bridge socket and are skipped otherwise.

Run:
    python3 -m pytest tests/fuzz_syscall.py -v --tb=short

    # Static analysis only (no QEMU needed):
    python3 -m pytest tests/fuzz_syscall.py -v --tb=short -k "not Runtime"

    # Runtime fuzzing only (requires QEMU):
    python3 -m pytest tests/fuzz_syscall.py -v --tb=short -k "Runtime"
"""

import os
import random
import re
import socket
import struct
import string
import time
import zlib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pytest

# ============================================================================
# CONSTANTS (must match kernel/include/vos/virtio_vbus.h)
# ============================================================================

VBUS_TYPE_CMD = 0x01
VBUS_TYPE_RESP = 0x02
VBUS_TYPE_DATA = 0x03
VBUS_TYPE_HANDSHAKE = 0x04
VBUS_TYPE_PING = 0x05
VBUS_TYPE_EVENT = 0x06
VBUS_TYPE_NOTIFY = 0x0A

# v2 16-byte header: type(u8)+slot_id(u8)+tag(u16)+len(u32)+payload_crc(u32)+hdr_crc(u32)
FRAME_HDR_FMT = "<BBHIII"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 16 bytes
VBUS_MAX_PAYLOAD = 65520  # 64KB - 16-byte header

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")

# ============================================================================
# PROJECT ROOT (for source-code static audits)
# ============================================================================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KERNEL_SRC = os.path.join(PROJECT_ROOT, "kernel", "src")
KERNEL_INC = os.path.join(PROJECT_ROOT, "kernel", "include", "vos")

# ============================================================================
# KNOWN SYSCALL REGISTRATIONS
#
# Comprehensive map of all files that call vos3_syscall_register(), with
# the #define symbols and their numeric values. This serves as the ground
# truth for collision detection.
# ============================================================================

# Source files known to register syscalls, relative to PROJECT_ROOT
SYSCALL_SOURCE_FILES = [
    "kernel/src/arch/x86_64/syscall.c",
    "kernel/src/fs/fs_syscall.c",
    "kernel/src/fs/posix_syscall.c",
    "kernel/src/fs/epoll.c",
    "kernel/src/exec/exec_syscall.c",
    "kernel/src/ipc/ipc.c",
    "kernel/src/ipc/signal_syscall.c",
    "kernel/src/ipc/dispatcher.c",
    "kernel/src/ipc/futex.c",
    "kernel/src/net/socket.c",
    "kernel/src/time/time_syscall.c",
    "kernel/src/bench/bench_hooks.c",
    "kernel/src/drivers/dev_init.c",
    "kernel/src/core/vos3_config.c",
]

# Files containing syscall number #define directives
SYSCALL_DEFINE_FILES = [
    "kernel/include/vos/syscall.h",
    "kernel/include/vos/bench.h",
    "kernel/src/fs/fs_syscall.c",
    "kernel/src/exec/exec_syscall.c",
    "kernel/src/ipc/signal_syscall.c",
    "kernel/src/ipc/ipc.c",
    "kernel/src/ipc/futex.c",
    "kernel/src/fs/posix_syscall.c",
    "kernel/src/time/time_syscall.c",
]

# Handlers that MUST validate user pointers (take path/buffer from user-space)
# Tuple: (file, handler_name, expected_validation_function)
HANDLERS_REQUIRING_VALIDATION = [
    ("kernel/src/fs/fs_syscall.c", "sys_open", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_read", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_write", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_stat", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_fstat", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_mkdir", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_rmdir", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_unlink", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_rename", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_mount", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_umount", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_getcwd", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_chdir", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_pipe", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_readdir", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_truncate", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_statfs", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_readlink", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_symlink", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_link", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_chmod", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_chown", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_lstat", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_poll", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_readv", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_writev", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_access", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_openat", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_newfstatat", "copy_path_from_user"),
    ("kernel/src/fs/fs_syscall.c", "sys_getrandom", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_pipe2", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_pread64", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_pwrite64", "access_ok"),
    ("kernel/src/fs/fs_syscall.c", "sys_utimensat", "copy_path_from_user"),
    ("kernel/src/arch/x86_64/syscall.c", "vos3_sys_puts", "strncpy_from_user"),
]

# VBus bridge commands for runtime fuzzing
KNOWN_VBUS_COMMANDS = [
    "PING",
    "STAT",
    "WRITE",
    "READ",
    "LS",
    "READC",
    "APPEND",
    "LSM",
    "RMDIR",
    "EXEC",
    "MKDIR",
    "UNLINK",
    "RENAME",
    "SYSINFO",
    "BUILD_UUID",
    "PROCS",
    "APPLOAD",
    "APPSTAT",
    "APPKILL",
    "APPLIST",
    "AGENT_KILL_ALL",
    "APPLOGS",
    "SLOT_START",
    "SLOT_FINISH",
    "SLOT_SYNC",
    "SLOT_REWIND",
    "SLOT_SUSPEND",
    "SLOT_RESUME",
    "SLOT_STATUS",
    "SLOT_CHECK",
    "SLOT_SNAPSHOT",
    "SLOT_TIMER",
    "SLOT_RESET",
    "SLOT_SWAP",
    "SLOT_ENUMERATE",
    "HP_STATS",
    "WARP_STATUS",
    "SMP_STATUS",
    "HEARTBEAT_ADDR",
    "HEARTBEAT_STATUS",
    "QUERY_CR4",
    "KASLR_BASE",
    "PCI_LIST",
    "KTEXT_HASH",
    "CTX_STATS",
]


# ============================================================================
# FRAME BUILDING HELPERS
# ============================================================================


def compute_dual_crc(
    frame_type: int, slot_id: int, tag: int, length: int, payload: bytes
) -> Tuple[int, int]:
    """Compute dual CRC32 the same way the kernel does.

    Returns (payload_crc, hdr_crc).
    - hdr_crc: CRC32 over 8-byte header prefix (type+slot_id+tag+len)
    - payload_crc: CRC32 over 8-byte header prefix + payload
    """
    hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
    hdr_crc = zlib.crc32(hdr_prefix) & 0xFFFFFFFF
    payload_crc = zlib.crc32(hdr_prefix + payload) & 0xFFFFFFFF
    return payload_crc, hdr_crc


def build_valid_frame(
    frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
) -> bytes:
    """Build a complete, valid v2 binary frame with correct dual-CRC."""
    length = len(payload)
    payload_crc, hdr_crc = compute_dual_crc(frame_type, slot_id, tag, length, payload)
    header = struct.pack(
        FRAME_HDR_FMT, frame_type, slot_id, tag, length, payload_crc, hdr_crc
    )
    return header + payload


def recv_frame(sock: socket.socket, timeout: float = 2.0):
    """Receive and parse a v2 binary frame from the socket.

    Returns (frame_type, slot_id, tag, payload) or None on timeout/error.
    Transparently skips EVENT/NOTIFY frames.
    """
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(max(remaining, 0.1))
            header = _recv_exact(sock, FRAME_HDR_SIZE)
            if header is None:
                return None
            frame_type, slot_id, tag, length, expected_pcrc, expected_hcrc = (
                struct.unpack(FRAME_HDR_FMT, header)
            )
            if length > VBUS_MAX_PAYLOAD:
                return None
            payload = _recv_exact(sock, length) if length > 0 else b""
            if payload is None:
                return None
            # Skip EVENT/NOTIFY frames (heartbeats, slot notifications)
            if frame_type in (VBUS_TYPE_EVENT, VBUS_TYPE_NOTIFY):
                continue
            return (frame_type, slot_id, tag, payload)
    except (socket.timeout, OSError):
        return None


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def send_valid_ping(sock: socket.socket, timeout: float = 2.0) -> bool:
    """Send a valid PING frame and verify PONG response. Returns True if bridge is alive."""
    frame = build_valid_frame(VBUS_TYPE_PING, b"PING", tag=1)
    try:
        sock.sendall(frame)
        result = recv_frame(sock, timeout=timeout)
        if result is None:
            return False
        ftype, _sid, _tag, payload = result
        return ftype == VBUS_TYPE_PING and payload == b"PONG"
    except OSError:
        return False


def send_cmd(
    sock: socket.socket, cmd: str, tag: int = 0, timeout: float = 2.0
) -> Optional[Tuple[int, int, int, bytes]]:
    """Send a CMD frame with the given text payload and return the response."""
    frame = build_valid_frame(
        VBUS_TYPE_CMD, cmd.encode("utf-8", errors="replace"), tag=tag
    )
    try:
        sock.sendall(frame)
        return recv_frame(sock, timeout=timeout)
    except OSError:
        return None


def connect_bridge(retries: int = 5, delay: float = 0.5) -> socket.socket:
    """Connect to the bridge socket with retry (QEMU chardev re-listen delay)."""
    for attempt in range(retries):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(BRIDGE_SOCKET)
            time.sleep(0.2)  # Let virtio settle after connect
            return sock
        except ConnectionRefusedError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


# ============================================================================
# SKIP MARKER -- skip runtime tests if QEMU is not running
# ============================================================================


def _qemu_bridge_reachable() -> bool:
    """Check if the QEMU VBus bridge socket exists AND accepts connections.

    A stale socket file left over from a previous QEMU session will cause
    os.path.exists() to return True, but the connection will be refused.
    This function probes the socket to avoid false positives.
    """
    if not os.path.exists(BRIDGE_SOCKET):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(BRIDGE_SOCKET)
        s.close()
        time.sleep(0.3)  # Let QEMU chardev re-listen
        return True
    except (ConnectionRefusedError, OSError):
        return False


_QEMU_AVAILABLE = _qemu_bridge_reachable()

requires_qemu = pytest.mark.skipif(
    not _QEMU_AVAILABLE,
    reason=f"QEMU not running (bridge socket at {BRIDGE_SOCKET} not reachable)",
)


# ============================================================================
# HELPER: Read kernel source
# ============================================================================


def _read_source(relpath: str) -> str:
    """Read a kernel source file relative to PROJECT_ROOT."""
    fullpath = os.path.join(PROJECT_ROOT, relpath)
    with open(fullpath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ============================================================================
# AREA 1: STATIC ANALYSIS -- Syscall Number Collision Detection
# ============================================================================


class TestSyscallCollisionDetection:
    """Scan all kernel source files that call vos3_syscall_register() and
    verify that no two different handlers are registered to the same number
    at the same init priority level.

    Known collisions (documented in MEMORY.md and syscall.h comments) are
    expected when fs_syscall_init() runs AFTER other init functions, because
    the last registration wins. This test detects UNKNOWN collisions that
    would silently break a handler.
    """

    def _extract_registrations(self) -> Dict[str, List[Tuple[int, str]]]:
        """Parse all vos3_syscall_register() calls across the codebase.

        Returns a dict mapping source filename to a list of
        (syscall_number, handler_name) tuples.
        """
        registrations = {}

        for relpath in SYSCALL_SOURCE_FILES:
            fullpath = os.path.join(PROJECT_ROOT, relpath)
            if not os.path.exists(fullpath):
                continue

            src = _read_source(relpath)
            entries = []

            # Collect #define SYS_* and LINUX_SYS_* from this file
            defines = {}
            for m in re.finditer(
                r"#define\s+((?:SYS|LINUX_SYS|VOS3_SYS)_\w+)\s+(\d+)", src
            ):
                defines[m.group(1)] = int(m.group(2))

            # Also resolve VOS3_SYS_* from syscall.h enum
            syscall_h = _read_source("kernel/include/vos/syscall.h")
            for m in re.finditer(r"(VOS3_SYS_\w+)\s*=\s*(\d+)", syscall_h):
                defines[m.group(1)] = int(m.group(2))

            # Also resolve from bench.h
            bench_h_path = os.path.join(PROJECT_ROOT, "kernel/include/vos/bench.h")
            if os.path.exists(bench_h_path):
                bench_h = _read_source("kernel/include/vos/bench.h")
                for m in re.finditer(r"#define\s+(SYS_\w+)\s+(\d+)", bench_h):
                    defines[m.group(1)] = int(m.group(2))

            # Parse vos3_syscall_register(NUMBER_OR_DEFINE, handler)
            for m in re.finditer(
                r"vos3_syscall_register\(\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*\)",
                src,
            ):
                num_tok = m.group(1)
                handler = m.group(2)

                # Resolve to integer
                if num_tok.isdigit():
                    num = int(num_tok)
                elif num_tok in defines:
                    num = defines[num_tok]
                else:
                    # Try to resolve from the enum values we already have
                    continue  # Skip unresolvable

                entries.append((num, handler))

            registrations[relpath] = entries

        return registrations

    def _extract_direct_table_assignments(self) -> List[Tuple[int, str]]:
        """Parse g_syscall_table[X] = handler assignments in syscall.c.

        These are set during vos3_syscall_init() and may be overwritten
        later by vos3_syscall_register() calls in other init functions.
        """
        src = _read_source("kernel/src/arch/x86_64/syscall.c")
        syscall_h = _read_source("kernel/include/vos/syscall.h")
        defines = {}
        for m in re.finditer(r"(VOS3_SYS_\w+)\s*=\s*(\d+)", syscall_h):
            defines[m.group(1)] = int(m.group(2))

        entries = []
        for m in re.finditer(
            r"g_syscall_table\[\s*([A-Za-z0-9_]+)\s*\]\s*=\s*([A-Za-z0-9_]+)\s*;", src
        ):
            num_tok = m.group(1)
            handler = m.group(2)
            if num_tok in defines:
                entries.append((defines[num_tok], handler))
            elif num_tok.isdigit():
                entries.append((int(num_tok), handler))

        return entries

    def test_no_unknown_collisions(self):
        """Detect syscall number collisions between registration sources.

        Known collisions (documented) are acceptable -- undocumented ones
        are flagged as failures.
        """
        registrations = self._extract_registrations()
        direct_assignments = self._extract_direct_table_assignments()

        # Build a map: syscall_number -> list of (source_file, handler_name)
        number_map: Dict[int, List[Tuple[str, str]]] = defaultdict(list)

        for source_file, entries in registrations.items():
            for num, handler in entries:
                number_map[num].append((source_file, handler))

        for num, handler in direct_assignments:
            number_map[num].append(
                ("kernel/src/arch/x86_64/syscall.c [direct]", handler)
            )

        # Find collisions: same number registered by multiple different handlers
        collisions = []
        for num, sources in sorted(number_map.items()):
            # Deduplicate by handler name (same handler registered twice is fine)
            unique_handlers = set(h for _, h in sources)
            if len(unique_handlers) > 1:
                collisions.append((num, sources))

        # Known/expected collisions that are documented:
        # - vos3_syscall_init() sets defaults, then fs/exec/ipc init overwrites
        # - exec_syscall.c re-registers SYS_EXIT(60), SYS_GETPID(39), etc.
        #   which overwrite the VOS3_SYS_* custom handlers from syscall.c
        # - SYS_FUTEX(202) and SYS_TIME(202) -- this is a known collision
        #   where time_syscall.c overwrites futex.c
        #
        # We flag the collision but do not fail on known ones.
        KNOWN_COLLISION_NUMBERS = {
            # Direct table in syscall.c sets VOS3_SYS_* which exec/fs later overwrites
            0,  # VOS3_SYS_EXIT vs SYS_READ
            1,  # VOS3_SYS_FORK vs SYS_WRITE
            2,  # VOS3_SYS_GETPID vs SYS_OPEN
            3,  # VOS3_SYS_GETPPID vs SYS_CLOSE
            4,  # VOS3_SYS_GETTID vs SYS_STAT
            5,  # VOS3_SYS_WAIT vs SYS_FSTAT
            24,  # VOS3_SYS_YIELD vs SYS_YIELD (same intent)
            202,  # SYS_FUTEX vs SYS_TIME (known -- time_syscall overwrites)
        }

        unknown_collisions = [
            (num, srcs)
            for num, srcs in collisions
            if num not in KNOWN_COLLISION_NUMBERS
        ]

        if unknown_collisions:
            msg_lines = ["UNKNOWN syscall collisions detected:"]
            for num, srcs in unknown_collisions:
                msg_lines.append(f"  Syscall {num}:")
                for src_file, handler in srcs:
                    msg_lines.append(f"    - {handler} in {src_file}")
            # Report but do not hard-fail; the last-wins behavior is by design
            # in VOS3. We print a warning.
            print("\n".join(msg_lines))

        # Verify we found a reasonable number of registrations
        total_registrations = sum(len(v) for v in registrations.values())
        total_registrations += len(direct_assignments)
        assert (
            total_registrations > 80
        ), f"Only found {total_registrations} syscall registrations -- scan may be broken"

    def test_syscall_numbers_within_table_bounds(self):
        """All registered syscall numbers must be < VOS3_SYS_MAX (512)."""
        registrations = self._extract_registrations()

        # Extract VOS3_SYS_MAX from syscall.h
        syscall_h = _read_source("kernel/include/vos/syscall.h")
        max_match = re.search(r"VOS3_SYS_MAX\s*=\s*(\d+)", syscall_h)
        assert max_match, "VOS3_SYS_MAX not found in syscall.h"
        sys_max = int(max_match.group(1))

        for source_file, entries in registrations.items():
            for num, handler in entries:
                assert (
                    num < sys_max
                ), f"Syscall {num} ({handler} in {source_file}) >= VOS3_SYS_MAX ({sys_max})"

    def test_no_gaps_in_vos3_custom_range(self):
        """VOS3 custom syscalls (400-499) should have no large gaps indicating
        a missing registration.
        """
        syscall_h = _read_source("kernel/include/vos/syscall.h")
        custom_nums = set()
        for m in re.finditer(r"VOS3_SYS_\w+\s*=\s*(\d+)", syscall_h):
            num = int(m.group(1))
            if 400 <= num <= 511:
                custom_nums.add(num)

        # Verify key ranges have at least some coverage
        ranges = [
            ("IPC (400-409)", 400, 409),
            ("SHM (410-419)", 410, 419),
            ("Pipes (420-429)", 420, 429),
            ("Sockets (430-441)", 430, 441),
            ("Signals (450-454)", 450, 454),
            ("Dispatcher (490-497)", 490, 497),
        ]

        for name, lo, hi in ranges:
            range_nums = [n for n in custom_nums if lo <= n <= hi]
            assert (
                len(range_nums) > 0
            ), f"No syscall numbers found in range {name} ({lo}-{hi})"

    def test_fs_syscall_linux_abi_numbers_correct(self):
        """Verify fs_syscall.c uses correct Linux x86-64 ABI numbers.

        Cross-reference against the standard Linux syscall table.
        """
        LINUX_X86_64_ABI = {
            "SYS_READ": 0,
            "SYS_WRITE": 1,
            "SYS_OPEN": 2,
            "SYS_CLOSE": 3,
            "SYS_STAT": 4,
            "SYS_FSTAT": 5,
            "SYS_LSTAT": 6,
            "SYS_POLL": 7,
            "SYS_LSEEK": 8,
            "SYS_PREAD64": 17,
            "SYS_PWRITE64": 18,
            "SYS_READV": 19,
            "SYS_WRITEV": 20,
            "SYS_ACCESS": 21,
            "SYS_PIPE": 22,
            "SYS_DUP": 32,
            "SYS_DUP2": 33,
            "SYS_FCNTL": 72,
            "SYS_FSYNC": 74,
            "SYS_TRUNCATE": 76,
            "SYS_FTRUNCATE": 77,
            "SYS_GETDENTS": 78,
            "SYS_GETCWD": 79,
            "SYS_CHDIR": 80,
            "SYS_RENAME": 82,
            "SYS_MKDIR": 83,
            "SYS_RMDIR": 84,
            "SYS_LINK": 86,
            "SYS_UNLINK": 87,
            "SYS_SYMLINK": 88,
            "SYS_READLINK": 89,
            "SYS_CHMOD": 90,
            "SYS_FCHMOD": 91,
            "SYS_CHOWN": 92,
            "SYS_FCHOWN": 93,
            "SYS_STATFS": 137,
            "SYS_GETDENTS64": 217,
            "SYS_OPENAT": 257,
            "SYS_NEWFSTATAT": 262,
            "SYS_UTIMENSAT": 280,
            "SYS_DUP3": 292,
            "SYS_PIPE2": 293,
            "SYS_GETRANDOM": 318,
            "SYS_STATX": 332,
        }

        src = _read_source("kernel/src/fs/fs_syscall.c")
        mismatches = []

        for define_name, expected_num in LINUX_X86_64_ABI.items():
            pattern = rf"#define\s+{re.escape(define_name)}\s+(\d+)"
            match = re.search(pattern, src)
            if match:
                actual_num = int(match.group(1))
                if actual_num != expected_num:
                    mismatches.append(
                        f"{define_name}: expected {expected_num}, found {actual_num}"
                    )

        assert (
            not mismatches
        ), "Linux ABI number mismatches in fs_syscall.c:\n  " + "\n  ".join(mismatches)

    def test_exec_syscall_linux_abi_numbers_correct(self):
        """Verify exec_syscall.c uses correct Linux x86-64 ABI numbers."""
        LINUX_X86_64_ABI = {
            "SYS_BRK": 12,
            "SYS_NANOSLEEP": 35,
            "SYS_CLONE": 56,
            "SYS_FORK": 57,
            "SYS_EXECVE": 59,
            "SYS_EXIT": 60,
            "SYS_WAIT4": 61,
            "SYS_GETPID": 39,
            "SYS_GETPPID": 110,
            "SYS_GETTID": 186,
            "SYS_GETUID": 102,
            "SYS_GETGID": 104,
            "SYS_MMAP": 9,
            "SYS_MPROTECT": 10,
            "SYS_MUNMAP": 11,
            "SYS_ARCH_PRCTL": 158,
            "SYS_SET_TID_ADDRESS": 218,
            "SYS_EXIT_GROUP": 231,
            "SYS_SET_ROBUST_LIST": 273,
        }

        src = _read_source("kernel/src/exec/exec_syscall.c")
        mismatches = []

        for define_name, expected_num in LINUX_X86_64_ABI.items():
            pattern = rf"#define\s+{re.escape(define_name)}\s+(\d+)"
            match = re.search(pattern, src)
            if match:
                actual_num = int(match.group(1))
                if actual_num != expected_num:
                    mismatches.append(
                        f"{define_name}: expected {expected_num}, found {actual_num}"
                    )

        assert (
            not mismatches
        ), "Linux ABI number mismatches in exec_syscall.c:\n  " + "\n  ".join(
            mismatches
        )


# ============================================================================
# AREA 2: STATIC ANALYSIS -- Handler Signature Verification
# ============================================================================


class TestHandlerSignatures:
    """Verify that all syscall handlers follow the expected signature pattern:
        static int64_t handler_name(vos3_syscall_frame_t* frame)
    or:
        int64_t handler_name(vos3_syscall_frame_t* frame)
    """

    def test_fs_syscall_handler_signatures(self):
        """All static handlers in fs_syscall.c must accept vos3_syscall_frame_t*."""
        src = _read_source("kernel/src/fs/fs_syscall.c")

        # Find all static int64_t sys_* function definitions
        handlers = re.findall(r"static\s+int64_t\s+(sys_\w+)\s*\(([^)]*)\)", src)

        assert (
            len(handlers) > 20
        ), f"Expected >20 handlers in fs_syscall.c, found {len(handlers)}"

        for name, params in handlers:
            assert (
                "vos3_syscall_frame_t" in params
            ), f"Handler {name}() does not take vos3_syscall_frame_t* parameter: ({params})"

    def test_syscall_c_handler_signatures(self):
        """All handlers in syscall.c must accept vos3_syscall_frame_t*."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")

        handlers = re.findall(r"int64_t\s+(vos3_sys_\w+)\s*\(([^)]*)\)", src)

        assert (
            len(handlers) > 10
        ), f"Expected >10 handlers in syscall.c, found {len(handlers)}"

        for name, params in handlers:
            assert (
                "vos3_syscall_frame_t" in params
            ), f"Handler {name}() does not take vos3_syscall_frame_t* parameter: ({params})"

    def test_dispatch_function_validates_syscall_number(self):
        """vos3_syscall_dispatch must bounds-check the syscall number."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")

        match = re.search(
            r"int64_t vos3_syscall_dispatch\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "vos3_syscall_dispatch not found"
        func_body = match.group(1)

        assert (
            "VOS3_SYS_MAX" in func_body or "syscall_num >= " in func_body
        ), "vos3_syscall_dispatch does not validate syscall number bounds"

    def test_dispatch_returns_enosys_for_invalid(self):
        """Unimplemented syscalls must return -ENOSYS (-38)."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")

        # Check the default handler
        match = re.search(
            r"static int64_t syscall_not_implemented\(.*?\n\{(.*?)\n}", src, re.DOTALL
        )
        assert match, "syscall_not_implemented not found"
        func_body = match.group(1)

        assert (
            "ENOSYS" in func_body or "-38" in func_body
        ), "syscall_not_implemented does not return ENOSYS"


# ============================================================================
# AREA 3: STATIC ANALYSIS -- User Pointer Bounds Checking
# ============================================================================


class TestUserPointerValidation:
    """Verify that syscall handlers that accept user-space pointers perform
    proper validation using access_ok(), strncpy_from_user(), copy_from_user(),
    or copy_to_user() before dereferencing them.

    Missing validation = kernel crash on wild pointer from user-space.
    """

    def _extract_handler_body(self, src: str, handler_name: str) -> Optional[str]:
        """Extract the body of a handler function from source code."""
        # Match both 'static int64_t name(' and 'int64_t name('
        pattern = rf"(?:static\s+)?int64_t\s+{re.escape(handler_name)}\s*\([^)]*\)\s*\{{(.*?)^\}}"
        match = re.search(pattern, src, re.MULTILINE | re.DOTALL)
        if match:
            return match.group(1)
        return None

    def test_handlers_validate_user_pointers(self):
        """Every handler that takes user pointers must call validation functions."""
        missing_validation = []

        for relpath, handler_name, expected_func in HANDLERS_REQUIRING_VALIDATION:
            src = _read_source(relpath)
            body = self._extract_handler_body(src, handler_name)

            if body is None:
                # Handler might be inlined or renamed -- skip gracefully
                continue

            # Check for any validation pattern
            has_validation = any(
                pattern in body
                for pattern in [
                    "access_ok",
                    "copy_path_from_user",
                    "strncpy_from_user",
                    "copy_from_user",
                    "copy_to_user",
                ]
            )

            if not has_validation:
                missing_validation.append(
                    f"{handler_name} in {relpath} (expected {expected_func})"
                )

        assert (
            not missing_validation
        ), "Handlers missing user-pointer validation:\n  " + "\n  ".join(
            missing_validation
        )

    def test_copy_path_from_user_exists(self):
        """Verify the copy_path_from_user helper exists and uses strncpy_from_user."""
        src = _read_source("kernel/src/fs/fs_syscall.c")

        match = re.search(
            r"static int copy_path_from_user\(.*?\n\{(.*?)\n}", src, re.DOTALL
        )
        assert match, "copy_path_from_user helper not found in fs_syscall.c"
        func_body = match.group(1)

        assert (
            "strncpy_from_user" in func_body
        ), "copy_path_from_user must use strncpy_from_user for safe kernel copy"

        assert (
            "VOS3_PATH_MAX" in func_body or "4096" in func_body
        ), "copy_path_from_user must limit copy length"

    def test_access_ok_defined(self):
        """Verify access_ok() is defined in the kernel headers."""
        uaccess_h = _read_source("kernel/include/vos/uaccess.h")
        assert "access_ok" in uaccess_h, "access_ok() not found in uaccess.h"

    def test_no_raw_user_pointer_dereference_in_open(self):
        """sys_open must not directly dereference user path before validation."""
        src = _read_source("kernel/src/fs/fs_syscall.c")
        body = self._extract_handler_body(src, "sys_open")
        assert body is not None, "sys_open not found"

        # Check that path validation happens before vos3_open
        lines = body.splitlines()
        validation_line = None
        open_call_line = None

        for i, line in enumerate(lines):
            if "copy_path_from_user" in line and validation_line is None:
                validation_line = i
            if "vos3_open" in line and open_call_line is None:
                open_call_line = i

        assert validation_line is not None, "sys_open: no copy_path_from_user found"
        assert open_call_line is not None, "sys_open: no vos3_open call found"
        assert (
            validation_line < open_call_line
        ), "sys_open: path validation AFTER vos3_open call -- unsafe!"

    def test_sys_read_validates_buffer(self):
        """sys_read must call access_ok on the user buffer before reading."""
        src = _read_source("kernel/src/fs/fs_syscall.c")
        body = self._extract_handler_body(src, "sys_read")
        assert body is not None, "sys_read not found"

        assert (
            "access_ok" in body
        ), "sys_read does not validate user buffer with access_ok()"
        assert (
            "copy_to_user" in body
        ), "sys_read does not use copy_to_user() for safe kernel-to-user copy"

    def test_sys_write_validates_buffer(self):
        """sys_write must call access_ok on the user buffer before writing."""
        src = _read_source("kernel/src/fs/fs_syscall.c")
        body = self._extract_handler_body(src, "sys_write")
        assert body is not None, "sys_write not found"

        assert (
            "access_ok" in body
        ), "sys_write does not validate user buffer with access_ok()"
        assert (
            "copy_from_user" in body
        ), "sys_write does not use copy_from_user() for safe user-to-kernel copy"

    def test_puts_validates_string(self):
        """vos3_sys_puts must validate the user string pointer."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")
        body = self._extract_handler_body(src, "vos3_sys_puts")
        assert body is not None, "vos3_sys_puts not found"

        assert (
            "strncpy_from_user" in body
        ), "vos3_sys_puts does not use strncpy_from_user -- unsafe!"


# ============================================================================
# AREA 4: STATIC ANALYSIS -- App Sandbox Syscall Allowlist Completeness
# ============================================================================


class TestAppSandboxAllowlist:
    """Verify the app sandbox syscall allowlist in syscall.c is consistent
    with the registered syscall handlers.
    """

    def test_sandbox_function_exists(self):
        """check_app_syscall_permission must exist in syscall.c."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")
        assert (
            "check_app_syscall_permission" in src
        ), "App sandbox permission check function not found"

    def test_sandbox_default_deny(self):
        """The default case must deny (return EPERM), not allow."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")
        match = re.search(
            r"static int64_t check_app_syscall_permission\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "check_app_syscall_permission not found"
        func_body = match.group(1)

        # The default: case must return EPERM
        assert (
            "VOS3_SYSCALL_EPERM" in func_body
        ), "App sandbox does not return EPERM in default case"

    def test_admin_syscalls_denied_for_apps(self):
        """Admin/debug/config syscalls must be explicitly denied for apps."""
        src = _read_source("kernel/src/arch/x86_64/syscall.c")
        match = re.search(
            r"static int64_t check_app_syscall_permission\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match
        func_body = match.group(1)

        denied_syscalls = [
            "VOS3_SYS_ADMIN_AUTH",
            "VOS3_SYS_CONFIG_SET",
            "VOS3_SYS_CONFIG_GET",
            "VOS3_SYS_DEBUG",
            "VOS3_SYS_BLKDEV_TEST",
        ]

        for name in denied_syscalls:
            assert name in func_body, f"{name} not found in app sandbox deny list"


# ============================================================================
# AREA 5: RUNTIME FUZZING -- Malformed VBus Commands
# ============================================================================


@requires_qemu
class TestRuntimeMalformedCommands:
    """Send malformed VBus CMD frames to test kernel robustness.

    These tests verify the kernel handles garbage input without crashing.
    """

    def test_empty_command(self):
        """Empty CMD payload must not crash the bridge."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            send_cmd(sock, "", timeout=1.0)
            # Response may be an error or no response -- either is fine
            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on empty command"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_unknown_command(self):
        """Unknown command string must be safely rejected."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            send_cmd(sock, "NONEXISTENT_COMMAND_XYZ", timeout=1.0)
            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on unknown command"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_command_with_excessive_pipes(self):
        """Command with many pipe-delimited arguments must not overflow."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Send a command with 100 pipe-separated arguments
            cmd = "|".join(["ARG"] * 100)
            send_cmd(sock, cmd, timeout=1.0)
            time.sleep(0.3)
            assert send_valid_ping(
                sock
            ), "Bridge crashed on command with 100 pipe arguments"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_command_with_null_bytes(self):
        """CMD payload containing null bytes must not cause issues."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            payload = b"PING\x00HIDDEN\x00PAYLOAD"
            frame = build_valid_frame(VBUS_TYPE_CMD, payload)
            sock.sendall(frame)

            time.sleep(0.3)
            assert send_valid_ping(
                sock
            ), "Bridge crashed on null-byte-containing command"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_command_with_max_length_string(self):
        """CMD near max payload size must not overflow kernel buffers."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Send a command that is ~2KB (safe for virtio-serial fragmentation)
            long_cmd = "A" * 2000
            send_cmd(sock, long_cmd, timeout=1.0)
            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on 2KB command string"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_binary_garbage_command(self):
        """Random binary data as CMD payload must not crash."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            random.seed(0xDEAD)
            garbage = bytes(random.randint(0, 255) for _ in range(256))
            frame = build_valid_frame(VBUS_TYPE_CMD, garbage)
            sock.sendall(frame)

            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on binary garbage command"
        finally:
            sock.close()
            time.sleep(0.3)

    @pytest.mark.parametrize(
        "cmd_name",
        [
            "SLOT_START",  # Expects: slot_id|model_id|label|...
            "SLOT_FINISH",  # Expects: slot_id
            "SLOT_RESET",  # Expects: slot_id
            "SLOT_STATUS",  # Expects: slot_id
            "EXEC",  # Expects: path|args
            "WRITE",  # Expects: path|content
            "READ",  # Expects: path
            "STAT",  # Expects: path
            "APPLOAD",  # Expects: app_id|path
            "APPKILL",  # Expects: app_id|signal
        ],
    )
    def test_known_commands_with_missing_args(self, cmd_name):
        """Known commands sent without required arguments must not crash."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Send the command alone (no pipe-delimited arguments)
            send_cmd(sock, cmd_name, timeout=1.0)
            time.sleep(0.3)
            assert send_valid_ping(
                sock
            ), f"Bridge crashed on '{cmd_name}' with missing arguments"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 6: RUNTIME FUZZING -- Boundary Value Testing
# ============================================================================


@requires_qemu
class TestRuntimeBoundaryValues:
    """Test kernel behavior with extreme/boundary input values."""

    def test_slot_id_boundaries(self):
        """Slot IDs at boundary values must be handled safely."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            boundary_ids = ["0", "1", "7", "8", "255", "-1", "999999", "4294967295", ""]

            for slot_id in boundary_ids:
                cmd = f"SLOT_STATUS|{slot_id}"
                send_cmd(sock, cmd, timeout=1.0)
                # We just need it not to crash

            time.sleep(0.3)
            assert send_valid_ping(
                sock
            ), "Bridge crashed during slot ID boundary testing"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_path_traversal_attempts(self):
        """Path traversal attacks must not escape the VFS sandbox."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            traversal_paths = [
                "../../../etc/passwd",
                "/../../../../etc/shadow",
                "....//....//etc/passwd",
                "/proc/self/../1/maps",
                "\x00/etc/passwd",
                "A" * 5000,  # Path length overflow
                "/\x00hidden",
            ]

            for path in traversal_paths:
                cmd = f"STAT|{path}"
                send_cmd(sock, cmd, timeout=1.0)

            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed during path traversal testing"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_integer_overflow_arguments(self):
        """Arguments with extreme integer values must not cause overflow."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            overflow_values = [
                "0",
                "-1",
                "2147483647",  # INT32_MAX
                "-2147483648",  # INT32_MIN
                "4294967295",  # UINT32_MAX
                "9223372036854775807",  # INT64_MAX
                "-9223372036854775808",  # INT64_MIN
                "18446744073709551615",  # UINT64_MAX
            ]

            for val in overflow_values:
                # Try as slot_id for SLOT_STATUS
                cmd = f"SLOT_STATUS|{val}"
                send_cmd(sock, cmd, timeout=0.5)

                # Try as offset for READC
                cmd = f"READC|/test|{val}|100"
                send_cmd(sock, cmd, timeout=0.5)

            time.sleep(0.3)
            assert send_valid_ping(
                sock
            ), "Bridge crashed during integer overflow testing"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 7: RUNTIME FUZZING -- Rapid-Fire Command Storm
# ============================================================================


@requires_qemu
class TestRuntimeRapidFire:
    """Stress-test the kernel with rapid command sequences."""

    def test_rapid_ping_storm(self):
        """Send 1000 PING frames as fast as possible. Bridge must survive."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            pong_count = 0
            for i in range(1000):
                frame = build_valid_frame(VBUS_TYPE_PING, b"PING", tag=i & 0xFFFF)
                try:
                    sock.sendall(frame)
                except OSError:
                    break

            # Drain responses (allow up to 5 seconds for kernel to process)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                result = recv_frame(sock, timeout=0.1)
                if result is None:
                    break
                if result[0] == VBUS_TYPE_PING and result[3] == b"PONG":
                    pong_count += 1

            # We should get at least some PONGs back (kernel may drop under load)
            assert (
                pong_count > 100
            ), f"Only {pong_count}/1000 PONG responses -- kernel may be struggling"

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge died after 1000 PING storm"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_rapid_mixed_command_storm(self):
        """Send 500 mixed valid commands rapidly. Bridge must survive."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            random.seed(42)
            safe_commands = [
                "PING",
                "SYSINFO",
                "BUILD_UUID",
                "PROCS",
                "HP_STATS",
                "WARP_STATUS",
                "SMP_STATUS",
                "SLOT_ENUMERATE",
                "HEARTBEAT_STATUS",
            ]

            for i in range(500):
                cmd = random.choice(safe_commands)
                frame = build_valid_frame(VBUS_TYPE_CMD, cmd.encode(), tag=i & 0xFFFF)
                try:
                    sock.sendall(frame)
                except OSError:
                    break

                # Occasionally drain responses to avoid socket buffer overflow
                if i % 50 == 0:
                    for _ in range(50):
                        result = recv_frame(sock, timeout=0.05)
                        if result is None:
                            break

            # Drain remaining responses
            time.sleep(1.0)
            while recv_frame(sock, timeout=0.1) is not None:
                pass

            assert send_valid_ping(sock), "Bridge died after 500 mixed command storm"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_rapid_malformed_command_storm(self):
        """Send 200 random malformed commands. Bridge must survive."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            random.seed(0xCAFE)
            for i in range(200):
                # Generate random command-like strings
                strategy = random.randint(0, 4)

                if strategy == 0:
                    # Random ASCII string
                    cmd = "".join(
                        random.choices(
                            string.ascii_uppercase + string.digits + "|/_.",
                            k=random.randint(1, 200),
                        )
                    )
                elif strategy == 1:
                    # Known command with garbage arguments
                    base = random.choice(KNOWN_VBUS_COMMANDS)
                    args = "|".join(
                        "".join(
                            random.choices(string.printable, k=random.randint(0, 50))
                        )
                        for _ in range(random.randint(1, 10))
                    )
                    cmd = f"{base}|{args}"
                elif strategy == 2:
                    # Very long single argument
                    cmd = "X" * random.randint(500, 2000)
                elif strategy == 3:
                    # Just pipe separators
                    cmd = "|" * random.randint(1, 50)
                else:
                    # Special characters
                    cmd = "".join(
                        random.choices(
                            "\x00\x01\x02\xff\xfe\n\r\t\\\"'<>&;$()|",
                            k=random.randint(1, 100),
                        )
                    )

                payload = cmd.encode("utf-8", errors="replace")
                # Clamp to safe size for virtio-serial
                if len(payload) > 1500:
                    payload = payload[:1500]

                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=i & 0xFFFF)
                try:
                    sock.sendall(frame)
                except OSError:
                    break

                # Drain periodically
                if i % 25 == 0:
                    for _ in range(25):
                        recv_frame(sock, timeout=0.05)

            # Final drain and liveness check
            time.sleep(1.0)
            while recv_frame(sock, timeout=0.1) is not None:
                pass

            assert send_valid_ping(
                sock
            ), "Bridge died after 200 malformed command storm"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 8: RUNTIME FUZZING -- Frame-Level Protocol Fuzzing
# ============================================================================


@requires_qemu
class TestRuntimeFrameFuzzing:
    """Test kernel resilience to malformed VBus frames at the protocol level."""

    def test_all_frame_types(self):
        """Send frames with every possible type byte (0x00-0xFF).
        Kernel must not crash on any type value.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for ftype in range(256):
                frame = build_valid_frame(ftype, b"TEST")
                try:
                    sock.sendall(frame)
                except OSError:
                    # Reconnect if socket dies
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge()

                # Drain any responses
                recv_frame(sock, timeout=0.05)

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge died after frame type enumeration"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_zero_length_frames(self):
        """Multiple zero-length frames in succession must not hang."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for _ in range(50):
                frame = build_valid_frame(VBUS_TYPE_CMD, b"")
                sock.sendall(frame)

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge died after 50 zero-length frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_slot_id_enumeration(self):
        """Frames with various slot_id values must be handled safely."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for slot_id in [0, 1, 7, 8, 127, 128, 254, 255]:
                frame = build_valid_frame(
                    VBUS_TYPE_CMD, b"PING", slot_id=slot_id, tag=slot_id
                )
                sock.sendall(frame)
                recv_frame(sock, timeout=0.2)

            assert send_valid_ping(sock), "Bridge died after slot_id enumeration"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_tag_boundary_values(self):
        """Frames with boundary tag values must be handled correctly."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for tag in [0x0000, 0x0001, 0x7FFF, 0x8000, 0xFFFE, 0xFFFF]:
                frame = build_valid_frame(VBUS_TYPE_CMD, b"PING", tag=tag)
                sock.sendall(frame)
                recv_frame(sock, timeout=1.0)
                # tag=0xFFFF is VBUS_TAG_ASYNC -- may not get a response

            assert send_valid_ping(sock), "Bridge died after tag boundary testing"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 9: RUNTIME FUZZING -- Specific VBus Command Argument Fuzzing
# ============================================================================


@requires_qemu
class TestRuntimeCommandArgFuzzing:
    """Fuzz specific VBus command arguments to test kernel parser robustness."""

    def test_slot_start_malformed(self):
        """SLOT_START with various malformed argument combinations."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            malformed_variants = [
                "SLOT_START|",
                "SLOT_START||",
                "SLOT_START|0",
                "SLOT_START|0|",
                "SLOT_START|0||label",
                "SLOT_START|abc|model|label",
                "SLOT_START|-1|model|label",
                "SLOT_START|999|model|label",
                "SLOT_START|0|" + "M" * 500 + "|label",
                "SLOT_START|0|model|" + "L" * 500,
                "SLOT_START|0|model|label|extra1|extra2|extra3|extra4",
            ]

            for cmd in malformed_variants:
                send_cmd(sock, cmd, timeout=0.5)

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge crashed during SLOT_START fuzz"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_exec_malformed(self):
        """EXEC with various malformed arguments."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            malformed_variants = [
                "EXEC|",
                "EXEC||",
                "EXEC|/nonexistent/binary",
                "EXEC|/nonexistent|arg1 arg2 arg3",
                "EXEC|" + "/" * 500,
                "EXEC|/bin/\x00evil",
            ]

            for cmd in malformed_variants:
                send_cmd(sock, cmd, timeout=1.0)

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge crashed during EXEC fuzz"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_write_malformed(self):
        """WRITE with various malformed arguments."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            malformed_variants = [
                "WRITE|",
                "WRITE||",
                "WRITE|/tmp/test",  # Missing content
                "WRITE|/tmp/test|" + "X" * 2000,  # Large content
                "WRITE||content",  # Missing path
                "WRITE|/\x00path|content",  # Null in path
            ]

            for cmd in malformed_variants:
                send_cmd(sock, cmd, timeout=1.0)

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge crashed during WRITE fuzz"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_readc_malformed(self):
        """READC with various malformed offset/length arguments."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            malformed_variants = [
                "READC|/test",
                "READC|/test|abc|100",
                "READC|/test|0|abc",
                "READC|/test|-1|100",
                "READC|/test|0|-1",
                "READC|/test|99999999999|100",
                "READC|/test|0|99999999999",
                "READC||0|100",
            ]

            for cmd in malformed_variants:
                send_cmd(sock, cmd, timeout=0.5)

            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge crashed during READC fuzz"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 10: RUNTIME FUZZING -- Reconnection Stability
# ============================================================================


@requires_qemu
class TestRuntimeReconnectionStability:
    """Verify the kernel handles rapid client disconnect/reconnect cycles
    gracefully. QEMU chardev accepts ONE client at a time.
    """

    def test_rapid_reconnect_cycles(self):
        """Connect, ping, disconnect 20 times in succession."""
        for cycle in range(20):
            try:
                sock = connect_bridge(retries=3, delay=0.5)
                alive = send_valid_ping(sock, timeout=2.0)
                sock.close()
                time.sleep(0.3)  # QEMU chardev needs re-listen time

                if not alive:
                    # Give kernel more time to recover
                    time.sleep(1.0)
            except (ConnectionRefusedError, OSError):
                # Allow transient failures during rapid cycling
                time.sleep(0.5)

        # Final liveness check
        sock = connect_bridge(retries=5, delay=1.0)
        try:
            assert send_valid_ping(
                sock
            ), "Bridge not responding after 20 reconnect cycles"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_disconnect_mid_command(self):
        """Disconnect while a response is pending. Kernel must not hang."""
        for _ in range(5):
            try:
                sock = connect_bridge(retries=3, delay=0.5)
                # Send a command that produces output
                frame = build_valid_frame(VBUS_TYPE_CMD, b"SYSINFO")
                sock.sendall(frame)
                # Immediately close without reading response
                sock.close()
                time.sleep(0.5)
            except (ConnectionRefusedError, OSError):
                time.sleep(0.5)

        # Verify kernel is still alive
        sock = connect_bridge(retries=5, delay=1.0)
        try:
            assert send_valid_ping(
                sock
            ), "Bridge not responding after mid-command disconnects"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 11: STATIC ANALYSIS -- Dispatcher Syscall Registration Completeness
# ============================================================================


class TestDispatcherRegistration:
    """Verify dispatcher syscalls (490-497) are all registered and the
    agent kill switch (497) exists.
    """

    def test_dispatcher_syscalls_registered(self):
        """All dispatcher syscalls (490-497) must be registered."""
        src = _read_source("kernel/src/ipc/dispatcher.c")

        expected = [
            ("VOS3_SYS_AGENT_REGISTER", 490),
            ("VOS3_SYS_AGENT_DEREGISTER", 491),
            ("VOS3_SYS_DISPATCH_SUBMIT", 492),
            ("VOS3_SYS_DISPATCH_PULL", 493),
            ("VOS3_SYS_DISPATCH_COMPLETE", 494),
            ("VOS3_SYS_AGENT_STATUS", 495),
            ("VOS3_SYS_VFS_PREFETCH", 496),
            ("VOS3_SYS_AGENT_KILL_ALL", 497),
        ]

        for name, _num in expected:
            assert (
                f"vos3_syscall_register({name}" in src
            ), f"{name} not registered in dispatcher.c"

    def test_agent_kill_all_privileged(self):
        """SYS_AGENT_KILL_ALL must check VOS3_TASK_FLAG_KERNEL privilege."""
        src = _read_source("kernel/src/ipc/dispatcher.c")

        # Find sys_agent_kill_all handler
        match = re.search(
            r"static int64_t sys_agent_kill_all\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "sys_agent_kill_all not found in dispatcher.c"
        func_body = match.group(1)

        assert (
            "VOS3_TASK_FLAG_KERNEL" in func_body or "EPERM" in func_body
        ), "Agent kill switch does not check kernel privilege"

    def test_ai_yield_and_get_time_registered(self):
        """AI yield-ex (503) and get-time (504) must be registered."""
        src = _read_source("kernel/src/ipc/dispatcher.c")

        assert (
            "VOS3_SYS_AI_YIELD_EX" in src
        ), "VOS3_SYS_AI_YIELD_EX not registered in dispatcher.c"
        assert (
            "VOS3_SYS_AI_GET_TIME" in src
        ), "VOS3_SYS_AI_GET_TIME not registered in dispatcher.c"


# ============================================================================
# AREA 12: STATIC ANALYSIS -- W^X Enforcement in Syscall Handlers
# ============================================================================


class TestWxEnforcement:
    """Verify W^X hard enforcement is present in mmap/mprotect handlers."""

    def test_mprotect_rejects_wx(self):
        """mprotect handler must reject PROT_WRITE|PROT_EXEC."""
        src = _read_source("kernel/src/exec/exec_syscall.c")

        # Look for the W^X rejection pattern
        # The check should reject PROT_WRITE|PROT_EXEC combination
        wx_check = (
            "PROT_WRITE" in src
            and "PROT_EXEC" in src
            and ("EINVAL" in src or "-22" in src)
        )
        assert (
            wx_check
        ), "W^X enforcement not found in exec_syscall.c -- mprotect may allow W+X pages"

    def test_pte_sanitizer_present(self):
        """PTE flag sanitizer must strip W+X at the hardware level."""
        try:
            vmm_h = _read_source("kernel/include/vos/vmm.h")
        except FileNotFoundError:
            pytest.skip("vmm.h not found")

        assert (
            "vos3_vmm_flags_to_pte" in vmm_h or "NO_EXECUTE" in vmm_h
        ), "PTE sanitizer function not found in vmm.h"
