/**
 * @file microcode_check.c
 * @brief Boot-time microcode revision check
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P4.2
 *
 * See microcode_check.h for the public contract. See
 * docs/MICROCODE_BASELINE.md for the source of the per-family
 * baseline numbers (Intel/AMD 2024–2025 security advisories).
 *
 * Implementation notes
 * --------------------
 * * Intel SDM Vol 3A §9.11.7.1 spells out the wrmsr(0x8B, 0) →
 *   cpuid(1) → rdmsr(0x8B) dance. The cpuid serves to commit the
 *   microcode read; without it the rdmsr returns stale data.
 * * AMD KM Vol 2 §15.20: read MSR 0x8B directly; low 32 bits are
 *   the patch ID. We treat the low 32 bits identically for the
 *   "below baseline" comparison even though the AMD register layout
 *   differs slightly.
 * * The baseline table is a sampled set, pinned to the silicon this
 *   engagement actually targets. Adding a new model = one line.
 *
 * Defense rationale
 * -----------------
 * Below-baseline microcode does NOT panic. The operator may not have
 * a newer microcode available (vendor support ended, vendor-released
 * but not yet flashed, OEM-locked BIOS). Refusing to boot would deny
 * the operator any further visibility. Instead we:
 *   1. Boot in RESTRICTED_LEGACY (regardless of CPU class).
 *   2. Emit `[MICROCODE]` + `[SECURITY]` audit lines (loud).
 *   3. Let the manifest's risk_score reflect the exposure (−15).
 *   4. Let the sandbox rlimits auto-tighten (P4.3 wiring).
 */

#include "../../../include/arch/x86_64/microcode_check.h"
#include "../../../include/vos/console.h"

/* ============================================================================
 * Per-family baseline table — vOS·Adaptive·SHA=aeb3736·Phase=P4.2
 * Source: docs/MICROCODE_BASELINE.md
 * ============================================================================ */

typedef struct ucode_baseline_entry {
    vos3_cpu_vendor_t vendor;
    uint8_t  family;       /* Effective family (after ext_family fold). */
    uint8_t  model;        /* Effective model  (after ext_model  fold). */
    uint32_t min_revision; /* Minimum-secure microcode revision. */
} ucode_baseline_entry_t;

/* Conservative, sampled baseline. Each entry is the lowest microcode
 * revision known to ship fixes for the CVEs catalogued in
 * docs/HARDWARE_CVE_INVENTORY_2010_2026.md as of 2026-05-17. */
static const ucode_baseline_entry_t g_baselines[] = {
    /* Intel — Westmere (06_2CH/06_25H) — KAISER + retpoline class only. */
    { VOS3_CPU_VENDOR_INTEL, 0x06, 0x25, 0x00000013U },
    { VOS3_CPU_VENDOR_INTEL, 0x06, 0x2C, 0x0000001FU },
    /* Intel — Ivy Bridge / Haswell / Skylake / Cascade Lake (sampled). */
    { VOS3_CPU_VENDOR_INTEL, 0x06, 0x3A, 0x00000021U },  /* Ivy Bridge */
    { VOS3_CPU_VENDOR_INTEL, 0x06, 0x3C, 0x00000028U },  /* Haswell */
    { VOS3_CPU_VENDOR_INTEL, 0x06, 0x4E, 0x000000F0U },  /* Skylake (mobile) */
    { VOS3_CPU_VENDOR_INTEL, 0x06, 0x55, 0x05003604U },  /* Cascade Lake / SKX */
    /* AMD — Zen / Zen 2 / Zen 3 / Zen 4 (sampled). */
    { VOS3_CPU_VENDOR_AMD,   0x17, 0x01, 0x08001138U },  /* Zen Naples */
    { VOS3_CPU_VENDOR_AMD,   0x17, 0x31, 0x0830107CU },  /* Zen 2 Rome */
    { VOS3_CPU_VENDOR_AMD,   0x19, 0x21, 0x0A201025U },  /* Zen 3 Milan */
    { VOS3_CPU_VENDOR_AMD,   0x19, 0x11, 0x0A101148U },  /* Zen 4 Genoa */
};

static const uint32_t g_baseline_count =
    (uint32_t)(sizeof(g_baselines) / sizeof(g_baselines[0]));

/* ============================================================================
 * Effective family/model helpers (CPUID 1 fold rules — Intel SDM §3.2.2)
 * ============================================================================ */

