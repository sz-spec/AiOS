/**
 * @file stdlib.h
 * @brief VOS3 User-Space Standard Library
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_STDLIB_H
#define VOS3_USER_STDLIB_H

#include <stddef.h>
#include <stdint.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define EXIT_SUCCESS    0
#define EXIT_FAILURE    1

#define RAND_MAX        32767

/* ============================================================================
 * MEMORY ALLOCATION
 * ============================================================================ */

void *malloc(size_t size);
void *calloc(size_t nmemb, size_t size);
void *realloc(void *ptr, size_t size);
void free(void *ptr);

/* ============================================================================
 * PROCESS CONTROL
 * ============================================================================ */

void exit(int status) __attribute__((noreturn));
void abort(void) __attribute__((noreturn));
int atexit(void (*function)(void));

/* ============================================================================
 * WAIT STATUS MACROS (typically in sys/wait.h)
 * ============================================================================ */

/* Wait status bits */
#define WNOHANG         1
#define WUNTRACED       2

/* Status decoding macros */
#define WIFEXITED(s)    (((s) & 0x7f) == 0)
#define WEXITSTATUS(s)  (((s) >> 8) & 0xff)
#define WIFSIGNALED(s)  (((s) & 0x7f) > 0 && ((s) & 0x7f) < 0x7f)
#define WTERMSIG(s)     ((s) & 0x7f)
#define WIFSTOPPED(s)   (((s) & 0xff) == 0x7f)
#define WSTOPSIG(s)     (((s) >> 8) & 0xff)
#define WIFCONTINUED(s) ((s) == 0xffff)

/* ============================================================================
 * STRING CONVERSION
 * ============================================================================ */

int atoi(const char *nptr);
long atol(const char *nptr);
long long atoll(const char *nptr);

long strtol(const char *nptr, char **endptr, int base);
long long strtoll(const char *nptr, char **endptr, int base);
unsigned long strtoul(const char *nptr, char **endptr, int base);
unsigned long long strtoull(const char *nptr, char **endptr, int base);

/* ============================================================================
 * PSEUDO-RANDOM NUMBERS
 * ============================================================================ */

int rand(void);
void srand(unsigned int seed);

/* ============================================================================
 * SEARCHING AND SORTING
 * ============================================================================ */

void *bsearch(const void *key, const void *base, size_t nmemb, size_t size,
              int (*compar)(const void *, const void *));
void qsort(void *base, size_t nmemb, size_t size,
           int (*compar)(const void *, const void *));

/* ============================================================================
 * INTEGER ARITHMETIC
 * ============================================================================ */

int abs(int j);
long labs(long j);
long long llabs(long long j);

typedef struct {
    int quot;
    int rem;
} div_t;

typedef struct {
    long quot;
    long rem;
} ldiv_t;

div_t div(int numer, int denom);
ldiv_t ldiv(long numer, long denom);

/* ============================================================================
 * ENVIRONMENT
 * ============================================================================ */

char *getenv(const char *name);
int setenv(const char *name, const char *value, int overwrite);
int unsetenv(const char *name);

/* ============================================================================
 * MISC
 * ============================================================================ */

int system(const char *command);

#endif /* VOS3_USER_STDLIB_H */
