#ifndef VOS3_PRODUCTION_BUILD
/**
 * @file tls_integrity_test.c
 * @brief TLS 1.3 Crypto Integrity Test Suite — Attack Resistance Validation
 *
 * @details Freestanding kernel test module that validates the VOS3 TLS 1.3
 *          crypto primitives against protocol-level attack scenarios using
 *          direct function calls to AES-GCM, X25519, HKDF, cache-wipe, and
 *          FPU guard APIs.
 *
 *          Tests cover:
 *            1. AES-GCM tag forgery rejection
 *            2. AES-GCM ciphertext tampering detection
 *            3. AES-GCM empty plaintext edge case
 *            4. X25519 key exchange round-trip
 *            5. X25519 small-subgroup rejection
 *            6. X25519 RFC 7748 section 6.1 known test vector
 *            7. HKDF-SHA256 RFC 5869 Test Case 1
 *            8. Cache wipe effectiveness
 *            9. FPU guard state isolation
 *           10. AES-GCM determinism (same input -> same output)
 *
 * @note Test 9 (FPU Guard) requires this file to be compiled with:
 *         CFLAGS += -msse -msse2
 *       Add a per-file override in the Makefile:
 *         $(OBJ_DIR)/tests/tls_integrity_test.o: CFLAGS += -msse -msse2
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.2 -- Sovereign Crypto Shield (v35.1)
 */

#include "../../include/vos/tls.h"
#include "../../include/vos/crypto.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/entropy.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_tls_pass = 0;
static uint32_t g_tls_fail = 0;

#define TLS_ASSERT(cond, name)                                                \
    do {                                                                      \
        if (cond) {                                                           \
            g_tls_pass++;                                                     \
            VOS3_INFO("[TLS-TEST] PASS: %s", (name));                         \
        } else {                                                              \
            g_tls_fail++;                                                     \
            VOS3_ERROR("[TLS-TEST] FAIL: %s (line %d)", (name), __LINE__);    \
        }                                                                     \
    } while (0)

/**
 * @brief Check if a buffer is entirely filled with a specific byte value.
 *
 * @param buf   Buffer to check
 * @param val   Expected byte value
 * @param len   Buffer length
 * @return 1 if all bytes match val, 0 otherwise
 */
static int buf_all_equal(const uint8_t *buf, uint8_t val, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if (buf[i] != val) {
            return 0;
        }
    }
    return 1;
}

/**
 * @brief Compare two buffers byte-by-byte.
 *
 * @param a     First buffer
 * @param b     Second buffer
 * @param len   Length to compare
 * @return 1 if identical, 0 if different
 */
static int buf_equal(const uint8_t *a, const uint8_t *b, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if (a[i] != b[i]) {
            return 0;
        }
    }
    return 1;
}

/* ============================================================================
 * TEST 1: AES-GCM TAG FORGERY REJECTION
 *
 * Verifies that flipping a single bit in the authentication tag causes
 * decryption to fail with -1, and that the plaintext output buffer is
 * completely zeroed (no partial decrypt leak).
 * ============================================================================ */

static void test_aes_gcm_tag_forgery(void)
{
    VOS3_INFO("[TLS-TEST] === Test 1: AES-GCM Tag Forgery Rejection ===");

    /* Known 32-byte key (AES-256) */
    static const uint8_t key[32] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
        0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
        0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f
    };

    /* 12-byte IV */
    static const uint8_t iv[12] = {
        0xca, 0xfe, 0xba, 0xbe, 0xfa, 0xce,
        0xdb, 0xad, 0xde, 0xca, 0xf8, 0x88
    };

    /* 16-byte plaintext */
    static const uint8_t plaintext[16] = {
        0xd9, 0x31, 0x32, 0x25, 0xf8, 0x84, 0x06, 0xe5,
        0xa5, 0x59, 0x09, 0xc5, 0xaf, 0xf5, 0x26, 0x9a
    };

    /* No AAD for this test */
    uint8_t ciphertext[16];
    uint8_t tag[16];
    uint8_t decrypted[16];
    int rc;

    vos3_aes_gcm_ctx_t ctx;
    rc = vos3_aes_gcm_init(&ctx, key, 32);
    TLS_ASSERT(rc == 0, "T1: AES-GCM init");

    /* Encrypt */
    rc = vos3_aes_gcm_encrypt(&ctx, iv, NULL, 0,
                               plaintext, 16, ciphertext, tag);
    TLS_ASSERT(rc == 0, "T1: AES-GCM encrypt");

    /* Corrupt tag: flip bit 0 of byte 0 */
    uint8_t bad_tag[16];
    memcpy(bad_tag, tag, 16);
    bad_tag[0] ^= 0x01;

    /* Decrypt with corrupted tag -- must fail */
    memset(decrypted, 0xCC, 16);  /* fill with sentinel */
    rc = vos3_aes_gcm_decrypt(&ctx, iv, NULL, 0,
                               ciphertext, 16, bad_tag, decrypted);
    TLS_ASSERT(rc == -1, "T1: Forged tag rejected (rc=-1)");
    TLS_ASSERT(buf_all_equal(decrypted, 0x00, 16),
               "T1: Plaintext buffer zeroed on auth failure");

    vos3_aes_gcm_destroy(&ctx);
}

