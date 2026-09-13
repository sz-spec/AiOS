/**
 * @file stdlib.c
 * @brief VOS3 User-Space Standard Library Implementation
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* ============================================================================
 * SIMPLE HEAP ALLOCATOR
 * ============================================================================ */

/* Simple bump allocator with free list */

#define HEAP_MAGIC  0xDEADBEEF
#define ALIGN(x)    (((x) + 15) & ~15)

typedef struct heap_block {
    size_t              size;       /* Size including header */
    uint32_t            magic;      /* Magic number */
    uint32_t            free;       /* Is block free? */
    struct heap_block   *next;      /* Next block */
    struct heap_block   *prev;      /* Previous block */
} heap_block_t;

static heap_block_t *heap_start = (heap_block_t *)0;
static heap_block_t *heap_end = (heap_block_t *)0;
static void *heap_break = (void *)0;

/* Get program break */
static void *sbrk(intptr_t increment)
{
    if (heap_break == (void *)0) {
        /* Initialize heap break */
        long ret = syscall1(SYS_BRK, 0);
        if (ret < 0) {
            return (void *)-1;
        }
        heap_break = (void *)ret;
    }

    if (increment == 0) {
        return heap_break;
    }

    void *old_break = heap_break;
    void *new_break = (void *)((char *)heap_break + increment);

    long ret = syscall1(SYS_BRK, (long)new_break);
    if (ret < 0 || (void *)ret != new_break) {
        return (void *)-1;
    }

    heap_break = new_break;
    return old_break;
}

/* Find a free block that fits */
static heap_block_t *find_free_block(size_t size)
{
    heap_block_t *block = heap_start;
    while (block != (heap_block_t *)0) {
        if (block->free && block->size >= size) {
            return block;
        }
        block = block->next;
    }
    return (heap_block_t *)0;
}

/* Extend heap with new block */
static heap_block_t *extend_heap(size_t size)
{
    heap_block_t *block = (heap_block_t *)sbrk((intptr_t)size);
    if (block == (void *)-1) {
        return (heap_block_t *)0;
    }

    block->size = size;
    block->magic = HEAP_MAGIC;
    block->free = 0;
    block->next = (heap_block_t *)0;
    block->prev = heap_end;

    if (heap_end != (heap_block_t *)0) {
        heap_end->next = block;
    }
    heap_end = block;

    if (heap_start == (heap_block_t *)0) {
        heap_start = block;
    }

    return block;
}

/* Split a block if it's too large */
static void split_block(heap_block_t *block, size_t size)
{
    size_t remaining = block->size - size;
    if (remaining >= sizeof(heap_block_t) + 16) {
        heap_block_t *new_block = (heap_block_t *)((char *)block + size);
        new_block->size = remaining;
        new_block->magic = HEAP_MAGIC;
        new_block->free = 1;
        new_block->next = block->next;
        new_block->prev = block;

        if (block->next != (heap_block_t *)0) {
            block->next->prev = new_block;
        } else {
            heap_end = new_block;
        }

        block->next = new_block;
        block->size = size;
    }
}

/* Merge adjacent free blocks */
static void coalesce(heap_block_t *block)
{
    /* Merge with next */
    if (block->next != (heap_block_t *)0 && block->next->free) {
        block->size += block->next->size;
        block->next = block->next->next;
        if (block->next != (heap_block_t *)0) {
            block->next->prev = block;
        } else {
            heap_end = block;
        }
    }

    /* Merge with previous */
    if (block->prev != (heap_block_t *)0 && block->prev->free) {
        block->prev->size += block->size;
        block->prev->next = block->next;
        if (block->next != (heap_block_t *)0) {
            block->next->prev = block->prev;
        } else {
            heap_end = block->prev;
        }
    }
}

void *malloc(size_t size)
{
    if (size == 0) {
        return (void *)0;
    }

    /* Align size and add header */
    size_t total_size = ALIGN(size + sizeof(heap_block_t));

    /* Look for free block */
    heap_block_t *block = find_free_block(total_size);

    if (block != (heap_block_t *)0) {
        block->free = 0;
        split_block(block, total_size);
    } else {
        /* Extend heap */
        block = extend_heap(total_size);
        if (block == (heap_block_t *)0) {
            return (void *)0;
        }
    }

    /* Return pointer after header */
    return (void *)(block + 1);
}

