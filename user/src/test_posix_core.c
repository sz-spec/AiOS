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
#include "vos_sysinfo.h"

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


/* The versioned telemetry ABI must never write beyond its declared object.
 * A faulting cross-page copy may have written the accessible prefix. */
static int sysinfo_bytes_equal(const unsigned char *p, size_t n, unsigned char v)
{
    for (size_t i = 0; i < n; ++i) if (p[i] != v) return 0;
    return 1;
}

static void test_sysinfo_abi(void)
{
    struct {
        uint64_t before;
        vos3_sysinfo_t info;
        uint64_t after;
    } guarded;
    const uint64_t canary = UINT64_C(0xa5a5a5a5a5a5a5a5);
    const uint64_t sizes[] = {32, 0, 39, 41, UINT64_MAX};
    const uintptr_t invalid[] = {
        0, UINT64_C(0xffffffff80000000),
        UINT64_C(0x0000800000000000), UINT64_MAX - 15
    };
    unsigned char legacy[48];
    unsigned char *pages = NULL;
    const size_t page = 4096;
    int mapped_second = 1;
    int initial_failures = g_tests_failed;
    _Static_assert(sizeof(vos3_sysinfo_t) == 40, "telemetry ABI size");
    printf("\n--- Test: versioned sysinfo ABI ---\n");

    memset(&guarded, 0xa5, sizeof(guarded));
    if (vos3_get_sysinfo(&guarded.info) != 0 ||
        guarded.before != canary || guarded.after != canary ||
        guarded.info.total_pages == 0 ||
        guarded.info.free_pages > guarded.info.total_pages ||
        guarded.info.nr_tasks == 0 ||
        guarded.info.nr_zombies > guarded.info.nr_tasks ||
        guarded.info.hugepage_used > guarded.info.hugepage_total) {
        TEST_FAIL("sysinfo valid telemetry or object canaries");
    }
    for (size_t i = 0; i < sizeof(sizes)/sizeof(sizes[0]); ++i) {
        memset(&guarded, 0xa5, sizeof(guarded));
        long ret = syscall3(VOS3_SYSINFO_SYSCALL, (long)&guarded.info,
                            (long)sizes[i], VOS3_SYSINFO_VERSION);
        if (ret != -22 || !sysinfo_bytes_equal((unsigned char *)&guarded,
                                               sizeof(guarded), 0xa5))
            TEST_FAIL("sysinfo invalid size must reject without writes");
    }
    memset(&guarded, 0xa5, sizeof(guarded));
    if (syscall3(VOS3_SYSINFO_SYSCALL, (long)&guarded.info, 40,
                 VOS3_SYSINFO_VERSION + 1) != -22 ||
        !sysinfo_bytes_equal((unsigned char *)&guarded, sizeof(guarded), 0xa5))
        TEST_FAIL("sysinfo invalid version must reject without writes");
    /* A legacy 32-byte object with an eight-byte guard on each side. */
    memset(legacy, 0xa5, sizeof(legacy));
    if (syscall1(99, (long)(legacy + 8)) != -38 ||
        !sysinfo_bytes_equal(legacy, sizeof(legacy), 0xa5))
        TEST_FAIL("legacy Linux sysinfo must return ENOSYS without writes");
    for (size_t i = 0; i < sizeof(invalid)/sizeof(invalid[0]); ++i)
        if (syscall3(VOS3_SYSINFO_SYSCALL, (long)invalid[i], 40,
                     VOS3_SYSINFO_VERSION) != -14)
            TEST_FAIL("sysinfo invalid pointer must return EFAULT");

    long mapping = syscall6(9, 0, 2 * page, 3, 0x22, -1, 0);
    if ((unsigned long)mapping >= (unsigned long)-4095 || mapping == 0) {
        TEST_FAIL("sysinfo page test mmap failed");
        goto live;
    }
    pages = (unsigned char *)(uintptr_t)mapping;
    /* No user touch first: copy_to_user must populate writable lazy pages. */
    if (syscall3(VOS3_SYSINFO_SYSCALL, (long)(pages + page - 16), 40,
                 VOS3_SYSINFO_VERSION) != 0)
        TEST_FAIL("sysinfo lazy writable crossing failed");
    memset(pages, 0xa5, 2 * page);
    if (syscall3(VOS3_SYSINFO_SYSCALL, (long)(pages + page - 16), 40,
                 VOS3_SYSINFO_VERSION) != 0 ||
        !sysinfo_bytes_equal(pages, page - 16, 0xa5) ||
        !sysinfo_bytes_equal(pages + page + 24, page - 24, 0xa5))
        TEST_FAIL("sysinfo writable page crossing or exterior canaries");
    memset(pages, 0xa5, 2 * page);
    if (syscall3(10, (long)(pages + page), page, 1) != 0) {
        TEST_FAIL("sysinfo read-only page setup failed");
        goto cleanup;
    }
    if (syscall3(VOS3_SYSINFO_SYSCALL, (long)(pages + page - 16), 40,
                 VOS3_SYSINFO_VERSION) != -14 ||
        !sysinfo_bytes_equal(pages, page - 16, 0xa5) ||
        !sysinfo_bytes_equal(pages + page, page, 0xa5))
        TEST_FAIL("sysinfo read-only crossing must fault without protected writes");
    if (syscall3(10, (long)(pages + page), page, 0) != 0) {
        TEST_FAIL("sysinfo PROT_NONE page setup failed");
        goto cleanup;
    }
    memset(pages, 0xa5, page);
    if (syscall3(VOS3_SYSINFO_SYSCALL, (long)(pages + page - 16), 40,
                 VOS3_SYSINFO_VERSION) != -14 ||
        !sysinfo_bytes_equal(pages, page - 16, 0xa5))
        TEST_FAIL("sysinfo PROT_NONE crossing must return EFAULT");
    if (syscall3(10, (long)(pages + page), page, 1) != 0) {
        TEST_FAIL("sysinfo PROT_NONE inspection restore failed");
        goto cleanup;
    }
    if (!sysinfo_bytes_equal(pages + page, page, 0xa5))
        TEST_FAIL("sysinfo PROT_NONE page was modified");
    if (syscall2(11, (long)(pages + page), page) != 0) {
        TEST_FAIL("sysinfo unmapped page setup failed");
        goto cleanup;
    }
    mapped_second = 0;
    memset(pages, 0xa5, page);
    if (syscall3(VOS3_SYSINFO_SYSCALL, (long)(pages + page - 16), 40,
                 VOS3_SYSINFO_VERSION) != -14 ||
        !sysinfo_bytes_equal(pages, page - 16, 0xa5))
        TEST_FAIL("sysinfo unmapped crossing must return EFAULT");
cleanup:
    if (syscall2(11, (long)pages, mapped_second ? 2 * page : page) != 0)
        TEST_FAIL("sysinfo mapping cleanup failed");
live:
    memset(&guarded, 0xa5, sizeof(guarded));
    if (vos3_get_sysinfo(&guarded.info) != 0 || guarded.before != canary ||
        guarded.after != canary || guarded.info.total_pages == 0)
        TEST_FAIL("sysinfo final liveness or canaries");
    if (g_tests_failed == initial_failures) TEST_PASS("sysinfo_versioned_abi");
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
    test_sysinfo_abi();

    printf("\n========================================\n");
    printf("  RESULTS: %d PASS, %d FAIL\n",
           g_tests_passed, g_tests_failed);
    printf("========================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
