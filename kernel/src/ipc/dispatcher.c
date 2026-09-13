/**
 * @file dispatcher.c
 * @brief VOS3 Multi-Agent Dispatcher — Phase 3
 *
 * @details Kernel-managed agent dispatcher with work-stealing routing,
 *          per-agent work queues, predictive memory prefetch, and
 *          AI-aware scheduler integration.
 *
 * @version 1.0.0
 * @date 2026-03-23
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/dispatcher.h"
#include "../../include/vos/ipc.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/string.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/ai_guard.h"

/* Error codes */
#ifndef EINVAL
#define EINVAL  22
#endif
#ifndef ENOMEM
#define ENOMEM  12
#endif
#ifndef EAGAIN
#define EAGAIN  11
#endif
#ifndef ENOENT
#define ENOENT  2
#endif
#ifndef EPERM
#define EPERM   1
#endif

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

static vos3_dispatch_agent_t g_agents[VOS3_DISPATCH_MAX_AGENTS];
static vos3_spinlock_t       g_dispatch_lock = VOS3_SPINLOCK_INIT;
static uint64_t              g_next_item_id  = 1;
static uint64_t              g_total_dispatched = 0;
static uint64_t              g_total_completed  = 0;
static uint64_t              g_total_steals     = 0;
static int                   g_dispatch_initialized = 0;
static uint32_t              g_active_agent_count   = 0;

/** @brief Deferred balance check flag — set by ISR, consumed by reschedule */
static volatile uint32_t     g_balance_pending      = 0;

/* ============================================================================
 * HELPERS
 * ============================================================================ */

/**
 * @brief Get queue depth for an agent
 */
static inline uint32_t agent_queue_depth(const vos3_dispatch_agent_t* agent)
{
    return agent->q_head - agent->q_tail;
}

/**
 * @brief Find agent slot by TID
 * @return slot index, or -1 if not found
 */
static int find_agent_by_tid(vos3_tid_t tid)
{
    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        if (g_agents[i].status != VOS3_AGENT_EMPTY &&
            g_agents[i].tid == tid) {
            return (int)i;
        }
    }
    return -1;
}

/* ============================================================================
 * CORE DISPATCH — WORK-STEALING ALGORITHM
 * ============================================================================ */

int vos3_dispatch_task(const vos3_dispatch_item_t* item)
{
    if (item == NULL) {
        return -EINVAL;
    }

    vos3_spinlock_lock(&g_dispatch_lock);

    /* Assign monotonic item ID */
    uint64_t id = g_next_item_id++;

    int best = -1;
    uint32_t best_depth = VOS3_DISPATCH_QUEUE_DEPTH + 1;
    int is_steal = 0;

    /* Pass 1: IDLE agent with matching capabilities, shortest queue */
    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        if (g_agents[i].status != VOS3_AGENT_IDLE) {
            continue;
        }
        if ((g_agents[i].capabilities & item->type) == 0 &&
            item->type != 0) {
            continue;
        }
        uint32_t depth = agent_queue_depth(&g_agents[i]);
        if (depth < best_depth) {
            best = (int)i;
            best_depth = depth;
        }
    }

    /* Pass 2: BUSY agent with matching caps, queue not full */
    if (best < 0) {
        for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
            if (g_agents[i].status != VOS3_AGENT_BUSY) {
                continue;
            }
            if ((g_agents[i].capabilities & item->type) == 0 &&
                item->type != 0) {
                continue;
            }
            uint32_t depth = agent_queue_depth(&g_agents[i]);
            if (depth < VOS3_DISPATCH_QUEUE_DEPTH && depth < best_depth) {
                best = (int)i;
                best_depth = depth;
            }
        }
    }

    /* Pass 3: WORK-STEAL — any agent (any capability) with space */
    if (best < 0) {
        is_steal = 1;
        for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
            if (g_agents[i].status == VOS3_AGENT_EMPTY) {
                continue;
            }
            uint32_t depth = agent_queue_depth(&g_agents[i]);
            if (depth < VOS3_DISPATCH_QUEUE_DEPTH && depth < best_depth) {
                best = (int)i;
                best_depth = depth;
            }
        }
    }

    /* All agents full */
    if (best < 0) {
        vos3_spinlock_unlock(&g_dispatch_lock);
        return -EAGAIN;
    }

    /* Enqueue item */
    vos3_dispatch_agent_t* agent = &g_agents[best];
    uint32_t slot = agent->q_head & VOS3_DISPATCH_QUEUE_MASK;
    agent->queue[slot] = *item;
    agent->queue[slot].item_id = id;
    agent->queue[slot].assigned_agent = agent->slot_id;
    agent->q_head++;

    /* Update status */
    uint32_t depth = agent_queue_depth(agent);
    if (depth >= VOS3_DISPATCH_QUEUE_DEPTH) {
        agent->status = VOS3_AGENT_FULL;
    } else {
        agent->status = VOS3_AGENT_BUSY;
    }

    if (is_steal) {
        agent->tasks_stolen++;
        g_total_steals++;
    }

    g_total_dispatched++;

    vos3_spinlock_unlock(&g_dispatch_lock);
    return (int)id;
}

