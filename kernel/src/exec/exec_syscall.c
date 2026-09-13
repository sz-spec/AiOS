/**
 * @file exec_syscall.c
 * @brief VOS3 Process Management System Calls
 *
 * @details Implements exec, fork, wait, exit, and process info syscalls.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/elf.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/user.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/vfs.h"

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_BRK         12
#define SYS_NANOSLEEP   35
#define SYS_CLONE       56
#define SYS_FORK        57
#define SYS_EXECVE      59
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_GETPID      39
#define SYS_GETPPID     110
#define SYS_GETTID      186
#define SYS_GETUID      102
#define SYS_GETGID      104
#define SYS_SETUID      105
#define SYS_SETGID      106
#define SYS_SETSID      112
#define SYS_GETPGID     121
#define SYS_SETPGID     109
#define SYS_MMAP        9
#define SYS_MPROTECT    10
#define SYS_MUNMAP      11
#define SYS_ARCH_PRCTL      158
#define SYS_SET_TID_ADDRESS 218
#define SYS_EXIT_GROUP  231
#define SYS_SET_ROBUST_LIST  273

/* Linux clone() flags (subset needed for pthreads) */
#define CLONE_VM        0x00000100UL  /**< Share address space */
#define CLONE_FS        0x00000200UL  /**< Share filesystem */
#define CLONE_FILES     0x00000400UL  /**< Share file descriptors */
#define CLONE_SIGHAND   0x00000800UL  /**< Share signal handlers */
#define CLONE_THREAD    0x00010000UL  /**< Same thread group */
#define CLONE_SETTLS    0x00080000UL  /**< Set TLS to r8 argument */
#define CLONE_PARENT_SETTID   0x00100000UL
#define CLONE_CHILD_CLEARTID  0x00200000UL
#define CLONE_CHILD_SETTID    0x01000000UL
#define CLONE_SYSVSEM         0x00040000UL  /* Ignored — no SysV semaphores */
#define CLONE_DETACHED        0x00400000UL  /* Ignored — handled via is_thread */

/* sys_arch_prctl() codes */
#define ARCH_SET_GS  0x1001
#define ARCH_SET_FS  0x1002
#define ARCH_GET_FS  0x1003
#define ARCH_GET_GS  0x1004

/* MSR_FS_BASE: x86-64 per-thread %fs base register */
#define MSR_FS_BASE  0xC0000100U
#define MSR_GS_BASE         0xC0000101U
#define MSR_KERNEL_GSBASE   0xC0000102U  /* shadow GS used by SWAPGS */

static inline void exec_wrmsr(uint32_t msr, uint64_t val)
{
    __asm__ volatile("wrmsr"
                     : : "c"(msr),
                         "a"((uint32_t)(val & 0xFFFFFFFFULL)),
                         "d"((uint32_t)(val >> 32)));
}

