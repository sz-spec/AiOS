/**
 * @file signal.h
 * @brief VOS3 User-space Signal Handling
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_SIGNAL_H
#define VOS3_USER_SIGNAL_H

#include <stdint.h>

/* ============================================================================
 * SIGNAL NUMBERS
 * ============================================================================ */

#define SIGHUP      1       /**< Hangup */
#define SIGINT      2       /**< Interrupt (Ctrl+C) */
#define SIGQUIT     3       /**< Quit (Ctrl+\) */
#define SIGILL      4       /**< Illegal instruction */
#define SIGTRAP     5       /**< Trace/breakpoint trap */
#define SIGABRT     6       /**< Abort */
#define SIGBUS      7       /**< Bus error */
#define SIGFPE      8       /**< Floating point exception */
#define SIGKILL     9       /**< Kill (cannot be caught) */
#define SIGUSR1     10      /**< User-defined signal 1 */
#define SIGSEGV     11      /**< Segmentation fault */
#define SIGUSR2     12      /**< User-defined signal 2 */
#define SIGPIPE     13      /**< Broken pipe */
#define SIGALRM     14      /**< Alarm clock */
#define SIGTERM     15      /**< Termination */
#define SIGSTKFLT   16      /**< Stack fault */
#define SIGCHLD     17      /**< Child status changed */
#define SIGCONT     18      /**< Continue */
#define SIGSTOP     19      /**< Stop (cannot be caught) */
#define SIGTSTP     20      /**< Terminal stop (Ctrl+Z) */
#define SIGTTIN     21      /**< Background read from tty */
#define SIGTTOU     22      /**< Background write to tty */
#define SIGURG      23      /**< Urgent condition on socket */
#define SIGXCPU     24      /**< CPU time limit exceeded */
#define SIGXFSZ     25      /**< File size limit exceeded */
#define SIGVTALRM   26      /**< Virtual timer expired */
#define SIGPROF     27      /**< Profiling timer expired */
#define SIGWINCH    28      /**< Window size change */
#define SIGIO       29      /**< I/O possible */
#define SIGPWR      30      /**< Power failure */
#define SIGSYS      31      /**< Bad system call */

#define NSIG        32      /**< Number of signals */

/* ============================================================================
 * SIGNAL HANDLER TYPES
 * ============================================================================ */

/** @brief Signal handler function type */
typedef void (*sighandler_t)(int);

/** @brief Default signal action */
#define SIG_DFL     ((sighandler_t)0)

/** @brief Ignore signal */
#define SIG_IGN     ((sighandler_t)1)

/** @brief Error return */
#define SIG_ERR     ((sighandler_t)-1)

/* ============================================================================
 * SIGNAL SETS
 * ============================================================================ */

/** @brief Signal set type */
typedef uint64_t sigset_t;

/** @brief sigprocmask how values */
#define SIG_BLOCK       0   /**< Block signals in set */
#define SIG_UNBLOCK     1   /**< Unblock signals in set */
#define SIG_SETMASK     2   /**< Set mask to set */

/* Signal set manipulation */
static inline int sigemptyset(sigset_t *set) {
    *set = 0ULL;
    return 0;
}

static inline int sigfillset(sigset_t *set) {
    *set = ~0ULL;
    return 0;
}

static inline int sigaddset(sigset_t *set, int sig) {
    if (sig < 1 || sig >= NSIG) return -1;
    *set |= (1ULL << (sig - 1));
    return 0;
}

static inline int sigdelset(sigset_t *set, int sig) {
    if (sig < 1 || sig >= NSIG) return -1;
    *set &= ~(1ULL << (sig - 1));
    return 0;
}

static inline int sigismember(const sigset_t *set, int sig) {
    if (sig < 1 || sig >= NSIG) return -1;
    return (*set & (1ULL << (sig - 1))) != 0ULL;
}

/* ============================================================================
 * SIGACTION STRUCTURE
 * ============================================================================ */

/** @brief Signal action flags */
#define SA_NOCLDSTOP    0x00000001  /**< Don't send SIGCHLD when children stop */
#define SA_NOCLDWAIT    0x00000002  /**< Don't create zombie on child death */
#define SA_SIGINFO      0x00000004  /**< Use sa_sigaction instead of sa_handler */
#define SA_RESTORER     0x04000000  /**< sa_restorer is valid */
#define SA_ONSTACK      0x08000000  /**< Use alternate signal stack */
#define SA_RESTART      0x10000000  /**< Restart syscall on signal return */
#define SA_NODEFER      0x40000000  /**< Don't block signal while handling */
#define SA_RESETHAND    0x80000000  /**< Reset handler to SIG_DFL on entry */

/**
 * @brief Signal action structure — matches musl's k_sigaction ABI.
 *
 * Layout (x86_64):
 *   handler(8) + flags(8) + restorer(8) + mask[2](8) = 32 bytes
 */
struct sigaction {
    sighandler_t    sa_handler;     /**< Signal handler */
    unsigned long   sa_flags;       /**< Flags (SA_RESTART, SA_RESTORER, etc.) */
    void            (*sa_restorer)(void); /**< User-space signal return trampoline */
    unsigned int    sa_mask[2];     /**< Blocked signals during handler (64-bit) */
};

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Send signal to process
 * @param[in] pid Process ID (or special values)
 * @param[in] sig Signal number
 * @return 0 on success, -1 on error
 */
int kill(int pid, int sig);

/**
 * @brief Set signal handler (simple interface)
 * @param[in] sig Signal number
 * @param[in] handler New handler
 * @return Previous handler, or SIG_ERR on error
 */
sighandler_t signal(int sig, sighandler_t handler);

/**
 * @brief Set signal action
 * @param[in] sig Signal number
 * @param[in] act New action (may be NULL)
 * @param[out] oldact Previous action (may be NULL)
 * @return 0 on success, -1 on error
 */
int sigaction(int sig, const struct sigaction *act, struct sigaction *oldact);

/**
 * @brief Block/unblock signals
 * @param[in] how SIG_BLOCK, SIG_UNBLOCK, or SIG_SETMASK
 * @param[in] set Signal set to modify
 * @param[out] oldset Previous signal mask (may be NULL)
 * @return 0 on success, -1 on error
 */
int sigprocmask(int how, const sigset_t *set, sigset_t *oldset);

/**
 * @brief Wait for signal
 * @return Always returns -1 with errno set to EINTR
 */
int pause(void);

/**
 * @brief Raise signal to current process
 * @param[in] sig Signal number
 * @return 0 on success, non-zero on error
 */
int raise(int sig);

/**
 * @brief Set alarm timer
 * @param[in] seconds Seconds until SIGALRM
 * @return Remaining seconds from previous alarm
 */
unsigned int alarm(unsigned int seconds);

#endif /* VOS3_USER_SIGNAL_H */
