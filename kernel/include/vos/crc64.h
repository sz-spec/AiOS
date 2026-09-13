#ifndef VOS3_CRC64_H
#define VOS3_CRC64_H

#include <stdint.h>
#include <stddef.h>

/**
 * @brief Initialize CRC64-ECMA lookup table
 * Must be called once during boot before any CRC64 operations.
 */
void vos3_crc64_init(void);

/**
 * @brief Compute CRC64-ECMA checksum
 * @param crc Initial CRC value (0 for first call)
 * @param data Pointer to data
 * @param len Length of data in bytes
 * @return Updated CRC64 value
 */
uint64_t vos3_crc64(uint64_t crc, const void *data, size_t len);

#endif /* VOS3_CRC64_H */
