/**
 * @file ai_oom.c
 * @brief Overflow-safe reservation accounting for AI memory owners.
 */

#include "../../include/vos/ai_oom.h"

#include <stdint.h>

_Static_assert(sizeof(size_t) <= sizeof(uint64_t),
               "AI quota helpers require size_t to fit in uint64_t");

static void saturating_increment(uint64_t *counter)
{
    if (*counter != UINT64_MAX) {
        (*counter)++;
    }
}

static vos3_ai_quota_result_t validate_ledger(
    const vos3_ai_mem_quota_t *quota, uint64_t *total)
{
    uint64_t used = 0U;
    uint64_t reserved = 0U;

    if (quota == NULL) {
        return VOS3_AI_QUOTA_INVALID;
    }
    for (uint32_t i = 0U; i < VOS3_AI_QUOTA_MAX_RESERVATIONS; i++) {
        const vos3_ai_quota_entry_t *entry = &quota->entries[i];
        if (entry->state == VOS3_AI_RESERVATION_FREE) {
            if (entry->bytes != 0U) {
                return VOS3_AI_QUOTA_CORRUPT;
            }
            continue;
        }
        if (entry->bytes == 0U || entry->generation == 0U) {
            return VOS3_AI_QUOTA_CORRUPT;
        }
        if (entry->state == VOS3_AI_RESERVATION_RESERVED) {
            if (reserved > UINT64_MAX - entry->bytes) {
                return VOS3_AI_QUOTA_CORRUPT;
            }
            reserved += entry->bytes;
        } else if (entry->state == VOS3_AI_RESERVATION_COMMITTED) {
            if (used > UINT64_MAX - entry->bytes) {
                return VOS3_AI_QUOTA_CORRUPT;
            }
            used += entry->bytes;
        } else {
            return VOS3_AI_QUOTA_CORRUPT;
        }
    }
    if (used != quota->used || reserved != quota->reserved ||
        used > UINT64_MAX - reserved) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    if (total != NULL) {
        *total = used + reserved;
    }
    return VOS3_AI_QUOTA_OK;
}

static vos3_ai_quota_entry_t *lookup_entry(
    const vos3_ai_quota_reservation_t *reservation, uint32_t expected_state)
{
    vos3_ai_quota_entry_t *entry;

    if (reservation == NULL || reservation->owner == NULL ||
        reservation->generation == 0U ||
        reservation->index >= VOS3_AI_QUOTA_MAX_RESERVATIONS) {
        return NULL;
    }
    entry = &reservation->owner->entries[reservation->index];
    if (entry->generation != reservation->generation ||
        entry->state != expected_state || entry->bytes == 0U) {
        return NULL;
    }
    return entry;
}

vos3_ai_quota_result_t vos3_ai_quota_total(
    const vos3_ai_mem_quota_t *quota, uint64_t *total)
{
    if (quota == NULL || total == NULL) {
        return VOS3_AI_QUOTA_INVALID;
    }
    return validate_ledger(quota, total);
}

vos3_ai_quota_result_t vos3_ai_quota_try_reserve(
    vos3_ai_mem_quota_t *quota, uint64_t bytes, int enforce,
    vos3_ai_quota_reservation_t *reservation, int *warned,
    uint64_t *charged_after)
{
    uint64_t charged;
    uint32_t free_index = VOS3_AI_QUOTA_MAX_RESERVATIONS;
    uint64_t generation = 0U;
    int warning = 0;

    if (quota == NULL || bytes == 0U || reservation == NULL ||
        reservation->owner != NULL || reservation->generation != 0U) {
        return VOS3_AI_QUOTA_INVALID;
    }
    if (vos3_ai_quota_total(quota, &charged) != VOS3_AI_QUOTA_OK) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    if (bytes > UINT64_MAX - charged) {
        return VOS3_AI_QUOTA_OVERFLOW;
    }

    for (uint32_t i = 0U; i < VOS3_AI_QUOTA_MAX_RESERVATIONS; i++) {
        vos3_ai_quota_entry_t *entry = &quota->entries[i];
        if (entry->state == VOS3_AI_RESERVATION_FREE &&
            entry->generation != UINT64_MAX) {
            free_index = i;
            generation = entry->generation + 1U;
            break;
        }
    }
    if (free_index == VOS3_AI_QUOTA_MAX_RESERVATIONS) {
        return VOS3_AI_QUOTA_BUSY;
    }

    if (quota->limit != 0U &&
        (charged > quota->limit || bytes > quota->limit - charged)) {
        saturating_increment(&quota->limit_events);
        if (enforce != 0) {
            saturating_increment(&quota->denials);
            return VOS3_AI_QUOTA_EXCEEDED;
        }
        warning = 1;
    }

    quota->reserved += bytes;
    quota->entries[free_index].bytes = bytes;
    quota->entries[free_index].generation = generation;
    quota->entries[free_index].state = VOS3_AI_RESERVATION_RESERVED;
    reservation->owner = quota;
    reservation->generation = generation;
    reservation->index = free_index;
    if (warned != NULL) {
        *warned = warning;
    }
    if (charged_after != NULL) {
        *charged_after = charged + bytes;
    }
    return VOS3_AI_QUOTA_OK;
}

