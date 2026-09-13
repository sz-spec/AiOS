"""
backend/security/gpu_driver_gate.py
===================================

Sprint 21 / Item E1 — GPU driver-opacity fail-closed gate (NEW row).

What this is
------------

From the 80-problem agent-era catalog, E1:
  "NVIDIA driver opacity — the GPU kernel driver is a large, closed,
   ring-0 attack surface an agent's accelerator workload sits behind.
   A compromised or backdoored driver build undermines every isolation
   guarantee above it (E3 IOMMU, E4/E7 MIG, the Cluster C IFC story)."

NVIDIA's open GPU kernel module (the `open-gpu-kernel-modules` GPL/MIT
tree, default for datacenter Turing+ since R515) makes the ring-0
surface auditable and lets the kernel enforce module-signature
verification. The proprietary blob does neither. The catalog parked E1
as track-upstream because nothing *enforced* that a vOS deployment
actually ran the open, signed module — it would happily bind a GPU
behind an unsigned proprietary blob.

This module promotes E1 to **vOS-ENFORCED**: it probes the loaded
NVIDIA kernel module and **fail-closes GPU slot binding** unless it can
prove (1) the OPEN module is loaded (kernel not tainted PROPRIETARY by
it) AND (2) the module is SIGNED (kernel not tainted UNSIGNED_MODULE),
and — if the operator pins a build — that the loaded driver version
matches the attested build. A gate that refuses the unsafe bind is
enforcement; a doc that asks nicely is not.

Composition: runs alongside E3 (IOMMU) and E6 (perf-counter lockdown)
on the GPU bind path. Order is E1 (trusted driver) → E3 (no DMA
bypass) → O4 (isolated partition) → E6 (counters locked) → warp.

Enforcement contract
--------------------

    gate = GpuDriverGate()
    gate.require_trusted_driver_for_gpu_bind()   # raises
                                                 # UntrustedGpuDriver

Detection (Linux)
-----------------

  - /sys/module/nvidia present  → the module is loaded at all.
  - /sys/module/nvidia/taint    → per-module taint letters. We refuse on:
        'P'  proprietary module  → NOT the open module
        'E'  unsigned module     → signature not verified
        'O'  out-of-tree         → advisory only (the open module is
                                    out-of-tree too; not a refusal)
  - /sys/module/nvidia/version  → loaded driver version, compared to an
        operator-pinned attested build (VOS3_GPU_DRIVER_PINNED_VERSION)
        when set.

Honest scope ceilings
--------------------

  - The per-module `taint` letters are the kernel's own ground truth
    for proprietary/unsigned status — we do not re-implement signature
    verification, we read the kernel's verdict. Full supply-chain
    attestation (a Sigstore bundle binding the .ko build) is the deeper
    E1 follow-up; the version pin here is the interim attestation hook.
  - Non-Linux (macOS dev) → state UNKNOWN → fail-closed unless
    VOS3_GPU_DRIVER_DEV_OVERRIDE=1 (loud WARNING; never in production).
  - Reads /sys only; never mutates host state.
"""

from __future__ import annotations

import enum
import logging
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_GPU_DRIVER_DEV_OVERRIDE"
ENV_PINNED_VERSION = "VOS3_GPU_DRIVER_PINNED_VERSION"

_SYS_MODULE_NVIDIA = "/sys/module/nvidia"


class DriverState(enum.IntEnum):
    OPEN_SIGNED = 0  # open module, signed → trusted
    PROPRIETARY = 1  # proprietary blob loaded (taint 'P')
    UNSIGNED = 2  # module signature not verified (taint 'E')
    ABSENT = 3  # no nvidia module loaded
    VERSION_MISMATCH = 4  # loaded version != operator-pinned attested build
    UNKNOWN = 5  # can't probe (non-Linux / sysfs absent)


@dataclass(frozen=True)
class DriverReport:
    state: DriverState
    module_loaded: bool
    taint_flags: str
    version: str
    pinned_version: Optional[str]
    reason: str

    @property
    def trusted_for_gpu_bind(self) -> bool:
        return self.state == DriverState.OPEN_SIGNED


class UntrustedGpuDriver(Exception):
    """Raised by require_trusted_driver_for_gpu_bind() when the loaded
    GPU driver can't be proven open + signed (+ matching the pinned
    build). Fail-closed."""


