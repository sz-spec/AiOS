/**
 * @file trampoline.S
 * @brief VOS3 AP (Application Processor) Startup Trampoline
 *
 * @details This code is copied to low memory (0x1000) and executed by APs
 *          when they receive the STARTUP IPI. It transitions the AP from
 *          16-bit Real Mode through Protected Mode to 64-bit Long Mode,
 *          then jumps to the C entry point.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * Memory Layout:
 *   0x1000 - 0x1EFF : Trampoline code
 *   0x1F00 - 0x1FFF : Boot parameters (vos3_smp_boot_params_t)
 *
 * Boot Parameters at 0x1F00:
 *   +0x00 (8 bytes): PML4 physical address (CR3)
 *   +0x08 (8 bytes): Stack pointer
 *   +0x10 (8 bytes): C entry point address
 *   +0x18 (4 bytes): CPU ID
 *   +0x1C (4 bytes): APIC ID
 *   +0x20 (4 bytes): Ready flag (AP sets to 1)
 */

.section .rodata

.set AP_BASIC_REQUIRED, ((1 << 0) | (1 << 4) | (1 << 5) | (1 << 6) | \
                         (1 << 13) | (1 << 16) | (1 << 24) | \
                         (1 << 25) | (1 << 26))
.set AP_EXT_REQUIRED,   ((1 << 11) | (1 << 20) | (1 << 29))

/* ============================================================================
 * 16-BIT REAL MODE CODE
 * ============================================================================ */

.code16
.global vos3_trampoline_start
.global vos3_trampoline_end

vos3_trampoline_start:
    /*
     * Entry point - AP starts here in 16-bit Real Mode
     * CS:IP = 0x0100:0x0000 (linear 0x1000) after SIPI with vector 0x01
     */
    cli                             /* Disable interrupts */
    cld                             /* Clear direction flag */

    /* Set up segments for real mode - AT&T syntax */
    xorw %ax, %ax
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %ss
    movw $0x7000, %sp               /* Temporary stack in low memory */

    /* Load the temporary GDT (still in real mode addressing) */
    lgdtl (trampoline_gdt_desc - vos3_trampoline_start + 0x1000)

    /* Enable Protected Mode (CR0.PE = 1) */
    movl %cr0, %eax
    orl $1, %eax
    movl %eax, %cr0

    /* Far jump to flush prefetch queue and enter 32-bit Protected Mode */
    ljmpl $0x08, $(trampoline_32bit - vos3_trampoline_start + 0x1000)

/* ============================================================================
 * 32-BIT PROTECTED MODE CODE
 * ============================================================================ */

.code32
trampoline_32bit:
    /* Set up 32-bit data segments */
    movw $0x10, %ax                 /* Selector 0x10 = 32-bit data */
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %fs
    movw %ax, %gs
    movw %ax, %ss

    /* Do not infer an AP's capabilities from the BSP.  Heterogeneous or
     * filtered virtual CPUs must satisfy the same bootstrap contract before
     * CR4 or EFER is changed. */
    xorl %ecx, %ecx
    movl $0, %eax
    cpuid
    cmpl $1, %eax
    jb trampoline_feature_halt
    movl $1, %eax
    cpuid
    movl %edx, %eax
    andl $AP_BASIC_REQUIRED, %eax
    cmpl $AP_BASIC_REQUIRED, %eax
    jne trampoline_feature_halt
    movl $0x80000000, %eax
    cpuid
    cmpl $0x80000001, %eax
    jb trampoline_feature_halt
    movl $0x80000001, %eax
    cpuid
    movl %edx, %eax
    andl $AP_EXT_REQUIRED, %eax
    cmpl $AP_EXT_REQUIRED, %eax
    jne trampoline_feature_halt

    /* Enable PAE (CR4.PAE = 1) and PGE (CR4.PGE = 1) */
    movl %cr4, %eax
    orl $0x000000A0, %eax           /* PAE (bit 5) | PGE (bit 7) */
    movl %eax, %cr4

    /* Load PML4 address from boot parameters into CR3 */
    movl 0x1F00, %eax               /* Low 32 bits of PML4 address */
    movl %eax, %cr3

    /* Enable Long Mode via EFER MSR */
    movl $0xC0000080, %ecx          /* EFER MSR */
    rdmsr
    /* Match BSP paging: inherited kernel entries contain NX bits. */
    orl $0x00000900, %eax           /* LME (bit 8) | NXE (bit 11) */
    wrmsr

    /* Enable Paging (CR0.PG = 1) - this activates Long Mode */
    movl %cr0, %eax
    orl $0x80000000, %eax           /* PG (bit 31) */
    movl %eax, %cr0

    /* Far jump to 64-bit code segment */
    ljmpl $0x18, $(trampoline_64bit - vos3_trampoline_start + 0x1000)

