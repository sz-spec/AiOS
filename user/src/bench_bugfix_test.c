/**
 * @file bench_bugfix_test.c
 * @brief VOS3 Kernel Bugfix Validation Tests
 *
 * @details Tests the 5 kernel bug fixes from the Day 12 scan:
 *          1. brk shrink — heap pages are unmapped when brk decreases
 *          2. Page table leak — fork/exit cycles don't leak memory
 *          3. Scheduler responsiveness — tasks switch promptly via pipe
 *          4. disk_write checks — write + read-back integrity
 *          5. (VMM docs — comments only, no functional test)
 *
 * @version 1.1.0
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
 * HELPERS
 * ============================================================================ */

static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

/* Raw brk syscall — returns new brk on success, or current brk on failure */
static long do_brk(long addr)
{
    return syscall1(SYS_BRK, addr);
}

/* ============================================================================
 * TEST 1: brk shrink (Fix #5)
 *
 * Grow the heap by N pages, write a pattern, then shrink.
 * After shrinking, grow again — the re-mapped pages should be zeroed
 * (because the old physical frames were freed and new ones allocated).
 * ============================================================================ */

static void test_brk_shrink(void)
{
    printf("\n--- Test: brk shrink ---\n");

    /* Get current brk */
    long base = do_brk(0);
    if (base <= 0) {
        TEST_FAIL("brk_shrink: get initial brk");
        return;
    }

    /* Grow by 4 pages */
    long grown = do_brk(base + 4 * 4096);
    if (grown != base + 4 * 4096) {
        TEST_FAIL("brk_shrink: grow 4 pages");
        return;
    }
    TEST_PASS("brk_shrink: grow 4 pages");

    /* Write pattern to pages 2-3 (the ones we'll shrink away) */
    unsigned char *p = (unsigned char *)(base + 2 * 4096);
    for (int i = 0; i < 2 * 4096; i++) {
        p[i] = 0xAB;
    }

    /* Shrink to 2 pages (freeing pages 2-3) */
    do_brk(base + 2 * 4096);
    /* Verify by querying current brk */
    long current = do_brk(0);
    if (current != base + 2 * 4096) {
        TEST_FAIL("brk_shrink: shrink to 2 pages");
        return;
    }
    TEST_PASS("brk_shrink: shrink to 2 pages");

    /* Grow back to 4 pages — new pages should be zero-filled */
    long regrown = do_brk(base + 4 * 4096);
    if (regrown != base + 4 * 4096) {
        TEST_FAIL("brk_shrink: regrow to 4 pages");
        return;
    }

    /* Check that the re-mapped pages are zeroed (not 0xAB) */
    int dirty_count = 0;
    p = (unsigned char *)(base + 2 * 4096);
    for (int i = 0; i < 2 * 4096; i++) {
        if (p[i] != 0) {
            dirty_count++;
        }
    }

    if (dirty_count == 0) {
        TEST_PASS("brk_shrink: re-mapped pages are zeroed");
    } else {
        printf("    (found %d non-zero bytes in re-mapped region)\n", dirty_count);
        TEST_FAIL("brk_shrink: re-mapped pages are zeroed");
    }

    /* Restore brk to original */
    do_brk(base);
}

/* ============================================================================
 * TEST 2: Page table leak (Fix #3)
 *
 * Fork child processes that allocate memory and exit.
 * If page tables leak, we'll eventually run out of physical memory
 * and fork() will fail. With proper cleanup, all forks succeed.
 * ============================================================================ */

static void test_page_table_leak(void)
{
    printf("\n--- Test: page table leak ---\n");

    int success_count = 0;
    int fail_count = 0;
    const int NUM_FORKS = 10;

    for (int i = 0; i < NUM_FORKS; i++) {
        pid_t pid = fork();
        if (pid < 0) {
            fail_count++;
            break;
        }
        if (pid == 0) {
            /* Child: allocate 8 pages of heap, touch them, then exit */
            long base = do_brk(0);
            long grown = do_brk(base + 8 * 4096);
            if (grown == base + 8 * 4096) {
                volatile unsigned char *mem = (volatile unsigned char *)base;
                for (int j = 0; j < 8 * 4096; j += 4096) {
                    mem[j] = (unsigned char)(i & 0xFF);
                }
            }
            exit(0);
        }

        /* Parent: wait for child */
        int status;
        waitpid(pid, &status, 0);
        if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
            success_count++;
        } else {
            fail_count++;
        }
    }

    if (success_count == NUM_FORKS && fail_count == 0) {
        TEST_PASS("pt_leak: 10 fork/alloc/exit cycles succeeded");
    } else {
        printf("    (success=%d, fail=%d out of %d)\n",
               success_count, fail_count, NUM_FORKS);
        TEST_FAIL("pt_leak: 10 fork/alloc/exit cycles succeeded");
    }

    /* Additional: fork 5 more to verify memory didn't degrade */
    int extra_ok = 0;
    for (int i = 0; i < 5; i++) {
        pid_t pid = fork();
        if (pid < 0) break;
        if (pid == 0) exit(0);
        int status;
        waitpid(pid, &status, 0);
        if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
            extra_ok++;
        }
    }

    if (extra_ok == 5) {
        TEST_PASS("pt_leak: 5 additional fork/exit (no degradation)");
    } else {
        printf("    (only %d / 5 extra forks succeeded)\n", extra_ok);
        TEST_FAIL("pt_leak: 5 additional fork/exit (no degradation)");
    }
}