static inline uint64_t exec_rdmsr(uint32_t msr)
{
    uint32_t lo, hi;
    __asm__ volatile("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr));
    return ((uint64_t)hi << 32) | lo;
}

/* mmap protection flags */
#define PROT_NONE       0x0
#define PROT_READ       0x1
#define PROT_WRITE      0x2
#define PROT_EXEC       0x4

/* mmap flags */
#define MAP_SHARED      0x01
#define MAP_PRIVATE     0x02
#define MAP_ANONYMOUS   0x20
#define MAP_FIXED       0x10

/** @brief User-space base for mmap allocations (bump allocator) */
#define VOS3_MMAP_BASE  0x0000000030000000ULL
/* Phase v17 (K-C5): g_mmap_next moved to vos3_task_t.mmap_next (per-process).
 * Legacy global kept only as fallback for init task before task struct exists. */
static uint64_t g_mmap_next = VOS3_MMAP_BASE;

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Copy argv/envp arrays from user space
 * @param[in] user_array User space array pointer
 * @param[out] count Number of elements
 * @return Kernel copy of array, or NULL on error
 */
static const char** copy_string_array(const char* const* user_array, size_t* count)
{
    if (user_array == NULL) {
        *count = 0U;
        return NULL;
    }

    /* Count elements */
    size_t n = 0U;
    for (n = 0U; n < 256U; n++) {
        const char* ptr;
        if (vos3_copy_from_user(&ptr, &user_array[n], sizeof(ptr)) != 0) {
            return NULL;
        }
        if (ptr == NULL) {
            break;
        }
    }

    *count = n;
    if (n == 0U) {
        return NULL;
    }

    /* Allocate kernel array */
    const char** result = (const char**)vos3_kmalloc((n + 1U) * sizeof(char*));
    if (result == NULL) {
        return NULL;
    }

    /* Copy each string */
    for (size_t i = 0U; i < n; i++) {
        const char* user_str;
        if (vos3_copy_from_user(&user_str, &user_array[i], sizeof(user_str)) != 0) {
            goto cleanup;
        }

        /* Allocate and copy string */
        char temp[4096];
        int64_t len = vos3_strncpy_from_user(temp, user_str, sizeof(temp) - 1U);
        if (len < 0) {
            goto cleanup;
        }
        temp[len] = '\0';

        char* kstr = (char*)vos3_kmalloc((size_t)len + 1U);
        if (kstr == NULL) {
            goto cleanup;
        }
        memcpy(kstr, temp, (size_t)len + 1U);
        result[i] = kstr;
    }

    result[n] = NULL;
    return result;

cleanup:
    for (size_t i = 0U; i < n && result[i] != NULL; i++) {
        vos3_kfree((void*)result[i]);
    }
    vos3_kfree(result);
    return NULL;
}

/**
 * @brief Free copied string array
 */
static void free_string_array(const char** array, size_t count)
{
    if (array == NULL) {
        return;
    }

    for (size_t i = 0U; i < count; i++) {
        if (array[i] != NULL) {
            vos3_kfree((void*)array[i]);
        }
    }
    vos3_kfree(array);
}

/* ============================================================================
 * SYSCALL HANDLERS
 * ============================================================================ */

/**
 * @brief sys_fork - Create child process
 *
 * Passes user-mode state (syscall frame) to vos3_fork so the
 * child can return to user mode at the correct location with
 * all callee-saved registers properly restored.
 */
static int64_t sys_fork(vos3_syscall_frame_t* frame)
{
    uint64_t user_rsp = frame->user_rsp;

    VOS3_DEBUG("sys_fork: BEFORE fork, frame->rcx=0x%llx, frame->r11=0x%llx",
               (unsigned long long)frame->rcx, (unsigned long long)frame->r11);

    int result = vos3_fork_with_frame(frame, user_rsp);

    VOS3_DEBUG("sys_fork: AFTER fork, frame->rcx=0x%llx, frame->r11=0x%llx, result=%d",
               (unsigned long long)frame->rcx, (unsigned long long)frame->r11, result);

    return (int64_t)result;
}

/* vos3_fork_child_return: defined in user.c, used as thread entry trampoline */
extern void vos3_fork_child_return(void);

/**
 * @brief sys_clone - Create thread or process
 *
 * With CLONE_VM: creates a thread sharing the caller's address space.
 * Without CLONE_VM: behaves like fork() (delegates to vos3_fork_with_frame).
 *
 * Thread syscall args (Linux convention):
 *   rdi = flags
 *   rsi = child_stack (top of new thread's stack)
 *   rdx = parent_tidptr  (ignored for now)
 *   r10 = child_tidptr   (ignored for now)
 *   r8  = tls            (used if CLONE_SETTLS)
 */
static int64_t sys_clone(vos3_syscall_frame_t* frame)
{
    uint64_t flags      = frame->rdi;
    uint64_t child_stack = frame->rsi;   /* top of new thread stack */
    uint64_t tls        = frame->r8;     /* TLS base if CLONE_SETTLS */

    /* If not sharing VM, just fork */
    if (!(flags & CLONE_VM)) {
        uint64_t user_rsp = frame->user_rsp;
        return (int64_t)vos3_fork_with_frame(frame, user_rsp);
    }

    /* --- Thread creation path --- */

    /* Validate child_stack: must be non-NULL and in user space */
    if (child_stack == 0) {
        return -22;  /* EINVAL */
    }
    if (!access_ok((void*)child_stack, sizeof(uint64_t))) {
        return -14;  /* EFAULT */
    }

    /* Validate TLS pointer if CLONE_SETTLS requested */
    if ((flags & CLONE_SETTLS) && tls != 0) {
        if (!access_ok((void*)tls, sizeof(uint64_t))) {
            return -14;  /* EFAULT */
        }
    }

    vos3_task_t* parent = vos3_sched_current();
    if (parent == NULL) {
        return -1;
    }

    VOS3_DEBUG("sys_clone: creating thread, parent='%s' pid=%u stack=0x%llx tls=0x%llx",
               parent->name, parent->pid,
               (unsigned long long)child_stack,
               (unsigned long long)tls);

    /* Allocate thread task struct (copy from parent) */
    vos3_task_t* child = (vos3_task_t*)vos3_kzalloc(sizeof(vos3_task_t));
    if (child == NULL) {
        VOS3_ERROR("sys_clone: failed to allocate thread task");
        return -12;  /* ENOMEM */
    }
    memcpy(child, parent, sizeof(vos3_task_t));

    /* Assign new TID; PID = parent's PID for CLONE_THREAD (Linux TGID semantics) */
    static uint32_t s_thread_pid = 300U;
    child->tid = s_thread_pid++;
    child->pid = (flags & CLONE_THREAD) ? parent->pid : child->tid;

    /* Thread name: "<parent>/t" */
    size_t name_len = strlen(parent->name);
    if (name_len > VOS3_TASK_NAME_LEN - 3U) {
        name_len = VOS3_TASK_NAME_LEN - 3U;
    }
    memcpy(child->name, parent->name, name_len);
    child->name[name_len]     = '/';
    child->name[name_len + 1U] = 't';
    child->name[name_len + 2U] = '\0';

    /*
     * Thread's user-space stack is provided by the caller (child_stack arg),
     * managed in user-space.  The memcpy copied the parent's kernel-allocated
     * user_stack pointer — clear it so the reaper doesn't double-free it.
     */
    child->user_stack      = NULL;
    child->user_stack_size = 0;

    /* FPU: each thread must have its own state buffer.
     * The memcpy copied the parent's fpu_state pointer — clear it so the
     * #NM handler allocates a fresh buffer on first FPU use. */
    child->fpu_state_raw  = NULL;
    child->fpu_state      = NULL;
    child->fpu_initialized = 0;

    /* Allocate kernel stack via vmap (guard page + mapped pages) */
    uintptr_t clone_guard_va = 0;
    child->kernel_stack = vos3_vmap_stack_alloc(&clone_guard_va);
    if (child->kernel_stack == NULL) {
        VOS3_ERROR("sys_clone: failed to allocate vmap kernel stack");
        vos3_kfree(child);
        return -12;  /* ENOMEM */
    }
    child->kernel_stack_size = VOS3_VMAP_STACK_PAGES * VOS3_PAGE_SIZE;
    child->kernel_stack_guard = clone_guard_va;

    /* CLONE_VM: share address space (no COW clone) */
    /* child->address_space already copied from parent via memcpy */

    /* CLONE_FILES: share fd_table — bump table ref_count (not per-file) */
    if ((flags & CLONE_FILES) && parent->fd_table != NULL) {
        __atomic_add_fetch(&parent->fd_table->ref_count, 1U, __ATOMIC_ACQ_REL);
    }

    /* Mark as thread */
    child->is_thread = 1;
    child->flags |= VOS3_TASK_FLAG_THREAD;
    child->flags |= VOS3_TASK_FLAG_USER;

    /* Thread group: tgid = process leader's pid */
    if (parent->is_thread) {
        child->tgid = parent->tgid;
        child->thread_group_leader = parent->thread_group_leader;
    } else {
        child->tgid = parent->pid;
        child->thread_group_leader = parent;
    }

    /* Link into thread group list */
    child->thread_next = parent->thread_next;
    parent->thread_next = child;

    /* TLS: set %fs base if requested */
    child->tls_base = (flags & CLONE_SETTLS) ? tls : parent->tls_base;
    child->user_gs_base = parent->user_gs_base;

    /*
     * Thread return state:
     *   - RIP = same as parent after syscall (frame->rcx)
     *   - RSP = provided child_stack (fresh thread stack)
     *   - RFLAGS = same as parent (frame->r11)
     *   - Callee-saved regs = 0 (thread starts fresh)
     *   - RAX = 0 (vos3_fork_child_return returns 0 to child)
     */
    child->fork_ret_rip    = frame->rcx;
    child->fork_ret_rsp    = child_stack;
    child->fork_ret_rflags = frame->r11;
    /*
     * Inherit parent's callee-saved registers — this matches Linux clone()
     * semantics exactly.  The child shares the parent's address space
     * (CLONE_VM), so the parent's stack frame (pointed to by RBP) remains
     * mapped and readable.  The child needs the parent's RBP to correctly
     * access local variables compiled with -fno-omit-frame-pointer (-O2
     * default) before it establishes its own frame on the new child stack.
     * Zeroing these (as for fork) would cause SIGSEGV via [rbp-N] with rbp=0.
     */
    child->fork_ret_rbx    = frame->rbx;
    child->fork_ret_rbp    = frame->rbp;
    child->fork_ret_r12    = frame->r12;
    child->fork_ret_r13    = frame->r13;
    child->fork_ret_r14    = frame->r14;
    child->fork_ret_r15    = frame->r15;
    /* Caller-saved regs needed by musl's clone.s (r9=func ptr, r8=tls) */
    child->fork_ret_r8     = frame->r8;
    child->fork_ret_r9     = frame->r9;
    child->fork_ret_r10    = frame->r10;
    child->is_fork_child   = 1;

    /* Set up kernel stack context pointing to fork_child_return trampoline */
    uint64_t kstack_top = (uint64_t)(uintptr_t)child->kernel_stack + child->kernel_stack_size;
    kstack_top &= ~15ULL;
    kstack_top -= 8;  /* 16n-8 alignment (after ret, RSP is 16n-8) */
    child->context = (vos3_context_t*)(kstack_top - sizeof(vos3_context_t));
    memset(child->context, 0, sizeof(vos3_context_t));
    child->context->rip = (uint64_t)(uintptr_t)vos3_fork_child_return;
    child->kernel_rsp   = (uint64_t)(uintptr_t)child->context;

    /* Thread has no parent (detached) */
    child->parent   = parent;
    child->children = NULL;
    child->sibling  = parent->children;
    parent->children = child;

    child->state = VOS3_TASK_READY;
    child->next  = NULL;
    child->prev  = NULL;

    /* Register and schedule */
    if (vos3_task_register(child) != 0) {
        VOS3_ERROR("sys_clone: failed to register thread in task table");
        vos3_kfree(child->kernel_stack);
        vos3_kfree(child);
        return -12;  /* ENOMEM */
    }

    vos3_sched_add_task(child);

    /* CLONE_PARENT_SETTID: write child TID to parent's *ptid (rdx) */
    if (flags & CLONE_PARENT_SETTID) {
        uint32_t* ptid = (uint32_t*)frame->rdx;
        if (ptid != NULL && (uintptr_t)ptid < 0xFFFF800000000000ULL) {
            uint32_t tid_val = child->tid;
            copy_to_user(ptid, &tid_val, sizeof(uint32_t));
        }
    }

    /* CLONE_CHILD_CLEARTID: save ctid pointer (r10) for futex_wake on exit */
    if (flags & CLONE_CHILD_CLEARTID) {
        child->clear_child_tid = (volatile uint32_t*)frame->r10;
    }

    VOS3_INFO("sys_clone: created thread '%s' tid=%u from parent '%s' pid=%u",
              child->name, child->tid, parent->name, parent->pid);

    return (int64_t)child->tid;
}

/**
 * @brief sys_arch_prctl - Set/Get architecture-specific thread state
 *
 * Primarily used by musl/glibc to set the %fs base for TLS.
 * arg1 (rdi) = code
 * arg2 (rsi) = addr
 */
static int64_t sys_arch_prctl(int code, uint64_t addr)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -22;  /* EINVAL */
    }

    if ((code == ARCH_SET_FS || code == ARCH_SET_GS) && addr > USER_SPACE_END) {
        return -22;  /* Reject noncanonical/kernel bases before WRMSR. */
    }

    switch (code) {
        case ARCH_SET_FS:
            /* Store TLS base in task and write to hardware MSR immediately */
            current->tls_base = addr;
            exec_wrmsr(MSR_FS_BASE, addr);
            VOS3_DEBUG("arch_prctl ARCH_SET_FS=0x%llx for task '%s'",
                       (unsigned long long)addr, current->name);
            return 0;

        case ARCH_GET_FS: {
            uint64_t fs_base = exec_rdmsr(MSR_FS_BASE);
            uint64_t* user_ptr = (uint64_t*)addr;
            if (user_ptr == NULL || !access_ok(user_ptr, sizeof(uint64_t))) {
                return -14;  /* EFAULT */
            }
            if (copy_to_user(user_ptr, &fs_base, sizeof(uint64_t)) != 0) {
                return -14;  /* EFAULT */
            }
            return 0;
        }

        case ARCH_SET_GS:
            /*
             * With SWAPGS at every ring-0 entry, IA32_GS_BASE holds the
             * kernel GS while we run here.  User TLS lives in the shadow
             * register (IA32_KERNEL_GSBASE) so SWAPGS on return restores it.
             */
            current->user_gs_base = addr;
            exec_wrmsr(MSR_KERNEL_GSBASE, addr);
            return 0;

        case ARCH_GET_GS: {
            /* After SWAPGS, user GS is in the shadow register. Never expose
             * the kernel's CPU-local entry-state address to user space. */
            uint64_t gs_base = exec_rdmsr(MSR_KERNEL_GSBASE);
            uint64_t* user_ptr = (uint64_t*)addr;
            if (user_ptr == NULL || !access_ok(user_ptr, sizeof(uint64_t))) {
                return -14;  /* EFAULT */
            }
            if (copy_to_user(user_ptr, &gs_base, sizeof(uint64_t)) != 0) {
                return -14;  /* EFAULT */
            }
            return 0;
        }

        default:
            return -22;  /* EINVAL */
    }
}

