/**
 * @file scheduler.c
 * @brief VOS3 Round-Robin Scheduler
 *
 * @details Priority-based round-robin scheduler with preemption support.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/scheduler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/percpu.h"
#include "../../include/vos/entry_state.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/ipc.h"
#include "../../include/arch/x86_64/cpu.h"
#include "../../include/arch/x86_64/smp.h"
#include "../../include/vos/bench.h"
#include "../../include/arch/x86_64/gdt.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/dispatcher.h"
#include "../../include/vos/tcp.h"

/* ============================================================================
 * MSR ACCESS (local inline — no external dependency)
 * ============================================================================ */
#define MSR_FS_BASE  0xC0000100U   /**< %fs base address MSR (per-thread TLS) */

static inline void sched_wrmsr(uint32_t msr, uint64_t val)
{
    __asm__ volatile("wrmsr"
                     : : "c"(msr),
                         "a"((uint32_t)(val & 0xFFFFFFFFULL)),
                         "d"((uint32_t)(val >> 32)));
}

/* ============================================================================
 * SCHEDULER STATE
 * ============================================================================ */

/** @brief Per-priority run queues */
static vos3_run_queue_t g_run_queues[VOS3_PRIORITY_COUNT];

/* ============================================================================
 * [QUANTUM-LEAP] v21.0.1 — INTERACTIVE FAST-PATH RUN QUEUE
 * ============================================================================
 * Sits ABOVE the priority queues. Tasks with latency_class==INTERACTIVE
 * are routed here regardless of their static priority, so a low-priority
 * "human-facing chat task" still preempts a HIGH-priority background
 * batch when both are runnable. The picker checks this queue first; if
 * empty, falls through to the existing priority sweep so legacy
 * behavior is unchanged for tasks that don't set latency_class. */
#include "../../include/vos/dispatch_hint.h"
#include "../../include/vos/compiler.h"  /* likely / unlikely — P-05 */
/* [OLYMPUS-FIX G-19] Static initializer — secondary CPUs that read
 * g_interactive_rq before vos3_sched_init runs (e.g. an early ISR)
 * will see a clean zeroed state instead of BSS-uninitialized memory.
 * BSS zero-init covers this on real targets, but the explicit init
 * is defense-in-depth and makes the contract obvious. The vos3_run_queue_t
 * is a pointer-list head/tail/count layout — all-zero is "empty queue",
 * matching what rq_init() produces. */
static vos3_run_queue_t g_interactive_rq = {0};

/* Helper: choose the right queue for a task at enqueue time.
 * [OLYMPUS-FIX P-05] unlikely() hint — the INTERACTIVE branch is the
 * minority case (most tasks default to LC_BATCH after the F-3 fix),
 * so the branch predictor should expect "fall through to priority
 * queue" as the common path. */
static inline vos3_run_queue_t* rq_for_task(const vos3_task_t* t) {
    if (unlikely(t->latency_class == VOS3_LC_INTERACTIVE)) {
        return &g_interactive_rq;
    }
    return &g_run_queues[t->priority];
}

/* ============================================================================
 * [OLYMPUS-FIX APEX-HOME v21.0.4 hook] IPI preemption stub
 * ============================================================================
 *
 * When a VOS3_LC_INTERACTIVE task is enqueued onto a remote CPU (one
 * that's currently running BATCH/CONTROL work), we want to preempt
 * that CPU IMMEDIATELY — not wait for the next 100Hz scheduler tick.
 * That requires sending an IPI (Inter-Processor Interrupt) via the
 * LAPIC ICR.
 *
 * The full implementation needs:
 *   1. LAPIC ICR programming (kernel/src/arch/x86_64/apic.c, deferred)
 *   2. A reschedule IPI vector wired to vos3_irq_handler that calls
 *      vos3_sched_reschedule on the receiving CPU.
 *   3. CPU-affinity awareness so we don't IPI ourselves.
 *
 * Until #1 lands, the hook is a NO-OP behind VOS3_LATENCY_IPI. The
 * call sites are wired so that flipping the flag activates the path
 * without further refactoring. The hook is called from the enqueue
 * paths; "missed" preemptions degrade to next-tick scheduling — the
 * v20.x behavior — which is what the OLYMPUS audit said was correct
 * fallback.
 *
 * Why scaffold not "production": calling a not-yet-wired ICR write
 * sequence on real hardware would either crash (uninitialized LAPIC
 * register) or silently send to the wrong vector. Both worse than
 * the legacy 100Hz behavior. The hook stays inert until the apic.c
 * side ships.
 */
static inline void vos3_sched_ipi_preempt_hook(uint32_t target_cpu,
                                               const vos3_task_t* incoming)
{
    (void)target_cpu;
    (void)incoming;
#ifdef VOS3_LATENCY_IPI
    /* Activated path — only enabled when arch/x86_64/apic.c provides
     * the ICR write. Until then this branch does not compile. */
    extern void vos3_apic_send_resched_ipi(uint32_t cpu);
    vos3_apic_send_resched_ipi(target_cpu);
#endif /* VOS3_LATENCY_IPI */
}

/** @brief Sleep queue (sorted by wake time) */
static vos3_task_t* g_sleep_queue = NULL;

/** @brief Current running task per CPU
 *  [OLYMPUS-FIX P-10 cluster] — placed on its own cache line so that
 *  per-CPU current/idle pointer writes do not invalidate adjacent
 *  scheduler statistics or queue heads on other CPUs.
 *
 *  Cyber overlay (Stage 6): static dropped to give core_cookie.c
 *  external linkage to this array (extern decl at core_cookie.c:48).
 *  Required by vos3_sched_sibling_compatible() which inspects the
 *  task currently scheduled on the SMT-sibling CPU. */
vos3_task_t* g_current_task[256] __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/** @brief Idle task per CPU */
static vos3_task_t* g_idle_task[256] __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/** @brief Scheduler lock */
static vos3_spinlock_t g_sched_lock = VOS3_SPINLOCK_INIT;

/* Published under g_sched_lock; consumed only on the incoming stack.
 * A CPU may own both current and outgoing during a switch. */
static vos3_task_t* g_switch_outgoing[256];

/** @brief Sleep queue lock */
static vos3_spinlock_t g_sleep_lock = VOS3_SPINLOCK_INIT;

/** @brief System tick counter */
static volatile uint64_t g_tick_count = 0ULL;

