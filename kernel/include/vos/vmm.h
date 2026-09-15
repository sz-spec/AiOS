/**
 * @file vmm.h
 * @brief VOS3 Virtual Memory Manager
 *
 * @details x86_64 4-level paging implementation with support for
 *          4 KiB, 2 MiB, and 1 GiB pages.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_VMM_H
#define VOS3_VMM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "../arch/x86_64/memory_map.h"

/* ============================================================================
 * PAGE TABLE CONSTANTS
 * ============================================================================ */

/** @brief Number of entries per page table level */
#define VOS3_PT_ENTRIES         ((size_t)512U)

/** @brief Page table entry size */
#define VOS3_PTE_SIZE           ((size_t)8U)

/** @brief Page table size (one page) */
#define VOS3_PT_SIZE            (VOS3_PT_ENTRIES * VOS3_PTE_SIZE)

/** @brief Index mask for page table levels */
#define VOS3_PT_INDEX_MASK      ((uint64_t)0x1FFU)

/** @brief Physical address mask in PTE */
#define VOS3_PTE_ADDR_MASK      ((uint64_t)0x000FFFFFFFFFF000ULL)

/** @brief Static 2 MiB large page address mask (conservative 40-bit fallback).
 *  Zeroes bits [20:0] (reserved in 2MB PDE). Used before CPU detection runs.
 *  After boot, use vos3_vmm_large_addr_mask() for the CPUID-derived mask. */
#define VOS3_PTE_LARGE_ADDR_MASK ((uint64_t)0x000000FFFFE00000ULL)

/** @brief Get the dynamic 2MB large page address mask based on MAXPHYADDR.
 *  Returns a mask with bits [MAXPHYADDR-1:21] set, bits [20:0] zeroed.
 *  Falls back to VOS3_PTE_LARGE_ADDR_MASK before vos3_vmm_set_maxphyaddr(). */
uint64_t vos3_vmm_large_addr_mask(void);

/** @brief Set MAXPHYADDR from CPUID leaf 0x80000008 result.
 *  Called once from kmain after vos3_cpu_detect(). */
void vos3_vmm_set_maxphyaddr(uint8_t phys_addr_bits);

/** @brief Set Cognitive Priority (bit 52) on the PTE at @p vaddr.
 *  Marks the page as semantically important to an AI agent — acts as a
 *  "Sticky Cache" hint for the Cognitive Pager. Pages with this bit set
 *  are preserved by cold scrub (Identity Buffer) and prioritised by
 *  the L3 Color Guard during slot eviction decisions.
 *  @param vaddr Virtual address whose PTE receives the cognitive bit.
 *  @return 0 on success, -1 if PTE not found. */
int vos3_vmm_set_cognitive_priority(uintptr_t vaddr);

/** @brief Check if a PTE has Cognitive Priority (bit 52) set.
 *  @param vaddr Virtual address to check.
 *  @return 1 if cognitive-priority, 0 otherwise. */
int vos3_vmm_is_cognitive_priority(uintptr_t vaddr);

/** @brief Backward-compat alias for vos3_vmm_set_cognitive_priority(). */
int vos3_vmm_set_tombstone(uintptr_t vaddr);

/** @brief Backward-compat alias for vos3_vmm_is_cognitive_priority(). */
int vos3_vmm_is_tombstoned(uintptr_t vaddr);

/** @brief 1 GiB huge page address mask */
#define VOS3_PTE_HUGE_ADDR_MASK  ((uint64_t)0x000FFFFFC0000000ULL)

/* ============================================================================
 * PAGE TABLE INDEX EXTRACTION
 * ============================================================================ */

/** @brief Extract PML4 index from virtual address */
#define VOS3_PML4_INDEX(addr)   (((uint64_t)(addr) >> 39U) & VOS3_PT_INDEX_MASK)

/** @brief Extract PDPT index from virtual address */
#define VOS3_PDPT_INDEX(addr)   (((uint64_t)(addr) >> 30U) & VOS3_PT_INDEX_MASK)

