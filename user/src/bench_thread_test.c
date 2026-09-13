/**
 * @file bench_thread_test.c
 * @brief VOS3 Task 1.4 Threading Tests
 *
 * @details Tests POSIX thread creation via clone() syscall, TLS via
 *          arch_prctl(ARCH_SET_FS), and shared-memory counter increment.
 *
 * Tests:
 *   1. basic_clone_thread — create thread via clone(), increment shared counter
 *   2. arch_prctl_set_fs  — set %fs base via arch_prctl, read it back
 *   3. set_tid_address    — verify set_tid_address returns current TID
 *
 * @version 1.0.0
 * @date 2026-03-14
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
    printf("  [PASS] %s\n", (name)); \
    g_tests_passed++; \
} while (0)

#define TEST_FAIL(name, ...) do { \
    printf("  [FAIL] %s\n", (name)); \
    g_tests_failed++; \
} while (0)

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_CLONE           56
#define SYS_ARCH_PRCTL      158
#define SYS_SET_TID_ADDRESS 218

/* ============================================================================
 * CLONE FLAGS
 * ============================================================================ */

#define CLONE_VM        0x00000100UL
#define CLONE_FS        0x00000200UL
#define CLONE_FILES     0x00000400UL
#define CLONE_SIGHAND   0x00000800UL
#define CLONE_THREAD    0x00010000UL
#define CLONE_SETTLS    0x00080000UL

#define SIGCHLD         17UL

/* arch_prctl codes */
#define ARCH_SET_FS     0x1002
#define ARCH_GET_FS     0x1003

/* ============================================================================
 * TEST 1: basic_clone_thread
 *
 * Create a thread via raw clone(). Thread increments a shared counter
 * 1000 times, sets done flag, then calls exit.
 * Main thread busy-waits and verifies counter == 1000.
 * ============================================================================ */

static volatile int g_shared_counter = 0;
static volatile int g_thread_done    = 0;

/* Thread stack (BSS — zero-initialized, shared via CLONE_VM) */
static char g_thread_stack[65536] __attribute__((aligned(16)));

static void test_basic_clone_thread(void)
{
    printf("\n--- Test: basic_clone_thread ---\n");

    g_shared_counter = 0;
    g_thread_done    = 0;

    /* Set up thread stack: pre-decrement to 16n-8 alignment (ABI req) */
    unsigned long long* sp = (unsigned long long*)(g_thread_stack + sizeof(g_thread_stack));
    *(--sp) = 0ULL;  /* dummy return address — thread calls sys_exit, never uses it */

    long flags = (long)(CLONE_VM | CLONE_FS | CLONE_FILES | CLONE_SIGHAND | CLONE_THREAD);

    /*
     * Call clone().  Returns:
     *   Parent: child TID (> 0)
     *   Child:  0
     *
     * Both parent and child continue at the instruction after syscall5().
     * Child's RSP = sp (the thread stack we set up above).
     * Child's RAX = 0  → the if (tid == 0) branch is taken.
     */
    long tid = syscall5(SYS_CLONE,
                        flags,
                        (long)sp,
                        0L,      /* parent_tidptr — ignored */
                        0L,      /* child_tidptr  — ignored */
                        0L);     /* tls           — not used (no CLONE_SETTLS) */

    if (tid == 0) {
        /*
         * === CHILD / THREAD path ===
         * RSP = thread stack.  We MUST NOT return from this path.
         * All work must end with sys_exit.
         */
        for (int i = 0; i < 1000; i++) {
            g_shared_counter++;
        }
        /* Memory barrier: ensure the counter write is visible before done flag */
        __asm__ volatile("" ::: "memory");
        g_thread_done = 1;
        syscall1(SYS_EXIT, 0);
        __builtin_unreachable();
    }

    /* === PARENT path === */
    if (tid < 0) {
        printf("  clone() failed: %ld\n", tid);
        TEST_FAIL("basic_clone_thread");
        return;
    }

    printf("  Thread created, tid=%ld — waiting for completion...\n", tid);

    /* Busy-wait for thread to finish (no futex yet) */
    unsigned int spin = 0;
    while (!g_thread_done) {
        __asm__ volatile("pause" ::: "memory");
        spin++;
        if (spin > 50000000U) {
            printf("  Timeout waiting for thread!\n");
            TEST_FAIL("basic_clone_thread");
            return;
        }
    }

    /* Verify the counter */
    if (g_shared_counter == 1000) {
        printf("  counter=%d (expected 1000)\n", g_shared_counter);
        TEST_PASS("basic_clone_thread");
    } else {
        printf("  counter=%d (expected 1000)\n", g_shared_counter);
        TEST_FAIL("basic_clone_thread");
    }
}

/* ============================================================================
 * TEST 2: arch_prctl_set_fs
 *
 * Call arch_prctl(ARCH_SET_FS, addr) and verify the value via ARCH_GET_FS.
 * A real TLS region is a struct, but any non-zero address works for the test.
 * ============================================================================ */

static unsigned long long g_fake_tls_area[4] = { 0xDEADBEEF1337ULL, 0, 0, 0 };

static void test_arch_prctl_set_fs(void)
{
    printf("\n--- Test: arch_prctl_set_fs ---\n");

    unsigned long long tls_base = (unsigned long long)(uintptr_t)g_fake_tls_area;

    long ret = syscall2(SYS_ARCH_PRCTL, (long)ARCH_SET_FS, (long)tls_base);
    if (ret != 0) {
        printf("  ARCH_SET_FS failed: %ld\n", ret);
        TEST_FAIL("arch_prctl_set_fs");
        return;
    }

    /* Read it back */
    unsigned long long readback = 0;
    ret = syscall2(SYS_ARCH_PRCTL, (long)ARCH_GET_FS, (long)&readback);
    if (ret != 0) {
        printf("  ARCH_GET_FS failed: %ld\n", ret);
        TEST_FAIL("arch_prctl_set_fs");
        return;
    }

    if (readback == tls_base) {
        printf("  fsbase=0x%llx (correct)\n", readback);
        TEST_PASS("arch_prctl_set_fs");
    } else {
        printf("  fsbase=0x%llx expected=0x%llx\n", readback, tls_base);
        TEST_FAIL("arch_prctl_set_fs");
    }

    /* Reset to 0 to avoid affecting subsequent tests */
    syscall2(SYS_ARCH_PRCTL, (long)ARCH_SET_FS, 0L);
}

/* ============================================================================
 * TEST 3: set_tid_address
 *
 * Linux requires set_tid_address to return the current TID.
 * musl calls this during startup.
 * ============================================================================ */

static void test_set_tid_address(void)
{
    printf("\n--- Test: set_tid_address ---\n");

    static int s_ctid = 0;

    long my_tid   = syscall0(SYS_GETTID);
    long ret      = syscall1(SYS_SET_TID_ADDRESS, (long)&s_ctid);

    if (ret == my_tid) {
        printf("  set_tid_address returned tid=%ld (correct)\n", ret);
        TEST_PASS("set_tid_address");
    } else {
        printf("  set_tid_address returned %ld, expected %ld\n", ret, my_tid);
        TEST_FAIL("set_tid_address");
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("\n=== bench_thread_test ===\n");

    test_basic_clone_thread();
    test_arch_prctl_set_fs();
    test_set_tid_address();

    printf("\nResults: %d passed, %d failed\n", g_tests_passed, g_tests_failed);

    if (g_tests_failed == 0) {
        printf("[bench_thread_test] ALL PASS\n");
    } else {
        printf("[bench_thread_test] FAILURES DETECTED\n");
    }

    return (g_tests_failed == 0) ? 0 : 1;
}
