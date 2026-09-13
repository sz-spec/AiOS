/**
 * @file grep.c
 * @brief VOS3 grep utility - search for patterns in files
 *
 * @details Simple grep implementation for VOS3. Supports basic
 *          substring matching.
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

#define MAX_LINE    1024

/**
 * @brief Check if line contains pattern
 */
static int match(const char *line, const char *pattern, int ignore_case)
{
    if (ignore_case) {
        /* Case-insensitive search */
        const char *l = line;
        size_t plen = strlen(pattern);

        while (*l != '\0') {
            const char *p = pattern;
            const char *t = l;
            int matched = 1;

            while (*p != '\0' && *t != '\0') {
                char c1 = *t;
                char c2 = *p;

                /* Convert to lowercase */
                if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
                if (c2 >= 'A' && c2 <= 'Z') c2 += 32;

                if (c1 != c2) {
                    matched = 0;
                    break;
                }
                p++;
                t++;
            }

            if (matched && *p == '\0') {
                return 1;
            }
            l++;
        }
        return 0;
    } else {
        /* Case-sensitive search */
        return strstr(line, pattern) != NULL;
    }
}

/**
 * @brief Search for pattern in file descriptor
 */
static int grep_fd(int fd, const char *pattern, const char *filename,
                   int show_filename, int ignore_case, int invert, int count_only)
{
    char line[MAX_LINE];
    int pos = 0;
    int c;
    int match_count = 0;
    int found = 0;

    while (1) {
        ssize_t n = read(fd, &c, 1);
        if (n <= 0) {
            /* End of file or error */
            if (pos > 0) {
                /* Process remaining line */
                line[pos] = '\0';
                int m = match(line, pattern, ignore_case);
                if (invert) m = !m;
                if (m) {
                    match_count++;
                    found = 1;
                    if (!count_only) {
                        if (show_filename) {
                            printf("%s:", filename);
                        }
                        printf("%s\n", line);
                    }
                }
            }
            break;
        }

        if (c == '\n' || pos >= MAX_LINE - 1) {
            /* End of line */
            line[pos] = '\0';
            int m = match(line, pattern, ignore_case);
            if (invert) m = !m;
            if (m) {
                match_count++;
                found = 1;
                if (!count_only) {
                    if (show_filename) {
                        printf("%s:", filename);
                    }
                    printf("%s\n", line);
                }
            }
            pos = 0;
        } else {
            line[pos++] = (char)c;
        }
    }

    if (count_only) {
        if (show_filename) {
            printf("%s:", filename);
        }
        printf("%d\n", match_count);
    }

    return found ? 0 : 1;
}

/**
 * @brief Print usage
 */
static void usage(const char *progname)
{
    printf("Usage: %s [options] pattern [file...]\n", progname);
    printf("Search for pattern in files or standard input.\n");
    printf("\nOptions:\n");
    printf("  -i    Ignore case distinctions\n");
    printf("  -v    Invert match (select non-matching lines)\n");
    printf("  -c    Print only count of matching lines\n");
    printf("  -h    Suppress filename prefix\n");
    printf("  -H    Print filename prefix\n");
    printf("  --help Show this help\n");
}

int main(int argc, char *argv[])
{
    int ignore_case = 0;
    int invert = 0;
    int count_only = 0;
    int show_filename = -1;  /* -1 = auto, 0 = no, 1 = yes */
    int arg_idx = 1;
    const char *pattern = NULL;
    int result = 1;  /* No match by default */

    /* Parse options */
    while (arg_idx < argc && argv[arg_idx][0] == '-') {
        const char *opt = argv[arg_idx];

        if (strcmp(opt, "--help") == 0) {
            usage(argv[0]);
            return 0;
        }

        /* Skip the dash */
        opt++;

        while (*opt != '\0') {
            switch (*opt) {
                case 'i':
                    ignore_case = 1;
                    break;
                case 'v':
                    invert = 1;
                    break;
                case 'c':
                    count_only = 1;
                    break;
                case 'h':
                    show_filename = 0;
                    break;
                case 'H':
                    show_filename = 1;
                    break;
                default:
                    printf("grep: invalid option -- '%c'\n", *opt);
                    usage(argv[0]);
                    return 2;
            }
            opt++;
        }
        arg_idx++;
    }

    /* Get pattern */
    if (arg_idx >= argc) {
        printf("grep: missing pattern\n");
        usage(argv[0]);
        return 2;
    }

    pattern = argv[arg_idx++];

    /* Determine show_filename if auto */
    int num_files = argc - arg_idx;
    if (show_filename == -1) {
        show_filename = (num_files > 1) ? 1 : 0;
    }

    /* Process files or stdin */
    if (arg_idx >= argc) {
        /* Read from stdin */
        result = grep_fd(STDIN_FILENO, pattern, "(standard input)",
                        show_filename, ignore_case, invert, count_only);
    } else {
        /* Process each file */
        for (; arg_idx < argc; arg_idx++) {
            if (strcmp(argv[arg_idx], "-") == 0) {
                int r = grep_fd(STDIN_FILENO, pattern, "(standard input)",
                               show_filename, ignore_case, invert, count_only);
                if (r == 0) result = 0;
            } else {
                int fd = open(argv[arg_idx], O_RDONLY, 0);
                if (fd < 0) {
                    printf("grep: %s: No such file or directory\n", argv[arg_idx]);
                    continue;
                }

                int r = grep_fd(fd, pattern, argv[arg_idx],
                               show_filename, ignore_case, invert, count_only);
                if (r == 0) result = 0;

                close(fd);
            }
        }
    }

    return result;
}
