.section .text.boot,"ax"
.code64
.global _start
_start:
    cli
    /* The trampoline deliberately enables IF before entering a fresh task.
     * This oracle has no IDT, so mask both legacy PICs first: IF can be
     * observed without making the fixture depend on external IRQ delivery. */
    movb $0xff, %al
    outb %al, $0x21
    outb %al, $0xa1
    movq $boot_stack_top, %rsp
    andq $~0xF, %rsp
    xorq %rbp, %rbp
    call kmain
1:  hlt
    jmp 1b

/* First-instruction SysV/IF probe used as the real task trampoline's entry.
 * RDI is the task argument. Capture the untouched entry RSP and RFLAGS before
 * making a normal ABI-aligned C call, then return through the trampoline. */
.global trampoline_entry_probe
.type trampoline_entry_probe, @function
trampoline_entry_probe:
    movq %rdi, %r8
    movq %rsp, %rsi
    pushfq
    popq %rdx
    movq %r8, %rdi
    subq $8, %rsp
    call trampoline_entry_observed
    addq $8, %rsp
    ret
.size trampoline_entry_probe, . - trampoline_entry_probe

.section .bss,"aw",@nobits
.global boot_stack_bottom
.global boot_stack_top
.align 16
boot_stack_bottom: .skip 16384
boot_stack_top:
