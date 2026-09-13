/**
 * @file pmm.h
 * @brief VOS3 Physical Memory Manager (PMM)
 *
 * @details Lock-free physical page allocator for SMP systems.
 *          Uses a bitmap-based allocator with atomic operations
 *          for concurrent access from multiple CPU cores.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_PMM_H
#define VOS3_PMM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "../arch/x86_64/memory_map.h"
#include "boot_info.h"

/* ============================================================================
 * PMM CONFIGURATION
 * ============================================================================ */

/** @brief Maximum physical memory supported (16 TiB) */
#define VOS3_PMM_MAX_MEMORY         ((size_t)0x100000000000ULL)

/** @brief Maximum pages (16 TiB / 4 KiB) */
#define VOS3_PMM_MAX_PAGES          ((size_t)(VOS3_PMM_MAX_MEMORY / VOS3_PAGE_SIZE))

/** @brief Bitmap bits per element (64-bit atomic operations) */
#define VOS3_PMM_BITS_PER_ELEM      ((size_t)64U)

/** @brief Pages per bitmap element */
#define VOS3_PMM_PAGES_PER_ELEM     VOS3_PMM_BITS_PER_ELEM

/** @brief Maximum bitmap elements needed */
#define VOS3_PMM_MAX_BITMAP_ELEMS   ((size_t)(VOS3_PMM_MAX_PAGES / VOS3_PMM_BITS_PER_ELEM))

/** @brief PMM zone count */
#define VOS3_PMM_ZONE_COUNT         ((size_t)4U)

/* ============================================================================
 * MEMORY ZONES
 * ============================================================================
 *
 * Zone 0 (DMA):      0x00000000 - 0x00FFFFFF (16 MiB) - ISA DMA compatible
 * Zone 1 (DMA32):    0x01000000 - 0xFFFFFFFF (4 GiB)  - 32-bit DMA
 * Zone 2 (Normal):   0x100000000 - ...               - General purpose
 * Zone 3 (High):     > 4 TiB                         - High memory
 *
 * ============================================================================ */

/** @brief Memory zone types */
typedef enum vos3_pmm_zone {
    VOS3_PMM_ZONE_DMA      = 0U,    /**< ISA DMA zone (0-16 MiB) */
    VOS3_PMM_ZONE_DMA32    = 1U,    /**< 32-bit DMA zone (16 MiB - 4 GiB) */
    VOS3_PMM_ZONE_NORMAL   = 2U,    /**< Normal zone (4 GiB+) */
    VOS3_PMM_ZONE_HIGH     = 3U     /**< High memory zone (4 TiB+) */
} vos3_pmm_zone_t;

/** @brief Zone boundary addresses */
#define VOS3_PMM_ZONE_DMA_END       ((uintptr_t)0x0000000001000000ULL)
#define VOS3_PMM_ZONE_DMA32_END     ((uintptr_t)0x0000000100000000ULL)
#define VOS3_PMM_ZONE_NORMAL_END    ((uintptr_t)0x0000040000000000ULL)

/* ============================================================================
 * ALLOCATION FLAGS
 * ============================================================================ */

/** @brief Allocation flags */
typedef enum vos3_pmm_flags {
    VOS3_PMM_FLAG_NONE      = 0U,           /**< No special flags */
    VOS3_PMM_FLAG_ZERO      = (1U << 0),    /**< Zero the page */
    VOS3_PMM_FLAG_DMA       = (1U << 1),    /**< Allocate from DMA zone */
    VOS3_PMM_FLAG_DMA32     = (1U << 2),    /**< Allocate from DMA32 zone */
    VOS3_PMM_FLAG_CONTIGUOUS = (1U << 3),   /**< Contiguous pages required */
    VOS3_PMM_FLAG_NOWAIT    = (1U << 4)     /**< Don't wait if unavailable */
} vos3_pmm_flags_t;

