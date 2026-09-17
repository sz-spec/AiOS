/**
 * @file signal_syscall.c
 * @brief VOS3 Signal System Calls
 *
 * @details System call handlers for signal-related operations.
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/ipc.h"
#include "../../include/vos/task.h"
#include "../../include/vos/timer.h"

/* Signal aliases from ipc.h */
#define SIGKILL VOS3_SIGKILL
#define SIGSTOP VOS3_SIGSTOP
#define SIGCONT VOS3_SIGCONT

/* Type aliases */
typedef vos3_sighandler_t sighandler_t;
#include "../../include/vos/scheduler.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/user.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_KILL            62
#define SYS_RT_SIGACTION    13
#define SYS_RT_SIGPROCMASK  14
#define SYS_RT_SIGRETURN    15
#define SYS_PAUSE           34
#define SYS_ALARM           37
#define SYS_TKILL           200
#define SYS_TGKILL          234

/* ============================================================================
 * SIGNAL HELPERS
 * ============================================================================ */

/**
 * @brief Convert POSIX signal number to VOS3 signal number
 *
 * Since we use the same numbering, this is just validation.
 */
static int sig_validate(int sig)
{
    if (sig <= 0 || sig > VOS3_SIG_MAX) {
        return -22;  /* EINVAL */
    }
    return sig;
}

/* ============================================================================
 * SYSCALL HANDLERS
 * ============================================================================ */

/**
 * @brief sys_kill - Send signal to process
 * @param[in] pid Target PID
 *   - pid > 0: Send to process with that PID
 *   - pid == 0: Send to all processes in sender's process group
 *   - pid == -1: Send to all processes (except init)
 *   - pid < -1: Send to process group |pid|
 * @param[in] sig Signal number (0 = check permissions only)
 */
static int64_t sys_kill(int32_t pid, int sig)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -3;  /* ESRCH */
    }

    /* Validate signal */
    if (sig != 0) {
        int vsig = sig_validate(sig);
        if (vsig < 0) {
            return (int64_t)vsig;
        }
    }

    int result = 0;

    if (pid > 0) {
        /* Send to specific process */
        vos3_task_t* target = vos3_task_find_by_pid((uint32_t)pid);
        if (target == NULL) {
            return -3;  /* ESRCH */
        }

        result = vos3_signal_check_permission(current, target, sig);
        if (result != 0) {
            return (int64_t)result;
        }

        if (sig != 0) {
            result = vos3_signal_send(target->tid, sig);
        }
    } else if (pid == 0) {
        /* Send to own process group */
        uint32_t pgid = current->pgid;
        int count = 0;

        for (vos3_tid_t i = 0U; i < VOS3_MAX_TASKS; i++) {
            vos3_task_t* task = vos3_task_get(i);
            if (task != NULL && task->pgid == pgid) {
                if (vos3_signal_check_permission(current, task, sig) == 0) {
                    if (sig != 0) {
                        vos3_signal_send(task->tid, sig);
                    }
                    count++;
                }
            }
        }

        if (count == 0) {
            return -3;  /* ESRCH */
        }
    } else if (pid == -1) {
        /* Send to all processes (except init and self) */
        int count = 0;

        for (vos3_tid_t i = 0U; i < VOS3_MAX_TASKS; i++) {
            vos3_task_t* task = vos3_task_get(i);
            if (task != NULL && task->pid != 1U && task != current) {
                if (vos3_signal_check_permission(current, task, sig) == 0) {
                    if (sig != 0) {
                        vos3_signal_send(task->tid, sig);
                    }
                    count++;
                }
            }
        }

        if (count == 0) {
            return -3;  /* ESRCH */
        }
    } else {
        /* Send to process group |pid| */
        uint32_t pgid = (uint32_t)(-pid);
        int count = 0;

        for (vos3_tid_t i = 0U; i < VOS3_MAX_TASKS; i++) {
            vos3_task_t* task = vos3_task_get(i);
            if (task != NULL && task->pgid == pgid) {
                if (vos3_signal_check_permission(current, task, sig) == 0) {
                    if (sig != 0) {
                        vos3_signal_send(task->tid, sig);
                    }
                    count++;
                }
            }
        }

        if (count == 0) {
            return -3;  /* ESRCH */
        }
    }

    return (int64_t)result;
}

