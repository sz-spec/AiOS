/**
 * @file pmm.c
 * @brief VOS3 Physical Memory Manager Implementation
 *
 * @details Lock-free bitmap-based physical page allocator.
 *          Uses atomic bit operations for SMP-safe allocation.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/pmm.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/boot_info.h"
#include "../../include/vos/console.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/string.h"
#include "../../include/vos/percpu.h"
#include "../../include/vos/heap.h"

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Global PMM state (BSS, zero-initialized) */
static vos3_pmm_t g_pmm __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/** @brief Early boot bitmap (before dynamic allocation)
 *  v23.12: Doubled to 32768 elements × 64 bits = 2,097,152 pages = 8 GiB capacity
 *  (was 16384 = 4 GiB; extended for UEFI memory maps above 4 GB) */
static uint64_t g_early_bitmap[32768U] __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/**
 * @brief Early boot reference count array (Phase 28 COW support)
 * @details v23.12: Supports up to 2M pages (8 GiB memory). Each entry is 4 bytes.
 *          Max refcount per page is 2^32-1 (4 billion).
 */
#define VOS3_PMM_EARLY_REFCOUNT_SIZE ((size_t)(2U * 1024U * 1024U))
static uint32_t g_early_refcount[VOS3_PMM_EARLY_REFCOUNT_SIZE] __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/* ============================================================================
 * HUGEPAGE POOL — Pre-reserved at boot before fragmentation
 * ============================================================================ */

/* Open-core split: PRO builds get a larger huge-page pool ceiling (enterprise
 * inference workloads); CORE builds keep the conservative default. The pool is
 * a static BSS array sized to this ceiling. */
#ifdef VOS3_PRO
#define VOS3_HUGEPAGE_POOL_MAX  5120
#else
#define VOS3_HUGEPAGE_POOL_MAX  256
#endif

static uint64_t g_hugepage_pool[VOS3_HUGEPAGE_POOL_MAX];
static uint32_t g_hugepage_pool_count = 0;
static uint32_t g_hugepage_pool_used  = 0;
static vos3_spinlock_t g_hugepage_lock = VOS3_SPINLOCK_INIT;

/* Buddy fallback extents retain provenance until every issued huge page is
 * returned. Metadata is allocated/freed outside g_hugepage_lock. */
typedef struct huge_fallback {
    uint64_t phys;
    uint32_t count;
    uint32_t live_mask;
    struct huge_fallback* next;
} huge_fallback_t;
static huge_fallback_t* g_huge_fallbacks;

/* LIFO Recovery: per-slot release timestamp to prevent Context Collapse.
 * Pages freed within the cooldown window are deferred during allocation. */
static uint64_t g_hugepage_release_tick[VOS3_HUGEPAGE_POOL_MAX];
#define VOS3_HP_COOLDOWN_TICKS  50U  /* 500ms at 100Hz */

/* ============================================================================
 * ANTI-FRAGMENTATION GUARD — Phase 7.4 HugePage Reserve
 * ============================================================================
 * Prevents buddy allocator from splitting Order-9+ (2MB+) blocks to
 * satisfy smaller requests when HugePage-capable contiguous regions
 * are running low. This ensures vos3_pmm_alloc_huge() reliably succeeds
 * whenever free memory exceeds 30% of total.
 * ============================================================================ */

/** @brief Buddy order corresponding to a HugePage (512 pages = 2MB) */
#define VOS3_BUDDY_HP_ORDER     9U

/** @brief Minimum Order-9+ buddy blocks to preserve from splitting.
 *  Below this threshold, sub-HP requests fall back to bitmap scan
 *  rather than splitting a HugePage-capable block. */
#define VOS3_HP_ANTIFRAG_MIN    4U

/* ============================================================================
 * PER-CPU ORDER-0 PAGE CACHE — Lock-free single-page fast path
 * Reduces global bitmap contention and eliminates races between
 * lock-free single-page alloc and locked contiguous/buddy paths.
 * (Phase 5.5H hardening — SMP scale-out)
 * ============================================================================ */

/** @brief Cache depth per CPU. Small enough to avoid hoarding, large enough
 *         to amortize the bitmap scan cost (batch refill). */
#define VOS3_PCPU_PAGE_CACHE_SIZE  16U

/**
 * @brief Per-CPU page cache structure (cache-line aligned)
 * @details Stack-based: pages[0..count-1] are valid physical addresses.
 *          Only the owning CPU reads/writes this with IRQs off.
 */
typedef struct vos3_pcpu_page_cache {
    uintptr_t   pages[VOS3_PCPU_PAGE_CACHE_SIZE]; /**< Cached phys addrs */
    uint32_t    count;                              /**< Current fill level */
    uint32_t    _pad;
} __attribute__((aligned(64))) vos3_pcpu_page_cache_t;

static vos3_pcpu_page_cache_t g_pcpu_cache[VOS3_MAX_CPUS];

/* Forward declaration — defined in buddy allocator section below.
 * Referenced by vos3_pmm_alloc_huge() emergency replenishment (Phase 7.4). */
static uint32_t g_buddy_initialized;

/* ============================================================================
 * INTERNAL HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Set a bit in the bitmap (mark page as allocated)
 * @param[in] page Page index
 * @return Previous state (0 = was free, 1 = was allocated)
 */
static inline int pmm_bitmap_set(size_t page)
{
    size_t elem = vos3_pmm_page_to_elem(page);
    uint8_t bit = (uint8_t)vos3_pmm_page_to_bit(page);

    return vos3_atomic_bit_test_set(&g_pmm.bitmap[elem], bit);
}

/**
 * @brief Clear a bit in the bitmap (mark page as free)
 * @param[in] page Page index
 * @return Previous state (0 = was free, 1 = was allocated)
 */
static inline int pmm_bitmap_clear(size_t page)
{
    size_t elem = vos3_pmm_page_to_elem(page);
    uint8_t bit = (uint8_t)vos3_pmm_page_to_bit(page);

    return vos3_atomic_bit_test_clear(&g_pmm.bitmap[elem], bit);
}

/**
 * @brief Test if a page is allocated
 * @param[in] page Page index
 * @return 1 if allocated, 0 if free
 */
static inline int pmm_bitmap_test(size_t page)
{
    size_t elem = vos3_pmm_page_to_elem(page);
    uint8_t bit = (uint8_t)vos3_pmm_page_to_bit(page);

    uint64_t value = vos3_atomic_load64(&g_pmm.bitmap[elem]);
    return vos3_bit_test(value, bit);
}

/**
 * @brief Find a free page in a bitmap element (lock-free)
 * @param[in] elem Bitmap element index
 * @param[out] page_out Found page index
 * @return 1 if found and allocated, 0 otherwise
 */
static int pmm_find_free_in_elem(size_t elem, size_t* page_out)
{
    volatile uint64_t* ptr = &g_pmm.bitmap[elem];
    uint64_t current;
    uint64_t desired;
    uint8_t bit;

    /* Load current value */
    current = vos3_atomic_load64(ptr);

    /* Keep trying until we find and allocate a bit, or element is full */
    while (current != 0xFFFFFFFFFFFFFFFFULL) {
        /* Find first clear bit */
        bit = vos3_bit_scan_forward_clear(current);
        if (bit >= 64U) {
            return 0;  /* Should not happen if current != all 1s */
        }

        /* Try to set the bit atomically */
        desired = current | (1ULL << bit);
        if (vos3_atomic_cas64_bool(ptr, &current, desired)) {
            /* Success! Set refcount to 1 (Phase 28 COW) */
            size_t page = (elem * VOS3_PMM_BITS_PER_ELEM) + bit;
            if (page < g_pmm.refcount_size) {
                vos3_atomic_store32(&g_pmm.refcount[page], 1U);
            }
            *page_out = page;
            return 1;
        }
        /* CAS failed, current was updated, retry */
    }

    return 0;  /* Element is full */
}

/**
 * @brief Determine zone for a page index
 * @param[in] page Page index
 * @return Zone type
 */
static vos3_pmm_zone_t pmm_page_to_zone(size_t page)
{
    uintptr_t addr = vos3_pmm_page_to_addr(page);

    if (addr < VOS3_PMM_ZONE_DMA_END) {
        return VOS3_PMM_ZONE_DMA;
    } else if (addr < VOS3_PMM_ZONE_DMA32_END) {
        return VOS3_PMM_ZONE_DMA32;
    } else if (addr < VOS3_PMM_ZONE_NORMAL_END) {
        return VOS3_PMM_ZONE_NORMAL;
    }
    return VOS3_PMM_ZONE_HIGH;
}

/**
 * @brief Update statistics after allocation
 * @param[in] zone Zone where page was allocated
 */
static void pmm_stats_alloc(vos3_pmm_zone_t zone)
{
    vos3_atomic_fetch_add64(&g_pmm.stats.zones[zone].alloc_count, 1ULL);
    vos3_atomic_fetch_sub64(&g_pmm.stats.zones[zone].free_pages, 1ULL);
    vos3_atomic_fetch_sub64(&g_pmm.stats.free_memory, VOS3_PAGE_SIZE);
    vos3_atomic_fetch_add64(&g_pmm.stats.used_memory, VOS3_PAGE_SIZE);
}

/**
 * @brief Update statistics after free
 * @param[in] zone Zone where page was freed
 */
static void pmm_stats_free(vos3_pmm_zone_t zone)
{
    vos3_atomic_fetch_add64(&g_pmm.stats.zones[zone].free_count, 1ULL);
    vos3_atomic_fetch_add64(&g_pmm.stats.zones[zone].free_pages, 1ULL);
    vos3_atomic_fetch_add64(&g_pmm.stats.free_memory, VOS3_PAGE_SIZE);
    vos3_atomic_fetch_sub64(&g_pmm.stats.used_memory, VOS3_PAGE_SIZE);
}