/* ============================================================================
 * TEST 2: AES-GCM CIPHERTEXT TAMPERING
 *
 * Verifies that flipping a single bit in the ciphertext causes
 * decryption to fail, and the output buffer is zeroed.
 * ============================================================================ */

static void test_aes_gcm_ciphertext_tamper(void)
{
    VOS3_INFO("[TLS-TEST] === Test 2: AES-GCM Ciphertext Tampering ===");

    static const uint8_t key[32] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
        0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
        0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f
    };

    static const uint8_t iv[12] = {
        0xca, 0xfe, 0xba, 0xbe, 0xfa, 0xce,
        0xdb, 0xad, 0xde, 0xca, 0xf8, 0x88
    };

    static const uint8_t plaintext[16] = {
        0xd9, 0x31, 0x32, 0x25, 0xf8, 0x84, 0x06, 0xe5,
        0xa5, 0x59, 0x09, 0xc5, 0xaf, 0xf5, 0x26, 0x9a
    };

    uint8_t ciphertext[16];
    uint8_t tag[16];
    uint8_t decrypted[16];
    int rc;

    vos3_aes_gcm_ctx_t ctx;
    rc = vos3_aes_gcm_init(&ctx, key, 32);
    TLS_ASSERT(rc == 0, "T2: AES-GCM init");

    rc = vos3_aes_gcm_encrypt(&ctx, iv, NULL, 0,
                               plaintext, 16, ciphertext, tag);
    TLS_ASSERT(rc == 0, "T2: AES-GCM encrypt");

    /* Tamper with ciphertext: flip bit 0 of byte 0 */
    uint8_t bad_ct[16];
    memcpy(bad_ct, ciphertext, 16);
    bad_ct[0] ^= 0x01;

    /* Decrypt with tampered ciphertext but original tag */
    memset(decrypted, 0xCC, 16);
    rc = vos3_aes_gcm_decrypt(&ctx, iv, NULL, 0,
                               bad_ct, 16, tag, decrypted);
    TLS_ASSERT(rc == -1, "T2: Tampered ciphertext rejected (rc=-1)");
    TLS_ASSERT(buf_all_equal(decrypted, 0x00, 16),
               "T2: Plaintext buffer zeroed on tamper detection");

    vos3_aes_gcm_destroy(&ctx);
}

/* ============================================================================
 * TEST 3: AES-GCM EMPTY PLAINTEXT (EDGE CASE)
 *
 * Verifies that encrypting/decrypting zero-length plaintext with AAD
 * works correctly, and that a tag-forged empty ciphertext is rejected.
 * ============================================================================ */

