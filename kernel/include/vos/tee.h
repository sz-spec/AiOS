/**
 * @file tee.h
 * @brief VOS3 Trusted-Execution-Environment interface.
 *
 * @details Phase B.3/B.4 addition. Exposes:
 *   - Detected TEE environment (Intel TDX / AMD SEV-SNP / VM / baremetal)
 *   - RTMR extension API for AI-model binding (TDX only)
 *   - Accumulated measurement state for later quote export
 *
 *          The RTMR path implements the 2026 Sovereign-AI bar identified in
 *          the M&A audit: each model load extends RTMR[1] with the SHA-384
 *          of the loaded weights, so the customer's attestation quote
 *          cryptographically binds (platform, kernel, model).
 *
 * @version 1.0.0
 * @date 2026-04-19
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_TEE_H
#define VOS3_TEE_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Detected TEE environment (populated at boot by vos3_cpu_harden_silicon)
 * ============================================================================ */

extern int g_vos3_tee_hv_present;      /**< CPUID.1:ECX bit 31 */
extern int g_vos3_tee_in_intel_tdx;    /**< TDX guest signature on CPUID 0x21 */
extern int g_vos3_tee_in_amd_sev_snp;  /**< SEV_STATUS bit 2 set */

typedef enum vos3_tee_env {
    VOS3_TEE_BAREMETAL      = 0,
    VOS3_TEE_STANDARD_VM    = 1,
    VOS3_TEE_INTEL_TDX      = 2,
    VOS3_TEE_AMD_SEV_SNP    = 3,
} vos3_tee_env_t;

vos3_tee_env_t vos3_tee_env(void);

/* ============================================================================
 * Runtime Measurement Registers (RTMR) — Intel TDX
 * ============================================================================ */

#define VOS3_RTMR_COUNT           4U    /* Architectural — 4 RTMRs per TD */
#define VOS3_RTMR_DIGEST_SIZE    48U    /* SHA-384 digest size in bytes    */

/* VOS3 RTMR allocation. Convention chosen for this project — documented
 * here so quote verifiers can interpret the chain. */
#define VOS3_RTMR_KERNEL_CODE     0U    /* boot-time kernel image hash    */
#define VOS3_RTMR_AI_MODELS       1U    /* model-load-extend chain        */
#define VOS3_RTMR_INTENT_MANIFEST 2U    /* IntentManifest grants          */
#define VOS3_RTMR_AGENT_POLICY    3U    /* per-agent policy decisions     */

/**
 * @brief Extend an RTMR with a 48-byte SHA-384 digest.
 *
 * Wraps TDCALL leaf 2 (TDG.MR.RTMR.EXTEND). Safe to call on non-TDX hosts:
 * if the kernel is not running inside a Trust Domain, the call is a no-op
 * and returns VOS3_TEE_ENOTSUP. Callers should not branch on this return
 * for functional correctness — the measurement is purely attestation data.
 *
 * @param[in] index      RTMR index (0-3)
 * @param[in] digest48   48-byte SHA-384 digest buffer, 64-byte aligned
 *                       if possible (TDX module requires the digest buffer
 *                       to be accessible; VOS3 uses a BSS page).
 * @return 0 on success, negative errno-like code on failure.
 */
int vos3_tee_rtmr_extend(uint32_t index, const uint8_t *digest48);

/**
 * @brief Compute SHA-384 of a model's weight bytes and extend RTMR[1].
 *
 * Convenience function used at model-load time. Records the extended
 * digest in the per-slot measurement ring so vos3_tee_quote() can emit
 * the full chain later.
 *
 * @param[in] slot_id    AI model slot index
 * @param[in] weights    Pointer to contiguous model weight bytes
 * @param[in] len        Length in bytes
 * @return 0 on success (measurement always recorded locally; RTMR extend
 *         is best-effort and is skipped cleanly on non-TDX platforms).
 */
int vos3_tee_model_measure(uint8_t slot_id, const void *weights, size_t len);

/**
 * @brief v20.3-PRODIGY — activate a slot with a single composed-commitment
 *        extend on RTMR[1] (TCB reduction).
 *
 * Replaces the v20.1/v20.2 pattern of three sequential TDCALLs
 *   RTMR[1] << SHA-384(model)
 *   RTMR[1] << SHA-384(intent_manifest)
 *   RTMR[1] << SHA-384(agent_policy)
 * with one
 *   RTMR[1] << SHA-384( h_M || h_I || h_P )
 * where h_M, h_I, h_P are the three component hashes computed once.
 *
 * Individual component hashes remain exposed in the measurement ring
 * and in the JSON-LD IntegrityCertificate emitted by the backend, so
 * auditor visibility is unchanged — only the kernel-side TCB
 * round-trip count is halved (from 3 to 2; RTMR[0] kernel-code is
 * still measured separately at boot).
 *
 * Security note: SHA-384 is collision-resistant, so
 *     SHA-384(h_M || h_I || h_P) = SHA-384(h_M' || h_I' || h_P')
 * iff (h_M, h_I, h_P) = (h_M', h_I', h_P') with overwhelming
 * probability. The one-shot extend therefore binds the triple with
 * the same cryptographic strength as three independent extends.
 *
 * @param[in] slot_id             AI model slot index
 * @param[in] model_digest48      SHA-384 of model weight bytes
 * @param[in] intent_digest48     SHA-384 of IntentManifest bytes
 * @param[in] policy_digest48     SHA-384 of agent-policy bytes; may be
 *                                NULL to signal "no policy" (treated
 *                                as all-zero SHA-384 for binding)
 * @param[out] commitment48       48-byte SHA-384 of the composed
 *                                triple, written for the caller to
 *                                expose on the IntegrityCertificate
 * @return 0 on success (measurement always recorded; hardware extend
 *         best-effort). VOS3_TEE_EINVAL on null mandatory digests.
 */
