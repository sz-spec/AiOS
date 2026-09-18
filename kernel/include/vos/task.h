/**
 * @file task.h
 * @brief VOS3 Task/Process Management
 *
 * @details Task and thread structures for process management.
 *          Supports kernel threads and user processes.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_TASK_H
#define VOS3_TASK_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "../arch/x86_64/memory_map.h"

/* ============================================================================
 * TASK CONFIGURATION
 * ============================================================================ */

/** @brief Maximum number of tasks */
#define VOS3_MAX_TASKS          ((size_t)1024U)

/** @brief Maximum task name length */
#define VOS3_TASK_NAME_LEN      ((size_t)32U)

/** @brief Default kernel stack size per task (16 KiB) */
#define VOS3_TASK_KSTACK_SIZE   ((size_t)0x4000U)

/** @brief Default user stack size (64 KiB) */
#define VOS3_USTACK_SIZE        ((size_t)0x10000U)

/** @brief Task ID type */
typedef uint32_t vos3_tid_t;

/** @brief Process ID type */
typedef uint32_t vos3_pid_t;

/** @brief Invalid task/process ID */
#define VOS3_TID_INVALID        ((vos3_tid_t)0xFFFFFFFFU)
#define VOS3_PID_INVALID        ((vos3_pid_t)0xFFFFFFFFU)

/* ============================================================================
 * TASK STATES
 * ============================================================================ */

/** @brief Task states */
typedef enum vos3_task_state {
    VOS3_TASK_READY     = 0U,   /**< Ready to run */
    VOS3_TASK_RUNNING   = 1U,   /**< Currently running */
    VOS3_TASK_BLOCKED   = 2U,   /**< Blocked on resource */
    VOS3_TASK_SLEEPING  = 3U,   /**< Sleeping (timed wait) */
    VOS3_TASK_ZOMBIE    = 4U,   /**< Terminated, waiting for cleanup */
    VOS3_TASK_DEAD      = 5U    /**< Fully terminated */
} vos3_task_state_t;

/** @brief Task flags */
typedef enum vos3_task_flags {
    VOS3_TASK_FLAG_NONE     = 0U,
    VOS3_TASK_FLAG_KERNEL   = (1U << 0),    /**< Kernel task */
    VOS3_TASK_FLAG_USER     = (1U << 1),    /**< User task */
    VOS3_TASK_FLAG_IDLE     = (1U << 2),    /**< Idle task */
    VOS3_TASK_FLAG_PINNED   = (1U << 3),    /**< Pinned to CPU */
    VOS3_TASK_FLAG_APP      = (1U << 4),    /**< Sandboxed app task (Phase N) */
    VOS3_TASK_FLAG_THREAD   = (1U << 5),    /**< Thread — shares address space via clone() */
    VOS3_TASK_FLAG_QUEUED   = (1U << 6),    /**< Currently in a scheduler run queue */
    VOS3_TASK_FLAG_AI_AGENT     = (1U << 7),  /**< AI agent — gets extended timeslice */
    VOS3_TASK_FLAG_AI_INFERENCE = (1U << 8),  /**< Phase 6.1: Active inference — anti-preemption */
} vos3_task_flags_t;

/** @brief Inference scheduling states (Batch F: Inference-Aware Scheduling) */
#define VOS3_INFERENCE_IDLE      0
#define VOS3_INFERENCE_LOADING   1
#define VOS3_INFERENCE_INFERRING 2
#define VOS3_INFERENCE_YIELDING  3

/** @brief Task priority levels */
typedef enum vos3_task_priority {
    VOS3_PRIORITY_IDLE      = 0U,
    VOS3_PRIORITY_LOW       = 1U,
    VOS3_PRIORITY_NORMAL    = 2U,
    VOS3_PRIORITY_HIGH      = 3U,
    VOS3_PRIORITY_REALTIME  = 4U,
    VOS3_PRIORITY_COUNT     = 5U
} vos3_task_priority_t;

/* ============================================================================
 * CPU CONTEXT
 * ============================================================================ */

/**
 * @brief CPU context saved during context switch
 * @note Only callee-saved registers need to be saved
 */
typedef struct __attribute__((packed)) vos3_context {
    /* Callee-saved registers (System V AMD64 ABI) */
    uint64_t r15;
    uint64_t r14;
    uint64_t r13;
    uint64_t r12;
    uint64_t rbx;
    uint64_t rbp;

    /* Return address (for context switch) */
    uint64_t rip;
} vos3_context_t;

