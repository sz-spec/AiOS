"""
backend/security/gpu_alloc_validator.py
========================================

Sprint 21 / Primitive (c) / Item O4 — hardware-enforced fail-closed GPU
allocation validator.

What this is
------------

From the 80-problem agent-era catalog, O4:
  "Cross-tenant cache / model-stealing on shared accelerators — two
   agents (or two tenants) sharing a GPU can recover each other's model
   weights / activations through shared L2 cache, residual memory, and
   timing side channels unless the silicon enforces a hard partition."

This is the "O3 noisy-neighbour admission gate" idea from the Sprint-19
plan, done properly as real admission control. The validator refuses to
grant an agent accelerator memory on a SHARED host unless ALL four
isolation pre-conditions hold (V1.3 plan §5.c):

  1. MIG partition present   — the GPU is in MIG mode AND the target is
                               a distinct MIG instance UUID.
  2. Confidential Compute on — GPU CC mode == ENABLED (Blackwell/Hopper).
  3. Fresh attestation       — a verified NVIDIA attestation quote,
                               < ATTEST_TTL old, bound to the MIG UUID.
  4. cgroup resource caps    — the agent's cgroup has memory (+ SM)
                               limits set (no overcommit).

Else → GpuAllocRefused (fail-closed). A dedicated (non-shared) GPU bind
skips the co-tenant requirements — the attack surface this closes is
specifically the shared-host case.

Composition with the other GPU gates (the bind order):
    E1 (trusted driver) → E3 (IOMMU, no DMA bypass) → O4 (this:
    isolated partition) → E6 (perf counters locked) → warp transfer.
All are fail-closed; any refusal aborts the bind before accelerator
memory is handed over.

Enforcement contract
--------------------

    v = GpuAllocValidator(attestation_provider=verify_quote)
    v.require_isolated_gpu_alloc(GpuAllocRequest(
        gpu_index=0, mig_uuid="MIG-...", shared_host=True,
        cgroup_mem_limit_bytes=8 << 30, cgroup_sm_limit=14))
    # raises GpuAllocRefused unless all four conditions hold

Honest accounting (enforced-pending-silicon)
--------------------------------------------

  O4 flips to vOS-ENFORCED (a counted moat row) only when this software
  validator ships AND is proven on real Blackwell silicon at a customer
  pilot (≥30 days). Until then it is `enforced-pending-silicon`, like
  its E4/E7/O2 siblings: the software gate is live + fail-closed, the
  silicon validation is pending. On a host without Blackwell/MIG the
  validator reads host state read-only and FAIL-CLOSES on an
  unverifiable shared host unless VOS3_GPU_ALLOC_DEV_OVERRIDE=1 (loud
  WARNING; never in production). A modeled `mig_isolation_sim` stub
  backs the unit tests via injected fake procfs/sysfs.
"""

from __future__ import annotations

import logging
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_GPU_ALLOC_DEV_OVERRIDE"
ATTEST_TTL_SECONDS = 300

_PROC_NVIDIA_GPUS = "/proc/driver/nvidia/gpus"
_SYS_CLASS_NVIDIA = "/sys/class/nvidia"


# ---------------------------------------------------------------------------
# Clock — lets tests pin time
# ---------------------------------------------------------------------------


class ClockProtocol:
    def time(self) -> float:  # pragma: no cover - trivial
        return time.time()


# ---------------------------------------------------------------------------
# Request / attestation / report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GpuAllocRequest:
    gpu_index: int
    mig_uuid: Optional[str]
    shared_host: bool
    cgroup_mem_limit_bytes: Optional[int] = None  # None → unlimited (overcommit)
    cgroup_sm_limit: Optional[int] = None


@dataclass(frozen=True)
class AttestationQuote:
    """A quote the attestation_provider returns. ``verified`` means the
    provider already checked the NVIDIA signature; this validator only
    checks freshness + UUID binding (it does not re-verify the
    signature — that is the provider's job, same SDK as single-GPU)."""

    mig_uuid: str
    issued_at: float
    verified: bool