/**
 * @brief sys_set_tid_address - Store thread's clear_child_tid pointer
 *
 * Required by musl libc startup. Stub that stores the pointer and
 * returns current TID (which is what musl expects).
 */
static int64_t sys_set_tid_address(uint64_t tidptr)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return 0;
    }
    current->clear_child_tid = (volatile uint32_t*)(uintptr_t)tidptr;
    return (int64_t)current->tid;
}

/* ============================================================================
 * ENVIRONMENT SANITIZER
 * ============================================================================ */

/** @brief Whitelisted environment variable prefixes for sys_execve */
static const char* const g_env_whitelist[] = {
    "PATH=",
    "HOME=",
    "LANG=",
    "TERM=",
    "VOS3_",
    NULL
};

/** @brief Dangerous environment variables to always strip (RCE vectors) */
static const char* const g_env_blacklist[] = {
    "LD_PRELOAD=",
    "LD_LIBRARY_PATH=",
    "PYTHONPATH=",
    "GIT_PAGER=",
    "LD_AUDIT=",
    "LD_DEBUG=",
    NULL
};

/**
 * @brief Check if an environment variable matches a prefix
 */
static int env_matches_prefix(const char* var, const char* prefix)
{
    size_t plen = strlen(prefix);
    return (memcmp(var, prefix, plen) == 0) ? 1 : 0;
}

