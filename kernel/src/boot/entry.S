/**
 * @file entry.S
 * @brief VOS3 Kernel Entry Point
 *
 * @details Assembly entry point for the kernel. Called by bootloader
 *          after setting up long mode and paging. Sets up initial
 *          stack and calls kernel_main().
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

.code64
.section .text.entry, "ax"

/* ============================================================================
 * EXTERNAL SYMBOLS
 * ============================================================================ */

.extern kernel_main
.extern _boot_stack_top
.extern _bss_start
.extern _bss_end

/* ============================================================================
 * KERNEL ENTRY POINT
 * ============================================================================ */

.global _start
.type _start, @function
_start:
    /*
     * At this point:
     * - We're in 64-bit long mode
     * - Paging is enabled with higher-half mapping
     * - RDI contains pointer to boot_info structure
     * - Interrupts are disabled
     */

    /* Disable interrupts (should already be disabled) */
    cli

    /* Clear direction flag */
    cld

    /* Save boot_info pointer */
    movq %rdi, %r12

    /* Set up kernel stack */
    leaq _boot_stack_top(%rip), %rsp

    /* Align stack to 16 bytes */
    andq $-16, %rsp

    /* Clear frame pointer for stack traces */
    xorq %rbp, %rbp

    /* Clear BSS section */
    leaq _bss_start(%rip), %rdi
    leaq _bss_end(%rip), %rcx
    subq %rdi, %rcx
    shrq $3, %rcx           /* Divide by 8 (qwords) */
    xorq %rax, %rax
    rep stosq

    /* Restore boot_info pointer as first argument */
    movq %r12, %rdi

    /* Call kernel_main(boot_info) */
    call kernel_main

    /* If kernel_main returns, halt */
.global _halt
_halt:
    cli
1:
    hlt
    jmp 1b

.size _start, . - _start

/* ============================================================================
 * AP (APPLICATION PROCESSOR) ENTRY POINT
 * ============================================================================ */

.global _ap_start
.type _ap_start, @function
_ap_start:
    /*
     * AP startup trampoline target
     * RDI = CPU ID
     * RSI = Stack pointer for this CPU
     */

    /* Disable interrupts */
    cli

    /* Set up this CPU's stack */
    movq %rsi, %rsp
    andq $-16, %rsp

    /* Clear frame pointer */
    xorq %rbp, %rbp

    /* Call ap_main(cpu_id) */
    call ap_main

    /* Should not return, but halt if it does */
    jmp _halt

.size _ap_start, . - _ap_start

/* ============================================================================
 * STACK VALIDATION
 * ============================================================================ */

.global _check_stack
.type _check_stack, @function
_check_stack:
    /*
     * Check if stack is valid
     * Returns: 1 if valid, 0 if corrupted
     */
    leaq _boot_stack_bottom(%rip), %rax
    cmpq %rax, %rsp
    jb .stack_invalid           /* RSP below stack bottom */

    leaq _boot_stack_top(%rip), %rax
    cmpq %rax, %rsp
    ja .stack_invalid           /* RSP above stack top */

    movq $1, %rax
    ret

.stack_invalid:
    xorq %rax, %rax
    ret

.size _check_stack, . - _check_stack

/* ============================================================================
 * CPU CONTROL
 * ============================================================================ */

.global vos3_cpu_halt
.type vos3_cpu_halt, @function
vos3_cpu_halt:
    cli
    hlt
    ret
.size vos3_cpu_halt, . - vos3_cpu_halt

.global vos3_cpu_pause
.type vos3_cpu_pause, @function
vos3_cpu_pause:
    pause
    ret
.size vos3_cpu_pause, . - vos3_cpu_pause

/* ============================================================================
 * CONTROL REGISTER ACCESS
 * ============================================================================ */

.global vos3_read_cr0
.type vos3_read_cr0, @function
vos3_read_cr0:
    movq %cr0, %rax
    ret
.size vos3_read_cr0, . - vos3_read_cr0

.global vos3_write_cr0
.type vos3_write_cr0, @function
vos3_write_cr0:
    movq %rdi, %cr0
    ret
.size vos3_write_cr0, . - vos3_write_cr0

.global vos3_read_cr2
.type vos3_read_cr2, @function
vos3_read_cr2:
    movq %cr2, %rax
    ret
.size vos3_read_cr2, . - vos3_read_cr2

.global vos3_read_cr3
.type vos3_read_cr3, @function
vos3_read_cr3:
    movq %cr3, %rax
    ret
.size vos3_read_cr3, . - vos3_read_cr3

.global vos3_write_cr3
.type vos3_write_cr3, @function
vos3_write_cr3:
    movq %rdi, %cr3
    ret
.size vos3_write_cr3, . - vos3_write_cr3

.global vos3_read_cr4
.type vos3_read_cr4, @function
vos3_read_cr4:
    movq %cr4, %rax
    ret
.size vos3_read_cr4, . - vos3_read_cr4

.global vos3_write_cr4
.type vos3_write_cr4, @function
vos3_write_cr4:
    movq %rdi, %cr4
    ret
.size vos3_write_cr4, . - vos3_write_cr4

/* ============================================================================
 * MSR ACCESS
 * ============================================================================ */

.global vos3_read_msr
.type vos3_read_msr, @function
vos3_read_msr:
    movl %edi, %ecx
    rdmsr
    shlq $32, %rdx
    orq %rdx, %rax
    ret
.size vos3_read_msr, . - vos3_read_msr

.global vos3_write_msr
.type vos3_write_msr, @function
vos3_write_msr:
    movl %edi, %ecx
    movl %esi, %eax
    movq %rsi, %rdx
    shrq $32, %rdx
    wrmsr
    ret
.size vos3_write_msr, . - vos3_write_msr

/* ============================================================================
 * CPUID
 * ============================================================================ */

.global vos3_cpuid
.type vos3_cpuid, @function
vos3_cpuid:
    /*
     * vos3_cpuid(uint32_t leaf, uint32_t subleaf, uint32_t* eax, uint32_t* ebx, uint32_t* ecx, uint32_t* edx)
     * Arguments: RDI=leaf, RSI=subleaf, RDX=eax*, RCX=ebx*, R8=ecx*, R9=edx*
     */
    pushq %rbx

    /* Save output pointers */
    movq %rdx, %r10
    movq %rcx, %r11

    /* Set up CPUID inputs */
    movl %edi, %eax
    movl %esi, %ecx

    /* Execute CPUID */
    cpuid

    /* Store results */
    movl %eax, (%r10)
    movl %ebx, (%r11)
    movl %ecx, (%r8)
    movl %edx, (%r9)

    popq %rbx
    ret
.size vos3_cpuid, . - vos3_cpuid

/* ============================================================================
 * END OF FILE
 * ============================================================================ */