static void test_aes_gcm_empty_plaintext(void)
{
    VOS3_INFO("[TLS-TEST] === Test 3: AES-GCM Empty Plaintext ===");

    static const uint8_t key[16] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f
    };

    static const uint8_t iv[12] = {
        0x01, 0x02, 0x03, 0x04, 0x05, 0x06,
        0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c
    };

    static const uint8_t aad[8] = {
        0xfe, 0xed, 0xfa, 0xce, 0xde, 0xad, 0xbe, 0xef
    };

    uint8_t tag[16];
    int rc;

    vos3_aes_gcm_ctx_t ctx;
    rc = vos3_aes_gcm_init(&ctx, key, 16);
    TLS_ASSERT(rc == 0, "T3: AES-128-GCM init");

    /* Encrypt empty plaintext with AAD */
    rc = vos3_aes_gcm_encrypt(&ctx, iv, aad, 8,
                               NULL, 0, NULL, tag);
    TLS_ASSERT(rc == 0, "T3: Encrypt empty plaintext succeeds");

    /* Decrypt with valid tag */
    rc = vos3_aes_gcm_decrypt(&ctx, iv, aad, 8,
                               NULL, 0, tag, NULL);
    TLS_ASSERT(rc == 0, "T3: Decrypt empty ciphertext with valid tag succeeds");

    /* Flip tag bit and try again */
    uint8_t bad_tag[16];
    memcpy(bad_tag, tag, 16);
    bad_tag[0] ^= 0x01;

    rc = vos3_aes_gcm_decrypt(&ctx, iv, aad, 8,
                               NULL, 0, bad_tag, NULL);
    TLS_ASSERT(rc == -1, "T3: Forged tag on empty ciphertext rejected");

    vos3_aes_gcm_destroy(&ctx);
}

/* ============================================================================
 * TEST 4: X25519 KEY EXCHANGE ROUND-TRIP
 *
 * Generates two independent keypairs and verifies that X25519 shared
 * secret computation is commutative: DH(privA, pubB) == DH(privB, pubA).
 * ============================================================================ */

static void test_x25519_roundtrip(void)
{
    VOS3_INFO("[TLS-TEST] === Test 4: X25519 Key Exchange Round-Trip ===");

    uint8_t privA[32], pubA[32];
    uint8_t privB[32], pubB[32];
    uint8_t sharedA[32], sharedB[32];
    int rc;

    /* Generate keypair A */
    vos3_x25519_keygen(privA, pubA);

    /* Generate keypair B */
    vos3_x25519_keygen(privB, pubB);

    /* Compute shared secrets from both sides */
    rc = vos3_x25519_shared(sharedA, privA, pubB);
    TLS_ASSERT(rc == 0, "T4: X25519 shared (A*pubB) succeeds");

    rc = vos3_x25519_shared(sharedB, privB, pubA);
    TLS_ASSERT(rc == 0, "T4: X25519 shared (B*pubA) succeeds");

    /* Both shared secrets must be identical */
    TLS_ASSERT(buf_equal(sharedA, sharedB, 32),
               "T4: Shared secrets match (commutative DH)");
}

/* ============================================================================
 * TEST 5: X25519 SMALL-SUBGROUP REJECTION
 *
 * Verifies that an all-zeros public key (small-subgroup / low-order point)
 * is rejected by vos3_x25519_shared(), returning -1.
 * ============================================================================ */

static void test_x25519_small_subgroup(void)
{
    VOS3_INFO("[TLS-TEST] === Test 5: X25519 Small-Subgroup Rejection ===");

    uint8_t priv[32], pub[32];
    uint8_t zero_pub[32];
    uint8_t shared[32];
    int rc;

    /* Generate a valid keypair */
    vos3_x25519_keygen(priv, pub);

    /* All-zeros public key = small-subgroup point on Curve25519 */
    memset(zero_pub, 0, 32);

    rc = vos3_x25519_shared(shared, priv, zero_pub);
    TLS_ASSERT(rc == -1, "T5: All-zeros public key rejected (rc=-1)");
}

/* ============================================================================
 * TEST 6: X25519 KNOWN TEST VECTOR (RFC 7748 Section 6.1)
 *
 * Uses the published test vectors from RFC 7748 to verify correctness
 * of the X25519 scalar multiplication implementation.
 * ============================================================================ */

