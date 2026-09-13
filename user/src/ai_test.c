/**
 * @file ai_test.c
 * @brief VOS3 AI Memory Guard Test Utility
 *
 * @details Tests AI memory protection by exercising memory operations
 *          and verifying pipes/write syscalls work correctly alongside
 *          the new AI Guard infrastructure in the kernel.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 17.5 - Telemetry Export
 * @note Phase 18 - Device Infrastructure & IOCTL
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "ioctl.h"

/**
 * @brief Test basic memory operations
 */
static int test_memory_ops(void)
{
    printf("[AI-TEST] Testing memory operations...\n");

    /* Allocate memory on the heap */
    char *buf = malloc(256);
    if (!buf) {
        printf("[AI-TEST] FAIL: malloc failed\n");
        return -1;
    }

    /* Write to buffer */
    strcpy(buf, "AI Guard Test Buffer");

    /* Verify content */
    if (strcmp(buf, "AI Guard Test Buffer") != 0) {
        printf("[AI-TEST] FAIL: memory corruption detected\n");
        free(buf);
        return -1;
    }

    printf("[AI-TEST] PASS: Memory allocation OK\n");
    free(buf);
    return 0;
}

/**
 * @brief Test sys_write functionality
 */
static int test_write_syscall(void)
{
    printf("[AI-TEST] Testing sys_write...\n");

    const char *msg = "[AI-TEST] sys_write test message\n";
    int ret = write(1, msg, strlen(msg));

    if (ret < 0) {
        printf("[AI-TEST] FAIL: write returned %d\n", ret);
        return -1;
    }

    printf("[AI-TEST] PASS: sys_write OK (wrote %d bytes)\n", ret);
    return 0;
}

/**
 * @brief Test pipe functionality
 */
static int test_pipes(void)
{
    int pipefd[2];

    printf("[AI-TEST] Testing pipes...\n");

    if (pipe(pipefd) < 0) {
        printf("[AI-TEST] FAIL: pipe() failed\n");
        return -1;
    }

    /* Write to pipe */
    const char *test_data = "AI Guard Pipe Test";
    write(pipefd[1], test_data, strlen(test_data));

    /* Read from pipe */
    char buf[64] = {0};
    int n = read(pipefd[0], buf, sizeof(buf) - 1);

    close(pipefd[0]);
    close(pipefd[1]);

    if (n <= 0) {
        printf("[AI-TEST] FAIL: pipe read returned %d\n", n);
        return -1;
    }

    if (strcmp(buf, test_data) != 0) {
        printf("[AI-TEST] FAIL: pipe data mismatch\n");
        return -1;
    }

    printf("[AI-TEST] PASS: Pipes OK (%d bytes transferred)\n", n);
    return 0;
}

/**
 * @brief Test array bounds (simulated AI buffer test)
 */
static int test_array_bounds(void)
{
    printf("[AI-TEST] Testing array bounds...\n");

    /* Allocate a buffer like an AI tensor would */
    size_t tensor_size = 1024;
    char *tensor = malloc(tensor_size);
    if (!tensor) {
        printf("[AI-TEST] FAIL: tensor allocation failed\n");
        return -1;
    }

    /* Fill with pattern */
    for (size_t i = 0; i < tensor_size; i++) {
        tensor[i] = (char)(i & 0xFF);
    }

    /* Verify pattern */
    int errors = 0;
    for (size_t i = 0; i < tensor_size; i++) {
        if (tensor[i] != (char)(i & 0xFF)) {
            errors++;
        }
    }

    free(tensor);

    if (errors > 0) {
        printf("[AI-TEST] FAIL: %d integrity errors\n", errors);
        return -1;
    }

    printf("[AI-TEST] PASS: Array bounds OK\n");
    return 0;
}

/**
 * @brief Test /dev/ai_telemetry device read (Phase 17.5.5)
 */
