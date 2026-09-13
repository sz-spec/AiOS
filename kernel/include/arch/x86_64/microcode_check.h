/**
 * @file microcode_check.h
 * @brief Boot-time microcode revision check
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P4.2
 *
 * Reads the CPU's microcode revision (Intel: MSR 0x8B after a CPUID
 * "kick"; AMD: MSR 0x8B directly) and compares it to the compiled-in
 * Minimum Secure Baseline derived from Intel/AMD 2024–2025 security
 * advisories. See docs/MICROCODE_BASELINE.md for the per-family table.
 *
 * Below-baseline microcode is NOT a refuse-to-boot condition — the
 * operator may not have a newer microcode available. We boot
 * Restricted-Legacy regardless of CPU class and emit a loud audit
 * line:
 *
 *   [MICROCODE] revision=0x000000ce baseline=0x00000100 below_baseline=yes
 *   [SECURITY] Microcode outdated. System logic vulnerable to
 *              speculative execution side-channels.
 *
 * The line is parsed by services/hardware_manifest.py and produces a
 * -15 risk-score penalty + forced RESTRICTED_LEGACY tier (so the
 * sandbox rlimits halve, AAA plan §4.3).
 *
 * Honest scope
 * ------------
 * The baseline table is sampled — not exhaustive. We pin the worst
 * widely-deployed pre-CVE-fix revisions for the families this engagement
 * actually targets. A live microcode-update path (loading a new blob
 * at boot) is OUT OF SCOPE; that's a kernel feature, not a security
 * audit, and the operator runs the update offline.
 */

#ifndef VOS3_ARCH_X86_64_MICROCODE_CHECK_H
#define VOS3_ARCH_X86_64_MICROCODE_CHECK_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "arch/x86_64/cpu.h"

/** @brief MSR holding the BIOS microcode signature ID. After a
 *  CPUID-kick the high 32 bits contain the revision. */
#define VOS3_MSR_IA32_UCODE_REV     ((uint32_t)0x0000008BU)

/** @brief Result of the boot-time microcode check. */
typedef struct vos3_microcode_status {
    uint32_t revision;          /**< Hardware-reported microcode rev. */
    uint32_t baseline;          /**< Minimum-secure baseline for this CPU. */
    int      below_baseline;    /**< 1 if revision < baseline, else 0. */
    int      checked;           /**< 0 if no baseline available (unknown CPU). */
} vos3_microcode_status_t;

/**
 * @brief Read the microcode revision from the running CPU.
 *
 * Algorithm (Intel SDM Vol 3A §9.11.7.1):
 *   1. wrmsr(0x8B, 0)
 *   2. cpuid(eax=1)
 *   3. rdmsr(0x8B) → revision = (msr >> 32)
 *
 * AMD families simply rdmsr(0x8B); the high 32 bits are the patch ID.
 * The function picks the right path based on info->vendor.
 *
 * @param info populated CPU info (must have vendor + family + model)
 * @return microcode revision (0 if rdmsr/wrmsr fail or MSR unsupported)
 */
uint32_t vos3_microcode_read_revision(const vos3_cpu_info_t* info);

/**
 * @brief Look up the minimum-secure baseline for this CPU.
 * @return baseline revision, or 0 if unknown CPU (no baseline → assume safe).
 */
uint32_t vos3_microcode_baseline_for(const vos3_cpu_info_t* info);

/**
 * @brief Run the full boot-time check and populate the status struct.
 * Emits the `[MICROCODE]` + `[SECURITY]` audit lines to the console.
 */
void vos3_microcode_check_boot(const vos3_cpu_info_t* info,
                               vos3_microcode_status_t* out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_MICROCODE_CHECK_H */
