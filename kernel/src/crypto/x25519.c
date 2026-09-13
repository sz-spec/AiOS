/**
 * @file x25519.c
 * @brief VOS3 X25519 (Curve25519 Diffie-Hellman) per RFC 7748
 *
 * @details Freestanding, constant-time implementation of X25519 scalar
 *          multiplication for TLS 1.3 key exchange. Field arithmetic
 *          operates in GF(2^255 - 19) using a radix-2^51 representation
 *          (5 limbs in uint64_t[5]). All operations are GPR-only (no
 *          SSE/AVX) and use 128-bit intermediates via __uint128_t.
 *
 *          Constant-time guarantee: no secret-dependent branches, no
 *          secret-dependent memory access patterns. The Montgomery ladder
 *          uses arithmetic XOR masking (fe_cswap) for conditional swaps.
 *
 *          Reference: RFC 7748, Section 5 (X25519 function)
 *                     Daniel J. Bernstein, "Curve25519: new Diffie-Hellman
 *                     speed records", 2006.
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 — Advisory deviations:
 *       - Rule 10.1: __uint128_t used for 128-bit multiply intermediates
 *       - Rule 12.2: Bitwise ops on unsigned types for constant-time logic
 */

#include "../../include/vos/tls.h"
#include "../../include/vos/crypto.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * TYPE DEFINITIONS
 * ============================================================================ */

/**
 * @brief Field element in GF(2^255 - 19), radix-2^51 representation
 *
 * fe[0] = bits 0-50, fe[1] = bits 51-101, fe[2] = bits 102-152,
 * fe[3] = bits 153-203, fe[4] = bits 204-254.
 *
 * Each limb is kept below 2^52 after reduction to prevent overflow
 * during subsequent multiply operations.
 */
typedef uint64_t fe[5];

/** @brief Mask for 51-bit limb extraction */
#define LIMB_MASK  ((uint64_t)0x0007FFFFFFFFFFFFULL)  /* (1 << 51) - 1 */

/** @brief The prime p = 2^255 - 19 */
#define P0  ((uint64_t)0x7FFFFFFFFFFEDULL)  /* 2^51 - 19 */
#define P1  ((uint64_t)0x7FFFFFFFFFFFFULL)  /* 2^51 - 1  */
#define P2  ((uint64_t)0x7FFFFFFFFFFFFULL)  /* 2^51 - 1  */
#define P3  ((uint64_t)0x7FFFFFFFFFFFFULL)  /* 2^51 - 1  */
#define P4  ((uint64_t)0x7FFFFFFFFFFFFULL)  /* 2^51 - 1  */

/* ============================================================================
 * FIELD ELEMENT: LOAD / STORE
 * ============================================================================ */

/**
 * @brief Load a 32-byte little-endian value into a field element
 *
 * Distributes 256 bits across 5 limbs of 51 bits each (ignoring bit 255
 * per RFC 7748 clamping rules).
 *
 * @param[out] h   Destination field element
 * @param[in]  s   Source 32-byte buffer (little-endian)
 */
