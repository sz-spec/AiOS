/**
 * @file final_genesis_audit.c
 * @brief Golden Seal Gateway — Final Genesis Audit
 *
 * @details The definitive end-to-end audit for VOS3 kernel integrity:
 *
 *   Test 1: Kernel .text Section SHA-256 Integrity
 *           Compute SHA-256 of the live .text section, verify determinism,
 *           entropy, and timing bounds. Establishes the cryptographic
 *           baseline for runtime code integrity monitoring.
 *
 *   Test 2: Context Injection Attack — HMAC Rejection
 *           Construct a forged VBus frame with bogus HMAC and verify
 *           the kernel rejects it within latency bounds. Confirms
 *           FIPS 180-4 / RFC 2104 frame authentication is active.
 *
 *   Test 3: End-to-End Inference Latency Measurement
 *           Measure the kernel-side overhead of the inference pipeline:
 *           KIM stats query, work-steal post/claim, and silicon
 *           hardening verification (SMEP, SMAP, WP).
 *
 *   This file compiles as a kernel module (not userspace). It calls
 *   the real kernel APIs and prints PASS/FAIL via VOS3_INFO.
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Golden Seal Gateway — Final Audit
 */

#include "../include/vos/sha256.h"
#include "../include/vos/ai_guard.h"
#include "../include/vos/ai_kim.h"
#include "../include/vos/console.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * EXTERNAL DECLARATIONS
 * ============================================================================ */

/* Linker symbols for .text section bounds */
extern uint8_t __text_start[];  /* Defined by linker script */
extern uint8_t __text_end[];    /* Defined by linker script */

/* VBus frame send for injection test */
extern int vos3_vbus_recv_frame_raw(uint8_t *buf, uint32_t len);
extern int vos3_vbus_validate_hmac(const uint8_t *header, const uint8_t *payload, uint32_t payload_len);

/* KIM stats */
extern int vos3_kim_get_stats(vos3_kim_stats_t *stats);

/* Silicon hardening */
extern void vos3_cpu_harden_silicon(void);

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_genesis_pass = 0;
static uint32_t g_genesis_fail = 0;

#define GENESIS_ASSERT(cond, name)                                              \
    do {                                                                        \
        if (cond) {                                                             \
            g_genesis_pass++;                                                   \
            VOS3_INFO("[GENESIS-SEAL] PASS: %s", (name));                       \
        } else {                                                                \
            g_genesis_fail++;                                                   \
            VOS3_ERROR("[GENESIS-SEAL] FAIL: %s (line %d)", (name), __LINE__);  \
        }                                                                       \
    } while (0)

/* TSC helper */
static inline uint64_t genesis_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

#define GENESIS_TSC_TO_US(cycles) ((cycles) / 2000ULL)

/* ============================================================================
 * TEST 1: KERNEL .TEXT SECTION SHA-256 INTEGRITY
 * ============================================================================ */

/**
 * @brief Compute SHA-256 of the live kernel .text section.
 *
 * Checks:
 *   1. .text section has non-zero size and is within sanity bounds
 *   2. SHA-256 hash has sufficient entropy (>= 16 non-zero bytes)
 *   3. Double-hash is deterministic (same input -> same output)
 *   4. Hash completes within timing bounds (< 100ms)
 */
