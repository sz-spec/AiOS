/**
 * @file date.c
 * @brief VOS3 date utility - display or set date/time
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
    printf("Usage: %s [options] [+format]\n", progname);
    printf("Display the current date and time.\n");
    printf("\nOptions:\n");
    printf("  -u, --utc    Display time in UTC\n");
    printf("  -I           Output in ISO 8601 format\n");
    printf("  -R           Output in RFC 2822 format\n");
    printf("  --help       Show this help\n");
    printf("\nFormat specifiers:\n");
    printf("  %%a  Abbreviated weekday name\n");
    printf("  %%A  Full weekday name\n");
    printf("  %%b  Abbreviated month name\n");
    printf("  %%B  Full month name\n");
    printf("  %%d  Day of month (01-31)\n");
    printf("  %%H  Hour (00-23)\n");
    printf("  %%I  Hour (01-12)\n");
    printf("  %%m  Month (01-12)\n");
    printf("  %%M  Minute (00-59)\n");
    printf("  %%p  AM/PM\n");
    printf("  %%S  Second (00-60)\n");
    printf("  %%Y  Year with century\n");
    printf("  %%y  Year without century\n");
    printf("  %%Z  Timezone name\n");
    printf("  %%%%  Literal %%\n");
}

int main(int argc, char *argv[])
{
    int use_utc = 1;  /* Always UTC for now */
    int iso_format = 0;
    int rfc_format = 0;
    const char *format = NULL;

    /* Parse options */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "-u") == 0 || strcmp(argv[i], "--utc") == 0) {
            use_utc = 1;
        } else if (strcmp(argv[i], "-I") == 0) {
            iso_format = 1;
        } else if (strcmp(argv[i], "-R") == 0) {
            rfc_format = 1;
        } else if (argv[i][0] == '+') {
            format = &argv[i][1];
        } else {
            printf("date: invalid option -- '%s'\n", argv[i]);
            usage(argv[0]);
            return 1;
        }
    }

    /* Get current time */
    time_t now = time(NULL);
    struct tm tm_buf;
    struct tm *tm;

    if (use_utc) {
        tm = gmtime_r(&now, &tm_buf);
    } else {
        tm = localtime_r(&now, &tm_buf);
    }

    if (tm == NULL) {
        printf("date: cannot get current time\n");
        return 1;
    }

    char buf[256];

    if (format != NULL) {
        /* Custom format */
        strftime(buf, sizeof(buf), format, tm);
        printf("%s\n", buf);
    } else if (iso_format) {
        /* ISO 8601 format: 2026-02-16T12:30:45+00:00 */
        strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", tm);
        printf("%s+00:00\n", buf);
    } else if (rfc_format) {
        /* RFC 2822 format: Mon, 16 Feb 2026 12:30:45 +0000 */
        strftime(buf, sizeof(buf), "%a, %d %b %Y %H:%M:%S +0000", tm);
        printf("%s\n", buf);
    } else {
        /* Default format: Mon Feb 16 12:30:45 UTC 2026 */
        strftime(buf, sizeof(buf), "%a %b %e %H:%M:%S", tm);
        printf("%s %s %d\n", buf, use_utc ? "UTC" : "local", tm->tm_year + 1900);
    }

    return 0;
}
