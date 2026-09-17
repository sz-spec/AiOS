/**
 * @file signal.c
 * @brief VOS3 User-space Signal Functions
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../include/signal.h"
#include "../include/syscall.h"
#include "../include/unistd.h"

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_KILL            62
#define SYS_RT_SIGACTION    13
#define SYS_RT_SIGPROCMASK  14
#define SYS_PAUSE           34
#define SYS_ALARM           37

/* ============================================================================
 * SIGNAL RETURN TRAMPOLINE (defined in restore_rt.S)
 * ============================================================================ */

extern void __restore_rt(void);

/* ============================================================================
 * SIGNAL FUNCTIONS
 * ============================================================================ */

int kill(int pid, int sig)
{
    long result = syscall2(SYS_KILL, (long)pid, (long)sig);
    return (int)result;
}

sighandler_t signal(int sig, sighandler_t handler)
{
    struct sigaction act, oldact;

    act.sa_handler = handler;
    act.sa_flags = SA_RESTART | SA_RESTORER;
    act.sa_restorer = __restore_rt;
    act.sa_mask[0] = 0;
    act.sa_mask[1] = 0;

    if (sigaction(sig, &act, &oldact) < 0) {
        return SIG_ERR;
    }

    return oldact.sa_handler;
}

int sigaction(int sig, const struct sigaction *act, struct sigaction *oldact)
{
    struct sigaction kernel_act;
    if (act != NULL && act->sa_handler != SIG_DFL && act->sa_handler != SIG_IGN) {
        kernel_act = *act;
        if (!(kernel_act.sa_flags & SA_RESTORER) || kernel_act.sa_restorer == NULL) {
            kernel_act.sa_flags |= SA_RESTORER;
            kernel_act.sa_restorer = __restore_rt;
        }
        act = &kernel_act;
    }
    long result = syscall4(SYS_RT_SIGACTION,
                           (long)sig,
                           (long)act,
                           (long)oldact,
                           sizeof(sigset_t));
    return (int)result;
}

int sigprocmask(int how, const sigset_t *set, sigset_t *oldset)
{
    long result = syscall4(SYS_RT_SIGPROCMASK,
                           (long)how,
                           (long)set,
                           (long)oldset,
                           sizeof(sigset_t));
    return (int)result;
}

int pause(void)
{
    long result = syscall0(SYS_PAUSE);
    return (int)result;
}

int raise(int sig)
{
    return kill(getpid(), sig);
}

unsigned int alarm(unsigned int seconds)
{
    long result = syscall1(SYS_ALARM, (long)seconds);
    return (unsigned int)result;
}
