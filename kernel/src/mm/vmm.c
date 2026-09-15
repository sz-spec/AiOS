/**
 * @file vmm.c
 * @brief VOS3 Virtual Memory Manager Implementation
 *
 * @details x86_64 4-level paging implementation.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/vmm.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/console.h"
#include "../../include/vos/boot_info.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/percpu.h"
#include "../../include/vos/entry_state.h"
#include "../../include/vos/vfs.h"
/* Note: AI PTE bit constants (VOS3_PTE_AI_MASK etc.) are now defined
 * directly in vmm.h, eliminating the vmm -> ai_guard layering violation.
 * ai_guard.h includes vmm.h and re-exports them. */
#include "../../include/arch/x86_64/smp.h"
#include "../../include/arch/x86_64/idt.h"

/* ============================================================================
 * EXTERNAL FUNCTIONS (from entry.S)
 * ============================================================================ */

extern uint64_t vos3_read_cr3(void);
extern void vos3_write_cr3(uint64_t value);

/* Forward declaration for IPI TLB flush handler (defined below, registered in vmm_init) */
static void vos3_vmm_tlb_flush_ipi_handler(vos3_int_frame_t* frame);

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Kernel address space */
static vos3_address_space_t g_kernel_space;

/** @brief Software address-space binding belongs to the executing CPU. */
static vos3_address_space_t* g_current_spaces[VOS3_MAX_CPUS];

/* create/clone allocate this descriptor from one physical page. */
_Static_assert(sizeof(vos3_address_space_t) <= VOS3_PAGE_SIZE,
               "address-space descriptor exceeds its allocation");

/** @brief VMM statistics */
static vos3_vmm_stats_t g_vmm_stats;

/** @brief VMM initialization flag */
static volatile uint32_t g_vmm_initialized = 0U;

/** @brief VMM lock for global operations */
static vos3_spinlock_t g_vmm_lock = VOS3_SPINLOCK_INIT;

/** @brief Dynamic 2MB large page address mask (CPUID MAXPHYADDR).
 *  Initialized to static fallback; updated by vos3_vmm_set_maxphyaddr(). */
static uint64_t g_pte_large_addr_mask = VOS3_PTE_LARGE_ADDR_MASK;

uint64_t vos3_vmm_large_addr_mask(void)
{
    return g_pte_large_addr_mask;
}

void vos3_vmm_set_maxphyaddr(uint8_t phys_addr_bits)
{
    /* Build mask: bits [phys_addr_bits-1 : 21] set, bits [20:0] zeroed.
     * Example: phys_addr_bits=40 → ((1<<40)-1) & ~((1<<21)-1) = 0x000000FFFFE00000
     * Example: phys_addr_bits=48 → ((1<<48)-1) & ~((1<<21)-1) = 0x0000FFFFFFE00000
     * Clamp to Intel architectural max of 52 bits. */
    if (phys_addr_bits < 21U) phys_addr_bits = 36U;  /* Sane minimum */
    if (phys_addr_bits > 52U) phys_addr_bits = 52U;  /* Intel max */
    uint64_t phys_max = (1ULL << phys_addr_bits) - 1ULL;
    uint64_t align_mask = ~((1ULL << 21U) - 1ULL);  /* Zero bits [20:0] */
    g_pte_large_addr_mask = phys_max & align_mask;
    vos3_console_printf("[VMM] MAXPHYADDR=%u → large_addr_mask=0x%llx\n",
                        (unsigned)phys_addr_bits,
                        (unsigned long long)g_pte_large_addr_mask);
}

/* Bit 52 — software-available PTE bit used as Cognitive Priority / Sticky Cache hint.
 * When set, the page is semantically important to an AI agent and should be
 * preserved by cold scrub (Identity Buffer) and prioritised by the L3 Color Guard.
 * Originally named "tombstone" — renamed to reflect its broader role in the
 * Cognitive Pager subsystem. */
#define VMM_PTE_COGNITIVE_BIT  ((uint64_t)(1ULL << 52))

int vos3_vmm_set_cognitive_priority(uintptr_t vaddr)
{
    vos3_pte_t pte;
    if (vos3_vmm_get_pte(vaddr, &pte) != 0) return -1;
    /* K-C5: Atomic CAS */
    vos3_pte_t desired = pte | VMM_PTE_COGNITIVE_BIT;
    while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
        desired = pte | VMM_PTE_COGNITIVE_BIT;
    }
    return 0;
}

int vos3_vmm_is_cognitive_priority(uintptr_t vaddr)
{
    vos3_pte_t pte;
    if (vos3_vmm_get_pte(vaddr, &pte) != 0) return 0;
    return (pte & VMM_PTE_COGNITIVE_BIT) ? 1 : 0;
}

/* Backward-compat aliases for cold scrub / recovery subsystem */
int vos3_vmm_set_tombstone(uintptr_t vaddr)
{
    return vos3_vmm_set_cognitive_priority(vaddr);
}

int vos3_vmm_is_tombstoned(uintptr_t vaddr)
{
    return vos3_vmm_is_cognitive_priority(vaddr);
}

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Zero a page
 * @param[in] virt Virtual address of page
 */
static void zero_page(void* virt)
{
    uint64_t* ptr = (uint64_t*)virt;
    for (size_t i = 0U; i < (VOS3_PAGE_SIZE / sizeof(uint64_t)); i++) {
        ptr[i] = 0ULL;
    }
}

/**
 * @brief Allocate a page table
 * @return Virtual address of new page table, or NULL on failure
 */
#ifdef VOS3_PROCESS_ROOTS_TEST
static int test_table_alloc_budget = -1;
void vos3_vmm_test_table_alloc_budget(int budget)
{
    test_table_alloc_budget = budget;
}
#endif

static vos3_pte_t* alloc_page_table(void)
{
#ifdef VOS3_PROCESS_ROOTS_TEST
    if (test_table_alloc_budget == 0) {
        return NULL;
    }
    if (test_table_alloc_budget > 0) {
        --test_table_alloc_budget;
    }
#endif
    uintptr_t phys = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys == 0U) {
        VOS3_DEBUG("VMM: alloc_page_table failed - PMM returned 0");
        return NULL;
    }

    vos3_pte_t* virt = (vos3_pte_t*)vos3_phys_to_virt(phys);
    VOS3_DEBUG("VMM: alloc_page_table: phys=0x%llx virt=0x%llx",
               (unsigned long long)phys, (unsigned long long)(uintptr_t)virt);
    vos3_atomic_fetch_add64(&g_vmm_stats.page_tables, 1ULL);

    return virt;
}

/**
 * @brief Free a page table
 * @param[in] pt Page table virtual address
 */
static void free_page_table(vos3_pte_t* pt)
{
    uintptr_t phys = vos3_virt_to_phys((const void*)pt);
    vos3_pmm_free(phys);
    vos3_atomic_fetch_sub64(&g_vmm_stats.page_tables, 1ULL);
}

/**
 * @brief Get or create page table entry
 * @param[in,out] parent Parent table
 * @param[in] index Index in parent
 * @param[in] create Create if not present
 * @param[in] is_user Set USER bit on intermediate tables (for user-space mappings)
 * @return Pointer to child table, or NULL
 */
static vos3_pte_t* get_or_create_table(vos3_pte_t* parent, size_t index, int create, int is_user)
{
    if (parent == NULL) {
        VOS3_DEBUG("VMM: get_or_create_table: parent is NULL");
        return NULL;
    }

    vos3_pte_t entry = parent[index];

    if (vos3_pte_is_present(entry)) {
        /* Entry exists */
        if (vos3_pte_is_large(entry)) {
            /* Large page - can't descend */
            VOS3_DEBUG("VMM: get_or_create_table: large page at index %zu", index);
            return NULL;
        }
        uintptr_t phys = vos3_pte_get_addr(entry);
        return (vos3_pte_t*)vos3_phys_to_virt(phys);
    }

    if (!create) {
        return NULL;
    }

    VOS3_DEBUG("VMM: get_or_create_table: creating at index %zu (user=%d)", index, is_user);

    /* Allocate new page table */
    vos3_pte_t* new_table = alloc_page_table();
    if (new_table == NULL) {
        VOS3_DEBUG("VMM: get_or_create_table: alloc_page_table failed");
        return NULL;
    }

    /* Install in parent with appropriate flags */
    uintptr_t phys = vos3_virt_to_phys((const void*)new_table);
    uint64_t flags = VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
    if (is_user) {
        flags |= VOS3_PTE_USER;
    }
    parent[index] = vos3_pte_create(phys, flags);

    return new_table;
}

/**
 * @brief Walk page tables to find PTE
 * @param[in] pml4 PML4 table
 * @param[in] virt Virtual address
 * @param[in] create Create tables if needed
 * @param[in] is_user Set USER bit on intermediate tables if creating
 * @param[out] level Level at which walk stopped (4=PT, 3=PD, 2=PDPT, 1=PML4)
 * @return Pointer to PTE, or NULL
 */
static vos3_pte_t* walk_page_tables(vos3_pte_t* pml4, uintptr_t virt,
                                     int create, int is_user, int* level)
{
    size_t pml4_idx = VOS3_PML4_INDEX(virt);
    size_t pdpt_idx = VOS3_PDPT_INDEX(virt);
    size_t pd_idx   = VOS3_PD_INDEX(virt);
    size_t pt_idx   = VOS3_PT_INDEX(virt);

    /* vOS·Adaptive·Phase=P3.1-polish — high-frequency PTE-establish
     * trace, gated by BENCH_MODE.
     *
     * Before this gate landed, mapping a 256 MiB MMIO range (e.g.
     * the ECAM window in pci_ecam.c) emitted ~65 K of these lines
     * and stretched the boot-to-driver-init window from <30 s to
     * ~120 s under QEMU TCG. The lines themselves are useful when
     * debugging page-fault chains but useless for every other run.
     * BENCH_MODE builds drop them; default builds keep them. */
#ifndef BENCH_MODE
    VOS3_DEBUG("VMM: walk_page_tables: virt=0x%llx pml4=%p indices=[%zu,%zu,%zu,%zu] user=%d",
               (unsigned long long)virt, (void*)pml4,
               pml4_idx, pdpt_idx, pd_idx, pt_idx, is_user);
#endif

    /* Level 1: PML4 -> PDPT */
    vos3_pte_t* pdpt = get_or_create_table(pml4, pml4_idx, create, is_user);
    if (pdpt == NULL) {
        VOS3_DEBUG("VMM: walk failed at PML4 (level 1)");
        if (level) *level = 1;
        return &pml4[pml4_idx];
    }

    /* Check for 1 GiB page */
    if (vos3_pte_is_present(pdpt[pdpt_idx]) && vos3_pte_is_large(pdpt[pdpt_idx])) {
        if (level) *level = 2;
        return &pdpt[pdpt_idx];
    }

    /* Level 2: PDPT -> PD */
    vos3_pte_t* pd = get_or_create_table(pdpt, pdpt_idx, create, is_user);
    if (pd == NULL) {
        VOS3_DEBUG("VMM: walk failed at PDPT (level 2)");
        if (level) *level = 2;
        return &pdpt[pdpt_idx];
    }

    /* Check for 2 MiB page */
    if (vos3_pte_is_present(pd[pd_idx]) && vos3_pte_is_large(pd[pd_idx])) {
        if (level) *level = 3;
        return &pd[pd_idx];
    }

    /* Level 3: PD -> PT */
    vos3_pte_t* pt = get_or_create_table(pd, pd_idx, create, is_user);
    if (pt == NULL) {
        VOS3_DEBUG("VMM: walk failed at PD (level 3)");
        if (level) *level = 3;
        return &pd[pd_idx];
    }

    /* Level 4: PT entry (4 KiB page) */
    if (level) *level = 4;
    return &pt[pt_idx];
}