/* ============================================================================
 * PREDICTIVE MEMORY — VFS PREFETCH
 * ============================================================================ */

int vos3_vfs_prefetch(const char* path, uint32_t flags)
{
    (void)path;  /* Path used for naming only in simplified version */

    uint32_t shm_flags = (flags & VOS3_AGENT_CAP_INFERENCE) ?
                         VOS3_SHM_FLAG_HUGETLB : 0;
    size_t size = (shm_flags & VOS3_SHM_FLAG_HUGETLB) ?
                  (2UL * 1024 * 1024) : (4096);

    /* Create a prefetch SHM region */
    vos3_ipc_id_t shm_id = vos3_shm_create("prefetch", size, shm_flags);
    if (shm_id == VOS3_IPC_INVALID || shm_id == VOS3_IPC_EEXIST) {
        return -ENOMEM;
    }

    return (int)shm_id;
}

/* ============================================================================
 * AI LOAD BALANCER — Called from scheduler tick
 * ============================================================================ */

void vos3_dispatch_balance_request(void)
{
    /* ISR-safe: no lock, just set the atomic flag */
    g_balance_pending = 1;
}

int vos3_dispatch_balance_pending(void)
{
    if (g_balance_pending) {
        g_balance_pending = 0;
        return 1;
    }
    return 0;
}

void vos3_dispatch_balance_check(void)
{
    if (!g_dispatch_initialized) {
        return;
    }

    /* Fast path: no registered agents → nothing to balance */
    if (g_active_agent_count == 0) {
        return;
    }

    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        if (g_agents[i].status == VOS3_AGENT_EMPTY) {
            continue;
        }

        vos3_task_t* task = vos3_task_get(g_agents[i].tid);
        if (task == NULL) {
            continue;
        }

        uint32_t depth = agent_queue_depth(&g_agents[i]);

        if (depth > VOS3_DISPATCH_QUEUE_DEPTH / 2) {
            /* Heavy load — boost to HIGH priority */
            vos3_task_set_priority(task, VOS3_PRIORITY_HIGH);
        } else if (depth == 0) {
            /* Idle — drop to LOW priority, save CPU */
            vos3_task_set_priority(task, VOS3_PRIORITY_LOW);
        } else {
            /* Normal load */
            vos3_task_set_priority(task, VOS3_PRIORITY_NORMAL);
        }
    }
}

/* ============================================================================
 * SYSCALL HANDLERS
 * ============================================================================ */

/**
 * @brief sys_agent_register — Register calling task as a dispatcher agent
 * Args: RDI = name_ptr (user string), RSI = capabilities (uint32_t)
 * Returns: slot_id (>= 0) on success, negative error on failure
 */