static uint8_t effective_family(const vos3_cpu_info_t* info)
{
    uint8_t f = info->family;
    if (f == 0x0FU) {
        f = (uint8_t)(f + info->ext_family);
    }
    return f;
}

static uint8_t effective_model(const vos3_cpu_info_t* info)
{
    uint8_t m = info->model;
    if (info->family == 0x06U || info->family == 0x0FU) {
        m = (uint8_t)(m | (info->ext_model << 4));
    }
    return m;
}

/* ============================================================================
 * Public API
 * ============================================================================ */

uint32_t vos3_microcode_read_revision(const vos3_cpu_info_t* info)
{
    if (info == NULL) {
        return 0U;
    }
    if (info->vendor == VOS3_CPU_VENDOR_INTEL) {
        /* Intel: kick the MSR with a zero, run CPUID 1, read back. */
        vos3_write_msr(VOS3_MSR_IA32_UCODE_REV, 0U);
        uint32_t a, b, c, d;
        vos3_cpuid(VOS3_CPUID_FEATURES, 0U, &a, &b, &c, &d);
        uint64_t v = vos3_read_msr(VOS3_MSR_IA32_UCODE_REV);
        return (uint32_t)(v >> 32);
    }
    if (info->vendor == VOS3_CPU_VENDOR_AMD) {
        /* AMD: read directly, low 32 bits are the patch ID. */
        uint64_t v = vos3_read_msr(VOS3_MSR_IA32_UCODE_REV);
        return (uint32_t)(v & 0xFFFFFFFFU);
    }
    return 0U;
}

uint32_t vos3_microcode_baseline_for(const vos3_cpu_info_t* info)
{
    if (info == NULL) {
        return 0U;
    }
    const uint8_t f = effective_family(info);
    const uint8_t m = effective_model(info);
    for (uint32_t i = 0U; i < g_baseline_count; i++) {
        if (g_baselines[i].vendor == info->vendor &&
            g_baselines[i].family == f &&
            g_baselines[i].model  == m) {
            return g_baselines[i].min_revision;
        }
    }
    /* Unknown CPU — no baseline → 0 → never flagged as below-baseline. */
    return 0U;
}

/* Print "0x" + 8 hex digits without depending on printf %x. */
static void print_hex32(uint32_t v)
{
    static const char digits[] = "0123456789abcdef";
    char buf[11];
    buf[0]  = '0';
    buf[1]  = 'x';
    buf[2]  = digits[(v >> 28) & 0xFU];
    buf[3]  = digits[(v >> 24) & 0xFU];
    buf[4]  = digits[(v >> 20) & 0xFU];
    buf[5]  = digits[(v >> 16) & 0xFU];
    buf[6]  = digits[(v >> 12) & 0xFU];
    buf[7]  = digits[(v >>  8) & 0xFU];
    buf[8]  = digits[(v >>  4) & 0xFU];
    buf[9]  = digits[(v >>  0) & 0xFU];
    buf[10] = '\0';
    vos3_console_puts(buf);
}

void vos3_microcode_check_boot(const vos3_cpu_info_t* info,
                               vos3_microcode_status_t* out)
{
    if (out == NULL) {
        return;
    }
    out->revision = 0U;
    out->baseline = 0U;
    out->below_baseline = 0;
    out->checked = 0;
    if (info == NULL) {
        return;
    }

    out->revision = vos3_microcode_read_revision(info);
    out->baseline = vos3_microcode_baseline_for(info);

    if (out->baseline == 0U) {
        /* Unknown CPU model — cannot judge. Emit informational line. */
        vos3_console_puts("[MICROCODE] revision=");
        print_hex32(out->revision);
        vos3_console_puts(" baseline=unknown below_baseline=unchecked\n");
        return;
    }

    out->checked = 1;
    out->below_baseline = (out->revision < out->baseline) ? 1 : 0;

    vos3_console_puts("[MICROCODE] revision=");
    print_hex32(out->revision);
    vos3_console_puts(" baseline=");
    print_hex32(out->baseline);
    vos3_console_puts(out->below_baseline
        ? " below_baseline=yes\n"
        : " below_baseline=no\n");

    if (out->below_baseline) {
        vos3_console_puts(
            "[SECURITY] Microcode outdated. System logic vulnerable to "
            "speculative execution side-channels.\n"
        );
    }
}
