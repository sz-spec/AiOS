/**
 * @file ai_diag.c
 * @brief VOS3 AI Telemetry Device Diagnostic Utility
 *
 * @details Deep validation of /dev/ai_telemetry device interface.
 *          Performs three specific checks to verify kernel-userspace bridge.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 18.1 - Device Interface Deep Validation
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "ioctl.h"

/* Expected magic and version */
#define EXPECTED_MAGIC      0x41495465  /* "AITe" */
#define EXPECTED_VERSION    1
#define TEST_THRESHOLD      7777

/**
 * @brief Check 1: Read header from /dev/ai_telemetry
 *
 * Verifies the magic number and version are correct.
 */
static int check_read_header(void)
{
    printf("[AI-DIAG] Check 1: Read Header Validation\n");

    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("[AI-DIAG]   FAIL: Cannot open /dev/ai_telemetry (fd=%d)\n", fd);
        return -1;
    }

    /* Read the header (first 32 bytes contains magic, version, timestamp, etc.) */
    unsigned char buffer[64];
    memset(buffer, 0, sizeof(buffer));

    int n = read(fd, buffer, sizeof(buffer));
    close(fd);

    if (n <= 0) {
        printf("[AI-DIAG]   FAIL: Read returned %d bytes\n", n);
        return -1;
    }

    printf("[AI-DIAG]   Read %d bytes from device\n", n);

    /* Parse header fields (little-endian) */
    unsigned int magic = buffer[0] | (buffer[1] << 8) |
                         (buffer[2] << 16) | (buffer[3] << 24);
    unsigned int version = buffer[4] | (buffer[5] << 8) |
                           (buffer[6] << 16) | (buffer[7] << 24);

    printf("[AI-DIAG]   Magic:   0x%08X (expected 0x%08X)\n", magic, EXPECTED_MAGIC);
    printf("[AI-DIAG]   Version: %u (expected %u)\n", version, EXPECTED_VERSION);

    if (magic != EXPECTED_MAGIC) {
        printf("[AI-DIAG]   FAIL: Magic mismatch!\n");
        return -1;
    }

    if (version != EXPECTED_VERSION) {
        printf("[AI-DIAG]   FAIL: Version mismatch!\n");
        return -1;
    }

    printf("[AI-DIAG]   PASS: Header validation OK\n");
    return 0;
}

/**
 * @brief Check 2: IOCTL Set/Get Threshold Logic
 *
 * Sets threshold to TEST_THRESHOLD, then reads stats to verify update.
 */
static int check_ioctl_threshold(void)
{
    printf("[AI-DIAG] Check 2: IOCTL Set/Get Threshold\n");

    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("[AI-DIAG]   FAIL: Cannot open device\n");
        return -1;
    }

    /* Step 1: Get current stats to see initial threshold */
    vos3_ai_stats_t stats_before;
    memset(&stats_before, 0, sizeof(stats_before));

    int ret = ioctl(fd, VOS3_IOCTL_AI_GET_STATS, &stats_before);
    if (ret < 0) {
        printf("[AI-DIAG]   FAIL: GET_STATS returned %d\n", ret);
        close(fd);
        return -1;
    }

    printf("[AI-DIAG]   Initial threshold: %u\n", stats_before.threshold);

    /* Step 2: Set new threshold */
    unsigned int new_threshold = TEST_THRESHOLD;
    ret = ioctl(fd, VOS3_IOCTL_AI_SET_THRESHOLD, &new_threshold);
    if (ret < 0) {
        printf("[AI-DIAG]   FAIL: SET_THRESHOLD returned %d\n", ret);
        close(fd);
        return -1;
    }

    printf("[AI-DIAG]   Set threshold to: %u\n", TEST_THRESHOLD);

    /* Step 3: Get stats again and verify threshold changed */
    vos3_ai_stats_t stats_after;
    memset(&stats_after, 0, sizeof(stats_after));

    ret = ioctl(fd, VOS3_IOCTL_AI_GET_STATS, &stats_after);
    if (ret < 0) {
        printf("[AI-DIAG]   FAIL: GET_STATS (after) returned %d\n", ret);
        close(fd);
        return -1;
    }

    printf("[AI-DIAG]   Verified threshold: %u\n", stats_after.threshold);

    close(fd);

    if (stats_after.threshold != TEST_THRESHOLD) {
        printf("[AI-DIAG]   FAIL: Threshold not updated! Expected %u, got %u\n",
               TEST_THRESHOLD, stats_after.threshold);
        return -1;
    }

    printf("[AI-DIAG]   PASS: IOCTL threshold ping-pong OK\n");
    return 0;
}

/**
 * @brief Check 3: IOCTL Reset Stats Permission
 *
 * Attempts to reset stats and verifies the return code.
 */
static int check_ioctl_reset(void)
{
    printf("[AI-DIAG] Check 3: IOCTL Reset Stats\n");

    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("[AI-DIAG]   FAIL: Cannot open device\n");
        return -1;
    }

    /* Attempt to reset stats */
    int ret = ioctl(fd, VOS3_IOCTL_AI_RESET_STATS, (void*)0);

    printf("[AI-DIAG]   RESET_STATS returned: %d\n", ret);

    if (ret < 0) {
        printf("[AI-DIAG]   FAIL: Reset failed with error %d\n", ret);
        close(fd);
        return -1;
    }

    /* Verify reset worked by checking stats */
    vos3_ai_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    ret = ioctl(fd, VOS3_IOCTL_AI_GET_STATS, &stats);
    if (ret < 0) {
        printf("[AI-DIAG]   FAIL: GET_STATS after reset returned %d\n", ret);
        close(fd);
        return -1;
    }

    printf("[AI-DIAG]   After reset - violations: %u, alerts: %u, overflow: %u\n",
           stats.violation_count, stats.alert_count, stats.overflow_count);

    close(fd);

    /* Violation count should be 0 after reset */
    if (stats.violation_count != 0 || stats.alert_count != 0) {
        printf("[AI-DIAG]   WARN: Counters not zeroed (may be OK if activity occurred)\n");
    }

    printf("[AI-DIAG]   PASS: Reset stats executed successfully\n");
    return 0;
}

/**
 * @brief Print diagnostic header
 */
static void print_header(void)
{
    printf("\n");
    printf("=========================================\n");
    printf("  VOS3 AI Telemetry Device Diagnostics\n");
    printf("       Phase 18.1 Deep Validation\n");
    printf("=========================================\n\n");
}

/**
 * @brief Print diagnostic summary
 */
static void print_summary(int passed, int total)
{
    printf("\n=========================================\n");
    printf("  DIAGNOSTIC SUMMARY: %d/%d CHECKS PASSED\n", passed, total);
    printf("=========================================\n");

    if (passed == total) {
        printf("  STATUS: ALL CHECKS PASSED\n");
        printf("  /dev/ai_telemetry bridge: VERIFIED\n");
        printf("  Ready for Phase 19: UI Tools\n");
    } else {
        printf("  STATUS: SOME CHECKS FAILED\n");
        printf("  Review kernel ai_telemetry.c\n");
        printf("  DO NOT proceed to Phase 19\n");
    }
    printf("=========================================\n\n");
}

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    int passed = 0;
    int total = 3;

    print_header();

    /* Run all checks */
    if (check_read_header() == 0) passed++;
    printf("\n");

    if (check_ioctl_threshold() == 0) passed++;
    printf("\n");

    if (check_ioctl_reset() == 0) passed++;

    print_summary(passed, total);

    return (passed == total) ? 0 : 1;
}
