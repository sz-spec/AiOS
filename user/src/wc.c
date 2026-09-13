/**
 * @file wc.c
 * @brief VOS3 wc utility - word, line, and character count
 *
 * @details Simple wc implementation for VOS3.
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
 * @brief Count lines, words, and characters in file descriptor
 */
static void wc_fd(int fd, const char *filename, int show_lines, int show_words,
                  int show_chars, long *total_lines, long *total_words, long *total_chars)
{
    char buf[512];
    ssize_t n;
    long lines = 0;
    long words = 0;
    long chars = 0;
    int in_word = 0;

    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        chars += n;

        for (ssize_t i = 0; i < n; i++) {
            char c = buf[i];

            if (c == '\n') {
                lines++;
            }

            /* Check for word boundaries */
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                if (in_word) {
                    words++;
                    in_word = 0;
                }
            } else {
                in_word = 1;
            }
        }
    }

    /* Count final word if file doesn't end with whitespace */
    if (in_word) {
        words++;
    }

    /* Print counts */
    if (show_lines) {
        printf("%7ld", lines);
    }
    if (show_words) {
        printf("%7ld", words);
    }
    if (show_chars) {
        printf("%7ld", chars);
    }
    if (filename != NULL) {
        printf(" %s", filename);
    }
    printf("\n");

    /* Update totals */
    *total_lines += lines;
    *total_words += words;
    *total_chars += chars;
}

/**
 * @brief Print usage
 */
static void usage(const char *progname)
{
    printf("Usage: %s [options] [file...]\n", progname);
    printf("Print line, word, and character counts.\n");
    printf("\nOptions:\n");
    printf("  -l    Print line count only\n");
    printf("  -w    Print word count only\n");
    printf("  -c    Print character count only\n");
    printf("  --help Show this help\n");
}

int main(int argc, char *argv[])
{
    int show_lines = 0;
    int show_words = 0;
    int show_chars = 0;
    int arg_idx = 1;
    long total_lines = 0;
    long total_words = 0;
    long total_chars = 0;
    int num_files = 0;

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
                case 'l':
                    show_lines = 1;
                    break;
                case 'w':
                    show_words = 1;
                    break;
                case 'c':
                    show_chars = 1;
                    break;
                default:
                    printf("wc: invalid option -- '%c'\n", *opt);
                    usage(argv[0]);
                    return 1;
            }
            opt++;
        }
        arg_idx++;
    }

    /* Default: show all */
    if (!show_lines && !show_words && !show_chars) {
        show_lines = 1;
        show_words = 1;
        show_chars = 1;
    }

    /* Count files */
    num_files = argc - arg_idx;

    /* Process files or stdin */
    if (num_files == 0) {
        /* Read from stdin */
        wc_fd(STDIN_FILENO, NULL, show_lines, show_words, show_chars,
              &total_lines, &total_words, &total_chars);
    } else {
        /* Process each file */
        for (; arg_idx < argc; arg_idx++) {
            if (strcmp(argv[arg_idx], "-") == 0) {
                wc_fd(STDIN_FILENO, "-", show_lines, show_words, show_chars,
                      &total_lines, &total_words, &total_chars);
            } else {
                int fd = open(argv[arg_idx], O_RDONLY, 0);
                if (fd < 0) {
                    printf("wc: %s: No such file or directory\n", argv[arg_idx]);
                    continue;
                }

                wc_fd(fd, argv[arg_idx], show_lines, show_words, show_chars,
                      &total_lines, &total_words, &total_chars);

                close(fd);
            }
        }

        /* Print totals if multiple files */
        if (num_files > 1) {
            if (show_lines) {
                printf("%7ld", total_lines);
            }
            if (show_words) {
                printf("%7ld", total_words);
            }
            if (show_chars) {
                printf("%7ld", total_chars);
            }
            printf(" total\n");
        }
    }

    return 0;
}