/* ============================================================================
 * PER-CPU PAGE CACHE OPERATIONS
 * ============================================================================ */

/**
 * @brief Try to pop a page from the calling CPU's local cache
 * @param[in] cpu_id Current CPU's logical ID
 * @return Physical address, or 0 if cache is empty
 * @note Caller must have IRQs disabled (no preemption between cpu_id read
 *       and cache access).
 */
static inline uintptr_t pcpu_cache_pop(uint32_t cpu_id)
{
    vos3_pcpu_page_cache_t *c = &g_pcpu_cache[cpu_id];
    if (c->count == 0U) {
        return 0U;
    }
    c->count--;
    uintptr_t addr = c->pages[c->count];

    /* K-HIGH-7 fix: Record allocation stats here (at actual consumption),
     * not during cache refill (which only stages pages). */
    size_t page = vos3_pmm_addr_to_page(addr);
    vos3_pmm_zone_t zone = pmm_page_to_zone(page);
    pmm_stats_alloc(zone);

    return addr;
}

/**
 * @brief Try to push a page into the calling CPU's local cache
 * @param[in] cpu_id Current CPU's logical ID
 * @param[in] addr   Physical address to cache
 * @return 1 if cached, 0 if cache is full
 */
static inline int pcpu_cache_push(uint32_t cpu_id, uintptr_t addr)
{
    vos3_pcpu_page_cache_t *c = &g_pcpu_cache[cpu_id];
    if (c->count >= VOS3_PCPU_PAGE_CACHE_SIZE) {
        return 0;
    }
    c->pages[c->count] = addr;
    c->count++;
    return 1;
}

/**
 * @brief Batch-refill the per-CPU cache from the global bitmap
 * @param[in] cpu_id Current CPU's logical ID
 * @param[in] flags  Allocation flags (DMA/DMA32/zero)
 * @return Number of pages refilled
 *
 * @details Allocates up to VOS3_PCPU_PAGE_CACHE_SIZE pages from the
 *          global lock-free bitmap and pushes them into the local cache.
 *          This amortizes the bitmap scan cost across multiple allocs.
 */
static uint32_t pcpu_cache_refill(uint32_t cpu_id, vos3_pmm_flags_t flags)
{
    vos3_pcpu_page_cache_t *c = &g_pcpu_cache[cpu_id];
    uint32_t filled = 0U;
    vos3_pmm_flags_t raw_flags = flags & ~((vos3_pmm_flags_t)VOS3_PMM_FLAG_ZERO);

    /* Determine bitmap search range based on zone flags */
    size_t start_elem;
    size_t end_elem;

    if ((raw_flags & VOS3_PMM_FLAG_DMA) != 0U) {
        start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_DMA]);
        end_elem = vos3_pmm_page_to_elem(g_pmm.zone_end[VOS3_PMM_ZONE_DMA]);
    } else if ((raw_flags & VOS3_PMM_FLAG_DMA32) != 0U) {
        start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_DMA32]);
        end_elem = vos3_pmm_page_to_elem(g_pmm.zone_end[VOS3_PMM_ZONE_DMA32]);
    } else {
        start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_NORMAL]);
        end_elem = vos3_pmm_page_to_elem(g_pmm.total_pages);
    }

    for (size_t e = start_elem; e < end_elem; e++) {
        size_t page = 0U;
        while (c->count < VOS3_PCPU_PAGE_CACHE_SIZE &&
               pmm_find_free_in_elem(e, &page)) {
            /* K-HIGH-7 fix: Do NOT call pmm_stats_alloc() here — the page
             * is not yet consumed. Stats are recorded when pcpu_cache_pop()
             * returns the page to the caller. This prevents double-counting. */
            c->pages[c->count] = vos3_pmm_page_to_addr(page);
            c->count++;
            filled++;
            if (c->count >= VOS3_PCPU_PAGE_CACHE_SIZE || filled >= VOS3_PCPU_PAGE_CACHE_SIZE) {
                return filled;
            }
        }
    }

    return filled;
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_pmm_init(const vos3_boot_info_t* boot_info)
{
    if (boot_info == NULL) {
        return VOS3_PMM_ERR_INVALID;
    }

    if (g_pmm.initialized != 0U) {
        return VOS3_PMM_OK;  /* Already initialized */
    }

    /* Use early bitmap for now */
    g_pmm.bitmap = g_early_bitmap;
    g_pmm.bitmap_size = sizeof(g_early_bitmap) / sizeof(uint64_t);

    /* Initialize reference count array (Phase 28 COW support) */
    g_pmm.refcount = g_early_refcount;
    g_pmm.refcount_size = VOS3_PMM_EARLY_REFCOUNT_SIZE;

    /* Initialize all refcounts to 0 */
    for (size_t i = 0U; i < g_pmm.refcount_size; i++) {
        g_pmm.refcount[i] = 0U;
    }

    /* Initialize all pages as allocated (will free usable ones below) */
    for (size_t i = 0U; i < g_pmm.bitmap_size; i++) {
        g_pmm.bitmap[i] = 0xFFFFFFFFFFFFFFFFULL;
    }

    /* Set memory boundaries */
    g_pmm.base_addr = 0U;
    g_pmm.end_addr = boot_info->total_memory;
    g_pmm.total_pages = vos3_pmm_addr_to_page(g_pmm.end_addr);

    /* Cap to bitmap capacity */
    size_t max_pages = g_pmm.bitmap_size * VOS3_PMM_BITS_PER_ELEM;
    if (g_pmm.total_pages > max_pages) {
        g_pmm.total_pages = max_pages;
        g_pmm.end_addr = vos3_pmm_page_to_addr(g_pmm.total_pages);
    }

    /* Initialize zone boundaries */
    g_pmm.zone_start[VOS3_PMM_ZONE_DMA] = 0U;
    g_pmm.zone_end[VOS3_PMM_ZONE_DMA] =
        vos3_pmm_addr_to_page(VOS3_PMM_ZONE_DMA_END);

    g_pmm.zone_start[VOS3_PMM_ZONE_DMA32] =
        g_pmm.zone_end[VOS3_PMM_ZONE_DMA];
    g_pmm.zone_end[VOS3_PMM_ZONE_DMA32] =
        vos3_pmm_addr_to_page(VOS3_PMM_ZONE_DMA32_END);

    g_pmm.zone_start[VOS3_PMM_ZONE_NORMAL] =
        g_pmm.zone_end[VOS3_PMM_ZONE_DMA32];
    g_pmm.zone_end[VOS3_PMM_ZONE_NORMAL] =
        vos3_pmm_addr_to_page(VOS3_PMM_ZONE_NORMAL_END);

    g_pmm.zone_start[VOS3_PMM_ZONE_HIGH] =
        g_pmm.zone_end[VOS3_PMM_ZONE_NORMAL];
    g_pmm.zone_end[VOS3_PMM_ZONE_HIGH] = g_pmm.total_pages;

    /* Cap zone ends to total pages */
    for (size_t z = 0U; z < VOS3_PMM_ZONE_COUNT; z++) {
        if (g_pmm.zone_end[z] > g_pmm.total_pages) {
            g_pmm.zone_end[z] = g_pmm.total_pages;
        }
        if (g_pmm.zone_start[z] > g_pmm.total_pages) {
            g_pmm.zone_start[z] = g_pmm.total_pages;
        }
    }

    /* Initialize statistics */
    g_pmm.stats.total_memory = boot_info->total_memory;
    g_pmm.stats.reserved_memory = 0U;
    g_pmm.stats.kernel_memory = 0U;
    g_pmm.stats.free_memory = 0U;
    g_pmm.stats.used_memory = 0U;

    /* v23.12: Region overlap detector — warn and clamp colliding USABLE entries */
    for (uint32_t i = 0U; i < boot_info->mem_map_entries; i++) {
        const vos3_boot_mmap_entry_t* a =
            vos3_boot_get_mmap_entry(boot_info, i);
        if (a == NULL || a->type != VOS3_MMAP_USABLE) {
            continue;
        }
        uint64_t a_start = a->base;
        uint64_t a_end   = a->base + a->length;

        for (uint32_t j = i + 1U; j < boot_info->mem_map_entries; j++) {
            const vos3_boot_mmap_entry_t* b =
                vos3_boot_get_mmap_entry(boot_info, j);
            if (b == NULL || b->type != VOS3_MMAP_USABLE) {
                continue;
            }
            uint64_t b_start = b->base;
            uint64_t b_end   = b->base + b->length;

            if (a_start < b_end && b_start < a_end) {
                VOS3_WARN("PMM: OVERLAP entries %u [0x%llx-0x%llx] & %u [0x%llx-0x%llx]",
                          i, (unsigned long long)a_start, (unsigned long long)a_end,
                          j, (unsigned long long)b_start, (unsigned long long)b_end);
            }
        }
    }

    /* Process memory map - mark usable regions as free */
    for (uint32_t i = 0U; i < boot_info->mem_map_entries; i++) {
        const vos3_boot_mmap_entry_t* entry =
            vos3_boot_get_mmap_entry(boot_info, i);

        if (entry == NULL) {
            continue;
        }

        if (entry->type == VOS3_MMAP_USABLE) {
            /* Mark this region as free */
            uintptr_t start = vos3_page_align_up(entry->base);
            uintptr_t end = vos3_page_align_down(entry->base + entry->length);

            if (end > start) {
                size_t start_page = vos3_pmm_addr_to_page(start);
                size_t end_page = vos3_pmm_addr_to_page(end);

                for (size_t p = start_page; p < end_page && p < g_pmm.total_pages; p++) {
                    /* Clear bit = mark as free */
                    size_t elem = vos3_pmm_page_to_elem(p);
                    uint8_t bit = (uint8_t)vos3_pmm_page_to_bit(p);
                    g_pmm.bitmap[elem] &= ~(1ULL << bit);

                    vos3_pmm_zone_t zone = pmm_page_to_zone(p);
                    g_pmm.stats.zones[zone].free_pages++;
                    g_pmm.stats.zones[zone].total_pages++;
                    g_pmm.stats.free_memory += VOS3_PAGE_SIZE;
                }
            }
        } else {
            /* Reserved memory */
            g_pmm.stats.reserved_memory += entry->length;
        }
    }

    /* Reserve kernel memory (do this directly without calling reserve_range
     * since we're not initialized yet) */
    if (boot_info->kernel_phys_start < boot_info->kernel_phys_end) {
        uintptr_t kstart = vos3_page_align_down(boot_info->kernel_phys_start);
        uintptr_t kend = vos3_page_align_up(boot_info->kernel_phys_end);
        size_t kstart_page = vos3_pmm_addr_to_page(kstart);
        size_t kend_page = vos3_pmm_addr_to_page(kend);

        if (kend_page > g_pmm.total_pages) {
            kend_page = g_pmm.total_pages;
        }

        for (size_t p = kstart_page; p < kend_page; p++) {
            size_t elem = vos3_pmm_page_to_elem(p);
            uint8_t bit = (uint8_t)vos3_pmm_page_to_bit(p);
            uint64_t old_val = g_pmm.bitmap[elem];
            g_pmm.bitmap[elem] |= (1ULL << bit);  /* Mark as allocated */

            /* Update stats if page was free */
            if ((old_val & (1ULL << bit)) == 0ULL) {
                vos3_pmm_zone_t zone = pmm_page_to_zone(p);
                if (g_pmm.stats.zones[zone].free_pages > 0) {
                    g_pmm.stats.zones[zone].free_pages--;
                }
                if (g_pmm.stats.free_memory >= VOS3_PAGE_SIZE) {
                    g_pmm.stats.free_memory -= VOS3_PAGE_SIZE;
                }
            }
        }

        g_pmm.stats.kernel_memory =
            boot_info->kernel_phys_end - boot_info->kernel_phys_start;

        VOS3_INFO("PMM: kernel reserved 0x%llx - 0x%llx (%llu KiB)",
                  (unsigned long long)boot_info->kernel_phys_start,
                  (unsigned long long)boot_info->kernel_phys_end,
                  (unsigned long long)(boot_info->kernel_phys_end
                                       - boot_info->kernel_phys_start) / 1024U);
    }

    /* Reserve first page (null pointer protection) */
    pmm_bitmap_set(0U);

    /* Reserve page 1 (0x1000) for SMP trampoline
     * This area will be used to copy the 16-bit AP startup code */
    pmm_bitmap_set(1U);

    /* Mark as initialized */
    g_pmm.initialized = 1U;
    vos3_memory_barrier();

    return VOS3_PMM_OK;
}

