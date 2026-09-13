/**
 * @file sha256.c
 * @brief VOS3 SHA-256 and HMAC-SHA256 — FIPS 180-4 / RFC 2104
 *
 * @details Self-contained implementation for VBus frame authentication.
 *          GPR-only (no SSE/AVX), freestanding (no libc). Same style as
 *          kernel/src/crypto/entropy.c.
 *
 * @version 1.0.0
 * @date 2026-04-06
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/sha256.h"
#include "../../include/vos/string.h"   /* memset, memcpy */

/* ============================================================================
 * SHA-256 ROUND CONSTANTS (FIPS 180-4 §4.2.2)
 * ============================================================================ */

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

/* ============================================================================
 * HELPER MACROS
 * ============================================================================ */

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

#define CH(x, y, z)   (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z)  (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))

#define SIGMA0(x) (ROTR(x,  2) ^ ROTR(x, 13) ^ ROTR(x, 22))
#define SIGMA1(x) (ROTR(x,  6) ^ ROTR(x, 11) ^ ROTR(x, 25))
#define sigma0(x) (ROTR(x,  7) ^ ROTR(x, 18) ^ ((x) >>  3))
#define sigma1(x) (ROTR(x, 17) ^ ROTR(x, 19) ^ ((x) >> 10))

/** @brief Big-endian load 32-bit */
static inline uint32_t be32_load(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | (uint32_t)p[3];
}

