/**
 * @file baremetal_preflight.h
 * @brief Mandatory CPU feature gate before the kernel initializes hardware.
 *
 * The evaluator consumes the canonical vos3_cpu_info_t snapshot.  Keeping
 * CPUID collection in arch/x86_64/cpu.c gives every boot path the same leaf
 * bounds and prevents this gate from growing a second feature detector.
 */

#ifndef VOS3_BAREMETAL_PREFLIGHT_H
#define VOS3_BAREMETAL_PREFLIGHT_H

#include <stdint.h>
#include <arch/x86_64/cpu.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum vos3_preflight_status {
    VOS3_PREFLIGHT_OK = 0,
    VOS3_PREFLIGHT_NO_CPUID = -1,
    VOS3_PREFLIGHT_NO_FPU = -2,
    VOS3_PREFLIGHT_NO_SSE = -3,
    VOS3_PREFLIGHT_NO_SSE2 = -4,
    VOS3_PREFLIGHT_NO_NX = -5,
    VOS3_PREFLIGHT_NO_TSC = -6,
    VOS3_PREFLIGHT_INVALID = -7,
    VOS3_PREFLIGHT_NO_MSR = -8,
    VOS3_PREFLIGHT_NO_PAE = -9,
    VOS3_PREFLIGHT_NO_PGE = -10,
    VOS3_PREFLIGHT_NO_PAT = -11,
    VOS3_PREFLIGHT_NO_FXSR = -12,
    VOS3_PREFLIGHT_NO_SYSCALL = -13,
    VOS3_PREFLIGHT_NO_LONG_MODE = -14,
} vos3_preflight_status_t;

typedef struct vos3_preflight_features {
    uint8_t pcid_supported;
    uint8_t invpcid_supported;
    uint8_t sha_ni_supported;
    uint8_t x2apic_supported;
    uint8_t reserved[4];
} vos3_preflight_features_t;

/** Evaluate a canonical CPU snapshot without executing CPUID. */
int vos3_baremetal_preflight_evaluate(
    const vos3_cpu_info_t *cpu,
    vos3_preflight_features_t *out_features);

/** Collect a bounded canonical CPU snapshot, then evaluate it. */
int vos3_baremetal_preflight(vos3_preflight_features_t *out_features);

/** Stable text for boot diagnostics. */
const char *vos3_preflight_status_str(int status);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_BAREMETAL_PREFLIGHT_H */