/**
 * Walk page tables to Page Directory level (for 2MB large page mapping).
 * Creates intermediate PML4->PDPT tables as needed, but does NOT create PT.
 * @return Pointer to PDE (pd[pd_idx]) or NULL on failure.
 */
static vos3_pte_t* walk_to_pd(vos3_pte_t* pml4, uintptr_t virt,
                               int create, int is_user)
{
    size_t pml4_idx = VOS3_PML4_INDEX(virt);
    size_t pdpt_idx = VOS3_PDPT_INDEX(virt);
    size_t pd_idx   = VOS3_PD_INDEX(virt);

    /* Level 1: PML4 -> PDPT */
    vos3_pte_t* pdpt = get_or_create_table(pml4, pml4_idx, create, is_user);
    if (pdpt == NULL) return NULL;

    /* Reject if 1GB huge page already occupies this PDPT slot */
    if (vos3_pte_is_present(pdpt[pdpt_idx]) && vos3_pte_is_large(pdpt[pdpt_idx]))
        return NULL;

    /* Level 2: PDPT -> PD */
    vos3_pte_t* pd = get_or_create_table(pdpt, pdpt_idx, create, is_user);
    if (pd == NULL) return NULL;

    /* Return pointer to PDE — caller installs 2MB mapping here */
    return &pd[pd_idx];
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_vmm_init(const void* boot_info)
{
    if (g_vmm_initialized != 0U) {
        return VOS3_VMM_OK;
    }

    VOS3_INFO("VMM: Initializing Virtual Memory Manager");

    const vos3_boot_info_t* info = (const vos3_boot_info_t*)boot_info;
    (void)info;

    /* Get current PML4 from CR3 */
    uint64_t cr3 = vos3_read_cr3();
    g_kernel_space.pml4_phys = cr3 & VOS3_PTE_ADDR_MASK;
    g_kernel_space.pml4 = (vos3_pte_t*)vos3_phys_to_virt(g_kernel_space.pml4_phys);
    g_kernel_space.lock = VOS3_SPINLOCK_INIT;
    g_kernel_space.ref_count = 1U;
    g_kernel_space.flags = 0U;

    g_current_spaces[get_cpu_id()] = &g_kernel_space;
    vos3_entry_bind_roots(g_kernel_space.pml4_phys, 0);

    /* Initialize statistics */
    g_vmm_stats.pages_mapped = 0U;
    g_vmm_stats.large_pages = 0U;
    g_vmm_stats.huge_pages = 0U;
    g_vmm_stats.page_tables = 0U;
    g_vmm_stats.tlb_flushes = 0U;

    /* Count existing mappings (from bootloader) */
    /* For simplicity, we just note that the kernel is mapped */

    g_vmm_initialized = 1U;

    /* Register IPI handler for SMP TLB shootdown (vector 0xFB) */
    vos3_int_register(VOS3_IPI_TLB_FLUSH, vos3_vmm_tlb_flush_ipi_handler);

    /* E3 (v23.11): Tear down PML4[0] identity map.
     * The bootloader/entry.S sets up an identity map in PML4 entry 0
     * (virtual 0x0000000000000000 - 0x0000007FFFFFFFFF) for the transition
     * from physical to higher-half addressing.  After VMM init the kernel
     * runs exclusively in the higher half.  Leaving PML4[0] live allows
     * stale boot data to be accessed via two virtual addresses and could
     * leak KASLR entropy.  Zero it and flush the TLB. */
    g_kernel_space.pml4[0] = 0ULL;
    __asm__ volatile("mov %%cr3, %%rax; mov %%rax, %%cr3"
                     ::: "rax", "memory");
    VOS3_INFO("VMM: PML4[0] identity map cleared (boot dual-address eliminated)");

    VOS3_INFO("VMM: PML4 at physical 0x%016llx, virtual 0x%016llx",
              (unsigned long long)g_kernel_space.pml4_phys,
              (unsigned long long)(uintptr_t)g_kernel_space.pml4);

    VOS3_INFO("VMM: Initialization complete (TLB flush IPI: vector 0x%02x)",
              VOS3_IPI_TLB_FLUSH);

    return VOS3_VMM_OK;
}

int vos3_vmm_map(uintptr_t virt, uintptr_t phys, vos3_vmm_flags_t flags)
{
    if (g_vmm_initialized == 0U)
        return VOS3_VMM_ERR_NOTINIT;

    int is_large = (flags & VOS3_VMM_FLAG_LARGE) ? 1 : 0;

    /* Alignment check: 2MB for large, 4KB for normal */
    uintptr_t align_mask = is_large ? (VOS3_LARGE_PAGE_SIZE - 1U) : (VOS3_PAGE_SIZE - 1U);
    if ((virt & align_mask) != 0U || (phys & align_mask) != 0U)
        return VOS3_VMM_ERR_ALIGN;

    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;

    /* v23.7: Higher-half enforcement — reject kernel-privileged mappings that
     * target userspace addresses.  Only VOS3_VMM_FLAG_USER callers (exec, mmap)
     * may create mappings below 0xFFFF800000000000.  Prevents kernel bugs from
     * building an "Escalation Bridge" into user address space (Defect #3). */
    if (is_user && !(flags & VOS3_VMM_FLAG_USER))
        return VOS3_VMM_ERR_ALIGN;  /* Repurpose alignment error — no new error code needed */

    /* A1: W^X — reject a simultaneous WRITE+EXEC mapping request, unifying this
     * VMM-flags entry with the explicit -EINVAL rejection in
     * vos3_vmm_mprotect_range(). vos3_vmm_flags_to_pte() also strips exec from
     * W+X as a defense-in-depth backstop, but rejecting here makes the API
     * contract consistent and surfaces the bug at the call site instead of
     * silently handing back a non-executable page. (The raw-flags loader path
     * vos3_vmm_map_user sanitizes-to-NX instead, so RWX ELF segments still
     * load.) */
    if ((flags & VOS3_VMM_FLAG_WRITE) && (flags & VOS3_VMM_FLAG_EXEC))
        return VOS3_VMM_ERR_INVALID;  /* W^X violation */

    vos3_address_space_t* as = is_user ? vos3_vmm_get_current_space() : &g_kernel_space;

    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    if (is_large) {
        /* ---- 2MB Large Page Path ---- */
        vos3_pte_t* pde = walk_to_pd(as->pml4, virt, 1, is_user);
        if (pde == NULL) {
            vos3_spinlock_release(&as->lock);
            vos3_irq_restore(irqflags);
            return VOS3_VMM_ERR_NOMEM;
        }

        /* Reject if PDE already occupied (existing PT or large page) */
        if (vos3_pte_is_present(*pde)) {
            /* If PDE points to a PT (not a large page), check if the PT
             * is entirely empty.  This happens when 4KB pages were unmapped
             * but the PT page was left behind.  Reclaim it so the 2MB
             * large page mapping can proceed. */
            if (!vos3_pte_is_large(*pde)) {
                uintptr_t pt_phys = vos3_pte_get_addr(*pde);
                vos3_pte_t* pt = (vos3_pte_t*)vos3_phys_to_virt(pt_phys);
                int empty = 1;
                for (uint32_t i = 0; i < 512U; i++) {
                    if (pt[i] != 0) { empty = 0; break; }
                }
                if (empty) {
                    /* PT is empty — free it and clear the PDE */
                    *pde = 0;
                    vos3_vmm_invlpg(virt);
                    free_page_table(pt);
                } else {
                    vos3_spinlock_release(&as->lock);
                    vos3_irq_restore(irqflags);
                    return VOS3_VMM_ERR_MAPPED;
                }
            } else {
                vos3_spinlock_release(&as->lock);
                vos3_irq_restore(irqflags);
                return VOS3_VMM_ERR_MAPPED;
            }
        }

        /* Build 2MB PDE: aligned phys | PS bit | flags (PAT-aware)
         * Dynamic mask from CPUID MAXPHYADDR zeroes reserved bits [20:0]
         * and caps physical address to detected address width. */
        uint64_t pte_flags = vos3_vmm_flags_to_pte_ex(flags, 1);
        uint64_t masked_phys = phys & vos3_vmm_large_addr_mask();
        *pde = masked_phys | pte_flags | VOS3_PTE_LARGE;

        vos3_atomic_fetch_add64(&g_vmm_stats.pages_mapped, 512ULL);

        vos3_vmm_invlpg(virt);

        /* Warm L3 cache for newly mapped HugePage (Claw Protocol) */
        __asm__ volatile ("prefetcht2 (%0)" :: "r"(virt) : "memory");

        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_OK;
    }

    /* ---- Existing 4KB Path (unchanged) ---- */
    int level;
    vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 1, is_user, &level);

    if (pte == NULL) {
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_NOMEM;
    }

    if (level != 4) {
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_MAPPED;
    }

    if (vos3_pte_is_present(*pte)) {
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_MAPPED;
    }

    uint64_t pte_flags = vos3_vmm_flags_to_pte_ex(flags, 0);
    *pte = vos3_pte_create(phys, pte_flags);

    vos3_atomic_fetch_add64(&g_vmm_stats.pages_mapped, 1ULL);

    vos3_vmm_invlpg(virt);
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);
    return VOS3_VMM_OK;
}

int vos3_vmm_map_range(uintptr_t virt_start, uintptr_t phys_start,
                       size_t size, vos3_vmm_flags_t flags)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    /* Align addresses down and size up */
    uintptr_t virt = vos3_page_align_down(virt_start);
    uintptr_t phys = vos3_page_align_down(phys_start);
    size_t aligned_size = vos3_page_align_up(virt_start + size) - virt;

    size_t pages = aligned_size / VOS3_PAGE_SIZE;
    int result = VOS3_VMM_OK;

    for (size_t i = 0U; i < pages; i++) {
        result = vos3_vmm_map(virt, phys, flags);
        if (result != VOS3_VMM_OK && result != VOS3_VMM_ERR_MAPPED) {
            return result;
        }
        virt += VOS3_PAGE_SIZE;
        phys += VOS3_PAGE_SIZE;
    }

    return VOS3_VMM_OK;
}

