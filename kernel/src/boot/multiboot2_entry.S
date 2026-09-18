/**
 * @file multiboot2_entry.S
 * @brief Multiboot2 entry point for VOS3
 *
 * @details Contains the multiboot2 header and 32-to-64 bit transition code.
 *          Sets up long mode and paging before jumping to C code.
 *
 * @version 1.0.0
 * @date 2026-02-16
 */

/* Multiboot2 constants */
.set MULTIBOOT2_MAGIC,          0xE85250D6
.set MULTIBOOT2_ARCH_I386,      0
.set MULTIBOOT2_TAG_END,        0
.set MULTIBOOT2_TAG_INFO_REQ,   1
.set MULTIBOOT2_TAG_ADDR,       2
.set MULTIBOOT2_TAG_ENTRY,      3
.set MULTIBOOT2_TAG_CONSOLE,    4
.set MULTIBOOT2_TAG_FRAMEBUFFER, 5
.set MULTIBOOT2_TAG_MODULE_ALIGN, 6

/* Page table constants */
.set PAGE_PRESENT,      (1 << 0)
.set PAGE_WRITABLE,     (1 << 1)
.set PAGE_USER,         (1 << 2)    /* User accessible - NOT used in boot */
.set PAGE_HUGE,         (1 << 7)
.set PAGE_GLOBAL,       (1 << 8)    /* Global page - not flushed on CR3 write */
/* NX bit is bit 63 - cannot be set in 32-bit mode easily */

/* CR0 bits */
.set CR0_PE,    (1 << 0)
.set CR0_WP,    (1 << 16)
.set CR0_PG,    (1 << 31)

/* CR0 bits for FPU/SSE */
.set CR0_EM,    (1 << 2)   /* Emulation — must be CLEAR for SSE */
.set CR0_MP,    (1 << 1)   /* Monitor Coprocessor */

/* CR4 bits */
.set CR4_PAE,       (1 << 5)
.set CR4_OSFXSR,    (1 << 9)    /* OS support for FXSAVE/FXRSTOR */
.set CR4_OSXMMEXCPT,(1 << 10)   /* OS support for SSE exceptions */

/* EFER bits */
.set EFER_MSR,  0xC0000080
.set EFER_LME,  (1 << 8)
.set EFER_NXE,  (1 << 11)

/* Features used unconditionally before the C runtime can install an IDT. */
.set BASIC_REQUIRED, ((1 << 0) | (1 << 4) | (1 << 5) | (1 << 6) | \
                      (1 << 13) | (1 << 16) | (1 << 24) | \
                      (1 << 25) | (1 << 26))
.set EXT_REQUIRED,   ((1 << 11) | (1 << 20) | (1 << 29))

/* ============================================================================
 * PVH ELF NOTE (for QEMU -kernel direct boot)
 * ============================================================================
 * QEMU 10.x requires a PVH note for direct ELF boot.
 * We detect PVH vs Multiboot2 boot by checking EAX magic.
 */

.set XEN_ELFNOTE_PHYS32_ENTRY, 18
.set MULTIBOOT2_BOOTLOADER_MAGIC, 0x36D76289

.section .note.Xen, "a", @note
.align 4
pvh_note:
    .long 4                         /* namesz = sizeof("Xen\0") */
    .long 4                         /* descsz = sizeof(uint32_t) */
    .long XEN_ELFNOTE_PHYS32_ENTRY  /* type = PVH entry point */
    .asciz "Xen"                    /* name (4 bytes with null) */
    .long _multiboot2_start         /* desc = 32-bit entry point */

/* ============================================================================
 * MULTIBOOT2 HEADER
 * ============================================================================ */

.section .multiboot2, "a"
.align 8

multiboot2_header:
    .long MULTIBOOT2_MAGIC              /* Magic number */
    .long MULTIBOOT2_ARCH_I386          /* Architecture (i386) */
    .long multiboot2_header_end - multiboot2_header  /* Header length */
    .long -(MULTIBOOT2_MAGIC + MULTIBOOT2_ARCH_I386 + (multiboot2_header_end - multiboot2_header))  /* Checksum */

    /* End tag */
    .align 8
    .word MULTIBOOT2_TAG_END
    .word 0
    .long 8
multiboot2_header_end:

/* ============================================================================
 * 32-BIT BOOTSTRAP CODE
 * ============================================================================ */

.code32
.section .text.boot32, "ax"

