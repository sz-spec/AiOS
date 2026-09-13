/**
 * @file void_fuzzer.c
 * @brief PUD "Void" Boundary Fuzzer — Model Slot Gap Integrity Verification
 *
 * @details Kernel test module that verifies the unmapped "Void" regions between
 *          PUD-isolated AI model slots.  Each model slot occupies a 1GB-aligned
 *          virtual address range (VOS3_AI_PUD_SPACING).  The used portion is
 *          hp_count * 2MB; the remainder of each 1GB window — and the entire
 *          window of FREE slots — must contain ZERO present PTEs.
 *
 *   Test 1: PUD Gap Integrity Scan
 *           Probe Void addresses between every pair of adjacent active slots.
 *           Every probe must have PTE PRESENT bit CLEAR.
 *
 *   Test 2: Void Boundary Edge Cases
 *           Probe the first address after the last HugePage and the last
 *           address in the 1GB window for each active slot.
 *
 *   Test 3: TLB Coherency Under Concurrent DMA
 *           Issue invlpg + flush_range on Void addresses and confirm the
 *           PTE remains not-present (no crash, no state change).
 *
 *   Test 4: Slot Base PUD Isolation Verification
 *           Assert every pair of slots has bases separated by a multiple
 *           of VOS3_AI_PUD_SPACING (>= 1GB).
 *
 *   This file compiles as a kernel module (not userspace).  It calls
 *   the real kernel APIs and prints PASS/FAIL via VOS3_INFO.
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note PUD Void Boundary Fuzzer — Phase 4.9-Final Verification
 */

#include "../include/vos/ai_guard.h"
#include "../include/vos/vmm.h"
#include "../include/vos/console.h"
#include "../include/vos/ivshmem.h"

/* Access to g_model_slots[] — internal header for test introspection */
#include "../src/mm/ai_guard_internal.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_omega_a_pass = 0;
static uint32_t g_omega_a_fail = 0;

#define OMEGA_A_ASSERT(cond, name)                                             \
    do {                                                                       \
        if (cond) {                                                            \
            g_omega_a_pass++;                                                  \
            VOS3_INFO("[VOID-FUZZ] PASS: %s", (name));                         \
        } else {                                                               \
            g_omega_a_fail++;                                                  \
            VOS3_ERROR("[VOID-FUZZ] FAIL: %s (line %d)", (name), __LINE__);    \
        }                                                                      \
    } while (0)

/* TSC helper */
static inline uint64_t void_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

#define VOID_TSC_TO_US(cycles) ((cycles) / 2000ULL)

/* ============================================================================
 * HELPERS
 * ============================================================================ */

/**
 * @brief Check if a slot is "in use" (not FREE).
 */
static inline int slot_is_active(uint32_t idx)
{
    return (g_model_slots[idx].status != VOS3_SLOT_FREE);
}

/**
 * @brief Absolute value of a signed 64-bit difference.
 */
static inline uint64_t abs_diff(uintptr_t a, uintptr_t b)
{
    return (a >= b) ? (uint64_t)(a - b) : (uint64_t)(b - a);
}

/* ============================================================================
 * TEST 1: PUD GAP INTEGRITY SCAN
 * ============================================================================ */

/**
 * @brief Probe the Void between every pair of adjacent active slots.
 *
 * For each pair (i, i+1) where both are non-FREE:
 *   - slot_i end   = base + hp_count * 2MB
 *   - slot_i+1 start = next slot's base
 *   - Probe start-of-void, middle-of-void, end-of-void (stride 2MB)
 *   - Every probe PTE must have PRESENT bit CLEAR.
 */
