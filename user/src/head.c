/**
 * @file head.c
 * @brief VOS3 head utility - output first part of files
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
#include "unistd.h"

/* Open flags (must match kernel) */
#define O_RDONLY    0x0000

/* External open function */
extern int open(const char *pathname, int flags, unsigned int mode);

/**
 * @brief Output first n lines from file descriptor
 */
static int head_fd(int fd, int num_lines)
{
    char c;
    int lines = 0;

    while (lines < num_lines) {
        ssize_t n = read(fd, &c, 1);
        if (n <= 0) {
            break;
        }

        putchar(c);

        if (c == '\n') {
            lines++;
        }
    }

    return 0;
}

/**
 * @brief Print usage
 */
static void usage(const char *progname)
{
    printf("Usage: %s [options] [file...]\n", progname);
    printf("Print first 10 lines of each file.\n");
    printf("\nOptions:\n");
    printf("  -n NUM  Print first NUM lines\n");
    printf("  --help  Show this help\n");
}

int main(int argc, char *argv[])
{
    int num_lines = 10;
    int arg_idx = 1;
    int num_files = 0;

    /* Parse options */
    while (arg_idx < argc && argv[arg_idx][0] == '-') {
        const char *opt = argv[arg_idx];

        if (strcmp(opt, "--help") == 0) {
            usage(argv[0]);
            return 0;
        }

        if (strcmp(opt, "-n") == 0) {
            arg_idx++;
            if (arg_idx >= argc) {
                printf("head: option requires an argument -- 'n'\n");
                return 1;
            }
            num_lines = atoi(argv[arg_idx]);
            if (num_lines <= 0) {
                printf("head: invalid number of lines: '%s'\n", argv[arg_idx]);
                return 1;
            }
            arg_idx++;
            continue;
        }

        /* Handle -N format */
        if (opt[1] >= '0' && opt[1] <= '9') {
            num_lines = atoi(&opt[1]);
            if (num_lines <= 0) {
                printf("head: invalid number of lines\n");
                return 1;
            }
            arg_idx++;
            continue;
        }

        printf("head: invalid option -- '%s'\n", opt);
        usage(argv[0]);
        return 1;
    }

    /* Count files */
    num_files = argc - arg_idx;

    /* Process files or stdin */
    if (num_files == 0) {
        head_fd(STDIN_FILENO, num_lines);
    } else {
        for (int i = arg_idx; i < argc; i++) {
            if (num_files > 1) {
                if (i > arg_idx) {
                    printf("\n");
                }
                printf("==> %s <==\n", argv[i]);
            }

            if (strcmp(argv[i], "-") == 0) {
                head_fd(STDIN_FILENO, num_lines);
            } else {
                int fd = open(argv[i], O_RDONLY, 0);
                if (fd < 0) {
                    printf("head: %s: No such file or directory\n", argv[i]);
                    continue;
                }

                head_fd(fd, num_lines);
                close(fd);
            }
        }
    }

    return 0;
}