int vos3_tee_slot_activate_bound(uint8_t slot_id,
                                 const uint8_t *model_digest48,
                                 const uint8_t *intent_digest48,
                                 const uint8_t *policy_digest48,
                                 uint8_t        commitment48[48]);

/* ============================================================================
 * Measurement ring — module-local record of RTMR extensions so quote export
 * can reproduce the chain without reading RTMR state back from the TD module.
 * ============================================================================ */

typedef struct vos3_tee_measurement {
    uint32_t rtmr_index;                            /**< 0-3 */
    uint64_t tick;                                  /**< Boot tick at extend */
    uint8_t  slot_id;                               /**< 0 = not slot-bound */
    uint8_t  pad[3];
    uint8_t  digest[VOS3_RTMR_DIGEST_SIZE];
} vos3_tee_measurement_t;

#define VOS3_TEE_MEASUREMENT_RING_SIZE   128U

/**
 * @brief Snapshot the first N measurements for quote export / audit.
 *
 * @param[out] out     Caller buffer
 * @param[in]  max     Max entries to copy
 * @return number of entries copied
 */
uint32_t vos3_tee_measurements_snapshot(vos3_tee_measurement_t *out, uint32_t max);

/* Return codes */
#define VOS3_TEE_OK            0
#define VOS3_TEE_ENOTSUP      -1   /* Not running in a TEE that supports this op */
#define VOS3_TEE_EINVAL       -2   /* Invalid argument */
#define VOS3_TEE_ETDCALL      -3   /* TDCALL returned non-zero status */

/* ============================================================================
 * v20.5-SINGULARITY — In-kernel IntentManifest validator.
 *
 * See ``kernel/src/mm/intent_validator.c`` for the full discussion. In
 * brief: a structural validator that parses the binary IntentManifest
 * envelope, enforces field-count / size / ASCII-cleanliness invariants,
 * and emits a SHA-384 digest of the same bytes it validated (closing
 * the validate-vs-measure TOCTOU window).
 *
 * The function is part of the kernel image whose hash sits in RTMR[0],
 * so its behaviour is part of the measured trust base — tampering
 * with it changes RTMR[0] and any attestation verifier sees the
 * platform as a different one.
 * ============================================================================ */

int vos3_intent_validate(const uint8_t *buf,
                         size_t          len,
                         uint8_t         digest_out48[48]);

#define VOS3_INTENT_OK                       0
#define VOS3_INTENT_E_NULL                  -1
#define VOS3_INTENT_E_TOO_SHORT             -2
#define VOS3_INTENT_E_TOO_LONG              -3
#define VOS3_INTENT_E_BAD_MAGIC             -4
#define VOS3_INTENT_E_BAD_VERSION           -5
#define VOS3_INTENT_E_BAD_UTF8              -6
#define VOS3_INTENT_E_TOO_MANY_MODELS       -7
#define VOS3_INTENT_E_TOO_MANY_TOOLS        -8
#define VOS3_INTENT_E_TOO_MANY_ROLES        -9
#define VOS3_INTENT_E_FIELD_TRUNCATED      -10
#define VOS3_INTENT_E_NULL_DIGEST_OUT      -11
/* Stage 10.2 — manifest schema v2 */
#define VOS3_INTENT_E_BAD_CONFIDENCE       -12   /**< v2: score outside 0..1000 */

/* ============================================================================
 * Stage 10.2 — IntentManifest schema versions
 *
 * v1 (original): offset 18 is a "reserved must-be-zero" word.
 *
 * v2 (Stage 10.2): offset 18 is repurposed as min_confidence_score, the
 *   minimum LLM-self-reported confidence the Action Bridge will accept
 *   for any action originating from a slot bound to this manifest.
 *   Range 0..1000 (fixed-point representing 0.000..1.000); higher = more
 *   restrictive. Backward compatibility: v1 manifests are still accepted
 *   and treated as score=0 (no guardrail).
 *
 * Wire layout (additive — v2 keeps every other v1 invariant):
 *
 *   offset  size   v1 field          v2 field
 *   ------  ----   --------          --------
 *      0     8     MAGIC             MAGIC
 *      8     2     version=1         version=2
 *     10     2     flags=0           flags=0
 *     12     2     model_count       model_count
 *     14     2     tool_count        tool_count
 *     16     2     role_count        role_count
 *     18     2     reserved=0        min_confidence_score (0..1000)
 *     20    var    body              body
 * ============================================================================ */

#define VOS3_INTENT_VERSION_V1               1U
#define VOS3_INTENT_VERSION_V2               2U
#define VOS3_INTENT_MAX_CONFIDENCE_SCORE  1000U

#endif /* VOS3_TEE_H */
