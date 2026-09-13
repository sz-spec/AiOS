/**
 * @file sha384.h
 * @brief SHA-384 (FIPS 180-4 §6.5) — freestanding kernel implementation.
 *
 * @details Used by the TEE layer to produce 48-byte digests for Intel TDX
 *          RTMR extensions. Streaming API (init / update / final) plus a
 *          one-shot vos3_sha384() convenience wrapper.
 */

#ifndef VOS3_SHA384_H
#define VOS3_SHA384_H

#include <stdint.h>
#include <stddef.h>

#define VOS3_SHA384_DIGEST_SIZE    48U
#define VOS3_SHA384_BLOCK_SIZE    128U

typedef struct vos3_sha384_ctx {
    uint64_t state[8];            /* H0..H7 */
    uint64_t count_hi;            /* total bits (high)  */
    uint64_t count_lo;            /* total bits (low)   */
    uint8_t  buf[VOS3_SHA384_BLOCK_SIZE];
    uint32_t buf_len;
} vos3_sha384_ctx_t;

void vos3_sha384_init(vos3_sha384_ctx_t *ctx);
void vos3_sha384_update(vos3_sha384_ctx_t *ctx, const void *data, size_t len);
void vos3_sha384_final(vos3_sha384_ctx_t *ctx, uint8_t out[VOS3_SHA384_DIGEST_SIZE]);

/** @brief One-shot: hash `len` bytes of `data` into `out`. */
void vos3_sha384(const void *data, size_t len,
                 uint8_t out[VOS3_SHA384_DIGEST_SIZE]);

#endif /* VOS3_SHA384_H */
