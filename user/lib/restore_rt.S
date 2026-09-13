/*
 * restore_rt.S — User-space signal return trampoline for VOS3 custom libc
 *
 * After a signal handler returns, execution resumes here.
 * We issue rt_sigreturn (syscall 15) to restore the pre-signal context.
 *
 * This matches musl's __restore_rt (musl/src/signal/x86_64/restore.s).
 */

.global __restore_rt
.type __restore_rt, @function
__restore_rt:
    mov $15, %rax       /* SYS_RT_SIGRETURN */
    syscall
.size __restore_rt, . - __restore_rt
