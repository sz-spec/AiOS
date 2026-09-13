/**
 * @file recovery_bridge.h
 * @brief VOS3 Recovery Bridge — HMAC Challenge-Response Quarantine Recovery
 *
 * @details Provides a secure recovery path from Sovereign Quarantine via
 *          HMAC-SHA256 challenge-response. On success, restores model slots
 *          and PUD levels from pre-quarantine snapshots.
 *
 * @version 1.0.0
 * @date 2026-04-13
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 6.1: Immutable Source & Business Continuity Hardening
 */

#ifndef VOS3_RECOVERY_BRIDGE_H
#define VOS3_RECOVERY_BRIDGE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Recovery bridge telemetry.
 */
typedef struct recovery_stats {
    uint32_t attempts;
    uint32_t successes;
    uint32_t failures;
    uint64_t last_attempt_tsc;
    uint8_t  key_initialized;
} recovery_stats_t;

/* ============================================================================
 * API
 * ============================================================================ */

int  recovery_init(const uint8_t sovereign_key[32]);
int  recovery_get_nonce(uint8_t nonce_out[32]);
int  recovery_attempt(const uint8_t challenge_response[32]);
void recovery_get_stats(recovery_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_RECOVERY_BRIDGE_H */