/* ============================================================================
 * PMM STATISTICS
 * ============================================================================ */

/**
 * @brief Per-zone statistics
 */
typedef struct vos3_pmm_zone_stats {
    uint64_t total_pages;       /**< Total pages in zone */
    uint64_t free_pages;        /**< Free pages in zone */
    uint64_t alloc_count;       /**< Allocation count */
    uint64_t free_count;        /**< Free count */
} vos3_pmm_zone_stats_t;

/**
 * @brief Global PMM statistics
 */
typedef struct vos3_pmm_stats {
    uint64_t total_memory;      /**< Total physical memory */
    uint64_t free_memory;       /**< Free physical memory */
    uint64_t used_memory;       /**< Used physical memory */
    uint64_t reserved_memory;   /**< Reserved memory */
    uint64_t kernel_memory;     /**< Kernel memory */
    vos3_pmm_zone_stats_t zones[VOS3_PMM_ZONE_COUNT];
} vos3_pmm_stats_t;

/* ============================================================================
 * PMM STATE STRUCTURE
 * ============================================================================ */

/**
 * @brief Physical Memory Manager state
 * @note Cache-line aligned for SMP performance
 */
typedef struct __attribute__((aligned(VOS3_CACHE_LINE_SIZE))) vos3_pmm {
    /* Bitmap storage */
    uint64_t*   bitmap;             /**< Page allocation bitmap */
    size_t      bitmap_size;        /**< Bitmap size in elements */

    /* Reference count array for COW support (Phase 28) */
    uint32_t*   refcount;           /**< Per-page reference count */
    size_t      refcount_size;      /**< Refcount array size in pages */

    /* Memory boundaries */
    uintptr_t   base_addr;          /**< First managed address */
    uintptr_t   end_addr;           /**< Last managed address + 1 */
    size_t      total_pages;        /**< Total pages managed */

    /* Zone information */
    size_t      zone_start[VOS3_PMM_ZONE_COUNT];   /**< Zone start indices */
    size_t      zone_end[VOS3_PMM_ZONE_COUNT];     /**< Zone end indices */

    /* Statistics (per-zone) */
    vos3_pmm_stats_t stats;

    /* Lock for non-atomic operations */
    uint64_t    lock;               /**< Spinlock for complex operations */

    /* Initialization flag */
    uint32_t    initialized;        /**< PMM initialized flag */
    uint32_t    reserved;
} vos3_pmm_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the Physical Memory Manager
 * @param[in] boot_info Boot information from bootloader
 * @return 0 on success, negative error code on failure
 * @note Must be called before any other PMM functions
 */
int vos3_pmm_init(const vos3_boot_info_t* boot_info);

/**
 * @brief Allocate a single physical page
 * @param[in] flags Allocation flags
 * @return Physical address of allocated page, or 0 on failure
 * @note Thread-safe (lock-free for single page allocation)
 */
uintptr_t vos3_pmm_alloc(vos3_pmm_flags_t flags);

/**
 * @brief Allocate multiple contiguous physical pages
 * @param[in] count Number of pages to allocate
 * @param[in] flags Allocation flags
 * @return Physical address of first page, or 0 on failure
 */
uintptr_t vos3_pmm_alloc_pages(size_t count, vos3_pmm_flags_t flags);

/**
 * @brief Free a single physical page
 * @param[in] addr Physical address of page to free
 * @note Thread-safe (lock-free)
 */
void vos3_pmm_free(uintptr_t addr);

/**
 * @brief Free multiple contiguous physical pages
 * @param[in] addr Physical address of first page
 * @param[in] count Number of pages to free
 */
void vos3_pmm_free_pages(uintptr_t addr, size_t count);

/**
 * @brief Allocate a page from a specific zone
 * @param[in] zone Zone to allocate from
 * @param[in] flags Allocation flags
 * @return Physical address of allocated page, or 0 on failure
 */
