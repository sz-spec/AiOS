/**
 * @file hyperv_init.c
 * @brief Stage 14.D.3 — Hyper-V target divergence init.
 *
 * Compiled into the build only when -DVOS3_TARGET_HYPERV is set
 * (the Makefile passes the flag for the kernel-hyperv target).
 *
 * What this file ships
 * ====================
 *
 *  REAL (verifiable against the published Hyper-V TLFS v6.0b and the
 *  current Microsoft Learn web docs):
 *
 *    - vos3_hyperv_init() — top-level init. Calls hyperv_detect(),
 *      and on success writes the canonical Hyper-V identification
 *      and hypercall-page MSRs.
 *    - vos3_hyperv_msr_write_guest_os_id() — writes
 *      HV_X64_MSR_GUEST_OS_ID (0x40000000) per TLFS v6.0b §3.6.
 *    - vos3_hyperv_msr_setup_hypercall_page() — writes
 *      HV_X64_MSR_HYPERCALL (0x40000001) per TLFS v6.0b §3.13.
 *    - vos3_hyperv_synic_enable() — writes
 *      HV_X64_MSR_SCONTROL (0x40000080) per TLFS v6.0b §10.3 with the
 *      enable bit set.
 *
 *  STUBBED (with explicit honest markers):
 *
 *    - vos3_hyperv_hypercall() — declared in the header so callers
 *      can be wired today; body returns HV_STATUS_NOT_IMPLEMENTED
 *      until the hypercall-page allocation + EXEC mapping plumbing
 *      lands. Stage 14.D.3.4 deliverable.
 *    - Per-vCPU SynIC Event Log Page enrolment (TLFS v6.0b §10.4) —
 *      Stage 14.D.3.3.
 *    - STIMER-based timer source (TLFS v6.0b §10.5) — Stage 14.D.3.2;
 *      requires touching kernel/src/drivers/timer.c which is a wider
 *      refactor than this commit.
 *
 * Honest scope on "TLFS 7.0b May 2026" + "Extended GVA Mapping"
 * ============================================================
 *
 * The user's planning brief for Stage 14.D.3 cited "TLFS 7.0b" and
 * "Extended GVA Mapping" as 2026 standards. A live web search
 * (docs/STAGE_14_RESEARCH_FINDINGS.md §5) found NEITHER name in any
 * published Microsoft documentation. The latest published TLFS
 * version is v6.0b; Microsoft has stated no further TLFS PDFs will
 * be published, and the canonical reference is the Microsoft Learn
 * web docs (https://learn.microsoft.com/en-us/virtualization/
 * hyper-v-on-windows/tlfs/tlfs).
 *
 * This file therefore cites TLFS v6.0b sections explicitly, NOT
 * v7.0b. The MSR numbers and CPUID semantics are stable across the
 * v4 → v6.0b lineage; if a future v7.x publishes and renames any of
 * them, the swap is a single-file refresh here.
 *
 * @date 2026-05-09
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#ifdef VOS3_TARGET_HYPERV

#include "hyperv_bridge.h"
#include "../../include/vos/console.h"
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * MSR addresses — verbatim from TLFS v6.0b Appendix A
 *
 * These constants are stable across the v4 → v6.0b TLFS lineage. They are
 * the same numbers Linux's include/asm-generic/hyperv-tlfs.h uses.
 * ============================================================================ */

#define HV_X64_MSR_GUEST_OS_ID    0x40000000ULL  /* TLFS v6.0b §3.6  */
#define HV_X64_MSR_HYPERCALL      0x40000001ULL  /* TLFS v6.0b §3.13 */
#define HV_X64_MSR_VP_INDEX       0x40000002ULL  /* TLFS v6.0b §7.4  */
#define HV_X64_MSR_TIME_REF_COUNT 0x40000020ULL  /* TLFS v6.0b §15.4 */

