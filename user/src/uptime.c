/**
 * @file uptime.c
 * @brief VOS3 uptime utility - show system uptime
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "time.h"

/**
 * @brief Print usage
 */
static void usage(const char *progname)
{
    printf("Usage: %s [options]\n", progname);
    printf("Display system uptime and current time.\n");
    printf("\nOptions:\n");
    printf("  -p, --pretty   Show uptime in pretty format\n");
    printf("  -s, --since    Show system up since time\n");
    printf("  --help         Show this help\n");
}

/**
 * @brief Format uptime duration
 */
static void format_uptime(long seconds, char *buf, size_t size, int pretty)
{
    long days = seconds / 86400;
    long hours = (seconds % 86400) / 3600;
    long mins = (seconds % 3600) / 60;
    long secs = seconds % 60;

    if (pretty) {
        if (days > 0) {
            snprintf(buf, size, "up %ld day%s, %ld hour%s, %ld minute%s",
                    days, days == 1 ? "" : "s",
                    hours, hours == 1 ? "" : "s",
                    mins, mins == 1 ? "" : "s");
        } else if (hours > 0) {
            snprintf(buf, size, "up %ld hour%s, %ld minute%s",
                    hours, hours == 1 ? "" : "s",
                    mins, mins == 1 ? "" : "s");
        } else if (mins > 0) {
            snprintf(buf, size, "up %ld minute%s",
                    mins, mins == 1 ? "" : "s");
        } else {
            snprintf(buf, size, "up %ld second%s",
                    secs, secs == 1 ? "" : "s");
        }
    } else {
        if (days > 0) {
            snprintf(buf, size, "up %ld day%s, %2ld:%02ld",
                    days, days == 1 ? "" : "s", hours, mins);
        } else {
            snprintf(buf, size, "up %2ld:%02ld", hours, mins);
        }
    }
}

int main(int argc, char *argv[])
{
    int pretty = 0;
    int show_since = 0;

    /* Parse options */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--pretty") == 0) {
            pretty = 1;
        } else if (strcmp(argv[i], "-s") == 0 || strcmp(argv[i], "--since") == 0) {
            show_since = 1;
        } else {
            printf("uptime: invalid option -- '%s'\n", argv[i]);
            usage(argv[0]);
            return 1;
        }
    }

    /* Get current time and monotonic time (uptime) */
    time_t now = time(NULL);

    struct timespec mono;
    if (clock_gettime(CLOCK_MONOTONIC, &mono) < 0) {
        printf("uptime: cannot get uptime\n");
        return 1;
    }

    long uptime_secs = (long)mono.tv_sec;

    if (show_since) {
        /* Calculate boot time */
        time_t boot_time = now - uptime_secs;
        struct tm tm_buf;
        struct tm *tm = localtime_r(&boot_time, &tm_buf);

        if (tm == NULL) {
            printf("uptime: cannot determine boot time\n");
            return 1;
        }

        char buf[64];
        strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", tm);
        printf("%s\n", buf);
        return 0;
    }

    if (pretty) {
        char buf[128];
        format_uptime(uptime_secs, buf, sizeof(buf), 1);
        printf("%s\n", buf);
        return 0;
    }

    /* Default format: current time + uptime */
    struct tm tm_buf;
    struct tm *tm = localtime_r(&now, &tm_buf);

    if (tm == NULL) {
        printf("uptime: cannot get current time\n");
        return 1;
    }

    char time_buf[32];
    strftime(time_buf, sizeof(time_buf), "%H:%M:%S", tm);

    char uptime_buf[64];
    format_uptime(uptime_secs, uptime_buf, sizeof(uptime_buf), 0);

    /* Simple output - no load average in this minimal implementation */
    printf(" %s %s\n", time_buf, uptime_buf);

    return 0;
}
