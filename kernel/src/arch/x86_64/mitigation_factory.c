/**
 * @file mitigation_factory.c
 * @brief Adaptive mitigation tier selection (Meltdown / KPTI / KAISER)
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P1
 *
 * See mitigation_mode.h for the full design contract.
 */

#include "arch/x86_64/mitigation_mode.h"
#include "arch/x86_64/cpu.h"
#include "arch/x86_64/microcode_check.h"
#include "vos/console.h"

/* ============================================================================
 * State (read-only after init)
 * ============================================================================ */

static vos3_mitigation_mode_t g_mode = VOS3_MIT_UNINITIALIZED;
static int g_has_pcid;
static int g_has_invpcid;
static int g_has_smep;
static int g_has_smap;
static int g_has_sha_ni;

/* P4.2 — microcode status captured during init; surfaced by
 * vos3_mitigation_print_summary() so the manifest parser sees the
 * [MICROCODE] line in the same boot block as [KPTI]. */
static vos3_microcode_status_t g_ucode_status;

/* ============================================================================
 * Pure helpers (testable as source-level invariants)
 * ============================================================================ */

static int caps_have_lm(const vos3_cpu_info_t* info)
{
    return (info->ext_features_edx & VOS3_CPU_FEAT_LM) != 0U;
}

static int caps_have_pcid(const vos3_cpu_info_t* info)
{
    return (info->features_ecx & VOS3_CPU_FEAT_PCID) != 0U;
}

static int caps_have_invpcid(const vos3_cpu_info_t* info)
{
    return (info->ext7_ebx & VOS3_CPU_EXT7_INVPCID) != 0U;
}

static int caps_have_smep(const vos3_cpu_info_t* info)
{
    return (info->ext7_ebx & VOS3_CPU_EXT7_SMEP) != 0U;
}

static int caps_have_smap(const vos3_cpu_info_t* info)
{
    return (info->ext7_ebx & VOS3_CPU_EXT7_SMAP) != 0U;
}

/* SHA-NI is CPUID.07H.EBX bit 29 — not in cpu.h yet, define locally
 * so we don't drag the public header for a single bit. */
#define VOS3_CPU_EXT7_SHA_NI    (1U << 29)
static int caps_have_sha_ni(const vos3_cpu_info_t* info)
{
    return (info->ext7_ebx & VOS3_CPU_EXT7_SHA_NI) != 0U;
}

/* ============================================================================
 * REFUSE_32BIT panic path — early boot, before full console drivers
 * ============================================================================ */

__attribute__((noreturn))
static void vos3_refuse_32bit_and_halt(void)
{
    /* Best-effort console output — kmsg/serial setup happens before
     * mitigation_factory_init() in the boot sequence so this should land. */
    vos3_console_puts(
        "[VOS3_BOOT_REFUSE] 32-bit-only CPU detected (no x86_64 long-mode).\n"
        "Sovereign support window starts at x86_64 (2003+). Halting.\n"
    );
    for (;;) {
        __asm__ volatile ("cli; hlt");
    }
}

/* ============================================================================
 * Public API
 * ============================================================================ */

