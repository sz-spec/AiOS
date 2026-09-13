/**
 * @file ghost_stream_stress.c
 * @brief Atomic streaming mask and slot state transition stress testing.
 *
 * @details Stress-tests the g_streaming_mask atomic bitmask and slot status
 *          transitions used by the AI Guard subsystem:
 *
 *   Test 1: Atomic Mask Set/Clear Linearizability (100K cycles)
 *           Verify fetch_or / fetch_and on slot 1 bit with zero collisions.
 *
 *   Test 2: Multi-Slot Mask Independence
 *           Verify no bit bleed between slots 0-3 during set/clear.
 *
 *   Test 3: Rapid Alternating Status Transitions (10K cycles)
 *           Lock-protected slot status changes with mask bit coherence.
 *
 *   Test 4: Token Routing Safety
 *           Verify vos3_ai_slot_is_streaming / vos3_ai_any_slot_streaming
 *           consistency with mask state.
 *
 *   Test 5: Mask Snapshot Consistency
 *           Multi-bit atomic snapshot correctness for slots 0 and 2.
 *
 *   Test 6: Fetch-AND Return Value Verification
 *           Validates "last streaming slot" detection via old_mask inspection.
 *
 *   This file compiles as a kernel module (not userspace). It calls
 *   the real kernel APIs and prints PASS/FAIL via VOS3_INFO.
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Ghost Stream Stress -- Atomic Mask Verification Suite
 */

#include "../include/vos/ai_guard.h"
#include "../include/vos/console.h"
#include "../include/vos/sync.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * EXTERNAL STATE UNDER TEST
 * ============================================================================ */

extern uint8_t              g_streaming_mask;
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_omega_c_pass = 0;
static uint32_t g_omega_c_fail = 0;

#define OMEGA_C_ASSERT(cond, name)                                              \
    do {                                                                        \
        if (cond) {                                                             \
            g_omega_c_pass++;                                                   \
            VOS3_INFO("[GHOST-STREAM] PASS: %s", (name));                       \
        } else {                                                                \
            g_omega_c_fail++;                                                   \
            VOS3_ERROR("[GHOST-STREAM] FAIL: %s (line %d)", (name), __LINE__);  \
        }                                                                       \
    } while (0)

/* TSC helper */
static inline uint64_t ghost_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

#define GHOST_TSC_TO_US(cycles) ((cycles) / 2000ULL)

/* ============================================================================
 * TEST 1: ATOMIC MASK SET/CLEAR LINEARIZABILITY (100K CYCLES)
 * ============================================================================ */

/**
 * @brief 100,000 atomic set/clear cycles on slot 1 bit.
 *
 * For each iteration:
 *   1. Atomic fetch_or: set bit 1
 *   2. Atomic load: verify bit 1 is set
 *   3. Atomic fetch_and: clear bit 1
 *   4. Atomic load: verify bit 1 is clear
 *
 * Any verification failure increments collision_count.
 * The original g_streaming_mask is saved/restored around the test.
 */
