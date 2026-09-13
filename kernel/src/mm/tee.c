/**
 * @file tee.c
 * @brief VOS3 TEE runtime — RTMR extension + measurement ring.
 *
 * @details See tee.h. Bridges AI slot lifecycle events to the Intel TDX
 *          TDCALL interface for Runtime Measurement Register updates,
 *          implementing the 2026 AI-attestation pattern where every model
 *          load is bound into the platform's signed quote.
 *
 * @version 1.0.0
 * @date 2026-04-19
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/tee.h"
#include "../../include/vos/sha384.h"
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/timer.h"

/* ----------------------------------------------------------------------------
 * Environment flags — populated by ai_guard.c at boot.
 * -------------------------------------------------------------------------- */

int g_vos3_tee_hv_present      = 0;
int g_vos3_tee_in_intel_tdx    = 0;
int g_vos3_tee_in_amd_sev_snp  = 0;

vos3_tee_env_t vos3_tee_env(void)
{
    if (g_vos3_tee_in_intel_tdx != 0)   return VOS3_TEE_INTEL_TDX;
    if (g_vos3_tee_in_amd_sev_snp != 0) return VOS3_TEE_AMD_SEV_SNP;
    if (g_vos3_tee_hv_present != 0)     return VOS3_TEE_STANDARD_VM;
    return VOS3_TEE_BAREMETAL;
}

/* ----------------------------------------------------------------------------
 * TDCALL wrapper — Intel TDX guest → TDX module ABI.
 *
 * Opcode: 66 0F 01 CC (TDCALL). ABI:
 *   RAX[in]  = leaf number
 *   RCX[in]  = leaf-specific arg0
 *   RDX[in]  = leaf-specific arg1
 *   ...
 *   RAX[out] = completion status (0 = success)
 * -------------------------------------------------------------------------- */

#define TDG_MR_RTMR_EXTEND     0x2U

/* Page-aligned buffer that backs the TDCALL digest argument.
 * The TDX module requires the digest memory to be accessible — using a
 * dedicated BSS buffer avoids stack-alignment concerns. */
static __attribute__((aligned(64))) uint8_t s_tdx_digest_buf[VOS3_RTMR_DIGEST_SIZE];

static inline uint64_t vos3_tdcall_rtmr_extend(uint32_t index, const uint8_t *digest48)
{
    /* Copy caller's digest into the aligned TDX buffer. */
    for (uint32_t i = 0U; i < VOS3_RTMR_DIGEST_SIZE; i++) {
        s_tdx_digest_buf[i] = digest48[i];
    }

    uint64_t rax = TDG_MR_RTMR_EXTEND;
    uint64_t rcx = (uint64_t)index;
    /* RDX is the *linear* address of the digest buffer. The TDX module
     * translates it via the TD's EPT. */
    uint64_t rdx = (uint64_t)(uintptr_t)s_tdx_digest_buf;

    __asm__ volatile (
        ".byte 0x66, 0x0F, 0x01, 0xCC"     /* TDCALL */
        : "+a"(rax), "+c"(rcx), "+d"(rdx)
        :
        : "memory"
    );

    return rax;  /* 0 = success */
}

int vos3_tee_rtmr_extend(uint32_t index, const uint8_t *digest48)
{
    if (index >= VOS3_RTMR_COUNT || digest48 == NULL) {
        return VOS3_TEE_EINVAL;
    }
    if (g_vos3_tee_in_intel_tdx == 0) {
        /* Not a TD — no silicon path available. AMD SEV-SNP has its own
         * attestation flow (SNP_GUEST_REQUEST / SNP_REPORT); not wired in
         * this patch. Best-effort: return ENOTSUP, caller still records
         * the measurement locally via the ring. */
        return VOS3_TEE_ENOTSUP;
    }

    const uint64_t status = vos3_tdcall_rtmr_extend(index, digest48);
    if (status != 0U) {
        VOS3_WARN("[TEE] TDG.MR.RTMR.EXTEND rtmr=%u status=0x%llx",
                  (unsigned)index, (unsigned long long)status);
        return VOS3_TEE_ETDCALL;
    }
    return VOS3_TEE_OK;
}

/* ----------------------------------------------------------------------------
 * Measurement ring
 * -------------------------------------------------------------------------- */

static vos3_tee_measurement_t s_ring[VOS3_TEE_MEASUREMENT_RING_SIZE];
static volatile uint32_t      s_ring_count = 0U;  /* monotonic; never wraps */

static void ring_record(uint32_t rtmr_index, uint8_t slot_id, const uint8_t *digest48)
{
    const uint32_t idx = s_ring_count % VOS3_TEE_MEASUREMENT_RING_SIZE;
    vos3_tee_measurement_t *m = &s_ring[idx];
    m->rtmr_index = rtmr_index;
    m->slot_id    = slot_id;
    m->tick       = vos3_timer_get_ticks();
    for (uint32_t i = 0U; i < VOS3_RTMR_DIGEST_SIZE; i++) {
        m->digest[i] = digest48[i];
    }
    /* Bump last so readers never see a half-populated entry. */
    __asm__ volatile("" ::: "memory");
    s_ring_count++;
}

uint32_t vos3_tee_measurements_snapshot(vos3_tee_measurement_t *out, uint32_t max)
{
    if (out == NULL || max == 0U) return 0U;
    const uint32_t have = s_ring_count < VOS3_TEE_MEASUREMENT_RING_SIZE
                            ? s_ring_count : VOS3_TEE_MEASUREMENT_RING_SIZE;
    const uint32_t n = (have < max) ? have : max;
    for (uint32_t i = 0U; i < n; i++) {
        out[i] = s_ring[i];
    }
    return n;
}

