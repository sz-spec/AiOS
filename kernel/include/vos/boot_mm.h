#ifndef VOS3_BOOT_MM_H
#define VOS3_BOOT_MM_H

#include "boot_info.h"

/**
 * @brief Initialize memory management subsystems.
 *
 * PMM, UEFI memory map parsing, HugePage pool reservation,
 * VMM, and kernel heap.  Panics on fatal failure.
 *
 * @param boot_info Validated boot information structure.
 * @return 0 on success (never returns on fatal error).
 */
int boot_mm_init(const vos3_boot_info_t *boot_info);

#endif /* VOS3_BOOT_MM_H */
