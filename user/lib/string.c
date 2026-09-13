/**
 * @file string.c
 * @brief VOS3 User-Space String Functions
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "string.h"
#include "stdlib.h"

/* ============================================================================
 * STRING LENGTH
 * ============================================================================ */

size_t strlen(const char *s)
{
    const char *p = s;
    while (*p != '\0') {
        p++;
    }
    return (size_t)(p - s);
}

size_t strnlen(const char *s, size_t maxlen)
{
    size_t len = 0;
    while (len < maxlen && s[len] != '\0') {
        len++;
    }
    return len;
}

/* ============================================================================
 * STRING COPY
 * ============================================================================ */

char *strcpy(char *dest, const char *src)
{
    char *d = dest;
    while ((*d++ = *src++) != '\0') {
        /* Copy including null terminator */
    }
    return dest;
}

char *strncpy(char *dest, const char *src, size_t n)
{
    size_t i;
    for (i = 0; i < n && src[i] != '\0'; i++) {
        dest[i] = src[i];
    }
    for (; i < n; i++) {
        dest[i] = '\0';
    }
    return dest;
}

char *strcat(char *dest, const char *src)
{
    char *d = dest;
    while (*d != '\0') {
        d++;
    }
    while ((*d++ = *src++) != '\0') {
        /* Copy */
    }
    return dest;
}

char *strncat(char *dest, const char *src, size_t n)
{
    char *d = dest;
    while (*d != '\0') {
        d++;
    }
    while (n > 0 && *src != '\0') {
        *d++ = *src++;
        n--;
    }
    *d = '\0';
    return dest;
}

/* ============================================================================
 * STRING COMPARE
 * ============================================================================ */

int strcmp(const char *s1, const char *s2)
{
    while (*s1 != '\0' && *s1 == *s2) {
        s1++;
        s2++;
    }
    return (unsigned char)*s1 - (unsigned char)*s2;
}

int strncmp(const char *s1, const char *s2, size_t n)
{
    if (n == 0) {
        return 0;
    }
    while (n > 1 && *s1 != '\0' && *s1 == *s2) {
        s1++;
        s2++;
        n--;
    }
    return (unsigned char)*s1 - (unsigned char)*s2;
}

static int tolower_char(int c)
{
    if (c >= 'A' && c <= 'Z') {
        return c + ('a' - 'A');
    }
    return c;
}

int strcasecmp(const char *s1, const char *s2)
{
    while (*s1 != '\0' && tolower_char(*s1) == tolower_char(*s2)) {
        s1++;
        s2++;
    }
    return tolower_char((unsigned char)*s1) - tolower_char((unsigned char)*s2);
}

int strncasecmp(const char *s1, const char *s2, size_t n)
{
    if (n == 0) {
        return 0;
    }
    while (n > 1 && *s1 != '\0' && tolower_char(*s1) == tolower_char(*s2)) {
        s1++;
        s2++;
        n--;
    }
    return tolower_char((unsigned char)*s1) - tolower_char((unsigned char)*s2);
}

/* ============================================================================
 * STRING SEARCH
 * ============================================================================ */

char *strchr(const char *s, int c)
{
    while (*s != '\0') {
        if (*s == (char)c) {
            return (char *)s;
        }
        s++;
    }
    return (c == '\0') ? (char *)s : (char *)0;
}

char *strrchr(const char *s, int c)
{
    const char *last = (char *)0;
    while (*s != '\0') {
        if (*s == (char)c) {
            last = s;
        }
        s++;
    }
    return (c == '\0') ? (char *)s : (char *)last;
}

char *strstr(const char *haystack, const char *needle)
{
    size_t needle_len = strlen(needle);
    if (needle_len == 0) {
        return (char *)haystack;
    }

    while (*haystack != '\0') {
        if (*haystack == *needle) {
            if (strncmp(haystack, needle, needle_len) == 0) {
                return (char *)haystack;
            }
        }
        haystack++;
    }
    return (char *)0;
}

char *strpbrk(const char *s, const char *accept)
{
    while (*s != '\0') {
        const char *a = accept;
        while (*a != '\0') {
            if (*s == *a) {
                return (char *)s;
            }
            a++;
        }
        s++;
    }
    return (char *)0;
}

size_t strspn(const char *s, const char *accept)
{
    size_t count = 0;
    while (*s != '\0') {
        const char *a = accept;
        int found = 0;
        while (*a != '\0') {
            if (*s == *a) {
                found = 1;
                break;
            }
            a++;
        }
        if (!found) {
            break;
        }
        s++;
        count++;
    }
    return count;
}

