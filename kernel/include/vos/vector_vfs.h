/**
 * @file vector_vfs.h
 * @brief VOS3 Vector VFS — Per-Slot Embedding Index for Semantic Memory
 *
 * @details Flat brute-force uint8 embedding index. 1024 shards x 64 dimensions
 *          = 64KB embedding scan fits in L1 cache. No graph maintenance,
 *          constant-time, MISRA-safe. Dot product max = 255*255*64 = 4,161,600
 *          fits in uint32_t with massive headroom.
 *
 *          Design: For <=1024 shards at 64 dimensions (uint8_t quantized),
 *          flat brute-force integer dot-product is optimal. No HNSW, no ANN
 *          graph — those are over-engineered for this scale.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 3.1: Vector VFS & Sovereign Neural Interface
 */

#ifndef VOS3_VECTOR_VFS_H
#define VOS3_VECTOR_VFS_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Per-slot shard capacity; 1024 x 64B = 64KB fits L1 */
#define VECVFS_MAX_SHARDS     1024U

/** @brief Quantized embedding dimension (uint8_t per component) */
#define VECVFS_EMBED_DIM      64U

/** @brief Token payload per shard (raw text/token IDs) */
#define VECVFS_PAYLOAD_SIZE   256U

/** @brief Maximum top-K results from a query */
#define VECVFS_MAX_RESULTS    8U

/** @brief "VECV" in ASCII — magic for validation */
#define VECVFS_MAGIC          0x56454356U

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Single embedding shard with payload.
 *
 * @details Each shard stores a quantized uint8 embedding vector and a
 *          256-byte payload (token IDs, text snippets, etc.). The hot loop
 *          in vecvfs_query() only reads embedding[64] per shard — payload
 *          is accessed only for top-K winners.
 */
typedef struct vecvfs_shard {
    uint8_t     embedding[VECVFS_EMBED_DIM];  /**< Quantized uint8 embedding */
    uint8_t     payload[VECVFS_PAYLOAD_SIZE];  /**< Token/text payload */
    uint32_t    payload_len;                   /**< Actual payload bytes used */
    uint32_t    seq_id;                        /**< Insertion sequence number */
    uint8_t     valid;                         /**< 1 = shard in use */
} vecvfs_shard_t;

/**
 * @brief Per-slot embedding index.
 *
 * @details Contains up to VECVFS_MAX_SHARDS entries. Static allocation,
 *          no dynamic memory. One index per model slot.
 */
typedef struct vecvfs_index {
    vecvfs_shard_t  shards[VECVFS_MAX_SHARDS];
    uint32_t        shard_count;               /**< Active shards */
    uint32_t        next_seq;                  /**< Monotonic seq counter */
    uint8_t         slot_id;                   /**< Owning model slot */
    uint8_t         initialized;               /**< 1 = index ready */
} vecvfs_index_t;

/**
 * @brief Query result entry.
 */
typedef struct vecvfs_result {
    uint32_t    shard_idx;                     /**< Index into shards[] */
    uint32_t    score;                         /**< Dot-product similarity */
} vecvfs_result_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize the Vector VFS subsystem.
 * @return 0 on success, negative errno on failure
 */
int vecvfs_init(void);

/**
 * @brief Initialize the embedding index for a specific slot.
 * @param slot_id Model slot (0..VOS3_MODEL_SLOT_MAX-1)
 * @return 0 on success, negative errno on failure
 */
int vecvfs_index_init(uint8_t slot_id);

/**
 * @brief Insert a shard into a slot's index.
 * @param slot_id   Model slot
 * @param embedding Quantized embedding vector (VECVFS_EMBED_DIM bytes)
 * @param payload   Payload data (up to VECVFS_PAYLOAD_SIZE bytes)
 * @param payload_len Payload length in bytes
 * @return 0 on success, -ENOSPC if full, -EINVAL on bad args
 */
int vecvfs_insert(uint8_t slot_id, const uint8_t embedding[VECVFS_EMBED_DIM],
                  const void *payload, uint32_t payload_len);

/**
 * @brief Query the index for top-K nearest shards (dot-product similarity).
 *
 * @details Constant-time flat scan: always iterates all VECVFS_MAX_SHARDS
 *          positions regardless of match position. No early exit, no
 *          branch-dependent access pattern — privacy-safe.
 *
 * @param slot_id     Model slot
 * @param query       Query embedding (VECVFS_EMBED_DIM bytes)
 * @param results     Output array for results
 * @param max_results Maximum results to return (capped at VECVFS_MAX_RESULTS)
 * @param out_count   Output: actual number of results
 * @return 0 on success, negative errno on failure
 */
int vecvfs_query(uint8_t slot_id, const uint8_t query[VECVFS_EMBED_DIM],
                 vecvfs_result_t *results, uint32_t max_results,
                 uint32_t *out_count);

/**
 * @brief Delete a shard by index (tombstone — sets valid=0).
 * @param slot_id   Model slot
 * @param shard_idx Index of shard to delete
 * @return 0 on success, negative errno on failure
 */
int vecvfs_delete(uint8_t slot_id, uint32_t shard_idx);

/**
 * @brief Get slot index statistics.
 * @param slot_id  Model slot
 * @param count    Output: active shard count
 * @param capacity Output: maximum capacity
 * @return 0 on success, negative errno on failure
 */
int vecvfs_stat(uint8_t slot_id, uint32_t *count, uint32_t *capacity);

/**
 * @brief Clear all shards in a slot's index.
 * @param slot_id Model slot
 * @return 0 on success, negative errno on failure
 */
int vecvfs_clear(uint8_t slot_id);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VECTOR_VFS_H */
