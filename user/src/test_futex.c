/**
 * @file test_futex.c
 * @brief VOS3 Task 1.5 — Futex / Mutex Tests
 *
 * @details Tests for:
 *   1. futex_wait_no_sleep: FUTEX_WAIT with mismatched value returns -EAGAIN
 *   2. futex_mutex: two threads synchronize via a simple spinmutex backed by
 *      FUTEX_WAIT / FUTEX_WAKE.  Verifies counter == 200 after both threads
 *      each increment it 100 times while holding the lock.
 *   3. futex_wake_no_waiter: FUTEX_WAKE with no waiters returns 0 (not an error)
 *
 * The mutex implementation intentionally uses atomic compare-and-swap to
 * acquire and FUTEX_WAIT to block, mirroring the real-world pattern used
 * by musl/glibc mutex implementations.
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
#include <stdint.h>

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
    printf("  [FAIL] " name "\n", ##__VA_ARGS__); \
    g_tests_failed++; \
} while (0)

/* ============================================================================
 * SYSCALL NUMBERS (Task 1.4 + 1.5)
 * ============================================================================ */

#define SYS_FUTEX           202
#define SYS_CLONE           56
#define SYS_EXIT            60

/* ============================================================================
 * FUTEX OPERATIONS
 * ============================================================================ */

#define FUTEX_WAIT          0
#define FUTEX_WAKE          1
#define FUTEX_PRIVATE_FLAG  0x80

/* Private futex (process-local): OR in FUTEX_PRIVATE_FLAG for efficiency */
#define FUTEX_WAIT_PRIVATE  (FUTEX_WAIT | FUTEX_PRIVATE_FLAG)
#define FUTEX_WAKE_PRIVATE  (FUTEX_WAKE | FUTEX_PRIVATE_FLAG)

/* ============================================================================
 * CLONE FLAGS (matching bench_thread_test.c)
 * ============================================================================ */

#define CLONE_VM        0x00000100UL
#define CLONE_FS        0x00000200UL
#define CLONE_FILES     0x00000400UL
#define CLONE_SIGHAND   0x00000800UL
#define CLONE_THREAD    0x00010000UL

/* ============================================================================
 * ERRNO VALUES
 * ============================================================================ */

#define EAGAIN  11

/* ============================================================================
 * FUTEX HELPERS
 * ============================================================================ */

/**
 * @brief futex_wait — block if *uaddr == val
 * @return 0 if woken, -EAGAIN if *uaddr != val
 */
static long futex_wait(volatile uint32_t* uaddr, uint32_t val)
{
    return syscall4(SYS_FUTEX, (long)uaddr, FUTEX_WAIT_PRIVATE, (long)val, 0L);
}

/**
 * @brief futex_wake — wake up to n waiters on uaddr
 * @return number of tasks woken
 */
static long futex_wake(volatile uint32_t* uaddr, int n)
{
    return syscall3(SYS_FUTEX, (long)uaddr, FUTEX_WAKE_PRIVATE, (long)n);
}

/* ============================================================================
 * SIMPLE FUTEX MUTEX
 *
 * State machine:
 *   0 = unlocked
 *   1 = locked, no waiters
 *   2 = locked, waiters present
 *
 * This is the standard "futex lock" pattern from Ulrich Drepper's paper.
 * ============================================================================ */

typedef volatile uint32_t futex_mutex_t;

#define FUTEX_MUTEX_INIT  0U   /* unlocked */

/**
 * @brief Atomic compare-and-swap (GCC builtin)
 */
static inline uint32_t cmpxchg32(volatile uint32_t* ptr, uint32_t expected, uint32_t desired)
{
    __atomic_compare_exchange_n(ptr, &expected, desired,
                                /*weak=*/0,
                                __ATOMIC_SEQ_CST,
                                __ATOMIC_SEQ_CST);
    return expected;  /* returns old value */
}

static inline uint32_t atomic_load32(volatile uint32_t* ptr)
{
    return __atomic_load_n(ptr, __ATOMIC_SEQ_CST);
}

static inline void atomic_store32(volatile uint32_t* ptr, uint32_t val)
{
    __atomic_store_n(ptr, val, __ATOMIC_SEQ_CST);
}

static inline uint32_t atomic_exchange32(volatile uint32_t* ptr, uint32_t val)
{
    return __atomic_exchange_n(ptr, val, __ATOMIC_SEQ_CST);
}

/**
 * @brief Lock the futex mutex (blocking)
 */
static void futex_lock(futex_mutex_t* m)
{
    /* Fast path: try to take unlocked → locked (no waiters) */
    uint32_t c = cmpxchg32(m, 0U, 1U);
    if (c == 0U) {
        return;  /* Acquired lock */
    }

    /* Slow path: set to 2 (locked with waiters) and sleep */
    do {
        if (c == 2U || cmpxchg32(m, 1U, 2U) != 0U) {
            futex_wait(m, 2U);
        }
        c = cmpxchg32(m, 0U, 2U);
    } while (c != 0U);
}

