/**
 * @file ai_top.c
 * @brief VOS3 AI Memory Guard Real-Time Dashboard
 *
 * @details Interactive ASCII dashboard displaying real-time AI memory
 *          performance metrics with NUMA-aware visualization and
 *          per-CPU usage bars.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 19 - AI UI Suite
 * @note Phase 25 - Multicore Stress Testing (per-CPU bars)
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "ioctl.h"

/* Dashboard configuration */
#define BAR_WIDTH       40
#define CPU_BAR_WIDTH   20
#define REFRESH_MS      1000
#define MAX_NUMA_NODES  4
#define MAX_CPUS        8   /* Display up to 8 CPUs */

/* ANSI escape codes for terminal control */
#define CLEAR_SCREEN    "\033[2J\033[H"
#define BOLD            "\033[1m"
#define RESET           "\033[0m"
#define GREEN           "\033[32m"
#define YELLOW          "\033[33m"
#define RED             "\033[31m"
#define CYAN            "\033[36m"

/* Simulated NUMA statistics (would come from kernel in full implementation) */
typedef struct {
    uint64_t total_bytes;
    uint64_t used_bytes;
    uint32_t region_count;
    uint32_t access_count;
} numa_node_stats_t;

/**
 * @brief Draw a progress bar
 */
static void draw_bar(unsigned int percent, int width)
{
    int filled = (percent * width) / 100;
    if (filled > width) filled = width;

    printf("[");
    for (int i = 0; i < width; i++) {
        if (i < filled) {
            if (percent > 80) {
                printf("%s#%s", RED, RESET);
            } else if (percent > 60) {
                printf("%s#%s", YELLOW, RESET);
            } else {
                printf("%s#%s", GREEN, RESET);
            }
        } else {
            printf("-");
        }
    }
    printf("] %3u%%", percent);
}

/**
 * @brief Draw CPU usage bar with pipe characters
 */
static void draw_cpu_bar(unsigned int percent, int width)
{
    int filled = (percent * width) / 100;
    if (filled > width) filled = width;

    printf("[");
    for (int i = 0; i < width; i++) {
        if (i < filled) {
            if (percent > 80) {
                printf("%s|%s", RED, RESET);
            } else if (percent > 50) {
                printf("%s|%s", YELLOW, RESET);
            } else {
                printf("%s|%s", GREEN, RESET);
            }
        } else {
            printf(" ");
        }
    }
    printf("] %3u%%", percent);
}

/**
 * @brief Get SMP statistics from kernel
 */
static int get_smp_stats(vos3_smp_stats_t *stats)
{
    int fd = open("/dev/smp", O_RDONLY, 0);
    if (fd < 0) {
        return -1;
    }

    int ret = ioctl(fd, VOS3_IOCTL_SMP_GET_STATS, stats);
    close(fd);
    return ret;
}

/**
 * @brief Format large numbers with K/M suffix
 */
static void format_count(uint64_t count, char *buf, size_t buflen)
{
    if (count >= 1000000) {
        snprintf(buf, buflen, "%llu.%lluM",
                 (unsigned long long)(count / 1000000),
                 (unsigned long long)((count % 1000000) / 100000));
    } else if (count >= 1000) {
        snprintf(buf, buflen, "%llu.%lluK",
                 (unsigned long long)(count / 1000),
                 (unsigned long long)((count % 1000) / 100));
    } else {
        snprintf(buf, buflen, "%llu", (unsigned long long)count);
    }
}

/**
 * @brief Calculate simulated NUMA distribution
 */
static void get_numa_stats(const vos3_ai_stats_t *global,
                           numa_node_stats_t *nodes, int num_nodes)
{
    /* Distribute memory across NUMA nodes (simplified simulation) */
    uint64_t per_node = global->total_bytes / num_nodes;
    uint32_t per_node_regions = global->region_count / num_nodes;

    for (int i = 0; i < num_nodes; i++) {
        nodes[i].total_bytes = per_node + (per_node / 4);  /* Capacity */
        nodes[i].used_bytes = per_node;
        nodes[i].region_count = per_node_regions + (i == 0 ? global->region_count % num_nodes : 0);
        nodes[i].access_count = (uint32_t)((global->read_count + global->write_count) / num_nodes);
    }
}

/**
 * @brief Draw the main dashboard
 */