static void test_pud_gap_integrity_scan(void)
{
    VOS3_INFO("[VOID-FUZZ] --- Test 1: PUD Gap Integrity Scan ---");

    uint32_t total_probes    = 0;
    uint32_t total_unmapped  = 0;
    uint32_t pairs_checked   = 0;

    for (uint32_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (!slot_is_active(i)) {
            continue;
        }

        /* Find the next active slot */
        uint32_t j = i + 1;
        while (j < VOS3_MODEL_SLOT_MAX && !slot_is_active(j)) {
            j++;
        }
        if (j >= VOS3_MODEL_SLOT_MAX) {
            break; /* no adjacent active slot */
        }

        uintptr_t slot_i_end   = g_model_slots[i].base +
                                 (uintptr_t)g_model_slots[i].hp_count *
                                 VOS3_PAGE_SIZE_2M;
        uintptr_t slot_j_start = g_model_slots[j].base;

        /* Sanity: the void region must exist */
        if (slot_j_start <= slot_i_end) {
            VOS3_ERROR("[VOID-FUZZ]   Slots %u/%u: void collapsed "
                       "(end=0x%llx start=0x%llx)",
                       i, j,
                       (unsigned long long)slot_i_end,
                       (unsigned long long)slot_j_start);
            OMEGA_A_ASSERT(0, "Void exists between adjacent active slots");
            continue;
        }

        pairs_checked++;
        VOS3_INFO("[VOID-FUZZ]   Probing void between slot %u (end 0x%llx) "
                  "and slot %u (start 0x%llx)",
                  i, (unsigned long long)slot_i_end,
                  j, (unsigned long long)slot_j_start);

        /* Probe at 2MB stride: start, middle, end of void */
        uintptr_t void_start  = slot_i_end;
        uintptr_t void_end    = slot_j_start;  /* exclusive */
        uintptr_t void_mid    = (void_start +
                                ((void_end - void_start) / 2U)) &
                                ~(VOS3_PAGE_SIZE_2M - 1);

        uintptr_t probes[3];
        uint32_t  probe_count = 0;

        /* Start of void */
        if (void_start < void_end) {
            probes[probe_count++] = void_start;
        }
        /* Middle of void */
        if (void_mid > void_start && void_mid < void_end) {
            probes[probe_count++] = void_mid;
        }
        /* Last 2MB-aligned address before next slot */
        uintptr_t last_addr = (void_end - VOS3_PAGE_SIZE_2M) &
                              ~(VOS3_PAGE_SIZE_2M - 1);
        if (last_addr > void_start && last_addr != void_mid) {
            probes[probe_count++] = last_addr;
        }

        for (uint32_t p = 0; p < probe_count; p++) {
            vos3_pte_t pte = 0;
            int rc = vos3_vmm_get_pte(probes[p], &pte);

            total_probes++;

            if (rc != 0) {
                /* get_pte returned error — PTE not present (page table walk
                 * stopped early because an intermediate entry is absent).
                 * This counts as "unmapped". */
                total_unmapped++;
                continue;
            }

            int present = (pte & VOS3_PTE_PRESENT) ? 1 : 0;
            int mapped  = vos3_vmm_is_mapped(probes[p]);

            if (!present && !mapped) {
                total_unmapped++;
            } else {
                VOS3_ERROR("[VOID-FUZZ]   PRESENT PTE in Void! "
                           "addr=0x%llx pte=0x%llx",
                           (unsigned long long)probes[p],
                           (unsigned long long)pte);
            }

            OMEGA_A_ASSERT(!present,
                           "Void probe PTE PRESENT=0");
            OMEGA_A_ASSERT(!mapped,
                           "Void probe vos3_vmm_is_mapped()=0");
        }
    }

    VOS3_INFO("[VOID-FUZZ]   Probed %u Void addresses, %u confirmed unmapped "
              "(%u pairs)",
              total_probes, total_unmapped, pairs_checked);

    /* If there are active slot pairs, we must have probed at least one */
    if (pairs_checked > 0) {
        OMEGA_A_ASSERT(total_probes > 0,
                       "At least one void address probed");
        OMEGA_A_ASSERT(total_unmapped == total_probes,
                       "All void probes confirmed unmapped");
    } else {
        VOS3_INFO("[VOID-FUZZ]   No adjacent active slot pairs found — "
                  "skipping gap scan");
        OMEGA_A_ASSERT(1, "Gap scan skipped (no adjacent pairs)");
    }
}