uintptr_t vos3_pmm_alloc(vos3_pmm_flags_t flags)
{
    if (g_pmm.initialized == 0U) {
        return 0U;
    }

    /* === Per-CPU page cache fast path (Phase 5.5H — SMP scale-out) ===
     * For normal (non-DMA) single-page allocs, check the CPU-local cache
     * first. This avoids global bitmap contention entirely on the hot path
     * and eliminates races with the locked contiguous/buddy allocators.
     * IRQs must be disabled to prevent preemption between get_cpu_id()
     * and cache access (ensures we stay on the same CPU). */
    if ((flags & (VOS3_PMM_FLAG_DMA | VOS3_PMM_FLAG_DMA32)) == 0U) {
        uint64_t rflags;
        __asm__ volatile("pushfq; pop %0; cli" : "=r"(rflags));

        uint32_t cpu_id = get_cpu_id();
        uintptr_t cached = pcpu_cache_pop(cpu_id);
        if (cached == 0U) {
            /* Cache empty — batch refill from global bitmap */
            pcpu_cache_refill(cpu_id, flags);
            cached = pcpu_cache_pop(cpu_id);
        }

        __asm__ volatile("push %0; popfq" :: "r"(rflags) : "memory", "cc");

        if (cached != 0U) {
            /* Initialize refcount=1 for COW tracking (K-CRIT-3 fix) */
            size_t pg = vos3_pmm_addr_to_page(cached);
            if (pg < g_pmm.refcount_size) {
                vos3_atomic_store32(&g_pmm.refcount[pg], 1U);
            }
            /* Zero if requested */
            if ((flags & VOS3_PMM_FLAG_ZERO) != 0U) {
                void* virt = vos3_phys_to_virt(cached);
                uint64_t* ptr = (uint64_t*)virt;
                for (size_t i = 0U; i < VOS3_PAGE_SIZE / sizeof(uint64_t); i++) {
                    ptr[i] = 0ULL;
                }
            }
            return cached;
        }
        /* Cache refill also failed — fall through to full bitmap scan */
    }

    /* Resilience-Matrix F6 — heap shrink on PMM low-watermark.
     *
     * The slow path is hit when the per-CPU cache cannot be refilled.
     * If we're also below the low-water threshold, call into the heap
     * to release empty slabs back to the PMM before scanning the
     * global bitmap. This is rate-limited so we don't shrink on every
     * alloc once we fall below the line.
     *
     * Threshold: 5% of total normal-zone pages. Cooldown: 1024 alloc
     * attempts between shrinks. Both are conservative — first-fit on
     * the slab side does the real work; this is just the trigger. */
    {
        static uint64_t s_shrink_cooldown_left = 0ULL;
        static const uint64_t SHRINK_COOLDOWN = 1024ULL;
        const uint64_t free_normal =
            __atomic_load_n(&g_pmm.stats.zones[VOS3_PMM_ZONE_NORMAL].free_pages,
                            __ATOMIC_RELAXED);
        const uint64_t total_normal =
            (uint64_t)(g_pmm.zone_end[VOS3_PMM_ZONE_NORMAL] -
                       g_pmm.zone_start[VOS3_PMM_ZONE_NORMAL]);
        if (s_shrink_cooldown_left > 0ULL) {
            s_shrink_cooldown_left--;
        } else if (total_normal != 0ULL &&
                   (free_normal * 20ULL) < total_normal) {
            /* free < 5% of total — pressure relief. */
            extern size_t vos3_heap_shrink(void);
            (void)vos3_heap_shrink();
            s_shrink_cooldown_left = SHRINK_COOLDOWN;
        }
    }

    /* === Slow path: global bitmap scan (original logic) === */
    size_t start_elem;
    size_t end_elem;

    /* Determine search range based on flags */
    if ((flags & VOS3_PMM_FLAG_DMA) != 0U) {
        start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_DMA]);
        end_elem = vos3_pmm_page_to_elem(g_pmm.zone_end[VOS3_PMM_ZONE_DMA]);
    } else if ((flags & VOS3_PMM_FLAG_DMA32) != 0U) {
        start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_DMA32]);
        end_elem = vos3_pmm_page_to_elem(g_pmm.zone_end[VOS3_PMM_ZONE_DMA32]);
    } else {
        /* Search all zones, prefer normal first */
        start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_NORMAL]);
        end_elem = vos3_pmm_page_to_elem(g_pmm.total_pages);

        /* If no element found in normal+, fall back to DMA32 */
        size_t page = 0U;
        for (size_t e = start_elem; e < end_elem; e++) {
            if (pmm_find_free_in_elem(e, &page)) {
                vos3_pmm_zone_t zone = pmm_page_to_zone(page);
                pmm_stats_alloc(zone);

                /* Initialize refcount=1 for COW tracking */
                if (page < g_pmm.refcount_size) {
                    vos3_atomic_store32(&g_pmm.refcount[page], 1U);
                }

                uintptr_t addr = vos3_pmm_page_to_addr(page);

                /* Zero if requested */
                if ((flags & VOS3_PMM_FLAG_ZERO) != 0U) {
                    void* virt = vos3_phys_to_virt(addr);
                    uint64_t* ptr = (uint64_t*)virt;
                    for (size_t i = 0U; i < VOS3_PAGE_SIZE / sizeof(uint64_t); i++) {
                        ptr[i] = 0ULL;
                    }
                }

                return addr;
            }
        }

        /* Fall back to lower zones */
        start_elem = 0U;
        end_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[VOS3_PMM_ZONE_NORMAL]);
    }

    /* Search the determined range */
    size_t page = 0U;
    for (size_t e = start_elem; e < end_elem; e++) {
        if (pmm_find_free_in_elem(e, &page)) {
            vos3_pmm_zone_t zone = pmm_page_to_zone(page);
            pmm_stats_alloc(zone);

            /* Initialize refcount=1 for COW tracking */
            if (page < g_pmm.refcount_size) {
                vos3_atomic_store32(&g_pmm.refcount[page], 1U);
            }

            uintptr_t addr = vos3_pmm_page_to_addr(page);

            /* Zero if requested */
            if ((flags & VOS3_PMM_FLAG_ZERO) != 0U) {
                void* virt = vos3_phys_to_virt(addr);
                uint64_t* ptr = (uint64_t*)virt;
                for (size_t i = 0U; i < VOS3_PAGE_SIZE / sizeof(uint64_t); i++) {
                    ptr[i] = 0ULL;
                }
            }

            return addr;
        }
    }

    return 0U;  /* Out of memory */
}

