/**
 * @file ai_prefetch.c
 * @brief VOS3 Inference-Predictive HugePage Mapping ("Claw Protocol")
 *
 * @details Tracks per-slot load frequency and timing to predict which
 *          model slot will be loaded next. Pre-allocates HugePages for the
 *          predicted slot so that the actual load path encounters zero
 *          allocation latency (prefetch hit) instead of contending with
 *          the HugePage pool (cold path).
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/ai_prefetch.h"
#include "../../include/vos/console.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/atomic.h"

/* ============================================================================
 * INTERNAL STATE
 * ============================================================================ */

static vos3_prefetch_state_t g_prefetch = {0};
static vos3_spinlock_t       g_prefetch_lock = VOS3_SPINLOCK_INIT;

/** @brief Pre-allocated HugePage physical addresses */
static uint64_t g_prefetch_hp[VOS3_PREFETCH_RESERVE_COUNT] = {0};
static uint32_t g_prefetch_hp_count = 0;

/* ============================================================================
 * PREDICTION ENGINE
 * ============================================================================ */

/**
 * @brief Predict the next slot to load based on frequency analysis
 *
 * Strategy: choose the slot with the highest load_count among
 * non-active (non-currently-loaded) slots. Ties broken by most
 * recent load time (temporal locality).
 *
 * @param[in] exclude_slot Slot to exclude from prediction (just loaded)
 * @return Predicted slot ID, or 0xFF if no prediction possible
 */
static uint8_t predict_next_slot(uint8_t exclude_slot)
{
    uint32_t best_count = 0;
    uint64_t best_tick  = 0;
    uint8_t  best_slot  = 0xFF;

    for (uint8_t s = 0; s < VOS3_PREFETCH_MAX_SLOTS; s++) {
        if (s == exclude_slot) {
            continue;
        }
        if (g_prefetch.load_count[s] == 0) {
            continue;  /* Never loaded — can't predict */
        }

        /* Prefer highest frequency; break ties with recency */
        if (g_prefetch.load_count[s] > best_count ||
            (g_prefetch.load_count[s] == best_count &&
             g_prefetch.last_load_tick[s] > best_tick)) {
            best_count = g_prefetch.load_count[s];
            best_tick  = g_prefetch.last_load_tick[s];
            best_slot  = s;
        }
    }

    return best_slot;
}

/**
 * @brief Release all pre-allocated HugePages back to the pool
 */
static void release_reservation(void)
{
    for (uint32_t i = 0; i < g_prefetch_hp_count; i++) {
        if (g_prefetch_hp[i] != 0) {
            vos3_pmm_free_huge(g_prefetch_hp[i]);
            g_prefetch_hp[i] = 0;
        }
    }

    if (g_prefetch_hp_count > 0) {
        VOS3_DEBUG("[PREFETCH] Released %u pre-allocated HugePages",
                   g_prefetch_hp_count);
    }

    g_prefetch_hp_count = 0;
    g_prefetch.prefetch_pages = 0;
    g_prefetch.prefetch_active = 0;
}

/**
 * @brief Pre-allocate HugePages for predicted slot
 */
