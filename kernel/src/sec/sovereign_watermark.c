/**
 * @file sovereign_watermark.c
 * @brief VOS3 Genesis RC1 -- Sovereign Build Watermark
 *
 * Prints build-mode metadata at end of boot_ai_init().
 * In production builds, confirms audit suite stripped and
 * crypto/AI paths optimized to -O3.
 */

#include "../../include/vos/console.h"
#include <stdint.h>

#ifndef VOS3_PRODUCTION_BUILD
static const char g_build_mode[] = "DEBUG";
#else
static const char g_build_mode[] = "GENESIS_RC1";
#endif

void vos3_print_sovereign_watermark(void)
{
    VOS3_INFO("[SOVEREIGN] VOS3 Build Mode: %s", g_build_mode);
    VOS3_INFO("[SOVEREIGN] Guardian Seal: Runtime SHA-256 of .text");
#ifdef VOS3_PRODUCTION_BUILD
    VOS3_INFO("[SOVEREIGN] Audit Suite: STRIPPED");
    VOS3_INFO("[SOVEREIGN] Optimization: -O3 (crypto/ai), -O2 (core)");
#else
    VOS3_INFO("[SOVEREIGN] Audit Suite: ACTIVE (26 phase tests)");
    VOS3_INFO("[SOVEREIGN] Optimization: -O2 -g (debug)");
#endif
}
