/**
 * @file mlkem768.c
 * @brief ML-KEM-768 + SHAKE-128/256 (Stage 14 scaffolding).
 *
 * SCOPE — please read before adding callers
 * ==========================================
 *
 * This file ships in two clearly-delimited parts:
 *
 *   PART 1 — REAL: Keccak-f[1600] permutation + SHAKE-128/256 sponge.
 *     Implements FIPS 202 §3 (Keccak-f) and §4 (SHA-3 / SHAKE family).
 *     Self-tested at boot via vos3_mlkem768_self_test() against the
 *     KAT-known-empty-input vectors. This part is correct and usable as
 *     a general-purpose primitive.
 *
 *   PART 2 — STUBBED: ML-KEM-768 keygen / encaps / decaps.
 *     The three public functions return VOS3_MLKEM_E_NOT_IMPLEMENTED.
 *     Reason: a ship-grade Kyber-768 implementation (NTT, sample-from-CBD,
 *     polynomial encode/decode, K-PKE, Fujisaki-Okamoto, constant-time)
 *     is roughly 1500 lines of careful crypto C with full FIPS 203 KAT
 *     coverage. It cannot be honestly squeezed into the same commit as
 *     the SHAKE foundation. Stage 14.B.2 is the ML-KEM lattice port.
 *
 * Why ship the half-and-half
 * --------------------------
 *
 * A surprising amount of "PQ readiness" work depends on having a real
 * SHAKE implementation in tree. The kernel can use SHAKE today for:
 *   - High-rate domain-separated derivation (replaces ad-hoc
 *     SHA-256-with-counter constructions in some places).
 *   - Sigstore v3 bundle building if the upstream spec mandates SHA3.
 *   - The eventual ML-KEM port — every lattice operation feeds through
 *     SHAKE somewhere.
 *
 * Shipping SHAKE now means the foundation is ready when the lattice
 * port lands; nothing else has to change in the surrounding kernel.
 *
 * @date 2026-05-09
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#include "../../include/vos/mlkem768.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * PART 1 — REAL: Keccak-f[1600] + SHAKE-128/256
 *
 * Reference: FIPS 202, "SHA-3 Standard". Implementation style is the
 * Bertoni/Daemen/Peeters/Van Assche public-domain reference — restated
 * for kernel context (no malloc, fixed-size state).
 *
 * The state is 25 64-bit lanes (200 bytes). The permutation is 24 rounds.
 * SHAKE-128 has rate r=168 bytes; SHAKE-256 has rate r=136 bytes.
 * Both have capacity c=2*security_strength.
 * ============================================================================ */

#define KECCAK_LANES   25U
#define KECCAK_ROUNDS  24U
#define SHAKE128_RATE  168U
#define SHAKE256_RATE  136U

static const uint64_t k_keccak_rc[KECCAK_ROUNDS] = {
    0x0000000000000001ULL, 0x0000000000008082ULL,
    0x800000000000808AULL, 0x8000000080008000ULL,
    0x000000000000808BULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL,
    0x000000000000008AULL, 0x0000000000000088ULL,
    0x0000000080008009ULL, 0x000000008000000AULL,
    0x000000008000808BULL, 0x800000000000008BULL,
    0x8000000000008089ULL, 0x8000000000008003ULL,
    0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800AULL, 0x800000008000000AULL,
    0x8000000080008081ULL, 0x8000000000008080ULL,
    0x0000000080000001ULL, 0x8000000080008008ULL,
};

static const unsigned k_keccak_rho[24] = {
     1,  3,  6, 10, 15, 21, 28, 36,
    45, 55,  2, 14, 27, 41, 56,  8,
    25, 43, 62, 18, 39, 61, 20, 44,
};

static const unsigned k_keccak_pi[24] = {
    10,  7, 11, 17, 18,  3,  5, 16,
     8, 21, 24,  4, 15, 23, 19, 13,
    12,  2, 20, 14, 22,  9,  6,  1,
};

static inline uint64_t rotl64(uint64_t x, unsigned n)
{
    return (x << n) | (x >> ((64u - n) & 63u));
}

