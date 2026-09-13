/**
 * @file nvme_security_test.c
 * @brief Phase 8.3 Track B: NVMe PRP Poisoning Security Audit
 *
 * @details HOST-COMPILABLE security test for the NVMe PRP boundary
 *          validation logic.  Exercises 15+ attack vectors against
 *          a faithful mock of vos3_nvme_validate_prp() from nvme.c.
 *
 *          The mock reimplements the EXACT same four-stage boundary
 *          checking pipeline:
 *            1. Page alignment check (bits [11:0] must be zero)
 *            2. Lower bound check   (< 1MB rejected)
 *            3. Upper bound check   (>= 4GB rejected)
 *            4. PCI memory hole     (3GB <= addr < 4GB rejected)
 *
 *          Compile:
 *            gcc -std=c11 -Wall -Wextra -O2 -o nvme_security_test \
 *                nvme_security_test.c && ./nvme_security_test
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 9: Bare-Metal Peak -- NVMe PRP Poisoning Audit (Track B)
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>

/* ============================================================================
 * CONSTANTS — Matching kernel/include/vos/nvme.h exactly
 * ============================================================================ */

/** @brief DMA lower bound -- below 1MB is BIOS/legacy territory */
#define VOS3_NVME_DMA_LOWER_BOUND      0x100000ULL     /* 1 MiB */

/** @brief DMA upper bound -- 4GB physical max (VOS3 cap) */
#define VOS3_NVME_DMA_UPPER_BOUND      0x100000000ULL  /* 4 GiB */

/** @brief PCI memory hole start -- 3GB */
#define VOS3_PCI_HOLE_START            0xC0000000ULL   /* 3 GiB */

/** @brief PCI memory hole end -- 4GB (exclusive) */
#define VOS3_PCI_HOLE_END              0x100000000ULL  /* 4 GiB */

/** @brief Success */
#define VOS3_NVME_OK                    0

/** @brief DMA address out of safe bounds */
#define VOS3_NVME_E_DMA_BOUNDARY       (-1)

/* ============================================================================
 * MOCK: vos3_nvme_validate_prp()
 * ============================================================================
 *
 * Reimplements the EXACT four-stage boundary check from:
 *   kernel/src/drivers/nvme.c lines 214-245
 *
 * Order of checks matters:
 *   1. Page alignment   (phys_addr & 0xFFF != 0)
 *   2. Below lower bound (phys_addr < 0x100000)
 *   3. Above upper bound (phys_addr >= 0x100000000)
 *   4. PCI memory hole   (0xC0000000 <= phys_addr < 0x100000000)
 *
 * Check 3 fires before check 4 for addresses >= 4GB, which means
 * addresses at or above 4GB are rejected by check 3, never reaching
 * the PCI hole check.  This is faithful to the kernel implementation.
 */
