/* libc.ld — Linker script for musl libc.so (unified with ld-musl-x86_64.so.1) */

ENTRY(_dlstart)

PHDRS
{
    phdr PT_PHDR   PHDRS;
    text PT_LOAD   FILEHDR PHDRS FLAGS(5);   /* r-x */
    data PT_LOAD   FLAGS(6);                  /* rw- */
    dyn  PT_DYNAMIC;
}

SECTIONS
{
    . = SIZEOF_HEADERS;

    .hash       : { *(.hash) }                         :text
    .gnu.hash   : { *(.gnu.hash) }                     :text
    .dynsym     : { *(.dynsym) }                       :text
    .dynstr     : { *(.dynstr) }                       :text
    .rela.dyn   : { *(.rela.dyn) *(.rela.plt) }        :text
    .plt        : { *(.plt) *(.plt.*) }                :text
    .text       : { *(.text .text.*) }                 :text
    .rodata     : { *(.rodata .rodata.*) }             :text

    . = ALIGN(0x1000);

    .data.rel.ro : { *(.data.rel.ro .data.rel.ro.*) }  :data
    .dynamic    : { *(.dynamic) }                       :data :dyn
    .got        : { *(.got) }                           :data
    .got.plt    : { *(.got.plt) }                       :data
    .data       : { *(.data .data.*) }                  :data
    .bss        : { *(.bss .bss.* COMMON) }             :data

    /DISCARD/   : {
        *(.comment .note.* .eh_frame .eh_frame_hdr)
        *(.gnu.warning*)
    }
}
