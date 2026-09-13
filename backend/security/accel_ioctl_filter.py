"""
backend/security/accel_ioctl_filter.py
=======================================

Sprint 21 / Item E2 — accelerator ioctl allowlist (seccomp-style,
fail-closed) (NEW row).

What this is
------------

From the 80-problem agent-era catalog, E2:
  "CUDA / ROCm ioctl fuzzer surface — the GPU userspace stack reaches
   the kernel driver through a very wide, lightly-validated ioctl
   interface on /dev/nvidiactl, /dev/nvidia*, /dev/nvidia-uvm,
   /dev/kfd. Fuzzers routinely find driver memory-corruption bugs
   reachable from an unprivileged ioctl — a compromised agent workload
   that can issue arbitrary ioctls to the accelerator fd has a direct
   ring-0 escalation path."

The defensive primitive the catalog calls for is a **seccomp-style
allowlist on the accelerator fd**: enumerate the small set of ioctls a
legitimate inference/training workload actually issues, and refuse
everything else. This module is the policy layer for that filter: it
decodes a Linux ioctl request code, checks it against a per-device
allowlist, and **fail-closes** (refuses) any request not on the list.

Default-deny is the whole point: an empty/limited allowlist means a
fuzzed or attacker-chosen ioctl the workload never legitimately needs
is refused, shrinking the reachable driver surface to the audited set.

Enforcement contract
--------------------

    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    filt.require_ioctl(request_code)   # raises IoctlBlocked if the
                                       # (type, nr) is not allowlisted

    # Inspection without raising:
    if filt.is_allowed(request_code): ...

Where this runs
---------------

The actual interception is a seccomp-bpf `SCMP_ACT_ERRNO` rule on
`ioctl` (argument-filtered by fd + request) or an LD_PRELOAD shim that
consults this oracle — both live at the sandbox-launch layer
(backend/sandbox/*). This module is the single source of truth for the
policy those enforcers compile, exactly as kernel_gate_connector.py is
the oracle the eBPF write-gate mirrors. Honest scope ceiling: this
module decides; the seccomp/preload layer enforces. On a host without
that layer wired, the filter still gates every call routed through
``require_ioctl`` at the Python boundary.

ioctl encoding
--------------

Linux ioctl request codes pack dir/size/type/nr (asm-generic/ioctl.h):
    bits  0..7   nr     (command number)
    bits  8..15  type   (the "magic", e.g. 'F' for NVIDIA)
    bits 16..29  size   (sizeof the arg struct)
    bits 30..31  dir    (none/write/read)
The allowlist is expressed as (type, nr) pairs — size/dir are advisory
(a driver may bump a struct size across versions; the command identity
is (type, nr)).
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_ACCEL_IOCTL_DEV_OVERRIDE"
ENV_EXTRA_ALLOW = "VOS3_ACCEL_IOCTL_EXTRA_ALLOW"  # "F:42,F:43" (hex type:nr)

# Linux asm-generic/ioctl.h bit layout.
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS  # 8
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS  # 16
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS  # 30


@dataclass(frozen=True)
class DecodedIoctl:
    raw: int
    dir: int
    type: int
    nr: int
    size: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.type, self.nr)

    @property
    def type_char(self) -> str:
        return chr(self.type) if 0x20 <= self.type <= 0x7E else f"0x{self.type:02x}"

    def __str__(self) -> str:
        return (
            f"ioctl(type={self.type_char!r}, nr={self.nr}, "
            f"size={self.size}, dir={self.dir}) [0x{self.raw:08x}]"
        )


def decode_ioctl(request: int) -> DecodedIoctl:
    if not isinstance(request, int) or request < 0:
        raise ValueError(f"ioctl request must be a non-negative int: {request!r}")
    return DecodedIoctl(
        raw=request,
        nr=(request >> _IOC_NRSHIFT) & ((1 << _IOC_NRBITS) - 1),
        type=(request >> _IOC_TYPESHIFT) & ((1 << _IOC_TYPEBITS) - 1),
        size=(request >> _IOC_SIZESHIFT) & ((1 << _IOC_SIZEBITS) - 1),
        dir=(request >> _IOC_DIRSHIFT) & 0x3,
    )


class AccelDevice(str, enum.Enum):
    NVIDIA_CTL = "nvidiactl"  # /dev/nvidiactl  (control)
    NVIDIA_GPU = "nvidia"  # /dev/nvidia0..N (per-GPU)
    NVIDIA_UVM = "nvidia-uvm"  # /dev/nvidia-uvm (unified memory)
    AMD_KFD = "kfd"  # /dev/kfd        (ROCm)


# NVIDIA control magic. The real NVIDIA ioctl set is large and version-
# specific; this is a CONSERVATIVE default of the handful a normal
# inference workload issues (card query, attach, alloc, free, map,
# event). Operators tune it via VOS3_ACCEL_IOCTL_EXTRA_ALLOW after
# auditing their stack — default-deny means an un-audited ioctl is
# refused, not silently permitted.
_NV_MAGIC = ord("F")  # 0x46

_DEFAULT_ALLOWLISTS: dict[AccelDevice, frozenset[tuple[int, int]]] = {
    AccelDevice.NVIDIA_CTL: frozenset(
        {
            (_NV_MAGIC, 0x2A),  # NV_ESC_CARD_INFO
            (_NV_MAGIC, 0x2B),  # NV_ESC_ENV_INFO
            (_NV_MAGIC, 0x29),  # NV_ESC_ATTACH_GPUS_TO_FD
            (_NV_MAGIC, 0x2D),  # NV_ESC_RM_ALLOC
            (_NV_MAGIC, 0x2E),  # NV_ESC_RM_FREE
            (_NV_MAGIC, 0x4A),  # NV_ESC_RM_MAP_MEMORY
            (_NV_MAGIC, 0x4B),  # NV_ESC_RM_UNMAP_MEMORY
            (_NV_MAGIC, 0x21),  # NV_ESC_RM_CONTROL
        }
    ),
    AccelDevice.NVIDIA_GPU: frozenset(
        {
            (_NV_MAGIC, 0x21),  # NV_ESC_RM_CONTROL
            (_NV_MAGIC, 0x2D),  # NV_ESC_RM_ALLOC
            (_NV_MAGIC, 0x4A),  # NV_ESC_RM_MAP_MEMORY
            (_NV_MAGIC, 0x4B),  # NV_ESC_RM_UNMAP_MEMORY
        }
    ),
    AccelDevice.NVIDIA_UVM: frozenset(
        {
            (0x0, 0x1),  # UVM_INITIALIZE
            (0x0, 0x2),  # UVM_DEINITIALIZE
            (0x0, 0x25),  # UVM_ALLOC
            (0x0, 0x26),  # UVM_FREE
        }
    ),
    AccelDevice.AMD_KFD: frozenset(
        {
            (ord("K"), 0x01),  # AMDKFD_IOC_GET_VERSION
            (ord("K"), 0x02),  # AMDKFD_IOC_CREATE_QUEUE
            (ord("K"), 0x07),  # AMDKFD_IOC_ALLOC_MEMORY_OF_GPU
        }
    ),
}


class IoctlBlocked(Exception):
    """Raised by require_ioctl() when a request's (type, nr) is not on the
    accelerator device's allowlist. Fail-closed."""

    def __init__(self, decoded: DecodedIoctl, device: "AccelDevice"):
        self.decoded = decoded
        self.device = device
        super().__init__(
            f"ioctl {decoded} not allowlisted for /dev/{device.value} "
            f"(default-deny)"
        )