.global _multiboot2_start
.type _multiboot2_start, @function
_multiboot2_start:
    /* Serial diagnostics overwrite AL; preserve the boot protocol magic. */
    movl %eax, %esi
    /* Disable interrupts */
    cli

    /* Debug: Init serial port for debug output */
    movw $0x3F8, %dx           /* COM1 base */
    addw $1, %dx               /* IER */
    movb $0x00, %al
    outb %al, %dx              /* Disable interrupts */
    addw $2, %dx               /* LCR (offset 3) */
    movb $0x80, %al
    outb %al, %dx              /* Enable DLAB */
    subw $3, %dx               /* DLL (offset 0) */
    movb $0x03, %al            /* 38400 baud */
    outb %al, %dx
    incw %dx                   /* DLH */
    movb $0x00, %al
    outb %al, %dx
    addw $2, %dx               /* LCR */
    movb $0x03, %al            /* 8 bits, no parity, 1 stop bit */
    outb %al, %dx
    incw %dx                   /* FCR */
    movb $0xC7, %al            /* Enable FIFO */
    outb %al, %dx
    incw %dx                   /* MCR */
    movb $0x0B, %al            /* DTR + RTS + OUT2 */
    outb %al, %dx

    /* Debug: Send "MB" to serial */
    movw $0x3F8, %dx
    movb $'M', %al
    outb %al, %dx
    movb $'B', %al
    outb %al, %dx
    movb $'\r', %al
    outb %al, %dx
    movb $'\n', %al
    outb %al, %dx

    /* Debug: Print "MB" to VGA to show we got here */
    movl $0xB8000, %edi
    movw $0x2F4D, (%edi)       /* 'M' in green on black */
    movw $0x2F42, 2(%edi)      /* 'B' in green on black */

    /* Set up minimal stack for 32-bit code */
    movl $boot32_stack_top, %esp

    /* Check if this is Multiboot2 boot (EAX = 0x36D76289) or PVH boot */
    movl %esi, %eax
    cmpl $MULTIBOOT2_BOOTLOADER_MAGIC, %eax
    je .multiboot2_boot

    /* PVH boot - send "PV" to serial for debug */
    movw $0x3F8, %dx
    movb $'P', %al
    outb %al, %dx
    movb $'V', %al
    outb %al, %dx

    /* PVH boot - set EBX to 0 (invalid boot info pointer) */
    /* The C code will detect this and use fallback boot info */
    xorl %ebx, %ebx
    jmp .boot_continue

.multiboot2_boot:
    /* Multiboot2 boot - save info pointer and magic */
    movl %ebx, multiboot2_info_ptr
    movl %eax, multiboot2_magic

.boot_continue:

    /* Debug: Print "32" to show we're in 32-bit setup */
    movw $0x2F33, 4(%edi)      /* '3' */
    movw $0x2F32, 6(%edi)      /* '2' */

    /* Check for CPUID support */
    pushfl
    popl %eax
    movl %eax, %ecx
    xorl $(1 << 21), %eax   /* Flip ID bit */
    pushl %eax
    popfl
    pushfl
    popl %eax
    pushl %ecx
    popfl
    cmpl %ecx, %eax
    je cpu_preflight_fail

    /* The bootstrap itself uses MSRs, PAE and NX, while the kernel later
     * unconditionally uses PAT, PGE, FXSAVE, SSE and the TSC.  Refuse before
     * executing any of those instructions when the contract is absent. */
    xorl %ecx, %ecx
    movl $0, %eax
    cpuid
    cmpl $1, %eax
    jb cpu_preflight_fail

    movl $1, %eax
    cpuid
    movl %edx, %eax
    andl $BASIC_REQUIRED, %eax
    cmpl $BASIC_REQUIRED, %eax
    jne cpu_preflight_fail

    /* Check the complete extended contract before touching EFER.NXE. */
    movl $0x80000000, %eax
    cpuid
    cmpl $0x80000001, %eax
    jb cpu_preflight_fail

    movl $0x80000001, %eax
    cpuid
    movl %edx, %eax
    andl $EXT_REQUIRED, %eax
    cmpl $EXT_REQUIRED, %eax
    jne cpu_preflight_fail

    /* Set up identity mapping for first 4GB and higher-half mapping */
    call setup_page_tables

    /* Load page tables */
    movl $pml4_table, %eax
    movl %eax, %cr3

    /* Enable PAE */
    movl %cr4, %eax
    orl $CR4_PAE, %eax
    movl %eax, %cr4

    /* Enable long mode and NX in EFER */
    movl $EFER_MSR, %ecx
    rdmsr
    orl $(EFER_LME | EFER_NXE), %eax
    wrmsr

    /* Enable paging */
    movl %cr0, %eax
    orl $(CR0_PG | CR0_WP), %eax
    movl %eax, %cr0

    /* Debug: Send "64" to serial */
    movw $0x3F8, %dx
    movb $'6', %al
    outb %al, %dx
    movb $'4', %al
    outb %al, %dx
    movb $'\r', %al
    outb %al, %dx
    movb $'\n', %al
    outb %al, %dx

    /* Debug: Print "64" to show long mode setup complete */
    movl $0xB8000, %edi
    movw $0x2F36, 8(%edi)      /* '6' */
    movw $0x2F34, 10(%edi)     /* '4' */

    /* Load 64-bit GDT */
    lgdt gdt64_ptr

    /* Jump to 64-bit code */
    ljmp $0x08, $long_mode_start

