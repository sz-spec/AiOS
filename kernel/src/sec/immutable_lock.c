/**
 * @file immutable_lock.c
 * @brief VOS3 Guardian Seal — Immutable .text Lock + Sovereign Quarantine
 *
 * @details On boot, computes SHA-256 of the kernel .text section and stores
 *          the hash as the Guardian Boot Hash. Subsequent verification calls
 *          recompute the hash and constant-time compare. On mismatch, enters
 *          Sovereign Quarantine: all model slots set FREE, all PUDs forced
 *          to SOVEREIGN, data preserved, recovery via HMAC challenge-response.
 *
 * @version 1.0.0
 * @date 2026-04-13
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 6.1: Immutable Source & Business Continuity Hardening
 */

#include "../../include/vos/immutable_lock.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/vspace.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * LINKER SYMBOLS
 * ============================================================================ */

extern uint8_t _text_start[];
extern uint8_t _text_end[];

/* ============================================================================
 * EXTERNAL STATE
 * ============================================================================ */

extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];
extern vspace_state_t *vspace_get_state(void);

/* ============================================================================
 * STATIC STATE (BSS)
 * ============================================================================ */

static uint8_t          g_guardian_boot_hash[GUARDIAN_HASH_SIZE];
static uint8_t          g_guardian_initialized;
static uint8_t          g_quarantine_active;
static guardian_state_t g_guardian_state;
static guardian_stats_t g_guardian_stats;
static guardian_pud_snapshot_t g_pud_snapshots[GUARDIAN_MAX_PUDS];
static uint8_t          g_slot_snapshots[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void il_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) p[i] = 0;
}

static void il_memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; i++) d[i] = s[i];
}

/**
 * @brief Constant-time compare via XOR accumulate.
 * @return 0 if equal, non-zero if different
 */
static int il_memcmp_ct(const uint8_t *a, const uint8_t *b, size_t len)
{
    uint8_t diff = 0;
    for (size_t i = 0; i < len; i++) {
        diff |= a[i] ^ b[i];
    }
    return (int)diff;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize Guardian Seal by computing the boot-time .text hash.
 */
int guardian_init(void)
{
    vos3_sha256_ctx_t ctx;
    size_t text_size = (size_t)(_text_end - _text_start);

    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, _text_start, text_size);
    vos3_sha256_final(&ctx, g_guardian_boot_hash);

    g_guardian_initialized = 1;
    g_guardian_state = GUARDIAN_STATE_OPERATIONAL;
    g_quarantine_active = 0;

    il_memzero(&g_guardian_stats, sizeof(g_guardian_stats));
    g_guardian_stats.current_state = GUARDIAN_STATE_OPERATIONAL;

    VOS3_INFO("[GUARDIAN] Seal initialized: .text size=%u bytes, "
              "hash=%02x%02x%02x%02x...",
              (unsigned)text_size,
              g_guardian_boot_hash[0], g_guardian_boot_hash[1],
              g_guardian_boot_hash[2], g_guardian_boot_hash[3]);

    return 0;
}

/**
 * @brief Verify .text integrity against boot-time hash.
 * @return 0 on success (match), -1 on mismatch (quarantine entered)
 */
int guardian_verify_text(void)
{
    if (!g_guardian_initialized) return -22; /* EINVAL */

    uint64_t tsc_start = vos3_rdtsc();

    uint8_t live_hash[GUARDIAN_HASH_SIZE];
    vos3_sha256_ctx_t ctx;
    size_t text_size = (size_t)(_text_end - _text_start);

    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, _text_start, text_size);
    vos3_sha256_final(&ctx, live_hash);

    uint64_t tsc_end = vos3_rdtsc();

    if (il_memcmp_ct(live_hash, g_guardian_boot_hash, GUARDIAN_HASH_SIZE) != 0) {
        g_guardian_stats.violations_detected++;
        VOS3_ERROR("[GUARDIAN] .text integrity VIOLATION detected!");
        guardian_enter_quarantine();
        return -1;
    }

    g_guardian_stats.verification_count++;
    g_guardian_stats.last_verify_tsc = tsc_end;
    g_guardian_stats.last_verify_cycles = tsc_end - tsc_start;

    return 0;
}

/**
 * @brief Enter Sovereign Quarantine (idempotent).
 *
 * 1. Snapshot model slot status and PUD levels
 * 2. Set all model slots to FREE
 * 3. Set all active PUDs to SOVEREIGN with clipboard isolation
 * 4. Record telemetry
 */
void guardian_enter_quarantine(void)
{
    if (g_quarantine_active) return; /* Idempotent */

    /* Snapshot model slots for later restoration */
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        g_slot_snapshots[i] = (uint8_t)g_model_slots[i].status;
    }

    /* Snapshot PUD levels */
    vspace_state_t *state = vspace_get_state();
    if (state && state->initialized) {
        for (uint8_t i = 0; i < GUARDIAN_MAX_PUDS; i++) {
            g_pud_snapshots[i].level = state->puds[i].level;
            g_pud_snapshots[i].active = state->puds[i].active;
        }

        /* Force all active PUDs to SOVEREIGN */
        for (uint8_t i = 0; i < GUARDIAN_MAX_PUDS; i++) {
            if (state->puds[i].active) {
                state->puds[i].level = PUD_LEVEL_SOVEREIGN;
                state->puds[i].clipboard_isolated = 1;
            }
        }
    }

    /* Suspend all model slots */
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        g_model_slots[i].status = VOS3_SLOT_FREE;
    }

    g_quarantine_active = 1;
    g_guardian_state = GUARDIAN_STATE_QUARANTINE;
    g_guardian_stats.quarantine_entries++;
    g_guardian_stats.current_state = GUARDIAN_STATE_QUARANTINE;

    VOS3_WARN("[GUARDIAN] *** SOVEREIGN QUARANTINE ACTIVATED ***");
    VOS3_WARN("[GUARDIAN] All AI slots suspended, PUDs locked to SOVEREIGN");
}

/**
 * @brief Restore model slots and PUDs from pre-quarantine snapshots.
 */
void guardian_restore_from_snapshots(void)
{
    /* Restore model slot status */
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        g_model_slots[i].status = (vos3_model_slot_status_t)g_slot_snapshots[i];
    }

    /* Restore PUD levels */
    vspace_state_t *state = vspace_get_state();
    if (state && state->initialized) {
        for (uint8_t i = 0; i < GUARDIAN_MAX_PUDS; i++) {
            if (state->puds[i].active && g_pud_snapshots[i].active) {
                state->puds[i].level = g_pud_snapshots[i].level;
            }
        }
    }

    g_quarantine_active = 0;
    g_guardian_state = GUARDIAN_STATE_OPERATIONAL;
    g_guardian_stats.current_state = GUARDIAN_STATE_OPERATIONAL;

    VOS3_INFO("[GUARDIAN] Restored from snapshots — OPERATIONAL");
}

/**
 * @brief Run a guardian self-audit (verify + log).
 */
void guardian_self_audit(void)
{
    if (!g_guardian_initialized) return;
    guardian_verify_text();
}

/**
 * @brief Get guardian telemetry.
 */
void guardian_get_stats(guardian_stats_t *out)
{
    if (out != NULL) {
        il_memcpy(out, &g_guardian_stats, sizeof(g_guardian_stats));
    }
}

/**
 * @brief Get current guardian state.
 */
guardian_state_t guardian_get_state(void)
{
    return g_guardian_state;
}

/**
 * @brief Check if system is in quarantine.
 * @return 1 if quarantined, 0 if operational
 */
int guardian_is_quarantined(void)
{
    return (int)g_quarantine_active;
}
