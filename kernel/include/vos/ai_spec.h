/**
 * @file ai_spec.h
 * @brief VOS3 Speculative Decoding & Context Window Types
 *
 * @details Phase 2.2: Eagle-Wing speculative decoding engine.
 *          Draft model generates K candidate tokens, Target model verifies
 *          in parallel. Accepted tokens skip full inference for 2-4x throughput.
 *
 *          Also defines the rolling context window for TQ4-backed prefix
 *          freezing with sliding active window.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.2: Speculative Decoding & NPU Dispatch
 */

#ifndef VOS3_AI_SPEC_H
#define VOS3_AI_SPEC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * SPECULATION ROLE CONSTANTS
 * ============================================================================ */

#define VOS3_SPEC_ROLE_NONE    0U  /**< Not participating in speculation */
#define VOS3_SPEC_ROLE_DRAFT   1U  /**< Draft (small/fast) model */
#define VOS3_SPEC_ROLE_TARGET  2U  /**< Target (large/accurate) model */

/* ============================================================================
 * SPECULATION DEPTH
 * ============================================================================ */

#define VOS3_SPEC_DEFAULT_K    4U  /**< Default speculation depth */
#define VOS3_SPEC_MAX_K        8U  /**< Maximum speculation depth */

/* ============================================================================
 * GHOST TOKEN FLAGS (Phase 2.3: Speculative VBus)
 * ============================================================================ */

#define VOS3_SPEC_GHOST_FLAG    0x10U  /**< Token is unverified (ghost) */
#define VOS3_SPEC_VERIFY_FLAG   0x20U  /**< Token is verified replacement */
#define VOS3_SPEC_FINAL_FLAG    0x08U  /**< Last token in speculative batch */

/* ============================================================================
 * SPECULATIVE BATCH (Draft → Target via ISC)
 * ============================================================================ */

/**
 * @brief Speculative batch: K drafted tokens sent from Draft to Target.
 *
 * @details Fits within ISC mailbox (4KB). At VOS3_SPEC_MAX_K=8:
 *          4 + 32 + 32 + 4 + 4 = 76 bytes — well within 4KB limit.
 */
typedef struct vos3_spec_batch {
    uint32_t    count;                              /**< Tokens drafted (1..K) */
    uint32_t    draft_tokens[VOS3_SPEC_MAX_K];      /**< Drafted token IDs */
    int32_t     draft_logits[VOS3_SPEC_MAX_K];      /**< Draft logit x10000 (fixed-point) */
    uint32_t    seq_pos;                            /**< Starting sequence position */
    uint32_t    batch_id;                           /**< Monotonic batch counter */
} vos3_spec_batch_t;

/* ============================================================================
 * SPECULATIVE RESULT (Target → Caller)
 * ============================================================================ */

/**
 * @brief Verification result: how many drafts accepted + corrected tokens.
 *
 * @details The target verifies K drafted tokens. If all K match, a bonus
 *          K+1th token is appended (free extra token). If mismatch at
 *          position i, accepted=i and verified_tokens[i] is the correction.
 */
typedef struct vos3_spec_result {
    uint32_t    accepted;                           /**< How many draft tokens accepted */
    uint32_t    verified_tokens[VOS3_SPEC_MAX_K + 1]; /**< Accepted + 1 correction token */
    uint32_t    total_tokens;                       /**< = accepted + 1 */
    uint32_t    batch_id;                           /**< Matches draft batch */
} vos3_spec_result_t;

/* ============================================================================
 * CONTEXT WINDOW STATE
 * ============================================================================ */

/**
 * @brief Rolling context window for TQ4-backed prefix management.
 *
 * @details Allows freezing system prompt tokens (compressed to Tier-1 TQ4)
 *          and maintaining a sliding active window of recent tokens.
 *          When active_len exceeds max_active, oldest tokens are evicted
 *          to Tier-1 warm storage.
 */
typedef struct vos3_context_window {
    uint32_t    frozen_len;       /**< Tokens in frozen prefix (TQ4 compressed) */
    uint32_t    active_start;     /**< Start position of active sliding window */
    uint32_t    active_len;       /**< Current active window length */
    uint32_t    max_active;       /**< Maximum active window size */
    uint32_t    total_generated;  /**< Total tokens generated in session */
    uint8_t     frozen;           /**< 1 if prefix has been frozen */
} vos3_context_window_t;

/* ============================================================================
 * SPECULATIVE ENGINE API
 * ============================================================================ */