/**
 * @brief Full CPU state (for interrupts/syscalls)
 */
typedef struct __attribute__((packed)) vos3_cpu_state {
    /* General purpose registers */
    uint64_t r15, r14, r13, r12, r11, r10, r9, r8;
    uint64_t rbp, rdi, rsi, rdx, rcx, rbx, rax;

    /* Interrupt info */
    uint64_t int_num;
    uint64_t error_code;

    /* CPU-pushed on interrupt */
    uint64_t rip;
    uint64_t cs;
    uint64_t rflags;
    uint64_t rsp;
    uint64_t ss;
} vos3_cpu_state_t;

/* ============================================================================
 * WAIT QUEUE ENTRY (needed before task struct for embedding)
 * ============================================================================ */

/**
 * @brief Wait queue entry — embedded in vos3_task_t to avoid dangling
 *        stack pointers when a task blocks on a wait queue.
 */
struct vos3_wait_queue;
typedef struct vos3_wait_entry {
    struct vos3_task* task;         /**< Waiting task */
    struct vos3_wait_entry* next;   /**< Next in queue */
    struct vos3_wait_queue* queue;  /**< Owning queue, NULL when detached */
} vos3_wait_entry_t;

/* ============================================================================
 * TASK STRUCTURE
 * ============================================================================ */

/* Forward declarations */
struct vos3_task;
struct vos3_address_space;
struct vos3_fd_table;
struct vos3_dentry;
struct vos3_ai_guard_ctx;

/**
 * @brief Task entry function type
 */
typedef void (*vos3_task_entry_t)(void* arg);

/** @brief Cleanup for a resource pin held while a task is blocked. */
typedef void (*vos3_task_wait_cleanup_t)(void* context);

/**
 * @brief Task structure
 */
