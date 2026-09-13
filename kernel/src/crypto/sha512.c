/**
 * @file sha512.c
 * @brief SHA-512 (FIPS 180-4 §6.4) — freestanding kernel implementation.
 *
 * Same 80-round core as sha384.c, with the SHA-512 initial values and a full
 * 64-byte digest. Zero external dependencies, GPR-only, safe at arbitrary
 * kernel call sites. Required by the Ed25519 verifier (RFC 8032) for the M3
 * model-file SecureBoot gate.
 *
 * Verified against FIPS 180-4 Appendix D + RFC 8032 internal use:
 *   SHA-512("abc") = ddaf35a193617aba cc417349ae204131 12e6fa4e89a97ea2
 *                    0a9eeee64b55d39a 2192992a274fc1a8 36ba3c23a3feebbd
 *                    454d4423643ce80e 2a9ac94fa54ca49f
 *
 * @version 1.0.0
 * @date 2026-06-07
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/sha512.h"

/* Round constants K[0..79] — FIPS 180-4 §4.2.3 (identical to SHA-384). */
static const uint64_t K512[80] = {
    0x428A2F98D728AE22ULL, 0x7137449123EF65CDULL, 0xB5C0FBCFEC4D3B2FULL, 0xE9B5DBA58189DBBCULL,
    0x3956C25BF348B538ULL, 0x59F111F1B605D019ULL, 0x923F82A4AF194F9BULL, 0xAB1C5ED5DA6D8118ULL,
    0xD807AA98A3030242ULL, 0x12835B0145706FBEULL, 0x243185BE4EE4B28CULL, 0x550C7DC3D5FFB4E2ULL,
    0x72BE5D74F27B896FULL, 0x80DEB1FE3B1696B1ULL, 0x9BDC06A725C71235ULL, 0xC19BF174CF692694ULL,
    0xE49B69C19EF14AD2ULL, 0xEFBE4786384F25E3ULL, 0x0FC19DC68B8CD5B5ULL, 0x240CA1CC77AC9C65ULL,
    0x2DE92C6F592B0275ULL, 0x4A7484AA6EA6E483ULL, 0x5CB0A9DCBD41FBD4ULL, 0x76F988DA831153B5ULL,
    0x983E5152EE66DFABULL, 0xA831C66D2DB43210ULL, 0xB00327C898FB213FULL, 0xBF597FC7BEEF0EE4ULL,
    0xC6E00BF33DA88FC2ULL, 0xD5A79147930AA725ULL, 0x06CA6351E003826FULL, 0x142929670A0E6E70ULL,
    0x27B70A8546D22FFCULL, 0x2E1B21385C26C926ULL, 0x4D2C6DFC5AC42AEDULL, 0x53380D139D95B3DFULL,
    0x650A73548BAF63DEULL, 0x766A0ABB3C77B2A8ULL, 0x81C2C92E47EDAEE6ULL, 0x92722C851482353BULL,
    0xA2BFE8A14CF10364ULL, 0xA81A664BBC423001ULL, 0xC24B8B70D0F89791ULL, 0xC76C51A30654BE30ULL,
    0xD192E819D6EF5218ULL, 0xD69906245565A910ULL, 0xF40E35855771202AULL, 0x106AA07032BBD1B8ULL,
    0x19A4C116B8D2D0C8ULL, 0x1E376C085141AB53ULL, 0x2748774CDF8EEB99ULL, 0x34B0BCB5E19B48A8ULL,
    0x391C0CB3C5C95A63ULL, 0x4ED8AA4AE3418ACBULL, 0x5B9CCA4F7763E373ULL, 0x682E6FF3D6B2B8A3ULL,
    0x748F82EE5DEFB2FCULL, 0x78A5636F43172F60ULL, 0x84C87814A1F0AB72ULL, 0x8CC702081A6439ECULL,
    0x90BEFFFA23631E28ULL, 0xA4506CEBDE82BDE9ULL, 0xBEF9A3F7B2C67915ULL, 0xC67178F2E372532BULL,
    0xCA273ECEEA26619CULL, 0xD186B8C721C0C207ULL, 0xEADA7DD6CDE0EB1EULL, 0xF57D4F7FEE6ED178ULL,
    0x06F067AA72176FBAULL, 0x0A637DC5A2C898A6ULL, 0x113F9804BEF90DAEULL, 0x1B710B35131C471BULL,
    0x28DB77F523047D84ULL, 0x32CAAB7B40C72493ULL, 0x3C9EBE0A15C9BEBCULL, 0x431D67C49C100D4CULL,
    0x4CC5D4BECB3E42B6ULL, 0x597F299CFC657E2AULL, 0x5FCB6FAB3AD6FAECULL, 0x6C44198C4A475817ULL,
};

/* SHA-512 initial hash values — FIPS 180-4 §5.3.5. */
static const uint64_t H512_0[8] = {
    0x6A09E667F3BCC908ULL, 0xBB67AE8584CAA73BULL,
    0x3C6EF372FE94F82BULL, 0xA54FF53A5F1D36F1ULL,
    0x510E527FADE682D1ULL, 0x9B05688C2B3E6C1FULL,
    0x1F83D9ABFB41BD6BULL, 0x5BE0CD19137E2179ULL,
};

static inline uint64_t ror64(uint64_t x, unsigned n) {
    return (x >> n) | (x << (64U - n));
}

