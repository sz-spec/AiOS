"""
Universal Hardware Manifest — vOS·Adaptive·SHA=aeb3736·Phase=P3.2

Parses the kernel's serial boot log (`[KPTI]` lines, `[PCI-ECAM]`
lines, microcode-baseline lines once #42 lands) and produces a
structured `HardwareManifest` describing the current host's
capability tier.

Why a backend-side parser?
--------------------------
The kernel already does the heavy CPUID + ACPI work and prints the
result. Re-doing it from Python (via /proc/cpuinfo or ctypes-CPUID)
would duplicate effort AND drift from the kernel's view. Instead
we parse the kernel's own log lines — same source of truth.

The manifest is consumed by:
  * sandbox_provider selection (P4.3 wires risk-score → rlimits)
  * the AAA cert report (so the operator can see what tier landed)
  * compliance evidence (EU AI Act Annex IV §V.1)

Honest scope
------------
* Parsing is regex-based. The kernel's log format is byte-stable
  by design (pinned by source-level tests on the boot-summary
  strings). A future kernel refactor that changes those strings
  would need to update both the kernel test AND this parser.
* The parser is FAULT-TOLERANT: missing fields → manifest mode
  drops to RESTRICTED_LEGACY (security > availability — when we
  can't see what we have, assume the worst).
* `cpufeature` Python lib is NOT a dependency — if installed we
  use it, but the manifest works without it. The kernel klog
  remains the canonical source for capability detection.
"""

from __future__ import annotations

import dataclasses
import platform
import re
from typing import Optional

# Backend-process Python view (best-effort, optional). Lets us flag
# when Python and kernel disagree about the host.
try:
    import cpufeature as _cpufeat  # noqa: F401  (only via getattr below)

    _CPUFEAT_AVAILABLE = True
except ImportError:
    _CPUFEAT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Kernel klog regexes — must match the strings emitted by:
#   kernel/src/arch/x86_64/mitigation_factory.c
#   kernel/src/arch/x86_64/pci_ecam.c
#   kernel/src/arch/x86_64/kpti.c
# Pinned by source-level tests in tests/kernel/test_mitigation_factory_source.py
# ---------------------------------------------------------------------------

_RE_KPTI_MODE = re.compile(
    r"\[KPTI\]\s+mode=(?P<mode>\S+)"
    r"\s+pcid=(?P<pcid>yes|no)"
    r"\s+invpcid=(?P<invpcid>yes|no)"
    r"\s+smep=(?P<smep>yes|no)"
    r"\s+smap=(?P<smap>yes|no)"
    r"\s+sha-ni=(?P<sha_ni>yes|no)"
    r"\s+budget=(?P<budget>\S+)"
)
_RE_KPTI_INIT_READY = re.compile(r"\[KPTI\]\s+init:\s+ready\b")
_RE_KPTI_STRIP = re.compile(
    r"\[KPTI\]\s+PML4\s+strip:\s+kept=\[(?P<kept>[^\]]+)\]"
    r"\s+stripped_present_entries=(?P<stripped>\d+)"
)
_RE_PCI_ECAM_OK = re.compile(
    r"\[PCI-ECAM\]\s+available\s+—\s+buses\s+"
    r"(?P<start_bus>[0-9A-Fa-f]+)\.\.(?P<end_bus>[0-9A-Fa-f]+)"
    r"\s+mapped\s+MMIO@(?P<mmio_base>0x[0-9A-Fa-f]+)"
    r"\s+\(PCD=(?P<pcd>[01]),\s+NX=(?P<nx>[01])\)"
)
_RE_PCI_ECAM_FALLBACK = re.compile(
    r"\[PCI-ECAM\]\s+MCFG absent\s+—\s+Port-I/O fallback"
)

# P4.2 — microcode baseline check line. Emitted by
# kernel/src/arch/x86_64/microcode_check.c::vos3_microcode_check_boot.
# Three shapes:
#   [MICROCODE] revision=0xNN baseline=0xNN below_baseline=yes|no
#   [MICROCODE] revision=0xNN baseline=unknown below_baseline=unchecked
_RE_MICROCODE = re.compile(
    r"\[MICROCODE\]\s+revision=(?P<rev>0x[0-9A-Fa-f]+)"
    r"\s+baseline=(?P<base>0x[0-9A-Fa-f]+|unknown)"
    r"\s+below_baseline=(?P<below>yes|no|unchecked)"
)


