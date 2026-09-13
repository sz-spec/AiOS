/**
 * @file spsc.h
 * @brief VOS3 Lock-Free SPSC Ring Buffer (User-Space)
 *
 * @details Self-contained header-only SPSC ring buffer for user-space
 *          AI agent messaging. Uses C11 __atomic_* builtins with
 *          acquire/release ordering (plain mov on x86_64 TSO).
 *          128-byte-aligned regions defeat L2 adjacent-line prefetcher.
 *
 * @version 1.0.0
 * @date 2026-03-22
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2 — Lockless AI Messaging
 */

#ifndef SPSC_H
#define SPSC_H

#include <stdint.h>

/* ============================================================================
 * CONSTANTS & ATTRIBUTES
 * ============================================================================ */

/** @brief Region alignment for L2 prefetcher isolation */
#define SPSC_REGION_SIZE    128U

/** @brief Struct alignment for 128-byte prefetch-pair isolation */
#define SPSC_ALIGNED        __attribute__((aligned(128)))

/** @brief Slot alignment for cache-line isolation */
#define SPSC_SLOT_ALIGNED   __attribute__((aligned(64)))

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief 64-byte cache-line-aligned message slot
 *
 * Structured for AI agent messaging with type/length/payload.
 */
typedef struct spsc_slot {
    uint32_t    msg_type;       /*  4 bytes — message type discriminator */
    uint32_t    msg_len;        /*  4 bytes — payload length */
    uint8_t     payload[52];    /* 52 bytes — message payload */
    uint32_t    _reserved;      /*  4 bytes — alignment/future use */
} SPSC_SLOT_ALIGNED spsc_slot_t;

_Static_assert(sizeof(spsc_slot_t) == 64,
               "spsc_slot_t must be exactly 64 bytes (one cache line)");

/**
 * @brief SPSC ring buffer descriptor — 3 x 128-byte regions (384 bytes total)
 *
 * Region 1 (producer-owned):  head + tail_cached + padding
 * Region 2 (consumer-owned):  tail + head_cached + padding
 * Region 3 (read-only meta):  mask + capacity + slots ptr + padding
 */
typedef struct spsc_ring {
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
    spsc_slot_t*        slots;          /*  8 bytes */
    uint8_t             _pad2[104];     /* 104 bytes → 128 total */
} SPSC_ALIGNED spsc_ring_t;

_Static_assert(sizeof(spsc_ring_t) == 384,
               "spsc_ring_t must be exactly 384 bytes (3 x 128)");

/* ============================================================================
 * MESSAGE TYPES
 * ============================================================================ */

#define SPSC_MSG_DATA       1U    /* Normal data message */
#define SPSC_MSG_DONE       0xFFFFFFFFU  /* Sentinel: producer is done */

/* ============================================================================
 * INLINE OPERATIONS
 * ============================================================================ */

/**
 * @brief Initialize SPSC ring buffer
 * @param ring     Ring buffer descriptor (caller-allocated)
 * @param slots    Pre-allocated slot array (must be capacity entries)
 * @param capacity Number of slots (MUST be power of 2)
 * @return 0 on success, -1 if capacity is not a power of 2 or is zero
 */
static inline int spsc_init(spsc_ring_t* ring,
                             spsc_slot_t* slots,
                             uint64_t capacity)
{
    /* Validate power-of-2 and non-zero */
    if (capacity == 0U || (capacity & (capacity - 1U)) != 0U) {
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
static inline int spsc_push(spsc_ring_t* ring,
                             const spsc_slot_t* src)
{
    const uint64_t head = ring->head;   /* local read — producer owns this */

    /* Check if full using cached tail */
    if (head - ring->tail_cached > ring->mask) {
        /* Stale cache — reload tail from consumer region (acquire) */
        ring->tail_cached = __atomic_load_n(&ring->tail, __ATOMIC_ACQUIRE);
        if (head - ring->tail_cached > ring->mask) {
            return -1;  /* truly full */
        }
    }

    /* Write slot data */
    ring->slots[head & ring->mask] = *src;

    /* Publish new head (release — ensures slot write is visible first) */
    __atomic_store_n(&ring->head, head + 1U, __ATOMIC_RELEASE);

    return 0;
}

/**
 * @brief Pop one slot from the ring (consumer side)
 * @param ring Ring buffer
 * @param dst  Destination slot to copy into
 * @return 0 on success, -1 if ring is empty
 *
 * @note Emits 'pause' on empty to reduce pipeline stalls in spin loops.
 *       Prefetches next slot after successful pop to hide memory latency.
 */
static inline int spsc_pop(spsc_ring_t* ring,
                            spsc_slot_t* dst)
{
    const uint64_t tail = ring->tail;   /* local read — consumer owns this */

    /* Check if empty using cached head */
    if (ring->head_cached == tail) {
        /* Stale cache — reload head from producer region (acquire) */
        ring->head_cached = __atomic_load_n(&ring->head, __ATOMIC_ACQUIRE);
        if (ring->head_cached == tail) {
            /* pause: prevent CPU pipeline stalls in QEMU spin loops */
            __asm__ volatile ("pause" ::: "memory");
            return -1;  /* truly empty */
        }
    }

    /* Read slot data (head was loaded with acquire, so slot data is visible) */
    *dst = ring->slots[tail & ring->mask];

    /* Prefetch next slot to hide memory latency on subsequent pop */
    __builtin_prefetch(&ring->slots[(tail + 1U) & ring->mask], 0, 3);

    /* Publish new tail (release — ensures slot read completed first) */
    __atomic_store_n(&ring->tail, tail + 1U, __ATOMIC_RELEASE);

    return 0;
}

/**
 * @brief Push a batch of slots into the ring with a single atomic head update
 * @param ring  Ring buffer
 * @param src   Array of slots to push
 * @param count Number of slots (1-8)
 * @return 0 on success, -1 if ring doesn't have room for all slots
 *
 * @note Reduces cache-coherency traffic by count× compared to individual pushes.
 *       The consumer sees all slots atomically when head is updated.
 */
static inline int spsc_push_batch(spsc_ring_t* ring,
                                    const spsc_slot_t* src,
                                    uint32_t count)
{
    if (count == 0U || count > 8U) {
        return -1;
    }

    const uint64_t head = ring->head;   /* local read — producer owns this */

    /* Check if room for all `count` slots using cached tail */
    uint64_t last_idx = head + (uint64_t)count - 1U;
    if (last_idx - ring->tail_cached > ring->mask) {
        /* Stale cache — reload tail from consumer region (acquire) */
        ring->tail_cached = __atomic_load_n(&ring->tail, __ATOMIC_ACQUIRE);
        if (last_idx - ring->tail_cached > ring->mask) {
            return -1;  /* not enough room */
        }
    }

    /* Copy all slots into ring */
    for (uint32_t i = 0U; i < count; i++) {
        ring->slots[(head + i) & ring->mask] = src[i];
    }

    /* Single atomic head update — reduces coherency traffic by count× */
    __atomic_store_n(&ring->head, head + (uint64_t)count, __ATOMIC_RELEASE);

    return 0;
}

/**
 * @brief Query approximate occupancy
 * @param ring Ring buffer
 * @return Approximate number of items in the ring
 */
static inline uint64_t spsc_size(const spsc_ring_t* ring)
{
    const uint64_t head = __atomic_load_n(&ring->head, __ATOMIC_ACQUIRE);
    const uint64_t tail = __atomic_load_n(&ring->tail, __ATOMIC_ACQUIRE);
    return head - tail;
}

#endif /* SPSC_H */
