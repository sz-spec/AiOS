/**
 * @file sni.h
 * @brief VOS3 Sovereign Neural Interface (SNI) — Context Injection Loop
 *
 * @details The SNI connects semantic retrieval (Vector VFS) to the KV-cache
 *          injection pipeline. For each query, it finds the top-K matching
 *          shards and injects their payloads into the slot's context window,
 *          enabling context-aware inference without leaving the kernel address
 *          space.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 3.1: Vector VFS & Sovereign Neural Interface
 */

#ifndef VOS3_SNI_H
#define VOS3_SNI_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "vector_vfs.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Top-3 shards injected per query */
#define SNI_MAX_INJECT    3U

/** @brief One session per model slot */
#define SNI_MAX_SESSIONS  4U

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Per-slot SNI session tracking.
 */
typedef struct sni_session {
    uint8_t     slot_id;                /**< Target model slot */
    uint8_t     active;                 /**< 1 = session running */
    uint32_t    queries_served;         /**< Total queries processed */
    uint32_t    shards_injected;        /**< Total shards injected */
    uint32_t    injection_failures;     /**< Failed injections (no space) */
    uint64_t    total_query_cycles;     /**< Sum of query latency (TSC) */
    uint64_t    last_query_cycles;      /**< Latest query latency */
} sni_session_t;

/**
 * @brief Global SNI statistics.
 */
typedef struct sni_stats {
    uint32_t    sessions_started;
    uint32_t    sessions_stopped;
    uint32_t    total_queries;
    uint32_t    total_injections;
    uint64_t    total_cycles;
} sni_stats_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize the SNI subsystem.
 * @return 0 on success, negative errno on failure
 */
int vos3_sni_init(void);

/**
 * @brief Start an SNI session for a model slot.
 * @param slot_id Target model slot (0..VOS3_MODEL_SLOT_MAX-1)
 * @return 0 on success, negative errno on failure
 */
int vos3_sni_start(uint8_t slot_id);

/**
 * @brief Execute a semantic query and inject top-K results into context.
 *
 * @details Steps:
 *   1. vecvfs_query() — get top-3 shards
 *   2. For each shard, inject payload via vos3_ctx_window_advance()
 *   3. Update TSC-based latency tracking
 *
 * @param slot_id Target model slot
 * @param query   Query embedding (VECVFS_EMBED_DIM bytes)
 * @return 0 on success, negative errno on failure
 */
int vos3_sni_query(uint8_t slot_id, const uint8_t query[VECVFS_EMBED_DIM]);

/**
 * @brief Stop an SNI session for a model slot.
 * @param slot_id Target model slot
 * @return 0 on success, negative errno on failure
 */
int vos3_sni_stop(uint8_t slot_id);

/**
 * @brief Get global SNI statistics.
 * @param out Output statistics structure
 */
void vos3_sni_get_stats(sni_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SNI_H */