void *calloc(size_t nmemb, size_t size)
{
    size_t total = nmemb * size;
    if (nmemb != 0 && total / nmemb != size) {
        return (void *)0;  /* Overflow */
    }

    void *ptr = malloc(total);
    if (ptr != (void *)0) {
        memset(ptr, 0, total);
    }
    return ptr;
}

void *realloc(void *ptr, size_t size)
{
    if (ptr == (void *)0) {
        return malloc(size);
    }

    if (size == 0) {
        free(ptr);
        return (void *)0;
    }

    heap_block_t *block = (heap_block_t *)ptr - 1;
    if (block->magic != HEAP_MAGIC) {
        return (void *)0;  /* Invalid pointer */
    }

    size_t old_size = block->size - sizeof(heap_block_t);
    if (size <= old_size) {
        return ptr;  /* Already big enough */
    }

    /* Allocate new block and copy */
    void *new_ptr = malloc(size);
    if (new_ptr != (void *)0) {
        memcpy(new_ptr, ptr, old_size);
        free(ptr);
    }
    return new_ptr;
}

void free(void *ptr)
{
    if (ptr == (void *)0) {
        return;
    }

    heap_block_t *block = (heap_block_t *)ptr - 1;
    if (block->magic != HEAP_MAGIC) {
        return;  /* Invalid pointer or already freed */
    }

    block->free = 1;
    coalesce(block);
}

/* ============================================================================
 * PROCESS CONTROL
 * ============================================================================ */

static void (*atexit_funcs[32])(void);
static int atexit_count = 0;

int atexit(void (*function)(void))
{
    if (atexit_count >= 32) {
        return -1;
    }
    atexit_funcs[atexit_count++] = function;
    return 0;
}

void exit(int status)
{
    /* Call atexit handlers in reverse order */
    while (atexit_count > 0) {
        atexit_funcs[--atexit_count]();
    }

    _exit(status);
}

void abort(void)
{
    /* TODO: Send SIGABRT */
    _exit(134);  /* 128 + SIGABRT(6) */
}

/* ============================================================================
 * STRING CONVERSION
 * ============================================================================ */

static int isspace_char(int c)
{
    return c == ' ' || c == '\t' || c == '\n' ||
           c == '\r' || c == '\f' || c == '\v';
}

static int isdigit_char(int c)
{
    return c >= '0' && c <= '9';
}

long strtol(const char *nptr, char **endptr, int base)
{
    const char *s = nptr;
    long result = 0;
    int negative = 0;

    /* Skip whitespace */
    while (isspace_char(*s)) {
        s++;
    }

    /* Handle sign */
    if (*s == '-') {
        negative = 1;
        s++;
    } else if (*s == '+') {
        s++;
    }

    /* Handle base prefix */
    if (base == 0 || base == 16) {
        if (*s == '0' && (s[1] == 'x' || s[1] == 'X')) {
            if (base == 0) base = 16;
            s += 2;
        } else if (base == 0 && *s == '0') {
            base = 8;
            s++;
        } else if (base == 0) {
            base = 10;
        }
    }

    /* Convert digits */
    while (*s != '\0') {
        int digit;
        if (isdigit_char(*s)) {
            digit = *s - '0';
        } else if (*s >= 'a' && *s <= 'z') {
            digit = *s - 'a' + 10;
        } else if (*s >= 'A' && *s <= 'Z') {
            digit = *s - 'A' + 10;
        } else {
            break;
        }

        if (digit >= base) {
            break;
        }

        result = result * base + digit;
        s++;
    }

    if (endptr != (char **)0) {
        *endptr = (char *)s;
    }

    return negative ? -result : result;
}

long long strtoll(const char *nptr, char **endptr, int base)
{
    return (long long)strtol(nptr, endptr, base);
}

unsigned long strtoul(const char *nptr, char **endptr, int base)
{
    /* Simple implementation - reuse strtol */
    return (unsigned long)strtol(nptr, endptr, base);
}

unsigned long long strtoull(const char *nptr, char **endptr, int base)
{
    return (unsigned long long)strtoul(nptr, endptr, base);
}

int atoi(const char *nptr)
{
    return (int)strtol(nptr, (char **)0, 10);
}

long atol(const char *nptr)
{
    return strtol(nptr, (char **)0, 10);
}