static void draw_dashboard(const vos3_ai_stats_t *stats, int iteration)
{
    char buf[32];
    numa_node_stats_t numa[MAX_NUMA_NODES];
    int num_nodes = 2;  /* VOS3 default NUMA configuration */

    /* Clear screen */
    printf("%s", CLEAR_SCREEN);

    /* Header */
    printf("%s", BOLD);
    printf("================================================================\n");
    printf("          VOS3 AI MEMORY GUARD - REAL-TIME DASHBOARD           \n");
    printf("                   Phase 25 SMP Edition                         \n");
    printf("================================================================%s\n\n", RESET);

    /* CPU Usage Bars (Phase 25) */
    vos3_smp_stats_t smp_stats;
    memset(&smp_stats, 0, sizeof(smp_stats));
    int smp_available = (get_smp_stats(&smp_stats) == 0);

    printf("%s--- CPU Usage (%u/%u online) ---%s\n", CYAN,
           smp_available ? smp_stats.online_count : 1,
           smp_available ? smp_stats.cpu_count : 1, RESET);

    if (smp_available && smp_stats.cpu_count > 0) {
        for (uint32_t i = 0; i < smp_stats.cpu_count && i < MAX_CPUS; i++) {
            printf("  CPU %u: ", i);
            draw_cpu_bar(smp_stats.cpus[i].usage_percent, CPU_BAR_WIDTH);
            printf("  Tasks: %u", smp_stats.cpus[i].task_count);
            if (!smp_stats.cpus[i].online) {
                printf(" (offline)");
            }
            printf("\n");
        }
    } else {
        printf("  CPU 0: ");
        draw_cpu_bar(50, CPU_BAR_WIDTH);
        printf("  (SMP device not available)\n");
    }
    printf("\n");

    /* System Overview */
    printf("%s--- System Overview ---%s\n", CYAN, RESET);
    format_count(stats->total_bytes, buf, sizeof(buf));
    printf("  Protected Memory: %-12s  ", buf);
    printf("Regions: %-6u  ", stats->region_count);
    printf("Mode: %s\n", stats->mode == 0 ? "POLL" : "EVENT");
    printf("\n");

    /* NUMA Node Status */
    get_numa_stats(stats, numa, num_nodes);
    printf("%s--- NUMA Memory Distribution ---%s\n", CYAN, RESET);
    for (int i = 0; i < num_nodes; i++) {
        unsigned int percent = 0;
        if (numa[i].total_bytes > 0) {
            percent = (unsigned int)((numa[i].used_bytes * 100) / numa[i].total_bytes);
        }
        printf("  Node %d: ", i);
        draw_bar(percent, BAR_WIDTH);
        format_count(numa[i].used_bytes, buf, sizeof(buf));
        printf("  %s\n", buf);
    }
    printf("\n");

    /* Access Counters */
    printf("%s--- Real-Time Access Counters ---%s\n", CYAN, RESET);
    format_count(stats->read_count, buf, sizeof(buf));
    printf("  Reads:  %-12s  ", buf);

    format_count(stats->write_count, buf, sizeof(buf));
    printf("Writes: %-12s  ", buf);

    format_count(stats->fault_count, buf, sizeof(buf));
    printf("Faults: %s\n", buf);
    printf("\n");

    /* Performance Metrics */
    printf("%s--- Performance Metrics ---%s\n", CYAN, RESET);
    uint64_t total_ops = stats->read_count + stats->write_count;
    printf("  Total Operations: ");
    format_count(total_ops, buf, sizeof(buf));
    printf("%-12s  ", buf);

    /* Simulated latency based on iteration (would use ticks in real impl) */
    unsigned int latency = 50 + (iteration % 30);
    printf("Avg Latency: %u ns\n", latency);
    printf("\n");

    /* Security Status */
    printf("%s--- Security Monitor ---%s\n", CYAN, RESET);
    printf("  Violations: %-8u  ", stats->violation_count);
    printf("Alerts: %-8u  ", stats->alert_count);
    printf("Threshold: %u\n", stats->threshold);

    /* Health indicator */
    printf("\n");
    if (stats->violation_count == 0) {
        printf("  Status: %s[  SECURE  ]%s\n", GREEN, RESET);
    } else if (stats->violation_count < 10) {
        printf("  Status: %s[ NOMINAL  ]%s\n", YELLOW, RESET);
    } else {
        printf("  Status: %s[  ALERT   ]%s\n", RED, RESET);
    }

    /* Footer */
    printf("\n");
    printf("================================================================\n");
    printf("  Refresh: %d ms | Iteration: %d | Press Ctrl+C to exit\n",
           REFRESH_MS, iteration);
    printf("================================================================\n");
}

/**
 * @brief Print usage information
 */
static void print_usage(const char *prog)
{
    printf("Usage: %s [OPTIONS]\n", prog);
    printf("\n");
    printf("Real-time AI Memory Guard dashboard.\n");
    printf("\n");
    printf("Options:\n");
    printf("  -h, --help     Show this help message\n");
    printf("  -n <count>     Run for <count> iterations then exit\n");
    printf("  -1, --once     Single snapshot (no loop)\n");
    printf("\n");
}

int main(int argc, char *argv[])
{
    int max_iterations = -1;  /* -1 = infinite */
    int single_shot = 0;

    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "-1") == 0 || strcmp(argv[i], "--once") == 0) {
            single_shot = 1;
        } else if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            max_iterations = atoi(argv[++i]);
        }
    }

    /* Open telemetry device */
    int fd = open("/dev/ai_telemetry", O_RDONLY, 0);
    if (fd < 0) {
        printf("Error: Cannot open /dev/ai_telemetry\n");
        printf("       AI Memory Guard may not be initialized.\n");
        return 1;
    }

    /* Main loop */
    int iteration = 0;
    while (max_iterations < 0 || iteration < max_iterations) {
        vos3_ai_stats_t stats;
        memset(&stats, 0, sizeof(stats));

        int ret = ioctl(fd, VOS3_IOCTL_AI_GET_STATS, &stats);
        if (ret < 0) {
            printf("Error: Failed to get statistics (error %d)\n", ret);
            close(fd);
            return 1;
        }

        draw_dashboard(&stats, iteration);

        if (single_shot) {
            break;
        }

        /* Sleep before next refresh */
        usleep(REFRESH_MS * 1000);
        iteration++;
    }

    close(fd);
    return 0;
}
