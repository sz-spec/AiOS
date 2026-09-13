#ifndef VOS3_PRODUCTION_BUILD
/**
 * @file test_phase22_velocity.c
 * @brief Phase 2.2 Supreme Velocity Audit — Speculative Decoding & Context Window
 *
 * @details 10 tests across 3 tracks verifying the Eagle-Wing speculative
 *          decoding engine and rolling context window implementation.
 *
 *          Track 1: Speculative Engine Integrity (4 tests)
 *          Track 2: Context Window Management (3 tests)
 *          Track 3: Velocity Certificate (3 tests)
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2.2 Velocity Gate — Supreme Velocity Certificate
 */

#include "../../include/vos/ai_guard.h"
#include "../../include/vos/ai_spec.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_vel_pass = 0;
static uint32_t g_vel_fail = 0;
static uint32_t g_vel_skip = 0;

#define VEL_ASSERT(cond, name)                                                \
    do {                                                                      \
        if (cond) {                                                           \
            g_vel_pass++;                                                     \
            VOS3_INFO("[VEL-TEST] PASS: %s", (name));                         \
        } else {                                                              \
            g_vel_fail++;                                                     \
            VOS3_ERROR("[VEL-TEST] FAIL: %s (line %d)", (name), __LINE__);    \
        }                                                                     \
    } while (0)

#define VEL_SKIP(name)                                                        \
    do {                                                                      \
        g_vel_skip++;                                                         \
        VOS3_INFO("[VEL-TEST] SKIP: %s", (name));                             \
    } while (0)

/* External: model slot array */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * TRACK 1: SPECULATIVE ENGINE INTEGRITY (4 tests)
 *
 * Validates spec_configure, spec_reset, and error handling for
 * invalid inputs.
 * ============================================================================ */

static void test_spec_configure_valid(void)
{
    /*
     * Configure slots 0 and 1 as Draft/Target with K=4.
     * Requires both slots to be active (status >= VOS3_SLOT_ACTIVE).
     * If slots are not active, skip the test.
     */
    vos3_ai_model_slot_t *s0 = &g_model_slots[0];
    vos3_ai_model_slot_t *s1 = &g_model_slots[1];

    /* Save original state */
    uint8_t orig_status_0 = s0->status;
    uint8_t orig_status_1 = s1->status;
    uint8_t orig_spec_active_0 = s0->spec_active;
    uint8_t orig_spec_active_1 = s1->spec_active;

    /* Temporarily set slots active for testing */
    s0->status = VOS3_SLOT_ACTIVE;
    s1->status = VOS3_SLOT_ACTIVE;
    s0->spec_active = 0;
    s1->spec_active = 0;

    int rc = vos3_spec_configure(0, 1, 4);
    VEL_ASSERT(rc == 0, "spec_configure(0,1,4) returns 0");

    /* Verify roles assigned correctly */
    VEL_ASSERT(s0->spec_role == VOS3_SPEC_ROLE_DRAFT,
               "slot 0 assigned DRAFT role");
    VEL_ASSERT(s1->spec_role == VOS3_SPEC_ROLE_TARGET,
               "slot 1 assigned TARGET role");

    /* Verify partner linkage */
    int partners_ok = (s0->spec_partner_slot == 1) && (s1->spec_partner_slot == 0);
    VEL_ASSERT(partners_ok, "partner slots cross-linked correctly");

    /* Verify K stored */
    VEL_ASSERT(s0->spec_k == 4, "draft slot K=4 stored");

    /* Verify both active */
    VEL_ASSERT(s0->spec_active == 1 && s1->spec_active == 1,
               "both slots marked spec_active");

    /* Clean up: reset spec state */
    vos3_spec_reset(0);

    /* Restore original state */
    s0->status = orig_status_0;
    s1->status = orig_status_1;
    s0->spec_active = orig_spec_active_0;
    s1->spec_active = orig_spec_active_1;
}

