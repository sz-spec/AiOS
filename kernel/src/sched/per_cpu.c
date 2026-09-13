/**
 * @file per_cpu.c
 * @brief VOS3 Per-CPU Run Queues + Work-Stealing Scheduler Extension
 *
 * @details Implements per-CPU run queues for SMP scalability (up to 64 CPUs).
 *          Each CPU has its own set of priority queues protected by a local
 *          spinlock, eliminating the global scheduler lock bottleneck.
 *
 *          Work-stealing: When a CPU's queues are empty, it scans other CPUs
 *          and steals the lowest-priority task from the busiest queue.
 *
 *          Load balancing: Periodic migration of tasks from overloaded CPUs
 *          to underloaded ones, triggered from the timer tick handler.
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.6 — SMP Scale-Out
 */

#include "../../include/vos/per_cpu_rq.h"
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief Per-CPU run queue array (cache-line aligned) */
static vos3_pcpu_rq_t g_pcpu_rq[VOS3_PCPU_MAX_CPUS]
    __attribute__((aligned(64)));

/** @brief Number of online CPUs */
static uint32_t g_num_cpus = 0;

/** @brief Per-CPU RQ initialized flag */
static uint32_t g_pcpu_initialized = 0;

/* ============================================================================
 * LOCAL RUN QUEUE HELPERS (duplicated from scheduler.c pattern)
 * ============================================================================ */

static void pcpu_rq_init(vos3_run_queue_t *rq)
{
    rq->head  = NULL;
    rq->tail  = NULL;
    rq->count = 0U;
}

static void pcpu_rq_enqueue(vos3_run_queue_t *rq, vos3_task_t *task)
{
    task->next = NULL;
    task->prev = rq->tail;

    if (rq->tail != NULL) {
        rq->tail->next = task;
    } else {
        rq->head = task;
    }

    rq->tail = task;
    rq->count++;
    task->flags |= VOS3_TASK_FLAG_QUEUED;
}

static vos3_task_t *pcpu_rq_dequeue(vos3_run_queue_t *rq)
{
    vos3_task_t *task = rq->head;
    if (task == NULL) return NULL;

    rq->head = task->next;
    if (rq->head != NULL) {
        rq->head->prev = NULL;
    } else {
        rq->tail = NULL;
    }

    task->next = NULL;
    task->prev = NULL;
    rq->count--;
    task->flags &= ~VOS3_TASK_FLAG_QUEUED;

    return task;
}

/**
 * @brief Remove a specific task from a run queue
 * @return 1 if found and removed, 0 if not found
 */
static int pcpu_rq_remove(vos3_run_queue_t *rq, vos3_task_t *task)
{
    if (rq->count == 0) return 0;

    /* Check if this task is in this queue */
    vos3_task_t *cur = rq->head;
    while (cur != NULL) {
        if (cur == task) {
            /* Unlink */
            if (cur->prev != NULL) {
                cur->prev->next = cur->next;
            } else {
                rq->head = cur->next;
            }

            if (cur->next != NULL) {
                cur->next->prev = cur->prev;
            } else {
                rq->tail = cur->prev;
            }

            cur->next = NULL;
            cur->prev = NULL;
            rq->count--;
            task->flags &= ~VOS3_TASK_FLAG_QUEUED;
            return 1;
        }
        cur = cur->next;
    }

    return 0;
}

/**
 * @brief Steal the tail (lowest priority task) from a queue
 * @return Stolen task, or NULL if queue empty
 */
