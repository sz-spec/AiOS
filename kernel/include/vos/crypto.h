/**
 * @file crypto.h
 * @brief VOS3 Cryptographic Utilities — Cache Wipe, FPU Guard, HKDF
 *
 * @details Common cryptographic helpers for the VOS3 Sovereign Crypto Shield:
 *          - vos3_cache_wipe(): Flush cache lines + zero buffer (CLFLUSHOPT/CLFLUSH)
 *          - vos3_fpu_begin/end(): Protect AI register state during AES-NI ops
 *          - HKDF-SHA256: RFC 5869 key derivation using existing HMAC-SHA256
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.2 — Sovereign Crypto Shield (v35.1)
 */

#ifndef VOS3_CRYPTO_H
#define VOS3_CRYPTO_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CACHE LINE WIPE — CLFLUSHOPT with CLFLUSH fallback (Haswell 2013+)
 * ============================================================================ */

/**
 * @brief Securely wipe a buffer: zero memory, then flush from all cache levels
 *
 * Uses CLFLUSHOPT when available (CPUID.07H:EBX[23]), falls back to CLFLUSH
 * (available on all x86_64 since Pentium 4). Prevents secret residue in L1/L2/L3.
 *
 * @param[in,out] buf  Buffer to wipe
 * @param[in]     len  Buffer length in bytes
 *
 * @note Thread-safe (operates on caller's buffer only)
 * @note Must NOT be called from ISR context (uses volatile memset)
 */
void vos3_cache_wipe(void *buf, size_t len);

/**
 * @brief Flush cache lines for a buffer WITHOUT zeroing
 *
 * @param[in] buf  Buffer to flush
 * @param[in] len  Buffer length in bytes
 */
void vos3_cache_flush(const void *buf, size_t len);

/* ============================================================================
 * FPU / SIMD GUARD — Protect AI register state during crypto (AES-NI)
 * ============================================================================ */

/**
 * @brief Begin FPU/SIMD section — saves current task's FPU state
 *
 * Must be called before any AES-NI, PCLMULQDQ, or SSE operations in
 * kernel crypto code. Clears CR0.TS so SIMD instructions don't fault.
 * Paired with vos3_fpu_end().
 *
 * @note Nesting is NOT supported — do not call begin twice without end
 * @note Must NOT be called from ISR context
 */
void vos3_fpu_begin(void);

/**
 * @brief End FPU/SIMD section — restores previous FPU state
 *
 * Restores CR0.TS to re-enable lazy FPU switching. Must be called
 * after every vos3_fpu_begin().
 */
void vos3_fpu_end(void);

/* ============================================================================
 * HKDF-SHA256 — RFC 5869 HMAC-based Extract-and-Expand KDF
 * ============================================================================ */

/**
 * @brief HKDF-Extract: derive a pseudorandom key from input keying material
 *
 * PRK = HMAC-SHA256(salt, IKM)
 *
 * @param[in]  salt      Optional salt (if NULL, uses 32 zero bytes)
 * @param[in]  salt_len  Salt length
 * @param[in]  ikm       Input Keying Material
 * @param[in]  ikm_len   IKM length
 * @param[out] prk       Output PRK (32 bytes)
 */
void vos3_hkdf_extract(const uint8_t *salt, size_t salt_len,
                        const uint8_t *ikm, size_t ikm_len,
                        uint8_t prk[32]);

/**
 * @brief HKDF-Expand: derive output keying material from PRK
 *
 * OKM = T(1) || T(2) || ... where T(i) = HMAC-SHA256(PRK, T(i-1) || info || i)
 *
 * @param[in]  prk       Pseudorandom key (32 bytes from Extract)
 * @param[in]  prk_len   PRK length (must be >= 32)
 * @param[in]  info      Context and application specific info
 * @param[in]  info_len  Info length
 * @param[out] okm       Output Keying Material
 * @param[in]  okm_len   Desired output length (max 255 * 32 = 8160 bytes)
 * @return 0 on success, -1 on invalid parameters
 */
int vos3_hkdf_expand(const uint8_t *prk, size_t prk_len,
                     const uint8_t *info, size_t info_len,
                     uint8_t *okm, size_t okm_len);

/**
 * @brief HKDF-Expand-Label for TLS 1.3 key derivation
 *
 * Derives keys per RFC 8446 §7.1:
 * HKDF-Expand-Label(Secret, Label, Context, Length)
 *
 * @param[in]  secret       Input secret (32 bytes)
 * @param[in]  label        ASCII label (without "tls13 " prefix — added internally)
 * @param[in]  label_len    Label length
 * @param[in]  context      Hash of handshake context (may be NULL for empty)
 * @param[in]  context_len  Context length
 * @param[out] out          Output key material
 * @param[in]  out_len      Desired output length
 * @return 0 on success, -1 on error
 */
int vos3_tls13_hkdf_expand_label(const uint8_t secret[32],
                                  const char *label, size_t label_len,
                                  const uint8_t *context, size_t context_len,
                                  uint8_t *out, size_t out_len);

/* ============================================================================
 * AES-NI CAPABILITY DETECTION
 * ============================================================================ */

/**
 * @brief Check if CPU supports AES-NI
 * @return 1 if AES-NI available, 0 otherwise
 */
int vos3_cpu_has_aesni(void);

/**
 * @brief Check if CPU supports PCLMULQDQ (for GCM)
 * @return 1 if PCLMULQDQ available, 0 otherwise
 */
int vos3_cpu_has_pclmulqdq(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_CRYPTO_H */
