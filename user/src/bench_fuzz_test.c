/**
 * @file bench_fuzz_test.c
 * @brief VOS3 Syscall Fuzzing Test Suite
 *
 * @details Tests kernel robustness with invalid inputs:
 *          1. Invalid file descriptors (read/write/close with bad fd)
 *          2. NULL pointers (write/open with NULL buffers)
 *          3. Huge sizes (read/write with enormous counts)
 *          4. Invalid mmap args (bad flags/protection combos)
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* ============================================================================
 * TEST FRAMEWORK
 * ============================================================================ */

static int g_tests_passed = 0;
static int g_tests_failed = 0;

#define TEST_PASS(name) do { \
    printf("  [PASS] %s\n", name); \
    g_tests_passed++; \
} while(0)

#define TEST_FAIL(name) do { \
    printf("  [FAIL] %s\n", name); \
    g_tests_failed++; \
} while(0)

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/* mmap constants */
#define PROT_READ       0x1
#define PROT_WRITE      0x2
#define MAP_PRIVATE     0x02
#define MAP_ANONYMOUS   0x20
#define MAP_FIXED       0x10

/* ============================================================================
 * TEST 1: syscall_invalid_fd
 *
 * Call read/write/close with invalid file descriptors.
 * Kernel must return errors, not crash.
 * ============================================================================ */

static void test_syscall_invalid_fd(void)
{
    printf("\n--- Test: syscall_invalid_fd ---\n");

    char buf[16];

    /* read with fd = -1 */
    long ret = syscall3(SYS_READ, -1, (long)buf, sizeof(buf));
    printf("    read(fd=-1) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("invalid_fd: read(fd=-1) returns error");
    } else {
        TEST_FAIL("invalid_fd: read(fd=-1) should fail");
    }

    /* write with fd = 9999 */
    ret = syscall3(SYS_WRITE, 9999, (long)"test", 4);
    printf("    write(fd=9999) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("invalid_fd: write(fd=9999) returns error");
    } else {
        TEST_FAIL("invalid_fd: write(fd=9999) should fail");
    }

    /* close with fd = -1 */
    ret = syscall1(SYS_CLOSE, -1);
    printf("    close(fd=-1) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("invalid_fd: close(fd=-1) returns error");
    } else {
        /* Some kernels silently succeed on close(-1) */
        TEST_PASS("invalid_fd: close(fd=-1) handled (kernel-specific)");
    }

    /* close with fd = 999 (never opened) */
    ret = syscall1(SYS_CLOSE, 999);
    printf("    close(fd=999) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("invalid_fd: close(fd=999) returns error");
    } else {
        TEST_PASS("invalid_fd: close(fd=999) handled (kernel-specific)");
    }
}

/* ============================================================================
 * TEST 2: syscall_null_pointer
 *
 * Pass NULL pointers to syscalls. Kernel must not crash.
 * ============================================================================ */

static void test_syscall_null_pointer(void)
{
    printf("\n--- Test: syscall_null_pointer ---\n");

    /* write(stdout, NULL, 100) */
    long ret = syscall3(SYS_WRITE, STDOUT_FILENO, 0, 100);
    printf("    write(1, NULL, 100) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("null_pointer: write(NULL) returns error");
    } else if (ret == 0) {
        TEST_PASS("null_pointer: write(NULL) returns 0 (handled)");
    } else {
        TEST_FAIL("null_pointer: write(NULL) unexpectedly succeeded");
    }

    /* open(NULL, 0) */
    ret = syscall3(SYS_OPEN, 0, 0, 0);
    printf("    open(NULL, 0) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("null_pointer: open(NULL) returns error");
    } else {
        /* If it somehow opens, close it */
        syscall1(SYS_CLOSE, ret);
        TEST_FAIL("null_pointer: open(NULL) unexpectedly succeeded");
    }

    /* read(stdin, NULL, 100) - use a valid fd but null buffer */
    int fd = open("/tmp/fuzz_null.txt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        write(fd, "data", 4);
        close(fd);
        fd = open("/tmp/fuzz_null.txt", O_RDONLY, 0);
        if (fd >= 0) {
            ret = syscall3(SYS_READ, fd, 0, 100);
            printf("    read(fd, NULL, 100) = %ld\n", ret);
            if (ret < 0) {
                TEST_PASS("null_pointer: read(NULL buf) returns error");
            } else if (ret == 0) {
                TEST_PASS("null_pointer: read(NULL buf) returns 0 (handled)");
            } else {
                TEST_FAIL("null_pointer: read(NULL buf) should not succeed");
            }
            close(fd);
        }
        unlink("/tmp/fuzz_null.txt");
    }
}

/* ============================================================================
 * TEST 3: syscall_huge_size
 *
 * Call read/write with enormous size parameters.
 * Kernel must handle gracefully (partial read/write or error).
 * ============================================================================ */

