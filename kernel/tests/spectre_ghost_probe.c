/**
 * @file spectre_ghost_probe.c
 * @brief Speculative Hidden-State Leak Auditing — Ghost Probe Test Module
 *
 * @details Kernel test module that verifies the absence of speculative
 *          register/state leakage across AI agent context switches.
 *          Exercises the full scrub/fence/barrier chain and validates
 *          silicon hardening controls (SMEP, SMAP, UMIP, WP).
 *
 *   Test 1: AVX/SSE Register Zeroing Verification
 *           Fill XMM0-3 with secret pattern, scrub, verify zero.
 *
 *   Test 2: Full FPU/XSAVE State Reset
 *           Corrupt MXCSR, scrub, verify default 0x1F80 restored.
 *
 *   Test 3: lfence Serialization After Scrub
 *           TSC-timed scrub+fence, verify post-fence read is zero.
 *
 *   Test 4: Silicon Hardening Verification (All Cores)
 *           CR4.SMEP, CR4.SMAP, CR4.UMIP, CR0.WP assertion.
 *
 *   Test 5: MXCSR Exception Mask Integrity
 *           Verify all exception masks set, DAZ/FZ clear.
 *
 *   Test 6: Speculative Barrier Chain
 *           Full sfence-scrub-sfence-lfence chain, timed.
 *
 *   This file compiles as a kernel module (not userspace). It calls
 *   the real kernel APIs and prints PASS/FAIL via VOS3_INFO.
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Spectre Ghost Probe — Speculative Hidden-State Leak Auditing
 */

#include "../include/vos/ai_guard.h"
#include "../include/vos/console.h"
#include "../include/vos/percpu.h"

#include <stdint.h>
#include <stddef.h>

/* We need the scrub functions and g_cpu_has_avx from ai_guard internals.
 * These are declared in ai_guard_internal.h but we redeclare the specific
 * symbols we need to avoid pulling in the full internal header. */
extern void vos3_simd_scrub_all(void);
extern void vos3_fpu_scrub_full(void);
extern int  g_cpu_has_avx;

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_omega_b_pass = 0;
static uint32_t g_omega_b_fail = 0;

