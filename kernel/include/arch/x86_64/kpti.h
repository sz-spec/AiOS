/**
 * @file kpti.h
 * @brief Process-root transitions with CPU-local entry/exit trampolines.
 * Each process owns full/restricted roots. Every return prepares the current
 * process's user mappings; entry uses its full root for kernel uaccess.
 * Isolation is PARTIAL: PML4[256] (direct physical map) and PML4[511]
 * (kernel image) remain mapped. Full Meltdown isolation is not yet achieved.
 * CR3 loads use PCID 0 and flush; distinct-PCID optimization is pending.
 */

#ifndef VOS3_ARCH_X86_64_KPTI_H
#define VOS3_ARCH_X86_64_KPTI_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "arch/x86_64/cpu.h"
#include "arch/x86_64/mitigation_mode.h"

typedef enum vos3_kpti_status {
    VOS3_KPTI_STATUS_UNINITIALIZED = 0,
    VOS3_KPTI_STATUS_READY         = 1,  /**< process-root transitions initialized */
    VOS3_KPTI_STATUS_DISABLED      = 2,  /**< reserved legacy status; init failures now halt */
    VOS3_KPTI_STATUS_INIT_FAILED   = 3   /**< allocation or copy failed — system panicked */
} vos3_kpti_status_t;

/** Validate mitigation/VMM ordering; failure halts rather than disabling isolation. */
vos3_kpti_status_t vos3_kpti_init(void);
void vos3_kpti_prepare_user_return(void);

/** Return this CPU's restricted root, or zero before a user return is prepared. */
uint64_t vos3_kpti_get_user_cr3(void);

/**
 * @brief Get the CR3 value to load on kernel-mode entry.
 * @return This CPU's bound full process root, using PCID 0.
 */
uint64_t vos3_kpti_get_kernel_cr3(void);

/**
 * @brief Read latched KPTI status.
 */
vos3_kpti_status_t vos3_kpti_get_status(void);
__attribute__((noreturn)) void vos3_kpti_panic_init_failed(const char* reason);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_KPTI_H */