/**
 * Internal unmap helper.
 * @param free_phys  When non-zero, free the physical page after unmapping.
 *                   Callers like the ELF loader unmap a kernel mapping but keep
 *                   the physical page for a subsequent user-space mapping, so
 *                   they must pass free_phys=0.  sys_munmap / sys_brk pass 1.
 */
static int vmm_unmap_internal(uintptr_t virt, int free_phys)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    if ((virt & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return VOS3_VMM_ERR_ALIGN;
    }

    /* Determine if this is a user-space address */
    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;

    /* Select appropriate address space — use task's space, not vos3_vmm_get_current_space() */
    vos3_address_space_t* as;
    if (is_user) {
        vos3_task_t* caller = vos3_sched_current();
        as = (caller != NULL && caller->address_space != NULL)
            ? caller->address_space : vos3_vmm_get_current_space();
    } else {
        as = &g_kernel_space;
    }
    if (as == NULL) as = &g_kernel_space;

    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    int level;
    vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 0, is_user, &level);

    if (pte == NULL || !vos3_pte_is_present(*pte)) {
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    /* Extract physical address before clearing PTE */
    uintptr_t page_phys = vos3_pte_get_addr(*pte);

    /* Clear the entry */
    *pte = 0ULL;

    if (level == 4) {
        vos3_atomic_fetch_sub64(&g_vmm_stats.pages_mapped, 1ULL);
    } else if (level == 3) {
        vos3_atomic_fetch_sub64(&g_vmm_stats.large_pages, 1ULL);
    } else if (level == 2) {
        vos3_atomic_fetch_sub64(&g_vmm_stats.huge_pages, 1ULL);
    }

    /* Invalidate TLB before releasing lock (Phase 1.2.1) */
    vos3_vmm_invlpg(virt);

    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);

    /* Free the physical page if requested (COW-safe: pmm_free checks refcount) */
    if (free_phys && page_phys != 0) {
        vos3_pmm_free(page_phys);
    }

    return VOS3_VMM_OK;
}

int vos3_vmm_unmap(uintptr_t virt)
{
    /* Public API: unmap only, do NOT free the physical page.
     * Callers like ELF loader / vos3_vmm_unmap_pages need the physical page
     * to remain allocated for subsequent remapping. */
    return vmm_unmap_internal(virt, 0);
}

int vos3_vmm_unmap_large(uintptr_t virt)
{
    if ((virt & (VOS3_LARGE_PAGE_SIZE - 1U)) != 0U)
        return VOS3_VMM_ERR_ALIGN;

    /* This API targets kernel-owned AI huge pages. User huge-page lifetime
     * and remote TLB invalidation require their own explicit contract. */
    VOS3_ASSERT(virt >= VOS3_KERNEL_SPACE_START,
                "vos3_vmm_unmap_large: user-space vaddr on SMP is unsafe");
    vos3_address_space_t* as = &g_kernel_space;

    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    int level;
    vos3_pte_t* pde = walk_page_tables(as->pml4, virt, 0, 0, &level);

    if (pde == NULL || level != 3 || !vos3_pte_is_present(*pde) || !vos3_pte_is_large(*pde)) {
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    *pde = 0;
    vos3_atomic_fetch_sub64(&g_vmm_stats.pages_mapped, 512ULL);

    vos3_vmm_invlpg(virt);
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);
    return VOS3_VMM_OK;
}

size_t vos3_vmm_unmap_range(uintptr_t virt_start, size_t size)
{
    if (g_vmm_initialized == 0U) {
        return 0U;
    }

    uintptr_t virt = vos3_page_align_down(virt_start);
    size_t aligned_size = vos3_page_align_up(virt_start + size) - virt;
    size_t pages = aligned_size / VOS3_PAGE_SIZE;
    size_t unmapped = 0U;

    for (size_t i = 0U; i < pages; i++) {
        /* Free physical pages — called from sys_munmap / sys_brk shrink */
        if (vmm_unmap_internal(virt, 1) == VOS3_VMM_OK) {
            unmapped++;
        }
        virt += VOS3_PAGE_SIZE;
    }

    return unmapped;
}

int vos3_vmm_validate_user_unmap(uintptr_t start, size_t size)
{
    if ((start & 4095U) || !size || (size & 4095U) ||
        start > VOS3_USER_SPACE_END || size > VOS3_USER_SPACE_END - start + 1U) return -22;
    vos3_task_t* task = vos3_sched_current();
    if (!task || !task->address_space) return -22;
    vos3_address_space_t* as = task->address_space;
    vos3_irqflags_t irq = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);
    int result = 0;
    for (uintptr_t va = start; va < start + size;) {
        int level = 4;
        vos3_pte_t* pte = walk_page_tables(as->pml4, va, 0, 1, &level);
        if (pte && vos3_pte_is_present(*pte) && level != 4) { result = -95; break; }
        /* Skip an absent subtree rather than scanning every page in a hole. */
        uintptr_t span = (uintptr_t)1 << (12 + 9 * (4 - level));
        va = (va | (span - 1)) + 1;
    }
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irq);
    return result;
}

int vos3_vmm_virt_to_phys(uintptr_t virt, uintptr_t* phys)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    if (phys == NULL) {
        return VOS3_VMM_ERR_INVALID;
    }

    /* Determine if this is a user-space address */
    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;

    /* Use current address space for user addresses, kernel space for kernel addresses */
    vos3_address_space_t* as = is_user
        ? vos3_vmm_get_current_space() : &g_kernel_space;

    int level;
    vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 0, is_user, &level);

    if (pte == NULL || !vos3_pte_is_present(*pte)) {
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    uintptr_t page_phys;
    uintptr_t offset;

    if (level == 4) {
        /* 4 KiB page */
        page_phys = vos3_pte_get_addr(*pte);
        offset = virt & 0xFFFULL;
    } else if (level == 3) {
        /* 2 MiB page */
        page_phys = *pte & vos3_vmm_large_addr_mask();
        offset = virt & 0x1FFFFFULL;
    } else if (level == 2) {
        /* 1 GiB page */
        page_phys = *pte & VOS3_PTE_HUGE_ADDR_MASK;
        offset = virt & 0x3FFFFFFFULL;
    } else {
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    *phys = page_phys + offset;
    return VOS3_VMM_OK;
}

int vos3_vmm_is_mapped(uintptr_t virt)
{
    if (g_vmm_initialized == 0U) {
        return 0;
    }

    /* Determine if this is a user-space address */
    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;

    /* Select appropriate address space */
    vos3_address_space_t* as = is_user
        ? vos3_vmm_get_current_space() : &g_kernel_space;

    int level;
    vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 0, is_user, &level);

    return (pte != NULL && vos3_pte_is_present(*pte)) ? 1 : 0;
}

int vos3_vmm_get_pte(uintptr_t virt, vos3_pte_t* pte)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    if (pte == NULL) {
        return VOS3_VMM_ERR_INVALID;
    }

    /* Determine if this is a user-space address */
    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;

    /* Select appropriate address space */
    vos3_address_space_t* as = is_user
        ? vos3_vmm_get_current_space() : &g_kernel_space;

    int level;
    vos3_pte_t* entry = walk_page_tables(as->pml4, virt, 0, is_user, &level);

    if (entry == NULL) {
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    *pte = *entry;
    return VOS3_VMM_OK;
}

int vos3_vmm_set_pte(uintptr_t virt, vos3_pte_t pte)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;

    vos3_address_space_t* as = is_user
        ? vos3_vmm_get_current_space() : &g_kernel_space;

    int level;
    vos3_pte_t* entry = walk_page_tables(as->pml4, virt, 0, is_user, &level);

    if (entry == NULL) {
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    *entry = pte;
    return VOS3_VMM_OK;
}

int vos3_vmm_cas_pte(uintptr_t virt, vos3_pte_t *expected, vos3_pte_t desired)
{
    if (g_vmm_initialized == 0U)
        return VOS3_VMM_ERR_NOTINIT;
    if (expected == NULL)
        return VOS3_VMM_ERR_INVALID;

    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;
    vos3_address_space_t *as = is_user ? vos3_vmm_get_current_space()
                                                             : &g_kernel_space;
    int level;
    vos3_pte_t *entry = walk_page_tables(as->pml4, virt, 0, is_user, &level);
    if (entry == NULL)
        return VOS3_VMM_ERR_NOTMAPPED;

    /* Phase 4.9-Final: hardware-level CAS on the PTE entry.
     * On x86_64 this compiles to LOCK CMPXCHG8B/CMPXCHGQ. */
    if (__atomic_compare_exchange_n(entry, expected, desired,
                                    0 /* strong */,
                                    __ATOMIC_ACQ_REL,
                                    __ATOMIC_ACQUIRE)) {
        return VOS3_VMM_OK;  /* Swapped successfully */
    }
    return -1;  /* CAS failed — *expected now holds current value */
}

int vos3_vmm_update_flags(uintptr_t virt, vos3_vmm_flags_t flags)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    /* AG-A2 fix: AI Guard allocates at 0xFFFF888100000000, which is below
     * VOS3_KERNEL_BASE (0xFFFFFFFF80000000) but above the user-kernel boundary.
     * Use the canonical address split to avoid misclassifying kernel
     * direct-map addresses as user-space. */
    int is_user = (virt < 0xFFFF800000000000ULL) ? 1 : 0;

    /* Select appropriate address space */
    vos3_address_space_t* as = is_user
        ? vos3_vmm_get_current_space() : &g_kernel_space;

    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    int level;
    vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 0, is_user, &level);

    if (pte == NULL || !vos3_pte_is_present(*pte)) {
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_NOTMAPPED;
    }

    /* Update flags while preserving address and large page bit */
    uintptr_t addr = vos3_pte_get_addr(*pte);
    uint64_t pte_flags = vos3_vmm_flags_to_pte(flags);

    if (vos3_pte_is_large(*pte)) {
        pte_flags |= VOS3_PTE_LARGE;
    }

    /* AG-A1 fix: Preserve AI Guard PTE bits (9=MONITORED, 10=PROTECTED,
     * 11=GUARD). The old code replaced the entire PTE, destroying these
     * custom bits on any flag update (e.g., mprotect, reprotect tick). */
    uint64_t ai_bits = (*pte) & VOS3_PTE_AI_MASK;
    if (*pte & VOS3_PTE_COW) {
        pte_flags = (pte_flags & ~VOS3_PTE_WRITABLE) | VOS3_PTE_COW;
    }
    *pte = vos3_pte_create(addr, pte_flags) | ai_bits;

    vos3_vmm_invlpg(virt);
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);

    return VOS3_VMM_OK;
}

