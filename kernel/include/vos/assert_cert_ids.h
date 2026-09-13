/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Certification Assertion ID Registry — M4 of v21.3 plan
 *
 * Centralizes every VOS3_ASSERT_CERT() ID so call sites use symbolic
 * names and the registry document what each one tests. The Python
 * runner cross-references this file to detect ID drift.
 *
 * ID-allocation discipline (see assert_cert.h):
 *   - Each ID used at EXACTLY ONE call site.
 *   - Gaps are reserved for future invariants — not failures.
 *   - Range 1..255 is current allocation space; runner accepts up to
 *     65535 if downstream extends.
 *
 * Honest accounting: this file currently defines 34 cert IDs, used
 * across 60 VOS3_ASSERT_CERT() call sites in vos3_run_cert_harness().
 * The audit-doc tally (116/120) is a separate count over a separate
 * dimension; the harness is parallel, not equal.
 */

#ifndef VOS3_INCLUDE_VOS_ASSERT_CERT_IDS_H
#define VOS3_INCLUDE_VOS_ASSERT_CERT_IDS_H

/* ----- 1..9: Canaries (kmain.c) -----
 *
 * Five "I am running on x86_64 long mode with the basics" checks.
 * If any canary fails, downstream cert points are likely meaningless
 * because the kernel is not running on the hardware it expects.
 */
#define VOS3_CERT_CANARY_LONG_MODE          1   /* EFER.LME == 1 */
#define VOS3_CERT_CANARY_PAGING             2   /* CR0.PG == 1 */
#define VOS3_CERT_CANARY_NX_ENABLED         3   /* EFER.NXE == 1 */
#define VOS3_CERT_CANARY_SSE2_AVAILABLE     4   /* CPUID.1:EDX[26] == 1 */
#define VOS3_CERT_CANARY_KERNEL_VIRT_HIGH   5   /* _kernel_virt_start >= 0xFFFF8000... */

/* ----- 10..19: Boot path -----
 *
 * State that should be true after early boot phases complete.
 */
#define VOS3_CERT_BOOT_GDT_LOADED           10  /* SGDT limit > 0 */
#define VOS3_CERT_BOOT_IDT_LOADED           11  /* SIDT limit >= 0xFFF (256 vectors) */
#define VOS3_CERT_BOOT_RSDP_FOUND           12  /* g_acpi_info.rsdp_phys != 0 */
#define VOS3_CERT_BOOT_MADT_PARSED          13  /* g_acpi_info.cpu_count >= 1 */
#define VOS3_CERT_BOOT_FADT_FOUND           14  /* g_acpi_info.fadt_found == 1 */

/* ----- 20..29: XSAVE -----
 *
 * Probe output + boot-init result (gated when VOS3_HW_XSAVE is off).
 */
#define VOS3_CERT_XSAVE_PROBE_OK            20  /* probe state == 2 */
#define VOS3_CERT_XSAVE_AREA_SIZE_RANGE     21  /* 512 <= area_size <= 4096 */
#define VOS3_CERT_XSAVE_BOOT_INIT_RESULT    22  /* boot_init returns 0 OR -ENODEV */

/* ----- 30..39: LAPIC -----
 *
 * Detection + (when flag on) MMIO mapping + LVT programming.
 */
#define VOS3_CERT_APIC_DETECT_OK            30  /* vos3_apic_detect() == 0 */
#define VOS3_CERT_APIC_MMIO_MAPPED          31  /* g_apic_base_va != NULL when flag on */
#define VOS3_CERT_APIC_SPIV_PROGRAMMED      32  /* SPIV reg reads back 0x1FF when flag on */

/* ----- 40..49: IOAPIC -----
 *
 * MADT enumeration + (when flag on) init result + max_redir sanity.
 */
#define VOS3_CERT_IOAPIC_MADT_ENUMERATED    40  /* g_acpi_info.ioapic_count >= 1 */
#define VOS3_CERT_IOAPIC_INIT_RESULT        41  /* vos3_ioapic_init() == 0 OR -ENODEV */
#define VOS3_CERT_IOAPIC_MAX_REDIR_RANGE    42  /* 16 <= max_redir <= 256 when flag on */

/* ----- 50..59: Scheduler -----
 *
 * Post-init invariants on the run queue + idle task.
 */