trampoline_feature_halt:
    movw $0x3F8, %dx
    movb $'A', %al
    outb %al, %dx
    movb $'P', %al
    outb %al, %dx
    movb $'F', %al
    outb %al, %dx
    movb $'\r', %al
    outb %al, %dx
    movb $'\n', %al
    outb %al, %dx
1:  hlt
    jmp 1b

/* ============================================================================
 * 64-BIT LONG MODE CODE
 * ============================================================================ */

.code64
trampoline_64bit:
    /* Set up 64-bit data segments */
    movw $0x20, %ax                 /* Selector 0x20 = 64-bit data */
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %fs
    movw %ax, %gs
    movw %ax, %ss

    /* Enable SSE: clear CR0.EM (bit 2), set CR0.MP (bit 1) */
    movq %cr0, %rax
    andq $~(1 << 2), %rax
    orq  $(1 << 1), %rax
    movq %rax, %cr0

    /* Enable SSE: set CR4.OSFXSR (bit 9) + OSXMMEXCPT (bit 10) */
    movq %cr4, %rax
    orq  $((1 << 9) | (1 << 10)), %rax
    movq %rax, %cr4

    /* Load stack pointer from boot parameters */
    movq 0x1F08, %rsp

    /* Load CPU ID and APIC ID into argument registers */
    movl 0x1F18, %edi               /* cpu_id -> RDI (first arg) */
    movl 0x1F1C, %esi               /* apic_id -> RSI (second arg) */

    /* Load C entry point address */
    movq 0x1F10, %rax

    /* Align stack to 16 bytes (ABI requirement) */
    andq $~0xF, %rsp

    /* Call the C entry point: vos3_ap_entry(cpu_id, apic_id) */
    call *%rax

    /* Should never return, but just in case */
trampoline_halt:
    cli
    hlt
    jmp trampoline_halt

/* ============================================================================
 * TEMPORARY GDT FOR AP BOOT
 * ============================================================================
 *
 * This GDT is used only during the transition from Real Mode to Long Mode.
 * Once the AP reaches the C entry point, it will load the proper kernel GDT.
 *
 * Layout:
 *   0x00: Null descriptor
 *   0x08: 32-bit Code (for Protected Mode transition)
 *   0x10: 32-bit Data
 *   0x18: 64-bit Code (for Long Mode)
 *   0x20: 64-bit Data
 */

.align 16
trampoline_gdt:
    /* 0x00: Null descriptor */
    .quad 0x0000000000000000

    /* 0x08: 32-bit Code segment (ring 0, execute/read) */
    .word 0xFFFF                    /* Limit 0:15 */
    .word 0x0000                    /* Base 0:15 */
    .byte 0x00                      /* Base 16:23 */
    .byte 0x9A                      /* Access: P=1, DPL=0, S=1, E=1, DC=0, RW=1, A=0 */
    .byte 0xCF                      /* Flags: G=1, DB=1, L=0, Limit 16:19 = 0xF */
    .byte 0x00                      /* Base 24:31 */

    /* 0x10: 32-bit Data segment (ring 0, read/write) */
    .word 0xFFFF
    .word 0x0000
    .byte 0x00
    .byte 0x92                      /* Access: P=1, DPL=0, S=1, E=0, DC=0, RW=1, A=0 */
    .byte 0xCF
    .byte 0x00

    /* 0x18: 64-bit Code segment (ring 0, execute/read, long mode) */
    .word 0x0000                    /* Limit (ignored in 64-bit mode) */
    .word 0x0000                    /* Base (ignored in 64-bit mode) */
    .byte 0x00
    .byte 0x9A                      /* Access: P=1, DPL=0, S=1, E=1, DC=0, RW=1, A=0 */
    .byte 0x20                      /* Flags: G=0, DB=0, L=1 (64-bit), Limit = 0 */
    .byte 0x00

    /* 0x20: 64-bit Data segment (ring 0, read/write) */
    .word 0x0000
    .word 0x0000
    .byte 0x00
    .byte 0x92                      /* Access: P=1, DPL=0, S=1, E=0, DC=0, RW=1, A=0 */
    .byte 0x00
    .byte 0x00

trampoline_gdt_end:

/* GDT Descriptor (GDTR format) */
.align 4
trampoline_gdt_desc:
    .word trampoline_gdt_end - trampoline_gdt - 1    /* Limit */
    .long trampoline_gdt - vos3_trampoline_start + 0x1000    /* Base (physical) */

vos3_trampoline_end:

/* ============================================================================
 * END OF TRAMPOLINE
 * ============================================================================ */
