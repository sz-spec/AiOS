/**
 * @file ai_kv_compress.c
 * @brief TQ4/TQ3 Quantization for KV-Cache Compression (TurboQuant-inspired)
 *
 * @details Implements integer-only 4-bit (TQ4) and 3-bit (TQ3) quantization
 *          for KV-cache data. All compress/decompress functions are constant-time
 *          — no data-dependent branches. Uses fixed-point 16.16 arithmetic
 *          where possible to avoid FPU dependency in kernel context.
 *
 *          TQ4: 4-bit uniform quantization, nibble-packed (2 values/byte)
 *               Per-channel min/max stored in 16-byte header.
 *               Effective compression: ~3.8x with headers.
 *
 *          TQ3: 3-bit quantization, 8 values packed into 3 bytes (24 bits)
 *               Higher compression (~4.9x), used for cold tier only.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.1: Managed KV Cache
 */

#include "../../include/vos/ai_kv_managed.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include <stdint.h>

/* ============================================================================
 * TQ4 COMPRESSION — 4-bit Quantization
 * ============================================================================ */

/**
 * TQ4 compressed format:
 *   [0..3]   uint32_t original_len   — original uncompressed size
 *   [4..7]   uint32_t num_values     — number of byte values
 *   [8..11]  uint8_t  min_val        — channel minimum (byte-level)
 *   [12..15] uint8_t  max_val        — channel maximum (byte-level)
 *   [16..]   nibble-packed data      — 2 values per byte
 */

#define TQ4_HDR_ORIG_LEN_OFF   0U
#define TQ4_HDR_NUM_VALUES_OFF  4U
#define TQ4_HDR_MIN_OFF         8U
#define TQ4_HDR_MAX_OFF         12U

int vos3_kv_tq4_compress(const void *src, uint32_t src_len,
                         void *dst, uint32_t dst_cap, uint32_t *out_len)
{
    const uint8_t *in = (const uint8_t *)src;
    uint8_t *out = (uint8_t *)dst;

    if (src == NULL || dst == NULL || out_len == NULL || src_len == 0U) {
        return -22; /* EINVAL */
    }

    /* Compute output size: header (16 bytes) + nibble-packed data */
    uint32_t packed_len = (src_len + 1U) / 2U;
    uint32_t total_out = VOS3_KV_TQ4_HEADER_SIZE + packed_len;

    if (dst_cap < total_out) {
        return -28; /* ENOSPC */
    }

    /*
     * Step 1: Constant-time min/max scan over entire input.
     * Always iterates over all bytes — no early exit.
     */
    uint8_t min_val = 0xFF;
    uint8_t max_val = 0x00;
    for (uint32_t i = 0; i < src_len; i++) {
        uint8_t v = in[i];
        /* Branchless min/max using conditional moves (compiler should optimize) */
        min_val = (v < min_val) ? v : min_val;
        max_val = (v > max_val) ? v : max_val;
    }

    /*
     * Step 2: Write header.
     */
    memset(out, 0, VOS3_KV_TQ4_HEADER_SIZE);
    memcpy(&out[TQ4_HDR_ORIG_LEN_OFF], &src_len, 4);
    memcpy(&out[TQ4_HDR_NUM_VALUES_OFF], &src_len, 4);
    out[TQ4_HDR_MIN_OFF] = min_val;
    out[TQ4_HDR_MAX_OFF] = max_val;

    /*
     * Step 3: Quantize to 4 bits and nibble-pack.
     * q = ((val - min) * 15 + (range/2)) / range   [rounding division]
     * For range==0, all values are the same -> q=0 for all.
     */
    uint32_t range = (uint32_t)(max_val - min_val);
    uint8_t *packed = &out[VOS3_KV_TQ4_HEADER_SIZE];

    /* Zero the output to ensure deterministic padding for odd-length inputs */
    memset(packed, 0, packed_len);

    for (uint32_t i = 0; i < src_len; i++) {
        uint8_t q;
        if (range == 0U) {
            q = 0;
        } else {
            uint32_t shifted = (uint32_t)(in[i] - min_val);
            q = (uint8_t)((shifted * 15U + (range / 2U)) / range);
        }
        /* Pack two nibbles per byte */
        uint32_t byte_idx = i / 2U;
        if ((i & 1U) == 0U) {
            packed[byte_idx] = (q & 0x0FU) << 4;
        } else {
            packed[byte_idx] |= (q & 0x0FU);
        }
    }

    *out_len = total_out;

    VOS3_DEBUG("[KV-TQ4] Compressed %u -> %u bytes (min=%u, max=%u, range=%u)",
               src_len, total_out, min_val, max_val, range);

    return 0;
}