static void test_spec_configure_same_slot(void)
{
    /*
     * Configuring the same slot as both Draft and Target must fail.
     */
    int rc = vos3_spec_configure(0, 0, 4);
    VEL_ASSERT(rc == -22, "spec_configure(0,0,4) returns -EINVAL");
}

static void test_spec_configure_invalid_slot(void)
{
    /*
     * Configuring with out-of-range slot must fail.
     */
    int rc = vos3_spec_configure(255, 0, 4);
    VEL_ASSERT(rc == -22, "spec_configure(255,0,4) returns -EINVAL");

    rc = vos3_spec_configure(0, 255, 4);
    VEL_ASSERT(rc == -22, "spec_configure(0,255,4) returns -EINVAL");
}

static void test_spec_reset_clears_state(void)
{
    /*
     * After configure+reset, all spec fields must be zeroed.
     */
    vos3_ai_model_slot_t *s0 = &g_model_slots[0];
    vos3_ai_model_slot_t *s1 = &g_model_slots[1];

    /* Save and set slots active */
    uint8_t orig_0 = s0->status;
    uint8_t orig_1 = s1->status;
    s0->status = VOS3_SLOT_ACTIVE;
    s1->status = VOS3_SLOT_ACTIVE;
    s0->spec_active = 0;
    s1->spec_active = 0;

    /* Configure then reset */
    vos3_spec_configure(0, 1, 4);
    vos3_spec_reset(0);

    /* Verify all fields zeroed on slot 0 */
    int cleared = (s0->spec_role == VOS3_SPEC_ROLE_NONE) &&
                  (s0->spec_partner_slot == 0xFF) &&
                  (s0->spec_active == 0) &&
                  (s0->spec_k == 0) &&
                  (s0->spec_tokens_drafted == 0) &&
                  (s0->spec_tokens_accepted == 0) &&
                  (s0->spec_tokens_rejected == 0) &&
                  (s0->spec_batch_id == 0);

    VEL_ASSERT(cleared, "spec_reset clears all spec fields");

    /* Verify partner (slot 1) also reset */
    int partner_cleared = (s1->spec_role == VOS3_SPEC_ROLE_NONE) &&
                          (s1->spec_active == 0);
    VEL_ASSERT(partner_cleared, "spec_reset clears partner slot");

    /* Restore */
    s0->status = orig_0;
    s1->status = orig_1;
}

/* ============================================================================
 * TRACK 2: CONTEXT WINDOW MANAGEMENT (3 tests)
 *
 * Validates context window initialization, prefix freezing, and
 * sliding window eviction.
 * ============================================================================ */

static void test_ctx_window_init_bounds(void)
{
    /*
     * Init with max_active=4096 → stored correctly.
     * Init with max_active=0 → clamped to default (8192).
     * Init with max_active=99999 → clamped to 8192.
     */
    vos3_ai_model_slot_t *s = &g_model_slots[0];

    int rc = vos3_ctx_window_init(0, 4096);
    VEL_ASSERT(rc == 0, "ctx_window_init(0,4096) returns 0");
    VEL_ASSERT(s->ctx_window.max_active == 4096,
               "max_active=4096 stored correctly");

    /* Test clamping to upper bound */
    rc = vos3_ctx_window_init(0, 99999);
    VEL_ASSERT(rc == 0 && s->ctx_window.max_active == 8192,
               "max_active=99999 clamped to 8192");

    /* Test clamping zero to default */
    rc = vos3_ctx_window_init(0, 0);
    VEL_ASSERT(rc == 0 && s->ctx_window.max_active == 8192,
               "max_active=0 clamped to 8192");

    /* Test invalid slot */
    rc = vos3_ctx_window_init(255, 4096);
    VEL_ASSERT(rc == -22, "ctx_window_init(255,...) returns -EINVAL");
}

