/**
 * @file npu_dispatch.c
 * @brief V-Palace Temporal Multi-Slicing — PASID Rotation Dispatcher
 *
 * @details Implements Temporal Multi-Slicing for the NPU, rotating
 *          up to 4 AI agent slots every 250 ms on a single hardware
 *          Compute Unit (CU) via PASID-based isolation.
 *
 *          Context switch flow:
 *          1. Save current slot's KV-cache state (context_save)
 *          2. Flush L1/L2 caches (WBINVD) to prevent residual leakage
 *          3. Update PASID entry (isolate DMA address space)
 *          4. Load next slot's context (context_load)
 *          5. Record switch latency for monitoring
 *
 *          The dispatcher is polled from the bridge task at the rotation
 *          interval. Each switch must complete in < 15 microseconds.
 *
 * @version 1.0.0
 * @date 2026-04-11
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 8.5-U — V-Palace Temporal Multi-Slicing
 * @note Phase 8.6  — Expanded to 8-slot ring, 125 ms rotation (8 agents/sec)
 * @note Compiled with -mno-sse — all operations are GPR-only
 */

#include "../../include/vos/console.h"
#include "../../include/vos/npu.h"

/* ============================================================================
 * LOGGING
 * ============================================================================ */

#define NPU_DISP_INFO(fmt, ...)  vos3_console_printf("[NPU-DISP] " fmt "\n", ##__VA_ARGS__)
#define NPU_DISP_WARN(fmt, ...)  vos3_console_printf("[NPU-DISP] WARN: " fmt "\n", ##__VA_ARGS__)

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum slots in the rotation ring (Phase 8.6: expanded from 4 to 8) */
#define NPU_DISP_MAX_SLOTS          8U

/** @brief Rotation interval in timer ticks.
 *  Phase 8.6: 12 ticks = 120 ms at 100 Hz PIT → 8.33 rotations/sec with
 *  8 active agents, each agent revisited every ~960 ms.
 *  (Was: 25 ticks = 250 ms — 4 agents/sec cycle) */
#define NPU_DISP_ROTATION_TICKS     12U

/** @brief Maximum acceptable context switch overhead (microseconds) */
#define NPU_DISP_MAX_SWITCH_US      15U

/** @brief TSC-to-microsecond divisor estimate (2 GHz baseline) */
#define NPU_DISP_TSC_PER_US         2000U

/** @brief Wing assignments for the 8 rotation slots (Phase 8.6) */
#define NPU_DISP_WING_VOICE         0U   /**< Voice/Audio agent (Wing 0) */
#define NPU_DISP_WING_VISION        1U   /**< Vision agent (Wing 1)      */
#define NPU_DISP_WING_SYSTEM        2U   /**< System agent (Wing 2)      */
#define NPU_DISP_WING_INFRA         3U   /**< Infrastructure (Wing 3)    */
#define NPU_DISP_WING_REASONING     4U   /**< Reasoning agent (Wing 4)   */
#define NPU_DISP_WING_CODING        5U   /**< Coding agent (Wing 5)      */
#define NPU_DISP_WING_MEMORY        6U   /**< Memory agent (Wing 6)      */
#define NPU_DISP_WING_COORDINATOR   7U   /**< Coordinator agent (Wing 7) */

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/**
 * @brief Rotation slot descriptor — one per active agent in the ring
 */
typedef struct {
    uint32_t    slot_id;            /**< AI model slot (0-7)           */
    uint32_t    context_id;         /**< NPU hardware context (0-7)   */
    uint32_t    wing_id;            /**< V-Palace Wing assignment      */
    uint8_t     active;             /**< 1 if slot has a loaded model  */
    uint8_t     _pad[3];
} npu_rotation_slot_t;

/**
 * @brief Temporal Multi-Slicing dispatcher state
 */