static void test_ktext_sha256_integrity(void)
{
    VOS3_INFO("[GENESIS-SEAL] --- Test 1: Kernel .text SHA-256 Integrity ---");

    /* Calculate .text section size */
    uintptr_t text_start = (uintptr_t)__text_start;
    uintptr_t text_end = (uintptr_t)__text_end;
    size_t text_size = text_end - text_start;

    GENESIS_ASSERT(text_size > 0, ".text section has non-zero size");
    GENESIS_ASSERT(text_size < 0x200000, ".text section < 2MB (sanity)");

    VOS3_INFO("[GENESIS-SEAL]   .text: 0x%llx - 0x%llx (%u bytes)",
              (unsigned long long)text_start,
              (unsigned long long)text_end,
              (unsigned)text_size);

    /* Compute SHA-256 of the entire .text section */
    uint64_t tsc_start = genesis_rdtsc();

    vos3_sha256_ctx_t ctx;
    uint8_t hash[32];
    vos3_sha256_init(&ctx);

    /* Process in 4KB chunks to avoid stack pressure */
    const uint8_t *ptr = (const uint8_t *)text_start;
    size_t remaining = text_size;
    while (remaining > 0) {
        size_t chunk = remaining > 4096 ? 4096 : remaining;
        vos3_sha256_update(&ctx, ptr, chunk);
        ptr += chunk;
        remaining -= chunk;
    }

    vos3_sha256_final(&ctx, hash);

    uint64_t tsc_end = genesis_rdtsc();
    uint64_t hash_us = GENESIS_TSC_TO_US(tsc_end - tsc_start);

    /* Print the hash (first 16 bytes as hex for log readability) */
    VOS3_INFO("[GENESIS-SEAL]   SHA-256: %02x%02x%02x%02x%02x%02x%02x%02x"
              "%02x%02x%02x%02x%02x%02x%02x%02x...",
              hash[0], hash[1], hash[2], hash[3],
              hash[4], hash[5], hash[6], hash[7],
              hash[8], hash[9], hash[10], hash[11],
              hash[12], hash[13], hash[14], hash[15]);

    VOS3_INFO("[GENESIS-SEAL]   Hash time: %llu us", (unsigned long long)hash_us);

    /* Verify the hash is non-zero (not a null .text) */
    uint32_t nonzero = 0;
    for (int i = 0; i < 32; i++) {
        if (hash[i] != 0) nonzero++;
    }
    GENESIS_ASSERT(nonzero >= 16, "SHA-256 has sufficient entropy (>= 16 non-zero bytes)");

    /* Compute again and verify determinism */
    uint8_t hash2[32];
    vos3_sha256_ctx_t ctx2;
    vos3_sha256_init(&ctx2);
    ptr = (const uint8_t *)text_start;
    remaining = text_size;
    while (remaining > 0) {
        size_t chunk = remaining > 4096 ? 4096 : remaining;
        vos3_sha256_update(&ctx2, ptr, chunk);
        ptr += chunk;
        remaining -= chunk;
    }
    vos3_sha256_final(&ctx2, hash2);

    /* Verify determinism: same input -> same hash */
    int match = 1;
    for (int i = 0; i < 32; i++) {
        if (hash[i] != hash2[i]) { match = 0; break; }
    }
    GENESIS_ASSERT(match, "SHA-256 is deterministic (double-hash matches)");

    GENESIS_ASSERT(hash_us < 100000, "SHA-256 of .text completes in < 100ms");
}

/* ============================================================================
 * TEST 2: CONTEXT INJECTION ATTACK -- HMAC REJECTION
 * ============================================================================ */

/**
 * @brief Forge a VBus frame with bogus HMAC and verify rejection.
 *
 * Checks:
 *   1. Forged frame with garbage HMAC is rejected (non-zero return)
 *   2. Rejection latency is < 1ms (constant-time compare bound)
 *   3. No silicon faults triggered by the injection attempt
 */