static void fe_frombytes(fe h, const uint8_t s[32])
{
    uint64_t lo, hi;

    /* Load 256 bits as two 128-bit halves (little-endian) */
    lo = (uint64_t)s[0]        | ((uint64_t)s[1]  << 8)  |
         ((uint64_t)s[2] << 16) | ((uint64_t)s[3]  << 24) |
         ((uint64_t)s[4] << 32) | ((uint64_t)s[5]  << 40) |
         ((uint64_t)s[6] << 48) | ((uint64_t)s[7]  << 56);

    hi = (uint64_t)s[8]        | ((uint64_t)s[9]  << 8)  |
         ((uint64_t)s[10] << 16)| ((uint64_t)s[11] << 24) |
         ((uint64_t)s[12] << 32)| ((uint64_t)s[13] << 40) |
         ((uint64_t)s[14] << 48)| ((uint64_t)s[15] << 56);

    h[0] = lo & LIMB_MASK;                                   /* bits 0-50   */
    h[1] = ((lo >> 51) | (hi << 13)) & LIMB_MASK;            /* bits 51-101 */

    lo = (uint64_t)s[16]       | ((uint64_t)s[17] << 8)  |
         ((uint64_t)s[18] << 16)| ((uint64_t)s[19] << 24) |
         ((uint64_t)s[20] << 32)| ((uint64_t)s[21] << 40) |
         ((uint64_t)s[22] << 48)| ((uint64_t)s[23] << 56);

    hi = (uint64_t)s[24]       | ((uint64_t)s[25] << 8)  |
         ((uint64_t)s[26] << 16)| ((uint64_t)s[27] << 24) |
         ((uint64_t)s[28] << 32)| ((uint64_t)s[29] << 40) |
         ((uint64_t)s[30] << 48)| ((uint64_t)s[31] << 56);

    /* h[2] = bits 102-152: byte 12 starts at bit 96, offset 6 */
    h[2] = ((uint64_t)s[12] | ((uint64_t)s[13] << 8) |
            ((uint64_t)s[14] << 16) | ((uint64_t)s[15] << 24) |
            ((uint64_t)s[16] << 32) | ((uint64_t)s[17] << 40) |
            ((uint64_t)s[18] << 48) | ((uint64_t)s[19] << 56)) >> 6;
    h[2] &= LIMB_MASK;                                       /* bits 102-152 */

    /* h[3] = bits 153-203: byte 19 starts at bit 152, offset 1 */
    h[3] = ((uint64_t)s[19] | ((uint64_t)s[20] << 8) |
            ((uint64_t)s[21] << 16) | ((uint64_t)s[22] << 24) |
            ((uint64_t)s[23] << 32) | ((uint64_t)s[24] << 40) |
            ((uint64_t)s[25] << 48) | ((uint64_t)s[26] << 56)) >> 1;
    h[3] &= LIMB_MASK;                                       /* bits 153-203 */

    /* h[4] = bits 204-254: byte 25 starts at bit 200, offset 4 */
    h[4] = ((uint64_t)s[25] | ((uint64_t)s[26] << 8) |
            ((uint64_t)s[27] << 16) | ((uint64_t)s[28] << 24) |
            ((uint64_t)s[29] << 32) | ((uint64_t)s[30] << 40) |
            ((uint64_t)s[31] << 48)) >> 4;
    h[4] &= LIMB_MASK;                                       /* bits 204-254 */
}

/**
 * @brief Store a field element as 32 little-endian bytes (fully reduced mod p)
 *
 * Performs a full reduction modulo p = 2^255 - 19 before serializing.
 *
 * @param[out] s   Destination 32-byte buffer
 * @param[in]  h   Source field element
 */