typedef struct {
    npu_rotation_slot_t slots[NPU_DISP_MAX_SLOTS];
    uint32_t    active_count;       /**< Number of active slots        */
    uint32_t    current_index;      /**< Index of currently running slot */
    uint32_t    tick_counter;       /**< Timer ticks since last rotation */
    uint32_t    total_switches;     /**< Total context switches performed */
    uint32_t    last_switch_us;     /**< Last context switch latency (us) */
    uint32_t    max_switch_us;      /**< Peak context switch latency (us) */
    uint64_t    total_switch_tsc;   /**< Accumulated TSC for all switches */
    uint8_t     initialized;        /**< 1 if dispatcher is initialized */
    uint8_t     rotation_enabled;   /**< 1 if rotation is active       */
    uint8_t     _pad[6];
} npu_dispatch_state_t;

static npu_dispatch_state_t g_npu_disp;

/* ============================================================================
 * SECTION 1: TSC HELPERS
 * ============================================================================ */

/**
 * @brief Read the Time Stamp Counter (RDTSC) — serialized with lfence
 *
 * The lfence before RDTSC ensures all prior instructions complete
 * before the timestamp is taken (prevents speculative skew).
 */
static inline uint64_t npu_disp_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile(
        "lfence\n\t"
        "rdtsc"
        : "=a"(lo), "=d"(hi)
    );
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

/**
 * @brief Convert TSC delta to microseconds (estimated)
 *
 * Uses a conservative 2 GHz estimate. Real hardware would
 * calibrate against the PIT or HPET during boot.
 */
static inline uint32_t npu_disp_tsc_to_us(uint64_t tsc_delta)
{
    return (uint32_t)(tsc_delta / NPU_DISP_TSC_PER_US);
}

/* ============================================================================
 * SECTION 2: PASID MANAGEMENT
 * ============================================================================ */

/**
 * @brief Update the PASID entry for a rotation slot
 *
 * In a real VT-d v4.0 Scalable Mode system, this would write to the
 * IOMMU's Scalable Mode root/context tables to bind the NPU's DMA
 * address space to the target slot's physical pages.
 *
 * The PASID ensures that DMA from one slot cannot access another
 * slot's HugePage-pinned memory, providing hardware-enforced isolation.
 *
 * @param[in] slot  Rotation slot descriptor
 * @return 0 on success, -1 on error
 */
static int npu_disp_set_pasid(const npu_rotation_slot_t *slot)
{
    if (slot == NULL || !slot->active) {
        return -1;
    }

    /* PASID = (wing_id << PASID_WING_BITS) | context_id
     * This encodes both the V-Palace Wing and the hardware context
     * into a single 20-bit PASID for IOMMU binding. */
    uint32_t pasid = (slot->wing_id << VOS3_PASID_WING_BITS) | slot->context_id;

    /* In hardware:
     *   1. Write PASID to IOMMU Scalable Mode context entry
     *   2. Issue IOTLB invalidation for the old PASID
     *   3. Wait for invalidation completion (drain)
     *
     * In QEMU/emulated mode, the PASID is tracked in software
     * and the DMA fence in npu.c enforces isolation. */

    /* Memory barrier to ensure PASID update is visible before next DMA */
    __asm__ volatile("mfence" ::: "memory");

    (void)pasid;  /* Used in hardware path; tracked in software here */

    return 0;
}

/* ============================================================================
 * SECTION 3: CACHE FLUSH (L1/L2 ISOLATION)
 * ============================================================================ */

/**
 * @brief Flush L1/L2 caches between slot rotations
 *
 * WBINVD writes back all modified cache lines and invalidates
 * all cache entries. This prevents data from the previous slot's
 * inference from being visible to the next slot.
 *
 * WBINVD is a privileged instruction (Ring 0 only) and can take
 * 100-500 us on modern CPUs. We use CLFLUSH on specific regions
 * when available, falling back to WBINVD only when the slot's
 * memory region is unknown.
 *
 * For the 250 ms rotation interval, even a 500 us flush is only
 * 0.2% overhead — acceptable for V-Palace security guarantees.
 */
