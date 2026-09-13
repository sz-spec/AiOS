# Microcode Baseline (Minimum-Secure Revisions)

**Anchor:** `aeb3736` · **Phase:** P4.2 · **Source-of-truth:** `kernel/src/arch/x86_64/microcode_check.c`

## Why a baseline

Every CVE in `docs/HARDWARE_CVE_INVENTORY_2010_2026.md` flagged
`requires_microcode=True` was closed by a specific microcode revision
shipped by Intel or AMD. Running below that revision means the
side-channel mitigation simply isn't in place — the kernel KPTI dance
runs, but the speculative-execution gadget still exists.

The kernel reads the running microcode revision at boot (MSR `0x8B`
following the Intel SDM Vol 3A §9.11.7.1 dance), compares it against
the per-family baseline below, and emits:

```
[MICROCODE] revision=0x00000ce baseline=0x05003604 below_baseline=yes
[SECURITY] Microcode outdated. System logic vulnerable to speculative execution side-channels.
```

The Universal Hardware Manifest parser picks this up and:

1. forces `manifest.mode = RESTRICTED_LEGACY` (regardless of CPU class),
2. deducts `-15` from `risk_score`, and
3. triggers the P4.3 sandbox rlimit-halving via the mode change.

## Baseline table (sampled — 2026-05-17)

| Vendor | Family | Model | Codename | Minimum-secure revision |
|---|---|---|---|---|
| Intel | 0x06 | 0x25 | Westmere (mobile) | `0x00000013` |
| Intel | 0x06 | 0x2C | Westmere (EP/EX) | `0x0000001F` |
| Intel | 0x06 | 0x3A | Ivy Bridge | `0x00000021` |
| Intel | 0x06 | 0x3C | Haswell | `0x00000028` |
| Intel | 0x06 | 0x4E | Skylake (mobile) | `0x000000F0` |
| Intel | 0x06 | 0x55 | Cascade Lake / SKX | `0x05003604` |
| AMD | 0x17 | 0x01 | Zen (Naples) | `0x08001138` |
| AMD | 0x17 | 0x31 | Zen 2 (Rome) | `0x0830107C` |
| AMD | 0x19 | 0x21 | Zen 3 (Milan) | `0x0A201025` |
| AMD | 0x19 | 0x11 | Zen 4 (Genoa) | `0x0A101148` |

Each row is the lowest revision known to ship the full mitigation set
for the CVEs in `compliance_audit.py::_BASELINE` flagged
`requires_microcode=True`.

## How to extend

1. Pick a family/model not yet pinned.
2. Cross-reference the vendor's published microcode-revision advisories
   (Intel `intel-ucode-202?.tgz` release notes, AMD ucode patches).
3. Choose the **lowest** revision that ships the CVE fix and add a row
   to `g_baselines` in `microcode_check.c` and a row to the table above.
4. Re-run the gate.

## Reading the revision (kernel-side, summarized)

```c
// Intel:
vos3_write_msr(0x8B, 0);          // kick
vos3_cpuid(1, 0, ...);            // commit
rev = (vos3_read_msr(0x8B) >> 32) // revision in high 32 bits

// AMD:
rev = (uint32_t)vos3_read_msr(0x8B)  // patch ID in low 32 bits
```

The vendor branch lives in `microcode_check.c::vos3_microcode_read_revision`.

## What's NOT in scope

- **Live microcode updates** — loading a fresh microcode blob at boot
  is a separate kernel feature. P4.2 only **checks** the revision the
  firmware/CPU shipped with. Operators run the update offline (BIOS,
  OS-provided microcode loader on bare metal).
- **Refusing to boot** below baseline — the operator may not have a
  newer microcode available. We boot with the side channel acknowledged
  in the audit row + the sandbox tightened.
- **Per-stepping baselines** — we pin per (family, model). Stepping
  differences are real but the table would explode; if a stepping-level
  difference matters in practice, that's a follow-up entry.

## Verification

Source-level: `backend/tests/kernel/test_mitigation_factory_source.py`
pins the `[MICROCODE]` line shape. The Python-side parsing is pinned by
`backend/tests/hardware/test_universal_manifest.py::test_microcode_*`
and the rlimit-tightening behavior is pinned by
`backend/tests/sandbox/test_adaptive_rlimits.py::test_outdated_microcode_forces_restricted_tier`.