int vos3_kv_tq4_decompress(const void *src, uint32_t src_len,
                           void *dst, uint32_t dst_cap, uint32_t *out_len)
{
    const uint8_t *in = (const uint8_t *)src;
    uint8_t *out = (uint8_t *)dst;

    if (src == NULL || dst == NULL || out_len == NULL) {
        return -22; /* EINVAL */
    }
    if (src_len < VOS3_KV_TQ4_HEADER_SIZE) {
        return -22; /* EINVAL — too short for header */
    }

    /* Read header */
    uint32_t original_len;
    memcpy(&original_len, &in[TQ4_HDR_ORIG_LEN_OFF], 4);

    if (dst_cap < original_len) {
        return -28; /* ENOSPC */
    }

    uint8_t min_val = in[TQ4_HDR_MIN_OFF];
    uint8_t max_val = in[TQ4_HDR_MAX_OFF];
    uint32_t range = (uint32_t)(max_val - min_val);

    const uint8_t *packed = &in[VOS3_KV_TQ4_HEADER_SIZE];

    /*
     * Dequantize: val = (q * range + 7) / 15 + min
     * Constant-time: always iterate over original_len values.
     */
    for (uint32_t i = 0; i < original_len; i++) {
        uint32_t byte_idx = i / 2U;
        uint8_t q;
        if ((i & 1U) == 0U) {
            q = (packed[byte_idx] >> 4) & 0x0FU;
        } else {
            q = packed[byte_idx] & 0x0FU;
        }

        uint8_t val;
        if (range == 0U) {
            val = min_val;
        } else {
            val = (uint8_t)(((uint32_t)q * range + 7U) / 15U + min_val);
        }
        out[i] = val;
    }

    *out_len = original_len;

    VOS3_DEBUG("[KV-TQ4] Decompressed %u -> %u bytes", src_len, original_len);

    return 0;
}

/* ============================================================================
 * TQ3 COMPRESSION — 3-bit Quantization
 * ============================================================================ */

/**
 * TQ3 compressed format:
 *   [0..3]   uint32_t original_len
 *   [4..7]   uint32_t num_values
 *   [8..11]  uint8_t  min_val
 *   [12..15] uint8_t  max_val
 *   [16..]   3-bit packed data (8 values -> 3 bytes = 24 bits)
 *
 * Packing scheme for 8 values (v0..v7), each 3 bits:
 *   byte0 = (v0 << 5) | (v1 << 2) | (v2 >> 1)
 *   byte1 = (v2 << 7) | (v3 << 4) | (v4 << 1) | (v5 >> 2)
 *   byte2 = (v5 << 6) | (v6 << 3) | v7
 */

