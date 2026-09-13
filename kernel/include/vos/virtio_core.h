/**
 * @file virtio_core.h
 * @brief VOS3 Shared VirtIO Core — PCI Discovery, Queue Management, I/O Helpers
 *
 * @details Shared infrastructure for all VirtIO device drivers.
 *          Extracted from virtio_blk.c/h for reuse by virtio_vbus driver.
 *
 *          Provides:
 *          - Generic VirtIO PCI constants and status bits
 *          - Split Virtqueue descriptor/ring types
 *          - PCI bus scan and config space access
 *          - Queue initialization with caller-provided DMA memory
 *          - Inline I/O port helpers
 *
 * @version 1.0.0
 * @date 2026-04-01
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1 — Binary Bridge
 */

#ifndef VOS3_VIRTIO_CORE_H
#define VOS3_VIRTIO_CORE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "sync.h"

/* ============================================================================
 * VIRTIO PCI CONSTANTS
 * ============================================================================ */

/** @brief VirtIO PCI vendor ID (Red Hat / OASIS) */
#define VIRTIO_PCI_VENDOR_ID            0x1AF4U

/* ============================================================================
 * VIRTIO DEVICE STATUS BITS (spec §2.1)
 * ============================================================================ */

/** @brief Guest OS has found the device */
#define VIRTIO_STATUS_ACK               0x01U

/** @brief Guest OS knows how to drive the device */
#define VIRTIO_STATUS_DRIVER            0x02U

/** @brief Driver is set up and ready to drive the device */
#define VIRTIO_STATUS_DRIVER_OK         0x04U

/** @brief Driver has acknowledged all features */
#define VIRTIO_STATUS_FEATURES_OK       0x08U

/** @brief Device needs reset due to error */
#define VIRTIO_STATUS_NEEDS_RESET       0x40U

/** @brief Something went wrong in the driver/guest */
#define VIRTIO_STATUS_FAILED            0x80U

/* ============================================================================
 * VIRTQUEUE DESCRIPTOR FLAGS (spec §2.6.5)
 * ============================================================================ */

/** @brief Next descriptor in chain */
#define VIRTQ_DESC_F_NEXT               0x01U

/** @brief Buffer is device-writable */
#define VIRTQ_DESC_F_WRITE              0x02U

/* ============================================================================
 * LEGACY I/O PORT REGISTER OFFSETS (spec §4.1.4.8)
 * ============================================================================ */

#define VIRTIO_PCI_HOST_FEATURES        0x00  /* 4 bytes, R   */
#define VIRTIO_PCI_GUEST_FEATURES       0x04  /* 4 bytes,   W */
#define VIRTIO_PCI_QUEUE_PFN            0x08  /* 4 bytes, R/W */
#define VIRTIO_PCI_QUEUE_SIZE           0x0C  /* 2 bytes, R   */
#define VIRTIO_PCI_QUEUE_SEL            0x0E  /* 2 bytes,   W */
#define VIRTIO_PCI_QUEUE_NOTIFY         0x10  /* 2 bytes,   W */
#define VIRTIO_PCI_STATUS               0x12  /* 1 byte,  R/W */
#define VIRTIO_PCI_ISR                  0x13  /* 1 byte,  R   */
#define VIRTIO_PCI_CONFIG               0x14  /* device-specific config */

/* ============================================================================
 * PCI CONFIG SPACE PORTS
 * ============================================================================ */

#define PCI_CONFIG_ADDR                 0x0CF8U
#define PCI_CONFIG_DATA                 0x0CFCU

/* ============================================================================
 * VIRTUAL TO PHYSICAL ADDRESS CONVERSION (for DMA)
 * ============================================================================ */

/** @brief Kernel higher-half base (from linker script) */
#define VIRTIO_KBASE  ((uintptr_t)0xFFFFFFFF80000000ULL)

/* ============================================================================
 * INLINE I/O PORT HELPERS
 * ============================================================================ */

