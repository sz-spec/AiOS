/**
 * @file fake_interp.c
 * @brief Minimal ET_DYN interpreter for VOS3 PT_INTERP test
 *
 * Built as ET_DYN (PIE) shared object. When loaded by the kernel as an
 * interpreter (via PT_INTERP), receives the auxiliary vector on the stack,
 * validates AT_ENTRY and AT_BASE, then jumps to the main program's entry.
 */

/* Assembly entry point — grab RSP and call C handler */
__asm__(
    ".text\n"
    ".global _start\n"
    ".type _start, @function\n"
    "_start:\n"
    "    movq %rsp, %rdi\n"     /* Pass original stack pointer as arg1 */
    "    call _interp_main\n"   /* Call C handler */
    "    ud2\n"                  /* Should not return */
);

/* ============================================================================
 * INLINE SYSCALLS (no libc dependency)
 * ============================================================================ */

#define SYS_WRITE  1
#define SYS_EXIT   60

#define AT_NULL    0
#define AT_BASE    7
#define AT_ENTRY   9

static long syscall3(long num, long a1, long a2, long a3)
{
    long ret;
    __asm__ volatile(
        "syscall"
        : "=a"(ret)
        : "a"(num), "D"(a1), "S"(a2), "d"(a3)
        : "rcx", "r11", "memory"
    );
    return ret;
}

static void write_str(const char *s)
{
    long len = 0;
    while (s[len]) len++;
    syscall3(SYS_WRITE, 1, (long)s, len);
}

static void _exit_now(int code) __attribute__((noreturn));
static void _exit_now(int code)
{
    __asm__ volatile(
        "syscall"
        :
        : "a"((long)SYS_EXIT), "D"((long)code)
        : "rcx", "r11", "memory"
    );
    __builtin_unreachable();
}

/* ============================================================================
 * INTERPRETER MAIN
 * ============================================================================ */

/**
 * Called from _start with the original stack pointer.
 *
 * Stack layout (Linux / VOS3 ABI):
 *   [sp+0]  argc
 *   [sp+8]  argv[0] ... argv[argc-1], NULL
 *   [...]   envp[0] ... envp[n], NULL
 *   [...]   auxv[0].a_type, auxv[0].a_val, ..., AT_NULL, 0
 */
__attribute__((visibility("hidden")))
void _interp_main(unsigned long *sp)
{
    unsigned long *original_sp = sp;  /* Save for jump to main */

    /* Parse argc */
    unsigned long argc = *sp++;

    /* Skip argv */
    sp += argc + 1;  /* argv entries + NULL terminator */

    /* Skip envp */
    while (*sp != 0) sp++;
    sp++;  /* past NULL terminator */

    /* Parse auxiliary vector */
    unsigned long at_entry = 0, at_base = 0;
    int found_entry = 0, found_base = 0;

    while (sp[0] != AT_NULL) {
        if (sp[0] == AT_ENTRY) { at_entry = sp[1]; found_entry = 1; }
        if (sp[0] == AT_BASE)  { at_base  = sp[1]; found_base  = 1; }
        sp += 2;
    }

    /* Validate auxv */
    if (found_entry && found_base && at_base == 0x40000000ULL) {
        write_str("[PASS] test_pt_interp: auxv_valid\n");
    } else {
        write_str("[FAIL] test_pt_interp: auxv invalid\n");
        _exit_now(1);
    }

    /* Jump to main program's entry point with original stack frame.
     * Restore RSP to the value the kernel set up (points to argc),
     * then jump to AT_ENTRY — the main program's _start will read
     * argc/argv/envp from the stack as usual. */
    __asm__ volatile(
        "movq %0, %%rsp\n"
        "jmpq *%1\n"
        :
        : "r"(original_sp), "r"(at_entry)
    );
    __builtin_unreachable();
}
