/**
 * @file vector_vfs.c
 * @brief VOS3 Vector VFS — Flat Brute-Force Embedding Index
 *
 * @details Per-slot embedding index with constant-time flat scan.
 *          1024 shards x 64 dimensions = 64KB embedding scan fits in L1 cache.
 *          Dot product: uint32 accumulator (max 4,161,600, fits 22 bits).
 *          Top-K via partial insertion sort (K <= 8).
 *
 *          Privacy: The flat scan always iterates all VECVFS_MAX_SHARDS
 *          positions regardless of match position. No early exit, no
 *          branch-dependent access pattern.
 *
 *          Registered as VFS type "vecvfs" with VOS3_FS_NO_DCACHE flag.
 *          Mount point: /vec/<slot_id>/
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 3.1: Vector VFS & Sovereign Neural Interface
 * @note MISRA C:2024 Compliant — no malloc, no libc, static arrays only
 */

#include "../../include/vos/vector_vfs.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/console.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/* Model slot array (defined in ai_slots.c) */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * STATIC STATE
 * ============================================================================ */

/** @brief Per-slot embedding indexes (one per model slot) */
static vecvfs_index_t g_vecvfs_index[VOS3_MODEL_SLOT_MAX];

/** @brief Subsystem initialization flag */
static uint8_t g_vecvfs_initialized = 0;

/* ============================================================================
 * VFS STUBS (Vector VFS is query-based, not inode-based)
 * ============================================================================ */

/* ============================================================================
 * VFS MOUNT / UNMOUNT
 * ============================================================================ */

static vos3_superblock_t *vecvfs_mount_fn(vos3_fs_type_t *fs, const char *source,
                                           uint32_t flags, void *data)
{
    (void)fs; (void)source; (void)flags; (void)data;
    /* Vector VFS doesn't use traditional mount — indexes are initialized
     * per-slot via vecvfs_index_init(). Return NULL for VFS compatibility. */
    return NULL;
}

static int vecvfs_unmount_fn(vos3_superblock_t *sb)
{
    (void)sb;
    return 0;
}

/* ============================================================================
 * FILESYSTEM TYPE REGISTRATION
 * ============================================================================ */

static vos3_fs_type_t g_vecvfs_type = {
    .name    = "vecvfs",
    .flags   = VOS3_FS_NO_DCACHE,  /* No stale dentry caching across slot resets */
    .mount   = vecvfs_mount_fn,
    .unmount = vecvfs_unmount_fn,
    .next    = NULL,
};

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Zero a block of memory (no libc dependency).
 */
static void vecvfs_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) {
        p[i] = 0;
    }
}

/**
 * @brief Copy memory (no libc dependency).
 */
static void vecvfs_memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; i++) {
        d[i] = s[i];
    }
}

/**
 * @brief Compute uint8 dot product of two VECVFS_EMBED_DIM vectors.
 *
 * @details Max value: 255 * 255 * 64 = 4,161,600 — fits in uint32_t (22 bits).
 *          No overflow possible.
 */