/* mprotect preflights every page and VMA split before changing permissions.
 * PROT_NONE retains the physical frame with USER cleared, preserving data on
 * restoration. Shared writable frames remain read-only until a COW fault. */
int vos3_vmm_mprotect_range(uintptr_t addr, size_t len, int prot)
{
    if ((addr & 4095U) || (prot & ~7) || (prot & 6) == 6 ||
        addr > VOS3_USER_SPACE_END || len > SIZE_MAX - 4095U) return -22;
    if (len == 0) return 0;
    size_t rounded = (len + 4095U) & ~(size_t)4095U;
    if (rounded > VOS3_USER_SPACE_END - addr + 1U) return -22;
    uintptr_t end = addr + rounded;
    vos3_task_t* task = vos3_sched_current();
    if (!task || !task->address_space) return -22;
    vos3_address_space_t* as = task->address_space;
    vos3_vma_t planned[VOS3_MAX_VMAS] = {0};
    int extra_fds[VOS3_MAX_VMAS];
    unsigned count = 0, fd_count = 0;
    int error = -12;
    vos3_irqflags_t irq = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    for (uintptr_t va = addr; va < end; va += VOS3_PAGE_SIZE) {
        int level;
        vos3_pte_t* pte = walk_page_tables(as->pml4, va, 0, 1, &level);
        int resident = pte && vos3_pte_is_present(*pte);
        if (!resident && !vos3_vmm_find_vma(as, va)) goto abort;
        /* Partial huge-page permission splitting is not implemented. */
        if (resident && level != 4) { error = -95; goto abort; }
    }
    for (unsigned i = 0; i < VOS3_MAX_VMAS; ++i) {
        vos3_vma_t old = as->vmas[i];
        if (!old.valid) continue;
        uintptr_t cuts[4]; unsigned n = 0;
        cuts[n++] = old.vm_start;
        if (addr > old.vm_start && addr < old.vm_end) cuts[n++] = addr;
        if (end > old.vm_start && end < old.vm_end) cuts[n++] = end;
        cuts[n++] = old.vm_end;
        for (unsigned j = 0; j + 1 < n; ++j) {
            if (count == VOS3_MAX_VMAS) goto abort;
            vos3_vma_t part = old;
            part.vm_start = cuts[j]; part.vm_end = cuts[j + 1];
            part.vm_offset += cuts[j] - old.vm_start;
            if (cuts[j] >= addr && cuts[j] < end) part.vm_prot = prot;
            if (j && old.vm_fd >= 0) {
                part.vm_fd = vos3_dup(task->fd_table, old.vm_fd);
                if (part.vm_fd < 0) goto abort;
                extra_fds[fd_count++] = part.vm_fd;
            }
            planned[count++] = part;
        }
    }
    for (unsigned i = 0; i < VOS3_MAX_VMAS; ++i) as->vmas[i] = planned[i];
    as->num_vmas = count;
    for (uintptr_t va = addr; va < end; va += VOS3_PAGE_SIZE) {
        int level;
        vos3_pte_t* pte = walk_page_tables(as->pml4, va, 0, 1, &level);
        if (!pte || !vos3_pte_is_present(*pte)) continue;
        uint64_t value = *pte & ~(VOS3_PTE_USER | VOS3_PTE_WRITABLE |
                                  VOS3_PTE_NO_EXECUTE | VOS3_PTE_COW);
        if (prot) value |= VOS3_PTE_USER;
        if (!(prot & 4)) value |= VOS3_PTE_NO_EXECUTE;
        if (prot & 2) {
            if (vos3_pmm_ref_get(vos3_pte_get_addr(value)) > 1U) value |= VOS3_PTE_COW;
            else value |= VOS3_PTE_WRITABLE;
        }
        *pte = value;
        vos3_vmm_invlpg(va);
    }
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irq);
    return 0;
abort:
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irq);
    for (unsigned i = 0; i < fd_count; ++i) vos3_close(extra_fds[i]);
    return error;
}

void vos3_vmm_invlpg(uintptr_t virt)
{
    __asm__ volatile ("invlpg (%0)" :: "r" (virt) : "memory");
    /* invlpg alone is sufficient to flush a single TLB entry.
     * The previous CR3 reload flushed the entire TLB unnecessarily. */
}

void vos3_vmm_flush_tlb(void)
{
    uint64_t cr3 = vos3_read_cr3();
    vos3_write_cr3(cr3);
    vos3_atomic_fetch_add64(&g_vmm_stats.tlb_flushes, 1ULL);
}

/* ============================================================================
 * Phase 4.2: IPI-Optimized SMP TLB Flush Range
 * ============================================================================ */

/** @brief Shared state for IPI TLB shootdown handler */
static volatile uintptr_t g_flush_base = 0;
static volatile size_t    g_flush_size = 0;
static volatile uint32_t  g_flush_ack_count = 0;

/** @brief Spinlock protecting flush_range publish-wait sequence */
static vos3_spinlock_t g_flush_lock = VOS3_SPINLOCK_INIT;

/**
 * @brief IPI handler for remote TLB flush (registered on vector 0xFB)
 */
static void vos3_vmm_tlb_flush_ipi_handler(vos3_int_frame_t* frame)
{
    (void)frame;

    uintptr_t base = g_flush_base;
    size_t    size = g_flush_size;

    for (uintptr_t a = base; a < base + size; a += VOS3_PAGE_SIZE_2M) {
        vos3_vmm_invlpg(a);
    }

    /* Acknowledge: caller spins on this counter */
    __atomic_fetch_add(&g_flush_ack_count, 1, __ATOMIC_RELEASE);

    /* Send EOI to LAPIC */
    vos3_lapic_eoi();
}

void vos3_vmm_flush_range(uintptr_t base, size_t size)
{
    uint32_t ncpus = vos3_smp_cpu_count();

    /* Flush local TLB first */
    for (uintptr_t a = base; a < base + size; a += VOS3_PAGE_SIZE_2M) {
        vos3_vmm_invlpg(a);
    }

    /* If single-CPU, no IPI needed */
    if (ncpus <= 1U) {
        vos3_atomic_fetch_add64(&g_vmm_stats.tlb_flushes, 1ULL);
        return;
    }

    uint32_t expected_acks = ncpus - 1U;

    /* Serialize concurrent flush_range callers — prevents globals corruption */
    vos3_spinlock_lock(&g_flush_lock);

    /* Set shared state for IPI handler */
    g_flush_base = base;
    g_flush_size = size;

    /* Reset acknowledge counter */
    __atomic_store_n(&g_flush_ack_count, 0, __ATOMIC_SEQ_CST);
    __asm__ volatile("mfence" ::: "memory");

    /* Short-Hand IPI Broadcast: "All Excluding Self" — single LAPIC write */
    vos3_lapic_send_ipi_shorthand(VOS3_IPI_TLB_FLUSH,
                                  VOS3_ICR_ALL_EXCL_SELF);

    /* IPI Acknowledge Barrier: spin with deterministic timeout.
     * __builtin_ia32_pause mitigates speculative side-channels during TLB shootdown.
     * Timeout after ~100K iterations (~5ms at 2GHz with ~100-cycle pause). */
    {
        uint32_t timeout = 100000U;
        while (__atomic_load_n(&g_flush_ack_count, __ATOMIC_ACQUIRE) < expected_acks) {
            __asm__ volatile("pause" ::: "memory");
            if (--timeout == 0) {
                VOS3_WARN("VMM: TLB flush IPI timeout (got %u/%u acks)",
                          __atomic_load_n(&g_flush_ack_count, __ATOMIC_RELAXED),
                          expected_acks);
                break;
            }
        }
    }

    vos3_spinlock_unlock(&g_flush_lock);

    vos3_atomic_fetch_add64(&g_vmm_stats.tlb_flushes, 1ULL);
}

int vos3_vmm_flush_range_checked(uintptr_t base, size_t size)
{
    uint32_t ncpus = vos3_smp_cpu_count();

    /* Flush local TLB first */
    for (uintptr_t a = base; a < base + size; a += VOS3_PAGE_SIZE_2M) {
        vos3_vmm_invlpg(a);
    }

    /* If single-CPU, no IPI needed — always succeeds */
    if (ncpus <= 1U) {
        vos3_atomic_fetch_add64(&g_vmm_stats.tlb_flushes, 1ULL);
        return 0;
    }

    uint32_t expected_acks = ncpus - 1U;

    vos3_spinlock_lock(&g_flush_lock);

    g_flush_base = base;
    g_flush_size = size;

    __atomic_store_n(&g_flush_ack_count, 0, __ATOMIC_SEQ_CST);
    __asm__ volatile("mfence" ::: "memory");

    vos3_lapic_send_ipi_shorthand(VOS3_IPI_TLB_FLUSH,
                                  VOS3_ICR_ALL_EXCL_SELF);

    {
        uint32_t timeout = 100000U;
        while (__atomic_load_n(&g_flush_ack_count, __ATOMIC_ACQUIRE) < expected_acks) {
            __asm__ volatile("pause" ::: "memory");
            if (--timeout == 0) {
                vos3_spinlock_unlock(&g_flush_lock);
                VOS3_WARN("VMM: TLB flush IPI timeout (checked, got %u/%u acks)",
                          __atomic_load_n(&g_flush_ack_count, __ATOMIC_RELAXED),
                          expected_acks);
                return -1;  /* Timeout — caller marks slot CORRUPT */
            }
        }
    }

    vos3_spinlock_unlock(&g_flush_lock);
    vos3_atomic_fetch_add64(&g_vmm_stats.tlb_flushes, 1ULL);
    return 0;
}

void vos3_vmm_get_stats(vos3_vmm_stats_t* stats)
{
    if (stats == NULL) {
        return;
    }

    stats->pages_mapped = vos3_atomic_load64(&g_vmm_stats.pages_mapped);
    stats->large_pages = vos3_atomic_load64(&g_vmm_stats.large_pages);
    stats->huge_pages = vos3_atomic_load64(&g_vmm_stats.huge_pages);
    stats->page_tables = vos3_atomic_load64(&g_vmm_stats.page_tables);
    stats->tlb_flushes = vos3_atomic_load64(&g_vmm_stats.tlb_flushes);
}