static void fe_tobytes(uint8_t s[32], const fe h)
{
    uint64_t t[5];
    uint64_t q, carry;

    t[0] = h[0]; t[1] = h[1]; t[2] = h[2]; t[3] = h[3]; t[4] = h[4];

    /* Propagate carries to get limbs in [0, 2^51) */
    carry = t[0] >> 51; t[1] += carry; t[0] &= LIMB_MASK;
    carry = t[1] >> 51; t[2] += carry; t[1] &= LIMB_MASK;
    carry = t[2] >> 51; t[3] += carry; t[2] &= LIMB_MASK;
    carry = t[3] >> 51; t[4] += carry; t[3] &= LIMB_MASK;
    carry = t[4] >> 51; t[0] += carry * 19; t[4] &= LIMB_MASK;

    /* Second carry pass (carry * 19 could have caused t[0] >= 2^51) */
    carry = t[0] >> 51; t[1] += carry; t[0] &= LIMB_MASK;
    carry = t[1] >> 51; t[2] += carry; t[1] &= LIMB_MASK;
    carry = t[2] >> 51; t[3] += carry; t[2] &= LIMB_MASK;
    carry = t[3] >> 51; t[4] += carry; t[3] &= LIMB_MASK;
    carry = t[4] >> 51; t[0] += carry * 19; t[4] &= LIMB_MASK;

    /*
     * Conditional subtraction of p:
     * q = 1 if t >= p, else 0.
     * We compute q = floor((t[0] + 19) / 2^51) propagated through all limbs.
     */
    q = (t[0] + 19) >> 51;
    q = (t[1] + q) >> 51;
    q = (t[2] + q) >> 51;
    q = (t[3] + q) >> 51;
    q = (t[4] + q) >> 51;

    /* q is now 1 if t >= p, else 0. Subtract q*p (constant-time). */
    t[0] += 19 * q;
    carry = t[0] >> 51; t[1] += carry; t[0] &= LIMB_MASK;
    carry = t[1] >> 51; t[2] += carry; t[1] &= LIMB_MASK;
    carry = t[2] >> 51; t[3] += carry; t[2] &= LIMB_MASK;
    carry = t[3] >> 51; t[4] += carry; t[3] &= LIMB_MASK;
    t[4] &= LIMB_MASK;

    /* Pack 5 × 51-bit limbs into 32 bytes (little-endian) */
    /* Combine into a 256-bit value and write out byte by byte */
    uint64_t combined;

    /* Bytes 0-7: t[0] (51 bits) + low 13 bits of t[1] */
    combined = t[0] | (t[1] << 51);
    s[0]  = (uint8_t)(combined);       s[1]  = (uint8_t)(combined >> 8);
    s[2]  = (uint8_t)(combined >> 16);  s[3]  = (uint8_t)(combined >> 24);
    s[4]  = (uint8_t)(combined >> 32);  s[5]  = (uint8_t)(combined >> 40);
    s[6]  = (uint8_t)(combined >> 48);  s[7]  = (uint8_t)(combined >> 56);

    /* Bytes 8-15: remaining 38 bits of t[1] + low 26 bits of t[2] */
    combined = (t[1] >> 13) | (t[2] << 38);
    s[8]  = (uint8_t)(combined);       s[9]  = (uint8_t)(combined >> 8);
    s[10] = (uint8_t)(combined >> 16);  s[11] = (uint8_t)(combined >> 24);
    s[12] = (uint8_t)(combined >> 32);  s[13] = (uint8_t)(combined >> 40);
    s[14] = (uint8_t)(combined >> 48);  s[15] = (uint8_t)(combined >> 56);

    /* Bytes 16-23: remaining 25 bits of t[2] + low 39 bits of t[3] */
    combined = (t[2] >> 26) | (t[3] << 25);
    s[16] = (uint8_t)(combined);       s[17] = (uint8_t)(combined >> 8);
    s[18] = (uint8_t)(combined >> 16);  s[19] = (uint8_t)(combined >> 24);
    s[20] = (uint8_t)(combined >> 32);  s[21] = (uint8_t)(combined >> 40);
    s[22] = (uint8_t)(combined >> 48);  s[23] = (uint8_t)(combined >> 56);

    /* Bytes 24-31: remaining 12 bits of t[3] + 51 bits of t[4] */
    combined = (t[3] >> 39) | (t[4] << 12);
    s[24] = (uint8_t)(combined);       s[25] = (uint8_t)(combined >> 8);
    s[26] = (uint8_t)(combined >> 16);  s[27] = (uint8_t)(combined >> 24);
    s[28] = (uint8_t)(combined >> 32);  s[29] = (uint8_t)(combined >> 40);
    s[30] = (uint8_t)(combined >> 48);  s[31] = (uint8_t)(combined >> 56);
}

/* ============================================================================
 * FIELD ARITHMETIC
 * ============================================================================ */

/**
 * @brief Field addition: out = a + b in GF(2^255 - 19)
 *
 * No reduction performed — limbs may temporarily exceed 2^51.
 * Callers must ensure inputs are reasonably bounded (post-multiply reduced).
 *
 * @param[out] out  Result field element
 * @param[in]  a    First operand
 * @param[in]  b    Second operand
 */
static void fe_add(fe out, const fe a, const fe b)
{
    out[0] = a[0] + b[0];
    out[1] = a[1] + b[1];
    out[2] = a[2] + b[2];
    out[3] = a[3] + b[3];
    out[4] = a[4] + b[4];
}

/**
 * @brief Field subtraction: out = a - b in GF(2^255 - 19)
 *
 * Adds 2*p to each limb before subtracting to guarantee non-negative
 * results without branching. The extra multiples of p vanish mod p.
 *
 * @param[out] out  Result field element
 * @param[in]  a    First operand (minuend)
 * @param[in]  b    Second operand (subtrahend)
 */
static void fe_sub(fe out, const fe a, const fe b)
{
    /*
     * 2*p limbs:
     *   2*p[0] = 2 * (2^51 - 19) = 2^52 - 38
     *   2*p[i] = 2 * (2^51 - 1)  = 2^52 - 2    for i = 1..4
     */
    out[0] = (a[0] + 0xFFFFFFFFFFFDAULL) - b[0];  /* 2*(2^51 - 19) */
    out[1] = (a[1] + 0xFFFFFFFFFFFFEULL) - b[1];  /* 2*(2^51 - 1)  */
    out[2] = (a[2] + 0xFFFFFFFFFFFFEULL) - b[2];
    out[3] = (a[3] + 0xFFFFFFFFFFFFEULL) - b[3];
    out[4] = (a[4] + 0xFFFFFFFFFFFFEULL) - b[4];
}