/** @brief Scheduler running flag */
static volatile int g_sched_running = 0;

/** @brief Scheduler initialized flag */
static int g_sched_initialized = 0;

/** @brief Scheduler statistics
 *  [OLYMPUS-FIX P-10] — cache-line aligned. The struct's fields
 *  (total_switches, voluntary_switches, preemptions, idle_ticks,
 *  task_count, runnable_count, blocked_count) are mutated from
 *  multiple CPU paths (tick, reschedule, add/remove task). Without
 *  alignment, all writes share one or two cache lines and ping-pong
 *  invalidations on every update. */
static vos3_sched_stats_t g_sched_stats __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/* [OLYMPUS-FIX P-11] APEX-HOME — per-CPU scheduler state, padded.
 *
 * Previous design: two parallel uint32_t/int arrays of 256 entries.
 * 16 entries fit in one 64-byte cache line, so CPU N+0 incrementing
 * its task_count invalidated CPU N+1..15's slots on every schedule
 * decision. The load balancer's full sweep (find_least_loaded_cpu)
 * read all 256 entries, ping-ponging cache lines across the box.
 *
 * New design: one struct per CPU, each on its own 64-byte line. All
 * per-CPU scheduler state lives together so a single line load gives
 * the load balancer everything it needs for that CPU.
 *
 * Cost: 256 × 64 = 16 KiB BSS instead of (256 × 4) + (256 × 4) =
 * 2 KiB. Worth every byte on any 8+ core PC/NUT host.
 */
typedef struct vos3_cpu_sched_state {
    volatile uint32_t task_count;       /* legacy g_cpu_sched_state[i].task_count */
    volatile int      need_reschedule;  /* legacy g_cpu_sched_state[i].need_reschedule */
    uint8_t           _pad[VOS3_CACHE_LINE_SIZE
                           - sizeof(uint32_t)
                           - sizeof(int)];
} vos3_cpu_sched_state_t;

static vos3_cpu_sched_state_t g_cpu_sched_state[256]
    __attribute__((aligned(VOS3_CACHE_LINE_SIZE))) = {{0, 0, {0}}};

/** @brief Deferred reap flag — set in ISR, processed in process context (K-R2) */
static volatile int g_reap_pending = 0;

/** @brief Deferred AI Guard reprotect flag — set in ISR, processed in process context (K-R3) */
static volatile int g_ai_guard_dirty = 0;

/** @brief Deferred AI monitor work. The monitor performs integrity hashing,
 * pressure reclaim, callbacks and PTE work and therefore must never execute
 * on the timer interrupt stack. Multiple timer IRQs may coalesce; the worker
 * consumes the current absolute timer tick. */
static volatile int g_ai_monitor_pending = 0;

/** @brief Deferred TCP timer work flag — set every 10 ticks (100ms) in ISR,
 *  processed in process context. K-R4: vos3_tcp_timer_tick() acquires
 *  g_tcp_lock which must never be taken from ISR context. */
static volatile int g_tcp_work_pending = 0;

/** @brief Tick counter for TCP timer decimation (fire every 10 ticks = 100ms) */
static volatile uint32_t g_tcp_timer_acc = 0;

/** @brief TCP timer decimation interval: every 10 ticks (100ms at 100Hz PIT) */
#define SCHED_TCP_TIMER_INTERVAL  10U

/* ============================================================================
 * RUN QUEUE OPERATIONS
 * ============================================================================ */

/**
 * @brief Initialize a run queue
 */
static void rq_init(vos3_run_queue_t* rq)
{
    rq->head = NULL;
    rq->tail = NULL;
    rq->count = 0U;
}

/**
 * @brief Add task to end of run queue
 */
static void rq_enqueue(vos3_run_queue_t* rq, vos3_task_t* task)
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

/**
 * @brief Remove specific task from run queue
 */
static void rq_remove(vos3_run_queue_t* rq, vos3_task_t* task)
{
    if (task->prev != NULL) {
        task->prev->next = task->next;
    } else {
        rq->head = task->next;
    }

    if (task->next != NULL) {
        task->next->prev = task->prev;
    } else {
        rq->tail = task->prev;
    }

    task->next = NULL;
    task->prev = NULL;
    rq->count--;
    task->flags &= ~VOS3_TASK_FLAG_QUEUED;
}

/* ============================================================================
 * SCHEDULER CORE
 * ============================================================================ */

/**
 * @brief Select next task to run (SMP-aware)
 * @return Next task, or idle task if none available
 */
static int sched_task_selectable_locked(vos3_task_t* task, uint32_t cpu)
{
    vos3_task_state_t state = __atomic_load_n(&task->state, __ATOMIC_ACQUIRE);
    if (state != VOS3_TASK_READY && state != VOS3_TASK_RUNNING)
        return 0;
    if ((task->flags & (VOS3_TASK_FLAG_IDLE | VOS3_TASK_FLAG_PINNED)) &&
        task->cpu_id != cpu)
        return 0;
#ifdef NATIVE_SMP_TEST
    if (task->sched_owner_plus_one != 0 &&
        task->sched_owner_plus_one != cpu + 1U)
        return 0;
#endif
    uint32_t owner = __atomic_load_n(&task->sched_execution_owner, __ATOMIC_ACQUIRE);
    return owner == 0U ||
        (owner == cpu + 1U && task == g_current_task[cpu] &&
         __atomic_load_n(&g_switch_outgoing[cpu], __ATOMIC_ACQUIRE) == NULL);
}

/* Caller holds g_sched_lock with local IRQs disabled. Selection alone must
 * not expose a window where a reaper or another CPU can claim next. */
