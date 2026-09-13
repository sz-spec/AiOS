/**
 * @file string.h
 * @brief VOS3 User-Space String Functions
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_STRING_H
#define VOS3_USER_STRING_H

#include <stddef.h>

/* ============================================================================
 * STRING FUNCTIONS
 * ============================================================================ */

/* Length */
size_t strlen(const char *s);
size_t strnlen(const char *s, size_t maxlen);

/* Copy */
char *strcpy(char *dest, const char *src);
char *strncpy(char *dest, const char *src, size_t n);
char *strcat(char *dest, const char *src);
char *strncat(char *dest, const char *src, size_t n);

/* Compare */
int strcmp(const char *s1, const char *s2);
int strncmp(const char *s1, const char *s2, size_t n);
int strcasecmp(const char *s1, const char *s2);
int strncasecmp(const char *s1, const char *s2, size_t n);

/* Search */
char *strchr(const char *s, int c);
char *strrchr(const char *s, int c);
char *strstr(const char *haystack, const char *needle);
char *strpbrk(const char *s, const char *accept);
size_t strspn(const char *s, const char *accept);
size_t strcspn(const char *s, const char *reject);
char *strtok(char *str, const char *delim);
char *strtok_r(char *str, const char *delim, char **saveptr);

/* Duplicate */
char *strdup(const char *s);
char *strndup(const char *s, size_t n);

/* ============================================================================
 * MEMORY FUNCTIONS
 * ============================================================================ */

void *memcpy(void *dest, const void *src, size_t n);
void *memmove(void *dest, const void *src, size_t n);
void *memset(void *s, int c, size_t n);
int memcmp(const void *s1, const void *s2, size_t n);
void *memchr(const void *s, int c, size_t n);

/* ============================================================================
 * ERROR STRINGS
 * ============================================================================ */

char *strerror(int errnum);

#endif /* VOS3_USER_STRING_H */
