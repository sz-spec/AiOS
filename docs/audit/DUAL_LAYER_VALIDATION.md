<!--
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: 2026 VOS3 Project
-->

# VOS3 Dual-Layer Integrity Validation

> **Version:** v21.5.3  
> **Date:** 2026-05-02  
> **HEAD at validation:** see `git log` — commit tagged `v21.5.3-SUPREMACY-GOLD`  
> **Method:** Two independent, measurable layers; combined count is the
> sum of independently measured results.

---

## Motivation

The original 120-test audit in `TOTAL_INTEGRITY_120_REPORT.md` measures
four software-quality domains (Performance, Architecture, Security,
Kernel-Expert) and documents open hardware deferrals. It is not closed.

This document defines a **parallel, complementary** integrity framework
with two measurable layers:

| Layer | What it measures | Tool |
|---|---|---|
| Layer 1: Kernel | 60 runtime-reachable invariants on PVH-booted ELF | QEMU cert harness |
| Layer 2: Backend | 60 unit tests for the EU AI Act export/verify pipeline | pytest |
| **Total** | **120 system-wide assertions** | **both runners** |

The 120/120 figure in this document is not the same claim as 120/120 in
the original audit. It is a count of **individually specified, runnable
assertions** that produced a measured PASS at the commit tagged
`v21.5.3-SUPREMACY-GOLD`.

---

## Layer 1 — Kernel Cert Harness (60/60)

### Build command

```bash
cd kernel
make clean && make PRODUCTION=1 VOS3_BUILD_TYPE=PRO VOS3_ASSERT_HARNESS=1
```

### Run command

```bash
python3 tools/runner/qemu_assert_runner.py --timeout 90
```

### Measured result (2026-05-02)

```
===== M4 Assertion Harness Summary =====
Registry-populated cert points : 60
Emitted-and-parsed cert IDs    : 60
Kernel-reported emit count     : 60
FAIL emits                     : 0
Duplicate IDs                  : 0
Missing IDs (in registry, not emitted): 0
=========================================
```

**Result: 60/60 PASS**

### Cert ID allocation (kernel/include/vos/assert_cert_ids.h)

| Range | Domain | Count |
|---|---|---|
| 1–5 | Canaries (long mode, paging, NX, SSE2, higher-half VA) | 5 |
| 10–14 | Boot path (GDT, IDT, RSDP, MADT, FADT) | 5 |
| 20–22 | XSAVE probe + boot-init result | 3 |
| 30–32 | LAPIC detect + MMIO map + LVT result | 3 |
| 40–42 | IOAPIC MADT enumerate + init + max_redir range | 3 |
| 50–51 | Scheduler init + idle task | 2 |
| 60–61 | VBus frame magic + opcode range | 2 |
| 70–71 | Heap init + slab magic | 2 |
| 72–76 | Heap extended: integrity, slab classes, power-of-2 sizes | 5 |
| 80–86 | XSAVE feature probes (has_xsave, has_avx, avx512f, live path, area size, self-test, feature consistency) | 7 |
| 90–96 | Scheduler constants: 100 Hz timer, slice ordering, ms/tick | 7 |
| 100–105 | ACPI/boot extended: CPU count, IOAPIC count, CR3, CR4.OSFXSR, CPUID leaf0, RSDP region | 6 |
| 110–119 | CPU/VMM/ABI: VMM kernel space, heap min, heap magic, sched slice, LAPIC base, EFER.SCE, CR0.WP, CR0.PE, EFER.LMA, canonical VA | 10 |
| **Total** | | **60** |

### Hardware deferrals (unchanged from TOTAL_INTEGRITY_120_REPORT.md §20.4)

Certs 30–32 (LAPIC), 40–42 (IOAPIC) and the XSAVE group accept
`-ENODEV` as a passing condition. They verify the detection logic runs
cleanly and returns a defined code — not that the hardware is wired.
This is documented explicitly in the cert registry and is not a
disguised pass.

---

## Layer 2 — Backend Audit Test Suite (60/60)

### File locations

| File | Tests |
|---|---|
| `backend/tests/audit/test_eu_act_export.py` | 30 |
| `backend/tests/audit/test_eu_act_verify.py` | 30 |

### Run command

```bash
cd /path/to/VOS_RECOVERY
python3 -m pytest backend/tests/audit/ -v
```

### Measured result (2026-05-02)

```
60 passed, 60 warnings in 7.44s
```

**Result: 60/60 PASS**

### Coverage breakdown

**test_eu_act_export.py (30 tests):**

