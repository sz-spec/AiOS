/**
 * @file test_posix_core.c
 * @brief VOS3 Task 1.5 — POSIX Core Syscall Tests
 *
 * @details Tests for:
 *   1. uname: sysname == "VOS3", machine == "x86_64"
 *   2. getrlimit: RLIMIT_NOFILE soft limit == 1024
 *   3. setrlimit: lower RLIMIT_CORE, read back, restore
 *   4. getrusage: does not crash, returns sane utime
 *   5. getitimer: returns zeros (no active timer)
 *   6. setitimer: install timer, read back positive remaining time
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
 * SYSCALL NUMBERS (Task 1.5)
 * ============================================================================ */

#define SYS_UNAME       63
#define SYS_GETRUSAGE   98
#define SYS_GETRLIMIT   97
#define SYS_SETRLIMIT   160
#define SYS_GETITIMER   36
#define SYS_SETITIMER   38

/* ============================================================================
 * TYPE DEFINITIONS (match kernel ABI exactly)
 * ============================================================================ */

/** struct utsname — Linux ABI (65-byte fields) */
typedef struct {
    char sysname[65];
    char nodename[65];
    char release[65];
    char version[65];
    char machine[65];
    char domainname[65];
} posix_utsname_t;

/** struct timeval */
typedef struct {
    long tv_sec;
    long tv_usec;
} posix_timeval_t;

/** struct rusage */
typedef struct {
    posix_timeval_t ru_utime;
    posix_timeval_t ru_stime;
    long ru_maxrss;
    long ru_ixrss;
    long ru_idrss;
    long ru_isrss;
    long ru_minflt;
    long ru_majflt;
    long ru_nswap;
    long ru_inblock;
    long ru_oublock;
    long ru_msgsnd;
    long ru_msgrcv;
    long ru_nsignals;
    long ru_nvcsw;
    long ru_nivcsw;
} posix_rusage_t;

/** struct rlimit */
typedef struct {
    unsigned long rlim_cur;
    unsigned long rlim_max;
} posix_rlimit_t;

/** struct itimerval */
typedef struct {
    posix_timeval_t it_interval;
    posix_timeval_t it_value;
} posix_itimerval_t;

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define RUSAGE_SELF     0
#define RLIMIT_CORE     4
#define RLIMIT_NOFILE   7
#define ITIMER_REAL     0

/* ============================================================================
 * TEST 1: uname
 * ============================================================================ */

static void test_uname(void)
{
    printf("\n--- Test: uname ---\n");

    posix_utsname_t uts;
    memset(&uts, 0, sizeof(uts));

    long ret = syscall1(SYS_UNAME, (long)&uts);
    if (ret != 0) {
        TEST_FAIL("uname: syscall returned %ld", ret);
        return;
    }

    printf("  sysname='%s' nodename='%s' machine='%s'\n",
           uts.sysname, uts.nodename, uts.machine);

    if (strcmp(uts.sysname, "VOS3") != 0) {
        TEST_FAIL("uname: sysname expected 'VOS3', got '%s'", uts.sysname);
        return;
    }
    if (strcmp(uts.machine, "x86_64") != 0) {
        TEST_FAIL("uname: machine expected 'x86_64', got '%s'", uts.machine);
        return;
    }

    TEST_PASS("uname");
}

/* ============================================================================
 * TEST 2: getrlimit
 * ============================================================================ */

static void test_getrlimit(void)
{
    printf("\n--- Test: getrlimit ---\n");

    posix_rlimit_t rl;
    memset(&rl, 0, sizeof(rl));

    long ret = syscall2(SYS_GETRLIMIT, RLIMIT_NOFILE, (long)&rl);
    if (ret != 0) {
        TEST_FAIL("getrlimit: syscall returned %ld", ret);
        return;
    }

    printf("  RLIMIT_NOFILE: soft=%lu hard=%lu\n", rl.rlim_cur, rl.rlim_max);

    if (rl.rlim_cur != 1024UL) {
        TEST_FAIL("getrlimit: NOFILE soft expected 1024, got %lu", rl.rlim_cur);
        return;
    }

    TEST_PASS("getrlimit");
}

/* ============================================================================
 * TEST 3: setrlimit
 * ============================================================================ */

static void test_setrlimit(void)
{
    printf("\n--- Test: setrlimit ---\n");

    /* Read original RLIMIT_CORE */
    posix_rlimit_t orig;
    memset(&orig, 0, sizeof(orig));
    long ret = syscall2(SYS_GETRLIMIT, RLIMIT_CORE, (long)&orig);
    if (ret != 0) {
        TEST_FAIL("setrlimit: getrlimit failed: %ld", ret);
        return;
    }

    /* Set RLIMIT_CORE to {0, 0} */
    posix_rlimit_t zero = { 0UL, 0UL };
    ret = syscall2(SYS_SETRLIMIT, RLIMIT_CORE, (long)&zero);
    if (ret != 0) {
        TEST_FAIL("setrlimit: setrlimit(CORE,0) returned %ld", ret);
        return;
    }

    /* Read it back */
    posix_rlimit_t check;
    memset(&check, 0xFF, sizeof(check));
    ret = syscall2(SYS_GETRLIMIT, RLIMIT_CORE, (long)&check);
    if (ret != 0) {
        TEST_FAIL("setrlimit: getrlimit after set returned %ld", ret);
        return;
    }

    if (check.rlim_cur != 0UL) {
        TEST_FAIL("setrlimit: CORE soft expected 0, got %lu", check.rlim_cur);
        return;
    }

    /* Restore original */
    syscall2(SYS_SETRLIMIT, RLIMIT_CORE, (long)&orig);

    printf("  RLIMIT_CORE: set to 0, read back 0\n");
    TEST_PASS("setrlimit");
}

