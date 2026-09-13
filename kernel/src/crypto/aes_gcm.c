/**
 * @file aes_gcm.c
 * @brief VOS3 AES-GCM Authenticated Encryption — NIST SP 800-38D
 *
 * @details Freestanding kernel implementation of AES-128-GCM and AES-256-GCM
 *          for TLS 1.3 record protection.
 *
 *          Two code paths:
 *          - GPR-only (fallback): S-box + ShiftRows + MixColumns + AddRoundKey
 *          - AES-NI (fast): stub that falls back to GPR for now
 *
 *          GCM uses CTR mode (encrypt-only AES) + GHASH (GF(2^128) polynomial
 *          multiplication) for authentication.
 *
 *          Security properties:
 *          - Constant-time tag comparison in decrypt (prevents timing oracles)
 *          - Plaintext zeroed on authentication failure (no partial output)
 *          - Key material wiped in destroy via vos3_cache_wipe()
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.2 -- Sovereign Crypto Shield (v35.1)
 *       Built with per-file CFLAGS override: -maes -msse2 -mpclmul
 *       (but the GPR fallback works without any SIMD instructions)
 */

#include "../../include/vos/tls.h"
#include "../../include/vos/crypto.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * AES S-BOX (FIPS 197 Figure 7)
 * ============================================================================
 *
 * The forward S-box maps each byte through the GF(2^8) multiplicative
 * inverse followed by an affine transformation. Used in SubBytes and
 * key expansion.
 */

static const uint8_t aes_sbox[256] = {
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5,
    0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0,
    0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc,
    0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a,
    0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0,
    0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b,
    0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85,
    0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5,
    0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17,
    0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88,
    0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c,
    0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9,
    0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6,
    0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e,
    0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94,
    0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68,
    0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
};

/* ============================================================================
 * AES ROUND CONSTANTS (FIPS 197 §5.2)
 * ============================================================================ */

static const uint8_t aes_rcon[11] = {
    0x00, /* unused (rcon[0] is not used) */
    0x01, 0x02, 0x04, 0x08, 0x10,
    0x20, 0x40, 0x80, 0x1b, 0x36
};

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/** @brief Load 32-bit value from byte array (big-endian) */
static inline uint32_t be32_load(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | (uint32_t)p[3];
}

