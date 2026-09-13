/**
 * @file atomic.h
 * @brief VOS3 Atomic Operations for Lock-Free Programming
 *
 * @details Provides atomic operations for x86_64 using inline assembly.
 *          Essential for SMP-safe data structures like the PMM bitmap.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ATOMIC_H
#define VOS3_ATOMIC_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ============================================================================
 * MEMORY ORDERING
 * ============================================================================ */

/** @brief Memory barrier (full fence) */
static inline void vos3_memory_barrier(void)
{
    __asm__ volatile ("mfence" ::: "memory");
}

/** @brief Read memory barrier */
static inline void vos3_read_barrier(void)
{
    __asm__ volatile ("lfence" ::: "memory");
}

/** @brief Write memory barrier */
static inline void vos3_write_barrier(void)
{
    __asm__ volatile ("sfence" ::: "memory");
}

/** @brief Compiler barrier (prevents reordering) */
static inline void vos3_compiler_barrier(void)
{
    __asm__ volatile ("" ::: "memory");
}

/* ============================================================================
 * ATOMIC LOAD/STORE (64-bit)
 * ============================================================================ */

/**
 * @brief Atomic load (64-bit)
 * @param[in] ptr Pointer to value
 * @return Loaded value
 */
static inline uint64_t vos3_atomic_load64(const volatile uint64_t* ptr)
{
    uint64_t value;
    __asm__ volatile (
        "movq %1, %0"
        : "=r" (value)
        : "m" (*ptr)
        : "memory"
    );
    return value;
}

/**
 * @brief Atomic store (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to store
 */
static inline void vos3_atomic_store64(volatile uint64_t* ptr, uint64_t value)
{
    __asm__ volatile (
        "movq %1, %0"
        : "=m" (*ptr)
        : "r" (value)
        : "memory"
    );
}

/**
 * @brief Atomic load (32-bit)
 * @param[in] ptr Pointer to value
 * @return Loaded value
 */
static inline uint32_t vos3_atomic_load32(const volatile uint32_t* ptr)
{
    uint32_t value;
    __asm__ volatile (
        "movl %1, %0"
        : "=r" (value)
        : "m" (*ptr)
        : "memory"
    );
    return value;
}

/**
 * @brief Atomic store (32-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to store
 */
static inline void vos3_atomic_store32(volatile uint32_t* ptr, uint32_t value)
{
    __asm__ volatile (
        "movl %1, %0"
        : "=m" (*ptr)
        : "r" (value)
        : "memory"
    );
}

/* ============================================================================
 * COMPARE AND SWAP (CAS)
 * ============================================================================ */

/**
 * @brief Compare and swap (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] expected Expected current value
 * @param[in] desired New value if current equals expected
 * @return Previous value at ptr
 * @note If return == expected, the swap succeeded
 */
static inline uint64_t vos3_atomic_cas64(
    volatile uint64_t* ptr,
    uint64_t expected,
    uint64_t desired)
{
    uint64_t previous;
    __asm__ volatile (
        "lock cmpxchgq %2, %1"
        : "=a" (previous), "+m" (*ptr)
        : "r" (desired), "0" (expected)
        : "memory", "cc"
    );
    return previous;
}

/**
 * @brief Compare and swap (32-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] expected Expected current value
 * @param[in] desired New value if current equals expected
 * @return Previous value at ptr
 */
static inline uint32_t vos3_atomic_cas32(
    volatile uint32_t* ptr,
    uint32_t expected,
    uint32_t desired)
{
    uint32_t previous;
    __asm__ volatile (
        "lock cmpxchgl %2, %1"
        : "=a" (previous), "+m" (*ptr)
        : "r" (desired), "0" (expected)
        : "memory", "cc"
    );
    return previous;
}

/**
 * @brief Compare and swap with bool result (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in,out] expected Pointer to expected value (updated on failure)
 * @param[in] desired New value if current equals expected
 * @return 1 if swap succeeded, 0 otherwise
 */