uintptr_t vos3_pmm_alloc_pages(size_t count, vos3_pmm_flags_t flags)
{
    if (g_pmm.initialized == 0U || count == 0U) {
        return 0U;
    }

    if (count == 1U) {
        return vos3_pmm_alloc(flags);
    }

    /* For contiguous allocation, we need a lock */
    vos3_spinlock_acquire(&g_pmm.lock);

    size_t start_page = 0U;
    size_t consecutive = 0U;
    size_t search_start = 1U;  /* Skip page 0 */
    size_t search_end = g_pmm.total_pages;

    /* Find consecutive free pages */
    for (size_t p = search_start; p < search_end; p++) {
        if (!pmm_bitmap_test(p)) {
            /* Page is free */
            if (consecutive == 0U) {
                start_page = p;
            }
            consecutive++;

            if (consecutive >= count) {
                /* Found enough pages — attempt atomic claim.
                 * Lock-free single-page alloc (vos3_pmm_alloc) can
                 * race with us: it doesn't hold g_pmm.lock.  Check
                 * pmm_bitmap_set() return; if a page was already
                 * stolen, undo and restart scan from after the
                 * collision. (Phase 5.5H hardening — K-C2a fix) */
                int stolen = 0;
                size_t i;
                for (i = 0U; i < count; i++) {
                    int was_set = pmm_bitmap_set(start_page + i);
                    if (was_set) {
                        /* Page was already taken by lock-free path.
                         * Undo the pages we just claimed. */
                        for (size_t j = 0U; j < i; j++) {
                            pmm_bitmap_clear(start_page + j);
                            vos3_pmm_zone_t undo_zone = pmm_page_to_zone(start_page + j);
                            pmm_stats_free(undo_zone);
                        }
                        stolen = 1;
                        break;
                    }
                    vos3_pmm_zone_t zone = pmm_page_to_zone(start_page + i);
                    pmm_stats_alloc(zone);
                }

                if (stolen) {
                    /* Restart scan past the stolen page */
                    p = start_page + i;
                    consecutive = 0U;
                    continue;
                }

                /* Publish the initial ownership reference for every page only
                 * after the whole run is claimed. Failed claims never touch
                 * a competing allocator's reference count. */
                for (size_t j = 0U; j < count; j++) {
                    size_t page = start_page + j;
                    if (page < g_pmm.refcount_size) {
                        vos3_atomic_store32(&g_pmm.refcount[page], 1U);
                    }
                }

                vos3_spinlock_release(&g_pmm.lock);

                uintptr_t addr = vos3_pmm_page_to_addr(start_page);

                /* Zero if requested */
                if ((flags & VOS3_PMM_FLAG_ZERO) != 0U) {
                    void* virt = vos3_phys_to_virt(addr);
                    uint64_t* ptr = (uint64_t*)virt;
                    size_t qwords = (count * VOS3_PAGE_SIZE) / sizeof(uint64_t);
                    for (size_t i = 0U; i < qwords; i++) {
                        ptr[i] = 0ULL;
                    }
                }

                return addr;
            }
        } else {
            /* Page is allocated, reset consecutive count */
            consecutive = 0U;
        }
    }

    vos3_spinlock_release(&g_pmm.lock);
    return 0U;  /* Could not find contiguous pages */
}