/** @brief Extract PD index from virtual address */
#define VOS3_PD_INDEX(addr)     (((uint64_t)(addr) >> 21U) & VOS3_PT_INDEX_MASK)

/** @brief Extract PT index from virtual address */
#define VOS3_PT_INDEX(addr)     (((uint64_t)(addr) >> 12U) & VOS3_PT_INDEX_MASK)

/** @brief Extract page offset from virtual address */
#define VOS3_PAGE_OFFSET(addr)  ((uint64_t)(addr) & 0xFFFULL)

/* ============================================================================
 * PAGE TABLE ENTRY FLAGS
 * ============================================================================ */

/** @brief Page present */
#define VOS3_PTE_PRESENT        ((uint64_t)(1ULL << 0))

/** @brief Page writable */
#define VOS3_PTE_WRITABLE       ((uint64_t)(1ULL << 1))

/** @brief User accessible */
#define VOS3_PTE_USER           ((uint64_t)(1ULL << 2))

/** @brief Write-through caching */
#define VOS3_PTE_WRITE_THROUGH  ((uint64_t)(1ULL << 3))

/** @brief Cache disabled */
#define VOS3_PTE_CACHE_DISABLE  ((uint64_t)(1ULL << 4))

/** @brief Page accessed */
#define VOS3_PTE_ACCESSED       ((uint64_t)(1ULL << 5))

/** @brief Page dirty (written to) */
#define VOS3_PTE_DIRTY          ((uint64_t)(1ULL << 6))

/** @brief Large page (2 MiB in PD, 1 GiB in PDPT) */
#define VOS3_PTE_LARGE          ((uint64_t)(1ULL << 7))

/** @brief Global page (not flushed on CR3 write) */
#define VOS3_PTE_GLOBAL         ((uint64_t)(1ULL << 8))

/** @brief Available for OS use (bits 9-11) */
#define VOS3_PTE_OS_AVAIL1      ((uint64_t)(1ULL << 9))
#define VOS3_PTE_OS_AVAIL2      ((uint64_t)(1ULL << 10))
#define VOS3_PTE_OS_AVAIL3      ((uint64_t)(1ULL << 11))

/** @brief Copy-on-Write flag (software, uses OS_AVAIL1) */
/* Software-only leaf bit; do not alias AI_MONITORED (bit 9).
 * IA-32e leaf entries ignore bits 58:52 (Intel SDM vol. 3A paging tables). */
#define VOS3_PTE_COW            ((uint64_t)(1ULL << 52))

/* ============================================================================
 * AI GUARD PTE BITS (Using OS-available bits 9-11)
 *
 * Defined here in vmm.h so that vmm.c can use them without depending on
 * ai_guard.h, breaking the vmm -> ai_guard layering violation.
 * ai_guard.h re-exports these via #include "vmm.h".
 * ============================================================================ */

/** @brief Page access is monitored by AI Guard */
#define VOS3_PTE_AI_MONITORED       ((uint64_t)(1ULL << 9))

/** @brief Page is AI-protected (immutable model weights) */
#define VOS3_PTE_AI_PROTECTED       ((uint64_t)(1ULL << 10))

/** @brief Red zone guard page */
#define VOS3_PTE_AI_GUARD_PAGE      ((uint64_t)(1ULL << 11))

/** @brief Mask for all AI-related PTE bits */
#define VOS3_PTE_AI_MASK            (VOS3_PTE_AI_MONITORED | \
                                     VOS3_PTE_AI_PROTECTED | \
                                     VOS3_PTE_AI_GUARD_PAGE)

/** @brief Maximum number of VBus/AI application contexts.
 *  Duplicated from ai_guard.h so vmm.c can validate app_id bounds
 *  without depending on the AI Guard subsystem. Must stay in sync. */
#define VOS3_VMM_MAX_APP_CONTEXTS   ((uint8_t)8U)