/** @brief Store 32-bit value to byte array (big-endian) */
static inline void be32_store(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

/** @brief Load 64-bit value from byte array (big-endian) */
static inline uint64_t be64_load(const uint8_t *p)
{
    return ((uint64_t)p[0] << 56) | ((uint64_t)p[1] << 48) |
           ((uint64_t)p[2] << 40) | ((uint64_t)p[3] << 32) |
           ((uint64_t)p[4] << 24) | ((uint64_t)p[5] << 16) |
           ((uint64_t)p[6] << 8)  | (uint64_t)p[7];
}

/** @brief Store 64-bit value to byte array (big-endian) */
static inline void be64_store(uint8_t *p, uint64_t v)
{
    p[0] = (uint8_t)(v >> 56);
    p[1] = (uint8_t)(v >> 48);
    p[2] = (uint8_t)(v >> 40);
    p[3] = (uint8_t)(v >> 32);
    p[4] = (uint8_t)(v >> 24);
    p[5] = (uint8_t)(v >> 16);
    p[6] = (uint8_t)(v >> 8);
    p[7] = (uint8_t)v;
}

/** @brief XOR 16 bytes: dst ^= src */
static inline void xor_block(uint8_t *dst, const uint8_t *src)
{
    for (int i = 0; i < 16; i++) {
        dst[i] ^= src[i];
    }
}

/** @brief Copy 16 bytes */
static inline void copy_block(uint8_t *dst, const uint8_t *src)
{
    for (int i = 0; i < 16; i++) {
        dst[i] = src[i];
    }
}

/* ============================================================================
 * AES GF(2^8) MULTIPLICATION (for MixColumns)
 * ============================================================================
 *
 * MixColumns multiplies the state column by a fixed polynomial in GF(2^8)
 * with the irreducible polynomial x^8 + x^4 + x^3 + x + 1 (0x11b).
 *
 * We need multiply by 2 (xtime) and multiply by 3 (xtime XOR original).
 */

/** @brief Multiply by 2 in GF(2^8) -- "xtime" */
static inline uint8_t xtime(uint8_t x)
{
    return (uint8_t)((x << 1) ^ (((x >> 7) & 1U) * 0x1bU));
}

/** @brief Multiply two values in GF(2^8) */
static inline uint8_t gf_mul(uint8_t a, uint8_t b)
{
    uint8_t result = 0;
    uint8_t hi;

    for (int i = 0; i < 8; i++) {
        if (b & 1U) {
            result ^= a;
        }
        hi = (uint8_t)(a & 0x80U);
        a = (uint8_t)(a << 1);
        if (hi) {
            a ^= 0x1bU;
        }
        b >>= 1;
    }
    return result;
}

/* ============================================================================
 * AES KEY EXPANSION (FIPS 197 §5.2)
 * ============================================================================
 *
 * Expands the cipher key into the key schedule (round keys).
 * AES-128: 10 rounds, 44 words (176 bytes)
 * AES-256: 14 rounds, 60 words (240 bytes)
 */

/**
 * @brief Expand AES key into round key schedule
 *
 * @param[out] round_keys  Output buffer for expanded keys
 *                         AES-128: 176 bytes (11 x 16)
 *                         AES-256: 240 bytes (15 x 16)
 * @param[in]  key         Cipher key (16 or 32 bytes)
 * @param[in]  key_len     Key length in bytes (16 or 32)
 * @return Number of rounds (10 for AES-128, 14 for AES-256)
 */
static int aes_key_expand(uint8_t *round_keys,
                           const uint8_t *key,
                           size_t key_len)
{
    uint32_t *w = (uint32_t *)round_keys;
    int nk;      /* Key length in 32-bit words */
    int nr;      /* Number of rounds */
    int nw;      /* Total words in expanded key */

    if (key_len == 16) {
        nk = 4;
        nr = 10;
    } else {
        /* key_len == 32 (validated by caller) */
        nk = 8;
        nr = 14;
    }
    nw = 4 * (nr + 1);

    /* Copy original key into first nk words */
    for (int i = 0; i < nk; i++) {
        w[i] = be32_load(key + 4 * i);
    }

    /* Expand remaining words */
    for (int i = nk; i < nw; i++) {
        uint32_t temp = w[i - 1];

        if ((i % nk) == 0) {
            /* RotWord: rotate left by 8 bits */
            temp = (temp << 8) | (temp >> 24);

            /* SubWord: apply S-box to each byte */
            temp = ((uint32_t)aes_sbox[(temp >> 24) & 0xFF] << 24) |
                   ((uint32_t)aes_sbox[(temp >> 16) & 0xFF] << 16) |
                   ((uint32_t)aes_sbox[(temp >> 8)  & 0xFF] << 8)  |
                   ((uint32_t)aes_sbox[(temp)       & 0xFF]);

            /* XOR with round constant */
            temp ^= (uint32_t)aes_rcon[i / nk] << 24;
        } else if (nk > 6 && (i % nk) == 4) {
            /* AES-256 extra SubWord at position 4 */
            temp = ((uint32_t)aes_sbox[(temp >> 24) & 0xFF] << 24) |
                   ((uint32_t)aes_sbox[(temp >> 16) & 0xFF] << 16) |
                   ((uint32_t)aes_sbox[(temp >> 8)  & 0xFF] << 8)  |
                   ((uint32_t)aes_sbox[(temp)       & 0xFF]);
        }

        w[i] = w[i - nk] ^ temp;
    }

    return nr;
}

/* ============================================================================
 * AES SINGLE-BLOCK ENCRYPT (FIPS 197 §5.1)
 * ============================================================================
 *
 * SubBytes + ShiftRows + MixColumns + AddRoundKey for each round.
 * The last round omits MixColumns.
 *
 * State is organized as a 4x4 column-major matrix of bytes:
 *   [ s0  s4  s8  s12 ]
 *   [ s1  s5  s9  s13 ]
 *   [ s2  s6  s10 s14 ]
 *   [ s3  s7  s11 s15 ]
 */

/**
 * @brief Encrypt a single 16-byte block using AES
 *
 * @param[out] out         16-byte ciphertext output
 * @param[in]  in          16-byte plaintext input
 * @param[in]  round_keys  Expanded key schedule
 * @param[in]  rounds      Number of rounds (10 or 14)
 */
static void aes_encrypt_block(uint8_t out[16],
                                const uint8_t in[16],
                                const uint8_t *round_keys,
                                int rounds)
{
    uint8_t state[16];
    const uint32_t *rk = (const uint32_t *)round_keys;

    /* Copy input to state */
    copy_block(state, in);

    /* Initial AddRoundKey (round 0) */
    for (int i = 0; i < 4; i++) {
        uint32_t k = rk[i];
        state[4*i + 0] ^= (uint8_t)(k >> 24);
        state[4*i + 1] ^= (uint8_t)(k >> 16);
        state[4*i + 2] ^= (uint8_t)(k >> 8);
        state[4*i + 3] ^= (uint8_t)(k);
    }

    /* Rounds 1 through (rounds - 1) */
    for (int round = 1; round < rounds; round++) {
        uint8_t tmp[16];

        /* SubBytes: apply S-box to every byte */
        for (int i = 0; i < 16; i++) {
            state[i] = aes_sbox[state[i]];
        }

        /* ShiftRows:
         *   Row 0: no shift
         *   Row 1: shift left by 1
         *   Row 2: shift left by 2
         *   Row 3: shift left by 3
         *
         * State is column-major: state[row + 4*col]
         */
        copy_block(tmp, state);
        /* Row 1 */
        state[1]  = tmp[5];
        state[5]  = tmp[9];
        state[9]  = tmp[13];
        state[13] = tmp[1];
        /* Row 2 */
        state[2]  = tmp[10];
        state[6]  = tmp[14];
        state[10] = tmp[2];
        state[14] = tmp[6];
        /* Row 3 */
        state[3]  = tmp[15];
        state[7]  = tmp[3];
        state[11] = tmp[7];
        state[15] = tmp[11];

        /* MixColumns: each column is multiplied by the fixed polynomial
         *   [2 3 1 1]
         *   [1 2 3 1]
         *   [1 1 2 3]
         *   [3 1 1 2]
         */
        for (int c = 0; c < 4; c++) {
            uint8_t s0 = state[4*c + 0];
            uint8_t s1 = state[4*c + 1];
            uint8_t s2 = state[4*c + 2];
            uint8_t s3 = state[4*c + 3];

            state[4*c + 0] = xtime(s0) ^ xtime(s1) ^ s1 ^ s2 ^ s3;
            state[4*c + 1] = s0 ^ xtime(s1) ^ xtime(s2) ^ s2 ^ s3;
            state[4*c + 2] = s0 ^ s1 ^ xtime(s2) ^ xtime(s3) ^ s3;
            state[4*c + 3] = xtime(s0) ^ s0 ^ s1 ^ s2 ^ xtime(s3);
        }

        /* AddRoundKey */
        const uint32_t *rk_round = rk + round * 4;
        for (int i = 0; i < 4; i++) {
            uint32_t k = rk_round[i];
            state[4*i + 0] ^= (uint8_t)(k >> 24);
            state[4*i + 1] ^= (uint8_t)(k >> 16);
            state[4*i + 2] ^= (uint8_t)(k >> 8);
            state[4*i + 3] ^= (uint8_t)(k);
        }
    }

    /* Final round (no MixColumns) */
    {
        uint8_t tmp[16];

        /* SubBytes */
        for (int i = 0; i < 16; i++) {
            state[i] = aes_sbox[state[i]];
        }

        /* ShiftRows */
        copy_block(tmp, state);
        state[1]  = tmp[5];
        state[5]  = tmp[9];
        state[9]  = tmp[13];
        state[13] = tmp[1];
        state[2]  = tmp[10];
        state[6]  = tmp[14];
        state[10] = tmp[2];
        state[14] = tmp[6];
        state[3]  = tmp[15];
        state[7]  = tmp[3];
        state[11] = tmp[7];
        state[15] = tmp[11];

        /* AddRoundKey (final) */
        const uint32_t *rk_final = rk + rounds * 4;
        for (int i = 0; i < 4; i++) {
            uint32_t k = rk_final[i];
            state[4*i + 0] ^= (uint8_t)(k >> 24);
            state[4*i + 1] ^= (uint8_t)(k >> 16);
            state[4*i + 2] ^= (uint8_t)(k >> 8);
            state[4*i + 3] ^= (uint8_t)(k);
        }
    }

    /* Copy state to output */
    copy_block(out, state);
}

/* ============================================================================
 * GHASH -- GF(2^128) POLYNOMIAL MULTIPLICATION (NIST SP 800-38D §6.3-6.4)
 * ============================================================================
 *
 * GHASH is the universal hash function used in GCM. It operates in GF(2^128)
 * with the reducing polynomial:
 *
 *   P(x) = x^128 + x^7 + x^2 + x + 1
 *
 * The representation uses the "bit-reflected" convention where the most
 * significant bit of the first byte corresponds to x^0 (the constant term).
 * This means the reducing constant is R = 0xE1000000_00000000 (in the high
 * 64-bit word).
 *
 * The bit-by-bit algorithm processes one bit of the multiplier at a time,
 * performing conditional XOR and polynomial reduction.
 */

/** @brief GF(2^128) reducing polynomial constant: x^7 + x^2 + x + 1 in MSB-first */
#define GHASH_R_HI  0xE100000000000000ULL
#define GHASH_R_LO  0x0000000000000000ULL

/**
 * @brief Multiply two 128-bit values in GF(2^128)
 *
 * Uses the bit-by-bit schoolbook algorithm. For each bit of Y:
 *   - If bit is set, XOR running product Z with V
 *   - Right-shift V, conditionally XOR with reducing polynomial R
 *
 * @param[out] out  16-byte result (Z = X * Y in GF(2^128))
 * @param[in]  X    16-byte multiplicand (hash subkey H)
 * @param[in]  Y    16-byte multiplier
 */
static void ghash_multiply(uint8_t out[16],
                            const uint8_t X[16],
                            const uint8_t Y[16])
{
    uint64_t Vh, Vl;   /* V = X, shifted right each iteration */
    uint64_t Zh, Zl;   /* Z = accumulated product */

    /* Load X into V (big-endian: first 8 bytes = high word) */
    Vh = be64_load(X);
    Vl = be64_load(X + 8);

    /* Z starts at 0 */
    Zh = 0;
    Zl = 0;

    /* Process each bit of Y, MSB first (128 bits total)
     *
     * CONSTANT-TIME: Uses arithmetic masking instead of branches
     * to prevent timing side-channels on secret Y values.
     * mask = 0 - bit → all-ones if bit==1, all-zeros if bit==0.
     */
    for (int i = 0; i < 128; i++) {
        /* Extract bit i of Y (MSB-first within each byte) — constant-time */
        int byte_idx = i / 8;
        int bit_idx  = 7 - (i % 8);
        uint64_t bit_val = (uint64_t)((Y[byte_idx] >> bit_idx) & 1U);

        /* Arithmetic mask: all-ones if bit==1, all-zeros if bit==0 */
        uint64_t mask = (uint64_t)0 - bit_val;
        Zh ^= (Vh & mask);
        Zl ^= (Vl & mask);

        /* Right-shift V by 1, with constant-time polynomial reduction */
        uint64_t carry = Vl & 1ULL;
        Vl = (Vl >> 1) | (Vh << 63);
        Vh = Vh >> 1;

        /* Reduce: if carry==1, XOR with R; mask avoids branch */
        uint64_t reduce_mask = (uint64_t)0 - carry;
        Vh ^= (GHASH_R_HI & reduce_mask);
        /* Vl ^= (GHASH_R_LO & reduce_mask); -- R_LO is 0, so no-op */
    }

    /* Store result */
    be64_store(out, Zh);
    be64_store(out + 8, Zl);
}

/**
 * @brief Incremental GHASH over data blocks
 *
 * Computes: tag = GHASH(H, data)
 *
 * For each 16-byte block Xi of the input:
 *   tag = (tag XOR Xi) * H    (in GF(2^128))
 *
 * The last block is zero-padded if len is not a multiple of 16.
 * The tag accumulator should be pre-initialized by the caller (typically
 * to all zeros for the first call).
 *
 * @param[in,out] tag   16-byte GHASH accumulator (modified in place)
 * @param[in]     H     16-byte hash subkey
 * @param[in]     data  Input data
 * @param[in]     len   Input data length in bytes
 */
static void ghash_update(uint8_t tag[16],
                          const uint8_t H[16],
                          const uint8_t *data,
                          size_t len)
{
    uint8_t block[16];

    /* Process full 16-byte blocks */
    while (len >= 16) {
        xor_block(tag, data);
        ghash_multiply(block, tag, H);
        copy_block(tag, block);
        data += 16;
        len -= 16;
    }

    /* Process partial final block (zero-padded) */
    if (len > 0) {
        memset(block, 0, 16);
        memcpy(block, data, len);
        xor_block(tag, block);
        ghash_multiply(block, tag, H);
        copy_block(tag, block);
    }
}

/* ============================================================================
 * AES-CTR INCREMENT (NIST SP 800-38D §6.2)
 * ============================================================================
 *
 * GCM uses a 32-bit big-endian counter in the last 4 bytes of the 16-byte
 * counter block. The first 12 bytes (nonce/IV) remain constant.
 */

/** @brief Increment the 32-bit big-endian counter in bytes [12..15] */
static inline void ctr_inc32(uint8_t counter[16])
{
    uint32_t c = be32_load(counter + 12);
    c++;
    be32_store(counter + 12, c);
}

/* ============================================================================
 * AES-GCM PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize AES-GCM context with key
 *
 * Expands key schedule, computes hash subkey H = AES(K, 0^128),
 * and detects AES-NI availability for future acceleration.
 *
 * @param[out] ctx      AES-GCM context
 * @param[in]  key      Encryption key (16 or 32 bytes)
 * @param[in]  key_len  Key length in bytes
 * @return 0 on success, -1 on invalid key length
 */
int vos3_aes_gcm_init(vos3_aes_gcm_ctx_t *ctx,
                       const uint8_t *key, size_t key_len)
{
    uint8_t zero_block[16];

    if (!ctx || !key) {
        return -1;
    }

    if (key_len != 16 && key_len != 32) {
        return -1;
    }

    /* Zero the context */
    memset(ctx, 0, sizeof(*ctx));

    /* Store key metadata */
    ctx->key_len = (uint8_t)key_len;

    /* Expand key schedule */
    ctx->rounds = (uint8_t)aes_key_expand(ctx->round_keys, key, key_len);

    /* Compute hash subkey: H = AES(K, 0^128) */
    memset(zero_block, 0, 16);
    aes_encrypt_block(ctx->H, zero_block, ctx->round_keys, (int)ctx->rounds);

    /* Detect AES-NI support (stub: always use GPR fallback for now) */
    ctx->use_aesni = 0;
#if 0
    /* Future: enable AES-NI path when implemented */
    ctx->use_aesni = (uint8_t)vos3_cpu_has_aesni();
#endif

    return 0;
}

/**
 * @brief AES-GCM encrypt and authenticate
 *
 * Implements NIST SP 800-38D Algorithm for Authenticated Encryption:
 *
 * 1. Compute J0 = IV || 0x00000001  (for 12-byte IV)
 * 2. GHASH the AAD
 * 3. CTR encrypt: counter starts at inc32(J0)
 * 4. GHASH the ciphertext
 * 5. Append length block: [AAD_bits_64 || CT_bits_64]
 * 6. Tag = GHASH_result XOR AES(K, J0)
 *
 * @param[in]  ctx         Initialized AES-GCM context
 * @param[in]  iv          12-byte initialization vector (nonce)
 * @param[in]  aad         Additional authenticated data (may be NULL if aad_len == 0)
 * @param[in]  aad_len     AAD length in bytes
 * @param[in]  plaintext   Data to encrypt (may be NULL if pt_len == 0)
 * @param[in]  pt_len      Plaintext length in bytes
 * @param[out] ciphertext  Encrypted output buffer (pt_len bytes)
 * @param[out] tag         16-byte authentication tag output
 * @return 0 on success, -1 on invalid parameters
 */
int vos3_aes_gcm_encrypt(const vos3_aes_gcm_ctx_t *ctx,
                          const uint8_t iv[12],
                          const uint8_t *aad, size_t aad_len,
                          const uint8_t *plaintext, size_t pt_len,
                          uint8_t *ciphertext,
                          uint8_t tag[16])
{
    uint8_t J0[16];         /* Initial counter block */
    uint8_t counter[16];    /* Working counter for CTR mode */
    uint8_t keystream[16];  /* AES output for CTR XOR */
    uint8_t ghash_acc[16];  /* GHASH accumulator */
    uint8_t len_block[16];  /* Final length block for GHASH */

    if (!ctx || !iv || !tag) {
        return -1;
    }
    if (pt_len > 0 && (!plaintext || !ciphertext)) {
        return -1;
    }
    if (aad_len > 0 && !aad) {
        return -1;
    }
    /* GCM length-block overflow guard: aad_len and pt_len are multiplied by 8
     * to compute bit-lengths stored as 64-bit BE in the GHASH length block.
     * Reject inputs where byte_len * 8 would overflow uint64_t. */
    if (aad_len > (UINT64_MAX / 8) || pt_len > (UINT64_MAX / 8)) {
        return -1;
    }

    /* Step 1: Construct J0 from 12-byte IV
     * J0 = IV || 0x00000001 */
    memcpy(J0, iv, 12);
    J0[12] = 0x00;
    J0[13] = 0x00;
    J0[14] = 0x00;
    J0[15] = 0x01;

    /* Step 2: Initialize GHASH accumulator to zero */
    memset(ghash_acc, 0, 16);

    /* Step 3: GHASH the AAD */
    if (aad_len > 0) {
        ghash_update(ghash_acc, ctx->H, aad, aad_len);
    }

    /* Step 4: CTR-mode encryption
     * Counter starts at inc32(J0) = IV || 0x00000002 */
    copy_block(counter, J0);
    ctr_inc32(counter);

    size_t remaining = pt_len;
    size_t offset = 0;

    while (remaining > 0) {
        /* Encrypt counter block to produce keystream */
        aes_encrypt_block(keystream, counter, ctx->round_keys, (int)ctx->rounds);

        /* XOR plaintext with keystream to produce ciphertext */
        size_t chunk = (remaining >= 16) ? 16 : remaining;
        for (size_t i = 0; i < chunk; i++) {
            ciphertext[offset + i] = plaintext[offset + i] ^ keystream[i];
        }

        offset += chunk;
        remaining -= chunk;

        /* Increment counter for next block */
        ctr_inc32(counter);
    }

    /* Step 5: GHASH the ciphertext */
    if (pt_len > 0) {
        ghash_update(ghash_acc, ctx->H, ciphertext, pt_len);
    }

    /* Step 6: Append length block
     * len_block = [AAD_len_bits (64-bit BE) || CT_len_bits (64-bit BE)] */
    be64_store(len_block, (uint64_t)aad_len * 8);
    be64_store(len_block + 8, (uint64_t)pt_len * 8);
    xor_block(ghash_acc, len_block);

    {
        uint8_t tmp[16];
        ghash_multiply(tmp, ghash_acc, ctx->H);
        copy_block(ghash_acc, tmp);
    }

    /* Step 7: Tag = GHASH_result XOR AES(K, J0) */
    aes_encrypt_block(keystream, J0, ctx->round_keys, (int)ctx->rounds);
    xor_block(ghash_acc, keystream);
    copy_block(tag, ghash_acc);

    return 0;
}

/**
 * @brief AES-GCM decrypt and verify
 *
 * Implements NIST SP 800-38D Algorithm for Authenticated Decryption:
 *
 * 1. Recompute authentication tag over (AAD, ciphertext)
 * 2. Constant-time compare with provided tag
 * 3. On match: CTR decrypt ciphertext to produce plaintext
 * 4. On mismatch: zero plaintext buffer, return -1
 *
 * @param[in]  ctx         Initialized AES-GCM context
 * @param[in]  iv          12-byte initialization vector (nonce)
 * @param[in]  aad         Additional authenticated data (may be NULL if aad_len == 0)
 * @param[in]  aad_len     AAD length in bytes
 * @param[in]  ciphertext  Data to decrypt (may be NULL if ct_len == 0)
 * @param[in]  ct_len      Ciphertext length in bytes
 * @param[in]  tag         16-byte authentication tag to verify
 * @param[out] plaintext   Decrypted output buffer (ct_len bytes)
 * @return 0 on success (tag verified), -1 on authentication failure
 *
 * @note On authentication failure, the plaintext buffer is zeroed.
 *       No partial decrypted output is ever returned.
 */
int vos3_aes_gcm_decrypt(const vos3_aes_gcm_ctx_t *ctx,
                          const uint8_t iv[12],
                          const uint8_t *aad, size_t aad_len,
                          const uint8_t *ciphertext, size_t ct_len,
                          const uint8_t tag[16],
                          uint8_t *plaintext)
{
    uint8_t J0[16];
    uint8_t counter[16];
    uint8_t keystream[16];
    uint8_t ghash_acc[16];
    uint8_t len_block[16];
    uint8_t computed_tag[16];

    if (!ctx || !iv || !tag) {
        return -1;
    }
    if (ct_len > 0 && (!ciphertext || !plaintext)) {
        return -1;
    }
    if (aad_len > 0 && !aad) {
        return -1;
    }
    /* GCM length-block overflow guard (same as encrypt path) */
    if (aad_len > (UINT64_MAX / 8) || ct_len > (UINT64_MAX / 8)) {
        return -1;
    }

    /* Step 1: Construct J0 from 12-byte IV */
    memcpy(J0, iv, 12);
    J0[12] = 0x00;
    J0[13] = 0x00;
    J0[14] = 0x00;
    J0[15] = 0x01;

    /* Step 2: Compute authentication tag over AAD and ciphertext */
    memset(ghash_acc, 0, 16);

    /* GHASH AAD */
    if (aad_len > 0) {
        ghash_update(ghash_acc, ctx->H, aad, aad_len);
    }

    /* GHASH ciphertext */
    if (ct_len > 0) {
        ghash_update(ghash_acc, ctx->H, ciphertext, ct_len);
    }

    /* Append length block */
    be64_store(len_block, (uint64_t)aad_len * 8);
    be64_store(len_block + 8, (uint64_t)ct_len * 8);
    xor_block(ghash_acc, len_block);

    {
        uint8_t tmp[16];
        ghash_multiply(tmp, ghash_acc, ctx->H);
        copy_block(ghash_acc, tmp);
    }

    /* Tag = GHASH_result XOR AES(K, J0) */
    aes_encrypt_block(keystream, J0, ctx->round_keys, (int)ctx->rounds);
    xor_block(ghash_acc, keystream);
    copy_block(computed_tag, ghash_acc);

    /* Step 3: Constant-time tag comparison
     *
     * SECURITY: Must not branch on tag contents. XOR each byte and
     * accumulate into a single difference variable. Any non-zero bit
     * means authentication failure.
     */
    {
        volatile uint8_t diff = 0;
        for (int i = 0; i < 16; i++) {
            diff |= computed_tag[i] ^ tag[i];
        }

        if (diff != 0) {
            /* Authentication failure -- zero plaintext buffer */
            if (ct_len > 0 && plaintext) {
                memset(plaintext, 0, ct_len);
            }
            return -1;
        }
    }

    /* Step 4: CTR-mode decryption (identical to encryption)
     * Counter starts at inc32(J0) */
    copy_block(counter, J0);
    ctr_inc32(counter);

    size_t remaining = ct_len;
    size_t offset = 0;

    while (remaining > 0) {
        aes_encrypt_block(keystream, counter, ctx->round_keys, (int)ctx->rounds);

        size_t chunk = (remaining >= 16) ? 16 : remaining;
        for (size_t i = 0; i < chunk; i++) {
            plaintext[offset + i] = ciphertext[offset + i] ^ keystream[i];
        }

        offset += chunk;
        remaining -= chunk;
        ctr_inc32(counter);
    }

    return 0;
}

/**
 * @brief Destroy AES-GCM context -- securely wipe all key material
 *
 * Zeros the round key schedule and hash subkey H, then flushes
 * from CPU caches via vos3_cache_wipe() to prevent key residue
 * in L1/L2/L3.
 *
 * @param[in] ctx  Context to destroy (safe to call with NULL)
 */
void vos3_aes_gcm_destroy(vos3_aes_gcm_ctx_t *ctx)
{
    if (!ctx) {
        return;
    }

    /* Wipe round keys -- the most sensitive material */
    vos3_cache_wipe(ctx->round_keys, sizeof(ctx->round_keys));

    /* Wipe hash subkey H */
    vos3_cache_wipe(ctx->H, sizeof(ctx->H));

    /* Zero remaining metadata */
    ctx->key_len = 0;
    ctx->rounds = 0;
    ctx->use_aesni = 0;
    ctx->_padding = 0;
}
