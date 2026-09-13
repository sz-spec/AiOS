ENTRY(_dlstart)

PHDRS
{
    phdr PT_PHDR   PHDRS;
    text PT_LOAD   FILEHDR PHDRS FLAGS(5);   /* r-x */
    data PT_LOAD   FLAGS(6);                  /* rw- */
    dyn  PT_DYNAMIC;                          /* .dynamic section */
}

SECTIONS
{
    . = SIZEOF_HEADERS;

    .hash       : { *(.hash) }             :text
    .gnu.hash   : { *(.gnu.hash) }         :text
    .dynsym     : { *(.dynsym) }           :text
    .dynstr     : { *(.dynstr) }           :text
    .rela.dyn   : { *(.rela.dyn) *(.rela.*) } :text
    .text       : { *(.text .text.*) }     :text
    .rodata     : { *(.rodata .rodata.*) } :text

    . = ALIGN(0x1000);

    .dynamic    : { *(.dynamic) }          :data :dyn
    .got        : { *(.got) }              :data
    .got.plt    : { *(.got.plt) }          :data
    .data       : { *(.data .data.*) }     :data
    .bss        : { *(.bss .bss.* COMMON) } :data

    /DISCARD/   : { *(.comment .note.* .eh_frame .eh_frame_hdr) }
}
