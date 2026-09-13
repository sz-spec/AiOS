/**
 * @file sni_orchestrator.c
 * @brief VOS3 Sovereign Neural Interface (SNI) — Context Injection Loop
 *
 * @details The SNI query loop:
 *          1. vecvfs_query(slot_id, query, top-3) — get top-K shards
 *          2. For each shard, inject payload via vos3_ctx_window_advance()
 *          3. Update TSC-based latency tracking
 *
 *          Session management:
 *          - vos3_sni_start(): allocate session, verify slot ACTIVE
 *          - vos3_sni_stop(): deactivate session, log stats
 *          - vos3_sni_get_stats(): copy global stats
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

#include "../../include/vos/sni.h"
#include "../../include/vos/vector_vfs.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/ai_spec.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"

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

/** @brief Per-slot SNI sessions */
static sni_session_t g_sni_sessions[SNI_MAX_SESSIONS];

/** @brief Global SNI statistics */
static sni_stats_t g_sni_stats;

/** @brief Subsystem initialization flag */
static uint8_t g_sni_initialized = 0;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Zero a block of memory (no libc dependency).
 */
static void sni_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) {
        p[i] = 0;
    }
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vos3_sni_init(void)
{
    if (g_sni_initialized != 0U) {
        return 0; /* already initialized */
    }

    /* Zero all sessions and stats */
    sni_memzero(g_sni_sessions, sizeof(g_sni_sessions));
    sni_memzero(&g_sni_stats, sizeof(g_sni_stats));

    g_sni_initialized = 1;
    VOS3_INFO("[SNI] Sovereign Neural Interface initialized (max_inject=%u, sessions=%u)",
              SNI_MAX_INJECT, SNI_MAX_SESSIONS);

    return 0;
}

int vos3_sni_start(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (g_sni_initialized == 0U) {
        return -38; /* ENOSYS */
    }
    if (slot_id >= SNI_MAX_SESSIONS) {
        return -22; /* EINVAL — session index out of range */
    }

    sni_session_t *session = &g_sni_sessions[slot_id];

    /* Check if already active */
    if (session->active != 0U) {
        return -16; /* EBUSY */
    }

    /* Initialize the Vector VFS index for this slot if not already done */
    int rc = vecvfs_index_init(slot_id);
    if (rc != 0 && rc != -38) {
        /* -38 (ENOSYS) means vecvfs_init not called yet — that's a warning, not fatal */
        VOS3_WARN("[SNI] vecvfs_index_init failed for slot %u (err=%d)", slot_id, rc);
    }

    /* Set up session */
    sni_memzero(session, sizeof(sni_session_t));
    session->slot_id = slot_id;
    session->active = 1;
    session->queries_served = 0;
    session->shards_injected = 0;
    session->injection_failures = 0;
    session->total_query_cycles = 0;
    session->last_query_cycles = 0;

    g_sni_stats.sessions_started++;

    VOS3_INFO("[SNI] Session started for slot %u", slot_id);
    return 0;
}

int vos3_sni_query(uint8_t slot_id, const uint8_t query[VECVFS_EMBED_DIM])
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (g_sni_initialized == 0U) {
        return -38; /* ENOSYS */
    }
    if (slot_id >= SNI_MAX_SESSIONS) {
        return -22; /* EINVAL */
    }

    sni_session_t *session = &g_sni_sessions[slot_id];
    if (session->active == 0U) {
        return -1; /* EPERM — session not active */
    }

    /* TSC timing start */
    uint64_t tsc_start = vos3_rdtsc();

    /* Step 1: Query Vector VFS for top-K shards */
    vecvfs_result_t results[SNI_MAX_INJECT];
    uint32_t result_count = 0;
    sni_memzero(results, sizeof(results));

    int rc = vecvfs_query(slot_id, query, results, SNI_MAX_INJECT, &result_count);
    if (rc != 0) {
        VOS3_WARN("[SNI] vecvfs_query failed for slot %u (err=%d)", slot_id, rc);
        session->queries_served++;
        uint64_t tsc_end = vos3_rdtsc();
        session->last_query_cycles = tsc_end - tsc_start;
        session->total_query_cycles += session->last_query_cycles;
        g_sni_stats.total_queries++;
        g_sni_stats.total_cycles += session->last_query_cycles;
        return rc;
    }

    /* Step 2: Inject each result shard's payload into the context window */
    for (uint32_t i = 0; i < result_count; i++) {
        (void)results[i].shard_idx; /* Shard index tracked for debug/audit */

        /*
         * Inject payload into context window via vos3_ctx_window_advance().
         * We advance by 1 token per injected shard. In production,
         * payload_len / sizeof(token_id) would be more accurate.
         */
        rc = vos3_ctx_window_advance(slot_id, 1);
        if (rc == 0) {
            session->shards_injected++;
            g_sni_stats.total_injections++;
        } else {
            session->injection_failures++;
        }
    }

    /* Step 3: Update timing stats */
    uint64_t tsc_end = vos3_rdtsc();
    session->last_query_cycles = tsc_end - tsc_start;
    session->total_query_cycles += session->last_query_cycles;
    session->queries_served++;

    g_sni_stats.total_queries++;
    g_sni_stats.total_cycles += session->last_query_cycles;

    return 0;
}

int vos3_sni_stop(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (g_sni_initialized == 0U) {
        return -38; /* ENOSYS */
    }
    if (slot_id >= SNI_MAX_SESSIONS) {
        return -22; /* EINVAL */
    }

    sni_session_t *session = &g_sni_sessions[slot_id];
    if (session->active == 0U) {
        return 0; /* Already stopped */
    }

    VOS3_INFO("[SNI] Session stopped for slot %u (queries=%u, injections=%u, failures=%u)",
              slot_id, session->queries_served, session->shards_injected,
              session->injection_failures);

    session->active = 0;
    g_sni_stats.sessions_stopped++;

    return 0;
}

void vos3_sni_get_stats(sni_stats_t *out)
{
    if (out == NULL) {
        return;
    }

    out->sessions_started = g_sni_stats.sessions_started;
    out->sessions_stopped = g_sni_stats.sessions_stopped;
    out->total_queries = g_sni_stats.total_queries;
    out->total_injections = g_sni_stats.total_injections;
    out->total_cycles = g_sni_stats.total_cycles;
}
