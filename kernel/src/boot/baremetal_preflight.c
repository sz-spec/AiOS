/**
 * @file baremetal_preflight.c
 * @brief Fail-closed CPU prerequisites for every native boot path.
 */

#include <vos/baremetal_preflight.h>

static void clear_optional_features(vos3_preflight_features_t *features)
{
    if (features == (void *)0) {
        return;
    }
    features->pcid_supported = 0U;
    features->invpcid_supported = 0U;
    features->sha_ni_supported = 0U;
    features->x2apic_supported = 0U;
    for (uint32_t i = 0U; i < 4U; i++) {
        features->reserved[i] = 0U;
    }
}

int vos3_baremetal_preflight_evaluate(
    const vos3_cpu_info_t *cpu,
    vos3_preflight_features_t *out_features)
{
    clear_optional_features(out_features);
    if (cpu == (void *)0) {
        return VOS3_PREFLIGHT_INVALID;
    }

    /* cpu.c only populates the basic feature registers after leaf 1 was
     * proven available.  Checking the recorded bound avoids interpreting a
     * zero-filled snapshot as an ordinary feature failure. */
    if (cpu->max_std_leaf < VOS3_CPUID_FEATURES) {
        return VOS3_PREFLIGHT_NO_CPUID;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_FPU) == 0U) {
        return VOS3_PREFLIGHT_NO_FPU;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_TSC) == 0U) {
        return VOS3_PREFLIGHT_NO_TSC;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_MSR) == 0U) {
        return VOS3_PREFLIGHT_NO_MSR;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_PAE) == 0U) {
        return VOS3_PREFLIGHT_NO_PAE;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_PGE) == 0U) {
        return VOS3_PREFLIGHT_NO_PGE;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_PAT) == 0U) {
        return VOS3_PREFLIGHT_NO_PAT;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_FXSR) == 0U) {
        return VOS3_PREFLIGHT_NO_FXSR;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_SSE) == 0U) {
        return VOS3_PREFLIGHT_NO_SSE;
    }
    if ((cpu->features_edx & VOS3_CPU_FEAT_SSE2) == 0U) {
        return VOS3_PREFLIGHT_NO_SSE2;
    }

    /* NX is mandatory for the kernel's W^X contract.  A processor which
     * does not expose the extended feature leaf is equivalent to !NX. */
    if (cpu->max_ext_leaf < VOS3_CPUID_EXT_FEATURES) {
        return VOS3_PREFLIGHT_NO_NX;
    }
    if ((cpu->ext_features_edx & VOS3_CPU_FEAT_SYSCALL) == 0U) {
        return VOS3_PREFLIGHT_NO_SYSCALL;
    }
    if ((cpu->ext_features_edx & VOS3_CPU_FEAT_NX) == 0U) {
        return VOS3_PREFLIGHT_NO_NX;
    }
    if ((cpu->ext_features_edx & VOS3_CPU_FEAT_LM) == 0U) {
        return VOS3_PREFLIGHT_NO_LONG_MODE;
    }

    if (out_features != (void *)0) {
        out_features->pcid_supported =
            (cpu->features_ecx & VOS3_CPU_FEAT_PCID) != 0U;
        out_features->x2apic_supported =
            (cpu->features_ecx & VOS3_CPU_FEAT_X2APIC) != 0U;
        if (cpu->max_std_leaf >= VOS3_CPUID_EXTENDED_FEAT) {
            out_features->invpcid_supported =
                (cpu->ext7_ebx & VOS3_CPU_EXT7_INVPCID) != 0U;
            out_features->sha_ni_supported =
                (cpu->ext7_ebx & VOS3_CPU_EXT7_SHA) != 0U;
        }
    }
    return VOS3_PREFLIGHT_OK;
}

int vos3_baremetal_preflight(vos3_preflight_features_t *out_features)
{
    vos3_cpu_info_t cpu;
    vos3_cpu_detect_features(&cpu);
    return vos3_baremetal_preflight_evaluate(&cpu, out_features);
}

const char *vos3_preflight_status_str(int status)
{
    switch (status) {
    case VOS3_PREFLIGHT_OK: return "OK";
    case VOS3_PREFLIGHT_NO_CPUID: return "CPUID feature leaf unavailable";
    case VOS3_PREFLIGHT_NO_FPU: return "x87 FPU unavailable";
    case VOS3_PREFLIGHT_NO_SSE: return "SSE unavailable";
    case VOS3_PREFLIGHT_NO_SSE2: return "SSE2 unavailable";
    case VOS3_PREFLIGHT_NO_NX: return "NX/XD unavailable";
    case VOS3_PREFLIGHT_NO_TSC: return "TSC unavailable";
    case VOS3_PREFLIGHT_NO_MSR: return "model-specific registers unavailable";
    case VOS3_PREFLIGHT_NO_PAE: return "physical-address extension unavailable";
    case VOS3_PREFLIGHT_NO_PGE: return "global pages unavailable";
    case VOS3_PREFLIGHT_NO_PAT: return "page-attribute table unavailable";
    case VOS3_PREFLIGHT_NO_FXSR: return "FXSAVE/FXRSTOR unavailable";
    case VOS3_PREFLIGHT_NO_SYSCALL: return "SYSCALL/SYSRET unavailable";
    case VOS3_PREFLIGHT_NO_LONG_MODE: return "long mode unavailable";
    case VOS3_PREFLIGHT_INVALID: return "invalid CPU feature snapshot";
    default: return "unknown CPU preflight failure";
    }
}
