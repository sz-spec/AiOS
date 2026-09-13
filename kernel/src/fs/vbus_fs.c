/**
 * @file vbus_fs.c
 * @brief vBus FS provenance side-table — implementation.
 *
 * Sprint 15 / Item K4. See kernel/include/vos/vbus_fs.h for the rationale
 * + honest-scope ceiling. This file implements a small fixed-size open-
 * addressing hash table keyed by inode pointer.
 *
 * Concurrency: a single bitlock guards the entire table. The hot path
 * is "lookup at file-open" which is microseconds, so contention is
 * negligible at production fan-out. The audit-ring drain holds the lock
 * for the duration of an iterate() call; we cap iterate at table-size
 * (1024) so that drain is bounded.
 *
 * @date 2026-05-23
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#include "../../include/vos/vbus_fs.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * Internal table
 * ============================================================================ */

typedef struct vbus_fs_slot {
    struct vos3_inode*       inode;   /* NULL means free slot */
    vos3_vbus_fs_provenance_t prov;
} vbus_fs_slot_t;

static vbus_fs_slot_t s_table[VBUS_FS_PROVENANCE_TABLE_SLOTS];
static uint32_t       s_count = 0U;
static uint32_t       s_initialized = 0U;
static uint64_t       s_lock = 0U;  /* simple spinlock — 0 = free, 1 = held */

/* Lightweight spinlock helpers. We use the existing kernel atomic
 * primitives (defined for x86_64 in the existing mm code). For
 * portability we use __sync_*; the kernel is built with -fno-builtin so
 * these resolve to the actual atomic instructions. */
static inline void vbus_fs_lock(void)
{
    while (__sync_lock_test_and_set(&s_lock, 1U)) {
        /* spin */
        __asm__ __volatile__("pause" : : : "memory");
    }
}

static inline void vbus_fs_unlock(void)
{
    __sync_lock_release(&s_lock);
}

/* ============================================================================
 * Hash function — pointer-based
 *
 * inode pointers are 8-byte aligned. We multiply by a 64-bit golden-ratio
 * constant and shift; this gives ~uniform distribution over the table
 * size without any per-pointer comparisons.
 * ============================================================================ */

static inline uint32_t vbus_fs_hash(const struct vos3_inode* inode)
{
    uint64_t k = (uint64_t)(uintptr_t)inode;
    /* Fibonacci hashing — see Knuth TAOCP §6.4. The constant is
     * 2^64 / phi rounded to nearest odd. */
    k ^= k >> 33;
    k *= 0x9E3779B97F4A7C15ULL;
    k ^= k >> 33;
    return (uint32_t)(k & (VBUS_FS_PROVENANCE_TABLE_SLOTS - 1U));
}

/* Find the slot index for an inode, or -1 if not found.
 * Linear probe up to the entire table (worst case). Caller must hold the
 * lock. */
static int vbus_fs_find_locked(const struct vos3_inode* inode)
{
    uint32_t idx = vbus_fs_hash(inode);
    uint32_t i;
    for (i = 0U; i < VBUS_FS_PROVENANCE_TABLE_SLOTS; i++) {
        uint32_t cur = (idx + i) & (VBUS_FS_PROVENANCE_TABLE_SLOTS - 1U);
        if (s_table[cur].inode == inode) {
            return (int)cur;
        }
        if (s_table[cur].inode == NULL) {
            /* Found a free slot before finding the inode — definitely
             * not present (open addressing invariant). */
            return -1;
        }
    }
    return -1;
}

/* Find a free slot for an inode (after confirming it's not already
 * present). Returns the slot index or -1 if the table is full. */
static int vbus_fs_find_free_locked(const struct vos3_inode* inode)
{
    uint32_t idx = vbus_fs_hash(inode);
    uint32_t i;
    for (i = 0U; i < VBUS_FS_PROVENANCE_TABLE_SLOTS; i++) {
        uint32_t cur = (idx + i) & (VBUS_FS_PROVENANCE_TABLE_SLOTS - 1U);
        if (s_table[cur].inode == NULL) {
            return (int)cur;
        }
    }
    return -1;
}

/* ============================================================================
 * Public API
 * ============================================================================ */

