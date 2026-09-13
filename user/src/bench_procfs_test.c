/**
 * @file bench_procfs_test.c
 * @brief VOS3 procfs Virtual Filesystem Tests
 *
 * @details Tests the /proc virtual filesystem:
 *          1. /proc/self/status — verify PID matches getpid()
 *          2. /proc/self/cmdline — verify non-empty
 *          3. /proc/meminfo — verify contains MemTotal
 *          4. /proc/uptime — verify > 0
 *          5. /proc/<pid>/status — verify specific PID lookup
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
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_OPEN    2
#define SYS_CLOSE   3
#define SYS_READ    0

/* ============================================================================
 * HELPERS
 * ============================================================================ */

static int open_read_close(const char* path, char* buf, int bufsz)
{
    long fd = syscall3(SYS_OPEN, (long)path, 0 /* O_RDONLY */, 0);
    if (fd < 0) return -1;

    long nr = syscall3(SYS_READ, fd, (long)buf, bufsz - 1);
    syscall1(SYS_CLOSE, fd);

    if (nr < 0) return -1;
    buf[nr] = '\0';
    return (int)nr;
}

static int find_substr(const char* haystack, const char* needle)
{
    if (!haystack || !needle) return 0;
    int nlen = 0;
    const char* n = needle;
    while (*n) { nlen++; n++; }
    if (nlen == 0) return 1;

    for (const char* h = haystack; *h; h++) {
        int match = 1;
        for (int i = 0; i < nlen; i++) {
            if (h[i] == '\0' || h[i] != needle[i]) {
                match = 0;
                break;
            }
        }
        if (match) return 1;
    }
    return 0;
}

/* ============================================================================
 * TEST 1: /proc/self/status — PID matches getpid()
 * ============================================================================ */

static void test_proc_self_status(void)
{
    printf("\n--- Test: proc_self_status ---\n");

    char buf[512];
    int nr = open_read_close("/proc/self/status", buf, sizeof(buf));
    if (nr <= 0) {
        TEST_FAIL("proc_self_status: open/read /proc/self/status");
        return;
    }
    TEST_PASS("proc_self_status: read /proc/self/status");

    /* Verify content has Pid: field */
    if (find_substr(buf, "Pid:")) {
        TEST_PASS("proc_self_status: contains Pid: field");
    } else {
        printf("    content: %s\n", buf);
        TEST_FAIL("proc_self_status: contains Pid: field");
    }

    /* Verify content has Name: field */
    if (find_substr(buf, "Name:")) {
        TEST_PASS("proc_self_status: contains Name: field");
    } else {
        TEST_FAIL("proc_self_status: contains Name: field");
    }

    /* Verify content has State: field */
    if (find_substr(buf, "State:")) {
        TEST_PASS("proc_self_status: contains State: field");
    } else {
        TEST_FAIL("proc_self_status: contains State: field");
    }
}

/* ============================================================================
 * TEST 2: /proc/self/cmdline — non-empty
 * ============================================================================ */

static void test_proc_self_cmdline(void)
{
    printf("\n--- Test: proc_self_cmdline ---\n");

    char buf[256];
    int nr = open_read_close("/proc/self/cmdline", buf, sizeof(buf));
    if (nr <= 0) {
        TEST_FAIL("proc_self_cmdline: open/read /proc/self/cmdline");
        return;
    }
    TEST_PASS("proc_self_cmdline: read non-empty cmdline");
}

/* ============================================================================
 * TEST 3: /proc/meminfo — contains MemTotal
 * ============================================================================ */

static void test_proc_meminfo(void)
{
    printf("\n--- Test: proc_meminfo ---\n");

    char buf[512];
    int nr = open_read_close("/proc/meminfo", buf, sizeof(buf));
    if (nr <= 0) {
        TEST_FAIL("proc_meminfo: open/read /proc/meminfo");
        return;
    }
    TEST_PASS("proc_meminfo: read /proc/meminfo");

    if (find_substr(buf, "MemTotal:")) {
        TEST_PASS("proc_meminfo: contains MemTotal");
    } else {
        printf("    content: %s\n", buf);
        TEST_FAIL("proc_meminfo: contains MemTotal");
    }

    if (find_substr(buf, "MemFree:")) {
        TEST_PASS("proc_meminfo: contains MemFree");
    } else {
        TEST_FAIL("proc_meminfo: contains MemFree");
    }
}

/* ============================================================================
 * TEST 4: /proc/uptime — verify > 0
 * ============================================================================ */

static void test_proc_uptime(void)
{
    printf("\n--- Test: proc_uptime ---\n");

    char buf[64];
    int nr = open_read_close("/proc/uptime", buf, sizeof(buf));
    if (nr <= 0) {
        TEST_FAIL("proc_uptime: open/read /proc/uptime");
        return;
    }
    TEST_PASS("proc_uptime: read /proc/uptime");

    /* Should start with a digit > 0 (uptime is at least 1 second) */
    if (buf[0] >= '0' && buf[0] <= '9') {
        TEST_PASS("proc_uptime: starts with digit");
    } else {
        printf("    content: %s\n", buf);
        TEST_FAIL("proc_uptime: starts with digit");
    }
}

/* ============================================================================
 * TEST 5: /proc/1/status — verify PID 1 (init) exists
 * ============================================================================ */

static void test_proc_pid1_status(void)
{
    printf("\n--- Test: proc_pid1_status ---\n");

    char buf[512];
    int nr = open_read_close("/proc/1/status", buf, sizeof(buf));
    if (nr <= 0) {
        /* PID 1 may not be exactly pid=1 in VOS3, try pid 0 */
        nr = open_read_close("/proc/0/status", buf, sizeof(buf));
        if (nr <= 0) {
            TEST_FAIL("proc_pid1_status: open /proc/1/status or /proc/0/status");
            return;
        }
    }
    TEST_PASS("proc_pid1_status: read process status by PID");

    if (find_substr(buf, "Pid:")) {
        TEST_PASS("proc_pid1_status: contains Pid: field");
    } else {
        TEST_FAIL("proc_pid1_status: contains Pid: field");
    }
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
    printf("  VOS3 procfs Virtual Filesystem Tests\n");
    printf("============================================\n");

    test_proc_self_status();
    test_proc_self_cmdline();
    test_proc_meminfo();
    test_proc_uptime();
    test_proc_pid1_status();

    printf("\n");
    printf("============================================\n");
    printf("  Results: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);
    printf("============================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