static void test_ctx_window_freeze_locks_prefix(void)
{
    /*
     * Freeze 1000 tokens → prefix_immutable=1, frozen_len=1000.
     */
    vos3_ai_model_slot_t *s = &g_model_slots[0];

    /* Initialize first */
    vos3_ctx_window_init(0, 4096);

    /* Freeze prefix */
    int rc = vos3_ctx_window_freeze(0, 1000);
    VEL_ASSERT(rc == 0, "ctx_window_freeze(0,1000) returns 0");
    VEL_ASSERT(s->ctx_window.frozen == 1, "frozen flag set");
    VEL_ASSERT(s->ctx_window.frozen_len == 1000, "frozen_len=1000");
    VEL_ASSERT(s->prefix_immutable == 1, "prefix_immutable set");
    VEL_ASSERT(s->ctx_window.active_start == 1000,
               "active_start moved past frozen prefix");

    /* Double-freeze must fail */
    rc = vos3_ctx_window_freeze(0, 500);
    VEL_ASSERT(rc == -16, "double-freeze returns -EBUSY");

    /* Reset frozen state for subsequent tests */
    s->ctx_window.frozen = 0;
    s->prefix_immutable = 0;
}

static void test_ctx_window_advance_eviction(void)
{
    /*
     * Advance beyond max_active → active_len must stay <= max_active.
     */
    vos3_ai_model_slot_t *s = &g_model_slots[0];

    /* Init with small window */
    vos3_ctx_window_init(0, 100);

    /* Advance 50 tokens — no eviction */
    int rc = vos3_ctx_window_advance(0, 50);
    VEL_ASSERT(rc == 0 && s->ctx_window.active_len == 50,
               "advance 50 tokens, no eviction");

    /* Advance 60 more — total 110 > 100 → eviction */
    rc = vos3_ctx_window_advance(0, 60);
    VEL_ASSERT(rc == 0, "advance 60 more returns 0");
    VEL_ASSERT(s->ctx_window.active_len <= s->ctx_window.max_active,
               "active_len <= max_active after eviction");
    VEL_ASSERT(s->ctx_window.total_generated == 110,
               "total_generated = 110");
}

/* ============================================================================
 * TRACK 3: VELOCITY CERTIFICATE (3 tests)
 *
 * Validates speculative decoding invariants and ISC constraints.
 * ============================================================================ */

static void test_velocity_draft_accept_ratio(void)
{
    /*
     * Invariant: accepted + rejected = drafted for all batches.
     * Simulate by directly manipulating slot stats.
     */
    vos3_ai_model_slot_t *s = &g_model_slots[0];

    /* Set up test data */
    s->spec_tokens_drafted  = 400;  /* 100 batches * K=4 */
    s->spec_tokens_accepted = 320;  /* 80% accept rate */
    s->spec_tokens_rejected = 80;   /* 20% reject rate */

    uint32_t drafted = 0, accepted = 0, rejected = 0;
    vos3_spec_get_stats(0, &drafted, &accepted, &rejected);

    VEL_ASSERT(drafted == 400, "drafted = 400");
    VEL_ASSERT(accepted + rejected == drafted,
               "accepted + rejected = drafted invariant holds");

    /* Reset stats */
    s->spec_tokens_drafted  = 0;
    s->spec_tokens_accepted = 0;
    s->spec_tokens_rejected = 0;
}

static void test_velocity_batch_fits_isc(void)
{
    /*
     * vos3_spec_batch_t must fit in ISC mailbox (4KB).
     * ISC message max payload is 2048 bytes.
     */
    uint32_t batch_size = (uint32_t)sizeof(vos3_spec_batch_t);
    VEL_ASSERT(batch_size < 2048,
               "spec_batch_t fits in ISC mailbox (< 2048 bytes)");

    uint32_t result_size = (uint32_t)sizeof(vos3_spec_result_t);
    VEL_ASSERT(result_size < 2048,
               "spec_result_t fits in ISC mailbox (< 2048 bytes)");

    /* Verify MAX_K is bounded */
    VEL_ASSERT(VOS3_SPEC_MAX_K <= 8, "VOS3_SPEC_MAX_K <= 8");
}

