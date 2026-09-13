# SPDX-License-Identifier: LicenseRef-VOS3-Pro-Proprietary
# SPDX-FileCopyrightText: 2026 VOS3 Project (Sovereign Enterprise Edition)
#
# NOTICE: This file carries a PROPOSED proprietary license header as part of
# the v20.5.2 Open-Core transition. The repo-wide LICENSE file remains MIT
# pending legal review of the dual-license split. Until that review
# completes, this file is governed by the existing MIT LICENSE at the
# repo root. See backend/pro/README.md and docs/strategy/OPEN_CORE_LICENSING.md.
"""
VOS3 Sovereign Fine-Tuning Engine — v20.2.1-TRAIN (PRO wrapper)
================================================================

QLoRA fine-tuning pipeline anchored to VOS3 kernel primitives.

This module is the **PRO-flavor entry point** that gates the training
pipeline behind `vos3_pro_license_check`. The actual implementation —
the QLoRA glue, dataset streaming, MMR step recording, TPM PCR sealing —
lives in the CORE-flavor file `backend/services/finetune_engine.py`.

Open-Core charter:
  - The training engine itself is CORE (operators on a CORE build can
    fine-tune their own private models for personal use).
  - This PRO wrapper adds: (a) per-org concurrency/throughput billing
    hooks, (b) NPU-cluster scheduling integration, and (c) the license-
    gated path that surfaces the engine through the VOS3 control plane
    so multi-tenant deployments can offer fine-tuning as a PRO-tier
    service.

==============================================================================
SCAFFOLD: Reconstructed on 2026-05-01 due to data loss. Integrity vs
original v20.6 ELF not guaranteed.

Recovered: SPDX header + module docstring (lines 1..16, byte-faithful).
Scaffolded (no transcript coverage): the wrapper class below. Public
surface (`SovereignFineTunerPro.train`, `quota_for_org`,
`is_npu_cluster_available`) is designed to compose with the CORE engine
without duplicating its logic.
==============================================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

# Import the CORE engine. This wrapper does NOT reimplement training —
# it merely gates and bills. Keeping the actual gradient code in CORE is
# what lets unlicensed users run private training without paying for a
# license they don't need.
try:
    from backend.services.finetune_engine import (  # type: ignore
        SovereignFineTuner,
        FineTuneDependencyError,
    )
except (
    ImportError
):  # pragma: no cover — backend may not be importable from every entrypoint
    SovereignFineTuner = None  # type: ignore[assignment]
    FineTuneDependencyError = RuntimeError  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


class ProLicenseRequired(RuntimeError):
    """Raised when a PRO-only finetune entrypoint is invoked but the
    running build either is not PRO or has no valid license blob.
    Caller (FastAPI route) MUST translate to HTTP 402 Payment Required."""


@dataclass(frozen=True)
class OrgFineTuneQuota:
    """Per-organization fine-tuning quota. Limits are advisory at this
    layer; hard enforcement lives in the kernel slot subsystem (NPU
    cluster reservations) and the billing middleware."""

    max_concurrent_runs: int
    monthly_compute_hours: float
    npu_clusters_allowed: int


# ---------------------------------------------------------------------------
# License gate — defers to kernel/src/pro/license_check.c via the VBus.
# In dev mode (no kernel running), the env override
# VOS3_PRO_LICENSE_BYPASS_FOR_TEST=1 mirrors the kernel's same-named
# build flag. NEVER set this in production; the regional_policy
# enforcement in middleware/auth.py logs every bypass.
# ---------------------------------------------------------------------------


def _pro_license_active() -> bool:
    """True iff the running deployment can serve PRO-only finetune ops."""
    if os.environ.get("VOS3_PRO_LICENSE_BYPASS_FOR_TEST") == "1":
        logger.warning(
            "finetune_engine.pro: BYPASS active via "
            "VOS3_PRO_LICENSE_BYPASS_FOR_TEST=1 — never set this in production."
        )
        return True
    # Kernel-rooted check: would VBus-call into kernel/src/pro/license_check.c.
    # For now, gate on the same compile-time flag the kernel uses.
    return os.environ.get("VOS3_PRO") == "1"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_npu_cluster_available() -> bool:
    """Probe for a usable NPU cluster (PRO-only feature).

    Looks at `VOS3_NPU_DEVICE` AND `VOS3_NPU_CLUSTER_COUNT >= 1`.
    Returns False on CORE builds or when no NPU is bound.
    """
    if not _pro_license_active():
        return False
    if not os.environ.get("VOS3_NPU_DEVICE"):
        return False
    try:
        clusters = int(os.environ.get("VOS3_NPU_CLUSTER_COUNT", "0"))
    except ValueError:
        clusters = 0
    return clusters >= 1


def quota_for_org(org_id: str) -> OrgFineTuneQuota:
    """Return the configured fine-tune quota for an org.

    SCAFFOLD: today returns a single hard-coded "starter" tier. A real
    implementation would query the org's subscription via the billing
    service. The shape is stable so callers don't need to change when
    that wiring lands.
    """
    if not _pro_license_active():
        return OrgFineTuneQuota(
            max_concurrent_runs=0,
            monthly_compute_hours=0.0,
            npu_clusters_allowed=0,
        )
    # Default starter tier for any PRO org until billing service is wired.
    return OrgFineTuneQuota(
        max_concurrent_runs=1,
        monthly_compute_hours=10.0,
        npu_clusters_allowed=1,
    )


class SovereignFineTunerPro:
    """PRO-gated entrypoint to the CORE fine-tuning engine."""

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        if not _pro_license_active():
            raise ProLicenseRequired(
                "fine-tuning requires a PRO license. Build is CORE or "
                "VOS3_PRO is unset. See docs/strategy/OPEN_CORE_LICENSING.md."
            )
        if SovereignFineTuner is None:
            raise FineTuneDependencyError(
                "backend.services.finetune_engine.SovereignFineTuner could not "
                "be imported — check the heavyweight training deps install."
            )
        self._inner = SovereignFineTuner()
        self.quota = quota_for_org(org_id)

    def train(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Forward to the CORE engine after recording the PRO entry-point
        invocation in the audit log. The CORE engine handles the actual
        QLoRA glue, MMR step recording, and TPM sealing.
        """
        logger.info(
            "finetune_engine.pro: train() invoked",
            extra={"org_id": self.org_id, "quota": self.quota.__dict__},
        )
        return self._inner.train(*args, **kwargs)
