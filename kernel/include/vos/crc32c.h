/**
 * @file crc32c.h
 * @brief VOS3 CRC32C (Castagnoli) — Shared Kernel Module
 *
 * @details Single canonical CRC32C (polynomial 0x82F63B78) for all kernel
 *          subsystems: VBus, DMA, clipboard, etc.
 *          - Software fallback: 256-entry lookup table
 *          - Hardware acceleration: SSE4.2 crc32q/crc32b instructions
 *
 * @version 1.0.0
 * @date 2026-04-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_CRC32C_H
#define VOS3_CRC32C_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/**
 * @brief Initialize CRC32C lookup table and detect SSE4.2 hardware support.
 *
 * Safe to call multiple times; subsequent calls are no-ops.
 * Called automatically by crc32c() on first use if not explicitly called.
 */
void crc32c_init(void);

/**
 * @brief Compute CRC32C (Castagnoli) checksum over a data buffer.
 *
 * Uses hardware SSE4.2 crc32q/crc32b instructions when available,
 * falls back to software lookup table otherwise.
 *
 * @param data  Pointer to data buffer
 * @param len   Number of bytes to checksum
 * @return CRC32C checksum
 */
uint32_t crc32c(const void *data, size_t len);

/**
 * @brief Incremental CRC32C: feed additional data into a running checksum.
 *
 * Use for computing CRC over non-contiguous buffers:
 *   uint32_t state = CRC32C_INIT;
 *   state = crc32c_update(state, buf_a, len_a);
 *   state = crc32c_update(state, buf_b, len_b);
 *   uint32_t final = crc32c_finish(state);
 *
 * @param state  Running CRC state (start with CRC32C_INIT)
 * @param data   Pointer to data buffer
 * @param len    Number of bytes to feed
 * @return Updated running CRC state (NOT finalized)
 */
uint32_t crc32c_update(uint32_t state, const void *data, size_t len);

/** @brief Finalize a running CRC32C state to produce the final checksum. */
static inline uint32_t crc32c_finish(uint32_t state)
{
    return state ^ 0xFFFFFFFFU;
}

/** @brief Initial CRC32C state for incremental computation. */
#define CRC32C_INIT  0xFFFFFFFFU

/**
 * @brief Query whether SSE4.2 hardware CRC32C is available.
 *
 * @return 1 if hardware CRC32C is supported, 0 for software fallback.
 */
int crc32c_has_hw(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_CRC32C_H */