static void make_reservation(uint8_t predicted_slot)
{
    (void)predicted_slot;

    uint32_t allocated = 0;

    for (uint32_t i = 0; i < VOS3_PREFETCH_RESERVE_COUNT; i++) {
        uint64_t hp = vos3_pmm_alloc_huge();
        if (hp == 0) {
            break;  /* Pool exhausted */
        }
        g_prefetch_hp[i] = hp;
        allocated++;

        /* Issue prefetcht2 to warm L3 cache line for this page */
        __asm__ volatile ("prefetcht2 (%0)" :: "r"((uintptr_t)hp) : "memory");
    }

    g_prefetch_hp_count = allocated;
    g_prefetch.prefetch_pages = allocated;

    if (allocated > 0) {
        g_prefetch.prefetch_active = 1;
        VOS3_DEBUG("[PREFETCH] Pre-allocated %u HugePages for predicted slot %u",
                   allocated, predicted_slot);
    } else {
        g_prefetch.prefetch_active = 0;
        VOS3_DEBUG("[PREFETCH] Cannot allocate HugePages — pool exhausted");
    }
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

void vos3_prefetch_init(void)
{
    vos3_spinlock_acquire(&g_prefetch_lock);

    for (uint8_t i = 0; i < VOS3_PREFETCH_MAX_SLOTS; i++) {
        g_prefetch.load_count[i]     = 0;
        g_prefetch.last_load_tick[i] = 0;
    }
    g_prefetch.last_loaded_slot  = 0xFF;
    g_prefetch.predicted_next    = 0xFF;
    g_prefetch.prefetch_active   = 0;
    g_prefetch.prefetch_pages    = 0;
    g_prefetch.prefetch_hits     = 0;
    g_prefetch.prefetch_misses   = 0;
    g_prefetch.total_predictions = 0;
    g_prefetch_hp_count = 0;

    for (uint32_t i = 0; i < VOS3_PREFETCH_RESERVE_COUNT; i++) {
        g_prefetch_hp[i] = 0;
    }

    vos3_spinlock_release(&g_prefetch_lock);

    VOS3_INFO("[PREFETCH] Claw Protocol initialized (reserve=%u HP/prediction)",
              VOS3_PREFETCH_RESERVE_COUNT);
}

void vos3_prefetch_on_slot_load(uint8_t slot_id)
{
    if (slot_id >= VOS3_PREFETCH_MAX_SLOTS) {
        return;
    }

    vos3_spinlock_acquire(&g_prefetch_lock);

    /* Record this load event */
    g_prefetch.load_count[slot_id]++;
    g_prefetch.last_load_tick[slot_id] = vos3_timer_get_ticks();
    g_prefetch.last_loaded_slot = slot_id;

    /* Predict next slot */
    uint8_t predicted = predict_next_slot(slot_id);

    if (predicted != g_prefetch.predicted_next && predicted != 0xFF) {
        /* Prediction changed — release old, reserve new */
        release_reservation();
        g_prefetch.predicted_next = predicted;
        g_prefetch.total_predictions++;
        make_reservation(predicted);

        VOS3_INFO("[PREFETCH] Slot %u loaded -> predict slot %u next "
                  "(freq=%u, reserved=%u HP)",
                  slot_id, predicted,
                  g_prefetch.load_count[predicted],
                  g_prefetch.prefetch_pages);
    } else if (predicted == 0xFF) {
        /* No prediction possible (only one slot ever used) */
        release_reservation();
        g_prefetch.predicted_next = 0xFF;
    }

    vos3_spinlock_release(&g_prefetch_lock);
}

void vos3_prefetch_on_slot_reset(uint8_t slot_id)
{
    if (slot_id >= VOS3_PREFETCH_MAX_SLOTS) {
        return;
    }

    vos3_spinlock_acquire(&g_prefetch_lock);

    /* If the reset slot was our prediction target, re-predict */
    if (slot_id == g_prefetch.predicted_next) {
        release_reservation();

        uint8_t new_pred = predict_next_slot(g_prefetch.last_loaded_slot);
        g_prefetch.predicted_next = new_pred;

        if (new_pred != 0xFF) {
            g_prefetch.total_predictions++;
            make_reservation(new_pred);
            VOS3_DEBUG("[PREFETCH] Slot %u reset — re-predict slot %u",
                       slot_id, new_pred);
        }
    }

    vos3_spinlock_release(&g_prefetch_lock);
}

int vos3_prefetch_consume(uint8_t slot_id)
{
    if (slot_id >= VOS3_PREFETCH_MAX_SLOTS) {
        return 0;
    }

    vos3_spinlock_acquire(&g_prefetch_lock);

    if (g_prefetch.prefetch_active && slot_id == g_prefetch.predicted_next) {
        /* Hit! The prediction was correct — caller takes ownership of pages */
        g_prefetch.prefetch_hits++;
        g_prefetch.prefetch_active = 0;
        /* Note: We do NOT free pages here — caller consumes them.
         * Clear tracking so release_reservation() won't double-free. */
        g_prefetch_hp_count = 0;
        g_prefetch.prefetch_pages = 0;

        VOS3_INFO("[PREFETCH] HIT: slot %u matched prediction "
                  "(hits=%u misses=%u total=%u)",
                  slot_id, g_prefetch.prefetch_hits,
                  g_prefetch.prefetch_misses,
                  g_prefetch.total_predictions);

        vos3_spinlock_release(&g_prefetch_lock);
        return 1;
    }

    if (g_prefetch.prefetch_active && slot_id != g_prefetch.predicted_next) {
        /* Miss — prediction was wrong */
        g_prefetch.prefetch_misses++;
        VOS3_DEBUG("[PREFETCH] MISS: slot %u loaded but predicted %u",
                   slot_id, g_prefetch.predicted_next);
    }

    vos3_spinlock_release(&g_prefetch_lock);
    return 0;
}

void vos3_prefetch_get_stats(uint32_t *hits, uint32_t *misses, uint32_t *total)
{
    if (hits)   *hits   = g_prefetch.prefetch_hits;
    if (misses) *misses = g_prefetch.prefetch_misses;
    if (total)  *total  = g_prefetch.total_predictions;
}
