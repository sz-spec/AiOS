/**
 * @file hybrid_key.c
 * @brief VOS3 Hybrid Identity Key — implementation
 *
 * @details Implements
 *
 *              K_id = HMAC-SHA256( K_tpm_secret , K_keyboard_entropy )
 *
 *          per hybrid_key.h. Two independent failure domains contribute
 *          to K_id; an attacker must compromise both to predict it.
 *
 * @version 1.0.0
 * @date 2026-05-04
 */

#include "hybrid_key.h"
#include "tpm2.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/console.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Local helpers (freestanding — no libc)
 * ============================================================================ */

static void hk_memzero(void *dst, size_t n)
{
    volatile uint8_t *p = (volatile uint8_t *)dst;
    while (n--) *p++ = 0;
}

static void hk_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
}

/* ============================================================================
 * Cached key state
 * ============================================================================ */

static struct {
    uint8_t key[VOS3_HYBRID_KEY_SIZE];
    uint8_t valid;
} g_hybrid;

/* ============================================================================
 * Core derivation
 * ============================================================================ */

int vos3_hybrid_key_derive(const uint8_t *kbd_entropy,
                           size_t kbd_len,
                           uint8_t out[VOS3_HYBRID_KEY_SIZE])
{
    if (!out) return -1;
    hk_memzero(out, VOS3_HYBRID_KEY_SIZE);

    if (!kbd_entropy || kbd_len < VOS3_HYBRID_KEY_MIN_KBD_BYTES) {
        VOS3_WARN("[HYBRID_KEY] insufficient keyboard entropy: %u < %u",
                  (unsigned)kbd_len, (unsigned)VOS3_HYBRID_KEY_MIN_KBD_BYTES);
        return -1;
    }

    if (!tpm2_is_present()) {
        VOS3_WARN("[HYBRID_KEY] TPM not present — cannot derive K_tpm_secret");
        return -1;
    }

    /* 1. Pull K_tpm_secret from the TPM's true RNG. We treat this buffer
     *    as sensitive — wipe before return regardless of outcome. */
    uint8_t tpm_secret[VOS3_HYBRID_KEY_SIZE];
    if (tpm2_get_random(tpm_secret, VOS3_HYBRID_KEY_SIZE) != 0) {
        VOS3_WARN("[HYBRID_KEY] tpm2_get_random failed");
        hk_memzero(tpm_secret, sizeof(tpm_secret));
        return -1;
    }

    /* 2. Compute K_id = HMAC-SHA256(K_tpm_secret, K_keyboard_entropy).
     *    Per hybrid_key.h: K_tpm_secret is the HMAC KEY (the secret),
     *    K_keyboard_entropy is the HMAC MESSAGE (the public-ish input).
     *    Using the high-entropy TPM bytes as the key makes the PRF
     *    secure even if keyboard entropy is partially predictable. */
    vos3_hmac_sha256(tpm_secret, VOS3_HYBRID_KEY_SIZE,
                     kbd_entropy, kbd_len,
                     out);

    /* 3. Best-effort wipe of the TPM-derived secret. */
    hk_memzero(tpm_secret, sizeof(tpm_secret));
    return 0;
}

/* ============================================================================
 * Cached-install API
 * ============================================================================ */

int vos3_hybrid_key_install(const uint8_t *kbd_entropy, size_t kbd_len)
{
    uint8_t tmp[VOS3_HYBRID_KEY_SIZE];
    if (vos3_hybrid_key_derive(kbd_entropy, kbd_len, tmp) != 0) {
        hk_memzero(&g_hybrid, sizeof(g_hybrid));
        return -1;
    }
    hk_memcpy(g_hybrid.key, tmp, VOS3_HYBRID_KEY_SIZE);
    g_hybrid.valid = 1U;
    hk_memzero(tmp, sizeof(tmp));
    VOS3_INFO("[HYBRID_KEY] installed (HMAC-SHA256 of TPM secret × keyboard entropy)");
    return 0;
}

int vos3_hybrid_key_get(uint8_t out[VOS3_HYBRID_KEY_SIZE])
{
    if (!out) return -1;
    if (!g_hybrid.valid) {
        hk_memzero(out, VOS3_HYBRID_KEY_SIZE);
        return -1;
    }
    hk_memcpy(out, g_hybrid.key, VOS3_HYBRID_KEY_SIZE);
    return 0;
}

void vos3_hybrid_key_clear(void)
{
    hk_memzero(&g_hybrid, sizeof(g_hybrid));
}
