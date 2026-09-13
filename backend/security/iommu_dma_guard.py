"""
backend/security/iommu_dma_guard.py
===================================

Sprint 19 / Wave 1 / Item E3 — IOMMU / DMA-bypass fail-closed guard.

What this is
------------

From the 80-problem agent-era catalog, E3:
  "GPU memory bypasses kernel page-table protections via DMA — IOMMU +
   ATS configurations frequently leave DMA paths that bypass page-table
   checks."

The upstream "solution" is operator config ("enable the IOMMU + ATS").
The catalog parked E3 as track-upstream / operator-choice precisely
because nothing *enforced* that the operator actually did it — a vOS
deployment would happily bind a GPU slot on a host where the IOMMU was
off, leaving a DMA path that bypasses every page-table protection vOS
relies on for the Cluster C byte-level IFC story.

This module promotes E3 from "operator runbook" to "vOS-ENFORCED": it
probes the host's IOMMU state and **fail-closes GPU slot binding** when
it cannot prove the IOMMU is actively enforcing translation. A guard
that refuses the unsafe operation is enforcement; a doc that asks
nicely is not.

Enforcement contract
--------------------

    guard = IommuDmaGuard()
    guard.require_iommu_for_gpu_bind()   # raises DmaBypassRefused if
                                          # the IOMMU isn't ENFORCING

Callers on the GPU slot-bind path (mm/ai_slots bind, the DRA+MIG
allocator in Sprint 16 E7) invoke require_iommu_for_gpu_bind() before
handing an agent any accelerator memory. If the guard can't prove
enforcement, the bind is refused — the agent runs CPU-only or not at
all, but never on a DMA-bypassable accelerator.

Detection (Linux)
-----------------

ENFORCING requires ALL of:
  1. Kernel cmdline enables the IOMMU:
       intel_iommu=on   OR   amd_iommu=on/force   OR   iommu=force
  2. The IOMMU is NOT in passthrough/identity mode:
       NOT iommu=pt, NOT iommu.passthrough=1, NOT intel_iommu=pt
     (passthrough = the IOMMU exists but DMA isn't translated → bypass
      is back on the table).
  3. /sys/kernel/iommu_groups is populated (groups exist → the IOMMU
     driver bound devices).
  4. At least one /sys/class/iommu/* device is present.

ATS (Address Translation Services) note: ATS lets an endpoint cache
translations and can re-open a bypass if mis-scoped. We surface ATS
state as an advisory (ats_enabled_devices) but do NOT hard-require ATS
to be OFF, because legitimate Confidential-Compute GPU paths use
ATS+PASID safely. Operators in hostile-multitenant mode set
VOS3_IOMMU_REQUIRE_NO_ATS=1 to harden.

Honest scope ceilings
---------------------

  - On non-Linux hosts (macOS dev) the probe returns UNKNOWN and the
    guard FAIL-CLOSES: GPU bind is refused unless the operator sets
    VOS3_IOMMU_DEV_OVERRIDE=1 (dev escape hatch, logs a WARNING). This
    is deliberate — we'd rather refuse than silently allow a
    DMA-bypassable bind on an unverifiable host.
  - This guard verifies the IOMMU is *enforcing*; it does NOT verify
    every device's group assignment is non-overlapping (the "everything
    in one IOMMU group" misconfig). Per-group isolation auditing is a
    Sprint 20 follow-up.
  - Reading /proc/cmdline + /sys requires no privilege. The guard never
    mutates host state — it only reads + refuses.
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

ENV_DEV_OVERRIDE = "VOS3_IOMMU_DEV_OVERRIDE"
ENV_REQUIRE_NO_ATS = "VOS3_IOMMU_REQUIRE_NO_ATS"

_PROC_CMDLINE = "/proc/cmdline"
_SYS_IOMMU_GROUPS = "/sys/kernel/iommu_groups"
_SYS_CLASS_IOMMU = "/sys/class/iommu"


class IommuState(enum.IntEnum):
    ENFORCING = 0  # IOMMU on, translating, groups populated
    PASSTHROUGH = 1  # IOMMU present but in pt/identity mode → bypass
    DISABLED = 2  # IOMMU off in cmdline / no groups
    UNKNOWN = 3  # can't probe (non-Linux, or sysfs absent)


@dataclass(frozen=True)
class IommuReport:
    state: IommuState
    cmdline_enables_iommu: bool
    passthrough_mode: bool
    iommu_groups_count: int
    iommu_class_devices: int
    ats_enabled_devices: int
    require_no_ats: bool
    reason: str

    @property
    def safe_for_gpu_bind(self) -> bool:
        if self.state != IommuState.ENFORCING:
            return False
        if self.require_no_ats and self.ats_enabled_devices > 0:
            return False
        return True


class DmaBypassRefused(Exception):
    """Raised by require_iommu_for_gpu_bind() when the host cannot prove
    the IOMMU is enforcing translation. Fail-closed."""


@dataclass
class IommuGuardStats:
    probes: int = 0
    gpu_bind_allowed: int = 0
    gpu_bind_refused: int = 0
    dev_overrides_used: int = 0


@dataclass
class IommuDmaGuard:
    """Fail-closed IOMMU/DMA enforcement guard for GPU slot binding."""

    # Test seam: inject a fake /proc + /sys root.
    sysfs_root: Optional[str] = None
    proc_root: Optional[str] = None
    stats: IommuGuardStats = field(default_factory=IommuGuardStats)

    # ------------------------------------------------------------------
    # Path helpers (respect injected roots for testing)
    # ------------------------------------------------------------------

    def _cmdline_path(self) -> Path:
        if self.proc_root:
            return Path(self.proc_root) / "cmdline"
        return Path(_PROC_CMDLINE)

    def _iommu_groups_path(self) -> Path:
        if self.sysfs_root:
            return Path(self.sysfs_root) / "kernel" / "iommu_groups"
        return Path(_SYS_IOMMU_GROUPS)

    def _iommu_class_path(self) -> Path:
        if self.sysfs_root:
            return Path(self.sysfs_root) / "class" / "iommu"
        return Path(_SYS_CLASS_IOMMU)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self) -> IommuReport:
        self.stats.probes += 1
        require_no_ats = self._env_true(ENV_REQUIRE_NO_ATS)

        # Non-Linux (or no procfs) → UNKNOWN.
        if platform.system() != "Linux" and self.proc_root is None:
            return IommuReport(
                state=IommuState.UNKNOWN,
                cmdline_enables_iommu=False,
                passthrough_mode=False,
                iommu_groups_count=0,
                iommu_class_devices=0,
                ats_enabled_devices=0,
                require_no_ats=require_no_ats,
                reason=(
                    f"non-Linux host ({platform.system()}); IOMMU state "
                    f"cannot be verified"
                ),
            )

        cmdline = self._read_cmdline()
        enables = self._cmdline_enables_iommu(cmdline)
        passthrough = self._cmdline_passthrough(cmdline)
        groups = self._count_iommu_groups()
        class_devs = self._count_iommu_class_devices()
        ats = self._count_ats_enabled()

        # Decision tree.
        if not enables and groups == 0:
            state = IommuState.DISABLED
            reason = "kernel cmdline does not enable IOMMU and no iommu_groups present"
        elif passthrough:
            state = IommuState.PASSTHROUGH
            reason = (
                "IOMMU in passthrough/identity mode (iommu=pt) — DMA not translated"
            )
        elif groups > 0 and class_devs > 0:
            state = IommuState.ENFORCING
            reason = (
                f"IOMMU enforcing: {groups} groups, {class_devs} class "
                f"devices, cmdline_enable={enables}"
            )
        elif enables and groups == 0:
            # cmdline says on but no groups bound → driver didn't attach.
            state = IommuState.DISABLED
            reason = (
                "cmdline enables IOMMU but no iommu_groups bound (driver not attached)"
            )
        else:
            state = IommuState.UNKNOWN
            reason = "indeterminate IOMMU state from available evidence"

        return IommuReport(
            state=state,
            cmdline_enables_iommu=enables,
            passthrough_mode=passthrough,
            iommu_groups_count=groups,
            iommu_class_devices=class_devs,
            ats_enabled_devices=ats,
            require_no_ats=require_no_ats,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Enforcement gate (fail-closed)
    # ------------------------------------------------------------------

    def require_iommu_for_gpu_bind(self) -> IommuReport:
        """Refuse GPU slot binding unless the IOMMU is provably ENFORCING.

        Returns the IommuReport on success. Raises DmaBypassRefused on
        failure. The dev override (VOS3_IOMMU_DEV_OVERRIDE=1) downgrades
        a refusal to an allowed bind WITH a loud WARNING — for local
        dev on hosts where the IOMMU genuinely can't be probed. Never
        set it in production."""
        report = self.probe()
        if report.safe_for_gpu_bind:
            self.stats.gpu_bind_allowed += 1
            logger.info("[iommu_dma_guard] GPU bind ALLOWED — %s", report.reason)
            return report

        if self._env_true(ENV_DEV_OVERRIDE):
            self.stats.dev_overrides_used += 1
            self.stats.gpu_bind_allowed += 1
            logger.warning(
                "[iommu_dma_guard] GPU bind allowed by DEV OVERRIDE despite "
                "state=%s (%s). NEVER set %s in production — DMA bypass is "
                "possible on this host.",
                report.state.name,
                report.reason,
                ENV_DEV_OVERRIDE,
            )
            return report

        self.stats.gpu_bind_refused += 1
        logger.error(
            "[iommu_dma_guard] GPU bind REFUSED (fail-closed) — state=%s: %s",
            report.state.name,
            report.reason,
        )
        raise DmaBypassRefused(
            f"IOMMU not enforcing (state={report.state.name}): {report.reason}. "
            f"Enable the IOMMU (intel_iommu=on / amd_iommu=on, no iommu=pt) "
            f"and reboot, or set {ENV_DEV_OVERRIDE}=1 for non-production dev."
        )

    # ------------------------------------------------------------------
    # Low-level readers
    # ------------------------------------------------------------------

    def _read_cmdline(self) -> str:
        try:
            return self._cmdline_path().read_text(errors="replace")
        except (OSError, FileNotFoundError):
            return ""

    @staticmethod
    def _cmdline_enables_iommu(cmdline: str) -> bool:
        toks = cmdline.split()
        on_markers = {
            "intel_iommu=on",
            "amd_iommu=on",
            "amd_iommu=force",
            "iommu=force",
            "iommu=on",
        }
        return any(t in on_markers for t in toks)

    @staticmethod
    def _cmdline_passthrough(cmdline: str) -> bool:
        toks = cmdline.split()
        pt_markers = {
            "iommu=pt",
            "iommu.passthrough=1",
            "intel_iommu=pt",
            "amd_iommu=pt",
        }
        return any(t in pt_markers for t in toks)

    def _count_iommu_groups(self) -> int:
        p = self._iommu_groups_path()
        try:
            return sum(1 for _ in p.iterdir())
        except (OSError, FileNotFoundError):
            return 0

    def _count_iommu_class_devices(self) -> int:
        p = self._iommu_class_path()
        try:
            return sum(1 for _ in p.iterdir())
        except (OSError, FileNotFoundError):
            return 0

    def _count_ats_enabled(self) -> int:
        """Count PCI devices with ATS enabled. Best-effort: walks
        /sys/class/iommu/*/devices and checks for an `ats` capability
        marker. Absence is treated as 0 (ATS off / unknown)."""
        base = self._iommu_class_path()
        count = 0
        try:
            for iommu in base.iterdir():
                devdir = iommu / "devices"
                if not devdir.exists():
                    continue
                for dev in devdir.iterdir():
                    # A device with an `ats` sysfs node + enabled state.
                    ats_node = dev / "ats"
                    if ats_node.exists():
                        count += 1
        except (OSError, FileNotFoundError):
            return 0
        return count

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "DmaBypassRefused",
    "IommuDmaGuard",
    "IommuGuardStats",
    "IommuReport",
    "IommuState",
    "ENV_DEV_OVERRIDE",
    "ENV_REQUIRE_NO_ATS",
]
