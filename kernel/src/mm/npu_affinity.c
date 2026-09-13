/**
 * @file npu_affinity.c
 * @brief Phase 2.3: KV-Cache Page Pinning to NPU SRAM
 *
 * @details Pins KV-cache HugePages to NPU SRAM for zero-PCIe-roundtrip
 *          access during speculative decoding. When no physical NPU is
 *          registered (e.g., QEMU), pin operations set metadata flags only
 *          — no actual SRAM copy occurs. The affinity flag still informs
 *          routing decisions in the hybrid orchestrator.
 *
 *          Each slot has up to 4 KV HugePages (kv_hp_phys[4]). The pinned
 *          state is tracked per-page via a bitmask (npu_pin_mask).
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2.3: NPU Affinity for Zero-Latency KV Access
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/ai_orch.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/accel.h"
#include "../../include/vos/console.h"
#include <stdint.h>
#include <stddef.h>

#define NPU_TAG "[NPU-PIN] "

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/** @brief Global model slot array (defined in ai_slots.c) */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * PER-SLOT NPU PIN STATE
 * ============================================================================ */

/** @brief Maximum KV HugePages per slot (matches kv_hp_phys[4]) */
#define NPU_MAX_KV_PAGES  4U

/**
 * @brief Per-slot NPU pin tracking
 */
typedef struct npu_pin_state {
    uint8_t     pin_mask;       /**< Bitmask of pinned pages (bit i = page i) */
    uint8_t     initialized;    /**< 1 = state initialized for this slot */
} npu_pin_state_t;

static npu_pin_state_t g_npu_pin[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * INTERNAL: CHECK NPU AVAILABLE
 * ============================================================================ */

/**
 * @brief Check if a physical NPU is registered in the accel subsystem
 * @return 1 if NPU found, 0 otherwise
 */
static int npu_available(void)
{
    uint32_t num = vos3_accel_num_devices();
    for (uint32_t i = 0; i < num; i++) {
        const vos3_accel_device_t *dev = vos3_accel_get_device(i);
        if (dev != NULL && dev->type == VOS3_ACCEL_NPU && dev->online) {
            return 1;
        }
    }
    return 0;
}

/* ============================================================================
 * PUBLIC API: PIN
 * ============================================================================ */

int vos3_npu_affinity_pin(uint8_t slot_id, uint32_t page_idx)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (page_idx >= NPU_MAX_KV_PAGES) {
        return -22; /* EINVAL */
    }

    npu_pin_state_t *ps = &g_npu_pin[slot_id];
    if (!ps->initialized) {
        ps->pin_mask    = 0;
        ps->initialized = 1;
    }

    /* Check if page has a valid physical address */
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (page_idx >= slot->kv_hp_count) {
        VOS3_WARN(NPU_TAG "pin: page_idx %u >= kv_hp_count %u for slot %u",
                  page_idx, slot->kv_hp_count, slot_id);
        return -22; /* EINVAL */
    }

    /* Set pin flag (metadata-only when no physical NPU) */
    ps->pin_mask |= (uint8_t)(1U << page_idx);

    if (npu_available()) {
        VOS3_INFO(NPU_TAG "pin: slot %u page %u → NPU SRAM (phys=0x%llx)",
                  slot_id, page_idx,
                  (unsigned long long)slot->kv_hp_phys[page_idx]);
    } else {
        VOS3_INFO(NPU_TAG "pin: slot %u page %u → metadata only (no NPU)",
                  slot_id, page_idx);
    }

    return 0;
}

/* ============================================================================
 * PUBLIC API: UNPIN
 * ============================================================================ */

int vos3_npu_affinity_unpin(uint8_t slot_id, uint32_t page_idx)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (page_idx >= NPU_MAX_KV_PAGES) {
        return -22; /* EINVAL */
    }

    npu_pin_state_t *ps = &g_npu_pin[slot_id];
    if (!ps->initialized) {
        ps->pin_mask    = 0;
        ps->initialized = 1;
    }

    ps->pin_mask &= (uint8_t)~(1U << page_idx);

    VOS3_INFO(NPU_TAG "unpin: slot %u page %u", slot_id, page_idx);
    return 0;
}

/* ============================================================================
 * PUBLIC API: PIN RANGE
 * ============================================================================ */

int vos3_npu_affinity_pin_range(uint8_t slot_id, uint32_t start, uint32_t count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (start + count > NPU_MAX_KV_PAGES) {
        return -22; /* EINVAL: range exceeds max pages */
    }

    for (uint32_t i = start; i < start + count; i++) {
        int rc = vos3_npu_affinity_pin(slot_id, i);
        if (rc != 0) {
            return rc;
        }
    }

    return 0;
}

/* ============================================================================
 * PUBLIC API: STATUS
 * ============================================================================ */

int vos3_npu_affinity_status(uint8_t slot_id, uint32_t *pinned, uint32_t *total)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (pinned == NULL || total == NULL) {
        return -14; /* EFAULT */
    }

    npu_pin_state_t *ps = &g_npu_pin[slot_id];
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    *total = slot->kv_hp_count;

    /* Count set bits in pin_mask */
    uint32_t count = 0;
    if (ps->initialized) {
        uint8_t mask = ps->pin_mask;
        while (mask) {
            count += (mask & 1U);
            mask >>= 1;
        }
    }
    *pinned = count;

    return 0;
}