@dataclass
class DriverGateStats:
    probes: int = 0
    gpu_bind_allowed: int = 0
    gpu_bind_refused: int = 0
    dev_overrides_used: int = 0


@dataclass
class GpuDriverGate:
    """Fail-closed GPU driver-trust gate for GPU slot binding."""

    # Test seam: inject a fake /sys/module/nvidia root.
    module_root: Optional[str] = None
    stats: DriverGateStats = field(default_factory=DriverGateStats)

    def _module_path(self) -> Path:
        if self.module_root:
            return Path(self.module_root)
        return Path(_SYS_MODULE_NVIDIA)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self) -> DriverReport:
        self.stats.probes += 1
        pinned = os.environ.get(ENV_PINNED_VERSION, "").strip() or None

        if platform.system() != "Linux" and self.module_root is None:
            return DriverReport(
                state=DriverState.UNKNOWN,
                module_loaded=False,
                taint_flags="",
                version="",
                pinned_version=pinned,
                reason=(
                    f"non-Linux host ({platform.system()}); GPU driver "
                    f"trust cannot be verified"
                ),
            )

        mod = self._module_path()
        if not mod.exists():
            return DriverReport(
                state=DriverState.ABSENT,
                module_loaded=False,
                taint_flags="",
                version="",
                pinned_version=pinned,
                reason="no nvidia kernel module loaded (/sys/module/nvidia absent)",
            )

        taint = self._read(mod / "taint")
        version = self._read(mod / "version")

        if "P" in taint:
            state = DriverState.PROPRIETARY
            reason = (
                "nvidia module is the PROPRIETARY blob (kernel taint 'P') — "
                "open GPU kernel module required"
            )
        elif "E" in taint:
            state = DriverState.UNSIGNED
            reason = (
                "nvidia module is UNSIGNED (kernel taint 'E') — a signed "
                "module build is required"
            )
        elif pinned is not None and version and version != pinned:
            state = DriverState.VERSION_MISMATCH
            reason = (
                f"loaded driver version {version!r} != operator-pinned "
                f"attested build {pinned!r}"
            )
        else:
            state = DriverState.OPEN_SIGNED
            reason = (
                f"open signed nvidia module (taint={taint or 'none'!r}, "
                f"version={version or 'unknown'!r})"
            )

        return DriverReport(
            state=state,
            module_loaded=True,
            taint_flags=taint,
            version=version,
            pinned_version=pinned,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Enforcement gate (fail-closed)
    # ------------------------------------------------------------------

    def require_trusted_driver_for_gpu_bind(self) -> DriverReport:
        """Refuse GPU slot binding unless the loaded driver is provably
        open + signed (+ matching the pinned build). Returns the report
        on success; raises UntrustedGpuDriver on failure. Dev override
        VOS3_GPU_DRIVER_DEV_OVERRIDE=1 downgrades to allow WITH a loud
        WARNING (never in production)."""
        report = self.probe()
        if report.trusted_for_gpu_bind:
            self.stats.gpu_bind_allowed += 1
            logger.info("[gpu_driver_gate] GPU bind ALLOWED — %s", report.reason)
            return report

        if self._env_true(ENV_DEV_OVERRIDE):
            self.stats.dev_overrides_used += 1
            self.stats.gpu_bind_allowed += 1
            logger.warning(
                "[gpu_driver_gate] GPU bind allowed by DEV OVERRIDE despite "
                "state=%s (%s). NEVER set %s in production — the GPU driver "
                "trust chain is unverified.",
                report.state.name,
                report.reason,
                ENV_DEV_OVERRIDE,
            )
            return report

        self.stats.gpu_bind_refused += 1
        logger.error(
            "[gpu_driver_gate] GPU bind REFUSED (fail-closed) — state=%s: %s",
            report.state.name,
            report.reason,
        )
        raise UntrustedGpuDriver(
            f"GPU driver not trusted (state={report.state.name}): "
            f"{report.reason}. Load the open, signed NVIDIA kernel module "
            f"(open-gpu-kernel-modules) and reboot, or set "
            f"{ENV_DEV_OVERRIDE}=1 for non-production dev."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read(p: Path) -> str:
        try:
            return p.read_text(errors="replace").strip()
        except (OSError, FileNotFoundError):
            return ""

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "DriverState",
    "DriverReport",
    "UntrustedGpuDriver",
    "GpuDriverGate",
    "DriverGateStats",
    "ENV_DEV_OVERRIDE",
    "ENV_PINNED_VERSION",
]