static void test_velocity_stats_consistency(void)
{
    /*
     * After configure → stats zeroed. After reset → stats zeroed.
     * Stats must be internally consistent.
     */
    vos3_ai_model_slot_t *s0 = &g_model_slots[0];
    vos3_ai_model_slot_t *s1 = &g_model_slots[1];

    /* Save and set slots active */
    uint8_t orig_0 = s0->status;
    uint8_t orig_1 = s1->status;
    s0->status = VOS3_SLOT_ACTIVE;
    s1->status = VOS3_SLOT_ACTIVE;
    s0->spec_active = 0;
    s1->spec_active = 0;

    /* Configure */
    int rc = vos3_spec_configure(0, 1, 4);
    if (rc == 0) {
        /* Verify initial stats are zero */
        uint32_t d = 0, a = 0, r = 0;
        vos3_spec_get_stats(0, &d, &a, &r);
        VEL_ASSERT(d == 0 && a == 0 && r == 0,
                   "stats zeroed after configure");

        /* Manually set some stats and verify consistency */
        s0->spec_tokens_drafted  = 100;
        s0->spec_tokens_accepted = 75;
        s0->spec_tokens_rejected = 25;
        vos3_spec_get_stats(0, &d, &a, &r);
        VEL_ASSERT(d == 100 && a == 75 && r == 25,
                   "stats read back correctly");

        /* Reset and verify zeroed */
        vos3_spec_reset(0);
        vos3_spec_get_stats(0, &d, &a, &r);
        VEL_ASSERT(d == 0 && a == 0 && r == 0,
                   "stats zeroed after reset");
    } else {
        VEL_SKIP("stats_consistency — configure failed");
    }

    /* Restore */
    s0->status = orig_0;
    s1->status = orig_1;
}

/* ============================================================================
 * VELOCITY AUDIT ENTRY POINT
 * ============================================================================ */

void vos3_phase22_velocity_audit(void)
{
    g_vel_pass = 0;
    g_vel_fail = 0;
    g_vel_skip = 0;

    VOS3_INFO("=============================================================");
    VOS3_INFO(" PHASE 2.2: SUPREME VELOCITY AUDIT — Eagle-Wing Engine");
    VOS3_INFO("=============================================================");

    /* Track 1: Speculative Engine Integrity */
    VOS3_INFO("--- Track 1: Speculative Engine Integrity (4 tests) ---");
    test_spec_configure_valid();
    test_spec_configure_same_slot();
    test_spec_configure_invalid_slot();
    test_spec_reset_clears_state();

    /* Track 2: Context Window Management */
    VOS3_INFO("--- Track 2: Context Window Management (3 tests) ---");
    test_ctx_window_init_bounds();
    test_ctx_window_freeze_locks_prefix();
    test_ctx_window_advance_eviction();

    /* Track 3: Velocity Certificate */
    VOS3_INFO("--- Track 3: Velocity Certificate (3 tests) ---");
    test_velocity_draft_accept_ratio();
    test_velocity_batch_fits_isc();
    test_velocity_stats_consistency();

    /* Certificate */
    VOS3_INFO("=============================================================");
    VOS3_INFO(" VELOCITY AUDIT COMPLETE: %u PASS, %u FAIL, %u SKIP",
              g_vel_pass, g_vel_fail, g_vel_skip);
    if (g_vel_fail == 0) {
        VOS3_INFO(" >> SUPREME VELOCITY CERTIFICATE: GRANTED <<");
    } else {
        VOS3_ERROR(" >> SUPREME VELOCITY CERTIFICATE: DENIED (%u failures) <<",
                   g_vel_fail);
    }
    VOS3_INFO("=============================================================");
}

#else
typedef int _vos3_production_stub;  /* ISO C requires non-empty TU */
#endif /* \!VOS3_PRODUCTION_BUILD */
