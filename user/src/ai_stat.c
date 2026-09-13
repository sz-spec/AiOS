/**
 * @file ai_stat.c
 * @brief VOS3 AI Memory Guard Statistics Tool
 *
 * @details Production tool for displaying AI memory protection statistics.
 *          Uses verified IOCTL interface to fetch real-time data from kernel.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 19 + 22.6 - AI UI Suite (Enterprise Lock Aware)
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "ioctl.h"

/**
 * @brief Format bytes into human-readable string
 */
static void format_bytes(uint64_t bytes, char *buf, size_t buflen)
{
    if (bytes >= 1024ULL * 1024 * 1024) {
        unsigned int gb = (unsigned int)(bytes / (1024ULL * 1024 * 1024));
        unsigned int mb = (unsigned int)((bytes % (1024ULL * 1024 * 1024)) / (1024 * 1024));
        snprintf(buf, buflen, "%u.%u GiB", gb, mb / 100);
    } else if (bytes >= 1024 * 1024) {
        unsigned int mb = (unsigned int)(bytes / (1024 * 1024));
        unsigned int kb = (unsigned int)((bytes % (1024 * 1024)) / 1024);
        snprintf(buf, buflen, "%u.%u MiB", mb, kb / 100);
    } else if (bytes >= 1024) {
        unsigned int kb = (unsigned int)(bytes / 1024);
        snprintf(buf, buflen, "%u KiB", kb);
    } else {
        snprintf(buf, buflen, "%llu B", (unsigned long long)bytes);
    }
}

/**
 * @brief Determine security health status
 */
static const char *get_health_status(uint32_t violations, uint32_t alerts)
{
    if (violations == 0 && alerts == 0) {
        return "HEALTHY";
    } else if (violations < 10 && alerts < 5) {
        return "NOMINAL";
    } else if (violations < 50) {
        return "WARNING";
    } else {
        return "CRITICAL";
    }
}

/**
 * @brief Get system mode string (Phase 22.6)
 */
static const char *get_system_mode_str(uint32_t mode)
{
    switch (mode) {
        case 0x01: return "PRIVATE (Privacy Shield)";
        case 0x02: return "ENTERPRISE";
        case 0x82: return "ENTERPRISE LOCKED";
        default:   return "UNCONFIGURED";
    }
}

/**
 * @brief Print statistics header
 */
static void print_header(void)
{
    printf("\n");
    printf("========================================\n");
    printf("     VOS3 AI Memory Guard Statistics    \n");
    printf("         Phase 19 + 22.6 (Business)     \n");
    printf("========================================\n\n");
}

/**
 * @brief Print statistics summary
 */
static void print_stats(const vos3_ai_stats_t *stats)
{
    char bytes_buf[32];

    /* Memory Overview */
    printf("--- Memory Overview ---\n");
    format_bytes(stats->total_bytes, bytes_buf, sizeof(bytes_buf));
    printf("  Total Protected Memory: %s\n", bytes_buf);
    printf("  Active Regions:         %u\n", stats->region_count);
    printf("  Anomaly Threshold:      %u\n", stats->threshold);
    printf("\n");

    /* Access Statistics */
    printf("--- Access Statistics ---\n");
    printf("  Read Operations:   %llu\n", (unsigned long long)stats->read_count);
    printf("  Write Operations:  %llu\n", (unsigned long long)stats->write_count);
    printf("  Page Faults:       %llu\n", (unsigned long long)stats->fault_count);
    printf("\n");

    /* Security Status */
    printf("--- Security Status ---\n");
    printf("  Violations Detected:  %u\n", stats->violation_count);
    printf("  Pending Alerts:       %u\n", stats->alert_count);
    printf("  Dropped Alerts:       %u\n", stats->overflow_count);
    printf("  Telemetry Mode:       %s\n", stats->mode == 0 ? "POLL" : "EVENT");
    printf("\n");

    /* System Mode (Phase 22.6) */
    printf("--- System Mode (Phase 22.6) ---\n");
    printf("  Identity Mode:        %s\n", get_system_mode_str(stats->mode & 0xFF));
    if ((stats->mode & 0x80) != 0) {
        printf("  Business Lock:        ACTIVE\n");
        printf("  Owner Lock Bit:       SET\n");
        printf("  Policy Enforcement:   STRICT\n");
    } else {
        printf("  Business Lock:        INACTIVE\n");
    }
    printf("\n");

    /* Health Assessment */
    const char *health = get_health_status(stats->violation_count, stats->alert_count);
    printf("========================================\n");
    if ((stats->mode & 0x80) != 0) {
        printf("  BUSINESS LOCK: [ ENFORCED ]\n");
    }
    printf("  SECURITY HEALTH: [ %s ]\n", health);
    printf("========================================\n\n");
}

/**
 * @brief Print usage information
 */
static void print_usage(const char *prog)
{
    printf("Usage: %s [OPTIONS]\n", prog);
    printf("\n");
    printf("Display AI Memory Guard statistics.\n");
    printf("\n");
    printf("Options:\n");
    printf("  -h, --help     Show this help message\n");
    printf("  -r, --reset    Reset statistics counters\n");
    printf("  -t <value>     Set anomaly threshold\n");
    printf("\n");
}

int main(int argc, char *argv[])
{
    int do_reset = 0;
    int set_threshold = 0;
    unsigned int new_threshold = 0;

    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--reset") == 0) {
            do_reset = 1;
        } else if (strcmp(argv[i], "-t") == 0 && i + 1 < argc) {
            set_threshold = 1;
            new_threshold = (unsigned int)atoi(argv[++i]);
        }
    }

    /* Open telemetry device */
    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("Error: Cannot open /dev/ai_telemetry\n");
        printf("       AI Memory Guard may not be initialized.\n");
        return 1;
    }

    /* Reset counters if requested */
    if (do_reset) {
        int ret = ioctl(fd, VOS3_IOCTL_AI_RESET_STATS, (void*)0);
        if (ret < 0) {
            printf("Warning: Failed to reset statistics (error %d)\n", ret);
        } else {
            printf("Statistics counters reset.\n\n");
        }
    }

    /* Set threshold if requested */
    if (set_threshold) {
        int ret = ioctl(fd, VOS3_IOCTL_AI_SET_THRESHOLD, &new_threshold);
        if (ret < 0) {
            printf("Warning: Failed to set threshold (error %d)\n", ret);
        } else {
            printf("Anomaly threshold set to %u.\n\n", new_threshold);
        }
    }

    /* Get current statistics */
    vos3_ai_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    int ret = ioctl(fd, VOS3_IOCTL_AI_GET_STATS, &stats);
    if (ret < 0) {
        printf("Error: Failed to get statistics (error %d)\n", ret);
        close(fd);
        return 1;
    }

    close(fd);

    /* Display statistics */
    print_header();
    print_stats(&stats);

    return 0;
}
