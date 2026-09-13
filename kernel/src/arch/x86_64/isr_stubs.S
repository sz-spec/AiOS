#include "../../../include/vos/entry_state.h"

/**
 * @file isr_stubs.S
 * @brief VOS3 Interrupt Service Routine Assembly Stubs
 *
 * @details Low-level assembly stubs for interrupt entry and exit.
 *          Saves/restores all registers and calls C handler.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

.code64
.section .text

/* ============================================================================
 * MACRO: ISR stub without error code
 * ============================================================================ */
.macro ISR_NOERR num
.global isr_stub_\num
.type isr_stub_\num, @function
isr_stub_\num:
    pushq $0                    /* Push dummy error code */
    pushq $\num                 /* Push interrupt number */
    jmp isr_common_stub
.size isr_stub_\num, . - isr_stub_\num
.endm

/* ============================================================================
 * MACRO: ISR stub with error code (pushed by CPU)
 * ============================================================================ */
.macro ISR_ERR num
.global isr_stub_\num
.type isr_stub_\num, @function
isr_stub_\num:
    pushq $\num                 /* Push interrupt number (error code already pushed) */
    jmp isr_common_stub
.size isr_stub_\num, . - isr_stub_\num
.endm

/* ============================================================================
 * CPU EXCEPTIONS (0-31)
 * ============================================================================ */
ISR_NOERR 0     /* #DE - Divide Error */
ISR_NOERR 1     /* #DB - Debug */
ISR_NOERR 2     /* NMI */
ISR_NOERR 3     /* #BP - Breakpoint */
ISR_NOERR 4     /* #OF - Overflow */
ISR_NOERR 5     /* #BR - Bound Range */
ISR_NOERR 6     /* #UD - Invalid Opcode */
ISR_NOERR 7     /* #NM - Device Not Available */
ISR_ERR   8     /* #DF - Double Fault */
ISR_NOERR 9     /* Coprocessor Segment Overrun */
ISR_ERR   10    /* #TS - Invalid TSS */
ISR_ERR   11    /* #NP - Segment Not Present */
ISR_ERR   12    /* #SS - Stack Fault */
ISR_ERR   13    /* #GP - General Protection */
ISR_ERR   14    /* #PF - Page Fault */
ISR_NOERR 15    /* Reserved */
ISR_NOERR 16    /* #MF - x87 FPU Error */
ISR_ERR   17    /* #AC - Alignment Check */
ISR_NOERR 18    /* #MC - Machine Check */
ISR_NOERR 19    /* #XM - SIMD Exception */
ISR_NOERR 20    /* #VE - Virtualization */
ISR_ERR   21    /* #CP - Control Protection */
ISR_NOERR 22    /* Reserved */
ISR_NOERR 23    /* Reserved */
ISR_NOERR 24    /* Reserved */
ISR_NOERR 25    /* Reserved */
ISR_NOERR 26    /* Reserved */
ISR_NOERR 27    /* Reserved */
ISR_NOERR 28    /* Hypervisor Injection */
ISR_ERR   29    /* VMM Communication */
ISR_ERR   30    /* Security Exception */
ISR_NOERR 31    /* Reserved */

/* ============================================================================
 * HARDWARE IRQS (32-47)
 * ============================================================================ */
ISR_NOERR 32    /* IRQ0 - PIT Timer */
ISR_NOERR 33    /* IRQ1 - Keyboard */
ISR_NOERR 34    /* IRQ2 - Cascade */
ISR_NOERR 35    /* IRQ3 - COM2 */
ISR_NOERR 36    /* IRQ4 - COM1 */
ISR_NOERR 37    /* IRQ5 - LPT2 */
ISR_NOERR 38    /* IRQ6 - Floppy */
ISR_NOERR 39    /* IRQ7 - LPT1 / Spurious */
ISR_NOERR 40    /* IRQ8 - RTC */
ISR_NOERR 41    /* IRQ9 - ACPI */
ISR_NOERR 42    /* IRQ10 */
ISR_NOERR 43    /* IRQ11 */
ISR_NOERR 44    /* IRQ12 - PS/2 Mouse */
ISR_NOERR 45    /* IRQ13 - FPU */
ISR_NOERR 46    /* IRQ14 - ATA Primary */
ISR_NOERR 47    /* IRQ15 - ATA Secondary */

/* ============================================================================
 * SYSTEM CALL (0x80)
 * ============================================================================ */
ISR_NOERR 128   /* Syscall */

/* ============================================================================
 * APIC INTERRUPTS (250-255)
 * ============================================================================ */
ISR_NOERR 250   /* IPI: Halt */
ISR_NOERR 251   /* IPI: TLB Flush */
ISR_NOERR 252   /* IPI: Schedule */
ISR_NOERR 253   /* APIC Timer */
ISR_NOERR 254   /* APIC Error */
ISR_NOERR 255   /* APIC Spurious */

