/*
 * hello_auxv.ld — Linker script for hello_auxv
 *
 * Adds PT_INTERP program header for the .interp section, telling the
 * kernel to load /bin/auxv_interp.  Also includes PT_PHDR and places
 * the ELF file header + program headers inside the first LOAD segment
 * so the dynamic linker can find them via AT_PHDR.
 */

ENTRY(_start)

PHDRS
{
    phdr    PT_PHDR  PHDRS;
    interp  PT_INTERP;
    text    PT_LOAD  FILEHDR PHDRS FLAGS(5);   /* r-x, includes ELF+phdrs */
    data    PT_LOAD  FLAGS(6);                  /* rw- */
}

SECTIONS
{
    . = 0x400000 + SIZEOF_HEADERS;

    .interp : { *(.interp) } :interp :text

    .text : { *(.text .text.*) } :text

    .rodata : { *(.rodata .rodata.*) } :text

    . = ALIGN(0x1000);

    .data : { *(.data .data.*) } :data

    .bss : { *(.bss .bss.* COMMON) } :data

    /DISCARD/ : { *(.comment .note.* .eh_frame .eh_frame_hdr) }
}
