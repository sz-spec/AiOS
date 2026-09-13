/**
 * @file syscall_entry.S
 * @brief VOS3 System Call Entry Point
 *
 * @details Assembly entry point for SYSCALL instruction.
 *          Handles the transition from user mode to kernel mode.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../../include/vos/entry_state.h"

.section .text
.code64

/* Segment selectors (must match GDT) */
.set USER_CS, 0x1B      /* User code segment (ring 3): 0x18 | 3 */
.set USER_DS, 0x23      /* User data segment (ring 3): 0x20 | 3 */

/* ============================================================================
 * SYSCALL ENTRY
 * ============================================================================
 *
 * When SYSCALL is executed:
 *   - RCX = user RIP (return address)
 *   - R11 = user RFLAGS
 *   - RIP = value from LSTAR MSR (this function)
 *   - CS  = value from STAR MSR [47:32]
 *   - SS  = value from STAR MSR [47:32] + 8
 *   - RFLAGS = RFLAGS & ~SFMASK
 *
 * System call arguments (Linux/SysV convention):
 *   RAX = syscall number
 *   RDI = arg1
 *   RSI = arg2
 *   RDX = arg3
 *   R10 = arg4 (not RCX, since SYSCALL clobbers it)
 *   R8  = arg5
 *   R9  = arg6
 *
 * Return:
 *   RAX = return value
 */

.global vos3_syscall_entry
.type vos3_syscall_entry, @function
vos3_syscall_entry:
    /* SYSCALL always arrives from ring-3; swap to kernel GS */
    swapgs
    lfence

    /*
     * At entry:
     *   - We're in kernel mode (ring 0)
     *   - Still using user stack (RSP)
     *   - RCX = user RIP
     *   - R11 = user RFLAGS
     *   - RAX = syscall number
     *   - Arguments in RDI, RSI, RDX, R10, R8, R9
     */

    /* No task-stack access until the process's full root is active. */
    movq %rsp, %gs:VOS3_ENTRY_USER_RSP
    movq %rax, %gs:VOS3_ENTRY_SCRATCH_RAX
    movq %gs:VOS3_ENTRY_KERNEL_CR3, %rax
    movq %rax, %cr3
    movq %gs:VOS3_ENTRY_SCRATCH_RAX, %rax
    movq %gs:VOS3_ENTRY_KERNEL_RSP, %rsp
    andq $-16, %rsp

    /*
     * Push user RSP onto kernel stack FIRST (will pop last on return).
     * CPU-local scratch cannot be overwritten by another CPU. Save it
     * to the task stack before enabling interrupts, so it also survives
     * another task's syscall while this task is blocked.
     */
    pushq %gs:VOS3_ENTRY_USER_RSP

    /* Save user RIP and RFLAGS (from RCX and R11) */
    pushq %r11                  /* User RFLAGS */
    pushq %rcx                  /* User RIP */

    /* Push syscall arguments in order for frame structure */
    pushq %rax                  /* Syscall number */
    pushq %rdi                  /* Arg 1 */
    pushq %rsi                  /* Arg 2 */
    pushq %rdx                  /* Arg 3 */
    pushq %r10                  /* Arg 4 */
    pushq %r8                   /* Arg 5 */
    pushq %r9                   /* Arg 6 */

    /* Push callee-saved registers */
    pushq %rbx
    pushq %rbp
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15

    /* Pass frame pointer to C handler */
    movq %rsp, %rdi

    /* Enable interrupts during syscall handling */
    sti

    /* Call the C dispatcher */
    call vos3_syscall_dispatch

    /* Disable interrupts for return sequence */
    cli

    /*
     * Stash return value into the frame's saved-rax slot (96(%rsp)).
     * Stack layout at this point (bottom=rsp):
     *   0: r15, 8: r14, 16: r13, 24: r12, 32: rbp, 40: rbx,
     *  48: r9,  56: r8,  64: r10, 72: rdx, 80: rsi, 88: rdi,
     *  96: rax (syscall nr — repurposed as retval store),
     * 104: rcx(userRIP), 112: r11(userRFLAGS), 120: userRSP
     *
     * No global needed; value travels on kernel stack through pops.
     */
    movq %rax, 96(%rsp)
    call vos3_kpti_prepare_user_return

    /* Restore ALL callee-saved registers properly */
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbp
    popq %rbx

    /*
     * Restore syscall argument registers.  rax slot now holds retval —
     * pop it directly into %rax instead of skipping it.
     */
    popq %r9
    popq %r8
    popq %r10
    popq %rdx
    popq %rsi
    popq %rdi
    popq %rax                   /* Load return value from frame's rax slot */

    /* Pop user RIP and RFLAGS */
    popq %rcx                   /* User RIP (for IRETQ) */
    popq %r11                   /* User RFLAGS (for IRETQ) */

    /*
     * Stack now: [rsp+0] = userRSP.
     * Swap %rax (retval) with [rsp] (userRSP) so:
     *   %rax  = userRSP (temp, needed for IRETQ frame)
     *   [rsp] = retval  (parked below the frame we are about to push)
     */
    xchgq   %rax, (%rsp)

    /*
     * Build IRETQ frame (5 pushes = 40 bytes):
     *   [RSP+32] SS
     *   [RSP+24] RSP (user stack)
     *   [RSP+16] RFLAGS
     *   [RSP+8]  CS
     *   [RSP+0]  RIP
     * Retval is at [RSP+40] — one slot below the frame.
     */
    pushq $USER_DS              /* SS */
    pushq %rax                  /* User RSP */
    pushq %r11                  /* User RFLAGS */
    pushq $USER_CS              /* CS */
    pushq %rcx                  /* User RIP */

    /* Recover return value from below the IRETQ frame */
    movq    40(%rsp), %rax

    jmp vos3_kpti_user_iret

.size vos3_syscall_entry, . - vos3_syscall_entry


/* ============================================================================
 * DATA SECTION
 * ============================================================================ */

.section .data


/* ============================================================================
 * SYSCALL STACK
 * ============================================================================ */

.section .bss

/* Allocate a dedicated syscall stack (16 KB) */
.align 16
.global g_syscall_stack
g_syscall_stack:
    .skip 16384
.global g_syscall_stack_top
g_syscall_stack_top:
