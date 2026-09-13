/**
 * @file sha256.h
 * @brief VOS3 SHA-256 and HMAC-SHA256 — FIPS 180-4 / RFC 2104
 *
 * @details Self-contained SHA-256 hash and HMAC-SHA256 MAC for VBus
 *          frame authentication. GPR-only (no SSE), freestanding.
 *
 * @version 1.0.0
 * @date 2026-04-06
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_SHA256_H
#define VOS3_SHA256_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

#define VOS3_SHA256_BLOCK_SIZE  64U
#define VOS3_SHA256_DIGEST_SIZE 32U

/** SHA-256 context */
typedef struct vos3_sha256_ctx {
    uint32_t state[8];
    uint64_t total_len;
    uint8_t  buf[VOS3_SHA256_BLOCK_SIZE];
    uint32_t buf_len;
} vos3_sha256_ctx_t;

/** HMAC-SHA256 context (incremental) */
typedef struct vos3_hmac_ctx {
    vos3_sha256_ctx_t inner;
    uint8_t           o_key_pad[VOS3_SHA256_BLOCK_SIZE];
} vos3_hmac_ctx_t;

/* Raw SHA-256 */
void vos3_sha256_init(vos3_sha256_ctx_t *ctx);
void vos3_sha256_update(vos3_sha256_ctx_t *ctx, const void *data, size_t len);
void vos3_sha256_final(vos3_sha256_ctx_t *ctx, uint8_t out[32]);

/* Single-shot HMAC-SHA256 */
void vos3_hmac_sha256(const uint8_t *key, size_t key_len,
                      const void *data, size_t data_len,
                      uint8_t out[32]);

/* Incremental HMAC-SHA256 (avoids header+payload concatenation) */
void vos3_hmac_sha256_init(vos3_hmac_ctx_t *ctx,
                           const uint8_t *key, size_t key_len);
void vos3_hmac_sha256_update(vos3_hmac_ctx_t *ctx,
                             const void *data, size_t len);
void vos3_hmac_sha256_final(vos3_hmac_ctx_t *ctx, uint8_t out[32]);

/**
 * @brief Constant-time memory equality check (timing-attack resistant).
 *
 * Uses volatile XOR accumulator to prevent compiler optimization of
 * early-exit branches that would leak comparison results via timing.
 *
 * @param a   First buffer
 * @param b   Second buffer
 * @param len Number of bytes to compare
 * @return 1 if equal, 0 if different
 */
int vos3_ct_equal(const uint8_t *a, const uint8_t *b, size_t len);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SHA256_H */
