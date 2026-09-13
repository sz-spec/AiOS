/**
 * @file stdio.c
 * @brief VOS3 User-Space Standard I/O Implementation
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "string.h"
#include "unistd.h"
#include "stdlib.h"

/* ============================================================================
 * STANDARD STREAMS
 * ============================================================================ */

static FILE stdin_file = {
    .fd = STDIN_FILENO,
    .flags = 1,  /* Read */
    .eof = 0,
    .error = 0,
    .buf = (char *)0,
    .buf_size = 0,
    .buf_pos = 0,
    .buf_len = 0
};

static FILE stdout_file = {
    .fd = STDOUT_FILENO,
    .flags = 2,  /* Write */
    .eof = 0,
    .error = 0,
    .buf = (char *)0,
    .buf_size = 0,
    .buf_pos = 0,
    .buf_len = 0
};

static FILE stderr_file = {
    .fd = STDERR_FILENO,
    .flags = 2,  /* Write */
    .eof = 0,
    .error = 0,
    .buf = (char *)0,
    .buf_size = 0,
    .buf_pos = 0,
    .buf_len = 0
};

FILE *stdin = &stdin_file;
FILE *stdout = &stdout_file;
FILE *stderr = &stderr_file;

/* ============================================================================
 * CHARACTER I/O
 * ============================================================================ */

int fputc(int c, FILE *stream)
{
    unsigned char ch = (unsigned char)c;
    ssize_t ret = write(stream->fd, &ch, 1);
    if (ret != 1) {
        stream->error = 1;
        return EOF;
    }
    return c;
}

int putc(int c, FILE *stream)
{
    return fputc(c, stream);
}

int putchar(int c)
{
    return fputc(c, stdout);
}

int fgetc(FILE *stream)
{
    unsigned char c;
    ssize_t ret = read(stream->fd, &c, 1);
    if (ret <= 0) {
        if (ret == 0) {
            stream->eof = 1;
        } else {
            stream->error = 1;
        }
        return EOF;
    }
    return c;
}

int getc(FILE *stream)
{
    return fgetc(stream);
}

int getchar(void)
{
    return fgetc(stdin);
}

int ungetc(int c, FILE *stream)
{
    /* Simple implementation - not fully compliant */
    (void)stream;
    (void)c;
    return EOF;  /* Not supported */
}

/* ============================================================================
 * STRING I/O
 * ============================================================================ */

int fputs(const char *s, FILE *stream)
{
    size_t len = strlen(s);
    ssize_t ret = write(stream->fd, s, len);
    if (ret < 0 || (size_t)ret != len) {
        return EOF;
    }
    return (int)len;
}

int puts(const char *s)
{
    int ret = fputs(s, stdout);
    if (ret == EOF) {
        return EOF;
    }
    if (fputc('\n', stdout) == EOF) {
        return EOF;
    }
    return ret + 1;
}

char *fgets(char *s, int size, FILE *stream)
{
    if (size <= 0) {
        return (char *)0;
    }

    char *p = s;
    int count = 0;

    while (count < size - 1) {
        int c = fgetc(stream);
        if (c == EOF) {
            if (count == 0) {
                return (char *)0;
            }
            break;
        }
        *p++ = (char)c;
        count++;
        if (c == '\n') {
            break;
        }
    }

    *p = '\0';
    return s;
}

char *gets(char *s)
{
    /* Deprecated but simple for shell use */
    char *p = s;
    int c;

    while ((c = getchar()) != EOF && c != '\n') {
        *p++ = (char)c;
    }
    *p = '\0';

    return (c == EOF && p == s) ? (char *)0 : s;
}

/* ============================================================================
 * FORMATTED OUTPUT
 * ============================================================================ */

