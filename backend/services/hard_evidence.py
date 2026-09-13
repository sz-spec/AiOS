"""
Hard-Evidence bypass — vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C

When the local backend runs on a dev host that ISN'T a vOS kernel
(e.g., the engineer's Apple Silicon Mac, or any non-vOS Linux box),
the HardwareManifest correctly reports `mode="UNKNOWN"` because no
kernel klog exists. P4.3 then drops the sandbox into the RESTRICTED
rlimit tier (Security > Availability), which is correct for a real
production deployment but inconvenient for daily development.

This module provides an OPERATOR-SUPPLIED override: if a valid GCP
KVM verification artifact exists at
`infra/verify/reports/GCP_EVIDENCE_AAA.summary.txt`, an UNKNOWN-mode
manifest is treated as PROTECTED for rlimit-tier purposes.

Trust model (read carefully)
----------------------------
* The bypass ONLY applies to `mode="UNKNOWN"`. It NEVER upgrades
  RESTRICTED_LEGACY → PROTECTED — when the kernel told us it's not
  protected, we believe it.
* The evidence file MUST contain real PASS lines (PROTECTED_FULL or
  PROTECTED_PCID_ONLY tier). A touched-empty file is rejected.
* The anchor SHA `aeb3736` MUST appear in the evidence — a summary
  from a different engagement is rejected.
* Every activation logs at WARNING level so the bypass cannot be silent.

This is a developer-ergonomics override, not a security feature.
The risk of a malicious actor placing a forged evidence file in the
repo is bounded by the fact that they'd already need write access to
the source tree — which is a higher-privilege position than tier
bypass anyway.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Path is repo-relative. The constants ARE the policy: changing where
# the evidence lives means deliberate edits in both the GCP script and here.
_EVIDENCE_REL_PATH = "infra/verify/reports/GCP_EVIDENCE_AAA.summary.txt"
_ENGAGEMENT_ANCHOR = "aeb3736"

# Minimum required lines — both forms of the script's PASS row for a
# valid PROTECTED tier. Either is acceptable evidence; we don't insist
# on PROTECTED_FULL because some hosts genuinely lack SMEP/SMAP and
# the script correctly accepts PROTECTED_PCID_ONLY as a successful
# kernel decision.
_REQUIRED_PASS_PATTERNS = (
    "PASS  Mitigation tier = PROTECTED_FULL",
    "PASS  Mitigation tier = PROTECTED_PCID_ONLY",
)


def _find_repo_root() -> Optional[Path]:
    """Walk up from this file looking for the repo root (has `.git/`)."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    return None


def evidence_path() -> Optional[Path]:
    """Return the path the evidence file SHOULD live at, or None if we
    can't locate the repo root (e.g., service running outside a git
    checkout — production scenario)."""
    override = os.environ.get("VOS3_HARD_EVIDENCE_PATH")
    if override:
        return Path(override)
    root = _find_repo_root()
    if root is None:
        return None
    return root / _EVIDENCE_REL_PATH


def is_present_and_valid() -> bool:
    """True iff a real GCP-verification artifact is on disk.

    Validation gates:
      1. File exists and is readable.
      2. Contains the engagement anchor SHA `aeb3736` — rejects
         summaries from other engagements / future re-anchorings.
      3. Contains a `PASS` line for either PROTECTED_FULL or
         PROTECTED_PCID_ONLY — rejects a touched-empty file or a
         summary where the verification failed.

    All failures are logged at DEBUG (not WARN) — absence of the
    bypass is the COMMON case (most hosts won't have it), and we
    don't want to spam the log.
    """
    path = evidence_path()
    if path is None:
        logger.debug("hard-evidence: repo root not locatable, bypass impossible")
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, IsADirectoryError) as exc:
        logger.debug("hard-evidence: %s not readable (%s)", path, exc)
        return False

    if _ENGAGEMENT_ANCHOR not in text:
        logger.debug("hard-evidence: %s missing anchor %s", path, _ENGAGEMENT_ANCHOR)
        return False

    if not any(needle in text for needle in _REQUIRED_PASS_PATTERNS):
        logger.debug("hard-evidence: %s contains no PROTECTED-tier PASS line", path)
        return False

    return True


def announce_activation(host_mode: str) -> None:
    """Emit a single WARNING-level log line the operator will see.

    Called by `services.app_sandbox._adaptive_rlimits` immediately
    before it applies the tier upgrade.
    """
    path = evidence_path()
    logger.warning(
        "vOS hard-evidence bypass ACTIVE: manifest.mode=%s upgraded to PROTECTED "
        "for rlimit-tier purposes (evidence: %s, anchor=%s). This affects ONLY the "
        "rlimit budget; the manifest's mode field is unchanged for audit honesty.",
        host_mode,
        path,
        _ENGAGEMENT_ANCHOR,
    )


__all__ = [
    "evidence_path",
    "is_present_and_valid",
    "announce_activation",
]