vos3_address_space_t* vos3_vmm_create_address_space(void)
{
    /* Allocate address space structure */
    uintptr_t phys = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys == 0U) {
        VOS3_ERROR("VMM: create_address_space - failed to alloc structure");
        return NULL;
    }

    vos3_address_space_t* as = (vos3_address_space_t*)vos3_phys_to_virt(phys);

    /* Allocate PML4 (alloc_page_table returns zeroed page) */
    as->pml4 = alloc_page_table();
    if (as->pml4 == NULL) {
        VOS3_ERROR("VMM: create_address_space - failed to alloc PML4");
        vos3_pmm_free(phys);
        return NULL;
    }

    as->pml4_phys = vos3_virt_to_phys((const void*)as->pml4);
    /* A process owns both top-level roots. Do not copy the boot-global user
     * root: that would couple different processes' mappings. Keep this root
     * empty until the KPTI transition code installs its permitted mappings. */
    as->user_pml4 = alloc_page_table();
    if (as->user_pml4 == NULL) {
        free_page_table(as->pml4);
        vos3_pmm_free(phys);
        return NULL;
    }
    as->user_pml4_phys = vos3_virt_to_phys((const void*)as->user_pml4);
    as->lock = VOS3_SPINLOCK_INIT;
    as->ref_count = 1U;
    as->flags = 0U;
    as->brk = 0ULL;
    as->brk_start = 0ULL;

    /* Explicitly clear user-space entries (0-255) - they should already be zero
     * from alloc_page_table, but be defensive */
    for (size_t i = 0U; i < 256U; i++) {
        as->pml4[i] = 0ULL;
    }

    /* Copy kernel mappings (upper half: entries 256-511) */
    for (size_t i = 256U; i < 512U; i++) {
        as->pml4[i] = g_kernel_space.pml4[i];
    }

    VOS3_DEBUG("VMM: create_address_space - new PML4 at phys=0x%llx virt=0x%llx",
               (unsigned long long)as->pml4_phys,
               (unsigned long long)(uintptr_t)as->pml4);

    return as;
}

/**
 * Recursively free user-space page tables (PML4 entries 0-255).
 * Kernel-space entries (256-511) are shared and must NOT be freed.
 * Leaf data pages are freed, then the page table structure itself.
 */
static void free_user_page_tables(vos3_pte_t *pml4)
{
    /* Only walk user-space half (entries 0-255) */
    for (int i = 0; i < 256; i++) {
        if (!vos3_pte_is_present(pml4[i])) continue;
        if (vos3_pte_is_large(pml4[i])) continue; /* 512 GiB huge — shouldn't happen */

        vos3_pte_t *pdpt = (vos3_pte_t *)vos3_phys_to_virt(vos3_pte_get_addr(pml4[i]));

        for (int j = 0; j < 512; j++) {
            if (!vos3_pte_is_present(pdpt[j])) continue;
            if (vos3_pte_is_large(pdpt[j])) {
                /* 1 GiB huge page — do not free (pool-managed resource) */
                pdpt[j] = 0;
                continue;
            }

            vos3_pte_t *pd = (vos3_pte_t *)vos3_phys_to_virt(vos3_pte_get_addr(pdpt[j]));

            for (int k = 0; k < 512; k++) {
                if (!vos3_pte_is_present(pd[k])) continue;
                if (vos3_pte_is_large(pd[k])) {
                    /* 2 MiB large page — DO NOT free the physical page.
                     * These are SHM HugePage mappings whose physical memory
                     * belongs to the hugepage pool (freed via vos3_pmm_free_huge
                     * when the SHM is destroyed).  Freeing here would corrupt
                     * the regular PMM bitmap and permanently leak pool entries.
                     * Just clear the PDE so the mapping is removed. */
                    pd[k] = 0;
                    continue;
                }

                vos3_pte_t *pt = (vos3_pte_t *)vos3_phys_to_virt(vos3_pte_get_addr(pd[k]));

                for (int l = 0; l < 512; l++) {
                    if (vos3_pte_is_present(pt[l])) {
                        /* 4 KiB leaf page — free user data page */
                        vos3_pmm_free(vos3_pte_get_addr(pt[l]));
                    }
                }
                free_page_table(pt);  /* Free PT */
            }
            free_page_table(pd);  /* Free PD */
        }
        free_page_table(pdpt);  /* Free PDPT */
    }
}

void vos3_vmm_destroy_address_space(vos3_address_space_t* as)
{
    if (as == NULL || as == &g_kernel_space) {
        return;
    }

    /* Close any dup'd file descriptors in VMA entries */
    {
        extern int vos3_close(int fd);
        for (uint32_t i = 0; i < VOS3_MAX_VMAS; i++) {
            if (as->vmas[i].valid && as->vmas[i].vm_fd >= 0) {
                vos3_close(as->vmas[i].vm_fd);
                as->vmas[i].vm_fd = -1;
            }
        }
    }

    /* Free all user-space page tables and data pages */
    free_user_page_tables(as->pml4);

    /* Free PML4 itself */
    free_page_table(as->pml4);

    /* The restricted root will share lower user tables with the full root.
     * Free only this top-level page, never walk/free shared tables twice. */
    if (as->user_pml4 != NULL) {
        free_page_table(as->user_pml4);
    }

    /* Free the address_space structure */
    uintptr_t phys = vos3_virt_to_phys((const void*)as);
    vos3_pmm_free(phys);
}

void vos3_vmm_switch_address_space(vos3_address_space_t* as)
{
    if (as == NULL) {
        return;
    }

    /* Keep the software binding and CR3 update indivisible with respect to
     * local scheduling; another CPU has its own independent slot. */
    vos3_irqflags_t irqflags = vos3_irq_save();
    g_current_spaces[get_cpu_id()] = as;
    vos3_entry_bind_roots(as->pml4_phys, as->user_pml4_phys);
    vos3_write_cr3(as->pml4_phys);
    vos3_irq_restore(irqflags);
}

vos3_address_space_t* vos3_vmm_get_kernel_space(void)
{
    return &g_kernel_space;
}

vos3_address_space_t* vos3_vmm_get_current_space(void)
{
    vos3_address_space_t* as = g_current_spaces[get_cpu_id()];
    return as != NULL ? as : &g_kernel_space;
}

/* ============================================================================
 * DEMAND PAGING — ADDRESS VALIDATION
 * ============================================================================ */

/**
 * @brief Check if a user-space address is valid for demand paging
 *
 * Returns true if addr falls within the task's heap range [brk_start, brk),
 * user stack range, or a valid VMA (mmap region).
 *
 * @param[in] addr  Faulting address
 * @return 1 if valid (should be lazily allocated), 0 otherwise
 */
vos3_vma_t* vos3_vmm_find_vma(vos3_address_space_t* as, uintptr_t addr)
{
    if (as == NULL) return NULL;
    for (uint32_t i = 0; i < VOS3_MAX_VMAS; i++) {
        if (as->vmas[i].valid &&
            addr >= as->vmas[i].vm_start && addr < as->vmas[i].vm_end) {
            return &as->vmas[i];
        }
    }
    return NULL;
}

int vos3_vmm_is_valid_user_addr(uintptr_t addr)
{
    extern vos3_task_t* vos3_sched_current(void);
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->address_space == NULL) {
        return 0;
    }

    vos3_address_space_t* as = task->address_space;

    /* Check heap range: [brk_start, brk) */
    if (as->brk_start != 0 && as->brk != 0) {
        if (addr >= as->brk_start && addr < as->brk) {
            return 1;
        }
    }

    /* Check user stack range */
    if (task->user_stack != NULL && task->user_stack_size > 0) {
        uintptr_t stack_top = (uintptr_t)task->user_stack + task->user_stack_size;
        uintptr_t stack_bottom = (uintptr_t)task->user_stack;
        if (addr >= stack_bottom && addr < stack_top) {
            return 1;
        }
    }

    /* Check VMA list (mmap regions)
     * Iterate all slots — after munmap, holes appear in the array */
    for (uint32_t i = 0; i < VOS3_MAX_VMAS; i++) {
        if (as->vmas[i].valid &&
            addr >= as->vmas[i].vm_start && addr < as->vmas[i].vm_end) {
            return 1;
        }
    }

    return 0;
}

/* ============================================================================
 * USER SPACE MAPPING FUNCTIONS
 * ============================================================================ */

/** @brief Temporary mapping region start (in kernel higher half) */
#define VOS3_TEMP_MAP_START     ((uintptr_t)0xFFFFFFFF90000000ULL)
#define VOS3_TEMP_MAP_SIZE      ((size_t)0x10000000ULL)  /* 256 MiB */

/** @brief Next temporary mapping address */
static uintptr_t g_temp_map_next = VOS3_TEMP_MAP_START;

int vos3_vmm_map_user(uint64_t vaddr, uint64_t paddr, uint64_t flags)
{
    if (g_vmm_initialized == 0U) {
        VOS3_DEBUG("VMM: map_user failed - not initialized");
        return VOS3_VMM_ERR_NOTINIT;
    }

    /* Validate user space address */
    if (vaddr >= VOS3_KERNEL_BASE) {
        VOS3_DEBUG("VMM: map_user failed - vaddr 0x%llx >= KERNEL_BASE",
                   (unsigned long long)vaddr);
        return VOS3_VMM_ERR_INVALID;
    }

    /* Check alignment */
    if ((vaddr & (VOS3_PAGE_SIZE - 1U)) != 0U ||
        (paddr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        VOS3_DEBUG("VMM: map_user failed - alignment vaddr=0x%llx paddr=0x%llx",
                   (unsigned long long)vaddr, (unsigned long long)paddr);
        return VOS3_VMM_ERR_ALIGN;
    }

    /* Prefer explicit task ownership, including during exec's construction
     * of a new process image. Fall back to this CPU's software binding. */
    vos3_task_t* caller = vos3_sched_current();
    vos3_address_space_t* as = (caller != NULL && caller->address_space != NULL)
        ? caller->address_space : vos3_vmm_get_current_space();
    if (as == NULL) {
        VOS3_DEBUG("VMM: map_user - no current space, using kernel space");
        as = &g_kernel_space;
    }

    VOS3_DEBUG("VMM: map_user vaddr=0x%llx paddr=0x%llx pml4=0x%llx flags=0x%llx",
               (unsigned long long)vaddr, (unsigned long long)paddr,
               (unsigned long long)(uintptr_t)as->pml4,
               (unsigned long long)flags);

    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    int level;
    /* User mapping - always set is_user=1 for intermediate tables */
    vos3_pte_t* pte = walk_page_tables(as->pml4, (uintptr_t)vaddr, 1, 1, &level);

    if (pte == NULL) {
        VOS3_DEBUG("VMM: map_user failed - walk_page_tables returned NULL (level=%d)", level);
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_NOMEM;
    }

    if (level != 4) {
        VOS3_DEBUG("VMM: map_user failed - level=%d (expected 4), huge page conflict", level);
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_MAPPED;
    }

    /* A1 W^X enforcement (raw-flags chokepoint): this API receives raw PTE
     * flags from arbitrary callers — notably the ELF loader's
     * elf_flags_to_pte(), which does NOT enforce W^X and would emit a
     * Writable+executable (RWX) PTE for a PF_R|PF_W|PF_X segment. Force NX on
     * any writable user page so no RWX page can ever be created here. We strip
     * (rather than reject) because an RWX PT_LOAD segment must still LOAD —
     * just non-executable — so well-formed binaries are unaffected while the
     * code-injection surface is closed. The translated VMM-flags path
     * (vos3_vmm_map) rejects W|X outright; this raw path sanitizes. */
    if (flags & VOS3_PTE_WRITABLE) {
        flags |= VOS3_PTE_NO_EXECUTE;
    }

    /* Allow remapping in user space */
    *pte = vos3_pte_create((uintptr_t)paddr, flags);

    VOS3_DEBUG("VMM: map_user success - pte=0x%llx", (unsigned long long)*pte);

    vos3_atomic_fetch_add64(&g_vmm_stats.pages_mapped, 1ULL);

    /* TLB invalidation MUST happen before releasing lock to prevent
     * stale TLB entries being visible to other tasks/CPUs (Phase 1.2.1) */
    vos3_vmm_invlpg((uintptr_t)vaddr);

    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);

    return VOS3_VMM_OK;
}