static void sched_reserve_switch_locked(vos3_task_t* prev, vos3_task_t* next,
                                        uint32_t cpu)
{
    if (next == NULL || !sched_task_selectable_locked(next, cpu) ||
        __atomic_load_n(&g_switch_outgoing[cpu], __ATOMIC_ACQUIRE) != NULL)
        VOS3_PANIC("Invalid scheduler handoff reservation");
    if (prev != NULL &&
        __atomic_load_n(&prev->sched_execution_owner, __ATOMIC_ACQUIRE) != cpu + 1U)
        VOS3_PANIC("Outgoing task lacks CPU reservation");
    if (prev == next)
        return;
    uint32_t expected = 0U;
    if (!__atomic_compare_exchange_n(&next->sched_execution_owner, &expected,
                                     cpu + 1U, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        VOS3_PANIC("Incoming task already reserved");
    __atomic_store_n(&g_switch_outgoing[cpu], prev, __ATOMIC_RELEASE);
}

/* Called by context.S after moving RSP, before restoring registers/returning.
 * Do not reference the outgoing task after releasing its reservation. */
void vos3_sched_switch_stack_ack(void)
{
    uint32_t cpu = get_cpu_id();
    vos3_task_t* prev = __atomic_exchange_n(&g_switch_outgoing[cpu], NULL,
                                           __ATOMIC_ACQ_REL);
#ifdef VOS3_HW_XSAVE
    extern void vos3_xsave_restore_for_task(vos3_task_t*);
    vos3_xsave_restore_for_task(g_current_task[cpu]);
#endif
    if (prev != NULL) {
        if (__atomic_load_n(&prev->sched_execution_owner, __ATOMIC_ACQUIRE) != cpu + 1U)
            VOS3_PANIC("Stack acknowledgement owner mismatch");
        __atomic_store_n(&prev->sched_execution_owner, 0U, __ATOMIC_RELEASE);
    }
}

/* Serializes the last execution check with every incoming reservation.
 * Retired tasks must already be detached from task/wait/sleep ownership. */
int vos3_sched_claim_task_reap(vos3_task_t* task)
{
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_sched_lock);
    int claimed = 0;
    if (__atomic_load_n(&task->state, __ATOMIC_ACQUIRE) == VOS3_TASK_DEAD &&
        !(task->flags & (VOS3_TASK_FLAG_QUEUED | VOS3_TASK_FLAG_IDLE)) &&
        !vos3_fpu_task_owned(task)) {
        uint32_t expected = 0U;
        claimed = __atomic_compare_exchange_n(&task->sched_execution_owner,
                    &expected, UINT32_MAX, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    }
    vos3_spinlock_unlock(&g_sched_lock);
    vos3_irq_restore(flags);
    return claimed;
}

static vos3_task_t* pick_next_task(void)
{
    uint32_t cpu = get_cpu_id();
    for (int prio = (int)VOS3_PRIORITY_COUNT; prio >= 0; prio--) {
        vos3_run_queue_t* rq = prio == (int)VOS3_PRIORITY_COUNT ?
                              &g_interactive_rq : &g_run_queues[prio];
        for (vos3_task_t* task = rq->head; task != NULL; task = task->next) {
            if (!sched_task_selectable_locked(task, cpu))
                continue;
#ifdef NATIVE_SMP_TEST
            task->sched_owner_plus_one = cpu + 1U;
#endif
            rq_remove(rq, task);
            return task;
        }
    }
    if (!sched_task_selectable_locked(g_idle_task[cpu], cpu))
        VOS3_PANIC("Idle task unavailable for scheduler handoff");
    return g_idle_task[cpu];
}

/**
 * @brief Put current task back on run queue
 */
static void put_prev_task(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    /* Don't re-queue idle tasks */
    if ((task->flags & VOS3_TASK_FLAG_IDLE) != 0U) {
        return;
    }

    /* Only re-queue if still runnable and not already queued */
    if (task->state == VOS3_TASK_RUNNING &&
        !(task->flags & VOS3_TASK_FLAG_QUEUED)) {
        task->state = VOS3_TASK_READY;
        /* [QUANTUM-LEAP v21.0.1] route via latency-class helper */
        rq_enqueue(rq_for_task(task), task);
    }
}

/**
 * @brief Perform the actual context switch (SMP-aware)
 */
static void do_context_switch(vos3_task_t* prev, vos3_task_t* next)
{
    uint32_t cpu_id = get_cpu_id();

    if (prev == next) {
        return;
    }

    /* Sanity: never switch to a dead or zombie task */
    if (next->state == VOS3_TASK_ZOMBIE || next->state == VOS3_TASK_DEAD) {
        VOS3_PANIC("BUG: switching to dead/zombie task '%s' tid=%u",
                   next->name, next->tid);
    }
    if (next->context == NULL || next->context->rip < 0x1000ULL) {
        VOS3_PANIC("BUG: task '%s' tid=%u has invalid RIP=0x%llx",
                   next->name, next->tid,
                   (unsigned long long)(next->context ? next->context->rip : 0));
    }

    /* Detach lazy hardware ownership while prev is still current and its
     * CPU reservation prevents migration/reclamation. */
    vos3_fpu_switch_out(prev);

    /* Update current task for this CPU */
    g_current_task[cpu_id] = next;
    next->cpu_id = cpu_id;
#ifdef NATIVE_SMP_WORKLOAD
    if (next->pid > 0 && !(next->flags & VOS3_TASK_FLAG_IDLE) &&
        !next->native_smp_reported) {
        VOS3_INFO("NATIVE_SMP schedule pid=%u cpu=%u apic=%u", next->pid,
                  cpu_id, vos3_lapic_id());
        next->native_smp_reported = 1;
    }
#endif

    /* Also update per-CPU structure */
    percpu_set_current(next);

    /* Update task states */
    if (prev != NULL && prev->state == VOS3_TASK_RUNNING) {
        prev->state = VOS3_TASK_READY;
    }
    next->state = VOS3_TASK_RUNNING;

    /* Reset time slice */
    next->time_slice = (next->priority == VOS3_PRIORITY_IDLE) ?
                       VOS3_SCHED_SLICE_IDLE :
                       ((uint64_t)(next->priority + 1U) * VOS3_SCHED_DEFAULT_SLICE / 2U);

    /* Phase 3 + Batch F: Inference-aware timeslice for AI agents */
    if (next->flags & VOS3_TASK_FLAG_AI_AGENT) {
        if (next->inference_state == VOS3_INFERENCE_INFERRING) {
            next->time_slice *= 4;  /* 4x for active inference */
        } else {
            next->time_slice *= 2;  /* 2x for AI agents */
        }
    }

    next->last_scheduled = g_tick_count;
    next->context_switches++;

    g_sched_stats.total_switches++;
    percpu_inc_context_switches();

    VOS3_DEBUG("CPU%u Switch: %s -> %s", cpu_id,
               prev ? prev->name : "(none)",
               next->name);

    /*
     * Update the syscall kernel stack pointer for the next task.
     * This is CRITICAL: each task must use its own kernel stack for syscalls
     * to prevent stack corruption when tasks block inside syscalls.
     *
     * Without this, all tasks share a global syscall stack, and when one task
     * blocks (e.g., in waitpid) and another task makes a syscall, the second
     * task's syscall would overwrite the first task's saved stack state,
     * causing a crash (RIP=0) when the first task resumes.
     */
    if (next->kernel_stack != NULL) {
        uint64_t stack_top = (uint64_t)(uintptr_t)next->kernel_stack +
                              next->kernel_stack_size;
        vos3_entry_set_kernel_stack(stack_top);

        /*
         * Update TSS.RSP0 so that interrupts/faults from user mode
         * use this task's kernel stack, not the shared boot stack.
         * Without this, all user-mode interrupts land on the boot stack,
         * and a second task's interrupt overwrites the first task's
         * saved context — causing RIP corruption (e.g. RIP=0x2).
         */
        vos3_tss_set_rsp0(cpu_id, vos3_entry_trampoline_top());
    }

    /*
     * Switch address space if necessary.
     * Each user task has its own address space (page tables), and we must
     * switch to the correct one before returning to user mode.
     */
    if (next->address_space != NULL &&
        (prev == NULL || prev->address_space != next->address_space)) {
        vos3_vmm_switch_address_space(next->address_space);
    }

    /*
     * Update %fs base (TLS) for the incoming task.
     * Only write the MSR if the value actually changed — wrmsr costs
     * ~200+ cycles, while the branch is ~1 cycle.  Most context switches
     * are between non-thread tasks where tls_base==0 on both sides.
     */
    if (prev == NULL || prev->tls_base != next->tls_base) {
        sched_wrmsr(MSR_FS_BASE, next->tls_base);
    }
    if (prev == NULL || prev->user_gs_base != next->user_gs_base) {
        sched_wrmsr(VOS3_MSR_KERNEL_GS_BASE, next->user_gs_base);
    }

    /* Set CR0.TS for lazy FPU switching — next FPU/SSE/AVX instruction
     * will trigger #NM, where we save/restore FPU state on demand.
     * This avoids saving/restoring 512-1024 bytes on every context switch
     * for tasks that never use FPU (e.g., shell, management tasks). */
    vos3_fpu_set_ts();

    /* Benchmark: record CSW start timestamp */
    vos3_bench_csw_start(prev != NULL ? prev->tid : 0U, next->tid);

    /* HW-1 (M1) — extended state save/restore around the context switch.
     * Inert when VOS3_HW_XSAVE is not defined. */
#ifdef VOS3_HW_XSAVE
    extern void vos3_xsave_save_for_task(vos3_task_t *);
    extern void vos3_xsave_restore_for_task(vos3_task_t *);
    vos3_xsave_save_for_task(prev);
#endif

    /* Perform context switch */
    if (prev != NULL) {
        vos3_context_switch(&prev->context, next->context);
    } else {
        /* First switch - just load new context */
        vos3_context_switch(NULL, next->context);
    }

    /* Incoming extended state is restored by the assembly acknowledgement.
     * The local next here belongs to a suspended, earlier invocation. */

    /* Benchmark: record CSW end timestamp (runs on new task) */
    vos3_bench_csw_end();
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_sched_init(void)
{
    if (g_sched_initialized != 0) {
        return VOS3_SCHED_ERR_INVALID;
    }

    /* Initialize run queues */
    for (size_t i = 0U; i < VOS3_PRIORITY_COUNT; i++) {
        rq_init(&g_run_queues[i]);
    }
    /* [QUANTUM-LEAP v21.0.1] interactive fast-path queue */
    rq_init(&g_interactive_rq);

    /* Clear per-CPU state */
    for (size_t i = 0U; i < 256U; i++) {
        g_current_task[i] = NULL;
        g_idle_task[i] = NULL;
        g_switch_outgoing[i] = NULL;
    }

    /* Clear sleep queue */
    g_sleep_queue = NULL;

    /* Clear statistics */
    g_sched_stats.total_switches = 0ULL;
    g_sched_stats.voluntary_switches = 0ULL;
    g_sched_stats.preemptions = 0ULL;
    g_sched_stats.idle_ticks = 0ULL;
    g_sched_stats.task_count = 0ULL;
    g_sched_stats.runnable_count = 0ULL;
    g_sched_stats.blocked_count = 0ULL;
    g_sched_stats.sleeping_count = 0ULL;

    g_tick_count = 0ULL;
    g_sched_running = 0;

    /* Clear per-CPU reschedule flags and task counts */
    for (size_t i = 0U; i < 256U; i++) {
        g_cpu_sched_state[i].need_reschedule = 0;
        g_cpu_sched_state[i].task_count = 0;
    }

    /* Initialize task subsystem */
    int result = vos3_task_init();
    if (result != 0) {
        return result;
    }

    /* Create idle task for BSP */
    g_idle_task[0] = vos3_task_create_idle(0U);
    if (g_idle_task[0] == NULL) {
        return VOS3_SCHED_ERR_NOMEM;
    }

    g_sched_initialized = 1;

    VOS3_INFO("Scheduler initialized");

    return VOS3_SCHED_OK;
}

int vos3_sched_is_initialized(void)
{
    return g_sched_initialized;
}

__attribute__((noreturn))
void vos3_sched_start(void)
{
    if (g_sched_initialized == 0) {
        VOS3_PANIC("Scheduler not initialized");
    }

    VOS3_INFO("Starting scheduler (SMP-aware)");

    /* First context takes over interrupt state in every build. */
    (void)vos3_irq_save();
    g_sched_running = 1;

    /* Initialize BSP scheduler state */
    g_cpu_sched_state[0].need_reschedule = 0;
    g_cpu_sched_state[0].task_count = 0;

    /* Reserve bootstrap ownership before publishing the first task. */
    vos3_spinlock_lock(&g_sched_lock);
    vos3_task_t* first = pick_next_task();
    sched_reserve_switch_locked(NULL, first, 0U);
    vos3_spinlock_unlock(&g_sched_lock);

    g_current_task[0] = first;
    first->state = VOS3_TASK_RUNNING;
    first->cpu_id = 0;
    first->last_scheduled = g_tick_count;

    /* Update per-CPU structure */
    percpu_set_current(first);

    /* Set up syscall kernel stack for first task */
    if (first->kernel_stack != NULL) {
        vos3_entry_set_kernel_stack((uint64_t)(uintptr_t)first->kernel_stack +
                                   first->kernel_stack_size);
        vos3_tss_set_rsp0(0, vos3_entry_trampoline_top());
    }

    VOS3_INFO("First task: %s (CPU 0)", first->name);

    /* Jump to first task - never returns */
    vos3_context_switch(NULL, first->context);

    /* Should never reach here */
    VOS3_PANIC("Scheduler start returned");
    for (;;) {
        __asm__ volatile ("hlt");
    }
}

/**
 * @brief Find CPU with lowest task count for load balancing
 */
#ifdef NATIVE_SMP_TEST
/* Protected by g_sched_lock; only first enqueue advances this cursor. */
static uint32_t g_initial_cpu_cursor;

static uint32_t assign_initial_cpu_locked(vos3_task_t* task)
{
    /* CLONE_VM siblings and waking tasks keep their established owner. */
    if (task->sched_owner_plus_one != 0)
        return task->sched_owner_plus_one - 1;

    uint32_t target = 0;
    if (g_sched_running != 0) {
        uint32_t count = vos3_smp_cpu_count();
        if (count > 256U) count = 256U;
        if (count != 0) {
            uint32_t start = g_initial_cpu_cursor % count;
            for (uint32_t offset = 0; offset < count; offset++) {
                uint32_t candidate = (start + offset) % count;
                const vos3_smp_cpu_info_t* info = vos3_smp_get_cpu_info(candidate);
                if (info != NULL && __atomic_load_n(&info->started, __ATOMIC_ACQUIRE) != 0) {
                    target = candidate;
                    g_initial_cpu_cursor = (candidate + 1U) % count;
                    break;
                }
            }
        }
    }
    /* BSP owns bootstrap tasks. AP readiness is published only after the
     * AP's scheduler/entry state is initialized; failed CPUs are skipped. */
    task->sched_owner_plus_one = target + 1U;
    return target;
}
#else
static uint32_t find_least_loaded_cpu(void)
{
    uint32_t best_cpu = 0;
    uint32_t min_count = g_cpu_sched_state[0].task_count;
    uint32_t online = vos3_smp_online_count();

    for (uint32_t i = 1; i < online; i++) {
        if (g_cpu_sched_state[i].task_count < min_count) {
            min_count = g_cpu_sched_state[i].task_count;
            best_cpu = i;
        }
    }

    return best_cpu;
}
#endif

void vos3_sched_add_task(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    /* Disable interrupts to prevent deadlock: timer ISR also acquires
     * g_sched_lock via process_sleepers→wake→add_task and via reschedule. */
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_sched_lock);

    /* Guard: prevent double-add list corruption.
     * VOS3_TASK_FLAG_QUEUED is set/cleared atomically inside rq_enqueue,
     * rq_dequeue, and rq_remove — definitive queue membership tracking. */
    vos3_task_state_t state = __atomic_load_n(&task->state, __ATOMIC_ACQUIRE);
    if (state == VOS3_TASK_ZOMBIE || state == VOS3_TASK_DEAD ||
        __atomic_load_n(&task->sched_execution_owner, __ATOMIC_ACQUIRE) == UINT32_MAX ||
        (task->flags & VOS3_TASK_FLAG_QUEUED)) {
        vos3_spinlock_unlock(&g_sched_lock);
        vos3_irq_restore(flags);
        return;
    }

    if (task->state != VOS3_TASK_READY) {
        task->state = VOS3_TASK_READY;
    }

#ifdef NATIVE_SMP_TEST
    /* Bind once before publication; a racing CPU cannot claim both new
     * tasks merely by reaching the global run queue first. */
    uint32_t target_cpu = assign_initial_cpu_locked(task);
#else
    uint32_t target_cpu = find_least_loaded_cpu();
#endif
    task->cpu_id = target_cpu;
    g_cpu_sched_state[target_cpu].task_count++;

    /* [QUANTUM-LEAP v21.0.1] route via latency-class helper */
    rq_enqueue(rq_for_task(task), task);
    g_sched_stats.task_count++;
    g_sched_stats.runnable_count++;

    vos3_spinlock_unlock(&g_sched_lock);
    vos3_irq_restore(flags);

    /* [OLYMPUS-FIX APEX-HOME v21.0.4 hook] If we just enqueued an
     * INTERACTIVE task on a remote CPU, ask that CPU to reschedule
     * immediately rather than wait for the next 100Hz tick. Today
     * this is a no-op (IPI machinery deferred); flipping
     * VOS3_LATENCY_IPI activates the path. */
    if (task->latency_class == VOS3_LC_INTERACTIVE) {
        vos3_sched_ipi_preempt_hook(target_cpu, task);
    }

    VOS3_DEBUG("Added task '%s' to run queue (prio %u, target CPU %u)",
               task->name, task->priority, target_cpu);
}

void vos3_sched_remove_task(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_sched_lock);

    /* Use QUEUED flag — works regardless of what state caller already set */
    if (task->flags & VOS3_TASK_FLAG_QUEUED) {
        /* [QUANTUM-LEAP v21.0.1] route via latency-class helper —
         * symmetric with rq_enqueue, otherwise an INTERACTIVE task
         * would leak between queues on remove. */
        rq_remove(rq_for_task(task), task);
        g_sched_stats.runnable_count--;
    }

    vos3_spinlock_unlock(&g_sched_lock);
    vos3_irq_restore(flags);
}

void vos3_sched_tick(void)
{
    uint32_t cpu_id = get_cpu_id();

    /* Only BSP updates global tick count */
    if (cpu_id == 0) {
        g_tick_count++;
        /* Process sleeping tasks (BSP only) */
        vos3_sched_process_sleepers();
        /* K-R2: Defer task reaping to process context — task_reap() acquires
         * g_reaper_lock which must never be taken from ISR context. */
        g_reap_pending = 1;
        /* The old timer path ran the complete AI monitor in hard-IRQ context.
         * Only publish work here; the process-context safe point below runs it. */
        __atomic_store_n(&g_ai_monitor_pending, 1, __ATOMIC_RELEASE);
        /* K-R3: Defer AI Guard reprotect to process context — modifies PTEs
         * via vos3_vmm_update_flags() which must not run in ISR context. */
        g_ai_guard_dirty = 1;
        /* K-R4: Defer TCP timer to process context — vos3_tcp_timer_tick()
         * acquires g_tcp_lock which deadlocks if taken from ISR while a
         * TCP operation is mid-flight.  Decimated to every 100ms (10 ticks)
         * since TCP timeouts are coarse (RTOs ≥ 200ms). */
        g_tcp_timer_acc++;
        if (g_tcp_timer_acc >= SCHED_TCP_TIMER_INTERVAL) {
            g_tcp_timer_acc = 0;
            g_tcp_work_pending = 1;
        }
        vos3_sched_request_deferred();
        /* Phase 3: AI load balancer — deferred to process context.
         * vos3_dispatch_balance_check() acquires g_task_lock which would
         * deadlock if called from ISR while a task holds that lock.
         * TODO(Task 5): Enable after regression validation.
         * vos3_dispatch_balance_request(); */
        /* Force reschedule check after processing sleepers —
         * without this, a woken task waits for idle's time_slice to expire */
        g_cpu_sched_state[0].need_reschedule = 1;
    }

    /* Re-arm owner-local reaping after the two-tick stack grace period and
     * for work that another CPU placed on the shared reaper list. */
    if (vos3_task_reap_pending()) vos3_sched_request_deferred();

    vos3_task_t* current = g_current_task[cpu_id];
    if (current == NULL) {
        return;
    }

    /* Track idle time */
    if ((current->flags & VOS3_TASK_FLAG_IDLE) != 0U) {
        g_sched_stats.idle_ticks++;
    }

    /* Update runtime */
    current->total_runtime++;

    /* Phase 30 / Task 1.5: Check alarm/itimer */
    if (current->alarm_deadline != 0ULL && g_tick_count >= current->alarm_deadline) {
        /* Reset or cancel depending on interval */
        if (current->itimer_interval_ticks != 0ULL) {
            /* Periodic: reschedule from now */
            current->alarm_deadline = g_tick_count + current->itimer_interval_ticks;
        } else {
            /* One-shot: cancel */
            current->alarm_deadline = 0ULL;
        }
        vos3_signal_send(current->tid, VOS3_SIGALRM);
    }

    /* Decrement time slice
     * [OLYMPUS-FIX P-04] Prefix decrement on a non-volatile field —
     * postfix forced a load/decrement/store pattern with a temporary
     * because the compiler had to honor the rvalue of the expression.
     * Prefix is in-place. Tiny win, but it lives on the 100Hz tick
     * path so it adds up. */
    if (current->time_slice > 0ULL) {
        --current->time_slice;
    }

    /* Check if preemption needed */
    if (current->time_slice == 0ULL) {
        /* Phase 6.1: Anti-preemption for active inference.
         * Tasks in INFERRING state get another full timeslice instead of
         * being preempted. This prevents mid-matmul context switches that
         * would thrash the L1D/L2 cache with partial tensor state.
         * Cap at 3 consecutive grants to prevent starvation. */
        if ((current->flags & VOS3_TASK_FLAG_AI_INFERENCE) &&
            current->inference_state == VOS3_INFERENCE_INFERRING &&
            current->inference_grants < 3U) {
            current->time_slice = (uint64_t)(current->priority + 1U) *
                                  VOS3_SCHED_DEFAULT_SLICE * 2U;
            current->inference_grants++;
            /* Do NOT set g_need_reschedule — task continues uninterrupted */
        } else {
            current->inference_grants = 0;
            current->involuntary_switches++;
            g_sched_stats.preemptions++;
            g_cpu_sched_state[cpu_id].need_reschedule = 1;
        }
    }
}

/**
 * @brief Process deferred work from ISR context
 *
 * Called from process context (syscall exit, reschedule, AP idle loop) to handle work
 * that was flagged during the timer ISR but cannot safely run there.
 * K-R2: task_reap() acquires locks.
 * K-R3: ai_guard_reprotect_tick() modifies PTEs.
 */
static uint32_t g_deferred_active[256];
static uint32_t g_deferred_pending[256];

void vos3_sched_request_deferred(void)
{
    vos3_sched_request_deferred_cpu(get_cpu_id());
}

void vos3_sched_request_deferred_cpu(uint32_t cpu_id)
{
    if (cpu_id < 256U)
        __atomic_store_n(&g_deferred_pending[cpu_id], 1U, __ATOMIC_RELEASE);
}

void vos3_sched_process_deferred(void)
{
    /* No resource reclamation from an interrupt/IRQ-disabled continuation. */
    uint64_t rflags;
    __asm__ volatile ("pushfq; popq %0" : "=r"(rflags));
    if (!(rflags & (1ULL << 9)))
        return; /* Interrupt/IRQ-disabled context is not a reclamation point. */
    /* A destructor may block/reschedule. Do not recursively drain work on
     * this CPU while a previous safe point is still in progress. Current
     * scheduling keeps executing contexts CPU-local (no live migration). */
    uint32_t cpu = get_cpu_id();
    if (cpu >= 256U)
        return;
    /* A zero observation consumes nothing: a racing producer leaves its
     * release-published bit for the next safe point. Avoid locked exchanges
     * on the frequent empty path; retain the guarded consuming path below. */
    if (__atomic_load_n(&g_deferred_pending[cpu], __ATOMIC_ACQUIRE) == 0U)
        return;
    if (__atomic_exchange_n(&g_deferred_active[cpu], 1U, __ATOMIC_ACQUIRE))
        return;
    /* Empty syscall safe points are frequent. Producers publish this bit when
     * they enqueue work, so the empty path takes no subsystem lock or scan. */
    if (__atomic_exchange_n(&g_deferred_pending[cpu], 0U,
                            __ATOMIC_ACQ_REL) == 0U) {
        __atomic_store_n(&g_deferred_active[cpu], 0U, __ATOMIC_RELEASE);
        return;
    }
    vos3_shm_reap_creators();
    vos3_vmm_reap_address_spaces();
    vos3_ai_guard_reap_contexts();
#ifdef NATIVE_SMP_TEST
    /* Every owner must revisit its dead tasks. */
    vos3_task_reap();
    if (cpu != 0)
        goto out;
#endif
    if (g_reap_pending != 0) {
        g_reap_pending = 0;
        vos3_task_reap();
    }
    if (__atomic_exchange_n(&g_ai_monitor_pending, 0,
                            __ATOMIC_ACQ_REL) != 0) {
        vos3_ai_monitor_tick();
    }
    if (g_ai_guard_dirty != 0) {
        g_ai_guard_dirty = 0;
        vos3_ai_guard_reprotect_tick();
    }
    /* K-R4: TCP timer — runs in process context so g_tcp_lock acquisition
     * cannot deadlock with in-flight TCP operations in other tasks. */
    if (g_tcp_work_pending != 0) {
        g_tcp_work_pending = 0;
        vos3_tcp_timer_tick();
    }
#ifdef NATIVE_SMP_TEST
out:
#endif
    __atomic_store_n(&g_deferred_active[cpu], 0U, __ATOMIC_RELEASE);
}

void vos3_sched_reschedule(void)
{
    uint32_t cpu_id = get_cpu_id();

    if (g_sched_running == 0) {
        return;
    }

    /* Disable interrupts for the entire reschedule sequence.
     * This prevents deadlock (timer ISR acquiring g_sched_lock while we
     * hold it) and races between g_current_task update and context switch. */
    vos3_irqflags_t flags = vos3_irq_save();

    vos3_spinlock_lock(&g_sched_lock);

    vos3_task_t* prev = g_current_task[cpu_id];
    put_prev_task(prev);

    vos3_task_t* next = pick_next_task();
    sched_reserve_switch_locked(prev, next, cpu_id);
    g_cpu_sched_state[cpu_id].need_reschedule = 0;

    vos3_spinlock_unlock(&g_sched_lock);

    if (next != prev) {
        do_context_switch(prev, next);
    } else {
        /* Same task re-selected: put_prev_task changed state to READY and
         * pick_next_task dequeued it.  Restore RUNNING state and reset the
         * time slice so this task isn't starved on the very next tick. */
        next->state = VOS3_TASK_RUNNING;
        next->time_slice = (next->priority == VOS3_PRIORITY_IDLE) ?
                           VOS3_SCHED_SLICE_IDLE :
                           ((uint64_t)(next->priority + 1U) *
                            VOS3_SCHED_DEFAULT_SLICE / 2U);
        /* Phase 3 + Batch F: Inference-aware timeslice for AI agents */
        if (next->flags & VOS3_TASK_FLAG_AI_AGENT) {
            if (next->inference_state == VOS3_INFERENCE_INFERRING) {
                next->time_slice *= 4;  /* 4x for active inference */
            } else {
                next->time_slice *= 2;  /* 2x for AI agents */
            }
        }
    }

    /* This continuation may have resumed on another CPU. The serviced
     * request was cleared before the switch, not through stale cpu_id here. */
    vos3_irq_restore(flags);

    /* K-R2/K-R3: Process deferred ISR work (reap + AI Guard reprotect)
     * in process context where lock acquisition and PTE modification
     * are safe. */
    vos3_sched_process_deferred();

    /* Deferred balance check: runs in process context (IRQs enabled),
     * safe to acquire g_task_lock.  Only runs on BSP (cpu 0).
     * TODO(Task 5): Enable after regression validation.
    if (cpu_id == 0 && vos3_dispatch_balance_pending()) {
        vos3_dispatch_balance_check();
    }
     */
}

void vos3_sched_yield(void)
{
    uint32_t cpu_id = get_cpu_id();
    vos3_task_t* current = g_current_task[cpu_id];
    if (current != NULL) {
        g_sched_stats.voluntary_switches++;
    }

    vos3_sched_reschedule();
}

vos3_task_t* vos3_sched_current(void)
{
    uint32_t cpu_id = get_cpu_id();
    return g_current_task[cpu_id];
}

vos3_task_t* vos3_sched_get_idle(void)
{
    uint32_t cpu_id = get_cpu_id();
    return g_idle_task[cpu_id];
}

void vos3_sched_get_stats(vos3_sched_stats_t* stats)
{
    if (stats == NULL) {
        return;
    }

    *stats = g_sched_stats;
}

void vos3_sched_print_stats(void)
{
    VOS3_INFO("Scheduler Statistics:");
    vos3_console_printf("  Total switches:     %llu\n",
                        (unsigned long long)g_sched_stats.total_switches);
    vos3_console_printf("  Voluntary yields:   %llu\n",
                        (unsigned long long)g_sched_stats.voluntary_switches);
    vos3_console_printf("  Preemptions:        %llu\n",
                        (unsigned long long)g_sched_stats.preemptions);
    vos3_console_printf("  Idle ticks:         %llu\n",
                        (unsigned long long)g_sched_stats.idle_ticks);
    vos3_console_printf("  Total tasks:        %llu\n",
                        (unsigned long long)g_sched_stats.task_count);
    vos3_console_printf("  Runnable:           %llu\n",
                        (unsigned long long)g_sched_stats.runnable_count);
    vos3_console_printf("  Uptime:             %llu ticks (%llu ms)\n",
                        (unsigned long long)g_tick_count,
                        (unsigned long long)vos3_sched_get_uptime_ms());
}

int vos3_sched_is_running(void)
{
    return g_sched_running;
}

uint64_t vos3_sched_get_ticks(void)
{
    return g_tick_count;
}

uint64_t vos3_sched_get_uptime_ms(void)
{
    return g_tick_count * VOS3_MS_PER_TICK;
}

/* ============================================================================
 * SLEEP QUEUE
 * ============================================================================ */

void vos3_sched_sleep_until(vos3_task_t* task, uint64_t wake_time)
{
    if (task == NULL) {
        return;
    }

    /* Disable interrupts to prevent deadlock: timer ISR acquires
     * g_sleep_lock in process_sleepers(). */
    vos3_irqflags_t flags = vos3_irq_save();

    vos3_spinlock_lock(&g_sleep_lock);

    task->wake_time = wake_time;
    task->state = VOS3_TASK_SLEEPING;

    /* Insert sorted by wake time */
    vos3_task_t** pp = &g_sleep_queue;
    while (*pp != NULL && (*pp)->wake_time <= wake_time) {
        pp = &(*pp)->next;
    }

    task->next = *pp;
    *pp = task;

    g_sched_stats.sleeping_count++;

    vos3_spinlock_unlock(&g_sleep_lock);

    vos3_irq_restore(flags);

    /* Remove from run queue (has its own irq_save) */
    vos3_sched_remove_task(task);
}

void vos3_sched_cancel_sleep(vos3_task_t* task)
{
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_sleep_lock);
    vos3_task_t** link = &g_sleep_queue;
    while (*link != NULL && *link != task)
        link = &(*link)->next;
    if (*link == task) {
        *link = task->next;
        task->next = NULL;
        task->wake_time = 0;
        g_sched_stats.sleeping_count--;
    }
    vos3_spinlock_unlock(&g_sleep_lock);
    vos3_irq_restore(flags);
}

