/**
 * @file ai_oom.h
 * @brief Overflow-safe AI memory quota transactions.
 *
 * These helpers perform accounting only.  The caller owns serialization and
 * must hold the lock that protects the quota for the whole operation.
 */

#ifndef VOS3_AI_OOM_H
#define VOS3_AI_OOM_H

#include <stddef.h>
#include <stdint.h>

typedef enum vos3_ai_quota_result {
    VOS3_AI_QUOTA_OK = 0,
    VOS3_AI_QUOTA_INVALID = -1,
    VOS3_AI_QUOTA_OVERFLOW = -2,
    VOS3_AI_QUOTA_EXCEEDED = -3,
    VOS3_AI_QUOTA_CORRUPT = -4,
    VOS3_AI_QUOTA_BUSY = -5
} vos3_ai_quota_result_t;

#define VOS3_AI_QUOTA_MAX_RESERVATIONS 64U

typedef enum vos3_ai_reservation_state {
    VOS3_AI_RESERVATION_FREE = 0,
    VOS3_AI_RESERVATION_RESERVED = 1,
    VOS3_AI_RESERVATION_COMMITTED = 2
} vos3_ai_reservation_state_t;

typedef struct vos3_ai_quota_entry {
    uint64_t bytes;
    uint64_t generation;
    uint32_t state;
} vos3_ai_quota_entry_t;

typedef struct vos3_ai_mem_quota {
    uint64_t limit;       /**< Zero means unlimited. */
    uint64_t used;        /**< Committed physical byte charge. */
    uint64_t reserved;    /**< Admitted charge awaiting commit/cancel. */
    uint64_t limit_events;/**< Saturating soft/hard limit-event counter. */
    uint64_t denials;     /**< Saturating hard policy-denial counter. */
    uint64_t failures;    /**< Saturating allocator-failure counter. */
    vos3_ai_quota_entry_t entries[VOS3_AI_QUOTA_MAX_RESERVATIONS];
} vos3_ai_mem_quota_t;

typedef struct vos3_ai_quota_reservation {
    vos3_ai_mem_quota_t *owner;
    uint64_t generation;
    uint32_t index;
} vos3_ai_quota_reservation_t;

vos3_ai_quota_result_t vos3_ai_quota_try_reserve(
    vos3_ai_mem_quota_t *quota, uint64_t bytes, int enforce,
    vos3_ai_quota_reservation_t *reservation, int *warned,
    uint64_t *charged_after);
vos3_ai_quota_result_t vos3_ai_quota_commit(
    vos3_ai_quota_reservation_t *reservation);
vos3_ai_quota_result_t vos3_ai_quota_cancel(
    vos3_ai_quota_reservation_t *reservation, int allocator_failed);
vos3_ai_quota_result_t vos3_ai_quota_release(
    const vos3_ai_quota_reservation_t *reservation);
vos3_ai_quota_result_t vos3_ai_quota_total(
    const vos3_ai_mem_quota_t *quota, uint64_t *total);

vos3_ai_quota_result_t vos3_ai_size_align_up(
    size_t size, size_t alignment, uint64_t *out);
vos3_ai_quota_result_t vos3_ai_size_add(
    uint64_t left, uint64_t right, uint64_t *out);
vos3_ai_quota_result_t vos3_ai_size_mul(
    uint64_t count, uint64_t unit, uint64_t *out);

#endif /* VOS3_AI_OOM_H */