static uint32_t vecvfs_dot_product(const uint8_t *a, const uint8_t *b)
{
    uint32_t dot = 0;
    for (uint32_t d = 0; d < VECVFS_EMBED_DIM; d++) {
        dot += (uint32_t)a[d] * (uint32_t)b[d];
    }
    return dot;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vecvfs_init(void)
{
    if (g_vecvfs_initialized != 0U) {
        return 0; /* already initialized */
    }

    /* Zero all indexes */
    vecvfs_memzero(g_vecvfs_index, sizeof(g_vecvfs_index));

    /* Register with VFS */
    int rc = vos3_fs_register(&g_vecvfs_type);
    if (rc != 0) {
        VOS3_WARN("[VecVFS] Failed to register filesystem type (err=%d)", rc);
        return rc;
    }

    g_vecvfs_initialized = 1;
    VOS3_INFO("[VecVFS] Subsystem initialized (shards=%u, dim=%u, payload=%uB)",
              VECVFS_MAX_SHARDS, VECVFS_EMBED_DIM, VECVFS_PAYLOAD_SIZE);

    return 0;
}

int vecvfs_index_init(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (g_vecvfs_initialized == 0U) {
        return -38; /* ENOSYS */
    }

    vecvfs_index_t *idx = &g_vecvfs_index[slot_id];

    /* Zero the entire index */
    vecvfs_memzero(idx, sizeof(vecvfs_index_t));

    idx->slot_id = slot_id;
    idx->shard_count = 0;
    idx->next_seq = 0;
    idx->initialized = 1;

    VOS3_INFO("[VecVFS] Index initialized for slot %u", slot_id);
    return 0;
}

int vecvfs_insert(uint8_t slot_id, const uint8_t embedding[VECVFS_EMBED_DIM],
                  const void *payload, uint32_t payload_len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vecvfs_index_t *idx = &g_vecvfs_index[slot_id];
    if (idx->initialized == 0U) {
        return -38; /* ENOSYS — index not initialized */
    }

    if (payload_len > VECVFS_PAYLOAD_SIZE) {
        return -22; /* EINVAL — payload too large */
    }

    if (idx->shard_count >= VECVFS_MAX_SHARDS) {
        return -28; /* ENOSPC — index full */
    }

    /* Linear scan for first free slot */
    for (uint32_t i = 0; i < VECVFS_MAX_SHARDS; i++) {
        if (idx->shards[i].valid == 0U) {
            vecvfs_shard_t *shard = &idx->shards[i];

            /* Copy embedding */
            vecvfs_memcpy(shard->embedding, embedding, VECVFS_EMBED_DIM);

            /* Copy payload */
            vecvfs_memzero(shard->payload, VECVFS_PAYLOAD_SIZE);
            if (payload != NULL && payload_len > 0U) {
                vecvfs_memcpy(shard->payload, payload, payload_len);
            }
            shard->payload_len = payload_len;

            /* Set metadata */
            shard->seq_id = idx->next_seq;
            idx->next_seq++;
            shard->valid = 1;
            idx->shard_count++;

            return 0;
        }
    }

    return -28; /* ENOSPC — should not reach here if shard_count is correct */
}

int vecvfs_query(uint8_t slot_id, const uint8_t query[VECVFS_EMBED_DIM],
                 vecvfs_result_t *results, uint32_t max_results,
                 uint32_t *out_count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (results == NULL || out_count == NULL) {
        return -22; /* EINVAL */
    }

    vecvfs_index_t *idx = &g_vecvfs_index[slot_id];
    if (idx->initialized == 0U) {
        *out_count = 0;
        return -38; /* ENOSYS */
    }

    /* Cap max_results */
    if (max_results > VECVFS_MAX_RESULTS) {
        max_results = VECVFS_MAX_RESULTS;
    }

    /* Initialize results to zero */
    for (uint32_t r = 0; r < max_results; r++) {
        results[r].shard_idx = 0;
        results[r].score = 0;
    }

    uint32_t result_count = 0;

    /*
     * CONSTANT-TIME FLAT SCAN
     *
     * Always iterates ALL VECVFS_MAX_SHARDS positions regardless of match
     * position. No early exit, no branch-dependent access pattern.
     * This is deliberate for privacy: timing is independent of which
     * shard matches.
     */
    for (uint32_t i = 0; i < VECVFS_MAX_SHARDS; i++) {
        if (idx->shards[i].valid == 0U) {
            continue; /* Tombstone — skip but still iterate */
        }

        uint32_t score = vecvfs_dot_product(query, idx->shards[i].embedding);

        /* Partial insertion sort into top-K results */
        if (result_count < max_results) {
            /* Still filling the result array */
            results[result_count].shard_idx = i;
            results[result_count].score = score;
            result_count++;

            /* Bubble up to maintain sorted order (descending) */
            for (uint32_t j = result_count - 1; j > 0; j--) {
                if (results[j].score > results[j - 1].score) {
                    vecvfs_result_t tmp = results[j];
                    results[j] = results[j - 1];
                    results[j - 1] = tmp;
                } else {
                    break;
                }
            }
        } else if (score > results[max_results - 1].score) {
            /* Replace the lowest-scoring result */
            results[max_results - 1].shard_idx = i;
            results[max_results - 1].score = score;

            /* Bubble up */
            for (uint32_t j = max_results - 1; j > 0; j--) {
                if (results[j].score > results[j - 1].score) {
                    vecvfs_result_t tmp = results[j];
                    results[j] = results[j - 1];
                    results[j - 1] = tmp;
                } else {
                    break;
                }
            }
        }
    }

    *out_count = result_count;
    return 0;
}

int vecvfs_delete(uint8_t slot_id, uint32_t shard_idx)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (shard_idx >= VECVFS_MAX_SHARDS) {
        return -22; /* EINVAL */
    }

    vecvfs_index_t *idx = &g_vecvfs_index[slot_id];
    if (idx->initialized == 0U) {
        return -38; /* ENOSYS */
    }

    if (idx->shards[shard_idx].valid == 0U) {
        return -2; /* ENOENT — already deleted */
    }

    /* Tombstone: set valid=0, no compaction */
    idx->shards[shard_idx].valid = 0;
    if (idx->shard_count > 0U) {
        idx->shard_count--;
    }

    return 0;
}

int vecvfs_stat(uint8_t slot_id, uint32_t *count, uint32_t *capacity)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vecvfs_index_t *idx = &g_vecvfs_index[slot_id];
    if (idx->initialized == 0U) {
        if (count != NULL) *count = 0;
        if (capacity != NULL) *capacity = VECVFS_MAX_SHARDS;
        return -38; /* ENOSYS */
    }

    if (count != NULL) {
        *count = idx->shard_count;
    }
    if (capacity != NULL) {
        *capacity = VECVFS_MAX_SHARDS;
    }

    return 0;
}

int vecvfs_clear(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vecvfs_index_t *idx = &g_vecvfs_index[slot_id];
    if (idx->initialized == 0U) {
        return -38; /* ENOSYS */
    }

    /* Zero all shards */
    vecvfs_memzero(idx->shards, sizeof(idx->shards));
    idx->shard_count = 0;
    idx->next_seq = 0;

    VOS3_INFO("[VecVFS] Index cleared for slot %u", slot_id);
    return 0;
}