vos3_mitigation_mode_t vos3_mitigation_factory_init(const vos3_cpu_info_t* info)
{
    if (info == NULL) {
        /* USER-APPROVED POLICY (R2 review): degrade to LEGACY_KAISER on a
         * NULL info pointer rather than panic. Rationale: a NULL here is a
         * kernel-internal contract violation (cpu_detect was skipped or
         * returned without populating). Halting the boot would deny the
         * operator any further visibility; LEGACY_KAISER is the worst
         * security tier we have *plus* the loudest possible warning, which
         * is what the operator needs to diagnose and recover.
         *
         * The warning below MUST be loud — anyone reading the boot log
         * needs to see this immediately, not buried in a feature-summary
         * line. Pre-banner blank lines + ASCII separator are intentional. */
        vos3_console_puts("\n\n");
        vos3_console_puts("****************************************************************\n");
        vos3_console_puts("***  [VOS3_KERNEL_BUG] mitigation_factory_init(NULL)         ***\n");
        vos3_console_puts("***                                                          ***\n");
        vos3_console_puts("***  Kernel-internal contract violation: cpu_info is NULL.   ***\n");
        vos3_console_puts("***  Defaulting to LEGACY_KAISER for boot survival.          ***\n");
        vos3_console_puts("***                                                          ***\n");
        vos3_console_puts("***  SECURITY POSTURE: REDUCED.                              ***\n");
        vos3_console_puts("***  Audit the boot trace IMMEDIATELY and file a bug.        ***\n");
        vos3_console_puts("****************************************************************\n\n");

        g_has_pcid = 0;
        g_has_invpcid = 0;
        g_has_smep = 0;
        g_has_smap = 0;
        g_has_sha_ni = 0;
        g_mode = VOS3_MIT_LEGACY_KAISER;
        return g_mode;
    }

    if (!caps_have_lm(info)) {
        g_mode = VOS3_MIT_REFUSE_32BIT;
        vos3_refuse_32bit_and_halt();
        /* unreachable */
    }

    g_has_pcid    = caps_have_pcid(info);
    g_has_invpcid = caps_have_invpcid(info);
    g_has_smep    = caps_have_smep(info);
    g_has_smap    = caps_have_smap(info);
    g_has_sha_ni  = caps_have_sha_ni(info);

    /* P4.2 — check microcode revision against the compiled-in baseline.
     * The `[MICROCODE]` + `[SECURITY]` lines are emitted by this call so
     * they appear in the same boot block the manifest parser scans. */
    vos3_microcode_check_boot(info, &g_ucode_status);

    /* Selection contract — see mitigation_mode.h for rationale.
     * Order matters: stronger tier wins; defense-in-depth derates if
     * any required sub-feature is missing.
     *
     * P4.2 amendment: below-baseline microcode forces LEGACY_KAISER
     * regardless of CPU class. The hardware is theoretically capable
     * of stronger protection, but unpatched microcode means the
     * speculative-execution side channels are not fully closed —
     * tightening sandbox rlimits is the load-bearing response.
     */
    if (g_ucode_status.below_baseline) {
        g_mode = VOS3_MIT_LEGACY_KAISER;
    } else if (g_has_pcid && g_has_invpcid && g_has_smep && g_has_smap) {
        g_mode = VOS3_MIT_PROTECTED_FULL;
    } else if (g_has_pcid) {
        /* PCID present but at least one of INVPCID/SMEP/SMAP absent.
         * This is the normal "select strongest supported tier" path:
         * INVPCID is a perf optimization (we can `mov cr3, X` instead),
         * and SMEP/SMAP are absent on Westmere/Sandy Bridge but the
         * tier still gives us full KPTI page-table separation. */
        g_mode = VOS3_MIT_PROTECTED_PCID;
    } else {
        g_mode = VOS3_MIT_LEGACY_KAISER;
    }

    return g_mode;
}

vos3_mitigation_mode_t vos3_get_mitigation_mode(void)
{
    return g_mode;
}

const char* vos3_mitigation_mode_name(vos3_mitigation_mode_t mode)
{
    switch (mode) {
        case VOS3_MIT_UNINITIALIZED:  return "UNINITIALIZED";
        case VOS3_MIT_PROTECTED_FULL: return "PROTECTED_FULL";
        case VOS3_MIT_PROTECTED_PCID: return "PROTECTED_PCID_ONLY";
        case VOS3_MIT_LEGACY_KAISER:  return "LEGACY_KAISER";
        case VOS3_MIT_REFUSE_32BIT:   return "REFUSE_32BIT";
        default:                      return "INVALID";
    }
}

static const char* yn(int v) { return v ? "yes" : "no"; }

static const char* budget_for_mode(vos3_mitigation_mode_t m)
{
    switch (m) {
        case VOS3_MIT_PROTECTED_FULL: return "<=2%";
        case VOS3_MIT_PROTECTED_PCID: return "<=3%";
        case VOS3_MIT_LEGACY_KAISER:  return "5-30%";
        default:                      return "n/a";
    }
}

void vos3_mitigation_print_summary(void)
{
    vos3_console_puts("[KPTI] mode=");
    vos3_console_puts(vos3_mitigation_mode_name(g_mode));
    vos3_console_puts(" pcid=");      vos3_console_puts(yn(g_has_pcid));
    vos3_console_puts(" invpcid=");   vos3_console_puts(yn(g_has_invpcid));
    vos3_console_puts(" smep=");      vos3_console_puts(yn(g_has_smep));
    vos3_console_puts(" smap=");      vos3_console_puts(yn(g_has_smap));
    vos3_console_puts(" sha-ni=");    vos3_console_puts(yn(g_has_sha_ni));
    vos3_console_puts(" budget=");    vos3_console_puts(budget_for_mode(g_mode));
    vos3_console_puts("\n");

    if (g_mode == VOS3_MIT_LEGACY_KAISER) {
        vos3_console_puts(
            "[KPTI] note: hardware predates 2010 PCID; "
            "syscall-heavy regression 5-30% documented; "
            "consider hardware refresh.\n"
        );
    }
}
