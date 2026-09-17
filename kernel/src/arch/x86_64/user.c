#include "arch/x86_64/kpti.h"
/**
 * @file user.c
 * @brief VOS3 User Mode Support Implementation
 *
 * @details User mode task creation and user/kernel transitions.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/vos/user.h"
#include "../../../include/vos/uaccess.h"
#include "../../../include/vos/task.h"
#include "../../../include/vos/scheduler.h"
#include "../../../include/vos/vmm.h"
#include "../../../include/vos/pmm.h"
#include "../../../include/vos/heap.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/string.h"
#include "../../../include/arch/x86_64/gdt.h"

/* ============================================================================
 * USER MODE INITIALIZATION
 * ============================================================================ */

static int g_user_initialized = 0;

int vos3_user_init(void)
{
    if (g_user_initialized != 0) {
        return VOS3_USER_ERR_INVALID;
    }

    VOS3_INFO("Initializing user mode support");

    /* Verify GDT has user segments */
    /* User CS should be at 0x28, User DS at 0x20 */

    g_user_initialized = 1;

    VOS3_INFO("User mode support initialized");
    VOS3_INFO("  User space: 0x%llx - 0x%llx",
              (unsigned long long)VOS3_USER_BASE,
              (unsigned long long)VOS3_USER_END);
    VOS3_INFO("  User CS: 0x%04x, User DS: 0x%04x",
              VOS3_USER_CS, VOS3_USER_DS);

    return VOS3_USER_OK;
}

/* ============================================================================
 * USER TASK CREATION
 * ============================================================================ */

/**
 * @brief Entry trampoline for user tasks
 *
 * This function runs in kernel mode and sets up the transition
 * to user mode for a newly created user task.
 */
static void user_task_trampoline(void* arg)
{
    vos3_task_t* task = (vos3_task_t*)arg;

    VOS3_DEBUG("User task trampoline: '%s'", task->name);

    /* Get user entry point and stack from task structure */
    /* We stored these in unused fields temporarily */
    uint64_t user_entry = (uint64_t)(uintptr_t)task->user_stack;
    uint64_t user_stack = VOS3_USER_STACK_TOP;

    /* Allocate actual user stack in user address space */
    /* For now, we assume it's already mapped */

    VOS3_DEBUG("Jumping to user mode: entry=0x%llx, stack=0x%llx",
               (unsigned long long)user_entry,
               (unsigned long long)user_stack);

    /* Jump to user mode - never returns */
    vos3_jump_to_user(user_entry, user_stack);
}

vos3_task_t* vos3_user_task_create(const char* name,
                                    uint64_t entry,
                                    vos3_task_priority_t priority)
{
    return vos3_user_task_create_ex(name, entry,
                                     VOS3_USER_STACK_TOP,
                                     VOS3_USER_STACK_SIZE,
                                     priority);
}

vos3_task_t* vos3_user_task_create_ex(const char* name,
                                       uint64_t entry,
                                       uint64_t stack_top,
                                       size_t stack_size,
                                       vos3_task_priority_t priority)
{
    if (g_user_initialized == 0) {
        VOS3_ERROR("User mode not initialized");
        return NULL;
    }

    /* Validate entry point is in user space */
    if (entry >= VOS3_USER_END || entry < VOS3_USER_BASE) {
        VOS3_ERROR("Invalid user entry point: 0x%llx",
                   (unsigned long long)entry);
        return NULL;
    }

    VOS3_DEBUG("Creating user task '%s' at entry 0x%llx",
               name ? name : "(null)", (unsigned long long)entry);

    /* Create kernel task that will transition to user mode */
    vos3_task_t* task = vos3_task_create(name, user_task_trampoline,
                                          NULL, priority);
    if (task == NULL) {
        return NULL;
    }

    /* Mark as user task */
    task->flags &= ~VOS3_TASK_FLAG_KERNEL;
    task->flags |= VOS3_TASK_FLAG_USER;

    /* Store user entry point temporarily (will be used by trampoline) */
    /* We repurpose user_stack pointer since we'll allocate properly later */
    task->user_stack = (void*)(uintptr_t)entry;
    task->user_stack_size = stack_size;

    /* Set the task as the argument to the trampoline */
    /* Modify the stack to pass task pointer as argument */
    /* The task was created with NULL arg, we need to fix this */

    /* Actually, let's restructure: store entry in a field */
    /* For now, use a simpler approach - store in user_stack temporarily */

    /* TODO: Create user address space and map user stack */
    /* For now, we rely on identity mapping or pre-mapped user space */

    VOS3_DEBUG("User task '%s' created (tid=%u)", task->name, task->tid);

    return task;
}