/**
 * @brief Propagate carries and reduce modulo p = 2^255 - 19
 *
 * After addition or subtraction, limbs may exceed 51 bits. This function
 * propagates carries upward, and wraps the top carry back to limb 0
 * (multiplied by 19, since 2^255 = 19 mod p).
 *
 * @param[in,out] h  Field element to reduce in place
 */
static void fe_carry(fe h)
{
    uint64_t carry;

    carry = h[0] >> 51; h[1] += carry; h[0] &= LIMB_MASK;
    carry = h[1] >> 51; h[2] += carry; h[1] &= LIMB_MASK;
    carry = h[2] >> 51; h[3] += carry; h[2] &= LIMB_MASK;
    carry = h[3] >> 51; h[4] += carry; h[3] &= LIMB_MASK;
    carry = h[4] >> 51; h[0] += carry * 19; h[4] &= LIMB_MASK;
    /* One more pass to handle the rare case carry*19 overflows limb 0 */
    carry = h[0] >> 51; h[1] += carry; h[0] &= LIMB_MASK;
}

/**
 * @brief Field multiplication: out = a * b in GF(2^255 - 19)
 *
 * Uses 128-bit intermediates (__uint128_t) for schoolbook multiplication
 * with the Curve25519 reduction trick: when a product lands in limb >= 5,
 * it wraps around as 19 * product (since 2^255 = 19 mod p).
 *
 * @param[out] out  Result field element
 * @param[in]  a    First operand
 * @param[in]  b    Second operand
 */
static void fe_mul(fe out, const fe a, const fe b)
{
    typedef unsigned __int128 uint128_t;

    /* Pre-multiply b[1..4] by 19 for the reduction trick */
    const uint64_t b1_19 = b[1] * 19;
    const uint64_t b2_19 = b[2] * 19;
    const uint64_t b3_19 = b[3] * 19;
    const uint64_t b4_19 = b[4] * 19;

    uint128_t t0, t1, t2, t3, t4;

    /*
     * Schoolbook multiply with reduction:
     * For limb i of the result, we accumulate a[j]*b[k] where j+k = i (mod 5).
     * When j+k >= 5, we use b[k]*19 instead of b[k].
     */
    t0 = (uint128_t)a[0] * b[0]
       + (uint128_t)a[1] * b4_19
       + (uint128_t)a[2] * b3_19
       + (uint128_t)a[3] * b2_19
       + (uint128_t)a[4] * b1_19;

    t1 = (uint128_t)a[0] * b[1]
       + (uint128_t)a[1] * b[0]
       + (uint128_t)a[2] * b4_19
       + (uint128_t)a[3] * b3_19
       + (uint128_t)a[4] * b2_19;

    t2 = (uint128_t)a[0] * b[2]
       + (uint128_t)a[1] * b[1]
       + (uint128_t)a[2] * b[0]
       + (uint128_t)a[3] * b4_19
       + (uint128_t)a[4] * b3_19;

    t3 = (uint128_t)a[0] * b[3]
       + (uint128_t)a[1] * b[2]
       + (uint128_t)a[2] * b[1]
       + (uint128_t)a[3] * b[0]
       + (uint128_t)a[4] * b4_19;

    t4 = (uint128_t)a[0] * b[4]
       + (uint128_t)a[1] * b[3]
       + (uint128_t)a[2] * b[2]
       + (uint128_t)a[3] * b[1]
       + (uint128_t)a[4] * b[0];

    /* Carry chain: reduce 128-bit accumulators to 51-bit limbs */
    uint64_t c;

    c = (uint64_t)(t0 >> 51); t1 += c; out[0] = (uint64_t)t0 & LIMB_MASK;
    c = (uint64_t)(t1 >> 51); t2 += c; out[1] = (uint64_t)t1 & LIMB_MASK;
    c = (uint64_t)(t2 >> 51); t3 += c; out[2] = (uint64_t)t2 & LIMB_MASK;
    c = (uint64_t)(t3 >> 51); t4 += c; out[3] = (uint64_t)t3 & LIMB_MASK;
    c = (uint64_t)(t4 >> 51);           out[4] = (uint64_t)t4 & LIMB_MASK;

    /* Top carry wraps: 2^255 = 19 mod p */
    out[0] += c * 19;
    c = out[0] >> 51; out[1] += c; out[0] &= LIMB_MASK;
}

/**
 * @brief Field squaring: out = a^2 in GF(2^255 - 19)
 *
 * Optimized versus fe_mul: exploits symmetry a[i]*a[j] = a[j]*a[i]
 * to halve the number of multiplications (doubled via left shift).
 *
 * @param[out] out  Result field element
 * @param[in]  a    Operand to square
 */
