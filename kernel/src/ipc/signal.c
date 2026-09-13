/**
 * @file signal.c
 * @brief VOS3 Signal Implementation
 *
 * @details Asynchronous signal delivery to tasks.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/ipc.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/user.h"

/* ============================================================================
 * SIGNAL PENDING STORAGE
 * ============================================================================ */

/*
 * Signal pending info is stored per-task.
 * We use a simple array indexed by task ID.
 * In a real implementation, this would be part of the task structure.
 */

/** @brief Signal pending info per task */
static vos3_sigpending_t* g_sig_pending[VOS3_MAX_TASKS];

/** @brief Signal lock */
static vos3_spinlock_t g_sig_lock = VOS3_SPINLOCK_INIT;

/* ============================================================================
 * DEFAULT ACTIONS
 * ============================================================================ */

/**
 * @brief Default action for signals
 */
typedef enum vos3_sigdefault {
    VOS3_SIGDEF_TERM,       /**< Terminate process */
    VOS3_SIGDEF_CORE,       /**< Terminate with core dump */
    VOS3_SIGDEF_STOP,       /**< Stop process */
    VOS3_SIGDEF_CONT,       /**< Continue if stopped */
    VOS3_SIGDEF_IGN,        /**< Ignore */
} vos3_sigdefault_t;

/** @brief Default actions for standard signals */
static const vos3_sigdefault_t g_sig_defaults[VOS3_SIG_MAX + 1] = {
    [0]             = VOS3_SIGDEF_IGN,
    [VOS3_SIGHUP]   = VOS3_SIGDEF_TERM,   /*  1 */
    [VOS3_SIGINT]   = VOS3_SIGDEF_TERM,   /*  2 */
    [VOS3_SIGQUIT]  = VOS3_SIGDEF_CORE,   /*  3 */
    [VOS3_SIGILL]   = VOS3_SIGDEF_CORE,   /*  4 */
    [VOS3_SIGTRAP]  = VOS3_SIGDEF_CORE,   /*  5 */
    [VOS3_SIGABRT]  = VOS3_SIGDEF_CORE,   /*  6 */
    [VOS3_SIGBUS]   = VOS3_SIGDEF_CORE,   /*  7 */
    [VOS3_SIGFPE]   = VOS3_SIGDEF_CORE,   /*  8 */
    [VOS3_SIGKILL]  = VOS3_SIGDEF_TERM,   /*  9 */
    [VOS3_SIGUSR1]  = VOS3_SIGDEF_TERM,   /* 10 */
    [VOS3_SIGSEGV]  = VOS3_SIGDEF_CORE,   /* 11 */
    [VOS3_SIGUSR2]  = VOS3_SIGDEF_TERM,   /* 12 */
    [VOS3_SIGPIPE]  = VOS3_SIGDEF_TERM,   /* 13 */
    [VOS3_SIGALRM]  = VOS3_SIGDEF_TERM,   /* 14 */
    [VOS3_SIGTERM]  = VOS3_SIGDEF_TERM,   /* 15 */
    [16]            = VOS3_SIGDEF_IGN,     /* Reserved (SIGSTKFLT on Linux) */
    [VOS3_SIGCHLD]  = VOS3_SIGDEF_IGN,    /* 17 */
    [VOS3_SIGCONT]  = VOS3_SIGDEF_CONT,   /* 18 */
    [VOS3_SIGSTOP]  = VOS3_SIGDEF_STOP,   /* 19 */
    [VOS3_SIGTSTP]  = VOS3_SIGDEF_STOP,   /* 20 */
    [21]            = VOS3_SIGDEF_STOP,    /* SIGTTIN  — background read */
    [22]            = VOS3_SIGDEF_STOP,    /* SIGTTOU  — background write */
    [23]            = VOS3_SIGDEF_IGN,     /* SIGURG   — urgent socket data */
    [24]            = VOS3_SIGDEF_CORE,    /* SIGXCPU  — CPU time limit */
    [25]            = VOS3_SIGDEF_CORE,    /* SIGXFSZ  — file size limit */
    [26]            = VOS3_SIGDEF_TERM,    /* SIGVTALRM — virtual alarm */
    [27]            = VOS3_SIGDEF_TERM,    /* SIGPROF  — profiling timer */
    [28]            = VOS3_SIGDEF_IGN,     /* SIGWINCH — window resize */
    [29]            = VOS3_SIGDEF_TERM,    /* SIGIO    — I/O possible */
    [30]            = VOS3_SIGDEF_TERM,    /* SIGPWR   — power failure */
    [31]            = VOS3_SIGDEF_CORE,    /* SIGSYS   — bad syscall */
};

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Get or create signal pending info for a task
 */