/** @brief PAT bit for 4KB pages (bit 7 in PT entries) */
#define VOS3_PTE_PAT_4K         ((uint64_t)(1ULL << 7))

/** @brief PAT bit for 2MB large pages (bit 12 in PD entries; bit 7 is PS) */
#define VOS3_PTE_PAT_2M         ((uint64_t)(1ULL << 12))

/** @brief No execute (requires NX support and EFER.NXE) */
#define VOS3_PTE_NO_EXECUTE     ((uint64_t)(1ULL << 63))

/* ============================================================================
 * PCID (Process Context ID) CONSTANTS — Phase 4.2 ASID Isolation
 * ============================================================================ */

/** @brief Default kernel PCID */
#define VOS3_PCID_KERNEL        0U

/** @brief Dedicated AI model PCID (Oracle-42 side-channel mitigation) */
#define VOS3_PCID_AI_MODEL      1U

/** @brief Maximum PCID value (x86_64 12-bit field) */
#define VOS3_PCID_MAX           4095U

/* ============================================================================
 * VMAP STACK CONSTANTS
 * ============================================================================ */

/** @brief Virtual base for kernel task stacks (vmap stacks with guard pages) */
#define VOS3_KERNEL_STACK_VBASE     0xFFFFC00000000000ULL

/** @brief Number of stack pages per task (excluding guard page).
 *  16 pages = 64KB, matching VOS3_KSTACK_CORE_SIZE for deep exec/signal paths. */
#define VOS3_VMAP_STACK_PAGES       16U

/** @brief Total virtual range per stack slot: 1 guard + STACK_PAGES mapped */
#define VOS3_VMAP_STACK_SLOT_SIZE   ((VOS3_VMAP_STACK_PAGES + 1U) * VOS3_PAGE_SIZE)

/* ============================================================================
 * COMMON FLAG COMBINATIONS
 * ============================================================================ */

/** @brief Kernel code: present, global, no-write, no-execute off */
#define VOS3_PTE_KERNEL_CODE    (VOS3_PTE_PRESENT | VOS3_PTE_GLOBAL)

/** @brief Kernel data: present, writable, global, no-execute */
#define VOS3_PTE_KERNEL_DATA    (VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE | \
                                 VOS3_PTE_GLOBAL | VOS3_PTE_NO_EXECUTE)

/** @brief Kernel read-only data: present, global, no-execute */
#define VOS3_PTE_KERNEL_RODATA  (VOS3_PTE_PRESENT | VOS3_PTE_GLOBAL | \
                                 VOS3_PTE_NO_EXECUTE)

/** @brief User code: present, user */
#define VOS3_PTE_USER_CODE      (VOS3_PTE_PRESENT | VOS3_PTE_USER)

/** @brief User data: present, user, writable, no-execute */
#define VOS3_PTE_USER_DATA      (VOS3_PTE_PRESENT | VOS3_PTE_USER | \
                                 VOS3_PTE_WRITABLE | VOS3_PTE_NO_EXECUTE)

/** @brief MMIO: present, writable, cache-disabled, no-execute */
#define VOS3_PTE_MMIO           (VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE | \
                                 VOS3_PTE_CACHE_DISABLE | VOS3_PTE_NO_EXECUTE)

/* ============================================================================
 * PAGE SIZE DEFINITIONS
 * ============================================================================ */

/** @brief 4 KiB page */
#define VOS3_PAGE_SIZE_4K       ((size_t)0x1000U)

/** @brief 2 MiB large page */
#define VOS3_PAGE_SIZE_2M       ((size_t)0x200000U)

/** @brief 1 GiB huge page */
#define VOS3_PAGE_SIZE_1G       ((size_t)0x40000000U)

/* ============================================================================
 * VMM TYPES
 * ============================================================================ */

/** @brief Page table entry type */
typedef uint64_t vos3_pte_t;

/** @brief Page table type (512 entries) */
typedef vos3_pte_t vos3_page_table_t[VOS3_PT_ENTRIES];

/**
 * @brief VMM mapping flags
 */