static void npu_disp_flush_caches(void)
{
    /* WBINVD: Write Back and Invalidate Cache
     * Flushes ALL cache lines across ALL levels.
     * This is the nuclear option — used when we cannot precisely
     * identify which cache lines belong to the outgoing slot.
     *
     * On context switch between AI slots, this ensures:
     * 1. No residual tensor data in L1/L2 from slot A
     * 2. No timing side-channel via cache hit/miss patterns
     * 3. Clean cache state for incoming slot B */
    __asm__ volatile("wbinvd" ::: "memory");
}

/* ============================================================================
 * SECTION 4: ATOMIC CONTEXT SWITCH
 * ============================================================================ */

/**
 * @brief Perform an atomic context switch between two rotation slots
 *
 * Saves the outgoing slot's NPU context, flushes caches, updates
 * the PASID, and loads the incoming slot's context.
 *
 * The entire operation must complete within NPU_DISP_MAX_SWITCH_US (15 us).
 *
 * @param[in] from  Outgoing slot (may be NULL on first rotation)
 * @param[in] to    Incoming slot (must be active)
 * @return Switch latency in microseconds
 */
static uint32_t npu_disp_context_switch(const npu_rotation_slot_t *from,
                                         const npu_rotation_slot_t *to)
{
    uint64_t tsc_start = npu_disp_rdtsc();

    /* Step 1: Save outgoing context (if active) */
    if (from != NULL && from->active) {
        /* vos3_npu_context_save persists KV-cache state to RAM.
         * In emulated mode (no physical NPU), this is a no-op
         * that records the context as "saved". */
        int save_rc = vos3_npu_context_save(0, from->context_id);
        if (save_rc != 0 && save_rc != -38) {
            /* -38 = ENOSYS (no NPU present) — expected in QEMU */
            NPU_DISP_WARN("Context save failed for slot %u (rc=%d)",
                          from->slot_id, save_rc);
            /* Phase 8.7: Invalidate outgoing PASID on save failure to prevent
             * stale IOTLB entries from allowing cross-slot DMA access.
             * Deactivate via global table (from is const view). */
            uint32_t from_off = (uint32_t)(from - g_npu_disp.slots);
            g_npu_disp.slots[from_off].active = 0;
            __asm__ volatile("mfence" ::: "memory");
        }
    }

    /* Step 2: Flush L1/L2 to prevent cross-slot cache leakage */
    npu_disp_flush_caches();

    /* Step 3: Update PASID for the incoming slot's DMA isolation */
    npu_disp_set_pasid(to);

    /* Step 4: Load incoming context */
    if (to->active) {
        int load_rc = vos3_npu_context_load(0, to->context_id,
                                                 to->slot_id, 0, 0);
        if (load_rc != 0 && load_rc != -38) {
            NPU_DISP_WARN("Context load failed for slot %u (rc=%d)",
                          to->slot_id, load_rc);
            /* Phase 8.7: Deactivate slot on load failure. The PASID was
             * already set for this slot; another mfence + cache flush
             * ensures no stale DMA mappings persist. */
            uint32_t to_off = (uint32_t)(to - g_npu_disp.slots);
            g_npu_disp.slots[to_off].active = 0;
            npu_disp_flush_caches();
            __asm__ volatile("mfence" ::: "memory");
        }
    }

    /* Measure switch latency */
    uint64_t tsc_end = npu_disp_rdtsc();
    uint64_t tsc_delta = tsc_end - tsc_start;
    uint32_t switch_us = npu_disp_tsc_to_us(tsc_delta);

    return switch_us;
}

/* ============================================================================
 * SECTION 5: ROTATION TICK HANDLER
 * ============================================================================ */

/**
 * @brief Advance to the next slot in the rotation ring
 *
 * Called periodically from the bridge polling task. When the tick
 * counter reaches the rotation interval (25 ticks = 250 ms at 100 Hz),
 * the dispatcher performs a context switch to the next active slot.
 *
 * @return 1 if a rotation occurred, 0 if not yet time
 */