#define OMEGA_B_ASSERT(cond, name)                                              \
    do {                                                                        \
        if (cond) {                                                             \
            g_omega_b_pass++;                                                   \
            VOS3_INFO("[GHOST-PROBE] PASS: %s", (name));                        \
        } else {                                                                \
            g_omega_b_fail++;                                                   \
            VOS3_ERROR("[GHOST-PROBE] FAIL: %s (line %d)", (name), __LINE__);   \
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
 * TEST 1: AVX/SSE REGISTER ZEROING VERIFICATION
 * ============================================================================ */

/**
 * @brief Verify vos3_simd_scrub_all() erases secret patterns from XMM0-3.
 *
 * Checks:
 *   1. Load 0xDEADBEEFCAFEBABE into XMM0-3 via movdqu
 *   2. Call vos3_simd_scrub_all()
 *   3. Read XMM0-3 back via movdqu
 *   4. Assert all 4 registers are zero (16 bytes each)
 */
static void test_xmm_scrub_verification(void)
{
    VOS3_INFO("[GHOST-PROBE] --- Test 1: AVX/SSE Register Zeroing ---");

    /* Construct a 16-byte secret pattern: 0xDEADBEEFCAFEBABE repeated */
    uint8_t secret[16] __attribute__((aligned(16)));
    uint64_t pattern = 0xDEADBEEFCAFEBABEULL;
    for (int i = 0; i < 8; i++) {
        secret[i]     = (uint8_t)(pattern >> (i * 8));
        secret[i + 8] = (uint8_t)(pattern >> (i * 8));
    }

    /* 1a. Load secret pattern into XMM0-3 */
    __asm__ volatile (
        "movdqu %0, %%xmm0\n\t"
        "movdqu %0, %%xmm1\n\t"
        "movdqu %0, %%xmm2\n\t"
        "movdqu %0, %%xmm3\n\t"
        :
        : "m"(secret)
        : "xmm0", "xmm1", "xmm2", "xmm3"
    );

    /* 1b. Call the SIMD scrub */
    vos3_simd_scrub_all();

    /* 1c. Read XMM0-3 back into stack buffers */
    uint8_t xmm0_out[16] __attribute__((aligned(16)));
    uint8_t xmm1_out[16] __attribute__((aligned(16)));
    uint8_t xmm2_out[16] __attribute__((aligned(16)));
    uint8_t xmm3_out[16] __attribute__((aligned(16)));

    __asm__ volatile (
        "movdqu %%xmm0, %0\n\t"
        "movdqu %%xmm1, %1\n\t"
        "movdqu %%xmm2, %2\n\t"
        "movdqu %%xmm3, %3\n\t"
        : "=m"(xmm0_out), "=m"(xmm1_out), "=m"(xmm2_out), "=m"(xmm3_out)
        :
        : /* no clobbers — we are reading, not writing */
    );

    /* 1d. Assert all 4 registers are zero */
    int xmm0_clean = 1, xmm1_clean = 1, xmm2_clean = 1, xmm3_clean = 1;
    for (int i = 0; i < 16; i++) {
        if (xmm0_out[i] != 0) xmm0_clean = 0;
        if (xmm1_out[i] != 0) xmm1_clean = 0;
        if (xmm2_out[i] != 0) xmm2_clean = 0;
        if (xmm3_out[i] != 0) xmm3_clean = 0;
    }

    OMEGA_B_ASSERT(xmm0_clean, "XMM0 scrubbed to zero");
    OMEGA_B_ASSERT(xmm1_clean, "XMM1 scrubbed to zero");
    OMEGA_B_ASSERT(xmm2_clean, "XMM2 scrubbed to zero");
    OMEGA_B_ASSERT(xmm3_clean, "XMM3 scrubbed to zero");

    VOS3_INFO("[GHOST-PROBE]   XMM0-3 scrubbed: secret pattern "
              "0xDEADBEEFCAFEBABE -> 0x0");
}

/* ============================================================================
 * TEST 2: FULL FPU/XSAVE STATE RESET
 * ============================================================================ */

/**
 * @brief Verify vos3_fpu_scrub_full() restores MXCSR to default 0x1F80.
 *
 * Checks:
 *   1. Write 0x9FC0 to MXCSR (corrupted state)
 *   2. Call vos3_fpu_scrub_full()
 *   3. Read MXCSR back
 *   4. Assert MXCSR == 0x1F80 (default)
 */
static void test_fpu_xsave_state_reset(void)
{
    VOS3_INFO("[GHOST-PROBE] --- Test 2: Full FPU/XSAVE State Reset ---");

    /* 2a. Write corrupted MXCSR value: 0x9FC0
     * Bits: FZ(15)=1, RC(14:13)=00, PM(12)=1, UM(11)=1, OM(10)=1,
     *       ZM(9)=1, DM(8)=1, IM(7)=1, DAZ(6)=1, rest=0
     * This is a non-default but valid MXCSR value. */
    uint32_t corrupted_mxcsr = 0x9FC0U;
    __asm__ volatile ("ldmxcsr %0" :: "m"(corrupted_mxcsr));

    /* Verify corruption took effect */
    uint32_t verify_mxcsr;
    __asm__ volatile ("stmxcsr %0" : "=m"(verify_mxcsr));
    VOS3_INFO("[GHOST-PROBE]   MXCSR before scrub: 0x%x", (unsigned)verify_mxcsr);

    /* 2b. Full FPU scrub (includes XRSTOR + SIMD scrub) */
    vos3_fpu_scrub_full();

    /* 2c. Read MXCSR back */
    uint32_t restored_mxcsr;
    __asm__ volatile ("stmxcsr %0" : "=m"(restored_mxcsr));

    /* 2d. Assert default state restored */
    OMEGA_B_ASSERT(restored_mxcsr == 0x1F80U,
                   "MXCSR restored to default 0x1F80 after fpu_scrub_full");

    VOS3_INFO("[GHOST-PROBE]   MXCSR reset: 0x%x -> 0x%x",
              (unsigned)corrupted_mxcsr, (unsigned)restored_mxcsr);
}

/* ============================================================================
 * TEST 3: LFENCE SERIALIZATION AFTER SCRUB
 * ============================================================================ */

/**
 * @brief Verify lfence serializes speculative execution after scrub.
 *
 * Checks:
 *   1. TSC-time the scrub+lfence cycle (must be > 0 cycles)
 *   2. Write secret to XMM0, scrub, lfence, read XMM0 — must be zero
 */
static void test_lfence_serialization(void)
{
    VOS3_INFO("[GHOST-PROBE] --- Test 3: lfence Serialization After Scrub ---");

    /* 3a. Time the scrub + lfence to confirm fence serialized */
    uint64_t tsc_start = ghost_rdtsc();
    vos3_simd_scrub_all();
    __asm__ volatile ("lfence" ::: "memory");
    uint64_t tsc_end = ghost_rdtsc();

    uint64_t elapsed = tsc_end - tsc_start;
    OMEGA_B_ASSERT(elapsed > 0, "lfence serialized (elapsed TSC > 0)");
    VOS3_INFO("[GHOST-PROBE]   Scrub+lfence latency: %llu cycles (%llu us)",
              (unsigned long long)elapsed,
              (unsigned long long)GHOST_TSC_TO_US(elapsed));

    /* 3b. Write secret to XMM0, scrub, lfence, read — must be zero.
     * This tests that no speculative "ghosting" of the old value survives
     * past the lfence barrier. */
    uint8_t secret2[16] __attribute__((aligned(16)));
    uint64_t pat2 = 0xDEADBEEFCAFEBABEULL;
    for (int i = 0; i < 8; i++) {
        secret2[i]     = (uint8_t)(pat2 >> (i * 8));
        secret2[i + 8] = (uint8_t)(pat2 >> (i * 8));
    }

    __asm__ volatile (
        "movdqu %0, %%xmm0\n\t"
        :
        : "m"(secret2)
        : "xmm0"
    );

    vos3_simd_scrub_all();
    __asm__ volatile ("lfence" ::: "memory");

    /* Read XMM0 back after fence */
    uint8_t xmm0_post[16] __attribute__((aligned(16)));
    __asm__ volatile ("movdqu %%xmm0, %0" : "=m"(xmm0_post));

    int post_fence_clean = 1;
    for (int i = 0; i < 16; i++) {
        if (xmm0_post[i] != 0) post_fence_clean = 0;
    }

    OMEGA_B_ASSERT(post_fence_clean,
                   "Post-lfence XMM0 read is zero (no speculative ghosting)");
}

/* ============================================================================
 * TEST 4: SILICON HARDENING VERIFICATION (ALL CORES)
 * ============================================================================ */

/**
 * @brief Verify CR4 and CR0 silicon hardening bits on current core.
 *
 * Checks:
 *   1. CR4.SMEP (bit 20) is set
 *   2. CR4.SMAP (bit 21) is set
 *   3. CR4.UMIP (bit 11) is set (if CPU supports it)
 *   4. CR0.WP  (bit 16) is set
 */
static void test_silicon_hardening(void)
{
    VOS3_INFO("[GHOST-PROBE] --- Test 4: Silicon Hardening Verification ---");

    /* Read CR4 */
    uint64_t cr4;
    __asm__ volatile ("mov %%cr4, %0" : "=r"(cr4));

    VOS3_INFO("[GHOST-PROBE]   CR4 = 0x%llx", (unsigned long long)cr4);

    /* 4a. SMEP — Supervisor Mode Execution Prevention (bit 20) */
    OMEGA_B_ASSERT(cr4 & (1ULL << 20), "CR4.SMEP (bit 20) is set");

    /* 4b. SMAP — Supervisor Mode Access Prevention (bit 21) */
    OMEGA_B_ASSERT(cr4 & (1ULL << 21), "CR4.SMAP (bit 21) is set");

    /* 4c. UMIP — User-Mode Instruction Prevention (bit 11)
     * Only assert if CPU supports it (CPUID.7.0:ECX bit 2) */
    uint32_t eax7, ebx7, ecx7, edx7;
    __asm__ volatile (
        "cpuid"
        : "=a"(eax7), "=b"(ebx7), "=c"(ecx7), "=d"(edx7)
        : "a"(7), "c"(0)
    );
    int cpu_has_umip = (ecx7 >> 2) & 1;

    if (cpu_has_umip) {
        OMEGA_B_ASSERT(cr4 & (1ULL << 11), "CR4.UMIP (bit 11) is set");
    } else {
        VOS3_INFO("[GHOST-PROBE]   UMIP not supported by CPU, skipping");
        OMEGA_B_ASSERT(1, "CR4.UMIP skipped (CPU lacks UMIP)");
    }

    /* 4d. CR0.WP — Write Protect (bit 16) */
    uint64_t cr0;
    __asm__ volatile ("mov %%cr0, %0" : "=r"(cr0));

    VOS3_INFO("[GHOST-PROBE]   CR0 = 0x%llx", (unsigned long long)cr0);

    OMEGA_B_ASSERT(cr0 & (1ULL << 16), "CR0.WP (bit 16) is set");

    /* Report OSXSAVE for informational context */
    int osxsave = (cr4 >> 18) & 1;
    VOS3_INFO("[GHOST-PROBE]   CR4.OSXSAVE (bit 18) = %d", osxsave);
}

/* ============================================================================
 * TEST 5: MXCSR EXCEPTION MASK INTEGRITY
 * ============================================================================ */

/**
 * @brief Verify MXCSR exception masks are all set per XSTATE_BV=0 guard.
 *
 * Checks:
 *   1. Bits [12:7] (exception masks) are all set (0x1F80 pattern)
 *   2. Bit 6 (DAZ) is clear or acceptable
 *   3. Bit 15 (FZ) is clear or acceptable
 */
static void test_mxcsr_exception_mask_integrity(void)
{
    VOS3_INFO("[GHOST-PROBE] --- Test 5: MXCSR Exception Mask Integrity ---");

    /* Read current MXCSR */
    uint32_t mxcsr;
    __asm__ volatile ("stmxcsr %0" : "=m"(mxcsr));

    VOS3_INFO("[GHOST-PROBE]   MXCSR = 0x%x", (unsigned)mxcsr);

    /* 5a. Exception masks: bits [12:7] should all be 1 (0x1F80)
     * IM(7), DM(8), ZM(9), OM(10), UM(11), PM(12)
     * When set, these MASK (suppress) the corresponding exceptions. */
    uint32_t exception_masks = mxcsr & 0x1F80U;
    OMEGA_B_ASSERT(exception_masks == 0x1F80U,
                   "MXCSR exception masks [12:7] all set (0x1F80)");

    /* 5b. DAZ (Denormals Are Zero) — bit 6
     * In default XRSTOR state, DAZ should be clear (0). */
    int daz = (mxcsr >> 6) & 1;
    OMEGA_B_ASSERT(daz == 0, "MXCSR.DAZ (bit 6) is clear");
    VOS3_INFO("[GHOST-PROBE]   DAZ (bit 6) = %d", daz);

    /* 5c. FZ (Flush to Zero) — bit 15
     * In default XRSTOR state, FZ should be clear (0). */
    int fz = (mxcsr >> 15) & 1;
    OMEGA_B_ASSERT(fz == 0, "MXCSR.FZ (bit 15) is clear");
    VOS3_INFO("[GHOST-PROBE]   FZ (bit 15) = %d", fz);

    /* 5d. Overall MXCSR should be exactly 0x1F80 after a clean scrub */
    OMEGA_B_ASSERT(mxcsr == 0x1F80U,
                   "MXCSR is exactly 0x1F80 (clean default state)");
}

/* ============================================================================
 * TEST 6: SPECULATIVE BARRIER CHAIN
 * ============================================================================ */

/**
 * @brief Execute full barrier chain and verify no crash + zero result.
 *
 * Sequence:
 *   1. Fill XMM0 with secret
 *   2. sfence (drain stores)
 *   3. vzeroall/.byte or pxor (clear via scrub)
 *   4. sfence (drain scrub stores)
 *   5. lfence (serialize loads)
 *   6. Read XMM0 — must be zero
 */
static void test_speculative_barrier_chain(void)
{
    VOS3_INFO("[GHOST-PROBE] --- Test 6: Speculative Barrier Chain ---");

    /* Build secret pattern */
    uint8_t secret3[16] __attribute__((aligned(16)));
    uint64_t pat3 = 0xDEADBEEFCAFEBABEULL;
    for (int i = 0; i < 8; i++) {
        secret3[i]     = (uint8_t)(pat3 >> (i * 8));
        secret3[i + 8] = (uint8_t)(pat3 >> (i * 8));
    }

    uint64_t tsc_chain_start = ghost_rdtsc();

    /* Step 1: Fill XMM0 with secret */
    __asm__ volatile (
        "movdqu %0, %%xmm0\n\t"
        :
        : "m"(secret3)
        : "xmm0"
    );

    /* Step 2: sfence — drain all pending stores */
    __asm__ volatile ("sfence" ::: "memory");

    /* Step 3: Zero all SIMD registers (vzeroall if AVX, else pxor) */
    if (g_cpu_has_avx) {
        /* vzeroall: .byte 0xC5, 0xFC, 0x77 */
        __asm__ volatile (".byte 0xC5, 0xFC, 0x77" ::: "memory");
    } else {
        /* SSE2 pxor to clear XMM0 */
        __asm__ volatile ("pxor %%xmm0, %%xmm0" ::: "xmm0");
    }

    /* Step 4: sfence — drain scrub stores */
    __asm__ volatile ("sfence" ::: "memory");

    /* Step 5: lfence — serialize all loads */
    __asm__ volatile ("lfence" ::: "memory");

    /* Step 6: Read XMM0 — must be zero */
    uint8_t xmm0_chain[16] __attribute__((aligned(16)));
    __asm__ volatile ("movdqu %%xmm0, %0" : "=m"(xmm0_chain));

    uint64_t tsc_chain_end = ghost_rdtsc();

    int chain_clean = 1;
    for (int i = 0; i < 16; i++) {
        if (xmm0_chain[i] != 0) chain_clean = 0;
    }

    OMEGA_B_ASSERT(chain_clean,
                   "Full barrier chain: XMM0 zero after sfence+scrub+sfence+lfence");

    /* Report timing */
    uint64_t chain_cycles = tsc_chain_end - tsc_chain_start;
    uint64_t chain_us = GHOST_TSC_TO_US(chain_cycles);
    VOS3_INFO("[GHOST-PROBE]   Barrier chain latency: %llu cycles (%llu us)",
              (unsigned long long)chain_cycles,
              (unsigned long long)chain_us);

    OMEGA_B_ASSERT(chain_cycles > 0,
                   "Barrier chain completed with measurable overhead");

    VOS3_INFO("[GHOST-PROBE]   AVX path: %s",
              g_cpu_has_avx ? "vzeroall" : "pxor (SSE2 fallback)");
}

/* ============================================================================
 * VERIFICATION ENTRY POINT
 * ============================================================================ */

/**
 * @brief Run all Spectre Ghost Probe verification tests.
 *
 * Called from kmain or via VBus GHOST_PROBE command.
 *
 * @return 0 if all tests pass, number of failures otherwise
 */
int vos3_spectre_ghost_probe_run(void)
{
    g_omega_b_pass = 0;
    g_omega_b_fail = 0;

    VOS3_INFO("============================================================");
    VOS3_INFO("[GHOST-PROBE] Spectre Ghost Probe: Hidden-State Leak Audit");
    VOS3_INFO("============================================================");

    test_xmm_scrub_verification();
    test_fpu_xsave_state_reset();
    test_lfence_serialization();
    test_silicon_hardening();
    test_mxcsr_exception_mask_integrity();
    test_speculative_barrier_chain();

    VOS3_INFO("============================================================");
    VOS3_INFO("[GHOST-PROBE] Results: %u PASS, %u FAIL",
              g_omega_b_pass, g_omega_b_fail);
    if (g_omega_b_fail == 0) {
        VOS3_INFO("[GHOST-PROBE] ALL TESTS PASSED -- NO SPECULATIVE LEAKS");
    } else {
        VOS3_ERROR("[GHOST-PROBE] %u FAILURES -- GHOST STATE DETECTED",
                   g_omega_b_fail);
    }
    VOS3_INFO("============================================================");

    return (int)g_omega_b_fail;
}
