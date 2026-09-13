/**
 * @file pci_ecam.c
 * @brief PCIe Enhanced Configuration Access Mechanism — implementation
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P3
 *
 * See pci_ecam.h for the full design contract.
 */

#include "arch/x86_64/pci_ecam.h"
#include "vos/acpi.h"
#include "vos/vmm.h"
#include "vos/console.h"

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* ============================================================================
 * MCFG layout (ACPI spec §5.2.6, PCI Firmware Specification)
 * ============================================================================ */

#define MCFG_HEADER_SIZE   44U   /* SDT header + 8 bytes reserved */
#define MCFG_ALLOC_SIZE    16U   /* per-allocation entry size */

/* Per-allocation entry layout (offset within MCFG, after header):
 *   +0   u64 base_addr        (ECAM MMIO physical base)
 *   +8   u16 segment_group    (we use only segment 0)
 *   +10  u8  start_bus
 *   +11  u8  end_bus
 *   +12  u32 reserved
 */

/* ============================================================================
 * State (read-only after init)
 * ============================================================================ */

static vos3_pci_ecam_status_t g_status = VOS3_PCI_ECAM_UNINITIALIZED;
static uint64_t g_base_paddr = 0;
static uint64_t g_base_vaddr = 0;   /* uintptr_t cast back from `void*` */
static uint8_t  g_start_bus = 0;
static uint8_t  g_end_bus = 0;

/* ============================================================================
 * Helpers
 * ============================================================================ */

static uint64_t read_u64_le(const uint8_t* p)
{
    return ((uint64_t)p[0])
        | ((uint64_t)p[1] << 8)
        | ((uint64_t)p[2] << 16)
        | ((uint64_t)p[3] << 24)
        | ((uint64_t)p[4] << 32)
        | ((uint64_t)p[5] << 40)
        | ((uint64_t)p[6] << 48)
        | ((uint64_t)p[7] << 56);
}

static uint32_t read_u32_le(const uint8_t* p)
{
    return ((uint32_t)p[0])
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
}

static int sig_equals(const char* a, const char* b)
{
    return a[0] == b[0] && a[1] == b[1] && a[2] == b[2] && a[3] == b[3];
}

/* Scan the ACPI info table list for "MCFG". Returns the table's
 * physical address, or 0 if not found. */
static uint64_t find_mcfg_paddr(void)
{
    const vos3_acpi_info_t* info = vos3_acpi_get_info();
    if (info == NULL) {
        return 0;
    }
    for (uint32_t i = 0; i < info->table_count; i++) {
        if (sig_equals(info->table_sigs[i], "MCFG")) {
            return info->table_phys[i];
        }
    }
    return 0;
}

/* ============================================================================
 * Public init
 * ============================================================================ */

vos3_pci_ecam_status_t vos3_pci_ecam_init(void)
{
    /* Idempotent for TERMINAL states only. AVAILABLE means we've
     * already mapped the range — return the latched success. MAP_FAILED
     * means VMM rejected the mapping — retrying won't help (would
     * burn PMM pages on each retry). NO_MCFG is transient: the
     * first call from boot_drivers_init's pci_bus_scan runs BEFORE
     * ACPI parse, so MCFG is correctly "absent" then; a follow-up
     * call from kmain after ACPI completes retries successfully. */
    if (g_status == VOS3_PCI_ECAM_AVAILABLE ||
        g_status == VOS3_PCI_ECAM_MAP_FAILED) {
        return g_status;
    }

    uint64_t mcfg_paddr = find_mcfg_paddr();
    if (mcfg_paddr == 0) {
        vos3_console_puts("[PCI-ECAM] MCFG absent — Port-I/O fallback active\n");
        g_status = VOS3_PCI_ECAM_NO_MCFG;
        return g_status;
    }

    /* Read MCFG via the kernel direct-phys-map. MCFG itself is a small
     * ACPI table (< 4 KiB on typical hosts) — direct-map access is
     * fine. The first allocation entry starts at offset MCFG_HEADER_SIZE.
     *
     * Direct-map: VA = PA + VOS3_PHYS_MAP_OFFSET (handled by the kernel
     * higher-half PML4[256]). */
    const uint8_t* mcfg = (const uint8_t*)vos3_phys_to_virt(mcfg_paddr);

    /* MCFG length is at offset 4 of the SDT header (standard ACPI). */
    uint32_t mcfg_length = read_u32_le(mcfg + 4);
    if (mcfg_length < MCFG_HEADER_SIZE + MCFG_ALLOC_SIZE) {
        vos3_console_puts("[PCI-ECAM] MCFG too short — Port-I/O fallback\n");
        g_status = VOS3_PCI_ECAM_NO_MCFG;
        return g_status;
    }

    /* Take the first allocation only. Multi-segment hosts (large
     * servers) have more, but vOS targets q35-class topology where
     * one allocation covers segment 0, bus range [start, end]. */
    const uint8_t* alloc = mcfg + MCFG_HEADER_SIZE;
    uint64_t base_paddr = read_u64_le(alloc + 0);
    /* segment is alloc+8..+9 — we ignore (must be 0 for our scope) */
    uint8_t  start_bus = alloc[10];
    uint8_t  end_bus = alloc[11];

    if (end_bus < start_bus || base_paddr == 0) {
        vos3_console_puts("[PCI-ECAM] MCFG malformed — Port-I/O fallback\n");
        g_status = VOS3_PCI_ECAM_NO_MCFG;
        return g_status;
    }

    /* ECAM range size: 1 MiB per bus (= 32 dev × 8 func × 4 KiB). */
    size_t bus_count = (size_t)(end_bus - start_bus + 1U);
    size_t range_size = bus_count * 0x100000U;

    /* Map with VOS3_PTE_MMIO — PCD=1 (cache-disabled), NX=1.
     * vos3_vmm_map_pages returns a kernel-virtual pointer or NULL. */
    void* mapped = vos3_vmm_map_pages(base_paddr, range_size, VOS3_PTE_MMIO);
    if (mapped == NULL) {
        vos3_console_puts(
            "[PCI-ECAM] vmm_map_pages failed — Port-I/O fallback\n"
        );
        g_status = VOS3_PCI_ECAM_MAP_FAILED;
        return g_status;
    }

    g_base_paddr = base_paddr;
    g_base_vaddr = (uint64_t)(uintptr_t)mapped;
    g_start_bus = start_bus;
    g_end_bus = end_bus;
    g_status = VOS3_PCI_ECAM_AVAILABLE;

    vos3_console_puts("[PCI-ECAM] available — buses ");
    vos3_console_puthex((uint64_t)start_bus, 2);
    vos3_console_puts("..");
    vos3_console_puthex((uint64_t)end_bus, 2);
    vos3_console_puts(" mapped MMIO@0x");
    vos3_console_puthex(base_paddr, 16);
    vos3_console_puts(" (PCD=1, NX=1)\n");

    return g_status;
}

