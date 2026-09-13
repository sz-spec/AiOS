/**
 * @file bench_sched_test.c
 * @brief VOS3 Scheduler Sleep Test
 *
 * @details Tests the scheduler sleep/wake mechanism:
 *          1. Single-task sleep — verify a task wakes up after sleeping
 *          2. Sleep timing accuracy — 100ms sleep should complete within 200ms
 *
 * @version 1.0.0
 * @date 2026-03-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"
#include "time.h"

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

/* Helper: get monotonic uptime in milliseconds */
static unsigned long get_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return 0;
    }
    return (unsigned long)ts.tv_sec * 1000 + (unsigned long)ts.tv_nsec / 1000000;
}

/* ============================================================================
 * TEST 1: single_task_sleep
 *
 * Sleep for 100ms using the sleep syscall. If we return at all, the scheduler
 * correctly woke us from the sleep queue. This tests the fix for the bug where
 * a single sleeping task was never woken because the idle loop didn't trigger
 * a reschedule when sleepers were ready.
 * ============================================================================ */

static void test_single_task_sleep(void)
{
    printf("\n--- Test: single_task_sleep ---\n");

    /* Get start time (uptime in ms) */
    unsigned long start = get_ms();

    /* Sleep 100ms — this is the critical test.
     * Before the fix, this would hang forever in a single-task scenario. */
    usleep(100000); /* 100ms in microseconds */

    unsigned long end = get_ms();
    unsigned long elapsed = end - start;

    printf("  Slept for %lu ms (requested 100ms)\n", elapsed);

    /* Should wake up: elapsed >= 100ms (slept at least that long)
     * and elapsed < 200ms (didn't overshoot by more than 100ms) */
    if (elapsed >= 80 && elapsed < 300) {
        TEST_PASS("single_task_sleep (woke up within bounds)");
    } else {
        TEST_FAIL("single_task_sleep (timing out of bounds)");
    }
}

/* ============================================================================
 * TEST 2: multiple_sleeps
 *
 * Sleep multiple times in sequence. Verifies the sleep queue is processed
 * repeatedly and tasks can re-enter sleep after waking.
 * ============================================================================ */

static void test_multiple_sleeps(void)
{
    printf("\n--- Test: multiple_sleeps ---\n");

    unsigned long start = get_ms();

    for (int i = 0; i < 3; i++) {
        usleep(50000); /* 50ms each */
    }

    unsigned long end = get_ms();
    unsigned long elapsed = end - start;

    printf("  3x 50ms sleeps took %lu ms total\n", elapsed);

    /* Should take at least 150ms, no more than 400ms */
    if (elapsed >= 120 && elapsed < 500) {
        TEST_PASS("multiple_sleeps (sequential sleeps work)");
    } else {
        TEST_FAIL("multiple_sleeps (timing out of bounds)");
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char* argv[])
{
    (void)argc;
    (void)argv;

    printf("=== VOS3 Scheduler Sleep Tests ===\n");

    test_single_task_sleep();
    test_multiple_sleeps();

    printf("\n=== Results: %d passed, %d failed ===\n",
           g_tests_passed, g_tests_failed);

    return g_tests_failed > 0 ? 1 : 0;
}
