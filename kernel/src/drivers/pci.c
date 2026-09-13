/**
 * @file pci.c
 * @brief VOS3 Generic PCI Bus Scanner
 *
 * @details Enumerates PCI configuration space across bus 0-255,
 *          device 0-31, function 0-7. Stores discovered devices in
 *          g_pci_devices[] for use by HAL and driver init.
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase v20.0 — HAL Foundation
 */

#include "../../include/vos/pci.h"
#include "../../include/vos/virtio_core.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/pci_ecam.h"

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

static vos3_pci_device_t g_pci_devices[VOS3_PCI_MAX_DEVICES];
static int g_pci_count = 0;

/* ============================================================================
 * PCI CONFIG SPACE ACCESS — universal transport dispatcher
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P3
 *
 * Tries ECAM first (MCFG-mapped MMIO, mandatory for PCIe modern
 * config space > offset 0xFF). Falls back to legacy Port-I/O via
 * `virtio_pci_read32` (0xCF8 / 0xCFC) when ECAM is unavailable —
 * older chipsets without MCFG, or when MMIO mapping failed.
 *
 * Per-call dispatch cost is a single is_available() bool read; the
 * branch predictor locks onto it after the first few calls so the
 * steady-state per-read overhead is < 1 ns.
 * ============================================================================ */

static uint32_t pci_read32(uint8_t bus, uint8_t dev, uint8_t func, uint8_t reg)
{
    if (vos3_pci_ecam_is_available()) {
        return vos3_pci_ecam_read32(bus, dev, func, (uint16_t)reg);
    }
    /* Legacy fallback — 256-byte config space only. The 0xFFFFFFFF
     * sentinel is honored by the scanner as "no device". */
    return virtio_pci_read32(bus, dev, func, reg);
}

/* ============================================================================
 * DEVICE CLASSIFICATION
 * ============================================================================ */

const char *vos3_pci_class_name(uint8_t class_code, uint8_t subclass)
{
    switch (class_code) {
        case PCI_CLASS_STORAGE:
            switch (subclass) {
                case PCI_SUBCLASS_IDE:    return "IDE Controller";
                case PCI_SUBCLASS_AHCI:   return "AHCI Controller";
                case PCI_SUBCLASS_NVME:   return "NVMe Controller";
                default:                  return "Storage Controller";
            }
        case PCI_CLASS_NETWORK:   return "Network Controller";
        case PCI_CLASS_DISPLAY:
            if (subclass == PCI_SUBCLASS_VGA) return "VGA Controller";
            return "Display Controller";
        case PCI_CLASS_MULTIMEDIA: return "Multimedia Device";
        case PCI_CLASS_MEMORY:     return "Memory Controller";
        case PCI_CLASS_BRIDGE:     return "Bridge Device";
        case PCI_CLASS_COMM:
            if (subclass == 0x80U) return "VirtIO-Serial";
            return "Communication Controller";
        case PCI_CLASS_SYSTEM:     return "System Device";
        case 0xFFU:
            if (subclass == 0x00U) return "ivshmem/Misc";
            return "Unassigned";
        default:                   return "Unknown";
    }
}

/* ============================================================================
 * BUS SCAN — Recursive with PCI-to-PCI Bridge Support (v23.11)
 * ============================================================================ */

/** @brief Maximum bridge recursion depth (prevents infinite loops) */
#define PCI_MAX_BRIDGE_DEPTH  8U

/** @brief PCI header type: standard device */
#define PCI_HEADER_TYPE_DEVICE  0x00U

/** @brief PCI header type: PCI-to-PCI bridge */
#define PCI_HEADER_TYPE_BRIDGE  0x01U

/**
 * @brief Recursively scan a single PCI bus
 *
 * Enumerates all devices on the given bus. When a PCI-to-PCI bridge
 * (header type 0x01) is found, reads its secondary bus number and
 * recurses into it. Depth-limited to PCI_MAX_BRIDGE_DEPTH.
 *
 * @param bus   Bus number to scan
 * @param depth Current recursion depth (0 = root bus)
 */