typedef enum vos3_vmm_flags {
    VOS3_VMM_FLAG_NONE      = 0U,
    VOS3_VMM_FLAG_WRITE     = (1U << 0),    /**< Writable */
    VOS3_VMM_FLAG_USER      = (1U << 1),    /**< User accessible */
    VOS3_VMM_FLAG_EXEC      = (1U << 2),    /**< Executable */
    VOS3_VMM_FLAG_NOCACHE   = (1U << 3),    /**< Disable caching */
    VOS3_VMM_FLAG_GLOBAL    = (1U << 4),    /**< Global (not flushed) */
    VOS3_VMM_FLAG_LARGE     = (1U << 5),    /**< Use large pages if possible */
    VOS3_VMM_FLAG_HUGE      = (1U << 6),    /**< Use huge pages if possible */
    VOS3_VMM_FLAG_COW       = (1U << 7),    /**< Copy-on-Write */
    VOS3_VMM_FLAG_SHARED    = (1U << 8),    /**< Shared mapping */
    VOS3_VMM_FLAG_DEVICE    = (1U << 9),    /**< Strong Uncacheable (UC) for MMIO/NPU BARs */
    VOS3_VMM_FLAG_WRITE_COMBINE = (1U << 10), /**< Write-Combining (WC) for NPU framebuffers */
} vos3_vmm_flags_t;

/* ============================================================================
 * VMA (Virtual Memory Area) for mmap regions
 * ============================================================================ */

/** @brief Maximum VMAs per address space */
#define VOS3_MAX_VMAS   64U

/**
 * @brief Virtual Memory Area descriptor
 *
 * Tracks mmap'd regions for demand paging and munmap.
 */
typedef struct vos3_vma {
    uint64_t    vm_start;       /**< Start virtual address (page-aligned) */
    uint64_t    vm_end;         /**< End virtual address (page-aligned) */
    int         vm_prot;        /**< Protection: PROT_READ | PROT_WRITE | PROT_EXEC */
    int         vm_flags;       /**< Flags: MAP_SHARED | MAP_PRIVATE | MAP_ANONYMOUS */
    int         vm_fd;          /**< File descriptor (-1 for anonymous) */
    uint64_t    vm_offset;      /**< File offset */
    uint8_t     valid;          /**< Entry in use */
} vos3_vma_t;

/**
 * @brief Address space structure
 */
typedef struct vos3_address_space {
    vos3_pte_t* pml4;           /**< PML4 virtual address */
    uintptr_t   pml4_phys;      /**< PML4 physical address */
    vos3_pte_t* user_pml4;      /**< Owned restricted root; populated by KPTI */
    uintptr_t   user_pml4_phys; /**< Restricted root physical address */
    uint64_t    lock;           /**< Spinlock for modifications */
    uint32_t    ref_count;      /**< Reference count */
    uint32_t    flags;          /**< Address space flags */
    uint64_t    brk;            /**< Program break (heap end) */
    uint64_t    brk_start;      /**< Initial program break */
    vos3_vma_t  vmas[VOS3_MAX_VMAS]; /**< mmap VMA descriptors */
    uint32_t    num_vmas;       /**< Number of valid VMAs */
} vos3_address_space_t;

/**
 * @brief VMM statistics
 */
typedef struct vos3_vmm_stats {
    uint64_t pages_mapped;      /**< Total pages mapped */
    uint64_t large_pages;       /**< 2 MiB pages in use */
    uint64_t huge_pages;        /**< 1 GiB pages in use */
    uint64_t page_tables;       /**< Page tables allocated */
    uint64_t tlb_flushes;       /**< TLB flush count */
} vos3_vmm_stats_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the Virtual Memory Manager
 * @param[in] boot_info Boot information from bootloader
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_init(const void* boot_info);

