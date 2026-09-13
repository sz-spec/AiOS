/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 IOAPIC driver — HW-3 (M3) of the v21.3 plan
 *
 * The IOAPIC uses indirect register access:
 *   IOREGSEL  at +0x00  (write the register index)
 *   IOWIN     at +0x10  (read/write the selected register's value)
 *
 * Redirection table starts at register 0x10, 2 dwords per entry. Entry
 * N occupies registers (0x10 + 2*N) for the low dword and (0x11 + 2*N)
 * for the high dword.
 *
 * GATED behind VOS3_HW_IOAPIC. Default builds compile every active
 * write to a no-op returning -ENODEV; the driver is link-graph-safe
 * but inert until the flag is enabled.
 *
 * Activation prerequisite: ACPI MADT parser must have populated
 * g_acpi_info.ioapics[0]. The acpi.c parser does this today
 * (kernel/src/drivers/acpi.c:298+).
 */

#include "../../../include/vos/ioapic.h"
#include "../../../include/vos/acpi.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/vmm.h"

#include <stdint.h>
#include <stddef.h>

#ifndef ENODEV
# define ENODEV  19
#endif
#ifndef EINVAL
# define EINVAL  22
#endif

/* ---- File-static state ---- */

static volatile uint32_t *g_ioapic_base_va    __attribute__((unused)) = ((void *)0);
static uint8_t            g_ioapic_max_redir  = 0u;
static uint32_t           g_ioapic_gsi_base   = 0u;
static uint8_t            g_ioapic_id         __attribute__((unused)) = 0u;

/* Standard 4 KiB MMIO size for an IOAPIC. */
#define VOS3_IOAPIC_MMIO_SIZE   0x1000u

/* IOREGSEL is at offset 0; IOWIN is at offset 0x10. As u32 array
 * indices, that is [0] and [4]. */
#define IOAPIC_IDX_IOREGSEL     0u
#define IOAPIC_IDX_IOWIN        4u

/* ---- Indirect register access ---- */

static inline uint32_t ioapic_read(uint8_t reg)
{
#ifdef VOS3_HW_IOAPIC
    g_ioapic_base_va[IOAPIC_IDX_IOREGSEL] = reg;
    return g_ioapic_base_va[IOAPIC_IDX_IOWIN];
#else
    (void)reg;
    return 0u;
#endif
}

static inline void ioapic_write(uint8_t reg, uint32_t value)
{
#ifdef VOS3_HW_IOAPIC
    g_ioapic_base_va[IOAPIC_IDX_IOREGSEL] = reg;
    g_ioapic_base_va[IOAPIC_IDX_IOWIN]    = value;
#else
    (void)reg; (void)value;
#endif
}

/* ---- Public API ---- */

int vos3_ioapic_init(void)
{
#ifndef VOS3_HW_IOAPIC
    VOS3_INFO("[IOAPIC] init skipped — VOS3_HW_IOAPIC not defined");
    return -ENODEV;
#else
    if (g_ioapic_base_va != ((void *)0)) {
        return 0;  /* already initialized */
    }

    const vos3_acpi_info_t *info = vos3_acpi_get_info();
    if (info == ((void *)0) || info->ioapic_count == 0u) {
        VOS3_WARN("[IOAPIC] ACPI MADT did not enumerate any IOAPIC");
        return -ENODEV;
    }

    /* Use the first IOAPIC. Multi-IOAPIC systems can be supported
     * later by extending g_ioapic_* into per-IOAPIC arrays. */
    const vos3_acpi_ioapic_t *first = &info->ioapics[0];

    void *va = vos3_vmm_map_pages((uint64_t)first->addr,
                                  VOS3_IOAPIC_MMIO_SIZE,
                                  VOS3_PTE_MMIO);
    if (va == ((void *)0)) {
        VOS3_ERROR("[IOAPIC] vmm_map_pages failed for phys=0x%x",
                   (unsigned)first->addr);
        return -EINVAL;
    }

    g_ioapic_base_va   = (volatile uint32_t *)va;
    g_ioapic_gsi_base  = first->gsi_base;
    g_ioapic_id        = first->id;

    /* REG_VER returns:
     *   bits [7:0]  = APIC version
     *   bits [23:16] = max redirection entry index (count - 1)
     */
    uint32_t ver = ioapic_read(VOS3_IOAPIC_REG_VER);
    g_ioapic_max_redir = (uint8_t)(((ver >> 16) & 0xFFu) + 1u);

    /* Mask every redirection entry. Drivers must explicitly program
     * the IRQs they care about via vos3_ioapic_program(). */
    for (uint8_t i = 0; i < g_ioapic_max_redir; i++) {
        ioapic_write(VOS3_IOAPIC_REG_REDTBL_BASE + (uint8_t)(i * 2u),
                     VOS3_IOAPIC_REDTBL_MASKED);
        ioapic_write(VOS3_IOAPIC_REG_REDTBL_BASE + (uint8_t)(i * 2u + 1u),
                     0u);  /* dest = APIC ID 0, will be set by program() */
    }

    VOS3_INFO("[IOAPIC] initialized: id=%u phys=0x%x va=%p "
              "redir_count=%u gsi_base=%u",
              (unsigned)g_ioapic_id,
              (unsigned)first->addr,
              va,
              (unsigned)g_ioapic_max_redir,
              (unsigned)g_ioapic_gsi_base);
    return 0;
#endif
}

int vos3_ioapic_program(uint8_t irq, uint8_t vector, uint8_t target_cpu)
{
#ifndef VOS3_HW_IOAPIC
    (void)irq; (void)vector; (void)target_cpu;
    return -ENODEV;
#else
    if (g_ioapic_base_va == ((void *)0)) return -ENODEV;
    if (irq >= g_ioapic_max_redir)       return -EINVAL;

    uint32_t low  = (uint32_t)vector
                  | VOS3_IOAPIC_REDTBL_DELIV_FIXED
                  | VOS3_IOAPIC_REDTBL_DEST_PHYSICAL
                  | VOS3_IOAPIC_REDTBL_POLARITY_HIGH
                  | VOS3_IOAPIC_REDTBL_TRIGGER_EDGE;
                  /* mask bit clear → unmasked */
    uint32_t high = ((uint32_t)target_cpu & 0xFFu) << 24;

    /* Write high first so an in-flight interrupt cannot see a half-
     * programmed entry routed to APIC ID 0 with the new vector. The
     * masked sentinel from init still applies until the low write
     * commits. */
    ioapic_write(VOS3_IOAPIC_REG_REDTBL_BASE + (uint8_t)(irq * 2u + 1u), high);
    ioapic_write(VOS3_IOAPIC_REG_REDTBL_BASE + (uint8_t)(irq * 2u),     low);
    return 0;
#endif
}

int vos3_ioapic_set_mask(uint8_t irq, int masked)
{
#ifndef VOS3_HW_IOAPIC
    (void)irq; (void)masked;
    return -ENODEV;
#else
    if (g_ioapic_base_va == ((void *)0)) return -ENODEV;
    if (irq >= g_ioapic_max_redir)       return -EINVAL;

    uint8_t off = (uint8_t)(VOS3_IOAPIC_REG_REDTBL_BASE + irq * 2u);
    uint32_t v  = ioapic_read(off);
    if (masked) v |= VOS3_IOAPIC_REDTBL_MASKED;
    else        v &= ~VOS3_IOAPIC_REDTBL_MASKED;
    ioapic_write(off, v);
    return 0;
#endif
}

uint8_t  vos3_ioapic_max_redir(void)  { return g_ioapic_max_redir; }
uint32_t vos3_ioapic_gsi_base(void)   { return g_ioapic_gsi_base; }

int vos3_ioapic_self_test(void)
{
#ifndef VOS3_HW_IOAPIC
    return 0;
#else
    if (g_ioapic_base_va == ((void *)0)) return -1;
    /* Read REG_VER twice via the indirect register interface. The
     * register has no side effects on read, so the two values must
     * match exactly. If they don't, IOREGSEL/IOWIN aren't wired
     * correctly (likely a wrong MMIO size or a non-strong-UC mapping
     * that lets the CPU reorder the two reads). */
    uint32_t v1 = ioapic_read(VOS3_IOAPIC_REG_VER);
    uint32_t v2 = ioapic_read(VOS3_IOAPIC_REG_VER);
    if (v1 != v2) {
        VOS3_ERROR("[IOAPIC] self_test FAIL: ver=0x%x/0x%x",
                   (unsigned)v1, (unsigned)v2);
        return -1;
    }
    VOS3_INFO("[IOAPIC] self_test PASS (ver=0x%x)", (unsigned)v1);
    return 0;
#endif
}
