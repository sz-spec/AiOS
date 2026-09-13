/**
 * @file memory_map.h
 * @brief VOS3 Kernel Memory Map for x86_64 Architecture
 *
 * @details Defines the complete virtual and physical memory layout for the
 *          VOS3 Higher-Half Kernel targeting x86_64 bare metal systems.
 *          Designed for SMP support with up to 256 CPU cores.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @warning All pointer arithmetic must use these defined boundaries
 */

#ifndef VOS3_ARCH_X86_64_MEMORY_MAP_H
#define VOS3_ARCH_X86_64_MEMORY_MAP_H

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * MISRA C:2024 Compliance Definitions
 * ============================================================================ */

/** @brief Standard integer types for kernel development */
#include <stdint.h>
#include <stddef.h>

/** @brief Compile-time assertion macro (MISRA C:2024 Rule 11.1) */
#define VOS3_STATIC_ASSERT(cond, msg) _Static_assert((cond), msg)

/** @brief Cache line size for x86_64 (64 bytes) */
#define VOS3_CACHE_LINE_SIZE        ((size_t)64U)

/** @brief Page size (4 KiB) */
#define VOS3_PAGE_SIZE              ((size_t)4096U)

/** @brief Large page size (2 MiB) */
#define VOS3_LARGE_PAGE_SIZE        ((size_t)0x200000U)

/** @brief Huge page size (1 GiB) */
#define VOS3_HUGE_PAGE_SIZE         ((size_t)0x40000000U)

/** @brief Page shift for 4 KiB pages */
#define VOS3_PAGE_SHIFT             ((uint8_t)12U)

/** @brief Maximum supported CPU cores */
#define VOS3_MAX_CPUS               ((uint16_t)256U)

/* ============================================================================
 * CANONICAL ADDRESS SPACE (x86_64)
 * ============================================================================
 *
 * x86_64 uses 48-bit virtual addresses with canonical form:
 * - Lower half:  0x0000000000000000 - 0x00007FFFFFFFFFFF (User space)
 * - Non-canon:   0x0000800000000000 - 0xFFFF7FFFFFFFFFFF (Invalid)
 * - Upper half:  0xFFFF800000000000 - 0xFFFFFFFFFFFFFFFF (Kernel space)
 *
 * VOS3 uses Higher-Half Kernel starting at 0xFFFFFFFF80000000
 * ============================================================================ */

/** @brief Start of user space (Ring 3) */
#define VOS3_USER_SPACE_START       ((uintptr_t)0x0000000000000000ULL)

/** @brief End of user space (exclusive) */
#define VOS3_USER_SPACE_END         ((uintptr_t)0x00007FFFFFFFFFFFULL)

/** @brief User space size (128 TiB) */
#define VOS3_USER_SPACE_SIZE        ((size_t)0x0000800000000000ULL)

/** @brief Start of kernel space (Ring 0) - Higher Half */
#define VOS3_KERNEL_SPACE_START     ((uintptr_t)0xFFFF800000000000ULL)

/** @brief Higher-Half Kernel base address */
#define VOS3_KERNEL_BASE            ((uintptr_t)0xFFFFFFFF80000000ULL)

/** @brief Physical memory direct mapping offset */
#define VOS3_PHYS_MAP_OFFSET        ((uintptr_t)0xFFFF800000000000ULL)

/* ============================================================================
 * KERNEL VIRTUAL ADDRESS LAYOUT
 * ============================================================================
 *
 * 0xFFFF800000000000 - 0xFFFF87FFFFFFFFFF : Physical Memory Direct Map (8 TiB)
 * 0xFFFF880000000000 - 0xFFFF8FFFFFFFFFFF : MMIO Region (8 TiB)
 * 0xFFFF900000000000 - 0xFFFF9FFFFFFFFFFF : vmalloc region (16 TiB)
 * 0xFFFFA00000000000 - 0xFFFFA0FFFFFFFFFF : Per-CPU data (1 TiB)
 * 0xFFFFA10000000000 - 0xFFFFA1FFFFFFFFFF : Kernel stacks (1 TiB)
 * 0xFFFFFFFF80000000 - 0xFFFFFFFF9FFFFFFF : Kernel code (.text) (512 MiB)
 * 0xFFFFFFFFA0000000 - 0xFFFFFFFFBFFFFFFF : Kernel data (.data/.bss) (512 MiB)
 * 0xFFFFFFFFC0000000 - 0xFFFFFFFFDFFFFFFF : Kernel modules (512 MiB)
 * 0xFFFFFFFFE0000000 - 0xFFFFFFFFFFFFFFFF : Fixed mappings (512 MiB)
 *
 * ============================================================================ */

