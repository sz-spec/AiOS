/**
 * @file syscall.h
 * @brief VOS3 System Call Interface
 *
 * @details System call numbers, structures, and kernel-side handlers.
 *          Uses the SYSCALL/SYSRET instructions for fast system calls.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_SYSCALL_H
#define VOS3_SYSCALL_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "../uapi/vos_sysinfo.h"
#include "../arch/x86_64/cpu.h"  /* canonical MSR addresses and EFER flags */

/* ============================================================================
 * SYSTEM CALL NUMBERS
 * ============================================================================
 *
 * Range allocation:
 *   0-350   : Linux ABI compatibility (registered by fs_syscall.c)
 *             DO NOT use for custom syscalls — will be silently overwritten.
 *   400-489 : VOS3 custom (IPC, SHM, pipes, sockets, signals, admin, time)
 *   490-499 : Agent dispatcher (Phase 3)
 *   500-511 : Reserved (future)
 *
 * WARNING: fs_syscall_init() runs AFTER ipc_init() — if both register the
 *          same number, fs_syscall wins. vos3_syscall_register() logs a
 *          warning when overwriting an existing handler.
 * ============================================================================ */

/** @brief System call numbers */
typedef enum vos3_syscall_num {
    /* Process management */
    VOS3_SYS_EXIT           = 0,    /**< Exit current process */
    VOS3_SYS_FORK           = 1,    /**< Create child process */
    VOS3_SYS_GETPID         = 2,    /**< Get process ID */
    VOS3_SYS_GETPPID        = 3,    /**< Get parent process ID */
    VOS3_SYS_GETTID         = 4,    /**< Get thread ID */
    VOS3_SYS_WAIT           = 5,    /**< Wait for child */
    VOS3_SYS_EXEC           = 6,    /**< Execute program */

    /* Thread management — Linux-compatible numbers */
    VOS3_SYS_YIELD          = 24,   /**< Yield CPU (Linux sched_yield=24) */

    /* Memory management — Linux-compatible numbers */
    VOS3_SYS_BRK            = 20,   /**< Change data segment size */
    VOS3_SYS_MMAP           = 21,   /**< Map memory */
    VOS3_SYS_MUNMAP         = 22,   /**< Unmap memory */
    VOS3_SYS_MPROTECT       = 23,   /**< Change memory protection */

    /* File operations — Linux-compatible numbers */
    VOS3_SYS_OPEN           = 30,   /**< Open file */
    VOS3_SYS_CLOSE          = 31,   /**< Close file */
    VOS3_SYS_READ           = 32,   /**< Read from file */
    VOS3_SYS_WRITE          = 33,   /**< Write to file */
    VOS3_SYS_LSEEK          = 34,   /**< Seek in file */
    VOS3_SYS_STAT           = 35,   /**< Get file status */
    VOS3_SYS_FSTAT          = 36,   /**< Get file status by fd */

    /* Time — Linux-compatible */
    VOS3_SYS_GETTIME        = 40,   /**< Get system time (no Linux conflict at 40) */

    /* ========================================================================
     * VOS3 CUSTOM SYSCALLS — 400-499 range (Phase 1.2 Reorganization)
     *
     * All VOS3-specific syscalls live in the 400-499 range to avoid
     * collisions with Linux standard syscall numbers (0-350).
     * Linux-compatible aliases (e.g., __NR_socket=41) remain at their
     * standard numbers and are registered separately.
     * ======================================================================== */

    /* 400-409: IPC Core (Message Queues) */
    VOS3_SYS_MSGQ_CREATE    = 400,  /**< Create message queue */
    VOS3_SYS_MSGQ_DESTROY   = 401,  /**< Destroy message queue */
    VOS3_SYS_MSGQ_SEND      = 402,  /**< Send to message queue */
    VOS3_SYS_MSGQ_RECV      = 403,  /**< Receive from message queue */

    /* 410-419: Shared Memory */
    VOS3_SYS_SHM_CREATE     = 410,  /**< Create shared memory region */
    VOS3_SYS_SHM_DESTROY    = 411,  /**< Destroy shared memory region */
    VOS3_SYS_SHM_MAP        = 412,  /**< Map shared memory into address space */
    VOS3_SYS_SHM_UNMAP      = 413,  /**< Unmap shared memory */
    VOS3_SYS_SHM_SIZE       = 414,  /**< Get shared memory size */
    VOS3_SYS_SHM_CREATE_DEVICE = 415, /**< Create device MMIO shared memory */

    /* 420-429: Pipes */
    VOS3_SYS_PIPE_CREATE    = 420,  /**< Create pipe pair */
    VOS3_SYS_PIPE_READ      = 421,  /**< Read from pipe */
    VOS3_SYS_PIPE_WRITE     = 422,  /**< Write to pipe */
    VOS3_SYS_PIPE_CLOSE     = 423,  /**< Close pipe end */

    /* 430-441: Sockets (VOS3 custom numbers) */
    VOS3_SYS_SOCKET         = 430,  /**< Create socket */
    VOS3_SYS_BIND           = 431,  /**< Bind socket */
    VOS3_SYS_LISTEN         = 432,  /**< Listen for connections */
    VOS3_SYS_ACCEPT         = 433,  /**< Accept connection */
    VOS3_SYS_CONNECT        = 434,  /**< Connect to remote */
    VOS3_SYS_SENDTO         = 435,  /**< Send datagram */
    VOS3_SYS_RECVFROM       = 436,  /**< Receive datagram */
    VOS3_SYS_SEND           = 437,  /**< Send on connected socket */
    VOS3_SYS_RECV           = 438,  /**< Receive on connected socket */
    VOS3_SYS_SHUTDOWN       = 439,  /**< Shutdown socket */
    VOS3_SYS_GETSOCKOPT     = 440,  /**< Get socket option */
    VOS3_SYS_SETSOCKOPT     = 441,  /**< Set socket option */

    /* 442-444: Block Device & IOCTL */
    VOS3_SYS_BLKDEV_TEST    = 442,  /**< Run block device self-test */
    VOS3_SYS_BLKDEV_INFO    = 443,  /**< Get block device info */
    VOS3_SYS_IOCTL          = 444,  /**< VOS3 ioctl (dev_init) */

    /* 450-454: Signals (VOS3 custom numbers) */
    VOS3_SYS_KILL           = 450,  /**< Send signal to process */
    VOS3_SYS_SIGNAL         = 451,  /**< Set signal handler */
    VOS3_SYS_SIGACTION      = 452,  /**< Set signal action */
    VOS3_SYS_SIGPROCMASK    = 453,  /**< Change signal mask */
    VOS3_SYS_SIGWAIT        = 454,  /**< Wait for signal */

    /* 460-462: AI Guard / App Context Isolation */
    VOS3_SYS_APP_CTX_CREATE  = 460, /**< Create per-app AI guard context */
    VOS3_SYS_APP_CTX_DESTROY = 461, /**< Destroy per-app AI guard context */
    VOS3_SYS_APP_CTX_SWITCH  = 462, /**< Switch active app context */

    /* 470-474: Admin & Configuration */
    VOS3_SYS_CONFIG_GET     = 470,  /**< Get VOS3 configuration */
    VOS3_SYS_CONFIG_SET     = 471,  /**< Set VOS3 configuration */
    VOS3_SYS_DELEGATION_GET = 472,  /**< Get delegation policy */
    VOS3_SYS_DELEGATION_SET = 473,  /**< Set delegation policy */
    VOS3_SYS_ADMIN_AUTH     = 474,  /**< Admin token authentication */

    /* 475-478: Console & Debug */
    VOS3_SYS_PUTCHAR        = 475,  /**< Write character to console */
    VOS3_SYS_PUTS           = 476,  /**< Write string to console */
    VOS3_SYS_GETCHAR        = 477,  /**< Read character from console */
    VOS3_SYS_DEBUG          = 478,  /**< Debug output */

    /* 480-482: Time & Sleep (VOS3 custom) */
    VOS3_SYS_GETTICKS       = 480,  /**< Get system tick count */
    VOS3_SYS_SLEEP          = 481,  /**< Sleep for milliseconds */
    VOS3_SYS_NANOSLEEP      = 482,  /**< Sleep with nanosecond precision */

    /* Versioned native telemetry; Linux syscall 99 is unsupported. */
    VOS3_SYS_SYSINFO        = VOS3_SYSINFO_SYSCALL,

    /* 490-499: Agent Dispatcher (Phase 3) */
    VOS3_SYS_AGENT_REGISTER    = 490,  /**< Register agent */
    VOS3_SYS_AGENT_DEREGISTER  = 491,  /**< Deregister agent */
    VOS3_SYS_DISPATCH_SUBMIT   = 492,  /**< Submit work item */
    VOS3_SYS_DISPATCH_PULL     = 493,  /**< Pull work item */
    VOS3_SYS_DISPATCH_COMPLETE = 494,  /**< Complete work item */
    VOS3_SYS_AGENT_STATUS      = 495,  /**< Query dispatcher status */
    VOS3_SYS_VFS_PREFETCH      = 496,  /**< VFS prefetch */
    VOS3_SYS_AGENT_KILL_ALL    = 497,  /**< Kill all agents (privileged) */
    VOS3_SYS_INFERENCE_HINT    = 498,  /**< Set inference scheduling state (Batch F) */

    /* 500-511: AI Model (Phase 4.2 / 4.2.5) */
    VOS3_SYS_AI_MODEL_LOAD    = 500,  /**< Load AI model weights into protected memory */
    VOS3_SYS_AI_SNAPSHOT      = 501,  /**< Session persistence: demote slot to L3 cache */
    VOS3_SYS_AI_CHECKPOINT    = 502,  /**< Rolling checkpoint (Phase 4.2.7) */
    VOS3_SYS_AI_YIELD_EX     = 503,  /**< AI yield-ex: voluntary DORMANT with timed/event wake */
    VOS3_SYS_AI_GET_TIME     = 504,  /**< AI get-time: deterministic per-slot clock (Phase 4.2.11) */

    /* Maximum syscall number — 512 to accommodate 400-511 range */
    VOS3_SYS_MAX            = 512
} vos3_syscall_num_t;