static int test_telemetry_device(void)
{
    printf("[AI-TEST] Testing /dev/ai_telemetry read...\n");

    /* Open telemetry device */
    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("[AI-TEST] FAIL: Cannot open /dev/ai_telemetry\n");
        return -1;
    }

    /* Read telemetry snapshot */
    unsigned char buffer[128];
    int n = read(fd, buffer, sizeof(buffer));
    close(fd);

    if (n <= 0) {
        printf("[AI-TEST] FAIL: Read returned %d\n", n);
        return -1;
    }

    /* Verify magic: "AITe" = 0x41495465 (little-endian: 65 54 49 41) */
    unsigned int magic = buffer[0] | (buffer[1] << 8) | (buffer[2] << 16) | (buffer[3] << 24);
    if (magic != 0x41495465) {
        printf("[AI-TEST] FAIL: Bad magic 0x%08X (expected 0x41495465)\n", magic);
        return -1;
    }

    /* Verify version */
    unsigned int version = buffer[4] | (buffer[5] << 8) | (buffer[6] << 16) | (buffer[7] << 24);
    if (version != 1) {
        printf("[AI-TEST] FAIL: Bad version %u (expected 1)\n", version);
        return -1;
    }

    /* Get region count (at offset 20) */
    unsigned int region_count = buffer[20] | (buffer[21] << 8) | (buffer[22] << 16) | (buffer[23] << 24);

    printf("[AI-TEST] PASS: Telemetry read OK (magic=AITe, version=%u, regions=%u, %d bytes)\n",
           version, region_count, n);
    return 0;
}

/**
 * @brief Test /dev/ai_telemetry ioctl (Phase 18)
 */
static int test_telemetry_ioctl(void)
{
    printf("[AI-TEST] Testing /dev/ai_telemetry ioctl...\n");

    /* Open telemetry device */
    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("[AI-TEST] FAIL: Cannot open /dev/ai_telemetry for ioctl\n");
        return -1;
    }

    /* Test GET_STATS ioctl */
    vos3_ai_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    int ret = ioctl(fd, VOS3_IOCTL_AI_GET_STATS, &stats);
    if (ret < 0) {
        printf("[AI-TEST] FAIL: ioctl GET_STATS returned %d\n", ret);
        close(fd);
        return -1;
    }

    printf("[AI-TEST] IOCTL Stats:\n");
    printf("  - Regions: %u\n", stats.region_count);
    printf("  - Violations: %u\n", stats.violation_count);
    printf("  - Total bytes: %llu\n", (unsigned long long)stats.total_bytes);
    printf("  - Mode: %s\n", stats.mode == 0 ? "POLL" : "EVENT");
    printf("  - Threshold: %u\n", stats.threshold);

    close(fd);

    printf("[AI-TEST] PASS: IOCTL GET_STATS OK (violations=%u)\n", stats.violation_count);
    return 0;
}

/**
 * @brief Print test summary header
 */
static void print_header(void)
{
    printf("\n");
    printf("=====================================\n");
    printf("  VOS3 AI Memory Guard Test Suite\n");
    printf("        Phase 17.5 + Phase 18\n");
    printf("=====================================\n\n");
}

/**
 * @brief Print test summary
 */
static void print_summary(int passed, int total)
{
    printf("\n=====================================\n");
    printf("  TEST SUMMARY: %d/%d PASSED\n", passed, total);
    printf("=====================================\n");

    if (passed == total) {
        printf("  STATUS: ALL TESTS PASSED\n");
        printf("  AI Guard + IOCTL: VERIFIED\n");
    } else {
        printf("  STATUS: SOME TESTS FAILED\n");
        printf("  Review failures above\n");
    }
    printf("=====================================\n\n");
}

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    int passed = 0;
    int total = 6;

    print_header();

    /* Run Phase 17 tests */
    printf("--- Phase 17: Core AI Guard Tests ---\n\n");
    if (test_memory_ops() == 0) passed++;
    if (test_write_syscall() == 0) passed++;
    if (test_pipes() == 0) passed++;
    if (test_array_bounds() == 0) passed++;

    /* Phase 17.5: Telemetry device test */
    printf("\n--- Phase 17.5: Telemetry Device ---\n\n");
    if (test_telemetry_device() == 0) passed++;

    /* Phase 18: IOCTL test */
    printf("\n--- Phase 18: Device IOCTL ---\n\n");
    if (test_telemetry_ioctl() == 0) passed++;

    print_summary(passed, total);

    return (passed == total) ? 0 : 1;
}