typedef struct vos3_task {
    /* ===== Identity (cache line 0) ===== */
    vos3_tid_t          tid;            /**< Task ID */
    vos3_pid_t          pid;            /**< Process ID */
    char                name[VOS3_TASK_NAME_LEN]; /**< Task name */

    /* ===== State ===== */
    vos3_task_state_t   state;          /**< Current state */
    vos3_task_flags_t   flags;          /**< Task flags */
    vos3_task_priority_t priority;      /**< Priority level */
    uint32_t            cpu_id;         /**< Current/last CPU */

    /* ===== Scheduling ===== */
    uint64_t            time_slice;     /**< Remaining time slice (ticks) */
    uint64_t            total_runtime;  /**< Total runtime (ticks) */
    uint64_t            last_scheduled; /**< Last schedule time */
    uint64_t            wake_time;      /**< Wake time for sleeping tasks */

    /* ===== Context ===== */
    vos3_context_t*     context;        /**< Saved context pointer */
    uint64_t            kernel_rsp;     /**< Kernel stack pointer */

    /* ===== Fork Return State ===== */
    uint64_t            fork_ret_rip;   /**< User RIP for fork child return */
    uint64_t            fork_ret_rsp;   /**< User RSP for fork child return */
    uint64_t            fork_ret_rflags;/**< User RFLAGS for fork child return */
    /* Callee-saved registers (must be restored in child) */
    uint64_t            fork_ret_rbx;   /**< RBX for fork child return */
    uint64_t            fork_ret_rbp;   /**< RBP for fork child return */
    uint64_t            fork_ret_r12;   /**< R12 for fork child return */
    uint64_t            fork_ret_r13;   /**< R13 for fork child return */
    uint64_t            fork_ret_r14;   /**< R14 for fork child return */
    uint64_t            fork_ret_r15;   /**< R15 for fork child return */
    uint64_t            fork_ret_r8;    /**< R8 for clone child return (caller-saved) */
    uint64_t            fork_ret_r9;    /**< R9 for clone child return (thread func ptr) */
    uint64_t            fork_ret_r10;   /**< R10 for clone child return (caller-saved) */
    int                 is_fork_child;  /**< Flag: 1 if child needs fork return */
    /* §4.1 (v20.6): explicit 4-byte hole between `int is_fork_child` and the
     * 8-aligned `void* kernel_stack`. Compiler emitted this implicitly
     * before; making it explicit documents the layout. */
    uint32_t            _pad_after_fork_flag;

    /* ===== Stacks ===== */
    void*               kernel_stack;   /**< Kernel stack base */
    size_t              kernel_stack_size;
    uintptr_t           kernel_stack_guard; /**< Guard page addr at bottom of kernel stack (0=none) */
    void*               user_stack;     /**< User stack base (if user task) */
    size_t              user_stack_size;

    /* ===== Memory ===== */
    struct vos3_address_space* address_space; /**< Address space */
    struct vos3_ai_guard_ctx*  ai_guard_ctx;  /**< AI memory guard context */

    /* ===== File System ===== */
    struct vos3_fd_table* fd_table;     /**< File descriptor table */
    struct vos3_dentry*   cwd;          /**< Current working directory */

    /* ===== Linked list ===== */
    struct vos3_task*   next;           /**< Next task in queue */
    struct vos3_task*   prev;           /**< Previous task in queue */

    /* ===== Parent/Child ===== */
    struct vos3_task*   parent;         /**< Parent task */
    struct vos3_task*   children;       /**< First child */
    struct vos3_task*   sibling;        /**< Next sibling */

    /* ===== Exit info =====
     * §4.1 (v20.6): reordered uint64 → int + explicit pad to remove the
     * implicit 4-byte hole between exit_code and reap_after_tick. */
    uint64_t            reap_after_tick;/**< Earliest tick at which reaper may free this task */
    int                 exit_code;      /**< Exit code */
    uint32_t            _pad_after_exit_code;

    /* ===== Credentials ===== */
    uint32_t            uid;            /**< User ID */
    uint32_t            gid;            /**< Group ID */
    uint32_t            euid;           /**< Effective user ID */
    uint32_t            egid;           /**< Effective group ID */

    /* ===== Session/Process Group ===== */
    uint32_t            sid;            /**< Session ID */
    uint32_t            pgid;           /**< Process group ID */

    /* ===== Alarm Timer (Phase 30) ===== */
    uint64_t            alarm_deadline; /**< Tick count when SIGALRM fires (0 = no alarm) */

    /* ===== App Isolation (Phase N) ===== */
    uint8_t             app_id;           /**< App ID (0-7, 0 = system) */
    uint8_t             inference_state;  /**< Inference scheduling state (Batch F) */
    uint8_t             inference_grants; /**< Phase 6.1: Anti-preemption grants remaining */
    uint8_t             _app_pad[5];     /**< Alignment padding */
    uint64_t            cpu_ticks_used; /**< CPU ticks consumed by this task */
    uint64_t            cpu_ticks_limit;/**< Max CPU ticks (0 = unlimited) */

    /* ===== Statistics ===== */
    uint64_t            context_switches; /**< Context switch count */
    uint64_t            voluntary_switches; /**< Voluntary switches */
    uint64_t            involuntary_switches; /**< Preemption count */

    /* ===== Threading (Task 1.4) ===== */
    vos3_pid_t          tgid;               /**< Thread group ID (= pid for process leader) */
    struct vos3_task*   thread_group_leader;/**< Main thread of this process group */
    struct vos3_task*   thread_next;        /**< Next thread in same process group */
    uint32_t            is_thread;          /**< 1 if created by clone(CLONE_VM) */
    uint64_t            tls_base;           /**< %fs base for TLS — written to MSR_FS_BASE on ctx switch */
    uint64_t            user_gs_base;       /**< User GS shadow; never the kernel entry-state base */
    volatile uint32_t*  clear_child_tid;    /**< Write 0 + futex_wake on exit (CLONE_CHILD_CLEARTID) */

    /* ===== Interval Timer (Task 1.5) ===== */
    uint64_t            itimer_interval_ticks; /**< ITIMER_REAL repeat interval in ticks (0 = one-shot) */

    /* ===== Executable path (Task 2.5) ===== */
    char                exe_path[256];   /**< Full path of executed binary */

    /* ===== FPU/SSE/AVX Lazy Context Switch (Phase 1.2) ===== */
    void*               fpu_state_raw;   /**< Raw allocation pointer (for kfree) */
    uint8_t*            fpu_state;       /**< 64-byte aligned XSAVE/FXSAVE area */
    uint32_t            fpu_initialized; /**< 1 if FPU state has been saved at least once */

    /* ===== Per-process mmap bump allocator (Phase v17 K-C5) ===== */
    uint64_t            mmap_next;      /**< Legacy layout field; AS mmap_next is authoritative */

    /* ===== Wait Queue Entry (embedded to avoid stack-allocated dangling pointers) ===== */
    vos3_wait_entry_t   wq_entry;       /**< Embedded wait queue entry for blocking */

    /* ===== CET Shadow Stack (Directive 4) ===== */
    uint64_t            shadow_stack_base;  /**< Shadow stack base VA (0 = not allocated) */

    /* ===== Two-Plane Scheduler hint =====
     * [QUANTUM-LEAP-SCAFFOLD] HOME-DOMINANCE v21.0  (revised v21.0.4 APEX-VERIFY)
     *
     * 2-bit latency class consumed by the Two-Plane dispatcher.
     * Default 0 == VOS3_LC_BATCH so that pre-v21 task structs (created
     * by callers that never zero-initialise this field, or that BSS-
     * default it) fall into the throughput plane — preserving v20.x
     * priority-queue behavior. New callers that want the interactive
     * fast-path must explicitly set latency_class = VOS3_LC_INTERACTIVE.
     * See kernel/include/vos/dispatch_hint.h for the full taxonomy.
     */
    uint8_t             latency_class;      /**< vos3_latency_class_t — 0..3 */
    uint8_t             _qlscaffold_pad[7]; /**< reserved for future hints   */

    /* ===== Per-task XSAVE area =====
     * [OLYMPUS-FIX APEX-HOME E1 — LOGIC-COMPLETE v21.2.2]
     *
     * Holds the XSAVE/XRSTOR state for this task's extended CPU
     * registers (x87, SSE, AVX, AVX-512 if enabled). Allocated in
     * vos3_task_create() based on the runtime size reported by
     * vos3_xsave_get_area_size() (CPUID(0xD,0).ECX). Freed in
     * vos3_task_destroy().
     *
     * The pointer is non-NULL on every task created after the XSAVE
     * subsystem has probed, but the area is NOT yet consumed by
     * context_switch.S — that wiring lives in v21.3.x-XSAVE-WIRE
     * (assembly change). When context_switch.S is updated to call
     * vos3_xsave_save(task->xsave_area, ...) on switch-out and
     * vos3_xsave_restore(...) on switch-in, no further changes to
     * vos3_task_t are required.
     */
    void               *xsave_area;       /**< 64-byte aligned XSAVE buffer or NULL */
    void               *xsave_area_raw;   /**< raw kzalloc pointer (for free)        */
    uint32_t            xsave_area_size;  /**< bytes — from vos3_xsave_get_area_size */
    uint32_t            _xsave_pad;

    /* ===== Cyber overlay (Stage 2): SCHED_CORE cookie (SMT isolation) =====
     * Mirrors Linux core_cookie semantics. Two tasks may co-execute on
     * sibling logical CPUs of the same physical core iff they carry the
     * same cookie. Used by core_cookie.c (Z3-proven O(1) sibling table).
     */
    uint64_t            core_cookie;        /**< SMT-isolation cookie (0 = any) */

#ifdef NATIVE_SMP_TEST
    /* Immutable after first dispatch, under g_sched_lock. Zero means unbound.
     * Appended so assembly offsets of existing members do not change. */
    uint32_t sched_owner_plus_one;
#endif
#ifdef NATIVE_SMP_WORKLOAD
    uint32_t native_smp_reported;
#endif
    /* Immutable per-registration security principal; never a PID/TID alias.
     * Assigned before publication; fork/clone registration replaces copied value. */
    uint64_t identity_cookie;
    struct vos3_signal_state* signal_state; /**< Owned; appended to preserve assembly offsets. */
    vos3_task_wait_cleanup_t wait_cleanup;  /**< Claimed once by resume or final reaper. */
    void* wait_cleanup_context;             /**< Context published before wait_cleanup. */
    /* CPU/stack reservation, not a cancellation or lifetime reference.
     * Zero: not executing; cpu+1: reserved/running/switching out;
     * UINT32_MAX: reclamation claimed. Mutated by scheduler protocol only. */
    uint32_t sched_execution_owner;
    uint32_t retirement_started;             /**< At most one reaper publication. */
    struct vos3_task* reaper_next;           /**< Never aliases run/sleep links. */
} __attribute__((aligned(VOS3_CACHE_LINE_SIZE))) vos3_task_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the task subsystem
 * @return 0 on success, negative error code on failure
 */
