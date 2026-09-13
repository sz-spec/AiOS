/**
 * @file storage_hal.c
 * @brief Phase 9: Storage HAL -- AHCI/NVMe/VirtIO-Block abstraction
 *
 * Probes PCI bus for storage controllers by class code.
 * Initializes the highest-priority device found:
 *   NVMe (class 01:08) > AHCI (class 01:06) > VirtIO-Block (fallback)
 *
 * AHCI and NVMe use MMIO registers mapped through VMM.
 * Both support command queues for sector-level I/O.
 *
 * @version 1.0.0
 * @date 2026-04-09
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 9: Bare-Metal Peak (v23.0)
 */

#include "../../include/vos/storage_hal.h"
#include "../../include/vos/nvme.h"
#include "../../include/vos/pci.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include <stdint.h>

/* External VirtIO-Block API (3-arg signatures per virtio_blk.h) */
extern int vos3_virtio_blk_read(uint64_t sector, uint32_t count, void *buffer);
extern int vos3_virtio_blk_write(uint64_t sector, uint32_t count, const void *buffer);

/* Storage device table */
static vos3_storage_dev_t   g_storage_devs[VOS3_STORAGE_MAX_DEVICES];
static uint32_t             g_storage_count;
static int                  g_primary_idx = -1;  /* Index of active device */
static vos3_storage_stats_t g_storage_stats;
static int                  g_storage_inited;

/* ============================================================================
 * AHCI (SATA) -- Register Layout
 * ============================================================================ */

/* AHCI HBA Memory Registers (offset from BAR5/ABAR) */
#define AHCI_HBA_CAP        0x00    /* Host Capabilities */
#define AHCI_HBA_GHC        0x04    /* Global Host Control */
#define AHCI_HBA_IS         0x08    /* Interrupt Status */
#define AHCI_HBA_PI         0x0C    /* Ports Implemented */
#define AHCI_HBA_VS         0x10    /* Version */
#define AHCI_HBA_CAP2       0x24    /* Extended Capabilities */
#define AHCI_HBA_BOHC       0x28    /* BIOS/OS Handoff Control */

/* AHCI Port Registers (offset from port base = 0x100 + port*0x80) */
#define AHCI_PORT_CLB       0x00    /* Command List Base Address */
#define AHCI_PORT_CLBU      0x04    /* Command List Base Upper */
#define AHCI_PORT_FB        0x08    /* FIS Base Address */
#define AHCI_PORT_FBU       0x0C    /* FIS Base Upper */
#define AHCI_PORT_IS        0x10    /* Interrupt Status */
#define AHCI_PORT_IE        0x14    /* Interrupt Enable */
#define AHCI_PORT_CMD       0x18    /* Command and Status */
#define AHCI_PORT_TFD       0x20    /* Task File Data */
#define AHCI_PORT_SIG       0x24    /* Signature */
#define AHCI_PORT_SSTS      0x28    /* SATA Status */
#define AHCI_PORT_SCTL      0x2C    /* SATA Control */
#define AHCI_PORT_SERR      0x30    /* SATA Error */
#define AHCI_PORT_SACT      0x34    /* SATA Active */
#define AHCI_PORT_CI        0x38    /* Command Issue */

/* Port signatures */
#define AHCI_SIG_ATA        0x00000101  /* SATA drive */
#define AHCI_SIG_ATAPI      0xEB140101  /* SATAPI device */

/* Port CMD flags */
#define AHCI_CMD_ST         (1U << 0)   /* Start */
#define AHCI_CMD_FRE        (1U << 4)   /* FIS Receive Enable */
#define AHCI_CMD_FR         (1U << 14)  /* FIS Receive Running */
#define AHCI_CMD_CR         (1U << 15)  /* Command List Running */

/* ============================================================================
 * NVMe -- Register Layout
 * ============================================================================ */