/** @brief Big-endian store 32-bit */
static inline void be32_store(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

/* ============================================================================
 * SHA-256 COMPRESSION
 * ============================================================================ */

static void sha256_compress(uint32_t state[8], const uint8_t block[64])
{
    uint32_t W[64];
    uint32_t a, b, c, d, e, f, g, h;

    /* Prepare message schedule */
    for (int t = 0; t < 16; t++) {
        W[t] = be32_load(block + t * 4);
    }
    for (int t = 16; t < 64; t++) {
        W[t] = sigma1(W[t - 2]) + W[t - 7] + sigma0(W[t - 15]) + W[t - 16];
    }

    /* Initialize working variables */
    a = state[0]; b = state[1]; c = state[2]; d = state[3];
    e = state[4]; f = state[5]; g = state[6]; h = state[7];

    /* 64 rounds */
    for (int t = 0; t < 64; t++) {
        uint32_t T1 = h + SIGMA1(e) + CH(e, f, g) + K[t] + W[t];
        uint32_t T2 = SIGMA0(a) + MAJ(a, b, c);
        h = g; g = f; f = e; e = d + T1;
        d = c; c = b; b = a; a = T1 + T2;
    }

    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

/* ============================================================================
 * SHA-256 PUBLIC API
 * ============================================================================ */

void vos3_sha256_init(vos3_sha256_ctx_t *ctx)
{
    ctx->state[0] = 0x6a09e667;
    ctx->state[1] = 0xbb67ae85;
    ctx->state[2] = 0x3c6ef372;
    ctx->state[3] = 0xa54ff53a;
    ctx->state[4] = 0x510e527f;
    ctx->state[5] = 0x9b05688c;
    ctx->state[6] = 0x1f83d9ab;
    ctx->state[7] = 0x5be0cd19;
    ctx->total_len = 0;
    ctx->buf_len = 0;
}

void vos3_sha256_update(vos3_sha256_ctx_t *ctx, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    ctx->total_len += len;

    /* Fill partial block first */
    if (ctx->buf_len > 0) {
        uint32_t fill = VOS3_SHA256_BLOCK_SIZE - ctx->buf_len;
        if (len < fill) {
            memcpy(ctx->buf + ctx->buf_len, p, len);
            ctx->buf_len += (uint32_t)len;
            return;
        }
        memcpy(ctx->buf + ctx->buf_len, p, fill);
        sha256_compress(ctx->state, ctx->buf);
        p += fill;
        len -= fill;
        ctx->buf_len = 0;
    }

    /* Process full blocks */
    while (len >= VOS3_SHA256_BLOCK_SIZE) {
        sha256_compress(ctx->state, p);
        p += VOS3_SHA256_BLOCK_SIZE;
        len -= VOS3_SHA256_BLOCK_SIZE;
    }

    /* Buffer remaining bytes */
    if (len > 0) {
        memcpy(ctx->buf, p, len);
        ctx->buf_len = (uint32_t)len;
    }
}

void vos3_sha256_final(vos3_sha256_ctx_t *ctx, uint8_t out[32])
{
    uint64_t total_bits = ctx->total_len * 8;

    /* Padding: 0x80 + zeros + 8-byte big-endian bit count */
    uint8_t pad = 0x80;
    vos3_sha256_update(ctx, &pad, 1);

    /* Zero-pad until 56 bytes mod 64 */
    uint8_t zero = 0;
    while (ctx->buf_len != 56) {
        vos3_sha256_update(ctx, &zero, 1);
    }

    /* Append total bit count (big-endian) */
    uint8_t len_be[8];
    be32_store(len_be,     (uint32_t)(total_bits >> 32));
    be32_store(len_be + 4, (uint32_t)total_bits);
    vos3_sha256_update(ctx, len_be, 8);

    /* Output digest */
    for (int i = 0; i < 8; i++) {
        be32_store(out + i * 4, ctx->state[i]);
    }

    /* Zeroize context for security */
    memset(ctx, 0, sizeof(*ctx));
}

/* ============================================================================
 * HMAC-SHA256 (RFC 2104)
 * ============================================================================ */

void vos3_hmac_sha256(const uint8_t *key, size_t key_len,
                      const void *data, size_t data_len,
                      uint8_t out[32])
{
    vos3_hmac_ctx_t ctx;
    vos3_hmac_sha256_init(&ctx, key, key_len);
    vos3_hmac_sha256_update(&ctx, data, data_len);
    vos3_hmac_sha256_final(&ctx, out);
}

void vos3_hmac_sha256_init(vos3_hmac_ctx_t *ctx,
                           const uint8_t *key, size_t key_len)
{
    uint8_t k_pad[VOS3_SHA256_BLOCK_SIZE];

    /* If key > block size, hash it first */
    if (key_len > VOS3_SHA256_BLOCK_SIZE) {
        vos3_sha256_ctx_t kh;
        vos3_sha256_init(&kh);
        vos3_sha256_update(&kh, key, key_len);
        uint8_t key_hash[32];
        vos3_sha256_final(&kh, key_hash);
        memcpy(k_pad, key_hash, 32);
        memset(k_pad + 32, 0, VOS3_SHA256_BLOCK_SIZE - 32);
        memset(key_hash, 0, 32);
    } else {
        memcpy(k_pad, key, key_len);
        memset(k_pad + key_len, 0, VOS3_SHA256_BLOCK_SIZE - key_len);
    }

    /* Compute o_key_pad and i_key_pad */
    uint8_t i_key_pad[VOS3_SHA256_BLOCK_SIZE];
    for (uint32_t i = 0; i < VOS3_SHA256_BLOCK_SIZE; i++) {
        ctx->o_key_pad[i] = k_pad[i] ^ 0x5c;
        i_key_pad[i]      = k_pad[i] ^ 0x36;
    }

    /* Start inner hash: H(i_key_pad || ...) */
    vos3_sha256_init(&ctx->inner);
    vos3_sha256_update(&ctx->inner, i_key_pad, VOS3_SHA256_BLOCK_SIZE);

    /* Zeroize temporaries */
    memset(k_pad, 0, VOS3_SHA256_BLOCK_SIZE);
    memset(i_key_pad, 0, VOS3_SHA256_BLOCK_SIZE);
}

void vos3_hmac_sha256_update(vos3_hmac_ctx_t *ctx,
                             const void *data, size_t len)
{
    vos3_sha256_update(&ctx->inner, data, len);
}

void vos3_hmac_sha256_final(vos3_hmac_ctx_t *ctx, uint8_t out[32])
{
    /* Finalize inner hash */
    uint8_t inner_digest[32];
    vos3_sha256_final(&ctx->inner, inner_digest);

    /* Outer hash: H(o_key_pad || inner_digest) */
    vos3_sha256_ctx_t outer;
    vos3_sha256_init(&outer);
    vos3_sha256_update(&outer, ctx->o_key_pad, VOS3_SHA256_BLOCK_SIZE);
    vos3_sha256_update(&outer, inner_digest, 32);
    vos3_sha256_final(&outer, out);

    /* Zeroize */
    memset(inner_digest, 0, 32);
    memset(ctx, 0, sizeof(*ctx));
}
