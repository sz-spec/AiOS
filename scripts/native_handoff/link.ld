ENTRY(_start)
SECTIONS {
  . = 0xffffffff80000000;
  .text : { *(.text.boot) *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) }
  .data : { *(.data .data.*) }
  .bss : { *(.bss .bss.*) *(COMMON) }
  /DISCARD/ : { *(.eh_frame) *(.comment) *(.note*) }
}