/* ============================================================================
 * TEST 4: getrusage
 * ============================================================================ */

static void test_getrusage(void)
{
    printf("\n--- Test: getrusage ---\n");

    posix_rusage_t ru;
    memset(&ru, 0xFF, sizeof(ru));  /* pre-fill with garbage to detect zeroing */

    long ret = syscall2(SYS_GETRUSAGE, RUSAGE_SELF, (long)&ru);
    if (ret != 0) {
        TEST_FAIL("getrusage: syscall returned %ld", ret);
        return;
    }

    /* utime.tv_usec must be in [0, 999999] */
    if (ru.ru_utime.tv_usec < 0 || ru.ru_utime.tv_usec > 999999L) {
        TEST_FAIL("getrusage: tv_usec out of range: %ld", ru.ru_utime.tv_usec);
        return;
    }

    /* stime should be zero (VOS3 doesn't track kernel time separately) */
    if (ru.ru_stime.tv_sec != 0 || ru.ru_stime.tv_usec != 0) {
        TEST_FAIL("getrusage: stime expected 0, got %ld.%ld",
                  ru.ru_stime.tv_sec, ru.ru_stime.tv_usec);
        return;
    }

    printf("  utime=%ld.%06ld nvcsw=%ld nivcsw=%ld\n",
           ru.ru_utime.tv_sec, ru.ru_utime.tv_usec,
           ru.ru_nvcsw, ru.ru_nivcsw);

    TEST_PASS("getrusage");
}

/* ============================================================================
 * TEST 5: getitimer (no active timer → zeros)
 * ============================================================================ */

static void test_getitimer_empty(void)
{
    printf("\n--- Test: getitimer (no active timer) ---\n");

    posix_itimerval_t itv;
    memset(&itv, 0xFF, sizeof(itv));

    long ret = syscall2(SYS_GETITIMER, ITIMER_REAL, (long)&itv);
    if (ret != 0) {
        TEST_FAIL("getitimer: syscall returned %ld", ret);
        return;
    }

    if (itv.it_value.tv_sec != 0 || itv.it_value.tv_usec != 0 ||
        itv.it_interval.tv_sec != 0 || itv.it_interval.tv_usec != 0) {
        TEST_FAIL("getitimer: expected all zeros, got value=%ld.%ld interval=%ld.%ld",
                  itv.it_value.tv_sec, itv.it_value.tv_usec,
                  itv.it_interval.tv_sec, itv.it_interval.tv_usec);
        return;
    }

    printf("  No active timer: all fields zero\n");
    TEST_PASS("getitimer_empty");
}

/* ============================================================================
 * TEST 6: setitimer + getitimer (install timer, read back)
 * ============================================================================ */

static void test_setitimer(void)
{
    printf("\n--- Test: setitimer ---\n");

    /* Install a 5-second one-shot timer */
    posix_itimerval_t set_itv;
    set_itv.it_value.tv_sec  = 5L;
    set_itv.it_value.tv_usec = 0L;
    set_itv.it_interval.tv_sec  = 0L;
    set_itv.it_interval.tv_usec = 0L;

    long ret = syscall3(SYS_SETITIMER, ITIMER_REAL, (long)&set_itv, 0L);
    if (ret != 0) {
        TEST_FAIL("setitimer: set returned %ld", ret);
        return;
    }

    /* Immediately read back — remaining should be <= 5s and > 0 */
    posix_itimerval_t get_itv;
    memset(&get_itv, 0, sizeof(get_itv));

    ret = syscall2(SYS_GETITIMER, ITIMER_REAL, (long)&get_itv);
    if (ret != 0) {
        TEST_FAIL("setitimer: getitimer after set returned %ld", ret);
        goto cancel;
    }

    printf("  After set: remaining=%ld.%06ld interval=%ld.%06ld\n",
           get_itv.it_value.tv_sec, get_itv.it_value.tv_usec,
           get_itv.it_interval.tv_sec, get_itv.it_interval.tv_usec);

    if (get_itv.it_value.tv_sec < 0 || get_itv.it_value.tv_sec > 5L) {
        TEST_FAIL("setitimer: remaining time %ld not in [0,5]", get_itv.it_value.tv_sec);
        goto cancel;
    }

    TEST_PASS("setitimer");

cancel: ;
    /* Cancel the timer (semicolon after label required before declaration in C11) */
    posix_itimerval_t cancel_itv;
    cancel_itv.it_value.tv_sec  = 0L;
    cancel_itv.it_value.tv_usec = 0L;
    cancel_itv.it_interval.tv_sec  = 0L;
    cancel_itv.it_interval.tv_usec = 0L;
    syscall3(SYS_SETITIMER, ITIMER_REAL, (long)&cancel_itv, 0L);
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("========================================\n");
    printf("  VOS3 POSIX Core Syscall Tests (1.5)\n");
    printf("========================================\n");

    test_uname();
    test_getrlimit();
    test_setrlimit();
    test_getrusage();
    test_getitimer_empty();
    test_setitimer();

    printf("\n========================================\n");
    printf("  RESULTS: %d PASS, %d FAIL\n",
           g_tests_passed, g_tests_failed);
    printf("========================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
