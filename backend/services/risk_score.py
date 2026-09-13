"""
Adaptive Risk Score → adaptive rlimits — vOS·Adaptive·SHA=aeb3736·Phase=P4.3

Wires the P3.2 HardwareManifest into the P4.3 sandbox-tier decision.
Read a manifest, return a tier-appropriate rlimits dictionary.

Two-tier policy (AAA plan §4.3):
  * PROTECTED → baseline rlimits (modern host can sustain them).
  * RESTRICTED_LEGACY → halved RLIMIT_AS, halved RLIMIT_CPU, and
    RLIMIT_NPROC reduced from 4 to 2. The risk-score formula
    (services.hardware_manifest.compute_risk_score) is informational
    only; the rlimit-switch is mode-based as the plan specifies.
  * UNKNOWN → matches RESTRICTED_LEGACY. Security > Availability —
    when we cannot prove the host is protected, we assume it is not.

NOT in scope:
  * Dynamic re-tier on a running process. Manifest is read at the
    boot/sandbox-init moment; subsequent kernel events do not
    retrigger this function (documented in
    docs/UNIVERSAL_HARDWARE_MANIFEST.md — "live periodic re-parse"
    is explicitly out of scope).
  * Per-app overrides. The §4.3 decision is a global host-level
    policy. Per-manifest overrides would need a follow-up plan
    iteration plus an audit-row contract change.

The function is pure — no I/O, no global state, deterministic on
its inputs. Caching is the caller's responsibility (see
app_sandbox._active_manifest()).
"""

from __future__ import annotations

from typing import Dict, Tuple

from services.hardware_manifest import HardwareManifest, compute_risk_score

# Baseline rlimits used on a PROTECTED host. These match the values
# that were hard-coded into app_sandbox._sandbox_preexec() before P4.3
# — keeping them identical ensures the existing test suite continues
# to pass on a modern host (the common-path delta is zero).
DEFAULT_BASE_MEMORY_MB = 256
DEFAULT_BASE_CPU_SECONDS = 60
DEFAULT_BASE_NPROC = 4
DEFAULT_BASE_NOFILE = 64
DEFAULT_BASE_FSIZE_MB = 10

# Floors. Halving 32 MB or 1 second of CPU would render the sandbox
# unusable on legacy hardware; clamp to these minimums.
_MIN_MEMORY_MB = 32
_MIN_CPU_SECONDS = 5


def compute_adaptive_rlimits(
    manifest: HardwareManifest,
    *,
    base_memory_mb: int = DEFAULT_BASE_MEMORY_MB,
    base_cpu_seconds: int = DEFAULT_BASE_CPU_SECONDS,
) -> Dict[str, Tuple[int, int]]:
    """Return a dict of RLIMIT_* → (soft, hard) tuples for this host.

    Keys: "RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_NPROC", "RLIMIT_NOFILE",
    "RLIMIT_FSIZE". Values are pure ints — callers feed them directly
    to resource.setrlimit().
    """
    mem_mb = base_memory_mb
    cpu_s = base_cpu_seconds
    nproc = DEFAULT_BASE_NPROC

    if manifest.mode != "PROTECTED":
        # AAA plan §4.3: halve memory + CPU, drop NPROC to 2.
        mem_mb = max(_MIN_MEMORY_MB, base_memory_mb // 2)
        cpu_s = max(_MIN_CPU_SECONDS, base_cpu_seconds // 2)
        nproc = 2

    mem_bytes = mem_mb * 1024 * 1024
    fsize_bytes = DEFAULT_BASE_FSIZE_MB * 1024 * 1024

    return {
        "RLIMIT_AS": (mem_bytes, mem_bytes),
        "RLIMIT_CPU": (cpu_s, cpu_s * 2),
        "RLIMIT_NPROC": (nproc, nproc),
        "RLIMIT_NOFILE": (DEFAULT_BASE_NOFILE, DEFAULT_BASE_NOFILE),
        "RLIMIT_FSIZE": (fsize_bytes, fsize_bytes),
    }


def tier_for_manifest(manifest: HardwareManifest) -> str:
    """Map a manifest mode to its rlimit tier label.

    "PROTECTED_TIER"   → baseline rlimits
    "RESTRICTED_TIER"  → halved rlimits (mode in {RESTRICTED_LEGACY, UNKNOWN})
    """
    return "PROTECTED_TIER" if manifest.mode == "PROTECTED" else "RESTRICTED_TIER"


__all__ = [
    "HardwareManifest",
    "compute_risk_score",
    "compute_adaptive_rlimits",
    "tier_for_manifest",
    "DEFAULT_BASE_MEMORY_MB",
    "DEFAULT_BASE_CPU_SECONDS",
    "DEFAULT_BASE_NPROC",
    "DEFAULT_BASE_NOFILE",
    "DEFAULT_BASE_FSIZE_MB",
]