int vos3_task_init(void);

/**
 * @brief Create a new kernel task
 * @param[in] name Task name
 * @param[in] entry Entry function
 * @param[in] arg Argument to entry function
 * @param[in] priority Task priority
 * @return Task pointer, or NULL on failure
 */
vos3_task_t* vos3_task_create(const char* name,
                               vos3_task_entry_t entry,
                               void* arg,
                               vos3_task_priority_t priority);

/**
 * @brief Create the idle task
 * @param[in] cpu_id CPU ID for this idle task
 * @return Task pointer, or NULL on failure
 */
vos3_task_t* vos3_task_create_idle(uint32_t cpu_id);

/**
 * @brief Destroy a task
 * @param[in] task Task to destroy
 */
/* Terminal tasks are deferred, never freed synchronously. Live/unpublished
 * objects require their construction/exit owner, and return ERR_INVALID. */
int vos3_task_destroy(vos3_task_t* task);

/**
 * @brief Defer destruction of a task (reaper pattern)
 *
 * Removes the task from the task table and enqueues it for deferred
 * resource cleanup.  The actual freeing happens in vos3_task_reap().
 * This avoids freeing a kernel stack that may still be referenced.
 *
 * @param[in] task Task to defer-destroy (must be in ZOMBIE state)
 */
