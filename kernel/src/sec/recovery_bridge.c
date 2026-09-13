/**
 * @file recovery_bridge.c
 * @brief VOS3 Recovery Bridge — HMAC Challenge-Response Quarantine Recovery
 *
 * @details Provides secure recovery from Sovereign Quarantine:
 *          1. Caller retrieves current nonce via recovery_get_nonce()
 *          2. Caller computes HMAC-SHA256(sovereign_key, nonce)
 *          3. Caller submits response via recovery_attempt()
 *          4. On match + .text re-verify: restore from snapshots
 *          5. On failure: nonce rotated, attempt logged
 *
 * @version 1.0.0
 * @date 2026-04-13
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 6.1: Immutable Source & Business Continuity Hardening
 */

#include "../../include/vos/recovery_bridge.h"
#include "../../include/vos/immutable_lock.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * STATIC STATE (BSS)
 * ============================================================================ */

static uint8_t         g_recovery_key[32];
static uint8_t         g_recovery_nonce[32];
static uint8_t         g_recovery_key_set;
static uint8_t         g_nonce_valid;
static recovery_stats_t g_recovery_stats;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void rb_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) p[i] = 0;
}

static void rb_memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; i++) d[i] = s[i];
}

/**
 * @brief Constant-time compare via XOR accumulate.
 */
static int rb_memcmp_ct(const uint8_t *a, const uint8_t *b, size_t len)
{
    uint8_t diff = 0;
    for (size_t i = 0; i < len; i++) {
        diff |= a[i] ^ b[i];
    }
    return (int)diff;
}

/**
 * @brief Rotate nonce using entropy subsystem.
 */
static void rb_rotate_nonce(void)
{
    vos3_entropy_extract(g_recovery_nonce, 32);
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize recovery bridge with a sovereign key.
 */
int recovery_init(const uint8_t sovereign_key[32])
{
    if (sovereign_key == NULL) return -22; /* EINVAL */

    rb_memcpy(g_recovery_key, sovereign_key, 32);
    g_recovery_key_set = 1;

    /* Generate initial nonce */
    vos3_entropy_extract(g_recovery_nonce, 32);
    g_nonce_valid = 1;

    rb_memzero(&g_recovery_stats, sizeof(g_recovery_stats));
    g_recovery_stats.key_initialized = 1;

    VOS3_INFO("[RECOVERY] Bridge initialized with sovereign key");
    return 0;
}

/**
 * @brief Get the current challenge nonce.
 */
int recovery_get_nonce(uint8_t nonce_out[32])
{
    if (nonce_out == NULL) return -22;
    if (!g_recovery_key_set || !g_nonce_valid) return -22;

    rb_memcpy(nonce_out, g_recovery_nonce, 32);
    return 0;
}

/**
 * @brief Attempt quarantine recovery via HMAC challenge-response.
 *
 * @param challenge_response HMAC-SHA256(sovereign_key, nonce) computed by caller
 * @return 0 on success (restored to OPERATIONAL)
 * @return -22 (EINVAL) if not quarantined or not initialized
 * @return -1 (EPERM) if HMAC mismatch
 * @return -5 (EIO) if .text still corrupted after valid HMAC
 */
int recovery_attempt(const uint8_t challenge_response[32])
{
    if (!g_recovery_key_set || !g_nonce_valid) return -22;
    if (challenge_response == NULL) return -22;
    if (!guardian_is_quarantined()) return -22;

    g_recovery_stats.attempts++;
    g_recovery_stats.last_attempt_tsc = vos3_rdtsc();

    /* Compute expected response: HMAC-SHA256(key, nonce) */
    uint8_t expected[32];
    vos3_hmac_sha256(g_recovery_key, 32,
                     g_recovery_nonce, 32,
                     expected);

    /* Constant-time compare */
    if (rb_memcmp_ct(expected, challenge_response, 32) != 0) {
        g_recovery_stats.failures++;
        rb_rotate_nonce(); /* Rotate nonce after failure */
        VOS3_WARN("[RECOVERY] Challenge-response FAILED — nonce rotated");
        return -1; /* EPERM */
    }

    /* HMAC valid — transition to RECOVERY state */
    VOS3_INFO("[RECOVERY] Challenge-response valid — verifying .text integrity");

    /* Re-verify .text before restoring */
    int verify_rc = guardian_verify_text();
    if (verify_rc != 0) {
        /* .text still corrupted — stay in quarantine */
        VOS3_ERROR("[RECOVERY] .text still corrupted — staying in QUARANTINE");
        rb_rotate_nonce();
        return -5; /* EIO */
    }

    /* .text is clean — restore from snapshots */
    guardian_restore_from_snapshots();

    g_recovery_stats.successes++;
    rb_rotate_nonce(); /* Always rotate after attempt */

    VOS3_INFO("[RECOVERY] *** RECOVERY SUCCESSFUL — OPERATIONAL ***");
    return 0;
}

/**
 * @brief Get recovery bridge telemetry.
 */
void recovery_get_stats(recovery_stats_t *out)
{
    if (out != NULL) {
        rb_memcpy(out, &g_recovery_stats, sizeof(g_recovery_stats));
    }
}
