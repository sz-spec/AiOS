"""
backend/services/sched_class_shim.py
====================================

Sprint 22 / Item J5 (software sliver) — cgroup v2 inference-class
scheduling shim (fail-closed) (NEW row).

What this is
------------

From the 80-problem agent-era catalog, J5:
  "Real-time scheduling for inference clashes with CFS — an agent that
   bumps its inference threads to SCHED_FIFO/RR to shave tail latency can
   starve the rest of the box (control plane, other tenants, even the
   kernel's own threads). The OS has no notion of an 'inference class'
   that gets RT-ish latency WITHIN a bounded budget."

The software sliver vOS can enforce today (the hardware-bound part —
RT_PREEMPT tuning — stays in Partition 2): a **cgroup v2 inference
class** with a bounded CPU budget. This shim **refuses to launch an
RT-priority (SCHED_FIFO/RR) inference workload unless it is placed in
the registered inference cgroup class AND that class has a bounded
cpu.max** — so RT inference gets its latency without an unbounded budget
that could starve the system. A normal (CFS) launch is unaffected.

Enforcement contract
--------------------

    shim = SchedClassShim(cgroup_root="/sys/fs/cgroup")
    shim.require_inference_launch(InferenceLaunch(
        sched_policy=SchedPolicy.RT_FIFO, rt_priority=80,
        cgroup="vos3-inference.slice/agent-7"))
    # raises SchedClassRefused if the RT launch is outside the bounded
    # inference class

Honest scope ceilings
--------------------

  - This is the cgroup-placement + budget-bound policy. It does NOT itself
    call sched_setscheduler / write cgroup files — the sandbox launcher
    does that; this shim is the gate the launcher consults (same
    decide-vs-enforce split as the other Sprint-21/22 gates). The
    RT_PREEMPT kernel + per-core RT throttling knobs are the Partition-2
    hardware-bound half of J5.
  - Non-Linux / no cgroup v2 → fail-closed for RT launches unless
    VOS3_SCHED_DEV_OVERRIDE=1 (loud WARNING; never in production). CFS
    launches are always allowed (no RT starvation risk).
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_SCHED_DEV_OVERRIDE"
DEFAULT_INFERENCE_CLASS = "vos3-inference.slice"
_SYS_FS_CGROUP = "/sys/fs/cgroup"


class SchedPolicy(str, enum.Enum):
    NORMAL = "normal"  # SCHED_OTHER / CFS — not gated
    RT_FIFO = "rt_fifo"  # SCHED_FIFO
    RT_RR = "rt_rr"  # SCHED_RR

    @property
    def is_realtime(self) -> bool:
        return self in (SchedPolicy.RT_FIFO, SchedPolicy.RT_RR)


@dataclass(frozen=True)
class InferenceLaunch:
    sched_policy: SchedPolicy
    cgroup: str  # cgroup path relative to cgroup_root
    rt_priority: int = 0


class SchedClassRefused(Exception):
    """Raised by require_inference_launch() when an RT inference launch is
    outside the bounded inference cgroup class. Fail-closed."""


@dataclass
class SchedClassStats:
    checks: int = 0
    allowed: int = 0
    allowed_cfs: int = 0
    refused: int = 0
    dev_overrides_used: int = 0


@dataclass
class SchedClassShim:
    """Fail-closed cgroup v2 inference-class placement gate for RT launches."""

    cgroup_root: Optional[str] = None
    inference_class: str = DEFAULT_INFERENCE_CLASS
    stats: SchedClassStats = field(default_factory=SchedClassStats)

    def _root(self) -> Path:
        return Path(self.cgroup_root) if self.cgroup_root else Path(_SYS_FS_CGROUP)

    # ------------------------------------------------------------------
    # Probes
    # ------------------------------------------------------------------

    def _is_cgroup_v2_with_cpu(self) -> bool:
        try:
            ctrls = (
                (self._root() / "cgroup.controllers")
                .read_text(errors="replace")
                .split()
            )
        except (OSError, FileNotFoundError):
            return False
        return "cpu" in ctrls

    def _in_inference_class(self, cgroup: str) -> bool:
        norm = cgroup.strip("/")
        return norm == self.inference_class or norm.startswith(
            self.inference_class + "/"
        )

    def _cpu_budget_bounded(self, cgroup: str) -> bool:
        """A bounded cpu.max is '<quota> <period>' with a numeric quota;
        the unbounded sentinel is 'max'."""
        cpu_max = self._root() / cgroup.strip("/") / "cpu.max"
        try:
            quota = cpu_max.read_text(errors="replace").split()[0].strip()
        except (OSError, FileNotFoundError, IndexError):
            return False
        return quota.isdigit()

    # ------------------------------------------------------------------
    # Enforcement gate (fail-closed)
    # ------------------------------------------------------------------

    def require_inference_launch(self, launch: InferenceLaunch) -> InferenceLaunch:
        """Allow the launch. CFS launches always pass. RT launches pass
        only if placed in the bounded inference cgroup class. Raises
        SchedClassRefused otherwise (fail-closed)."""
        self.stats.checks += 1

        if not launch.sched_policy.is_realtime:
            self.stats.allowed += 1
            self.stats.allowed_cfs += 1
            return launch

        def _refuse(reason: str):
            if self._env_true(ENV_DEV_OVERRIDE):
                self.stats.dev_overrides_used += 1
                self.stats.allowed += 1
                logger.warning(
                    "[sched_class_shim] RT inference launch allowed by DEV "
                    "OVERRIDE despite: %s. NEVER set %s in production.",
                    reason,
                    ENV_DEV_OVERRIDE,
                )
                return True
            self.stats.refused += 1
            logger.error(
                "[sched_class_shim] RT inference launch REFUSED (fail-closed) " "— %s",
                reason,
            )
            raise SchedClassRefused(reason)

        if not self._is_cgroup_v2_with_cpu():
            if _refuse("cgroup v2 with the cpu controller is not available"):
                return launch

        if not self._in_inference_class(launch.cgroup):
            if _refuse(
                f"RT inference cgroup {launch.cgroup!r} is outside the "
                f"inference class {self.inference_class!r}"
            ):
                return launch

        if not self._cpu_budget_bounded(launch.cgroup):
            if _refuse(
                f"inference cgroup {launch.cgroup!r} has no bounded cpu.max "
                f"budget — RT inference could starve the system"
            ):
                return launch

        self.stats.allowed += 1
        logger.info(
            "[sched_class_shim] RT inference launch ALLOWED — policy=%s prio=%d "
            "cgroup=%s (bounded inference class)",
            launch.sched_policy.value,
            launch.rt_priority,
            launch.cgroup,
        )
        return launch

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "SchedPolicy",
    "InferenceLaunch",
    "SchedClassRefused",
    "SchedClassShim",
    "SchedClassStats",
    "DEFAULT_INFERENCE_CLASS",
    "ENV_DEV_OVERRIDE",
]
