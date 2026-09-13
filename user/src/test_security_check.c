/**
 * @file test_security_check.c
 * @brief Kernel Security Validation — Exploit Simulation
 *
 * Tests that the stabilization sprint security fixes correctly reject
 * invalid user pointers in sys_puts, sys_arch_prctl, and sys_clone.
 *
 * 8 attack vectors:
 *   1-3: sys_puts with NULL, kernel addr, canonical hole
 *   4-6: arch_prctl ARCH_GET_FS/GS with NULL and kernel addr
 *   7-8: clone with NULL and kernel-space child_stack
 *
 * @version 1.0.0
 * @date 2026-03-31
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"

/* Syscall numbers */
#define SYS_EXIT        60
#define SYS_CLONE       56
#define SYS_ARCH_PRCTL  158
#define SYS_PUTS        476

/* arch_prctl codes */
#define ARCH_GET_FS     0x1003
#define ARCH_GET_GS     0x1004

/* clone flags */
#define CLONE_VM        0x00000100UL
#define CLONE_FS        0x00000200UL
#define CLONE_FILES     0x00000400UL
#define CLONE_SIGHAND   0x00000800UL
#define CLONE_THREAD    0x00010000UL

/* Expected error codes */
#define EFAULT          14
#define EINVAL          22

/* Invalid addresses */
#define ADDR_NULL       0x0UL
#define ADDR_KERNEL     0xFFFF800000000000UL
#define ADDR_HOLE       0x0000800000000000UL

/* Counters */
static int g_pass = 0;
static int g_fail = 0;

/* Syscall wrappers */
static inline long syscall1(long num, long arg1)
{
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num), "D" (arg1)
        : "rcx", "r11", "rdx", "rsi", "r8", "r9", "r10", "memory"
    );
    return ret;
}

static inline long syscall2(long num, long arg1, long arg2)
{
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num), "D" (arg1), "S" (arg2)
        : "rcx", "r11", "rdx", "r8", "r9", "r10", "memory"
    );
    return ret;
}

static inline long syscall5(long num, long arg1, long arg2, long arg3,
                            long arg4, long arg5)
{
    long ret;
    register long r10 __asm__("r10") = arg4;
    register long r8 __asm__("r8") = arg5;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num), "D" (arg1), "S" (arg2), "d" (arg3), "r" (r10), "r" (r8)
        : "rcx", "r11", "r9", "memory"
    );
    return ret;
}

/**
 * @brief Check syscall result against expected error.
 */
static void check(const char *name, long result, long expected_neg)
{
    /* expected_neg is the positive error code (e.g. 14 for EFAULT).
       The syscall should return -expected_neg. */
    if (result == -(long)expected_neg) {
        g_pass++;
        printf("  [PASS] %s (ret=%ld, expected=-%ld)\n", name, result, expected_neg);
    } else {
        g_fail++;
        printf("  [FAIL] %s (ret=%ld, expected=-%ld)\n", name, result, expected_neg);
    }
}

int main(int argc, char **argv)
{
    (void)argc; (void)argv;

    printf("\n");
    printf("================================================\n");
    printf("    VOS3 Kernel Security Validation Suite\n");
    printf("    Stabilization Sprint — Break-Fix Tests\n");
    printf("================================================\n\n");

    long ret;

    /* ---- Attack 1-3: sys_puts with invalid pointers ---- */
    printf("[ATTACK GROUP 1] sys_puts (syscall %d)\n", SYS_PUTS);

    ret = syscall1(SYS_PUTS, (long)ADDR_NULL);
    check("sys_puts(NULL)", ret, EFAULT);

    ret = syscall1(SYS_PUTS, (long)ADDR_KERNEL);
    check("sys_puts(kernel_addr)", ret, EFAULT);

    ret = syscall1(SYS_PUTS, (long)ADDR_HOLE);
    check("sys_puts(canonical_hole)", ret, EFAULT);

    printf("\n");

    /* ---- Attack 4-6: arch_prctl with invalid output pointers ---- */
    printf("[ATTACK GROUP 2] arch_prctl (syscall %d)\n", SYS_ARCH_PRCTL);

    ret = syscall2(SYS_ARCH_PRCTL, ARCH_GET_FS, (long)ADDR_NULL);
    check("arch_prctl(GET_FS, NULL)", ret, EFAULT);

    ret = syscall2(SYS_ARCH_PRCTL, ARCH_GET_FS, (long)ADDR_KERNEL);
    check("arch_prctl(GET_FS, kernel_addr)", ret, EFAULT);

    ret = syscall2(SYS_ARCH_PRCTL, ARCH_GET_GS, (long)ADDR_KERNEL);
    check("arch_prctl(GET_GS, kernel_addr)", ret, EFAULT);

    printf("\n");

    /* ---- Attack 7-8: clone with invalid child_stack ---- */
    printf("[ATTACK GROUP 3] clone (syscall %d)\n", SYS_CLONE);

    unsigned long clone_flags = CLONE_VM | CLONE_FS | CLONE_FILES |
                                CLONE_SIGHAND | CLONE_THREAD;

    /* NULL child_stack → EINVAL (our fix returns -22 for NULL stack) */
    ret = syscall5(SYS_CLONE, (long)clone_flags, (long)ADDR_NULL, 0, 0, 0);
    check("clone(flags, NULL_stack)", ret, EINVAL);

    /* Kernel-space child_stack → EFAULT */
    ret = syscall5(SYS_CLONE, (long)clone_flags, (long)ADDR_KERNEL, 0, 0, 0);
    check("clone(flags, kernel_stack)", ret, EFAULT);

    printf("\n");

    /* ---- Summary ---- */
    printf("================================================\n");
    printf("  Security Validation: %d PASS, %d FAIL\n", g_pass, g_fail);
    printf("================================================\n\n");

    if (g_fail > 0) {
        printf("[FAIL] SECURITY VALIDATION FAILED\n");
    } else {
        printf("[PASS] ALL SECURITY CHECKS PASSED\n");
    }

    return g_fail;
}