/* ============================================================================
 * SYSCALL FRAME
 * ============================================================================ */

/**
 * @brief System call register frame
 *
 * Register usage for system calls (System V AMD64 ABI modified):
 *   RAX = syscall number (input) / return value (output)
 *   RDI = arg1
 *   RSI = arg2
 *   RDX = arg3
 *   R10 = arg4 (R10 instead of RCX, since SYSCALL uses RCX)
 *   R8  = arg5
 *   R9  = arg6
 *
 * SYSCALL instruction saves:
 *   RCX = return RIP
 *   R11 = saved RFLAGS
 */
typedef struct vos3_syscall_frame {
    /* Saved by syscall stub */
    uint64_t r15;
    uint64_t r14;
    uint64_t r13;
    uint64_t r12;
    uint64_t rbp;
    uint64_t rbx;

    /* Syscall arguments */
    uint64_t r9;            /**< Arg 6 */
    uint64_t r8;            /**< Arg 5 */
    uint64_t r10;           /**< Arg 4 (instead of RCX) */
    uint64_t rdx;           /**< Arg 3 */
    uint64_t rsi;           /**< Arg 2 */
    uint64_t rdi;           /**< Arg 1 */
    uint64_t rax;           /**< Syscall number / return value */

    /* Saved by SYSCALL instruction */
    uint64_t rcx;           /**< User RIP */
    uint64_t r11;           /**< User RFLAGS */
    uint64_t user_rsp;      /**< Saved on this task's stack before interrupts */
} vos3_syscall_frame_t;