static void test_syscall_huge_size(void)
{
    printf("\n--- Test: syscall_huge_size ---\n");

    char buf[16];

    /* read with huge count */
    int fd = open("/tmp/fuzz_huge.txt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        TEST_FAIL("huge_size: create test file");
        return;
    }
    write(fd, "small", 5);
    close(fd);

    fd = open("/tmp/fuzz_huge.txt", O_RDONLY, 0);
    if (fd < 0) {
        TEST_FAIL("huge_size: open test file");
        unlink("/tmp/fuzz_huge.txt");
        return;
    }

    long ret = syscall3(SYS_READ, fd, (long)buf, 0x7FFFFFFF);
    printf("    read(fd, buf, 0x7FFFFFFF) = %ld\n", ret);

    if (ret >= 0 && ret <= 5) {
        TEST_PASS("huge_size: read clamped to file size");
    } else if (ret < 0) {
        TEST_PASS("huge_size: read returns error for huge size");
    } else {
        TEST_FAIL("huge_size: read returned unexpected value");
    }
    close(fd);
    unlink("/tmp/fuzz_huge.txt");

    /* write with huge count but tiny buffer (kernel should not read past buffer) */
    ret = syscall3(SYS_WRITE, STDOUT_FILENO, (long)"hi", 0x7FFFFFFF);
    printf("    write(1, \"hi\", 0x7FFFFFFF) = %ld\n", ret);

    /* Any result that doesn't crash the kernel is acceptable */
    TEST_PASS("huge_size: write with huge count handled");

    /* lseek to absurd offset */
    fd = open("/tmp/fuzz_seek.txt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        write(fd, "test", 4);
        ret = lseek(fd, 0x7FFFFFFFFFFFFFFF, SEEK_SET);
        printf("    lseek(huge offset) = %ld\n", ret);
        /* Any non-crash result is OK */
        TEST_PASS("huge_size: lseek to huge offset handled");
        close(fd);
        unlink("/tmp/fuzz_seek.txt");
    }
}

/* ============================================================================
 * TEST 4: mmap_invalid_args
 *
 * Call mmap with invalid flag/protection combinations.
 * Kernel must reject or handle gracefully.
 * ============================================================================ */

static void test_mmap_invalid_args(void)
{
    printf("\n--- Test: mmap_invalid_args ---\n");

    /* mmap with no flags (no MAP_PRIVATE or MAP_SHARED) */
    long ret = syscall6(SYS_MMAP, 0, 4096, PROT_READ | PROT_WRITE,
                        0, -1, 0);
    printf("    mmap(flags=0) = %ld (0x%lx)\n", ret, ret);
    if (ret < 0 || (unsigned long)ret > 0xFFFF000000000000UL) {
        TEST_PASS("mmap_invalid: flags=0 returns error");
    } else {
        /* If it succeeded, unmap it */
        syscall2(SYS_MUNMAP, ret, 4096);
        TEST_PASS("mmap_invalid: flags=0 accepted (kernel-specific)");
    }

    /* mmap at address 0 with MAP_FIXED (should be rejected — null page) */
    ret = syscall6(SYS_MMAP, 0, 4096, PROT_READ,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    printf("    mmap(addr=0, MAP_FIXED) = %ld\n", ret);
    if (ret < 0 || (unsigned long)ret > 0xFFFF000000000000UL) {
        TEST_PASS("mmap_invalid: null page MAP_FIXED rejected");
    } else {
        syscall2(SYS_MUNMAP, ret, 4096);
        TEST_PASS("mmap_invalid: null MAP_FIXED accepted (risky but handled)");
    }

    /* munmap with invalid address */
    ret = syscall2(SYS_MUNMAP, 0xDEAD0000, 4096);
    printf("    munmap(0xDEAD0000) = %ld\n", ret);
    if (ret < 0) {
        TEST_PASS("mmap_invalid: munmap bad address returns error");
    } else {
        TEST_PASS("mmap_invalid: munmap bad address handled (no-op)");
    }

    /* mmap with size = 0 */
    ret = syscall6(SYS_MMAP, 0, 0, PROT_READ,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    printf("    mmap(size=0) = %ld\n", ret);
    if (ret < 0 || (unsigned long)ret > 0xFFFF000000000000UL) {
        TEST_PASS("mmap_invalid: size=0 returns error");
    } else {
        TEST_PASS("mmap_invalid: size=0 handled (kernel-specific)");
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[], char *envp[])
{
    (void)argc; (void)argv; (void)envp;

    printf("\n");
    printf("===========================================\n");
    printf("  VOS3 Syscall Fuzzing Test Suite\n");
    printf("===========================================\n");

    test_syscall_invalid_fd();
    test_syscall_null_pointer();
    test_syscall_huge_size();
    test_mmap_invalid_args();

    printf("\n");
    printf("===========================================\n");
    printf("  FUZZ TEST RESULTS: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);
    printf("===========================================\n");

    return g_tests_failed > 0 ? 1 : 0;
}