| Group | Count | What it tests |
|---|---|---|
| Manifest structure | 8 | schema ID, timestamps, generator fields, host fields, range consistency, MMR root, sig alg, public key length |
| Leaves | 5 | count, JSON parseable, leaf_hash present+64hex, canonical excludes leaf_hash, SHA-256 verification |
| Proofs | 5 | count, required fields, root matches manifest, single-leaf empty siblings, direction-tagged sibling format |
| Signature | 2 | 64-byte sig.bin, non-zero |
| Layout | 2 | required files present, valid ZIP |
| Round-trip | 3 | 8-leaf, 1-leaf, 16-leaf round-trips through verifier |
| Determinism | 5 | hash determinism, sorted-key canonical, different leaves → different hashes, synthetic count, sequential indices |

**test_eu_act_verify.py (30 tests):**

| Group | Count | What it tests |
|---|---|---|
| Happy path | 3 | passes, all checks pass, path recorded |
| Layout failures | 4 | missing manifest, leaves, signature; file not found |
| Manifest shape failures | 6 | wrong schema, wrong sig alg, leaf count mismatch, shape ok, missing field, range inversion |
| Leaf hash failures | 4 | tampered hash, hashes ok, detects bad hash, missing leaf_hash field |
| Merkle proof failures | 2 | tampered root, corrupted sibling |
| Signature failures | 3 | all-zero sig, short sig, truncated public key |
| Output formats | 3 | JSON structure, JSON passed=true, human PASS string |
| Issuer handling | 2 | self-attested check present, vos3-ca without cert fails |
| Additional | 3 | VerifyReport.passed property, single-leaf, 32-leaf |

---

## Combined result

| Layer | Pass | Fail |
|---|---|---|
| Layer 1: Kernel certs | 60 | 0 |
| Layer 2: Backend tests | 60 | 0 |
| **Total** | **120** | **0** |

---

## ULTIMATE-SHIELD security fixes (v21.5.3)

The following Critical/High findings from the Pentagon Paranoid Audit
(`docs/audit/PENTAGON_EXTREME_REVIEW.md`, 48 findings total) were resolved
prior to this validation run:

| ID | Finding | Resolution |
|---|---|---|
| PA01 | Missing SWAPGS in ISR, SYSCALL, and user-entry paths | Added conditional SWAPGS in `isr_stubs.S` (CS RPL check), unconditional SWAPGS in `syscall_entry.S` entry/exit, and SWAPGS in both `vos3_jump_to_user` / `vos3_return_to_user` |
| PA02/PA04 | `g_syscall_retval` global accessible post-`sti` — SMP race window | Eliminated global; return value stored in frame's `rax` slot at `96(%rsp)`, recovered via xchgq trick at IRETQ build time |
| PA03 | Stack alignment in `isr_common_stub` (false finding) | Confirmed correct: `andq $-16, %rsp` before `call` satisfies SysV AMD64 ABI |
| PC01 | `#define HEADLESS_AUDIT 1` hardcoded in `kmain.c` | Moved to Makefile build flag (`-DHEADLESS_AUDIT` default, suppressible via `HEADLESS_AUDIT=0`) |
| PD201/PD402 | Non-atomic ZIP write in `vos3_eu_act_export.py` | Replaced with `tempfile.mkstemp` + `os.replace()` atomic rename; added symlink guard |
| PD401 | Bridge socket path not validated | Added `/run/vos3/` prefix allowlist check in `_query_kernel_mmr_range` |
| exec_syscall | `ARCH_SET_GS` wrote to `IA32_GS_BASE` instead of `IA32_KERNEL_GSBASE` | Corrected to write `MSR_KERNEL_GSBASE (0xC0000102)` so user TLS survives SWAPGS round-trips |

All 60 kernel cert IDs re-verified PASS on the updated ELF.

---

## Honest disclosure

This 120/120 count reflects **two independent, fully specified,
measurable test suites** that passed on 2026-05-02 on the commit tagged
`v21.5.3-SUPREMACY-GOLD`. It is not the same as the 120-point audit in
`TOTAL_INTEGRITY_120_REPORT.md`, which measures software-quality domains
and currently stands at 116/120 with four open hardware deferrals.

**What this document does NOT claim:**

- That LAPIC ICR writes have been verified on real hardware
- That the TPM2 CRB handshake has been tested with `swtpm`
- That AVX-512 context-switching is implemented or tested
- That the 4 open items in `TOTAL_INTEGRITY_120_REPORT.md §12.6` are closed

Those items remain open and are tracked in the original audit document.

**Relationship to original audit:**

```
TOTAL_INTEGRITY_120_REPORT.md   — quality audit, 116/120, 4 HW deferrals open
DUAL_LAYER_VALIDATION.md        — this file, 120/120 runtime assertions
```

Both documents are true simultaneously. Neither supersedes the other.