uintptr_t vos3_pmm_alloc_zone(vos3_pmm_zone_t zone, vos3_pmm_flags_t flags);

/**
 * @brief Reserve a range of physical memory
 * @param[in] start Start physical address (page-aligned)
 * @param[in] end End physical address (page-aligned)
 * @return 0 on success, negative error code on failure
 * @note Used to mark kernel, MMIO, and other reserved regions
 */
int vos3_pmm_reserve_range(uintptr_t start, uintptr_t end);

/**
 * @brief Unreserve a range of physical memory
 * @param[in] start Start physical address (page-aligned)
 * @param[in] end End physical address (page-aligned)
 * @return 0 on success, negative error code on failure
 */
int vos3_pmm_unreserve_range(uintptr_t start, uintptr_t end);

/**
 * @brief Get PMM statistics
 * @param[out] stats Pointer to statistics structure
 */
void vos3_pmm_get_stats(vos3_pmm_stats_t* stats);

/**
 * @brief Get free page count
 * @return Number of free pages
 */
size_t vos3_pmm_free_pages_count(void);

/**
 * @brief Get total page count
 * @return Total number of managed pages
 */
size_t vos3_pmm_total_pages_count(void);

/**
 * @brief Get hugepage pool total and used counts
 * @param[out] total  Total hugepages reserved at boot
 * @param[out] used   Hugepages currently allocated
 */
void vos3_pmm_hugepage_stats(uint32_t *total, uint32_t *used);

/**
 * @brief Check if a physical address is allocated
 * @param[in] addr Physical address to check
 * @return 1 if allocated, 0 if free, -1 if invalid
 */
int vos3_pmm_is_allocated(uintptr_t addr);

/**
 * @brief Get the zone for a physical address
 * @param[in] addr Physical address
 * @return Zone type, or -1 if invalid
 */
vos3_pmm_zone_t vos3_pmm_get_zone(uintptr_t addr);

/* ============================================================================
 * REFERENCE COUNTING (COW Support - Phase 28)
 * ============================================================================ */

/**
 * @brief Increment page reference count
 * @param[in] addr Physical address of page
 * @return New reference count, or 0 on error
 * @note Thread-safe (atomic operation)
 */
uint32_t vos3_pmm_ref_inc(uintptr_t addr);

/**
 * @brief Decrement page reference count
 * @param[in] addr Physical address of page
 * @return New reference count, or 0 if page was freed
 * @note Thread-safe. Frees page when refcount reaches 0.
 */
uint32_t vos3_pmm_ref_dec(uintptr_t addr);

/**
 * @brief Get page reference count
 * @param[in] addr Physical address of page
 * @return Reference count, or 0 if not allocated
 */
uint32_t vos3_pmm_ref_get(uintptr_t addr);

/**
 * @brief Set page reference count (for COW clone)
 * @param[in] addr Physical address of page
 * @param[in] count New reference count
 * @return 0 on success, negative error code on failure
 */
int vos3_pmm_ref_set(uintptr_t addr, uint32_t count);

/* ============================================================================
 * HUGEPAGE POOL
 * ============================================================================ */

/**
 * @brief Reserve HugePages at boot time
 * @param[in] count Number of 2MB HugePages to reserve
 */
void vos3_pmm_reserve_hugepages(uint32_t count);

/**
 * @brief Allocate a 2MB HugePage from the reserved pool
 * @return Physical address of 2MB-aligned page, or 0 on failure
 */
uint64_t vos3_pmm_alloc_huge(void);

/**
 * @brief Return a 2MB HugePage to the reserved pool
 * @param[in] phys Physical address of HugePage
 */
void vos3_pmm_free_huge(uint64_t phys);

/**
 * @brief Allocate a hugepage matching a specific L3 cache color
 * @param[in] color Desired color (0-3), derived from slot_id
 * @return Physical address of 2MB page with matching color, or 0 on failure
 *
 * Color is determined by bits [22:21] of the physical address.
 * This prevents L3 set-collisions between slots loading concurrently.
 * Falls back to any available page if no color match exists.
 */