static vos3_task_t *pcpu_rq_steal_tail(vos3_run_queue_t *rq)
{
    vos3_task_t *task = rq->tail;
    if (task == NULL) return NULL;

    if (task->prev != NULL) {
        task->prev->next = NULL;
        rq->tail = task->prev;
    } else {
        rq->head = NULL;
        rq->tail = NULL;
    }

    task->next = NULL;
    task->prev = NULL;
    rq->count--;
    task->flags &= ~VOS3_TASK_FLAG_QUEUED;

    return task;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vos3_pcpu_init(uint32_t num_cpus)
{
    if (num_cpus == 0 || num_cpus > VOS3_PCPU_MAX_CPUS) {
        return -1;
    }

    g_num_cpus = num_cpus;

    for (uint32_t cpu = 0; cpu < num_cpus; cpu++) {
        vos3_pcpu_rq_t *rq = &g_pcpu_rq[cpu];

        rq->cpu_id     = cpu;
        rq->nr_running = 0;
        rq->lock       = VOS3_SPINLOCK_INIT;
        rq->switches   = 0;
        rq->steals_from = 0;
        rq->steals_to  = 0;
        rq->idle_ticks  = 0;
        rq->load_avg   = 0;
        rq->last_balance_tick = 0;

        for (uint32_t p = 0; p < VOS3_PRIORITY_COUNT; p++) {
            pcpu_rq_init(&rq->queues[p]);
        }
    }

    g_pcpu_initialized = 1;

    vos3_console_printf("[PCPU] Per-CPU run queues initialized for %u CPUs\n",
                        num_cpus);
    return 0;
}

int vos3_pcpu_active(void)
{
    return g_pcpu_initialized ? 1 : 0;
}

void vos3_pcpu_enqueue(vos3_task_t *task, int cpu_id)
{
    if (!g_pcpu_initialized || task == NULL) return;

    /* Auto-select least loaded CPU if -1 */
    uint32_t target;
    if (cpu_id < 0 || (uint32_t)cpu_id >= g_num_cpus) {
        target = vos3_pcpu_find_least_loaded();
    } else {
        target = (uint32_t)cpu_id;
    }

    /* Determine priority queue index */
    uint32_t prio = (uint32_t)task->priority;
    if (prio >= VOS3_PRIORITY_COUNT) {
        prio = VOS3_PRIORITY_NORMAL;
    }

    vos3_pcpu_rq_t *rq = &g_pcpu_rq[target];

    vos3_spinlock_lock(&rq->lock);
    pcpu_rq_enqueue(&rq->queues[prio], task);
    rq->nr_running++;
    vos3_spinlock_unlock(&rq->lock);

    /* Store CPU affinity hint in task for migration tracking */
    task->cpu_id = target;
}

vos3_task_t *vos3_pcpu_dequeue(uint32_t cpu_id)
{
    if (!g_pcpu_initialized || cpu_id >= g_num_cpus) return NULL;

    vos3_pcpu_rq_t *rq = &g_pcpu_rq[cpu_id];
    vos3_task_t *task = NULL;

    vos3_spinlock_lock(&rq->lock);

    /* Scan priority queues from highest to lowest */
    for (int p = VOS3_PRIORITY_COUNT - 1; p >= 0; p--) {
        if (rq->queues[p].count > 0) {
            task = pcpu_rq_dequeue(&rq->queues[p]);
            if (task != NULL) {
                rq->nr_running--;
                rq->switches++;
                vos3_spinlock_unlock(&rq->lock);
                return task;
            }
        }
    }

    vos3_spinlock_unlock(&rq->lock);

    /* Local queues empty — attempt work-stealing */
    uint32_t busiest_cpu = cpu_id;
    uint32_t busiest_load = 0;

    /* Find the busiest CPU (excluding ourselves) */
    for (uint32_t c = 0; c < g_num_cpus; c++) {
        if (c == cpu_id) continue;
        uint32_t load = g_pcpu_rq[c].nr_running;
        if (load > busiest_load) {
            busiest_load = load;
            busiest_cpu = c;
        }
    }

    if (busiest_load < VOS3_PCPU_STEAL_THRESH || busiest_cpu == cpu_id) {
        return NULL;  /* Nobody worth stealing from */
    }

    /* Steal from the busiest CPU's lowest-priority non-empty queue */
    vos3_pcpu_rq_t *victim = &g_pcpu_rq[busiest_cpu];

    vos3_spinlock_lock(&victim->lock);

    for (uint32_t p = 0; p < VOS3_PRIORITY_COUNT; p++) {
        if (victim->queues[p].count > 0) {
            task = pcpu_rq_steal_tail(&victim->queues[p]);
            if (task != NULL) {
                victim->nr_running--;
                victim->steals_from++;
                vos3_spinlock_unlock(&victim->lock);

                /* Update stolen task's CPU affinity */
                task->cpu_id = cpu_id;

                /* Track stats on receiver */
                rq->steals_to++;
                rq->switches++;

                return task;
            }
        }
    }

    vos3_spinlock_unlock(&victim->lock);
    return NULL;
}

void vos3_pcpu_remove(vos3_task_t *task)
{
    if (!g_pcpu_initialized || task == NULL) return;

    /* Check the task's last known CPU first */
    uint32_t hint_cpu = task->cpu_id;
    if (hint_cpu < g_num_cpus) {
        vos3_pcpu_rq_t *rq = &g_pcpu_rq[hint_cpu];
        uint32_t prio = (uint32_t)task->priority;
        if (prio >= VOS3_PRIORITY_COUNT) prio = VOS3_PRIORITY_NORMAL;

        vos3_spinlock_lock(&rq->lock);
        if (pcpu_rq_remove(&rq->queues[prio], task)) {
            rq->nr_running--;
            vos3_spinlock_unlock(&rq->lock);
            return;
        }
        vos3_spinlock_unlock(&rq->lock);
    }

    /* Fallback: search all CPUs and all priority levels */
    for (uint32_t c = 0; c < g_num_cpus; c++) {
        vos3_pcpu_rq_t *rq = &g_pcpu_rq[c];
        vos3_spinlock_lock(&rq->lock);
        for (uint32_t p = 0; p < VOS3_PRIORITY_COUNT; p++) {
            if (pcpu_rq_remove(&rq->queues[p], task)) {
                rq->nr_running--;
                vos3_spinlock_unlock(&rq->lock);
                return;
            }
        }
        vos3_spinlock_unlock(&rq->lock);
    }
}

void vos3_pcpu_balance(uint32_t current_cpu)
{
    if (!g_pcpu_initialized || g_num_cpus <= 1) return;

    vos3_pcpu_rq_t *my_rq = &g_pcpu_rq[current_cpu];

    /* Only balance periodically */
    /* Note: we don't have direct tick access here, but callers
     * should rate-limit calls to every VOS3_PCPU_BALANCE_INTERVAL ticks */

    /* Update load average (exponential moving average, period ~1s) */
    /* load_avg = (load_avg * 7 + nr_running * 1024) / 8 */
    my_rq->load_avg = (my_rq->load_avg * 7 + my_rq->nr_running * 1024) / 8;

    /* Find imbalance */
    uint32_t max_load = 0, max_cpu = current_cpu;
    uint32_t min_load = 0xFFFFFFFFU, min_cpu = current_cpu;

    for (uint32_t c = 0; c < g_num_cpus; c++) {
        uint32_t load = g_pcpu_rq[c].nr_running;
        if (load > max_load) { max_load = load; max_cpu = c; }
        if (load < min_load) { min_load = load; min_cpu = c; }
    }

    /* Only rebalance if significant imbalance (>2 task difference) */
    if (max_load <= min_load + 2) return;

    /* We only migrate if WE are the max-loaded CPU (avoids thundering herd) */
    if (max_cpu != current_cpu) return;

    /* Migrate one task from our lowest-priority queue to the min CPU */
    vos3_task_t *migrant = NULL;

    vos3_spinlock_lock(&my_rq->lock);
    for (uint32_t p = 0; p < VOS3_PRIORITY_COUNT; p++) {
        if (my_rq->queues[p].count > 1) {  /* Keep at least 1 task per priority */
            migrant = pcpu_rq_steal_tail(&my_rq->queues[p]);
            if (migrant != NULL) {
                my_rq->nr_running--;
                break;
            }
        }
    }
    vos3_spinlock_unlock(&my_rq->lock);

    if (migrant == NULL) return;

    /* Place on target CPU */
    uint32_t prio = (uint32_t)migrant->priority;
    if (prio >= VOS3_PRIORITY_COUNT) prio = VOS3_PRIORITY_NORMAL;

    vos3_pcpu_rq_t *target_rq = &g_pcpu_rq[min_cpu];

    vos3_spinlock_lock(&target_rq->lock);
    pcpu_rq_enqueue(&target_rq->queues[prio], migrant);
    target_rq->nr_running++;
    vos3_spinlock_unlock(&target_rq->lock);

    migrant->cpu_id = min_cpu;
    my_rq->steals_from++;
    target_rq->steals_to++;
}

uint32_t vos3_pcpu_find_least_loaded(void)
{
    if (!g_pcpu_initialized || g_num_cpus == 0) return 0;

    uint32_t min_load = 0xFFFFFFFFU;
    uint32_t min_cpu = 0;

    for (uint32_t c = 0; c < g_num_cpus; c++) {
        uint32_t load = g_pcpu_rq[c].nr_running;
        if (load < min_load) {
            min_load = load;
            min_cpu = c;
        }
    }

    return min_cpu;
}

int vos3_pcpu_get_stats(uint32_t cpu_id, vos3_pcpu_stats_t *stats)
{
    if (!g_pcpu_initialized || cpu_id >= g_num_cpus || stats == NULL) {
        return -1;
    }

    vos3_pcpu_rq_t *rq = &g_pcpu_rq[cpu_id];

    stats->cpu_id      = cpu_id;
    stats->nr_running  = rq->nr_running;
    stats->switches    = rq->switches;
    stats->steals_from = rq->steals_from;
    stats->steals_to   = rq->steals_to;
    stats->idle_ticks  = rq->idle_ticks;
    stats->load_avg    = rq->load_avg;

    for (uint32_t p = 0; p < VOS3_PRIORITY_COUNT; p++) {
        stats->queue_lengths[p] = (uint32_t)rq->queues[p].count;
    }

    return 0;
}

uint32_t vos3_pcpu_nr_running(uint32_t cpu_id)
{
    if (!g_pcpu_initialized || cpu_id >= g_num_cpus) return 0;
    return g_pcpu_rq[cpu_id].nr_running;
}

uint32_t vos3_pcpu_num_cpus(void)
{
    return g_num_cpus;
}