/** @brief Physical memory direct map start */
#define VOS3_PHYS_MAP_START         ((uintptr_t)0xFFFF800000000000ULL)

/** @brief Physical memory direct map size (8 TiB) */
#define VOS3_PHYS_MAP_SIZE          ((size_t)0x0000080000000000ULL)

/** @brief MMIO mapping region start */
#define VOS3_MMIO_START             ((uintptr_t)0xFFFF880000000000ULL)

/** @brief MMIO mapping region size (8 TiB) */
#define VOS3_MMIO_SIZE              ((size_t)0x0000080000000000ULL)

/** @brief vmalloc region start */
#define VOS3_VMALLOC_START          ((uintptr_t)0xFFFF900000000000ULL)

/** @brief vmalloc region size (16 TiB) */
#define VOS3_VMALLOC_SIZE           ((size_t)0x0000100000000000ULL)

/** @brief Per-CPU data region start */
#define VOS3_PERCPU_START           ((uintptr_t)0xFFFFA00000000000ULL)

/** @brief Per-CPU data region size (1 TiB) */
#define VOS3_PERCPU_SIZE            ((size_t)0x0000010000000000ULL)

/** @brief Per-CPU region size per core (4 GiB) */
#define VOS3_PERCPU_CORE_SIZE       ((size_t)0x0000000100000000ULL)

/** @brief Kernel stack region start */
#define VOS3_KSTACK_START           ((uintptr_t)0xFFFFA10000000000ULL)

/** @brief Kernel stack region size (1 TiB) */
#define VOS3_KSTACK_SIZE            ((size_t)0x0000010000000000ULL)

/** @brief Kernel stack size per core (64 KiB with guard pages) */
#define VOS3_KSTACK_CORE_SIZE       ((size_t)0x0000000000010000ULL)

/** @brief Kernel code section start (.text) */
#define VOS3_KTEXT_START            ((uintptr_t)0xFFFFFFFF80000000ULL)

/** @brief Kernel code section size (512 MiB) */
#define VOS3_KTEXT_SIZE             ((size_t)0x0000000020000000ULL)

/** @brief Kernel data section start (.data, .bss) */
#define VOS3_KDATA_START            ((uintptr_t)0xFFFFFFFFA0000000ULL)

/** @brief Kernel data section size (512 MiB) */
#define VOS3_KDATA_SIZE             ((size_t)0x0000000020000000ULL)

/** @brief Kernel modules region start */
#define VOS3_KMODULES_START         ((uintptr_t)0xFFFFFFFFC0000000ULL)

/** @brief Kernel modules region size (512 MiB) */
#define VOS3_KMODULES_SIZE          ((size_t)0x0000000020000000ULL)

/** @brief Fixed mappings region start */
#define VOS3_FIXMAP_START           ((uintptr_t)0xFFFFFFFFE0000000ULL)

/** @brief Fixed mappings region size (512 MiB) */
#define VOS3_FIXMAP_SIZE            ((size_t)0x0000000020000000ULL)

/* ============================================================================
 * PHYSICAL MEMORY REGIONS
 * ============================================================================ */

/** @brief Conventional memory start (below 1 MiB) */
#define VOS3_PHYS_CONV_START        ((uintptr_t)0x0000000000000000ULL)

/** @brief Conventional memory end */
#define VOS3_PHYS_CONV_END          ((uintptr_t)0x0000000000100000ULL)

/** @brief Extended memory start (1 MiB+) */
#define VOS3_PHYS_EXT_START         ((uintptr_t)0x0000000000100000ULL)

/** @brief Physical kernel load address */
#define VOS3_PHYS_KERNEL_LOAD       ((uintptr_t)0x0000000000200000ULL)

/** @brief ISA DMA region end (16 MiB) */
#define VOS3_PHYS_DMA_ISA_END       ((uintptr_t)0x0000000001000000ULL)