/**
 * @brief Map a virtual address to a physical address
 * @param[in] virt Virtual address (page-aligned)
 * @param[in] phys Physical address (page-aligned)
 * @param[in] flags Mapping flags
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_map(uintptr_t virt, uintptr_t phys, vos3_vmm_flags_t flags);

/**
 * @brief Map a range of pages
 * @param[in] virt_start Virtual start address
 * @param[in] phys_start Physical start address
 * @param[in] size Size in bytes (will be rounded up to pages)
 * @param[in] flags Mapping flags
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_map_range(uintptr_t virt_start, uintptr_t phys_start,
                       size_t size, vos3_vmm_flags_t flags);

/**
 * @brief Unmap a virtual address
 * @param[in] virt Virtual address
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_unmap(uintptr_t virt);

/**
 * @brief Unmap a range of pages
 * @param[in] virt_start Virtual start address
 * @param[in] size Size in bytes
 * @return Number of pages unmapped
 */
size_t vos3_vmm_unmap_range(uintptr_t virt_start, size_t size);

/**
 * @brief Unmap a 2MB large page
 * @param[in] virt Virtual address (2MB-aligned)
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_unmap_large(uintptr_t virt);

/**
 * @brief Translate virtual address to physical
 * @param[in] virt Virtual address
 * @param[out] phys Physical address output
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_virt_to_phys(uintptr_t virt, uintptr_t* phys);

/**
 * @brief Check if virtual address is mapped
 * @param[in] virt Virtual address
 * @return 1 if mapped, 0 if not mapped
 */
int vos3_vmm_is_mapped(uintptr_t virt);

/**
 * @brief Get page table entry for virtual address
 * @param[in] virt Virtual address
 * @param[out] pte Page table entry output
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_get_pte(uintptr_t virt, vos3_pte_t* pte);
/* Reject unsupported huge user leaves before destructive VMA edits. */
int vos3_vmm_validate_user_unmap(uintptr_t start, size_t size);

/**
 * @brief Write a PTE value directly (read-modify-write pattern)
 * @param[in] virt Virtual address
 * @param[in] pte PTE value to write
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_set_pte(uintptr_t virt, vos3_pte_t pte);

/**
 * @brief Atomic compare-and-swap on a PTE entry.
 *
 * Phase 4.9-Final: SMP-safe PTE modification for PTE inversion.
 * Atomically replaces the PTE at @p virt if its current value equals
 * @p *expected.  On failure, @p *expected is updated with the current value.
 *
 * @param[in]     virt     Virtual address owning the PTE
 * @param[in,out] expected Pointer to expected PTE value (updated on CAS failure)
 * @param[in]     desired  Desired new PTE value
 * @return 0 on success (swapped), -1 on CAS failure (*expected updated),
 *         negative VMM error on walk failure
 */
int vos3_vmm_cas_pte(uintptr_t virt, vos3_pte_t *expected, vos3_pte_t desired);

/**
 * @brief Update mapping flags
 * @param[in] virt Virtual address
 * @param[in] flags New flags
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_update_flags(uintptr_t virt, vos3_vmm_flags_t flags);

/**
 * @brief Change memory protection on a page range (mprotect)
 *
 * @param addr  Page-aligned start address
 * @param len   Length in bytes
 * @param prot  PROT_READ/PROT_WRITE/PROT_EXEC bitmask
 * @return 0 on success, negative errno on failure
 */
int vos3_vmm_mprotect_range(uintptr_t addr, size_t len, int prot);

/**
 * @brief Invalidate TLB entry for address
 * @param[in] virt Virtual address
 */
void vos3_vmm_invlpg(uintptr_t virt);

/**
 * @brief Flush entire TLB
 */
void vos3_vmm_flush_tlb(void);

/**
 * @brief Get VMM statistics
 * @param[out] stats Statistics output
 */
void vos3_vmm_get_stats(vos3_vmm_stats_t* stats);

/**
 * @brief Create new address space
 * @return Address space pointer, or NULL on failure
 */
vos3_address_space_t* vos3_vmm_create_address_space(void);

/**
 * @brief Destroy address space
 * @param[in] as Address space to destroy
 */