/* ============================================================================
 * TEST 3: Scheduler responsiveness (Fix #1)
 *
 * Fork a child that immediately exits. Measure how long waitpid
 * takes to return — this tests the scheduler's ability to promptly
 * wake the parent when the child's state changes.
 * ============================================================================ */

static void test_sched_responsiveness(void)
{
    printf("\n--- Test: scheduler responsiveness ---\n");

    const int NUM_FORKS = 5;
    unsigned long long latencies[5];

    for (int i = 0; i < NUM_FORKS; i++) {
        unsigned long long t0 = rdtsc();
        pid_t pid = fork();

        if (pid < 0) {
            TEST_FAIL("sched: fork failed");
            return;
        }
        if (pid == 0) {
            /* Child: exit immediately */
            exit(42);
        }

        /* Parent: wait and measure */
        int status;
        waitpid(pid, &status, 0);
        unsigned long long t1 = rdtsc();

        latencies[i] = t1 - t0;

        if (!WIFEXITED(status) || WEXITSTATUS(status) != 42) {
            TEST_FAIL("sched: child exit status incorrect");
            return;
        }
    }

    /* Find min and max latencies */
    unsigned long long min_lat = latencies[0];
    unsigned long long max_lat = latencies[0];
    for (int i = 1; i < NUM_FORKS; i++) {
        if (latencies[i] < min_lat) min_lat = latencies[i];
        if (latencies[i] > max_lat) max_lat = latencies[i];
    }

    printf("    fork/exit/waitpid latencies (cycles): ");
    for (int i = 0; i < NUM_FORKS; i++) {
        printf("%llu ", latencies[i]);
    }
    printf("\n");

    /* Verify all iterations completed with correct exit code */
    TEST_PASS("sched: 5 fork/exit/waitpid cycles correct");

    /* Verify max latency is reasonable.
     * On QEMU at ~1 GHz, fork+exit+waitpid should be < 100M cycles.
     * If the scheduler has issues, this would be much higher. */
    if (max_lat < 500000000ULL) { /* 500M cycles = ~500ms */
        TEST_PASS("sched: latency within bounds");
    } else {
        printf("    (max %llu cycles exceeds 500M ceiling)\n", max_lat);
        TEST_FAIL("sched: latency within bounds");
    }
}

/* ============================================================================
 * TEST 4: Filesystem write integrity (Fix #4 — partial coverage)
 *
 * Write a file, read it back, verify contents match.
 * This exercises the disk_write_sector success path.
 * (Error injection paths require kernel-side fault injection.)
 * ============================================================================ */

static void test_fs_write_integrity(void)
{
    printf("\n--- Test: filesystem write integrity ---\n");

    const char *path = "/tmp/bugfix_test_file";
    const char *data = "VOS3 bugfix test data 0123456789ABCDEF";
    int len = 0;
    while (data[len]) len++;

    /* Write */
    int fd = open(path, 0x41, 0644); /* O_WRONLY | O_CREAT = 0x41 */
    if (fd < 0) {
        TEST_FAIL("fs_write: create test file");
        return;
    }
    long written = write(fd, data, len);
    close(fd);

    if (written != len) {
        printf("    (wrote %ld / %d bytes)\n", written, len);
        TEST_FAIL("fs_write: write correct number of bytes");
        return;
    }
    TEST_PASS("fs_write: write correct number of bytes");

    /* Read back */
    fd = open(path, 0, 0); /* O_RDONLY = 0 */
    if (fd < 0) {
        TEST_FAIL("fs_write: reopen test file");
        return;
    }
    char buf[128];
    for (int i = 0; i < 128; i++) buf[i] = 0;
    long rd = read(fd, buf, 127);
    close(fd);

    if (rd != len) {
        printf("    (read %ld / %d bytes)\n", rd, len);
        TEST_FAIL("fs_write: read back correct number of bytes");
        return;
    }
    TEST_PASS("fs_write: read back correct number of bytes");

    /* Verify */
    int mismatch = 0;
    for (int i = 0; i < len; i++) {
        if (buf[i] != data[i]) mismatch++;
    }
    if (mismatch == 0) {
        TEST_PASS("fs_write: data integrity verified");
    } else {
        printf("    (%d byte mismatches)\n", mismatch);
        TEST_FAIL("fs_write: data integrity verified");
    }

    /* Cleanup */
    unlink(path);
}

