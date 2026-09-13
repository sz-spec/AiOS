/* vos3_syscall_cp.s — cancellation-point syscall stub for VOS3
 * No cancellation support; just forward to __syscall_cp_c.
 *
 * Signature: long __syscall_cp(volatile void *p, long nr, long a, long b, long c, long d, long e)
 * Arguments: rdi=p, rsi=nr, rdx=a, rcx=b, r8=c, r9=d, [stack]=e
 *
 * We skip 'p' and shift args to match __syscall_cp_c(nr, a, b, c, d, e, f)
 */
.text
.global __syscall_cp
.hidden __syscall_cp
.type   __syscall_cp, @function
__syscall_cp:
__cp_begin:
__cp_end:
    /* Shift: rdi=p(skip), rsi=nr, rdx=a, rcx=b, r8=c, r9=d, 8(%rsp)=e */
    movq %rsi, %rdi       /* nr */
    movq %rdx, %rsi       /* a  */
    movq %rcx, %rdx       /* b  */
    movq %r8,  %rcx       /* c  */
    movq %r9,  %r8        /* d  */
    movq 8(%rsp), %r9     /* e  */
    /* f=0 (we only support up to 6 args) */
    pushq $0
    pushq $0              /* align stack for __syscall_cp_c call */
    call __syscall_cp_c
    addq $16, %rsp
    ret
.size __syscall_cp, .-__syscall_cp

.global __cp_cancel
.hidden __cp_cancel
.type   __cp_cancel, @function
__cp_cancel:
    jmp __cancel
.size __cp_cancel, .-__cp_cancel

.global __cp_begin
.hidden __cp_begin
.global __cp_end
.hidden __cp_end
