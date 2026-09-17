/**
 * @file syscall.c
 * @brief VOS3 System Call Implementation
 *
 * @details System call initialization, registration, and dispatch.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/vos/syscall.h"
#include "../../../include/vos/task.h"
#include "../../../include/vos/scheduler.h"
#include "../../../include/vos/timer.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/vos3_config.h"
#include "../../../include/vos/ai_guard.h"
#include "../../../include/vos/uaccess.h"
#include "../../../include/vos/entry_state.h"
#include "../../../include/arch/x86_64/cpu.h"
#include "../../../include/arch/x86_64/gdt.h"

/* syscall_entry.S restores these slots by fixed byte offsets. */
_Static_assert(offsetof(vos3_syscall_frame_t, rax) == 96, "syscall return-value slot");
_Static_assert(offsetof(vos3_syscall_frame_t, user_rsp) == 120, "syscall saved user stack");
_Static_assert(sizeof(vos3_syscall_frame_t) == 128, "complete syscall stack frame");

/* ============================================================================
 * EXTERNAL SYMBOLS
 * ============================================================================ */

/* From syscall_entry.S */
extern char g_syscall_stack_top[];

/* ============================================================================
 * SYSCALL TABLE
 * ============================================================================ */

/** @brief System call handler table */
static vos3_syscall_handler_t g_syscall_table[VOS3_SYS_MAX];

/** @brief Syscall initialized flag */
static int g_syscall_initialized = 0;

/* ============================================================================
 * MSR ACCESS
 * ============================================================================ */

/**
 * @brief Write to Model-Specific Register
 */
static inline void wrmsr(uint32_t msr, uint64_t value)
{
    uint32_t low = (uint32_t)(value & 0xFFFFFFFFULL);
    uint32_t high = (uint32_t)(value >> 32);
    __asm__ volatile ("wrmsr" : : "c"(msr), "a"(low), "d"(high));
}

/**
 * @brief Read from Model-Specific Register
 */
static inline uint64_t rdmsr(uint32_t msr)
{
    uint32_t low, high;
    __asm__ volatile ("rdmsr" : "=a"(low), "=d"(high) : "c"(msr));
    return ((uint64_t)high << 32) | (uint64_t)low;
}

/* ============================================================================
 * DEFAULT HANDLER
 * ============================================================================ */

/**
 * @brief Default handler for unimplemented syscalls
 */
static int64_t syscall_not_implemented(vos3_syscall_frame_t* frame)
{
    VOS3_WARN("Unimplemented syscall: %llu",
                 (unsigned long long)frame->rax);
    return VOS3_SYSCALL_ENOSYS;
}

/* ============================================================================
 * SYSTEM CALL IMPLEMENTATIONS
 * ============================================================================ */

/**
 * @brief SYS_EXIT - Exit current process
 */
int64_t vos3_sys_exit(vos3_syscall_frame_t* frame)
{
    int exit_code = (int)frame->rdi;

    VOS3_DEBUG("sys_exit(%d)", exit_code);

    vos3_task_exit(exit_code);

    /* Never reached */
    return 0;
}

/**
 * @brief SYS_GETPID - Get process ID
 */
int64_t vos3_sys_getpid(vos3_syscall_frame_t* frame)
{
    (void)frame;

    vos3_task_t* current = vos3_task_current();
    if (current == NULL) {
        return VOS3_SYSCALL_ERR;
    }

    return (int64_t)current->pid;
}

/**
 * @brief SYS_GETPPID - Get parent process ID
 */
int64_t vos3_sys_getppid(vos3_syscall_frame_t* frame)
{
    (void)frame;

    vos3_task_t* current = vos3_task_current();
    if (current == NULL || current->parent == NULL) {
        return 0;  /* No parent (init process) */
    }

    return (int64_t)current->parent->pid;
}

/**
 * @brief SYS_GETTID - Get thread ID
 */
int64_t vos3_sys_gettid(vos3_syscall_frame_t* frame)
{
    (void)frame;

    vos3_task_t* current = vos3_task_current();
    if (current == NULL) {
        return VOS3_SYSCALL_ERR;
    }

    return (int64_t)current->tid;
}

/**
 * @brief SYS_YIELD - Yield CPU to other tasks
 */
int64_t vos3_sys_yield(vos3_syscall_frame_t* frame)
{
    (void)frame;

    vos3_task_yield();

    return 0;
}

/**
 * @brief SYS_SLEEP - Sleep for milliseconds
 */
int64_t vos3_sys_sleep(vos3_syscall_frame_t* frame)
{
    uint64_t ms = frame->rdi;

    VOS3_DEBUG("sys_sleep(%llu ms)", (unsigned long long)ms);

    vos3_task_sleep_ms(ms);

    return 0;
}

/**
 * @brief SYS_GETTIME - Get system uptime in milliseconds
 */
int64_t vos3_sys_gettime(vos3_syscall_frame_t* frame)
{
    (void)frame;

    return (int64_t)vos3_timer_get_uptime_ms();
}

/**
 * @brief SYS_GETTICKS - Get system tick count
 */
int64_t vos3_sys_getticks(vos3_syscall_frame_t* frame)
{
    (void)frame;

    return (int64_t)vos3_timer_get_ticks();
}

/**
 * @brief SYS_PUTCHAR - Write character to console
 */
int64_t vos3_sys_putchar(vos3_syscall_frame_t* frame)
{
    char c = (char)frame->rdi;

    vos3_console_putc(c);

    return (int64_t)(unsigned char)c;
}

/**
 * @brief SYS_PUTS - Write string to console
 */
int64_t vos3_sys_puts(vos3_syscall_frame_t* frame)
{
    const char* str = (const char*)frame->rdi;

    if (str == NULL) {
        return VOS3_SYSCALL_EFAULT;
    }

    /* Safe copy from user space into kernel buffer */
    char kbuf[256];
    int64_t len = strncpy_from_user(kbuf, str, sizeof(kbuf));
    if (len < 0) {
        return VOS3_SYSCALL_EFAULT;
    }

    vos3_console_puts(kbuf);

    return 0;
}

/**
 * @brief SYS_DEBUG - Debug output
 */
int64_t vos3_sys_debug(vos3_syscall_frame_t* frame)
{
    uint64_t value = frame->rdi;

    VOS3_DEBUG("sys_debug: 0x%016llx (%llu)",
               (unsigned long long)value,
               (unsigned long long)value);

    return 0;
}

/* ============================================================================
 * BLOCK DEVICE SYSCALLS (Day 7 - Kernel-only test interface)
 * ============================================================================ */

/* External block device test function */
extern int vos3_virtio_blk_test(void);
extern void vos3_virtio_blk_print_status(void);
extern int vos3_virtio_blk_available(void);
extern uint64_t vos3_virtio_blk_capacity(void);

/**
 * @brief SYS_BLKDEV_TEST - Run block device self-test
 *
 * SECURITY: This syscall runs kernel-level tests.
 * User-space cannot directly read/write to disk - must use VFS.
 */
int64_t vos3_sys_blkdev_test(vos3_syscall_frame_t* frame)
{
    (void)frame;

    /* DEBUG: Force visible output */
    vos3_console_puts("[BLKDEV] >>> SYSCALL 110 ENTERED <<<\n");

    VOS3_INFO("[SYSCALL] Running block device self-test...");

    int result = vos3_virtio_blk_test();

    VOS3_INFO("[BLKDEV] >>> TEST RETURNED: %d <<<", result);

    return (int64_t)result;
}

/**
 * @brief SYS_BLKDEV_INFO - Get block device info
 *
 * Returns disk capacity in sectors (or 0 if no disk).
 */
int64_t vos3_sys_blkdev_info(vos3_syscall_frame_t* frame)
{
    (void)frame;

    if (!vos3_virtio_blk_available()) {
        return 0;
    }

    vos3_virtio_blk_print_status();

    return (int64_t)vos3_virtio_blk_capacity();
}