/**
 * @brief musl k_sigaction layout (from arch/x86_64/ksigaction.h)
 *
 * struct k_sigaction {
 *     void (*handler)(int);       // offset 0,  8 bytes
 *     unsigned long flags;        // offset 8,  8 bytes
 *     void (*restorer)(void);     // offset 16, 8 bytes
 *     unsigned mask[2];           // offset 24, 8 bytes
 * };  // Total: 32 bytes
 */
typedef struct musl_k_sigaction {
    uint64_t handler;
    uint64_t flags;
    uint64_t restorer;
    uint32_t mask[2];
} musl_k_sigaction_t;

static int sig_decode_user_flags(uint64_t flags, uint32_t* decoded)
{
    uint32_t low = (uint32_t)flags;
    /* musl's public sa_flags is signed int, assigned to unsigned long in
     * k_sigaction. SA_RESETHAND therefore legitimately sign-extends. */
    if (flags != (uint64_t)low &&
        flags != (uint64_t)(int64_t)(int32_t)low) return -22;
    const uint32_t supported = 0x10000000U | 0x40000000U |
                               0x80000000U | VOS3_SA_RESTORER;
    if ((low & ~supported) != 0) return -22;
    *decoded = (low & VOS3_SA_RESTORER) |
        ((low & 0x10000000U) ? VOS3_SA_RESTART : 0) |
        ((low & 0x40000000U) ? VOS3_SA_NODEFER : 0) |
        ((low & 0x80000000U) ? VOS3_SA_RESETHAND : 0);
    return 0;
}

/**
 * @brief sys_rt_sigaction - Get/set signal action (musl-compatible ABI)
 */
static int64_t sys_rt_sigaction(int sig, const void* act, void* oldact,
                                 size_t sigsetsize)
{
    if (sigsetsize != sizeof(uint64_t)) return -22;

    int vsig = sig_validate(sig);
    if (vsig < 0) {
        return (int64_t)vsig;
    }

    /* Cannot change action for SIGKILL or SIGSTOP */
    if (act != NULL && (sig == SIGKILL || sig == SIGSTOP)) {
        return -22;  /* EINVAL */
    }

    /* Phase 29: Validate user pointers before any kernel work */
    if (act != NULL && !access_ok(act, sizeof(musl_k_sigaction_t))) {
        return -14;  /* EFAULT */
    }
    if (oldact != NULL && !access_ok(oldact, sizeof(musl_k_sigaction_t))) {
        return -14;  /* EFAULT */
    }

    vos3_sigaction_t koldact;
    vos3_sigaction_t kact;

    /* Copy from user if provided — musl's k_sigaction layout */
    if (act != NULL) {
        musl_k_sigaction_t user_act;
        if (vos3_copy_from_user(&user_act, act, sizeof(user_act)) != 0) {
            return -14;  /* EFAULT */
        }

        kact.handler = (vos3_sighandler_t)(uintptr_t)user_act.handler;
        if (sig_decode_user_flags(user_act.flags, &kact.flags) != 0) return -22;
        kact.sa_restorer = user_act.restorer;
        /* Linux bit 0 denotes signal 1; internal bit 1 denotes signal 1.
         * Signals 32..64 are not implemented; their mask bits are ignored. */
        kact.mask = (user_act.mask[0] & 0x7fffffffU) << 1;
    }

    int result = vos3_sigaction(sig,
                                 act != NULL ? &kact : NULL,
                                 oldact != NULL ? &koldact : NULL);

    if (result == 0 && oldact != NULL) {
        musl_k_sigaction_t user_oldact;
        user_oldact.handler = (uint64_t)(uintptr_t)koldact.handler;
        user_oldact.flags = (koldact.flags & VOS3_SA_RESTORER) |
            ((koldact.flags & VOS3_SA_RESTART) ? 0x10000000ULL : 0) |
            ((koldact.flags & VOS3_SA_NODEFER) ? 0x40000000ULL : 0) |
            ((koldact.flags & VOS3_SA_RESETHAND) ? 0x80000000ULL : 0);
        user_oldact.restorer = koldact.sa_restorer;
        user_oldact.mask[0] = koldact.mask >> 1;
        user_oldact.mask[1] = 0;

        if (vos3_copy_to_user(oldact, &user_oldact, sizeof(user_oldact)) != 0) {
            return -14;  /* EFAULT */
        }
    }

    return (int64_t)result;
}

