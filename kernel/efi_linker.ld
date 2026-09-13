/**
 * @file efi_linker.ld
 * @brief PE32+ EFI Application Linker Script for VOS3 UEFI Boot Stub
 *
 * @details Produces an ELF binary with section layout matching the PE32+
 *          format expected by objcopy --target=efi-app-x86_64.
 *
 *          Sections:
 *          .text   - Code (efi_main and helpers)
 *          .data   - Initialized data (g_efi_boot_info, etc.)
 *          .bss    - Zero-initialized data (raw memory map buffer)
 *          .reloc  - Empty PE relocation section (required by UEFI)
 *
 * @note The EFI application runs in UEFI physical address mode with
 *       identity mapping.  No virtual address tricks needed here.
 *
 * @version 8.1.0
 * @date 2026-04-10
 */

OUTPUT_FORMAT("elf64-x86-64")
OUTPUT_ARCH(i386:x86-64)
ENTRY(efi_main)

SECTIONS
{
    . = 0;

    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
    }

    . = ALIGN(4096);

    .data : {
        *(.data .data.*)
        *(.got .got.*)
    }

    . = ALIGN(4096);

    .bss : {
        *(.bss .bss.*)
        *(COMMON)
    }

    . = ALIGN(4096);

    /* Empty .reloc section required by UEFI PE32+ loader.
     * Without this, some firmware rejects the image. */
    .reloc : {
        *(.reloc)
    }

    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.debug*)
    }
}
