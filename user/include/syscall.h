/**
 * @file syscall.h
 * @brief VOS3 User-Space System Call Interface
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_SYSCALL_H
#define VOS3_USER_SYSCALL_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * SYSTEM CALL NUMBERS
 * ============================================================================ */

/* File operations */
#define SYS_READ        0
#define SYS_WRITE       1
#define SYS_OPEN        2
#define SYS_CLOSE       3
#define SYS_STAT        4
#define SYS_FSTAT       5
#define SYS_POLL        7
#define SYS_LSEEK       8
#define SYS_PIPE        22
#define SYS_DUP         32
#define SYS_DUP2        33

/* File control */
#define SYS_FCNTL       72

/* Process operations */
#define SYS_GETPID      39
#define SYS_FORK        57
#define SYS_EXECVE      59
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_GETPPID     110
#define SYS_GETTID      186

/* Directory operations */
#define SYS_GETCWD      79
#define SYS_CHDIR       80
#define SYS_MKDIR       83
#define SYS_RMDIR       84
#define SYS_GETDENTS    78
#define SYS_UNLINK      87
#define SYS_RENAME      82

/* Memory operations */
#define SYS_BRK         12
#define SYS_MMAP        9
#define SYS_MUNMAP      11

/* Time operations */
#define SYS_NANOSLEEP   35

/* ============================================================================
 * SYSTEM CALL INTERFACE
 * ============================================================================ */

/**
 * @brief Invoke system call with 0-6 arguments
 */
/*
 * Syscall wrappers.
 * IMPORTANT: The kernel zeros all scratch registers (rdi, rsi, rdx, r8, r9, r10)
 * on return to prevent kernel data leakage. All such registers must be in the
 * clobber list so GCC doesn't store live values in them across syscalls.
 */
static inline long syscall0(long num)
{
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num)
        : "rcx", "r11", "rdx", "rsi", "rdi", "r8", "r9", "r10", "memory"
    );
    return ret;
}

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

static inline long syscall3(long num, long arg1, long arg2, long arg3)
{
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num), "D" (arg1), "S" (arg2), "d" (arg3)
        : "rcx", "r11", "r8", "r9", "r10", "memory"
    );
    return ret;
}

static inline long syscall4(long num, long arg1, long arg2, long arg3, long arg4)
{
    long ret;
    register long r10 __asm__("r10") = arg4;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num), "D" (arg1), "S" (arg2), "d" (arg3), "r" (r10)
        : "rcx", "r11", "r8", "r9", "memory"
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

static inline long syscall6(long num, long arg1, long arg2, long arg3,
                            long arg4, long arg5, long arg6)
{
    long ret;
    register long r10 __asm__("r10") = arg4;
    register long r8 __asm__("r8") = arg5;
    register long r9 __asm__("r9") = arg6;
    __asm__ volatile (
        "syscall"
        : "=a" (ret)
        : "a" (num), "D" (arg1), "S" (arg2), "d" (arg3),
          "r" (r10), "r" (r8), "r" (r9)
        : "rcx", "r11", "memory"
    );
    return ret;
}

#endif /* VOS3_USER_SYSCALL_H */