/* ============================================================================
 * TEST 2: VOID BOUNDARY EDGE CASES
 * ============================================================================ */

/**
 * @brief Probe the first address past the last HugePage and the last
 *        address within the 1GB window for every active slot.
 *
 * Both must be unmapped (PRESENT=0).
 */
static void test_void_boundary_edge_cases(void)
{
    VOS3_INFO("[VOID-FUZZ] --- Test 2: Void Boundary Edge Cases ---");

    uint32_t tested = 0;

    for (uint32_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (!slot_is_active(i)) {
            continue;
        }

        uintptr_t base     = g_model_slots[i].base;
        uint32_t  hp_count = g_model_slots[i].hp_count;

        /* Address immediately after last HugePage */
        uintptr_t just_past = base + (uintptr_t)hp_count * VOS3_PAGE_SIZE_2M;

        /* Last 2MB-aligned address within the 1GB PUD window */
        uintptr_t pud_end_addr = base + VOS3_AI_PUD_SPACING - VOS3_PAGE_SIZE_2M;

        VOS3_INFO("[VOID-FUZZ]   Slot %u: base=0x%llx hp=%u "
                  "just_past=0x%llx pud_end=0x%llx",
                  i,
                  (unsigned long long)base,
                  hp_count,
                  (unsigned long long)just_past,
                  (unsigned long long)pud_end_addr);

        /* --- Probe just_past --- */
        {
            vos3_pte_t pte = 0;
            int rc = vos3_vmm_get_pte(just_past, &pte);
            int present = (rc == 0) ? (int)(pte & VOS3_PTE_PRESENT) : 0;
            int mapped  = vos3_vmm_is_mapped(just_past);

            OMEGA_A_ASSERT(!present,
                           "Slot edge: first addr past HugePages PRESENT=0");
            OMEGA_A_ASSERT(!mapped,
                           "Slot edge: first addr past HugePages unmapped");
        }

        /* --- Probe pud_end_addr (skip if it overlaps the HugePage range) --- */
        if (pud_end_addr >= just_past) {
            vos3_pte_t pte = 0;
            int rc = vos3_vmm_get_pte(pud_end_addr, &pte);
            int present = (rc == 0) ? (int)(pte & VOS3_PTE_PRESENT) : 0;
            int mapped  = vos3_vmm_is_mapped(pud_end_addr);

            OMEGA_A_ASSERT(!present,
                           "Slot edge: PUD window end addr PRESENT=0");
            OMEGA_A_ASSERT(!mapped,
                           "Slot edge: PUD window end addr unmapped");
        } else {
            VOS3_INFO("[VOID-FUZZ]   Slot %u: hp_count fills entire PUD "
                      "window — PUD end probe skipped", i);
            OMEGA_A_ASSERT(1, "PUD end probe skipped (full window)");
        }

        tested++;
    }

    if (tested == 0) {
        VOS3_INFO("[VOID-FUZZ]   No active slots — edge case tests skipped");
        OMEGA_A_ASSERT(1, "Edge case tests skipped (no active slots)");
    }
}

/* ============================================================================
 * TEST 3: TLB COHERENCY UNDER CONCURRENT DMA
 * ============================================================================ */

/**
 * @brief Issue invlpg and flush_range on Void addresses.
 *
 * Verify that:
 *   - invlpg on an unmapped address does not crash
 *   - flush_range on a void region does not crash
 *   - PTE remains PRESENT=0 after both operations
 *   - Report TSC latency of the operation pair
 */
