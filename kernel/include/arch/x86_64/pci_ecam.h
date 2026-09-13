/**
 * @file pci_ecam.h
 * @brief VOS3 PCIe Enhanced Configuration Access Mechanism (ECAM)
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P3
 *
 * Public API for ECAM-based PCI configuration-space access.
 * Used by kernel/src/drivers/pci.c as the PREFERRED transport when
 * an MCFG ACPI table is present; falls back to legacy Port-I/O
 * (`virtio_pci_read32`) when ECAM is unavailable.
 *
 * Physical-address computation (Intel SDM Vol 3A §11.11.4, OSDev wiki):
 *   PA = mcfg_base + (bus << 20) | (dev << 15) | (func << 12) | offset
 *
 * The MMIO range is mapped with VOS3_PTE_MMIO (PCD=1, NX=1) — every
 * configuration-space read/write hits the device fabric directly,
 * never the CPU cache. This is the "Non-Cacheable" memory attribute
 * the directive required for hardware-register synchronization.
 *
 * Honest-scope notes
 * ------------------
 * - ECAM covers a single segment (segment_group=0). Multi-segment
 *   hosts (very-large servers) would need multiple init calls; the
 *   current implementation supports the typical x86 server topology
 *   that QEMU q35 emulates.
 * - The mapping is established LAZILY on first read via init-once.
 *   Subsequent calls are branch-free fast-path.
 * - KPTI integration: this MMIO range is mapped into the kernel
 *   PML4 only (not the user PML4 — there's no need for user-mode
 *   to see PCI config space, and not mapping it preserves the
 *   Phase-1.2 Kernel-Silence strip invariant for higher-half
 *   PML4 indices outside [256, 511]).
 */

#ifndef VOS3_ARCH_X86_64_PCI_ECAM_H
#define VOS3_ARCH_X86_64_PCI_ECAM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stdbool.h>

typedef enum vos3_pci_ecam_status {
    VOS3_PCI_ECAM_UNINITIALIZED   = 0,
    VOS3_PCI_ECAM_AVAILABLE       = 1,  /**< MCFG found, range mapped, ready */
    VOS3_PCI_ECAM_NO_MCFG         = 2,  /**< MCFG absent — fall back to Port-I/O */
    VOS3_PCI_ECAM_MAP_FAILED      = 3   /**< MCFG found but VMM map_pages failed */
} vos3_pci_ecam_status_t;

/**
 * @brief Probe the ACPI table list for MCFG and, if present, map the
 *        ECAM MMIO range with non-cacheable attributes.
 *
 * Idempotent — repeated calls return the already-latched status
 * without re-mapping. Safe to call from multiple init paths.
 *
 * Preconditions:
 *   - ACPI parse must have completed (vos3_acpi_get_info() returns
 *     a populated table list).
 *   - VMM must be online (vos3_vmm_map_pages must be callable).
 *
 * Returns the latched status. Callers DO NOT need to check this
 * before calling vos3_pci_ecam_read32 — the read function itself
 * returns 0xFFFFFFFF (the "no device" sentinel) when ECAM isn't
 * available, which the PCI scanner already handles as "skip".
 */
vos3_pci_ecam_status_t vos3_pci_ecam_init(void);

/**
 * @brief Read latched status without invoking init.
 */
vos3_pci_ecam_status_t vos3_pci_ecam_get_status(void);

/**
 * @brief True iff ECAM is available and the dispatcher in pci.c
 *        should use it. Used by the dispatcher to choose transport.
 */
bool vos3_pci_ecam_is_available(void);

/**
 * @brief Read 32 bits from PCIe configuration space via ECAM.
 *
 * @param bus    PCI bus number (0-255; though MCFG may restrict to a
 *               sub-range, e.g., 0-31 on QEMU q35)
 * @param dev    PCI device number (0-31)
 * @param func   PCI function number (0-7)
 * @param offset Byte offset within the 4 KiB per-function config
 *               space. Must be 4-byte-aligned and < 4096.
 *
 * @return The 32-bit register value, or 0xFFFFFFFF if:
 *           - ECAM is not available (caller should fall back)
 *           - bus is outside the MCFG-allocated range
 *           - offset is out of bounds or unaligned
 *           - the device is absent (hardware returns all-1s)
 */
uint32_t vos3_pci_ecam_read32(uint8_t bus, uint8_t dev, uint8_t func,
                              uint16_t offset);

/**
 * @brief Write 32 bits to PCIe configuration space via ECAM.
 *
 * Same parameter rules as read32. Returns true on success, false
 * if the write was refused (ECAM unavailable, out-of-range, etc.).
 */
bool vos3_pci_ecam_write32(uint8_t bus, uint8_t dev, uint8_t func,
                           uint16_t offset, uint32_t value);

/**
 * @brief Get the diagnostic state for the boot log + integrity audit.
 *
 * @param[out] base_paddr  physical base of the ECAM range (0 if N/A)
 * @param[out] base_vaddr  virtual base after VMM mapping (NULL if N/A)
 * @param[out] start_bus   first bus covered by the MCFG allocation
 * @param[out] end_bus     last bus covered (inclusive)
 *
 * Returns true if ECAM is AVAILABLE; false otherwise. Safe to call
 * before init (the out-params are zeroed).
 */
bool vos3_pci_ecam_get_diagnostic(uint64_t* base_paddr,
                                  uint64_t* base_vaddr,
                                  uint8_t*  start_bus,
                                  uint8_t*  end_bus);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_PCI_ECAM_H */
