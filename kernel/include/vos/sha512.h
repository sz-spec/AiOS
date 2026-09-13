/**
 * @file sha512.h
 * @brief SHA-512 (FIPS 180-4 §6.4) — freestanding kernel implementation.
 *
 * @details Required by the Ed25519 verifier (RFC 8032 uses SHA-512 internally)
 *          for the M3 model-file SecureBoot gate. Shares the SHA-512 round
 *          core with the existing sha384.c; this variant uses the SHA-512
 *          initial values and emits the full 64-byte digest. Streaming API
 *          (init / update / final) plus a one-shot vos3_sha512() wrapper.
 *          Zero external dependencies, GPR-only, safe at arbitrary kernel
 *          call sites.
 */

#ifndef VOS3_SHA512_H
#define VOS3_SHA512_H

#include <stdint.h>
#include <stddef.h>

#define VOS3_SHA512_DIGEST_SIZE    64U
#define VOS3_SHA512_BLOCK_SIZE    128U

typedef struct vos3_sha512_ctx {
    uint64_t state[8];            /* H0..H7 */
    uint64_t count_hi;            /* total bits (high)  */
    uint64_t count_lo;            /* total bits (low)   */
    uint8_t  buf[VOS3_SHA512_BLOCK_SIZE];
    uint32_t buf_len;
} vos3_sha512_ctx_t;

void vos3_sha512_init(vos3_sha512_ctx_t *ctx);
void vos3_sha512_update(vos3_sha512_ctx_t *ctx, const void *data, size_t len);
void vos3_sha512_final(vos3_sha512_ctx_t *ctx, uint8_t out[VOS3_SHA512_DIGEST_SIZE]);

/** @brief One-shot: hash `len` bytes of `data` into `out` (64 bytes). */
void vos3_sha512(const void *data, size_t len,
                 uint8_t out[VOS3_SHA512_DIGEST_SIZE]);

#endif /* VOS3_SHA512_H */