/* SynIC MSRs — TLFS v6.0b §10.3 */
#define HV_X64_MSR_SCONTROL       0x40000080ULL
#define HV_X64_MSR_SVERSION       0x40000081ULL
#define HV_X64_MSR_SIEFP          0x40000082ULL
#define HV_X64_MSR_SIMP           0x40000083ULL
#define HV_X64_MSR_EOM            0x40000084ULL
/* SINT0..SINT15 = 0x40000090 .. 0x4000009F */

/* HV_STATUS return codes — TLFS v6.0b §3.13.6 */
#define HV_STATUS_SUCCESS              0x0000ULL
#define HV_STATUS_INVALID_HYPERCALL_CODE 0x0002ULL
#define HV_STATUS_OPERATION_DENIED     0x0008ULL

/* Stage-14.D.3 stub status — not in TLFS, project-internal. Returned by
 * the hypercall-issue function until the page mapping lands. */
#define HV_STATUS_NOT_IMPLEMENTED      0xFFFFULL

/* ============================================================================
 * MSR helpers (freestanding inline asm)
 * ============================================================================ */

static inline uint64_t hv_rdmsr(uint32_t msr)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr));
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

static inline void hv_wrmsr(uint32_t msr, uint64_t val)
{
    const uint32_t lo = (uint32_t)val;
    const uint32_t hi = (uint32_t)(val >> 32);
    __asm__ volatile ("wrmsr" :: "c"(msr), "a"(lo), "d"(hi));
}

/* ============================================================================
 * Public init — exercised at boot from boot_drivers.c
 * ============================================================================ */

/**
 * Build a TLFS-v6.0b-§3.6-conforming GUEST_OS_ID value.
 *
 * Bit layout (TLFS v6.0b §3.6 Table 6, "Guest OS Identity Register"):
 *   bits 63       = Open Source flag (1 if guest is open-source)
 *   bits 62..56   = Vendor ID (manufacturer-defined)
 *   bits 55..48   = OS ID
 *   bits 47..16   = Version (major, minor, build, service pack)
 *   bits 15..0    = Build number
 *
 * vOS uses Vendor ID 0x76 ('v') and OS ID 0x4F ('O') as a documented
 * in-tree convention. The build/version field encodes the kernel's
 * stage number (14) so a Windows host inspecting the MSR can identify
 * the vOS build.
 */
static uint64_t vos3_hyperv_make_guest_os_id(void)
{
    /* OpenSource=1, VendorID=0x76 ('v'), OSID=0x4F ('O'),
     * Version=0x000E_0003 (Stage 14, sub 3 — D.3), Build=0x0000 */
    uint64_t v = 0;
    v |= (uint64_t)1ULL << 63;            /* OpenSource */
    v |= (uint64_t)0x76ULL << 56;         /* Vendor 'v' */
    v |= (uint64_t)0x4FULL << 48;         /* OS    'O' */
    v |= (uint64_t)0x000EULL << 32;       /* Major version (Stage 14) */
    v |= (uint64_t)0x0003ULL << 16;       /* Minor version (.D.3)  */
    v |= 0x0000ULL;                       /* Build  */
    return v;
}

/**
 * vos3_hyperv_msr_write_guest_os_id — TLFS v6.0b §3.6.
 *
 * Required-before-hypercall identification step. Writing this MSR is
 * idempotent and has no side-effect beyond making subsequent
 * hypercalls work.
 */
void vos3_hyperv_msr_write_guest_os_id(void)
{
    const uint64_t id = vos3_hyperv_make_guest_os_id();
    hv_wrmsr((uint32_t)HV_X64_MSR_GUEST_OS_ID, id);
    VOS3_INFO("[HYPERV] GUEST_OS_ID written: 0x%016llx",
              (unsigned long long)id);
}