/**
 * @brief Unlock the futex mutex
 */
static void futex_unlock(futex_mutex_t* m)
{
    /* Atomically decrement from 1 or 2 to 0 */
    uint32_t prev = atomic_exchange32(m, 0U);
    if (prev == 2U) {
        /* There are waiters — wake one */
        futex_wake(m, 1);
    }
}

/* ============================================================================
 * TEST 1: futex_wait with mismatched value (should return -EAGAIN immediately)
 * ============================================================================ */

static void test_futex_wait_no_sleep(void)
{
    printf("\n--- Test: futex_wait_no_sleep ---\n");

    volatile uint32_t addr = 42U;

    /* Wait with val=99 but *addr=42: should return -EAGAIN without blocking */
    long ret = futex_wait(&addr, 99U);

    printf("  futex_wait(42, expect=99) returned %ld (expected -%d)\n", ret, EAGAIN);

    if (ret == -(long)EAGAIN) {
        TEST_PASS("futex_wait_no_sleep");
    } else {
        TEST_FAIL("futex_wait_no_sleep: expected -%d, got %ld", EAGAIN, ret);
    }
}

/* ============================================================================
 * TEST 2: futex_wake with no waiters (should return 0, not an error)
 * ============================================================================ */

static void test_futex_wake_no_waiter(void)
{
    printf("\n--- Test: futex_wake_no_waiter ---\n");

    volatile uint32_t addr = 0U;

    long ret = futex_wake(&addr, 1);

    printf("  futex_wake (no waiters) returned %ld (expected 0)\n", ret);

    if (ret == 0L) {
        TEST_PASS("futex_wake_no_waiter");
    } else {
        TEST_FAIL("futex_wake_no_waiter: expected 0, got %ld", ret);
    }
}

/* ============================================================================
 * TEST 3: futex mutex — two threads synchronize, counter == 200
 * ============================================================================ */

static futex_mutex_t g_mutex = FUTEX_MUTEX_INIT;
static volatile int  g_counter = 0;
static volatile int  g_thread_done = 0;

/* Thread stack for the child */
static char g_mutex_stack[65536] __attribute__((aligned(16)));

/**
 * Thread function: acquire mutex, increment counter 100 times, release.
 * Signals done and exits.
 */
static void thread_mutex_work(void)
{
    for (int i = 0; i < 100; i++) {
        futex_lock(&g_mutex);
        g_counter++;
        futex_unlock(&g_mutex);
    }

    /* Memory barrier before signaling done */
    __asm__ volatile("" ::: "memory");
    g_thread_done = 1;

    syscall1(SYS_EXIT, 0);
    __builtin_unreachable();
}

static void test_futex_mutex(void)
{
    printf("\n--- Test: futex_mutex ---\n");

    g_counter     = 0;
    g_thread_done = 0;
    atomic_store32(&g_mutex, FUTEX_MUTEX_INIT);

    /* Set up thread stack */
    unsigned long long* sp = (unsigned long long*)(g_mutex_stack + sizeof(g_mutex_stack));
    *(--sp) = 0ULL;  /* dummy return address */

    long flags = (long)(CLONE_VM | CLONE_FS | CLONE_FILES | CLONE_SIGHAND | CLONE_THREAD);

    long tid = syscall5(SYS_CLONE,
                        flags,
                        (long)sp,
                        0L, 0L, 0L);

    if (tid == 0) {
        /* === CHILD path === */
        thread_mutex_work();
        /* unreachable — thread_mutex_work calls SYS_EXIT */
        __builtin_unreachable();
    }

    if (tid < 0) {
        printf("  clone() failed: %ld\n", tid);
        TEST_FAIL("futex_mutex: clone failed");
        return;
    }

    printf("  Thread tid=%ld spawned, parent doing 100 increments...\n", tid);

    /* Parent also increments 100 times */
    for (int i = 0; i < 100; i++) {
        futex_lock(&g_mutex);
        g_counter++;
        futex_unlock(&g_mutex);
    }

    /* Wait for child to finish */
    unsigned int spin = 0;
    while (!g_thread_done) {
        __asm__ volatile("pause" ::: "memory");
        spin++;
        if (spin > 100000000U) {
            printf("  Timeout waiting for child thread!\n");
            TEST_FAIL("futex_mutex: timeout");
            return;
        }
    }

    printf("  counter=%d (expected 200)\n", g_counter);

    if (g_counter == 200) {
        TEST_PASS("futex_mutex");
    } else {
        TEST_FAIL("futex_mutex: counter=%d, expected 200", g_counter);
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("========================================\n");
    printf("  VOS3 Futex Tests (Task 1.5)\n");
    printf("========================================\n");

    test_futex_wait_no_sleep();
    test_futex_wake_no_waiter();
    test_futex_mutex();

    printf("\n========================================\n");
    printf("  RESULTS: %d PASS, %d FAIL\n",
           g_tests_passed, g_tests_failed);
    printf("========================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