static inline int vos3_atomic_cas64_bool(
    volatile uint64_t* ptr,
    uint64_t* expected,
    uint64_t desired)
{
    uint64_t prev = vos3_atomic_cas64(ptr, *expected, desired);
    if (prev == *expected) {
        return 1;
    }
    *expected = prev;
    return 0;
}

/* ============================================================================
 * ATOMIC FETCH AND OPERATE
 * ============================================================================ */

/**
 * @brief Atomic fetch and add (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to add
 * @return Previous value
 */
static inline uint64_t vos3_atomic_fetch_add64(volatile uint64_t* ptr, uint64_t value)
{
    uint64_t previous;
    __asm__ volatile (
        "lock xaddq %0, %1"
        : "=r" (previous), "+m" (*ptr)
        : "0" (value)
        : "memory", "cc"
    );
    return previous;
}

/**
 * @brief Atomic fetch and sub (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to subtract
 * @return Previous value
 */
static inline uint64_t vos3_atomic_fetch_sub64(volatile uint64_t* ptr, uint64_t value)
{
    return vos3_atomic_fetch_add64(ptr, (uint64_t)(-(int64_t)value));
}

/**
 * @brief Atomic fetch and add (32-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to add
 * @return Previous value
 */
static inline uint32_t vos3_atomic_fetch_add32(volatile uint32_t* ptr, uint32_t value)
{
    uint32_t previous;
    __asm__ volatile (
        "lock xaddl %0, %1"
        : "=r" (previous), "+m" (*ptr)
        : "0" (value)
        : "memory", "cc"
    );
    return previous;
}

/**
 * @brief Atomic fetch and sub (32-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to subtract
 * @return Previous value
 */
static inline uint32_t vos3_atomic_fetch_sub32(volatile uint32_t* ptr, uint32_t value)
{
    return vos3_atomic_fetch_add32(ptr, (uint32_t)(-(int32_t)value));
}

/**
 * @brief Atomic fetch and or (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to OR
 * @return Previous value
 */
static inline uint64_t vos3_atomic_fetch_or64(volatile uint64_t* ptr, uint64_t value)
{
    uint64_t prev = vos3_atomic_load64(ptr);
    while (!vos3_atomic_cas64_bool(ptr, &prev, prev | value)) {
        /* Retry with updated prev */
    }
    return prev;
}

/**
 * @brief Atomic fetch and and (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value Value to AND
 * @return Previous value
 */
static inline uint64_t vos3_atomic_fetch_and64(volatile uint64_t* ptr, uint64_t value)
{
    uint64_t prev = vos3_atomic_load64(ptr);
    while (!vos3_atomic_cas64_bool(ptr, &prev, prev & value)) {
        /* Retry with updated prev */
    }
    return prev;
}

/**
 * @brief Atomic exchange (64-bit)
 * @param[in,out] ptr Pointer to value
 * @param[in] value New value
 * @return Previous value
 */
static inline uint64_t vos3_atomic_exchange64(volatile uint64_t* ptr, uint64_t value)
{
    uint64_t previous;
    __asm__ volatile (
        "xchgq %0, %1"
        : "=r" (previous), "+m" (*ptr)
        : "0" (value)
        : "memory"
    );
    return previous;
}

/* ============================================================================
 * ATOMIC BIT OPERATIONS (Lock-Free Bitmap)
 * ============================================================================ */

/**
 * @brief Atomically test and set a bit
 * @param[in,out] ptr Pointer to 64-bit value
 * @param[in] bit Bit position (0-63)
 * @return Previous value of the bit (0 or 1)
 */
static inline int vos3_atomic_bit_test_set(volatile uint64_t* ptr, uint8_t bit)
{
    unsigned char previous;
    __asm__ volatile (
        "lock btsq %2, %1\n\t"
        "setc %0"
        : "=qm" (previous), "+m" (*ptr)
        : "r" ((uint64_t)bit)
        : "memory", "cc"
    );
    return (int)previous;
}

/**
 * @brief Atomically test and clear a bit
 * @param[in,out] ptr Pointer to 64-bit value
 * @param[in] bit Bit position (0-63)
 * @return Previous value of the bit (0 or 1)
 */