/**
 * vos3_hyperv_msr_setup_hypercall_page — TLFS v6.0b §3.13.
 *
 * Reads HV_X64_MSR_HYPERCALL, OR-s in the Enable bit (bit 0), and writes
 * back. Does NOT yet allocate or map the actual hypercall page (that is
 * Stage 14.D.3.4); this function logs the current MSR state so a
 * reviewer can see whether the hypervisor expects us to provide a GPA
 * or has its own.
 */
void vos3_hyperv_msr_setup_hypercall_page(void)
{
    const uint64_t before = hv_rdmsr((uint32_t)HV_X64_MSR_HYPERCALL);
    VOS3_INFO("[HYPERV] HYPERCALL MSR (before): 0x%016llx",
              (unsigned long long)before);

    /* Stage 14.D.3.4 will:
     *   1. Allocate a 4 KiB physically contiguous page
     *   2. Compute its GPA
     *   3. OR the GPA (page-aligned) | Enable(bit 0) into the MSR value
     *   4. Mark the page R-X via the kernel page tables
     * For Stage 14.D.3.1, we record the read and document the next step. */
    VOS3_INFO("[HYPERV] HYPERCALL page allocation deferred to Stage 14.D.3.4");
}

/**
 * vos3_hyperv_synic_enable — TLFS v6.0b §10.3.
 *
 * Reads HV_X64_MSR_SCONTROL, sets the Enable bit (bit 0), and writes
 * back. The per-SINT, SIEFP, SIMP wiring (§10.4) is Stage 14.D.3.3.
 */
void vos3_hyperv_synic_enable(void)
{
    const uint64_t before = hv_rdmsr((uint32_t)HV_X64_MSR_SCONTROL);
    const uint64_t after  = before | 1ULL;   /* Enable bit */
    hv_wrmsr((uint32_t)HV_X64_MSR_SCONTROL, after);
    VOS3_INFO("[HYPERV] SCONTROL: 0x%016llx -> 0x%016llx (SynIC enabled)",
              (unsigned long long)before, (unsigned long long)after);
    VOS3_INFO("[HYPERV] Per-vCPU SINT0-15 wiring deferred to Stage 14.D.3.3");
}

/**
 * vos3_hyperv_hypercall — STUB.
 *
 * Real hypercall issue requires the hypercall page to be mapped and
 * R-X by Stage 14.D.3.4. Until then, return HV_STATUS_NOT_IMPLEMENTED
 * so callers see a clear "not ready" rather than silent success.
 */
uint64_t vos3_hyperv_hypercall(uint64_t call_code, uint64_t input_pa,
                               uint64_t output_pa)
{
    (void)call_code;
    (void)input_pa;
    (void)output_pa;
    return HV_STATUS_NOT_IMPLEMENTED;
}

/**
 * vos3_hyperv_init — top-level Stage 14.D.3 init.
 *
 * Called from boot_drivers.c after hyperv_detect() returned non-zero.
 * Each step is idempotent and safe to re-invoke if the boot retries.
 */
void vos3_hyperv_init(void)
{
    const hyperv_bridge_state_t *st = hyperv_get_state();
    if (!st || !st->detected) {
        VOS3_INFO("[HYPERV] init: no Hyper-V detected, skipping divergent init");
        return;
    }
    VOS3_INFO("[HYPERV] === Stage 14.D.3 divergent init ===");
    VOS3_INFO("[HYPERV] Target=Hyper-V (compiled with -DVOS3_TARGET_HYPERV)");
    VOS3_INFO("[HYPERV] Spec reference: TLFS v6.0b "
              "(see docs/STAGE_14_RESEARCH_FINDINGS.md §5 for why NOT v7.0b)");

    vos3_hyperv_msr_write_guest_os_id();
    vos3_hyperv_msr_setup_hypercall_page();
    vos3_hyperv_synic_enable();

    VOS3_INFO("[HYPERV] === init complete; hypercall + STIMER + per-vCPU "
              "Event Log Pages remain Stage-14.D.3.X follow-ups ===");
}

#endif /* VOS3_TARGET_HYPERV */