long long atoll(const char *nptr)
{
    return strtoll(nptr, (char **)0, 10);
}

/* ============================================================================
 * RANDOM NUMBERS
 * ============================================================================ */

static unsigned int rand_seed = 1;

void srand(unsigned int seed)
{
    rand_seed = seed;
}

int rand(void)
{
    /* Linear congruential generator */
    rand_seed = rand_seed * 1103515245 + 12345;
    return (int)((rand_seed >> 16) & RAND_MAX);
}

/* ============================================================================
 * INTEGER ARITHMETIC
 * ============================================================================ */

int abs(int j)
{
    return (j < 0) ? -j : j;
}

long labs(long j)
{
    return (j < 0) ? -j : j;
}

long long llabs(long long j)
{
    return (j < 0) ? -j : j;
}

div_t div(int numer, int denom)
{
    div_t result;
    result.quot = numer / denom;
    result.rem = numer % denom;
    return result;
}

ldiv_t ldiv(long numer, long denom)
{
    ldiv_t result;
    result.quot = numer / denom;
    result.rem = numer % denom;
    return result;
}

/* ============================================================================
 * ENVIRONMENT (stub implementation)
 * ============================================================================ */

static char *env_vars[64] = { (char *)0 };
static int env_count = 0;

char *getenv(const char *name)
{
    size_t name_len = strlen(name);
    for (int i = 0; i < env_count && i < 64; i++) {
        if (env_vars[i] == (char *)0) {
            continue;  /* Skip NULL entries */
        }
        if (strncmp(env_vars[i], name, name_len) == 0 &&
            env_vars[i][name_len] == '=') {
            return env_vars[i] + name_len + 1;
        }
    }
    return (char *)0;
}

int setenv(const char *name, const char *value, int overwrite)
{
    /* Check if already exists */
    size_t name_len = strlen(name);
    for (int i = 0; i < env_count && i < 64; i++) {
        if (env_vars[i] == (char *)0) {
            continue;  /* Skip NULL entries */
        }
        if (strncmp(env_vars[i], name, name_len) == 0 &&
            env_vars[i][name_len] == '=') {
            if (!overwrite) {
                return 0;
            }
            /* Replace */
            free(env_vars[i]);
            size_t len = name_len + 1 + strlen(value) + 1;
            env_vars[i] = (char *)malloc(len);
            if (env_vars[i] == (char *)0) {
                return -1;
            }
            strcpy(env_vars[i], name);
            env_vars[i][name_len] = '=';
            strcpy(env_vars[i] + name_len + 1, value);
            return 0;
        }
    }

    /* Add new */
    if (env_count >= 63) {
        return -1;
    }

    size_t len = name_len + 1 + strlen(value) + 1;
    env_vars[env_count] = (char *)malloc(len);
    if (env_vars[env_count] == (char *)0) {
        return -1;
    }
    strcpy(env_vars[env_count], name);
    env_vars[env_count][name_len] = '=';
    strcpy(env_vars[env_count] + name_len + 1, value);
    env_count++;
    env_vars[env_count] = (char *)0;

    return 0;
}

int unsetenv(const char *name)
{
    size_t name_len = strlen(name);
    for (int i = 0; i < env_count && i < 64; i++) {
        if (env_vars[i] == (char *)0) {
            continue;  /* Skip NULL entries */
        }
        if (strncmp(env_vars[i], name, name_len) == 0 &&
            env_vars[i][name_len] == '=') {
            free(env_vars[i]);
            /* Shift remaining */
            for (int j = i; j < env_count - 1; j++) {
                env_vars[j] = env_vars[j + 1];
            }
            env_count--;
            env_vars[env_count] = (char *)0;
            return 0;
        }
    }
    return 0;
}

int system(const char *command)
{
    if (command == (const char *)0) {
        return 1;  /* Shell available */
    }

    pid_t pid = fork();
    if (pid < 0) {
        return -1;
    }

    if (pid == 0) {
        /* Child */
        char *argv[] = { "/bin/sh", "-c", (char *)command, (char *)0 };
        execve("/bin/sh", argv, (char *const *)0);
        _exit(127);
    }

    /* Parent */
    int status;
    if (waitpid(pid, &status, 0) < 0) {
        return -1;
    }

    return status;
}
