/**
 * @file dispatcher.h
 * @brief VOS3 Multi-Agent Dispatcher — Phase 3
 *
 * @details Kernel-managed agent dispatcher with work-stealing routing,
 *          per-agent work queues, and predictive memory prefetch.
 *
 * @version 1.0.0
 * @date 2026-03-23
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_DISPATCHER_H
#define VOS3_DISPATCHER_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "task.h"
#include "sync.h"

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

#define VOS3_DISPATCH_MAX_AGENTS    64
#define VOS3_DISPATCH_QUEUE_DEPTH   128   /* Per-agent, power of 2 */
#define VOS3_DISPATCH_QUEUE_MASK    (VOS3_DISPATCH_QUEUE_DEPTH - 1U)

/* Syscall numbers 490-496 are defined in syscall.h enum */

/* ============================================================================
 * AGENT CAPABILITY FLAGS
 * ============================================================================ */

#define VOS3_AGENT_CAP_COMPUTE      (1U << 0)   /**< General compute */
#define VOS3_AGENT_CAP_INFERENCE    (1U << 1)   /**< Model inference */
#define VOS3_AGENT_CAP_SEARCH       (1U << 2)   /**< Graph/tree search */
#define VOS3_AGENT_CAP_IO           (1U << 3)   /**< I/O intensive */

/* ============================================================================
 * AGENT STATUS
 * ============================================================================ */

typedef enum {
    VOS3_AGENT_EMPTY   = 0,    /**< Slot not in use */
    VOS3_AGENT_IDLE    = 1,    /**< Registered, no pending work */
    VOS3_AGENT_BUSY    = 2,    /**< Processing a work item */
    VOS3_AGENT_FULL    = 3     /**< Queue at capacity */
} vos3_agent_status_t;

/* ============================================================================
 * WORK ITEM
 * ============================================================================ */

typedef struct {
    uint64_t    item_id;        /**< Unique work item ID (monotonic) */
    uint32_t    type;           /**< Application-defined task type */
    uint32_t    priority;       /**< 0=low, 3=realtime */
    uint64_t    payload_addr;   /**< User-space address of input data */
    uint64_t    payload_size;   /**< Input data size */
    uint64_t    result_addr;    /**< User-space address for output */
    uint64_t    result_size;    /**< Max output size */
    uint64_t    submitted_at;   /**< Timestamp (uptime_ms) */
    uint32_t    requester_tid;  /**< Who submitted this */
    uint32_t    assigned_agent; /**< Agent slot ID (set by dispatcher) */
} vos3_dispatch_item_t;

/* ============================================================================
 * PER-AGENT DESCRIPTOR
 * ============================================================================ */

typedef struct {
    /* Identity */
    uint32_t            slot_id;        /**< Index in agent table */
    vos3_tid_t          tid;            /**< Kernel task ID */
    char                name[32];       /**< Agent name */
    uint32_t            capabilities;   /**< Capability bitmask */
    vos3_agent_status_t status;         /**< Current status */

    /* Per-agent work queue (circular buffer) */
    vos3_dispatch_item_t queue[VOS3_DISPATCH_QUEUE_DEPTH];
    volatile uint32_t   q_head;         /**< Next write position */
    volatile uint32_t   q_tail;         /**< Next read position */
    vos3_spinlock_t     q_lock;         /**< Queue lock */

    /* Prefetch SHM (predictive memory) */
    int32_t             prefetch_shm_id; /**< -1 if none */

    /* Statistics */
    uint64_t            tasks_completed;
    uint64_t            tasks_stolen;    /**< Items received via work-steal */
    uint64_t            total_busy_ms;   /**< Cumulative processing time */
} vos3_dispatch_agent_t;

/* ============================================================================
 * DISPATCHER STATUS (returned by AGENT_STATUS syscall)
 * ============================================================================ */

typedef struct {
    uint32_t    active_agents;      /**< Registered agents */
    uint32_t    idle_agents;        /**< Agents with empty queues */
    uint32_t    total_pending;      /**< Sum of all queue depths */
    uint64_t    total_dispatched;   /**< Lifetime items dispatched */
    uint64_t    total_completed;    /**< Lifetime items completed */
    uint64_t    total_steals;       /**< Lifetime work-steal events */
} vos3_dispatch_status_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize the dispatcher subsystem
 * @return 0 on success, negative error code on failure
 */
int vos3_dispatcher_init(void);

/**
 * @brief Dispatch a work item to the best available agent
 * @param[in] item Work item to dispatch
 * @return item_id (>0) on success, negative error on failure
 */
int vos3_dispatch_task(const vos3_dispatch_item_t* item);

/**
 * @brief Request a deferred balance check (ISR-safe, no locks).
 * Call from timer ISR; the actual balance runs in process context.
 */
void vos3_dispatch_balance_request(void);

/**
 * @brief Check and clear the deferred balance flag.
 * @return 1 if balance was pending, 0 otherwise.
 */
int vos3_dispatch_balance_pending(void);

/**
 * @brief Balance check — adjusts agent priorities based on queue depth.
 * Must be called from process context (not ISR).
 */
void vos3_dispatch_balance_check(void);

/**
 * @brief Predictive memory prefetch — create SHM for agent data
 * @param[in] path File path (used for naming)
 * @param[in] flags Capability flags for sizing
 * @return SHM ID on success, negative error on failure
 */
int vos3_vfs_prefetch(const char* path, uint32_t flags);

/**
 * @brief Kill all registered agents, drain queues, scrub model memory.
 * @return Number of agents killed (>= 0)
 */
int vos3_dispatcher_kill_all(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_DISPATCHER_H */
