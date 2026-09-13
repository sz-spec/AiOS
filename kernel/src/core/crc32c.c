/**
 * @file crc32c.c
 * @brief VOS3 CRC32C (Castagnoli, polynomial 0x82F63B78) — Canonical Implementation
 *
 * @details Single shared CRC32C for all kernel subsystems (VBus, DMA, clipboard).
 *          - Software path: 256-entry lookup table (byte-by-byte)
 *          - Hardware path: SSE4.2 crc32q (8-byte) + crc32b (tail) instructions
 *          - Auto-detects SSE4.2 via CPUID at initialization
 *
 *          Note: IEEE CRC32 (polynomial 0xEDB88320) in vos3_config.c is a
 *          *different* algorithm and is intentionally kept separate.
 *
 * @version 1.0.0
 * @date 2026-04-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/crc32c.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * STATIC STATE
 * ============================================================================ */

static int      g_crc32c_has_sse42  = 0;
static uint32_t g_crc32c_table[256];
static int      g_crc32c_ready      = 0;

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

void crc32c_init(void)
{
    if (g_crc32c_ready)
        return;

    /* Build 256-entry lookup table for software CRC32C */
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (int j = 0; j < 8; j++) {
            if (crc & 1)
                crc = (crc >> 1) ^ 0x82F63B78U;  /* Castagnoli polynomial */
            else
                crc = crc >> 1;
        }
        g_crc32c_table[i] = crc;
    }

    /* Detect SSE4.2 hardware CRC32C support */
    uint32_t eax, ebx, ecx, edx;
    vos3_cpuid(VOS3_CPUID_FEATURES, 0, &eax, &ebx, &ecx, &edx);
    g_crc32c_has_sse42 = (ecx & VOS3_CPU_FEAT_SSE42) ? 1 : 0;

    g_crc32c_ready = 1;
}

int crc32c_has_hw(void)
{
    return g_crc32c_has_sse42;
}

/* ============================================================================
 * INCREMENTAL SOFTWARE PATH (seeded, returns running state)
 * ============================================================================ */

static uint32_t crc32c_sw_update(uint32_t state, const uint8_t *p, size_t len)
{
    for (size_t i = 0; i < len; i++)
        state = g_crc32c_table[(state ^ p[i]) & 0xFF] ^ (state >> 8);
    return state;
}

/* ============================================================================
 * INCREMENTAL HARDWARE PATH (SSE4.2 crc32q/crc32b instructions)
 * ============================================================================ */

static uint32_t crc32c_hw_update(uint32_t state, const uint8_t *p, size_t len)
{
    uint64_t crc = (uint64_t)state;

    /* Process 8 bytes at a time via crc32q */
    while (len >= 8) {
        uint64_t val;
        __builtin_memcpy(&val, p, 8);
        __asm__ volatile("crc32q %1, %0" : "+r"(crc) : "r"(val));
        p += 8;
        len -= 8;
    }

    /* Tail bytes via crc32b — use memory operand (r/m8 required) */
    while (len > 0) {
        __asm__ volatile("crc32b %1, %0" : "+r"(crc) : "m"(*p));
        p++;
        len--;
    }

    return (uint32_t)crc;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

uint32_t crc32c_update(uint32_t state, const void *data, size_t len)
{
    if (!g_crc32c_ready)
        crc32c_init();

    if (g_crc32c_has_sse42)
        return crc32c_hw_update(state, (const uint8_t *)data, len);
    return crc32c_sw_update(state, (const uint8_t *)data, len);
}

uint32_t crc32c(const void *data, size_t len)
{
    uint32_t state = crc32c_update(CRC32C_INIT, data, len);
    return crc32c_finish(state);
}