/* ============================================================================
 * SYSCALL HANDLER TYPE
 * ============================================================================ */

/**
 * @brief System call handler function type
 * @param[in] frame Pointer to syscall frame
 * @return Return value (placed in RAX)
 */
typedef int64_t (*vos3_syscall_handler_t)(vos3_syscall_frame_t* frame);

/* ============================================================================
 * MSR DEFINITIONS
 * ============================================================================ */

/* VOS3_MSR_EFER/STAR/LSTAR/CSTAR/SFMASK and EFER flag bits — see cpu.h (included above) */

/** @brief RFLAGS bits to clear on syscall entry */
#define VOS3_SYSCALL_RFLAGS_MASK  ((uint64_t)0x0000000000000200ULL)  /* Clear IF */

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the system call interface
 * @return 0 on success, negative error code on failure
 */
int vos3_syscall_init(void);

/** Configure local SYSCALL MSRs after installing this CPU's GDT and GS. */
void vos3_syscall_init_cpu(void);

/**
 * @brief Register a system call handler
 * @param[in] num System call number
 * @param[in] handler Handler function
 * @return 0 on success, negative error code on failure
 */
int vos3_syscall_register(vos3_syscall_num_t num, vos3_syscall_handler_t handler);

/**
 * @brief System call dispatcher (called from assembly)
 * @param[in] frame Pointer to syscall frame
 * @return Return value for user
 */
int64_t vos3_syscall_dispatch(vos3_syscall_frame_t* frame);

/* ============================================================================
 * ASSEMBLY ENTRY POINTS
 * ============================================================================ */