static void test_mask_linearizability(void)
{
    VOS3_INFO("[GHOST-STREAM] --- Test 1: Atomic Mask Set/Clear Linearizability ---");

    /* Save original mask */
    uint8_t saved_mask = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* Clear all bits so we start clean */
    __atomic_store_n(&g_streaming_mask, 0, __ATOMIC_SEQ_CST);

    volatile uint32_t collision_count = 0;
    const uint32_t iterations = 100000U;

    uint64_t tsc_start = ghost_rdtsc();

    for (uint32_t i = 0; i < iterations; i++) {
        /* Step 1: Set bit 1 */
        __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << 1), __ATOMIC_SEQ_CST);

        /* Step 2: Verify bit 1 is set */
        uint8_t snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
        if (!(snap & (1U << 1))) {
            collision_count++;
        }

        /* Step 3: Clear bit 1 */
        __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << 1), __ATOMIC_SEQ_CST);

        /* Step 4: Verify bit 1 is clear */
        snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
        if (snap & (1U << 1)) {
            collision_count++;
        }
    }

    uint64_t tsc_end = ghost_rdtsc();
    uint64_t elapsed_us = GHOST_TSC_TO_US(tsc_end - tsc_start);

    VOS3_INFO("[GHOST-STREAM]   100K set/clear cycles on slot 1: %u collisions (%llu us)",
              collision_count, (unsigned long long)elapsed_us);

    OMEGA_C_ASSERT(collision_count == 0,
                   "100K atomic set/clear cycles: zero collisions");

    /* Restore original mask */
    __atomic_store_n(&g_streaming_mask, saved_mask, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * TEST 2: MULTI-SLOT MASK INDEPENDENCE
 * ============================================================================ */

/**
 * @brief Verify no bit bleed between slots 0-3.
 *
 * For each slot:
 *   - Set that slot's bit atomically
 *   - Verify ONLY that slot's bit is set (no bleed to other bits)
 *   - Clear that slot's bit
 *   - Verify ONLY that slot's bit is clear
 */
static void test_mask_independence(void)
{
    VOS3_INFO("[GHOST-STREAM] --- Test 2: Multi-Slot Mask Independence ---");

    /* Save original mask */
    uint8_t saved_mask = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* Clear all bits so we start clean */
    __atomic_store_n(&g_streaming_mask, 0, __ATOMIC_SEQ_CST);

    uint32_t bleed_count = 0;

    for (uint8_t slot = 0; slot < VOS3_MODEL_SLOT_MAX; slot++) {
        uint8_t bit = (uint8_t)(1U << slot);

        /* Set this slot's bit */
        __atomic_fetch_or(&g_streaming_mask, bit, __ATOMIC_SEQ_CST);

        /* Read snapshot */
        uint8_t snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

        /* Verify ONLY this slot's bit is set */
        if ((snap & bit) == 0) {
            VOS3_ERROR("[GHOST-STREAM]   Slot %u: bit not set after fetch_or", slot);
            bleed_count++;
        }

        /* Verify no OTHER bits in the slot range [0..3] are set */
        uint8_t other_bits = snap & (uint8_t)(0x0FU & ~bit);
        if (other_bits != 0) {
            VOS3_ERROR("[GHOST-STREAM]   Slot %u: bit bleed detected (mask=0x%02X, expected=0x%02X)",
                       slot, snap, bit);
            bleed_count++;
        }

        /* Clear this slot's bit */
        __atomic_fetch_and(&g_streaming_mask, (uint8_t)~bit, __ATOMIC_SEQ_CST);

        /* Verify bit is now clear */
        snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
        if (snap & bit) {
            VOS3_ERROR("[GHOST-STREAM]   Slot %u: bit still set after fetch_and", slot);
            bleed_count++;
        }
    }

    VOS3_INFO("[GHOST-STREAM]   4-slot independence verified (%u bleed events)", bleed_count);
    OMEGA_C_ASSERT(bleed_count == 0, "4-slot mask independence: zero bit bleed");

    /* Restore original mask */
    __atomic_store_n(&g_streaming_mask, saved_mask, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * TEST 3: RAPID ALTERNATING STATUS TRANSITIONS (10K CYCLES)
 * ============================================================================ */

/**
 * @brief 10,000 lock-protected status transitions on slot 1.
 *
 * Each iteration:
 *   1. Lock slot->lock
 *   2. Set status = VOS3_SLOT_STREAMING
 *   3. Atomic set mask bit
 *   4. Unlock
 *   5. Atomic load mask -> verify bit set
 *   6. Lock slot->lock
 *   7. Atomic clear mask bit (capture old)
 *   8. Set status = VOS3_SLOT_ACTIVE
 *   9. Unlock
 *  10. Atomic load mask -> verify bit clear
 *  11. Verify status == ACTIVE
 */
static void test_status_transitions(void)
{
    VOS3_INFO("[GHOST-STREAM] --- Test 3: Rapid Alternating Status Transitions ---");

    const uint8_t slot_id = 1;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Save original state */
    uint8_t saved_mask = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
    vos3_model_slot_status_t saved_status = slot->status;

    /* Ensure mask bit is clear before starting */
    __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << slot_id), __ATOMIC_SEQ_CST);

    uint32_t mismatch_count = 0;
    const uint32_t iterations = 10000U;

    uint64_t tsc_start = ghost_rdtsc();

    for (uint32_t i = 0; i < iterations; i++) {
        /* Phase A: Transition to STREAMING */
        vos3_spinlock_lock(&slot->lock);
        slot->status = VOS3_SLOT_STREAMING;
        __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << slot_id), __ATOMIC_SEQ_CST);
        vos3_spinlock_unlock(&slot->lock);

        /* Verify mask bit is set */
        uint8_t snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
        if (!(snap & (1U << slot_id))) {
            mismatch_count++;
        }

        /* Phase B: Transition to ACTIVE */
        vos3_spinlock_lock(&slot->lock);
        __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << slot_id), __ATOMIC_SEQ_CST);
        slot->status = VOS3_SLOT_ACTIVE;
        vos3_spinlock_unlock(&slot->lock);

        /* Verify mask bit is clear */
        snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
        if (snap & (1U << slot_id)) {
            mismatch_count++;
        }

        /* Verify status is ACTIVE */
        if (slot->status != VOS3_SLOT_ACTIVE) {
            mismatch_count++;
        }
    }

    uint64_t tsc_end = ghost_rdtsc();
    uint64_t elapsed_us = GHOST_TSC_TO_US(tsc_end - tsc_start);

    VOS3_INFO("[GHOST-STREAM]   10K status transitions: %u mismatches (%llu us)",
              mismatch_count, (unsigned long long)elapsed_us);

    OMEGA_C_ASSERT(mismatch_count == 0,
                   "10K status transitions: zero mask/status mismatches");

    /* Restore original state */
    slot->status = saved_status;
    __atomic_store_n(&g_streaming_mask, saved_mask, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * TEST 4: TOKEN ROUTING SAFETY
 * ============================================================================ */

/**
 * @brief Verify routing query functions reflect mask state.
 *
 * Sets slot 1 streaming, checks is_streaming(1)==1 and any_streaming()==1.
 * Clears slot 1, checks is_streaming(1)==0 and any_streaming()==0.
 */
static void test_token_routing(void)
{
    VOS3_INFO("[GHOST-STREAM] --- Test 4: Token Routing Safety ---");

    /* Save original mask */
    uint8_t saved_mask = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* Clear all bits to start from known state */
    __atomic_store_n(&g_streaming_mask, 0, __ATOMIC_SEQ_CST);

    /* Set slot 1 streaming via mask */
    __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << 1), __ATOMIC_SEQ_CST);

    /* Query functions should see slot 1 as streaming */
    int is_s1 = vos3_ai_slot_is_streaming(1);
    OMEGA_C_ASSERT(is_s1 == 1, "slot_is_streaming(1) returns 1 when bit set");

    int any_s = vos3_ai_any_slot_streaming();
    OMEGA_C_ASSERT(any_s == 1, "any_slot_streaming() returns 1 when slot 1 set");

    /* Clear slot 1 mask bit */
    __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << 1), __ATOMIC_SEQ_CST);

    /* Query functions should see slot 1 as not streaming */
    is_s1 = vos3_ai_slot_is_streaming(1);
    OMEGA_C_ASSERT(is_s1 == 0, "slot_is_streaming(1) returns 0 when bit clear");

    /* With all bits cleared, no slot should be streaming */
    any_s = vos3_ai_any_slot_streaming();
    OMEGA_C_ASSERT(any_s == 0, "any_slot_streaming() returns 0 when mask empty");

    /* Restore original mask */
    __atomic_store_n(&g_streaming_mask, saved_mask, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * TEST 5: MASK SNAPSHOT CONSISTENCY
 * ============================================================================ */

/**
 * @brief Multi-bit atomic snapshot correctness.
 *
 * Sets bits 0 and 2 simultaneously, takes snapshot, verifies only those
 * bits are set. Clears both, takes another snapshot, verifies all clear.
 */
static void test_snapshot_consistency(void)
{
    VOS3_INFO("[GHOST-STREAM] --- Test 5: Mask Snapshot Consistency ---");

    /* Save original mask */
    uint8_t saved_mask = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* Clear all bits */
    __atomic_store_n(&g_streaming_mask, 0, __ATOMIC_SEQ_CST);

    /* Set bits for slots 0 and 2 */
    __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << 0), __ATOMIC_SEQ_CST);
    __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << 2), __ATOMIC_SEQ_CST);

    /* Take atomic snapshot */
    uint8_t snapshot = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* Verify bits 0 and 2 are set */
    OMEGA_C_ASSERT((snapshot & (1U << 0)) != 0,
                   "Snapshot: bit 0 set");
    OMEGA_C_ASSERT((snapshot & (1U << 2)) != 0,
                   "Snapshot: bit 2 set");

    /* Verify bits 1 and 3 are clear */
    OMEGA_C_ASSERT((snapshot & (1U << 1)) == 0,
                   "Snapshot: bit 1 clear");
    OMEGA_C_ASSERT((snapshot & (1U << 3)) == 0,
                   "Snapshot: bit 3 clear");

    /* Combined check: mask should be exactly 0x05 */
    OMEGA_C_ASSERT(snapshot == 0x05,
                   "Snapshot exact value 0x05 (slots 0+2)");

    /* Clear both bits */
    __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << 0), __ATOMIC_SEQ_CST);
    __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << 2), __ATOMIC_SEQ_CST);

    /* Take another snapshot */
    snapshot = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* All bits should be clear */
    OMEGA_C_ASSERT(snapshot == 0x00,
                   "Snapshot after clear: all bits zero");

    /* Restore original mask */
    __atomic_store_n(&g_streaming_mask, saved_mask, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * TEST 6: FETCH-AND RETURN VALUE VERIFICATION
 * ============================================================================ */

/**
 * @brief Verify fetch_and return value for "last streaming slot" detection.
 *
 * The kernel uses the OLD mask returned by fetch_and to detect whether
 * we were the one who cleared the last streaming bit. This test verifies
 * that return-value contract.
 */
static void test_fetch_and_return_value(void)
{
    VOS3_INFO("[GHOST-STREAM] --- Test 6: Fetch-AND Return Value Verification ---");

    /* Save original mask */
    uint8_t saved_mask = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);

    /* Clear all bits */
    __atomic_store_n(&g_streaming_mask, 0, __ATOMIC_SEQ_CST);

    /* Set bit 1 */
    __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << 1), __ATOMIC_SEQ_CST);

    /* First fetch_and: clear bit 1, capture old mask */
    uint8_t old_mask = __atomic_fetch_and(&g_streaming_mask,
                                          (uint8_t)~(1U << 1),
                                          __ATOMIC_SEQ_CST);

    /* old_mask should have had bit 1 SET (we were the one to clear it) */
    OMEGA_C_ASSERT((old_mask & (1U << 1)) != 0,
                   "First fetch_and: old_mask had bit 1 SET");

    /* Verify mask is now clear */
    uint8_t current = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
    OMEGA_C_ASSERT((current & (1U << 1)) == 0,
                   "After first fetch_and: bit 1 is clear");

    /* Second fetch_and: clear bit 1 again (already clear) */
    old_mask = __atomic_fetch_and(&g_streaming_mask,
                                  (uint8_t)~(1U << 1),
                                  __ATOMIC_SEQ_CST);

    /* old_mask should have bit 1 CLEAR (already cleared by first op) */
    OMEGA_C_ASSERT((old_mask & (1U << 1)) == 0,
                   "Second fetch_and: old_mask had bit 1 CLEAR (idempotent)");

    VOS3_INFO("[GHOST-STREAM]   fetch_and return value contract verified");

    /* Restore original mask */
    __atomic_store_n(&g_streaming_mask, saved_mask, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * VERIFICATION ENTRY POINT
 * ============================================================================ */

/**
 * @brief Run all Ghost Stream atomic stress tests.
 *
 * Called from kmain or via VBus command.
 *
 * @return 0 if all tests pass, number of failures otherwise
 */
int vos3_ghost_stream_stress_run(void)
{
    g_omega_c_pass = 0;
    g_omega_c_fail = 0;

    VOS3_INFO("============================================================");
    VOS3_INFO("[GHOST-STREAM] Atomic Streaming Mask Stress Suite");
    VOS3_INFO("============================================================");

    test_mask_linearizability();
    test_mask_independence();
    test_status_transitions();
    test_token_routing();
    test_snapshot_consistency();
    test_fetch_and_return_value();

    VOS3_INFO("============================================================");
    VOS3_INFO("[GHOST-STREAM] Results: %u PASS, %u FAIL",
              g_omega_c_pass, g_omega_c_fail);
    if (g_omega_c_fail == 0) {
        VOS3_INFO("[GHOST-STREAM] ALL TESTS PASSED -- MASK INTEGRITY VERIFIED");
    } else {
        VOS3_ERROR("[GHOST-STREAM] %u FAILURES -- REVIEW REQUIRED", g_omega_c_fail);
    }
    VOS3_INFO("============================================================");

    return (int)g_omega_c_fail;
}
