/**
 * @file hcs.h
 * @brief VOS3 Hardened Context Switch (HCS) — microarchitectural sanitization
 *        barrier for AI slot transitions across trust domains.
 *
 * @details Implements the 2026 Q2 kernel-isolation standard for AI multi-tenancy:
 *   - Hardware L1D flush via MSR_IA32_FLUSH_CMD (0x10B) when CPUID 7:0 EDX[28]
 *     is present; falls back to the existing 32KB software-eviction buffer.
 *   - IBPB (MSR_IA32_PRED_CMD, 0x49) for BTB clear — vendor-agnostic.
 *   - Intel-only IBRS enable (MSR_IA32_SPEC_CTRL, 0x48, bit 0) on dispatch
 *     entry, matching the kernel's `spectre_v2=ibrs` mitigation semantics.
 *   - SMT-sibling safety helper: derives HT siblings from APIC ID so the
 *     scheduler can refuse co-scheduling slots of different trust domains
 *     on sibling logical cores (PR_SPEC_L1D_FLUSH caveat: flush does NOT
 *     mitigate sibling-thread leaks).
 *
 * @note The Linux `PR_SPEC_L1D_FLUSH` prctl uses inverted semantics
 *       (PR_SPEC_ENABLE = mitigation ON). Per the 2025-10-15 Jackman
 *       clarification patch, this helper keeps that discipline: the
 *       `g_hcs_policy_enabled` global is ON when mitigations are applied.
 *
 * Call sites (wired in ai_slots.c):
 *   - vos3_ai_model_slot_finish()   — slot teardown
 *   - vos3_ai_ctx_thaw_commit()     — post-thaw re-arm
 *
 * @version 1.0.0
 * @date 2026-04-19
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_HCS_H
#define VOS3_HCS_H

#include <stdint.h>

/* MSR addresses (x86 Architecture Volume 4). */
#define VOS3_MSR_IA32_SPEC_CTRL   0x48U   /* bit 0 = IBRS, bit 1 = STIBP */
#define VOS3_MSR_IA32_PRED_CMD    0x49U   /* write 1 = IBPB */
#define VOS3_MSR_IA32_FLUSH_CMD   0x10BU  /* write 1 = L1D flush */

#define VOS3_SPEC_CTRL_IBRS       0x1ULL
#define VOS3_SPEC_CTRL_STIBP      0x2ULL
#define VOS3_PRED_CMD_IBPB        0x1ULL
#define VOS3_FLUSH_CMD_L1D        0x1ULL

/**
 * @brief HCS invocation source — for telemetry + call-site attestation.
 */
typedef enum vos3_hcs_src {
    VOS3_HCS_SRC_SLOT_TEARDOWN  = 1,  /* vos3_ai_model_slot_finish() */
    VOS3_HCS_SRC_CTX_THAW       = 2,  /* vos3_ai_ctx_thaw_commit() */
    VOS3_HCS_SRC_CROSS_DOMAIN   = 3,  /* cross-trust-domain slot switch */
    VOS3_HCS_SRC_MANUAL         = 4,  /* explicit VBus command */
} vos3_hcs_src_t;

/**
 * @brief Global policy: apply HCS on cross-domain transitions.
 *
 * Semantics match Linux PR_SPEC_L1D_FLUSH (inverted vs other speculation
 * controls): **1 means mitigation ENABLED, 0 means DISABLED**. Default 1.
 * Toggle from VBus HCS_POLICY command or at boot via cmdline.
 */
extern int g_hcs_policy_enabled;

/**
 * @brief Telemetry counters (per-cause). Read-only from outside hcs.c.
 */
extern uint64_t g_hcs_calls_total;
extern uint64_t g_hcs_hw_l1d_flushes;
extern uint64_t g_hcs_sw_l1d_evictions;
extern uint64_t g_hcs_ibpb_barriers;
extern uint64_t g_hcs_ibrs_enables;

/**
 * @brief Execute a Hardened Context Switch barrier.
 *
 * Orders microarchitectural state flushes before the next AI slot starts
 * executing. Safe to call from any ring-0 context that can issue WRMSR.
 *
 * @param[in] src  Call-site identifier for telemetry / attestation.
 *
 * Sequence (skipped individually when unsupported):
 *   1. sfence — drain store buffer from outgoing slot
 *   2. hardware L1D flush (wrmsr 0x10B, 1) OR software eviction fallback
 *   3. IBPB (wrmsr 0x49, 1) — BTB clear
 *   4. Intel only: enable IBRS (wrmsr 0x48, |= 1)
 *   5. lfence — serialize speculation before caller resumes
 *
 * Telemetry: increments g_hcs_calls_total plus the per-step counters.
 */
void vos3_hcs_flush(vos3_hcs_src_t src);

/**
 * @brief Test whether two logical CPUs share a physical core (SMT siblings).
 *
 * Uses the standard x86 convention: logical CPUs that share a physical core
 * differ only in the low bits of their APIC ID. For a dual-thread core the
 * two threads share apic_id >> 1. For wider SMT (e.g. SMT-4) callers should
 * consult CPUID leaf 0xB (extended topology) for exact masks.
 *
 * @param[in] apic_a  APIC ID of CPU A
 * @param[in] apic_b  APIC ID of CPU B
 * @return 1 if the two CPUs are HT siblings of the same physical core,
 *         0 otherwise (including when a == b).
 *
 * Callers (scheduler / slot activation) MUST use this check before
 * co-scheduling slots of different trust domains. L1D flush does not
 * mitigate concurrent sibling-thread leaks — per LKML 2025-10-15.
 */
int vos3_hcs_smt_siblings(uint32_t apic_a, uint32_t apic_b);

/**
 * @brief Reset all HCS telemetry counters to zero. Pentest-only.
 */
void vos3_hcs_reset_counters(void);

#endif /* VOS3_HCS_H */