static vos3_sigpending_t* sig_get_pending(vos3_tid_t tid)
{
    if (tid >= VOS3_MAX_TASKS) {
        return NULL;
    }

    if (g_sig_pending[tid] == NULL) {
        g_sig_pending[tid] = (vos3_sigpending_t*)vos3_kzalloc(sizeof(vos3_sigpending_t));
        if (g_sig_pending[tid] != NULL) {
            /* Initialize with default handlers */
            for (int i = 0; i <= VOS3_SIG_MAX; i++) {
                g_sig_pending[tid]->actions[i].handler = VOS3_SIG_DFL;
                g_sig_pending[tid]->actions[i].mask = 0U;
                g_sig_pending[tid]->actions[i].flags = 0U;
            }
        }
    }

    return g_sig_pending[tid];
}

/**
 * @brief Check if signal can be caught/ignored
 */
static int sig_can_catch(int signum)
{
    /* SIGKILL and SIGSTOP cannot be caught or ignored */
    return (signum != VOS3_SIGKILL && signum != VOS3_SIGSTOP) ? 1 : 0;
}

/**
 * @brief Execute default action for signal
 */
static void sig_do_default(vos3_task_t* task, int signum)
{
    if (signum <= 0 || signum > VOS3_SIG_MAX) {
        return;
    }

    vos3_sigdefault_t action = g_sig_defaults[signum];

    switch (action) {
        case VOS3_SIGDEF_TERM:
            VOS3_INFO("Task '%s' terminated by signal %d", task->name, signum);
            vos3_task_exit(128 + signum);
            break;

        case VOS3_SIGDEF_CORE:
            VOS3_INFO("Task '%s' core dumped by signal %d", task->name, signum);
            /* TODO: Generate core dump */
            vos3_task_exit(128 + signum);
            break;

        case VOS3_SIGDEF_STOP:
            VOS3_INFO("Task '%s' stopped by signal %d", task->name, signum);
            vos3_task_set_state(task, VOS3_TASK_BLOCKED);
            vos3_sched_yield();
            break;

        case VOS3_SIGDEF_CONT:
            VOS3_INFO("Task '%s' continued by signal %d", task->name, signum);
            if (task->state == VOS3_TASK_BLOCKED) {
                vos3_task_wake(task);
            }
            break;

        case VOS3_SIGDEF_IGN:
            /* Do nothing */
            break;
    }
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

void vos3_signal_init_task(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    vos3_spinlock_lock(&g_sig_lock);

    /* Allocate signal pending info */
    vos3_sigpending_t* sp = sig_get_pending(task->tid);
    if (sp != NULL) {
        sp->pending = 0U;
        sp->blocked = 0U;
    }

    vos3_spinlock_unlock(&g_sig_lock);
}

int vos3_signal_send(vos3_tid_t tid, int signum)
{
    if (signum <= 0 || signum > VOS3_SIG_MAX) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_task_t* task = vos3_task_get(tid);
    if (task == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    vos3_spinlock_lock(&g_sig_lock);

    vos3_sigpending_t* sp = sig_get_pending(tid);
    if (sp == NULL) {
        vos3_spinlock_unlock(&g_sig_lock);
        return VOS3_IPC_ERR_NOMEM;
    }

    /* Set signal bit in pending mask */
    sp->pending |= (1U << (uint32_t)signum);

    vos3_spinlock_unlock(&g_sig_lock);

    VOS3_DEBUG("Signal %d sent to task '%s' (tid=%u)", signum, task->name, tid);

    /* Wake task if it's sleeping/blocked */
    if (task->state == VOS3_TASK_SLEEPING || task->state == VOS3_TASK_BLOCKED) {
        vos3_task_wake(task);
    }

    return VOS3_IPC_OK;
}

vos3_sighandler_t vos3_signal_handler(int signum, vos3_sighandler_t handler)
{
    if (signum <= 0 || signum > VOS3_SIG_MAX) {
        return VOS3_SIG_ERR;
    }

    if (sig_can_catch(signum) == 0) {
        return VOS3_SIG_ERR;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return VOS3_SIG_ERR;
    }

    vos3_spinlock_lock(&g_sig_lock);

    vos3_sigpending_t* sp = sig_get_pending(current->tid);
    if (sp == NULL) {
        vos3_spinlock_unlock(&g_sig_lock);
        return VOS3_SIG_ERR;
    }

    vos3_sighandler_t old = sp->actions[signum].handler;
    sp->actions[signum].handler = handler;

    vos3_spinlock_unlock(&g_sig_lock);

    return old;
}

int vos3_sigaction(int signum, const vos3_sigaction_t* act,
                   vos3_sigaction_t* oldact)
{
    if (signum <= 0 || signum > VOS3_SIG_MAX) {
        return VOS3_IPC_ERR_INVALID;
    }

    if (act != NULL && sig_can_catch(signum) == 0) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_spinlock_lock(&g_sig_lock);

    vos3_sigpending_t* sp = sig_get_pending(current->tid);
    if (sp == NULL) {
        vos3_spinlock_unlock(&g_sig_lock);
        return VOS3_IPC_ERR_NOMEM;
    }

    if (oldact != NULL) {
        *oldact = sp->actions[signum];
    }

    if (act != NULL) {
        sp->actions[signum] = *act;
    }

    vos3_spinlock_unlock(&g_sig_lock);

    return VOS3_IPC_OK;
}

int vos3_sigprocmask(int how, const uint32_t* set, uint32_t* oldset)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_spinlock_lock(&g_sig_lock);

    vos3_sigpending_t* sp = sig_get_pending(current->tid);
    if (sp == NULL) {
        vos3_spinlock_unlock(&g_sig_lock);
        return VOS3_IPC_ERR_NOMEM;
    }

    if (oldset != NULL) {
        *oldset = sp->blocked;
    }

    if (set != NULL) {
        /* Cannot block SIGKILL or SIGSTOP */
        uint32_t mask = *set & ~((1U << VOS3_SIGKILL) | (1U << VOS3_SIGSTOP));

        switch (how) {
            case VOS3_SIG_BLOCK:
                sp->blocked |= mask;
                break;

            case VOS3_SIG_UNBLOCK:
                sp->blocked &= ~mask;
                break;

            case VOS3_SIG_SETMASK:
                sp->blocked = mask;
                break;

            default:
                vos3_spinlock_unlock(&g_sig_lock);
                return VOS3_IPC_ERR_INVALID;
        }
    }

    vos3_spinlock_unlock(&g_sig_lock);

    return VOS3_IPC_OK;
}

int vos3_sigwait(const uint32_t* set)
{
    if (set == NULL) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return VOS3_IPC_ERR_INVALID;
    }

    /* Wait for a signal in the set */
    for (;;) {
        vos3_spinlock_lock(&g_sig_lock);

        vos3_sigpending_t* sp = sig_get_pending(current->tid);
        if (sp == NULL) {
            vos3_spinlock_unlock(&g_sig_lock);
            return VOS3_IPC_ERR_NOMEM;
        }

        /* Check for pending signals in the set */
        uint32_t ready = sp->pending & *set;
        if (ready != 0U) {
            /* Find first set bit */
            for (int sig = 1; sig <= VOS3_SIG_MAX; sig++) {
                if ((ready & (1U << (uint32_t)sig)) != 0U) {
                    /* Clear the signal */
                    sp->pending &= ~(1U << (uint32_t)sig);
                    vos3_spinlock_unlock(&g_sig_lock);
                    return sig;
                }
            }
        }

        vos3_spinlock_unlock(&g_sig_lock);

        /* Block until a signal arrives */
        vos3_task_block();
    }
}

