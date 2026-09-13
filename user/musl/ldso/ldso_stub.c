/* ldso_stub.c — Minimal __dls2 stub for VOS3 Phase 1
 *
 * Called by _dlstart_c after self-relocation completes.
 * Signature: void __dls2(unsigned char *base, size_t *sp)
 *
 * 'base' = load base address of the interpreter
 * 'sp'   = original stack pointer (argc, argv, envp, auxv)
 */

#include <stddef.h>
#include <stdint.h>

#define AT_NULL   0
#define AT_ENTRY  9
#define AT_PAGESZ 6

#define SYS_write 1
#define SYS_exit  60

#ifndef hidden
#define hidden __attribute__((__visibility__("hidden")))
#endif

static inline long stub_syscall3(long n, long a1, long a2, long a3)
{
    unsigned long ret;
    __asm__ __volatile__ ("syscall"
        : "=a"(ret)
        : "a"(n), "D"(a1), "S"(a2), "d"(a3)
        : "rcx", "r11", "memory");
    return ret;
}

static inline long stub_syscall1(long n, long a1)
{
    unsigned long ret;
    __asm__ __volatile__ ("syscall"
        : "=a"(ret)
        : "a"(n), "D"(a1)
        : "rcx", "r11", "memory");
    return ret;
}

static size_t stub_strlen(const char *s)
{
    size_t n = 0;
    while (s[n]) n++;
    return n;
}

/* CRTJMP: jump to application entry point with stack restored.
 * Stack alignment: andq $-16 ensures 16-byte alignment per SysV ABI. */
#define CRTJMP(pc, sp) __asm__ __volatile__( \
    "mov %1,%%rsp ; andq $-16,%%rsp ; jmp *%0" \
    : : "r"(pc), "r"(sp) : "memory" )

hidden void __dls2(unsigned char *base, size_t *sp)
{
    /* Parse stack: argc, argv[], NULL, envp[], NULL, auxv[] */
    size_t argc = sp[0];
    size_t *argv = sp + 1;
    size_t *envp = argv + argc + 1;  /* skip argv + NULL terminator */

    /* Skip envp to find auxv */
    size_t *p = envp;
    while (*p) p++;
    p++;  /* skip NULL terminator */

    /* p now points to auxv */
    size_t *auxv = p;

    /* Find AT_ENTRY */
    size_t entry = 0;
    for (size_t i = 0; auxv[i] != AT_NULL; i += 2) {
        if (auxv[i] == AT_ENTRY) {
            entry = auxv[i + 1];
        }
    }

    /* Print hello message */
    const char *msg = "[musl-ldso] Hello from musl ldso on VOS3!\n";
    stub_syscall3(SYS_write, 1, (long)msg, (long)stub_strlen(msg));

    if (entry == 0) {
        const char *err = "[musl-ldso] FATAL: AT_ENTRY not found in auxv\n";
        stub_syscall3(SYS_write, 1, (long)err, (long)stub_strlen(err));
        stub_syscall1(SYS_exit, 1);
        __builtin_unreachable();
    }

    /* Jump to application entry point, restoring stack */
    CRTJMP((void *)entry, argv - 1);
    __builtin_unreachable();
}
