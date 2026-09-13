"""
backend/security/kernel_gate_connector.py
=========================================

Sprint 17 / Wave 2 / Cluster C-2 — Userspace bridge to the eBPF LSM
byte-level write-gate kernel module.

What this is
------------

The Python side of the kernel/userspace contract defined in
`kernel/include/vos/taint_maps.h`. Pushes per-(pid, fd) color entries
into the BPF map BEFORE a write() or sendmsg() syscall, so the LSM
hook in `kernel/src/sec/taint_gate.c` can evaluate the egress policy
in-kernel and return -EPERM if denied.

Two operating modes:

  - LIVE    (Linux ≥ 5.7 with bpffs mounted, libbpf available):
            Real bpf() syscall via ctypes; map entries land in
            /sys/fs/bpf/vos3/taint_colors.
  - MOCK    (any other host, including macOS dev):
            In-memory simulator that records pushes/decisions so
            tests + dev iteration work without a real kernel module.

The caller never picks the mode. The connector auto-detects on
construction and exposes the same public API in both modes. Tests
force MOCK mode by passing `force_mock=True`.

Why we ship MOCK mode
---------------------

The eBPF LSM module is Sprint 18 / kernel-side work. Shipping the
userspace bridge ahead of the verifier-clean MVP — but with a MOCK
backend — gives the rest of the agent stack (DualLLMRouter, egress
checkers) a stable API to integrate against TODAY, so the kernel
side can land later without changing any Python callers.

Public API
----------

    connector = KernelGateConnector()  # auto-detects mode
    receipt = connector.push_tainted_buffer(
        fd=42, pid=os.getpid(), buffer=tainted_buffer,
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    # receipt.pushed is True iff the map entry was accepted.
    # In LIVE mode, the next write(fd, ...) is gated by the LSM.
    # In MOCK mode, call connector.simulate_write(fd, pid, length)
    # to get the decision the kernel WOULD have returned.

Honest scope ceilings
---------------------

  - Even LIVE mode is a "best-effort" gate: there's a race between
    the userspace push and the actual write() syscall. A compromised
    agent process can `write()` in between. Closing this requires
    either (a) a setsockopt-style ioctl that atomically marks the fd
    + pushes the color in one syscall, OR (b) a kernel-side path
    that pulls colors from a userspace-shared memory ring keyed by
    the syscall's `iov` pointer. Both are Sprint 19+ work.

  - 64 KB per-fd color budget. Buffers >64 KB are chunked at push
    time; the bridge computes the max-color over each chunk and
    issues sequential pushes. Each push is a separate map update.

  - MOCK mode does NOT enforce anything. It only records what the
    kernel WOULD do so tests can assert behavior. Production code
    detects MOCK and emits a WARNING-level log so the operator sees
    when the gate isn't real.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import enum
import errno as errno_mod
import logging
import os
import platform
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Constants — KEEP IN SYNC with kernel/include/vos/taint_maps.h
VOS3_TAINT_MAX_COLOR_BYTES = 64 * 1024
VOS3_TAINT_MAP_MAX_ENTRIES = 1024
VOS3_TAINT_MAP_PIN_PATH = "/sys/fs/bpf/vos3/taint_colors"
# Phase 20: pid-namespace config map (slot 0 = {u64 dev; u64 ino}); see
# struct vos3_taint_pidns_cfg / taint_config in kernel/src/sec/taint_gate.c.
VOS3_TAINT_CONFIG_PIN_PATH = "/sys/fs/bpf/vos3/taint_config"
_PIDNS_CFG_FMT = "<QQ"  # dev (u64 LE), ino (u64 LE)

# Mirror of kernel/include/vos/taint_maps.h VOS3_TAINT_MODE_*
VOS3_TAINT_MODE_PER_BYTE = 0
VOS3_TAINT_MODE_PER_FD = 1

# bpf(2) syscall constants — KEEP IN SYNC with <linux/bpf.h>.
# See: https://docs.kernel.org/userspace-api/ebpf/syscall.html
_BPF_MAP_LOOKUP_ELEM = 1
_BPF_MAP_UPDATE_ELEM = 2
_BPF_MAP_DELETE_ELEM = 3
_BPF_OBJ_GET = 7

# bpf(2) syscall number per arch (x86_64=321, aarch64=280).
# uname machine → number lookup; covers the targets vOS deploys on.
_BPF_SYSCALL_NRS = {
    "x86_64": 321,
    "aarch64": 280,
    "riscv64": 280,  # matches aarch64 nr per the generic syscall table
}


def _bpf_syscall_nr() -> int:
    nr = _BPF_SYSCALL_NRS.get(platform.machine())
    if nr is None:
        raise RuntimeError(
            f"unknown bpf() syscall nr for arch {platform.machine()!r}; "
            f"add a mapping in _BPF_SYSCALL_NRS"
        )
    return nr


# ---------------------------------------------------------------------------
# Mirrored enums — value parity is contract with the kernel header
# ---------------------------------------------------------------------------


class TaintLabel(enum.IntEnum):
    PUBLIC = 0
    UNTRUSTED = 1
    SECRET = 2
    TOXIC = 3


class SinkKind(enum.IntEnum):
    NETWORK_EGRESS = 0
    FILE_WRITE = 1
    USER_STDOUT = 2
    AUDIT_LOG = 3


# Default sink-policy ceilings — match C7 / C-1 defaults.
DEFAULT_SINK_POLICY = {
    SinkKind.NETWORK_EGRESS: TaintLabel.UNTRUSTED,
    SinkKind.FILE_WRITE: TaintLabel.SECRET,
    SinkKind.USER_STDOUT: TaintLabel.SECRET,
    SinkKind.AUDIT_LOG: TaintLabel.TOXIC,
}


class GateMode(enum.IntEnum):
    LIVE = 0
    MOCK = 1


class TaintMode(enum.IntEnum):
    """Per-fd vs per-byte enforcement mode (Sprint 18 / Wave 3.C).

    - PER_FD (default, MVP): kernel uses the pre-computed max_color
      scalar. Cheap. Verifier-trivial. Adequate when the userspace
      bridge has already pushed only the slice being written.

    - PER_BYTE: kernel walks colors[0..min(length, 64 KiB)] each hook
      invocation via the Sprint-20 verifier-clean "loop dance" (outer
      bpf_loop over constant chunks + inner unrolled masked scan with
      early-exit on the first toxic byte), computing max over the
      actual write range. More expensive but defends against userspace
      mis-pushing a buffer whose declared max_color doesn't match its
      actual contents. Requires kernel ≥ 5.17 (bpf_loop helper).
      Coverage is the full 64 KiB per-fd color budget; larger buffers
      are chunked into separate map entries at push time.
    """

    PER_BYTE = VOS3_TAINT_MODE_PER_BYTE  # = 0
    PER_FD = VOS3_TAINT_MODE_PER_FD  # = 1


class DecisionKind(enum.IntEnum):
    ALLOW = 0
    DENY = 1


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PushReceipt:
    """Returned by push_tainted_buffer(). `pushed` is True iff the
    map entry was accepted (LIVE) or recorded (MOCK)."""

    pushed: bool
    mode: GateMode
    pid: int
    fd: int
    length: int
    max_color: TaintLabel
    sink_kind: SinkKind
    sink_max_label: TaintLabel
    buffer_sha256: str
    pushed_ns: int
    reason: str = ""


@dataclass(frozen=True)
class GateDecision:
    """In MOCK mode the bridge can simulate the kernel's decision so
    tests assert behavior without a real LSM. In LIVE mode this is
    recovered from the audit ring."""

    decision: DecisionKind
    pid: int
    fd: int
    length: int
    max_color: TaintLabel
    sink_max_label: TaintLabel
    errno: int  # -EPERM(13) on DENY; 0 on ALLOW
    buffer_sha256: str
    reason: str = ""


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class KernelGateStats:
    pushes: int = 0
    push_failures: int = 0
    decisions_allow: int = 0
    decisions_deny: int = 0
    chunks_emitted: int = 0
    mock_simulate_calls: int = 0


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# bpf(2) syscall binding via ctypes — Sprint 18 / Wave 3.B
#
# Production wrapper around the bpf() syscall used by KernelGateConnector
# in LIVE mode. Reads/writes the pinned BPF_MAP_TYPE_HASH that the eBPF
# LSM program (kernel/src/sec/taint_gate.c) creates and consults.
#
# Design notes:
#   - The map pin must already exist — we use BPF_OBJ_GET, not BPF_MAP_
#     CREATE. The kernel-side program is responsible for the map; the
#     userspace bridge only pushes entries. If the map is absent, LIVE
#     mode degrades to MOCK behavior (logs WARNING + push returns
#     False), preserving the safe-fail contract.
#   - Map key/value structs MUST match kernel/include/vos/taint_maps.h
#     byte-for-byte. We use struct.pack/unpack with explicit
#     little-endian formats and __attribute__((packed)) layout in C.
#   - The bpf_attr union passed to bpf() is opcode-specific; we pack
#     each operation's specific layout.
#   - Errors surface as Python exceptions only at the wrapper layer;
#     the connector catches + degrades to safe-fail (returns
#     pushed=False).
# ---------------------------------------------------------------------------


class _BpfBindingUnavailable(Exception):
    """Raised when libc syscall() isn't accessible — non-Linux hosts
    or hardened sandboxes."""


@dataclass
class _BpfSyscall:
    """Thin ctypes wrapper around bpf(2). Stateless beyond the cached
    libc reference."""

    _libc: Optional[Any] = None

    def __post_init__(self) -> None:
        # Hard platform guard — even though libSystem on macOS has a
        # syscall() symbol, syscall numbers there don't match Linux.
        # Invoking syscall(280, ...) on Darwin could call a wrong
        # syscall (setattrlist family on some Darwin versions, etc.).
        # The bpf() interface only exists on Linux ≥ 3.18 (kernel
        # ≥ 5.7 for the LSM hooks vOS needs).
        if platform.system() != "Linux":
            raise _BpfBindingUnavailable(
                f"bpf() syscall not available on {platform.system()}; "
                f"LIVE mode requires Linux ≥ 5.7"
            )
        libname = ctypes.util.find_library("c")
        if libname is None:
            raise _BpfBindingUnavailable("libc not found via ctypes.util")
        try:
            self._libc = ctypes.CDLL(libname, use_errno=True)
        except OSError as exc:
            raise _BpfBindingUnavailable(f"failed to load libc: {exc}") from exc
        # syscall(int number, ...) — variadic. We pass long-sized args.
        self._libc.syscall.restype = ctypes.c_long

    # -----------------------------------------------------------------
    # bpf(cmd, attr_buf, size)
    # -----------------------------------------------------------------

    def _bpf(self, cmd: int, attr_buf: bytes) -> int:
        attr = ctypes.create_string_buffer(attr_buf, len(attr_buf))
        nr = _bpf_syscall_nr()
        rc = self._libc.syscall(
            ctypes.c_long(nr),
            ctypes.c_long(cmd),
            ctypes.cast(attr, ctypes.c_void_p),
            ctypes.c_long(len(attr_buf)),
        )
        if rc < 0:
            return -ctypes.get_errno()
        return rc

    # -----------------------------------------------------------------
    # BPF_OBJ_GET — open a pinned map by its bpffs path.
    # bpf_attr layout: { u64 pathname; u32 bpf_fd; u32 file_flags; }
    # -----------------------------------------------------------------

    def obj_get(self, pin_path: str) -> int:
        path_bytes = pin_path.encode("utf-8") + b"\x00"
        path_buf = ctypes.create_string_buffer(path_bytes)
        attr = struct.pack(
            "<QII",
            ctypes.cast(path_buf, ctypes.c_void_p).value or 0,
            0,  # bpf_fd unused
            0,  # file_flags unused
        )
        rc = self._bpf(_BPF_OBJ_GET, attr)
        # NOTE: path_buf MUST outlive the syscall — we keep a local
        # reference via the explicit variable here.
        _ = path_buf
        if rc < 0:
            raise OSError(-rc, f"BPF_OBJ_GET({pin_path}) failed")
        return rc

    # -----------------------------------------------------------------
    # BPF_MAP_UPDATE_ELEM
    # bpf_attr layout (UPDATE):
    #   { u32 map_fd; u32 _pad; u64 key_ptr; u64 value_ptr; u64 flags; }
    # -----------------------------------------------------------------

    BPF_ANY = 0
    BPF_NOEXIST = 1
    BPF_EXIST = 2

    def map_update_elem(
        self, map_fd: int, key: bytes, value: bytes, flags: int = BPF_ANY
    ) -> None:
        key_buf = ctypes.create_string_buffer(key, len(key))
        val_buf = ctypes.create_string_buffer(value, len(value))
        attr = struct.pack(
            "<IIQQQ",
            map_fd,
            0,
            ctypes.cast(key_buf, ctypes.c_void_p).value or 0,
            ctypes.cast(val_buf, ctypes.c_void_p).value or 0,
            flags,
        )
        rc = self._bpf(_BPF_MAP_UPDATE_ELEM, attr)
        _ = (key_buf, val_buf)
        if rc < 0:
            raise OSError(
                -rc,
                f"BPF_MAP_UPDATE_ELEM(map_fd={map_fd}) failed",
            )

    # -----------------------------------------------------------------
    # BPF_MAP_DELETE_ELEM
    # bpf_attr layout: { u32 map_fd; u32 _pad; u64 key_ptr; }
    # -----------------------------------------------------------------

    def map_delete_elem(self, map_fd: int, key: bytes) -> bool:
        key_buf = ctypes.create_string_buffer(key, len(key))
        attr = struct.pack(
            "<IIQ",
            map_fd,
            0,
            ctypes.cast(key_buf, ctypes.c_void_p).value or 0,
        )
        rc = self._bpf(_BPF_MAP_DELETE_ELEM, attr)
        _ = key_buf
        if rc < 0:
            # ENOENT (-2) is "no such entry" — not an error, just False.
            if rc == -errno_mod.ENOENT:
                return False
            raise OSError(
                -rc,
                f"BPF_MAP_DELETE_ELEM(map_fd={map_fd}) failed",
            )
        return True


# ---------------------------------------------------------------------------
# Map key/value packing — KEEP IN SYNC with kernel/include/vos/taint_maps.h
# ---------------------------------------------------------------------------

# struct vos3_taint_map_key { u32 pid; u32 fd; }
_KEY_FMT = "<II"
_KEY_SIZE = struct.calcsize(_KEY_FMT)

# struct vos3_taint_color_entry __attribute__((packed)) {
#   u8 colors[65536]; u32 length; u8 max_color; u8 sink_kind;
#   u8 sink_max_label; u8 mode; u64 sha_low; u64 sha_high; u64 pushed_ns;
# }
_VALUE_FMT = f"<{VOS3_TAINT_MAX_COLOR_BYTES}sIBBBBQQQ"
_VALUE_SIZE = struct.calcsize(_VALUE_FMT)


def _pack_map_key(pid: int, fd: int) -> bytes:
    return struct.pack(_KEY_FMT, pid & 0xFFFFFFFF, fd & 0xFFFFFFFF)


def _pack_map_value(
    *,
    colors: bytes,
    max_color: int,
    sink_kind: int,
    sink_max_label: int,
    mode: int,
    sha256_low: int,
    sha256_high: int,
    pushed_ns: int,
) -> bytes:
    # Right-pad the color array to the full 64 KB; kernel ignores
    # bytes past `length`.
    padded = colors + b"\x00" * (VOS3_TAINT_MAX_COLOR_BYTES - len(colors))
    return struct.pack(
        _VALUE_FMT,
        padded,
        len(colors),
        max_color,
        sink_kind,
        sink_max_label,
        mode,
        sha256_low,
        sha256_high,
        pushed_ns,
    )


def _sha256_pack(sha256_hex: str) -> tuple[int, int]:
    """Pack first 16 bytes of SHA-256 hex into (high, low) u64s, matching
    kernel/include/vos/taint_maps.h vos3_taint_sha_pack()."""
    if not sha256_hex:
        return 0, 0
    raw = (
        bytes.fromhex(sha256_hex[:32])
        if len(sha256_hex) >= 32
        else bytes.fromhex(sha256_hex).ljust(16, b"\x00")
    )
    high = int.from_bytes(raw[:8], "big")
    low = int.from_bytes(raw[8:16], "big")
    return high, low


# ---------------------------------------------------------------------------
# Atomic Mark-and-Push wire format — Finding B3-1 closure (Option (a)).
# KEEP IN SYNC with struct vos3_taint_mark_push_arg in
# kernel/include/vos/taint_maps.h: a 16-byte header
#   { u32 abi_version; u32 fd; u32 flags; u32 _pad; }
# prepended to the UNCHANGED struct vos3_taint_color_entry (_VALUE_FMT). The
# value half is therefore byte-identical to a plain BPF_MAP_UPDATE_ELEM value
# (invariant G5).
# ---------------------------------------------------------------------------

VOS3_TAINT_MARK_PUSH_ABI = 1
VOS3_TAINT_MP_REPLACE = 1 << 0
VOS3_TAINT_MP_ONESHOT = 1 << 1
_MARK_PUSH_HDR_FMT = "<IIII"
_MARK_PUSH_HDR_SIZE = struct.calcsize(_MARK_PUSH_HDR_FMT)  # 16
_MARK_PUSH_SIZE = _MARK_PUSH_HDR_SIZE + _VALUE_SIZE  # 16 + 65568 = 65584


def _pack_mark_push_arg(
    *,
    fd: int,
    colors: bytes,
    sink_kind: SinkKind,
    sink_max_label: TaintLabel,
    mode: TaintMode = TaintMode.PER_FD,
    claimed_max_color: int,
    abi: int = VOS3_TAINT_MARK_PUSH_ABI,
    flags: int = 0,
    sha256_hex: str = "",
    pushed_ns: int = 0,
) -> bytes:
    """Pack a ``vos3_taint_mark_push_arg`` — the exact bytes the kernel ioctl
    ``VOS3_TAINT_IOC_MARK_PUSH`` receives. The value half is produced by the
    SAME ``_pack_map_value`` used for BPF_MAP_UPDATE_ELEM, so the embedded entry
    struct is byte-identical (invariant G5). ``claimed_max_color`` is whatever
    the (possibly compromised) caller asserts — the commit re-derives and
    overrides it (invariant G4)."""
    sha_high, sha_low = _sha256_pack(sha256_hex)
    value = _pack_map_value(
        colors=colors,
        max_color=claimed_max_color,
        sink_kind=int(sink_kind),
        sink_max_label=int(sink_max_label),
        mode=int(mode),
        sha256_low=sha_low,
        sha256_high=sha_high,
        pushed_ns=pushed_ns,
    )
    header = struct.pack(_MARK_PUSH_HDR_FMT, abi, fd, flags, 0)
    return header + value


def _unpack_mark_push_arg(packed: bytes) -> tuple[int, int, int, dict]:
    """Decode a ``vos3_taint_mark_push_arg`` into (abi, fd, flags, entry).
    Raises ValueError on a wrong-sized arg BEFORE any caller mutates state, so
    a truncated/oversized push commits nothing (no partial / split state)."""
    if len(packed) != _MARK_PUSH_SIZE:
        raise ValueError(
            f"mark_push arg must be exactly {_MARK_PUSH_SIZE} bytes, "
            f"got {len(packed)}"
        )
    abi, fd, flags, _pad = struct.unpack(
        _MARK_PUSH_HDR_FMT, packed[:_MARK_PUSH_HDR_SIZE]
    )
    (
        padded,
        length,
        max_color,
        sink_kind,
        sink_max_label,
        mode,
        _sha_low,
        _sha_high,
        pushed_ns,
    ) = struct.unpack(_VALUE_FMT, packed[_MARK_PUSH_HDR_SIZE:])
    entry = {
        "colors": padded,
        "length": length,
        "claimed_max": max_color,
        "sink_kind": sink_kind,
        "sink_max_label": sink_max_label,
        "mode": mode,
        "pushed_ns": pushed_ns,
    }
    return abi, fd, flags, entry


def discover_pidns() -> Optional[tuple[int, int]]:
    """Phase 20: discover THIS process's pid-namespace identity as the kernel
    sees it — the ``(dev, ino)`` that ``bpf_get_ns_current_pid_tgid()`` expects.

    Follows the ``/proc/self/ns/pid`` magic symlink to the nsfs inode (the
    earlier 19.6b bug was statting the symlink itself, not the target — so we
    ``os.stat`` WITHOUT ``follow_symlinks=False``). Returns ``(st_dev, st_ino)``
    or ``None`` when there is no procfs (non-Linux dev hosts), so callers no-op
    cleanly instead of guessing. No hardcoded namespace values anywhere.
    """
    try:
        st = os.stat("/proc/self/ns/pid")  # follows the magic symlink -> nsfs inode
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    return (int(st.st_dev), int(st.st_ino))


def _detect_live_mode_capable() -> bool:
    """LIVE mode requires:
      - Linux (not Darwin/Win)
      - bpffs mounted at /sys/fs/bpf
      - libbpf available (we'd ctypes.CDLL it)

    For the skeleton we only check the platform + bpffs. Real libbpf
    binding is Sprint-18 work. If the connector ever needs to be
    LIVE on dev, set VOS3_TAINT_GATE_FORCE_LIVE=1 to bypass the
    detection (used by integration tests on a real Linux runner)."""
    if os.environ.get("VOS3_TAINT_GATE_FORCE_LIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    if platform.system() != "Linux":
        return False
    return os.path.isdir("/sys/fs/bpf")


@dataclass
class KernelGateConnector:
    """Userspace bridge for Cluster C-2 byte-level write-gate."""

    # If True, ALWAYS use MOCK regardless of platform. Tests use this.
    force_mock: bool = False
    # Per-fd policy overrides — caller can pin a tighter sink ceiling.
    policy_overrides: dict[SinkKind, TaintLabel] = field(default_factory=dict)

    _mode: GateMode = field(init=False)
    _mock_table: dict[tuple[int, int], dict[str, Any]] = field(
        default_factory=dict, init=False
    )
    # Per-fd MARK set (Finding B3-1 / Option (a)). A (pid, fd) here is "gated"
    # — committed atomically via mark_push(). MARKED + missing entry => DENY.
    _mock_marks: set[tuple[int, int]] = field(default_factory=set, init=False)
    # Phase 20: discovered pid-namespace identity (dev, ino), or None.
    _pidns: Optional[tuple[int, int]] = field(default=None, init=False)
    _decisions: list[GateDecision] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    stats: KernelGateStats = field(default_factory=KernelGateStats, init=False)

    # LIVE-mode resources (Sprint 18 / Wave 3.B).
    _bpf: Optional["_BpfSyscall"] = field(default=None, init=False)
    _map_fd: Optional[int] = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.force_mock or not _detect_live_mode_capable():
            self._mode = GateMode.MOCK
            logger.info(
                "[kernel_gate_connector] MOCK mode (no live BPF map). "
                "Pushes recorded in-memory only; not enforced by the kernel."
            )
            return

        # LIVE attempt — try to bind bpf() + open the pinned map.
        try:
            self._bpf = _BpfSyscall()
        except _BpfBindingUnavailable as exc:
            self._mode = GateMode.MOCK
            logger.warning(
                "[kernel_gate_connector] LIVE requested but bpf() "
                "binding unavailable (%s); falling back to MOCK. "
                "Pushes will NOT be enforced by the kernel.",
                exc,
            )
            return

        try:
            self._map_fd = self._bpf.obj_get(VOS3_TAINT_MAP_PIN_PATH)
        except OSError as exc:
            self._mode = GateMode.MOCK
            self._bpf = None
            logger.warning(
                "[kernel_gate_connector] LIVE requested but pinned map "
                "at %s could not be opened (errno=%d %s); falling back "
                "to MOCK. Load the kernel-side program via `bpftool prog "
                "load kernel/bpf/taint_gate.bpf.o "
                "%s autoattach` first.",
                VOS3_TAINT_MAP_PIN_PATH,
                exc.errno,
                exc.strerror,
                "/sys/fs/bpf/vos3/taint_gate_prog",
            )
            return

        self._mode = GateMode.LIVE
        logger.info(
            "[kernel_gate_connector] LIVE mode at %s (map_fd=%d)",
            VOS3_TAINT_MAP_PIN_PATH,
            self._map_fd,
        )

    @property
    def mode(self) -> GateMode:
        return self._mode

    # -----------------------------------------------------------------
    # Sink-policy lookup
    # -----------------------------------------------------------------

    def sink_max_label(self, sink: SinkKind) -> TaintLabel:
        if sink in self.policy_overrides:
            return self.policy_overrides[sink]
        return DEFAULT_SINK_POLICY[sink]

    # -----------------------------------------------------------------
    # Push API
    # -----------------------------------------------------------------

    def push_tainted_buffer(
        self,
        *,
        fd: int,
        pid: int,
        buffer: Any,
        sink_kind: SinkKind,
        taint_mode: TaintMode = TaintMode.PER_FD,
    ) -> PushReceipt:
        """Push the byte-color array for `buffer` into the kernel map
        keyed by (pid, fd).

        `buffer` is duck-typed: must expose `.content: bytes`,
        `.colors: bytes`, `.sha256: str`, and `.max_color()` returning
        a TaintLabel-int. The Cluster C-1 TaintedBuffer satisfies this.
        Passing a buffer with `len(content) > 64 KB` triggers chunked
        push (one map entry per 64 KB slice). The PushReceipt reports
        the OVERALL push outcome; per-chunk failures roll up.

        `taint_mode` selects between PER_FD (default, scalar max_color)
        and PER_BYTE (full colors[] walked by the kernel hook over the
        actual write range — Sprint 18 / Wave 3.C). PER_BYTE requires
        a Linux kernel ≥ 5.17 with bpf_loop() support.
        """
        if not isinstance(fd, int) or fd < 0:
            raise ValueError(f"invalid fd: {fd!r}")
        if not isinstance(pid, int) or pid < 0:
            raise ValueError(f"invalid pid: {pid!r}")
        if buffer is None:
            raise ValueError("buffer is required")

        try:
            content = buffer.content
            colors = buffer.colors
            sha256 = buffer.sha256
            max_color = TaintLabel(int(buffer.max_color()))
        except AttributeError as exc:
            raise TypeError(f"buffer is missing TaintedBuffer-shaped attributes: {exc}")

        if len(content) != len(colors):
            raise ValueError(
                f"buffer invariant violated: len(content)={len(content)} "
                f"!= len(colors)={len(colors)}"
            )

        sink_max = self.sink_max_label(sink_kind)

        # Chunk if too large for a single map entry.
        chunks: list[tuple[bytes, bytes]] = []
        if len(content) <= VOS3_TAINT_MAX_COLOR_BYTES:
            chunks.append((content, colors))
        else:
            for off in range(0, len(content), VOS3_TAINT_MAX_COLOR_BYTES):
                end = min(off + VOS3_TAINT_MAX_COLOR_BYTES, len(content))
                chunks.append((content[off:end], colors[off:end]))

        pushed_ok = True
        pushed_ns = time.monotonic_ns()
        with self._lock:
            for chunk_content, chunk_colors in chunks:
                if not self._push_one(
                    fd=fd,
                    pid=pid,
                    content=chunk_content,
                    colors=chunk_colors,
                    sha256=sha256,
                    sink_kind=sink_kind,
                    sink_max=sink_max,
                    pushed_ns=pushed_ns,
                    taint_mode=taint_mode,
                ):
                    pushed_ok = False
                self.stats.chunks_emitted += 1
            if pushed_ok:
                self.stats.pushes += 1
            else:
                self.stats.push_failures += 1

        return PushReceipt(
            pushed=pushed_ok,
            mode=self._mode,
            pid=pid,
            fd=fd,
            length=len(content),
            max_color=max_color,
            sink_kind=sink_kind,
            sink_max_label=sink_max,
            buffer_sha256=sha256,
            pushed_ns=pushed_ns,
            reason="ok" if pushed_ok else "chunk push rejected by kernel",
        )

    def _push_one(
        self,
        *,
        fd: int,
        pid: int,
        content: bytes,
        colors: bytes,
        sha256: str,
        sink_kind: SinkKind,
        sink_max: TaintLabel,
        pushed_ns: int,
        taint_mode: TaintMode = TaintMode.PER_FD,
    ) -> bool:
        max_color_val = max(colors) if colors else int(TaintLabel.PUBLIC)
        if self._mode == GateMode.MOCK:
            self._mock_table[(pid, fd)] = {
                "colors": colors,
                "length": len(content),
                "max_color": max_color_val,
                "sink_kind": int(sink_kind),
                "sink_max_label": int(sink_max),
                "sha256": sha256,
                "pushed_ns": pushed_ns,
                "taint_mode": int(taint_mode),
            }
            return True
        # LIVE path — bpf(BPF_MAP_UPDATE_ELEM) via ctypes
        # (Sprint 18 / Wave 3.B + 3.C).
        if self._bpf is None or self._map_fd is None:
            # Shouldn't happen — __post_init__ either bound LIVE
            # successfully or fell back to MOCK. Defensive log.
            logger.error(
                "[kernel_gate_connector] LIVE mode flag set but bpf "
                "binding missing; push for (pid=%d, fd=%d) DROPPED.",
                pid,
                fd,
            )
            return False
        sha_high, sha_low = _sha256_pack(sha256)
        key = _pack_map_key(pid, fd)
        value = _pack_map_value(
            colors=colors,
            max_color=max_color_val,
            sink_kind=int(sink_kind),
            sink_max_label=int(sink_max),
            mode=int(taint_mode),
            sha256_low=sha_low,
            sha256_high=sha_high,
            pushed_ns=pushed_ns,
        )
        try:
            self._bpf.map_update_elem(self._map_fd, key, value)
        except OSError as exc:
            logger.error(
                "[kernel_gate_connector] LIVE push (pid=%d, fd=%d) "
                "rejected by kernel: errno=%d %s",
                pid,
                fd,
                exc.errno,
                exc.strerror,
            )
            return False
        return True

    # -----------------------------------------------------------------
    # Atomic Mark-and-Push — Finding B3-1 closure (Option (a)).
    # -----------------------------------------------------------------

    def mark_push(self, packed_arg: bytes, *, pid: int) -> PushReceipt:
        """Atomic Mark-and-Push (design spec §3). Consumes a packed
        ``vos3_taint_mark_push_arg`` (the exact bytes the kernel ioctl
        ``VOS3_TAINT_IOC_MARK_PUSH`` receives) and, in ONE operation, sets the
        per-fd MARK bit AND installs the color entry. The commit RE-DERIVES
        ``max(colors[0:length])`` and OVERRIDES the caller-supplied
        ``max_color`` (invariant G4), so a lying/compromised arg cannot
        under-report past the sink ceiling.

        Validation happens before any state mutation, so a malformed arg
        (wrong size, bad ABI, negative ids) commits nothing — there is no
        partial / split-push window (the structural property that closes the
        B3-1 race).

        MOCK mode performs the same marshalling in-memory so the policy is
        exercised host-side. LIVE mode would issue the ioctl on the control
        device (Linux >= 5.17 BPF-LSM) — that ctypes binding is Sprint-19.2
        work and is unavailable on non-Linux dev hosts, which therefore stay
        in MOCK."""
        abi, fd, _flags, entry = _unpack_mark_push_arg(packed_arg)
        if abi != VOS3_TAINT_MARK_PUSH_ABI:
            raise ValueError(f"unsupported mark_push ABI {abi}")
        if fd < 0 or pid < 0:
            raise ValueError("fd and pid must be non-negative")

        length = entry["length"]
        colors = entry["colors"][:length]
        # G4 — re-derive; the caller's claimed max_color is NOT trusted
        # (mirrors _push_one's recompute and the kernel commit handler).
        real_max = max(colors) if colors else int(TaintLabel.PUBLIC)
        sink_max = TaintLabel(entry["sink_max_label"])
        key = (pid, fd)

        with self._lock:
            if self._mode == GateMode.MOCK:
                self._mock_marks.add(key)
                self._mock_table[key] = {
                    "colors": colors,
                    "length": length,
                    "max_color": real_max,
                    "claimed_max": entry["claimed_max"],
                    "sink_kind": int(entry["sink_kind"]),
                    "sink_max_label": int(sink_max),
                    "sha256": "",
                    "pushed_ns": entry["pushed_ns"],
                    "taint_mode": int(entry["mode"]),
                }
                self.stats.pushes += 1
                return PushReceipt(
                    pushed=True,
                    mode=self._mode,
                    pid=pid,
                    fd=fd,
                    length=length,
                    max_color=TaintLabel(real_max),
                    sink_kind=SinkKind(int(entry["sink_kind"])),
                    sink_max_label=sink_max,
                    buffer_sha256="",
                    pushed_ns=entry["pushed_ns"],
                    reason="mark+push committed (MOCK; not kernel-enforced)",
                )

        # LIVE — ioctl(VOS3_TAINT_IOC_MARK_PUSH) binding is Sprint-19.2 work
        # (not reachable on this host). Fail-honest: do not silently claim
        # enforcement that did not happen.
        logger.error(
            "[kernel_gate_connector] mark_push requested in LIVE mode but the "
            "VOS3_TAINT_IOC_MARK_PUSH ioctl binding is not wired yet "
            "(Sprint 19.2); (pid=%d, fd=%d) NOT marked/pushed.",
            pid,
            fd,
        )
        self.stats.push_failures += 1
        return PushReceipt(
            pushed=False,
            mode=self._mode,
            pid=pid,
            fd=fd,
            length=length,
            max_color=TaintLabel(real_max),
            sink_kind=SinkKind(int(entry["sink_kind"])),
            sink_max_label=sink_max,
            buffer_sha256="",
            pushed_ns=entry["pushed_ns"],
            reason="LIVE ioctl binding not wired (Sprint 19.2)",
        )

    def is_marked(self, *, fd: int, pid: int) -> bool:
        """True if (pid, fd) carries the per-fd MARK bit."""
        with self._lock:
            return (pid, fd) in self._mock_marks

    def gc_entry(self, *, fd: int, pid: int) -> None:
        """MOCK-only: model GC / TTL expiry — drop the color entry but KEEP the
        fd's MARK bit, so the next write fails closed. Mirrors the kernel
        dropping a stale map value while the fd is still open + marked."""
        if self._mode != GateMode.MOCK:
            raise RuntimeError("gc_entry is MOCK-only")
        with self._lock:
            self._mock_table.pop((pid, fd), None)

    def peek_entry(self, *, fd: int, pid: int) -> Optional[dict]:
        """MOCK-only introspection: the stored map entry dict (or None). Used by
        the B3 audit suite to assert the re-derived max_color (G4)."""
        with self._lock:
            return self._mock_table.get((pid, fd))

    # -----------------------------------------------------------------
    # Simulate (MOCK-only) — let tests assert the kernel's response
    # without needing a real LSM. Maps to "what the audit ring would
    # have shown if this fd had been written to".
    # -----------------------------------------------------------------

    def simulate_write(
        self,
        *,
        fd: int,
        pid: int,
        length: Optional[int] = None,
        write_start: int = 0,
    ) -> GateDecision:
        """Simulate the kernel's decision for a write of `length` bytes
        starting at offset `write_start` in the pushed TaintedBuffer.

        Semantics mirror the kernel-side LSM hook:
          - PER_FD mode (default): decision = max_color > sink_max_label?
            The write_start/length are recorded but don't affect the
            max_color comparison (matches the kernel skeleton's
            vos3_max_color_per_fd path).
          - PER_BYTE mode (Sprint 20 / Primitive (b)): decision walks
            colors[write_start : write_start + min(length, 64 KiB)] and
            takes the max over that exact range, with an early-exit
            deny_off on the first byte exceeding the sink ceiling. This
            is the kernel-side equivalent of Cluster C-1's
            slice-then-egress idiom, now over the full per-fd budget.

        On entries pushed without an explicit `taint_mode`, the entry's
        recorded mode is used. Default for old-API callers is PER_FD.
        """
        if self._mode != GateMode.MOCK:
            raise RuntimeError(
                "simulate_write is MOCK-only — use the audit ring in LIVE mode"
            )
        with self._lock:
            self.stats.mock_simulate_calls += 1
            entry = self._mock_table.get((pid, fd))
            marked = (pid, fd) in self._mock_marks
        if entry is None:
            if marked:
                # Finding B3-1 closure (Option (a)): a MARKED fd whose color
                # entry is absent (GC / TTL / tamper) FAILS CLOSED — never the
                # historical "no entry -> default ALLOW".
                gd = GateDecision(
                    decision=DecisionKind.DENY,
                    pid=pid,
                    fd=fd,
                    length=length or 0,
                    max_color=TaintLabel.PUBLIC,
                    sink_max_label=TaintLabel.PUBLIC,
                    errno=-13,  # -EPERM
                    buffer_sha256="",
                    reason="marked fd, no entry — fail-closed (B3-1)",
                )
                with self._lock:
                    self.stats.decisions_deny += 1
                    self._decisions.append(gd)
                return gd
            # UNMARKED fd never carried a TaintedBuffer → untainted I/O is not
            # gated (invariant G3). This is the legacy default, unchanged.
            return GateDecision(
                decision=DecisionKind.ALLOW,
                pid=pid,
                fd=fd,
                length=length or 0,
                max_color=TaintLabel.PUBLIC,
                sink_max_label=TaintLabel.PUBLIC,
                errno=0,
                buffer_sha256="",
                reason="no entry for (pid, fd) — default ALLOW (unmarked)",
            )

        entry_mode = TaintMode(entry.get("taint_mode", int(TaintMode.PER_FD)))
        sink_max = TaintLabel(entry["sink_max_label"])

        if entry_mode == TaintMode.PER_BYTE:
            # Walk the actual write range in the colors[] array.
            # Sprint 20 / Primitive (b): the kernel's verifier-clean
            # "loop dance" (outer bpf_loop over constant chunks + inner
            # unrolled masked scan) covers the FULL 64 KiB per-fd budget,
            # not the old 4096-byte single-bpf_loop cap. MOCK mirrors that
            # coverage so a toxic byte anywhere in the pushed slice is
            # caught, and the early-exit clean-prefix semantics hold.
            PERBYTE_BOUND = VOS3_TAINT_MAX_COLOR_BYTES  # 64 KiB
            colors_arr = entry["colors"]
            stored_length = entry["length"]
            span_end = min(
                stored_length,
                write_start
                + min(length if length is not None else stored_length, PERBYTE_BOUND),
            )
            if write_start >= span_end:
                # Nothing to scan — defensive allow with PUBLIC color.
                max_color_val = int(TaintLabel.PUBLIC)
                deny_off = -1
            else:
                slice_bytes = colors_arr[write_start:span_end]
                max_color_val = (
                    max(slice_bytes) if slice_bytes else int(TaintLabel.PUBLIC)
                )
                # Early-exit offset: first byte exceeding the sink ceiling
                # (mirrors the kernel callback's deny_off), -1 if none.
                ceiling = int(TaintLabel(entry["sink_max_label"]))
                deny_off = next(
                    (write_start + i for i, c in enumerate(slice_bytes) if c > ceiling),
                    -1,
                )
            max_color = TaintLabel(max_color_val)
            reason_prefix = (
                f"per-byte max(colors[{write_start}:{span_end}])="
                f"{max_color_val}" + (f" deny_off={deny_off}" if deny_off >= 0 else "")
            )
        else:
            max_color = TaintLabel(entry["max_color"])
            reason_prefix = f"per-fd max_color={int(max_color)}"

        denied = int(max_color) > int(sink_max)
        decision = DecisionKind.DENY if denied else DecisionKind.ALLOW
        errno = -13 if denied else 0  # -EPERM
        with self._lock:
            if denied:
                self.stats.decisions_deny += 1
            else:
                self.stats.decisions_allow += 1
        gate_decision = GateDecision(
            decision=decision,
            pid=pid,
            fd=fd,
            length=length if length is not None else entry["length"],
            max_color=max_color,
            sink_max_label=sink_max,
            errno=errno,
            buffer_sha256=entry["sha256"],
            reason=(
                f"{reason_prefix} > sink_max={int(sink_max)} → -EPERM"
                if denied
                else f"{reason_prefix} ≤ sink_max={int(sink_max)} → ALLOW"
            ),
        )
        self._decisions.append(gate_decision)
        return gate_decision

    def evict(self, *, fd: int, pid: int) -> bool:
        """Remove a (pid, fd) entry from the map. Returns True if
        an entry was removed; False if there was no entry."""
        with self._lock:
            if self._mode == GateMode.MOCK:
                # close() semantics: drop BOTH the color entry and the MARK bit.
                self._mock_marks.discard((pid, fd))
                return self._mock_table.pop((pid, fd), None) is not None
            # LIVE: bpf(BPF_MAP_DELETE_ELEM) — Sprint 18 / Wave 3.B.
            if self._bpf is None or self._map_fd is None:
                return False
            try:
                return self._bpf.map_delete_elem(self._map_fd, _pack_map_key(pid, fd))
            except OSError as exc:
                logger.error(
                    "[kernel_gate_connector] LIVE evict (pid=%d, fd=%d) "
                    "rejected by kernel: errno=%d %s",
                    pid,
                    fd,
                    exc.errno,
                    exc.strerror,
                )
                return False

    def invalidate_slot(self, *, fd: int, pid: int) -> bool:
        """Token-rotation flush (Phase 28 / Gap G10): delete the (pid, fd) COLOR
        entry from the kernel map but KEEP the MARK bit. The next write on this
        fd then hits the kernel's marked-no-entry ladder and FAILS CLOSED
        (-EPERM) — closing the lifecycle gap between userspace token expiry and
        kernel-space enforcement.

        This is the PRODUCTION (both-mode) counterpart of the MOCK-only
        ``gc_entry``, and is distinct from ``evict``/close (which drops the MARK
        too, reverting the fd to untainted default-ALLOW). The explicit
        user-space map-clear transaction is ``bpf(BPF_MAP_DELETE_ELEM)`` on the
        colors map; the marks map is deliberately NOT cleared so the slot stays
        gated-and-denied until a fresh credential re-provisions it.

        Returns True iff a color entry was present to remove (idempotent: a
        second call returns False)."""
        with self._lock:
            if self._mode == GateMode.MOCK:
                # Drop the color entry; retain the MARK -> fail-closed stale slot.
                return self._mock_table.pop((pid, fd), None) is not None
            # LIVE: clear only the colors map; leave taint_marks so the kernel
            # hook denies (-EPERM) any write on the now-stale slot.
            if self._bpf is None or self._map_fd is None:
                return False
            try:
                return self._bpf.map_delete_elem(self._map_fd, _pack_map_key(pid, fd))
            except OSError as exc:
                logger.error(
                    "[kernel_gate_connector] LIVE invalidate_slot (pid=%d, fd=%d) "
                    "rejected by kernel: errno=%d %s",
                    pid,
                    fd,
                    exc.errno,
                    exc.strerror,
                )
                return False

    def decisions(self) -> tuple[GateDecision, ...]:
        with self._lock:
            return tuple(self._decisions)

    # -----------------------------------------------------------------
    # Phase 20 — pid-namespace provisioning + request-path egress gate.
    # -----------------------------------------------------------------

    def provision_pidns_config(self) -> Optional[tuple[int, int]]:
        """Discover this container's pid-namespace ``(dev, ino)`` and publish it
        to the kernel ``taint_config`` map (slot 0) so the LSM hook resolves the
        caller's tgid in OUR namespace (Phase 19.6). Idempotent; safe to call at
        startup. Returns the discovered ``(dev, ino)`` or ``None`` (no procfs).

        MOCK mode records it in-process only (no kernel map). LIVE mode writes
        the packed config via bpf(BPF_MAP_UPDATE_ELEM)."""
        ident = discover_pidns()
        if ident is None:
            logger.info(
                "[kernel_gate_connector] no /proc pidns (non-Linux?); "
                "pid-ns config not provisioned."
            )
            return None
        dev, ino = ident
        self._pidns = ident
        if self._mode == GateMode.MOCK:
            logger.info(
                "[kernel_gate_connector] MOCK: pid-ns discovered "
                "dev=%d ino=%d (not pushed to a kernel map).",
                dev,
                ino,
            )
            return ident
        # LIVE: write {dev, ino} into taint_config[0].
        if self._bpf is None:
            return ident
        try:
            cfg_fd = self._bpf.obj_get(VOS3_TAINT_CONFIG_PIN_PATH)
            key = struct.pack("<I", 0)
            val = struct.pack(_PIDNS_CFG_FMT, dev, ino)
            self._bpf.map_update_elem(cfg_fd, key, val)
            logger.info(
                "[kernel_gate_connector] LIVE: provisioned pid-ns "
                "config dev=%d ino=%d into %s",
                dev,
                ino,
                VOS3_TAINT_CONFIG_PIN_PATH,
            )
        except OSError as exc:
            logger.error(
                "[kernel_gate_connector] LIVE pid-ns provision failed "
                "(errno=%d %s); namespace translation will fall back to "
                "init-ns in the kernel.",
                exc.errno,
                exc.strerror,
            )
        return ident

    def enforce_egress(
        self,
        *,
        fd: int,
        pid: int,
        buffer: Any = None,
        sink_kind: SinkKind = SinkKind.NETWORK_EGRESS,
        taint_mode: "TaintMode" = TaintMode.PER_FD,
    ) -> GateDecision:
        """Request-path egress gate (Phase 20). Call this BEFORE an agent emits
        external I/O on ``fd``. Pushes the buffer's taint colors so the kernel
        LSM hook gates the subsequent write; returns the gate decision.

        - MOCK mode: records the push and returns the decision ``simulate_write``
          would yield (ALLOW unless a marked-no-entry / over-ceiling condition).
          Nothing is actually blocked — there is no kernel here.
        - LIVE mode: pushes colors; the kernel enforces the write and returns
          ``-EPERM`` on violation (surfaced to the caller as a failed write, not
          here). This call returns ALLOW from userspace in LIVE; the syscall is
          the enforcement point.

        Callers MUST fail closed on a DENY decision (raise / 403 / drop) and emit
        a security-audit record. This method never silently allows a DENY."""
        if buffer is not None:
            self.push_tainted_buffer(
                fd=fd,
                pid=pid,
                buffer=buffer,
                sink_kind=sink_kind,
                taint_mode=taint_mode,
            )
        if self._mode == GateMode.MOCK:
            decision = self.simulate_write(fd=fd, pid=pid)
            if decision.decision == DecisionKind.DENY:
                logger.warning(
                    "[SECURITY][egress-gate] DENY pid=%d fd=%d sink=%s "
                    "reason=%s (MOCK — caller must fail closed)",
                    pid,
                    fd,
                    sink_kind.name,
                    decision.reason,
                )
            return decision
        # LIVE: the kernel is the enforcement point; userspace returns ALLOW and
        # the gated write() will EPERM if the policy denies.
        return GateDecision(
            decision=DecisionKind.ALLOW,
            pid=pid,
            fd=fd,
            length=0,
            max_color=TaintLabel.PUBLIC,
            sink_max_label=self.sink_max_label(sink_kind),
            errno=0,
            buffer_sha256="",
            reason="LIVE: pushed; kernel enforces at write() (EPERM on violation)",
        )


# ---------------------------------------------------------------------------
# Process-wide singleton — the request-path entry point. Provisions the pid-ns
# config on first construction. Gated callers use get_kernel_gate().
# ---------------------------------------------------------------------------
_GATE_SINGLETON: Optional["KernelGateConnector"] = None
_GATE_LOCK = threading.Lock()


def get_kernel_gate() -> "KernelGateConnector":
    """Return the process-wide KernelGateConnector, constructing + provisioning
    the pid-ns config on first use. MOCK on non-Linux/dev hosts."""
    global _GATE_SINGLETON
    if _GATE_SINGLETON is None:
        with _GATE_LOCK:
            if _GATE_SINGLETON is None:
                gate = KernelGateConnector()
                try:
                    gate.provision_pidns_config()
                except Exception:  # never let gate init crash the app
                    logger.exception(
                        "[kernel_gate_connector] pid-ns provision "
                        "raised; continuing (gate still usable)."
                    )
                _GATE_SINGLETON = gate
    return _GATE_SINGLETON


def egress_gate_enabled() -> bool:
    """True iff the operator opted the live LSM egress gate into the request
    path via ``VOS3_ENABLE_LIVE_LSM_GATE``. Default OFF: dev hosts (macOS) and
    the CI suite never invoke the gate, so behaviour is unchanged unless a Linux
    deployment explicitly enables it."""
    return os.environ.get("VOS3_ENABLE_LIVE_LSM_GATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def init_egress_gate_if_enabled() -> Optional["KernelGateConnector"]:
    """App-startup hook. When the flag is set, construct + provision the gate
    (pid-ns config) and return it; otherwise return None (no-op). Call once at
    boot. Per-egress enforcement is then ``get_kernel_gate().enforce_egress()``
    at the actual I/O chokepoint — on Linux the kernel LSM enforces the write;
    on a dev host the gate is MOCK and records only. Flag-gated so wiring it in
    never regresses the dev/CI matrix."""
    if not egress_gate_enabled():
        return None
    gate = get_kernel_gate()
    logger.info(
        "[kernel_gate_connector] live LSM egress gate ENABLED via "
        "VOS3_ENABLE_LIVE_LSM_GATE (mode=%s)",
        gate.mode.name,
    )
    return gate


__all__ = [
    "DecisionKind",
    "DEFAULT_SINK_POLICY",
    "GateDecision",
    "GateMode",
    "KernelGateConnector",
    "KernelGateStats",
    "TaintMode",
    "PushReceipt",
    "SinkKind",
    "TaintLabel",
    "discover_pidns",
    "get_kernel_gate",
    "egress_gate_enabled",
    "init_egress_gate_if_enabled",
    "VOS3_TAINT_CONFIG_PIN_PATH",
    "VOS3_TAINT_MAP_MAX_ENTRIES",
    "VOS3_TAINT_MAX_COLOR_BYTES",
    "VOS3_TAINT_MAP_PIN_PATH",
    "VOS3_TAINT_MARK_PUSH_ABI",
    "VOS3_TAINT_MP_REPLACE",
    "VOS3_TAINT_MP_ONESHOT",
]