/**
 * @brief Syscall entry point (set in LSTAR)
 * @note Defined in syscall_entry.S
 */
extern void vos3_syscall_entry(void);

/* ============================================================================
 * SYSTEM CALL IMPLEMENTATIONS
 * ============================================================================ */

/* Process management */
int64_t vos3_sys_exit(vos3_syscall_frame_t* frame);
int64_t vos3_sys_getpid(vos3_syscall_frame_t* frame);
int64_t vos3_sys_getppid(vos3_syscall_frame_t* frame);
int64_t vos3_sys_gettid(vos3_syscall_frame_t* frame);

/* Thread management */
int64_t vos3_sys_yield(vos3_syscall_frame_t* frame);
int64_t vos3_sys_sleep(vos3_syscall_frame_t* frame);

/* Time */
int64_t vos3_sys_gettime(vos3_syscall_frame_t* frame);
int64_t vos3_sys_getticks(vos3_syscall_frame_t* frame);

/* Console */
int64_t vos3_sys_putchar(vos3_syscall_frame_t* frame);
int64_t vos3_sys_puts(vos3_syscall_frame_t* frame);

/* Debug */
int64_t vos3_sys_debug(vos3_syscall_frame_t* frame);

/* VOS3 Configuration & Delegation (Phase 24) */
int64_t vos3_sys_config_get(vos3_syscall_frame_t* frame);
int64_t vos3_sys_config_set(vos3_syscall_frame_t* frame);
int64_t vos3_sys_delegation_get(vos3_syscall_frame_t* frame);
int64_t vos3_sys_delegation_set(vos3_syscall_frame_t* frame);
int64_t vos3_sys_admin_auth(vos3_syscall_frame_t* frame);

/* Block Device (Day 7) */
int64_t vos3_sys_blkdev_test(vos3_syscall_frame_t* frame);
int64_t vos3_sys_blkdev_info(vos3_syscall_frame_t* frame);

/* App Context Isolation (Phase N) */
int64_t vos3_sys_app_ctx_create(vos3_syscall_frame_t* frame);
int64_t vos3_sys_app_ctx_destroy(vos3_syscall_frame_t* frame);
int64_t vos3_sys_app_ctx_switch(vos3_syscall_frame_t* frame);

/* ============================================================================
 * USER-SPACE SYSCALL MACROS (for user programs)
 * ============================================================================ */

#ifdef VOS3_USERSPACE

/**
 * @brief Invoke a system call (inline assembly)
 */
#define VOS3_SYSCALL0(num) \
    ({ \
        int64_t __ret; \
        __asm__ volatile ( \
            "syscall" \
            : "=a"(__ret) \
            : "a"((uint64_t)(num)) \
            : "rcx", "r11", "memory" \
        ); \
        __ret; \
    })

#define VOS3_SYSCALL1(num, a1) \
    ({ \
        int64_t __ret; \
        __asm__ volatile ( \
            "syscall" \
            : "=a"(__ret) \
            : "a"((uint64_t)(num)), "D"((uint64_t)(a1)) \
            : "rcx", "r11", "memory" \
        ); \
        __ret; \
    })

#define VOS3_SYSCALL2(num, a1, a2) \
    ({ \
        int64_t __ret; \
        __asm__ volatile ( \
            "syscall" \
            : "=a"(__ret) \
            : "a"((uint64_t)(num)), "D"((uint64_t)(a1)), "S"((uint64_t)(a2)) \
            : "rcx", "r11", "memory" \
        ); \
        __ret; \
    })

#define VOS3_SYSCALL3(num, a1, a2, a3) \
    ({ \
        int64_t __ret; \
        __asm__ volatile ( \
            "syscall" \
            : "=a"(__ret) \
            : "a"((uint64_t)(num)), "D"((uint64_t)(a1)), "S"((uint64_t)(a2)), \
              "d"((uint64_t)(a3)) \
            : "rcx", "r11", "memory" \
        ); \
        __ret; \
    })

#endif /* VOS3_USERSPACE */

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_SYSCALL_OK         (0)
#define VOS3_SYSCALL_ERR        (-1)
#define VOS3_SYSCALL_ENOSYS     (-38)   /**< Function not implemented */
#define VOS3_SYSCALL_EINVAL     (-22)   /**< Invalid argument */
#define VOS3_SYSCALL_EFAULT     (-14)   /**< Bad address */
#define VOS3_SYSCALL_EPERM      (-13)   /**< Operation not permitted */

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SYSCALL_H */