/* Number to string conversion helper */
static int format_number(char *buf, size_t size, unsigned long long value,
                         int base, int is_signed, int uppercase,
                         int width, int zero_pad, int left_align)
{
    static const char digits_lower[] = "0123456789abcdef";
    static const char digits_upper[] = "0123456789ABCDEF";
    const char *digits = uppercase ? digits_upper : digits_lower;

    char temp[32];
    int temp_len = 0;
    int negative = 0;

    /* Handle sign */
    if (is_signed && (long long)value < 0) {
        negative = 1;
        value = -(long long)value;
    }

    /* Convert number to string (reversed) */
    if (value == 0) {
        temp[temp_len++] = '0';
    } else {
        while (value > 0 && temp_len < (int)sizeof(temp)) {
            temp[temp_len++] = digits[value % base];
            value /= base;
        }
    }

    /* Calculate padding */
    int total_len = temp_len + (negative ? 1 : 0);
    int pad_len = (width > total_len) ? (width - total_len) : 0;

    int pos = 0;

    /* Left padding (spaces) */
    if (!left_align && !zero_pad) {
        while (pad_len > 0 && pos < (int)size - 1) {
            buf[pos++] = ' ';
            pad_len--;
        }
    }

    /* Sign */
    if (negative && pos < (int)size - 1) {
        buf[pos++] = '-';
    }

    /* Left padding (zeros) */
    if (!left_align && zero_pad) {
        while (pad_len > 0 && pos < (int)size - 1) {
            buf[pos++] = '0';
            pad_len--;
        }
    }

    /* Number digits (reversed) */
    while (temp_len > 0 && pos < (int)size - 1) {
        buf[pos++] = temp[--temp_len];
    }

    /* Right padding */
    if (left_align) {
        while (pad_len > 0 && pos < (int)size - 1) {
            buf[pos++] = ' ';
            pad_len--;
        }
    }

    return pos;
}

int vsnprintf(char *str, size_t size, const char *format, va_list ap)
{
    if (size == 0) {
        return 0;
    }

    size_t pos = 0;
    const char *fmt = format;

    while (*fmt != '\0' && pos < size - 1) {
        if (*fmt != '%') {
            str[pos++] = *fmt++;
            continue;
        }

        fmt++;  /* Skip '%' */

        /* Handle %% */
        if (*fmt == '%') {
            str[pos++] = '%';
            fmt++;
            continue;
        }

        /* Parse flags */
        int zero_pad = 0;
        int left_align = 0;

        while (*fmt == '0' || *fmt == '-') {
            if (*fmt == '0') zero_pad = 1;
            if (*fmt == '-') left_align = 1;
            fmt++;
        }

        /* Parse width */
        int width = 0;
        while (*fmt >= '0' && *fmt <= '9') {
            width = width * 10 + (*fmt - '0');
            fmt++;
        }

        /* Parse length modifier */
        int is_long = 0;
        int is_longlong = 0;
        if (*fmt == 'l') {
            is_long = 1;
            fmt++;
            if (*fmt == 'l') {
                is_longlong = 1;
                fmt++;
            }
        }

        /* Parse conversion specifier */
        switch (*fmt) {
            case 'd':
            case 'i': {
                long long val;
                if (is_longlong) {
                    val = va_arg(ap, long long);
                } else if (is_long) {
                    val = va_arg(ap, long);
                } else {
                    val = va_arg(ap, int);
                }
                pos += format_number(str + pos, size - pos, val, 10, 1, 0,
                                    width, zero_pad, left_align);
                break;
            }

            case 'u': {
                unsigned long long val;
                if (is_longlong) {
                    val = va_arg(ap, unsigned long long);
                } else if (is_long) {
                    val = va_arg(ap, unsigned long);
                } else {
                    val = va_arg(ap, unsigned int);
                }
                pos += format_number(str + pos, size - pos, val, 10, 0, 0,
                                    width, zero_pad, left_align);
                break;
            }

            case 'x': {
                unsigned long long val;
                if (is_longlong) {
                    val = va_arg(ap, unsigned long long);
                } else if (is_long) {
                    val = va_arg(ap, unsigned long);
                } else {
                    val = va_arg(ap, unsigned int);
                }
                pos += format_number(str + pos, size - pos, val, 16, 0, 0,
                                    width, zero_pad, left_align);
                break;
            }

            case 'X': {
                unsigned long long val;
                if (is_longlong) {
                    val = va_arg(ap, unsigned long long);
                } else if (is_long) {
                    val = va_arg(ap, unsigned long);
                } else {
                    val = va_arg(ap, unsigned int);
                }
                pos += format_number(str + pos, size - pos, val, 16, 0, 1,
                                    width, zero_pad, left_align);
                break;
            }

            case 'p': {
                void *ptr = va_arg(ap, void *);
                if (pos < size - 1) str[pos++] = '0';
                if (pos < size - 1) str[pos++] = 'x';
                pos += format_number(str + pos, size - pos,
                                    (unsigned long long)(uintptr_t)ptr,
                                    16, 0, 0, sizeof(void *) * 2, 1, 0);
                break;
            }

            case 'c': {
                int c = va_arg(ap, int);
                str[pos++] = (char)c;
                break;
            }

            case 's': {
                const char *s = va_arg(ap, const char *);
                if (s == (const char *)0) {
                    s = "(null)";
                }
                size_t len = strlen(s);

                /* Padding */
                int pad = (width > (int)len) ? (width - (int)len) : 0;

                if (!left_align) {
                    while (pad > 0 && pos < size - 1) {
                        str[pos++] = ' ';
                        pad--;
                    }
                }

                while (*s != '\0' && pos < size - 1) {
                    str[pos++] = *s++;
                }

                if (left_align) {
                    while (pad > 0 && pos < size - 1) {
                        str[pos++] = ' ';
                        pad--;
                    }
                }
                break;
            }

            default:
                /* Unknown format, just output as-is */
                if (pos < size - 1) str[pos++] = '%';
                if (pos < size - 1) str[pos++] = *fmt;
                break;
        }

        fmt++;
    }

    str[pos] = '\0';
    return (int)pos;
}

