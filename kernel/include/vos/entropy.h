/**
 * @file entropy.h
 * @brief VOS3 Entropy Subsystem — RDRAND/RDSEED + ChaCha20 CSPRNG
 *
 * @details Provides cryptographically secure random number generation using
 *          hardware RNG (RDRAND/RDSEED) seeded into a ChaCha20 stream cipher.
 *          Supports forward secrecy (re-key after every extraction) and
 *          fork safety (PID + RDTSC mixing).
 *
 * @version 1.0.0
 * @date 2026-03-19
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_ENTROPY_H
#define VOS3_ENTROPY_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/**
 * @brief Initialize the entropy subsystem
 *
 * Detects RDRAND/RDSEED via CPUID, seeds the ChaCha20 CSPRNG pool.
 * Must be called early in kernel init, after CPU detection.
 */
void vos3_entropy_init(void);

/**
 * @brief Reseed the entropy pool
 *
 * Mixes fresh hardware entropy into the CSPRNG state.
 * Called automatically after 1MB of output, and should be called
 * explicitly in fork child paths for fork safety.
 */
void vos3_entropy_reseed(void);

/**
 * @brief Extract cryptographically secure random bytes
 *
 * @param[out] buf  Destination buffer
 * @param[in]  len  Number of bytes to extract
 * @return 0 on success, -1 on failure
 *
 * Thread-safe (spinlock-protected). Provides forward secrecy by
 * re-keying after every extraction. Mixes PID + RDTSC for fork safety.
 */
int vos3_entropy_extract(void* buf, size_t len);

/**
 * @brief Get a single random 32-bit value
 * @return Random uint32_t
 */
uint32_t vos3_entropy_get_u32(void);

/**
 * @brief Get a single random 64-bit value
 * @return Random uint64_t
 */
uint64_t vos3_entropy_get_u64(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ENTROPY_H */