void vos3_sched_process_sleepers(void)
{
    /* Disable interrupts for consistency with task-context lock users.
     * When called from the timer ISR, interrupts are already disabled
     * (hardware clears IF on interrupt entry), so this is a no-op. */
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_sleep_lock);

    while (g_sleep_queue != NULL && g_sleep_queue->wake_time <= g_tick_count) {
        vos3_task_t* task = g_sleep_queue;
        g_sleep_queue = task->next;
        task->next = NULL;

        g_sched_stats.sleeping_count--;

        /* Wake up the task */
        task->wake_time = 0ULL;

        vos3_spinlock_unlock(&g_sleep_lock);
        vos3_irq_restore(flags);

        VOS3_DEBUG("Waking task '%s'", task->name);
        vos3_task_wake(task);

        flags = vos3_irq_save();
        vos3_spinlock_lock(&g_sleep_lock);
    }

    vos3_spinlock_unlock(&g_sleep_lock);
    vos3_irq_restore(flags);
}

/* ============================================================================
 * SMP SCHEDULER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Pre-create idle tasks for all APs
 *
 * Called by BSP during SMP init to avoid heap contention when
 * multiple APs try to create tasks simultaneously.
 *
 * @param[in] cpu_count  Total number of CPUs (including BSP)
 * @return 0 on success, negative error on failure
 */
