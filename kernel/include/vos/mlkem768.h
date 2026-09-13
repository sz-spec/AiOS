/**
 * @file mlkem768.h
 * @brief NIST FIPS 203 ML-KEM-768 — public API (Stage 14 scaffolding).
 *
 * Implements (or, in this stage, declares) the parameter-set-768 variant of
 * the Module-Lattice Key-Encapsulation Mechanism standardised in
 * FIPS 203 (final 2024-08-13).
 *
 * Honest scope ceiling — read before integrating
 * ===============================================
 *
 * Stage 14 ships this header + an implementation file
 * (kernel/src/crypto/mlkem768.c) whose **foundation primitive (SHAKE-128 /
 * SHAKE-256 over Keccak-f[1600]) is real and self-tested**, but whose
 * **lattice operations (NTT, polynomial sample, K-PKE encrypt/decrypt) are
 * declared and stubbed**. A real Kyber-768 implementation is roughly 1500
 * lines of constant-time C with extensive test-vector coverage; ship-grade
 * implementation is a multi-week project tracked under Stage 14.B.2.
 *
 * What works today
 * ----------------
 * - vos3_shake128_xof / vos3_shake256_xof: real, FIPS-202-conforming
 *   sponge-based extendable-output functions usable as building blocks.
 * - vos3_mlkem768_KEY_BYTES, _CT_BYTES, _SS_BYTES sizing constants — match
 *   FIPS 203 §7 parameter set ML-KEM-768.
 * - Public API surface (vos3_mlkem768_keygen / encaps / decaps) — declared
 *   here so the rest of the kernel can be wired against it today.
 *
 * What does NOT work yet
 * ----------------------
 * - vos3_mlkem768_keygen / encaps / decaps return VOS3_MLKEM_E_NOT_IMPLEMENTED
 *   pending the Stage 14.B.2 lattice port.
 * - The TLS 1.3 hybrid X25519MLKEM768 group is therefore NOT yet negotiable.
 *   tls13.c will continue to use X25519-only until Stage 14.B.2 lands.
 *
 * @date 2026-05-09
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#ifndef VOS3_MLKEM768_H
#define VOS3_MLKEM768_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Parameter-set sizes (FIPS 203 §7, ML-KEM-768)
 *
 * These constants are exact and authoritative — they are read off the spec
 * and serve as the binary interface contract. They will not change when the
 * lattice operations land in Stage 14.B.2.
 * ============================================================================ */

/** Public key bytes — encapsulation key length. (FIPS 203 §7 ML-KEM-768) */
#define VOS3_MLKEM768_PK_BYTES        1184U

/** Secret key bytes — decapsulation key length. */
#define VOS3_MLKEM768_SK_BYTES        2400U

/** Ciphertext bytes — encapsulated key length. */
#define VOS3_MLKEM768_CT_BYTES        1088U

/** Shared-secret bytes — output of decaps and encaps. */
#define VOS3_MLKEM768_SS_BYTES          32U

/** Random-coins bytes — encaps takes 32 bytes of randomness; keygen 64. */
#define VOS3_MLKEM768_ENCAPS_RAND_BYTES 32U
#define VOS3_MLKEM768_KEYGEN_RAND_BYTES 64U

/* ============================================================================
 * Return codes
 * ============================================================================ */

#define VOS3_MLKEM_OK                    0
#define VOS3_MLKEM_E_NULL               -1   /* NULL pointer */
#define VOS3_MLKEM_E_INVAL              -2   /* invalid argument */
#define VOS3_MLKEM_E_NOT_IMPLEMENTED  -100   /* Stage 14.B.2 stub */

/* ============================================================================
 * SHAKE — FIPS 202 extendable-output functions
 *
 * REAL in Stage 14. Self-test vectors checked at boot via
 * vos3_mlkem768_self_test() (see mlkem768.c). Usable as a general-purpose
 * primitive elsewhere in the kernel; not specific to ML-KEM.
 * ============================================================================ */

/**
 * @brief SHAKE-128 with 128-bit security; produces ``out_len`` bytes of output.
 *
 * @param[in]  in       Input bytes
 * @param[in]  in_len   Length of input
 * @param[out] out      Output buffer (caller-allocated)
 * @param[in]  out_len  Number of output bytes to produce
 * @return 0 on success, negative on error.
 */
int vos3_shake128_xof(const uint8_t *in, size_t in_len,
                      uint8_t *out, size_t out_len);

