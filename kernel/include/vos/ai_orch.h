/**
 * @file ai_orch.h
 * @brief VOS3 Hybrid Orchestration — NPU/GPU/CPU Device Routing & Ghost Streaming
 *
 * @details Phase 2.3: Routes draft model to NPU and target model to GPU for
 *          maximum hardware parallelism. Streams unverified "ghost tokens"
 *          immediately via STREAM_SPEC VBus frames for zero-latency perception.
 *
 *          Device routing priority:
 *            Draft model  → NPU (preferred) → CPU (fallback)
 *            Target model → GPU (preferred) → CPU (fallback)
 *
 *          Ghost token lifecycle:
 *            1. Draft generates K tokens → streamed as GHOST (unverified)
 *            2. Target verifies batch → mismatches streamed as VERIFY (corrected)
 *            3. Frontend replaces ghost tokens with verified output
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2.3: Hybrid Orchestration & Speculative VBus
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_AI_ORCH_H
#define VOS3_AI_ORCH_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "accel.h"     /* vos3_accel_type_t, vos3_accel_get_device() */
#include "ai_spec.h"   /* vos3_spec_batch_t, vos3_spec_result_t */

/* ============================================================================
 * ORCHESTRATION CONSTANTS
 * ============================================================================ */

/** @brief Maximum concurrent orchestration sessions */
#define VOS3_ORCH_MAX_SESSIONS      4U

/** @brief Ghost (unverified) token flag — set on draft tokens before verification */
#define VOS3_ORCH_GHOST_FLAG        0x10U

/** @brief Verified replacement token flag — set on corrected tokens after verification */
#define VOS3_ORCH_VERIFY_FLAG       0x20U

/** @brief Final token in speculative batch — signals end of ghost/verify sequence */
#define VOS3_ORCH_FINAL_FLAG        0x08U

/** @brief Latency budget in CPU cycles (50K cycles ≈ 16µs at 3GHz) */
#define VOS3_ORCH_LATENCY_BUDGET    50000ULL

/* ============================================================================
 * ORCHESTRATION SESSION STATE
 * ============================================================================ */

/**
 * @brief Per-session hybrid orchestration state
 *
 * @details Tracks the draft/target slot pair, device assignments, ghost token
 *          streaming state, and per-session performance counters. Each session
 *          binds one draft model slot to one target model slot.
 *
 *          sizeof must be < 256 bytes (4 cache lines) and aligned to 8 bytes.
 */
typedef struct vos3_orch_session {
    uint8_t             draft_slot;             /**< Draft model slot ID */
    uint8_t             target_slot;            /**< Target model slot ID */
    vos3_accel_type_t   draft_device;           /**< Draft device type (NPU/GPU/CPU) */
    vos3_accel_type_t   target_device;          /**< Target device type (GPU/NPU/CPU) */
    uint32_t            draft_device_id;        /**< Draft accel registry ID */
    uint32_t            target_device_id;       /**< Target accel registry ID */
    uint8_t             active;                 /**< 1 = session is live */
    uint8_t             ghost_streaming;        /**< 1 = ghost tokens enabled */
    uint8_t             _pad[6];                /**< Alignment padding */
    uint32_t            ghost_tokens_sent;      /**< Ghost tokens streamed this session */
    uint32_t            ghost_tokens_replaced;  /**< Ghost tokens replaced by verification */
    uint32_t            batches_dispatched;     /**< Total orchestration batches */
    uint64_t            last_dispatch_tsc;      /**< TSC of last dispatch */
} vos3_orch_session_t;

/* ============================================================================
 * ORCHESTRATION GLOBAL STATISTICS
 * ============================================================================ */

/**
 * @brief Global orchestration statistics across all sessions
 */