vos3_ai_quota_result_t vos3_ai_quota_commit(
    vos3_ai_quota_reservation_t *reservation)
{
    vos3_ai_mem_quota_t *quota;
    uint64_t bytes;
    vos3_ai_quota_entry_t *entry;

    entry = lookup_entry(reservation, VOS3_AI_RESERVATION_RESERVED);
    if (entry == NULL) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    quota = reservation->owner;
    bytes = entry->bytes;
    if (validate_ledger(quota, NULL) != VOS3_AI_QUOTA_OK) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    if (quota->reserved < bytes || quota->used > UINT64_MAX - bytes) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    quota->reserved -= bytes;
    quota->used += bytes;
    entry->state = VOS3_AI_RESERVATION_COMMITTED;
    return VOS3_AI_QUOTA_OK;
}

vos3_ai_quota_result_t vos3_ai_quota_cancel(
    vos3_ai_quota_reservation_t *reservation, int allocator_failed)
{
    vos3_ai_mem_quota_t *quota;
    uint64_t bytes;
    vos3_ai_quota_entry_t *entry;

    entry = lookup_entry(reservation, VOS3_AI_RESERVATION_RESERVED);
    if (entry == NULL) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    quota = reservation->owner;
    bytes = entry->bytes;
    if (validate_ledger(quota, NULL) != VOS3_AI_QUOTA_OK) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    if (quota->reserved < bytes) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    quota->reserved -= bytes;
    if (allocator_failed != 0) {
        saturating_increment(&quota->failures);
    }
    entry->bytes = 0U;
    entry->state = VOS3_AI_RESERVATION_FREE;
    return VOS3_AI_QUOTA_OK;
}

vos3_ai_quota_result_t vos3_ai_quota_release(
    const vos3_ai_quota_reservation_t *reservation)
{
    vos3_ai_quota_entry_t *entry = lookup_entry(
        reservation, VOS3_AI_RESERVATION_COMMITTED);
    vos3_ai_mem_quota_t *quota;
    uint64_t bytes;

    if (entry == NULL) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    quota = reservation->owner;
    bytes = entry->bytes;
    if (validate_ledger(quota, NULL) != VOS3_AI_QUOTA_OK) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    if (quota->used < bytes) {
        return VOS3_AI_QUOTA_CORRUPT;
    }
    quota->used -= bytes;
    entry->bytes = 0U;
    entry->state = VOS3_AI_RESERVATION_FREE;
    return VOS3_AI_QUOTA_OK;
}

vos3_ai_quota_result_t vos3_ai_size_align_up(
    size_t size, size_t alignment, uint64_t *out)
{
    size_t mask;

    if (size == 0U || alignment == 0U || out == NULL ||
        (alignment & (alignment - 1U)) != 0U) {
        return VOS3_AI_QUOTA_INVALID;
    }
    mask = alignment - 1U;
    if (size > SIZE_MAX - mask) {
        return VOS3_AI_QUOTA_OVERFLOW;
    }
    *out = (uint64_t)((size + mask) & ~mask);
    return VOS3_AI_QUOTA_OK;
}

vos3_ai_quota_result_t vos3_ai_size_add(
    uint64_t left, uint64_t right, uint64_t *out)
{
    if (out == NULL) {
        return VOS3_AI_QUOTA_INVALID;
    }
    if (left > UINT64_MAX - right) {
        return VOS3_AI_QUOTA_OVERFLOW;
    }
    *out = left + right;
    return VOS3_AI_QUOTA_OK;
}

vos3_ai_quota_result_t vos3_ai_size_mul(
    uint64_t count, uint64_t unit, uint64_t *out)
{
    if (out == NULL || count == 0U || unit == 0U) {
        return VOS3_AI_QUOTA_INVALID;
    }
    if (count > UINT64_MAX / unit) {
        return VOS3_AI_QUOTA_OVERFLOW;
    }
    *out = count * unit;
    return VOS3_AI_QUOTA_OK;
}
