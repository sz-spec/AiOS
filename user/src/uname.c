/**
 * @file uname.c
 * @brief VOS3 uname utility - print system information
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

/* Flags for what to print */
#define PRINT_SYSNAME   (1 << 0)
#define PRINT_NODENAME  (1 << 1)
#define PRINT_RELEASE   (1 << 2)
#define PRINT_VERSION   (1 << 3)
#define PRINT_MACHINE   (1 << 4)
#define PRINT_ALL       (PRINT_SYSNAME | PRINT_NODENAME | PRINT_RELEASE | \
                         PRINT_VERSION | PRINT_MACHINE)

/**
 * @brief Print usage
 */
static void usage(const char *progname)
{
    printf("Usage: %s [options]\n", progname);
    printf("Print system information.\n");
    printf("\nOptions:\n");
    printf("  -a, --all         Print all information\n");
    printf("  -s, --kernel-name Print kernel name\n");
    printf("  -n, --nodename    Print network node hostname\n");
    printf("  -r, --release     Print kernel release\n");
    printf("  -v, --version     Print kernel version\n");
    printf("  -m, --machine     Print machine hardware name\n");
    printf("  --help            Show this help\n");
}

int main(int argc, char *argv[])
{
    int flags = 0;

    /* Parse options */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "-a") == 0 || strcmp(argv[i], "--all") == 0) {
            flags = PRINT_ALL;
        } else if (strcmp(argv[i], "-s") == 0 || strcmp(argv[i], "--kernel-name") == 0) {
            flags |= PRINT_SYSNAME;
        } else if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--nodename") == 0) {
            flags |= PRINT_NODENAME;
        } else if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--release") == 0) {
            flags |= PRINT_RELEASE;
        } else if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) {
            flags |= PRINT_VERSION;
        } else if (strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--machine") == 0) {
            flags |= PRINT_MACHINE;
        } else {
            printf("uname: invalid option -- '%s'\n", argv[i]);
            usage(argv[0]);
            return 1;
        }
    }

    /* Default: print system name only */
    if (flags == 0) {
        flags = PRINT_SYSNAME;
    }

    /* Get system information */
    struct utsname buf;
    if (uname(&buf) < 0) {
        printf("uname: cannot get system information\n");
        return 1;
    }

    /* Print requested fields */
    int need_space = 0;

    if (flags & PRINT_SYSNAME) {
        if (need_space) putchar(' ');
        printf("%s", buf.sysname);
        need_space = 1;
    }

    if (flags & PRINT_NODENAME) {
        if (need_space) putchar(' ');
        printf("%s", buf.nodename);
        need_space = 1;
    }

    if (flags & PRINT_RELEASE) {
        if (need_space) putchar(' ');
        printf("%s", buf.release);
        need_space = 1;
    }

    if (flags & PRINT_VERSION) {
        if (need_space) putchar(' ');
        printf("%s", buf.version);
        need_space = 1;
    }

    if (flags & PRINT_MACHINE) {
        if (need_space) putchar(' ');
        printf("%s", buf.machine);
        need_space = 1;
    }

    putchar('\n');

    return 0;
}