/* ============================================================================
 * APP CONTEXT SYSCALLS (Phase N)
 * ============================================================================ */

/**
 * @brief SYS_APP_CTX_CREATE - Create per-app AI guard context
 * arg1 (rdi) = app_id
 */
int64_t vos3_sys_app_ctx_create(vos3_syscall_frame_t* frame)
{
    uint8_t app_id = (uint8_t)frame->rdi;
    VOS3_DEBUG("sys_app_ctx_create(%u)", app_id);
    return (int64_t)vos3_ai_guard_create_app_ctx(app_id);
}

/**
 * @brief SYS_APP_CTX_DESTROY - Destroy per-app AI guard context
 * arg1 (rdi) = app_id
 */
int64_t vos3_sys_app_ctx_destroy(vos3_syscall_frame_t* frame)
{
    uint8_t app_id = (uint8_t)frame->rdi;
    VOS3_DEBUG("sys_app_ctx_destroy(%u)", app_id);
    return (int64_t)vos3_ai_guard_destroy_app_ctx(app_id);
}

/**
 * @brief SYS_APP_CTX_SWITCH - Switch active app context
 * arg1 (rdi) = app_id
 */
int64_t vos3_sys_app_ctx_switch(vos3_syscall_frame_t* frame)
{
    uint8_t app_id = (uint8_t)frame->rdi;
    VOS3_DEBUG("sys_app_ctx_switch(%u)", app_id);
    return (int64_t)vos3_ai_guard_switch_ctx(app_id);
}

/* ============================================================================
 * AI MODEL LOAD (Phase 4.2)
 * ============================================================================ */

/**
 * @brief SYS_AI_MODEL_LOAD - Load AI model weights into protected HugePage memory
 * arg1 (rdi) = buffer pointer
 * arg2 (rsi) = size in bytes
 */
static int64_t vos3_sys_ai_model_load(vos3_syscall_frame_t* frame)
{
    const void *buf = (const void *)frame->rdi;
    size_t size     = (size_t)frame->rsi;

    /* Validate user-space pointer */
    if (buf == NULL || size == 0 || size > 128 * 1024 * 1024) {  /* Phase 4.3: 128MB */
        return -22;  /* EINVAL */
    }

    uintptr_t addr = vos3_ai_load_model(buf, size);
    int64_t result = addr ? (int64_t)addr : -12;  /* ENOMEM */

    /* Stack Protection: zero-fill locals to prevent weight data leakage
     * via kernel stack when returning to userspace */
    buf = NULL;
    size = 0;
    addr = 0;
    __asm__ volatile("" ::: "memory");  /* Prevent compiler from optimizing out */

    return result;
}

/**
 * @brief SYS_AI_SNAPSHOT - Demote slot cache lines to L3 (session persistence)
 * arg1 (rdi) = slot_id
 */
static int64_t vos3_sys_ai_snapshot(vos3_syscall_frame_t* frame)
{
    uint8_t slot_id = (uint8_t)frame->rdi;
    int rc = vos3_ai_model_snapshot(slot_id);
    return (int64_t)rc;
}

/**
 * @brief SYS_AI_CHECKPOINT - Rolling checkpoint for fast undo (Phase 4.2.7)
 * arg1 (rdi) = slot_id
 */
static int64_t vos3_sys_ai_checkpoint(vos3_syscall_frame_t* frame)
{
    uint8_t slot_id = (uint8_t)frame->rdi;
    int rc = vos3_ai_checkpoint(slot_id);
    return (int64_t)rc;
}

/* ============================================================================
 * APP PROCESS SANDBOX (Phase N.2.2)
 * ============================================================================ */

/**
 * @brief Check if a syscall is allowed for app-mode tasks.
 *
 * App tasks (VOS3_TASK_FLAG_APP set) have a narrow allowlist.
 * Returns 0 if allowed, VOS3_SYSCALL_EPERM if denied.
 */