int vos3_vbus_fs_init(void)
{
    /* Idempotent — second call is a no-op. */
    if (s_initialized) {
        return VBUS_FS_OK;
    }
    /* memset the slots to zero — NULL inode == free slot. */
    uint32_t i;
    for (i = 0U; i < VBUS_FS_PROVENANCE_TABLE_SLOTS; i++) {
        s_table[i].inode = (struct vos3_inode*)0;
    }
    s_count = 0U;
    s_initialized = 1U;
    vos3_console_printf(
        "[VBUS-FS] provenance side table initialized (%u slots)\n",
        VBUS_FS_PROVENANCE_TABLE_SLOTS);
    return VBUS_FS_OK;
}

int vos3_vbus_fs_attach(struct vos3_inode* inode,
                        const vos3_vbus_fs_provenance_t* prov)
{
    if (inode == (struct vos3_inode*)0 || prov == (const vos3_vbus_fs_provenance_t*)0) {
        return VBUS_FS_ERR_INVAL;
    }
    if (!s_initialized) {
        (void)vos3_vbus_fs_init();
    }

    vbus_fs_lock();

    int found = vbus_fs_find_locked(inode);
    if (found >= 0) {
        /* Existing entry. Refuse to overwrite if guarded. */
        if (s_table[found].prov.flags & VBUS_FS_PROVENANCE_GUARDED) {
            vbus_fs_unlock();
            return VBUS_FS_ERR_GUARDED;
        }
        /* Replace in place. */
        s_table[found].prov = *prov;
        vbus_fs_unlock();
        return VBUS_FS_OK;
    }

    /* New entry. */
    int slot = vbus_fs_find_free_locked(inode);
    if (slot < 0) {
        vbus_fs_unlock();
        vos3_console_printf(
            "[VBUS-FS] provenance table full (%u entries), refused attach\n",
            s_count);
        return VBUS_FS_ERR_NOSPC;
    }
    s_table[slot].inode = inode;
    s_table[slot].prov  = *prov;
    s_count++;
    vbus_fs_unlock();
    return VBUS_FS_OK;
}

int vos3_vbus_fs_lookup(struct vos3_inode* inode,
                        vos3_vbus_fs_provenance_t* out_prov)
{
    if (inode == (struct vos3_inode*)0 || out_prov == (vos3_vbus_fs_provenance_t*)0) {
        return VBUS_FS_ERR_INVAL;
    }
    if (!s_initialized) {
        return VBUS_FS_ERR_NOTFOUND;
    }
    vbus_fs_lock();
    int found = vbus_fs_find_locked(inode);
    if (found < 0) {
        vbus_fs_unlock();
        return VBUS_FS_ERR_NOTFOUND;
    }
    *out_prov = s_table[found].prov;
    vbus_fs_unlock();
    return VBUS_FS_OK;
}

int vos3_vbus_fs_detach(struct vos3_inode* inode)
{
    if (inode == (struct vos3_inode*)0) {
        return VBUS_FS_ERR_INVAL;
    }
    if (!s_initialized) {
        return VBUS_FS_OK;  /* no table, no attach ever happened */
    }
    vbus_fs_lock();
    int found = vbus_fs_find_locked(inode);
    if (found < 0) {
        vbus_fs_unlock();
        return VBUS_FS_OK;  /* never attached — silent no-op for free-path callers */
    }
    if (s_table[found].prov.flags & VBUS_FS_PROVENANCE_GUARDED) {
        vbus_fs_unlock();
        return VBUS_FS_ERR_GUARDED;
    }
    s_table[found].inode = (struct vos3_inode*)0;
    /* Note: leaving s_table[found].prov populated is fine — the next
     * find_locked() returns -1 on the NULL inode without inspecting the
     * old prov bytes. Avoids a memset() in the hot detach path. */
    s_count--;
    vbus_fs_unlock();
    return VBUS_FS_OK;
}

uint32_t vos3_vbus_fs_count(void)
{
    if (!s_initialized) {
        return 0U;
    }
    /* Read-only — no need to lock for a single uint32. The audit drain
     * tolerates a transient skew of 1 entry. */
    return s_count;
}

uint32_t vos3_vbus_fs_iterate(vos3_vbus_fs_iter_cb cb, void* user_data)
{
    if (cb == (vos3_vbus_fs_iter_cb)0 || !s_initialized) {
        return 0U;
    }
    vbus_fs_lock();
    uint32_t visited = 0U;
    uint32_t i;
    for (i = 0U; i < VBUS_FS_PROVENANCE_TABLE_SLOTS; i++) {
        if (s_table[i].inode != (struct vos3_inode*)0) {
            cb(s_table[i].inode, &s_table[i].prov, user_data);
            visited++;
        }
    }
    vbus_fs_unlock();
    return visited;
}