void vos3_task_defer_destroy(vos3_task_t* task);

/** @return nonzero while at least one task awaits deferred destruction. */
int vos3_task_reap_pending(void);

/**
 * @brief Register one resource pin that must survive a blocking wait.
 * @return 1 when armed, 0 if the task is terminal/invalid, -1 if cleanup won.
 */
int vos3_task_arm_wait_cleanup(vos3_task_t* task,
                               vos3_task_wait_cleanup_t cleanup,
                               void* context);

/**
 * @brief Claim a registered pin back after a normal wake.
 * @return 1 if the caller reclaimed the pin, 0 if no matching pin remained.
 */
int vos3_task_disarm_wait_cleanup(vos3_task_t* task,
                                  vos3_task_wait_cleanup_t cleanup,
                                  void* context);

/** @brief Run a stranded wait cleanup after the task is permanently quiescent. */
void vos3_task_run_wait_cleanup(vos3_task_t* task);

/**
 * @brief Reap dead tasks (free deferred resources)
 *
 * Drains the reaper queue and frees kernel stacks, fd tables,
 * address spaces, and task structs.  Safe to call from idle loop
 * or scheduler tick — dead tasks are guaranteed not to be current.
 */
void vos3_task_reap(void);

/**
 * @brief Get current running task
 * @return Current task pointer
 */
vos3_task_t* vos3_task_current(void);

/**
 * @brief Register an externally-created task in the task table
 * @param[in] task Task to register (must have valid tid)
 * @return 0 on success, -1 if table full
 */
int vos3_task_register(vos3_task_t* task);

/** Allocate a system-wide task/thread ID using the shared atomic sequence. */
vos3_tid_t vos3_task_alloc_tid(void);

/**
 * @brief Get task by ID
 * @param[in] tid Task ID
 * @return Task pointer, or NULL if not found
 */
vos3_task_t* vos3_task_get(vos3_tid_t tid);

/**
 * @brief Find task by PID
 * @param[in] pid Process ID
 * @return Task pointer, or NULL if not found
 */
vos3_task_t* vos3_task_find_by_pid(vos3_pid_t pid);

/**
 * @brief Set task state
 * @param[in] task Task to modify
 * @param[in] state New state
 */
void vos3_task_set_state(vos3_task_t* task, vos3_task_state_t state);

/**
 * @brief Set task priority
 * @param[in] task Task to modify
 * @param[in] priority New priority
 */
void vos3_task_set_priority(vos3_task_t* task, vos3_task_priority_t priority);

/**
 * @brief Count active tasks and zombie tasks
 * @param[out] nr_tasks   Total tasks in task table (may be NULL)
 * @param[out] nr_zombies Zombie tasks count (may be NULL)
 */
void vos3_task_count_stats(uint32_t* nr_tasks, uint32_t* nr_zombies);

/**
 * @brief Yield CPU to other tasks
 */
void vos3_task_yield(void);

/**
 * @brief Sleep for specified ticks
 * @param[in] ticks Number of timer ticks to sleep
 */
void vos3_task_sleep(uint64_t ticks);

/**
 * @brief Sleep for specified milliseconds
 * @param[in] ms Milliseconds to sleep
 */