void* vos3_vmm_get_kernel_addr(uint64_t user_vaddr)
{
    if (g_vmm_initialized == 0U) {
        return NULL;
    }

    /* Translate user virtual address to physical */
    uintptr_t phys;
    int result = vos3_vmm_virt_to_phys((uintptr_t)user_vaddr, &phys);
    if (result != VOS3_VMM_OK) {
        return NULL;
    }

    /* Convert physical to kernel virtual */
    return (void*)vos3_phys_to_virt(phys);
}

/*
 * WARNING: Uses a linear bump allocator that wraps around.
 * Mappings are NOT persistent — old virtual addresses are silently reused.
 * For persistent mappings, use static BSS buffers or kernel heap.
 */
void* vos3_vmm_map_pages(uint64_t paddr, size_t size, uint64_t flags)
{
    if (g_vmm_initialized == 0U) {
        return NULL;
    }

    /* Round size up to page boundary */
    size_t pages = (size + VOS3_PAGE_SIZE - 1U) / VOS3_PAGE_SIZE;
    size_t total_size = pages * VOS3_PAGE_SIZE;

    vos3_spinlock_acquire(&g_vmm_lock);

    /* Check if we have space in temp region */
    if (g_temp_map_next + total_size > VOS3_TEMP_MAP_START + VOS3_TEMP_MAP_SIZE) {
        /* WARNING: Wrap-around invalidates ALL previous temp mappings. */
        g_temp_map_next = VOS3_TEMP_MAP_START;
    }

    uintptr_t vaddr = g_temp_map_next;
    g_temp_map_next += total_size;

    vos3_spinlock_release(&g_vmm_lock);

    /* Map each page */
    for (size_t i = 0U; i < pages; i++) {
        uintptr_t page_vaddr = vaddr + (i * VOS3_PAGE_SIZE);
        uintptr_t page_paddr = (uintptr_t)paddr + (i * VOS3_PAGE_SIZE);

        vos3_vmm_flags_t vmm_flags = VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_GLOBAL;
        if ((flags & VOS3_PTE_NO_EXECUTE) != 0U) {
            /* No execute flag is implicitly set unless we add EXEC */
        } else {
            vmm_flags |= VOS3_VMM_FLAG_EXEC;
        }

        int result = vos3_vmm_map(page_vaddr, page_paddr, vmm_flags);
        if (result != VOS3_VMM_OK && result != VOS3_VMM_ERR_MAPPED) {
            /* Unmap what we've mapped so far */
            for (size_t j = 0U; j < i; j++) {
                vos3_vmm_unmap(vaddr + (j * VOS3_PAGE_SIZE));
            }
            return NULL;
        }
    }

    return (void*)vaddr;
}

void vos3_vmm_unmap_pages(void* vaddr, size_t size)
{
    if (g_vmm_initialized == 0U || vaddr == NULL) {
        return;
    }

    uintptr_t addr = (uintptr_t)vaddr;

    /* Unmap each page */
    size_t pages = (size + VOS3_PAGE_SIZE - 1U) / VOS3_PAGE_SIZE;
    for (size_t i = 0U; i < pages; i++) {
        vos3_vmm_unmap(addr + (i * VOS3_PAGE_SIZE));
    }
}

/* ============================================================================
 * COPY-ON-WRITE SUPPORT (Phase 28)
 * ============================================================================ */

/**
 * @brief Copy a single page (used for COW faults)
 * @param[in] dst Destination virtual address
 * @param[in] src Source virtual address
 */
static void copy_page(void* dst, const void* src)
{
    uint64_t* d = (uint64_t*)dst;
    const uint64_t* s = (const uint64_t*)src;
    for (size_t i = 0U; i < (VOS3_PAGE_SIZE / sizeof(uint64_t)); i++) {
        d[i] = s[i];
    }
}

/**
 * @brief Clone a page table level with COW semantics
 * @param[in] src Source page table
 * @param[in] level Current level (4=PML4, 3=PDPT, 2=PD, 1=PT)
 * @param[in] clone_kernel Whether to clone kernel entries
 * @return New page table, or NULL on failure
 */
/**
 * @brief Free a partially-cloned page table tree (Phase v17 K-C3 fix).
 *
 * Walks entries [0..count) at the given level, recursing into intermediate
 * tables and decrementing refcounts on COW leaf pages that were already
 * cloned before the failure.  Finally frees @p pt itself.
 */
static void free_partial_clone(vos3_pte_t* pt, int level, size_t count)
{
    if (pt == NULL) return;

    for (size_t i = 0; i < count; i++) {
        vos3_pte_t entry = pt[i];
        if (!vos3_pte_is_present(entry)) continue;

        if (level == 1 || vos3_pte_is_large(entry)) {
            /* Leaf page (4 KiB or large) — undo the ref_inc we did */
            uintptr_t phys = vos3_pte_get_addr(entry);
            vos3_pmm_ref_dec(phys);
        } else {
            /* Intermediate table — recurse then free the child table */
            uintptr_t child_phys = vos3_pte_get_addr(entry);
            vos3_pte_t* child = (vos3_pte_t*)vos3_phys_to_virt(child_phys);
            free_partial_clone(child, level - 1, 512U);
        }
    }
    free_page_table(pt);
}

static vos3_pte_t* clone_pt_level_cow(vos3_pte_t* src, int level, int clone_kernel)
{
    if (src == NULL) {
        return NULL;
    }

    /* Allocate new page table */
    vos3_pte_t* dst = alloc_page_table();
    if (dst == NULL) {
        return NULL;
    }

    /* Determine entry range to clone */
    size_t start_idx = 0U;
    size_t end_idx = 512U;

    /* For PML4 (level 4), only clone user space (0-255) unless clone_kernel */
    if (level == 4 && !clone_kernel) {
        end_idx = 256U;
    }

    for (size_t i = start_idx; i < end_idx; i++) {
        vos3_pte_t entry = src[i];

        if (!vos3_pte_is_present(entry)) {
            dst[i] = 0ULL;
            continue;
        }

        if (level == 1) {
            /* Level 1 = PT entries (4 KiB pages) */
            uintptr_t phys = vos3_pte_get_addr(entry);

            /* Mark both parent and child as read-only with COW flag */
            uint64_t cow_flags = entry;
            if (entry & (VOS3_PTE_WRITABLE | VOS3_PTE_COW))
                cow_flags = (entry & ~VOS3_PTE_WRITABLE) | VOS3_PTE_COW;

            /* Update source entry (parent) to be read-only COW */
            src[i] = cow_flags;

            /* Set child entry to same (read-only COW) */
            dst[i] = cow_flags;

            /* Increment physical page reference count */
            vos3_pmm_ref_inc(phys);
        } else if (vos3_pte_is_large(entry)) {
            /* Large page (2 MiB at level 2, 1 GiB at level 3) — COW support */
            uintptr_t phys = vos3_pte_get_addr(entry);

            /* Mark both parent and child as read-only with COW flag */
            uint64_t cow_flags = entry;
            if (entry & (VOS3_PTE_WRITABLE | VOS3_PTE_COW))
                cow_flags = (entry & ~VOS3_PTE_WRITABLE) | VOS3_PTE_COW;
            src[i] = cow_flags;
            dst[i] = cow_flags;

            /* Increment physical page reference count */
            vos3_pmm_ref_inc(phys);
        } else {
            /* Intermediate table - recurse */
            uintptr_t child_phys = vos3_pte_get_addr(entry);
            vos3_pte_t* child_src = (vos3_pte_t*)vos3_phys_to_virt(child_phys);
            vos3_pte_t* child_dst = clone_pt_level_cow(child_src, level - 1, 0);

            if (child_dst == NULL) {
                /* Phase v17 (K-C3): Properly unwind all entries cloned so far.
                 * Decrement refcounts on COW leaves and free child tables. */
                free_partial_clone(dst, level, i);
                return NULL;
            }

            /* Create entry pointing to new child table */
            uint64_t flags = entry & ~VOS3_PTE_ADDR_MASK;
            dst[i] = vos3_pte_create(vos3_virt_to_phys((const void*)child_dst), flags);
        }
    }

    /* For PML4, copy kernel mappings (entries 256-511) directly */
    if (level == 4 && !clone_kernel) {
        for (size_t i = 256U; i < 512U; i++) {
            dst[i] = g_kernel_space.pml4[i];
        }
    }

    return dst;
}

vos3_address_space_t* vos3_vmm_clone_cow(vos3_address_space_t* src)
{
    if (g_vmm_initialized == 0U) {
        VOS3_ERROR("VMM: clone_cow - not initialized");
        return NULL;
    }

    if (src == NULL) {
        VOS3_ERROR("VMM: clone_cow - src is NULL");
        return NULL;
    }

    VOS3_DEBUG("VMM: clone_cow - cloning address space 0x%llx",
               (unsigned long long)(uintptr_t)src);

    /* Allocate address space structure */
    uintptr_t phys = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys == 0U) {
        VOS3_ERROR("VMM: clone_cow - failed to alloc structure");
        return NULL;
    }

    vos3_address_space_t* dst = (vos3_address_space_t*)vos3_phys_to_virt(phys);

    /* Allocate the child's own restricted root before altering parent PTEs
     * for COW. No restricted page-table root is shared between processes. */
    dst->user_pml4 = alloc_page_table();
    if (dst->user_pml4 == NULL) {
        vos3_pmm_free(phys);
        return NULL;
    }
    dst->user_pml4_phys = vos3_virt_to_phys((const void*)dst->user_pml4);

    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&src->lock);

    /* Clone page tables with COW semantics */
    dst->pml4 = clone_pt_level_cow(src->pml4, 4, 0);

    vos3_spinlock_release(&src->lock);
    vos3_irq_restore(irqflags);

    if (dst->pml4 == NULL) {
        VOS3_ERROR("VMM: clone_cow - failed to clone page tables");
        free_page_table(dst->user_pml4);
        vos3_pmm_free(phys);
        return NULL;
    }

    dst->pml4_phys = vos3_virt_to_phys((const void*)dst->pml4);
    dst->lock = VOS3_SPINLOCK_INIT;
    dst->ref_count = 1U;
    dst->flags = src->flags;
    dst->brk = src->brk;
    dst->brk_start = src->brk_start;

    /* Copy VMA descriptors (mmap regions) */
    for (uint32_t i = 0; i < VOS3_MAX_VMAS; i++) {
        dst->vmas[i] = src->vmas[i];
    }
    dst->num_vmas = src->num_vmas;

    /* Flush TLB to ensure parent sees read-only COW pages */
    vos3_vmm_flush_tlb();

    VOS3_DEBUG("VMM: clone_cow - new PML4 at phys=0x%llx virt=0x%llx",
               (unsigned long long)dst->pml4_phys,
               (unsigned long long)(uintptr_t)dst->pml4);

    return dst;
}

