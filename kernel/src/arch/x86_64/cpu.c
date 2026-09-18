/**
 * @file cpu.c
 * @brief VOS3 CPU Identification Implementation
 *
 * @details CPU feature detection and identification utilities.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/arch/x86_64/cpu.h"
#include "../../../include/arch/x86_64/microcode_check.h"
#include "../../../include/vos/console.h"

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Copy 4 bytes from uint32_t to string
 */
static void copy_reg_to_str(char* dst, uint32_t reg)
{
    dst[0] = (char)(reg & 0xFFU);
    dst[1] = (char)((reg >> 8U) & 0xFFU);
    dst[2] = (char)((reg >> 16U) & 0xFFU);
    dst[3] = (char)((reg >> 24U) & 0xFFU);
}

/**
 * @brief Detect CPU vendor from vendor string
 */
static vos3_cpu_vendor_t detect_vendor(const char* vendor_string)
{
    /* Compare vendor strings */
    if (vendor_string[0] == 'G' && vendor_string[1] == 'e' &&
        vendor_string[2] == 'n' && vendor_string[3] == 'u' &&
        vendor_string[4] == 'i' && vendor_string[5] == 'n' &&
        vendor_string[6] == 'e' && vendor_string[7] == 'I' &&
        vendor_string[8] == 'n' && vendor_string[9] == 't' &&
        vendor_string[10] == 'e' && vendor_string[11] == 'l') {
        return VOS3_CPU_VENDOR_INTEL;
    }

    if (vendor_string[0] == 'A' && vendor_string[1] == 'u' &&
        vendor_string[2] == 't' && vendor_string[3] == 'h' &&
        vendor_string[4] == 'e' && vendor_string[5] == 'n' &&
        vendor_string[6] == 't' && vendor_string[7] == 'i' &&
        vendor_string[8] == 'c' && vendor_string[9] == 'A' &&
        vendor_string[10] == 'M' && vendor_string[11] == 'D') {
        return VOS3_CPU_VENDOR_AMD;
    }

    return VOS3_CPU_VENDOR_OTHER;
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

void vos3_cpu_detect_features(vos3_cpu_info_t* info)
{
    uint32_t eax, ebx, ecx, edx;

    if (info == NULL) {
        return;
    }

    /* Clear structure */
    uint8_t* ptr = (uint8_t*)info;
    for (size_t i = 0U; i < sizeof(vos3_cpu_info_t); i++) {
        ptr[i] = 0U;
    }

    /* Get vendor string and max standard leaf */
    vos3_cpuid(VOS3_CPUID_VENDOR, 0U, &eax, &ebx, &ecx, &edx);

    info->max_std_leaf = eax;

    /* Vendor string is in EBX:EDX:ECX order */
    copy_reg_to_str(&info->vendor_string[0], ebx);
    copy_reg_to_str(&info->vendor_string[4], edx);
    copy_reg_to_str(&info->vendor_string[8], ecx);
    info->vendor_string[12] = '\0';

    info->vendor = detect_vendor(info->vendor_string);

    /* Get version info and features (CPUID.01H) */
    if (info->max_std_leaf >= 1U) {
        vos3_cpuid(VOS3_CPUID_FEATURES, 0U, &eax, &ebx, &ecx, &edx);

        /* Version info */
        info->stepping = (uint8_t)(eax & 0xFU);
        info->model = (uint8_t)((eax >> 4U) & 0xFU);
        info->family = (uint8_t)((eax >> 8U) & 0xFU);
        info->type = (uint8_t)((eax >> 12U) & 0x3U);
        info->ext_model = (uint8_t)((eax >> 16U) & 0xFU);
        info->ext_family = (uint8_t)((eax >> 20U) & 0xFFU);

        /* Feature flags */
        info->features_edx = edx;
        info->features_ecx = ecx;

        /* Cache/misc info */
        info->clflush_size = (uint8_t)(((ebx >> 8U) & 0xFFU) * 8U);
        info->max_logical_cpus = (uint8_t)((ebx >> 16U) & 0xFFU);
        info->initial_apic_id = (uint8_t)((ebx >> 24U) & 0xFFU);
    }

    /* Get extended features (CPUID.07H) */
    if (info->max_std_leaf >= 7U) {
        vos3_cpuid(VOS3_CPUID_EXTENDED_FEAT, 0U, &eax, &ebx, &ecx, &edx);

        info->ext7_ebx = ebx;
        info->ext7_ecx = ecx;
        info->ext7_edx = edx;
    }

    /* Get max extended leaf */
    vos3_cpuid(VOS3_CPUID_EXT_MAX, 0U, &eax, &ebx, &ecx, &edx);
    info->max_ext_leaf = eax;

    /* Get extended features (CPUID.80000001H) */
    if (info->max_ext_leaf >= 0x80000001U) {
        vos3_cpuid(VOS3_CPUID_EXT_FEATURES, 0U, &eax, &ebx, &ecx, &edx);

        info->ext_features_edx = edx;
        info->ext_features_ecx = ecx;
    }

    /* Get brand string (CPUID.80000002H-80000004H) */
    if (info->max_ext_leaf >= 0x80000004U) {
        vos3_cpuid(VOS3_CPUID_BRAND1, 0U, &eax, &ebx, &ecx, &edx);
        copy_reg_to_str(&info->brand_string[0], eax);
        copy_reg_to_str(&info->brand_string[4], ebx);
        copy_reg_to_str(&info->brand_string[8], ecx);
        copy_reg_to_str(&info->brand_string[12], edx);

        vos3_cpuid(VOS3_CPUID_BRAND2, 0U, &eax, &ebx, &ecx, &edx);
        copy_reg_to_str(&info->brand_string[16], eax);
        copy_reg_to_str(&info->brand_string[20], ebx);
        copy_reg_to_str(&info->brand_string[24], ecx);
        copy_reg_to_str(&info->brand_string[28], edx);

        vos3_cpuid(VOS3_CPUID_BRAND3, 0U, &eax, &ebx, &ecx, &edx);
        copy_reg_to_str(&info->brand_string[32], eax);
        copy_reg_to_str(&info->brand_string[36], ebx);
        copy_reg_to_str(&info->brand_string[40], ecx);
        copy_reg_to_str(&info->brand_string[44], edx);

        info->brand_string[48] = '\0';
    }

    /* Get address sizes (CPUID.80000008H) */
    if (info->max_ext_leaf >= 0x80000008U) {
        vos3_cpuid(VOS3_CPUID_ADDR_SIZE, 0U, &eax, &ebx, &ecx, &edx);

        info->phys_addr_bits = (uint8_t)(eax & 0xFFU);
        info->virt_addr_bits = (uint8_t)((eax >> 8U) & 0xFFU);
    }

}

void vos3_cpu_detect_microcode(vos3_cpu_info_t* info)
{
    if (info == NULL) {
        return;
    }

    /* P4.2 — access the microcode MSR only in the full detector.  The early
     * preflight path deliberately uses vos3_cpu_detect_features() before an
     * IDT exists, so a hypervisor MSR policy cannot turn a feature check into
     * a triple fault. */
    info->microcode_revision = vos3_microcode_read_revision(info);
}

void vos3_cpu_detect(vos3_cpu_info_t* info)
{
    vos3_cpu_detect_features(info);
    vos3_cpu_detect_microcode(info);
}

int vos3_cpu_has_feature(const vos3_cpu_info_t* info, uint32_t feature)
{
    if (info == NULL) {
        return 0;
    }

    /* Check which feature register to use based on feature value */
    /* This is a simplified check - real implementation would need to know which register */
    return (info->features_edx & feature) != 0U;
}

void vos3_cpu_print_info(const vos3_cpu_info_t* info)
{
    if (info == NULL) {
        return;
    }

    VOS3_INFO("CPU Information:");

    /* Vendor */
    const char* vendor_name;
    switch (info->vendor) {
        case VOS3_CPU_VENDOR_INTEL:
            vendor_name = "Intel";
            break;
        case VOS3_CPU_VENDOR_AMD:
            vendor_name = "AMD";
            break;
        default:
            vendor_name = "Unknown";
            break;
    }
    vos3_console_printf("  Vendor: %s (%s)\n", vendor_name, info->vendor_string);

    /* Brand string */
    if (info->brand_string[0] != '\0') {
        /* Skip leading spaces */
        const char* brand = info->brand_string;
        while (*brand == ' ') {
            brand++;
        }
        vos3_console_printf("  Model:  %s\n", brand);
    }

    /* Version */
    uint8_t family = info->family;
    uint8_t model = info->model;

    if (family == 0x0FU) {
        family += info->ext_family;
    }
    if (family == 0x06U || family == 0x0FU) {
        model += (uint8_t)(info->ext_model << 4U);
    }

    vos3_console_printf("  Family: 0x%02x, Model: 0x%02x, Stepping: %u\n",
                        family, model, info->stepping);

    /* Address sizes */
    vos3_console_printf("  Address: %u-bit physical, %u-bit virtual\n",
                        info->phys_addr_bits, info->virt_addr_bits);

    /* Key features */
    vos3_console_printf("  Features: ");

    if (info->features_edx & VOS3_CPU_FEAT_FPU) vos3_console_puts("FPU ");
    if (info->features_edx & VOS3_CPU_FEAT_TSC) vos3_console_puts("TSC ");
    if (info->features_edx & VOS3_CPU_FEAT_MSR) vos3_console_puts("MSR ");
    if (info->features_edx & VOS3_CPU_FEAT_PAE) vos3_console_puts("PAE ");
    if (info->features_edx & VOS3_CPU_FEAT_APIC) vos3_console_puts("APIC ");
    if (info->features_edx & VOS3_CPU_FEAT_MTRR) vos3_console_puts("MTRR ");
    if (info->features_edx & VOS3_CPU_FEAT_PGE) vos3_console_puts("PGE ");
    if (info->features_edx & VOS3_CPU_FEAT_PAT) vos3_console_puts("PAT ");
    if (info->features_edx & VOS3_CPU_FEAT_SSE) vos3_console_puts("SSE ");
    if (info->features_edx & VOS3_CPU_FEAT_SSE2) vos3_console_puts("SSE2 ");

    if (info->features_ecx & VOS3_CPU_FEAT_SSE3) vos3_console_puts("SSE3 ");
    if (info->features_ecx & VOS3_CPU_FEAT_SSSE3) vos3_console_puts("SSSE3 ");
    if (info->features_ecx & VOS3_CPU_FEAT_SSE41) vos3_console_puts("SSE4.1 ");
    if (info->features_ecx & VOS3_CPU_FEAT_SSE42) vos3_console_puts("SSE4.2 ");
    if (info->features_ecx & VOS3_CPU_FEAT_AVX) vos3_console_puts("AVX ");
    if (info->features_ecx & VOS3_CPU_FEAT_AES) vos3_console_puts("AES ");
    if (info->features_ecx & VOS3_CPU_FEAT_XSAVE) vos3_console_puts("XSAVE ");
    if (info->features_ecx & VOS3_CPU_FEAT_VMX) vos3_console_puts("VMX ");
    if (info->features_ecx & VOS3_CPU_FEAT_X2APIC) vos3_console_puts("x2APIC ");
    if (info->features_ecx & VOS3_CPU_FEAT_HYPERVISOR) vos3_console_puts("[VM] ");

    if (info->ext_features_edx & VOS3_CPU_FEAT_SYSCALL) vos3_console_puts("SYSCALL ");
    if (info->ext_features_edx & VOS3_CPU_FEAT_NX) vos3_console_puts("NX ");
    if (info->ext_features_edx & VOS3_CPU_FEAT_PDPE1GB) vos3_console_puts("1GB ");
    if (info->ext_features_edx & VOS3_CPU_FEAT_LM) vos3_console_puts("LM ");

    vos3_console_puts("\n");
}