int vsprintf(char *str, const char *format, va_list ap)
{
    return vsnprintf(str, (size_t)-1, format, ap);
}

int snprintf(char *str, size_t size, const char *format, ...)
{
    va_list ap;
    va_start(ap, format);
    int ret = vsnprintf(str, size, format, ap);
    va_end(ap);
    return ret;
}

int sprintf(char *str, const char *format, ...)
{
    va_list ap;
    va_start(ap, format);
    int ret = vsprintf(str, format, ap);
    va_end(ap);
    return ret;
}

int vfprintf(FILE *stream, const char *format, va_list ap)
{
    char buf[1024];
    int len = vsnprintf(buf, sizeof(buf), format, ap);
    if (len > 0) {
        ssize_t ret = write(stream->fd, buf, (size_t)len);
        if (ret < 0) {
            return -1;
        }
    }
    return len;
}

int vprintf(const char *format, va_list ap)
{
    return vfprintf(stdout, format, ap);
}

int fprintf(FILE *stream, const char *format, ...)
{
    va_list ap;
    va_start(ap, format);
    int ret = vfprintf(stream, format, ap);
    va_end(ap);
    return ret;
}

int printf(const char *format, ...)
{
    va_list ap;
    va_start(ap, format);
    int ret = vprintf(format, ap);
    va_end(ap);
    return ret;
}

/* ============================================================================
 * FILE OPERATIONS
 * ============================================================================ */

/* Open flags */
#define O_RDONLY    0x0000
#define O_WRONLY    0x0001
#define O_RDWR      0x0002
#define O_CREAT     0x0040
#define O_TRUNC     0x0200
#define O_APPEND    0x0400

extern int open(const char *pathname, int flags, unsigned int mode);