/**
 * @brief Configure a Draft/Target speculation pair.
 * @param draft_slot  Slot ID for the draft (small) model
 * @param target_slot Slot ID for the target (large) model
 * @param k           Speculation depth (1..VOS3_SPEC_MAX_K)
 * @return 0 on success, negative errno on failure
 */
int vos3_spec_configure(uint8_t draft_slot, uint8_t target_slot, uint32_t k);

/**
 * @brief Generate K speculative tokens from the draft model.
 * @param draft_slot  Draft slot ID
 * @param input_token Input token to start speculation from
 * @return 0 on success, negative errno on failure
 */
int vos3_spec_generate(uint8_t draft_slot, uint32_t input_token);

/**
 * @brief Verify a speculative batch against the target model.
 * @param target_slot Target slot ID
 * @param batch       Speculative batch from draft model
 * @param result      Output verification result
 * @return 0 on success, negative errno on failure
 */
int vos3_spec_verify(uint8_t target_slot, const vos3_spec_batch_t *batch,
                     vos3_spec_result_t *result);

/**
 * @brief Reset speculation state for a slot.
 * @param slot_id Slot to reset
 * @return 0 on success, negative errno on failure
 */
int vos3_spec_reset(uint8_t slot_id);

/**
 * @brief Get speculation statistics for a slot.
 * @param slot_id  Slot to query
 * @param drafted  Output: total tokens drafted
 * @param accepted Output: total tokens accepted
 * @param rejected Output: total tokens rejected
 */
void vos3_spec_get_stats(uint8_t slot_id, uint32_t *drafted,
                         uint32_t *accepted, uint32_t *rejected);

/* ============================================================================
 * CONTEXT WINDOW API
 * ============================================================================ */

/**
 * @brief Initialize the rolling context window for a slot.
 * @param slot_id    Slot to initialize
 * @param max_active Maximum active window size (tokens)
 * @return 0 on success, negative errno on failure
 */
int vos3_ctx_window_init(uint8_t slot_id, uint32_t max_active);

/**
 * @brief Freeze prefix tokens (compress to TQ4 Tier-1).
 * @param slot_id    Slot to freeze
 * @param prefix_len Number of tokens to freeze
 * @return 0 on success, negative errno on failure
 */
int vos3_ctx_window_freeze(uint8_t slot_id, uint32_t prefix_len);

/**
 * @brief Advance the context window by new_tokens positions.
 * @param slot_id    Slot to advance
 * @param new_tokens Number of new tokens generated
 * @return 0 on success, negative errno on failure
 */
int vos3_ctx_window_advance(uint8_t slot_id, uint32_t new_tokens);

/**
 * @brief Get the current context window status.
 * @param slot_id Slot to query
 * @param out     Output context window state
 * @return 0 on success, negative errno on failure
 */
int vos3_ctx_window_status(uint8_t slot_id, vos3_context_window_t *out);

/* ============================================================================
 * KIM SPECULATIVE EXTENSIONS
 * ============================================================================ */

/**
 * @brief Single speculative forward pass: produce one token + logit.
 * @param slot_id    Slot to run inference on
 * @param input_token Input token ID
 * @param out_token  Output: predicted token ID (argmax)
 * @param out_logit  Output: max logit value (integer, x10000 fixed-point)
 * @return 0 on success, negative errno on failure
 */
int vos3_kim_generate_speculative(uint8_t slot_id, uint32_t input_token,
                                   uint32_t *out_token, int32_t *out_logit);

/**
 * @brief Batch verification: run target model forward on count tokens.
 * @param slot_id      Slot to run inference on
 * @param tokens       Array of token IDs to verify
 * @param count        Number of tokens
 * @param results      Output: argmax token at each position
 * @param result_count Output: number of results written
 * @return 0 on success, negative errno on failure
 */
int vos3_kim_verify_batch(uint8_t slot_id, const uint32_t *tokens,
                           uint32_t count, uint32_t *results,
                           uint32_t *result_count);

/* ============================================================================
 * DMA WARP SPECULATIVE EXTENSION
 * ============================================================================ */

/**
 * @brief DMA transfer speculative batch between slots.
 * @param draft_slot  Source (draft) slot
 * @param target_slot Destination (target) slot
 * @param batch_size  Number of bytes to transfer
 * @return 0 on success, negative errno on failure
 */
int vos3_dma_warp_spec_dispatch(uint8_t draft_slot, uint8_t target_slot,
                                 uint32_t batch_size);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_AI_SPEC_H */
