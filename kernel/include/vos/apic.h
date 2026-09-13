/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Local APIC — public API
 *
 * Implementation: kernel/src/arch/x86_64/apic.c.
 *
 * Detection (vos3_apic_detect, vos3_apic_get_base_phys,
 * vos3_apic_is_initialized) is always-on. The MMIO mapping + LVT
 * programming + ICR-write paths are gated behind VOS3_HW_LAPIC.
 */

#ifndef VOS3_INCLUDE_VOS_APIC_H
#define VOS3_INCLUDE_VOS_APIC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Detection — never writes a register, never maps memory. Idempotent.
 * Returns 0 on success (LAPIC present), -ENODEV otherwise. */
int      vos3_apic_detect(void);

/* Returns 1 iff detect succeeded AND the CPU advertises a LAPIC. */
int      vos3_apic_is_initialized(void);

/* Cached LAPIC physical base (from MSR_APIC_BASE). 0 if not detected. */
uint64_t vos3_apic_get_base_phys(void);

/* Returns non-zero if the CPU reported x2APIC mode active in
 * IA32_APIC_BASE.bit10 when vos3_apic_detect() ran.  When non-zero,
 * vos3_apic_send_resched_ipi() uses WRMSR to IA32_X2APIC_ICR (0x830)
 * instead of MMIO ICR_HIGH/ICR_LOW writes. */
int      vos3_apic_is_x2apic_mode(void);

/* Sets the kernel-virtual MMIO base. Called by vos3_apic_init_mmio()
 * (or by a custom boot path in tests). va == NULL disarms the
 * ICR-write path. */
void     vos3_apic_set_mmio_base(volatile uint32_t *va);

/* M2 (HW-2) — boot-side activation. Pulls phys from
 * g_acpi_info.lapic_addr (MADT-parsed) and maps the 4 KiB MMIO
 * region. Returns 0 on success, -ENODEV if VOS3_HW_LAPIC is off,
 * -EINVAL if the mapping fails. */
int      vos3_apic_init_mmio(void);

/* M2 — programs the Spurious Interrupt Vector register at +0xF0
 * (vector 0xFF | enable bit) and masks every LVT entry. MUST be
 * called after vos3_apic_init_mmio(). */
int      vos3_apic_init_lvt(void);

/* Reschedule IPI — fires the existing ICR-write body. Returns 0 on
 * success, -ENODEV if MMIO is not mapped, -EBUSY on delivery-status
 * timeout. */
int      vos3_apic_send_resched_ipi(uint32_t cpu);

/* Self-test — verifies the IPI write path completes without crashing.
 * Returns 0 on PASS, -1 on FAIL. */
int      vos3_apic_self_test(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_INCLUDE_VOS_APIC_H */