#define VOS3_CERT_SCHED_INIT_RC             50  /* vos3_sched_init() == 0 */
#define VOS3_CERT_SCHED_IDLE_TASK_PRESENT   51  /* idle task created with non-null context */

/* ----- 60..69: VBus -----
 *
 * Header magic + opcode range invariants.
 */
#define VOS3_CERT_VBUS_FRAME_MAGIC          60  /* VOS3_VBUS_FRAME_MAGIC well-defined */
#define VOS3_CERT_VBUS_OPCODE_RANGE         61  /* known opcode in [0x100, 0x4FF] */

/* ----- 70..79: Heap / slab -----
 *
 * K-CRIT-2 magic constant + freelist cookie initialization.
 */
#define VOS3_CERT_HEAP_INIT_RC              70  /* vos3_heap_init() == 0 */
#define VOS3_CERT_HEAP_SLAB_MAGIC_DEFINED   71  /* SLAB_OBJ_FREE_MAGIC literal value */

/* ----- 72..76: Heap extended -----
 *
 * Runtime heap integrity + spec constant self-consistency.
 * vos3_heap_check() is safe to call from the harness (no alloc side
 * effects; returns 0 on a healthy heap).
 */
#define VOS3_CERT_HEAP_INTEGRITY_OK         72  /* vos3_heap_check() == 0 */
#define VOS3_CERT_HEAP_SLAB_CLASSES_8       73  /* VOS3_HEAP_SLAB_CLASSES == 8 */
#define VOS3_CERT_HEAP_MAX_SLAB_POWER2      74  /* max-slab-size is a power of 2 */
#define VOS3_CERT_HEAP_ALIGN_POWER2         75  /* VOS3_HEAP_ALIGN is a power of 2 */
#define VOS3_CERT_HEAP_MAGIC_DEFINED        76  /* VOS3_HEAP_MAGIC == 0x48454150 ("HEAP") */

/* ----- 80..86: XSAVE feature probes -----
 *
 * Each probe call returns 0 or 1 (supported/not); −1 signals error.
 * Cert contract: return value >= 0 (probe ran without internal fault).
 * vos3_xsave_self_test() additionally accepts −ENODEV (−19).
 */
#define VOS3_CERT_XSAVE_HAS_XSAVE_PROBE    80  /* vos3_xsave_has_xsave() >= 0 */
#define VOS3_CERT_XSAVE_HAS_AVX_PROBE      81  /* vos3_xsave_has_avx() >= 0 */
#define VOS3_CERT_XSAVE_HAS_AVX512F_PROBE  82  /* vos3_xsave_has_avx512f() >= 0 */
#define VOS3_CERT_XSAVE_LIVE_PATH          83  /* vos3_xsave_live_path_compiled() >= 0 */
#define VOS3_CERT_XSAVE_AREA_ALIGN64       84  /* area_size == 0 OR divisible by 64 */
#define VOS3_CERT_XSAVE_SELF_TEST_RESULT   85  /* self_test() == 0 OR == -ENODEV */
#define VOS3_CERT_XSAVE_FEATURES_NONZERO   86  /* if has_xsave, features mask != 0 */

/* ----- 90..96: Scheduler time constants -----
 *
 * Strict priority ordering on time slices + 100 Hz timer contract.
 * Compile-time constants verified as link-time invariants so any
 * accidental redefinition is caught by the harness on the next boot.
 */
#define VOS3_CERT_SCHED_TIMER_100HZ         90  /* VOS3_TIMER_FREQ == 100 */
#define VOS3_CERT_SCHED_SLICE_IDLE_LT_LOW   91  /* IDLE < LOW */
#define VOS3_CERT_SCHED_SLICE_LOW_LT_NORMAL 92  /* LOW < NORMAL */
#define VOS3_CERT_SCHED_SLICE_NORMAL_LT_HIGH 93 /* NORMAL < HIGH */
#define VOS3_CERT_SCHED_SLICE_HIGH_LT_RT    94  /* HIGH < REALTIME */
#define VOS3_CERT_SCHED_MS_PER_TICK_10      95  /* VOS3_MS_PER_TICK == 10 */
#define VOS3_CERT_SCHED_TICKS_EQ_FREQ       96  /* VOS3_TICKS_PER_SEC == TIMER_FREQ */

