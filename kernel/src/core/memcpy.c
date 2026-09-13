/**
 * @file memcpy.c
 * @brief Global memcpy/memset symbols for compiler-generated implicit calls.
 *
 * When struct copies exceed the inline threshold (~256 bytes), GCC/Clang
 * emit library calls to memcpy. The -ftrivial-auto-var-init=zero flag
 * (production builds) causes GCC to emit memset calls for auto variable
 * initialization. This file provides both linkable symbols using ERMS
 * (rep movsb/stosb) without including string.h (which defines conflicting
 * static inline versions).
 */
#include <stddef.h>

void *memcpy(void *dst, const void *src, size_t n)
{
    __asm__ volatile(
        "rep movsb"
        : "+D"(dst), "+S"(src), "+c"(n)
        :: "memory"
    );
    return dst;
}

void *memset(void *dst, int c, size_t n)
{
    __asm__ volatile(
        "rep stosb"
        : "+D"(dst), "+c"(n)
        : "a"((unsigned char)c)
        : "memory"
    );
    return dst;
}
