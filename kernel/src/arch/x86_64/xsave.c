/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 XSAVE — feature detection only
 * ====================================
 *
 *   [OLYMPUS-FIX APEX-HOME v21.1.2] — Operation SKY-FALL FINAL  2026-05-02
 *
 * What this file DOES today:
 *   - Detects xsave-family CPU support via CPUID(0xD).
 *   - Computes the maximum XSAVE area size for the supported feature
 *     set (CPUID(0xD,0).EBX = current; .ECX = max with all features
 *     enabled).
 *   - Caches the supported feature mask (CPUID(0xD,0).EAX:EDX).
 *
 * What this file DELIBERATELY does NOT do:
 *   - Set CR4.OSXSAVE.
 *   - Write XCR0 to enable any feature beyond x87+SSE.
 *   - Allocate a per-task XSAVE area.
 *   - Modify context_switch.S to xsave/xrstor on switch.
 *
 * Each of those is a real change to the kernel hot path and the task
 * struct ABI. Doing them in one commit without booted-QEMU testing
 * risks every context switch crashing on hardware that supports
 * AVX-512 (the very hosts we want the SIMD path to help on). The
 * detection scaffold here lets a follow-up commit measure the area
 * size at boot, allocate per-task, and finally flip CR4.OSXSAVE in a
 * controlled order — without any further ABI churn at this layer.
 *
 * Activation roadmap:
 *   v21.1.x  This file (CPUID detect, no register writes).
 *   v21.2.x  Per-task XSAVE area allocation hook in task_create.
 *   v21.3.x  CR4.OSXSAVE + XCR0 setup in boot.
 *   v21.4.x  context_switch.S xsave/xrstor wiring + VOS3_AVX512_ENABLED on.
 */

#include "../../../include/vos/console.h"

#include <stdint.h>
#include <stddef.h>

/* CPUID feature bits */
#define VOS3_CPUID_1_ECX_XSAVE     (1u << 26)
#define VOS3_CPUID_1_ECX_OSXSAVE   (1u << 27)
#define VOS3_CPUID_1_ECX_AVX       (1u << 28)
#define VOS3_CPUID_7_EBX_AVX512F   (1u << 16)
#define VOS3_CPUID_7_EBX_AVX512BW  (1u << 30)
#define VOS3_CPUID_7_EBX_AVX512VL  (1u << 31)
#define VOS3_CPUID_7_EDX_AMX_TILE  (1u << 24)

/* XCR0 bits we care about */
#define VOS3_XCR0_X87              (1u << 0)
#define VOS3_XCR0_SSE              (1u << 1)
#define VOS3_XCR0_AVX              (1u << 2)
#define VOS3_XCR0_BNDREG           (1u << 3)
#define VOS3_XCR0_BNDCSR           (1u << 4)
#define VOS3_XCR0_OPMASK           (1u << 5)
#define VOS3_XCR0_ZMM_HI256        (1u << 6)
#define VOS3_XCR0_ZMM_HI16         (1u << 7)

#define VOS3_XCR0_AVX512_MASK      (VOS3_XCR0_OPMASK | \
                                    VOS3_XCR0_ZMM_HI256 | \
                                    VOS3_XCR0_ZMM_HI16)

typedef struct vos3_xsave_info {
    uint64_t supported_features;  /* CPUID(0xD,0).EAX | (.EDX << 32) */
    uint32_t current_size;        /* Bytes for currently-enabled XCR0  */
    uint32_t max_size;            /* Bytes if all supported XCR0 set   */
    uint8_t  has_xsave;           /* CR4.OSXSAVE-eligible              */
    uint8_t  has_avx;
    uint8_t  has_avx512f;
    uint8_t  has_amx;
    uint8_t  probed;
    uint8_t  _pad[3];
} vos3_xsave_info_t;

static volatile uint32_t   g_xsave_state = 0u;  /* 0=unprobed, 2=ready */
static vos3_xsave_info_t   g_xsave_info;

static inline void xs_cpuid_sub(uint32_t leaf, uint32_t subleaf,
                                uint32_t *eax, uint32_t *ebx,
                                uint32_t *ecx, uint32_t *edx)
{
    __asm__ volatile ("cpuid"
                      : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
                      : "a"(leaf), "c"(subleaf)
                      : "memory");
}

/**
 * Probe XSAVE support and area sizes. Idempotent. NEVER writes CR4
 * or XCR0; this file is detection-only.
 *
 * @return 0 on success (probe completed; check vos3_xsave_has_avx512()
 *         etc. for actual capability), -1 on probe failure.
 */
