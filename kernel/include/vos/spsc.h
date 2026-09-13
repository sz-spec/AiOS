/**
 * @file spsc.h
 * @brief VOS3 Lock-Free SPSC Ring Buffer (Kernel)
 *
 * @details Single-Producer Single-Consumer ring buffer with 128-byte-aligned
 *          regions to defeat L2 adjacent-line prefetcher interference.
 *          Uses unbounded monotonic uint64_t indices with cached-index
 *          optimization to minimize cross-region atomic reads.
 *
 * @version 1.0.0
 * @date 2026-03-22
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2 — Lockless AI Messaging
 */

#ifndef VOS3_SPSC_H
#define VOS3_SPSC_H

#include <stdint.h>
#include <vos/atomic.h>
#include <vos/compiler.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Region alignment for L2 prefetcher isolation (128-byte pairs) */
#define VOS3_SPSC_REGION_SIZE  128U

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief 64-byte cache-line-aligned message slot
 */
typedef struct vos3_spsc_slot {
    uint8_t data[64];
} __attribute__((aligned(64))) vos3_spsc_slot_t;

/**
 * @brief SPSC ring buffer descriptor — 3 x 128-byte regions (384 bytes total)
 *
 * Region 1 (producer-owned):  head + tail_cached + padding
 * Region 2 (consumer-owned):  tail + head_cached + padding
 * Region 3 (read-only meta):  mask + capacity + slots ptr + padding
 */
typedef struct vos3_spsc_ring {
    /* --- Region 1: Producer-owned (128 bytes) --- */
    volatile uint64_t   head;           /*  8 bytes */
    uint64_t            tail_cached;    /*  8 bytes */
    uint8_t             _pad0[112];     /* 112 bytes → 128 total */

    /* --- Region 2: Consumer-owned (128 bytes) --- */
    volatile uint64_t   tail;           /*  8 bytes */
    uint64_t            head_cached;    /*  8 bytes */
    uint8_t             _pad1[112];     /* 112 bytes → 128 total */

    /* --- Region 3: Read-only metadata (128 bytes) --- */
    uint64_t            mask;           /*  8 bytes */
    uint64_t            capacity;       /*  8 bytes */
    vos3_spsc_slot_t*   slots;          /*  8 bytes */
    uint8_t             _pad2[104];     /* 104 bytes → 128 total */
} __attribute__((aligned(128))) vos3_spsc_ring_t;

_Static_assert(sizeof(vos3_spsc_ring_t) == 384,
               "vos3_spsc_ring_t must be exactly 384 bytes (3 x 128)");

/* ============================================================================
 * INLINE OPERATIONS
 * ============================================================================ */

/**
 * @brief Initialize SPSC ring buffer
 * @param ring   Ring buffer descriptor (caller-allocated)
 * @param slots  Pre-allocated slot array (must be capacity entries, 64-byte aligned)
 * @param capacity Number of slots (MUST be power of 2)
 * @return 0 on success, -1 if capacity is not a power of 2 or is zero
 */
VOS3_ALWAYS_INLINE int vos3_spsc_init(vos3_spsc_ring_t* ring,
                                       vos3_spsc_slot_t* slots,
                                       uint64_t capacity)
{
    /* Validate power-of-2 and non-zero */
    if (unlikely(capacity == 0U || (capacity & (capacity - 1U)) != 0U)) {
        return -1;
    }

    ring->head         = 0U;
    ring->tail_cached  = 0U;
    ring->tail         = 0U;
    ring->head_cached  = 0U;
    ring->mask         = capacity - 1U;
    ring->capacity     = capacity;
    ring->slots        = slots;

    return 0;
}

/**
 * @brief Push one slot into the ring (producer side)
 * @param ring Ring buffer
 * @param src  Source slot to copy
 * @return 0 on success, -1 if ring is full
 */
VOS3_ALWAYS_INLINE int vos3_spsc_push(vos3_spsc_ring_t* ring,
                                       const vos3_spsc_slot_t* src)
{
    const uint64_t head = ring->head;   /* local read — producer owns this */

    /* Check if full using cached tail */
    if (head - ring->tail_cached > ring->mask) {
        /* Stale cache — reload tail from consumer region */
        ring->tail_cached = vos3_atomic_load64(&ring->tail);
        if (head - ring->tail_cached > ring->mask) {
            return -1;  /* truly full */
        }
    }

    /* Write slot data */
    ring->slots[head & ring->mask] = *src;

    /* Ensure slot write is visible before head update */
    vos3_write_barrier();

    /* Publish new head */
    vos3_atomic_store64(&ring->head, head + 1U);

    return 0;
}

/**
 * @brief Pop one slot from the ring (consumer side)
 * @param ring Ring buffer
 * @param dst  Destination slot to copy into
 * @return 0 on success, -1 if ring is empty
 */
VOS3_ALWAYS_INLINE int vos3_spsc_pop(vos3_spsc_ring_t* ring,
                                      vos3_spsc_slot_t* dst)
{
    const uint64_t tail = ring->tail;   /* local read — consumer owns this */

    /* Check if empty using cached head */
    if (ring->head_cached == tail) {
        /* Stale cache — reload head from producer region */
        ring->head_cached = vos3_atomic_load64(&ring->head);
        if (ring->head_cached == tail) {
            return -1;  /* truly empty */
        }
    }

    /* Ensure we read slot data after head was published */
    vos3_read_barrier();

    /* Read slot data */
    *dst = ring->slots[tail & ring->mask];

    /* Ensure slot read completes before tail update */
    vos3_write_barrier();

    /* Publish new tail */
    vos3_atomic_store64(&ring->tail, tail + 1U);

    return 0;
}

/**
 * @brief Query approximate occupancy
 * @param ring Ring buffer
 * @return Approximate number of items in the ring
 */
VOS3_ALWAYS_INLINE uint64_t vos3_spsc_size(const vos3_spsc_ring_t* ring)
{
    const uint64_t head = vos3_atomic_load64(&ring->head);
    const uint64_t tail = vos3_atomic_load64(&ring->tail);
    return head - tail;
}

#endif /* VOS3_SPSC_H */