/**
 * @brief sys_rt_sigprocmask - Get/set blocked signal mask
 */
static int64_t sys_rt_sigprocmask(int how, const void* set, void* oldset,
                                   size_t sigsetsize)
{
    if (sigsetsize != sizeof(uint64_t)) return -22;

    /* Phase 29: Validate user pointers before any kernel work */
    if (set != NULL && !access_ok(set, sizeof(uint64_t))) {
        return -14;  /* EFAULT */
    }
    if (oldset != NULL && !access_ok(oldset, sizeof(uint64_t))) {
        return -14;  /* EFAULT */
    }

    uint32_t kset = 0U;
    uint32_t koldset = 0U;

    /* Copy set from user */
    if (set != NULL) {
        uint64_t user_set;
        if (vos3_copy_from_user(&user_set, set, sizeof(user_set)) != 0) {
            return -14;  /* EFAULT */
        }
        kset = ((uint32_t)user_set & 0x7fffffffU) << 1;
    }

    int result = vos3_sigprocmask(how,
                                   set != NULL ? &kset : NULL,
                                   oldset != NULL ? &koldset : NULL);

    if (result == 0 && oldset != NULL) {
        uint64_t user_oldset = koldset >> 1;
        if (vos3_copy_to_user(oldset, &user_oldset, sizeof(user_oldset)) != 0) {
            return -14;  /* EFAULT */
        }
    }

    return (int64_t)result;
}

/**
 * @brief sys_pause - Wait for a signal
 */
static int64_t sys_pause(void)
{
    /* Block until any signal is delivered */
    uint32_t all_signals = 0xFFFFFFFFU;
    int sig = vos3_sigwait(&all_signals);

    /* pause() always returns -1 with EINTR */
    (void)sig;
    return -4;  /* EINTR */
}

/**
 * @brief sys_alarm - Set alarm timer (Phase 30: Implemented)
 *
 * Sets a timer that delivers SIGALRM to the calling process after
 * the specified number of seconds. Returns the number of seconds
 * remaining on the previous alarm, or 0 if no alarm was set.
 *
 * If seconds is 0, any pending alarm is cancelled without setting
 * a new one.
 */
static int64_t sys_alarm(uint32_t seconds)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return 0;
    }

    /* Calculate remaining seconds from previous alarm */
    uint32_t remaining = 0U;
    if (current->alarm_deadline != 0ULL) {
        uint64_t now = vos3_timer_get_ticks();
        if (current->alarm_deadline > now) {
            /* Convert remaining ticks to seconds (timer runs at ~100 Hz) */
            remaining = (uint32_t)((current->alarm_deadline - now) / VOS3_DEFAULT_TIMER_FREQ);
            if (remaining == 0U) {
                remaining = 1U;  /* At least 1 second if alarm pending */
            }
        }
    }

    /* Set new alarm or cancel existing */
    if (seconds == 0U) {
        current->alarm_deadline = 0ULL;  /* Cancel */
    } else {
        uint64_t now = vos3_timer_get_ticks();
        current->alarm_deadline = now + ((uint64_t)seconds * VOS3_DEFAULT_TIMER_FREQ);
    }

    return (int64_t)remaining;
}