# ---------------------------------------------------------------------------
# Manifest dataclass — frozen for safety (no mutation after build)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class HardwareManifest:
    """Snapshot of what the kernel told us about this host."""

    # High-level mode — drives sandbox tier + audit reporting
    mode: str  # "PROTECTED" | "RESTRICTED_LEGACY" | "UNKNOWN"

    # KPTI / mitigation tier (from mitigation_factory.c summary line)
    kpti_mode: Optional[
        str
    ]  # PROTECTED_FULL | PROTECTED_PCID_ONLY | LEGACY_KAISER | None
    pcid: bool
    invpcid: bool
    smep: bool
    smap: bool
    sha_ni: bool
    kpti_budget: Optional[str]  # "<=2%" | "<=3%" | "5-30%"

    # KPTI runtime status (init ready / strip outcome)
    kpti_init_ready: bool
    pml4_kept_indices: tuple  # e.g. ("256", "511")
    pml4_stripped_present_count: Optional[int]

    # PCI transport
    pci_transport: str  # "ECAM" | "PORT_IO" | "UNKNOWN"
    pci_ecam_mmio_base: Optional[int]  # int phys address, or None
    pci_ecam_bus_range: Optional[tuple]  # (start, end) ints
    pci_ecam_pcd: Optional[bool]
    pci_ecam_nx: Optional[bool]

    # Backend-side Python view (cross-check only; never overrides kernel)
    py_platform: str
    py_machine: str
    py_processor: str

    # P4.2 — microcode revision read from MSR 0x8B after the CPUID 0x1
    # "kick" (Intel SDM Vol 3A §9.11.7.1). Compared against the per-family
    # baseline in kernel/src/arch/x86_64/microcode_check.c. All three
    # fields are None on hosts where the kernel did NOT emit a
    # [MICROCODE] line (e.g., a partial mid-boot snapshot).
    microcode_revision: Optional[int]
    microcode_baseline: Optional[int]
    microcode_below_baseline: bool

    # P4.1 — count of known architectural CVEs the host is exposed to
    # (Meltdown / Spectre / L1TF / MDS / ZombieLoad). Populated by
    # services.compliance_audit when the manifest is built; defaults
    # to 0 when the audit hasn't run yet (so risk_score is unchanged
    # in that case).
    cve_exposure_count: int

    # Risk score — per AAA plan §4.3 formula (computed in compute_risk_score)
    risk_score: int

    @property
    def is_protected(self) -> bool:
        return self.mode == "PROTECTED"

    @property
    def is_restricted_legacy(self) -> bool:
        return self.mode == "RESTRICTED_LEGACY"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _yn(s: Optional[str]) -> bool:
    return s == "yes"


def parse_klog(klog_text: str) -> HardwareManifest:
    """Parse kernel boot serial output into a HardwareManifest.

    The text may be partial (mid-boot snapshot) — fields that haven't
    been emitted yet stay at their None / False defaults, and `mode`
    falls to RESTRICTED_LEGACY when we can't prove PROTECTED.

    Never raises on malformed input. The audit-honesty discipline
    requires that a parser feeding sandbox-tier decisions degrades
    gracefully rather than panicking.
    """
    # --- KPTI summary line ---
    kpti = _RE_KPTI_MODE.search(klog_text)
    if kpti:
        kpti_mode = kpti.group("mode")
        pcid = _yn(kpti.group("pcid"))
        invpcid = _yn(kpti.group("invpcid"))
        smep = _yn(kpti.group("smep"))
        smap = _yn(kpti.group("smap"))
        sha_ni = _yn(kpti.group("sha_ni"))
        budget = kpti.group("budget")
    else:
        kpti_mode = None
        pcid = invpcid = smep = smap = sha_ni = False
        budget = None

    kpti_init_ready = bool(_RE_KPTI_INIT_READY.search(klog_text))

    strip = _RE_KPTI_STRIP.search(klog_text)
    if strip:
        kept = tuple(s.strip() for s in strip.group("kept").split(","))
        stripped_n = int(strip.group("stripped"))
    else:
        kept = ()
        stripped_n = None

    # --- PCI-ECAM line ---
    ecam_ok = _RE_PCI_ECAM_OK.search(klog_text)
    if ecam_ok:
        pci_transport = "ECAM"
        pci_ecam_mmio_base = int(ecam_ok.group("mmio_base"), 16)
        pci_ecam_bus_range = (
            int(ecam_ok.group("start_bus"), 16),
            int(ecam_ok.group("end_bus"), 16),
        )
        pci_ecam_pcd = ecam_ok.group("pcd") == "1"
        pci_ecam_nx = ecam_ok.group("nx") == "1"
    elif _RE_PCI_ECAM_FALLBACK.search(klog_text):
        pci_transport = "PORT_IO"
        pci_ecam_mmio_base = None
        pci_ecam_bus_range = None
        pci_ecam_pcd = None
        pci_ecam_nx = None
    else:
        pci_transport = "UNKNOWN"
        pci_ecam_mmio_base = None
        pci_ecam_bus_range = None
        pci_ecam_pcd = None
        pci_ecam_nx = None

    # --- P4.2: microcode line ---
    ucode = _RE_MICROCODE.search(klog_text)
    if ucode:
        microcode_revision = int(ucode.group("rev"), 16)
        base_str = ucode.group("base")
        microcode_baseline = int(base_str, 16) if base_str != "unknown" else None
        microcode_below_baseline = ucode.group("below") == "yes"
    else:
        microcode_revision = None
        microcode_baseline = None
        microcode_below_baseline = False

    # --- High-level mode derivation (AAA plan §3.2 matrix) ---
    # PROTECTED requires PCID at minimum. Without it we drop to
    # RESTRICTED_LEGACY by the directive's Security > Availability rule.
    # P4.2 amendment: below-baseline microcode overrides regardless of
    # CPU class — unpatched speculative-execution mitigations leave the
    # side channels open even on modern silicon.
    if microcode_below_baseline:
        mode = "RESTRICTED_LEGACY"
    elif pcid and kpti_init_ready:
        mode = "PROTECTED"
    elif kpti_mode is None and not kpti_init_ready:
        mode = "UNKNOWN"
    else:
        mode = "RESTRICTED_LEGACY"

    manifest_pre_score = HardwareManifest(
        mode=mode,
        kpti_mode=kpti_mode,
        pcid=pcid,
        invpcid=invpcid,
        smep=smep,
        smap=smap,
        sha_ni=sha_ni,
        kpti_budget=budget,
        kpti_init_ready=kpti_init_ready,
        pml4_kept_indices=kept,
        pml4_stripped_present_count=stripped_n,
        pci_transport=pci_transport,
        pci_ecam_mmio_base=pci_ecam_mmio_base,
        pci_ecam_bus_range=pci_ecam_bus_range,
        pci_ecam_pcd=pci_ecam_pcd,
        pci_ecam_nx=pci_ecam_nx,
        py_platform=platform.system(),
        py_machine=platform.machine(),
        py_processor=platform.processor(),
        microcode_revision=microcode_revision,
        microcode_baseline=microcode_baseline,
        microcode_below_baseline=microcode_below_baseline,
        cve_exposure_count=0,  # filled in below by compliance_audit
        risk_score=0,  # filled in after CVE exposure
    )

    # P4.1: ask the compliance auditor how many architectural CVEs the
    # host is exposed to. Import is lazy to avoid a circular dependency
    # at module top-level (compliance_audit imports HardwareManifest).
    try:
        from services.compliance_audit import derive_cve_exposure

        cve_count = derive_cve_exposure(manifest_pre_score)
    except ImportError:
        cve_count = 0
    manifest_with_cve = dataclasses.replace(
        manifest_pre_score,
        cve_exposure_count=cve_count,
    )

    score = compute_risk_score(manifest_with_cve)
    return dataclasses.replace(manifest_with_cve, risk_score=score)