@dataclass
class IoctlFilterStats:
    checks: int = 0
    allowed: int = 0
    blocked: int = 0
    dev_overrides_used: int = 0


@dataclass
class AccelIoctlFilter:
    """Default-deny ioctl allowlist for a single accelerator device fd."""

    device: AccelDevice
    allowlist: Optional[frozenset[tuple[int, int]]] = None
    stats: IoctlFilterStats = field(default_factory=IoctlFilterStats)

    def __post_init__(self) -> None:
        if self.allowlist is None:
            base = set(_DEFAULT_ALLOWLISTS.get(self.device, frozenset()))
        else:
            base = set(self.allowlist)
        base |= self._extra_from_env()
        self.allowlist = frozenset(base)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_allowed(self, request: int) -> bool:
        decoded = decode_ioctl(request)
        return decoded.key in (self.allowlist or frozenset())

    def require_ioctl(self, request: int) -> DecodedIoctl:
        """Allow an allowlisted ioctl (returns the decoded request); raise
        IoctlBlocked otherwise. Dev override VOS3_ACCEL_IOCTL_DEV_OVERRIDE=1
        downgrades a block to allow WITH a loud WARNING (never in
        production)."""
        self.stats.checks += 1
        decoded = decode_ioctl(request)
        if decoded.key in (self.allowlist or frozenset()):
            self.stats.allowed += 1
            return decoded

        if self._env_true(ENV_DEV_OVERRIDE):
            self.stats.dev_overrides_used += 1
            self.stats.allowed += 1
            logger.warning(
                "[accel_ioctl_filter] %s on /dev/%s allowed by DEV OVERRIDE "
                "(not allowlisted). NEVER set %s in production — the "
                "accelerator ioctl surface is unfiltered.",
                decoded,
                self.device.value,
                ENV_DEV_OVERRIDE,
            )
            return decoded

        self.stats.blocked += 1
        logger.error(
            "[accel_ioctl_filter] %s on /dev/%s REFUSED (fail-closed, " "default-deny)",
            decoded,
            self.device.value,
        )
        raise IoctlBlocked(decoded, self.device)

    def allow(self, type_: int, nr: int) -> None:
        """Add a (type, nr) to the allowlist after an operator audit."""
        self.allowlist = frozenset(set(self.allowlist or frozenset()) | {(type_, nr)})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extra_from_env() -> set[tuple[int, int]]:
        raw = os.environ.get(ENV_EXTRA_ALLOW, "").strip()
        out: set[tuple[int, int]] = set()
        if not raw:
            return out
        for tok in raw.split(","):
            tok = tok.strip()
            if not tok or ":" not in tok:
                continue
            t_str, n_str = tok.split(":", 1)
            try:
                t = (
                    int(t_str, 0)
                    if not (len(t_str) == 1 and not t_str.isdigit())
                    else ord(t_str)
                )
                n = int(n_str, 0)
            except ValueError:
                logger.warning(
                    "[accel_ioctl_filter] ignoring malformed %s entry %r",
                    ENV_EXTRA_ALLOW,
                    tok,
                )
                continue
            out.add((t & 0xFF, n & 0xFF))
        return out

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "AccelDevice",
    "AccelIoctlFilter",
    "DecodedIoctl",
    "IoctlBlocked",
    "IoctlFilterStats",
    "decode_ioctl",
    "ENV_DEV_OVERRIDE",
    "ENV_EXTRA_ALLOW",
]