/**
 * @brief Filter environment variables through whitelist/blacklist
 *
 * Returns a new filtered array. Caller must free via free_string_array().
 * Strips dangerous vars (LD_PRELOAD etc), only passes whitelisted prefixes.
 */
static const char** sanitize_envp(const char** envp, size_t envc, size_t* out_count)
{
    if (envp == NULL || envc == 0U) {
        *out_count = 0U;
        return NULL;
    }

    /* Allocate output array (max same size as input) */
    const char** filtered = (const char**)vos3_kmalloc((envc + 1U) * sizeof(char*));
    if (filtered == NULL) {
        *out_count = 0U;
        return NULL;
    }

    size_t out = 0U;
    for (size_t i = 0U; i < envc; i++) {
        if (envp[i] == NULL) {
            continue;
        }

        /* Check blacklist first — always strip these */
        int blacklisted = 0;
        for (size_t b = 0U; g_env_blacklist[b] != NULL; b++) {
            if (env_matches_prefix(envp[i], g_env_blacklist[b])) {
                blacklisted = 1;
                break;
            }
        }
        if (blacklisted) {
            continue;
        }

        /* Check whitelist — only pass allowed prefixes */
        int allowed = 0;
        for (size_t w = 0U; g_env_whitelist[w] != NULL; w++) {
            if (env_matches_prefix(envp[i], g_env_whitelist[w])) {
                allowed = 1;
                break;
            }
        }
        if (!allowed) {
            continue;
        }

        /* Copy the string for the filtered array */
        size_t slen = strlen(envp[i]);
        char* copy = (char*)vos3_kmalloc(slen + 1U);
        if (copy == NULL) {
            break;
        }
        memcpy(copy, envp[i], slen + 1U);
        filtered[out++] = copy;
    }

    filtered[out] = NULL;
    *out_count = out;
    return filtered;
}

/**
 * @brief sys_execve - Execute program
 */