static void keccak_f1600(uint64_t state[KECCAK_LANES])
{
    for (unsigned r = 0u; r < KECCAK_ROUNDS; r++) {
        uint64_t bc[5];
        /* Theta */
        for (unsigned i = 0u; i < 5u; i++) {
            bc[i] = state[i] ^ state[i + 5] ^ state[i + 10]
                  ^ state[i + 15] ^ state[i + 20];
        }
        for (unsigned i = 0u; i < 5u; i++) {
            const uint64_t t = bc[(i + 4u) % 5u]
                             ^ rotl64(bc[(i + 1u) % 5u], 1u);
            for (unsigned j = 0u; j < 25u; j += 5u) {
                state[j + i] ^= t;
            }
        }
        /* Rho + Pi */
        uint64_t t = state[1];
        for (unsigned i = 0u; i < 24u; i++) {
            const unsigned j = k_keccak_pi[i];
            const uint64_t  s = state[j];
            state[j] = rotl64(t, k_keccak_rho[i]);
            t = s;
        }
        /* Chi */
        for (unsigned j = 0u; j < 25u; j += 5u) {
            uint64_t bc2[5];
            for (unsigned i = 0u; i < 5u; i++) {
                bc2[i] = state[j + i];
            }
            for (unsigned i = 0u; i < 5u; i++) {
                state[j + i] = bc2[i] ^ ((~bc2[(i + 1u) % 5u])
                                       & bc2[(i + 2u) % 5u]);
            }
        }
        /* Iota */
        state[0] ^= k_keccak_rc[r];
    }
}

/* Generic SHAKE: rate is the only differing parameter.
 *
 * SHAKE absorbs `in_len` bytes, applies the SHA-3 padding (0x1F start byte
 * + 0x80 final-bit at rate-1), then squeezes `out_len` bytes. Each squeeze
 * reads `rate` bytes then permutes.
 */
static int shake_generic(const uint8_t *in, size_t in_len,
                         uint8_t *out, size_t out_len,
                         size_t rate)
{
    if ((in == (const uint8_t *)0 && in_len != 0u) ||
        (out == (uint8_t *)0 && out_len != 0u)) {
        return VOS3_MLKEM_E_NULL;
    }
    if (rate != SHAKE128_RATE && rate != SHAKE256_RATE) {
        return VOS3_MLKEM_E_INVAL;
    }

    uint64_t state[KECCAK_LANES] = {0};
    uint8_t  block[SHAKE128_RATE];

    /* Absorb full blocks */
    size_t off = 0u;
    while (in_len - off >= rate) {
        for (size_t i = 0u; i < rate; i++) {
            block[i] = in[off + i];
        }
        for (size_t i = 0u; i < rate; i++) {
            ((uint8_t *)state)[i] ^= block[i];
        }
        keccak_f1600(state);
        off += rate;
    }

    /* Final block + SHA-3 SHAKE padding (0x1F ... 0x80) */
    const size_t left = in_len - off;
    for (size_t i = 0u; i < rate; i++) {
        block[i] = 0u;
    }
    for (size_t i = 0u; i < left; i++) {
        block[i] = in[off + i];
    }
    block[left] = 0x1Fu;          /* SHAKE domain-sep + first pad bit */
    block[rate - 1u] |= 0x80u;    /* final pad bit */
    for (size_t i = 0u; i < rate; i++) {
        ((uint8_t *)state)[i] ^= block[i];
    }

    /* Squeeze */
    size_t produced = 0u;
    while (produced < out_len) {
        keccak_f1600(state);
        const size_t take = (out_len - produced < rate)
                              ? (out_len - produced)
                              : rate;
        for (size_t i = 0u; i < take; i++) {
            out[produced + i] = ((const uint8_t *)state)[i];
        }
        produced += take;
    }

    return VOS3_MLKEM_OK;
}

int vos3_shake128_xof(const uint8_t *in, size_t in_len,
                      uint8_t *out, size_t out_len)
{
    return shake_generic(in, in_len, out, out_len, SHAKE128_RATE);
}

int vos3_shake256_xof(const uint8_t *in, size_t in_len,
                      uint8_t *out, size_t out_len)
{
    return shake_generic(in, in_len, out, out_len, SHAKE256_RATE);
}

/* ============================================================================
 * PART 2 — STUBBED: ML-KEM-768 keygen / encaps / decaps
 *
 * These exist so the kernel can be wired against the public surface today.
 * Stage 14.B.2 will replace each body with the real lattice computation.
 *
 * IMPORTANT: these stubs return VOS3_MLKEM_E_NOT_IMPLEMENTED — they do NOT
 * silently produce zeros, NULL, or "looks-OK-but-isn't" output. A caller
 * that ignores the return code and uses the buffers gets zeroed memory,
 * which would fail any cryptographic check immediately.
 * ============================================================================ */

int vos3_mlkem768_keygen(const uint8_t  rand[VOS3_MLKEM768_KEYGEN_RAND_BYTES],
                         uint8_t        pk[VOS3_MLKEM768_PK_BYTES],
                         uint8_t        sk[VOS3_MLKEM768_SK_BYTES])
{
    (void)rand;
    if (pk != (uint8_t *)0) {
        for (size_t i = 0u; i < VOS3_MLKEM768_PK_BYTES; i++) pk[i] = 0u;
    }
    if (sk != (uint8_t *)0) {
        for (size_t i = 0u; i < VOS3_MLKEM768_SK_BYTES; i++) sk[i] = 0u;
    }
    return VOS3_MLKEM_E_NOT_IMPLEMENTED;
}

