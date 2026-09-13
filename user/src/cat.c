/**
 * @file cat.c
 * @brief VOS3 cat utility - concatenate files to stdout
 *
 * @details Simple implementation of cat for VOS3. Supports reading
 *          from files or stdin.
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
 * @brief Copy file descriptor to stdout
 */
static int cat_fd(int fd)
{
    char buf[512];
    ssize_t n;

    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        ssize_t written = 0;
        while (written < n) {
            ssize_t w = write(STDOUT_FILENO, buf + written, (size_t)(n - written));
            if (w < 0) {
                return 1;
            }
            written += w;
        }
    }

    return (n < 0) ? 1 : 0;
}

/**
 * @brief Print usage
 */
static void usage(const char *progname)
{
    printf("Usage: %s [file...]\n", progname);
    printf("Concatenate files to standard output.\n");
    printf("With no file, read from standard input.\n");
}

int main(int argc, char *argv[])
{
    int result = 0;

    /* Check for help */
    if (argc > 1 && (strcmp(argv[1], "-h") == 0 || strcmp(argv[1], "--help") == 0)) {
        usage(argv[0]);
        return 0;
    }

    /* No arguments - read from stdin */
    if (argc == 1) {
        return cat_fd(STDIN_FILENO);
    }

    /* Process each file */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-") == 0) {
            /* "-" means stdin */
            if (cat_fd(STDIN_FILENO) != 0) {
                result = 1;
            }
        } else {
            int fd = open(argv[i], O_RDONLY, 0);
            if (fd < 0) {
                printf("cat: %s: No such file or directory\n", argv[i]);
                result = 1;
                continue;
            }

            if (cat_fd(fd) != 0) {
                result = 1;
            }

            close(fd);
        }
    }

    return result;
}
