/**
 * @file storage_hal.h
 * @brief Phase 9: Storage HAL -- Abstract interface for AHCI, NVMe, VirtIO-Block
 *
 * @details Provides a unified block device API. At boot, probes PCI for
 *          storage controllers. Priority: NVMe > AHCI > VirtIO-Block.
 *
 * @version 1.0.0
 * @date 2026-04-09
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 9: Bare-Metal Peak (v23.0)
 */

#ifndef VOS3_STORAGE_HAL_H
#define VOS3_STORAGE_HAL_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* Phase 9: Storage HAL -- Abstract interface for AHCI, NVMe, VirtIO-Block
 *
 * Provides a unified block device API. At boot, probes PCI for
 * storage controllers. Priority: NVMe > AHCI > VirtIO-Block.
 */

#define VOS3_STORAGE_MAX_DEVICES    4U
#define VOS3_STORAGE_SECTOR_SIZE    512U

/* Storage device type */
typedef enum {
    VOS3_STORAGE_NONE     = 0,
    VOS3_STORAGE_VIRTIO   = 1,   /* VirtIO-Block (QEMU) */
    VOS3_STORAGE_AHCI     = 2,   /* AHCI (SATA) */
    VOS3_STORAGE_NVME     = 3    /* NVMe (PCIe SSD) */
} vos3_storage_type_t;

/* Storage device descriptor */
typedef struct vos3_storage_dev {
    vos3_storage_type_t type;
    uint8_t             pci_bus;
    uint8_t             pci_dev;
    uint8_t             pci_func;
    uint32_t            bar0;           /* BAR0 address (MMIO base) */
    uintptr_t           mmio_base;      /* Kernel-mapped MMIO virtual address */
    uint64_t            sector_count;   /* Total sectors */
    uint32_t            sector_size;    /* Bytes per sector (512 or 4096) */
    uint32_t            max_transfer;   /* Max sectors per transfer */
    char                model[40];      /* Drive model string */
    uint8_t             active;         /* 1 = initialized and usable */

    /* AHCI-specific */
    uint32_t            ahci_port;      /* Port number */
    uint32_t            ahci_cap;       /* HBA capabilities */

    /* NVMe-specific */
    uint32_t            nvme_nsid;      /* Namespace ID */
    uint16_t            nvme_sqes;      /* Submission queue entry size */
    uint16_t            nvme_cqes;      /* Completion queue entry size */
} vos3_storage_dev_t;

/* Statistics */
typedef struct vos3_storage_stats {
    uint64_t            reads;
    uint64_t            writes;
    uint64_t            bytes_read;
    uint64_t            bytes_written;
    uint64_t            errors;
} vos3_storage_stats_t;

/* ---- HAL API ---- */

/* Initialize storage HAL -- probe PCI for controllers */
int vos3_storage_hal_init(void);

/* Read sectors from the primary storage device */
int vos3_storage_read(uint64_t lba, void *buf, uint32_t count);

/* Write sectors to the primary storage device */
int vos3_storage_write(uint64_t lba, const void *buf, uint32_t count);

/* Get storage device info */
int vos3_storage_get_device(uint32_t index, vos3_storage_dev_t *out);

/* Get device count */
uint32_t vos3_storage_device_count(void);

/* Get statistics */
int vos3_storage_get_stats(vos3_storage_stats_t *out);

/* Get the active (primary) storage type */
vos3_storage_type_t vos3_storage_active_type(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_STORAGE_HAL_H */