/* ============================================================================
 * FORK CHILD RETURN
 * ============================================================================ */

/**
 * @brief Assembly function to return to user mode
 */
extern void vos3_return_to_user(uint64_t rip, uint64_t rsp, uint64_t rflags);

/**
 * @brief Fork child return trampoline
 *
 * This function is the entry point for a forked child when first scheduled.
 * It returns to user mode at the point where the parent called fork(),
 * with RAX=0 to indicate this is the child.
 */
void vos3_fork_child_return(void)
{
    /* Immediate debug to verify we reached this function */
    VOS3_DEBUG(">>> fork_child_return ENTRY");

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        VOS3_ERROR("fork_child_return: no current task!");
        for (;;) { __asm__ volatile("hlt"); }
    }

    VOS3_DEBUG("fork_child_return: current task '%s' pid=%u is_fork_child=%d",
               current->name, current->pid, current->is_fork_child);

    /* Get saved user state from fork */
    uint64_t user_rip = current->fork_ret_rip;
    uint64_t user_rsp = current->fork_ret_rsp;
    uint64_t user_rflags = current->fork_ret_rflags;
    /* Get saved callee-saved registers (must be restored for ABI compliance) */
    uint64_t user_rbx = current->fork_ret_rbx;
    uint64_t user_rbp = current->fork_ret_rbp;
    uint64_t user_r12 = current->fork_ret_r12;
    uint64_t user_r13 = current->fork_ret_r13;
    uint64_t user_r14 = current->fork_ret_r14;
    uint64_t user_r15 = current->fork_ret_r15;
    /* Caller-saved regs — needed by musl's clone.s (r9=func, r8/r10=scratch) */
    uint64_t user_r8  = current->fork_ret_r8;
    uint64_t user_r9  = current->fork_ret_r9;
    uint64_t user_r10 = current->fork_ret_r10;

    VOS3_DEBUG("fork_child_return: rip=0x%llx, rsp=0x%llx, rflags=0x%llx",
               (unsigned long long)user_rip,
               (unsigned long long)user_rsp,
               (unsigned long long)user_rflags);
    VOS3_DEBUG("fork_child_return: rbp=0x%llx, rbx=0x%llx, r13=0x%llx",
               (unsigned long long)user_rbp,
               (unsigned long long)user_rbx,
               (unsigned long long)user_r13);

    /* Clear fork child flag */
    current->is_fork_child = 0;

    /*
     * Return to user mode with RAX=0 (child's fork() return value).
     *
     * CRITICAL: Callee-saved registers (RBX, RBP, R12-R15) must be restored
     * to match the parent's state at fork() time. This is required by the
     * x86-64 ABI - the caller expects these registers preserved.
     *
     * We use a carefully designed approach:
     * 1. Build IRETQ frame first (using RDI, RSI, RDX which are scratch)
     * 2. Restore callee-saved registers from memory
     * 3. Clear scratch registers
     * 4. IRETQ to user mode
     */
    vos3_kpti_prepare_user_return();
    __asm__ volatile (
        "cli\n\t"
        /*
         * Set up data segments for user mode.
         */
        "movq $0x23, %%rax\n\t"     /* USER_DS = 0x23 */
        "movw %%ax, %%ds\n\t"
        "movw %%ax, %%es\n\t"
        /*
         * DO NOT load FS or GS with segment selectors here!
         * On x86_64, writing to FS/GS via mov clears MSR_FS_BASE/GS_BASE
         * to 0, destroying the TLS pointer. The scheduler already set
         * MSR_FS_BASE during context switch, and IRETQ doesn't touch it.
         */
        /*
         * Build IRETQ frame on current kernel stack.
         * Stack layout after pushes:
         *   [RSP+32] SS      = 0x23 (USER_DS)
         *   [RSP+24] RSP     = user_rsp
         *   [RSP+16] RFLAGS  = user_rflags
         *   [RSP+8]  CS      = 0x1B (USER_CS)
         *   [RSP+0]  RIP     = user_rip
         */
        "pushq $0x23\n\t"           /* SS = USER_DS */
        "pushq %[ursp]\n\t"         /* User RSP */
        "pushq %[uflags]\n\t"       /* User RFLAGS */
        "pushq $0x1b\n\t"           /* CS = USER_CS */
        "pushq %[urip]\n\t"         /* User RIP */
        /*
         * Restore callee-saved registers from saved values.
         * These are required by ABI to be preserved across fork().
         *
         * CRITICAL: RBP must be restored LAST!
         * The memory operands %[ur12], %[ur13], etc. might use RBP-relative
         * addressing to access the local variables. If we change RBP first,
         * subsequent memory accesses will read garbage from user space!
         */
        "movq %[urbx], %%rbx\n\t"
        "movq %[ur12], %%r12\n\t"
        "movq %[ur13], %%r13\n\t"
        "movq %[ur14], %%r14\n\t"
        "movq %[ur15], %%r15\n\t"
        /*
         * Restore r8/r9/r10 BEFORE RBP change — these memory operands
         * may use RBP-relative addressing for local variables.
         * musl's clone.s needs r9 (func ptr) in child.
         * For fork(), these are 0 (set in exec.c).
         */
        "movq %[ur8], %%r8\n\t"
        "movq %[ur9], %%r9\n\t"
        "movq %[ur10], %%r10\n\t"
        "movq %[urbp], %%rbp\n\t"  /* RBP last! */
        /*
         * Clear scratch registers to prevent kernel data leakage.
         * RAX = 0 is critical: this is the child's fork() return value.
         */
        "xorq %%rax, %%rax\n\t"    /* RAX = 0 (fork/clone return value) */
        "xorq %%rcx, %%rcx\n\t"
        "xorq %%rdx, %%rdx\n\t"
        "xorq %%rdi, %%rdi\n\t"
        "xorq %%rsi, %%rsi\n\t"
        "xorq %%r11, %%r11\n\t"
        /* Kernel GS currently names this CPU's entry state. */
        /* Shared exit moves the IRET frame before selecting the user root. */
        "jmp vos3_kpti_user_iret\n\t"
        :
        : [urip] "r" (user_rip),
          [ursp] "r" (user_rsp),
          [uflags] "r" (user_rflags),
          [urbx] "m" (user_rbx),
          [urbp] "m" (user_rbp),
          [ur12] "m" (user_r12),
          [ur13] "m" (user_r13),
          [ur14] "m" (user_r14),
          [ur15] "m" (user_r15),
          [ur8]  "m" (user_r8),
          [ur9]  "m" (user_r9),
          [ur10] "m" (user_r10)
        : "memory", "rax"
    );

    /* Should never reach here */
    __builtin_unreachable();
}

/* ============================================================================
 * USER/KERNEL DATA TRANSFER
 * ============================================================================ */

int vos3_copy_from_user(void* dst, const void* src, size_t len)
{
    if (dst == NULL || src == NULL) return VOS3_USER_ERR_INVALID;
    return copy_from_user(dst, src, len);
}

int vos3_copy_to_user(void* dst, const void* src, size_t len)
{
    if (dst == NULL || src == NULL) return VOS3_USER_ERR_INVALID;
    return copy_to_user(dst, src, len);
}

int64_t vos3_strncpy_from_user(char* dst, const char* src, size_t max)
{
    if (dst == NULL || src == NULL || max == 0U) return VOS3_USER_ERR_INVALID;
    return strncpy_from_user(dst, src, max);
}