static int64_t sys_execve(const char* filename, const char* const* argv,
                          const char* const* envp)
{
    /* Copy filename from user space */
    char path[256];
    int64_t len = vos3_strncpy_from_user(path, filename, sizeof(path) - 1U);
    if (len < 0) {
        return -14;  /* EFAULT */
    }
    path[len] = '\0';

    /* Copy argv and envp */
    size_t argc, envc;
    const char** kargv = copy_string_array(argv, &argc);
    const char** kenvp = copy_string_array(envp, &envc);

    /* Sanitize environment: whitelist safe vars, strip dangerous ones */
    size_t safe_envc;
    const char** safe_envp = sanitize_envp(kenvp, envc, &safe_envc);

    /* Execute with sanitized environment */
    int result = vos3_exec(path, kargv, safe_envp != NULL ? safe_envp : kenvp);

    /* Clean up (only reached on error) */
    free_string_array(kargv, argc);
    free_string_array(kenvp, envc);
    if (safe_envp != NULL) {
        free_string_array(safe_envp, safe_envc);
    }

    return (int64_t)result;
}

/**
 * @brief sys_exit - Exit process
 */
static int64_t sys_exit(int status)
{
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL) {
        current->exit_code = status;
        vos3_task_exit(status);
    }
    /* Should not return */
    return 0;
}

/**
 * @brief sys_exit_group - Exit all threads in a process
 *
 * On a single-core OS this is identical to sys_exit.
 */
static int64_t sys_exit_group(int status)
{
    return sys_exit(status);
}

/**
 * @brief sys_set_robust_list - Stub for robust futex list
 *
 * musl's __pthread_exit calls this. Just return 0.
 */
static int64_t sys_set_robust_list(uint64_t head, size_t len)
{
    (void)head; (void)len;
    return 0;
}

/**
 * @brief sys_mprotect - Change memory protection on a page range
 */
static int64_t sys_mprotect(uint64_t addr, uint64_t len, int prot)
{
    if (addr == 0 || len == 0) {
        return 0;
    }
    return (int64_t)vos3_vmm_mprotect_range((uintptr_t)addr, (size_t)len, prot);
}

/**
 * @brief sys_wait4 - Wait for child process (Phase 29: Hardened)
 */
static int64_t sys_wait4(int pid, int* user_status, int options)
{
    int kstatus = 0;

    /* Phase 29: Validate status pointer if provided */
    if (user_status != NULL && !access_ok(user_status, sizeof(int))) {
        return -EFAULT;
    }

    int result = vos3_waitpid(pid, &kstatus, options);

    if (result >= 0 && user_status != NULL) {
        if (copy_to_user(user_status, &kstatus, sizeof(int)) != 0) {
            return -EFAULT;
        }
    }

    return (int64_t)result;
}

/**
 * @brief sys_getpid - Get process ID
 */
static int64_t sys_getpid(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return 0;
    }
    return (int64_t)current->pid;
}

/**
 * @brief sys_getppid - Get parent process ID
 */
static int64_t sys_getppid(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL || current->parent == NULL) {
        return 0;
    }
    return (int64_t)current->parent->pid;
}

/**
 * @brief sys_gettid - Get thread ID
 */
static int64_t sys_gettid(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return 0;
    }
    return (int64_t)current->tid;
}

/**
 * @brief sys_getuid - Get user ID
 */
static int64_t sys_getuid(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return 0;
    }
    return (int64_t)current->uid;
}

/**
 * @brief sys_getgid - Get group ID
 */
static int64_t sys_getgid(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return 0;
    }
    return (int64_t)current->gid;
}

/**
 * @brief sys_setuid - Set user ID
 */
static int64_t sys_setuid(uint32_t uid)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -1;
    }

    /* Only root can change to arbitrary UID */
    if (current->uid != 0U && uid != current->uid) {
        return -1;  /* EPERM */
    }

    current->uid = uid;
    return 0;
}

/**
 * @brief sys_setgid - Set group ID
 */
static int64_t sys_setgid(uint32_t gid)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -1;
    }

    /* Only root can change to arbitrary GID */
    if (current->uid != 0U && gid != current->gid) {
        return -1;  /* EPERM */
    }

    current->gid = gid;
    return 0;
}

/**
 * @brief sys_setsid - Create session
 */
static int64_t sys_setsid(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -1;
    }

    /* Already session leader? */
    if (current->pid == current->sid) {
        return -1;  /* EPERM */
    }

    current->sid = current->pid;
    current->pgid = current->pid;

    return (int64_t)current->sid;
}

/**
 * @brief sys_getpgid - Get process group ID
 */
static int64_t sys_getpgid(int pid)
{
    vos3_task_t* task;

    if (pid == 0) {
        task = vos3_sched_current();
    } else {
        task = vos3_task_find_by_pid((uint32_t)pid);
    }

    if (task == NULL) {
        return -3;  /* ESRCH */
    }

    return (int64_t)task->pgid;
}

/**
 * @brief sys_setpgid - Set process group ID
 */
static int64_t sys_setpgid(int pid, int pgid)
{
    vos3_task_t* current = vos3_sched_current();
    vos3_task_t* task;

    if (pid == 0) {
        task = current;
    } else {
        task = vos3_task_find_by_pid((uint32_t)pid);
    }

    if (task == NULL) {
        return -3;  /* ESRCH */
    }

    /* Can only set own pgid or child's */
    if (task != current && task->parent != current) {
        return -1;  /* EPERM */
    }

    if (pgid == 0) {
        pgid = (int)task->pid;
    }

    task->pgid = (uint32_t)pgid;
    return 0;
}

/* ============================================================================
 * MEMORY SYSCALLS
 * ============================================================================ */