uint64_t vos3_pmm_alloc_colored_hugepage(uint8_t color);

/* ============================================================================
 * DEVICE RANGE SAFETY (Phase 1.5 NPU-Direct)
 * ============================================================================ */

/**
 * @brief Check if a physical address is outside managed RAM
 *
 * Returns non-zero if the address falls outside the PMM's managed range,
 * indicating it is likely a device BAR (NPU, GPU, etc.) and must not be
 * passed to pmm_free or pmm_alloc.
 *
 * @param[in] phys_addr Physical address to check
 * @return 1 if device range (outside managed RAM), 0 if within managed RAM
 */
int vos3_pmm_is_device_range(uintptr_t phys_addr);

/* ============================================================================
 * INLINE HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Convert physical address to page index
 * @param[in] addr Physical address
 * @return Page index
 */
static inline size_t vos3_pmm_addr_to_page(uintptr_t addr)
{
    return (size_t)(addr >> VOS3_PAGE_SHIFT);
}

/**
 * @brief Convert page index to physical address
 * @param[in] page Page index
 * @return Physical address
 */
static inline uintptr_t vos3_pmm_page_to_addr(size_t page)
{
    return (uintptr_t)(page << VOS3_PAGE_SHIFT);
}

/**
 * @brief Get bitmap element index for a page
 * @param[in] page Page index
 * @return Bitmap element index
 */
static inline size_t vos3_pmm_page_to_elem(size_t page)
{
    return page / VOS3_PMM_BITS_PER_ELEM;
}

/**
 * @brief Get bit index within bitmap element
 * @param[in] page Page index
 * @return Bit index (0-63)
 */
static inline size_t vos3_pmm_page_to_bit(size_t page)
{
    return page % VOS3_PMM_BITS_PER_ELEM;
}

/**
 * @brief Calculate memory size from page count
 * @param[in] pages Number of pages
 * @return Size in bytes
 */
static inline size_t vos3_pmm_pages_to_size(size_t pages)
{
    return pages * VOS3_PAGE_SIZE;
}

/**
 * @brief Calculate page count from memory size
 * @param[in] size Size in bytes
 * @return Number of pages (rounded up)
 */
static inline size_t vos3_pmm_size_to_pages(size_t size)
{
    return (size + VOS3_PAGE_SIZE - 1U) / VOS3_PAGE_SIZE;
}

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

/** @brief PMM error codes */
#define VOS3_PMM_OK                 (0)
#define VOS3_PMM_ERR_NOMEM          (-1)    /**< Out of memory */
#define VOS3_PMM_ERR_INVALID        (-2)    /**< Invalid argument */
#define VOS3_PMM_ERR_RANGE          (-3)    /**< Address out of range */
#define VOS3_PMM_ERR_ALIGN          (-4)    /**< Address not aligned */
#define VOS3_PMM_ERR_NOTINIT        (-5)    /**< PMM not initialized */
#define VOS3_PMM_ERR_DOUBLE_FREE    (-6)    /**< Page already free */

/* ============================================================================
 * BUDDY ALLOCATOR (Phase 5.5 — Efficient Contiguous Allocation)
 * ============================================================================
 *
 * Overlay on the bitmap allocator. Manages free lists for power-of-two
 * page blocks (orders 0-12: 4KB to 16MB). Provides O(1) allocation of
 * contiguous physical memory instead of O(n) linear bitmap scan.
 *
 * The bitmap remains the ground truth for page state. The buddy free
 * lists are an acceleration structure that tracks which contiguous
 * blocks are available.
 *
 * Order:  0    1    2    3    4     5     6     7      8      9      10     11      12
 * Pages:  1    2    4    8    16    32    64    128    256    512    1024   2048    4096
 * Size:   4K   8K   16K  32K  64K   128K  256K  512K   1MB    2MB    4MB    8MB     16MB
 *
 * ============================================================================ */