#define NVME_REG_CAP        0x00    /* Controller Capabilities (64-bit) */
#define NVME_REG_VS         0x08    /* Version */
#define NVME_REG_INTMS      0x0C    /* Interrupt Mask Set */
#define NVME_REG_INTMC      0x10    /* Interrupt Mask Clear */
#define NVME_REG_CC         0x14    /* Controller Configuration */
#define NVME_REG_CSTS       0x1C    /* Controller Status */
#define NVME_REG_AQA        0x24    /* Admin Queue Attributes */
#define NVME_REG_ASQ        0x28    /* Admin SQ Base (64-bit) */
#define NVME_REG_ACQ        0x30    /* Admin CQ Base (64-bit) */

/* NVMe CC flags */
#define NVME_CC_EN          (1U << 0)   /* Enable */
#define NVME_CC_CSS_NVM     (0U << 4)   /* NVM Command Set */
#define NVME_CC_MPS_4K      (0U << 7)   /* Memory Page Size 4KB */
#define NVME_CC_IOSQES(n)   ((uint32_t)(n) << 16)  /* I/O SQ Entry Size (log2) */
#define NVME_CC_IOCQES(n)   ((uint32_t)(n) << 20)  /* I/O CQ Entry Size (log2) */

/* NVMe CSTS flags */
#define NVME_CSTS_RDY       (1U << 0)   /* Ready */
#define NVME_CSTS_CFS       (1U << 1)   /* Controller Fatal Status */

/* ---- MMIO Helpers ---- */

static inline uint32_t mmio_read32(uintptr_t base, uint32_t offset)
{
    volatile uint32_t *reg = (volatile uint32_t *)(base + offset);
    return *reg;
}

static inline void mmio_write32(uintptr_t base, uint32_t offset, uint32_t val)
{
    volatile uint32_t *reg = (volatile uint32_t *)(base + offset);
    *reg = val;
}

static inline uint64_t mmio_read64(uintptr_t base, uint32_t offset)
{
    volatile uint64_t *reg = (volatile uint64_t *)(base + offset);
    return *reg;
}

/* ---- PCI Config Space Helpers ---- */

static uint32_t pci_cfg_read32(uint8_t bus, uint8_t dev, uint8_t func, uint8_t off)
{
    uint32_t addr = (1U << 31) | ((uint32_t)bus << 16) | ((uint32_t)dev << 11) |
                    ((uint32_t)func << 8) | (off & 0xFC);
    __asm__ volatile("outl %0, %1" :: "a"(addr), "Nd"((uint16_t)0xCF8));
    uint32_t val;
    __asm__ volatile("inl %1, %0" : "=a"(val) : "Nd"((uint16_t)0xCFC));
    return val;
}

/* ---- AHCI Probe ---- */

