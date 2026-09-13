/**
 * @file string.h
 * @brief VOS3 String Functions
 *
 * @details Minimal string and memory functions for the kernel.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_STRING_H
#define VOS3_STRING_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stddef.h>
#include <stdint.h>

/* ============================================================================
 * MEMORY FUNCTIONS
 * ============================================================================ */

/**
 * @brief Copy memory
 * @param[out] dest Destination
 * @param[in] src Source
 * @param[in] n Number of bytes
 * @return dest
 *
 * Phase 4.2.18: Uses rep movsb for copies >= 64 bytes.
 * ERMS (Enhanced REP MOVSB) on modern x86_64 CPUs makes this
 * significantly faster than byte-by-byte without requiring SSE/AVX.
 */
static inline void* memcpy(void* dest, const void* src, size_t n)
{
    void* ret = dest;

    if (n >= 64U) {
        /* rep movsb: RCX=count, RSI=src, RDI=dest, direction=forward.
         * ERMS on modern x86_64 makes this ~5-10x faster than byte loop. */
        size_t dummy_c;
        void *dummy_d;
        const void *dummy_s;
        __asm__ volatile(
            "rep movsb"
            : "=D"(dummy_d), "=S"(dummy_s), "=c"(dummy_c)
            : "0"(dest), "1"(src), "2"(n)
            : "memory"
        );
        return ret;
    }

    uint8_t* d = (uint8_t*)dest;
    const uint8_t* s = (const uint8_t*)src;

    while (n > 0U) {
        *d++ = *s++;
        n--;
    }

    return ret;
}

/**
 * @brief Fill memory with a byte value
 * @param[out] dest Destination
 * @param[in] c Byte value
 * @param[in] n Number of bytes
 * @return dest
 */
static inline void* memset(void* dest, int c, size_t n)
{
    void* ret = dest;

    if (n >= 64U) {
        /* rep stosb: RCX=count, AL=value, RDI=dest.
         * ERMS on modern x86_64 makes this very fast. */
        size_t dummy_c;
        void *dummy_d;
        __asm__ volatile(
            "rep stosb"
            : "=D"(dummy_d), "=c"(dummy_c)
            : "0"(dest), "1"(n), "a"((uint8_t)c)
            : "memory"
        );
        return ret;
    }

    uint8_t* d = (uint8_t*)dest;
    uint8_t val = (uint8_t)c;

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wstringop-overflow"
    while (n > 0U) {
        *d++ = val;
        n--;
    }
#pragma GCC diagnostic pop

    return ret;
}

/**
 * @brief Move memory (handles overlapping regions)
 * @param[out] dest Destination
 * @param[in] src Source
 * @param[in] n Number of bytes
 * @return dest
 */
static inline void* memmove(void* dest, const void* src, size_t n)
{
    void* ret = dest;
    uint8_t* d = (uint8_t*)dest;
    const uint8_t* s = (const uint8_t*)src;

    if (d < s) {
        if (n >= 64U) {
            size_t dummy_c;
            void *dummy_d;
            const void *dummy_s;
            __asm__ volatile(
                "rep movsb"
                : "=D"(dummy_d), "=S"(dummy_s), "=c"(dummy_c)
                : "0"(dest), "1"(src), "2"(n)
                : "memory"
            );
            return ret;
        }
        while (n > 0U) {
            *d++ = *s++;
            n--;
        }
    } else if (d > s) {
        /* Backward copy — use rep movsb with STD (direction flag set) */
        if (n >= 64U) {
            size_t dummy_c;
            void *dummy_d;
            const void *dummy_s;
            __asm__ volatile(
                "std\n\t"
                "rep movsb\n\t"
                "cld"
                : "=D"(dummy_d), "=S"(dummy_s), "=c"(dummy_c)
                : "0"(d + n - 1), "1"(s + n - 1), "2"(n)
                : "memory"
            );
            return ret;
        }
        d += n;
        s += n;
        while (n > 0U) {
            *--d = *--s;
            n--;
        }
    }

    return ret;
}