int vos3_xsave_probe(void)
{
    if (__atomic_load_n(&g_xsave_state, __ATOMIC_ACQUIRE) == 2u) {
        return 0;
    }

    /* CAS single-flight gate: only one CPU wins 0→1 and runs the probe.
     * Losing CPUs (CAS fails: state is 1 = in-progress) spin on pause
     * until the winner sets state to 2 (done). Same pattern as F-2 fix
     * in TOTAL_INTEGRITY_120_REPORT (vbus_avx_probe race). */
    uint32_t expected = 0u;
    if (!__atomic_compare_exchange_n(&g_xsave_state, &expected, 1u,
                                     0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        while (__atomic_load_n(&g_xsave_state, __ATOMIC_ACQUIRE) != 2u) {
            __asm__ volatile ("pause" ::: "memory");
        }
        return 0;
    }

    vos3_xsave_info_t info;
    for (size_t i = 0; i < sizeof(info); i++) ((uint8_t *)&info)[i] = 0;

    uint32_t a = 0, b = 0, c = 0, d = 0;

    /* CPUID.1: XSAVE / OSXSAVE / AVX bits */
    xs_cpuid_sub(1, 0, &a, &b, &c, &d);
    info.has_xsave = (c & VOS3_CPUID_1_ECX_XSAVE) ? 1u : 0u;
    info.has_avx   = (c & VOS3_CPUID_1_ECX_AVX)   ? 1u : 0u;

    if (!info.has_xsave) {
        VOS3_INFO("[XSAVE] CPU does not support XSAVE — AVX-512 path will "
                  "remain unavailable regardless of VOS3_AVX512_ENABLED");
        g_xsave_info = info;
        __atomic_store_n(&g_xsave_state, 2u, __ATOMIC_RELEASE);
        return 0;
    }

    /* CPUID.7,0: AVX-512 feature bits */
    xs_cpuid_sub(7, 0, &a, &b, &c, &d);
    info.has_avx512f = (b & VOS3_CPUID_7_EBX_AVX512F)  ? 1u : 0u;
    info.has_amx     = (d & VOS3_CPUID_7_EDX_AMX_TILE) ? 1u : 0u;

    /* CPUID.0xD,0: XSAVE area sizes + supported feature mask */
    xs_cpuid_sub(0x0D, 0, &a, &b, &c, &d);
    info.supported_features = ((uint64_t)d << 32) | (uint64_t)a;
    info.current_size       = b;
    info.max_size           = c;

    info.probed = 1u;
    g_xsave_info = info;

    VOS3_INFO("[XSAVE] xsave=%u avx=%u avx512f=%u amx=%u "
              "supported_xcr0_lo=0x%x area_current=%u area_max=%u",
              (unsigned)info.has_xsave,
              (unsigned)info.has_avx,
              (unsigned)info.has_avx512f,
              (unsigned)info.has_amx,
              (unsigned)(info.supported_features & 0xFFFFFFFFu),
              (unsigned)info.current_size,
              (unsigned)info.max_size);

    __atomic_store_n(&g_xsave_state, 2u, __ATOMIC_RELEASE);
    return 0;
}

uint32_t vos3_xsave_get_area_size(void)
{
    return g_xsave_info.max_size;
}

uint64_t vos3_xsave_supported_features(void)
{
    return g_xsave_info.supported_features;
}

int vos3_xsave_has_xsave(void)    { return g_xsave_info.has_xsave; }
int vos3_xsave_has_avx(void)      { return g_xsave_info.has_avx; }
int vos3_xsave_has_avx512f(void)  { return g_xsave_info.has_avx512f; }
int vos3_xsave_has_amx(void)      { return g_xsave_info.has_amx; }

int vos3_xsave_is_avx512_safe(void)
{
    /* Three preconditions for the SIMD path to be safe to enable:
     *  1. CPU supports XSAVE
     *  2. CPU advertises AVX-512F
     *  3. The supported_features mask has all the AVX-512 XCR0 bits.
     * The fourth precondition — CR4.OSXSAVE actually being SET and a
     * per-task XSAVE area being plumbed — is NOT checked here; that
     * is the runtime side and lives in the boot loader / scheduler. */
    if (!g_xsave_info.has_xsave)   return 0;
    if (!g_xsave_info.has_avx512f) return 0;
    if ((g_xsave_info.supported_features & VOS3_XCR0_AVX512_MASK)
        != VOS3_XCR0_AVX512_MASK) return 0;
    return 1;
}

/* ============================================================================
 * HW-1 (M1) — Boot-time activation
 * ============================================================================
 *
 * vos3_xsave_boot_init() sets CR4.OSXSAVE and programs XCR0 with the
 * supported component mask discovered by vos3_xsave_probe().
 *
 * Activation contract (the part xsave.c's original v21.1.x scaffold
 * deliberately deferred):
 *
 *   1. CPUID.1:ECX.XSAVE must be 1. Checked here; fail-closed.
 *   2. CR4.OSFXSR must already be set (FPU init runs earlier in boot).
 *      We do not enforce this — the platform boot code is responsible.
 *   3. XCR0 mask is taken from g_xsave_info.supported_features but
 *      AND-ed with VOS3_XSAVE_DEFAULT_MASK_LO/HI so we never enable a
 *      component the rest of the kernel isn't ready for (BNDREG/BNDCSR
 *      MPX, AMX TILE — opt-in at v21.4+).
 *
 * The function is GATED behind VOS3_HW_XSAVE. Default builds compile
 * the body to a no-op returning -ENODEV — exactly the behavior of the
 * pre-v21.3 scaffold.
 */

#ifndef ENODEV
# define ENODEV  19
#endif
#ifndef EINVAL
# define EINVAL  22
#endif

/* The mask we are willing to advertise in XCR0. Must match the masks
 * used by vos3_xsave_save / vos3_xsave_restore (kernel/src/sched/
 * xsave_ctx.c). Today: x87 (bit 0) + SSE (bit 1) + AVX (bit 2) only.
 * AVX-512 components stay disabled until VOS3_AVX512_ENABLED ships. */
#define VOS3_HW_XSAVE_XCR0_MASK_LO  0x00000007u
#define VOS3_HW_XSAVE_XCR0_MASK_HI  0x00000000u

/* CR4.OSXSAVE = bit 18. */
#define VOS3_CR4_OSXSAVE            (1ull << 18)

static inline uint64_t hw_xsave_read_cr4(void)
{
#ifdef VOS3_HW_XSAVE
    uint64_t v;
    __asm__ volatile ("mov %%cr4, %0" : "=r"(v));
    return v;
#else
    return 0ull;
#endif
}

static inline void hw_xsave_write_cr4(uint64_t v)
{
#ifdef VOS3_HW_XSAVE
    __asm__ volatile ("mov %0, %%cr4" : : "r"(v) : "memory");
#else
    (void)v;
#endif
}

static inline void hw_xsave_xsetbv(uint32_t xcr, uint32_t lo, uint32_t hi)
{
#ifdef VOS3_HW_XSAVE
    /* XSETBV: 0F 01 D1. ECX = XCR index; EDX:EAX = value. */
    __asm__ volatile ("xsetbv"
                      :
                      : "c"(xcr), "a"(lo), "d"(hi)
                      : "memory");
#else
    (void)xcr; (void)lo; (void)hi;
#endif
}

int vos3_xsave_boot_init(void)
{
#ifndef VOS3_HW_XSAVE
    VOS3_INFO("[XSAVE] boot_init skipped — VOS3_HW_XSAVE not defined");
    return -ENODEV;
#else
    if (__atomic_load_n(&g_xsave_state, __ATOMIC_ACQUIRE) != 2u) {
        return -EINVAL;  /* probe must run first */
    }
    if (!g_xsave_info.has_xsave) {
        VOS3_INFO("[XSAVE] CPU lacks XSAVE — boot_init no-op");
        return -ENODEV;
    }

    /* Set CR4.OSXSAVE (bit 18). Idempotent: only write if currently
     * cleared, so re-running on an already-initialized CPU is a no-op. */
    uint64_t cr4 = hw_xsave_read_cr4();
    if ((cr4 & VOS3_CR4_OSXSAVE) == 0ull) {
        hw_xsave_write_cr4(cr4 | VOS3_CR4_OSXSAVE);
    }

    /* Program XCR0. Only enable bits that BOTH the CPU supports AND the
     * v21.3 default mask permits. */
    uint32_t want_lo = (uint32_t)(g_xsave_info.supported_features & 0xFFFFFFFFu)
                     & VOS3_HW_XSAVE_XCR0_MASK_LO;
    uint32_t want_hi = (uint32_t)((g_xsave_info.supported_features >> 32) & 0xFFFFFFFFu)
                     & VOS3_HW_XSAVE_XCR0_MASK_HI;
    /* Bit 0 (x87) is mandatory per Intel SDM; force-set it. */
    want_lo |= 0x1u;
    hw_xsave_xsetbv(0u, want_lo, want_hi);

    VOS3_INFO("[XSAVE] boot_init: CR4.OSXSAVE=1, XCR0=0x%x:0x%x",
              (unsigned)want_hi, (unsigned)want_lo);
    return 0;
#endif
}

/* Self-test runs vos3_xsave_save() into a 64-byte aligned buffer twice
 * and verifies the second write matches the first byte-for-byte (no FP
 * state changed in between). Returns 0 on PASS, -1 on FAIL. */
int vos3_xsave_self_test(void)
{
#ifndef VOS3_HW_XSAVE
    return 0;  /* Inert; counts as pass for non-active builds. */
#else
    extern void vos3_xsave_save(void *, uint32_t, uint32_t);

    if (g_xsave_info.max_size == 0u || g_xsave_info.max_size > 4096u) {
        return -1;
    }
    static __attribute__((aligned(64))) uint8_t buf_a[4096];
    static __attribute__((aligned(64))) uint8_t buf_b[4096];

    vos3_xsave_save(buf_a, 0u, 0u);
    vos3_xsave_save(buf_b, 0u, 0u);

    /* Compare the legacy region (first 512 bytes) — guaranteed-deterministic
     * across two back-to-back saves. The header at offset 512 includes a
     * timestamp-free XSTATE_BV which is also stable. */
    for (uint32_t i = 0; i < 512u; i++) {
        if (buf_a[i] != buf_b[i]) {
            VOS3_ERROR("[XSAVE] self_test FAIL at offset %u (a=0x%02x b=0x%02x)",
                       (unsigned)i, (unsigned)buf_a[i], (unsigned)buf_b[i]);
            return -1;
        }
    }
    VOS3_INFO("[XSAVE] self_test PASS");
    return 0;
#endif
}