/* ----- 100..105: ACPI / boot extended -----
 *
 * Tighter range checks on ACPI data parsed before the harness runs;
 * CR3/CR4 physical state; CPUID baseline capability level.
 */
#define VOS3_CERT_ACPI_CPU_COUNT_SANE       100 /* 1 <= cpu_count <= 256 */
#define VOS3_CERT_ACPI_IOAPIC_COUNT_SANE    101 /* 1 <= ioapic_count <= 32 */
#define VOS3_CERT_BOOT_CR3_NONZERO          102 /* CR3 != 0 (page table loaded) */
#define VOS3_CERT_BOOT_CR4_OSFXSR           103 /* CR4.OSFXSR (bit 9) set */
#define VOS3_CERT_BOOT_CPUID_LEAF0_GE7      104 /* CPUID.0:EAX >= 7 (modern CPU) */
#define VOS3_CERT_BOOT_RSDP_BIOS_REGION     105 /* rsdp_phys < 4 GiB */

/* ----- 110..119: CPU/VMM/ABI invariants -----
 *
 * CPU control-register bits that must remain set after the full boot
 * sequence; VMM kernel address-space initialized; heap/sched constants
 * cross-checked late in boot.
 */
#define VOS3_CERT_VMM_KERNEL_SPACE_NONNULL  110 /* vos3_vmm_get_kernel_space() != NULL */
#define VOS3_CERT_HEAP_MIN_SIZE_16          111 /* VOS3_HEAP_MIN_SIZE == 16 */
#define VOS3_CERT_HEAP_MAGIC_ASCII          112 /* VOS3_HEAP_MAGIC == 0x48454150 ("HEAP") */
#define VOS3_CERT_SCHED_DEFAULT_SLICE_10    113 /* VOS3_SCHED_DEFAULT_SLICE == 10 */
#define VOS3_CERT_APIC_BASE_CANONICAL       114 /* LAPIC phys 0 or >= 0xFEE00000 */
#define VOS3_CERT_BOOT_EFER_SCE             115 /* EFER.SCE (bit 0) — SYSCALL enabled */
#define VOS3_CERT_BOOT_CR0_WP               116 /* CR0.WP (bit 16) set */
#define VOS3_CERT_BOOT_CR0_PE               117 /* CR0.PE (bit 0) set */
#define VOS3_CERT_BOOT_EFER_LMA             118 /* EFER.LMA (bit 10) — long mode active */
#define VOS3_CERT_BOOT_CANONICAL_VA         119 /* _kernel_virt_start in canonical range */

/* ----- 120..126: M3 model-file SecureBoot enforcement matrix (Phase 5) -----
 *
 * Booted-kernel proof of the M3 gate: a real model blob is hashed, an OMS
 * Ed25519 signature is registered + verified against a provisioned trust
 * anchor, and the fail-closed negatives are exercised IN THE BOOTED KERNEL
 * (not just host KAT). Emitted by vos3_m3_qemu_selftest() under
 * VOS3_ASSERT_HARNESS + VOS3_M3_QEMU_SELFTEST.
 */
#define VOS3_CERT_M3_VALID_ACCEPT           120 /* valid OMS sig over digest -> accept */
#define VOS3_CERT_M3_ACTIVATION_STATE       121 /* read-gate verdict cached = verified */
#define VOS3_CERT_M3_FORGED_REJECT          122 /* bit-flipped signature -> reject */
#define VOS3_CERT_M3_TAMPERED_REJECT        123 /* tampered model digest -> reject */
#define VOS3_CERT_M3_UNTRUSTED_KEY_REJECT   124 /* signer != trust anchor -> reject */
#define VOS3_CERT_M3_UNREGISTERED_REJECT    125 /* slot with no signature -> reject */
#define VOS3_CERT_M3_MALLEABILITY_REJECT    126 /* S+L malleated sig -> reject (CVE-2026-4115) */

/* ----- Highest currently allocated ID. The runner uses this to
 * compute the populated-vs-capacity ratio honestly. ----- */
#define VOS3_CERT_MAX_ID_CURRENTLY_USED     126

#endif /* VOS3_INCLUDE_VOS_ASSERT_CERT_IDS_H */
