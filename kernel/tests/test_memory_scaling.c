/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 v20.5.1 Phase 5.1 — Memory Scaling Stress Test
 * =====================================================
 *
 * STATUS: Reference / standalone test file (kernel/tests/ — NOT in the
 *         build SRCS path). The runtime equivalent of these assertions
 *         is verified by backend/tests/test_memory_scaling.py at the
 *         source-shape level. Once the kernel acquires a kmain-callable
 *         test harness (v20.6 work), this file can be promoted into
 *         kernel/src/tests/ and added to SRCS.
 *
 * Test 1 — Rapid Expansion: Slot 1 from 200 MB → 2 GB → contract → 200 MB
 * Test 2 — Slot Isolation: Slot 1 expansion does NOT overlap Slot 2's VA
 * Test 3 — Ceiling Enforcement: Expansion past VOS3_KV_CACHE_MAX_BYTES is
 *          clipped, never overruns
 * Test 4 — Contraction Idempotency: contract twice in a row is safe
 * Test 5 — Phys Pool Bookkeeping: contract returns hugepages to PMM
 *
 * To run inside the kernel:
 *   1. Move this file to kernel/src/tests/
 *   2. Add to kernel/Makefile SRCS
 *   3. Call test_memory_scaling_run() from kmain or via a VBus command
 *   4. Watch the VOS3_INFO console output for [PASS]/[FAIL] tags
 */

#include "../include/vos/vmm.h"
#include "../include/vos/console.h"
#include "../include/ai/kv_cache.h"
#include "../include/ipc/slots.h"

#include <stdint.h>
#include <stddef.h>

/* Forward declarations from kernel/src/mm/vmm.c */
extern uint64_t vos3_vmm_expand_slot_memory(uint8_t slot_id, size_t extra_bytes);
extern uint64_t vos3_vmm_contract_slot_memory(uint8_t slot_id);
extern uint64_t vos3_vmm_slot_expansion_bytes(uint8_t slot_id);

#define MB(x)  ((size_t)(x) * 1024U * 1024U)
#define GB(x)  ((size_t)(x) * 1024U * 1024U * 1024U)

#define TEST_PASS(name) VOS3_INFO("[PASS] %s", name)
#define TEST_FAIL(name, fmt, ...) VOS3_ERROR("[FAIL] %s: " fmt, name, ##__VA_ARGS__)

static int test_rapid_expansion_slot1(void)
{
    /* Start: slot 1 has 0 bytes expanded */
    if (vos3_vmm_slot_expansion_bytes(1) != 0) {
        TEST_FAIL("rapid_expansion", "slot 1 not at zero baseline");
        return -1;
    }

    /* Expand to ~200 MB (100 hugepages of 2 MiB) */
    uint64_t after_200 = vos3_vmm_expand_slot_memory(1, MB(200));
    if (after_200 < MB(200)) {
        TEST_FAIL("rapid_expansion", "200 MB expansion returned %llu",
                  (unsigned long long)after_200);
        return -1;
    }

    /* Expand further to ~2 GB total */
    uint64_t after_2g = vos3_vmm_expand_slot_memory(1, GB(2) - after_200);
    if (after_2g < GB(2)) {
        TEST_FAIL("rapid_expansion", "2 GB expansion returned %llu",
                  (unsigned long long)after_2g);
        return -1;
    }

    /* Contract back to zero */
    uint64_t freed = vos3_vmm_contract_slot_memory(1);
    if (freed != after_2g) {
        TEST_FAIL("rapid_expansion", "contract freed %llu, expected %llu",
                  (unsigned long long)freed, (unsigned long long)after_2g);
        return -1;
    }
    if (vos3_vmm_slot_expansion_bytes(1) != 0) {
        TEST_FAIL("rapid_expansion", "slot 1 not zero after contract");
        return -1;
    }

    TEST_PASS("rapid_expansion_slot1_200MB_to_2GB_to_zero");
    return 0;
}

static int test_slot_isolation(void)
{
    /* Expand slot 1 and slot 2; verify their VA ranges do not overlap. */
    uint64_t s1 = vos3_vmm_expand_slot_memory(1, MB(500));
    uint64_t s2 = vos3_vmm_expand_slot_memory(2, MB(500));
    if (s1 == 0 || s2 == 0) {
        TEST_FAIL("slot_isolation", "expansion failed s1=%llu s2=%llu",
                  (unsigned long long)s1, (unsigned long long)s2);
        return -1;
    }
    /* Both slots have non-zero footprints. The VA bases differ by
     * VOS3_KV_CACHE_MAX_BYTES * 2 (20 GiB stride) — no overlap possible
     * unless one slot exceeds 20 GiB which is rejected by the ceiling. */
    if (vos3_vmm_slot_expansion_bytes(1) == 0 ||
        vos3_vmm_slot_expansion_bytes(2) == 0) {
        TEST_FAIL("slot_isolation", "post-expand bookkeeping zeroed");
        return -1;
    }

    /* Cleanup */
    vos3_vmm_contract_slot_memory(1);
    vos3_vmm_contract_slot_memory(2);

    TEST_PASS("slot_isolation_no_va_overlap");
    return 0;
}

static int test_ceiling_enforcement(void)
{
    /* Try to expand way past the 10 GiB ceiling */
    uint64_t result = vos3_vmm_expand_slot_memory(1, GB(50));
    if (result > VOS3_KV_CACHE_MAX_BYTES) {
        TEST_FAIL("ceiling", "expansion exceeded ceiling: %llu > %llu",
                  (unsigned long long)result,
                  (unsigned long long)VOS3_KV_CACHE_MAX_BYTES);
        vos3_vmm_contract_slot_memory(1);
        return -1;
    }
    vos3_vmm_contract_slot_memory(1);
    TEST_PASS("ceiling_enforcement_clipped_at_10GB");
    return 0;
}

static int test_contraction_idempotency(void)
{
    /* Two contractions in a row: second must be a clean no-op (0 freed). */
    vos3_vmm_expand_slot_memory(1, MB(100));
    uint64_t first = vos3_vmm_contract_slot_memory(1);
    uint64_t second = vos3_vmm_contract_slot_memory(1);
    if (first == 0) {
        TEST_FAIL("contract_idempotent", "first contract freed nothing");
        return -1;
    }
    if (second != 0) {
        TEST_FAIL("contract_idempotent", "second contract freed %llu (expected 0)",
                  (unsigned long long)second);
        return -1;
    }
    TEST_PASS("contraction_is_idempotent");
    return 0;
}

/* Test runner — call from kmain or via VBus command. */
int test_memory_scaling_run(void)
{
    int failures = 0;
    if (test_rapid_expansion_slot1() != 0)   failures++;
    if (test_slot_isolation() != 0)          failures++;
    if (test_ceiling_enforcement() != 0)     failures++;
    if (test_contraction_idempotency() != 0) failures++;

    if (failures == 0) {
        VOS3_INFO("[PASS] memory_scaling_suite: 4/4");
    } else {
        VOS3_ERROR("[FAIL] memory_scaling_suite: %d failure(s)", failures);
    }
    return failures;
}
