/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 IOAPIC — I/O Advanced Programmable Interrupt Controller
 *
 * The IOAPIC routes platform-level interrupt sources (GSI = Global
 * System Interrupt) to LAPIC vectors via a redirection table. This
 * driver replaces the legacy 8259 PIC for any IRQ source described
 * in the ACPI MADT.
 *
 * Implementation: kernel/src/arch/x86_64/ioapic.c.
 *
 * All write paths are GATED behind VOS3_HW_IOAPIC. Default builds
 * compile the bodies to no-ops returning -ENODEV.
 */

#ifndef VOS3_INCLUDE_VOS_IOAPIC_H
#define VOS3_INCLUDE_VOS_IOAPIC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* IOAPIC indirect register addresses (Intel ICH SDG). */
#define VOS3_IOAPIC_REG_ID          0x00u
#define VOS3_IOAPIC_REG_VER         0x01u
#define VOS3_IOAPIC_REG_ARB         0x02u
#define VOS3_IOAPIC_REG_REDTBL_BASE 0x10u  /* +2*N for entry N */

/* Redirection-table entry low-dword fields. */
#define VOS3_IOAPIC_REDTBL_VECTOR_MASK    0x000000FFu
#define VOS3_IOAPIC_REDTBL_DELIV_FIXED    (0u << 8)
#define VOS3_IOAPIC_REDTBL_DELIV_LOWPRI   (1u << 8)
#define VOS3_IOAPIC_REDTBL_DELIV_NMI      (4u << 8)
#define VOS3_IOAPIC_REDTBL_DEST_PHYSICAL  (0u << 11)
#define VOS3_IOAPIC_REDTBL_DEST_LOGICAL   (1u << 11)
#define VOS3_IOAPIC_REDTBL_POLARITY_HIGH  (0u << 13)
#define VOS3_IOAPIC_REDTBL_POLARITY_LOW   (1u << 13)
#define VOS3_IOAPIC_REDTBL_TRIGGER_EDGE   (0u << 15)
#define VOS3_IOAPIC_REDTBL_TRIGGER_LEVEL  (1u << 15)
#define VOS3_IOAPIC_REDTBL_MASKED         (1u << 16)

/* Initialize the FIRST IOAPIC enumerated by ACPI MADT.
 *
 * Steps:
 *   1. Read g_acpi_info.ioapics[0].ioapic_addr.
 *   2. vos3_vmm_map_pages() the 4 KiB MMIO region (strong-uncacheable).
 *   3. Read REG_VER → max redirection-entry count.
 *   4. Mask every redirection entry (safe default until drivers program
 *      specific IRQs via vos3_ioapic_program()).
 *
 * Returns 0 on success, -ENODEV if no IOAPIC enumerated, -EINVAL if
 * the MMIO mapping fails. No-op (returns -ENODEV) when VOS3_HW_IOAPIC
 * is not defined.
 */
int vos3_ioapic_init(void);

/* Program a single redirection-table entry.
 *
 *   irq         — entry index (0..ioapic_max_redir-1)
 *   vector      — destination IDT vector (32..255 typically)
 *   target_cpu  — destination LAPIC ID (physical mode)
 *
 * Always uses: fixed delivery, physical destination, active-high,
 * edge-triggered. Drivers needing other modes can call the lower-
 * level vos3_ioapic_write_redtbl_raw() instead.
 *
 * Returns 0 on success, -ENODEV if IOAPIC not initialized, -EINVAL on
 * out-of-range irq.
 */
int vos3_ioapic_program(uint8_t irq, uint8_t vector, uint8_t target_cpu);

/* Mask (or unmask) a redirection entry without touching the rest of
 * its programming. */
int vos3_ioapic_set_mask(uint8_t irq, int masked);

/* Total number of redirection entries supported by the active IOAPIC.
 * Returns 0 if not initialized. */
uint8_t vos3_ioapic_max_redir(void);

/* GSI base of the active IOAPIC (from MADT). 0 for the first IOAPIC
 * on a typical PC. */
uint32_t vos3_ioapic_gsi_base(void);

/* Self-test — verifies the indirect-register access works by reading
 * REG_VER twice and asserting the value matches. Returns 0 on PASS. */
int vos3_ioapic_self_test(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_INCLUDE_VOS_IOAPIC_H */