cpu_preflight_fail:
    /* Emit a machine-readable serial marker, display an error and halt. */
    movw $0x3F8, %dx
    movb $'C', %al
    outb %al, %dx
    movb $'P', %al
    outb %al, %dx
    movb $'U', %al
    outb %al, %dx
    movb $'F', %al
    outb %al, %dx
    movb $'\r', %al
    outb %al, %dx
    movb $'\n', %al
    outb %al, %dx
    movl $0xB8000, %edi
    movl $0x4F504F43, (%edi)    /* "CP" in red */
    movl $0x4F204F55, 4(%edi)   /* "U " */
    movl $0x4F414F46, 8(%edi)   /* "FA" */
1:  hlt
    jmp 1b

/* ============================================================================
 * PAGE TABLE SETUP (32-bit)
 *
 * Boot page table structure (KERNEL ONLY - no USER bit):
 *
 * PML4[0]   -> pdpt_table  (identity map, TEMPORARY - cleared after init)
 * PML4[256] -> pdpt_table  (0xFFFF800000000000 - direct physical map)
 * PML4[511] -> pdpt_kernel (0xFFFFFFFF80000000 - kernel text/data)
 *
 * pdpt_table: 512 x 1GB huge pages (maps first 512GB)
 * pdpt_kernel[510] -> pd_kernel (512 x 2MB pages for kernel)
 *
 * IMPORTANT: These boot page tables do NOT have USER bit set.
 * User-space processes get a fresh address space with empty PML4[0-255].
 * ============================================================================ */

setup_page_tables:
    /* Clear all page tables to zero */
    movl $pml4_table, %edi
    movl $(page_tables_end - pml4_table), %ecx
    shrl $2, %ecx
    xorl %eax, %eax
    rep stosl

    /* Set up PML4 entries (kernel-only, no USER bit) */
    movl $pml4_table, %edi

    /* Entry 0: Identity map (first 512GB) - TEMPORARY for boot transition
     * This will NOT be copied to user address spaces */
    movl $pdpt_table, %eax
    orl $(PAGE_PRESENT | PAGE_WRITABLE), %eax
    movl %eax, (%edi)

    /* Entry 256 (0xFFFF800000000000): Higher-half direct physical map
     * This IS copied to user address spaces (kernel access only) */
    movl %eax, 256*8(%edi)

    /* Entry 511 (0xFFFFFFFF80000000): Kernel text/data
     * This IS copied to user address spaces (kernel access only) */
    movl $pdpt_kernel, %eax
    orl $(PAGE_PRESENT | PAGE_WRITABLE), %eax
    movl %eax, 511*8(%edi)

    /* Set up PDPT for identity/direct mapping (first 512GB as 1GB huge pages)
     * NO USER bit - these are kernel-only mappings */
    movl $pdpt_table, %edi
    movl $0, %eax               /* Start at physical 0 */
    movl $512, %ecx             /* 512 entries (512GB) */
1:
    orl $(PAGE_PRESENT | PAGE_WRITABLE | PAGE_HUGE), %eax
    movl %eax, (%edi)
    movl $0, 4(%edi)            /* High 32 bits (no NX in 32-bit mode) */
    addl $0x40000000, %eax      /* Next 1GB */
    addl $8, %edi
    loop 1b

    /* Set up PDPT for kernel mapping at 0xFFFFFFFF80000000
     * Entry 510 maps 0xFFFFFFFF80000000 - 0xFFFFFFFFC0000000 */
    movl $pdpt_kernel, %edi
    movl $pd_kernel, %eax
    orl $(PAGE_PRESENT | PAGE_WRITABLE), %eax
    movl %eax, 510*8(%edi)      /* PDPT entry 510 */

    /* Set up PD for kernel (2MB huge pages starting at physical 0)
     * Virtual 0xFFFFFFFF80000000 -> Physical 0x000000
     * Virtual 0xFFFFFFFF80100000 -> Physical 0x100000 (kernel at 1MB)
     * Virtual 0xFFFFFFFF80200000 -> Physical 0x200000
     * etc.
     * NO USER bit - kernel only */
    movl $pd_kernel, %edi
    movl $0, %eax               /* Physical 0 (2MB aligned) */
    movl $512, %ecx             /* 512 entries (1GB total) */