int vos3_mlkem768_encaps(const uint8_t  pk[VOS3_MLKEM768_PK_BYTES],
                         const uint8_t  rand[VOS3_MLKEM768_ENCAPS_RAND_BYTES],
                         uint8_t        ct[VOS3_MLKEM768_CT_BYTES],
                         uint8_t        ss[VOS3_MLKEM768_SS_BYTES])
{
    (void)pk;
    (void)rand;
    if (ct != (uint8_t *)0) {
        for (size_t i = 0u; i < VOS3_MLKEM768_CT_BYTES; i++) ct[i] = 0u;
    }
    if (ss != (uint8_t *)0) {
        for (size_t i = 0u; i < VOS3_MLKEM768_SS_BYTES; i++) ss[i] = 0u;
    }
    return VOS3_MLKEM_E_NOT_IMPLEMENTED;
}

int vos3_mlkem768_decaps(const uint8_t  sk[VOS3_MLKEM768_SK_BYTES],
                         const uint8_t  ct[VOS3_MLKEM768_CT_BYTES],
                         uint8_t        ss[VOS3_MLKEM768_SS_BYTES])
{
    (void)sk;
    (void)ct;
    if (ss != (uint8_t *)0) {
        for (size_t i = 0u; i < VOS3_MLKEM768_SS_BYTES; i++) ss[i] = 0u;
    }
    return VOS3_MLKEM_E_NOT_IMPLEMENTED;
}

/* ============================================================================
 * Self-test
 *
 * Tests SHAKE-128/256 against the FIPS 202 KAT for empty input. Both digests
 * for empty-input are well-known; mismatching them would indicate a Keccak
 * codegen problem on this build.
 *
 *   SHAKE-128("", 16) =
 *     7F9C2BA4 E88F827D 61604550 7605853E
 *   SHAKE-256("", 32) =
 *     46B9DD2B 0BA88D13 233B3FEB 743EEB24
 *     3FCD52EA 62B81B82 B50C2764 6ED5762F
 *
 * Source: NIST SHAKE128/256 LongMsg KAT, empty-input row.
 * ============================================================================ */

static const uint8_t k_shake128_empty_kat[16] = {
    0x7Fu, 0x9Cu, 0x2Bu, 0xA4u, 0xE8u, 0x8Fu, 0x82u, 0x7Du,
    0x61u, 0x60u, 0x45u, 0x50u, 0x76u, 0x05u, 0x85u, 0x3Eu,
};

static const uint8_t k_shake256_empty_kat[32] = {
    0x46u, 0xB9u, 0xDDu, 0x2Bu, 0x0Bu, 0xA8u, 0x8Du, 0x13u,
    0x23u, 0x3Bu, 0x3Fu, 0xEBu, 0x74u, 0x3Eu, 0xEBu, 0x24u,
    0x3Fu, 0xCDu, 0x52u, 0xEAu, 0x62u, 0xB8u, 0x1Bu, 0x82u,
    0xB5u, 0x0Cu, 0x27u, 0x64u, 0x6Eu, 0xD5u, 0x76u, 0x2Fu,
};

static int bytes_eq(const uint8_t *a, const uint8_t *b, size_t n)
{
    uint8_t diff = 0u;
    for (size_t i = 0u; i < n; i++) {
        diff |= (uint8_t)(a[i] ^ b[i]);
    }
    return diff == 0u;
}

int vos3_mlkem768_self_test(void)
{
    uint8_t out128[16];
    uint8_t out256[32];

    if (vos3_shake128_xof((const uint8_t *)0, 0u, out128, sizeof(out128)) != 0) {
        VOS3_DEBUG("[MLKEM] SHAKE-128 xof returned error on self-test");
        return -1;
    }
    if (!bytes_eq(out128, k_shake128_empty_kat, sizeof(out128))) {
        VOS3_DEBUG("[MLKEM] SHAKE-128 empty-input KAT mismatch — codegen issue");
        return -2;
    }

    if (vos3_shake256_xof((const uint8_t *)0, 0u, out256, sizeof(out256)) != 0) {
        VOS3_DEBUG("[MLKEM] SHAKE-256 xof returned error on self-test");
        return -3;
    }
    if (!bytes_eq(out256, k_shake256_empty_kat, sizeof(out256))) {
        VOS3_DEBUG("[MLKEM] SHAKE-256 empty-input KAT mismatch — codegen issue");
        return -4;
    }

    VOS3_DEBUG("[MLKEM] SHAKE-128/256 KATs PASS; lattice ops STUBBED "
               "(rc=VOS3_MLKEM_E_NOT_IMPLEMENTED until Stage 14.B.2)");
    return VOS3_MLKEM_OK;
}