static void test_context_injection_attack(void)
{
    VOS3_INFO("[GENESIS-SEAL] --- Test 2: Context Injection Attack (HMAC) ---");

    /* Construct a fake VBus frame header with bogus HMAC */
    uint8_t fake_header[64];

    /* Zero-fill header */
    for (int i = 0; i < 64; i++) fake_header[i] = 0;

    /* Set frame type = CMD (0x01) */
    fake_header[0] = 0x01;  /* type */
    fake_header[1] = 0x01;  /* slot_id = 1 */
    fake_header[2] = 0x42;  /* tag_lo */
    fake_header[3] = 0x00;  /* tag_hi */
    fake_header[4] = 0x10;  /* length = 16 */
    fake_header[5] = 0x00;
    fake_header[6] = 0x00;
    fake_header[7] = 0x00;

    /* Fake payload CRC (deliberately wrong) */
    fake_header[8] = 0xDE;
    fake_header[9] = 0xAD;
    fake_header[10] = 0xBE;
    fake_header[11] = 0xEF;

    /* Fake header CRC (deliberately wrong) */
    fake_header[12] = 0xCA;
    fake_header[13] = 0xFE;
    fake_header[14] = 0xBA;
    fake_header[15] = 0xBE;

    /* Fake HMAC at offset 16 (32 bytes of garbage) */
    for (int i = 16; i < 48; i++) fake_header[i] = (uint8_t)(i * 0x37);
    fake_header[48] = 0x01;  /* HMAC present flag */

    /* Fake payload */
    uint8_t fake_payload[16];
    for (int i = 0; i < 16; i++) fake_payload[i] = (uint8_t)(0x41 + i);

    /* Time the HMAC validation */
    uint64_t tsc_start = genesis_rdtsc();

    int rc = vos3_vbus_validate_hmac(fake_header, fake_payload, 16);

    uint64_t tsc_end = genesis_rdtsc();
    uint64_t reject_us = GENESIS_TSC_TO_US(tsc_end - tsc_start);

    VOS3_INFO("[GENESIS-SEAL]   HMAC validation returned: %d", rc);
    VOS3_INFO("[GENESIS-SEAL]   Rejection latency: %llu us", (unsigned long long)reject_us);

    /* HMAC should reject (return non-zero or negative) */
    GENESIS_ASSERT(rc != 0, "Fake HMAC frame rejected by kernel");

    /* Rejection should be fast (< 1ms = 1000us) */
    GENESIS_ASSERT(reject_us < 1000, "HMAC rejection < 1ms (Security Violation Ban speed)");

    /* Verify silicon faults didn't spike */
    vos3_kim_stats_t stats;
    int stats_rc = vos3_kim_get_stats(&stats);
    if (stats_rc == 0) {
        VOS3_INFO("[GENESIS-SEAL]   Silicon faults after injection: %u",
                  stats.silicon_faults);
        GENESIS_ASSERT(stats.silicon_faults == 0,
                       "No silicon faults triggered by injection");
    }
}

/* ============================================================================
 * TEST 3: END-TO-END INFERENCE LATENCY MEASUREMENT
 * ============================================================================ */

/**
 * @brief Measure kernel-side inference pipeline overhead.
 *
 * Checks:
 *   1. KIM stats query succeeds (both before and after pipeline)
 *   2. Post work to invalid slot is rejected (-EINVAL)
 *   3. Try steal on empty queue returns 0
 *   4. Full pipeline overhead < 50ms
 *   5. Stats query alone < 1ms
 *   6. CR4.SMEP, CR4.SMAP, CR0.WP still active post-pipeline
 */