/**
 * @brief sys_brk - Change data segment size
 *
 * If addr is 0, returns the current program break.
 * Otherwise, sets the program break to addr and maps/unmaps pages as needed.
 */
static int64_t sys_brk(uint64_t addr)
{
    VOS3_DEBUG("sys_brk: ENTRY addr=0x%llx", (unsigned long long)addr);

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->address_space == NULL) {
        VOS3_DEBUG("sys_brk: no task or address_space");
        return -12;  /* ENOMEM */
    }

    vos3_address_space_t* as = task->address_space;

    VOS3_DEBUG("sys_brk: addr=0x%llx, current brk=0x%llx, brk_start=0x%llx",
               (unsigned long long)addr,
               (unsigned long long)as->brk,
               (unsigned long long)as->brk_start);

    /* If addr is 0, return current break */
    if (addr == 0ULL) {
        VOS3_DEBUG("sys_brk: returning current brk=0x%llx for addr=0 query",
                   (unsigned long long)as->brk);
        return (int64_t)as->brk;
    }

    /* Don't allow brk below the initial brk */
    if (addr < as->brk_start) {
        VOS3_DEBUG("sys_brk: addr below brk_start, returning current");
        return (int64_t)as->brk;
    }

    /* Page-align addresses */
    uint64_t old_brk_page = (as->brk + VOS3_PAGE_SIZE - 1ULL) & ~(VOS3_PAGE_SIZE - 1ULL);
    uint64_t new_brk_page = (addr + VOS3_PAGE_SIZE - 1ULL) & ~(VOS3_PAGE_SIZE - 1ULL);

    VOS3_DEBUG("sys_brk: old_brk_page=0x%llx, new_brk_page=0x%llx",
               (unsigned long long)old_brk_page, (unsigned long long)new_brk_page);

    /* Map new pages if brk is increasing */
    if (new_brk_page > old_brk_page) {
        VOS3_DEBUG("sys_brk: will map %llu pages",
                   (unsigned long long)((new_brk_page - old_brk_page) / VOS3_PAGE_SIZE));
        for (uint64_t page = old_brk_page; page < new_brk_page; page += VOS3_PAGE_SIZE) {
            /* Check if page is already mapped (e.g., pre-mapped by exec) */
            if (vos3_vmm_is_mapped((uintptr_t)page) != 0) {
                VOS3_DEBUG("sys_brk: page 0x%llx already mapped, skipping",
                           (unsigned long long)page);
                continue;
            }

            VOS3_DEBUG("sys_brk: mapping page 0x%llx", (unsigned long long)page);

            /* Allocate physical page */
            uint64_t phys = vos3_pmm_alloc(0);
            if (phys == 0ULL) {
                VOS3_ERROR("sys_brk: out of memory");
                return (int64_t)as->brk;
            }

            /* Map the page as user-writable, no-execute */
            int result = vos3_vmm_map_user(page, phys,
                                            VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                                            VOS3_PTE_USER | VOS3_PTE_NO_EXECUTE);
            if (result != 0) {
                VOS3_ERROR("sys_brk: map_user failed for page 0x%llx", (unsigned long long)page);
                vos3_pmm_free(phys);
                return (int64_t)as->brk;
            }

            /* Zero the page */
            void* kaddr = vos3_vmm_get_kernel_addr(page);
            if (kaddr != NULL) {
                memset(kaddr, 0, VOS3_PAGE_SIZE);
            }
            VOS3_DEBUG("sys_brk: page 0x%llx mapped successfully", (unsigned long long)page);
        }
        VOS3_DEBUG("sys_brk: all pages mapped");
    } else {
        VOS3_DEBUG("sys_brk: no new pages needed (old >= new)");
    }

    /* Unmap pages if brk is decreasing */
    if (new_brk_page < old_brk_page) {
        vos3_vmm_unmap_range(new_brk_page, old_brk_page - new_brk_page);
    }

    /* Update the brk */
    as->brk = addr;
    VOS3_DEBUG("sys_brk: new brk=0x%llx", (unsigned long long)as->brk);

    return (int64_t)as->brk;
}

/**
 * @brief sys_nanosleep - High-resolution sleep (Phase 29: Hardened)
 */
static int64_t sys_nanosleep(const void* user_req, void* user_rem)
{
    (void)user_rem;  /* TODO: Fill remaining time on signal interrupt */

    /* Phase 29: Validate request pointer */
    if (user_req == NULL || !access_ok(user_req, sizeof(int64_t) * 2)) {
        return -EFAULT;
    }

    /* Read timespec from user space */
    struct {
        int64_t tv_sec;
        int64_t tv_nsec;
    } ts;

    if (copy_from_user(&ts, user_req, sizeof(ts)) != 0) {
        return -EFAULT;
    }

    /* Validate values */
    if (ts.tv_sec < 0 || ts.tv_nsec < 0 || ts.tv_nsec >= 1000000000LL) {
        return -22;  /* EINVAL */
    }

    /* Convert to milliseconds */
    uint64_t ms = (uint64_t)ts.tv_sec * 1000ULL + (uint64_t)ts.tv_nsec / 1000000ULL;
    if (ms > 0ULL) {
        vos3_task_sleep_ms(ms);
    }

    return 0;
}

/* ============================================================================
 * MMAP / MUNMAP
 * ============================================================================ */

/**
 * @brief sys_mmap - Map anonymous memory into user address space
 *
 * Creates a VMA descriptor but allocates NO physical memory.
 * Pages are allocated on demand via the page fault handler.
 */