void vos3_vmm_destroy_address_space(vos3_address_space_t* as);

/**
 * @brief Switch to address space
 * @param[in] as Address space to switch to
 */
void vos3_vmm_switch_address_space(vos3_address_space_t* as);

/**
 * @brief Get kernel address space
 * @return Kernel address space pointer
 */
vos3_address_space_t* vos3_vmm_get_kernel_space(void);

/**
 * @brief Get current address space
 * @return Current address space pointer
 */
vos3_address_space_t* vos3_vmm_get_current_space(void);

/**
 * @brief Map a page in user address space
 * @param[in] vaddr Virtual address (user space)
 * @param[in] paddr Physical address
 * @param[in] flags PTE flags
 * @return 0 on success, negative error code on failure
 */
int vos3_vmm_map_user(uint64_t vaddr, uint64_t paddr, uint64_t flags);

/**
 * @brief Check if a user-space address is valid for demand paging
 *
 * @param[in] addr  Faulting address
 * @return 1 if valid (heap, stack, or VMA), 0 otherwise
 */
int vos3_vmm_is_valid_user_addr(uintptr_t addr);

/**
 * @brief Find VMA containing address
 * @param[in] as Address space to search
 * @param[in] addr Address to look up
 * @return Pointer to VMA, or NULL if not found
 */
vos3_vma_t* vos3_vmm_find_vma(vos3_address_space_t* as, uintptr_t addr);

/**
 * @brief Get kernel virtual address for a user virtual address
 * @param[in] user_vaddr User virtual address
 * @return Kernel virtual address, or NULL if not mapped
 */
void* vos3_vmm_get_kernel_addr(uint64_t user_vaddr);

/**
 * @brief Temporarily map physical pages to kernel space
 * @param[in] paddr Physical address
 * @param[in] size Size in bytes
 * @param[in] flags PTE flags
 * @return Kernel virtual address, or NULL on failure
 */
void* vos3_vmm_map_pages(uint64_t paddr, size_t size, uint64_t flags);

/**
 * @brief Unmap temporarily mapped pages
 * @param[in] vaddr Kernel virtual address from vos3_vmm_map_pages
 * @param[in] size Size in bytes
 */
void vos3_vmm_unmap_pages(void* vaddr, size_t size);

/**
 * @brief Allocate a vmap kernel stack with guard page
 *
 * Layout (low → high):
 *   [guard page, unmapped] [stack page 0] [stack page 1]
 *
 * @param[out] guard_out  Guard page virtual address (for fault detection)
 * @return Stack top pointer (usable RSP), or NULL on failure
 */
void* vos3_vmap_stack_alloc(uintptr_t* guard_out);

/**
 * @brief Free a vmap kernel stack
 * @param[in] stack_base  The value originally stored in task->kernel_stack
 * @param[in] guard_addr  The guard page address from task->kernel_stack_guard
 */
void vos3_vmap_stack_free(void* stack_base, uintptr_t guard_addr);

/**
 * @brief Clone address space with Copy-on-Write
 * @param[in] src Source address space
 * @return New address space with COW mappings, or NULL on failure
 */
vos3_address_space_t* vos3_vmm_clone_cow(vos3_address_space_t* src);

/**
 * @brief Handle Copy-on-Write page fault
 * @param[in] fault_addr Faulting virtual address
 * @param[in] error_code Page fault error code
 * @return 0 if COW handled, -1 if not a COW fault
 */
int vos3_vmm_handle_cow_fault(uintptr_t fault_addr, uint64_t error_code);

/* ============================================================================
 * PHASE 4.1: VBUS PTE INJECTION
 * ============================================================================ */

/**
 * @brief Map host physical pages into app inference memory (read-only)
 *
 * Maps pages with PTE flags: PRESENT | USER | NO_EXECUTE | AI_PROTECTED
 * (no WRITABLE — hardware-enforced read-only for model weight immutability).
 * Flushes TLB via invlpg after each page map.
 * Rolls back already-mapped pages on failure.
 *
 * @param[in] app_id      Application ID (0-7) for AI Guard context lookup
 * @param[in] host_phys   Array of host physical addresses (one per page)
 * @param[in] num_pages   Number of pages to map
 * @param[in] virt_base   Virtual base address for the mapping
 * @return 0 on success, -1 on failure (with rollback)
 */