void vos3_pmm_free(uintptr_t addr)
{
    if (g_pmm.initialized == 0U) {
        return;
    }

    /* Validate address alignment */
    if ((addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return;  /* Not page-aligned */
    }

    size_t page = vos3_pmm_addr_to_page(addr);

    /* Validate page index */
    if (page == 0U || page >= g_pmm.total_pages) {
        return;  /* Invalid page (page 0 is always reserved) */
    }

    /*
     * Phase 28 COW: Use reference counting.
     * Decrement refcount — page is only freed when refcount reaches 0.
     * All pages within refcount_size are tracked (alloc sets refcount=1).
     */
    if (page < g_pmm.refcount_size) {
        /* Always use ref_dec for tracked pages (handles CAS + bitmap clear) */
        vos3_pmm_ref_dec(addr);
        return;
    }

    /* Pages outside refcount_size: these should not normally be freed
     * through this path. Log and hard-fault to prevent silent corruption. */
    VOS3_ERROR("[PMM] free of untracked page %u (phys 0x%llx) — "
               "page index >= refcount_size (%u)",
               (unsigned)page, (unsigned long long)addr,
               (unsigned)g_pmm.refcount_size);

    /* Still clear the bitmap to avoid permanent leak, but warn loudly */
    int was_set = pmm_bitmap_clear(page);
    if (!was_set) {
        VOS3_WARN("[PMM] double-free detected: page %u (phys 0x%llx)",
                  (unsigned)page, (unsigned long long)addr);
    } else {
        vos3_pmm_zone_t zone = pmm_page_to_zone(page);
        pmm_stats_free(zone);
    }
}

void vos3_pmm_free_pages(uintptr_t addr, size_t count)
{
    if (g_pmm.initialized == 0U || count == 0U) {
        return;
    }

    for (size_t i = 0U; i < count; i++) {
        vos3_pmm_free(addr + (i * VOS3_PAGE_SIZE));
    }
}

uintptr_t vos3_pmm_alloc_zone(vos3_pmm_zone_t zone, vos3_pmm_flags_t flags)
{
    if (g_pmm.initialized == 0U || zone >= VOS3_PMM_ZONE_COUNT) {
        return 0U;
    }

    size_t start_elem = vos3_pmm_page_to_elem(g_pmm.zone_start[zone]);
    size_t end_elem = vos3_pmm_page_to_elem(g_pmm.zone_end[zone]);

    size_t page = 0U;
    for (size_t e = start_elem; e < end_elem; e++) {
        if (pmm_find_free_in_elem(e, &page)) {
            /* Verify page is in requested zone */
            if (page >= g_pmm.zone_start[zone] && page < g_pmm.zone_end[zone]) {
                pmm_stats_alloc(zone);

                uintptr_t addr = vos3_pmm_page_to_addr(page);

                if ((flags & VOS3_PMM_FLAG_ZERO) != 0U) {
                    void* virt = vos3_phys_to_virt(addr);
                    uint64_t* ptr = (uint64_t*)virt;
                    for (size_t i = 0U; i < VOS3_PAGE_SIZE / sizeof(uint64_t); i++) {
                        ptr[i] = 0ULL;
                    }
                }

                return addr;
            } else {
                /* Wrong zone, free it and continue */
                pmm_bitmap_clear(page);
            }
        }
    }

    return 0U;
}

int vos3_pmm_reserve_range(uintptr_t start, uintptr_t end)
{
    if (g_pmm.initialized == 0U) {
        return VOS3_PMM_ERR_NOTINIT;
    }

    if (start >= end) {
        return VOS3_PMM_ERR_INVALID;
    }

    /* Align addresses */
    start = vos3_page_align_down(start);
    end = vos3_page_align_up(end);

    size_t start_page = vos3_pmm_addr_to_page(start);
    size_t end_page = vos3_pmm_addr_to_page(end);

    if (end_page > g_pmm.total_pages) {
        end_page = g_pmm.total_pages;
    }

    for (size_t p = start_page; p < end_page; p++) {
        int was_free = !pmm_bitmap_set(p);
        if (was_free) {
            vos3_pmm_zone_t zone = pmm_page_to_zone(p);
            vos3_atomic_fetch_sub64(&g_pmm.stats.zones[zone].free_pages, 1ULL);
            vos3_atomic_fetch_sub64(&g_pmm.stats.free_memory, VOS3_PAGE_SIZE);
            vos3_atomic_fetch_add64(&g_pmm.stats.reserved_memory, VOS3_PAGE_SIZE);
        }
    }

    return VOS3_PMM_OK;
}

int vos3_pmm_unreserve_range(uintptr_t start, uintptr_t end)
{
    if (g_pmm.initialized == 0U) {
        return VOS3_PMM_ERR_NOTINIT;
    }

    if (start >= end) {
        return VOS3_PMM_ERR_INVALID;
    }

    /* Align addresses */
    start = vos3_page_align_down(start);
    end = vos3_page_align_up(end);

    size_t start_page = vos3_pmm_addr_to_page(start);
    size_t end_page = vos3_pmm_addr_to_page(end);

    if (end_page > g_pmm.total_pages) {
        end_page = g_pmm.total_pages;
    }

    for (size_t p = start_page; p < end_page; p++) {
        int was_set = pmm_bitmap_clear(p);
        if (was_set) {
            vos3_pmm_zone_t zone = pmm_page_to_zone(p);
            pmm_stats_free(zone);
            vos3_atomic_fetch_sub64(&g_pmm.stats.reserved_memory, VOS3_PAGE_SIZE);
        }
    }

    return VOS3_PMM_OK;
}

void vos3_pmm_get_stats(vos3_pmm_stats_t* stats)
{
    if (stats == NULL || g_pmm.initialized == 0U) {
        return;
    }

    /* Copy statistics (atomic loads for accuracy) */
    stats->total_memory = g_pmm.stats.total_memory;
    stats->free_memory = vos3_atomic_load64(&g_pmm.stats.free_memory);
    stats->used_memory = vos3_atomic_load64(&g_pmm.stats.used_memory);
    stats->reserved_memory = vos3_atomic_load64(&g_pmm.stats.reserved_memory);
    stats->kernel_memory = g_pmm.stats.kernel_memory;

    for (size_t z = 0U; z < VOS3_PMM_ZONE_COUNT; z++) {
        stats->zones[z].total_pages = g_pmm.stats.zones[z].total_pages;
        stats->zones[z].free_pages =
            vos3_atomic_load64(&g_pmm.stats.zones[z].free_pages);
        stats->zones[z].alloc_count =
            vos3_atomic_load64(&g_pmm.stats.zones[z].alloc_count);
        stats->zones[z].free_count =
            vos3_atomic_load64(&g_pmm.stats.zones[z].free_count);
    }
}

size_t vos3_pmm_free_pages_count(void)
{
    if (g_pmm.initialized == 0U) {
        return 0U;
    }
    return (size_t)(vos3_atomic_load64(&g_pmm.stats.free_memory) / VOS3_PAGE_SIZE);
}

size_t vos3_pmm_total_pages_count(void)
{
    if (g_pmm.initialized == 0U) {
        return 0U;
    }
    return g_pmm.total_pages;
}

void vos3_pmm_hugepage_stats(uint32_t *total, uint32_t *used)
{
    if (total) *total = g_hugepage_pool_count;
    if (used)  *used  = g_hugepage_pool_used;
}

int vos3_pmm_is_allocated(uintptr_t addr)
{
    if (g_pmm.initialized == 0U) {
        return -1;
    }

    if ((addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return -1;  /* Not page-aligned */
    }

    size_t page = vos3_pmm_addr_to_page(addr);
    if (page >= g_pmm.total_pages) {
        return -1;  /* Out of range */
    }

    return pmm_bitmap_test(page);
}

vos3_pmm_zone_t vos3_pmm_get_zone(uintptr_t addr)
{
    return pmm_page_to_zone(vos3_pmm_addr_to_page(addr));
}

/* ============================================================================
 * DEVICE RANGE SAFETY (Phase 1.5 NPU-Direct)
 * ============================================================================ */

int vos3_pmm_is_device_range(uintptr_t phys_addr)
{
    if (g_pmm.initialized == 0U) {
        return 1;  /* Conservative: treat as device if PMM not ready */
    }

    /* Address is a device range if it falls outside managed RAM */
    return (phys_addr < g_pmm.base_addr || phys_addr >= g_pmm.end_addr) ? 1 : 0;
}

/* ============================================================================
 * REFERENCE COUNTING (COW Support - Phase 28)
 * ============================================================================ */

uint32_t vos3_pmm_ref_inc(uintptr_t addr)
{
    if (g_pmm.initialized == 0U) {
        return 0U;
    }

    /* Validate address alignment */
    if ((addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return 0U;
    }

    size_t page = vos3_pmm_addr_to_page(addr);

    /* Validate page index */
    if (page >= g_pmm.refcount_size) {
        return 0U;
    }

    /* Atomically increment refcount */
    uint32_t new_count = vos3_atomic_fetch_add32(&g_pmm.refcount[page], 1U) + 1U;
    return new_count;
}

uint32_t vos3_pmm_ref_dec(uintptr_t addr)
{
    if (g_pmm.initialized == 0U) {
        return 0U;
    }

    /* Validate address alignment */
    if ((addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return 0U;
    }

    size_t page = vos3_pmm_addr_to_page(addr);

    /* Validate page index */
    if (page == 0U || page >= g_pmm.refcount_size) {
        return 0U;
    }

    /* CAS loop to atomically decrement refcount without TOCTOU.
     * Prevents SMP double-decrement: two CPUs both seeing current==1
     * would both pass a naive guard and both decrement, wrapping to
     * UINT32_MAX on the second CPU (memory leak / use-after-free).
     * (Phase 5.5H hardening — K-C1 fix) */
    for (;;) {
        uint32_t current = vos3_atomic_load32(&g_pmm.refcount[page]);
        if (current == 0U) {
            return 0U;  /* Already zero — nothing to decrement */
        }

        uint32_t desired = current - 1U;
        uint32_t prev = vos3_atomic_cas32(&g_pmm.refcount[page],
                                          current, desired);
        if (prev == current) {
            /* CAS succeeded — we own this decrement */
            if (desired == 0U) {
                /* Refcount reached 0: free the page */
                int was_set = pmm_bitmap_clear(page);
                if (was_set) {
                    vos3_pmm_zone_t zone = pmm_page_to_zone(page);
                    pmm_stats_free(zone);
                }
            }
            return desired;
        }
        /* CAS failed — another CPU modified refcount, retry with fresh value */
        vos3_cpu_relax(1U);
    }
}

uint32_t vos3_pmm_ref_get(uintptr_t addr)
{
    if (g_pmm.initialized == 0U) {
        return 0U;
    }

    /* Validate address alignment */
    if ((addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return 0U;
    }

    size_t page = vos3_pmm_addr_to_page(addr);

    /* Validate page index */
    if (page >= g_pmm.refcount_size) {
        return 0U;
    }

    return vos3_atomic_load32(&g_pmm.refcount[page]);
}

int vos3_pmm_ref_set(uintptr_t addr, uint32_t count)
{
    if (g_pmm.initialized == 0U) {
        return VOS3_PMM_ERR_NOTINIT;
    }

    /* Validate address alignment */
    if ((addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        return VOS3_PMM_ERR_ALIGN;
    }

    size_t page = vos3_pmm_addr_to_page(addr);

    /* Validate page index */
    if (page >= g_pmm.refcount_size) {
        return VOS3_PMM_ERR_RANGE;
    }

    vos3_atomic_store32(&g_pmm.refcount[page], count);
    return VOS3_PMM_OK;
}

/* ============================================================================
 * HUGEPAGE POOL FUNCTIONS
 * ============================================================================ */

void vos3_pmm_reserve_hugepages(uint32_t count)
{
    if (count > VOS3_HUGEPAGE_POOL_MAX)
        count = VOS3_HUGEPAGE_POOL_MAX;

    if (g_pmm.initialized == 0U) return;

    /* Scan bitmap for 2MB-aligned contiguous blocks (512 pages each).
     * Start at the first 2MB-aligned page boundary and stride by 512. */
    size_t pages_per_huge = VOS3_LARGE_PAGE_SIZE / VOS3_PAGE_SIZE;  /* 512 */
    size_t first_aligned = pages_per_huge;  /* Skip page 0 region */
    if (first_aligned == 0) first_aligned = pages_per_huge;

    vos3_spinlock_acquire(&g_pmm.lock);

    /* Safety: never consume more than 50% of free memory */
    uint64_t free_pages_now = vos3_atomic_load64(&g_pmm.stats.free_memory)
                              / VOS3_PAGE_SIZE;
    uint64_t max_huge_pages = (free_pages_now / pages_per_huge) / 2U;
    if (count > (uint32_t)max_huge_pages) {
        count = (uint32_t)max_huge_pages;
    }

    for (size_t start = first_aligned;
         start + pages_per_huge <= g_pmm.total_pages &&
         g_hugepage_pool_count < count;
         start += pages_per_huge) {

        /* Check if all 512 pages in this 2MB block are free */
        int all_free = 1;
        for (size_t p = start; p < start + pages_per_huge; p++) {
            if (pmm_bitmap_test(p)) {
                all_free = 0;
                break;
            }
        }

        if (!all_free) continue;

        /* Bitmap fast allocators do not take this lock. Claim transactionally
         * and do not overwrite the winning allocator's reference count. */
        size_t claimed = 0;
        for (; claimed < pages_per_huge; claimed++) {
            size_t page = start + claimed;
            if (pmm_bitmap_set(page)) break;
            pmm_stats_alloc(pmm_page_to_zone(page));
        }
        if (claimed != pages_per_huge) {
            for (size_t i = 0; i < claimed; i++) {
                pmm_bitmap_clear(start + i);
                pmm_stats_free(pmm_page_to_zone(start + i));
            }
            continue;
        }
        for (size_t i = 0; i < pages_per_huge; i++)
            if (start + i < g_pmm.refcount_size)
                vos3_atomic_store32(&g_pmm.refcount[start + i], 1U);

        g_hugepage_pool[g_hugepage_pool_count++] = vos3_pmm_page_to_addr(start);
    }

    vos3_spinlock_release(&g_pmm.lock);

    VOS3_INFO("PMM: reserved %u HugePages (%u MB)",
              g_hugepage_pool_count, g_hugepage_pool_count * 2);
}

/* Preserve buddy fallback capacity without admitting foreign frees into the
 * reserved pool. A power-of-two rounded buddy extent is freed only when all
 * requested huge pages have been returned. */
static uint64_t pmm_huge_fallback_alloc(uint32_t count)
{
    if (g_buddy_initialized == 0U || count == 0 || count > 16U) return 0;
    huge_fallback_t* extent = vos3_kmalloc(sizeof(*extent));
    if (extent == NULL) return 0;
    uint32_t allocated = 1U;
    while (allocated < count) allocated <<= 1U;
    uint64_t phys = vos3_pmm_buddy_alloc((size_t)allocated * 512U, 0);
    if (phys == 0 || (phys & ((uint64_t)allocated * VOS3_LARGE_PAGE_SIZE - 1U)) != 0) {
        /* Buddy's emergency bitmap fallback need not preserve order alignment.
         * Return its exact requested pages, never advertise a misaligned buddy. */
        if (phys != 0) vos3_pmm_free_pages(phys, (size_t)allocated * 512U);
        vos3_kfree(extent);
        return 0;
    }
    extent->phys = phys;
    extent->count = count;
    extent->live_mask = (1U << count) - 1U;
    vos3_irqflags_t irq = vos3_irq_save();
    vos3_spinlock_acquire(&g_hugepage_lock);
    extent->next = g_huge_fallbacks;
    g_huge_fallbacks = extent;
    vos3_spinlock_release(&g_hugepage_lock);
    vos3_irq_restore(irq);
    return phys;
}

uint64_t vos3_pmm_alloc_huge(void)
{
    vos3_irqflags_t f = vos3_irq_save();
    vos3_spinlock_acquire(&g_hugepage_lock);
    uint64_t phys = 0;
    if (g_hugepage_pool_used < g_hugepage_pool_count) {
        g_hugepage_release_tick[g_hugepage_pool_used] = 0;
        phys = g_hugepage_pool[g_hugepage_pool_used++];
    }
    vos3_spinlock_release(&g_hugepage_lock);
    vos3_irq_restore(f);
    return phys != 0 ? phys : pmm_huge_fallback_alloc(1);
}

static void pmm_huge_swap(uint32_t a, uint32_t b)
{
    uint64_t phys = g_hugepage_pool[a], tick = g_hugepage_release_tick[a];
    g_hugepage_pool[a] = g_hugepage_pool[b];
    g_hugepage_release_tick[a] = g_hugepage_release_tick[b];
    g_hugepage_pool[b] = phys;
    g_hugepage_release_tick[b] = tick;
}
static void pmm_huge_sift(uint32_t base, uint32_t root, uint32_t length)
{
    while (root < length / 2U) {
        uint32_t child = root * 2U + 1U;
        if (child + 1U < length && g_hugepage_pool[base + child] < g_hugepage_pool[base + child + 1U]) child++;
        if (g_hugepage_pool[base + root] >= g_hugepage_pool[base + child]) break;
        pmm_huge_swap(base + root, base + child);
        root = child;
    }
}

uint64_t vos3_pmm_alloc_huge_contiguous(uint32_t count)
{
    if (count == 0 || count > 16U) return 0;
    if (count == 1) return vos3_pmm_alloc_huge();
    vos3_irqflags_t f = vos3_irq_save();
    vos3_spinlock_acquire(&g_hugepage_lock);
    /* Sort only the free partition; active ownership and timestamps stay
     * paired. Select a whole contiguous run before publishing any ownership. */
    /* Heapsort bounds the IRQ-disabled sort to O(pool_count log pool_count). */
    uint32_t available = g_hugepage_pool_count - g_hugepage_pool_used;
    for (uint32_t i = available / 2U; i > 0; i--)
        pmm_huge_sift(g_hugepage_pool_used, i - 1U, available);
    for (uint32_t n = available; n > 1U; n--) {
        pmm_huge_swap(g_hugepage_pool_used, g_hugepage_pool_used + n - 1U);
        pmm_huge_sift(g_hugepage_pool_used, 0, n - 1U);
    }
    uint64_t result = 0;
    uint32_t run = 0;
    for (uint32_t i = g_hugepage_pool_used; i < g_hugepage_pool_count; i++) {
        run = (run && g_hugepage_pool[i] - g_hugepage_pool[i - 1] == VOS3_LARGE_PAGE_SIZE) ? run + 1 : 1;
        if (run == count) { result = g_hugepage_pool[i + 1 - count]; break; }
    }
    if (result != 0) {
        for (uint32_t n = 0; n < count; n++) {
            uint32_t index = g_hugepage_pool_used;
            while (index < g_hugepage_pool_count &&
                   g_hugepage_pool[index] != result + n * VOS3_LARGE_PAGE_SIZE) index++;
            uint32_t front = g_hugepage_pool_used++;
            g_hugepage_pool[index] = g_hugepage_pool[front];
            g_hugepage_release_tick[index] = g_hugepage_release_tick[front];
            g_hugepage_pool[front] = result + n * VOS3_LARGE_PAGE_SIZE;
            g_hugepage_release_tick[front] = 0;
        }
    }
    vos3_spinlock_release(&g_hugepage_lock);
    vos3_irq_restore(f);
    return result != 0 ? result : pmm_huge_fallback_alloc(count);
}

void vos3_pmm_free_huge(uint64_t phys)
{
    if (phys == 0 || (phys & (VOS3_LARGE_PAGE_SIZE - 1U)) != 0) return;
    vos3_irqflags_t f = vos3_irq_save();
    vos3_spinlock_acquire(&g_hugepage_lock);
    for (uint32_t i = 0; i < g_hugepage_pool_used; i++) {
        if (g_hugepage_pool[i] != phys) continue;
        uint32_t last = --g_hugepage_pool_used;
        g_hugepage_pool[i] = g_hugepage_pool[last];
        g_hugepage_release_tick[i] = g_hugepage_release_tick[last];
        g_hugepage_pool[last] = phys;
        g_hugepage_release_tick[last] = vos3_timer_get_ticks();
        vos3_spinlock_release(&g_hugepage_lock);
        vos3_irq_restore(f);
        return;
    }
    huge_fallback_t** link = &g_huge_fallbacks;
    huge_fallback_t* retired = NULL;
    for (; *link != NULL; link = &(*link)->next) {
        huge_fallback_t* extent = *link;
        if (phys < extent->phys) continue;
        uint64_t index = (phys - extent->phys) / VOS3_LARGE_PAGE_SIZE;
        if (index >= extent->count) continue;
        uint32_t bit = 1U << index;
        if ((extent->live_mask & bit) == 0) break; /* Duplicate return. */
        extent->live_mask &= ~bit;
        if (extent->live_mask == 0) { *link = extent->next; retired = extent; }
        break;
    }
    vos3_spinlock_release(&g_hugepage_lock);
    vos3_irq_restore(f);
    if (retired != NULL) {
        uint32_t allocated = 1U;
        while (allocated < retired->count) allocated <<= 1U;
        vos3_pmm_buddy_free(retired->phys, (size_t)allocated * 512U);
        vos3_kfree(retired);
    }
    /* Unknown/duplicate addresses must never decrement or overwrite the pool. */
}

uint64_t vos3_pmm_alloc_colored_hugepage(uint8_t color)
{
    vos3_irqflags_t f = vos3_irq_save();
    vos3_spinlock_acquire(&g_hugepage_lock);

    uint64_t result = 0;
    uint64_t now = vos3_timer_get_ticks();

    /* Scan unused pool entries for a page matching the requested L3 color.
     * Color = physical address bits [22:21] (2 bits → 4 colors for 2MB pages).
     * LIFO Recovery: skip pages freed within the cooldown window to prevent
     * Context Collapse (stale cognitive cache lines reused prematurely). */
    for (uint32_t i = g_hugepage_pool_used; i < g_hugepage_pool_count; i++) {
        /* Skip pages still in cooldown (recently freed by SLOT_RESET) */
        if (g_hugepage_release_tick[i] != 0 &&
            (now - g_hugepage_release_tick[i]) < VOS3_HP_COOLDOWN_TICKS) {
            continue;
        }
        uint8_t page_color = (uint8_t)((g_hugepage_pool[i] >> 21U) & 0x3U);
        if (page_color == (color & 0x3U)) {
            result = g_hugepage_pool[i];
            /* Swap with the current allocation frontier */
            g_hugepage_pool[i] = g_hugepage_pool[g_hugepage_pool_used];
            g_hugepage_release_tick[i] = g_hugepage_release_tick[g_hugepage_pool_used];
            g_hugepage_pool[g_hugepage_pool_used] = result;
            g_hugepage_release_tick[g_hugepage_pool_used] = 0;
            g_hugepage_pool_used++;
            break;
        }
    }

    /* Fallback: if no matching color (or all in cooldown), use any aged page */
    if (result == 0) {
        for (uint32_t i = g_hugepage_pool_used; i < g_hugepage_pool_count; i++) {
            if (g_hugepage_release_tick[i] != 0 &&
                (now - g_hugepage_release_tick[i]) < VOS3_HP_COOLDOWN_TICKS) {
                continue;
            }
            result = g_hugepage_pool[i];
            g_hugepage_pool[i] = g_hugepage_pool[g_hugepage_pool_used];
            g_hugepage_release_tick[i] = g_hugepage_release_tick[g_hugepage_pool_used];
            g_hugepage_pool[g_hugepage_pool_used] = result;
            g_hugepage_release_tick[g_hugepage_pool_used] = 0;
            g_hugepage_pool_used++;
            break;
        }
    }

    /* Last resort: ignore cooldown (better to reuse than OOM) */
    if (result == 0 && g_hugepage_pool_used < g_hugepage_pool_count) {
        result = g_hugepage_pool[g_hugepage_pool_used];
        g_hugepage_release_tick[g_hugepage_pool_used] = 0;
        g_hugepage_pool_used++;
    }

    vos3_spinlock_release(&g_hugepage_lock);
    vos3_irq_restore(f);
    return result;
}

/* ============================================================================
 * BUDDY ALLOCATOR — Phase 5.5 Overlay for O(1) Contiguous Allocation
 * ============================================================================
 *
 * The buddy system manages power-of-two free block lists on top of the
 * existing bitmap allocator. The bitmap remains ground truth; buddy lists
 * are an acceleration structure for contiguous allocation.
 *
 * On init, the buddy scans the bitmap to find maximal aligned free regions
 * and populates the free lists. On alloc, it finds the smallest sufficient
 * order, splitting larger blocks as needed. On free, it coalesces with
 * buddy partners to form larger blocks.
 *
 * This eliminates the O(N) linear scan in vos3_pmm_alloc_pages() for
 * contiguous allocations, replacing it with O(log N) buddy operations.
 * ============================================================================ */

/** @brief Buddy free list node — intrusive linked list via next index */
typedef struct buddy_node {
    size_t   page_idx;      /**< Starting page index */
    uint32_t next;          /**< Next node index (0xFFFFFFFF = end) */
    uint8_t  on_list;       /**< 1 if currently on a free list */
    uint8_t  _pad[3];
} buddy_node_t;

/** @brief Buddy allocator pool size */
#define BUDDY_POOL_SIZE     8192U

/** @brief Sentinel value for empty list / end of list */
#define BUDDY_NIL           0xFFFFFFFFU

/** @brief Buddy node pool (static — no heap allocation needed) */
static buddy_node_t g_buddy_pool[BUDDY_POOL_SIZE];
static uint32_t     g_buddy_pool_used = 0;

/** @brief Per-order free list heads */
static uint32_t     g_buddy_free_head[VOS3_BUDDY_MAX_ORDER];
static uint32_t     g_buddy_free_count[VOS3_BUDDY_MAX_ORDER];

/** @brief Buddy allocator lock (separate from PMM lock to reduce contention) */
static vos3_spinlock_t g_buddy_lock = VOS3_SPINLOCK_INIT;

/** @brief Buddy allocator initialized flag */
static uint32_t     g_buddy_initialized = 0;

/** @brief Buddy statistics */
static vos3_buddy_stats_t g_buddy_stats;

/* ---- Buddy Helper Functions ---- */

/**
 * @brief Get the minimum order that can hold `count` pages
 * @param[in] count Number of pages
 * @return Order (0-12), or VOS3_BUDDY_MAX_ORDER if too large
 */
static uint8_t buddy_order_for_count(size_t count)
{
    if (count == 0) return 0;
    if (count == 1) return 0;

    /* Find smallest power of 2 >= count */
    uint8_t order = 0;
    size_t size = 1;
    while (size < count && order < VOS3_BUDDY_MAX_ORDER - 1) {
        size <<= 1;
        order++;
    }
    return order;
}

/**
 * @brief Get the page count for a given order
 * @param[in] order Buddy order (0-12)
 * @return Number of pages (2^order)
 */
static inline size_t buddy_order_pages(uint8_t order)
{
    return (size_t)1U << order;
}

/**
 * @brief Get the buddy page index for a block
 *
 * For a block at page_idx of order `order`, the buddy is at
 * page_idx XOR (1 << order).
 *
 * @param[in] page_idx Starting page of the block
 * @param[in] order    Block order
 * @return Starting page of the buddy block
 */
static inline size_t buddy_partner(size_t page_idx, uint8_t order)
{
    return page_idx ^ ((size_t)1U << order);
}

/**
 * @brief Allocate a node from the buddy pool
 * @return Node index, or BUDDY_NIL if pool exhausted
 */
static uint32_t buddy_node_alloc(void)
{
    if (g_buddy_pool_used >= BUDDY_POOL_SIZE) {
        return BUDDY_NIL;
    }
    uint32_t idx = g_buddy_pool_used++;
    g_buddy_pool[idx].next    = BUDDY_NIL;
    g_buddy_pool[idx].on_list = 0;
    return idx;
}

/**
 * @brief Add a free block to the free list for a given order
 * @param[in] page_idx Starting page of the free block
 * @param[in] order    Block order
 * @return 0 on success, -1 if pool exhausted
 */
static int buddy_list_add(size_t page_idx, uint8_t order)
{
    if (order >= VOS3_BUDDY_MAX_ORDER) return -1;

    uint32_t idx = buddy_node_alloc();
    if (idx == BUDDY_NIL) return -1;

    g_buddy_pool[idx].page_idx = page_idx;
    g_buddy_pool[idx].on_list  = 1;
    g_buddy_pool[idx].next     = g_buddy_free_head[order];
    g_buddy_free_head[order]   = idx;
    g_buddy_free_count[order]++;

    return 0;
}

/**
 * @brief Remove a specific page from a free list
 * @param[in] page_idx Page to remove
 * @param[in] order    Order to search in
 * @return 0 if found and removed, -1 if not found
 */
static int buddy_list_remove(size_t page_idx, uint8_t order)
{
    if (order >= VOS3_BUDDY_MAX_ORDER) return -1;

    uint32_t *prev_ptr = &g_buddy_free_head[order];
    uint32_t cur = *prev_ptr;

    while (cur != BUDDY_NIL) {
        if (g_buddy_pool[cur].page_idx == page_idx) {
            /* Found — unlink */
            *prev_ptr = g_buddy_pool[cur].next;
            g_buddy_pool[cur].on_list = 0;
            g_buddy_pool[cur].next = BUDDY_NIL;
            g_buddy_free_count[order]--;
            return 0;
        }
        prev_ptr = &g_buddy_pool[cur].next;
        cur = *prev_ptr;
    }

    return -1;  /* Not found */
}

/**
 * @brief Pop the first block from a free list
 * @param[in] order Order to pop from
 * @return Page index of the popped block, or (size_t)-1 if empty
 */
static size_t buddy_list_pop(uint8_t order)
{
    if (order >= VOS3_BUDDY_MAX_ORDER) return (size_t)-1;

    uint32_t head = g_buddy_free_head[order];
    if (head == BUDDY_NIL) return (size_t)-1;

    size_t page_idx = g_buddy_pool[head].page_idx;
    g_buddy_free_head[order] = g_buddy_pool[head].next;
    g_buddy_pool[head].on_list = 0;
    g_buddy_pool[head].next = BUDDY_NIL;
    g_buddy_free_count[order]--;

    return page_idx;
}

/**
 * @brief Check if a contiguous range of pages is free in the bitmap
 * @param[in] start Starting page index
 * @param[in] count Number of pages to check
 * @return 1 if all pages are free, 0 otherwise
 */
static int buddy_range_is_free(size_t start, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        if (start + i >= g_pmm.total_pages) return 0;
        if (pmm_bitmap_test(start + i)) return 0;
    }
    return 1;
}

/**
 * @brief Mark a contiguous range of pages as allocated in the bitmap
 * @param[in] start Starting page index
 * @param[in] count Number of pages
 * @return 0 on success, -1 if any page was already stolen by lock-free path
 *
 * @details Checks pmm_bitmap_set() return value to detect pages stolen by
 *          the lock-free single-page allocator between buddy list pop and
 *          bitmap claim. On collision, undoes all already-claimed pages.
 *          (Phase 5.5H hardening — K-C2b fix)
 */
static int buddy_range_alloc(size_t start, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        int was_set = pmm_bitmap_set(start + i);
        if (was_set) {
            /* Page stolen by lock-free alloc — undo and fail */
            for (size_t j = 0; j < i; j++) {
                pmm_bitmap_clear(start + j);
                vos3_pmm_zone_t zone = pmm_page_to_zone(start + j);
                pmm_stats_free(zone);
            }
            return -1;
        }
        vos3_pmm_zone_t zone = pmm_page_to_zone(start + i);
        pmm_stats_alloc(zone);
    }
    for (size_t i = 0; i < count; i++)
        if (start + i < g_pmm.refcount_size)
            vos3_atomic_store32(&g_pmm.refcount[start + i], 1U);
    return 0;
}

/**
 * @brief Mark a contiguous range of pages as free in the bitmap
 * @param[in] start Starting page index
 * @param[in] count Number of pages
 */
static void buddy_range_free(size_t start, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        /* Caller exclusively owns this extent. Clear refs before publishing
         * free bitmap bits to lock-free allocation. */
        if (start + i < g_pmm.refcount_size)
            vos3_atomic_store32(&g_pmm.refcount[start + i], 0U);
        int was_set = pmm_bitmap_clear(start + i);
        if (was_set) {
            vos3_pmm_zone_t zone = pmm_page_to_zone(start + i);
            pmm_stats_free(zone);
        }
    }
}

/* ============================================================================
 * BUDDY PUBLIC API
 * ============================================================================ */

int vos3_pmm_buddy_init(void)
{
    if (g_pmm.initialized == 0U) {
        return VOS3_PMM_ERR_NOTINIT;
    }

    if (g_buddy_initialized != 0U) {
        return VOS3_PMM_OK;  /* Already initialized */
    }

    vos3_spinlock_acquire(&g_buddy_lock);

    /* Initialize free lists */
    for (uint8_t o = 0; o < VOS3_BUDDY_MAX_ORDER; o++) {
        g_buddy_free_head[o]  = BUDDY_NIL;
        g_buddy_free_count[o] = 0;
    }

    g_buddy_pool_used = 0;
    memset(&g_buddy_stats, 0, sizeof(g_buddy_stats));

    /* Scan bitmap to build initial free lists.
     * Start from the highest order and work down to minimize fragmentation.
     * For each order, scan the bitmap at that alignment stride. */
    for (int order = (int)VOS3_BUDDY_MAX_ORDER - 1; order >= 0; order--) {
        size_t block_pages = buddy_order_pages((uint8_t)order);
        size_t align_mask = block_pages - 1;

        /* Start scanning from first aligned page after page 0 (reserved) */
        size_t start = block_pages;  /* Skip page 0 region */
        if (start == 0) start = block_pages;

        for (size_t page = start; page + block_pages <= g_pmm.total_pages;
             page += block_pages) {
            /* Check alignment */
            if ((page & align_mask) != 0) continue;

            /* Check if entire block is free */
            if (buddy_range_is_free(page, block_pages)) {
                if (buddy_list_add(page, (uint8_t)order) == 0) {
                    /* Mark pages as allocated in bitmap so they aren't
                     * double-counted by lower orders. They'll be freed
                     * back to bitmap when actually allocated by a caller. */
                    /* Actually, leave them free in bitmap — buddy tracks
                     * the free state. When buddy alloc hands them out,
                     * we set the bitmap bits. When buddy free returns them,
                     * we clear the bitmap bits. */

                    /* Skip this range for lower orders */
                    /* (The outer loop stride already advances past it) */
                }
            }

            /* Don't exhaust the node pool */
            if (g_buddy_pool_used >= BUDDY_POOL_SIZE - 64) {
                break;
            }
        }

        if (g_buddy_pool_used >= BUDDY_POOL_SIZE - 64) {
            break;
        }
    }

    g_buddy_initialized = 1;

    vos3_spinlock_release(&g_buddy_lock);

    /* Log summary */
    uint32_t total_blocks = 0;
    for (uint8_t o = 0; o < VOS3_BUDDY_MAX_ORDER; o++) {
        if (g_buddy_free_count[o] > 0) {
            VOS3_INFO("PMM buddy: order %u (%uKB): %u free blocks",
                      o, (unsigned)(buddy_order_pages(o) * 4),
                      g_buddy_free_count[o]);
        }
        total_blocks += g_buddy_free_count[o];
    }
    VOS3_INFO("PMM buddy: initialized with %u free blocks, pool %u/%u nodes",
              total_blocks, g_buddy_pool_used, BUDDY_POOL_SIZE);

    return VOS3_PMM_OK;
}

/**
 * @brief Count free blocks of Order 9+ (HugePage-capable, 2MB+)
 * @return Total free blocks across orders 9..12
 * @note Must be called with g_buddy_lock held
 */
static uint32_t buddy_count_order9_plus(void)
{
    uint32_t total = 0;
    for (uint8_t o = VOS3_BUDDY_HP_ORDER; o < VOS3_BUDDY_MAX_ORDER; o++) {
        total += g_buddy_free_count[o];
    }
    return total;
}

uintptr_t vos3_pmm_buddy_alloc(size_t count, vos3_pmm_flags_t flags)
{
    if (g_pmm.initialized == 0U || count == 0) {
        return 0U;
    }

    /* Single page: use the fast lock-free bitmap path */
    if (count == 1) {
        return vos3_pmm_alloc(flags);
    }

    /* If buddy not initialized, fall back to bitmap scan */
    if (g_buddy_initialized == 0U) {
        return vos3_pmm_alloc_pages(count, flags);
    }

    uint8_t order = buddy_order_for_count(count);
    if (order >= VOS3_BUDDY_MAX_ORDER) {
        /* Too large for buddy — fall back */
        g_buddy_stats.fallback_count++;
        return vos3_pmm_alloc_pages(count, flags);
    }

    vos3_spinlock_acquire(&g_buddy_lock);

    /* Find the smallest available order >= requested */
    size_t page_idx = (size_t)-1;
    uint8_t found_order = order;

    for (uint8_t o = order; o < VOS3_BUDDY_MAX_ORDER; o++) {
        if (g_buddy_free_count[o] > 0) {
            page_idx = buddy_list_pop(o);
            if (page_idx != (size_t)-1) {
                found_order = o;
                break;
            }
        }
    }

    if (page_idx == (size_t)-1) {
        /* No block found — fall back to bitmap scan */
        vos3_spinlock_release(&g_buddy_lock);
        g_buddy_stats.fallback_count++;
        return vos3_pmm_alloc_pages(count, flags);
    }

    /* Phase 7.4 Anti-Fragmentation Guard:
     * If the requested order is below HugePage order (9) but we found a
     * block at Order 9+, check whether splitting would deplete
     * HugePage-capable blocks below the safety minimum.
     * If so, return the block to its free list and fall back to the
     * bitmap scanner, which can satisfy small requests from fragmented
     * regions without destroying contiguous 2MB blocks. */
    if (order < VOS3_BUDDY_HP_ORDER && found_order >= VOS3_BUDDY_HP_ORDER) {
        uint32_t hp_avail = buddy_count_order9_plus();
        /* hp_avail excludes the popped block; if fewer than MIN
         * remain on the free lists, refuse the split (conservative) */
        if (hp_avail < VOS3_HP_ANTIFRAG_MIN) {
            buddy_list_add(page_idx, found_order);
            vos3_spinlock_release(&g_buddy_lock);
            g_buddy_stats.fallback_count++;
            return vos3_pmm_alloc_pages(count, flags);
        }
    }

    /* Split larger blocks down to requested order */
    while (found_order > order) {
        found_order--;
        size_t buddy_page = page_idx + buddy_order_pages(found_order);
        buddy_list_add(buddy_page, found_order);
        g_buddy_stats.split_count++;
    }

    /* Mark pages as allocated in the bitmap.
     * buddy_range_alloc returns -1 if a lock-free single-page alloc
     * stole a page between our list pop and bitmap claim.
     * On collision, return the block to the free list and fall back
     * to the bitmap scanner. (Phase 5.5H — K-C2b fix) */
    size_t alloc_pages = buddy_order_pages(order);
    if (buddy_range_alloc(page_idx, alloc_pages) != 0) {
        /* Collision: return block to free list, fall back */
        buddy_list_add(page_idx, order);
        vos3_spinlock_release(&g_buddy_lock);
        g_buddy_stats.fallback_count++;
        return vos3_pmm_alloc_pages(count, flags);
    }

    g_buddy_stats.alloc_count++;

    vos3_spinlock_release(&g_buddy_lock);

    uintptr_t addr = vos3_pmm_page_to_addr(page_idx);

    /* Zero if requested */
    if ((flags & VOS3_PMM_FLAG_ZERO) != 0U) {
        void *virt = vos3_phys_to_virt(addr);
        uint64_t *ptr = (uint64_t *)virt;
        size_t qwords = (alloc_pages * VOS3_PAGE_SIZE) / sizeof(uint64_t);
        for (size_t i = 0; i < qwords; i++) {
            ptr[i] = 0ULL;
        }
    }

    return addr;
}

void vos3_pmm_buddy_free(uintptr_t addr, size_t count)
{
    if (g_pmm.initialized == 0U || count == 0) {
        return;
    }

    /* Single page: use fast path */
    if (count == 1) {
        vos3_pmm_free(addr);
        return;
    }

    /* If buddy not initialized, fall back to page-by-page free */
    if (g_buddy_initialized == 0U) {
        vos3_pmm_free_pages(addr, count);
        return;
    }

    size_t page_idx = vos3_pmm_addr_to_page(addr);
    uint8_t order = buddy_order_for_count(count);

    if (order >= VOS3_BUDDY_MAX_ORDER) {
        /* Too large for buddy — free page by page */
        vos3_pmm_free_pages(addr, count);
        return;
    }

    /* Acquire lock BEFORE bitmap changes to prevent TOCTOU race on SMP-2.
     * Without this, another CPU could see stale bitmap state during
     * coalescing, leading to double-merge corruption.
     * (Genesis Chaos Audit 2026-04-08, Track 1 Finding 1) */
    vos3_spinlock_acquire(&g_buddy_lock);

    /* Free pages in the bitmap (now under lock) */
    size_t block_pages = buddy_order_pages(order);
    buddy_range_free(page_idx, block_pages);

    /* Attempt to coalesce with buddy blocks */
    size_t current_page = page_idx;
    uint8_t current_order = order;

    while (current_order < VOS3_BUDDY_MAX_ORDER - 1) {
        size_t buddy_page = buddy_partner(current_page, current_order);

        /* Check if buddy is free and on the free list */
        size_t buddy_pages = buddy_order_pages(current_order);
        if (buddy_page + buddy_pages > g_pmm.total_pages) {
            break;  /* Buddy out of range */
        }

        /* Check if buddy is entirely free in bitmap */
        if (!buddy_range_is_free(buddy_page, buddy_pages)) {
            break;  /* Buddy is (partially) allocated */
        }

        /* Try to remove buddy from its free list */
        if (buddy_list_remove(buddy_page, current_order) != 0) {
            break;  /* Buddy not on free list (may be tracked differently) */
        }

        /* Merge: take the lower address as the new block start */
        if (buddy_page < current_page) {
            current_page = buddy_page;
        }

        current_order++;
        g_buddy_stats.merge_count++;
    }

    /* Add merged block to free list */
    buddy_list_add(current_page, current_order);
    g_buddy_stats.free_count++;

    vos3_spinlock_release(&g_buddy_lock);
}

void vos3_pmm_buddy_get_stats(vos3_buddy_stats_t *stats)
{
    if (stats == NULL) return;

    vos3_spinlock_acquire(&g_buddy_lock);
    *stats = g_buddy_stats;
    for (uint8_t o = 0; o < VOS3_BUDDY_MAX_ORDER; o++) {
        stats->free_blocks[o] = g_buddy_free_count[o];
    }
    vos3_spinlock_release(&g_buddy_lock);
}

/* ============================================================================
 * FRAGMENTATION SCORE — Phase 7.4 Anti-Fragmentation Observability
 * ============================================================================ */

void vos3_pmm_frag_score(uint32_t *hp_blocks_avail,
                         uint32_t *hp_pool_free,
                         uint32_t *frag_pct)
{
    /* Count Order-9+ buddy blocks (HugePage-capable contiguous regions) */
    vos3_spinlock_acquire(&g_buddy_lock);
    uint32_t hp_blocks = buddy_count_order9_plus();

    uint64_t total_free_buddy_pages = 0;
    uint64_t hp_capable_pages = 0;
    for (uint8_t o = 0; o < VOS3_BUDDY_MAX_ORDER; o++) {
        uint64_t pages_at_order = (uint64_t)g_buddy_free_count[o]
                                  * buddy_order_pages(o);
        total_free_buddy_pages += pages_at_order;
        if (o >= VOS3_BUDDY_HP_ORDER) {
            hp_capable_pages += pages_at_order;
        }
    }
    vos3_spinlock_release(&g_buddy_lock);

    /* Count unused HugePage pool entries */
    vos3_irqflags_t f = vos3_irq_save();
    vos3_spinlock_acquire(&g_hugepage_lock);
    uint32_t pool_free = g_hugepage_pool_count - g_hugepage_pool_used;
    vos3_spinlock_release(&g_hugepage_lock);
    vos3_irq_restore(f);

    if (hp_blocks_avail) *hp_blocks_avail = hp_blocks;
    if (hp_pool_free)    *hp_pool_free    = pool_free;

    /* Fragmentation percentage:
     *   0% = all buddy free pages are in Order-9+ blocks (no fragmentation)
     * 100% = all buddy free pages are in Order-0 blocks (fully fragmented)
     * Measures what fraction of free buddy pages CANNOT form HugePages. */
    if (frag_pct) {
        if (total_free_buddy_pages == 0) {
            *frag_pct = 100U;
        } else {
            *frag_pct = (uint32_t)(100ULL -
                         (hp_capable_pages * 100ULL / total_free_buddy_pages));
        }
    }
}