/* ============================================================================
 * TEST 5: Syscall ABI register preservation (Day 18 fix)
 *
 * Verifies that callee-saved registers (rbx, r12-r15) survive syscalls,
 * and that scratch registers (rdi, rsi, rdx) are restored (not zeroed)
 * so consecutive identical syscalls work correctly.
 * ============================================================================ */

static void test_syscall_abi_integrity(void)
{
    printf("\n--- Test: syscall ABI register integrity ---\n");

    /* --- Part A: Callee-saved registers (rbx, r12-r15) --- */

    uint64_t rbx_val  = 0xDEADBEEFCAFE0001ULL;
    uint64_t r12_val  = 0xDEADBEEFCAFE0012ULL;
    uint64_t r13_val  = 0xDEADBEEFCAFE0013ULL;
    uint64_t r14_val  = 0xDEADBEEFCAFE0014ULL;
    uint64_t r15_val  = 0xDEADBEEFCAFE0015ULL;

    uint64_t rbx_after, r12_after, r13_after, r14_after, r15_after;

    __asm__ volatile (
        /* Save rbx (GCC callee-saved, may be in use) */
        "pushq %%rbx\n\t"
        /* Load sentinel values */
        "movq %[bx],  %%rbx\n\t"
        "movq %[r12], %%r12\n\t"
        "movq %[r13], %%r13\n\t"
        "movq %[r14], %%r14\n\t"
        "movq %[r15], %%r15\n\t"
        /* Syscall: getpid (number 39) */
        "movq $39, %%rax\n\t"
        "syscall\n\t"
        /* Read registers back */
        "movq %%rbx, %[obx]\n\t"
        "movq %%r12, %[o12]\n\t"
        "movq %%r13, %[o13]\n\t"
        "movq %%r14, %[o14]\n\t"
        "movq %%r15, %[o15]\n\t"
        /* Restore rbx */
        "popq %%rbx\n\t"
        : [obx] "=m"(rbx_after), [o12] "=m"(r12_after),
          [o13] "=m"(r13_after), [o14] "=m"(r14_after),
          [o15] "=m"(r15_after)
        : [bx]  "m"(rbx_val),  [r12] "m"(r12_val),
          [r13] "m"(r13_val),  [r14] "m"(r14_val),
          [r15] "m"(r15_val)
        : "rax", "rcx", "r11", "rdi", "rsi", "rdx",
          "r8", "r9", "r10", "r12", "r13", "r14", "r15", "memory"
    );

    int callee_ok = (rbx_after == rbx_val && r12_after == r12_val &&
                     r13_after == r13_val && r14_after == r14_val &&
                     r15_after == r15_val);

    if (callee_ok) {
        TEST_PASS("abi: callee-saved regs preserved");
    } else {
        TEST_FAIL("abi: callee-saved regs preserved");
        if (rbx_after != rbx_val) printf("    RBX: expected 0x%lx, got 0x%lx\n", rbx_val, rbx_after);
        if (r12_after != r12_val) printf("    R12: expected 0x%lx, got 0x%lx\n", r12_val, r12_after);
        if (r13_after != r13_val) printf("    R13: expected 0x%lx, got 0x%lx\n", r13_val, r13_after);
        if (r14_after != r14_val) printf("    R14: expected 0x%lx, got 0x%lx\n", r14_val, r14_after);
        if (r15_after != r15_val) printf("    R15: expected 0x%lx, got 0x%lx\n", r15_val, r15_after);
    }

    /* --- Part B: Scratch register restoration (Day 18 fix) ---
     * Open the same file twice with syscall3. If the kernel zeroes
     * rdi/rsi/rdx on return, GCC reuses the stale zeroed values for
     * the second call, causing it to fail with EINVAL. */

    const char *path = "/proc/uptime";
    int fd1 = (int)syscall3(SYS_OPEN, (long)path, 0 /* O_RDONLY */, 0);
    int fd2 = (int)syscall3(SYS_OPEN, (long)path, 0 /* O_RDONLY */, 0);

    if (fd1 >= 0 && fd2 >= 0) {
        TEST_PASS("abi: scratch regs across consecutive syscalls");
    } else {
        printf("    fd1=%d fd2=%d (second should not fail)\n", fd1, fd2);
        TEST_FAIL("abi: scratch regs across consecutive syscalls");
    }
    if (fd1 >= 0) syscall1(SYS_CLOSE, fd1);
    if (fd2 >= 0) syscall1(SYS_CLOSE, fd2);
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    printf("\n");
    printf("============================================\n");
    printf("  VOS3 Kernel Bugfix Validation Tests\n");
    printf("============================================\n");

    test_brk_shrink();
    test_page_table_leak();
    test_sched_responsiveness();
    test_fs_write_integrity();
    test_syscall_abi_integrity();

    printf("\n");
    printf("============================================\n");
    printf("  Results: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);
    printf("============================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
