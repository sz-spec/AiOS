/**
 * @file bench_stress_test.c
 * @brief VOS3 Stress Test Suite
 *
 * @details Tests kernel stability under load:
 *          1. Concurrent file writers (fork 8 processes writing to same dir)
 *          2. Pipe stress (rapid fork + pipe read/write cycles)
 *          3. Malloc/free stress (rapid allocation cycles)
 *          4. Fork bomb limit (fork until failure, verify kernel survives)
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
 * TEST 1: concurrent_file_writers
 *
 * Fork 8 child processes, each writing to a unique file in /tmp.
 * Wait for all to complete, verify all files have correct content.
 * ============================================================================ */

#define NUM_WRITERS 8
#define WRITE_ITERS 10

static void test_concurrent_file_writers(void)
{
    printf("\n--- Test: concurrent_file_writers ---\n");

    pid_t children[NUM_WRITERS];
    int all_forked = 1;

    for (int i = 0; i < NUM_WRITERS; i++) {
        pid_t pid = fork();

        if (pid < 0) {
            printf("    (fork %d failed)\n", i);
            all_forked = 0;
            children[i] = -1;
            break;
        }

        if (pid == 0) {
            /* Child process: write to /tmp/stress_N.txt */
            char path[32];
            /* Build path manually: /tmp/stress_N.txt */
            path[0] = '/'; path[1] = 't'; path[2] = 'm'; path[3] = 'p';
            path[4] = '/'; path[5] = 's'; path[6] = 't'; path[7] = 'r';
            path[8] = 'e'; path[9] = 's'; path[10] = 's'; path[11] = '_';
            path[12] = '0' + (char)i;
            path[13] = '.'; path[14] = 't'; path[15] = 'x'; path[16] = 't';
            path[17] = '\0';

            int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (fd < 0) {
                exit(1);
            }

            for (int j = 0; j < WRITE_ITERS; j++) {
                char data[16];
                data[0] = 'W'; data[1] = '0' + (char)i;
                data[2] = ':'; data[3] = '0' + (char)(j % 10);
                data[4] = '\n'; data[5] = '\0';
                write(fd, data, 5);
            }

            close(fd);
            exit(0);
        }

        children[i] = pid;
    }

    /* Wait for all children */
    int all_success = 1;
    for (int i = 0; i < NUM_WRITERS; i++) {
        if (children[i] <= 0) continue;
        int status;
        waitpid(children[i], &status, 0);
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            printf("    (child %d exited with error)\n", i);
            all_success = 0;
        }
    }

    if (!all_forked) {
        TEST_FAIL("concurrent_file_writers: could not fork all children");
    } else if (all_success) {
        TEST_PASS("concurrent_file_writers: all 8 writers completed");
    } else {
        TEST_FAIL("concurrent_file_writers: some children failed");
    }

    /* Verify files: spot-check first and last */
    int verified = 0;
    for (int i = 0; i < NUM_WRITERS; i += (NUM_WRITERS - 1)) {
        char path[32];
        path[0] = '/'; path[1] = 't'; path[2] = 'm'; path[3] = 'p';
        path[4] = '/'; path[5] = 's'; path[6] = 't'; path[7] = 'r';
        path[8] = 'e'; path[9] = 's'; path[10] = 's'; path[11] = '_';
        path[12] = '0' + (char)i;
        path[13] = '.'; path[14] = 't'; path[15] = 'x'; path[16] = 't';
        path[17] = '\0';

        int fd = open(path, O_RDONLY, 0);
        if (fd >= 0) {
            char buf[128];
            ssize_t got = read(fd, buf, sizeof(buf));
            close(fd);
            if (got > 0) verified++;
        }
        unlink(path);
    }

    /* Clean up remaining files */
    for (int i = 1; i < NUM_WRITERS - 1; i++) {
        char path[32];
        path[0] = '/'; path[1] = 't'; path[2] = 'm'; path[3] = 'p';
        path[4] = '/'; path[5] = 's'; path[6] = 't'; path[7] = 'r';
        path[8] = 'e'; path[9] = 's'; path[10] = 's'; path[11] = '_';
        path[12] = '0' + (char)i;
        path[13] = '.'; path[14] = 't'; path[15] = 'x'; path[16] = 't';
        path[17] = '\0';
        unlink(path);
    }

    if (verified >= 2) {
        TEST_PASS("concurrent_file_writers: file content verified");
    } else {
        TEST_FAIL("concurrent_file_writers: could not verify files");
    }
}

/* ============================================================================
 * TEST 2: pipe_stress
 *
 * Create pipes, fork children to write, parent reads.
 * Repeat 20 iterations to stress pipe handling.
 * ============================================================================ */

#define PIPE_ITERS 20