void vos3_signal_deliver(vos3_syscall_frame_t *frame, int64_t syscall_result)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL || frame == NULL) {
        return;
    }

    vos3_spinlock_lock(&g_sig_lock);

    vos3_sigpending_t* sp = sig_get_pending(current->tid);
    if (sp == NULL || sp->pending == 0U) {
        vos3_spinlock_unlock(&g_sig_lock);
        return;
    }

    /* Check for unblocked pending signals */
    uint32_t deliverable = sp->pending & ~sp->blocked;
    if (deliverable == 0U) {
        vos3_spinlock_unlock(&g_sig_lock);
        return;
    }

    /* Find first deliverable signal */
    for (int sig = 1; sig <= VOS3_SIG_MAX; sig++) {
        if ((deliverable & (1U << (uint32_t)sig)) == 0U) {
            continue;
        }

        /* Clear the signal */
        sp->pending &= ~(1U << (uint32_t)sig);

        vos3_sigaction_t action = sp->actions[sig];

        vos3_spinlock_unlock(&g_sig_lock);

        VOS3_INFO("[SIG] Delivering signal %d to '%s', handler=%p",
                  sig, current->name, (void*)(uintptr_t)action.handler);

        if (action.handler == VOS3_SIG_DFL) {
            sig_do_default(current, sig);
            return;
        }

        if (action.handler == VOS3_SIG_IGN) {
            return;
        }

        /*
         * If SA_RESTORER is set and restorer is valid, deliver via
         * user-space stack frame (musl path). Otherwise fall back to
         * kernel-context handler call (legacy VOS3 tests).
         */
        if ((action.flags & VOS3_SA_RESTORER) == 0 || action.sa_restorer == 0) {
            VOS3_INFO("[SIG] Kernel-mode handler %p for signal %d (no SA_RESTORER)",
                      (void *)(uintptr_t)action.handler, sig);
            action.handler(sig);
            return;
        }

        /*
         * User-space signal delivery via stack frame.
         *
         * User RSP is part of this task's saved syscall frame.
         */
        uint64_t *user_rsp_ptr = &frame->user_rsp;
        uint64_t user_rsp = *user_rsp_ptr;

        /* Allocate sigframe on user stack with correct ABI alignment.
         * At handler entry, RSP must be 16n+8 (as if 'call' pushed ret addr). */
        uint64_t new_rsp = user_rsp - sizeof(vos3_sigframe_t);
        new_rsp = (new_rsp & ~0xFULL) - 8ULL;

        /* Build the signal frame */
        vos3_sigframe_t sf;
        sf.pretcode   = action.sa_restorer;
        sf.saved_rax  = (uint64_t)syscall_result;
        sf.saved_rdi  = frame->rdi;
        sf.saved_rsi  = frame->rsi;
        sf.saved_rdx  = frame->rdx;
        sf.saved_r10  = frame->r10;
        sf.saved_r8   = frame->r8;
        sf.saved_r9   = frame->r9;
        sf.saved_rcx  = frame->rcx;   /* original user RIP */
        sf.saved_r11  = frame->r11;   /* original user RFLAGS */
        sf.saved_rbx  = frame->rbx;
        sf.saved_rbp  = frame->rbp;
        sf.saved_r12  = frame->r12;
        sf.saved_r13  = frame->r13;
        sf.saved_r14  = frame->r14;
        sf.saved_r15  = frame->r15;
        sf.saved_rsp  = user_rsp;
        sf.signo      = (uint64_t)sig;

        /* Write sigframe to user stack */
        if (vos3_copy_to_user((void *)new_rsp, &sf, sizeof(sf)) != 0) {
            VOS3_WARN("[SIG] Failed to write sigframe to user stack at 0x%llx",
                      (unsigned long long)new_rsp);
            sig_do_default(current, sig);
            return;
        }

        /* Redirect execution to the signal handler */
        frame->rcx = (uint64_t)(uintptr_t)action.handler;  /* new user RIP */
        frame->rdi = (uint64_t)sig;                         /* handler arg1 = signum */
        *user_rsp_ptr = new_rsp;                            /* new user RSP */

        VOS3_INFO("[SIG] Frame: handler=%p signum=%d rsp=0x%llx restorer=0x%llx",
                  (void *)(uintptr_t)action.handler, sig,
                  (unsigned long long)new_rsp,
                  (unsigned long long)action.sa_restorer);
        return;
    }

    vos3_spinlock_unlock(&g_sig_lock);
}