/* ============================================================================
 * SYSCALL DISPATCH
 * ============================================================================ */

/**
 * @brief Signal syscall dispatcher
 */
static int64_t signal_syscall_handler(vos3_syscall_frame_t* frame)
{
    uint64_t num = frame->rax;
    uint64_t arg1 = frame->rdi;
    uint64_t arg2 = frame->rsi;
    uint64_t arg3 = frame->rdx;
    uint64_t arg4 = frame->r10;

    switch (num) {
        case SYS_KILL:
            return sys_kill((int32_t)arg1, (int)arg2);

        case SYS_RT_SIGACTION:
            return sys_rt_sigaction((int)arg1, (const void*)arg2,
                                     (void*)arg3, (size_t)arg4);

        case SYS_RT_SIGPROCMASK:
            return sys_rt_sigprocmask((int)arg1, (const void*)arg2,
                                       (void*)arg3, (size_t)arg4);

        case SYS_PAUSE:
            return sys_pause();

        case SYS_ALARM:
            return sys_alarm((uint32_t)arg1);

        case SYS_RT_SIGRETURN: {
            /*
             * rt_sigreturn: Restore pre-signal user state from the sigframe.
             *
             * When __restore_rt calls syscall(15), the user RSP points to
             * the sigframe data (after pretcode was consumed by the handler's
             * ret instruction). We read the saved state and restore the
             * syscall frame + user RSP so IRETQ returns to original code.
             */
            uint64_t *user_rsp_ptr = &frame->user_rsp;
            uint64_t user_rsp = *user_rsp_ptr;

            /* The sigframe data starts at user_rsp (pretcode already consumed by ret).
             * Layout: saved_rax, saved_rdi, ..., saved_rsp, signo */
            typedef struct {
                uint64_t saved_rax;
                uint64_t saved_rdi;
                uint64_t saved_rsi;
                uint64_t saved_rdx;
                uint64_t saved_r10;
                uint64_t saved_r8;
                uint64_t saved_r9;
                uint64_t saved_rcx;
                uint64_t saved_r11;
                uint64_t saved_rbx;
                uint64_t saved_rbp;
                uint64_t saved_r12;
                uint64_t saved_r13;
                uint64_t saved_r14;
                uint64_t saved_r15;
                uint64_t saved_rsp;
                uint64_t signo;
                uint64_t saved_blocked;
            } sigframe_data_t;

            sigframe_data_t sfd;
            if (vos3_copy_from_user(&sfd, (const void *)user_rsp,
                                     sizeof(sfd)) != 0) {
                VOS3_WARN("[SIG] rt_sigreturn: failed to read sigframe from 0x%llx",
                          (unsigned long long)user_rsp);
                return -14;  /* EFAULT */
            }

            /* A user-supplied frame must not select kernel addresses or
             * acquire IOPL/NT/VM/AC privileges through IRETQ. */
            if (!access_ok((void*)(uintptr_t)sfd.saved_rcx, 1) ||
                !access_ok((void*)(uintptr_t)sfd.saved_rsp, 1) ||
                sfd.saved_rcx == 0 || sfd.saved_rsp == 0) return -14;
            sfd.saved_r11 = (sfd.saved_r11 & 0x0000000000000CD5ULL) | 0x202ULL;
            uint32_t saved_mask = (uint32_t)sfd.saved_blocked;
            if (vos3_sigprocmask(VOS3_SIG_SETMASK, &saved_mask, NULL) != 0)
                return -22;

            /* Restore all frame registers */
            frame->rdi = sfd.saved_rdi;
            frame->rsi = sfd.saved_rsi;
            frame->rdx = sfd.saved_rdx;
            frame->r10 = sfd.saved_r10;
            frame->r8  = sfd.saved_r8;
            frame->r9  = sfd.saved_r9;
            frame->rcx = sfd.saved_rcx;   /* original user RIP */
            frame->r11 = sfd.saved_r11;   /* original user RFLAGS */
            frame->rbx = sfd.saved_rbx;
            frame->rbp = sfd.saved_rbp;
            frame->r12 = sfd.saved_r12;
            frame->r13 = sfd.saved_r13;
            frame->r14 = sfd.saved_r14;
            frame->r15 = sfd.saved_r15;

            /* Restore user RSP on kernel stack */
            *user_rsp_ptr = sfd.saved_rsp;

            VOS3_INFO("[SIG] rt_sigreturn: restored RIP=0x%llx RSP=0x%llx RAX=0x%llx",
                      (unsigned long long)sfd.saved_rcx,
                      (unsigned long long)sfd.saved_rsp,
                      (unsigned long long)sfd.saved_rax);

            /* Return saved RAX (original syscall return value) */
            return (int64_t)sfd.saved_rax;
        }

        case SYS_TKILL: {
            /* tkill(tid, sig): send signal to thread tid */
            int32_t tid = (int32_t)arg1;
            int     sig = (int)arg2;

            if (tid <= 0 || sig < 0 || sig > VOS3_SIG_MAX) {
                return -22;  /* EINVAL */
            }
            vos3_task_t* target = vos3_task_get((vos3_tid_t)tid);
            if (target == NULL) {
                return -3;   /* ESRCH */
            }
            int permission = vos3_signal_check_permission(vos3_sched_current(), target, sig);
            if (permission != 0) return permission;
            if (sig == 0) {
                return 0;
            }
            return (int64_t)vos3_signal_send((vos3_tid_t)tid, sig);
        }

        case SYS_TGKILL: {
            /*
             * tgkill(tgid, tid, sig): send signal sig to thread tid,
             * verifying it belongs to thread group tgid.
             */
            int32_t tgid = (int32_t)arg1;
            int32_t tid  = (int32_t)arg2;
            int     sig  = (int)arg3;

            if (tgid <= 0 || tid <= 0 || sig < 0 || sig > VOS3_SIG_MAX) {
                return -22;  /* EINVAL */
            }

            vos3_task_t* target = vos3_task_get((vos3_tid_t)tid);
            if (target == NULL) {
                return -3;   /* ESRCH */
            }

            /* A thread-directed request must identify its actual group. */
            if ((int32_t)target->tgid != tgid) {
                return -3;   /* ESRCH — tid not in this tgid */
            }

            int permission = vos3_signal_check_permission(vos3_sched_current(), target, sig);
            if (permission != 0) return permission;

            if (sig == 0) {
                return 0;    /* Signal 0 = existence check only */
            }

            return (int64_t)vos3_signal_send((vos3_tid_t)tid, sig);
        }

        default:
            return -38;  /* ENOSYS */
    }
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/**
 * @brief Register signal syscalls
 */
void vos3_signal_syscalls_init(void)
{
    VOS3_INFO("Registering signal syscalls");

    vos3_syscall_register(SYS_KILL,            signal_syscall_handler);
    vos3_syscall_register(SYS_RT_SIGACTION,    signal_syscall_handler);
    vos3_syscall_register(SYS_RT_SIGPROCMASK,  signal_syscall_handler);
    vos3_syscall_register(SYS_PAUSE,           signal_syscall_handler);
    vos3_syscall_register(SYS_ALARM,           signal_syscall_handler);
    /* Task 1.5: rt_sigreturn + tgkill + tkill */
    vos3_syscall_register(SYS_RT_SIGRETURN,    signal_syscall_handler);
    vos3_syscall_register(SYS_TKILL,           signal_syscall_handler);
    vos3_syscall_register(SYS_TGKILL,          signal_syscall_handler);

    VOS3_INFO("Signal syscalls registered (kill/sigaction/sigprocmask/pause/alarm/rt_sigreturn/tgkill)");
}