/**
 * @brief Compare memory
 * @param[in] s1 First buffer
 * @param[in] s2 Second buffer
 * @param[in] n Number of bytes
 * @return 0 if equal, <0 if s1<s2, >0 if s1>s2
 */
static inline int memcmp(const void* s1, const void* s2, size_t n)
{
    const uint8_t* p1 = (const uint8_t*)s1;
    const uint8_t* p2 = (const uint8_t*)s2;

    while (n > 0U) {
        if (*p1 != *p2) {
            return (*p1 < *p2) ? -1 : 1;
        }
        p1++;
        p2++;
        n--;
    }

    return 0;
}

/* ============================================================================
 * STRING FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get string length
 * @param[in] s String
 * @return Length (not including null terminator)
 */
static inline size_t strlen(const char* s)
{
    size_t len = 0U;
    while (*s++ != '\0') {
        len++;
    }
    return len;
}

/**
 * @brief Get string length with limit
 * @param[in] s String
 * @param[in] maxlen Maximum length to scan
 * @return Length (not including null terminator), or maxlen if not found
 */
static inline size_t strnlen(const char* s, size_t maxlen)
{
    size_t len = 0U;
    while (len < maxlen && *s++ != '\0') {
        len++;
    }
    return len;
}

/**
 * @brief Copy string
 * @param[out] dest Destination
 * @param[in] src Source
 * @return dest
 */
static inline char* strcpy(char* dest, const char* src)
{
    char* d = dest;
    while ((*d++ = *src++) != '\0') {
        /* Copy including null terminator */
    }
    return dest;
}

/**
 * @brief Copy string with limit
 * @param[out] dest Destination
 * @param[in] src Source
 * @param[in] n Maximum characters to copy
 * @return dest
 */
static inline char* strncpy(char* dest, const char* src, size_t n)
{
    char* d = dest;
    while (n > 0U && *src != '\0') {
        *d++ = *src++;
        n--;
    }
    while (n > 0U) {
        *d++ = '\0';
        n--;
    }
    return dest;
}

/**
 * @brief Compare strings
 * @param[in] s1 First string
 * @param[in] s2 Second string
 * @return 0 if equal, <0 if s1<s2, >0 if s1>s2
 */
static inline int strcmp(const char* s1, const char* s2)
{
    while (*s1 != '\0' && *s1 == *s2) {
        s1++;
        s2++;
    }
    return (int)(unsigned char)*s1 - (int)(unsigned char)*s2;
}

/**
 * @brief Compare strings with limit
 * @param[in] s1 First string
 * @param[in] s2 Second string
 * @param[in] n Maximum characters to compare
 * @return 0 if equal, <0 if s1<s2, >0 if s1>s2
 */
static inline int strncmp(const char* s1, const char* s2, size_t n)
{
    while (n > 0U && *s1 != '\0' && *s1 == *s2) {
        s1++;
        s2++;
        n--;
    }
    if (n == 0U) {
        return 0;
    }
    return (int)(unsigned char)*s1 - (int)(unsigned char)*s2;
}

/**
 * @brief Find character in string
 * @param[in] s String
 * @param[in] c Character to find
 * @return Pointer to first occurrence, or NULL if not found
 */
static inline char* strchr(const char* s, int c)
{
    while (*s != '\0') {
        if (*s == (char)c) {
            return (char*)s;
        }
        s++;
    }
    return (c == '\0') ? (char*)s : NULL;
}

/**
 * @brief Find last occurrence of character in string
 * @param[in] s String
 * @param[in] c Character to find
 * @return Pointer to last occurrence, or NULL if not found
 */
static inline char* strrchr(const char* s, int c)
{
    const char* last = NULL;
    while (*s != '\0') {
        if (*s == (char)c) {
            last = s;
        }
        s++;
    }
    return (c == '\0') ? (char*)s : (char*)last;
}

#ifdef __cplusplus
}
#endif

#endif /* VOS3_STRING_H */