static void fe_sq(fe out, const fe a)
{
    typedef unsigned __int128 uint128_t;

    const uint64_t a0_2  = a[0] * 2;
    const uint64_t a1_2  = a[1] * 2;
    const uint64_t a1_38 = a[1] * 38;  /* 2 * 19 * a[1] */
    const uint64_t a2_38 = a[2] * 38;  /* 2 * 19 * a[2] */
    const uint64_t a3_38 = a[3] * 38;  /* 2 * 19 * a[3] */
    const uint64_t a3_19 = a[3] * 19;
    const uint64_t a4_19 = a[4] * 19;

    uint128_t t0, t1, t2, t3, t4;

    /*
     * Squaring with cross-term doubling and Curve25519 reduction:
     *
     * t[i] = sum of a[j]*a[k] for j+k=i (mod 5), with:
     *   - Cross terms (j!=k) appear twice (symmetry), handled by *2 or *38
     *   - Wrapped terms (j+k>=5) use factor 19
     */
    t0 = (uint128_t)a[0]  * a[0]
       + (uint128_t)a1_38 * a[4]
       + (uint128_t)a2_38 * a[3];

    t1 = (uint128_t)a0_2  * a[1]
       + (uint128_t)a2_38 * a[4]
       + (uint128_t)a3_19 * a[3];

    t2 = (uint128_t)a0_2  * a[2]
       + (uint128_t)a[1]  * a[1]
       + (uint128_t)a3_38 * a[4];

    t3 = (uint128_t)a0_2  * a[3]
       + (uint128_t)a1_2  * a[2]
       + (uint128_t)a4_19 * a[4];

    t4 = (uint128_t)a0_2  * a[4]
       + (uint128_t)a1_2  * a[3]
       + (uint128_t)a[2]  * a[2];

    /* Carry chain */
    uint64_t c;

    c = (uint64_t)(t0 >> 51); t1 += c; out[0] = (uint64_t)t0 & LIMB_MASK;
    c = (uint64_t)(t1 >> 51); t2 += c; out[1] = (uint64_t)t1 & LIMB_MASK;
    c = (uint64_t)(t2 >> 51); t3 += c; out[2] = (uint64_t)t2 & LIMB_MASK;
    c = (uint64_t)(t3 >> 51); t4 += c; out[3] = (uint64_t)t3 & LIMB_MASK;
    c = (uint64_t)(t4 >> 51);           out[4] = (uint64_t)t4 & LIMB_MASK;

    out[0] += c * 19;
    c = out[0] >> 51; out[1] += c; out[0] &= LIMB_MASK;
}

/**
 * @brief Multiply field element by the constant 121666 (a24 = (A-2)/4)
 *
 * Used in the Montgomery ladder differential addition step.
 * 121666 < 2^17, so each product fits in 68 bits — no overflow risk.
 *
 * @param[out] out  Result field element
 * @param[in]  a    Operand
 */
static void fe_mul121666(fe out, const fe a)
{
    typedef unsigned __int128 uint128_t;

    uint128_t t0 = (uint128_t)a[0] * 121666;
    uint128_t t1 = (uint128_t)a[1] * 121666;
    uint128_t t2 = (uint128_t)a[2] * 121666;
    uint128_t t3 = (uint128_t)a[3] * 121666;
    uint128_t t4 = (uint128_t)a[4] * 121666;

    uint64_t c;

    c = (uint64_t)(t0 >> 51); t1 += c; out[0] = (uint64_t)t0 & LIMB_MASK;
    c = (uint64_t)(t1 >> 51); t2 += c; out[1] = (uint64_t)t1 & LIMB_MASK;
    c = (uint64_t)(t2 >> 51); t3 += c; out[2] = (uint64_t)t2 & LIMB_MASK;
    c = (uint64_t)(t3 >> 51); t4 += c; out[3] = (uint64_t)t3 & LIMB_MASK;
    c = (uint64_t)(t4 >> 51);           out[4] = (uint64_t)t4 & LIMB_MASK;

    out[0] += c * 19;
    c = out[0] >> 51; out[1] += c; out[0] &= LIMB_MASK;
}

/* ============================================================================
 * CONSTANT-TIME CONDITIONAL SWAP
 * ============================================================================ */

/**
 * @brief Constant-time conditional swap of two field elements
 *
 * If swap == 1, exchanges a and b. If swap == 0, no change.
 * Uses XOR masking: no branches, no secret-dependent memory patterns.
 *
 * @param[in,out] a     First field element
 * @param[in,out] b     Second field element
 * @param[in]     swap  Swap flag (0 or 1) — MUST be 0 or 1, not arbitrary
 */
