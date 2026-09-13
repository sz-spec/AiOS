/**
 * @file kpti.c
 * @brief Process-owned KPTI roots and CPU-local transition bindings.
 * Current isolation is partial: kernel-image and direct-map entries remain.
 */

#include "arch/x86_64/kpti.h"
#include "arch/x86_64/kpti_roots.h"
#include "arch/x86_64/mitigation_mode.h"
#include "arch/x86_64/cpu.h"
#include "arch/x86_64/memory_map.h"
#include "vos/vmm.h"
#include "vos/pmm.h"
#include "vos/console.h"
#include "vos/scheduler.h"
#include "vos/atomic.h"
#include "vos/entry_state.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * State (read-only after init)
 * ============================================================================ */

static vos3_kpti_status_t g_status = VOS3_KPTI_STATUS_UNINITIALIZED;

void vos3_kpti_prepare_user_return(void)
{
    vos3_task_t* task = vos3_sched_current();
    vos3_address_space_t* as = task != NULL ? task->address_space : NULL;
    if (g_status != VOS3_KPTI_STATUS_READY || as == NULL ||
        as == vos3_vmm_get_kernel_space() || as->user_pml4 == NULL ||
        as->pml4 == NULL || as->user_pml4 == as->pml4 ||
        as->user_pml4_phys == 0 || as->user_pml4_phys == as->pml4_phys) {
        VOS3_PANIC("User return without a complete process root pair");
    }
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);
    /* Preserve the existing, explicitly partial isolation boundary. Dedicated
     * entry/descriptor/IST mappings must replace these two broad entries
     * before full Meltdown isolation can be claimed. Dynamic task stacks
     * are NOT mapped; entry/exit use the CPU-local trampoline instead. */
    if (vos3_kpti_sync_root(as->user_pml4, as->pml4) != 0) {
        VOS3_PANIC("Invalid process root storage");
    }
    vos3_spinlock_release(&as->lock);
    vos3_entry_bind_roots(as->pml4_phys, as->user_pml4_phys);
    vos3_irq_restore(flags);
}

/* ============================================================================
 * Helpers
 * ============================================================================ */

static int mode_wants_kpti(vos3_mitigation_mode_t m)
{
    return (m == VOS3_MIT_PROTECTED_FULL)
        || (m == VOS3_MIT_PROTECTED_PCID)
        || (m == VOS3_MIT_LEGACY_KAISER);
}

/* ============================================================================
 * Fail-closed panic path
 * ============================================================================ */

void vos3_kpti_panic_init_failed(const char* reason)
{
    vos3_console_puts("\n\n");
    vos3_console_puts("****************************************************************\n");
    vos3_console_puts("***  [KPTI_INIT_FAILED] kernel page-table isolation failed   ***\n");
    vos3_console_puts("***                                                          ***\n");
    if (reason != NULL) {
        vos3_console_puts("***  reason: ");
        vos3_console_puts(reason);
        vos3_console_puts("\n");
    }
    vos3_console_puts("***                                                          ***\n");
    vos3_console_puts("***  Halting — Meltdown mitigation could not be established. ***\n");
    vos3_console_puts("****************************************************************\n\n");

    g_status = VOS3_KPTI_STATUS_INIT_FAILED;
    for (;;) {
        __asm__ volatile ("cli; hlt");
    }
}

/* ============================================================================
 * Public init
 * ============================================================================ */

vos3_kpti_status_t vos3_kpti_init(void)
{
    vos3_mitigation_mode_t mode = vos3_get_mitigation_mode();
    if (!mode_wants_kpti(mode)) {
        vos3_kpti_panic_init_failed("mitigation factory did not select a supported mode");
    }
    vos3_address_space_t* kernel_space = vos3_vmm_get_kernel_space();
    if (kernel_space == NULL || kernel_space->pml4 == NULL || kernel_space->pml4_phys == 0) {
        vos3_kpti_panic_init_failed("kernel address space is not initialized");
    }
    /* Root storage is owned by each address space, not a shared boot root. */
    vos3_entry_bind_roots(kernel_space->pml4_phys, 0);
    g_status = VOS3_KPTI_STATUS_READY;
    vos3_console_puts("[KPTI] process-root transitions enabled; isolation=partial; "
                      "retained=PML4[256,511]; CR3 reload uses PCID 0 with flush\n");
    return g_status;
}

/* ============================================================================
 * Accessors
 * ============================================================================ */

uint64_t vos3_kpti_get_user_cr3(void)
{
    if (g_status != VOS3_KPTI_STATUS_READY) {
        return 0U;
    }
    return vos3_entry_get_user_cr3();
}

uint64_t vos3_kpti_get_kernel_cr3(void)
{
    if (g_status != VOS3_KPTI_STATUS_READY) {
        return 0U;
    }
    return vos3_entry_get_kernel_cr3();
}

vos3_kpti_status_t vos3_kpti_get_status(void)
{
    return g_status;
}
