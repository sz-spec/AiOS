"""
backend/security/cvm_launch_gate.py
===================================

Sprint 22 / Item D2 (+D6 depth) — Confidential-VM launch gate
(anti-HECKLER, fail-closed) (NEW row; D6 is DEPTH).

What this is
------------

From the 80-problem agent-era catalog, D2:
  "HECKLER-class malicious-interrupt attacks on Confidential VMs — a
   malicious or compromised hypervisor injects interrupts (e.g. #NMI,
   spurious vectors) into an AMD SEV-SNP / Intel TDX confidential guest
   to coerce control flow and break the confidentiality the CVM is
   supposed to guarantee (HECKLER, WeSee, Ahoi-class attacks)."

The platform mitigation is **interrupt restriction**: SEV-SNP Restricted
Injection / alternate-injection, or TDX's interrupt-handling contract,
so the guest only accepts interrupts it expects. This gate **refuses to
launch a CVM unless the platform attestation proves interrupt filtering
is active** — a guest that would accept arbitrary hypervisor-injected
interrupts is never started.

D6 (DEPTH): "CVM overhead pushes operators to turn CC off." In the
FORTRESS profile this gate ALSO refuses a launch with Confidential
Compute disabled — you cannot quietly opt out of memory encryption on a
fortress workload. (Blackwell/SNP overhead is now <5%, so the trade-off
the catalog worried about has largely closed; the gate makes "off" an
explicit, refused choice rather than a silent default.)

Enforcement contract
--------------------

    gate = CvmLaunchGate(profile=CvmProfile.FORTRESS)
    gate.require_cvm_launch(attestation)   # raises CvmLaunchRefused unless
                                           # interrupt filtering (+ CC in
                                           # FORTRESS) is attested

Honest scope ceilings
--------------------

  - Restricted-injection state is not a plain sysfs node — it lives in
    the platform attestation report. This gate therefore takes a
    ``CvmAttestation`` (produced by the SEV-SNP / TDX attestation flow,
    same path as the GPU attestation in O4) as the authoritative input
    and verifies its claims; ``probe_cvm_tech()`` reads /proc/cpuinfo +
    /sys only as an advisory sanity cross-check.
  - The gate trusts the ``verified`` flag on the attestation (set by the
    attestation verifier that checked the AMD/Intel signature) — it does
    not re-verify the platform certificate chain here (that's the
    attestation service's job).
  - Non-attested / unverifiable launch → fail-closed unless
    VOS3_CVM_DEV_OVERRIDE=1 (loud WARNING; never in production).
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_CVM_DEV_OVERRIDE"

_PROC_CPUINFO = "/proc/cpuinfo"


class CvmTech(str, enum.Enum):
    SEV_SNP = "sev_snp"
    TDX = "tdx"
    NONE = "none"


class CvmProfile(str, enum.Enum):
    STANDARD = "standard"  # interrupt filtering required; CC recommended
    FORTRESS = "fortress"  # interrupt filtering AND CC both required (D6)


@dataclass(frozen=True)
class CvmAttestation:
    """A platform attestation report's relevant claims. ``verified`` means
    the attestation verifier already checked the AMD/Intel signature
    chain."""

    cvm_tech: CvmTech
    restricted_injection: bool  # interrupt filtering active (anti-HECKLER)
    cc_enabled: bool  # memory encryption / Confidential Compute on
    verified: bool
    measurement: str = ""  # launch measurement (audit)


class CvmLaunchRefused(Exception):
    """Raised by require_cvm_launch() when the platform cannot prove the
    CVM is launched with interrupt filtering (+ CC in FORTRESS).
    Fail-closed."""


@dataclass
class CvmGateStats:
    checks: int = 0
    allowed: int = 0
    refused: int = 0
    dev_overrides_used: int = 0


@dataclass
class CvmLaunchGate:
    """Fail-closed Confidential-VM launch gate."""

    profile: CvmProfile = CvmProfile.STANDARD
    proc_root: Optional[str] = None  # test seam for cpuinfo probe
    stats: CvmGateStats = field(default_factory=CvmGateStats)

    # ------------------------------------------------------------------
    # Advisory probe (cross-check only)
    # ------------------------------------------------------------------

    def probe_cvm_tech(self) -> CvmTech:
        """Best-effort detection from /proc/cpuinfo flags. Advisory only —
        the authoritative input is the attestation."""
        path = (
            Path(self.proc_root) / "cpuinfo" if self.proc_root else Path(_PROC_CPUINFO)
        )
        try:
            flags = path.read_text(errors="replace").lower()
        except (OSError, FileNotFoundError):
            return CvmTech.NONE
        if "sev_snp" in flags or "sev-snp" in flags:
            return CvmTech.SEV_SNP
        if "tdx" in flags:
            return CvmTech.TDX
        return CvmTech.NONE

    # ------------------------------------------------------------------
    # Enforcement gate (fail-closed)
    # ------------------------------------------------------------------

    def require_cvm_launch(
        self, attestation: Optional[CvmAttestation]
    ) -> CvmAttestation:
        """Allow a CVM launch iff interrupt filtering is attested (and, in
        FORTRESS, CC is enabled). Raises CvmLaunchRefused otherwise. Dev
        override VOS3_CVM_DEV_OVERRIDE=1 downgrades to allow WITH a loud
        WARNING (never in production)."""
        self.stats.checks += 1

        def _refuse(reason: str):
            if self._env_true(ENV_DEV_OVERRIDE):
                self.stats.dev_overrides_used += 1
                self.stats.allowed += 1
                logger.warning(
                    "[cvm_launch_gate] CVM launch allowed by DEV OVERRIDE "
                    "despite: %s. NEVER set %s in production.",
                    reason,
                    ENV_DEV_OVERRIDE,
                )
                return True  # overridden → allow
            self.stats.refused += 1
            logger.error(
                "[cvm_launch_gate] CVM launch REFUSED (fail-closed) — %s", reason
            )
            raise CvmLaunchRefused(reason)

        if attestation is None or not attestation.verified:
            if _refuse("no verified CVM attestation (unverifiable platform)"):
                return attestation or CvmAttestation(CvmTech.NONE, False, False, False)

        if attestation.cvm_tech is CvmTech.NONE:
            if _refuse("not a confidential VM (no SEV-SNP / TDX attested)"):
                return attestation

        if not attestation.restricted_injection:
            if _refuse(
                f"{attestation.cvm_tech.value} launch without restricted "
                f"interrupt injection — HECKLER-class attack possible"
            ):
                return attestation

        if self.profile is CvmProfile.FORTRESS and not attestation.cc_enabled:
            if _refuse(
                "FORTRESS profile requires Confidential Compute ENABLED; "
                "refusing a CC-off launch (D6)"
            ):
                return attestation

        self.stats.allowed += 1
        logger.info(
            "[cvm_launch_gate] CVM launch ALLOWED — %s, restricted_injection=on, "
            "cc=%s, profile=%s",
            attestation.cvm_tech.value,
            attestation.cc_enabled,
            self.profile.value,
        )
        return attestation

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "CvmTech",
    "CvmProfile",
    "CvmAttestation",
    "CvmLaunchRefused",
    "CvmLaunchGate",
    "CvmGateStats",
    "ENV_DEV_OVERRIDE",
]