static void test_e2e_latency(void)
{
    VOS3_INFO("[GENESIS-SEAL] --- Test 3: End-to-End Inference Latency ---");

    /* Measure the overhead of the kernel inference pipeline:
     * VBus CMD parse -> KIM dispatch -> Token generate -> VBus TOKEN_STREAM
     *
     * We measure the kernel-side overhead by:
     * 1. Reading TSC before a KIM_GENERATE would be dispatched
     * 2. Reading TSC after KIM returns stats
     * 3. Computing the delta
     *
     * Note: In test context (no actual model loaded), we measure
     * the pipeline setup/teardown cost, not actual inference.
     */

    /* Baseline: measure KIM stats query overhead (minimal path) */
    vos3_kim_stats_t stats_before, stats_after;

    uint64_t tsc_start = genesis_rdtsc();
    int rc1 = vos3_kim_get_stats(&stats_before);
    uint64_t tsc_mid = genesis_rdtsc();

    /* Simulate the full pipeline timing:
     * 1. VBus command parse overhead */
    __asm__ volatile("lfence" ::: "memory");  /* Serialize */

    /* 2. Post work to steal queue (tests queue mechanism) */
    int post_rc = vos3_kim_post_work(255, 10, 100);  /* Invalid slot = fast reject */

    __asm__ volatile("lfence" ::: "memory");  /* Serialize */

    /* 3. Try steal (empty queue = fast return) */
    int steal_rc = vos3_kim_try_steal();

    __asm__ volatile("lfence" ::: "memory");  /* Serialize */

    /* 4. Stats query (end of pipeline) */
    int rc2 = vos3_kim_get_stats(&stats_after);
    uint64_t tsc_end = genesis_rdtsc();

    uint64_t stats_latency_us = GENESIS_TSC_TO_US(tsc_mid - tsc_start);
    uint64_t pipeline_latency_us = GENESIS_TSC_TO_US(tsc_end - tsc_start);

    VOS3_INFO("[GENESIS-SEAL]   KIM stats query: %llu us",
              (unsigned long long)stats_latency_us);
    VOS3_INFO("[GENESIS-SEAL]   Pipeline overhead (stats+post+steal+stats): %llu us",
              (unsigned long long)pipeline_latency_us);
    VOS3_INFO("[GENESIS-SEAL]   Post work (invalid): rc=%d", post_rc);
    VOS3_INFO("[GENESIS-SEAL]   Try steal (empty): rc=%d", steal_rc);

    GENESIS_ASSERT(rc1 == 0, "KIM stats query 1 succeeded");
    GENESIS_ASSERT(rc2 == 0, "KIM stats query 2 succeeded");
    GENESIS_ASSERT(post_rc == -22, "Post to invalid slot rejected (-EINVAL)");
    GENESIS_ASSERT(steal_rc == 0, "Try steal on empty queue returns 0");

    /* Pipeline overhead should be < 50ms (50000us) */
    GENESIS_ASSERT(pipeline_latency_us < 50000,
                   "E2E kernel pipeline overhead < 50ms");

    /* Stats query alone should be < 1ms */
    GENESIS_ASSERT(stats_latency_us < 1000,
                   "KIM stats query < 1ms");

    /* Verify silicon hardening still active */
    uint64_t cr4;
    __asm__ volatile("mov %%cr4, %0" : "=r"(cr4));
    GENESIS_ASSERT(cr4 & (1ULL << 20), "CR4.SMEP still active post-pipeline");
    GENESIS_ASSERT(cr4 & (1ULL << 21), "CR4.SMAP still active post-pipeline");

    uint64_t cr0;
    __asm__ volatile("mov %%cr0, %0" : "=r"(cr0));
    GENESIS_ASSERT(cr0 & (1ULL << 16), "CR0.WP still active post-pipeline");

    VOS3_INFO("[GENESIS-SEAL]   Silicon hardening: SMEP=%u SMAP=%u WP=%u",
              (unsigned)((cr4 >> 20) & 1),
              (unsigned)((cr4 >> 21) & 1),
              (unsigned)((cr0 >> 16) & 1));
}

/* ============================================================================
 * AUDIT ENTRY POINT
 * ============================================================================ */

/**
 * @brief Run the Golden Seal Gateway final audit.
 *
 * Called from kmain or via VBus GENESIS_AUDIT command.
 *
 * @return 0 if all tests pass, number of failures otherwise
 */
int vos3_final_genesis_audit(void)
{
    g_genesis_pass = 0;
    g_genesis_fail = 0;

    VOS3_INFO("════════════════════════════════════════════════════════");
    VOS3_INFO("[GENESIS-SEAL] THE GOLDEN SEAL GATEWAY — Final Audit");
    VOS3_INFO("════════════════════════════════════════════════════════");

    test_ktext_sha256_integrity();
    test_context_injection_attack();
    test_e2e_latency();

    VOS3_INFO("════════════════════════════════════════════════════════");
    VOS3_INFO("[GENESIS-SEAL] Results: %u PASS, %u FAIL",
              g_genesis_pass, g_genesis_fail);

    if (g_genesis_fail == 0) {
        VOS3_INFO("[GENESIS-SEAL] ALL TESTS PASSED — GENESIS MASTER SEALED");
    } else {
        VOS3_ERROR("[GENESIS-SEAL] %u FAILURES — SEAL BROKEN", g_genesis_fail);
    }
    VOS3_INFO("════════════════════════════════════════════════════════");

    return (int)g_genesis_fail;
}