typedef struct vos3_orch_stats {
    uint32_t    sessions_started;       /**< Total sessions started */
    uint32_t    sessions_completed;     /**< Total sessions completed */
    uint32_t    npu_dispatches;         /**< Total dispatches routed to NPU */
    uint32_t    gpu_dispatches;         /**< Total dispatches routed to GPU */
    uint32_t    cpu_fallbacks;          /**< Total dispatches fell back to CPU */
    uint32_t    ghost_total_sent;       /**< Lifetime ghost tokens sent */
    uint32_t    ghost_total_replaced;   /**< Lifetime ghost tokens replaced */
    uint32_t    _pad;                   /**< Alignment padding */
    uint64_t    total_cycles;           /**< Lifetime cycles spent in dispatch */
} vos3_orch_stats_t;

/* ============================================================================
 * ORCHESTRATION API
 * ============================================================================ */

/**
 * @brief Initialize the hybrid orchestration subsystem
 * @return 0 on success
 */
int vos3_orch_init(void);

/**
 * @brief Start a new hybrid orchestration session
 *
 * @details Validates slots, finds a free session, routes draft to NPU and
 *          target to GPU (with CPU fallback), configures the speculative
 *          engine via vos3_spec_configure().
 *
 * @param[in] draft_slot   Draft (small/fast) model slot ID
 * @param[in] target_slot  Target (large/accurate) model slot ID
 * @return Session ID (0..VOS3_ORCH_MAX_SESSIONS-1) on success, negative errno on failure
 */
int vos3_orch_start(uint8_t draft_slot, uint8_t target_slot);

/**
 * @brief Dispatch one orchestration round
 *
 * @details Calls spec_generate on draft, optionally streams ghost tokens,
 *          then calls spec_verify on target and sends verified replacements.
 *
 * @param[in] session_id  Session ID from vos3_orch_start()
 * @param[in] input_token Input token to start speculation from
 * @return 0 on success, negative errno on failure
 */
int vos3_orch_dispatch(uint8_t session_id, uint32_t input_token);

/**
 * @brief Stop an orchestration session
 * @param[in] session_id  Session to stop
 * @return 0 on success, negative errno on failure
 */
int vos3_orch_stop(uint8_t session_id);

/**
 * @brief Get global orchestration statistics
 * @param[out] out  Statistics structure to fill
 */
void vos3_orch_get_stats(vos3_orch_stats_t *out);

/**
 * @brief Enable or disable ghost token streaming for a session
 * @param[in] session_id  Session to configure
 * @param[in] enable      1 = enable ghost streaming, 0 = disable
 * @return 0 on success, negative errno on failure
 */
int vos3_orch_set_ghost(uint8_t session_id, uint8_t enable);

/* ============================================================================
 * NPU AFFINITY API (npu_affinity.c)
 * ============================================================================ */

/**
 * @brief Pin a KV-cache page to NPU SRAM
 * @param[in] slot_id   Model slot
 * @param[in] page_idx  Page index within slot's KV pages
 * @return 0 on success, negative errno on failure
 */
int vos3_npu_affinity_pin(uint8_t slot_id, uint32_t page_idx);

/**
 * @brief Unpin a KV-cache page from NPU SRAM
 * @param[in] slot_id   Model slot
 * @param[in] page_idx  Page index
 * @return 0 on success, negative errno on failure
 */
int vos3_npu_affinity_unpin(uint8_t slot_id, uint32_t page_idx);

/**
 * @brief Pin a range of KV-cache pages to NPU SRAM
 * @param[in] slot_id  Model slot
 * @param[in] start    First page index
 * @param[in] count    Number of pages to pin
 * @return 0 on success, negative errno on failure
 */
int vos3_npu_affinity_pin_range(uint8_t slot_id, uint32_t start, uint32_t count);

/**
 * @brief Query NPU pin status for a slot
 * @param[in]  slot_id  Model slot
 * @param[out] pinned   Number of pinned pages
 * @param[out] total    Total KV pages
 * @return 0 on success, negative errno on failure
 */
int vos3_npu_affinity_status(uint8_t slot_id, uint32_t *pinned, uint32_t *total);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_AI_ORCH_H */
