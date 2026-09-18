/**
 * @file scheduler.h
 * @brief VOS3 Task Scheduler
 *
 * @details Round-robin scheduler with priority support.
 *          Handles task queues, context switching, and preemption.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_SCHEDULER_H
#define VOS3_SCHEDULER_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "task.h"

/* ============================================================================
 * SCHEDULER CONFIGURATION
 * ============================================================================ */

/** @brief Default time slice in ticks */
#define VOS3_SCHED_DEFAULT_SLICE    ((uint64_t)10U)

/** @brief Time slices per priority level */
#define VOS3_SCHED_SLICE_IDLE       ((uint64_t)1U)
#define VOS3_SCHED_SLICE_LOW        ((uint64_t)5U)
#define VOS3_SCHED_SLICE_NORMAL     ((uint64_t)10U)
#define VOS3_SCHED_SLICE_HIGH       ((uint64_t)20U)
#define VOS3_SCHED_SLICE_REALTIME   ((uint64_t)50U)

/** @brief Timer frequency (Hz) - 100Hz = 10ms tick */
#define VOS3_TIMER_FREQ             ((uint32_t)100U)

/** @brief Ticks per second */
#define VOS3_TICKS_PER_SEC          VOS3_TIMER_FREQ

/** @brief Milliseconds per tick */
#define VOS3_MS_PER_TICK            ((uint64_t)(1000U / VOS3_TIMER_FREQ))

/* ============================================================================
 * SCHEDULER STATISTICS
 * ============================================================================ */

/**
 * @brief Scheduler statistics
 */
typedef struct vos3_sched_stats {
    uint64_t total_switches;        /**< Total context switches */
    uint64_t voluntary_switches;    /**< Voluntary yields */
    uint64_t preemptions;           /**< Timer preemptions */
    uint64_t idle_ticks;            /**< Ticks spent idle */
    uint64_t task_count;            /**< Current task count */
    uint64_t runnable_count;        /**< Currently runnable tasks */
    uint64_t blocked_count;         /**< Currently blocked tasks */
    uint64_t sleeping_count;        /**< Currently sleeping tasks */
} vos3_sched_stats_t;

/* ============================================================================
 * RUN QUEUE
 * ============================================================================ */

/**
 * @brief Per-priority run queue
 */
typedef struct vos3_run_queue {
    vos3_task_t* head;              /**< First task in queue */
    vos3_task_t* tail;              /**< Last task in queue */
    size_t count;                   /**< Number of tasks */
} vos3_run_queue_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the scheduler
 * @return 0 on success, negative error code on failure
 */
int vos3_sched_init(void);

/**
 * @brief Query whether the scheduler was successfully initialized.
 * @return Non-zero if vos3_sched_init() completed successfully, 0 otherwise.
 */
int vos3_sched_is_initialized(void);

/**
 * @brief Start the scheduler
 * @note This function does not return until system shutdown
 */
__attribute__((noreturn))
void vos3_sched_start(void);

/**
 * @brief Add task to scheduler
 * @param[in] task Task to add
 */
void vos3_sched_add_task(vos3_task_t* task);

/**
 * @brief Remove task from scheduler
 * @param[in] task Task to remove
 */
void vos3_sched_remove_task(vos3_task_t* task);

/**
 * @brief Schedule next task (called from timer interrupt)
 * @note Must be called with interrupts disabled
 */
void vos3_sched_tick(void);

/**
 * @brief Trigger a reschedule
 * @note Causes context switch at next safe point
 */
void vos3_sched_reschedule(void);

/** Drain deferred resources in process context with interrupts enabled.
 * Caller must hold no subsystem locks. IRQ-disabled calls are no-ops. */
void vos3_sched_process_deferred(void);

/** Mark deferred work pending on the current CPU. IRQ-safe and nonblocking. */
void vos3_sched_request_deferred(void);

/** Mark deferred work pending on a specific scheduler owner CPU. */
void vos3_sched_request_deferred_cpu(uint32_t cpu_id);

/**
 * @brief Yield CPU to another task
 * @note Voluntary context switch
 */
void vos3_sched_yield(void);

/**
 * @brief Get current running task
 * @return Current task pointer
 */
vos3_task_t* vos3_sched_current(void);

/**
 * @brief Acknowledge stack handoff from context.S, with local IRQs disabled.
 * @note Never call from the outgoing stack or a resumed C switch invocation.
 */
void vos3_sched_switch_stack_ack(void);
/* Reclaimer precondition only; not permission to abandon a continuation. */
int vos3_sched_claim_task_reap(vos3_task_t* task);
void vos3_sched_cancel_sleep(vos3_task_t* task);

/**
 * @brief Get idle task for current CPU
 * @return Idle task pointer
 */
vos3_task_t* vos3_sched_get_idle(void);

/**
 * @brief Get scheduler statistics
 * @param[out] stats Statistics output
 */
void vos3_sched_get_stats(vos3_sched_stats_t* stats);