static void test_x25519_rfc7748_vector(void)
{
    VOS3_INFO("[TLS-TEST] === Test 6: X25519 RFC 7748 Known Vector ===");

    /* Alice's private key (clamped by scalarmult internally) */
    static const uint8_t alice_priv[32] = {
        0x77, 0x07, 0x6d, 0x0a, 0x73, 0x18, 0xa5, 0x7d,
        0x3c, 0x16, 0xc1, 0x72, 0x51, 0xb2, 0x66, 0x45,
        0xdf, 0x4c, 0x2f, 0x87, 0xeb, 0xc0, 0x99, 0x2a,
        0xb1, 0x77, 0xfb, 0xa5, 0x1d, 0xb9, 0x2c, 0x2a
    };

    /* Bob's public key */
    static const uint8_t bob_pub[32] = {
        0xde, 0x9e, 0xdb, 0x7d, 0x7b, 0x7d, 0xc1, 0xb4,
        0xd3, 0x5b, 0x61, 0xc2, 0xec, 0xe4, 0x35, 0x37,
        0x3f, 0x83, 0x43, 0xc8, 0x5b, 0x78, 0x67, 0x4d,
        0xad, 0xfc, 0x7e, 0x14, 0x6f, 0x88, 0x2b, 0x4f
    };

    /* Expected shared secret */
    static const uint8_t expected_shared[32] = {
        0x4a, 0x5d, 0x9d, 0x5b, 0xa4, 0xce, 0x2d, 0xe1,
        0x72, 0x8e, 0x3b, 0xf4, 0x80, 0x35, 0x0f, 0x25,
        0xe0, 0x7e, 0x21, 0xc9, 0x47, 0xd1, 0x9e, 0x33,
        0x76, 0xf0, 0x9b, 0x3c, 0x1e, 0x16, 0x17, 0x42
    };

    uint8_t shared[32];
    int rc;

    rc = vos3_x25519_shared(shared, alice_priv, bob_pub);
    TLS_ASSERT(rc == 0, "T6: X25519 RFC 7748 shared secret succeeds");
    TLS_ASSERT(buf_equal(shared, expected_shared, 32),
               "T6: Shared secret matches RFC 7748 expected value");
}

/* ============================================================================
 * TEST 7: HKDF-SHA256 KNOWN VECTOR (RFC 5869 Test Case 1)
 *
 * Validates HKDF-Extract and HKDF-Expand against published test vectors
 * from RFC 5869, Appendix A, Test Case 1.
 * ============================================================================ */

static void test_hkdf_rfc5869_vector(void)
{
    VOS3_INFO("[TLS-TEST] === Test 7: HKDF-SHA256 RFC 5869 Test Case 1 ===");

    /* IKM = 0x0b repeated 22 times */
    static const uint8_t ikm[22] = {
        0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b,
        0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b,
        0x0b, 0x0b, 0x0b, 0x0b, 0x0b, 0x0b
    };

    /* Salt */
    static const uint8_t salt[13] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,
        0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c
    };

    /* Info */
    static const uint8_t info[10] = {
        0xf0, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7,
        0xf8, 0xf9
    };

    /* Expected PRK (32 bytes) */
    static const uint8_t expected_prk[32] = {
        0x07, 0x77, 0x09, 0x36, 0x2c, 0x2e, 0x32, 0xdf,
        0x0d, 0xdc, 0x3f, 0x0d, 0xc4, 0x7b, 0xba, 0x63,
        0x90, 0xb6, 0xc7, 0x3b, 0xb5, 0x0f, 0x9c, 0x31,
        0x22, 0xec, 0x84, 0x4a, 0xd7, 0xc2, 0xb3, 0xe5
    };

    /* Expected OKM (42 bytes) */
    static const uint8_t expected_okm[42] = {
        0x3c, 0xb2, 0x5f, 0x25, 0xfa, 0xac, 0xd5, 0x7a,
        0x90, 0x43, 0x4f, 0x64, 0xd0, 0x36, 0x2f, 0x2a,
        0x2d, 0x2d, 0x0a, 0x90, 0xcf, 0x1a, 0x5a, 0x4c,
        0x5d, 0xb0, 0x2d, 0x56, 0xec, 0xc4, 0xc5, 0xbf,
        0x34, 0x00, 0x72, 0x08, 0xd5, 0xb8, 0x87, 0x18,
        0x58, 0x65
    };

    uint8_t prk[32];
    uint8_t okm[42];
    int rc;

    /* HKDF-Extract */
    vos3_hkdf_extract(salt, 13, ikm, 22, prk);
    TLS_ASSERT(buf_equal(prk, expected_prk, 32),
               "T7: HKDF-Extract PRK matches RFC 5869 expected");

    /* HKDF-Expand */
    rc = vos3_hkdf_expand(prk, 32, info, 10, okm, 42);
    TLS_ASSERT(rc == 0, "T7: HKDF-Expand returns success");
    TLS_ASSERT(buf_equal(okm, expected_okm, 42),
               "T7: HKDF-Expand OKM matches RFC 5869 expected");
}

/* ============================================================================
 * TEST 8: CACHE WIPE EFFECTIVENESS
 *
 * Verifies that vos3_cache_wipe() zeros a buffer completely. This ensures
 * the volatile zeroing implementation is not optimized away by the compiler.
 * ============================================================================ */

