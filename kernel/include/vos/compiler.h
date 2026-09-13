/**
 * @file compiler.h
 * @brief VOS3 Compiler Optimization Macros
 *
 * @details Performance-critical macros for branch prediction hints
 *          and inline optimization. Used throughout the network stack
 *          to achieve zero-latency packet processing.
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 4 - Performance Optimized Protocol Stack
 */

#ifndef VOS3_COMPILER_H
#define VOS3_COMPILER_H

/* ============================================================================
 * BRANCH PREDICTION HINTS
 * ============================================================================
 *
 * These macros help the CPU branch predictor by indicating which path
 * is most likely to be taken. This prevents pipeline stalls on the
 * fast path (valid packets).
 *
 * Usage:
 *   if (unlikely(error_condition)) return -1;  // Error path (rare)
 *   if (likely(normal_condition)) process();   // Normal path (common)
 */

/**
 * @brief Hint that condition is LIKELY to be true (fast path)
 *
 * Use for conditions that are expected to succeed most of the time.
 * Example: if (likely(packet_valid)) process_packet();
 *
 * @param x  Condition to evaluate
 * @return   The boolean result of the condition
 */
#define likely(x)   __builtin_expect(!!(x), 1)

/**
 * @brief Hint that condition is UNLIKELY to be true (error path)
 *
 * Use for security checks and error conditions that rarely trigger.
 * Example: if (unlikely(len < MIN_SIZE)) return -EINVAL;
 *
 * The CPU assumes this branch is NOT taken, so the pipeline continues
 * processing the "success" path without stalling.
 *
 * @param x  Condition to evaluate
 * @return   The boolean result of the condition
 */
#define unlikely(x) __builtin_expect(!!(x), 0)

/* ============================================================================
 * FUNCTION ATTRIBUTES
 * ============================================================================ */

/**
 * @brief Force function inlining (performance critical)
 *
 * Use for small, frequently-called functions like checksum calculations.
 */
#define VOS3_ALWAYS_INLINE  static inline __attribute__((always_inline))

/**
 * @brief Prevent function inlining (for debugging)
 */
#define VOS3_NOINLINE       __attribute__((noinline))

/**
 * @brief Mark function as hot (frequently executed)
 *
 * Tells the compiler to optimize this function more aggressively
 * and place it in a "hot" code section for better cache locality.
 */
#define VOS3_HOT            __attribute__((hot))

/**
 * @brief Mark function as cold (rarely executed)
 *
 * Used for error handlers. The compiler will optimize for size
 * instead of speed and place it away from hot code.
 */
#define VOS3_COLD           __attribute__((cold))

/**
 * @brief Prefetch data into cache (reduce latency)
 *
 * @param addr  Address to prefetch
 * @param rw    0 = read, 1 = write
 * @param locality  0-3, higher = keep in cache longer
 */
#define VOS3_PREFETCH(addr, rw, locality) \
    __builtin_prefetch((addr), (rw), (locality))

/**
 * @brief Prefetch for read (most common case)
 */
#define VOS3_PREFETCH_READ(addr) VOS3_PREFETCH((addr), 0, 3)

/**
 * @brief Prefetch for write
 */
#define VOS3_PREFETCH_WRITE(addr) VOS3_PREFETCH((addr), 1, 3)

/* ============================================================================
 * MEMORY BARRIERS
 * ============================================================================ */

/**
 * @brief Compiler memory barrier (prevent reordering)
 */
#define VOS3_BARRIER()      __asm__ volatile("" ::: "memory")

/**
 * @brief Full memory fence (CPU + compiler barrier)
 */
#define VOS3_MFENCE()       __asm__ volatile("mfence" ::: "memory")

/**
 * @brief Store fence
 */
#define VOS3_SFENCE()       __asm__ volatile("sfence" ::: "memory")

/**
 * @brief Load fence
 */
#define VOS3_LFENCE()       __asm__ volatile("lfence" ::: "memory")

/* ============================================================================
 * ALIGNMENT HINTS
 * ============================================================================ */

/**
 * @brief Align variable/struct to specified boundary
 */
#define VOS3_ALIGNED(n)     __attribute__((aligned(n)))

/**
 * @brief Cache-line aligned (64 bytes on x86_64)
 */
#define VOS3_CACHE_ALIGNED  VOS3_ALIGNED(64)

/**
 * @brief Pack structure (no padding)
 */
#define VOS3_PACKED         __attribute__((packed))

#endif /* VOS3_COMPILER_H */