/**
 * @brief Print scheduler statistics
 */
void vos3_sched_print_stats(void);

/**
 * @brief Check if scheduler is running
 * @return 1 if running, 0 otherwise
 */
int vos3_sched_is_running(void);

/**
 * @brief Get system uptime in ticks
 * @return Ticks since boot
 */
uint64_t vos3_sched_get_ticks(void);

/**
 * @brief Get system uptime in milliseconds
 * @return Milliseconds since boot
 */
uint64_t vos3_sched_get_uptime_ms(void);

/* ============================================================================
 * SMP SCHEDULER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Pre-create idle tasks for all APs
 *
 * Called by BSP during SMP init to avoid heap contention.
 *
 * @param[in] cpu_count  Total number of CPUs (including BSP)
 * @return 0 on success, negative error on failure
 */
int vos3_sched_create_ap_idle_tasks(uint32_t cpu_count);

/**
 * @brief Initialize scheduler for an Application Processor
 *
 * Sets up per-CPU scheduler state using pre-created idle task.
 *
 * @param[in] cpu_id  Logical CPU ID (1-255, not 0/BSP)
 * @return 0 on success, negative error on failure
 */
int vos3_sched_init_ap(uint32_t cpu_id);

/**
 * @brief Enter the scheduler loop for an Application Processor
 *
 * Enables interrupts and runs the scheduler on this CPU.
 *
 * @note This function does not return
 */
__attribute__((noreturn))
void vos3_sched_loop_ap(void);

/**
 * @brief Check if reschedule is pending for current CPU
 *
 * @return 1 if reschedule needed, 0 otherwise
 */
int vos3_sched_need_reschedule(void);
#ifdef NATIVE_SMP_TEST
void vos3_sched_request_reschedule(void);
#endif

/**
 * @brief Get task count assigned to a specific CPU
 *
 * Used for load balancing decisions.
 *
 * @param[in] cpu_id  CPU ID to query
 * @return Number of tasks assigned to that CPU
 */
uint32_t vos3_sched_get_cpu_task_count(uint32_t cpu_id);

/* ============================================================================
 * SLEEP QUEUE
 * ============================================================================ */

/**
 * @brief Add task to sleep queue
 * @param[in] task Task to sleep
 * @param[in] wake_time Tick count when to wake
 */
void vos3_sched_sleep_until(vos3_task_t* task, uint64_t wake_time);

/**
 * @brief Process sleeping tasks (called from timer)
 * @note Wakes tasks whose wake_time has passed
 */
void vos3_sched_process_sleepers(void);

/* ============================================================================
 * CONTEXT SWITCH (Assembly)
 * ============================================================================ */

/**
 * @brief Perform context switch
 * @param[in,out] old_ctx Pointer to save old context
 * @param[in] new_ctx Context to switch to
 * @note Implemented in context.S
 */
extern void vos3_context_switch(vos3_context_t** old_ctx, vos3_context_t* new_ctx);

/**
 * @brief Task entry trampoline
 * @note Implemented in context.S
 */
extern void vos3_task_entry_trampoline(void);

/**
 * @brief Idle loop
 * @note Implemented in context.S
 */
extern void vos3_idle_loop(void* arg);

/**
 * @brief Get current stack pointer
 */
extern uint64_t vos3_get_rsp(void);

/**
 * @brief Get current frame pointer
 */
extern uint64_t vos3_get_rbp(void);

/* ============================================================================
 * TIMER INTERFACE
 * ============================================================================ */

/**
 * @brief Initialize the timer for preemption
 * @param[in] frequency Timer frequency in Hz
 * @return 0 on success, negative error code on failure
 */
int vos3_timer_init(uint32_t frequency);

/**
 * @brief Timer interrupt handler
 * @note Called from IRQ0 handler
 */
void vos3_timer_handler(void);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_SCHED_OK           (0)
#define VOS3_SCHED_ERR_NOMEM    (-1)
#define VOS3_SCHED_ERR_INVALID  (-2)
#define VOS3_SCHED_ERR_NOTINIT  (-3)
#define VOS3_SCHED_ERR_RUNNING  (-4)

/* ============================================================================
 * Cyber overlay (Stage 3) — SCHED_CORE cookie API
 *
 * Implementations live in kernel/src/sched/core_cookie.c (Z3-proven O(1)
 * sibling-table SMT isolation). The cookie partitions tasks into trust
 * domains; two tasks may co-execute on sibling logical CPUs of the same
 * physical core only if their cookies match (or either is 0 = neutral).
 * ============================================================================ */

int      vos3_sched_set_cookie(vos3_task_t *task, uint64_t cookie);
uint64_t vos3_sched_get_cookie(const vos3_task_t *task);
int      vos3_sched_sibling_compatible(uint32_t cpu_id, const vos3_task_t *candidate);
uint64_t vos3_sched_cookie_rejections(void);
void     vos3_sched_core_init_topology(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SCHEDULER_H */