static void pci_scan_bus(uint8_t bus, uint8_t depth)
{
    if (depth > PCI_MAX_BRIDGE_DEPTH) {
        vos3_console_printf("[PCI] Bridge depth limit reached at bus %02x\n",
                            (unsigned)bus);
        return;
    }

    for (uint8_t dev = 0; dev < 32U; dev++) {
        for (uint8_t func = 0; func < 8U; func++) {
            uint32_t id_reg = pci_read32(bus, dev, func, 0x00U);
            uint16_t vendor = (uint16_t)(id_reg & 0xFFFFU);

            /* No device present */
            if (vendor == 0xFFFFU || vendor == 0x0000U) {
                if (func == 0) break;  /* No multi-function — skip remaining funcs */
                continue;
            }

            if (g_pci_count >= (int)VOS3_PCI_MAX_DEVICES) {
                vos3_console_puts("[PCI] Device table full\n");
                return;
            }

            uint16_t device_id = (uint16_t)(id_reg >> 16U);
            uint32_t class_reg = pci_read32(bus, dev, func, 0x08U);
            uint32_t hdr_reg   = pci_read32(bus, dev, func, 0x0CU);
            uint32_t irq_reg   = pci_read32(bus, dev, func, 0x3CU);

            vos3_pci_device_t *d = &g_pci_devices[g_pci_count];
            d->bus        = bus;
            d->dev        = dev;
            d->func       = func;
            d->vendor_id  = vendor;
            d->device_id  = device_id;
            d->class_code = (uint8_t)(class_reg >> 24U);
            d->subclass   = (uint8_t)((class_reg >> 16U) & 0xFFU);
            d->prog_if    = (uint8_t)((class_reg >> 8U) & 0xFFU);
            d->header_type = (uint8_t)((hdr_reg >> 16U) & 0xFFU);
            d->irq_line   = (uint8_t)(irq_reg & 0xFFU);
            d->_pad       = 0;

            /* Read BAR0-5 (only for type 0 device headers) */
            if ((d->header_type & 0x7FU) == PCI_HEADER_TYPE_DEVICE) {
                for (int b = 0; b < 6; b++) {
                    d->bar[b] = pci_read32(bus, dev, func,
                                           (uint8_t)(0x10U + b * 4U));
                }
            } else {
                for (int b = 0; b < 6; b++) d->bar[b] = 0;
            }

            /* Log discovery */
            vos3_console_printf("[PCI] %02x:%02x.%x %04x:%04x %s\n",
                (unsigned)bus, (unsigned)dev, (unsigned)func,
                (unsigned)vendor, (unsigned)device_id,
                vos3_pci_class_name(d->class_code, d->subclass));

            g_pci_count++;

            /* A1: PCI-to-PCI bridge — recurse into secondary bus.
             * Offset 0x18: primary(7:0), secondary(15:8), subordinate(23:16).
             * Read secondary bus number at bits [15:8] of register 0x18. */
            if ((d->header_type & 0x7FU) == PCI_HEADER_TYPE_BRIDGE) {
                uint32_t bus_reg = pci_read32(bus, dev, func, 0x18U);
                uint8_t secondary_bus = (uint8_t)((bus_reg >> 8U) & 0xFFU);
                if (secondary_bus != 0 && secondary_bus != bus) {
                    vos3_console_printf("[PCI] Bridge %02x:%02x.%x -> secondary bus %02x (depth %u)\n",
                        (unsigned)bus, (unsigned)dev, (unsigned)func,
                        (unsigned)secondary_bus, (unsigned)(depth + 1));
                    pci_scan_bus(secondary_bus, depth + 1);
                }
            }

            /* If not multi-function device, skip remaining functions */
            if (func == 0 && !(d->header_type & 0x80U)) {
                break;
            }
        }
    }
}

int vos3_pci_bus_scan(void)
{
    g_pci_count = 0;

    /* Lazy ECAM init — first call probes the MCFG ACPI table and
     * maps the MMIO range non-cacheably. Idempotent on subsequent
     * calls. If MCFG is absent (legacy chipset / pre-2010 board) or
     * the VMM map fails, the dispatcher transparently falls back to
     * Port-I/O via virtio_pci_read32. */
    (void)vos3_pci_ecam_init();

    vos3_console_puts("[PCI] Scanning buses (recursive bridge enumeration)...\n");

    /* A2: Start at bus 0 and recurse through bridges — no early termination.
     * The old flat bus 0-255 loop with early-termination break is removed. */
    pci_scan_bus(0, 0);

    vos3_console_printf("[PCI] Scan complete: %d devices found\n", g_pci_count);
    return g_pci_count;
}

/* ============================================================================
 * PUBLIC ACCESSORS
 * ============================================================================ */

const vos3_pci_device_t *vos3_pci_get_devices(void)
{
    return g_pci_devices;
}

int vos3_pci_get_count(void)
{
    return g_pci_count;
}

const vos3_pci_device_t *vos3_pci_find_device(uint16_t vendor_id, uint16_t device_id)
{
    for (int i = 0; i < g_pci_count; i++) {
        if (g_pci_devices[i].vendor_id == vendor_id &&
            g_pci_devices[i].device_id == device_id) {
            return &g_pci_devices[i];
        }
    }
    return NULL;
}