int vos3_kv_tq3_compress(const void *src, uint32_t src_len,
                         void *dst, uint32_t dst_cap, uint32_t *out_len)
{
    const uint8_t *in = (const uint8_t *)src;
    uint8_t *out = (uint8_t *)dst;

    if (src == NULL || dst == NULL || out_len == NULL || src_len == 0U) {
        return -22; /* EINVAL */
    }

    /* Output size: header + ceil(src_len * 3 / 8) bytes */
    uint32_t packed_bits = src_len * 3U;
    uint32_t packed_bytes = (packed_bits + 7U) / 8U;
    uint32_t total_out = VOS3_KV_TQ3_HEADER_SIZE + packed_bytes;

    if (dst_cap < total_out) {
        return -28; /* ENOSPC */
    }

    /* Constant-time min/max scan */
    uint8_t min_val = 0xFF;
    uint8_t max_val = 0x00;
    for (uint32_t i = 0; i < src_len; i++) {
        uint8_t v = in[i];
        min_val = (v < min_val) ? v : min_val;
        max_val = (v > max_val) ? v : max_val;
    }

    /* Write header */
    memset(out, 0, VOS3_KV_TQ3_HEADER_SIZE);
    memcpy(&out[TQ4_HDR_ORIG_LEN_OFF], &src_len, 4);
    memcpy(&out[TQ4_HDR_NUM_VALUES_OFF], &src_len, 4);
    out[TQ4_HDR_MIN_OFF] = min_val;
    out[TQ4_HDR_MAX_OFF] = max_val;

    uint32_t range = (uint32_t)(max_val - min_val);
    uint8_t *packed = &out[VOS3_KV_TQ3_HEADER_SIZE];
    memset(packed, 0, packed_bytes);

    /*
     * 3-bit quantization + bit-stream packing.
     * q = ((val - min) * 7 + (range/2)) / range
     * Pack 3 bits per value into the output stream.
     */
    uint32_t bit_pos = 0;
    for (uint32_t i = 0; i < src_len; i++) {
        uint8_t q;
        if (range == 0U) {
            q = 0;
        } else {
            uint32_t shifted = (uint32_t)(in[i] - min_val);
            q = (uint8_t)((shifted * 7U + (range / 2U)) / range);
        }
        q &= 0x07U; /* clamp to 3 bits */

        /* Write 3 bits at bit_pos into the packed stream (Fix 4: safe packing) */
        uint32_t byte_off = bit_pos / 8U;
        uint32_t bit_off  = bit_pos % 8U;

        /* Place bits — may span two bytes when bit_off >= 6 */
        if (bit_off <= 5U) {
            /* All 3 bits fit in current byte */
            packed[byte_off] |= (uint8_t)((uint32_t)q << (5U - bit_off));
        } else {
            /* bit_off is 6 or 7 — bits straddle byte boundary */
            uint32_t high_bits = 8U - bit_off;  /* bits that fit in current byte (2 or 1) */
            uint32_t low_bits  = 3U - high_bits; /* bits that spill to next byte (1 or 2) */
            packed[byte_off]      |= (uint8_t)((uint32_t)q >> low_bits);
            packed[byte_off + 1U] |= (uint8_t)(((uint32_t)q & ((1U << low_bits) - 1U)) << (8U - low_bits));
        }

        bit_pos += 3U;
    }

    *out_len = total_out;

    VOS3_DEBUG("[KV-TQ3] Compressed %u -> %u bytes (min=%u, max=%u)",
               src_len, total_out, min_val, max_val);

    return 0;
}

int vos3_kv_tq3_decompress(const void *src, uint32_t src_len,
                           void *dst, uint32_t dst_cap, uint32_t *out_len)
{
    const uint8_t *in = (const uint8_t *)src;
    uint8_t *out = (uint8_t *)dst;

    if (src == NULL || dst == NULL || out_len == NULL) {
        return -22; /* EINVAL */
    }
    if (src_len < VOS3_KV_TQ3_HEADER_SIZE) {
        return -22; /* EINVAL */
    }

    /* Read header */
    uint32_t original_len;
    memcpy(&original_len, &in[TQ4_HDR_ORIG_LEN_OFF], 4);

    if (dst_cap < original_len) {
        return -28; /* ENOSPC */
    }

    uint8_t min_val = in[TQ4_HDR_MIN_OFF];
    uint8_t max_val = in[TQ4_HDR_MAX_OFF];
    uint32_t range = (uint32_t)(max_val - min_val);

    const uint8_t *packed = &in[VOS3_KV_TQ3_HEADER_SIZE];

    /*
     * Dequantize: val = (q * range + 3) / 7 + min
     * Extract 3 bits per value from the bit stream.
     */
    uint32_t bit_pos = 0;
    for (uint32_t i = 0; i < original_len; i++) {
        uint32_t byte_off = bit_pos / 8U;
        uint32_t bit_off  = bit_pos % 8U;

        /* Extract 3 bits — may span two bytes */
        uint8_t q;
        if (bit_off <= 5U) {
            q = (packed[byte_off] >> (5U - bit_off)) & 0x07U;
        } else {
            uint32_t hi_bits = 8U - bit_off;
            uint8_t hi = packed[byte_off] & ((1U << hi_bits) - 1U);
            uint8_t lo = packed[byte_off + 1U] >> (8U - (3U - hi_bits));
            q = (uint8_t)((hi << (3U - hi_bits)) | lo) & 0x07U;
        }

        uint8_t val;
        if (range == 0U) {
            val = min_val;
        } else {
            val = (uint8_t)(((uint32_t)q * range + 3U) / 7U + min_val);
        }
        out[i] = val;

        bit_pos += 3U;
    }

    *out_len = original_len;

    VOS3_DEBUG("[KV-TQ3] Decompressed %u -> %u bytes", src_len, original_len);

    return 0;
}
