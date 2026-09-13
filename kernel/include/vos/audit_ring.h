/**
 * @file audit_ring.h
 * @brief Stage-10 — kernel-side compliance failure audit ring.
 *
 * Bounded, append-only, in-kernel ring of structural failures observed
 * by the security primitives (intent_validator, tee.c, action_bridge).
 *
 * Why this lives in the kernel and not in Python:
 *   The compliance failure stream MUST inherit the same trust boundary
 *   as the primitives whose failures it records. A Python-only audit
 *   log can be tampered by a compromised user-space; a ring in BSS
 *   that is read out through the existing attestation-signed VBus
 *   path inherits the platform measurement (RTMR[0]) of the kernel
 *   image — so a tamper of this ring's read path changes RTMR[0] and
 *   shows up as a different platform identity in attestation.
 *
 * Bounded — 64 entries, ~3 KiB BSS. No allocations on the hot path.
 *
 * Wire-up surface:
 *   - vos3_audit_emit_failure() called from cmd_intent_submit() failure
 *     paths in vbus_ai_cmds.c (Stage 10.1).
 *   - vos3_audit_snapshot() exposed to userspace via a future
 *     AUDIT_FAIL_QUOTE VBus diag command (Stage 10.3).
 *
 * @date 2026-05-08
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#ifndef VOS3_AUDIT_RING_H
#define VOS3_AUDIT_RING_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Failure-category enum
 *
 * Kept tight on purpose — every category is a distinct security boundary
 * crossing. Adding categories is cheap; renaming them is a wire-format
 * break, so name them precisely the first time.
 * ============================================================================ */

typedef enum vos3_audit_category {
    /* Reserved/unused — uninitialised entries surface as this. */
    VOS3_AUDIT_CAT_NONE             = 0,

    /* IntentManifest structural validator rejected the manifest.
     * rc carries the VOS3_INTENT_E_* code. */
    VOS3_AUDIT_CAT_INTENT_REJECT    = 1,

    /* TEE composed-commitment slot bind failed (vos3_tee_slot_activate_bound).
     * rc carries the VOS3_TEE_E* code. */
    VOS3_AUDIT_CAT_TEE_BIND_FAIL    = 2,

    /* Stage 10.2 — Action Bridge hallucination guardrail
     * (min_confidence_score gate) blocks an action. */
    VOS3_AUDIT_CAT_HALLUCINATION_BLOCK = 3,

    /* Stage 10.2.2 — would-block, but VOS_FORCE_PERMIT was active so the
     * action proceeded. Logged so the auditor still sees the would-have-
     * blocked decision; the rc field carries -(observed_score) just like
     * HALLUCINATION_BLOCK. Used by management for "Safe Rollout" mode:
     * gather telemetry on what WOULD be blocked before flipping enforcement
     * on. */
    VOS3_AUDIT_CAT_FORCE_PERMIT_OVERRIDE = 4,
} vos3_audit_category_t;

/* ============================================================================
 * Ring entry layout — 32 bytes packed.
 *
 * digest_prefix is the FIRST 8 BYTES of the SHA-384 digest of the manifest
 * (or model+intent+policy composition for TEE failures). Eight bytes is
 * enough to disambiguate failures across a single boot session without
 * blowing the entry size. The full digest would force a 56-byte entry.
 *
 * If the failure is so early that no digest could be computed (e.g. NULL
 * buffer, magic mismatch on byte 0), digest_prefix is all-zero.
 * ============================================================================ */

typedef struct vos3_audit_event {
    uint64_t tick;            /**< Boot tick at emit time */
    uint64_t digest_prefix;   /**< First 8 bytes of relevant SHA-384, big-endian-load */
    uint16_t category;        /**< vos3_audit_category_t */
    int16_t  rc;              /**< Underlying rejection code, negative */
    uint8_t  slot_id;         /**< AI slot ID involved (0xFF if N/A) */
    uint8_t  pad[3];          /**< Force 8-byte alignment */
    uint32_t seq;             /**< Monotonic emit sequence (never wraps in u32 lifetime) */
} vos3_audit_event_t;

/* ============================================================================
 * Ring sizing
 *
 * 64 entries × 32 bytes = 2 KiB exact. Fits comfortably in BSS without
 * crossing a page boundary, and is enough to cover a typical attack
 * burst (several dozen rejected manifests in quick succession) without
 * the auditor losing the head of the burst before they get a chance
 * to drain it.
 * ============================================================================ */

#define VOS3_AUDIT_RING_SIZE   64U

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Record a structural / security failure event into the audit ring.
 *
 * Safe to call from any kernel context (no allocations, no locks beyond
 * a memory-barrier ordering trick equivalent to s_ring's pattern in
 * tee.c). Entries that arrive after the ring is full overwrite the
 * oldest entry; the seq field lets a snapshot reader detect that
 * overwrite.
 *
 * @param[in] category       VOS3_AUDIT_CAT_*
 * @param[in] slot_id        AI slot id (0xFF if not slot-scoped)
 * @param[in] rc             Underlying negative return code
 * @param[in] digest48       Optional SHA-384 of the offending payload;
 *                           may be NULL — first 8 bytes are recorded
 *                           (or zero if NULL).
 */
void vos3_audit_emit_failure(uint16_t        category,
                             uint8_t         slot_id,
                             int16_t         rc,
                             const uint8_t  *digest48);

/**
 * @brief Snapshot up to ``max`` events, oldest-first.
 *
 * @param[out] out  Caller buffer
 * @param[in]  max  Max entries to copy
 * @return Number of entries copied (0..min(max, ring fill))
 */
uint32_t vos3_audit_snapshot(vos3_audit_event_t *out, uint32_t max);

/**
 * @brief Total events emitted since boot (monotonic, never resets).
 *
 * Useful for the JSON-LD compliance endpoint to detect whether a
 * snapshot lost the head of a burst (count > VOS3_AUDIT_RING_SIZE).
 */
uint32_t vos3_audit_total_emitted(void);

#endif /* VOS3_AUDIT_RING_H */