int vos3_vmm_handle_cow_fault(uintptr_t fault_addr, uint64_t error_code)
{
    if (g_vmm_initialized == 0U) {
        return -1;
    }

    /* Page fault error code bits:
     * Bit 0: Present (0 = not present, 1 = protection violation)
     * Bit 1: Write (0 = read, 1 = write)
     * Bit 2: User (0 = supervisor, 1 = user)
     */
    int is_present = (error_code & 0x1) != 0;
    int is_write = (error_code & 0x2) != 0;
    int is_user = (error_code & 0x4) != 0;

    /* COW fault: page must be present, write attempt, and have COW flag */
    if (!is_present || !is_write) {
        return -1;  /* Not a COW fault */
    }

    vos3_task_t* cow_task = vos3_sched_current();
    vos3_address_space_t* as = (cow_task != NULL && cow_task->address_space != NULL)
        ? cow_task->address_space : vos3_vmm_get_current_space();
    if (as == NULL) {
        as = &g_kernel_space;
    }

    /* Get the PTE */
    vos3_vma_t* fault_vma = vos3_vmm_find_vma(as, fault_addr);
    if (fault_vma != NULL && !(fault_vma->vm_prot & 2)) return -1;
    int level;
    vos3_pte_t* pte = walk_page_tables(as->pml4, fault_addr, 0, is_user, &level);

    if (pte == NULL) {
        return -1;  /* Not found */
    }

    vos3_pte_t entry = *pte;

    if (!vos3_pte_is_present(entry)) {
        return -1;  /* Page not present */
    }

    if (!vos3_pte_is_cow(entry)) {
        return -1;  /* Not a COW page */
    }

    uintptr_t old_phys = vos3_pte_get_addr(entry);
    uint32_t refcount = vos3_pmm_ref_get(old_phys);

    VOS3_DEBUG("VMM: COW fault at 0x%llx, level=%d, PTE=0x%llx, refcount=%u",
               (unsigned long long)fault_addr, level,
               (unsigned long long)entry, refcount);

    /* ---- Level 4: 4 KiB page COW ---- */
    if (level == 4) {
        if (refcount <= 1U) {
            /* Sole owner - just make writable, clear COW */
            *pte = (entry | VOS3_PTE_WRITABLE) & ~VOS3_PTE_COW;
            vos3_vmm_invlpg(fault_addr);
            VOS3_DEBUG("VMM: COW 4K - sole owner, made writable");
            return 0;
        }

        /* Multiple users - copy the page */
        uintptr_t new_phys = vos3_pmm_alloc(VOS3_PMM_FLAG_NONE);
        if (new_phys == 0U) {
            VOS3_ERROR("VMM: COW 4K - failed to allocate new page");
            return -1;
        }

        copy_page((void*)vos3_phys_to_virt(new_phys),
                  (void*)vos3_phys_to_virt(old_phys));

        uint64_t new_pte = (entry & ~VOS3_PTE_ADDR_MASK & ~VOS3_PTE_COW) | VOS3_PTE_WRITABLE;
        new_pte = (new_pte & ~VOS3_PTE_ADDR_MASK) | (new_phys & VOS3_PTE_ADDR_MASK);
        *pte = new_pte;

        vos3_pmm_ref_dec(old_phys);
        vos3_vmm_invlpg(fault_addr);

        VOS3_DEBUG("VMM: COW 4K - copied, new_phys=0x%llx",
                   (unsigned long long)new_phys);
        return 0;
    }

    /* ---- Level 3: 2 MiB huge page COW ---- */
    if (level == 3) {
        if (refcount <= 1U) {
            /* Sole owner - make writable, clear COW */
            *pte = (entry | VOS3_PTE_WRITABLE) & ~VOS3_PTE_COW;
            vos3_vmm_invlpg(fault_addr & ~((uintptr_t)VOS3_PAGE_SIZE_2M - 1));
            VOS3_DEBUG("VMM: COW 2M - sole owner, made writable");
            return 0;
        }

        /* Multiple users - full copy of 2 MiB huge page (no splitting) */
        /* Security: CVE-2017-1000405 — dirty bit only set after proper copy */
        uintptr_t new_phys = vos3_pmm_alloc_pages(512, 0);  /* 512 * 4K = 2 MiB contiguous */
        if (new_phys == 0U) {
            VOS3_WARN("VMM: COW 2M - failed to allocate 2 MiB contiguous block");
            return -1;
        }

        /* Copy entire 2 MiB */
        uint64_t* dst_v = (uint64_t*)vos3_phys_to_virt(new_phys);
        const uint64_t* src_v = (const uint64_t*)vos3_phys_to_virt(old_phys);
        for (size_t w = 0; w < (VOS3_PAGE_SIZE_2M / sizeof(uint64_t)); w++) {
            dst_v[w] = src_v[w];
        }

        /* Update PDE: new physical address, writable, large, no COW */
        uint64_t new_pte = (entry & ~VOS3_PTE_ADDR_MASK & ~VOS3_PTE_COW) | VOS3_PTE_WRITABLE;
        new_pte = (new_pte & ~VOS3_PTE_ADDR_MASK) | (new_phys & VOS3_PTE_ADDR_MASK);
        *pte = new_pte;

        vos3_pmm_ref_dec(old_phys);
        vos3_vmm_invlpg(fault_addr & ~((uintptr_t)VOS3_PAGE_SIZE_2M - 1));

        VOS3_DEBUG("VMM: COW 2M - full copy, new_phys=0x%llx",
                   (unsigned long long)new_phys);
        return 0;
    }

    /* ---- Level 2: 1 GiB huge page COW (extremely rare) ---- */
    if (level == 2) {
        VOS3_WARN("VMM: COW 1G page at 0x%llx — very large copy",
                  (unsigned long long)fault_addr);

        if (refcount <= 1U) {
            *pte = (entry | VOS3_PTE_WRITABLE) & ~VOS3_PTE_COW;
            vos3_vmm_invlpg(fault_addr & ~((uintptr_t)VOS3_PAGE_SIZE_1G - 1));
            VOS3_DEBUG("VMM: COW 1G - sole owner, made writable");
            return 0;
        }

        /* Full copy of 1 GiB — 262144 pages contiguous */
        uintptr_t new_phys = vos3_pmm_alloc_pages(262144, 0);
        if (new_phys == 0U) {
            VOS3_ERROR("VMM: COW 1G - failed to allocate 1 GiB contiguous block");
            return -1;
        }

        uint64_t* dst_v = (uint64_t*)vos3_phys_to_virt(new_phys);
        const uint64_t* src_v = (const uint64_t*)vos3_phys_to_virt(old_phys);
        for (size_t w = 0; w < (VOS3_PAGE_SIZE_1G / sizeof(uint64_t)); w++) {
            dst_v[w] = src_v[w];
        }

        uint64_t new_pte = (entry & ~VOS3_PTE_ADDR_MASK & ~VOS3_PTE_COW) | VOS3_PTE_WRITABLE;
        new_pte = (new_pte & ~VOS3_PTE_ADDR_MASK) | (new_phys & VOS3_PTE_ADDR_MASK);
        *pte = new_pte;

        vos3_pmm_ref_dec(old_phys);
        vos3_vmm_invlpg(fault_addr & ~((uintptr_t)VOS3_PAGE_SIZE_1G - 1));

        VOS3_DEBUG("VMM: COW 1G - full copy, new_phys=0x%llx",
                   (unsigned long long)new_phys);
        return 0;
    }

    return -1;  /* Unknown level */
}

/* ============================================================================
 * VMAP STACK ALLOCATOR
 *
 * Allocates per-task kernel stacks at VOS3_KERNEL_STACK_VBASE with guard pages.
 * Layout per slot (low → high):
 *   [Guard page, unmapped, 4K] [Stack page 0..N-1, 4K each]
 * RSP starts at top of last stack page (grows down toward guard page).
 * ============================================================================ */

/** @brief Next available vmap stack slot index (atomic bump allocator) */
static volatile uint64_t g_vmap_stack_next = 0;

void* vos3_vmap_stack_alloc(uintptr_t* guard_out)
{
    /* Atomically claim the next slot */
    uint64_t slot = __atomic_fetch_add(&g_vmap_stack_next, 1, __ATOMIC_SEQ_CST);

    /* Calculate virtual addresses for this slot */
    uintptr_t slot_base = VOS3_KERNEL_STACK_VBASE + slot * VOS3_VMAP_STACK_SLOT_SIZE;
    uintptr_t guard_va  = slot_base;  /* Guard page (unmapped) */
    uintptr_t stack_va  = slot_base + VOS3_PAGE_SIZE;  /* First mapped page */

    /* Allocate and map VOS3_VMAP_STACK_PAGES physical pages */
    vos3_vmm_flags_t flags = VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_GLOBAL;
    uintptr_t phys[VOS3_VMAP_STACK_PAGES];

    for (unsigned i = 0; i < VOS3_VMAP_STACK_PAGES; i++) {
        phys[i] = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
        if (phys[i] == 0) {
            VOS3_ERROR("vmap_stack: PMM alloc failed for page %u (slot %llu)",
                       i, (unsigned long long)slot);
            /* Free already allocated pages */
            for (unsigned j = 0; j < i; j++) {
                vos3_vmm_unmap(stack_va + j * VOS3_PAGE_SIZE);
                vos3_pmm_free(phys[j]);
            }
            return NULL;
        }

        int rc = vos3_vmm_map(stack_va + i * VOS3_PAGE_SIZE, phys[i], flags);
        if (rc != 0) {
            VOS3_ERROR("vmap_stack: map failed for page %u (rc=%d)", i, rc);
            vos3_pmm_free(phys[i]);
            /* Unmap and free already mapped pages */
            for (unsigned j = 0; j < i; j++) {
                vos3_vmm_unmap(stack_va + j * VOS3_PAGE_SIZE);
                vos3_pmm_free(phys[j]);
            }
            return NULL;
        }
    }

    /* Output guard page address for task struct */
    if (guard_out != NULL) {
        *guard_out = guard_va;
    }

    /* Return the base of the mapped stack region.
     * Caller sets RSP to (base + stack_size) since stacks grow down. */
    return (void*)stack_va;
}