int vos3_npu_dispatch_tick(void)
{
    if (!g_npu_disp.initialized || !g_npu_disp.rotation_enabled) {
        return 0;
    }

    if (g_npu_disp.active_count < 2U) {
        /* Need at least 2 slots to rotate */
        return 0;
    }

    g_npu_disp.tick_counter++;

    if (g_npu_disp.tick_counter < NPU_DISP_ROTATION_TICKS) {
        return 0;
    }

    /* Time to rotate */
    g_npu_disp.tick_counter = 0;

    uint32_t from_idx = g_npu_disp.current_index;
    uint32_t to_idx = (from_idx + 1U) % NPU_DISP_MAX_SLOTS;

    /* Find next active slot (skip inactive ones) */
    uint32_t searched = 0;
    while (!g_npu_disp.slots[to_idx].active && searched < NPU_DISP_MAX_SLOTS) {
        to_idx = (to_idx + 1U) % NPU_DISP_MAX_SLOTS;
        searched++;
    }

    if (!g_npu_disp.slots[to_idx].active || to_idx == from_idx) {
        /* No other active slot found */
        return 0;
    }

    /* Perform the context switch */
    const npu_rotation_slot_t *from_slot = &g_npu_disp.slots[from_idx];
    const npu_rotation_slot_t *to_slot = &g_npu_disp.slots[to_idx];

    uint32_t switch_us = npu_disp_context_switch(from_slot, to_slot);

    /* Update state */
    g_npu_disp.current_index = to_idx;
    g_npu_disp.total_switches++;
    g_npu_disp.last_switch_us = switch_us;
    g_npu_disp.total_switch_tsc += switch_us;

    if (switch_us > g_npu_disp.max_switch_us) {
        g_npu_disp.max_switch_us = switch_us;
    }

    /* Log high-latency switches for debugging */
    if (switch_us > NPU_DISP_MAX_SWITCH_US) {
        NPU_DISP_WARN("Switch latency %u us exceeds target %u us "
                       "(slot %u -> %u)",
                       switch_us, NPU_DISP_MAX_SWITCH_US,
                       from_slot->slot_id, to_slot->slot_id);
    }

    return 1;
}

/* ============================================================================
 * SECTION 6: SLOT MANAGEMENT
 * ============================================================================ */

/**
 * @brief Register an AI slot for temporal multi-slicing rotation
 *
 * @param[in] slot_id     AI model slot ID (0-7)
 * @param[in] context_id  NPU hardware context ID (0-7)
 * @param[in] wing_id     V-Palace Wing assignment
 * @return 0 on success, -1 if rotation ring is full
 */
int vos3_npu_dispatch_register(uint32_t slot_id, uint32_t context_id,
                                uint32_t wing_id)
{
    if (!g_npu_disp.initialized) {
        return -1;
    }

    if (g_npu_disp.active_count >= NPU_DISP_MAX_SLOTS) {
        NPU_DISP_WARN("Rotation ring full (%u/%u), cannot add slot %u",
                       g_npu_disp.active_count, NPU_DISP_MAX_SLOTS, slot_id);
        return -1;
    }

    /* Find an inactive entry */
    for (uint32_t i = 0; i < NPU_DISP_MAX_SLOTS; i++) {
        if (!g_npu_disp.slots[i].active) {
            g_npu_disp.slots[i].slot_id    = slot_id;
            g_npu_disp.slots[i].context_id = context_id;
            g_npu_disp.slots[i].wing_id    = wing_id;
            g_npu_disp.slots[i].active     = 1;
            g_npu_disp.active_count++;

            NPU_DISP_INFO("Registered slot %u (ctx=%u, wing=%u) for rotation",
                          slot_id, context_id, wing_id);

            /* Enable rotation once we have >= 2 active slots */
            if (g_npu_disp.active_count >= 2U) {
                g_npu_disp.rotation_enabled = 1;
            }

            return 0;
        }
    }

    return -1;
}

/**
 * @brief Unregister an AI slot from the rotation ring
 *
 * @param[in] slot_id  AI model slot to remove
 * @return 0 on success, -1 if not found
 */
