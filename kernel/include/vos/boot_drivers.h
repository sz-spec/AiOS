#ifndef VOS3_BOOT_DRIVERS_H
#define VOS3_BOOT_DRIVERS_H

#include "boot_info.h"

/**
 * @brief Initialize hardware driver subsystems.
 *
 * CRC64, PCI bus scan, ACPI, Storage HAL, ivshmem Warp Drive,
 * DMA engine, HD Audio, Driver Synthesis, NPU dispatch, and
 * kernel .text integrity hash.  Panics on fatal failure.
 *
 * @param boot_info Validated boot information structure.
 * @return 0 on success (never returns on fatal error).
 */
int boot_drivers_init(const vos3_boot_info_t *boot_info);

#endif /* VOS3_BOOT_DRIVERS_H */
