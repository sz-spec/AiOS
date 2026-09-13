/**
 * VOS3 Kernel Linker Script
 *
 * Memory Layout (Higher-Half Kernel with Multiboot2):
 *   Boot code: 0x0000000002000000 (32 MiB, physical)
 *   Kernel:    0xFFFFFFFF80000000 (Virtual, higher half)
 *
 * The multiboot2 header and 32-bit bootstrap code are placed at the
 * beginning of the physical image. After paging is set up, execution
 * continues in the higher-half kernel.
 */

/* Entry point - multiboot2 entry for GRUB/QEMU boot */
ENTRY(_multiboot2_start)

/* Leave low memory available for firmware allocations. Loading the full
 * image at 1 MiB overlaps reserved memory on the tested UEFI firmware.
 * Keep the bootstrap's fixed virtual-to-physical offset of 0xffffffff80000000.
 * This fixed-address Multiboot2 path is not a relocatable/KASLR loader. */
KERNEL_LMA = 0x0000000002000000;
KERNEL_VMA = 0xFFFFFFFF80000000 + KERNEL_LMA;

/* Program headers for ELF segments */
PHDRS
{
    boot PT_LOAD FLAGS(5);          /* R-X: Boot code */
    note PT_NOTE FLAGS(4);          /* R--: PVH note for QEMU */
    text PT_LOAD FLAGS(5);          /* R-X: Kernel code */
    rodata PT_LOAD FLAGS(4);        /* R--: Read-only data */
    data PT_LOAD FLAGS(6);          /* RW-: Writable data */
}

SECTIONS
{
    /* ========== BOOT SECTION (Physical) ========== */
    /* This runs before paging is enabled */
    . = KERNEL_LMA;

    .multiboot2 ALIGN(8) : AT(KERNEL_LMA)
    {
        KEEP(*(.multiboot2))
    } :boot

    /* PVH ELF Note for QEMU -kernel direct boot */
    .note.Xen ALIGN(4) : AT(ADDR(.note.Xen))
    {
        KEEP(*(.note.Xen))
    } :boot :note

    .text.boot32 ALIGN(16) : AT(ADDR(.text.boot32))
    {
        *(.text.boot32)
    } :boot

    .text.boot64 ALIGN(16) : AT(ADDR(.text.boot64))
    {
        *(.text.boot64)
    } :boot

    .data.boot ALIGN(16) : AT(ADDR(.data.boot))
    {
        *(.data.boot)
    } :boot

    .bss.boot ALIGN(4K) (NOLOAD) : AT(ADDR(.bss.boot))
    {
        *(.bss.boot)
    } :boot

    /* Calculate where kernel proper starts */
    _boot_end_phys = .;
    _boot_size = . - KERNEL_LMA;

    /* ========== KERNEL PROPER (Higher Half) ========== */
    /* Switch to virtual addresses */
    . = KERNEL_VMA + _boot_size;

    /* ========== TEXT SECTION ========== */
    .text ALIGN(4K) : AT(ADDR(.text) - KERNEL_VMA + KERNEL_LMA)
    {
        _text_start = .;

        /* Kernel entry points */
        *(.text.boot)
        *(.text.entry)

        /* All other code */
        *(.text .text.*)

        _text_end = .;
    } :text

    /* ========== RODATA SECTION ========== */
    .rodata ALIGN(4K) : AT(ADDR(.rodata) - KERNEL_VMA + KERNEL_LMA)
    {
        _rodata_start = .;

        /* Limine requests (must be contiguous) */
        KEEP(*(.limine_requests_start))
        KEEP(*(.limine_requests))
        KEEP(*(.limine_requests_end))

        *(.rodata .rodata.*)

        /* Exception tables */
        __start_exception_table = .;
        *(.exception_table)
        __end_exception_table = .;

        _rodata_end = .;
    } :rodata

    /* ========== DATA SECTION ========== */
    .data ALIGN(4K) : AT(ADDR(.data) - KERNEL_VMA + KERNEL_LMA)
    {
        _data_start = .;

        *(.data .data.*)

        _data_end = .;
    } :data

    /* ========== BSS SECTION ========== */
    .bss ALIGN(4K) : AT(ADDR(.bss) - KERNEL_VMA + KERNEL_LMA)
    {
        _bss_start = .;

        *(.bss .bss.*)
        *(COMMON)

        _bss_end = .;
    } :data

    /* ========== KERNEL STACKS (NOLOAD — not loaded from ELF, zeroed at boot) ========== */
    .stacks ALIGN(4K) (NOLOAD) : AT(ADDR(.stacks) - KERNEL_VMA + KERNEL_LMA)
    {
        /* Boot stack (64 KiB) */
        _boot_stack_bottom = .;
        . += 64K;
        _boot_stack_top = .;

        /* IST stacks (16 KiB each, 4 stacks for IST1-4) */
        _ist_stacks_start = .;

        _ist1_stack_bottom = .;
        . += 16K;
        _ist1_stack_top = .;

        _ist2_stack_bottom = .;
        . += 16K;
        _ist2_stack_top = .;

        _ist3_stack_bottom = .;
        . += 16K;
        _ist3_stack_top = .;

        _ist4_stack_bottom = .;
        . += 16K;
        _ist4_stack_top = .;

        _ist_stacks_end = .;
    } :data

    /* ========== KERNEL END ========== */
    . = ALIGN(4K);
    _kernel_end = .;

    /* ========== SYMBOLS ========== */

    /* Physical addresses (for bootloader) */
    _kernel_phys_start = KERNEL_LMA;
    _kernel_phys_end = _kernel_end - KERNEL_VMA + KERNEL_LMA;

    /* Virtual addresses */
    _kernel_virt_start = KERNEL_VMA;
    _kernel_virt_end = _kernel_end;

    /* Kernel size */
    _kernel_size = _kernel_end - KERNEL_VMA;

    /* ========== DISCARD ========== */
    /DISCARD/ :
    {
        *(.comment)
        *(.note.*)
        *(.eh_frame*)
    }
}
