/**
 * @file ivshmem.h
 * @brief VOS3 ivshmem (Inter-VM Shared Memory) PCI Driver
 *
 * @details Minimal PCI discovery driver for QEMU ivshmem-plain device.
 *          Scans PCI bus for vendor 0x1AF4 / device 0x1110, reads BAR2
 *          for the shared memory region, and maps it into kernel space.
 *
 * @version 0.1.0
 * @date 2026-04-03
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.9a — Skeleton for future Warp Drive work.
 *       Not wired into kmain.c yet (no QEMU -device ivshmem-plain).
 */

#ifndef VOS3_IVSHMEM_H
#define VOS3_IVSHMEM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * IVSHMEM PCI IDENTITY
 * ============================================================================ */

/** @brief ivshmem PCI vendor ID (Red Hat) */
#define IVSHMEM_VENDOR_ID   0x1AF4U

/** @brief ivshmem PCI device ID */
#define IVSHMEM_DEVICE_ID   0x1110U

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Probe PCI bus for ivshmem device and map shared memory
 *
 * @return 0 on success, -1 if device not found
 */
int vos3_ivshmem_init(void);

/**
 * @brief Get kernel virtual address of shared memory region
 *
 * @return Mapped base address, or NULL if not initialized
 */
void *vos3_ivshmem_base(void);

/**
 * @brief Get size of shared memory region in bytes
 *
 * @return Size in bytes, or 0 if not initialized
 */
size_t vos3_ivshmem_size(void);

/* ============================================================================
 * PHASE 4.9b: WARP DRIVE — Zone Partitioning
 * ============================================================================ */

/** Zone size per model slot (16MB = 64MB / 4 slots) */
#define VOS3_WARP_ZONE_SIZE     (16U * 1024U * 1024U)

/** Maximum warp zones (matches VOS3_MODEL_SLOT_MAX) */
#define VOS3_WARP_ZONE_MAX      4U

/**
 * @brief Get base address of a warp zone by slot_id
 *
 * @param[in] slot_id Slot index (0-3)
 * @return Kernel virtual address of the warp zone, or NULL if unavailable
 */
void *vos3_ivshmem_zone_base(uint8_t slot_id);

/**
 * @brief Get base address of a warp zone without ownership check (kernel-internal)
 *
 * @param[in] slot_id Slot index (0-3)
 * @return Kernel virtual address of the warp zone, or NULL if unavailable
 */
void *vos3_ivshmem_zone_base_unchecked(uint8_t slot_id);

/**
 * @brief Check if ivshmem is available
 *
 * @return 1 if ivshmem is mapped, 0 otherwise
 */
int vos3_ivshmem_available(void);

/**
 * @brief Cold scrub a warp zone — zero + clflush every cache line.
 *
 * @details Eliminates "Billing Ghost" residual data from a previous
 *          model slot tenant. Zeros the zone, then flushes all cache
 *          lines to ensure no stale inference data remains in L1/L2/L3.
 *
 * @param[in] slot_id Slot index (0-3)
 */
void vos3_ivshmem_cold_scrub(uint8_t slot_id);

/**
 * @brief v23.7: Acquire reader lock on a warp zone (blocks cold_scrub)
 * @param[in] slot_id Slot index (0-3)
 */
void vos3_ivshmem_zone_reader_acquire(uint8_t slot_id);

/**
 * @brief v23.7: Release reader lock on a warp zone
 * @param[in] slot_id Slot index (0-3)
 */
void vos3_ivshmem_zone_reader_release(uint8_t slot_id);

/**
 * @brief Partial cold scrub — scrub N cachelines from cursor position.
 *
 * @details Kinetic Fill variant: scrubs a small number of cachelines per
 *          call using movnti (non-temporal), advancing a per-slot cursor
 *          that wraps around for continuous background sanitization.
 *          Replaces dead-wait with productive jitter-work.
 *
 * @param[in] slot_id Slot index (0-3)
 * @param[in] lines   Number of 64-byte cachelines to scrub
 * @return Number of cachelines actually scrubbed
 */
uint32_t vos3_ivshmem_cold_scrub_partial(uint8_t slot_id, uint32_t lines);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_IVSHMEM_H */