/* ----------------------------------------------------------------------------
 * High-level: measure a model's weights and extend RTMR[1]
 * -------------------------------------------------------------------------- */

int vos3_tee_model_measure(uint8_t slot_id, const void *weights, size_t len)
{
    if (weights == NULL || len == 0U) {
        return VOS3_TEE_EINVAL;
    }

    uint8_t digest[VOS3_RTMR_DIGEST_SIZE];
    vos3_sha384(weights, len, digest);

    /* Always record locally — gives audit + quote export a view of model
     * history even on baremetal / SEV-SNP where the hardware extend path
     * is absent. */
    ring_record(VOS3_RTMR_AI_MODELS, slot_id, digest);

    /* Best-effort hardware extend. CPUID-gated inside vos3_tee_rtmr_extend. */
    const int rc = vos3_tee_rtmr_extend(VOS3_RTMR_AI_MODELS, digest);
    if (rc == VOS3_TEE_OK) {
        VOS3_INFO("[TEE] RTMR[1] extended: slot=%u len=%llu "
                  "digest=%02x%02x%02x%02x…",
                  (unsigned)slot_id, (unsigned long long)len,
                  digest[0], digest[1], digest[2], digest[3]);
    } else if (rc == VOS3_TEE_ENOTSUP) {
        /* Non-TDX platform: measurement is recorded in s_ring only. */
        VOS3_DEBUG("[TEE] model measurement recorded (non-TDX) "
                   "slot=%u digest=%02x%02x%02x%02x…",
                   (unsigned)slot_id,
                   digest[0], digest[1], digest[2], digest[3]);
    }

    return 0;
}

/* ----------------------------------------------------------------------------
 * v20.3-PRODIGY — composed-commitment slot activation (TCB reduction)
 * --------------------------------------------------------------------------
 *
 * Computes
 *     C = SHA-384( h_M || h_I || h_P )
 * and performs a SINGLE RTMR[1] extend with C instead of three
 * sequential extends with h_M, h_I, h_P. This halves the number of
 * TDCALLs on slot activation (one vs. three) while preserving
 * auditor-visible granularity: the component hashes are still
 * recorded in the measurement ring and surfaced in the JSON-LD
 * IntegrityCertificate, where the auditor can recompute C locally
 * and cross-check it against RTMR[1].
 *
 * The composition is over a FIXED-WIDTH, FIXED-ORDER concatenation
 * of three 48-byte values (no length prefixing needed — width is
 * architectural). Under SHA-384 collision resistance, distinct
 * (h_M, h_I, h_P) triples produce distinct C with negligible
 * adversarial advantage.
 *
 * NULL ``policy_digest48`` is admitted as "no policy yet" and
 * substituted with the all-zero SHA-384, so early slot activations
 * can still commit to a cert before the policy subsystem binds.
 * Auditor can see the all-zero h_P in the certificate and interpret
 * it exactly (fail-visible over fail-silent).
 */
int vos3_tee_slot_activate_bound(uint8_t slot_id,
                                 const uint8_t *model_digest48,
                                 const uint8_t *intent_digest48,
                                 const uint8_t *policy_digest48,
                                 uint8_t        commitment48[48])
{
    if (model_digest48 == NULL || intent_digest48 == NULL ||
        commitment48 == NULL) {
        return VOS3_TEE_EINVAL;
    }

    /* 1. Assemble the composed triple on the stack.
     * 144 bytes — fits comfortably in any kernel stack budget. */
    static const uint8_t _zero_sha384[VOS3_RTMR_DIGEST_SIZE] = {0};
    uint8_t triple[3 * VOS3_RTMR_DIGEST_SIZE];
    for (uint32_t i = 0U; i < VOS3_RTMR_DIGEST_SIZE; i++) {
        triple[i]                                    = model_digest48[i];
        triple[VOS3_RTMR_DIGEST_SIZE + i]            = intent_digest48[i];
        triple[2U * VOS3_RTMR_DIGEST_SIZE + i]       =
            (policy_digest48 != NULL) ? policy_digest48[i] : _zero_sha384[i];
    }

    /* 2. C = SHA-384(triple). */
    vos3_sha384(triple, sizeof(triple), commitment48);

    /* 3. Record all four artefacts in the measurement ring: the three
     * component hashes (for auditor granularity) plus the commitment
     * that is actually extended into the hardware register. */
    ring_record(VOS3_RTMR_AI_MODELS,        slot_id, model_digest48);
    ring_record(VOS3_RTMR_INTENT_MANIFEST,  slot_id, intent_digest48);
    ring_record(VOS3_RTMR_AGENT_POLICY,     slot_id,
                (policy_digest48 != NULL) ? policy_digest48 : _zero_sha384);
    ring_record(VOS3_RTMR_AI_MODELS,        slot_id, commitment48);

    /* 4. Single hardware extend with the commitment. Best-effort;
     * CPUID-gated inside vos3_tee_rtmr_extend. */
    const int rc = vos3_tee_rtmr_extend(VOS3_RTMR_AI_MODELS, commitment48);
    if (rc == VOS3_TEE_OK) {
        VOS3_INFO("[TEE] RTMR[1] composed-extend: slot=%u "
                  "C=%02x%02x%02x%02x… (TCB: 1 TDCALL vs 3 legacy)",
                  (unsigned)slot_id,
                  commitment48[0], commitment48[1],
                  commitment48[2], commitment48[3]);
    } else if (rc == VOS3_TEE_ENOTSUP) {
        VOS3_DEBUG("[TEE] composed commitment recorded (non-TDX) "
                   "slot=%u C=%02x%02x%02x%02x…",
                   (unsigned)slot_id,
                   commitment48[0], commitment48[1],
                   commitment48[2], commitment48[3]);
    }

    return 0;
}