static void test_cache_wipe(void)
{
    VOS3_INFO("[TLS-TEST] === Test 8: Cache Wipe Effectiveness ===");

    uint8_t buf[256];

    /* Fill with pattern 0xAA */
    memset(buf, 0xAA, 256);

    /* Verify fill took effect */
    TLS_ASSERT(buf_all_equal(buf, 0xAA, 256), "T8: Buffer filled with 0xAA");

    /* Wipe the buffer */
    vos3_cache_wipe(buf, 256);

    /* Verify every byte is zero */
    TLS_ASSERT(buf_all_equal(buf, 0x00, 256),
               "T8: Cache wipe zeroed all 256 bytes");
}

/* ============================================================================
 * TEST 9: FPU GUARD STATE ISOLATION
 *
 * Verifies that vos3_fpu_begin()/vos3_fpu_end() correctly saves and
 * restores XMM register state, preventing crypto operations from
 * corrupting AI inference register state.
 *
 * NOTE: This test requires -msse -msse2 compilation flags.
 * ============================================================================ */

static void test_fpu_guard_isolation(void)
{
    VOS3_INFO("[TLS-TEST] === Test 9: FPU Guard State Isolation ===");

    /*
     * We use .byte-encoded SSE instructions to write/read XMM0 directly.
     * This avoids requiring the compiler to generate SSE code elsewhere.
     *
     * Strategy:
     *   1. Write a known 128-bit pattern (0x4141...41) into XMM0
     *   2. Call vos3_fpu_begin() -- saves current FPU state
     *   3. Write a different pattern (0x4242...42) into XMM0 (simulating AES-NI)
     *   4. Call vos3_fpu_end() -- restores saved FPU state
     *   5. Read XMM0 and verify it contains the ORIGINAL pattern
     */

    uint8_t original[16] __attribute__((aligned(16)));
    uint8_t crypto_pattern[16] __attribute__((aligned(16)));
    uint8_t readback[16] __attribute__((aligned(16)));

    /* Prepare patterns */
    memset(original, 0x41, 16);       /* 'AAAA...' */
    memset(crypto_pattern, 0x42, 16); /* 'BBBB...' */

    /* Step 1: Load original pattern into XMM0 */
    __asm__ volatile(
        "movdqa (%0), %%xmm0"
        :
        : "r"(original)
        : "xmm0"
    );

    /* Step 2: FPU begin -- saves XMM state */
    vos3_fpu_begin();

    /* Step 3: Overwrite XMM0 with crypto pattern (simulating AES-NI work) */
    __asm__ volatile(
        "movdqa (%0), %%xmm0"
        :
        : "r"(crypto_pattern)
        : "xmm0"
    );

    /* Step 4: FPU end -- restores original XMM state */
    vos3_fpu_end();

    /* Step 5: Read back XMM0 */
    __asm__ volatile(
        "movdqa %%xmm0, (%0)"
        :
        : "r"(readback)
        : "memory"
    );

    /* Verify the original pattern was restored */
    TLS_ASSERT(buf_equal(readback, original, 16),
               "T9: XMM0 restored to original pattern after fpu_end()");
}

/* ============================================================================
 * TEST 10: AES-GCM DETERMINISM (SAME INPUT -> SAME OUTPUT)
 *
 * Verifies that encrypting the same plaintext with the same key, IV, and
 * AAD produces bit-identical ciphertext and tag on every invocation.
 * This is a critical property for correctness and reproducibility.
 * ============================================================================ */