int vos3_vmm_map_vbus_pages(uint8_t app_id, const uint64_t *host_phys,
                             size_t num_pages, uintptr_t virt_base);

/**
 * @brief Check if PTE has COW flag
 * @param[in] pte Page table entry
 * @return 1 if COW, 0 otherwise
 */
static inline int vos3_pte_is_cow(vos3_pte_t pte)
{
    return (pte & VOS3_PTE_COW) != 0U;
}

/* ============================================================================
 * INLINE HELPERS
 * ============================================================================ */

/**
 * @brief Create page table entry
 * @param[in] phys Physical address
 * @param[in] flags PTE flags
 * @return Page table entry
 */
static inline vos3_pte_t vos3_pte_create(uintptr_t phys, uint64_t flags)
{
    return (vos3_pte_t)((phys & VOS3_PTE_ADDR_MASK) | flags);
}

/**
 * @brief Get physical address from PTE
 * @param[in] pte Page table entry
 * @return Physical address
 */
static inline uintptr_t vos3_pte_get_addr(vos3_pte_t pte)
{
    return (uintptr_t)(pte & VOS3_PTE_ADDR_MASK);
}

/**
 * @brief Check if PTE is present
 * @param[in] pte Page table entry
 * @return 1 if present, 0 otherwise
 */
static inline int vos3_pte_is_present(vos3_pte_t pte)
{
    return (pte & VOS3_PTE_PRESENT) != 0U;
}

/**
 * @brief Check if PTE is large page
 * @param[in] pte Page table entry
 * @return 1 if large page, 0 otherwise
 */
static inline int vos3_pte_is_large(vos3_pte_t pte)
{
    return (pte & VOS3_PTE_LARGE) != 0U;
}

/**
 * @brief Check if PTE is writable
 * @param[in] pte Page table entry
 * @return 1 if writable, 0 otherwise
 */
static inline int vos3_pte_is_writable(vos3_pte_t pte)
{
    return (pte & VOS3_PTE_WRITABLE) != 0U;
}

/**
 * @brief Check if PTE is user accessible
 * @param[in] pte Page table entry
 * @return 1 if user accessible, 0 otherwise
 */
static inline int vos3_pte_is_user(vos3_pte_t pte)
{
    return (pte & VOS3_PTE_USER) != 0U;
}

/**
 * @brief Convert VMM flags to PTE flags
 * @param[in] flags VMM flags
 * @return PTE flags
 */
static inline uint64_t vos3_vmm_flags_to_pte(vos3_vmm_flags_t flags)
{
    uint64_t pte_flags = VOS3_PTE_PRESENT;

    if (flags & VOS3_VMM_FLAG_WRITE) {
        pte_flags |= VOS3_PTE_WRITABLE;
    }
    if (flags & VOS3_VMM_FLAG_USER) {
        pte_flags |= VOS3_PTE_USER;
    }
    /* W^X: writable pages MUST NOT be executable (force NX if both set) */
    if ((flags & VOS3_VMM_FLAG_WRITE) && (flags & VOS3_VMM_FLAG_EXEC)) {
        pte_flags |= VOS3_PTE_NO_EXECUTE;  /* strip exec from W+X */
    } else if (!(flags & VOS3_VMM_FLAG_EXEC)) {
        pte_flags |= VOS3_PTE_NO_EXECUTE;
    }
    if (flags & VOS3_VMM_FLAG_NOCACHE) {
        pte_flags |= VOS3_PTE_CACHE_DISABLE;
    }
    if (flags & VOS3_VMM_FLAG_GLOBAL) {
        pte_flags |= VOS3_PTE_GLOBAL;
    }

    return pte_flags;
}