static int ahci_probe(vos3_storage_dev_t *dev, const vos3_pci_device_t *pci)
{
    /* AHCI uses BAR5 (ABAR) for HBA memory registers */
    uint32_t abar = pci_cfg_read32(pci->bus, pci->dev, pci->func, 0x24); /* BAR5 offset */
    abar &= ~0xFU; /* Mask lower bits */
    if (abar == 0)
        return -1;

    /* Map AHCI MMIO region (4KB minimum) */
    uintptr_t mmio_va = 0xFFFF880030000000ULL; /* AHCI MMIO region */
    vos3_vmm_map(mmio_va, (uintptr_t)abar,
                 (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_NOCACHE));

    dev->mmio_base = mmio_va;
    dev->bar0 = abar;

    /* Read HBA capabilities */
    uint32_t cap = mmio_read32(mmio_va, AHCI_HBA_CAP);
    uint32_t pi  = mmio_read32(mmio_va, AHCI_HBA_PI);
    uint32_t ver = mmio_read32(mmio_va, AHCI_HBA_VS);

    dev->ahci_cap = cap;
    uint32_t num_ports = (cap & 0x1F) + 1;

    VOS3_INFO("[AHCI] HBA v%u.%u, %u ports, PI=0x%x, ABAR=0x%x",
              ver >> 16, (ver >> 8) & 0xFF, num_ports, pi, abar);

    /* Find first implemented port with a device attached */
    for (uint32_t p = 0; p < num_ports && p < 32; p++) {
        if (!(pi & (1U << p)))
            continue;

        /* Port registers are at HBA_BASE + 0x100 + port * 0x80 */
        uint32_t port_off = 0x100 + p * 0x80;
        uint32_t ssts = mmio_read32(mmio_va, port_off + 0x28);
        uint32_t det = ssts & 0xF;

        if (det == 3) { /* Device present and communication established */
            uint32_t sig = mmio_read32(mmio_va, port_off + 0x24);
            VOS3_INFO("[AHCI] Port %u: device present (sig=0x%x, det=%u)",
                      p, sig, det);

            if (sig == AHCI_SIG_ATA) {
                dev->ahci_port = p;
                dev->type = VOS3_STORAGE_AHCI;
                dev->sector_size = 512;
                dev->max_transfer = 128; /* Conservative: 64KB */
                dev->active = 1;

                /* TODO: Send IDENTIFY command to get sector count and model */
                dev->sector_count = 0; /* Unknown until IDENTIFY */
                __builtin_memset(dev->model, 0, sizeof(dev->model));

                VOS3_INFO("[AHCI] SATA drive on port %u initialized", p);
                return 0;
            }
        }
    }

    VOS3_INFO("[AHCI] No SATA drives found on any port");
    return -19; /* -ENODEV */
}

/* ---- NVMe Probe ---- */

static int nvme_probe(vos3_storage_dev_t *dev, const vos3_pci_device_t *pci)
{
    /* NVMe uses BAR0 for controller registers */
    uint32_t bar0 = pci->bar[0] & ~0xFU;
    if (bar0 == 0)
        return -1;

    /* Map NVMe MMIO region (at least 16KB for queues) */
    uintptr_t mmio_va = 0xFFFF880031000000ULL; /* NVMe MMIO region */

    /* Map 64KB (covers controller regs + doorbell stride) */
    for (uint32_t pg = 0; pg < 16; pg++) {
        vos3_vmm_map(mmio_va + pg * 0x1000,
                     (uintptr_t)bar0 + pg * 0x1000,
                     (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_NOCACHE));
    }

    dev->mmio_base = mmio_va;
    dev->bar0 = bar0;

    /* Read controller capabilities */
    uint64_t cap = mmio_read64(mmio_va, NVME_REG_CAP);
    uint32_t ver = mmio_read32(mmio_va, NVME_REG_VS);
    uint32_t csts = mmio_read32(mmio_va, NVME_REG_CSTS);

    uint32_t mqes = (uint32_t)(cap & 0xFFFF) + 1; /* Max Queue Entries */
    uint32_t dstrd = (uint32_t)((cap >> 32) & 0xF); /* Doorbell Stride */

    VOS3_INFO("[NVMe] Controller v%u.%u.%u, MQES=%u, DSTRD=%u, CSTS=0x%x, BAR0=0x%x",
              ver >> 16, (ver >> 8) & 0xFF, ver & 0xFF,
              mqes, dstrd, csts, bar0);

    /* Check if controller is in fatal state */
    if (csts & NVME_CSTS_CFS) {
        VOS3_INFO("[NVMe] Controller in fatal state, attempting reset");
        mmio_write32(mmio_va, NVME_REG_CC, 0); /* Disable */
        /* Wait for not ready */
        for (int i = 0; i < 100000; i++) {
            if (!(mmio_read32(mmio_va, NVME_REG_CSTS) & NVME_CSTS_RDY))
                break;
        }
    }

    dev->type = VOS3_STORAGE_NVME;
    dev->nvme_nsid = 1; /* Default namespace */
    dev->nvme_sqes = 6; /* log2(64) = 6 */
    dev->nvme_cqes = 4; /* log2(16) = 4 */
    dev->sector_size = 512;
    dev->max_transfer = 256; /* 128KB */
    dev->sector_count = 0; /* Unknown until Identify Namespace */
    dev->active = 1;
    __builtin_memset(dev->model, 0, sizeof(dev->model));

    VOS3_INFO("[NVMe] Controller detected and registered (full init requires Admin Queue setup)");

    /* NOTE: Full NVMe initialization (Admin Queue creation, Identify command,
     * I/O Queue creation) is Phase 9.2. This probe establishes the foundation. */

    return 0;
}

