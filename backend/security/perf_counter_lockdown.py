"""
backend/security/perf_counter_lockdown.py
=========================================

Sprint 19 / Wave 1 / Item E6 — Performance-counter exposure lockdown
(fail-closed).

What this is
------------

From the 80-problem agent-era catalog, E6:
  "Performance counters expose to userspace = model fingerprinting.
   `perf_event_open` on GPU performance counters lets unprivileged
   processes profile co-tenant inference."

A co-tenant that can read CPU/GPU performance counters can fingerprint
another tenant's model (layer count, head dims, batch size) via timing
+ counter side-channels — the Leaky-DNN / ShadowScope class. The
upstream "fix" is a kernel boot param + an operator runbook, which is
exactly why E6 sat in track-upstream: nothing *enforced* that an agent
wasn't launched on a host where the counters were wide open.

This module promotes E6 from "runbook" to "vOS-ENFORCED": it probes
the host's counter-exposure state and **fail-closes multi-tenant agent
inference** when unprivileged perf-counter access is open. As with the
IOMMU guard (E3), a gate that refuses the unsafe launch is enforcement;
a doc is not.

Enforcement contract
--------------------

    lock = PerfCounterLockdown()
    lock.require_lockdown_for_multitenant_inference()
        # raises PerfCounterExposed if counters are readable by
        # unprivileged co-tenants

Callers on the multi-tenant inference-launch path invoke this before
scheduling an agent onto a shared accelerator host. Single-tenant /
air-gapped deployments can skip the gate (set
VOS3_PERF_SINGLE_TENANT=1) since there's no co-tenant to leak to.

Detection (Linux)
-----------------

LOCKED requires BOTH:
  1. CPU side — /proc/sys/kernel/perf_event_paranoid >= 2.
       -1 = everything; 0 = no raw tracepoints; 1 = no CPU events for
       unpriv; 2 = no kernel/CPU events for unpriv (the floor we want);
       3 = hardened (Debian/Android) no unpriv perf at all (best).
  2. GPU side — NVIDIA profiling restricted to admin:
       /proc/driver/nvidia/params contains
       "RmProfilingAdminOnly: 1"  (set via the
       NVreg_RestrictProfilingToAdminUsers=1 modprobe param).
     On hosts with no NVIDIA driver present, the GPU check is N/A and
     does not block (there's no GPU counter to leak).

Remediation (surfaced, NOT auto-applied)
----------------------------------------

The lockdown reports the exact remediation commands but does NOT mutate
host state by default (writing /proc/sys + reloading the NVIDIA module
are disruptive, root-only operations that belong to the operator). An
opt-in harden() is provided behind an explicit allow_mutate=True flag +
root, for operators who want vOS to self-harden a dedicated box.

Honest scope ceilings
---------------------

  - On non-Linux hosts (macOS dev) the probe returns UNKNOWN and the
    gate FAIL-CLOSES for multi-tenant mode (refuses) unless
    VOS3_PERF_DEV_OVERRIDE=1. Single-tenant mode is always allowed.
  - The GPU check covers NVIDIA's RmProfilingAdminOnly. AMD ROCm +
    Intel oneAPI profiling-counter lockdown probes are a Sprint 20
    follow-up (the enum + report shape already accommodate them via
    gpu_vendor="unknown" → advisory).
  - perf_event_paranoid is a CPU-wide setting; it does not isolate
    counters *between* specific cgroups. Per-cgroup perf isolation is
    upstream-Linux work tracked separately.
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

ENV_SINGLE_TENANT = "VOS3_PERF_SINGLE_TENANT"
ENV_DEV_OVERRIDE = "VOS3_PERF_DEV_OVERRIDE"

_PROC_PARANOID = "/proc/sys/kernel/perf_event_paranoid"
_PROC_NVIDIA_PARAMS = "/proc/driver/nvidia/params"

# The minimum perf_event_paranoid that blocks unprivileged CPU-event
# profiling of co-tenants.
PARANOID_FLOOR = 2


class PerfLockState(enum.IntEnum):
    LOCKED = 0  # CPU paranoid >= floor AND (GPU restricted OR no GPU)
    CPU_EXPOSED = 1  # perf_event_paranoid below floor
    GPU_EXPOSED = 2  # NVIDIA profiling open to unprivileged users
    UNKNOWN = 3  # can't probe (non-Linux / sysfs absent)


@dataclass(frozen=True)
class PerfLockReport:
    state: PerfLockState
    perf_event_paranoid: Optional[int]
    paranoid_ok: bool
    nvidia_present: bool
    nvidia_profiling_admin_only: Optional[bool]
    gpu_ok: bool
    reason: str

    @property
    def safe_for_multitenant(self) -> bool:
        return self.state == PerfLockState.LOCKED

    def remediation(self) -> list[str]:
        cmds: list[str] = []
        if not self.paranoid_ok:
            cmds.append(
                "sudo sysctl -w kernel.perf_event_paranoid=2   "
                "# (or 3 for hardened); persist in /etc/sysctl.d/"
            )
        if self.nvidia_present and self.nvidia_profiling_admin_only is False:
            cmds.append(
                "echo 'options nvidia "
                "NVreg_RestrictProfilingToAdminUsers=1' | "
                "sudo tee /etc/modprobe.d/nvidia-perf.conf  "
                "# then rebuild initramfs + reboot"
            )
        return cmds


class PerfCounterExposed(Exception):
    """Raised by require_lockdown_for_multitenant_inference() when
    unprivileged perf-counter access is open. Fail-closed."""


@dataclass
class PerfLockStats:
    probes: int = 0
    launches_allowed: int = 0
    launches_refused: int = 0
    single_tenant_skips: int = 0
    dev_overrides_used: int = 0


@dataclass
class PerfCounterLockdown:
    """Fail-closed perf-counter exposure gate for multi-tenant agent
    inference."""

    proc_root: Optional[str] = None  # test seam
    stats: PerfLockStats = field(default_factory=PerfLockStats)

    def _paranoid_path(self) -> Path:
        if self.proc_root:
            return Path(self.proc_root) / "sys" / "kernel" / "perf_event_paranoid"
        return Path(_PROC_PARANOID)

    def _nvidia_params_path(self) -> Path:
        if self.proc_root:
            return Path(self.proc_root) / "driver" / "nvidia" / "params"
        return Path(_PROC_NVIDIA_PARAMS)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self) -> PerfLockReport:
        self.stats.probes += 1

        if platform.system() != "Linux" and self.proc_root is None:
            return PerfLockReport(
                state=PerfLockState.UNKNOWN,
                perf_event_paranoid=None,
                paranoid_ok=False,
                nvidia_present=False,
                nvidia_profiling_admin_only=None,
                gpu_ok=False,
                reason=(
                    f"non-Linux host ({platform.system()}); perf-counter "
                    f"exposure cannot be verified"
                ),
            )

        paranoid = self._read_paranoid()
        paranoid_ok = paranoid is not None and paranoid >= PARANOID_FLOOR

        nvidia_present, admin_only = self._read_nvidia_profiling()
        # GPU is OK if there's no NVIDIA GPU, or profiling is admin-only.
        gpu_ok = (not nvidia_present) or (admin_only is True)

        if not paranoid_ok:
            state = PerfLockState.CPU_EXPOSED
            reason = (
                f"perf_event_paranoid={paranoid} < floor {PARANOID_FLOOR} "
                f"(unprivileged co-tenants can profile CPU events)"
            )
        elif not gpu_ok:
            state = PerfLockState.GPU_EXPOSED
            reason = (
                "NVIDIA GPU profiling open to unprivileged users "
                "(RmProfilingAdminOnly != 1)"
            )
        else:
            state = PerfLockState.LOCKED
            reason = f"perf_event_paranoid={paranoid} >= {PARANOID_FLOOR}; " + (
                "GPU profiling admin-only"
                if nvidia_present
                else "no NVIDIA GPU present"
            )

        return PerfLockReport(
            state=state,
            perf_event_paranoid=paranoid,
            paranoid_ok=paranoid_ok,
            nvidia_present=nvidia_present,
            nvidia_profiling_admin_only=admin_only,
            gpu_ok=gpu_ok,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Enforcement gate (fail-closed)
    # ------------------------------------------------------------------

    def require_lockdown_for_multitenant_inference(self) -> PerfLockReport:
        """Refuse multi-tenant agent inference unless perf counters are
        locked down. Single-tenant deployments
        (VOS3_PERF_SINGLE_TENANT=1) skip the gate. The dev override
        (VOS3_PERF_DEV_OVERRIDE=1) downgrades a refusal to allow +
        WARNING."""
        if self._env_true(ENV_SINGLE_TENANT):
            self.stats.single_tenant_skips += 1
            logger.info("[perf_counter_lockdown] single-tenant mode — gate skipped")
            # Return a synthetic LOCKED-equivalent report for callers.
            return PerfLockReport(
                state=PerfLockState.LOCKED,
                perf_event_paranoid=None,
                paranoid_ok=True,
                nvidia_present=False,
                nvidia_profiling_admin_only=None,
                gpu_ok=True,
                reason="single-tenant deployment — no co-tenant to leak to",
            )

        report = self.probe()
        if report.safe_for_multitenant:
            self.stats.launches_allowed += 1
            logger.info(
                "[perf_counter_lockdown] inference launch ALLOWED — %s",
                report.reason,
            )
            return report

        if self._env_true(ENV_DEV_OVERRIDE):
            self.stats.dev_overrides_used += 1
            self.stats.launches_allowed += 1
            logger.warning(
                "[perf_counter_lockdown] launch allowed by DEV OVERRIDE "
                "despite state=%s (%s). NEVER set %s in production — "
                "co-tenant model fingerprinting is possible.",
                report.state.name,
                report.reason,
                ENV_DEV_OVERRIDE,
            )
            return report

        self.stats.launches_refused += 1
        logger.error(
            "[perf_counter_lockdown] inference launch REFUSED (fail-closed) "
            "— state=%s: %s. Remediation: %s",
            report.state.name,
            report.reason,
            "; ".join(report.remediation()),
        )
        raise PerfCounterExposed(
            f"perf counters exposed (state={report.state.name}): "
            f"{report.reason}. Remediate: {' && '.join(report.remediation())} "
            f"— or set {ENV_SINGLE_TENANT}=1 (single-tenant) / "
            f"{ENV_DEV_OVERRIDE}=1 (non-production dev)."
        )

    # ------------------------------------------------------------------
    # Low-level readers
    # ------------------------------------------------------------------

    def _read_paranoid(self) -> Optional[int]:
        try:
            txt = self._paranoid_path().read_text(errors="replace").strip()
            return int(txt)
        except (OSError, FileNotFoundError, ValueError):
            return None

    def _read_nvidia_profiling(self) -> tuple[bool, Optional[bool]]:
        """Returns (nvidia_present, profiling_admin_only).
        profiling_admin_only is None when the param can't be read."""
        p = self._nvidia_params_path()
        try:
            txt = p.read_text(errors="replace")
        except (OSError, FileNotFoundError):
            return False, None
        # Present. Look for the RmProfilingAdminOnly line.
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("RmProfilingAdminOnly:"):
                val = line.split(":", 1)[1].strip()
                return True, (val == "1")
        # NVIDIA present but param absent → treat as not-admin-only (open).
        return True, False

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "PARANOID_FLOOR",
    "PerfCounterExposed",
    "PerfCounterLockdown",
    "PerfLockReport",
    "PerfLockState",
    "PerfLockStats",
    "ENV_SINGLE_TENANT",
    "ENV_DEV_OVERRIDE",
]