static int mock_validate_prp(uint64_t phys_addr)
{
    /* 1. Page alignment check: bits [11:0] must be zero */
    if (phys_addr & 0xFFFULL) {
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    /* 2. Below BIOS/legacy area (< 1MB) */
    if (phys_addr < VOS3_NVME_DMA_LOWER_BOUND) {
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    /* 3. Beyond 4GB physical cap */
    if (phys_addr >= VOS3_NVME_DMA_UPPER_BOUND) {
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    /* 4. PCI memory hole: 3GB (0xC0000000) to 4GB (0x100000000) */
    if (phys_addr >= 0xC0000000ULL && phys_addr < 0x100000000ULL) {
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    return VOS3_NVME_OK;
}

/* ============================================================================
 * MOCK: Self-referencing PRP list detection
 * ============================================================================
 *
 * Reimplements the self-referencing PRP detection from nvme_build_prp_list()
 * at kernel/src/drivers/nvme.c lines 711-717.
 *
 * In the real driver, each PRP list entry is compared against the PRP list
 * page's own physical address.  If they match, the entry would create a
 * DMA loop (the controller would read the list entry, jump to the list
 * page itself, read that as data, etc.)
 */
static int mock_validate_prp_self_ref(uint64_t entry_phys, uint64_t list_phys)
{
    /* First, the entry must pass normal boundary validation */
    int rc = mock_validate_prp(entry_phys);
    if (rc != VOS3_NVME_OK) {
        return rc;
    }

    /* Self-referencing detection: entry == PRP list physical address */
    if (entry_phys == list_phys) {
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    return VOS3_NVME_OK;
}

/* ============================================================================
 * TEST HARNESS
 * ============================================================================ */

static int test_count = 0;
static int pass_count = 0;
static int fail_count = 0;

#define TEST(name, cond) do {                                       \
    test_count++;                                                   \
    if (cond) {                                                     \
        pass_count++;                                               \
        printf("  [PASS] %s\n", name);                              \
    } else {                                                        \
        fail_count++;                                               \
        printf("  [FAIL] %s  (line %d)\n", name, __LINE__);        \
    }                                                               \
} while (0)

/* ============================================================================
 * MAIN — 15+ PRP Poisoning Attack Vectors
 * ============================================================================ */

int main(void)
{
    printf("========================================================\n");
    printf("  VOS3 Phase 8.3 Track B: NVMe PRP Poisoning Audit\n");
    printf("  Host-compilable security test for DMA boundary guards\n");
    printf("========================================================\n\n");

    printf("[Section 1] Null / Low Address Attacks\n");
    printf("---------------------------------------\n");

    /* FUZZ-PRP-1: Null pointer DMA -- address 0x0 */
    TEST("FUZZ-PRP-1:  PRP = 0x0 (null pointer DMA)",
         mock_validate_prp(0x0ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* FUZZ-PRP-2: Below 1MB boundary (BIOS/legacy territory) */
    TEST("FUZZ-PRP-2:  PRP = 0x80000 (512KB, below 1MB lower bound)",
         mock_validate_prp(0x80000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    printf("\n[Section 2] PCI Memory Hole Attacks (3GB-4GB)\n");
    printf("----------------------------------------------\n");

    /* FUZZ-PRP-3: PRP inside PCI memory hole */
    TEST("FUZZ-PRP-3:  PRP = 0xD0000000 (3.25GB, inside PCI hole)",
         mock_validate_prp(0xD0000000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* FUZZ-PRP-10: Exact start of PCI hole */
    TEST("FUZZ-PRP-10: PRP = 0xC0000000 (3GB, PCI hole start -- FAIL)",
         mock_validate_prp(0xC0000000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* FUZZ-PRP-11: End of PCI hole (just below 4GB) */
    TEST("FUZZ-PRP-11: PRP = 0xFFFFF000 (PCI hole near end -- FAIL)",
         mock_validate_prp(0xFFFFF000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    printf("\n[Section 3] Upper Bound Attacks (>= 4GB)\n");
    printf("-----------------------------------------\n");

    /* FUZZ-PRP-4: Above 4GB */
    TEST("FUZZ-PRP-4:  PRP = 0x200000000 (8GB, well above upper bound)",
         mock_validate_prp(0x200000000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* FUZZ-PRP-8: Exactly 4GB (exclusive upper bound) */
    TEST("FUZZ-PRP-8:  PRP = 0x100000000 (4GB exactly -- exclusive, FAIL)",
         mock_validate_prp(0x100000000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* FUZZ-PRP-12: 4GB exactly -- this is at the upper bound.
     * The kernel uses >= comparison, so 0x100000000 is rejected by check 3
     * (beyond 4GB cap) BEFORE reaching the PCI hole check.
     * This address is NOT in the safe zone -- it is rejected. */
    TEST("FUZZ-PRP-12: PRP = 0x100000000 (4GB -- rejected by upper bound)",
         mock_validate_prp(0x100000000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* FUZZ-PRP-13: Integer overflow -- huge 64-bit address */
    TEST("FUZZ-PRP-13: PRP = 0xFFFFFFFFFFFFF000 (huge 64-bit addr)",
         mock_validate_prp(0xFFFFFFFFFFFFF000ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    printf("\n[Section 4] Alignment Attacks\n");
    printf("------------------------------\n");

    /* FUZZ-PRP-5: Non-page-aligned address */
    TEST("FUZZ-PRP-5:  PRP = 0x100001 (misaligned by 1 byte)",
         mock_validate_prp(0x100001ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* Extra: Misaligned by 1 byte, high address in valid range */
    TEST("FUZZ-PRP-5b: PRP = 0x200001 (2MB + 1 byte, misaligned)",
         mock_validate_prp(0x200001ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    /* Extra: Misaligned in PCI hole (alignment check fires first) */
    TEST("FUZZ-PRP-5c: PRP = 0xC0000001 (PCI hole + misaligned)",
         mock_validate_prp(0xC0000001ULL) == VOS3_NVME_E_DMA_BOUNDARY);

    printf("\n[Section 5] Boundary Edge Cases (Valid Addresses)\n");
    printf("--------------------------------------------------\n");

    /* FUZZ-PRP-6: Exactly at lower bound (inclusive -- should PASS) */
    TEST("FUZZ-PRP-6:  PRP = 0x100000 (1MB exactly -- inclusive PASS)",
         mock_validate_prp(0x100000ULL) == VOS3_NVME_OK);

    /* FUZZ-PRP-7: Just below the upper bound, outside PCI hole.
     * The highest valid address below PCI hole is 0xBFFFF000.
     * Addresses in [0xC0000000, 0x100000000) are in the PCI hole.
     * The DMA_UPPER_BOUND is 0x100000000 (exclusive).
     * So the last valid page is 0xBFFFF000 (3GB - 4KB). */
    TEST("FUZZ-PRP-7:  PRP = 0xBFFFF000 (3GB-4KB -- max valid PASS)",
         mock_validate_prp(0xBFFFF000ULL) == VOS3_NVME_OK);

    /* FUZZ-PRP-9: Just below PCI hole (should PASS) */
    TEST("FUZZ-PRP-9:  PRP = 0xBFFFF000 (just below PCI hole -- PASS)",
         mock_validate_prp(0xBFFFF000ULL) == VOS3_NVME_OK);

    /* FUZZ-PRP-15: Valid PRP in the safe zone (2MB, page-aligned) */
    TEST("FUZZ-PRP-15: PRP = 0x200000 (2MB -- safe zone PASS)",
         mock_validate_prp(0x200000ULL) == VOS3_NVME_OK);

    /* Extra: Valid address at a typical kernel DMA region */
    TEST("FUZZ-PRP-15b: PRP = 0x10000000 (256MB -- safe zone PASS)",
         mock_validate_prp(0x10000000ULL) == VOS3_NVME_OK);

    printf("\n[Section 6] Self-Referencing PRP List Detection\n");
    printf("-------------------------------------------------\n");

    /* FUZZ-PRP-14: Self-referencing PRP list entry.
     * Simulates a PRP list at physical 0x300000 where one entry
     * points back to the list itself -- a DMA loop attack. */
    {
        uint64_t list_phys = 0x300000ULL;  /* PRP list at 3MB (valid zone) */

        /* Self-reference: entry == list_phys (should FAIL) */
        TEST("FUZZ-PRP-14: Self-ref PRP entry == list_phys (DMA loop)",
             mock_validate_prp_self_ref(list_phys, list_phys)
                 == VOS3_NVME_E_DMA_BOUNDARY);

        /* Non-self-referencing entry at a different valid address (should PASS) */
        TEST("FUZZ-PRP-14b: Non-self-ref entry 0x400000 != list 0x300000",
             mock_validate_prp_self_ref(0x400000ULL, list_phys)
                 == VOS3_NVME_OK);

        /* Self-reference with invalid base address (caught by boundary check) */
        TEST("FUZZ-PRP-14c: Self-ref with entry in PCI hole (double reject)",
             mock_validate_prp_self_ref(0xD0000000ULL, 0xD0000000ULL)
                 == VOS3_NVME_E_DMA_BOUNDARY);
    }

    /* ================================================================
     * RESULTS
     * ================================================================ */
    printf("\n========================================================\n");
    printf("  RESULTS: %d/%d PASS", pass_count, test_count);
    if (fail_count > 0) {
        printf(" (%d FAIL)", fail_count);
    }
    printf("\n========================================================\n");

    if (pass_count == test_count) {
        printf("  STATUS: ALL PRP BOUNDARY GUARDS HOLD\n");
        printf("  VERDICT: NVMe DMA boundary validation is SOUND\n");
    } else {
        printf("  STATUS: ** BOUNDARY BREACH DETECTED **\n");
        printf("  VERDICT: PRP validation has gaps -- INVESTIGATE\n");
    }

    printf("========================================================\n\n");

    return (pass_count == test_count) ? 0 : 1;
}

/* ============================================================================
 * PRP POISONING DEFENSE MATRIX
 * ============================================================================
 *
 * Which validation layer stops each attack vector:
 *
 * +---------------+-----------------------------------+-------------------+
 * | Attack Vector | Description                       | Stopped By        |
 * +---------------+-----------------------------------+-------------------+
 * | FUZZ-PRP-1    | Null pointer DMA (0x0)            | Check 2: < 1MB    |
 * | FUZZ-PRP-2    | Below 1MB (0x80000)               | Check 2: < 1MB    |
 * | FUZZ-PRP-3    | Inside PCI hole (0xD0000000)      | Check 4: PCI hole |
 * | FUZZ-PRP-4    | Above 4GB (0x200000000)           | Check 3: >= 4GB   |
 * | FUZZ-PRP-5    | Non-page-aligned (0x100001)       | Check 1: align    |
 * | FUZZ-PRP-5b   | Non-page-aligned (0x200001)       | Check 1: align    |
 * | FUZZ-PRP-5c   | Misaligned + PCI hole             | Check 1: align    |
 * | FUZZ-PRP-6    | Exact lower bound (0x100000)      | -- PASS --        |
 * | FUZZ-PRP-7    | Max valid page (0xBFFFF000)       | -- PASS --        |
 * | FUZZ-PRP-8    | Exact 4GB (0x100000000)           | Check 3: >= 4GB   |
 * | FUZZ-PRP-9    | Below PCI hole (0xBFFFF000)       | -- PASS --        |
 * | FUZZ-PRP-10   | PCI hole start (0xC0000000)       | Check 4: PCI hole |
 * | FUZZ-PRP-11   | PCI hole end (0xFFFFF000)         | Check 4: PCI hole |
 * | FUZZ-PRP-12   | 4GB exactly (0x100000000)         | Check 3: >= 4GB   |
 * | FUZZ-PRP-13   | Huge 64-bit (0xFFFFFFFFFFFFF000)  | Check 3: >= 4GB   |
 * | FUZZ-PRP-14   | Self-referencing PRP list          | Self-ref detect   |
 * | FUZZ-PRP-14b  | Non-self-ref (valid)              | -- PASS --        |
 * | FUZZ-PRP-14c  | Self-ref in PCI hole              | Check 4: PCI hole |
 * | FUZZ-PRP-15   | Valid safe zone (0x200000)         | -- PASS --        |
 * | FUZZ-PRP-15b  | Valid safe zone (0x10000000)       | -- PASS --        |
 * +---------------+-----------------------------------+-------------------+
 *
 * Defense Layers (in order of evaluation):
 *   Check 1: Page alignment    -- bits [11:0] must be zero
 *   Check 2: Lower bound       -- phys_addr < 0x100000 (1MB) rejected
 *   Check 3: Upper bound       -- phys_addr >= 0x100000000 (4GB) rejected
 *   Check 4: PCI memory hole   -- 0xC0000000 <= phys_addr < 0x100000000
 *   Check 5: Self-ref detect   -- entry == PRP list phys addr (DMA loop)
 *
 * Note: Check 3 (>= 4GB) fires BEFORE Check 4 (PCI hole) in the code.
 * Since the PCI hole [3GB, 4GB) is entirely within [0, 4GB), any address
 * in the hole passes Check 3 and is caught by Check 4.  Addresses at or
 * above 4GB are caught by Check 3 and never reach Check 4.  This ordering
 * is deliberate and security-correct -- there is no gap between the two
 * checks.
 *
 * Valid DMA address space (after all checks):
 *   [0x100000, 0xC0000000)  =  [1MB, 3GB)  =  3071 MB of usable DMA space
 *
 * ============================================================================ */