#define CH(x, y, z)   (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z)  (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x)      (ror64((x), 28) ^ ror64((x), 34) ^ ror64((x), 39))
#define BSIG1(x)      (ror64((x), 14) ^ ror64((x), 18) ^ ror64((x), 41))
#define SSIG0(x)      (ror64((x),  1) ^ ror64((x),  8) ^ ((x) >>  7))
#define SSIG1(x)      (ror64((x), 19) ^ ror64((x), 61) ^ ((x) >>  6))

static uint64_t be64_load(const uint8_t *p)
{
    return ((uint64_t)p[0] << 56) | ((uint64_t)p[1] << 48) |
           ((uint64_t)p[2] << 40) | ((uint64_t)p[3] << 32) |
           ((uint64_t)p[4] << 24) | ((uint64_t)p[5] << 16) |
           ((uint64_t)p[6] <<  8) |  (uint64_t)p[7];
}

static void be64_store(uint64_t v, uint8_t *p)
{
    p[0] = (uint8_t)(v >> 56); p[1] = (uint8_t)(v >> 48);
    p[2] = (uint8_t)(v >> 40); p[3] = (uint8_t)(v >> 32);
    p[4] = (uint8_t)(v >> 24); p[5] = (uint8_t)(v >> 16);
    p[6] = (uint8_t)(v >>  8); p[7] = (uint8_t)(v      );
}

static void sha512_compress(uint64_t state[8], const uint8_t block[128])
{
    uint64_t W[80];

    for (unsigned t = 0U; t < 16U; t++) {
        W[t] = be64_load(block + (t * 8U));
    }
    for (unsigned t = 16U; t < 80U; t++) {
        W[t] = SSIG1(W[t - 2]) + W[t - 7] + SSIG0(W[t - 15]) + W[t - 16];
    }

    uint64_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint64_t e = state[4], f = state[5], g = state[6], h = state[7];

    for (unsigned t = 0U; t < 80U; t++) {
        const uint64_t T1 = h + BSIG1(e) + CH(e, f, g) + K512[t] + W[t];
        const uint64_t T2 = BSIG0(a) + MAJ(a, b, c);
        h = g; g = f; f = e; e = d + T1;
        d = c; c = b; b = a; a = T1 + T2;
    }

    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

void vos3_sha512_init(vos3_sha512_ctx_t *ctx)
{
    for (unsigned i = 0U; i < 8U; i++) ctx->state[i] = H512_0[i];
    ctx->count_hi = 0ULL;
    ctx->count_lo = 0ULL;
    ctx->buf_len  = 0U;
}

void vos3_sha512_update(vos3_sha512_ctx_t *ctx, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    size_t remaining = len;

    const uint64_t prev_lo = ctx->count_lo;
    ctx->count_lo += (uint64_t)len * 8ULL;
    if (ctx->count_lo < prev_lo) ctx->count_hi += 1ULL;

    if (ctx->buf_len > 0U) {
        const uint32_t need = VOS3_SHA512_BLOCK_SIZE - ctx->buf_len;
        const uint32_t take = (remaining < need) ? (uint32_t)remaining : need;
        for (uint32_t i = 0U; i < take; i++) {
            ctx->buf[ctx->buf_len + i] = p[i];
        }
        ctx->buf_len += take;
        p         += take;
        remaining -= take;
        if (ctx->buf_len == VOS3_SHA512_BLOCK_SIZE) {
            sha512_compress(ctx->state, ctx->buf);
            ctx->buf_len = 0U;
        }
    }

    while (remaining >= VOS3_SHA512_BLOCK_SIZE) {
        sha512_compress(ctx->state, p);
        p         += VOS3_SHA512_BLOCK_SIZE;
        remaining -= VOS3_SHA512_BLOCK_SIZE;
    }

    for (size_t i = 0U; i < remaining; i++) {
        ctx->buf[ctx->buf_len + i] = p[i];
    }
    ctx->buf_len += (uint32_t)remaining;
}

void vos3_sha512_final(vos3_sha512_ctx_t *ctx, uint8_t out[VOS3_SHA512_DIGEST_SIZE])
{
    ctx->buf[ctx->buf_len++] = 0x80U;
    if (ctx->buf_len > (VOS3_SHA512_BLOCK_SIZE - 16U)) {
        while (ctx->buf_len < VOS3_SHA512_BLOCK_SIZE) ctx->buf[ctx->buf_len++] = 0x00U;
        sha512_compress(ctx->state, ctx->buf);
        ctx->buf_len = 0U;
    }
    while (ctx->buf_len < (VOS3_SHA512_BLOCK_SIZE - 16U)) {
        ctx->buf[ctx->buf_len++] = 0x00U;
    }
    be64_store(ctx->count_hi, ctx->buf + 112U);
    be64_store(ctx->count_lo, ctx->buf + 120U);
    sha512_compress(ctx->state, ctx->buf);

    for (unsigned i = 0U; i < 8U; i++) {
        be64_store(ctx->state[i], out + (i * 8U));
    }
}

void vos3_sha512(const void *data, size_t len, uint8_t out[VOS3_SHA512_DIGEST_SIZE])
{
    vos3_sha512_ctx_t ctx;
    vos3_sha512_init(&ctx);
    vos3_sha512_update(&ctx, data, len);
    vos3_sha512_final(&ctx, out);
}