/* ---- HAL Init ---- */

int vos3_storage_hal_init(void)
{
    if (g_storage_inited)
        return 0;

    __builtin_memset(g_storage_devs, 0, sizeof(g_storage_devs));
    __builtin_memset(&g_storage_stats, 0, sizeof(g_storage_stats));
    g_storage_count = 0;
    g_primary_idx = -1;

    const vos3_pci_device_t *devs = vos3_pci_get_devices();
    int pci_count = vos3_pci_get_count();

    VOS3_INFO("[STORAGE-HAL] Scanning %d PCI devices for storage controllers", pci_count);

    /* Scan for NVMe first (highest priority) */
    for (int i = 0; i < pci_count && g_storage_count < VOS3_STORAGE_MAX_DEVICES; i++) {
        if (devs[i].class_code == 0x01 && devs[i].subclass == 0x08) {
            VOS3_INFO("[STORAGE-HAL] NVMe controller at %u:%u.%u",
                      devs[i].bus, devs[i].dev, devs[i].func);
            vos3_storage_dev_t *sd = &g_storage_devs[g_storage_count];
            sd->pci_bus = devs[i].bus;
            sd->pci_dev = devs[i].dev;
            sd->pci_func = devs[i].func;
            if (nvme_probe(sd, &devs[i]) == 0) {
                /* Full NVMe init: Admin Queue → Identify → I/O Queues */
                int nvme_rc = vos3_nvme_init(sd->mmio_base, sd->bar0);
                if (nvme_rc == 0) {
                    /* Update device info from Identify results */
                    const vos3_nvme_ctrl_t *ctrl = vos3_nvme_get_ctrl();
                    if (ctrl && ctrl->ns_size > 0) {
                        sd->sector_count = ctrl->ns_size;
                        sd->sector_size  = ctrl->ns_lba_size;
                    }
                    VOS3_INFO("[STORAGE-HAL] NVMe fully initialized (sectors=%llu, blksz=%u)",
                              (unsigned long long)sd->sector_count, sd->sector_size);
                } else {
                    VOS3_INFO("[STORAGE-HAL] NVMe full init failed (rc=%d), probe-only mode", nvme_rc);
                }
                if (g_primary_idx < 0)
                    g_primary_idx = (int)g_storage_count;
                g_storage_count++;
            }
        }
    }

    /* Scan for AHCI (medium priority) */
    for (int i = 0; i < pci_count && g_storage_count < VOS3_STORAGE_MAX_DEVICES; i++) {
        if (devs[i].class_code == 0x01 && devs[i].subclass == 0x06) {
            VOS3_INFO("[STORAGE-HAL] AHCI controller at %u:%u.%u",
                      devs[i].bus, devs[i].dev, devs[i].func);
            vos3_storage_dev_t *sd = &g_storage_devs[g_storage_count];
            sd->pci_bus = devs[i].bus;
            sd->pci_dev = devs[i].dev;
            sd->pci_func = devs[i].func;
            if (ahci_probe(sd, &devs[i]) == 0) {
                if (g_primary_idx < 0)
                    g_primary_idx = (int)g_storage_count;
                g_storage_count++;
            }
        }
    }

    /* VirtIO-Block fallback (always available as device index) */
    if (g_storage_count < VOS3_STORAGE_MAX_DEVICES) {
        vos3_storage_dev_t *sd = &g_storage_devs[g_storage_count];
        sd->type = VOS3_STORAGE_VIRTIO;
        sd->sector_size = 512;
        sd->max_transfer = 256; /* Matches VOS3_MAX_SECTORS_PER_REQ */
        sd->active = 1;
        __builtin_memset(sd->model, 0, sizeof(sd->model));
        if (g_primary_idx < 0)
            g_primary_idx = (int)g_storage_count;
        g_storage_count++;
        VOS3_INFO("[STORAGE-HAL] VirtIO-Block registered as fallback device");
    }

    g_storage_inited = 1;

    VOS3_INFO("[STORAGE-HAL] %u devices found, primary=%s",
              g_storage_count,
              g_primary_idx >= 0 ?
                  (g_storage_devs[g_primary_idx].type == VOS3_STORAGE_NVME ? "NVMe" :
                   g_storage_devs[g_primary_idx].type == VOS3_STORAGE_AHCI ? "AHCI" :
                   "VirtIO") : "none");

    return 0;
}

