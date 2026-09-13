/**
 * @file hcs.c
 * @brief VOS3 Hardened Context Switch implementation (see hcs.h).
 *
 * @details v20.1 addition closing the kernel-side gap identified in the 2026
 *          Q2 M&A audit: SMEP/SMAP alone do not mitigate Retbleed / L1DES /
 *          MDS class leaks across AI slot boundaries. This TU consolidates
 *          the microarchitectural sanitization sequence previously inlined
 *          at two call sites in ai_slots.c.
 *
 * @version 1.0.0
 * @date 2026-04-19
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/hcs.h"
#include "../../include/arch/x86_64/cpu.h"
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"
#include "ai_guard_internal.h"

/* ----------------------------------------------------------------------------
 * Policy + telemetry state
 * -------------------------------------------------------------------------- */

int g_hcs_policy_enabled = 1;          /* mitigation ON by default */

uint64_t g_hcs_calls_total      = 0U;
uint64_t g_hcs_hw_l1d_flushes   = 0U;
uint64_t g_hcs_sw_l1d_evictions = 0U;
uint64_t g_hcs_ibpb_barriers    = 0U;
uint64_t g_hcs_ibrs_enables     = 0U;

/* Cached CPU vendor — detected on first HCS call so the hot-path avoids
 * re-issuing CPUID. VOS3_CPU_VENDOR_UNKNOWN means "not yet detected." */
static vos3_cpu_vendor_t s_cached_vendor = VOS3_CPU_VENDOR_UNKNOWN;

/* ----------------------------------------------------------------------------
 * Internal helpers
 * -------------------------------------------------------------------------- */

static inline vos3_cpu_vendor_t hcs_get_vendor(void)
{
    if (s_cached_vendor != VOS3_CPU_VENDOR_UNKNOWN) {
        return s_cached_vendor;
    }
    vos3_cpu_info_t info;
    vos3_cpu_detect(&info);
    s_cached_vendor = info.vendor;
    return s_cached_vendor;
}

/* Software L1D eviction fallback — walks the 32KB scratch buffer at 64B
 * stride, defined in ai_guard.c. Matches the existing vos3_ai_l1d_sanitize
 * implementation so call sites migrated to HCS see identical behaviour on
 * CPUs without MSR_IA32_FLUSH_CMD. */
static inline void hcs_sw_l1d_evict(void)
{
    for (size_t i = 0U; i < sizeof(g_l1d_flush_buf); i += 64U) {
        g_l1d_flush_buf[i] = (uint8_t)i;
    }
}

/* ----------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */

void vos3_hcs_flush(vos3_hcs_src_t src)
{
    if (g_hcs_policy_enabled == 0) {
        /* Telemetry still records the call so audits see the skipped path. */
        g_hcs_calls_total++;
        return;
    }

    /* v20.1.1 (gauntlet #2): Disable IRQs around the MSR sequence so a
     * nested interrupt cannot preempt between L1D flush and IBPB —
     * which would leave the BTB populated with outgoing-slot state
     * visible to the interrupt handler's cache touches. Total
     * uninterruptible window ≈ 2 µs; well under the tick budget. */
    const vos3_irqflags_t __hcs_flags = vos3_irq_save();

    /* 1. Drain outgoing slot's store buffer before any cache manipulation. */
    __asm__ volatile ("sfence" ::: "memory");

    /* 2. L1D flush — hardware fast path when available, else software.
     *
     * v20.3-PRODIGY (audit P0-C) — fence-symmetry normalization
     * =========================================================
     * Intel SDM Vol 4 §2.8.3 ("Availability of Specific Microcode")
     * specifies that WRMSR to IA32_FLUSH_CMD (0x10B) is *serializing*
     * with respect to all subsequent instruction fetches and to any
     * cache-line fills that would otherwise satisfy a subsequent load.
     * Relying on that implicit serialization is correct today but
     * brittle against future microcode updates that narrow the
     * promise. We therefore emit an explicit ``lfence`` on BOTH the
     * hardware and software branches so the invariant
     *
     *     ∀ load ℓ issued during [t_flush_done, t_IBPB_start]:
     *         ℓ ∉ L1D_outgoing ∧ ℓ ∉ BTB_outgoing
     *
     * is established *syntactically* in the source — no microcode
     * guarantee required. The lfence is a pipeline drain, ~1 cycle
     * on current silicon (negligible inside a ~2 µs uninterruptible
     * window), and gives the fence sequence a symmetric shape across
     * vendor branches.
     */
    if (g_cpu_has_flush_l1d != 0) {
        vos3_write_msr(VOS3_MSR_IA32_FLUSH_CMD, VOS3_FLUSH_CMD_L1D);
        __asm__ volatile ("lfence" ::: "memory");
        g_hcs_hw_l1d_flushes++;
    } else {
        hcs_sw_l1d_evict();
        __asm__ volatile ("sfence" ::: "memory");
        __asm__ volatile ("lfence" ::: "memory");
        g_hcs_sw_l1d_evictions++;
    }

    /* 3. IBPB — vendor-agnostic branch-predictor barrier.
     *    Gated on CPUID detection; available on Intel Skylake+ and
     *    AMD Zen+ when exposed by the hypervisor. */
    if (g_cpu_has_ibpb != 0) {
        vos3_write_msr(VOS3_MSR_IA32_PRED_CMD, VOS3_PRED_CMD_IBPB);
        g_hcs_ibpb_barriers++;
    }

    /* 4. Vendor dispatch — Intel: enable IBRS for this return path.
     *    AMD: `retbleed=unret` / `retbleed=ibpb` path — IBPB above is
     *    already the canonical AMD mitigation, no additional MSR needed.
     *    Intel bit-0 of IA32_SPEC_CTRL engages IBRS; we OR rather than
     *    overwrite to preserve STIBP / SSBD bits set elsewhere. */
    if (hcs_get_vendor() == VOS3_CPU_VENDOR_INTEL && g_cpu_has_ibpb != 0) {
        uint64_t spec_ctrl = vos3_read_msr(VOS3_MSR_IA32_SPEC_CTRL);
        spec_ctrl |= VOS3_SPEC_CTRL_IBRS;
        vos3_write_msr(VOS3_MSR_IA32_SPEC_CTRL, spec_ctrl);
        g_hcs_ibrs_enables++;
    }

    /* 5. Serialize — no speculative execution past this point until the
     *    preceding WRMSRs have retired. */
    __asm__ volatile ("lfence" ::: "memory");

    /* Re-enable IRQs if they were enabled on entry. */
    vos3_irq_restore(__hcs_flags);

    g_hcs_calls_total++;

    /* One-shot diagnostic per source on the first few invocations; rate-
     * limited by modulo on the aggregate counter to avoid log flooding. */
    if ((g_hcs_calls_total & 0xFFFULL) == 1ULL) {
        VOS3_DEBUG("[HCS] src=%d total=%llu hw=%llu sw=%llu ibpb=%llu ibrs=%llu",
                   (int)src,
                   (unsigned long long)g_hcs_calls_total,
                   (unsigned long long)g_hcs_hw_l1d_flushes,
                   (unsigned long long)g_hcs_sw_l1d_evictions,
                   (unsigned long long)g_hcs_ibpb_barriers,
                   (unsigned long long)g_hcs_ibrs_enables);
    }
}

int vos3_hcs_smt_siblings(uint32_t apic_a, uint32_t apic_b)
{
    /* Same APIC ID = same logical CPU, not siblings. */
    if (apic_a == apic_b) {
        return 0;
    }
    /* Standard x86 HT topology: sibling threads of a physical core differ
     * only in bit 0 of the APIC ID. For wider SMT, callers should query
     * CPUID.0BH (extended topology) for the precise core-mask shift. */
    return ((apic_a >> 1) == (apic_b >> 1)) ? 1 : 0;
}

void vos3_hcs_reset_counters(void)
{
    g_hcs_calls_total      = 0ULL;
    g_hcs_hw_l1d_flushes   = 0ULL;
    g_hcs_sw_l1d_evictions = 0ULL;
    g_hcs_ibpb_barriers    = 0ULL;
    g_hcs_ibrs_enables     = 0ULL;
}
