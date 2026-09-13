/**
 * @file virtio_core.c
 * @brief VOS3 Shared VirtIO Core — PCI Discovery, Queue Init
 *
 * @details Generic VirtIO infrastructure shared by all VirtIO drivers.
 *          Extracted from virtio_blk.c for reuse by virtio_vbus.
 *
 * @version 1.0.0
 * @date 2026-04-01
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1 — Binary Bridge
 */

#include "../../include/vos/virtio_core.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * PCI CONFIG SPACE ACCESS
 * ============================================================================ */

uint32_t virtio_pci_read32(uint8_t bus, uint8_t dev, uint8_t func,
                            uint8_t offset)
{
    uint32_t addr = (1U << 31)
                  | ((uint32_t)bus << 16)
                  | ((uint32_t)dev << 11)
                  | ((uint32_t)func << 8)
                  | (offset & 0xFC);
    virtio_outl(PCI_CONFIG_ADDR, addr);
    return virtio_inl(PCI_CONFIG_DATA);
}

uint16_t virtio_pci_read16(uint8_t bus, uint8_t dev, uint8_t func,
                            uint8_t offset)
{
    uint32_t val = virtio_pci_read32(bus, dev, func, offset & 0xFC);
    return (uint16_t)(val >> ((offset & 2) * 8));
}

void virtio_pci_write16(uint8_t bus, uint8_t dev, uint8_t func,
                         uint8_t offset, uint16_t value)
{
    uint32_t addr = (1U << 31)
                  | ((uint32_t)bus << 16)
                  | ((uint32_t)dev << 11)
                  | ((uint32_t)func << 8)
                  | (offset & 0xFC);
    virtio_outl(PCI_CONFIG_ADDR, addr);
    uint32_t old = virtio_inl(PCI_CONFIG_DATA);
    int shift = (offset & 2) * 8;
    old &= ~(0xFFFFU << shift);
    old |= ((uint32_t)value << shift);
    virtio_outl(PCI_CONFIG_ADDR, addr);
    virtio_outl(PCI_CONFIG_DATA, old);
}

/* ============================================================================
 * PCI DEVICE DISCOVERY
 * ============================================================================ */

int virtio_pci_find_device(uint16_t subsys_id, uint8_t *out_bus,
                            uint8_t *out_dev, uint8_t *out_func,
                            uint16_t *out_io_base)
{
    for (uint16_t bus = 0; bus < 256; bus++) {
        for (uint8_t dev = 0; dev < 32; dev++) {
            uint32_t id = virtio_pci_read32((uint8_t)bus, dev, 0, 0x00);
            if (id == 0xFFFFFFFF) {
                continue;
            }

            uint16_t vendor = (uint16_t)(id & 0xFFFF);
            uint16_t device = (uint16_t)(id >> 16);

            /* Match VirtIO vendor and transitional device ID range */
            if (vendor != VIRTIO_PCI_VENDOR_ID) {
                continue;
            }
            if (device < 0x1000U || device > 0x103FU) {
                continue;
            }

            /* Verify subsystem ID matches requested type */
            uint32_t subsys = virtio_pci_read32((uint8_t)bus, dev, 0, 0x2C);
            uint16_t found_subsys = (uint16_t)(subsys >> 16);
            if (found_subsys != subsys_id) {
                continue;
            }

            /* Read BAR0 — must be I/O port for legacy */
            uint32_t bar0 = virtio_pci_read32((uint8_t)bus, dev, 0, 0x10);
            if (!(bar0 & 0x01)) {
                VOS3_WARN("[VIRTIO] Device %u:%u.0 BAR0 is MMIO, need I/O port",
                          bus, dev);
                continue;
            }

            *out_bus = (uint8_t)bus;
            *out_dev = dev;
            *out_func = 0;
            *out_io_base = (uint16_t)(bar0 & 0xFFFC);

            VOS3_INFO("[VIRTIO] Found PCI %u:%u.0 vendor=%04x device=%04x "
                      "subsys=%u io=0x%04x",
                      bus, dev, vendor, device, found_subsys, *out_io_base);
            return 0;
        }
    }
    return -1;
}

/* ============================================================================
 * VIRTQUEUE INITIALIZATION
 * ============================================================================ */

int virtio_queue_init(virtio_queue_t *vq, uint16_t index, uint16_t num_desc,
                      uint16_t io_base, void *dma_mem, size_t dma_size)
{
    if (num_desc == 0 || num_desc > 256) {
        VOS3_ERROR("[VIRTIO] Invalid queue size %u", num_desc);
        return -1;
    }

    /*
     * VirtIO Legacy Queue Layout (spec mandated):
     *   [Descriptors]  num * 16 bytes
     *   [Available]    6 + 2*num bytes
     *   [padding to next 4096 boundary]
     *   [Used]         6 + 8*num bytes
     */
    size_t desc_size  = (size_t)num_desc * sizeof(virtio_desc_t);
    size_t avail_size = 6 + (size_t)num_desc * 2;
    size_t used_offset = (desc_size + avail_size + 4095) & ~(size_t)4095;
    size_t used_size   = 6 + (size_t)num_desc * 8;
    size_t total_size  = (used_offset + used_size + 4095) & ~(size_t)4095;

    if (total_size > dma_size) {
        VOS3_ERROR("[VIRTIO] Queue needs %zu bytes but buffer is %zu",
                   total_size, dma_size);
        return -1;
    }

    memset(vq, 0, sizeof(*vq));
    vq->index = index;
    vq->num_desc = num_desc;
    vq->lock = VOS3_SPINLOCK_INIT;

    /* Clear DMA memory */
    memset(dma_mem, 0, total_size);

    /* Set up ring pointers */
    vq->desc  = (volatile virtio_desc_t*)dma_mem;
    vq->avail = (volatile virtio_avail_t*)((uint8_t*)dma_mem + desc_size);
    vq->used  = (volatile virtio_used_t*)((uint8_t*)dma_mem + used_offset);

    vq->desc_phys = virtio_virt_to_phys(dma_mem);

    /* Initialize descriptor free list (circular chain) */
    for (uint16_t i = 0; i < num_desc; i++) {
        vq->desc[i].next = (i + 1) % num_desc;
    }
    vq->free_head = 0;
    vq->num_free = num_desc;
    vq->last_used_idx = 0;

    /* Tell device about the queue (legacy interface) */
    virtio_outw(io_base + VIRTIO_PCI_QUEUE_SEL, index);
    virtio_outl(io_base + VIRTIO_PCI_QUEUE_PFN,
                (uint32_t)(virtio_virt_to_phys(dma_mem) / 4096));

    VOS3_DEBUG("[VIRTIO] Queue %u: %u desc @ phys 0x%llx",
               index, num_desc,
               (unsigned long long)virtio_virt_to_phys(dma_mem));

    return 0;
}