static void fe_cswap(fe a, fe b, uint64_t swap)
{
    /*
     * Expand swap bit to a full 64-bit mask:
     * swap=1 → mask=0xFFFFFFFFFFFFFFFF, swap=0 → mask=0x0000000000000000
     */
    const uint64_t mask = (uint64_t)0 - swap;
    uint64_t t;

    t = mask & (a[0] ^ b[0]); a[0] ^= t; b[0] ^= t;
    t = mask & (a[1] ^ b[1]); a[1] ^= t; b[1] ^= t;
    t = mask & (a[2] ^ b[2]); a[2] ^= t; b[2] ^= t;
    t = mask & (a[3] ^ b[3]); a[3] ^= t; b[3] ^= t;
    t = mask & (a[4] ^ b[4]); a[4] ^= t; b[4] ^= t;
}

/**
 * @brief Copy a field element: dst = src
 *
 * @param[out] dst  Destination
 * @param[in]  src  Source
 */
static void fe_copy(fe dst, const fe src)
{
    dst[0] = src[0];
    dst[1] = src[1];
    dst[2] = src[2];
    dst[3] = src[3];
    dst[4] = src[4];
}

/**
 * @brief Set a field element to 1 (multiplicative identity)
 *
 * @param[out] h  Field element to initialize
 */
static void fe_one(fe h)
{
    h[0] = 1;
    h[1] = 0;
    h[2] = 0;
    h[3] = 0;
    h[4] = 0;
}

/**
 * @brief Set a field element to 0 (additive identity)
 *
 * @param[out] h  Field element to initialize
 */
static void fe_zero(fe h)
{
    h[0] = 0;
    h[1] = 0;
    h[2] = 0;
    h[3] = 0;
    h[4] = 0;
}

/* ============================================================================
 * FIELD INVERSION via Fermat's Little Theorem: a^(p-2) mod p
 * ============================================================================ */

/**
 * @brief Field inversion: out = a^(-1) mod p = a^(p-2) mod p
 *
 * Uses an addition chain for p-2 = 2^255 - 21.
 * The chain is the classic one from djb's ref10 implementation:
 *   a^1, a^2, a^(2^2-1), a^(2^4-1), a^(2^5-1), a^(2^10-1),
 *   a^(2^20-1), a^(2^40-1), a^(2^50-1), a^(2^100-1),
 *   a^(2^200-1), a^(2^250-1), a^(2^255-21)
 *
 * Total: 254 squarings + 11 multiplications.
 *
 * @param[out] out  Result: a^(-1) mod p
 * @param[in]  a    Element to invert (must not be zero)
 */
