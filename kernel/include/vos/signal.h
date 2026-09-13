/**
 * @file signal.h
 * @brief VOS3 Signal Handling
 *
 * @details POSIX-compatible signal definitions and handling.
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_SIGNAL_H
#define VOS3_SIGNAL_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

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

/** @brief Number of signals */
#define NSIG        32

/** @brief Real-time signals (not implemented yet) */
#define SIGRTMIN    32
#define SIGRTMAX    64

/* ============================================================================
 * SIGNAL ACTIONS
 * ============================================================================ */

/** @brief Signal handler type */
typedef void (*sighandler_t)(int);

/** @brief Default signal action */
#define SIG_DFL     ((sighandler_t)0)

/** @brief Ignore signal */
#define SIG_IGN     ((sighandler_t)1)

/** @brief Error return */
#define SIG_ERR     ((sighandler_t)-1)

/* ============================================================================
 * SIGNAL FLAGS
 * ============================================================================ */

/** @brief sigaction flags */
#define SA_NOCLDSTOP    0x00000001  /**< Don't send SIGCHLD when children stop */
#define SA_NOCLDWAIT    0x00000002  /**< Don't create zombie on child death */
#define SA_SIGINFO      0x00000004  /**< Use sa_sigaction instead of sa_handler */
#define SA_ONSTACK      0x08000000  /**< Use alternate signal stack */
#define SA_RESTART      0x10000000  /**< Restart syscall on signal return */
#define SA_NODEFER      0x40000000  /**< Don't block signal while handling */
#define SA_RESETHAND    0x80000000  /**< Reset handler to SIG_DFL on entry */

/* ============================================================================
 * SIGNAL SETS
 * ============================================================================ */

/** @brief Signal set type (bitmask) */
typedef uint64_t sigset_t;

/** @brief sigprocmask how values */
#define SIG_BLOCK       0   /**< Block signals in set */
#define SIG_UNBLOCK     1   /**< Unblock signals in set */
#define SIG_SETMASK     2   /**< Set mask to set */

/* Signal set manipulation macros */
#define sigemptyset(set)        (*(set) = 0ULL)
#define sigfillset(set)         (*(set) = ~0ULL)
#define sigaddset(set, sig)     (*(set) |= (1ULL << ((sig) - 1)))
#define sigdelset(set, sig)     (*(set) &= ~(1ULL << ((sig) - 1)))
#define sigismember(set, sig)   ((*(set) & (1ULL << ((sig) - 1))) != 0ULL)

/* ============================================================================
 * SIGACTION STRUCTURE
 * ============================================================================ */

/** @brief Signal information structure */
typedef struct siginfo {
    int         si_signo;       /**< Signal number */
    int         si_errno;       /**< Error number */
    int         si_code;        /**< Signal code */
    int32_t     si_pid;         /**< Sending process ID */
    uint32_t    si_uid;         /**< Sending user ID */
    int         si_status;      /**< Exit value or signal */
    void*       si_addr;        /**< Faulting address */
    union {
        int     si_int;
        void*   si_ptr;
    } si_value;
} siginfo_t;

/** @brief Signal action structure */
typedef struct sigaction {
    union {
        sighandler_t    sa_handler;     /**< Signal handler */
        void (*sa_sigaction)(int, siginfo_t*, void*); /**< Alternate handler */
    };
    sigset_t    sa_mask;        /**< Signals to block during handler */
    int         sa_flags;       /**< Flags */
    void        (*sa_restorer)(void); /**< Restore function (unused) */
} sigaction_t;

/* ============================================================================
 * SIGNAL STATE (per task)
 * ============================================================================ */

/** @brief Signal state for a task */
typedef struct vos3_signal_state {
    sigset_t        pending;        /**< Pending signals */
    sigset_t        blocked;        /**< Blocked signals */
    sigaction_t     actions[NSIG];  /**< Signal handlers */
    uint32_t        in_handler;     /**< Currently handling signal */
    uint32_t        flags;          /**< Signal flags */
} vos3_signal_state_t;

