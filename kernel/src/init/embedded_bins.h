/**
 * @file embedded_bins.h
 * @brief Embedded user-space binaries header
 * @note This file includes the generated embedded binary data
 */

#ifndef VOS3_EMBEDDED_BINS_H
#define VOS3_EMBEDDED_BINS_H

#include <stdint.h>
#include <stddef.h>

/* Embedded binary structure */
typedef struct {
    const char* name;
    const uint8_t* data;
    size_t size;
} embedded_binary_t;

/* Embedded binaries table (defined in generated embedded_bins.c) */
extern const embedded_binary_t g_embedded_binaries[];
extern const size_t g_embedded_binaries_count;

/* Function to initialize embedded binaries */
int vos3_init_embedded_binaries(void);

#endif /* VOS3_EMBEDDED_BINS_H */