/**
 * @brief SHAKE-256 with 256-bit security.
 *
 * Same signature as SHAKE-128 but uses the higher-rate Keccak parameter set.
 */
int vos3_shake256_xof(const uint8_t *in, size_t in_len,
                      uint8_t *out, size_t out_len);

/* ============================================================================
 * ML-KEM-768 public API — STUBBED in Stage 14.B.1
 *
 * Each of the three functions below returns VOS3_MLKEM_E_NOT_IMPLEMENTED
 * until Stage 14.B.2 ships the lattice operations. Calling code that wants
 * to be hybrid-ready can integrate against this signature today — when the
 * stubs are replaced, no caller-side change is required.
 * ============================================================================ */

/**
 * @brief Generate a fresh ML-KEM-768 key pair.
 *
 * @param[in]  rand    64 bytes of CSPRNG-quality randomness (FIPS 203 §7
 *                     keygen seed)
 * @param[out] pk      Public/encapsulation key (VOS3_MLKEM768_PK_BYTES)
 * @param[out] sk      Secret/decapsulation key (VOS3_MLKEM768_SK_BYTES)
 * @return 0 on success, VOS3_MLKEM_E_NOT_IMPLEMENTED in Stage 14.B.1.
 */
int vos3_mlkem768_keygen(const uint8_t  rand[VOS3_MLKEM768_KEYGEN_RAND_BYTES],
                         uint8_t        pk[VOS3_MLKEM768_PK_BYTES],
                         uint8_t        sk[VOS3_MLKEM768_SK_BYTES]);

/**
 * @brief Encapsulate a fresh shared secret to a recipient's public key.
 *
 * @param[in]  pk      Recipient public key (VOS3_MLKEM768_PK_BYTES)
 * @param[in]  rand    32 bytes of CSPRNG randomness
 * @param[out] ct      Ciphertext (VOS3_MLKEM768_CT_BYTES)
 * @param[out] ss      Shared secret (VOS3_MLKEM768_SS_BYTES)
 * @return 0 on success, VOS3_MLKEM_E_NOT_IMPLEMENTED in Stage 14.B.1.
 */
int vos3_mlkem768_encaps(const uint8_t  pk[VOS3_MLKEM768_PK_BYTES],
                         const uint8_t  rand[VOS3_MLKEM768_ENCAPS_RAND_BYTES],
                         uint8_t        ct[VOS3_MLKEM768_CT_BYTES],
                         uint8_t        ss[VOS3_MLKEM768_SS_BYTES]);

/**
 * @brief Decapsulate a shared secret using a private key.
 *
 * @param[in]  sk      Decapsulation key
 * @param[in]  ct      Ciphertext to open
 * @param[out] ss      Shared secret
 * @return 0 on success, VOS3_MLKEM_E_NOT_IMPLEMENTED in Stage 14.B.1.
 */
int vos3_mlkem768_decaps(const uint8_t  sk[VOS3_MLKEM768_SK_BYTES],
                         const uint8_t  ct[VOS3_MLKEM768_CT_BYTES],
                         uint8_t        ss[VOS3_MLKEM768_SS_BYTES]);

/* ============================================================================
 * Hybrid X25519MLKEM768 — IETF draft-ietf-tls-hybrid-design
 *
 * Combines X25519 ECDH + ML-KEM-768 KEM into a single TLS 1.3 named-group.
 * Concatenates the X25519 shared secret with the ML-KEM ciphertext / public-
 * key bytes and feeds the result through HKDF.
 *
 * Wire format (server share, summary):
 *   8 bytes:    "X25519ML"  (group identifier; placeholder, real value is
 *                            an IANA-registered uint16 — to be confirmed
 *                            against the IETF spec at integration time)
 *   1216 bytes: X25519_pubkey || ML-KEM-768_pubkey
 *
 * STUBBED — depends on vos3_mlkem768_keygen above.
 * ============================================================================ */

/**
 * @brief Self-test the SHAKE primitives at boot.
 *
 * Runs SHAKE-128/256 against published FIPS 202 KAT vectors. Returns 0 on
 * success; logs and returns non-zero on mismatch (which would imply a
 * silent toolchain regression in Keccak-f[1600] codegen).
 *
 * The lattice-side tests are gated by VOS3_MLKEM_E_NOT_IMPLEMENTED until
 * Stage 14.B.2.
 */
int vos3_mlkem768_self_test(void);

#endif /* VOS3_MLKEM768_H */