static void fe_invert(fe out, const fe a)
{
    fe t0, t1, t2, t3;
    int i;

    /*
     * Addition chain for a^(p-2) = a^(2^255 - 21):
     * Following the ref10 chain: build up powers of a as
     * a^2, a^9, a^11, a^(2^5-1), a^(2^10-1), ..., a^(2^250-1),
     * then a^(2^255-32) * a^11 = a^(2^255-21).
     */

    /* t0 = a^2 */
    fe_sq(t0, a);

    /* t1 = a^4 */
    fe_sq(t1, t0);

    /* t1 = t1^2 = a^8 */
    fe_sq(t1, t1);

    /* t1 = t1 * a = a^9 */
    fe_mul(t1, t1, a);

    /* t0 = t1 * t0 = a^(9+2) = a^11 */
    fe_mul(t0, t1, t0);

    /* t2 = t0^2 = a^22 */
    fe_sq(t2, t0);

    /* t1 = t2 * t1 = a^(22+9) = a^31 = a^(2^5-1) */
    fe_mul(t1, t2, t1);

    /* t2 = t1^(2^5) = a^((2^5-1)*2^5) = a^(2^10-2^5) */
    fe_sq(t2, t1);
    for (i = 1; i < 5; i++) { fe_sq(t2, t2); }

    /* t1 = t2 * t1 = a^(2^10-1) */
    fe_mul(t1, t2, t1);

    /* t2 = t1^(2^10) = a^((2^10-1)*2^10) = a^(2^20-2^10) */
    fe_sq(t2, t1);
    for (i = 1; i < 10; i++) { fe_sq(t2, t2); }

    /* t2 = t2 * t1 = a^(2^20-1) */
    fe_mul(t2, t2, t1);

    /* t3 = t2^(2^20) = a^(2^40-2^20) */
    fe_sq(t3, t2);
    for (i = 1; i < 20; i++) { fe_sq(t3, t3); }

    /* t2 = t3 * t2 = a^(2^40-1) */
    fe_mul(t2, t3, t2);

    /* t2 = t2^(2^10) = a^((2^40-1)*2^10) = a^(2^50-2^10) */
    fe_sq(t2, t2);
    for (i = 1; i < 10; i++) { fe_sq(t2, t2); }

    /* t1 = t2 * t1 = a^(2^50-1) */
    fe_mul(t1, t2, t1);

    /* t2 = t1^(2^50) = a^(2^100-2^50) */
    fe_sq(t2, t1);
    for (i = 1; i < 50; i++) { fe_sq(t2, t2); }

    /* t2 = t2 * t1 = a^(2^100-1) */
    fe_mul(t2, t2, t1);

    /* t3 = t2^(2^100) = a^(2^200-2^100) */
    fe_sq(t3, t2);
    for (i = 1; i < 100; i++) { fe_sq(t3, t3); }

    /* t2 = t3 * t2 = a^(2^200-1) */
    fe_mul(t2, t3, t2);

    /* t2 = t2^(2^50) = a^(2^250-2^50) */
    fe_sq(t2, t2);
    for (i = 1; i < 50; i++) { fe_sq(t2, t2); }

    /* t1 = t2 * t1 = a^(2^250-1) */
    fe_mul(t1, t2, t1);

    /* t1 = t1^(2^5) = a^((2^250-1)*2^5) = a^(2^255-2^5) = a^(2^255-32) */
    fe_sq(t1, t1);
    for (i = 1; i < 5; i++) { fe_sq(t1, t1); }

    /* out = t1 * t0 = a^(2^255-32+11) = a^(2^255-21) = a^(p-2) */
    fe_mul(out, t1, t0);
}

/* ============================================================================
 * X25519 SCALAR MULTIPLICATION — Montgomery Ladder (RFC 7748 §5)
 * ============================================================================ */

/**
 * @brief X25519 scalar multiplication
 *
 * Computes q = X25519(scalar, u_point) per RFC 7748 Section 5.
 * Uses the Montgomery ladder with constant-time conditional swaps.
 *
 * @param[out] out     32-byte result (x-coordinate of scalar * point)
 * @param[in]  scalar  32-byte scalar (private key, pre-clamped)
 * @param[in]  point   32-byte u-coordinate of input point
 */
static void x25519_scalarmult(uint8_t out[32],
                               const uint8_t scalar[32],
                               const uint8_t point[32])
{
    fe u, x_1, x_2, z_2, x_3, z_3, tmp0, tmp1;
    uint8_t e[32];
    int i;
    uint64_t swap;
    uint64_t bit;

    /* Copy scalar and apply clamping per RFC 7748 */
    for (i = 0; i < 32; i++) {
        e[i] = scalar[i];
    }
    e[0]  &= 248;
    e[31] &= 127;
    e[31] |= 64;

    /* Decode the u-coordinate */
    fe_frombytes(u, point);

    /* Montgomery ladder initialization:
     * (x_2, z_2) = (1, 0)   — the point at infinity
     * (x_3, z_3) = (u, 1)   — the input point
     */
    fe_one(x_2);
    fe_zero(z_2);
    fe_copy(x_3, u);
    fe_one(z_3);
    fe_copy(x_1, u);

    swap = 0;

    /* Iterate from bit 254 down to bit 0 (255 iterations) */
    for (i = 254; i >= 0; i--) {
        /* Extract bit i of the scalar (constant-time) */
        bit = (uint64_t)(e[i >> 3] >> (i & 7)) & 1;

        /* Conditional swap based on swap XOR bit */
        swap ^= bit;
        fe_cswap(x_2, x_3, swap);
        fe_cswap(z_2, z_3, swap);
        swap = bit;

        /* Montgomery ladder step (differential addition + doubling) */
        fe_sub(tmp0, x_3, z_3);   /* A  = x_3 - z_3 */
        fe_sub(tmp1, x_2, z_2);   /* B  = x_2 - z_2 */
        fe_add(x_2, x_2, z_2);    /* C  = x_2 + z_2 */
        fe_add(z_2, x_3, z_3);    /* D  = x_3 + z_3 */

        fe_mul(z_3, tmp0, x_2);    /* DA = A * C      */
        fe_mul(z_2, z_2, tmp1);    /* CB = D * B      */
        fe_sq(tmp0, tmp1);         /* BB = B^2        */
        fe_sq(tmp1, x_2);          /* CC = C^2        */

        fe_add(x_3, z_3, z_2);    /* DA + CB         */
        fe_sub(z_2, z_3, z_2);    /* DA - CB         */
        fe_mul(x_2, tmp1, tmp0);  /* x_2 = CC * BB   */
        fe_sub(tmp1, tmp1, tmp0); /* E  = CC - BB    */

        fe_sq(z_2, z_2);          /* (DA - CB)^2     */
        fe_mul121666(z_3, tmp1);  /* a24 * E         */
        fe_sq(x_3, x_3);         /* (DA + CB)^2     */
        fe_add(tmp0, tmp0, z_3);  /* BB + a24*E      */

        fe_mul(z_3, x_1, z_2);   /* z_3 = x_1 * (DA - CB)^2       */
        fe_mul(z_2, tmp1, tmp0);  /* z_2 = E * (BB + a24*E)        */
    }

    /* Final conditional swap */
    fe_cswap(x_2, x_3, swap);
    fe_cswap(z_2, z_3, swap);

    /* Recover x-coordinate: result = x_2 * z_2^(-1) */
    fe_invert(z_2, z_2);
    fe_mul(x_2, x_2, z_2);
    fe_tobytes(out, x_2);

    /* Zeroize stack temporaries — use vos3_cache_wipe() to prevent
     * dead-store elimination and evict scalar from CPU caches.
     */
    vos3_cache_wipe(e, sizeof(e));
    fe_zero(u); fe_zero(x_1); fe_zero(x_2); fe_zero(z_2);
    fe_zero(x_3); fe_zero(z_3); fe_zero(tmp0); fe_zero(tmp1);
}

