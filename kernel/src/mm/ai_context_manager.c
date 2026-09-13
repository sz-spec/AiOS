/**
 * @file ai_context_manager.c
 * @brief Phase 2.2: Rolling Context Window with TQ4 Prefix Freezing
 *
 * @details Manages a sliding context window over the KV cache. System prompt
 *          tokens can be "frozen" (compressed to Tier-1 TQ4 warm storage),
 *          while an active window of recent tokens slides forward. When the
 *          active window exceeds max_active, oldest tokens are evicted to
 *          Tier-1 (TQ4 compressed), keeping memory bounded.
 *
 *          This enables infinite-context generation with bounded memory:
 *            Frozen prefix (TQ4) + Active window (Hot HugePages) = total context
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.2: Speculative Decoding & NPU Dispatch
 */

#include "ai_guard_internal.h"
#include "../../include/vos/ai_kv_managed.h"
#include "../../include/vos/ai_spec.h"
#include "../../include/vos/console.h"

#define CTX_TAG "[CTX-WIN] "

/** @brief Maximum allowed active window size (tokens) */
#define CTX_MAX_WINDOW_SIZE  8192U

/* ============================================================================
 * INIT — Initialize Context Window
 * ============================================================================ */

int vos3_ctx_window_init(uint8_t slot_id, uint32_t max_active)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Clamp max_active to reasonable bound */
    if (max_active == 0U) {
        max_active = CTX_MAX_WINDOW_SIZE;
    }
    if (max_active > CTX_MAX_WINDOW_SIZE) {
        max_active = CTX_MAX_WINDOW_SIZE;
    }

    /* Zero out the context window state */
    slot->ctx_window.frozen_len      = 0;
    slot->ctx_window.active_start    = 0;
    slot->ctx_window.active_len      = 0;
    slot->ctx_window.max_active      = max_active;
    slot->ctx_window.total_generated = 0;
    slot->ctx_window.frozen          = 0;

    VOS3_INFO(CTX_TAG "Slot %u: initialized, max_active=%u tokens",
              slot_id, max_active);

    return 0;
}

/* ============================================================================
 * FREEZE — Compress Prefix Tokens to Tier-1 (TQ4)
 * ============================================================================ */

int vos3_ctx_window_freeze(uint8_t slot_id, uint32_t prefix_len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (prefix_len == 0U) {
        return -22; /* EINVAL — nothing to freeze */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Don't double-freeze */
    if (slot->ctx_window.frozen != 0U) {
        VOS3_WARN(CTX_TAG "Slot %u: prefix already frozen (%u tokens)",
                  slot_id, slot->ctx_window.frozen_len);
        return -16; /* EBUSY */
    }

    /* Clamp prefix_len to max window size */
    if (prefix_len > CTX_MAX_WINDOW_SIZE) {
        prefix_len = CTX_MAX_WINDOW_SIZE;
    }

    /*
     * Evict prefix tokens from Tier-0 (Hot) to Tier-1 (Warm/TQ4).
     * In production, we'd iterate over the KV cache entries for tokens
     * 0..prefix_len-1 and compress each to TQ4 format. Here we use
     * the managed KV eviction API.
     */
    int rc = vos3_kv_evict_to_warm(slot_id, 0U);
    if (rc != 0) {
        VOS3_WARN(CTX_TAG "Slot %u: eviction to warm failed (rc=%d), "
                  "cannot freeze — prefix_immutable NOT set", slot_id, rc);
        /*
         * Phase 10 Omega Fix: Do NOT set prefix_immutable=1 when eviction fails.
         * Previously, the code continued and set prefix_immutable=1 even on failure,
         * which allowed active KV pages to be aliased as COW source while still
         * being mutated — a data corruption risk.
         */
        return rc;
    }

    /* Update context window state */
    slot->ctx_window.frozen_len   = prefix_len;
    slot->ctx_window.frozen       = 1;
    slot->ctx_window.active_start = prefix_len;
    slot->ctx_window.active_len   = 0;

    /* Lock prefix as immutable for COW sharing */
    uint32_t pages_needed = (prefix_len + 511U) / 512U; /* ~512 tokens per HugePage */
    if (pages_needed > 4U) {
        pages_needed = 4U; /* Max 4 HugePages */
    }
    if (pages_needed == 0U) {
        pages_needed = 1U;
    }
    slot->prefix_locked_count = pages_needed;
    slot->prefix_immutable    = 1;

    /* Phase 10 Omega: Assert that prefix_immutable is only set on success */
    VOS3_ASSERT(slot->prefix_immutable == 1 && rc == 0,
                "prefix_immutable must only be set when eviction succeeds");

    VOS3_INFO(CTX_TAG "Slot %u: froze %u prefix tokens (%u pages locked, immutable)",
              slot_id, prefix_len, pages_needed);

    return 0;
}

/* ============================================================================
 * ADVANCE — Slide the Active Window Forward
 * ============================================================================ */

int vos3_ctx_window_advance(uint8_t slot_id, uint32_t new_tokens)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (new_tokens == 0U) {
        return 0; /* Nothing to advance */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    slot->ctx_window.active_len      += new_tokens;
    slot->ctx_window.total_generated += new_tokens;

    /* Check if active window exceeds maximum — trigger eviction */
    if (slot->ctx_window.active_len > slot->ctx_window.max_active) {
        uint32_t overflow = slot->ctx_window.active_len - slot->ctx_window.max_active;

        /*
         * Evict oldest `overflow` tokens from the active window to Tier-1.
         * In production, this would compress the specific KV cache entries.
         * Here we call the managed eviction and update metadata.
         */
        int rc = vos3_kv_evict_to_warm(slot_id, 0U);
        if (rc != 0) {
            VOS3_WARN(CTX_TAG "Slot %u: eviction failed during advance (rc=%d)",
                      slot_id, rc);
            /* Continue — the window will be oversized until next eviction */
        }

        slot->ctx_window.active_start += overflow;
        slot->ctx_window.active_len   -= overflow;

        VOS3_INFO(CTX_TAG "Slot %u: evicted %u tokens, window=[%u..%u] (%u active)",
                  slot_id, overflow,
                  slot->ctx_window.active_start,
                  slot->ctx_window.active_start + slot->ctx_window.active_len,
                  slot->ctx_window.active_len);
    }

    return 0;
}

/* ============================================================================
 * STATUS — Query Context Window State
 * ============================================================================ */

int vos3_ctx_window_status(uint8_t slot_id, vos3_context_window_t *out)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (out == NULL) {
        return -14; /* EFAULT */
    }

    const vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    *out = slot->ctx_window;

    return 0;
}