static int64_t sys_agent_register(vos3_syscall_frame_t* frame)
{
    const char* user_name = (const char*)frame->rdi;
    uint32_t capabilities = (uint32_t)frame->rsi;

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -EINVAL;
    }

    /* Check if already registered */
    vos3_spinlock_lock(&g_dispatch_lock);

    if (find_agent_by_tid(current->tid) >= 0) {
        vos3_spinlock_unlock(&g_dispatch_lock);
        return -EINVAL;  /* Already registered */
    }

    /* Find first empty slot */
    int slot = -1;
    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        if (g_agents[i].status == VOS3_AGENT_EMPTY) {
            slot = (int)i;
            break;
        }
    }

    if (slot < 0) {
        vos3_spinlock_unlock(&g_dispatch_lock);
        return -ENOMEM;
    }

    /* Set up agent */
    vos3_dispatch_agent_t* agent = &g_agents[slot];
    memset(agent, 0, sizeof(*agent));
    agent->slot_id = (uint32_t)slot;
    agent->tid = current->tid;
    agent->capabilities = capabilities;
    agent->status = VOS3_AGENT_IDLE;
    agent->q_head = 0;
    agent->q_tail = 0;
    agent->prefetch_shm_id = -1;
    agent->q_lock = (vos3_spinlock_t)VOS3_SPINLOCK_INIT;

    /* Copy name from user space */
    if (user_name != NULL) {
        char kname[32];
        int64_t len = strncpy_from_user(kname, user_name, sizeof(kname));
        if (len >= 0) {
            memcpy(agent->name, kname, sizeof(agent->name));
            agent->name[31] = '\0';
        }
    }

    /* Set AI_AGENT flag on the task */
    current->flags |= VOS3_TASK_FLAG_AI_AGENT;

    g_active_agent_count++;

    vos3_spinlock_unlock(&g_dispatch_lock);

    return (int64_t)slot;
}

/**
 * @brief sys_agent_deregister — Unregister an agent
 * Args: RDI = slot_id
 * Returns: 0 on success
 */
static int64_t sys_agent_deregister(vos3_syscall_frame_t* frame)
{
    uint32_t slot_id = (uint32_t)frame->rdi;

    if (slot_id >= VOS3_DISPATCH_MAX_AGENTS) {
        return -EINVAL;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -EINVAL;
    }

    vos3_spinlock_lock(&g_dispatch_lock);

    vos3_dispatch_agent_t* agent = &g_agents[slot_id];

    /* Validate caller owns this slot */
    if (agent->status == VOS3_AGENT_EMPTY || agent->tid != current->tid) {
        vos3_spinlock_unlock(&g_dispatch_lock);
        return -EPERM;
    }

    /* Destroy prefetch SHM if any */
    if (agent->prefetch_shm_id >= 0) {
        vos3_shm_destroy((vos3_ipc_id_t)agent->prefetch_shm_id);
    }

    /* Clear the AI_AGENT flag */
    vos3_task_t* task = vos3_task_get(agent->tid);
    if (task != NULL) {
        task->flags &= ~VOS3_TASK_FLAG_AI_AGENT;
    }

    /* Clear slot */
    agent->status = VOS3_AGENT_EMPTY;

    if (g_active_agent_count > 0) {
        g_active_agent_count--;
    }

    vos3_spinlock_unlock(&g_dispatch_lock);
    return 0;
}

/**
 * @brief sys_dispatch_submit — Submit a work item to the dispatcher
 * Args: RDI = item_ptr (user-space vos3_dispatch_item_t*)
 * Returns: item_id or negative error
 */
static int64_t sys_dispatch_submit(vos3_syscall_frame_t* frame)
{
    const void* user_item = (const void*)frame->rdi;

    if (user_item == NULL) {
        return -EINVAL;
    }
    if (!access_ok(user_item, sizeof(vos3_dispatch_item_t))) {
        return -EFAULT;
    }

    vos3_dispatch_item_t item;
    if (copy_from_user(&item, user_item, sizeof(item)) != 0) {
        return -EFAULT;
    }

    /* Set metadata */
    item.submitted_at = vos3_sched_get_uptime_ms();
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL) {
        item.requester_tid = current->tid;
    }

    return (int64_t)vos3_dispatch_task(&item);
}

/**
 * @brief sys_dispatch_pull — Pull a work item from agent's queue
 * Args: RDI = slot_id, RSI = item_out_ptr (user-space)
 * Returns: 0 on success, -EAGAIN if no work
 */