static inline int vos3_atomic_bit_test_clear(volatile uint64_t* ptr, uint8_t bit)
{
    unsigned char previous;
    __asm__ volatile (
        "lock btrq %2, %1\n\t"
        "setc %0"
        : "=qm" (previous), "+m" (*ptr)
        : "r" ((uint64_t)bit)
        : "memory", "cc"
    );
    return (int)previous;
}

/**
 * @brief Test a bit (non-atomic, for read-only check)
 * @param[in] value Value to test
 * @param[in] bit Bit position (0-63)
 * @return Bit value (0 or 1)
 */
static inline int vos3_bit_test(uint64_t value, uint8_t bit)
{
    return (int)((value >> bit) & 1ULL);
}

/**
 * @brief Find first set bit (starting from LSB)
 * @param[in] value Value to scan
 * @return Bit position (0-63), or 64 if no bits set
 */
static inline uint8_t vos3_bit_scan_forward(uint64_t value)
{
    if (value == 0ULL) {
        return 64U;
    }
    uint64_t result;
    __asm__ volatile (
        "bsfq %1, %0"
        : "=r" (result)
        : "r" (value)
        : "cc"
    );
    return (uint8_t)result;
}

/**
 * @brief Find first clear bit (starting from LSB)
 * @param[in] value Value to scan
 * @return Bit position (0-63), or 64 if all bits set
 */
static inline uint8_t vos3_bit_scan_forward_clear(uint64_t value)
{
    return vos3_bit_scan_forward(~value);
}

/**
 * @brief Count set bits (population count)
 * @param[in] value Value to count
 * @return Number of set bits
 */
static inline uint8_t vos3_bit_popcount(uint64_t value)
{
    uint64_t result;
    __asm__ volatile (
        "popcntq %1, %0"
        : "=r" (result)
        : "r" (value)
        : "cc"
    );
    return (uint8_t)result;
}

/* ============================================================================
 * IRQ STATE MANAGEMENT
 * ============================================================================ */

/** @brief Saved interrupt flags type */
typedef unsigned long vos3_irqflags_t;

/**
 * @brief Save interrupt state and disable interrupts
 *
 * Saves the current RFLAGS (including IF) and then disables interrupts
 * via CLI. Must be paired with vos3_irq_restore().
 *
 * @return Saved RFLAGS value
 */
static inline vos3_irqflags_t vos3_irq_save(void)
{
    vos3_irqflags_t flags;
    __asm__ volatile (
        "pushfq\n\t"
        "pop %0\n\t"
        "cli"
        : "=r" (flags)
        :
        : "memory"
    );
    return flags;
}

/**
 * @brief Restore interrupt state from saved flags
 *
 * Restores the RFLAGS register, re-enabling interrupts if they were
 * enabled when vos3_irq_save() was called.
 *
 * @param[in] flags Saved RFLAGS from vos3_irq_save()
 */
static inline void vos3_irq_restore(vos3_irqflags_t flags)
{
    __asm__ volatile (
        "push %0\n\t"
        "popfq"
        :
        : "r" (flags)
        : "memory"
    );
}

/* ============================================================================
 * SPINLOCK (Simple)
 * ============================================================================ */

/** @brief Spinlock type */
typedef volatile uint64_t vos3_spinlock_t;

/** @brief Spinlock initializer */
#define VOS3_SPINLOCK_INIT  (0ULL)

/** @brief Initialize spinlock */
static inline void vos3_spinlock_init(vos3_spinlock_t* lock)
{
    *lock = VOS3_SPINLOCK_INIT;
}

/**
 * @brief Acquire spinlock
 * @param[in,out] lock Pointer to spinlock
 */
static inline void vos3_spinlock_acquire(vos3_spinlock_t* lock)
{
    while (vos3_atomic_exchange64(lock, 1ULL) != 0ULL) {
        /* Spin with PAUSE to reduce contention */
        __asm__ volatile ("pause" ::: "memory");
    }
}

