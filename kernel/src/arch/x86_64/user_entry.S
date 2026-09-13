#include "../../../include/vos/entry_state.h"

/**
 * @file user_entry.S
 * @brief VOS3 User Mode Entry Assembly
 *
 * @details Assembly routines for transitioning to user mode.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

.section .text
.code64

/* Segment selectors (must match GDT) */
.set USER_CS, 0x1B      /* User code segment (ring 3): 0x18 | 3 */
.set USER_DS, 0x23      /* User data segment (ring 3): 0x20 | 3 */

/* User RFLAGS: IF=1 (interrupts enabled), reserved bit 1 set */
.set USER_RFLAGS, 0x202

/* ============================================================================
 * JUMP TO USER MODE
 * ============================================================================
 *
 * void vos3_jump_to_user(uint64_t entry, uint64_t user_stack);
 *
 * Parameters:
 *   RDI = user entry point (RIP)
 *   RSI = user stack pointer (RSP)
 *
 * This function sets up the stack for IRETQ and transitions to user mode.
 * It never returns.
 *
 * IRETQ expects on stack (in order of push, i.e., SS first on stack):
 *   SS      (at RSP+32)
 *   RSP     (at RSP+24)  - user stack
 *   RFLAGS  (at RSP+16)
 *   CS      (at RSP+8)
 *   RIP     (at RSP+0)   - user entry
 */
.global vos3_jump_to_user
.type vos3_jump_to_user, @function
vos3_jump_to_user:
    cli
    pushq %rdi
    pushq %rsi
    pushq %rdx
    call vos3_kpti_prepare_user_return
    popq %rdx
    popq %rsi
    popq %rdi

    /* Disable interrupts during setup */
    cli

    /* Clear general purpose registers (security - don't leak kernel data) */
    xorq %rax, %rax
    xorq %rbx, %rbx
    xorq %rcx, %rcx
    xorq %rdx, %rdx
    /* Don't clear RDI and RSI yet - we need them */
    xorq %rbp, %rbp
    xorq %r8, %r8
    xorq %r9, %r9
    xorq %r10, %r10
    xorq %r11, %r11
    xorq %r12, %r12
    xorq %r13, %r13
    xorq %r14, %r14
    xorq %r15, %r15

    /* Load user data segment into segment registers */
    movw $USER_DS, %ax
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %fs
    /* Note: GS.Base is managed via SWAPGS / WRMSR, not the segment selector */
    /* Note: SS will be loaded by IRETQ */

    /* Build IRETQ frame on kernel stack */
    /* Push SS */
    pushq $USER_DS

    /* Push user RSP */
    pushq %rsi

    /* Push RFLAGS (interrupts enabled) */
    pushq $USER_RFLAGS

    /* Push CS */
    pushq $USER_CS

    /* Push user RIP (entry point) */
    pushq %rdi

    /* Clear remaining registers now */
    xorq %rdi, %rdi
    xorq %rsi, %rsi

    /* Swap to user GS.Base before entering ring-3 */
    jmp vos3_kpti_user_iret

.size vos3_jump_to_user, . - vos3_jump_to_user


/* ============================================================================
 * RETURN FROM SYSCALL TO USER (alternative to SYSRET)
 * ============================================================================
 *
 * void vos3_return_to_user(uint64_t rip, uint64_t rsp, uint64_t rflags);
 *
 * This is an alternative to SYSRET using IRETQ.
 * Useful when SYSRET cannot be used (e.g., when returning to non-canonical RIP).
 */
.global vos3_return_to_user
.type vos3_return_to_user, @function
vos3_return_to_user:
    cli
    pushq %rdi
    pushq %rsi
    pushq %rdx
    call vos3_kpti_prepare_user_return
    popq %rdx
    popq %rsi
    popq %rdi

    /* Parameters:
     *   RDI = RIP
     *   RSI = RSP
     *   RDX = RFLAGS
     */

    cli

    /* Set up data segments */
    movw $USER_DS, %ax
    movw %ax, %ds
    movw %ax, %es

    /* Build IRETQ frame */
    pushq $USER_DS      /* SS */
    pushq %rsi          /* RSP */
    pushq %rdx          /* RFLAGS */
    pushq $USER_CS      /* CS */
    pushq %rdi          /* RIP */

    /* Clear registers */
    xorq %rax, %rax
    xorq %rbx, %rbx
    xorq %rcx, %rcx
    xorq %rdx, %rdx
    xorq %rdi, %rdi
    xorq %rsi, %rsi
    xorq %rbp, %rbp
    xorq %r8, %r8
    xorq %r9, %r9
    xorq %r10, %r10
    xorq %r11, %r11
    xorq %r12, %r12
    xorq %r13, %r13
    xorq %r14, %r14
    xorq %r15, %r15

    /* Swap to user GS.Base before entering ring-3 */
    jmp vos3_kpti_user_iret

.size vos3_return_to_user, . - vos3_return_to_user


/* ============================================================================
 * TEST USER CODE
 * ============================================================================
 *
 * Simple test function that can run in user mode.
 * It makes a syscall to print and then exits.
 */
.global vos3_test_user_code
.type vos3_test_user_code, @function
vos3_test_user_code:
    /* sys_putchar('U') */
    movq $50, %rax      /* SYS_PUTCHAR */
    movq $'U', %rdi
    syscall

    /* sys_putchar('S') */
    movq $50, %rax
    movq $'S', %rdi
    syscall

    /* sys_putchar('R') */
    movq $50, %rax
    movq $'R', %rdi
    syscall

    /* sys_putchar('!') */
    movq $50, %rax
    movq $'!', %rdi
    syscall

    /* sys_putchar('\n') */
    movq $50, %rax
    movq $'\n', %rdi
    syscall

    /* sys_exit(0) */
    movq $0, %rax       /* SYS_EXIT */
    xorq %rdi, %rdi     /* exit code 0 */
    syscall

    /* Should not reach here */
    ud2

.size vos3_test_user_code, . - vos3_test_user_code

/* Shared exit: only CPU-local mapped storage is touched after changing CR3.
 * Input RSP points at the complete five-word IRET frame. All GPRs survive. */
.section .text
.global vos3_kpti_user_iret
.type vos3_kpti_user_iret, @function
vos3_kpti_user_iret:
    cli
    movq %rax, %gs:VOS3_ENTRY_SCRATCH_RAX
    movq %rdi, %gs:VOS3_ENTRY_SCRATCH_RDI
    movq %gs:VOS3_ENTRY_TRAMPOLINE_TOP, %rdi
    subq $64, %rdi
    movq 0(%rsp), %rax
    movq %rax, 0(%rdi)
    movq 8(%rsp), %rax
    movq %rax, 8(%rdi)
    movq 16(%rsp), %rax
    movq %rax, 16(%rdi)
    movq 24(%rsp), %rax
    movq %rax, 24(%rdi)
    movq 32(%rsp), %rax
    movq %rax, 32(%rdi)
    movq %gs:VOS3_ENTRY_USER_CR3, %rax
    movq %rdi, %rsp
    movq %rax, %cr3
    movq %gs:VOS3_ENTRY_SCRATCH_RDI, %rdi
    movq %gs:VOS3_ENTRY_SCRATCH_RAX, %rax
    swapgs
    iretq
.size vos3_kpti_user_iret, . - vos3_kpti_user_iret