/** @brief DMA32 region end (4 GiB) */
#define VOS3_PHYS_DMA32_END         ((uintptr_t)0x0000000100000000ULL)

/* ============================================================================
 * PAGE TABLE DEFINITIONS (4-Level Paging)
 * ============================================================================ */

/** @brief PML4 (Page Map Level 4) entries */
#define VOS3_PML4_ENTRIES           ((size_t)512U)

/** @brief PDPT (Page Directory Pointer Table) entries */
#define VOS3_PDPT_ENTRIES           ((size_t)512U)

/** @brief PD (Page Directory) entries */
#define VOS3_PD_ENTRIES             ((size_t)512U)

/** @brief PT (Page Table) entries */
#define VOS3_PT_ENTRIES             ((size_t)512U)

/** @brief Page table entry flags */
typedef enum vos3_pte_flags {
    VOS3_PTE_PRESENT        = (1ULL << 0),   /**< Page is present */
    VOS3_PTE_WRITABLE       = (1ULL << 1),   /**< Page is writable */
    VOS3_PTE_USER           = (1ULL << 2),   /**< User-accessible */
    VOS3_PTE_WRITE_THROUGH  = (1ULL << 3),   /**< Write-through caching */
    VOS3_PTE_CACHE_DISABLE  = (1ULL << 4),   /**< Cache disabled */
    VOS3_PTE_ACCESSED       = (1ULL << 5),   /**< Page was accessed */
    VOS3_PTE_DIRTY          = (1ULL << 6),   /**< Page was written */
    VOS3_PTE_HUGE           = (1ULL << 7),   /**< Huge page (2MiB/1GiB) */
    VOS3_PTE_GLOBAL         = (1ULL << 8),   /**< Global page */
    VOS3_PTE_NO_EXECUTE     = (1ULL << 63)   /**< No execute (NX bit) */
} vos3_pte_flags_t;

/** @brief Physical address mask (bits 12-51) */
#define VOS3_PTE_ADDR_MASK          ((uint64_t)0x000FFFFFFFFFF000ULL)

/* ============================================================================
 * PROTECTION RINGS
 * ============================================================================ */

/** @brief Ring 0 - Kernel mode (full privileges) */
#define VOS3_RING_KERNEL            ((uint8_t)0U)

/** @brief Ring 1 - Reserved for drivers */
#define VOS3_RING_DRIVER            ((uint8_t)1U)

/** @brief Ring 2 - Reserved */
#define VOS3_RING_RESERVED          ((uint8_t)2U)

/** @brief Ring 3 - User mode (restricted) */
#define VOS3_RING_USER              ((uint8_t)3U)

/* ============================================================================
 * MEMORY REGION DESCRIPTOR
 * ============================================================================ */

/**
 * @brief Memory region type enumeration
 */
typedef enum vos3_mem_type {
    VOS3_MEM_USABLE         = 0U,   /**< Usable RAM */
    VOS3_MEM_RESERVED       = 1U,   /**< Reserved (unusable) */
    VOS3_MEM_ACPI_RECLAIM   = 2U,   /**< ACPI reclaimable */
    VOS3_MEM_ACPI_NVS       = 3U,   /**< ACPI Non-Volatile Storage */
    VOS3_MEM_BAD            = 4U,   /**< Bad memory */
    VOS3_MEM_KERNEL         = 5U,   /**< Kernel code/data */
    VOS3_MEM_BOOTLOADER     = 6U,   /**< Bootloader reclaimable */
    VOS3_MEM_FRAMEBUFFER    = 7U    /**< Framebuffer memory */
} vos3_mem_type_t;

/**
 * @brief Memory region descriptor
 * @note Cache-line aligned for SMP performance
 */
typedef struct __attribute__((aligned(VOS3_CACHE_LINE_SIZE))) vos3_mem_region {
    uintptr_t       base;       /**< Physical base address */
    size_t          length;     /**< Region length in bytes */
    vos3_mem_type_t type;       /**< Region type */
    uint32_t        flags;      /**< Additional flags */
    uint32_t        reserved;   /**< Reserved for alignment */
} vos3_mem_region_t;

VOS3_STATIC_ASSERT(sizeof(vos3_mem_region_t) == VOS3_CACHE_LINE_SIZE,
                   "vos3_mem_region_t must be cache-line aligned");