void vos3_vmap_stack_free(void* stack_base, uintptr_t guard_addr)
{
    if (stack_base == NULL) {
        return;
    }

    uintptr_t stack_va = (uintptr_t)stack_base;

    /* Resolve, unmap, and free each stack page */
    for (unsigned i = 0; i < VOS3_VMAP_STACK_PAGES; i++) {
        uintptr_t va = stack_va + i * VOS3_PAGE_SIZE;
        uintptr_t pa = 0;
        (void)vos3_vmm_virt_to_phys(va, &pa);
        vos3_vmm_unmap(va);
        if (pa != 0) {
            vos3_pmm_free(pa);
        }
    }

    /* Guard page was never mapped — nothing to unmap */
    (void)guard_addr;
}

/* ============================================================================
 * PHASE 4.1: VBUS PTE INJECTION
 * ============================================================================ */

int vos3_vmm_map_vbus_pages(uint8_t app_id, const uint64_t *host_phys,
                             size_t num_pages, uintptr_t virt_base)
{
    if (g_vmm_initialized == 0U) {
        return VOS3_VMM_ERR_NOTINIT;
    }

    if (host_phys == NULL || num_pages == 0) {
        return VOS3_VMM_ERR_INVALID;
    }

    /* Validate app_id against AI Guard */
    if (app_id >= VOS3_VMM_MAX_APP_CONTEXTS) {
        VOS3_ERROR("[VMM] map_vbus_pages: invalid app_id %u", app_id);
        return VOS3_VMM_ERR_INVALID;
    }

    /* Alignment check */
    if ((virt_base & (VOS3_PAGE_SIZE - 1U)) != 0) {
        VOS3_ERROR("[VMM] map_vbus_pages: virt_base 0x%lx not page-aligned",
                   (unsigned long)virt_base);
        return VOS3_VMM_ERR_ALIGN;
    }

    /* Determine address space */
    int is_user = (virt_base < 0xFFFF800000000000ULL) ? 1 : 0;
    vos3_address_space_t* as = is_user
                                ? vos3_vmm_get_current_space() : &g_kernel_space;

    /*
     * PTE flags for VBus inference pages:
     *   PRESENT       — page is valid
     *   USER          — accessible from user-space
     *   NO_EXECUTE    — data pages, not code
     *   AI_PROTECTED  — hardware-enforced AI model immutability
     *   (no WRITABLE) — read-only via x86_64 MMU
     */
    uint64_t pte_flags = VOS3_PTE_PRESENT | VOS3_PTE_USER |
                         VOS3_PTE_NO_EXECUTE | VOS3_PTE_AI_PROTECTED;

    size_t mapped = 0;
    vos3_irqflags_t irqflags = vos3_irq_save();
    vos3_spinlock_acquire(&as->lock);

    for (size_t i = 0; i < num_pages; i++) {
        uintptr_t virt = virt_base + (i * VOS3_PAGE_SIZE);
        uint64_t phys = host_phys[i];

        /* Alignment check on physical address */
        if ((phys & (VOS3_PAGE_SIZE - 1U)) != 0) {
            VOS3_ERROR("[VMM] map_vbus_pages: phys[%zu]=0x%llx not aligned",
                       i, (unsigned long long)phys);
            goto rollback;
        }

        /* Walk page tables, allocate intermediate levels as needed */
        int level;
        vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 1, is_user, &level);

        if (pte == NULL) {
            VOS3_ERROR("[VMM] map_vbus_pages: walk failed at virt 0x%lx",
                       (unsigned long)virt);
            goto rollback;
        }

        /* Reject if page already mapped */
        if (vos3_pte_is_present(*pte)) {
            VOS3_ERROR("[VMM] map_vbus_pages: virt 0x%lx already mapped",
                       (unsigned long)virt);
            goto rollback;
        }

        /* Create PTE with AI_PROTECTED + read-only + NX + USER */
        *pte = vos3_pte_create((uintptr_t)phys, pte_flags);

        /* Flush TLB for this page */
        vos3_vmm_invlpg(virt);
        mapped++;
    }

    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);

    VOS3_INFO("[VMM] map_vbus_pages: app %u, %zu pages @ 0x%lx (AI_PROTECTED|NX|RO)",
              app_id, num_pages, (unsigned long)virt_base);
    return VOS3_VMM_OK;

rollback:
    /* Unmap already-mapped pages on failure */
    for (size_t j = 0; j < mapped; j++) {
        uintptr_t virt = virt_base + (j * VOS3_PAGE_SIZE);
        int level;
        vos3_pte_t* pte = walk_page_tables(as->pml4, virt, 0, is_user, &level);
        if (pte != NULL && vos3_pte_is_present(*pte)) {
            *pte = 0;
            vos3_vmm_invlpg(virt);
        }
    }

    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);

    VOS3_ERROR("[VMM] map_vbus_pages: rolled back %zu pages", mapped);
    return -1;
}

/* ============================================================================
 * v21.3.1 (Track A) — Slot-memory expansion + KV-cache dedup variant
 * ============================================================================
 *
 * Restored 2026-05-02 from TRUTH-BRIDGE forensic protocol per
 * OMEGA_RESUMPTION_PROTOCOL.md §2.3. Test bodies in
 * backend/tests/audit/test_recursive_integrity.py §1 (dedup_expand)
 * and test_stress_75_rounds.py::c10 document the exact expected
 * structure.
 *
 * vos3_vmm_expand_slot_memory:
 *   Certified expand path. Allocates `n` 2 MiB huge physical pages
 *   and maps them at consecutive virtual addresses starting at
 *   `va_start`. Returns bytes mapped (rolls back partial progress on
 *   failure via vos3_vmm_unmap_range).
 *
 * vos3_vmm_expand_slot_memory_dedup:
 *   PRO-only dedup-aware variant. For each fresh huge page, consults
 *   kv_compressor_dedupe_hint. On dedup hit, the existing shared phys
 *   is mapped and the freshly-allocated phys is freed. On miss, the
 *   fresh phys is mapped (registry took it).
 *
 * Both functions reference VOS3_VMM_FLAG_HUGE — required by
 * test_round_c10_huge_flag_used_in_expand.
 */

#ifdef VOS3_PRO
#include "../../include/vos/kv_compressor.h"
#endif

uint64_t vos3_vmm_expand_slot_memory(uintptr_t va_start,
                                     size_t n_huge_pages,
                                     vos3_vmm_flags_t base_flags)
{
    if (n_huge_pages == 0U) return 0ULL;
    const size_t hp_size = VOS3_PAGE_SIZE_2M;
    /* The expand path always uses 2 MiB pages (VOS3_VMM_FLAG_HUGE)
     * to keep TLB pressure proportional to slot count, not page count. */
    vos3_vmm_flags_t flags = base_flags | VOS3_VMM_FLAG_HUGE;
    uintptr_t cursor_va = va_start;
    size_t mapped = 0U;

    for (size_t i = 0; i < n_huge_pages; i++) {
        uint64_t phys = vos3_pmm_alloc_huge();
        if (phys == 0ULL) {
            if (mapped > 0U) {
                vos3_vmm_unmap_range(va_start, mapped * hp_size);
            }
            return (uint64_t)(mapped * hp_size);
        }

        int rc = vos3_vmm_map(cursor_va, (uintptr_t)phys, flags);
        if (rc != VOS3_VMM_OK) {
            vos3_pmm_free_huge(phys);
            if (mapped > 0U) {
                vos3_vmm_unmap_range(va_start, mapped * hp_size);
            }
            return (uint64_t)(mapped * hp_size);
        }

        cursor_va += hp_size;
        mapped++;
    }

    return (uint64_t)(mapped * hp_size);
}

#ifdef VOS3_PRO
uint64_t vos3_vmm_expand_slot_memory_dedup(uintptr_t va_start,
                                           size_t n_huge_pages,
                                           const uint8_t (*hashes)[VOS3_KV_HASH_BYTES],
                                           vos3_vmm_flags_t base_flags)
{
    if (n_huge_pages == 0U || hashes == NULL) return 0ULL;
    const size_t hp_size = VOS3_PAGE_SIZE_2M;
    /* Dedup variant uses VOS3_VMM_FLAG_HUGE — both expand paths agree on
     * the 2 MiB page size for KV-cache slots. */
    vos3_vmm_flags_t flags = base_flags | VOS3_VMM_FLAG_HUGE;
    uintptr_t cursor_va = va_start;
    size_t mapped = 0U;

    for (size_t i = 0; i < n_huge_pages; i++) {
        uint64_t phys = vos3_pmm_alloc_huge();
        if (phys == 0ULL) {
            /* PMM exhaustion: rollback already-mapped progress. */
            if (mapped > 0U) {
                vos3_vmm_unmap_range(va_start, mapped * hp_size);
            }
            return (uint64_t)(mapped * hp_size);
        }

        /* Consult the inter-slot dedup registry. Returns either:
         *   phys                — registry full / no match: caller maps fresh
         *   mapped_phys != phys — dedup hit: caller maps shared, frees fresh */
        uint64_t mapped_phys = kv_compressor_dedupe_hint(hashes[i], phys);

        int rc = vos3_vmm_map(cursor_va, (uintptr_t)mapped_phys, flags);
        if (rc != VOS3_VMM_OK) {
            /* Map failure rollback. Free `phys` — its disposition depends
             * on whether the registry took ownership. */
            if (mapped_phys == phys) {
                /* Registry full / miss: registry did NOT keep `phys`.
                 * Fresh allocation is unowned — must free here. */
                vos3_pmm_free_huge(phys);
            } else {
                /* Dedup hit: shared `mapped_phys` is owned by an earlier
                 * registration. Fresh `phys` is unused — free here. */
                vos3_pmm_free_huge(phys);
            }
            if (mapped > 0U) {
                vos3_vmm_unmap_range(va_start, mapped * hp_size);
            }
            return (uint64_t)(mapped * hp_size);
        }

        /* Successful map. Free fresh `phys` ONLY on dedup hit
         * (mapped_phys != phys). Else `phys` was registered, registry owns. */
        if (mapped_phys != phys) {
            vos3_pmm_free_huge(phys);
        }

        cursor_va += hp_size;
        mapped++;
    }

    return (uint64_t)(mapped * hp_size);
}
#endif /* VOS3_PRO */