1:
    orl $(PAGE_PRESENT | PAGE_WRITABLE | PAGE_HUGE), %eax
    movl %eax, (%edi)
    movl $0, 4(%edi)            /* High 32 bits */
    addl $0x200000, %eax        /* Next 2MB */
    addl $8, %edi
    loop 1b

    ret

/* ============================================================================
 * 64-BIT CODE
 * ============================================================================ */

.code64
.section .text.boot64, "ax"

.global long_mode_start
.type long_mode_start, @function
long_mode_start:
    /* Debug: Send "LM" to serial (now in 64-bit mode) */
    movw $0x3F8, %dx
    movb $'L', %al
    outb %al, %dx
    movb $'M', %al
    outb %al, %dx

    /* Reload segment registers */
    movw $0x10, %ax
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %fs
    movw %ax, %gs
    movw %ax, %ss

    /* Debug: Send "OK" to serial */
    movw $0x3F8, %dx
    movb $'O', %al
    outb %al, %dx
    movb $'K', %al
    outb %al, %dx
    movb $'\r', %al
    outb %al, %dx
    movb $'\n', %al
    outb %al, %dx

    /* Load multiboot2 info pointer */
    movl multiboot2_info_ptr(%rip), %ebx

    /* Jump to higher-half code */
    movabsq $higher_half_entry, %rax
    jmp *%rax

/* Higher-half entry point - runs after we're at virtual addresses */
.section .text.entry, "ax"

.global higher_half_entry
.type higher_half_entry, @function
higher_half_entry:
    /* Debug: Send "HH" to serial (higher half reached) */
    movw $0x3F8, %dx
    movb $'H', %al
    outb %al, %dx
    movb $'H', %al
    outb %al, %dx
    movb $'\r', %al
    outb %al, %dx
    movb $'\n', %al
    outb %al, %dx

    /* Now we're running at higher-half addresses */

    /* Set up 64-bit stack (use absolute address) */
    movabsq $_boot_stack_top, %rsp
    andq $-16, %rsp

    /* Clear frame pointer */
    xorq %rbp, %rbp

    /* Clear BSS - use absolute addresses */
    movabsq $_bss_start, %rdi
    movabsq $_bss_end, %rcx
    subq %rdi, %rcx
    shrq $3, %rcx
    xorq %rax, %rax
    rep stosq

    /* Enable SSE: clear CR0.EM, set CR0.MP */
    movq %cr0, %rax
    andq $~CR0_EM, %rax
    orq  $CR0_MP, %rax
    movq %rax, %cr0

    /* Enable SSE: set CR4.OSFXSR + CR4.OSXMMEXCPT */
    movq %cr4, %rax
    orq  $(CR4_OSFXSR | CR4_OSXMMEXCPT), %rax
    movq %rax, %cr4

    /* Convert multiboot2 info pointer to higher-half address */
    movl %ebx, %edi
    movabsq $0xFFFF800000000000, %rax
    addq %rax, %rdi

    /* Call multiboot2 processing */
    movabsq $multiboot2_process, %rax
    call *%rax

    /* Should never return */
1:  cli
    hlt
    jmp 1b

/* ============================================================================
 * DATA
 * ============================================================================ */

.section .data.boot

/* 64-bit GDT */
.align 16
gdt64:
    .quad 0                     /* Null descriptor */
gdt64_code:
    .quad 0x00AF9A000000FFFF    /* Code segment (L=1, D=0) */
gdt64_data:
    .quad 0x00CF92000000FFFF    /* Data segment */
gdt64_end:

gdt64_ptr:
    .word gdt64_end - gdt64 - 1
    .long gdt64

/* Multiboot2 info storage */
multiboot2_info_ptr:
    .long 0
multiboot2_magic:
    .long 0

/* ============================================================================
 * PAGE TABLES (BSS)
 * ============================================================================ */

.section .bss.boot
.align 4096

pml4_table:
    .space 4096
pdpt_table:
    .space 4096
pdpt_kernel:
    .space 4096
pd_kernel:
    .space 4096
page_tables_end:

/* 32-bit boot stack (v23.11: exported for boot secret scrubbing in kmain.c) */
.align 16
.global boot32_stack
boot32_stack:
    .space 4096
boot32_stack_top:

/* External symbols - these are at higher-half addresses */
.extern _boot_stack_top
.extern _bss_start
.extern _bss_end
.extern multiboot2_process
.extern kernel_main
