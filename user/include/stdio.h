/**
 * @file stdio.h
 * @brief VOS3 User-Space Standard I/O
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_STDIO_H
#define VOS3_USER_STDIO_H

#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define EOF         (-1)
#define BUFSIZ      1024

/* ============================================================================
 * FILE STRUCTURE (simplified)
 * ============================================================================ */

typedef struct _FILE {
    int     fd;         /* File descriptor */
    int     flags;      /* Mode flags */
    int     eof;        /* EOF indicator */
    int     error;      /* Error indicator */
    char    *buf;       /* Buffer */
    size_t  buf_size;   /* Buffer size */
    size_t  buf_pos;    /* Current position in buffer */
    size_t  buf_len;    /* Amount of data in buffer */
} FILE;

/* Standard streams */
extern FILE *stdin;
extern FILE *stdout;
extern FILE *stderr;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/* Character I/O */
int putchar(int c);
int getchar(void);
int putc(int c, FILE *stream);
int getc(FILE *stream);
int fputc(int c, FILE *stream);
int fgetc(FILE *stream);
int ungetc(int c, FILE *stream);

/* String I/O */
int puts(const char *s);
char *gets(char *s);  /* Deprecated but simple for shell */
char *fgets(char *s, int size, FILE *stream);
int fputs(const char *s, FILE *stream);

/* Formatted I/O */
int printf(const char *format, ...);
int fprintf(FILE *stream, const char *format, ...);
int sprintf(char *str, const char *format, ...);
int snprintf(char *str, size_t size, const char *format, ...);
int vprintf(const char *format, va_list ap);
int vfprintf(FILE *stream, const char *format, va_list ap);
int vsprintf(char *str, const char *format, va_list ap);
int vsnprintf(char *str, size_t size, const char *format, va_list ap);

/* File operations */
FILE *fopen(const char *pathname, const char *mode);
int fclose(FILE *stream);
size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream);
size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream);
int fseek(FILE *stream, long offset, int whence);
long ftell(FILE *stream);
void rewind(FILE *stream);
int fflush(FILE *stream);

/* Error handling */
int feof(FILE *stream);
int ferror(FILE *stream);
void clearerr(FILE *stream);
void perror(const char *s);

/* File descriptor access */
int fileno(FILE *stream);
FILE *fdopen(int fd, const char *mode);

#endif /* VOS3_USER_STDIO_H */