static int64_t sys_mmap(uint64_t addr, uint64_t length, int prot,
                         int flags, int fd, uint64_t offset)
{
    (void)offset;

    if (length == 0) {
        return -22;  /* EINVAL */
    }

    /* W^X enforcement: reject simultaneous WRITE+EXEC */
    if ((prot & 0x2) && (prot & 0x4)) {
        return -22;  /* EINVAL: W^X violation */
    }

    /* Validate file-backed mmap parameters */
    if (!(flags & MAP_ANONYMOUS)) {
        if (fd < 0) {
            return -9;  /* EBADF */
        }
    }

    /* Page-align length */
    length = (length + VOS3_PAGE_SIZE - 1) & ~((uint64_t)VOS3_PAGE_SIZE - 1);

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->address_space == NULL) {
        return -12;  /* ENOMEM */
    }

    vos3_address_space_t* as = task->address_space;

    /* Find free VMA slot */
    vos3_vma_t* vma = NULL;
    for (uint32_t i = 0; i < VOS3_MAX_VMAS; i++) {
        if (!as->vmas[i].valid) {
            vma = &as->vmas[i];
            break;
        }
    }
    if (vma == NULL) {
        VOS3_DEBUG("[MMAP] No free VMA slots");
        return -12;  /* ENOMEM */
    }

    /* Determine base address */
    uint64_t base;
    if ((flags & MAP_FIXED) && addr != 0 && (addr & (VOS3_PAGE_SIZE - 1)) == 0) {
        /* MAP_FIXED: use the exact requested address */
        base = addr;
    } else {
        /* Phase v17 (K-C5): Per-process mmap bump allocator.
         * Falls back to global for init task (before task struct is set up). */
        if (task != NULL && task->mmap_next >= VOS3_MMAP_BASE) {
            base = task->mmap_next;
            task->mmap_next += length;
        } else {
            base = g_mmap_next;
            g_mmap_next += length;
        }
    }

    /* Record VMA — NO physical memory allocated yet (demand paging) */
    vma->vm_start = base;
    vma->vm_end = base + length;
    vma->vm_prot = prot;
    vma->vm_flags = flags;
    if (flags & MAP_ANONYMOUS) {
        vma->vm_fd = -1;
    } else {
        int dup_fd = vos3_dup(task->fd_table, fd);
        if (dup_fd < 0) {
            vma->valid = 0;
            return -9;  /* EBADF */
        }
        vma->vm_fd = dup_fd;
    }
    vma->vm_offset = offset;
    vma->valid = 1;
    as->num_vmas++;

    VOS3_DEBUG("[MMAP] Mapped anonymous region 0x%llx-0x%llx (size=%llu)",
               (unsigned long long)base, (unsigned long long)(base + length),
               (unsigned long long)length);

    return (int64_t)base;
}

/**
 * @brief sys_munmap - Unmap memory region
 */
static int64_t sys_munmap(uint64_t addr, uint64_t length)
{
    if (length == 0 || (addr & (VOS3_PAGE_SIZE - 1)) != 0) {
        return -22;  /* EINVAL */
    }

    length = (length + VOS3_PAGE_SIZE - 1) & ~((uint64_t)VOS3_PAGE_SIZE - 1);

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->address_space == NULL) {
        return -22;  /* EINVAL */
    }

    vos3_address_space_t* as = task->address_space;
    uint64_t unmap_end = addr + length;

    /* Find VMA containing the start address */
    for (uint32_t i = 0; i < VOS3_MAX_VMAS; i++) {
        if (!as->vmas[i].valid) continue;
        if (addr < as->vmas[i].vm_start || addr >= as->vmas[i].vm_end) continue;

        vos3_vma_t* vma = &as->vmas[i];
        uint64_t vma_start = vma->vm_start;
        uint64_t vma_end   = vma->vm_end;

        /* Clamp unmap range to VMA bounds */
        if (unmap_end > vma_end) unmap_end = vma_end;

        /* Unmap physical pages in the requested range */
        vos3_vmm_unmap_range((uintptr_t)addr, (size_t)(unmap_end - addr));

        if (addr == vma_start && unmap_end == vma_end) {
            /* Case 1: Full match — remove entire VMA */
            int old_fd = vma->vm_fd;
            vma->valid = 0;
            vma->vm_fd = -1;
            if (as->num_vmas > 0) as->num_vmas--;
            if (old_fd >= 0) {
                vos3_close(old_fd);
            }

        } else if (addr == vma_start) {
            /* Case 2: Front trim — shrink from front */
            uint64_t trimmed = unmap_end - vma_start;
            vma->vm_start = unmap_end;
            vma->vm_offset += trimmed;

        } else if (unmap_end >= vma_end) {
            /* Case 3: Back trim — shrink from end */
            vma->vm_end = addr;

        } else {
            /* Case 4: Middle split — split into two VMAs */
            vos3_vma_t* new_vma = NULL;
            for (uint32_t j = 0; j < VOS3_MAX_VMAS; j++) {
                if (!as->vmas[j].valid) {
                    new_vma = &as->vmas[j];
                    break;
                }
            }
            if (new_vma == NULL) {
                /* No free slot — truncate to [vma_start, addr) as best effort */
                vma->vm_end = addr;
                return -12;  /* ENOMEM */
            }

            /* Create remainder [unmap_end, vma_end) */
            new_vma->vm_start = unmap_end;
            new_vma->vm_end   = vma_end;
            new_vma->vm_prot  = vma->vm_prot;
            new_vma->vm_flags = vma->vm_flags;
            new_vma->vm_offset = vma->vm_offset + (unmap_end - vma_start);
            new_vma->valid    = 1;

            /* Dup fd for the new VMA if file-backed */
            if (vma->vm_fd >= 0 && task->fd_table != NULL) {
                int dup_fd = vos3_dup(task->fd_table, vma->vm_fd);
                new_vma->vm_fd = (dup_fd >= 0) ? dup_fd : -1;
            } else {
                new_vma->vm_fd = -1;
            }

            as->num_vmas++;

            /* Shrink original to [vma_start, addr) */
            vma->vm_end = addr;
        }

        VOS3_DEBUG("[MUNMAP] Unmapped region 0x%llx-0x%llx",
                   (unsigned long long)addr, (unsigned long long)unmap_end);
        return 0;
    }

    return -22;  /* EINVAL: no VMA found */
}

