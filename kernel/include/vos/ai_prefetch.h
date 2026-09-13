/**
 * @file ai_prefetch.h
 * @brief VOS3 Inference-Predictive HugePage Mapping ("Claw Protocol")
 *
 * @details Preemptive HugePage reservation based on VBus slot activity
 *          patterns. Predicts next model slot to load based on frequency
 *          analysis and pre-reserves HugePages to eliminate allocation
 *          latency during weight swaps.
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_AI_PREFETCH_H
#define VOS3_AI_PREFETCH_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief Number of AI model slots tracked */
#define VOS3_PREFETCH_MAX_SLOTS     8U

/** @brief Number of HugePages to pre-reserve for predicted slot */
#define VOS3_PREFETCH_RESERVE_COUNT 16U

/* ============================================================================
 * PREFETCH STATE
 * ============================================================================ */

/**
 * @brief Per-slot load tracking + prediction state
 */
typedef struct vos3_prefetch_state {
    uint8_t  last_loaded_slot;                          /**< Most recently loaded slot */
    uint8_t  predicted_next;                            /**< Predicted next slot to load */
    uint8_t  prefetch_active;                           /**< 1 = prefetch reservation held */
    uint8_t  _pad;
    uint32_t load_count[VOS3_PREFETCH_MAX_SLOTS];       /**< Per-slot load frequency */
    uint64_t last_load_tick[VOS3_PREFETCH_MAX_SLOTS];   /**< When each slot was last loaded */
    uint32_t prefetch_pages;                            /**< Pre-allocated HugePage count */
    uint32_t prefetch_hits;                             /**< Times prediction was correct */
    uint32_t prefetch_misses;                           /**< Times prediction was wrong */
    uint32_t total_predictions;                         /**< Total predictions made */
} vos3_prefetch_state_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize prefetch prediction engine
 *
 * Zeroes all state. Called from kmain.c after AI guard init.
 */
void vos3_prefetch_init(void);

/**
 * @brief Notify prefetch engine of a slot load event
 *
 * Called when cmd_slot_start() fires. Records load event, updates
 * frequency table, and predicts next slot. If prediction changes,
 * releases old reservation and pre-reserves HugePages for the
 * predicted slot.
 *
 * @param[in] slot_id Slot that was just loaded (0-7)
 */
void vos3_prefetch_on_slot_load(uint8_t slot_id);

/**
 * @brief Notify prefetch engine of a slot reset
 *
 * Called when a slot is freed. If the reset slot was the predicted
 * target, releases the prefetch reservation and re-predicts.
 *
 * @param[in] slot_id Slot that was reset (0-7)
 */
void vos3_prefetch_on_slot_reset(uint8_t slot_id);

/**
 * @brief Attempt to consume pre-reserved HugePages for a slot load
 *
 * Called at the start of HugePage allocation for a new model load.
 * If slot_id matches the prediction, returns 1 (reservation can be
 * used). Otherwise returns 0 (normal allocation path).
 *
 * @param[in] slot_id Slot requesting HugePages
 * @return 1 if prefetch reservation matches and can be consumed, 0 otherwise
 */
int vos3_prefetch_consume(uint8_t slot_id);

/**
 * @brief Get current prefetch statistics
 * @param[out] hits Number of correct predictions
 * @param[out] misses Number of incorrect predictions
 * @param[out] total Total predictions made
 */
void vos3_prefetch_get_stats(uint32_t *hits, uint32_t *misses, uint32_t *total);

#endif /* VOS3_AI_PREFETCH_H */