/* ---- Read/Write (dispatch to active device) ---- */

int vos3_storage_read(uint64_t lba, void *buf, uint32_t count)
{
    if (!g_storage_inited || g_primary_idx < 0)
        return -6; /* -ENXIO */

    vos3_storage_dev_t *dev = &g_storage_devs[g_primary_idx];

    int rc = 0;
    switch (dev->type) {
    case VOS3_STORAGE_VIRTIO:
        /* VirtIO-Block: sector, count, buffer (matches virtio_blk.h API) */
        rc = vos3_virtio_blk_read(lba, count, buf);
        break;

    case VOS3_STORAGE_AHCI:
        /* AHCI full I/O: Phase 9.2 (requires Command Table + FIS setup) */
        VOS3_INFO("[AHCI] Read not yet implemented (probe-only in Phase 9.1)");
        rc = -38; /* -ENOSYS */
        break;

    case VOS3_STORAGE_NVME:
        rc = vos3_nvme_read(lba, count, buf);
        break;

    default:
        rc = -19; /* -ENODEV */
        break;
    }

    if (rc == 0) {
        g_storage_stats.reads += count;
        g_storage_stats.bytes_read += (uint64_t)count * dev->sector_size;
    } else {
        g_storage_stats.errors++;
    }

    return rc;
}

int vos3_storage_write(uint64_t lba, const void *buf, uint32_t count)
{
    if (!g_storage_inited || g_primary_idx < 0)
        return -6; /* -ENXIO */

    vos3_storage_dev_t *dev = &g_storage_devs[g_primary_idx];

    int rc = 0;
    switch (dev->type) {
    case VOS3_STORAGE_VIRTIO:
        /* VirtIO-Block: sector, count, buffer (matches virtio_blk.h API) */
        rc = vos3_virtio_blk_write(lba, count, buf);
        break;

    case VOS3_STORAGE_AHCI:
        rc = -38; /* -ENOSYS */
        break;

    case VOS3_STORAGE_NVME:
        rc = vos3_nvme_write(lba, count, buf);
        break;

    default:
        rc = -19; /* -ENODEV */
        break;
    }

    if (rc == 0) {
        g_storage_stats.writes += count;
        g_storage_stats.bytes_written += (uint64_t)count * dev->sector_size;
    } else {
        g_storage_stats.errors++;
    }

    return rc;
}

/* ---- Info/Stats ---- */

int vos3_storage_get_device(uint32_t index, vos3_storage_dev_t *out)
{
    if (index >= g_storage_count || !out)
        return -22; /* -EINVAL */
    *out = g_storage_devs[index];
    return 0;
}

uint32_t vos3_storage_device_count(void)
{
    return g_storage_count;
}

int vos3_storage_get_stats(vos3_storage_stats_t *out)
{
    if (!out) return -14; /* -EFAULT */
    *out = g_storage_stats;
    return 0;
}

vos3_storage_type_t vos3_storage_active_type(void)
{
    if (g_primary_idx < 0)
        return VOS3_STORAGE_NONE;
    return g_storage_devs[g_primary_idx].type;
}