FILE *fopen(const char *pathname, const char *mode)
{
    int flags = 0;
    int file_mode = 0644;

    if (strcmp(mode, "r") == 0 || strcmp(mode, "rb") == 0) {
        flags = O_RDONLY;
    } else if (strcmp(mode, "w") == 0 || strcmp(mode, "wb") == 0) {
        flags = O_WRONLY | O_CREAT | O_TRUNC;
    } else if (strcmp(mode, "a") == 0 || strcmp(mode, "ab") == 0) {
        flags = O_WRONLY | O_CREAT | O_APPEND;
    } else if (strcmp(mode, "r+") == 0 || strcmp(mode, "rb+") == 0) {
        flags = O_RDWR;
    } else if (strcmp(mode, "w+") == 0 || strcmp(mode, "wb+") == 0) {
        flags = O_RDWR | O_CREAT | O_TRUNC;
    } else if (strcmp(mode, "a+") == 0 || strcmp(mode, "ab+") == 0) {
        flags = O_RDWR | O_CREAT | O_APPEND;
    } else {
        return (FILE *)0;
    }

    int fd = open(pathname, flags, file_mode);
    if (fd < 0) {
        return (FILE *)0;
    }

    FILE *f = (FILE *)malloc(sizeof(FILE));
    if (f == (FILE *)0) {
        close(fd);
        return (FILE *)0;
    }

    f->fd = fd;
    f->flags = flags;
    f->eof = 0;
    f->error = 0;
    f->buf = (char *)0;
    f->buf_size = 0;
    f->buf_pos = 0;
    f->buf_len = 0;

    return f;
}

int fclose(FILE *stream)
{
    if (stream == (FILE *)0) {
        return EOF;
    }

    int ret = close(stream->fd);

    /* Don't free standard streams */
    if (stream != stdin && stream != stdout && stream != stderr) {
        if (stream->buf != (char *)0) {
            free(stream->buf);
        }
        free(stream);
    }

    return (ret < 0) ? EOF : 0;
}

size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream)
{
    size_t total = size * nmemb;
    if (total == 0) {
        return 0;
    }

    ssize_t ret = read(stream->fd, ptr, total);
    if (ret <= 0) {
        if (ret == 0) {
            stream->eof = 1;
        } else {
            stream->error = 1;
        }
        return 0;
    }

    return (size_t)ret / size;
}

size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream)
{
    size_t total = size * nmemb;
    if (total == 0) {
        return 0;
    }

    ssize_t ret = write(stream->fd, ptr, total);
    if (ret < 0) {
        stream->error = 1;
        return 0;
    }

    return (size_t)ret / size;
}

int fseek(FILE *stream, long offset, int whence)
{
    off_t ret = lseek(stream->fd, offset, whence);
    if (ret < 0) {
        return -1;
    }
    stream->eof = 0;
    return 0;
}

long ftell(FILE *stream)
{
    return (long)lseek(stream->fd, 0, SEEK_CUR);
}

void rewind(FILE *stream)
{
    fseek(stream, 0, SEEK_SET);
    stream->error = 0;
}

int fflush(FILE *stream)
{
    /* No buffering implemented */
    (void)stream;
    return 0;
}

/* ============================================================================
 * ERROR HANDLING
 * ============================================================================ */

int feof(FILE *stream)
{
    return stream->eof;
}

int ferror(FILE *stream)
{
    return stream->error;
}

void clearerr(FILE *stream)
{
    stream->eof = 0;
    stream->error = 0;
}

void perror(const char *s)
{
    /* Simple implementation */
    if (s != (const char *)0 && *s != '\0') {
        fputs(s, stderr);
        fputs(": ", stderr);
    }
    fputs("Error\n", stderr);
}

int fileno(FILE *stream)
{
    return stream->fd;
}

FILE *fdopen(int fd, const char *mode)
{
    (void)mode;

    FILE *f = (FILE *)malloc(sizeof(FILE));
    if (f == (FILE *)0) {
        return (FILE *)0;
    }

    f->fd = fd;
    f->flags = 3;  /* Read/write */
    f->eof = 0;
    f->error = 0;
    f->buf = (char *)0;
    f->buf_size = 0;
    f->buf_pos = 0;
    f->buf_len = 0;

    return f;
}
