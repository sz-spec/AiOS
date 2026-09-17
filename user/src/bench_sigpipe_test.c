/**
 * @file bench_sigpipe_test.c
 * @brief VOS3 SIGPIPE Validation Test
 *
 * @details Creates a pipe, closes the read end, writes to the
 *          write end, and verifies that SIGPIPE is delivered
 *          (default action = terminate process).
 *
 *          Test cases:
 *          1. Write to pipe with closed read end -> should get SIGPIPE
 *          2. Signal handler for SIGPIPE -> should catch it
 *          3. Write returns EPIPE (-32) after SIGPIPE handled
 *
 * @version 1.0.0
 * @date 2026-02-25
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"
#include "signal.h"

/* ============================================================================
 * GLOBALS
 * ============================================================================ */

static volatile int g_sigpipe_received = 0;

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
 * SIGNAL HANDLER
 * ============================================================================ */

static void sigpipe_handler(int sig)
{
    (void)sig;
    g_sigpipe_received = 1;
}

/* ============================================================================
 * TESTS
 * ============================================================================ */

/**
 * @brief Test 1: Write to pipe with closed read end (with handler installed)
 *
 * Install SIGPIPE handler, create pipe, close read end, write.
 * Expect: handler fires, write returns error.
 */
static void test_sigpipe_with_handler(void)
{
    printf("[TEST 1] SIGPIPE with handler installed\n");

    /* Install SIGPIPE handler */
    g_sigpipe_received = 0;

    struct sigaction sa = {0};
    sa.sa_handler = sigpipe_handler;
    sa.sa_flags = 0;
    sigaction(13, &sa, NULL);  /* 13 = SIGPIPE */

    /* Create pipe */
    int pipefd[2];
    int ret = pipe(pipefd);
    if (ret < 0) {
        printf("  [ERROR] pipe() failed: %d\n", ret);
        TEST_FAIL("pipe() creation");
        return;
    }

    printf("  pipe created: read_fd=%d, write_fd=%d\n", pipefd[0], pipefd[1]);

    /* Close read end */
    close(pipefd[0]);
    printf("  read end closed\n");

    /* Write to pipe — should trigger SIGPIPE */
    char buf[] = "hello";
    ssize_t written = write(pipefd[1], buf, 5);
    printf("  write() returned: %d\n", (int)written);

    /* Check results */
    if (g_sigpipe_received) {
        TEST_PASS("SIGPIPE signal received by handler");
    } else {
        TEST_FAIL("SIGPIPE signal NOT received");
    }

    if (written < 0) {
        TEST_PASS("write() returned error (expected)");
    } else {
        TEST_FAIL("write() did NOT return error");
    }

    close(pipefd[1]);
}

/**
 * @brief Test 2: Write to pipe with closed read end in child process
 *
 * Fork, child closes read end, parent writes.
 * Parent installs SIGPIPE handler to catch the signal.
 */
static void test_sigpipe_cross_process(void)
{
    printf("[TEST 2] SIGPIPE via cross-process pipe close\n");

    /* Install handler */
    g_sigpipe_received = 0;

    struct sigaction sa = {0};
    sa.sa_handler = sigpipe_handler;
    sa.sa_flags = 0;
    sigaction(13, &sa, NULL);

    /* Create pipe */
    int pipefd[2];
    if (pipe(pipefd) < 0) {
        TEST_FAIL("pipe() creation");
        return;
    }

    pid_t pid = fork();
    if (pid < 0) {
        TEST_FAIL("fork()");
        close(pipefd[0]);
        close(pipefd[1]);
        return;
    }

    if (pid == 0) {
        /* Child: close both ends and exit
         * This closes the read end, making the parent's write trigger SIGPIPE */
        close(pipefd[0]);
        close(pipefd[1]);
        /* Small delay so parent writes before child exits */
        _exit(0);
    }

    /* Parent: close read end (we only write), wait for child to close its end */
    close(pipefd[0]);

    /* Wait for child to exit (which closes its read fd) */
    int status;
    waitpid(pid, &status, 0);

    /* Now write — read end is closed by both parent and child */
    char buf[] = "test";
    ssize_t written = write(pipefd[1], buf, 4);
    printf("  write() after child exit returned: %d\n", (int)written);

    if (g_sigpipe_received) {
        TEST_PASS("SIGPIPE received after child closed pipe");
    } else {
        TEST_FAIL("SIGPIPE NOT received after child closed pipe");
    }

    close(pipefd[1]);
}

/**
 * @brief Test 3: Verify write to normal pipe succeeds (control test)
 */
static void test_normal_pipe_write(void)
{
    printf("[TEST 3] Normal pipe write (control test)\n");

    int pipefd[2];
    if (pipe(pipefd) < 0) {
        TEST_FAIL("pipe() creation");
        return;
    }

    /* Write to pipe with read end still open */
    char buf[] = "ok";
    ssize_t written = write(pipefd[1], buf, 2);

    if (written == 2) {
        TEST_PASS("Normal pipe write succeeds (2 bytes)");
    } else {
        printf("  write() returned: %d\n", (int)written);
        TEST_FAIL("Normal pipe write failed");
    }

    /* Read back to verify */
    char rbuf[4] = {0};
    ssize_t bytes_read = read(pipefd[0], rbuf, 2);
    if (bytes_read == 2 && rbuf[0] == 'o' && rbuf[1] == 'k') {
        TEST_PASS("Pipe data integrity verified");
    } else {
        TEST_FAIL("Pipe data integrity check failed");
    }

    close(pipefd[0]);
    close(pipefd[1]);
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
    printf("  VOS3 SIGPIPE Validation Test\n");
    printf("============================================\n");
    printf("\n");

    test_normal_pipe_write();
    printf("\n");

    test_sigpipe_with_handler();
    printf("\n");

    test_sigpipe_cross_process();
    printf("\n");

    /* Summary */
    printf("============================================\n");
    printf("[SIGPIPE] Test Summary:\n");
    printf("  Passed: %d\n", g_tests_passed);
    printf("  Failed: %d\n", g_tests_failed);
    printf("============================================\n");

    if (g_tests_failed == 0) {
        printf("\n  ** SIGPIPE: ALL TESTS PASSED **\n\n");
        return 0;
    } else {
        printf("\n  ** SIGPIPE: SOME TESTS FAILED **\n\n");
        return 1;
    }
}
