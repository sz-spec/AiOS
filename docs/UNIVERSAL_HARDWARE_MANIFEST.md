# Universal Hardware Manifest — Schema & Mode Matrix

**Anchor:** `aeb3736` · **Phase:** P3.2 · **Module:** `backend/services/hardware_manifest.py`

## Purpose

A single backend-side dataclass that describes the host's capability tier — built by parsing the kernel's serial boot log. Downstream consumers:

- **Sandbox-tier selection** (P4.3): `manifest.mode` → rlimits set
- **AAA cert reports**: human-readable summary of what the operator got
- **EU AI Act Annex IV §V.1**: compliance evidence record

The kernel's boot lines are the **canonical source of truth**. We don't re-do CPUID from Python; we parse the kernel's own output.

## Mode matrix (AAA plan §3.2)

| CPU class | KPTI mode | PCI transport | PQC backend | Manifest `.mode` |
|---|---|---|---|---|
| AVX-512 + SHA-NI + PCID+INVPCID+SMEP+SMAP (Ice Lake+, Zen 4+) | `PROTECTED_FULL` | ECAM | `oqs_avx512` | `PROTECTED` |
| AVX2 + AES-NI + PCID+INVPCID+SMEP+SMAP (Haswell–Comet, Zen 1–3) | `PROTECTED_FULL` | ECAM | `oqs_avx2` | `PROTECTED` |
| PCID + AES-NI no SMEP (Westmere–Sandy, Bulldozer) | `PROTECTED_PCID_ONLY` | ECAM or Port-I/O | `oqs_scalar` | `PROTECTED` |
| 64-bit no PCID (Nehalem, Phenom II) | `LEGACY_KAISER` | Port-I/O | `oqs_scalar` or `pure_python` | `RESTRICTED_LEGACY` |
| Pre-x86_64 | refused boot | — | — | (kernel never reaches Python) |
| Mid-boot snapshot / no klog yet | none | none | none | `UNKNOWN` |

**Mode derivation rule:** `PROTECTED` requires BOTH `pcid=yes` AND `kpti_init_ready=true`. Any missing field falls to `RESTRICTED_LEGACY` (Security > Availability — when we can't prove protection, assume we don't have it). Empty klog → `UNKNOWN`.

## Risk-score formula (P4.3 input)

```
score = 100
  - 20   if mode != PROTECTED
  - 10   if no SHA-NI                       (proxy for AES-NI era)
  - 10   if mode != PROTECTED               (proxy for RDRAND availability)
  - 10   if microcode_below_baseline        (placeholder until P4.2)
  -  5   if pqc_backend == "pure_python"
  -  5   if kpti_mode == "LEGACY_KAISER"
floor: 30
```

Reference measurements:

| Host | Mode | Score |
|---|---|---|
| Apple M-series via QEMU TCG, LEGACY_KAISER, pure_python | `RESTRICTED_LEGACY` | **60** |
| Hypothetical AVX-512 Linux + oqs-python | `PROTECTED` | ≥85 |
| Empty klog (boot just started) | `UNKNOWN` | ≤70 |

## Schema (`@dataclass(frozen=True)`)

```python
class HardwareManifest:
    # High-level
    mode: str                                  # "PROTECTED" | "RESTRICTED_LEGACY" | "UNKNOWN"

    # KPTI / mitigation (from `[KPTI] mode=...` line)
    kpti_mode: Optional[str]                   # PROTECTED_FULL | PROTECTED_PCID_ONLY | LEGACY_KAISER
    pcid: bool
    invpcid: bool
    smep: bool
    smap: bool
    sha_ni: bool
    kpti_budget: Optional[str]                 # "<=2%" | "<=3%" | "5-30%"

    # KPTI runtime (from `[KPTI] init: ready` + `[KPTI] PML4 strip` lines)
    kpti_init_ready: bool
    pml4_kept_indices: tuple                   # e.g. ("256", "511")
    pml4_stripped_present_count: Optional[int]

    # PCI transport (from `[PCI-ECAM] available` or `MCFG absent` lines)
    pci_transport: str                         # "ECAM" | "PORT_IO" | "UNKNOWN"
    pci_ecam_mmio_base: Optional[int]          # phys address, e.g. 0xB0000000
    pci_ecam_bus_range: Optional[tuple]        # (start, end) ints
    pci_ecam_pcd: Optional[bool]
    pci_ecam_nx: Optional[bool]

    # Backend-side Python view (cross-check only)
    py_platform: str                           # platform.system()
    py_machine: str                            # platform.machine()
    py_processor: str

    # Risk score (computed; AAA plan §4.3)
    risk_score: int

    @property
    def is_protected(self) -> bool: ...
    @property
    def is_restricted_legacy(self) -> bool: ...
```

## API

```python
from services.hardware_manifest import (
    parse_klog,             # text → HardwareManifest
    build_from_serial_file, # path → HardwareManifest
    build_empty,            # the safe-default conservative manifest
    compute_risk_score,     # pure fn over manifest → int
)
```

All entry points are **fail-closed**: malformed input never raises, always returns a manifest (`mode="UNKNOWN"` or `"RESTRICTED_LEGACY"` in the worst case).

## Source-of-truth pinning

The parser regexes match the strings emitted by:

| Kernel source | Emits | Parsed by regex |
|---|---|---|
| `kernel/src/arch/x86_64/mitigation_factory.c` | `[KPTI] mode=... pcid=... ...` | `_RE_KPTI_MODE` |
| `kernel/src/arch/x86_64/mitigation_factory.c` | (KAISER hint line) | (informational only, not parsed) |
| `kernel/src/arch/x86_64/kpti.c` | `[KPTI] PML4 strip: kept=[...] stripped_present_entries=N residual_attack_surface=...` | `_RE_KPTI_STRIP` |
| `kernel/src/arch/x86_64/kpti.c` | `[KPTI] init: ready ...` | `_RE_KPTI_INIT_READY` |
| `kernel/src/arch/x86_64/pci_ecam.c` | `[PCI-ECAM] available — buses ...` | `_RE_PCI_ECAM_OK` |
| `kernel/src/arch/x86_64/pci_ecam.c` | `[PCI-ECAM] MCFG absent — Port-I/O fallback active` | `_RE_PCI_ECAM_FALLBACK` |

Each kernel-side string is **pinned in source** by the corresponding `tests/kernel/test_*_source.py` regex test. A kernel refactor that drifts the format would break those tests first — surfacing the change before the parser silently misreads.

## What's NOT in scope (yet)

- **Microcode revision parsing** — depends on P4.2 (`kernel/src/arch/x86_64/microcode_check.c`). The manifest field placeholder exists; risk-score deduction is set to 0 until P4.2 ships.
- **AVX backend identification from Python** — currently we just check whether `oqs-python` is installed (via `services.pqc_sign.verify_path()`). When P4.2 lands, the kernel will print the CPU family + model and we'll cross-check.
- **Live periodic re-parse** — manifest is a one-shot read of the serial log. A long-running backend could re-parse on a tick to pick up new kernel events; out of scope for the initial implementation.