/* ============================================================================
 * SYSCALL DISPATCH WRAPPER
 * ============================================================================ */

/**
 * @brief Process syscall dispatcher
 */
static int64_t exec_syscall_handler(vos3_syscall_frame_t* frame)
{
    uint64_t num = frame->rax;
    uint64_t arg1 = frame->rdi;
    uint64_t arg2 = frame->rsi;
    uint64_t arg3 = frame->rdx;

    switch (num) {
        case SYS_MMAP:
            return sys_mmap(arg1, arg2, (int)arg3,
                           (int)frame->r10, (int)frame->r8, frame->r9);

        case SYS_MPROTECT:
            return sys_mprotect(arg1, arg2, (int)arg3);

        case SYS_MUNMAP:
            return sys_munmap(arg1, arg2);

        case SYS_BRK:
            return sys_brk(arg1);

        case SYS_NANOSLEEP:
            return sys_nanosleep((const void*)arg1, (void*)arg2);

        case SYS_CLONE:
            return sys_clone(frame);

        case SYS_FORK:
            return sys_fork(frame);

        case SYS_ARCH_PRCTL:
            return sys_arch_prctl((int)arg1, arg2);

        case SYS_SET_TID_ADDRESS:
            return sys_set_tid_address(arg1);

        case SYS_SET_ROBUST_LIST:
            return sys_set_robust_list(arg1, (size_t)arg2);

        case SYS_EXECVE:
            return sys_execve((const char*)arg1, (const char* const*)arg2,
                             (const char* const*)arg3);

        case SYS_EXIT:
            return sys_exit((int)arg1);

        case SYS_EXIT_GROUP:
            return sys_exit_group((int)arg1);

        case SYS_WAIT4:
            return sys_wait4((int)arg1, (int*)arg2, (int)arg3);

        case SYS_GETPID:
            return sys_getpid();

        case SYS_GETPPID:
            return sys_getppid();

        case SYS_GETTID:
            return sys_gettid();

        case SYS_GETUID:
            return sys_getuid();

        case SYS_GETGID:
            return sys_getgid();

        case SYS_SETUID:
            return sys_setuid((uint32_t)arg1);

        case SYS_SETGID:
            return sys_setgid((uint32_t)arg1);

        case SYS_SETSID:
            return sys_setsid();

        case SYS_GETPGID:
            return sys_getpgid((int)arg1);

        case SYS_SETPGID:
            return sys_setpgid((int)arg1, (int)arg2);

        default:
            return -38;  /* ENOSYS */
    }
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize process syscalls
 */
void vos3_exec_syscalls_init(void)
{
    VOS3_INFO("Registering process syscalls");

    /* Memory management */
    vos3_syscall_register(SYS_BRK, exec_syscall_handler);
    vos3_syscall_register(SYS_MMAP, exec_syscall_handler);
    vos3_syscall_register(SYS_MPROTECT, exec_syscall_handler);
    vos3_syscall_register(SYS_MUNMAP, exec_syscall_handler);
    vos3_syscall_register(SYS_NANOSLEEP, exec_syscall_handler);

    /* Process control */
    vos3_syscall_register(SYS_CLONE, exec_syscall_handler);
    vos3_syscall_register(SYS_FORK, exec_syscall_handler);
    vos3_syscall_register(SYS_ARCH_PRCTL, exec_syscall_handler);
    vos3_syscall_register(SYS_SET_TID_ADDRESS, exec_syscall_handler);
    vos3_syscall_register(SYS_SET_ROBUST_LIST, exec_syscall_handler);
    vos3_syscall_register(SYS_EXECVE, exec_syscall_handler);
    vos3_syscall_register(SYS_EXIT, exec_syscall_handler);
    vos3_syscall_register(SYS_EXIT_GROUP, exec_syscall_handler);
    vos3_syscall_register(SYS_WAIT4, exec_syscall_handler);
    vos3_syscall_register(SYS_GETPID, exec_syscall_handler);
    vos3_syscall_register(SYS_GETPPID, exec_syscall_handler);
    vos3_syscall_register(SYS_GETTID, exec_syscall_handler);
    vos3_syscall_register(SYS_GETUID, exec_syscall_handler);
    vos3_syscall_register(SYS_GETGID, exec_syscall_handler);
    vos3_syscall_register(SYS_SETUID, exec_syscall_handler);
    vos3_syscall_register(SYS_SETGID, exec_syscall_handler);
    vos3_syscall_register(SYS_SETSID, exec_syscall_handler);
    vos3_syscall_register(SYS_GETPGID, exec_syscall_handler);
    vos3_syscall_register(SYS_SETPGID, exec_syscall_handler);

    VOS3_INFO("Process syscalls registered");
}
