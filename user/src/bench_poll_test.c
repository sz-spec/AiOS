/**
 * @file bench_poll_test.c
 * @brief VOS3 poll() Syscall Tests
 *
 * @details Tests the poll() system call:
 *          1. poll() on pipe — verify readable after write
 *          2. poll() with timeout 0 — immediate return
 *          3. poll() on regular file — verify readable
 *          4. poll() with invalid fd — verify POLLNVAL
 *          5. poll() on pipe write end — verify writable
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
 * POLL CONSTANTS AND STRUCTURES
 * ============================================================================ */

#define POLLIN      0x0001
#define POLLOUT     0x0004
#define POLLERR     0x0008
#define POLLHUP     0x0010
#define POLLNVAL    0x0020

struct pollfd {
    int     fd;
    short   events;
    short   revents;
};

/* ============================================================================
 * TEST 1: poll() on pipe — readable after write
 * ============================================================================ */

static void test_poll_pipe_readable(void)
{
    printf("\n--- Test: poll_pipe_readable ---\n");

    int pfd[2];
    long rc = syscall1(SYS_PIPE, (long)pfd);
    if (rc < 0) {
        TEST_FAIL("poll_pipe_readable: pipe() failed");
        return;
    }

    /* Write some data to the pipe */
    const char* msg = "hello";
    syscall3(SYS_WRITE, pfd[1], (long)msg, 5);

    /* Poll the read end for POLLIN */
    struct pollfd fds;
    fds.fd = pfd[0];
    fds.events = POLLIN;
    fds.revents = 0;

    long nready = syscall3(SYS_POLL, (long)&fds, 1, 0);
    if (nready == 1 && (fds.revents & POLLIN)) {
        TEST_PASS("poll_pipe_readable: POLLIN after write");
    } else {
        printf("    nready=%ld revents=0x%x\n", nready, fds.revents);
        TEST_FAIL("poll_pipe_readable: POLLIN after write");
    }

    syscall1(SYS_CLOSE, pfd[0]);
    syscall1(SYS_CLOSE, pfd[1]);
}

/* ============================================================================
 * TEST 2: poll() with timeout 0 — immediate return on empty pipe
 * ============================================================================ */

static void test_poll_timeout_zero(void)
{
    printf("\n--- Test: poll_timeout_zero ---\n");

    int pfd[2];
    long rc = syscall1(SYS_PIPE, (long)pfd);
    if (rc < 0) {
        TEST_FAIL("poll_timeout_zero: pipe() failed");
        return;
    }

    /* Poll empty pipe read end — should return 0 immediately */
    struct pollfd fds;
    fds.fd = pfd[0];
    fds.events = POLLIN;
    fds.revents = 0;

    long nready = syscall3(SYS_POLL, (long)&fds, 1, 0);
    if (nready == 0) {
        TEST_PASS("poll_timeout_zero: returns 0 on empty pipe");
    } else {
        printf("    nready=%ld revents=0x%x\n", nready, fds.revents);
        TEST_FAIL("poll_timeout_zero: returns 0 on empty pipe");
    }

    syscall1(SYS_CLOSE, pfd[0]);
    syscall1(SYS_CLOSE, pfd[1]);
}

/* ============================================================================
 * TEST 3: poll() on regular file — always readable
 * ============================================================================ */

static void test_poll_regular_file(void)
{
    printf("\n--- Test: poll_regular_file ---\n");

    /* Open /proc/uptime — a virtual file that's always readable */
    long fd = syscall3(SYS_OPEN, (long)"/proc/uptime", 0, 0);
    if (fd < 0) {
        TEST_FAIL("poll_regular_file: open /proc/uptime");
        return;
    }

    struct pollfd fds;
    fds.fd = (int)fd;
    fds.events = POLLIN;
    fds.revents = 0;

    long nready = syscall3(SYS_POLL, (long)&fds, 1, 0);
    if (nready == 1 && (fds.revents & POLLIN)) {
        TEST_PASS("poll_regular_file: POLLIN on /proc/uptime");
    } else {
        printf("    nready=%ld revents=0x%x\n", nready, fds.revents);
        TEST_FAIL("poll_regular_file: POLLIN on /proc/uptime");
    }

    syscall1(SYS_CLOSE, fd);
}

/* ============================================================================
 * TEST 4: poll() with invalid fd — POLLNVAL
 * ============================================================================ */

static void test_poll_invalid_fd(void)
{
    printf("\n--- Test: poll_invalid_fd ---\n");

    struct pollfd fds;
    fds.fd = 999;  /* Invalid fd */
    fds.events = POLLIN;
    fds.revents = 0;

    long nready = syscall3(SYS_POLL, (long)&fds, 1, 0);
    if (nready == 1 && (fds.revents & POLLNVAL)) {
        TEST_PASS("poll_invalid_fd: POLLNVAL on bad fd");
    } else {
        printf("    nready=%ld revents=0x%x\n", nready, fds.revents);
        TEST_FAIL("poll_invalid_fd: POLLNVAL on bad fd");
    }
}

/* ============================================================================
 * TEST 5: poll() on pipe write end — writable
 * ============================================================================ */

static void test_poll_pipe_writable(void)
{
    printf("\n--- Test: poll_pipe_writable ---\n");

    int pfd[2];
    long rc = syscall1(SYS_PIPE, (long)pfd);
    if (rc < 0) {
        TEST_FAIL("poll_pipe_writable: pipe() failed");
        return;
    }

    /* Poll write end for POLLOUT */
    struct pollfd fds;
    fds.fd = pfd[1];
    fds.events = POLLOUT;
    fds.revents = 0;

    long nready = syscall3(SYS_POLL, (long)&fds, 1, 0);
    if (nready == 1 && (fds.revents & POLLOUT)) {
        TEST_PASS("poll_pipe_writable: POLLOUT on pipe write end");
    } else {
        printf("    nready=%ld revents=0x%x\n", nready, fds.revents);
        TEST_FAIL("poll_pipe_writable: POLLOUT on pipe write end");
    }

    syscall1(SYS_CLOSE, pfd[0]);
    syscall1(SYS_CLOSE, pfd[1]);
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
    printf("  VOS3 poll() Syscall Tests\n");
    printf("============================================\n");

    test_poll_pipe_readable();
    test_poll_timeout_zero();
    test_poll_regular_file();
    test_poll_invalid_fd();
    test_poll_pipe_writable();

    printf("\n");
    printf("============================================\n");
    printf("  Results: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);
    printf("============================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