/** @brief Default action types */
typedef enum vos3_sig_default {
    SIG_DEFAULT_TERM = 0,   /**< Terminate process */
    SIG_DEFAULT_IGN  = 1,   /**< Ignore signal */
    SIG_DEFAULT_CORE = 2,   /**< Terminate with core dump */
    SIG_DEFAULT_STOP = 3,   /**< Stop process */
    SIG_DEFAULT_CONT = 4,   /**< Continue process */
} vos3_sig_default_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize signal subsystem
 * @return 0 on success, negative error code on failure
 */
int vos3_signal_init(void);

/**
 * @brief Initialize signal state for a task
 * @param[out] state Signal state to initialize
 */
void vos3_signal_state_init(vos3_signal_state_t* state);

/**
 * @brief Copy signal state (for fork)
 * @param[out] dst Destination state
 * @param[in] src Source state
 */
void vos3_signal_state_copy(vos3_signal_state_t* dst,
                             const vos3_signal_state_t* src);

/**
 * @brief Send signal to a task
 * @param[in] task Target task
 * @param[in] sig Signal number
 * @return 0 on success, negative error code on failure
 */
int vos3_signal_send(struct vos3_task* task, int sig);

/**
 * @brief Send signal to process group
 * @param[in] pgid Process group ID
 * @param[in] sig Signal number
 * @return 0 on success, negative error code on failure
 */
int vos3_signal_send_group(uint32_t pgid, int sig);

/**
 * @brief Check and deliver pending signals
 * @param[in] task Task to check
 * @return 1 if signal was delivered, 0 otherwise
 *
 * Called from scheduler before returning to user space.
 */
int vos3_signal_check(struct vos3_task* task);

/**
 * @brief Get pending signals that are not blocked
 * @param[in] task Task to check
 * @return Signal number if pending, 0 if none
 */
int vos3_signal_pending(struct vos3_task* task);

/**
 * @brief Block signals
 * @param[in] how SIG_BLOCK, SIG_UNBLOCK, or SIG_SETMASK
 * @param[in] set Signals to modify
 * @param[out] oldset Previous mask (optional)
 * @return 0 on success, negative error code on failure
 */
int vos3_sigprocmask(int how, const sigset_t* set, sigset_t* oldset);

/**
 * @brief Set signal handler
 * @param[in] sig Signal number
 * @param[in] act New action (optional)
 * @param[out] oldact Previous action (optional)
 * @return 0 on success, negative error code on failure
 */
int vos3_sigaction(int sig, const sigaction_t* act, sigaction_t* oldact);

/**
 * @brief Simple signal handler setup
 * @param[in] sig Signal number
 * @param[in] handler Handler function
 * @return Previous handler, or SIG_ERR on error
 */
sighandler_t vos3_signal(int sig, sighandler_t handler);

/**
 * @brief Kill system call
 * @param[in] pid Target PID (or special values)
 * @param[in] sig Signal number
 * @return 0 on success, negative error code on failure
 */
int vos3_kill(int32_t pid, int sig);

/**
 * @brief Get default action for signal
 * @param[in] sig Signal number
 * @return Default action type
 */
vos3_sig_default_t vos3_signal_default_action(int sig);

/**
 * @brief Check if signal can be caught/ignored
 * @param[in] sig Signal number
 * @return 1 if catchable, 0 if not (SIGKILL/SIGSTOP)
 */
int vos3_signal_is_catchable(int sig);

/**
 * @brief Get signal name
 * @param[in] sig Signal number
 * @return Signal name string
 */
const char* vos3_signal_name(int sig);

/**
 * @brief Register signal syscalls
 */
void vos3_signal_syscalls_init(void);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_SIG_OK         (0)
#define VOS3_SIG_ERR_INVAL  (-22)   /**< Invalid signal number */
#define VOS3_SIG_ERR_PERM   (-1)    /**< Permission denied */
#define VOS3_SIG_ERR_SRCH   (-3)    /**< No such process */

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SIGNAL_H */