# ---------------------------------------------------------------------------
# Risk score — AAA plan §4.3 formula
# ---------------------------------------------------------------------------


def compute_risk_score(m: HardwareManifest) -> int:
    """Score 100 (best) down to a floor of 30.

    Per AAA plan §4.3, refined by the P4.2 directive (2026-05-17):
        score = 100
            - 20 if mode != PROTECTED
            - 10 if no SHA-NI   (proxy for AES-NI generation)
            - 10 if no RDRAND   (kernel doesn't currently print this;
                                  conservative: deduct when mode != PROTECTED)
            - 15 if microcode_below_baseline       (P4.2)
            - 3  per known unpatched CVE on host   (P4.1 — capped at 5 deductions)
            - 5  if pqc_backend == "pure_python"
            - 5  if kpti_mode == "LEGACY_KAISER"
        floor 30
    """
    score = 100
    if m.mode != "PROTECTED":
        score -= 20
    if not m.sha_ni:
        # Indirect proxy — host without SHA-NI is also likely older
        # than AES-NI's introduction so we conservatively deduct.
        score -= 10
    if m.mode != "PROTECTED":
        # RDRAND proxy — see docstring.
        score -= 10
    if m.microcode_below_baseline:
        # P4.2: outdated microcode means speculative-execution mitigations
        # (Spectre/Meltdown/MDS) are not fully closed.
        score -= 15
    if m.cve_exposure_count > 0:
        # P4.1: cap the CVE deduction at -15 so it can't double-count with
        # the microcode penalty for the same root cause.
        score -= min(15, m.cve_exposure_count * 3)
    try:
        from services.pqc_sign import verify_path as _pqc_backend

        if _pqc_backend() == "pure_python":
            score -= 5
    except ImportError:
        pass
    if m.kpti_mode == "LEGACY_KAISER":
        score -= 5
    return max(30, score)


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------


def build_from_serial_file(path: str) -> HardwareManifest:
    """Read a kernel-serial log file from disk and parse it.

    Typical usage during integration: the QEMU launch writes to
    /tmp/vos3_console.log; the backend reads it on startup.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return parse_klog(fh.read())
    except (FileNotFoundError, PermissionError):
        # Fail-closed: no klog → unknown host → conservative manifest.
        return parse_klog("")


def build_empty() -> HardwareManifest:
    """Returns the conservative all-zero manifest. Used when no
    kernel log is available (e.g., pure-Python test runs)."""
    return parse_klog("")


__all__ = [
    "HardwareManifest",
    "parse_klog",
    "compute_risk_score",
    "build_from_serial_file",
    "build_empty",
]