static int64_t check_app_syscall_permission(uint64_t syscall_num)
{
    vos3_task_t* current = vos3_task_current();
    if (current == NULL) {
        return 0;  /* Allow (no task context) */
    }

    if (!(current->flags & VOS3_TASK_FLAG_APP)) {
        return 0;  /* Not an app task, allow everything */
    }

    /* Narrow allowlist for app tasks */
    switch (syscall_num) {
        /* File I/O */
        case VOS3_SYS_OPEN:
        case VOS3_SYS_CLOSE:
        case VOS3_SYS_READ:
        case VOS3_SYS_WRITE:
        case VOS3_SYS_LSEEK:
        case VOS3_SYS_STAT:
        case VOS3_SYS_FSTAT:
        case 6:   /* SYS_LSTAT */
        case 17:  /* SYS_PREAD64 */
        case 18:  /* SYS_PWRITE64 */
        case 19:  /* SYS_READV */
        case 86:  /* SYS_LINK */
        case 88:  /* SYS_SYMLINK */
        case 89:  /* SYS_READLINK */
        case 90:  /* SYS_CHMOD */
        case 91:  /* SYS_FCHMOD */
        case 92:  /* SYS_CHOWN */
        case 93:  /* SYS_FCHOWN */
        case 217: /* SYS_GETDENTS64 */
        case 292: /* SYS_DUP3 */
        case 293: /* SYS_PIPE2 */
        /* Memory — Linux-compat numbers for musl libc */
        case 9:    /* SYS_MMAP (Linux) */
        case 10:   /* SYS_MPROTECT (Linux) */
        case 11:   /* SYS_MUNMAP (Linux) */
        case 12:   /* SYS_BRK (Linux) */
        case VOS3_SYS_BRK:   /* =20, also covers SYS_WRITEV=20 */
        case VOS3_SYS_MMAP:  /* =21, also covers SYS_ACCESS=21 */
        /* Process (safe subset) */
        case VOS3_SYS_EXIT:
        case VOS3_SYS_YIELD:   /* now at 24 (Linux sched_yield) */
        case VOS3_SYS_SLEEP:
        case VOS3_SYS_GETPID:
        case VOS3_SYS_GETPPID:
        case VOS3_SYS_GETTID:
        /* Time */
        case VOS3_SYS_GETTIME:
        case VOS3_SYS_GETTICKS:
        /* Console */
        case VOS3_SYS_PUTCHAR:
        case VOS3_SYS_PUTS:
        /* IPC — VOS3 custom syscalls (400+ range) */
        case VOS3_SYS_SHM_CREATE:
        case VOS3_SYS_SHM_DESTROY:
        case VOS3_SYS_SHM_MAP:
        case VOS3_SYS_SHM_UNMAP:
        case VOS3_SYS_SHM_SIZE:
        case 29:  /* LINUX_SYS_SHMGET → shm_create */
        case 67:  /* LINUX_SYS_SHMDT → shm_unmap */
        case VOS3_SYS_MSGQ_CREATE:
        case VOS3_SYS_MSGQ_DESTROY:
        case VOS3_SYS_MSGQ_SEND:
        case VOS3_SYS_MSGQ_RECV:
        case VOS3_SYS_PIPE_CREATE:
        case VOS3_SYS_PIPE_READ:
        case VOS3_SYS_PIPE_WRITE:
        case VOS3_SYS_PIPE_CLOSE:
        case VOS3_SYS_KILL:
        case VOS3_SYS_SIGNAL:
        case VOS3_SYS_SIGACTION:
        case VOS3_SYS_SIGPROCMASK:
        case VOS3_SYS_SIGWAIT:
        /* Network (VOS3 custom numbers) */
        case VOS3_SYS_SOCKET:
        case VOS3_SYS_BIND:
        case VOS3_SYS_LISTEN:
        case VOS3_SYS_ACCEPT:
        case VOS3_SYS_CONNECT:
        case VOS3_SYS_SENDTO:
        case VOS3_SYS_RECVFROM:
        case VOS3_SYS_SEND:
        case VOS3_SYS_RECV:
        case VOS3_SYS_SHUTDOWN:
        case VOS3_SYS_GETSOCKOPT:
        case VOS3_SYS_SETSOCKOPT:
        /* Network (Linux x86-64 ABI aliases) */
        case 41:   /* __NR_socket */
        case 42:   /* __NR_connect */
        case 43:   /* __NR_accept */
        case 44:   /* __NR_sendto */
        case 45:   /* __NR_recvfrom */
        case 46:   /* __NR_sendmsg */
        case 47:   /* __NR_recvmsg */
        case 49:   /* __NR_bind */
        case 50:   /* __NR_listen */
        case 54:   /* __NR_setsockopt */
        case 55:   /* __NR_getsockopt */
        /* Task 1.3: epoll / select / eventfd */
        case 23:   /* SYS_SELECT */
        case 213:  /* SYS_EPOLL_CREATE */
        case 232:  /* SYS_EPOLL_WAIT */
        case 233:  /* SYS_EPOLL_CTL */
        case 284:  /* SYS_EVENTFD2 */
        case 291:  /* SYS_EPOLL_CREATE1 */
        /* Task 1.4: threading */
        case 56:   /* SYS_CLONE */
        case 158:  /* SYS_ARCH_PRCTL */
        case 218:  /* SYS_SET_TID_ADDRESS */
        /* Task 2.7: openat, newfstatat, getrandom, statx */
        case 257:  /* SYS_OPENAT */
        case 262:  /* SYS_NEWFSTATAT */
        case 318:  /* SYS_GETRANDOM */
        case 332:  /* SYS_STATX (stub) */
        case 77:   /* SYS_FTRUNCATE */
        case 280:  /* SYS_UTIMENSAT */
        case 273:  /* SYS_SET_ROBUST_LIST */
        /* Task 1.5: POSIX core + futex */
        case 160:  /* SYS_SETRLIMIT (Linux) — was colliding with old VOS3_SYS_GETSOCKOPT */
        case 16:   /* SYS_IOCTL (Linux) */
        /* 20=SYS_WRITEV covered by VOS3_SYS_BRK=20 above */
        /* 21=SYS_ACCESS covered by VOS3_SYS_MMAP=21 above */
        case 15:   /* SYS_RT_SIGRETURN */
        case 37:   /* SYS_ALARM */
        case 38:   /* SYS_SETITIMER */
        case 62:   /* SYS_KILL */
        case 63:   /* SYS_UNAME */
        case 97:   /* SYS_GETRLIMIT */
        case 98:   /* SYS_GETRUSAGE */
        case 99:   /* SYS_SYSINFO */
        case 202:  /* SYS_FUTEX */
        case 231:  /* SYS_EXIT_GROUP */
        case 200:  /* SYS_TKILL */
        case 234:  /* SYS_TGKILL */
            return 0;  /* Allowed */

        /* Explicitly denied: admin/config/delegation/debug syscalls */
        case VOS3_SYS_ADMIN_AUTH:
        case VOS3_SYS_CONFIG_SET:
        case VOS3_SYS_CONFIG_GET:
        case VOS3_SYS_DELEGATION_SET:
        case VOS3_SYS_DELEGATION_GET:
        case VOS3_SYS_DEBUG:
        case VOS3_SYS_BLKDEV_TEST:
        case VOS3_SYS_BLKDEV_INFO:
        case VOS3_SYS_APP_CTX_CREATE:
        case VOS3_SYS_APP_CTX_DESTROY:
        case VOS3_SYS_APP_CTX_SWITCH:
            VOS3_WARN("[SANDBOX] App task '%s' (pid=%u) denied syscall %llu",
                      current->name, current->pid,
                      (unsigned long long)syscall_num);
            return VOS3_SYSCALL_EPERM;

        default:
            /* Deny everything else not in the allowlist */
            VOS3_WARN("[SANDBOX] App task '%s' (pid=%u) denied syscall %llu (not in allowlist)",
                      current->name, current->pid,
                      (unsigned long long)syscall_num);
            return VOS3_SYSCALL_EPERM;
    }
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/* MSRs are CPU-local. This must not reset the shared dispatch table or
 * overwrite the scheduler-owned per-CPU stack binding. */
void vos3_syscall_init_cpu(void)
{
    /* Configure SYSCALL/SYSRET MSRs */

    /* STAR MSR:
     * Bits 47:32 = kernel CS (for SYSCALL): 0x08
     * Bits 63:48 = user CS (for SYSRET): 0x1B (user CS = 0x23, but SYSRET adds 16)
     *
     * For SYSRET in 64-bit mode:
     *   CS = STAR[63:48] + 16 = 0x1B + 16 = 0x2B... wait that's wrong
     *
     * Actually:
     *   SYSCALL: CS = STAR[47:32], SS = STAR[47:32] + 8
     *   SYSRET:  CS = STAR[63:48] + 16 (64-bit), SS = STAR[63:48] + 8
     *
     * Our GDT:
     *   0x00: Null
     *   0x08: Kernel Code (ring 0)
     *   0x10: Kernel Data (ring 0)
     *   0x18: User Code (ring 3, 32-bit) - placeholder
     *   0x20: User Data (ring 3)
     *   0x28: User Code (ring 3, 64-bit)
     *
     * For SYSCALL: CS = STAR[47:32], SS = STAR[47:32] + 8
     *   => CS = 0x08 (kernel code), SS = 0x10 (kernel data)
     *
     * For SYSRET (64-bit): CS = STAR[63:48] + 16 | 3, SS = STAR[63:48] + 8 | 3
     *   With base=0x08: CS = 0x08+16 | 3 = 0x1B (user code), SS = 0x08+8 | 3 = 0x13
     *   SS=0x13 is kernel data with RPL=3, NOT user data (0x23).
     *
     * NOTE: VOS3 GDT has User CS at 0x18 and User DS at 0x20.
     * SYSRET requires User DS = base+8 and User CS = base+16, which doesn't
     * match our GDT order. VOS3 uses IRETQ exclusively for syscall return
     * (see syscall_entry.S), so this STAR value only affects SYSCALL entry.
     *
     * STAR = (user_cs_base << 48) | (kernel_cs << 32)
     */
    uint64_t star = ((uint64_t)0x0008ULL << 48) |  /* User CS base (for SYSRET, unused) */
                    ((uint64_t)0x0008ULL << 32);   /* Kernel CS (for SYSCALL entry) */
    wrmsr(VOS3_MSR_STAR, star);

    /* LSTAR = syscall entry point */
    wrmsr(VOS3_MSR_LSTAR, (uint64_t)(uintptr_t)vos3_syscall_entry);

    /* SFMASK = RFLAGS bits to clear on SYSCALL (clear IF to disable interrupts) */
    wrmsr(VOS3_MSR_SFMASK, VOS3_SYSCALL_RFLAGS_MASK);

    /* Enable SYSCALL instruction in EFER */
    uint64_t efer = rdmsr(VOS3_MSR_EFER);
    efer |= VOS3_EFER_SCE;
    wrmsr(VOS3_MSR_EFER, efer);

}

int vos3_syscall_init(void)
{
    if (g_syscall_initialized != 0) {
        return VOS3_SYSCALL_EINVAL;
    }

    VOS3_INFO("Initializing syscall interface");

    /* Initialize syscall table with default handler */
    for (size_t i = 0U; i < VOS3_SYS_MAX; i++) {
        g_syscall_table[i] = syscall_not_implemented;
    }

    /* Register implemented syscalls */

    /* Process management */
    g_syscall_table[VOS3_SYS_EXIT] = vos3_sys_exit;
    g_syscall_table[VOS3_SYS_GETPID] = vos3_sys_getpid;
    g_syscall_table[VOS3_SYS_GETPPID] = vos3_sys_getppid;
    g_syscall_table[VOS3_SYS_GETTID] = vos3_sys_gettid;

    /* Thread management */
    g_syscall_table[VOS3_SYS_YIELD] = vos3_sys_yield;
    g_syscall_table[VOS3_SYS_SLEEP] = vos3_sys_sleep;

    /* Time */
    g_syscall_table[VOS3_SYS_GETTIME] = vos3_sys_gettime;
    g_syscall_table[VOS3_SYS_GETTICKS] = vos3_sys_getticks;

    /* Console */
    g_syscall_table[VOS3_SYS_PUTCHAR] = vos3_sys_putchar;
    g_syscall_table[VOS3_SYS_PUTS] = vos3_sys_puts;

    /* Debug */
    g_syscall_table[VOS3_SYS_DEBUG] = vos3_sys_debug;

    /* Block Device (Day 7) */
    g_syscall_table[VOS3_SYS_BLKDEV_TEST] = vos3_sys_blkdev_test;
    g_syscall_table[VOS3_SYS_BLKDEV_INFO] = vos3_sys_blkdev_info;

    /* App Context Isolation (Phase N) */
    g_syscall_table[VOS3_SYS_APP_CTX_CREATE] = vos3_sys_app_ctx_create;
    g_syscall_table[VOS3_SYS_APP_CTX_DESTROY] = vos3_sys_app_ctx_destroy;
    g_syscall_table[VOS3_SYS_APP_CTX_SWITCH] = vos3_sys_app_ctx_switch;

    /* AI Model Loading (Phase 4.2) */
    g_syscall_table[VOS3_SYS_AI_MODEL_LOAD] = vos3_sys_ai_model_load;

    /* AI Snapshot — Session Persistence (Phase 4.2.5) */
    g_syscall_table[VOS3_SYS_AI_SNAPSHOT] = vos3_sys_ai_snapshot;

    /* AI Checkpoint — Rolling Checkpoint (Phase 4.2.7) */
    g_syscall_table[VOS3_SYS_AI_CHECKPOINT] = vos3_sys_ai_checkpoint;

    /* Set up kernel stack for syscalls */
    vos3_entry_set_kernel_stack((uint64_t)(uintptr_t)g_syscall_stack_top);

    vos3_syscall_init_cpu();

    g_syscall_initialized = 1;

    VOS3_INFO("Syscall interface initialized (LSTAR=0x%llx)",
              (unsigned long long)(uintptr_t)vos3_syscall_entry);

    /* Register VOS3 configuration & delegation syscalls (Phase 24) */
    vos3_config_register_syscalls();

    return VOS3_SYSCALL_OK;
}

int vos3_syscall_register(vos3_syscall_num_t num, vos3_syscall_handler_t handler)
{
    if ((uint32_t)num >= VOS3_SYS_MAX) {
        return VOS3_SYSCALL_EINVAL;
    }

    if (handler == NULL) {
        return VOS3_SYSCALL_EINVAL;
    }

    if (g_syscall_table[num] != NULL) {
        VOS3_WARN("Syscall %u: overwriting handler %p with %p",
                  (unsigned)num, (void*)(uintptr_t)g_syscall_table[num],
                  (void*)(uintptr_t)handler);
    }

    g_syscall_table[num] = handler;

    return VOS3_SYSCALL_OK;
}

/**
 * @brief System call dispatcher
 *
 * Called from assembly with pointer to syscall frame on stack.
 */
int64_t vos3_syscall_dispatch(vos3_syscall_frame_t* frame)
{
    uint64_t syscall_num = frame->rax;

    /* Debug: log blkdev syscalls */
    if (syscall_num == VOS3_SYS_BLKDEV_TEST || syscall_num == VOS3_SYS_BLKDEV_INFO) {
        vos3_console_puts("[DISPATCH] >>> BLKDEV SYSCALL RECEIVED <<<\n");
        VOS3_INFO("[DISPATCH] syscall_num=%llu", (unsigned long long)syscall_num);
    }

    /* Debug: log brk syscall entry */
    if (syscall_num == 12) {  /* SYS_BRK */
        VOS3_DEBUG("DISPATCH: sys_brk arg1=0x%llx",
                   (unsigned long long)frame->rdi);
    }

    /* Validate syscall number */
    if (syscall_num >= VOS3_SYS_MAX) {
        VOS3_WARN("Invalid syscall number: %llu",
                     (unsigned long long)syscall_num);
        return VOS3_SYSCALL_ENOSYS;
    }

    /* App sandbox check (Phase N.2.2) */
    int64_t perm_check = check_app_syscall_permission(syscall_num);
    if (perm_check != 0) {
        return perm_check;
    }

    /* Get handler */
    vos3_syscall_handler_t handler = g_syscall_table[syscall_num];

    /* Debug: trace blkdev handler */
    if (syscall_num == 220) {
        VOS3_INFO("[DISPATCH] handler ptr=0x%llx, expected=0x%llx",
                  (unsigned long long)(uintptr_t)handler,
                  (unsigned long long)(uintptr_t)vos3_sys_blkdev_test);
    }

    /* Call handler */
    int64_t result = handler(frame);

    /* The handler released subsystem locks; syscall_entry still has IF set.
     * Blocking wait/exit and timer continuations can all reschedule IRQ-off,
     * so they cannot be the sole opportunities to reclaim retired resources. */
    vos3_sched_process_deferred();

    /* Deliver pending signals before returning to user space */
    extern void vos3_signal_deliver(vos3_syscall_frame_t *frame, int64_t result);
    vos3_signal_deliver(frame, result);

    return result;
}

/**
 * @brief Debug function called from assembly before IRETQ
 *
 * This function is called from syscall_entry.S to debug the return values.
 * It must preserve all registers except RAX.
 */
void vos3_debug_syscall_return(uint64_t rcx, uint64_t r11, uint64_t user_rsp)
{
    /* Only print for interesting syscalls (like fork which returns 100) */
    static int fork_seen = 0;
    if (rcx == 0x4002A7) {
        fork_seen = 1;
        (void)fork_seen;
        VOS3_DEBUG("IRETQ-FORK: rcx(RIP)=0x%llx, r11(RFLAGS)=0x%llx, user_rsp=0x%llx",
                   (unsigned long long)rcx,
                   (unsigned long long)r11,
                   (unsigned long long)user_rsp);
    }
}

/**
 * @brief Debug function to print IRETQ stack frame contents
 */
void vos3_debug_iretq_frame(uint64_t* rsp)
{
    /* The IRETQ frame is:
     * [rsp+0]  = RIP
     * [rsp+8]  = CS
     * [rsp+16] = RFLAGS
     * [rsp+24] = RSP
     * [rsp+32] = SS
     */
    VOS3_DEBUG("IRETQ FRAME at RSP=%p:", (void*)rsp);

    /* Validate that RSP is accessible */
    if (rsp == NULL) {
        VOS3_DEBUG("  RSP is NULL!");
        return;
    }

    /* Try to access each value carefully */
    volatile uint64_t val0 = rsp[0];
    VOS3_DEBUG("  RIP    = 0x%llx", (unsigned long long)val0);

    volatile uint64_t val1 = rsp[1];
    VOS3_DEBUG("  CS     = 0x%llx", (unsigned long long)val1);

    volatile uint64_t val2 = rsp[2];
    VOS3_DEBUG("  RFLAGS = 0x%llx", (unsigned long long)val2);

    volatile uint64_t val3 = rsp[3];
    VOS3_DEBUG("  RSP    = 0x%llx", (unsigned long long)val3);

    volatile uint64_t val4 = rsp[4];
    VOS3_DEBUG("  SS     = 0x%llx", (unsigned long long)val4);
}