int vos3_sched_create_ap_idle_tasks(uint32_t cpu_count)
{
    VOS3_INFO("[SCHED] Pre-creating idle tasks for %u CPUs", cpu_count);

    for (uint32_t cpu_id = 1; cpu_id < cpu_count && cpu_id < 256; cpu_id++) {
        if (g_idle_task[cpu_id] != NULL) {
            continue;  /* Already created */
        }

        g_idle_task[cpu_id] = vos3_task_create_idle(cpu_id);
        if (g_idle_task[cpu_id] == NULL) {
            VOS3_ERROR("[SCHED] Failed to create idle task for CPU %u", cpu_id);
            return -1;
        }

        VOS3_DEBUG("[SCHED] Created idle task for CPU %u", cpu_id);
    }

    return 0;
}

/**
 * @brief Initialize scheduler for an Application Processor
 *
 * Sets up per-CPU state using the pre-created idle task.
 *
 * @param[in] cpu_id  Logical CPU ID for this AP
 * @return 0 on success, negative error on failure
 */
int vos3_sched_init_ap(uint32_t cpu_id)
{
    if (cpu_id == 0 || cpu_id >= 256) {
        return -1;  /* Invalid CPU ID or BSP */
    }

    /* Check if idle task was pre-created */
    if (g_idle_task[cpu_id] == NULL) {
        /* Fallback: try to create it now (may fail under contention) */
        g_idle_task[cpu_id] = vos3_task_create_idle(cpu_id);
        if (g_idle_task[cpu_id] == NULL) {
            VOS3_ERROR("[SCHED] Failed to create idle task for CPU %u", cpu_id);
            return -1;
        }
    }

    /* The AP bootstrap stack initially executes as its idle task. Keep
     * that identity reserved until the first real stack-switch ack. */
    vos3_irqflags_t init_flags = vos3_irq_save();
    vos3_spinlock_lock(&g_sched_lock);
    sched_reserve_switch_locked(NULL, g_idle_task[cpu_id], cpu_id);
    g_current_task[cpu_id] = g_idle_task[cpu_id];
    vos3_spinlock_unlock(&g_sched_lock);
    g_cpu_sched_state[cpu_id].need_reschedule = 0;
    g_cpu_sched_state[cpu_id].task_count = 0;

    /* Set initial current task in percpu structure */
    percpu_set_current(g_idle_task[cpu_id]);
    vos3_irq_restore(init_flags);

    VOS3_INFO("[SCHED] CPU %u scheduler initialized", cpu_id);

    return 0;
}

