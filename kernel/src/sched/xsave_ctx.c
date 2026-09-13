/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 XSAVE / XRSTOR — context-save primitives
 * ==============================================
 *
 *   [OLYMPUS-FIX APEX-HOME v21.2.0] — Operation ARMED-SUPREMACY  2026-05-02
 *
 * Production-shape inline-assembly wrappers around the x86_64 XSAVE
 * and XRSTOR family. Pairs with the detection scaffold in
 * kernel/src/arch/x86_64/xsave.c.
 *
 * Activation discipline:
 *
 *   - This file is GATED behind VOS3_XSAVE_LIVE. When the flag is
 *     undefined (the default for every shipped build today), the
 *     functions below are inert: they exist as compilable symbols
 *     so callers do not see "undefined reference" link errors when
 *     they wire the call into context_switch.S, but the bodies are
 *     no-ops.
 *
 *   - When VOS3_XSAVE_LIVE is defined, the bodies issue real XSAVE
 *     and XRSTOR instructions. Activating the flag without ALSO
 *     having (a) CR4.OSXSAVE set in boot, (b) XCR0 with the desired
 *     feature bits, and (c) a per-task XSAVE area allocated by
 *     vos3_task_t will result in #UD or corrupted SIMD state. Each
 *     of those is a separate, named v21.x follow-up tag — see
 *     §9.6 of docs/audit/TOTAL_INTEGRITY_120_REPORT.md.
 *
 * Why a separate file:
 *
 *   Putting the inline asm here (vs in scheduler.c or context_switch.S)
 *   keeps three benefits:
 *     1. The C compiler emits the XSAVE / XRSTOR bytes in a way the
 *        toolchain can verify (constraint matching), instead of
 *        having to hand-roll a binary in .S.
 *     2. The activation flag is one #ifdef instead of a Makefile
 *        section toggle.
 *     3. Future work that swaps XSAVE for XSAVEOPT or XSAVES (per
 *        Intel SDM Vol.1 §13.10) is local to this file.
 */

#include <stdint.h>
#include <stddef.h>

/* The XCR0 mask that the kernel wants to save. Today this is x87 + SSE
 * + AVX (bits 0+1+2 = 0x7). When the AVX-512 boot wiring lands, the
 * caller side will pass 0xE7 (adds OPMASK + ZMM_HI256 + HI16). The
 * mask is split into two 32-bit halves because XSAVE takes EDX:EAX. */
#define VOS3_XSAVE_DEFAULT_MASK_LO   0x00000007u  /* x87 + SSE + AVX */
#define VOS3_XSAVE_DEFAULT_MASK_HI   0x00000000u

/**
 * Save extended CPU state into `area`.
 *
 * @param area    Pointer to a VOS3_XSAVE_AREA_BYTES-aligned region
 *                whose size is at least vos3_xsave_get_area_size().
 *                Caller is responsible for both. The XSAVE instruction
 *                requires the area to be 64-byte aligned.
 * @param mask_lo Low 32 bits of XCR0 mask to save (0 = use default).
 * @param mask_hi High 32 bits of XCR0 mask (typically 0).
 *
 * @note Inert when VOS3_XSAVE_LIVE is undefined.
 */
void vos3_xsave_save(void *area, uint32_t mask_lo, uint32_t mask_hi)
{
#ifdef VOS3_XSAVE_LIVE
    if (mask_lo == 0u && mask_hi == 0u) {
        mask_lo = VOS3_XSAVE_DEFAULT_MASK_LO;
        mask_hi = VOS3_XSAVE_DEFAULT_MASK_HI;
    }
    /* XSAVE: opcode 0F AE /4. Memory operand in (%[a]); EDX:EAX = mask. */
    __asm__ __volatile__ (
        "xsave64 (%[a])"
        :
        : [a] "r"(area), "a"(mask_lo), "d"(mask_hi)
        : "memory"
    );
#else
    (void)area;
    (void)mask_lo;
    (void)mask_hi;
#endif
}

/**
 * Restore extended CPU state from `area`.
 *
 * Same alignment + size requirements as vos3_xsave_save. The mask must
 * match (or be a strict subset of) what was used at save time.
 *
 * @note Inert when VOS3_XSAVE_LIVE is undefined.
 */
void vos3_xsave_restore(const void *area, uint32_t mask_lo, uint32_t mask_hi)
{
#ifdef VOS3_XSAVE_LIVE
    if (mask_lo == 0u && mask_hi == 0u) {
        mask_lo = VOS3_XSAVE_DEFAULT_MASK_LO;
        mask_hi = VOS3_XSAVE_DEFAULT_MASK_HI;
    }
    /* XRSTOR64: opcode 0F AE /5. Same operand discipline as XSAVE. */
    __asm__ __volatile__ (
        "xrstor64 (%[a])"
        :
        : [a] "r"(area), "a"(mask_lo), "d"(mask_hi)
        : "memory"
    );
#else
    (void)area;
    (void)mask_lo;
    (void)mask_hi;
#endif
}

/**
 * Compile-time advertisement: is the live path active in this build?
 * Useful for boot-time logging so an operator can see whether the
 * kernel they booted has SIMD context-switching enabled.
 */
int vos3_xsave_live_path_compiled(void)
{
#ifdef VOS3_XSAVE_LIVE
    return 1;
#else
    return 0;
#endif
}

/* ============================================================================
 * HW-1 (M1) — Per-task save/restore wrappers
 * ============================================================================
 *
 * Scheduler.c calls these around vos3_context_switch(). They are the
 * intended entry points for context-switch xstate handling — calling
 * vos3_xsave_save/restore directly from scheduler.c would require the
 * caller to compute the area pointer + masks, and to know whether the
 * task has an allocated area at all.
 *
 * NULL-tolerant: tasks without an xsave_area (probe failed at boot,
 * or kzalloc failed at task_create) are silently skipped — same
 * effective behavior as the v20.x scheduler.
 *
 * Both functions are inert when VOS3_HW_XSAVE is not defined.
 */
#include "../../include/vos/task.h"

void vos3_xsave_save_for_task(struct vos3_task *t)
{
#ifdef VOS3_HW_XSAVE
    if (t == ((void *)0)) return;
    if (t->xsave_area == ((void *)0)) return;
    vos3_xsave_save(t->xsave_area,
                    VOS3_XSAVE_DEFAULT_MASK_LO,
                    VOS3_XSAVE_DEFAULT_MASK_HI);
#else
    (void)t;
#endif
}

void vos3_xsave_restore_for_task(struct vos3_task *t)
{
#ifdef VOS3_HW_XSAVE
    if (t == ((void *)0)) return;
    if (t->xsave_area == ((void *)0)) return;
    vos3_xsave_restore(t->xsave_area,
                       VOS3_XSAVE_DEFAULT_MASK_LO,
                       VOS3_XSAVE_DEFAULT_MASK_HI);
#else
    (void)t;
#endif
}