/**
 * @brief Convert VMM flags to PTE flags with PAT support for device/WC mappings
 *
 * Handles the PAT bit placement difference between 4KB pages (bit 7) and
 * 2MB large pages (bit 12, since bit 7 is PS).
 *
 * PAT entry mapping (assumes default + PAT4=WC via PAT MSR):
 *   DEVICE:        PWT=1, PCD=1, PAT=0 → PAT entry 3 = UC
 *   WRITE_COMBINE: PWT=0, PCD=0, PAT=1 → PAT entry 4 = WC
 *
 * @param[in] flags VMM flags
 * @param[in] is_large Non-zero for 2MB large page entries
 * @return PTE flags with correct PAT/PCD/PWT bits
 */
static inline uint64_t vos3_vmm_flags_to_pte_ex(vos3_vmm_flags_t flags, int is_large)
{
    uint64_t pte_flags = vos3_vmm_flags_to_pte(flags);

    if (flags & VOS3_VMM_FLAG_DEVICE) {
        /* Strong Uncacheable (UC): PCD=1, PWT=1 → PAT entry 3 */
        pte_flags |= VOS3_PTE_CACHE_DISABLE | VOS3_PTE_WRITE_THROUGH;
        /* Ensure NOCACHE flag doesn't double-set — already covered */
    } else if (flags & VOS3_VMM_FLAG_WRITE_COMBINE) {
        /* Write-Combining (WC): PAT=1, PCD=0, PWT=0 → PAT entry 4 */
        pte_flags &= ~(VOS3_PTE_CACHE_DISABLE | VOS3_PTE_WRITE_THROUGH);
        pte_flags |= is_large ? VOS3_PTE_PAT_2M : VOS3_PTE_PAT_4K;
    }

    return pte_flags;
}

/* ============================================================================
 * PHASE 4.2: IPI-OPTIMIZED SMP TLB FLUSH
 * ============================================================================ */

/** @brief Dedicated IPI vector for TLB shootdown */
#define VOS3_IPI_TLB_FLUSH      ((uint8_t)0xFBU)

/**
 * @brief Flush TLB range across all CPUs via IPI broadcast
 *
 * Flushes local TLB, then sends Short-Hand IPI "All Excluding Self"
 * with acknowledge barrier (spins until all APs confirm).
 *
 * @param[in] base  Virtual base address (2MB-aligned for HugePages)
 * @param[in] size  Size in bytes to flush
 */
void vos3_vmm_flush_range(uintptr_t base, size_t size);

/**
 * @brief Flush TLB range with error return (Phase 4.2.5)
 * @param[in] base  Virtual base address
 * @param[in] size  Size in bytes
 * @return 0 on success, -1 on IPI timeout
 */
int vos3_vmm_flush_range_checked(uintptr_t base, size_t size);

/**
 * @brief Load CR3 with PCID tag for ASID isolation
 * @param[in] pml4_phys Physical address of PML4
 * @param[in] pcid PCID value (0-4095)
 */
static inline void vos3_vmm_load_cr3_pcid(uintptr_t pml4_phys, uint16_t pcid)
{
    uint64_t cr3_val = (pml4_phys & ~0xFFFULL) | (pcid & 0xFFFULL);
    __asm__ volatile("mov %0, %%cr3" :: "r"(cr3_val) : "memory");
}

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_VMM_OK             (0)
#define VOS3_VMM_ERR_NOMEM      (-1)    /**< Out of memory */
#define VOS3_VMM_ERR_INVALID    (-2)    /**< Invalid argument */
#define VOS3_VMM_ERR_ALIGN      (-3)    /**< Address not aligned */
#define VOS3_VMM_ERR_MAPPED     (-4)    /**< Already mapped */
#define VOS3_VMM_ERR_NOTMAPPED  (-5)    /**< Not mapped */
#define VOS3_VMM_ERR_NOTINIT    (-6)    /**< VMM not initialized */

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VMM_H */
