/**
 * @file agent_mesh.h
 * @brief VOS3 Agentic Mesh — Multi-Agent Orchestration Protocol
 *
 * @details Trust-scored multi-agent dispatch with capability-based
 *          routing, Vector VFS blackboard, and ISC transport. Each
 *          model slot is a mesh node; COORDINATOR (slot 0) arbitrates
 *          task routing and consensus approval.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1: Agentic Mesh & vScreen Perception
 */

#ifndef VOS3_AGENT_MESH_H
#define VOS3_AGENT_MESH_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum mesh participants (matches VOS3_MODEL_SLOT_MAX) */
#define MESH_MAX_SLOTS          4U

/** @brief Pending task queue depth */
#define MESH_MAX_PENDING_TASKS  16U

/* Trust score parameters */
#define MESH_TRUST_MAX          1000U   /**< Maximum trust score */
#define MESH_TRUST_INITIAL      500U    /**< Starting trust for new slots */
#define MESH_TRUST_DECAY_RATE   1U      /**< Per-tick decay amount */
#define MESH_TRUST_PENALTY      50U     /**< Penalty for violation/failure */
#define MESH_TRUST_REWARD       5U      /**< Reward for successful completion */
#define MESH_TRUST_MIN_DISPATCH 100U    /**< Minimum trust to accept tasks */

/* Task types */
#define MESH_TASK_INFERENCE     0U      /**< Model inference request */
#define MESH_TASK_TOOL_CALL     1U      /**< External tool invocation */
#define MESH_TASK_CODE_GEN      2U      /**< Code generation */
#define MESH_TASK_VISION        3U      /**< Vision/image processing */
#define MESH_TASK_MEMORY_QUERY  4U      /**< Semantic memory query */
#define MESH_TASK_PEER          5U      /**< Peer-to-peer (worker→worker) */

/* Task status */
#define MESH_STATUS_FREE        0U      /**< Slot empty */
#define MESH_STATUS_PENDING     1U      /**< Awaiting dispatch */
#define MESH_STATUS_DISPATCHED  2U      /**< Sent to target */
#define MESH_STATUS_COMPLETED   3U      /**< Successfully completed */
#define MESH_STATUS_FAILED      4U      /**< Execution failed */
#define MESH_STATUS_REJECTED    5U      /**< Rejected by trust/caps check */

/** @brief Embedding dimension for task descriptors (matches VECVFS_EMBED_DIM) */
#define MESH_EMBED_DIM          64U

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Mesh task descriptor (88 bytes — fits ISC mailbox).
 */
typedef struct mesh_task {
    uint32_t    task_id;                        /**< Unique task identifier */
    uint8_t     type;                           /**< MESH_TASK_* constant */
    uint8_t     priority;                       /**< 0=low, 255=high */
    uint8_t     source_slot;                    /**< Requesting slot */
    uint8_t     target_slot;                    /**< Target slot (0xFF=auto) */
    uint8_t     payload_embedding[MESH_EMBED_DIM]; /**< Task semantic embedding */
    uint64_t    deadline_cycles;                /**< TSC deadline (0=no deadline) */
    uint8_t     status;                         /**< MESH_STATUS_* */
    uint8_t     result_code;                    /**< 0=ok, else errno */
    uint8_t     _pad[6];                        /**< Pad to 88 bytes */
} mesh_task_t;

/**
 * @brief Mesh task result.
 */
typedef struct mesh_result {
    uint32_t    task_id;                        /**< Matching task_id */
    uint8_t     result_code;                    /**< 0=success, else errno */
    uint8_t     _pad[3];
    uint8_t     result_embedding[MESH_EMBED_DIM]; /**< Result semantic embedding */
    uint8_t     result_payload[256];            /**< Raw result payload */
    uint32_t    payload_len;                    /**< Bytes used in result_payload */
} mesh_result_t;

/**
 * @brief Per-slot trust state.
 */
typedef struct mesh_trust {
    uint32_t    score;              /**< 0 - MESH_TRUST_MAX */
    uint32_t    violations;         /**< Cumulative violations */
    uint32_t    completions;        /**< Successful task completions */
    uint32_t    rejections;         /**< Tasks rejected (trust/caps) */
    uint64_t    last_decay_tick;    /**< TSC of last decay application */
} mesh_trust_t;

/**
 * @brief Global mesh state.
 */
typedef struct mesh_state {
    mesh_task_t     pending[MESH_MAX_PENDING_TASKS];    /**< Task queue */
    mesh_trust_t    trust[MESH_MAX_SLOTS];              /**< Per-slot trust */
    uint32_t        next_task_id;                       /**< Monotonic ID counter */
    uint32_t        active_tasks;                       /**< Tasks in-flight */
    uint8_t         initialized;                        /**< 1 = mesh ready */
} mesh_state_t;

/**
 * @brief Aggregate mesh statistics.
 */
typedef struct mesh_stats {
    uint32_t    tasks_dispatched;    /**< Total tasks dispatched */
    uint32_t    tasks_completed;     /**< Successful completions */
    uint32_t    tasks_failed;        /**< Failed tasks */
    uint32_t    tasks_rejected;      /**< Trust/caps rejections */
    uint32_t    peer_routes;         /**< Worker-to-worker via coordinator */
    uint64_t    total_dispatch_cycles;  /**< Cumulative dispatch latency */
} mesh_stats_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize the Agentic Mesh (zero state, set initial trust).
 * @return 0 on success
 */
int mesh_init(void);

/**
 * @brief Dispatch a task to the mesh.
 *
 * @details Validates source capabilities, finds optimal target,
 *          checks trust threshold, sends via ISC, stores embedding
 *          in Vector VFS blackboard.
 *
 * @param task  Task descriptor (target_slot=0xFF for auto-routing)
 * @return 0 on success, -EPERM, -ENOSPC, -EINVAL on failure
 */
int mesh_dispatch(const mesh_task_t *task);

/**
 * @brief Report task result and update trust.
 *
 * @param task_id   ID of completed task
 * @param result    Result descriptor
 * @return 0 on success, -EINVAL if task_id not found
 */
int mesh_result(uint32_t task_id, const mesh_result_t *result);

/**
 * @brief Query a slot's current trust score.
 *
 * @param slot_id    Model slot
 * @param out_score  Output: trust score (0-1000)
 * @return 0 on success, -EINVAL on bad slot
 */
int mesh_trust_score(uint8_t slot_id, uint32_t *out_score);

/**
 * @brief Penalize a slot's trust score.
 *
 * @param slot_id  Model slot
 * @param amount   Points to deduct (clamped at 0)
 * @return 0 on success, -EINVAL on bad slot
 */
int mesh_trust_penalize(uint8_t slot_id, uint32_t amount);

/**
 * @brief Reward a slot's trust score.
 *
 * @param slot_id  Model slot
 * @param amount   Points to add (capped at MESH_TRUST_MAX)
 * @return 0 on success, -EINVAL on bad slot
 */
int mesh_trust_reward(uint8_t slot_id, uint32_t amount);

/**
 * @brief Apply periodic trust decay.
 *
 * @param current_tick  Current TSC value for gating decay frequency
 */
void mesh_trust_tick(uint64_t current_tick);

/**
 * @brief Get aggregate mesh statistics.
 * @param out  Output stats structure
 */
void mesh_get_stats(mesh_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_AGENT_MESH_H */
