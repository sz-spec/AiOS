/**
 * @file immutable_lock.h
 * @brief VOS3 Guardian Seal — Immutable Source Lock + Sovereign Quarantine
 *
 * @details Protects kernel .text from unauthorized modification via SHA-256
 *          integrity verification. On breach detection, enters Sovereign
 *          Quarantine: AI paused, PUDs locked to SOVEREIGN, data preserved.
 *
 * @version 1.0.0
 * @date 2026-04-13
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 6.1: Immutable Source & Business Continuity Hardening
 */

#ifndef VOS3_IMMUTABLE_LOCK_H
#define VOS3_IMMUTABLE_LOCK_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define GUARDIAN_HASH_SIZE   32U
#define GUARDIAN_MAX_PUDS     8U

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Guardian state machine states.
 *
 * OPERATIONAL -> QUARANTINE (on .text breach)
 * QUARANTINE  -> RECOVERY   (on valid HMAC challenge-response)
 * RECOVERY    -> OPERATIONAL (on .text re-verify pass + snapshot restore)
 */
typedef enum guardian_state {
    GUARDIAN_STATE_OPERATIONAL = 0U,
    GUARDIAN_STATE_QUARANTINE  = 1U,
    GUARDIAN_STATE_RECOVERY    = 2U,
} guardian_state_t;

/**
 * @brief Guardian telemetry.
 */
typedef struct guardian_stats {
    uint64_t         verification_count;
    uint32_t         violations_detected;
    uint32_t         quarantine_entries;
    uint64_t         last_verify_tsc;
    uint64_t         last_verify_cycles;
    guardian_state_t current_state;
} guardian_stats_t;

/**
 * @brief Snapshot of a PUD's level and active state for quarantine recovery.
 */
typedef struct guardian_pud_snapshot {
    uint8_t level;
    uint8_t active;
} guardian_pud_snapshot_t;

/* ============================================================================
 * API
 * ============================================================================ */

int              guardian_init(void);
int              guardian_verify_text(void);
void             guardian_enter_quarantine(void);
void             guardian_self_audit(void);
void             guardian_get_stats(guardian_stats_t *out);
guardian_state_t guardian_get_state(void);
int              guardian_is_quarantined(void);
void             guardian_restore_from_snapshots(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_IMMUTABLE_LOCK_H */
