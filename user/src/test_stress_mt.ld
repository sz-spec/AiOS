/*
 * test_stress_mt.ld — Linker script for dynamically-linked musl test
 */

ENTRY(_start)

PHDRS
{
    phdr    PT_PHDR  PHDRS;
    interp  PT_INTERP;
    text    PT_LOAD  FILEHDR PHDRS FLAGS(5);   /* r-x */
    data    PT_LOAD  FLAGS(6);                  /* rw- */
    tls     PT_TLS;
    dyn     PT_DYNAMIC;
}

SECTIONS
{
    . = 0x400000 + SIZEOF_HEADERS;

    .interp   : { *(.interp) }                          :interp :text
    .dynsym   : { *(.dynsym) }                          :text
    .dynstr   : { *(.dynstr) }                          :text
    .hash     : { *(.hash) }                            :text
    .rela.dyn : { *(.rela.dyn) *(.rela.plt) }           :text
    .plt      : { *(.plt) *(.plt.*) }                   :text
    .text     : { *(.text .text.*) }                    :text
    .rodata   : { *(.rodata .rodata.*) }                :text

    . = ALIGN(0x1000);

    .tdata    : { *(.tdata .tdata.*) }                  :data :tls
    .tbss     : { *(.tbss .tbss.*) }                    :data :tls
    .dynamic  : { *(.dynamic) }                         :data :dyn
    .got      : { *(.got) }                             :data
    .got.plt  : { *(.got.plt) }                         :data
    .data     : { *(.data .data.*) }                    :data
    .bss      : { *(.bss .bss.* COMMON) }               :data

    /DISCARD/ : { *(.comment .note.* .eh_frame .eh_frame_hdr) }
}