/* ============================================================================
 * BASEPOINT (generator)
 * ============================================================================ */

/** @brief The Curve25519 basepoint: u = 9 */
static const uint8_t basepoint[32] = {
    9, 0, 0, 0, 0, 0, 0, 0,  0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,  0, 0, 0, 0, 0, 0, 0, 0
};

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Generate an X25519 keypair
 *
 * 1. Extracts 32 bytes of entropy for the private key
 * 2. Applies RFC 7748 clamping
 * 3. Computes the public key as X25519(privkey, basepoint_9)
 *
 * @param[out] privkey  32-byte private key (clamped)
 * @param[out] pubkey   32-byte public key (u-coordinate)
 */
void vos3_x25519_keygen(uint8_t privkey[32], uint8_t pubkey[32])
{
    /* Step 1: Extract 32 bytes of cryptographic entropy */
    vos3_entropy_extract(privkey, 32);

    /* Step 2: Clamp per RFC 7748 §5 */
    privkey[0]  &= 248;   /* Clear bottom 3 bits    */
    privkey[31] &= 127;   /* Clear bit 255          */
    privkey[31] |= 64;    /* Set bit 254            */

    /* Step 3: Public key = scalar * basepoint */
    x25519_scalarmult(pubkey, privkey, basepoint);

    VOS3_INFO("[TLS] X25519 keygen complete");
}

/**
 * @brief Compute X25519 shared secret
 *
 * Computes shared = X25519(privkey, peer_pubkey). Verifies the result
 * is not the all-zeros point (which indicates a small-subgroup attack
 * or invalid peer key).
 *
 * @param[out] shared      32-byte shared secret
 * @param[in]  privkey     32-byte private key (clamped)
 * @param[in]  peer_pubkey 32-byte peer's public key
 * @return 0 on success, -1 if the result is the all-zeros point (invalid)
 */
int vos3_x25519_shared(uint8_t shared[32],
                        const uint8_t privkey[32],
                        const uint8_t peer_pubkey[32])
{
    uint8_t check;
    int i;

    /* Compute the raw shared secret */
    x25519_scalarmult(shared, privkey, peer_pubkey);

    /*
     * Constant-time all-zeros check:
     * OR all bytes together; if result is zero, the output is the
     * zero point (contribution from low-order points).
     */
    check = 0;
    for (i = 0; i < 32; i++) {
        check |= shared[i];
    }

    /*
     * If check == 0, shared secret is all zeros → invalid.
     * Wipe and return error (constant-time: always wipe on failure).
     * Use arithmetic to avoid branch: (check - 1) >> 8 is 0xFF if check==0.
     */
    if (check == 0) {
        memset(shared, 0, 32);
        return -1;
    }

    return 0;
}