static inline uint8_t virtio_inb(uint16_t port)
{
    uint8_t ret;
    __asm__ volatile("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline void virtio_outb(uint16_t port, uint8_t val)
{
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint16_t virtio_inw(uint16_t port)
{
    uint16_t ret;
    __asm__ volatile("inw %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline void virtio_outw(uint16_t port, uint16_t val)
{
    __asm__ volatile("outw %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint32_t virtio_inl(uint16_t port)
{
    uint32_t ret;
    __asm__ volatile("inl %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline void virtio_outl(uint16_t port, uint32_t val)
{
    __asm__ volatile("outl %0, %1" : : "a"(val), "Nd"(port));
}

/** @brief Full memory barrier (mfence) */
static inline void virtio_mb(void)
{
    __asm__ volatile("mfence" ::: "memory");
}

/** @brief Convert kernel virtual address (KBASE range) to physical for DMA */
static inline uint64_t virtio_virt_to_phys(const void *vaddr)
{
    return (uint64_t)((uintptr_t)vaddr - VIRTIO_KBASE);
}

/* ============================================================================
 * GENERIC VIRTQUEUE TYPES
 * ============================================================================ */

/**
 * @brief Generic VirtQueue descriptor (16 bytes, packed)
 */
typedef struct virtio_desc {
    uint64_t addr;      /**< Physical address of buffer */
    uint32_t len;       /**< Length of buffer */
    uint16_t flags;     /**< Descriptor flags */
    uint16_t next;      /**< Next descriptor index (if NEXT flag set) */
} __attribute__((packed)) virtio_desc_t;

/**
 * @brief Generic VirtQueue available ring
 *
 * @note ring[] is sized at 256 (maximum). Drivers must only use
 *       entries 0..num_desc-1.
 */
typedef struct virtio_avail {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[256];
    uint16_t used_event;
} __attribute__((packed)) virtio_avail_t;

/**
 * @brief Generic VirtQueue used ring element
 */
typedef struct virtio_used_elem {
    uint32_t id;        /**< Descriptor head index */
    uint32_t len;       /**< Number of bytes written */
} __attribute__((packed)) virtio_used_elem_t;

/**
 * @brief Generic VirtQueue used ring
 */
typedef struct virtio_used {
    uint16_t flags;
    uint16_t idx;
    virtio_used_elem_t ring[256];
    uint16_t avail_event;
} __attribute__((packed)) virtio_used_t;

/**
 * @brief Generic VirtQueue state
 */
typedef struct virtio_queue {
    uint16_t index;           /**< Queue index */
    uint16_t num_desc;        /**< Number of descriptors */

    volatile virtio_desc_t*  desc;   /**< Descriptor ring (DMA) */
    volatile virtio_avail_t* avail;  /**< Available ring (DMA) */
    volatile virtio_used_t*  used;   /**< Used ring (DMA) */

    uint64_t desc_phys;       /**< Physical address of descriptor ring */

    uint16_t free_head;       /**< Free descriptor list head */
    uint16_t num_free;        /**< Number of free descriptors */
    uint16_t last_used_idx;   /**< Last seen used index */

    vos3_spinlock_t lock;     /**< Queue access lock */
} virtio_queue_t;

/* ============================================================================
 * PCI CONFIG SPACE ACCESS
 * ============================================================================ */

/**
 * @brief Read 32-bit PCI configuration register
 */
uint32_t virtio_pci_read32(uint8_t bus, uint8_t dev, uint8_t func,
                            uint8_t offset);

/**
 * @brief Read 16-bit PCI configuration register
 */
uint16_t virtio_pci_read16(uint8_t bus, uint8_t dev, uint8_t func,
                            uint8_t offset);

/**
 * @brief Write 16-bit PCI configuration register
 */
void virtio_pci_write16(uint8_t bus, uint8_t dev, uint8_t func,
                         uint8_t offset, uint16_t value);

/* ============================================================================
 * PCI DEVICE DISCOVERY
 * ============================================================================ */

/**
 * @brief Scan PCI bus for a VirtIO device by subsystem ID
 *
 * Scans all PCI buses/devices for vendor 0x1AF4 with transitional
 * device IDs (0x1000-0x103F) matching the given subsystem ID.
 *
 * @param[in]  subsys_id  Subsystem ID to match (e.g., 2=blk, 3=serial)
 * @param[out] out_bus    Found bus number
 * @param[out] out_dev    Found device number
 * @param[out] out_func   Found function number
 * @param[out] out_io_base Found I/O base from BAR0
 * @return 0 on success, -1 if not found
 */
int virtio_pci_find_device(uint16_t subsys_id, uint8_t *out_bus,
                            uint8_t *out_dev, uint8_t *out_func,
                            uint16_t *out_io_base);

/* ============================================================================
 * VIRTQUEUE INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize a split virtqueue with caller-provided DMA memory
 *
 * Sets up descriptor ring, available ring, and used ring within the
 * provided DMA buffer. Initializes the free descriptor chain.
 * Notifies the device of the queue's physical address.
 *
 * @param[out] vq         Queue state to initialize
 * @param[in]  index      Queue index for this device
 * @param[in]  num_desc   Number of descriptors (from device)
 * @param[in]  io_base    Device I/O port base
 * @param[in]  dma_mem    Caller-provided DMA buffer (KBASE range, page-aligned)
 * @param[in]  dma_size   Size of DMA buffer in bytes
 * @return 0 on success, -1 on failure
 */
int virtio_queue_init(virtio_queue_t *vq, uint16_t index, uint16_t num_desc,
                      uint16_t io_base, void *dma_mem, size_t dma_size);

/* ============================================================================
 * UTILITY
 * ============================================================================ */

/**
 * @brief Yield to QEMU event loop
 *
 * Write to POST diagnostic port (0x80) to force a TCG exit,
 * allowing QEMU's main loop to process async I/O completions.
 */
static inline void virtio_yield(void)
{
    virtio_outb(0x80, 0);
}

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VIRTIO_CORE_H */