size_t strcspn(const char *s, const char *reject)
{
    size_t count = 0;
    while (*s != '\0') {
        const char *r = reject;
        while (*r != '\0') {
            if (*s == *r) {
                return count;
            }
            r++;
        }
        s++;
        count++;
    }
    return count;
}

static char *strtok_state = (char *)0;

char *strtok(char *str, const char *delim)
{
    return strtok_r(str, delim, &strtok_state);
}

char *strtok_r(char *str, const char *delim, char **saveptr)
{
    char *token;

    if (str == (char *)0) {
        str = *saveptr;
    }

    /* Skip leading delimiters */
    str += strspn(str, delim);
    if (*str == '\0') {
        *saveptr = str;
        return (char *)0;
    }

    /* Find end of token */
    token = str;
    str = strpbrk(token, delim);
    if (str == (char *)0) {
        *saveptr = token + strlen(token);
    } else {
        *str = '\0';
        *saveptr = str + 1;
    }

    return token;
}

/* ============================================================================
 * STRING DUPLICATE
 * ============================================================================ */

char *strdup(const char *s)
{
    size_t len = strlen(s) + 1;
    char *dup = malloc(len);
    if (dup != (char *)0) {
        memcpy(dup, s, len);
    }
    return dup;
}

char *strndup(const char *s, size_t n)
{
    size_t len = strnlen(s, n);
    char *dup = malloc(len + 1);
    if (dup != (char *)0) {
        memcpy(dup, s, len);
        dup[len] = '\0';
    }
    return dup;
}

/* ============================================================================
 * MEMORY FUNCTIONS
 * ============================================================================ */

void *memcpy(void *dest, const void *src, size_t n)
{
    unsigned char *d = (unsigned char *)dest;
    const unsigned char *s = (const unsigned char *)src;
    while (n > 0) {
        *d++ = *s++;
        n--;
    }
    return dest;
}

void *memmove(void *dest, const void *src, size_t n)
{
    unsigned char *d = (unsigned char *)dest;
    const unsigned char *s = (const unsigned char *)src;

    if (d < s) {
        while (n > 0) {
            *d++ = *s++;
            n--;
        }
    } else if (d > s) {
        d += n;
        s += n;
        while (n > 0) {
            *--d = *--s;
            n--;
        }
    }
    return dest;
}

void *memset(void *s, int c, size_t n)
{
    unsigned char *p = (unsigned char *)s;
    while (n > 0) {
        *p++ = (unsigned char)c;
        n--;
    }
    return s;
}

int memcmp(const void *s1, const void *s2, size_t n)
{
    const unsigned char *p1 = (const unsigned char *)s1;
    const unsigned char *p2 = (const unsigned char *)s2;
    while (n > 0) {
        if (*p1 != *p2) {
            return *p1 - *p2;
        }
        p1++;
        p2++;
        n--;
    }
    return 0;
}

void *memchr(const void *s, int c, size_t n)
{
    const unsigned char *p = (const unsigned char *)s;
    while (n > 0) {
        if (*p == (unsigned char)c) {
            return (void *)p;
        }
        p++;
        n--;
    }
    return (void *)0;
}

/* ============================================================================
 * ERROR STRINGS
 * ============================================================================ */

static const char *error_strings[] = {
    "Success",                      /* 0 */
    "Operation not permitted",      /* 1 EPERM */
    "No such file or directory",    /* 2 ENOENT */
    "No such process",              /* 3 ESRCH */
    "Interrupted system call",      /* 4 EINTR */
    "I/O error",                    /* 5 EIO */
    "No such device or address",    /* 6 ENXIO */
    "Argument list too long",       /* 7 E2BIG */
    "Exec format error",            /* 8 ENOEXEC */
    "Bad file number",              /* 9 EBADF */
    "No child processes",           /* 10 ECHILD */
    "Try again",                    /* 11 EAGAIN */
    "Out of memory",                /* 12 ENOMEM */
    "Permission denied",            /* 13 EACCES */
    "Bad address",                  /* 14 EFAULT */
};

char *strerror(int errnum)
{
    static char unknown[32];
    if (errnum >= 0 && errnum < (int)(sizeof(error_strings) / sizeof(error_strings[0]))) {
        return (char *)error_strings[errnum];
    }

    /* Format unknown error */
    char *p = unknown;
    strcpy(p, "Unknown error ");
    p += 14;

    if (errnum < 0) {
        *p++ = '-';
        errnum = -errnum;
    }

    char buf[16];
    char *b = buf + sizeof(buf) - 1;
    *b = '\0';
    do {
        *--b = '0' + (errnum % 10);
        errnum /= 10;
    } while (errnum > 0);

    strcpy(p, b);
    return unknown;
}