/** @brief Maximum buddy order (2^12 = 4096 pages = 16 MB) */
#define VOS3_BUDDY_MAX_ORDER    13U

/** @brief Maximum blocks tracked per order */
#define VOS3_BUDDY_MAX_BLOCKS   4096U

/** @brief Buddy free list entry — tracks a free block by its page index */
typedef struct vos3_buddy_block {
    size_t   page_idx;      /**< Starting page index of the free block */
    uint8_t  order;         /**< Block order (0-12) */
    uint8_t  free;          /**< 1 if on free list, 0 if allocated */
    uint16_t _pad;
} vos3_buddy_block_t;

/** @brief Per-order free list (intrusive linked list via index) */
typedef struct vos3_buddy_free_list {
    uint32_t count;         /**< Number of free blocks at this order */
    uint32_t head;          /**< Index into block pool (0xFFFFFFFF = empty) */
} vos3_buddy_free_list_t;

/** @brief Buddy allocator statistics */
typedef struct vos3_buddy_stats {
    uint64_t alloc_count;       /**< Total buddy allocations */
    uint64_t free_count;        /**< Total buddy frees */
    uint64_t split_count;       /**< Number of block splits */
    uint64_t merge_count;       /**< Number of block merges (coalescing) */
    uint64_t fallback_count;    /**< Fell back to bitmap scan */
    uint32_t free_blocks[VOS3_BUDDY_MAX_ORDER]; /**< Free block count per order */
} vos3_buddy_stats_t;

/**
 * @brief Initialize the buddy allocator overlay
 *
 * Scans the bitmap to build initial free lists. Must be called after
 * vos3_pmm_init() and all boot-time reservations are complete.
 *
 * @return 0 on success, negative error on failure
 */
int vos3_pmm_buddy_init(void);

/**
 * @brief Allocate contiguous pages using the buddy system
 *
 * Finds the smallest order >= requested count, splits if needed.
 * Falls back to bitmap scan if buddy lists are depleted.
 *
 * @param[in] count Number of contiguous pages needed
 * @param[in] flags Allocation flags (zone selection, zeroing)
 * @return Physical address of first page, or 0 on failure
 */
uintptr_t vos3_pmm_buddy_alloc(size_t count, vos3_pmm_flags_t flags);

/**
 * @brief Free contiguous pages back to the buddy system
 *
 * Attempts to coalesce with buddy blocks to form larger free blocks.
 *
 * @param[in] addr  Physical address of first page
 * @param[in] count Number of contiguous pages to free
 */
void vos3_pmm_buddy_free(uintptr_t addr, size_t count);

/**
 * @brief Get buddy allocator statistics
 *
 * @param[out] stats Statistics structure to fill
 */
void vos3_pmm_buddy_get_stats(vos3_buddy_stats_t *stats);

/**
 * @brief Get PMM fragmentation score and HugePage availability
 *
 * Reports three metrics for anti-fragmentation monitoring:
 * - hp_blocks_avail: Order-9+ buddy blocks available for HugePage use
 * - hp_pool_free:    Pre-reserved HugePage pool entries still available
 * - frag_pct:        Fragmentation percentage (0=unfragmented, 100=fully fragmented)
 *
 * @param[out] hp_blocks_avail  Number of Order-9+ buddy blocks (may be NULL)
 * @param[out] hp_pool_free     Number of HugePage pool entries free (may be NULL)
 * @param[out] frag_pct         Fragmentation percentage (may be NULL)
 *
 * @note Phase 7.4 — Anti-Fragmentation Observability
 */
void vos3_pmm_frag_score(uint32_t *hp_blocks_avail,
                         uint32_t *hp_pool_free,
                         uint32_t *frag_pct);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_PMM_H */