/**
 * @brief Enter the scheduler loop for an Application Processor
 *
 * This function is called by APs after initialization. It enables
 * interrupts and enters the scheduling loop, running tasks assigned
 * to this CPU.
 *
 * @note This function does not return
 */
__attribute__((noreturn))
void vos3_sched_loop_ap(void)
{
    uint32_t cpu_id = get_cpu_id();

    VOS3_INFO("[SCHED] CPU %u entering scheduler loop", cpu_id);

    /* Set idle task as running */
    vos3_task_t* idle = g_idle_task[cpu_id];
    idle->state = VOS3_TASK_RUNNING;
    idle->last_scheduled = g_tick_count;

    /* Main scheduler loop */
    for (;;) {
#ifdef NATIVE_SMP_TEST
        __asm__ volatile ("sti" ::: "memory");
#endif
        /* K-R2/K-R3: Process deferred ISR work (reap + AI Guard reprotect)
         * while idle — safe process context. */
        vos3_sched_process_deferred();

#ifdef NATIVE_SMP_TEST
        /* Disable IRQs before the pending check; STI;HLT closes lost wakeups. */
        __asm__ volatile ("cli" ::: "memory");
        if (g_cpu_sched_state[cpu_id].need_reschedule != 0) {
            vos3_sched_reschedule();
            continue;
        }
#endif

        /* Enable interrupts and halt until next interrupt */
        __asm__ volatile (
            "sti\n\t"
            "hlt\n\t"
            ::: "memory"
        );

        /* Check if reschedule needed */
        if (g_cpu_sched_state[cpu_id].need_reschedule != 0) {
            vos3_sched_reschedule();
        }
    }
}

/**
 * @brief Check if reschedule is pending for current CPU
 *
 * @return 1 if reschedule pending, 0 otherwise
 */
int vos3_sched_need_reschedule(void)
{
    uint32_t cpu_id = get_cpu_id();
    return g_cpu_sched_state[cpu_id].need_reschedule;
}
#ifdef NATIVE_SMP_TEST
void vos3_sched_request_reschedule(void)
{
    g_cpu_sched_state[get_cpu_id()].need_reschedule = 1;
}
#endif

/**
 * @brief Get task count for a specific CPU
 *
 * @param[in] cpu_id  CPU ID to query
 * @return Number of tasks assigned to that CPU
 */
uint32_t vos3_sched_get_cpu_task_count(uint32_t cpu_id)
{
    if (cpu_id >= 256) {
        return 0;
    }
    return g_cpu_sched_state[cpu_id].task_count;
}