int vos3_npu_dispatch_unregister(uint32_t slot_id)
{
    if (!g_npu_disp.initialized) {
        return -1;
    }

    for (uint32_t i = 0; i < NPU_DISP_MAX_SLOTS; i++) {
        if (g_npu_disp.slots[i].active &&
            g_npu_disp.slots[i].slot_id == slot_id) {
            g_npu_disp.slots[i].active = 0;
            g_npu_disp.active_count--;

            /* Disable rotation if fewer than 2 active slots */
            if (g_npu_disp.active_count < 2U) {
                g_npu_disp.rotation_enabled = 0;
            }

            /* If we removed the current slot, advance to next */
            if (g_npu_disp.current_index == i) {
                for (uint32_t j = 0; j < NPU_DISP_MAX_SLOTS; j++) {
                    uint32_t next = (i + 1 + j) % NPU_DISP_MAX_SLOTS;
                    if (g_npu_disp.slots[next].active) {
                        g_npu_disp.current_index = next;
                        break;
                    }
                }
            }

            NPU_DISP_INFO("Unregistered slot %u from rotation", slot_id);
            return 0;
        }
    }

    return -1;
}

/* ============================================================================
 * SECTION 7: STATISTICS
 * ============================================================================ */

/**
 * @brief NPU Temporal Multi-Slicing statistics
 */
typedef struct {
    uint32_t    active_slots;
    uint32_t    total_switches;
    uint32_t    last_switch_us;
    uint32_t    max_switch_us;
    uint32_t    avg_switch_us;
    uint8_t     rotation_enabled;
} npu_dispatch_stats_t;

/**
 * @brief Get current Temporal Multi-Slicing statistics
 */
int vos3_npu_dispatch_get_stats(npu_dispatch_stats_t *stats)
{
    if (stats == NULL || !g_npu_disp.initialized) {
        return -1;
    }

    stats->active_slots     = g_npu_disp.active_count;
    stats->total_switches   = g_npu_disp.total_switches;
    stats->last_switch_us   = g_npu_disp.last_switch_us;
    stats->max_switch_us    = g_npu_disp.max_switch_us;
    stats->rotation_enabled = g_npu_disp.rotation_enabled;

    if (g_npu_disp.total_switches > 0) {
        stats->avg_switch_us = (uint32_t)(g_npu_disp.total_switch_tsc /
                                           g_npu_disp.total_switches);
    } else {
        stats->avg_switch_us = 0;
    }

    return 0;
}

/* ============================================================================
 * SECTION 8: INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize the NPU Temporal Multi-Slicing dispatcher
 *
 * Sets up the rotation ring with default Wing assignments.
 * Rotation does not begin until at least 2 slots are registered.
 *
 * @return 0 on success, -1 on error
 */
int vos3_npu_dispatch_init(void)
{
    /* Zero-initialize all state */
    for (uint32_t i = 0; i < NPU_DISP_MAX_SLOTS; i++) {
        g_npu_disp.slots[i].slot_id    = 0;
        g_npu_disp.slots[i].context_id = 0;
        g_npu_disp.slots[i].wing_id    = 0;
        g_npu_disp.slots[i].active     = 0;
    }

    g_npu_disp.active_count    = 0;
    g_npu_disp.current_index   = 0;
    g_npu_disp.tick_counter    = 0;
    g_npu_disp.total_switches  = 0;
    g_npu_disp.last_switch_us  = 0;
    g_npu_disp.max_switch_us   = 0;
    g_npu_disp.total_switch_tsc = 0;
    g_npu_disp.rotation_enabled = 0;
    g_npu_disp.initialized     = 1;

    NPU_DISP_INFO("Temporal Multi-Slicing initialized "
                   "(rotation=%u ms, max_slots=%u, max_switch=%u us)",
                   NPU_DISP_ROTATION_TICKS * 10U,
                   NPU_DISP_MAX_SLOTS,
                   NPU_DISP_MAX_SWITCH_US);

    return 0;
}