/**
 * @brief Try to acquire spinlock (non-blocking)
 * @param[in,out] lock Pointer to spinlock
 * @return 1 if acquired, 0 if already held
 */
static inline int vos3_spinlock_try_acquire(vos3_spinlock_t* lock)
{
    return (vos3_atomic_exchange64(lock, 1ULL) == 0ULL) ? 1 : 0;
}

/**
 * @brief Release spinlock
 * @param[in,out] lock Pointer to spinlock
 */
static inline void vos3_spinlock_release(vos3_spinlock_t* lock)
{
    vos3_atomic_store64(lock, 0ULL);
}

/** @brief Alias for vos3_spinlock_acquire */
static inline void vos3_spinlock_lock(vos3_spinlock_t* lock)
{
    vos3_spinlock_acquire(lock);
}

/** @brief Alias for vos3_spinlock_release */
static inline void vos3_spinlock_unlock(vos3_spinlock_t* lock)
{
    vos3_spinlock_release(lock);
}

/**
 * @brief Check if spinlock is held
 * @param[in] lock Pointer to spinlock
 * @return 1 if held, 0 if free
 */
static inline int vos3_spinlock_is_held(const vos3_spinlock_t* lock)
{
    return (vos3_atomic_load64(lock) != 0ULL) ? 1 : 0;
}

/* ============================================================================
 * CPU PAUSE / RELAXATION
 * ============================================================================ */

/**
 * @brief CPU pause instruction (reduces power in spin loops)
 */
static inline void vos3_cpu_pause(void)
{
    __asm__ volatile ("pause" ::: "memory");
}

/* ============================================================================
 * ATOMIC TYPES
 * ============================================================================ */

/** @brief Atomic 32-bit type */
typedef struct vos3_atomic32 {
    volatile uint32_t value;
} vos3_atomic32_t;

/** @brief Atomic 64-bit type */
typedef struct vos3_atomic64 {
    volatile uint64_t value;
} vos3_atomic64_t;

/** @brief Initialize atomic32 */
#define VOS3_ATOMIC32_INIT(v) { .value = (v) }

/** @brief Initialize atomic64 */
#define VOS3_ATOMIC64_INIT(v) { .value = (v) }

/* ============================================================================
 * TYPED ATOMIC32 OPERATIONS
 * ============================================================================ */

/** @brief Load from atomic32_t */
static inline int32_t vos3_atomic32_load(const vos3_atomic32_t* a)
{
    return (int32_t)vos3_atomic_load32(&a->value);
}

/** @brief Store to atomic32_t */
static inline void vos3_atomic32_store(vos3_atomic32_t* a, int32_t val)
{
    vos3_atomic_store32(&a->value, (uint32_t)val);
}

/** @brief Fetch and add for atomic32_t */
static inline int32_t vos3_atomic32_fetch_add(vos3_atomic32_t* a, int32_t val)
{
    int32_t prev = (int32_t)a->value;
    while (vos3_atomic_cas32(&a->value, (uint32_t)prev, (uint32_t)(prev + val)) != (uint32_t)prev) {
        prev = (int32_t)a->value;
    }
    return prev;
}

/** @brief Fetch and subtract for atomic32_t */
static inline int32_t vos3_atomic32_fetch_sub(vos3_atomic32_t* a, int32_t val)
{
    return vos3_atomic32_fetch_add(a, -val);
}

/** @brief Compare and exchange for atomic32_t */
static inline int32_t vos3_atomic32_cmpxchg(vos3_atomic32_t* a, int32_t expected, int32_t desired)
{
    return (int32_t)vos3_atomic_cas32(&a->value, (uint32_t)expected, (uint32_t)desired);
}

/**
 * @brief CPU relax (multiple pauses for longer waits)
 * @param[in] count Number of pause iterations
 */
static inline void vos3_cpu_relax(uint32_t count)
{
    for (uint32_t i = 0U; i < count; i++) {
        vos3_cpu_pause();
    }
}

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ATOMIC_H */
