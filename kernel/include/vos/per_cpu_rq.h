/**
 * @file per_cpu_rq.h
 * @brief VOS3 Per-CPU Run Queues + Work-Stealing Scheduler Extension
 *
 * @details Replaces the global run queue with per-CPU queues for SMP
 *          scalability. Each CPU has its own set of priority run queues
 *          protected by a per-CPU spinlock, eliminating the global
 *          scheduler lock contention.
 *
 *          Work-stealing: When a CPU's queues are empty, it steals from
 *          the busiest CPU's lowest-priority queue.
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.6 — SMP Scale-Out
 */

#ifndef VOS3_PER_CPU_RQ_H
#define VOS3_PER_CPU_RQ_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "task.h"
#include "sync.h"
#include "scheduler.h"

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief Maximum CPUs supported */
#define VOS3_PCPU_MAX_CPUS      64U

/** @brief Work-steal threshold: steal when local queue < this */
#define VOS3_PCPU_STEAL_THRESH  2U

/** @brief Load balance interval in ticks (every 100ms at 100Hz) */
#define VOS3_PCPU_BALANCE_INTERVAL  10U

/* ============================================================================
 * PER-CPU RUN QUEUE
 * ============================================================================ */

/** @brief Per-CPU scheduler state */
typedef struct vos3_pcpu_rq {
    /** Per-priority run queues (local to this CPU) */
    vos3_run_queue_t queues[VOS3_PRIORITY_COUNT];

    /** Total runnable tasks on this CPU */
    uint32_t nr_running;

    /** CPU index */
    uint32_t cpu_id;

    /** Per-CPU lock (replaces global g_sched_lock for this CPU) */
    vos3_spinlock_t lock;

    /** Statistics */
    uint64_t switches;          /**< Context switches on this CPU */
    uint64_t steals_from;       /**< Tasks stolen FROM this CPU */
    uint64_t steals_to;         /**< Tasks stolen TO this CPU */
    uint64_t idle_ticks;        /**< Ticks spent idle */

    /** Load tracking (exponentially weighted moving average) */
    uint32_t load_avg;          /**< Current load average (fixed-point × 1024) */
    uint64_t last_balance_tick; /**< Last load balance timestamp */

    /** Cache line padding to prevent false sharing */
    uint8_t  _pad[16];
} __attribute__((aligned(64))) vos3_pcpu_rq_t;

/** @brief Per-CPU run queue statistics */
typedef struct vos3_pcpu_stats {
    uint32_t cpu_id;
    uint32_t nr_running;
    uint64_t switches;
    uint64_t steals_from;
    uint64_t steals_to;
    uint64_t idle_ticks;
    uint32_t load_avg;
    uint32_t queue_lengths[VOS3_PRIORITY_COUNT];
} vos3_pcpu_stats_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize per-CPU run queues
 *
 * Must be called after vos3_sched_init() and SMP discovery.
 *
 * @param[in] num_cpus Number of online CPUs
 * @return 0 on success, -1 on failure
 */
int vos3_pcpu_init(uint32_t num_cpus);

/**
 * @brief Check if per-CPU run queues are active
 *
 * @return 1 if active, 0 if still using global queues
 */
int vos3_pcpu_active(void);

/**
 * @brief Enqueue a task to a CPU's run queue
 *
 * Task is added to the specified CPU's queue for its priority level.
 * If cpu_id is -1, selects the least-loaded CPU.
 *
 * @param[in] task   Task to enqueue
 * @param[in] cpu_id Target CPU (-1 for auto-select)
 */
void vos3_pcpu_enqueue(vos3_task_t *task, int cpu_id);

/**
 * @brief Dequeue the highest-priority task from the current CPU
 *
 * Scans priority queues from highest to lowest. If all local queues
 * are empty, attempts work-stealing from the busiest CPU.
 *
 * @param[in] cpu_id CPU requesting a task
 * @return Next task to run, or NULL if only idle
 */
vos3_task_t *vos3_pcpu_dequeue(uint32_t cpu_id);

/**
 * @brief Remove a specific task from its per-CPU queue
 *
 * Used when a task blocks, exits, or changes state.
 *
 * @param[in] task Task to remove
 */
void vos3_pcpu_remove(vos3_task_t *task);

/**
 * @brief Perform periodic load balancing across CPUs
 *
 * Called from the timer tick handler. Migrates tasks from
 * overloaded CPUs to underloaded ones.
 *
 * @param[in] current_cpu CPU calling the balance check
 */
void vos3_pcpu_balance(uint32_t current_cpu);

/**
 * @brief Find the least-loaded CPU for task placement
 *
 * @return CPU index of the least-loaded CPU
 */
uint32_t vos3_pcpu_find_least_loaded(void);

/**
 * @brief Get per-CPU statistics
 *
 * @param[in]  cpu_id CPU to query
 * @param[out] stats  Statistics output
 * @return 0 on success, -1 if invalid CPU
 */
int vos3_pcpu_get_stats(uint32_t cpu_id, vos3_pcpu_stats_t *stats);

/**
 * @brief Get the number of runnable tasks on a specific CPU
 *
 * @param[in] cpu_id CPU to query
 * @return Number of runnable tasks
 */
uint32_t vos3_pcpu_nr_running(uint32_t cpu_id);

/**
 * @brief Get total number of online CPUs
 *
 * @return Number of online CPUs
 */
uint32_t vos3_pcpu_num_cpus(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_PER_CPU_RQ_H */