vos3_pci_ecam_status_t vos3_pci_ecam_get_status(void)
{
    return g_status;
}

bool vos3_pci_ecam_is_available(void)
{
    return g_status == VOS3_PCI_ECAM_AVAILABLE;
}

/* ============================================================================
 * Config-space read / write
 * ============================================================================ */

static uint64_t ecam_va_for(uint8_t bus, uint8_t dev, uint8_t func, uint16_t offset)
{
    /* Offset 4-byte alignment + < 4 KiB check is the caller's job
     * but we double-check defensively. Returns 0 on out-of-range
     * so the caller can return the "no device" sentinel. */
    if (g_status != VOS3_PCI_ECAM_AVAILABLE) return 0;
    if (bus < g_start_bus || bus > g_end_bus) return 0;
    if (dev >= 32U) return 0;
    if (func >= 8U) return 0;
    if (offset >= 4096U) return 0;
    if ((offset & 0x3U) != 0U) return 0;

    /* Per Intel SDM Vol 3A §11.11.4 / OSDev wiki PCI Express:
     *   PA = base + ((bus - start_bus) << 20) | (dev << 15)
     *      | (func << 12) | offset
     */
    uint64_t va = g_base_vaddr;
    va += ((uint64_t)(bus - g_start_bus)) << 20;
    va += ((uint64_t)dev) << 15;
    va += ((uint64_t)func) << 12;
    va += (uint64_t)offset;
    return va;
}

uint32_t vos3_pci_ecam_read32(uint8_t bus, uint8_t dev, uint8_t func,
                              uint16_t offset)
{
    uint64_t va = ecam_va_for(bus, dev, func, offset);
    if (va == 0) {
        return 0xFFFFFFFFU;
    }
    /* Volatile to defeat any compiler caching of the MMIO load.
     * The PTE has PCD=1 so the CPU itself won't cache; the volatile
     * is a defense-in-depth against the C compiler reordering. */
    volatile uint32_t* p = (volatile uint32_t*)(uintptr_t)va;
    return *p;
}

bool vos3_pci_ecam_write32(uint8_t bus, uint8_t dev, uint8_t func,
                           uint16_t offset, uint32_t value)
{
    uint64_t va = ecam_va_for(bus, dev, func, offset);
    if (va == 0) {
        return false;
    }
    volatile uint32_t* p = (volatile uint32_t*)(uintptr_t)va;
    *p = value;
    return true;
}

/* ============================================================================
 * Diagnostic accessor
 * ============================================================================ */

bool vos3_pci_ecam_get_diagnostic(uint64_t* base_paddr,
                                  uint64_t* base_vaddr,
                                  uint8_t*  start_bus,
                                  uint8_t*  end_bus)
{
    if (base_paddr) *base_paddr = g_base_paddr;
    if (base_vaddr) *base_vaddr = g_base_vaddr;
    if (start_bus)  *start_bus  = g_start_bus;
    if (end_bus)    *end_bus    = g_end_bus;
    return g_status == VOS3_PCI_ECAM_AVAILABLE;
}