/* ============================================================================
 * PHYSICAL MEMORY MAP (Bootloader provided)
 * ============================================================================ */

/** @brief Maximum memory regions from bootloader */
#define VOS3_MAX_MEM_REGIONS        ((size_t)256U)

/**
 * @brief Physical memory map structure
 * @note Populated by bootloader during handover
 */
typedef struct vos3_phys_mem_map {
    uint64_t            total_memory;   /**< Total usable RAM in bytes */
    uint64_t            kernel_start;   /**< Kernel physical start */
    uint64_t            kernel_end;     /**< Kernel physical end */
    size_t              region_count;   /**< Number of valid regions */
    vos3_mem_region_t   regions[VOS3_MAX_MEM_REGIONS];  /**< Region array */
} vos3_phys_mem_map_t;

/* ============================================================================
 * ADDRESS CONVERSION MACROS (MISRA C:2024 Compliant)
 * ============================================================================ */

/**
 * @brief Convert physical address to virtual (direct map)
 * @param[in] phys Physical address
 * @return Virtual address in direct map region
 * @note MISRA C:2024 Rule 11.4 - Integer to pointer conversion
 */
static inline void* vos3_phys_to_virt(uintptr_t phys)
{
    return (void*)(phys + VOS3_PHYS_MAP_OFFSET);
}

/**
 * @brief Convert virtual address to physical (direct map)
 * @param[in] virt Virtual address in direct map region
 * @return Physical address
 * @note MISRA C:2024 Rule 11.4 - Pointer to integer conversion
 */
static inline uintptr_t vos3_virt_to_phys(const void* virt)
{
    return ((uintptr_t)virt - VOS3_PHYS_MAP_OFFSET);
}

/**
 * @brief Check if address is in kernel space
 * @param[in] addr Virtual address to check
 * @return 1 if kernel space, 0 otherwise
 */
static inline int vos3_is_kernel_addr(uintptr_t addr)
{
    return (addr >= VOS3_KERNEL_SPACE_START) ? 1 : 0;
}

/**
 * @brief Check if address is in user space
 * @param[in] addr Virtual address to check
 * @return 1 if user space, 0 otherwise
 */
static inline int vos3_is_user_addr(uintptr_t addr)
{
    return (addr <= VOS3_USER_SPACE_END) ? 1 : 0;
}

/**
 * @brief Align address up to page boundary
 * @param[in] addr Address to align
 * @return Page-aligned address (rounded up)
 */
static inline uintptr_t vos3_page_align_up(uintptr_t addr)
{
    return (addr + VOS3_PAGE_SIZE - 1U) & ~(VOS3_PAGE_SIZE - 1U);
}

/**
 * @brief Align address down to page boundary
 * @param[in] addr Address to align
 * @return Page-aligned address (rounded down)
 */
static inline uintptr_t vos3_page_align_down(uintptr_t addr)
{
    return addr & ~(VOS3_PAGE_SIZE - 1U);
}

/**
 * @brief Get Per-CPU region for a specific core
 * @param[in] cpu_id CPU core ID (0 to VOS3_MAX_CPUS-1)
 * @return Virtual address of Per-CPU region for the core
 */
static inline uintptr_t vos3_percpu_addr(uint16_t cpu_id)
{
    return VOS3_PERCPU_START + ((uintptr_t)cpu_id * VOS3_PERCPU_CORE_SIZE);
}

/**
 * @brief Get kernel stack base for a specific core
 * @param[in] cpu_id CPU core ID (0 to VOS3_MAX_CPUS-1)
 * @return Virtual address of kernel stack base for the core
 */
static inline uintptr_t vos3_kstack_base(uint16_t cpu_id)
{
    return VOS3_KSTACK_START + ((uintptr_t)cpu_id * VOS3_KSTACK_CORE_SIZE);
}

/**
 * @brief Get kernel stack top for a specific core
 * @param[in] cpu_id CPU core ID (0 to VOS3_MAX_CPUS-1)
 * @return Virtual address of kernel stack top (grows down)
 */
static inline uintptr_t vos3_kstack_top(uint16_t cpu_id)
{
    return vos3_kstack_base(cpu_id) + VOS3_KSTACK_CORE_SIZE - sizeof(uintptr_t);
}

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_MEMORY_MAP_H */