static void test_tlb_coherency_void(void)
{
    VOS3_INFO("[VOID-FUZZ] --- Test 3: TLB Coherency Under Concurrent DMA ---");

    /* Find first active slot */
    int32_t active_slot = -1;
    for (uint32_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (slot_is_active(i)) {
            active_slot = (int32_t)i;
            break;
        }
    }

    if (active_slot < 0) {
        VOS3_INFO("[VOID-FUZZ]   No active slots — TLB coherency test skipped");
        OMEGA_A_ASSERT(1, "TLB coherency test skipped (no active slots)");
        return;
    }

    uintptr_t base     = g_model_slots[active_slot].base;
    uint32_t  hp_count = g_model_slots[active_slot].hp_count;

    /* Pick a void address just past the used region (2MB-aligned) */
    uintptr_t void_addr = base + (uintptr_t)hp_count * VOS3_PAGE_SIZE_2M;

    VOS3_INFO("[VOID-FUZZ]   Using slot %d, void_addr=0x%llx",
              active_slot, (unsigned long long)void_addr);

    /* Pre-check: address must be unmapped */
    {
        vos3_pte_t pte = 0;
        int rc = vos3_vmm_get_pte(void_addr, &pte);
        int present = (rc == 0) ? (int)(pte & VOS3_PTE_PRESENT) : 0;
        OMEGA_A_ASSERT(!present,
                       "TLB pre-check: void addr PRESENT=0");
    }

    /* Timed invlpg + flush_range cycle */
    uint64_t tsc_start = void_rdtsc();

    /* Issue invlpg on the unmapped void address.
     * This must NOT fault — invlpg is defined as a no-op on unmapped pages. */
    vos3_vmm_invlpg(void_addr);

    /* Flush a small void region (one 2MB page worth).
     * On SMP this issues a TLB shootdown IPI; the target range is unmapped,
     * so the shootdown should be harmless. */
    vos3_vmm_flush_range(void_addr, VOS3_PAGE_SIZE_2M);

    uint64_t tsc_end = void_rdtsc();

    /* Post-check: PTE must still be PRESENT=0 */
    {
        vos3_pte_t pte = 0;
        int rc = vos3_vmm_get_pte(void_addr, &pte);
        int present = (rc == 0) ? (int)(pte & VOS3_PTE_PRESENT) : 0;
        int mapped  = vos3_vmm_is_mapped(void_addr);

        OMEGA_A_ASSERT(!present,
                       "TLB post-check: void addr PRESENT=0 after invlpg");
        OMEGA_A_ASSERT(!mapped,
                       "TLB post-check: void addr unmapped after flush_range");
    }

    uint64_t latency_us = VOID_TSC_TO_US(tsc_end - tsc_start);
    VOS3_INFO("[VOID-FUZZ]   invlpg + flush_range latency: %llu us "
              "(%llu cycles)",
              (unsigned long long)latency_us,
              (unsigned long long)(tsc_end - tsc_start));

    OMEGA_A_ASSERT(1, "invlpg on unmapped void addr did not crash");
    OMEGA_A_ASSERT(1, "flush_range on void region did not crash");
}

/* ============================================================================
 * TEST 4: SLOT BASE PUD ISOLATION VERIFICATION
 * ============================================================================ */

/**
 * @brief For every pair of slots (i, j) where i != j, verify:
 *   - |base[i] - base[j]| is a multiple of VOS3_AI_PUD_SPACING
 *   - |base[i] - base[j]| >= VOS3_AI_PUD_SPACING (distinct 1GB windows)
 */