void vos3_task_sleep_ms(uint64_t ms);

/**
 * @brief Wake a sleeping task
 * @param[in] task Task to wake
 */
void vos3_task_wake(vos3_task_t* task);

/**
 * @brief Exit current task
 * @param[in] exit_code Exit code
 * @note This function does not return
 */
__attribute__((noreturn))
void vos3_task_exit(int exit_code);

/**
 * Consume a task's CLONE_CHILD_CLEARTID registration exactly once.
 * When usercopy is unsafe (for example while handling a user page fault),
 * pass allow_usercopy=0; waiters are still notified without dereferencing
 * the user pointer.
 */
void vos3_task_release_clear_child_tid(vos3_task_t* task, int allow_usercopy);

/**
 * @brief Kill a task with a signal
 * @param[in] task Task to kill
 * @param[in] signal Signal number (e.g. 9 for SIGKILL)
 */
void vos3_task_kill(vos3_task_t* task, int signal);

/**
 * @brief Block current task
 */
void vos3_task_block(void);

/**
 * @brief Unblock a task
 * @param[in] task Task to unblock
 */
void vos3_task_unblock(vos3_task_t* task);

/**
 * @brief Set a task into sandboxed app mode (Phase N)
 * @param[in] task Task to configure
 * @param[in] app_id Application ID (0-7)
 * @return 0 on success, negative error code on failure
 */
int vos3_task_set_app_mode(vos3_task_t* task, uint8_t app_id);

/**
 * @brief Set CPU tick limit for a task (Phase N.2.3)
 * @param[in] task Task to configure
 * @param[in] limit Maximum ticks (0 = unlimited)
 */
void vos3_task_set_cpu_limit(vos3_task_t* task, uint64_t limit);

/**
 * @brief Check and enforce CPU quota for current task (Phase N.2.3)
 * Called from scheduler tick handler.
 */
void vos3_task_check_cpu_quota(void);

/* ============================================================================
 * TASK STATE HELPERS
 * ============================================================================ */

/**
 * @brief Check if task is runnable
 */
static inline int vos3_task_is_runnable(const vos3_task_t* task)
{
    return (task->state == VOS3_TASK_READY ||
            task->state == VOS3_TASK_RUNNING) ? 1 : 0;
}

/**
 * @brief Check if task is kernel task
 */
static inline int vos3_task_is_kernel(const vos3_task_t* task)
{
    return (task->flags & VOS3_TASK_FLAG_KERNEL) != 0U ? 1 : 0;
}

/**
 * @brief Check if task is idle task
 */
static inline int vos3_task_is_idle(const vos3_task_t* task)
{
    return (task->flags & VOS3_TASK_FLAG_IDLE) != 0U ? 1 : 0;
}

/**
 * @brief Check if task is sandboxed app task (Phase N)
 */
static inline int vos3_task_is_app(const vos3_task_t* task)
{
    return (task->flags & VOS3_TASK_FLAG_APP) != 0U ? 1 : 0;
}

/**
 * @brief Get task state name
 */
static inline const char* vos3_task_state_name(vos3_task_state_t state)
{
    static const char* names[] = {
        "READY", "RUNNING", "BLOCKED", "SLEEPING", "ZOMBIE", "DEAD"
    };
    return (state < 6U) ? names[state] : "UNKNOWN";
}

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_TASK_OK            (0)
#define VOS3_TASK_ERR_NOMEM     (-1)
#define VOS3_TASK_ERR_INVALID   (-2)
#define VOS3_TASK_ERR_NOTFOUND  (-3)
#define VOS3_TASK_ERR_LIMIT     (-4)

/* ============================================================================
 * FPU/SSE/AVX LAZY CONTEXT SWITCH (Phase 1.2)
 * ============================================================================ */

/**
 * @brief Initialize FPU subsystem (detect XSAVE, enable CR4.OSXSAVE)
 * Call after CPU detection and before scheduler start.
 */
void vos3_fpu_init(void);

/**
 * @brief Clear FPU ownership for a dying task
 * Must be called before freeing a task that may own FPU state.
 */
void vos3_fpu_release_owner(struct vos3_task* task);
void vos3_fpu_switch_out(struct vos3_task* task);
int vos3_fpu_task_owned(const struct vos3_task* task);

/**
 * @brief Set CR0.TS bit for lazy FPU switching
 * Called from context switch path.
 */
void vos3_fpu_set_ts(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_TASK_H */