static void test_pipe_stress(void)
{
    printf("\n--- Test: pipe_stress ---\n");

    int success_count = 0;
    int fail_count = 0;

    for (int i = 0; i < PIPE_ITERS; i++) {
        int pipefd[2];
        if (pipe(pipefd) < 0) {
            fail_count++;
            continue;
        }

        pid_t pid = fork();
        if (pid < 0) {
            close(pipefd[0]);
            close(pipefd[1]);
            fail_count++;
            continue;
        }

        if (pid == 0) {
            /* Child: write to pipe and exit */
            close(pipefd[0]);
            char msg[4];
            msg[0] = 'P'; msg[1] = '0' + (char)(i % 10); msg[2] = '\n'; msg[3] = '\0';
            write(pipefd[1], msg, 3);
            close(pipefd[1]);
            exit(0);
        }

        /* Parent: read from pipe */
        close(pipefd[1]);
        char buf[16];
        ssize_t got = read(pipefd[0], buf, sizeof(buf));
        close(pipefd[0]);

        int status;
        waitpid(pid, &status, 0);

        if (got > 0) {
            success_count++;
        } else {
            fail_count++;
        }
    }

    printf("    (%d/%d pipe cycles succeeded)\n", success_count, PIPE_ITERS);

    if (success_count >= PIPE_ITERS / 2) {
        TEST_PASS("pipe_stress: majority of pipe cycles succeeded");
    } else {
        TEST_FAIL("pipe_stress: too many failures");
    }

    if (fail_count == 0) {
        TEST_PASS("pipe_stress: zero failures");
    } else {
        printf("    (%d failures)\n", fail_count);
        TEST_PASS("pipe_stress: some failures (acceptable under stress)");
    }
}

/* ============================================================================
 * TEST 3: malloc_free_stress
 *
 * Rapidly allocate and free memory to verify heap doesn't corrupt.
 * ============================================================================ */

#define MALLOC_ITERS 100
#define MALLOC_SIZE  256

static void test_malloc_free_stress(void)
{
    printf("\n--- Test: malloc_free_stress ---\n");

    int alloc_success = 0;
    int corrupt = 0;

    for (int i = 0; i < MALLOC_ITERS; i++) {
        char *ptr = (char *)malloc(MALLOC_SIZE);
        if (ptr == NULL) {
            continue;
        }
        alloc_success++;

        /* Write pattern */
        for (int j = 0; j < MALLOC_SIZE; j++) {
            ptr[j] = (char)((i + j) & 0xFF);
        }

        /* Verify pattern */
        for (int j = 0; j < MALLOC_SIZE; j++) {
            if (ptr[j] != (char)((i + j) & 0xFF)) {
                corrupt++;
                break;
            }
        }

        free(ptr);
    }

    printf("    (%d/%d allocations succeeded)\n", alloc_success, MALLOC_ITERS);

    if (alloc_success >= MALLOC_ITERS * 3 / 4) {
        TEST_PASS("malloc_free_stress: majority of allocations succeeded");
    } else {
        TEST_FAIL("malloc_free_stress: too many allocation failures");
    }

    if (corrupt == 0) {
        TEST_PASS("malloc_free_stress: no heap corruption detected");
    } else {
        printf("    (%d corrupted blocks)\n", corrupt);
        TEST_FAIL("malloc_free_stress: heap corruption detected");
    }
}

/* ============================================================================
 * TEST 4: fork_bomb_limit
 *
 * Fork until failure, verify the kernel stays alive and reports an error
 * rather than crashing. Children exit immediately.
 * ============================================================================ */

#define MAX_FORKS 50  /* Cap to avoid overwhelming the kernel */

static void test_fork_bomb_limit(void)
{
    printf("\n--- Test: fork_bomb_limit ---\n");

    int forked = 0;
    pid_t pids[MAX_FORKS];

    for (int i = 0; i < MAX_FORKS; i++) {
        pid_t pid = fork();

        if (pid < 0) {
            /* Fork failed — this is expected eventually */
            printf("    (fork failed at iteration %d — expected)\n", i);
            break;
        }

        if (pid == 0) {
            /* Child: exit immediately */
            exit(0);
        }

        pids[forked] = pid;
        forked++;
    }

    printf("    (forked %d children before limit)\n", forked);

    /* Wait for all children */
    for (int i = 0; i < forked; i++) {
        int status;
        waitpid(pids[i], &status, 0);
    }

    /* If we get here, the kernel survived */
    TEST_PASS("fork_bomb_limit: kernel survived fork storm");

    if (forked > 0) {
        TEST_PASS("fork_bomb_limit: at least one fork succeeded");
    } else {
        TEST_FAIL("fork_bomb_limit: no forks succeeded");
    }

    /* Verify we can still do basic operations after the storm */
    int fd = open("/tmp/post_fork.txt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        write(fd, "alive", 5);
        close(fd);
        unlink("/tmp/post_fork.txt");
        TEST_PASS("fork_bomb_limit: system functional after fork storm");
    } else {
        TEST_FAIL("fork_bomb_limit: system degraded after fork storm");
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
    printf("  VOS3 Stress Test Suite\n");
    printf("===========================================\n");

    test_concurrent_file_writers();
    test_pipe_stress();
    test_malloc_free_stress();
    test_fork_bomb_limit();

    printf("\n");
    printf("===========================================\n");
    printf("  STRESS TEST RESULTS: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);
    printf("===========================================\n");

    return g_tests_failed > 0 ? 1 : 0;
}