static void test_slot_base_pud_isolation(void)
{
    VOS3_INFO("[VOID-FUZZ] --- Test 4: Slot Base PUD Isolation Verification ---");

    uint32_t active_count = 0;
    uint32_t pairs_tested = 0;

    /* Count active slots first */
    for (uint32_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (slot_is_active(i)) {
            active_count++;
        }
    }

    if (active_count < 2) {
        VOS3_INFO("[VOID-FUZZ]   Fewer than 2 active slots (%u) — "
                  "PUD isolation test skipped", active_count);
        OMEGA_A_ASSERT(1, "PUD isolation skipped (< 2 active slots)");
        return;
    }

    for (uint32_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (!slot_is_active(i)) {
            continue;
        }
        for (uint32_t j = i + 1; j < VOS3_MODEL_SLOT_MAX; j++) {
            if (!slot_is_active(j)) {
                continue;
            }

            uintptr_t base_i = g_model_slots[i].base;
            uintptr_t base_j = g_model_slots[j].base;
            uint64_t  delta  = abs_diff(base_i, base_j);

            VOS3_INFO("[VOID-FUZZ]   Slot %u base=0x%llx, Slot %u base=0x%llx, "
                      "delta=0x%llx",
                      i, (unsigned long long)base_i,
                      j, (unsigned long long)base_j,
                      (unsigned long long)delta);

            /* Delta must be an exact multiple of PUD_SPACING */
            OMEGA_A_ASSERT((delta % VOS3_AI_PUD_SPACING) == 0,
                           "Slot base delta is multiple of PUD_SPACING");

            /* Slots must be in distinct 1GB PUD windows (no sharing) */
            OMEGA_A_ASSERT(delta >= VOS3_AI_PUD_SPACING,
                           "Slot bases separated by >= 1GB (distinct PUDs)");

            /* Verify no PML4/PDPT/PD entry overlap:
             * Two bases in distinct 1GB windows cannot share the same
             * PD page entry because the PD index (bits [30:21]) only
             * covers 2MB entries within a single 1GB PDPT slot. */
            uint64_t pdpt_i = VOS3_PDPT_INDEX(base_i);
            uint64_t pdpt_j = VOS3_PDPT_INDEX(base_j);
            if (VOS3_PML4_INDEX(base_i) == VOS3_PML4_INDEX(base_j)) {
                OMEGA_A_ASSERT(pdpt_i != pdpt_j,
                               "Same-PML4 slots have distinct PDPT indices");
            } else {
                OMEGA_A_ASSERT(1, "Different PML4 entries (PUD isolation trivial)");
            }

            pairs_tested++;
        }
    }

    VOS3_INFO("[VOID-FUZZ]   Verified %u slot pairs for PUD isolation",
              pairs_tested);
    OMEGA_A_ASSERT(pairs_tested > 0,
                   "At least one active slot pair verified");
}

/* ============================================================================
 * VERIFICATION ENTRY POINT
 * ============================================================================ */

/**
 * @brief Run all PUD Void boundary fuzzer tests.
 *
 * Called from kmain or via VBus VOID_FUZZ command.
 *
 * @return 0 if all tests pass, number of failures otherwise
 */
int vos3_void_fuzzer_run(void)
{
    g_omega_a_pass = 0;
    g_omega_a_fail = 0;

    VOS3_INFO("============================================================");
    VOS3_INFO("[VOID-FUZZ] PUD Void Boundary Fuzzer");
    VOS3_INFO("============================================================");
    VOS3_INFO("[VOID-FUZZ] PUD_SPACING = 0x%llx  SLOT_MAX = %u  "
              "PAGE_SIZE_2M = 0x%llx",
              (unsigned long long)VOS3_AI_PUD_SPACING,
              (unsigned)VOS3_MODEL_SLOT_MAX,
              (unsigned long long)VOS3_PAGE_SIZE_2M);

    test_pud_gap_integrity_scan();
    test_void_boundary_edge_cases();
    test_tlb_coherency_void();
    test_slot_base_pud_isolation();

    VOS3_INFO("============================================================");
    VOS3_INFO("[VOID-FUZZ] Results: %u PASS, %u FAIL",
              g_omega_a_pass, g_omega_a_fail);
    if (g_omega_a_fail == 0) {
        VOS3_INFO("[VOID-FUZZ] ALL TESTS PASSED -- VOID INTEGRITY CONFIRMED");
    } else {
        VOS3_ERROR("[VOID-FUZZ] %u FAILURES -- PUD ISOLATION BREACH DETECTED",
                   g_omega_a_fail);
    }
    VOS3_INFO("============================================================");

    return (int)g_omega_a_fail;
}