/* ============================================================================
 * COMMON ISR STUB
 * ============================================================================
 * Stack layout at entry:
 *   [rsp+0]   = interrupt number
 *   [rsp+8]   = error code (or 0)
 *   [rsp+16]  = RIP (pushed by CPU)
 *   [rsp+24]  = CS (pushed by CPU)
 *   [rsp+32]  = RFLAGS (pushed by CPU)
 *   [rsp+40]  = RSP (if privilege change)
 *   [rsp+48]  = SS (if privilege change)
 *
 * We need to save all general-purpose registers to build the interrupt frame.
 * ============================================================================ */

.global isr_common_stub
.type isr_common_stub, @function
isr_common_stub:
    /* SWAPGS on entry from user mode (CS RPL=3 means ring-3) — and,
     * per P1.2 IRQ hot-path, CR3 swap inside the same conditional so
     * IRQs taken in kernel mode skip BOTH operations. This avoids
     * recursive state corruption: an IRQ that fires while we are
     * already on kernel CR3 must not swap CR3 again.
     *
     * Stack layout at this point:
     *   [rsp+0]  = interrupt number (pushed by ISR_NOERR / ISR_ERR)
     *   [rsp+8]  = error code (pushed by ISR_ERR, or 0 from ISR_NOERR)
     *   [rsp+16] = RIP (pushed by CPU)
     *   [rsp+24] = CS  (pushed by CPU) — low 2 bits = privilege level
     *   [rsp+32] = RFLAGS
     *
     * Testing byte at offset 24 catches the RPL bits (low 2 of CS).
     * vOS·Adaptive·Phase=P1
     */
    testb   $3, 24(%rsp)
    jz      .Lno_swapgs_entry           /* CS.RPL == 0 → came from kernel → skip */
    swapgs
    lfence
    /* CPU loaded its mapped trampoline through TSS.RSP0. The CPU-local
     * full root is validated on binding; do not silently skip isolation
     * transitions if it is missing. */
    pushq %rax
    movq %gs:VOS3_ENTRY_KERNEL_CR3, %rax
    movq %rax, %cr3
    popq %rax
    /* === end IRQ entry CR3 swap === */
.Lno_swapgs_entry:
    /* Save all general-purpose registers */
    pushq %rax
    pushq %rbx
    pushq %rcx
    pushq %rdx
    pushq %rsi
    pushq %rdi
    pushq %rbp
    pushq %r8
    pushq %r9
    pushq %r10
    pushq %r11
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15

    /* Ensure direction flag is clear */
    cld

    /* User IRQs arrive on the CPU trampoline; move their complete frame
     * onto the task stack before dispatch/preemption can reuse the trampoline. */
    testb $3, 144(%rsp)
    jz .Lframe_on_task_stack
    movq %rsp, %rdi
    andq $-16, %rsp
    call vos3_entry_move_irq_frame
    movq %rax, %rsp
.Lframe_on_task_stack:
    /* Pass pointer to interrupt frame as first argument */
    movq %rsp, %rdi

    /* Align stack to 16 bytes (ABI requirement) */
    movq %rsp, %rbp
    andq $-16, %rsp

    /* Call C handler */
    call vos3_int_dispatch

    cli
    testb $3, 144(%rbp)
    jz .Lkernel_return_prepared
    call vos3_kpti_prepare_user_return
.Lkernel_return_prepared:
    /* Restore stack */
    movq %rbp, %rsp

    /* Restore all general-purpose registers */
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %r11
    popq %r10
    popq %r9
    popq %r8
    popq %rbp
    popq %rdi
    popq %rsi
    popq %rdx
    popq %rcx
    popq %rbx
    popq %rax

    /* Remove interrupt number and error code from stack */
    addq $16, %rsp

    /* SWAPGS on return to user mode (CS RPL=3 in IRETQ frame) — and,
     * per P1.2 IRQ hot-path, CR3 swap inside the same conditional so
     * IRQs that came from (and return to) kernel mode skip both ops.
     *
     * Stack layout at this point (interrupt# + error code already
     * popped above via `addq $16, %rsp`):
     *   [rsp+0]  = RIP   (will pop on iretq)
     *   [rsp+8]  = CS    (privilege bits live here — testw/testb $3)
     *   [rsp+16] = RFLAGS
     *   [rsp+24] = RSP   (if privilege change)
     *   [rsp+32] = SS
     *
     * vOS·Adaptive·Phase=P1
     */
    testb   $3, 8(%rsp)
    jz      .Lno_swapgs_exit            /* CS.RPL == 0 → returning to kernel → skip */
    jmp vos3_kpti_user_iret
.Lno_swapgs_exit:
    /* Return from interrupt */
    iretq

.size isr_common_stub, . - isr_common_stub

/* ============================================================================
 * END OF FILE
 * ============================================================================ */
