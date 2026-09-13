/**
 * @file spec_decoder.c
 * @brief Phase 2.2: Eagle-Wing Speculative Decoding Engine
 *
 * @details Implements speculative decoding where a Draft model (small, fast)
 *          generates K candidate tokens, then the Target model (large, accurate)
 *          verifies them. Accepted tokens bypass full inference, yielding
 *          2-4x throughput improvement.
 *
 *          Architecture:
 *            Slot 0 = Draft  → generates K tokens autoregressively
 *            Slot 1 = Target → verifies K tokens in parallel
 *            ISC mailbox carries spec_batch between slots
 *            KV prefix sharing (COW) avoids duplicate system prompt computation
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.2: Speculative Decoding & NPU Dispatch
 */

#include "../../include/vos/ai_guard.h"
#include "../../include/vos/ai_spec.h"
#include "../../include/vos/console.h"
#include <stdint.h>
#include <stddef.h>

#define SPEC_TAG "[SPEC] "

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/** @brief Global model slot array (defined in ai_slots.c) */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/** @brief ISC send function (defined in ai_isc.c) */
extern int vos3_ai_isc_send(uint8_t from_slot, uint8_t to_slot,
                             uint32_t context_id, const void *msg, uint16_t len);

/** @brief KV prefix sharing (defined in ai_kv_prefix.c) */
extern int vos3_kv_prefix_share(uint8_t src_slot, uint8_t dst_slot, uint32_t count);
extern int vos3_kv_prefix_unshare(uint8_t slot_id);

/* ============================================================================
 * CONFIGURE — Pair Draft + Target Slots
 * ============================================================================ */

int vos3_spec_configure(uint8_t draft_slot, uint8_t target_slot, uint32_t k)
{
    /* Validate slot indices */
    if (draft_slot >= VOS3_MODEL_SLOT_MAX || target_slot >= VOS3_MODEL_SLOT_MAX) {
        VOS3_WARN(SPEC_TAG "configure: invalid slot (%u, %u)", draft_slot, target_slot);
        return -22; /* EINVAL */
    }
    if (draft_slot == target_slot) {
        VOS3_WARN(SPEC_TAG "configure: draft == target (%u)", draft_slot);
        return -22; /* EINVAL */
    }
    if (k == 0U || k > VOS3_SPEC_MAX_K) {
        k = VOS3_SPEC_DEFAULT_K; /* Clamp to default */
    }

    vos3_ai_model_slot_t *draft  = &g_model_slots[draft_slot];
    vos3_ai_model_slot_t *target = &g_model_slots[target_slot];

    /* Verify both slots have loaded models (status >= ACTIVE) */
    if (draft->status < VOS3_SLOT_ACTIVE) {
        VOS3_WARN(SPEC_TAG "configure: draft slot %u not active (status=%u)",
                  draft_slot, draft->status);
        return -1; /* EPERM */
    }
    if (target->status < VOS3_SLOT_ACTIVE) {
        VOS3_WARN(SPEC_TAG "configure: target slot %u not active (status=%u)",
                  target_slot, target->status);
        return -1; /* EPERM */
    }

    /* Check neither slot is already in a speculation session */
    if (draft->spec_active != 0U) {
        VOS3_WARN(SPEC_TAG "configure: draft slot %u already active", draft_slot);
        return -16; /* EBUSY */
    }
    if (target->spec_active != 0U) {
        VOS3_WARN(SPEC_TAG "configure: target slot %u already active", target_slot);
        return -16; /* EBUSY */
    }

    /* Configure roles */
    draft->spec_role         = VOS3_SPEC_ROLE_DRAFT;
    draft->spec_partner_slot = target_slot;
    draft->spec_k            = (uint8_t)k;
    draft->spec_active       = 1;
    draft->spec_batch_id     = 0;
    draft->spec_tokens_drafted  = 0;
    draft->spec_tokens_accepted = 0;
    draft->spec_tokens_rejected = 0;

    target->spec_role         = VOS3_SPEC_ROLE_TARGET;
    target->spec_partner_slot = draft_slot;
    target->spec_k            = (uint8_t)k;
    target->spec_active       = 1;
    target->spec_batch_id     = 0;
    target->spec_tokens_drafted  = 0;
    target->spec_tokens_accepted = 0;
    target->spec_tokens_rejected = 0;

    /* Share KV prefix from target → draft (COW) if target has locked prefix */
    if (target->prefix_immutable != 0U && target->prefix_locked_count > 0U) {
        int share_rc = vos3_kv_prefix_share(target_slot, draft_slot,
                                             target->prefix_locked_count);
        if (share_rc == 0) {
            VOS3_INFO(SPEC_TAG "Shared KV prefix: target slot %u -> draft slot %u "
                      "(%u pages)", target_slot, draft_slot,
                      target->prefix_locked_count);
        } else {
            VOS3_WARN(SPEC_TAG "KV prefix share failed (rc=%d), continuing without",
                      share_rc);
        }
    }

    VOS3_INFO(SPEC_TAG "Configured: draft=slot%u, target=slot%u, K=%u",
              draft_slot, target_slot, k);

    return 0;
}

/* ============================================================================
 * GENERATE — Draft K Speculative Tokens
 * ============================================================================ */

int vos3_spec_generate(uint8_t draft_slot, uint32_t input_token)
{
    if (draft_slot >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vos3_ai_model_slot_t *draft = &g_model_slots[draft_slot];

    if (draft->spec_role != VOS3_SPEC_ROLE_DRAFT) {
        VOS3_WARN(SPEC_TAG "generate: slot %u is not DRAFT role", draft_slot);
        return -1; /* EPERM */
    }
    if (draft->spec_active == 0U) {
        VOS3_WARN(SPEC_TAG "generate: slot %u not active", draft_slot);
        return -1; /* EPERM */
    }

    uint8_t target_slot = draft->spec_partner_slot;
    if (target_slot >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL — corrupted partner */
    }

    uint32_t k = (uint32_t)draft->spec_k;
    if (k == 0U || k > VOS3_SPEC_MAX_K) {
        k = VOS3_SPEC_DEFAULT_K;
    }

    /* Build speculative batch */
    vos3_spec_batch_t batch;
    batch.count    = 0;
    batch.seq_pos  = draft->ctx_window.total_generated;
    batch.batch_id = draft->spec_batch_id;

    uint32_t current_token = input_token;

    for (uint32_t i = 0; i < k; i++) {
        uint32_t out_token = 0;
        int32_t  out_logit = 0;

        /* Single forward pass on draft model */
        int rc = vos3_kim_generate_speculative(draft_slot, current_token,
                                                &out_token, &out_logit);
        if (rc != 0) {
            VOS3_WARN(SPEC_TAG "generate: forward pass failed at step %u (rc=%d)",
                      i, rc);
            break;
        }

        batch.draft_tokens[i] = out_token;
        batch.draft_logits[i] = out_logit;
        batch.count++;

        /* Feed predicted token as next input (autoregressive) */
        current_token = out_token;
    }

    if (batch.count == 0U) {
        VOS3_WARN(SPEC_TAG "generate: no tokens drafted");
        return -5; /* EIO */
    }

    /* Increment batch counter */
    draft->spec_batch_id++;
    draft->spec_tokens_drafted += batch.count;

    /* Send batch to target via ISC mailbox */
    int isc_rc = vos3_ai_isc_send(draft_slot, target_slot,
                                    0x5350U, /* "SP" context ID for spec */
                                    &batch, (uint16_t)sizeof(batch));
    if (isc_rc != 0) {
        VOS3_WARN(SPEC_TAG "generate: ISC send failed (rc=%d), batch_id=%u",
                  isc_rc, batch.batch_id);
        /* Batch was drafted but not delivered — stats still count */
    }

    VOS3_INFO(SPEC_TAG "Drafted %u tokens on slot %u, batch_id=%u, "
              "sent to target slot %u",
              batch.count, draft_slot, batch.batch_id, target_slot);

    return 0;
}

/* ============================================================================
 * VERIFY — Target Validates Speculative Batch
 * ============================================================================ */

int vos3_spec_verify(uint8_t target_slot, const vos3_spec_batch_t *batch,
                     vos3_spec_result_t *result)
{
    if (target_slot >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (batch == NULL || result == NULL) {
        return -14; /* EFAULT */
    }

    vos3_ai_model_slot_t *target = &g_model_slots[target_slot];

    if (target->spec_role != VOS3_SPEC_ROLE_TARGET) {
        VOS3_WARN(SPEC_TAG "verify: slot %u is not TARGET role", target_slot);
        return -1; /* EPERM */
    }
    if (target->spec_active == 0U) {
        return -1; /* EPERM */
    }

    uint32_t count = batch->count;
    if (count == 0U || count > VOS3_SPEC_MAX_K) {
        return -22; /* EINVAL */
    }

    /* Run target model verification on all K draft tokens */
    uint32_t target_tokens[VOS3_SPEC_MAX_K + 1];
    uint32_t verified_count = 0;

    int rc = vos3_kim_verify_batch(target_slot, batch->draft_tokens, count,
                                    target_tokens, &verified_count);
    if (rc != 0) {
        VOS3_WARN(SPEC_TAG "verify: batch verification failed (rc=%d)", rc);
        return rc;
    }

    /* Compare draft tokens with target tokens */
    result->accepted = 0;
    result->batch_id = batch->batch_id;

    for (uint32_t i = 0; i < count; i++) {
        if (target_tokens[i] == batch->draft_tokens[i]) {
            /* Accept — draft matches target */
            result->verified_tokens[i] = batch->draft_tokens[i];
            result->accepted++;
        } else {
            /* Reject — use target's correction */
            result->verified_tokens[i] = target_tokens[i];
            break; /* Stop at first mismatch */
        }
    }

    /* If all K accepted, add bonus token from target's next prediction */
    if (result->accepted == count && verified_count > count) {
        result->verified_tokens[count] = target_tokens[count];
        result->total_tokens = result->accepted + 1U;
    } else {
        result->total_tokens = result->accepted + 1U;
    }

    /* Update target slot statistics */
    target->spec_tokens_accepted += result->accepted;
    target->spec_tokens_rejected += (count - result->accepted);
    target->spec_batch_id = batch->batch_id;

    /* Mirror stats to draft partner */
    uint8_t draft_slot = target->spec_partner_slot;
    if (draft_slot < VOS3_MODEL_SLOT_MAX) {
        g_model_slots[draft_slot].spec_tokens_accepted += result->accepted;
        g_model_slots[draft_slot].spec_tokens_rejected += (count - result->accepted);
    }

    VOS3_INFO(SPEC_TAG "Verified batch_id=%u: accepted=%u/%u, total_tokens=%u",
              batch->batch_id, result->accepted, count, result->total_tokens);

    return 0;
}

/* ============================================================================
 * RESET — Clear Speculation State
 * ============================================================================ */

int vos3_spec_reset(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Clear partner's reference if they point to us */
    if (slot->spec_partner_slot < VOS3_MODEL_SLOT_MAX) {
        vos3_ai_model_slot_t *partner = &g_model_slots[slot->spec_partner_slot];
        if (partner->spec_partner_slot == slot_id) {
            partner->spec_role         = VOS3_SPEC_ROLE_NONE;
            partner->spec_partner_slot = 0xFF;
            partner->spec_active       = 0;
        }
    }

    /* Unshare KV prefix if we were a draft receiving shared prefix */
    if (slot->spec_role == VOS3_SPEC_ROLE_DRAFT && slot->kv_prefix_shared != 0U) {
        vos3_kv_prefix_unshare(slot_id);
    }

    /* Clear our speculation state */
    slot->spec_role            = VOS3_SPEC_ROLE_NONE;
    slot->spec_partner_slot    = 0xFF;
    slot->spec_active          = 0;
    slot->spec_k               = 0;
    slot->spec_tokens_drafted  = 0;
    slot->spec_tokens_accepted = 0;
    slot->spec_tokens_rejected = 0;
    slot->spec_batch_id        = 0;

    VOS3_INFO(SPEC_TAG "Reset speculation state for slot %u", slot_id);

    return 0;
}

/* ============================================================================
 * GET STATS — Read Speculation Statistics
 * ============================================================================ */

void vos3_spec_get_stats(uint8_t slot_id, uint32_t *drafted,
                         uint32_t *accepted, uint32_t *rejected)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        if (drafted)  *drafted  = 0;
        if (accepted) *accepted = 0;
        if (rejected) *rejected = 0;
        return;
    }

    const vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    if (drafted)  *drafted  = slot->spec_tokens_drafted;
    if (accepted) *accepted = slot->spec_tokens_accepted;
    if (rejected) *rejected = slot->spec_tokens_rejected;
}