static int64_t sys_dispatch_pull(vos3_syscall_frame_t* frame)
{
    uint32_t slot_id = (uint32_t)frame->rdi;
    void* user_item_out = (void*)frame->rsi;

    if (slot_id >= VOS3_DISPATCH_MAX_AGENTS) {
        return -EINVAL;
    }
    if (user_item_out == NULL ||
        !access_ok(user_item_out, sizeof(vos3_dispatch_item_t))) {
        return -EFAULT;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -EINVAL;
    }

    vos3_dispatch_agent_t* agent = &g_agents[slot_id];

    /* Validate caller owns this slot */
    if (agent->status == VOS3_AGENT_EMPTY || agent->tid != current->tid) {
        return -EPERM;
    }

    vos3_spinlock_lock(&agent->q_lock);

    if (agent->q_tail == agent->q_head) {
        vos3_spinlock_unlock(&agent->q_lock);
        return -EAGAIN;  /* No work */
    }

    /* Dequeue */
    uint32_t idx = agent->q_tail & VOS3_DISPATCH_QUEUE_MASK;
    vos3_dispatch_item_t item = agent->queue[idx];
    agent->q_tail++;

    /* Update status */
    if (agent->q_tail == agent->q_head) {
        agent->status = VOS3_AGENT_IDLE;
    } else {
        agent->status = VOS3_AGENT_BUSY;
    }

    vos3_spinlock_unlock(&agent->q_lock);

    /* Copy to user space */
    if (copy_to_user(user_item_out, &item, sizeof(item)) != 0) {
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_dispatch_complete — Mark a work item as completed
 * Args: RDI = slot_id, RSI = item_id
 * Returns: 0 on success
 */
static int64_t sys_dispatch_complete(vos3_syscall_frame_t* frame)
{
    uint32_t slot_id = (uint32_t)frame->rdi;
    (void)frame->rsi;  /* item_id — logged for tracing, not validated */

    if (slot_id >= VOS3_DISPATCH_MAX_AGENTS) {
        return -EINVAL;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -EINVAL;
    }

    vos3_dispatch_agent_t* agent = &g_agents[slot_id];

    if (agent->status == VOS3_AGENT_EMPTY || agent->tid != current->tid) {
        return -EPERM;
    }

    vos3_spinlock_lock(&g_dispatch_lock);

    agent->tasks_completed++;
    g_total_completed++;

    if (agent->q_tail == agent->q_head) {
        agent->status = VOS3_AGENT_IDLE;
    }

    vos3_spinlock_unlock(&g_dispatch_lock);
    return 0;
}

/**
 * @brief sys_agent_status — Query dispatcher status
 * Args: RDI = status_out_ptr (user-space vos3_dispatch_status_t*)
 * Returns: 0 on success
 */
static int64_t sys_agent_status(vos3_syscall_frame_t* frame)
{
    void* user_status = (void*)frame->rdi;

    if (user_status == NULL ||
        !access_ok(user_status, sizeof(vos3_dispatch_status_t))) {
        return -EFAULT;
    }

    vos3_dispatch_status_t status;
    memset(&status, 0, sizeof(status));

    vos3_spinlock_lock(&g_dispatch_lock);

    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        if (g_agents[i].status != VOS3_AGENT_EMPTY) {
            status.active_agents++;
            if (g_agents[i].status == VOS3_AGENT_IDLE) {
                status.idle_agents++;
            }
            status.total_pending += agent_queue_depth(&g_agents[i]);
        }
    }

    status.total_dispatched = g_total_dispatched;
    status.total_completed  = g_total_completed;
    status.total_steals     = g_total_steals;

    vos3_spinlock_unlock(&g_dispatch_lock);

    if (copy_to_user(user_status, &status, sizeof(status)) != 0) {
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_vfs_prefetch — Predictive memory prefetch syscall
 * Args: RDI = path_ptr (user-space string), RSI = flags
 * Returns: SHM ID or negative error
 */
static int64_t sys_vfs_prefetch(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    uint32_t flags = (uint32_t)frame->rsi;

    char kpath[256];
    if (user_path != NULL) {
        int64_t len = strncpy_from_user(kpath, user_path, sizeof(kpath));
        if (len < 0) {
            return -EFAULT;
        }
    } else {
        kpath[0] = '\0';
    }

    return (int64_t)vos3_vfs_prefetch(kpath, flags);
}

/* ============================================================================
 * PHASE 4.2.9: AI YIELD-EX SYSCALL
 * ============================================================================ */

static int64_t sys_ai_yield_ex(vos3_syscall_frame_t* frame)
{
    uint8_t  slot_id    = (uint8_t)frame->rdi;
    uint64_t timeout_ms = frame->rsi;
    uint64_t event_mask = frame->rdx;
    return (int64_t)vos3_ai_yield_ex(slot_id, timeout_ms, event_mask);
}

/* Phase 4.2.11: Deterministic per-slot clock (frozen during DORMANT) */
static int64_t sys_ai_get_time(vos3_syscall_frame_t* frame)
{
    uint8_t slot_id = (uint8_t)frame->rdi;
    return (int64_t)vos3_ai_slot_get_time(slot_id);
}

/* ============================================================================
 * AGENT KILL ALL — Emergency shutdown of all registered agents
 * ============================================================================ */

/**
 * @brief Kill all registered agents, drain queues, scrub model memory.
 *
 * Algorithm:
 *   1. Lock dispatch, snapshot all active agent TIDs
 *   2. Clear agent slots, destroy prefetch SHM
 *   3. Unlock, kill each task via SIGKILL
 *   4. Yield 50x, cleanup zombies, scrub all 8 model slots
 *
 * @return Count of agents killed (>= 0)
 */
int vos3_dispatcher_kill_all(void)
{
    if (!g_dispatch_initialized) {
        return 0;
    }

    /* Phase 1: Snapshot agent TIDs under lock */
    vos3_tid_t tids[VOS3_DISPATCH_MAX_AGENTS];
    uint32_t count = 0;

    vos3_spinlock_lock(&g_dispatch_lock);

    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        if (g_agents[i].status != VOS3_AGENT_EMPTY) {
            tids[count++] = g_agents[i].tid;

            /* Destroy prefetch SHM if any */
            if (g_agents[i].prefetch_shm_id >= 0) {
                vos3_shm_destroy((vos3_ipc_id_t)g_agents[i].prefetch_shm_id);
            }

            /* Clear slot */
            g_agents[i].status = VOS3_AGENT_EMPTY;
            g_agents[i].q_head = 0;
            g_agents[i].q_tail = 0;
        }
    }

    g_active_agent_count = 0;
    vos3_spinlock_unlock(&g_dispatch_lock);

    if (count == 0) {
        return 0;
    }

    /* Phase 2: Kill all agent tasks (outside lock) */
    for (uint32_t i = 0; i < count; i++) {
        vos3_task_t* task = vos3_task_get(tids[i]);
        if (task != NULL && task->state != VOS3_TASK_ZOMBIE &&
            task->state != VOS3_TASK_DEAD) {
            task->flags &= ~VOS3_TASK_FLAG_AI_AGENT;
            vos3_task_kill(task, 9);  /* SIGKILL */
        }
    }

    /* Phase 3: Yield to let tasks die (same pattern as APPKILL) */
    for (int i = 0; i < 50; i++) {
        vos3_task_yield();
    }

    /* Phase 4: Clean up zombie tasks */
    for (uint32_t i = 0; i < count; i++) {
        vos3_task_t* task = vos3_task_get(tids[i]);
        if (task != NULL &&
            (task->state == VOS3_TASK_ZOMBIE || task->state == VOS3_TASK_DEAD)) {
            vos3_task_defer_destroy(task);
        }
    }

    /* Phase 5: Scrub ALL model regions across all app contexts */
    for (uint8_t app_id = 0; app_id < 8; app_id++) {
        vos3_ai_guard_ctx_t* ctx = vos3_ai_guard_get_app_ctx(app_id);
        if (ctx != NULL) {
            uint64_t scrubbed = vos3_ai_guard_scrub_model_regions(app_id);
            if (scrubbed > 0) {
                VOS3_INFO("[DISPATCH] KILL_ALL: scrubbed %llu bytes for app_id=%u",
                          (unsigned long long)scrubbed, app_id);
            }
        }
    }

    VOS3_INFO("[DISPATCH] KILL_ALL: %u agents killed", count);
    return (int)count;
}

/**
 * @brief sys_agent_kill_all — Syscall 497 handler.
 * Privileged: requires VOS3_TASK_FLAG_KERNEL.
 */
static int64_t sys_agent_kill_all(vos3_syscall_frame_t* frame)
{
    (void)frame;

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -EINVAL;
    }
    if (!(current->flags & VOS3_TASK_FLAG_KERNEL)) {
        return -EPERM;
    }

    return (int64_t)vos3_dispatcher_kill_all();
}

/* ============================================================================
 * INFERENCE HINT SYSCALL (Batch F: Inference-Aware Scheduling)
 * ============================================================================ */

/**
 * @brief Set inference scheduling state for current AI agent task
 * @param state VOS3_INFERENCE_IDLE/LOADING/INFERRING/YIELDING
 * @return 0 on success, -EINVAL/-EPERM on error
 */
static int64_t sys_inference_hint(vos3_syscall_frame_t* frame)
{
    uint64_t state = frame->rdi;
    if (state > VOS3_INFERENCE_YIELDING) return -EINVAL;
    vos3_task_t *current = vos3_sched_current();
    if (current == NULL) return -EPERM;
    /* Only AI agent tasks can set inference state */
    if (!(current->flags & VOS3_TASK_FLAG_AI_AGENT)) return -EPERM;

    uint8_t old_state = current->inference_state;
    current->inference_state = (uint8_t)state;

    /* Phase 6.1: Set/clear AI_INFERENCE flag for scheduler anti-preemption */
    if (state == VOS3_INFERENCE_INFERRING) {
        current->flags |= VOS3_TASK_FLAG_AI_INFERENCE;
        current->inference_grants = 0; /* Reset grant counter */
    } else {
        current->flags &= ~VOS3_TASK_FLAG_AI_INFERENCE;
        current->inference_grants = 0;
    }

    /* Phase 6.1: Log state transitions for KIM telemetry */
    if (old_state != (uint8_t)state) {
        VOS3_DEBUG("[KIM] tid=%u inference: %u -> %u",
                   current->tid, old_state, (uint8_t)state);
    }

    return 0;
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

int vos3_dispatcher_init(void)
{
    if (g_dispatch_initialized) {
        return 0;
    }

    /* Zero all agent slots */
    memset(g_agents, 0, sizeof(g_agents));
    for (uint32_t i = 0; i < VOS3_DISPATCH_MAX_AGENTS; i++) {
        g_agents[i].status = VOS3_AGENT_EMPTY;
        g_agents[i].prefetch_shm_id = -1;
    }

    /* Register syscalls 490-496 */
    vos3_syscall_register(VOS3_SYS_AGENT_REGISTER,   sys_agent_register);
    vos3_syscall_register(VOS3_SYS_AGENT_DEREGISTER,  sys_agent_deregister);
    vos3_syscall_register(VOS3_SYS_DISPATCH_SUBMIT,   sys_dispatch_submit);
    vos3_syscall_register(VOS3_SYS_DISPATCH_PULL,     sys_dispatch_pull);
    vos3_syscall_register(VOS3_SYS_DISPATCH_COMPLETE, sys_dispatch_complete);
    vos3_syscall_register(VOS3_SYS_AGENT_STATUS,      sys_agent_status);
    vos3_syscall_register(VOS3_SYS_VFS_PREFETCH,      sys_vfs_prefetch);
    vos3_syscall_register(VOS3_SYS_AGENT_KILL_ALL,     sys_agent_kill_all);

    /* Batch F: Inference-Aware Scheduling */
    vos3_syscall_register(VOS3_SYS_INFERENCE_HINT,    sys_inference_hint);

    /* Phase 4.2.9: AI Yield-EX */
    vos3_syscall_register(VOS3_SYS_AI_YIELD_EX,      sys_ai_yield_ex);

    /* Phase 4.2.11: Deterministic Clock */
    vos3_syscall_register(VOS3_SYS_AI_GET_TIME,      sys_ai_get_time);

    g_dispatch_initialized = 1;

    VOS3_INFO("Dispatcher initialized: %d agent slots", VOS3_DISPATCH_MAX_AGENTS);

    return 0;
}