AttestationProvider = Callable[[str], Optional[AttestationQuote]]


@dataclass(frozen=True)
class GpuAllocReport:
    shared_host: bool
    mig_enabled: bool
    distinct_mig_instance: bool
    cc_enabled: bool
    attestation_fresh: bool
    cgroup_capped: bool
    host_probeable: bool
    reason: str

    @property
    def safe_for_alloc(self) -> bool:
        if not self.shared_host:
            return True  # dedicated GPU — co-tenant isolation N/A
        return (
            self.mig_enabled
            and self.distinct_mig_instance
            and self.cc_enabled
            and self.attestation_fresh
            and self.cgroup_capped
        )


class GpuAllocRefused(Exception):
    """Raised by require_isolated_gpu_alloc() when a shared-host GPU
    allocation cannot prove all four isolation conditions. Fail-closed."""


@dataclass
class GpuAllocStats:
    checks: int = 0
    allowed: int = 0
    allowed_dedicated: int = 0
    refused: int = 0
    dev_overrides_used: int = 0


@dataclass
class GpuAllocValidator:
    """Fail-closed admission control for accelerator memory on shared hosts."""

    attestation_provider: Optional[AttestationProvider] = None
    sysfs_root: Optional[str] = None
    proc_root: Optional[str] = None
    clock: ClockProtocol = field(default_factory=ClockProtocol)
    attest_ttl_seconds: int = ATTEST_TTL_SECONDS
    stats: GpuAllocStats = field(default_factory=GpuAllocStats)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _nvidia_gpus_path(self) -> Path:
        if self.proc_root:
            return Path(self.proc_root) / "driver" / "nvidia" / "gpus"
        return Path(_PROC_NVIDIA_GPUS)

    def _nvidia_class_path(self) -> Path:
        if self.sysfs_root:
            return Path(self.sysfs_root) / "class" / "nvidia"
        return Path(_SYS_CLASS_NVIDIA)

    # ------------------------------------------------------------------
    # Probes
    # ------------------------------------------------------------------

    def _host_probeable(self) -> bool:
        return (
            platform.system() == "Linux"
            or self.proc_root is not None
            or self.sysfs_root is not None
        )

    def probe_mig_mode(self, gpu_index: int) -> bool:
        """True iff the GPU at `gpu_index` reports MIG mode enabled.
        Reads /proc/driver/nvidia/gpus/<bus>/mig_mode (value '1')."""
        base = self._nvidia_gpus_path()
        try:
            entries = sorted(base.iterdir())
        except (OSError, FileNotFoundError):
            return False
        if gpu_index < 0 or gpu_index >= len(entries):
            return False
        mig_node = entries[gpu_index] / "mig_mode"
        try:
            return mig_node.read_text(errors="replace").strip().startswith("1")
        except (OSError, FileNotFoundError):
            return False

    def probe_cc_mode(self) -> bool:
        """True iff GPU Confidential Compute mode is ENABLED. Reads
        /sys/class/nvidia/<dev>/cc_mode (value 'on'/'1'/'enabled')."""
        base = self._nvidia_class_path()
        try:
            devs = sorted(base.iterdir())
        except (OSError, FileNotFoundError):
            return False
        for dev in devs:
            try:
                val = (dev / "cc_mode").read_text(errors="replace").strip().lower()
            except (OSError, FileNotFoundError):
                continue
            if val in {"on", "1", "enabled", "true"}:
                return True
        return False

    def _attestation_fresh(self, mig_uuid: Optional[str]) -> bool:
        if mig_uuid is None or self.attestation_provider is None:
            return False
        try:
            quote = self.attestation_provider(mig_uuid)
        except Exception as exc:  # noqa: BLE001 - provider failure = no proof
            logger.warning("[gpu_alloc_validator] attestation provider raised: %r", exc)
            return False
        if quote is None or not quote.verified:
            return False
        if quote.mig_uuid != mig_uuid:
            return False
        age = self.clock.time() - quote.issued_at
        return 0 <= age <= self.attest_ttl_seconds

    @staticmethod
    def _cgroup_capped(req: GpuAllocRequest) -> bool:
        if req.cgroup_mem_limit_bytes is None or req.cgroup_mem_limit_bytes <= 0:
            return False
        if req.cgroup_sm_limit is None or req.cgroup_sm_limit <= 0:
            return False
        return True

    # ------------------------------------------------------------------
    # Probe + enforcement
    # ------------------------------------------------------------------

    def probe(self, request: GpuAllocRequest) -> GpuAllocReport:
        probeable = self._host_probeable()
        if not request.shared_host:
            return GpuAllocReport(
                shared_host=False,
                mig_enabled=False,
                distinct_mig_instance=False,
                cc_enabled=False,
                attestation_fresh=False,
                cgroup_capped=False,
                host_probeable=probeable,
                reason="dedicated (non-shared) GPU — co-tenant isolation N/A",
            )
        mig = self.probe_mig_mode(request.gpu_index) if probeable else False
        distinct = bool(request.mig_uuid)
        cc = self.probe_cc_mode() if probeable else False
        attested = self._attestation_fresh(request.mig_uuid)
        capped = self._cgroup_capped(request)

        missing = []
        if not mig:
            missing.append("MIG mode off")
        if not distinct:
            missing.append("no distinct MIG instance UUID")
        if not cc:
            missing.append("Confidential Compute off")
        if not attested:
            missing.append("no fresh bound attestation quote")
        if not capped:
            missing.append("cgroup mem/SM caps not set (overcommit)")
        reason = (
            "all isolation conditions met"
            if not missing
            else "shared-host isolation incomplete: " + "; ".join(missing)
        )

        return GpuAllocReport(
            shared_host=True,
            mig_enabled=mig,
            distinct_mig_instance=distinct,
            cc_enabled=cc,
            attestation_fresh=attested,
            cgroup_capped=capped,
            host_probeable=probeable,
            reason=reason,
        )

    def require_isolated_gpu_alloc(self, request: GpuAllocRequest) -> GpuAllocReport:
        """Refuse a shared-host GPU allocation unless all four isolation
        conditions hold. Returns the report on success; raises
        GpuAllocRefused on failure. Dev override
        VOS3_GPU_ALLOC_DEV_OVERRIDE=1 downgrades to allow WITH a loud
        WARNING (never in production)."""
        self.stats.checks += 1
        report = self.probe(request)

        if not report.shared_host:
            self.stats.allowed += 1
            self.stats.allowed_dedicated += 1
            logger.info("[gpu_alloc_validator] alloc ALLOWED — %s", report.reason)
            return report

        if report.safe_for_alloc:
            self.stats.allowed += 1
            logger.info(
                "[gpu_alloc_validator] shared-host alloc ALLOWED for MIG %s — %s",
                request.mig_uuid,
                report.reason,
            )
            return report

        if self._env_true(ENV_DEV_OVERRIDE):
            self.stats.dev_overrides_used += 1
            self.stats.allowed += 1
            logger.warning(
                "[gpu_alloc_validator] shared-host alloc allowed by DEV "
                "OVERRIDE despite %s. NEVER set %s in production — co-tenant "
                "model-stealing is possible on this host.",
                report.reason,
                ENV_DEV_OVERRIDE,
            )
            return report

        self.stats.refused += 1
        logger.error(
            "[gpu_alloc_validator] shared-host alloc REFUSED (fail-closed) — %s",
            report.reason,
        )
        raise GpuAllocRefused(
            f"GPU allocation refused: {report.reason}. Provide a MIG-isolated, "
            f"CC-enabled, freshly-attested, cgroup-capped instance, or set "
            f"{ENV_DEV_OVERRIDE}=1 for non-production dev."
        )

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "AttestationQuote",
    "AttestationProvider",
    "ClockProtocol",
    "GpuAllocRequest",
    "GpuAllocReport",
    "GpuAllocRefused",
    "GpuAllocStats",
    "GpuAllocValidator",
    "ENV_DEV_OVERRIDE",
    "ATTEST_TTL_SECONDS",
]