static void test_aes_gcm_determinism(void)
{
    VOS3_INFO("[TLS-TEST] === Test 10: AES-GCM Determinism ===");

    static const uint8_t key[32] = {
        0x60, 0x3d, 0xeb, 0x10, 0x15, 0xca, 0x71, 0xbe,
        0x2b, 0x73, 0xae, 0xf0, 0x85, 0x7d, 0x77, 0x81,
        0x1f, 0x35, 0x2c, 0x07, 0x3b, 0x61, 0x08, 0xd7,
        0x2d, 0x98, 0x10, 0xa3, 0x09, 0x14, 0xdf, 0xf4
    };

    static const uint8_t iv[12] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05,
        0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b
    };

    static const uint8_t aad[12] = {
        0x4d, 0x23, 0xc3, 0xce, 0xc3, 0x34, 0xb4, 0x9b,
        0xdb, 0x37, 0x0c, 0x43
    };

    static const uint8_t plaintext[32] = {
        0x6b, 0xc1, 0xbe, 0xe2, 0x2e, 0x40, 0x9f, 0x96,
        0xe9, 0x3d, 0x7e, 0x11, 0x73, 0x93, 0x17, 0x2a,
        0xae, 0x2d, 0x8a, 0x57, 0x1e, 0x03, 0xac, 0x9c,
        0x9e, 0xb7, 0x6f, 0xac, 0x45, 0xaf, 0x8e, 0x51
    };

    uint8_t ct1[32], ct2[32];
    uint8_t tag1[16], tag2[16];
    int rc;

    vos3_aes_gcm_ctx_t ctx;
    rc = vos3_aes_gcm_init(&ctx, key, 32);
    TLS_ASSERT(rc == 0, "T10: AES-GCM init");

    /* First encryption */
    rc = vos3_aes_gcm_encrypt(&ctx, iv, aad, 12,
                               plaintext, 32, ct1, tag1);
    TLS_ASSERT(rc == 0, "T10: First encryption succeeds");

    /* Second encryption with identical inputs */
    rc = vos3_aes_gcm_encrypt(&ctx, iv, aad, 12,
                               plaintext, 32, ct2, tag2);
    TLS_ASSERT(rc == 0, "T10: Second encryption succeeds");

    /* Both ciphertexts must be identical */
    TLS_ASSERT(buf_equal(ct1, ct2, 32),
               "T10: Ciphertexts are bit-identical");

    /* Both tags must be identical */
    TLS_ASSERT(buf_equal(tag1, tag2, 16),
               "T10: Tags are bit-identical");

    vos3_aes_gcm_destroy(&ctx);
}

/* ============================================================================
 * PUBLIC API — Main test runner
 * ============================================================================ */

/**
 * @brief Run all TLS 1.3 crypto integrity tests.
 *
 * Executes 10 test cases covering AES-GCM, X25519, HKDF, cache wipe,
 * and FPU guard functionality. Results are logged via VOS3_INFO/VOS3_ERROR
 * and a final summary is printed.
 */
void vos3_tls_integrity_test(void)
{
    VOS3_INFO("[TLS-TEST] ====================================================");
    VOS3_INFO("[TLS-TEST] TLS 1.3 Crypto Integrity Test Suite — Starting");
    VOS3_INFO("[TLS-TEST] ====================================================");

    g_tls_pass = 0;
    g_tls_fail = 0;

    /* Test 1: AES-GCM tag forgery rejection */
    test_aes_gcm_tag_forgery();

    /* Test 2: AES-GCM ciphertext tampering */
    test_aes_gcm_ciphertext_tamper();

    /* Test 3: AES-GCM empty plaintext edge case */
    test_aes_gcm_empty_plaintext();

    /* Test 4: X25519 key exchange round-trip */
    test_x25519_roundtrip();

    /* Test 5: X25519 small-subgroup rejection */
    test_x25519_small_subgroup();

    /* Test 6: X25519 RFC 7748 known vector */
    test_x25519_rfc7748_vector();

    /* Test 7: HKDF-SHA256 RFC 5869 known vector */
    test_hkdf_rfc5869_vector();

    /* Test 8: Cache wipe effectiveness */
    test_cache_wipe();

    /* Test 9: FPU guard state isolation */
    test_fpu_guard_isolation();

    /* Test 10: AES-GCM determinism */
    test_aes_gcm_determinism();

    /* Summary */
    VOS3_INFO("[TLS-TEST] ====================================================");
    VOS3_INFO("[TLS-TEST] Results: %u/10 PASS, %u/10 FAIL",
              g_tls_pass, g_tls_fail);
    VOS3_INFO("[TLS-TEST] ====================================================");

    if (g_tls_fail == 0) {
        VOS3_INFO("[TLS-TEST] ALL TESTS PASSED — Crypto integrity verified");
    } else {
        VOS3_ERROR("[TLS-TEST] %u TESTS FAILED — Crypto integrity COMPROMISED",
                   g_tls_fail);
    }
}

#else
typedef int _vos3_production_stub;  /* ISO C requires non-empty TU */
#endif /* \!VOS3_PRODUCTION_BUILD */
