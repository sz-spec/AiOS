/**
 * @file heap.c
 * @brief VOS3 Kernel Heap Allocator Implementation
 *
 * @details Simple free-list based allocator with slab caches for
 *          common sizes. Thread-safe with spinlocks.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/heap.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/console.h"
#include "../../include/vos/entropy.h"

/* ============================================================================
 * INTERNAL STRUCTURES
 * ============================================================================ */

/**
 * @brief Allocation header (prepended to each allocation)
 */
typedef struct __attribute__((aligned(16))) vos3_alloc_header {
    uint32_t magic;         /**< Magic number for validation */
    uint32_t flags;         /**< Allocation flags */
    size_t   size;          /**< Allocation size (excluding header) */
    size_t   align;         /**< Original alignment */
} vos3_alloc_header_t;

/**
 * @brief Free block in free list
 */
typedef struct vos3_free_block {
    struct vos3_free_block* next;   /**< Next free block */
    size_t size;                     /**< Block size */
} vos3_free_block_t;

/**
 * @brief Slab structure (one page)
 */
#define VOS3_SLAB_MAGIC 0x534C4142U  /* "SLAB" */

typedef struct vos3_slab {
    uint32_t magic;                 /**< VOS3_SLAB_MAGIC for validation */
    struct vos3_slab* next;         /**< Next slab in list */
    struct vos3_slab_cache* cache;  /**< Parent cache */
    uint32_t free_count;            /**< Free objects in slab */
    uint32_t total_count;           /**< Total objects in slab */
    void* free_list;                /**< Free object list */
} vos3_slab_t;

/**
 * @brief Slab cache structure
 */
struct vos3_slab_cache {
    const char* name;               /**< Cache name */
    size_t obj_size;                /**< Object size */
    size_t align;                   /**< Object alignment */
    size_t slab_size;               /**< Slab size in pages */
    vos3_slab_t* partial;           /**< Partially filled slabs */
    vos3_slab_t* full;              /**< Full slabs */
    vos3_slab_t* empty;             /**< Empty slabs */
    uint64_t alloc_count;           /**< Allocation count */
    uint64_t free_count;            /**< Free count */
    vos3_spinlock_t lock;           /**< Cache lock */
};

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Size classes for slab allocator (16, 32, 64, 128, 256, 512, 1024, 2048) */
static const size_t g_slab_sizes[VOS3_HEAP_SLAB_CLASSES] = {
    16U, 32U, 64U, 128U, 256U, 512U, 1024U, 2048U
};

/** @brief Slab caches for common sizes */
static vos3_slab_cache_t* g_size_caches[VOS3_HEAP_SLAB_CLASSES];

/** @brief Heap statistics */
/* [OLYMPUS-FIX P-12] cache-line-aligned to stop two adjacent
 * fetch_add64 sites (pages_used + large_allocs in alloc_pages) from
 * sharing a line with anything updated on a different CPU. The struct
 * itself is 56-72 bytes today; aligned-64 pads it without enlarging. */
static vos3_heap_stats_t g_heap_stats __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/** @brief Heap lock */
static vos3_spinlock_t g_heap_lock = VOS3_SPINLOCK_INIT;

/** @brief Heap initialization flag */
static volatile uint32_t g_heap_initialized = 0U;

/** @brief Random cookie for XOR-obfuscating slab freelist pointers.
 *  Initialized once from hardware entropy in vos3_heap_init().
 *  Per CONFIG_SLAB_FREELIST_HARDENED: prevents use-after-free exploitation. */
static uintptr_t g_slab_freelist_cookie = 0U;

/* K-CRIT-2 (v20.6 → restored 2026-05-02): per-object integrity magic placed
 * at offset 8 in every freed slab slot. Validated on alloc; an attacker who
 * corrupts a freed slot's first 16 bytes (next-pointer + magic) is detected
 * before the next-pointer is dereferenced. Pairs with the XOR cookie above:
 * cookie hides the link target, magic authenticates that the slot is free.
 *
 * Boot-safety contract: every free slot in every newly-built free list is
 * stamped with this magic in create_slab() BEFORE the first alloc runs.
 * vos3_slab_free() re-stamps when returning to the free list.
 *
 * vos3_slab_create() enforces a 16-byte minimum object size so every freed
 * slot has room for (next_pointer @ +0, integrity_magic @ +8). 16-byte
 * minimum matches the smallest entry in g_slab_sizes already.
 */
#define SLAB_OBJ_FREE_MAGIC 0xDEADBEEFCAFEBABEULL

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Encode a freelist pointer with location-based XOR obfuscation.
 * @param ptr     The raw next-pointer to encode
 * @param location Address of the field storing this pointer (provides uniqueness)
 * @return Obfuscated pointer value
 */
static inline void* freelist_encode(void* ptr, void* location)
{
    return (void*)((uintptr_t)ptr ^ g_slab_freelist_cookie ^ (uintptr_t)location);
}

/**
 * @brief Decode a freelist pointer (inverse of encode — XOR is self-inverse).
 */
static inline void* freelist_decode(void* encoded, void* location)
{
    return (void*)((uintptr_t)encoded ^ g_slab_freelist_cookie ^ (uintptr_t)location);
}

/**
 * @brief Round up to alignment
 */
static inline size_t align_up(size_t value, size_t align)
{
    return (value + align - 1U) & ~(align - 1U);
}

/**
 * @brief Get size class index for size
 */
static int get_size_class(size_t size)
{
    for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
        if (size <= g_slab_sizes[i]) {
            return (int)i;
        }
    }
    return -1;  /* Too large for slab */
}

/**
 * @brief Allocate pages for large allocation
 */
static void* alloc_pages(size_t size)
{
    /* Guard against integer overflow in page count calculation */
    if (size > SIZE_MAX - sizeof(vos3_alloc_header_t) - VOS3_PAGE_SIZE) {
        return NULL;
    }
    size_t pages = (size + sizeof(vos3_alloc_header_t) + VOS3_PAGE_SIZE - 1U) /
                   VOS3_PAGE_SIZE;

    /* Allocate contiguous physical pages */
    uintptr_t phys = vos3_pmm_alloc_pages(pages, VOS3_PMM_FLAG_ZERO);
    if (phys == 0U) {
        return NULL;
    }

    /* Get virtual address (using direct mapping) */
    void* virt = (void*)vos3_phys_to_virt(phys);

    /* Set up header */
    vos3_alloc_header_t* header = (vos3_alloc_header_t*)virt;
    header->magic = VOS3_HEAP_MAGIC;
    header->flags = VOS3_HEAP_FLAG_NONE;
    header->size = pages * VOS3_PAGE_SIZE - sizeof(vos3_alloc_header_t);
    header->align = VOS3_PAGE_SIZE;

    vos3_atomic_fetch_add64(&g_heap_stats.pages_used, (uint64_t)pages);
    vos3_atomic_fetch_add64(&g_heap_stats.large_allocs, 1ULL);

    return (void*)(header + 1);
}

/**
 * @brief Free pages from large allocation
 */
static void free_pages(vos3_alloc_header_t* header)
{
    size_t total_size = header->size + sizeof(vos3_alloc_header_t);
    size_t pages = (total_size + VOS3_PAGE_SIZE - 1U) / VOS3_PAGE_SIZE;

    uintptr_t phys = vos3_virt_to_phys((const void*)header);
    vos3_pmm_free_pages(phys, pages);

    vos3_atomic_fetch_sub64(&g_heap_stats.pages_used, (uint64_t)pages);
    vos3_atomic_fetch_sub64(&g_heap_stats.large_allocs, 1ULL);
}

/* ============================================================================
 * SLAB ALLOCATOR
 * ============================================================================ */

/**
 * @brief Create a new slab
 */
static vos3_slab_t* create_slab(vos3_slab_cache_t* cache)
{
    /* Allocate page for slab */
    uintptr_t phys = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys == 0U) {
        return NULL;
    }

    vos3_slab_t* slab = (vos3_slab_t*)vos3_phys_to_virt(phys);

    /* Calculate object layout */
    size_t header_size = align_up(sizeof(vos3_slab_t), cache->align);
    size_t obj_size = align_up(cache->obj_size, cache->align);
    size_t available = VOS3_PAGE_SIZE - header_size;
    uint32_t count = (uint32_t)(available / obj_size);

    slab->magic = VOS3_SLAB_MAGIC;
    slab->next = NULL;
    slab->cache = cache;
    slab->free_count = count;
    slab->total_count = count;
    slab->free_list = NULL;

    /* Build free list (XOR-obfuscated per CONFIG_SLAB_FREELIST_HARDENED).
     * K-CRIT-2: every slot also stamps SLAB_OBJ_FREE_MAGIC at offset 8 so
     * vos3_slab_alloc() can validate the slot was genuinely free before
     * decoding the next-pointer (defense against forged freelists). */
    uint8_t* obj_start = (uint8_t*)slab + header_size;
    for (uint32_t i = 0U; i < count; i++) {
        void** obj = (void**)(obj_start + (i * obj_size));
        *obj = freelist_encode(slab->free_list, obj);
        *((uint64_t*)((uint8_t*)obj + sizeof(void*))) = SLAB_OBJ_FREE_MAGIC;
        slab->free_list = obj;
    }

    vos3_atomic_fetch_add64(&g_heap_stats.pages_used, 1ULL);

    return slab;
}

/**
 * @brief Destroy a slab
 */
static void destroy_slab(vos3_slab_t* slab)
{
    uintptr_t phys = vos3_virt_to_phys((const void*)slab);
    vos3_pmm_free(phys);
    vos3_atomic_fetch_sub64(&g_heap_stats.pages_used, 1ULL);
}

vos3_slab_cache_t* vos3_slab_create(const char* name, size_t obj_size, size_t align)
{
    if (obj_size == 0U) {
        return NULL;
    }

    if (align == 0U) {
        align = VOS3_HEAP_ALIGN;
    }

    /* Allocate cache structure */
    uintptr_t phys = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys == 0U) {
        return NULL;
    }

    vos3_slab_cache_t* cache = (vos3_slab_cache_t*)vos3_phys_to_virt(phys);

    cache->name = name;
    /* K-CRIT-2: bump min obj_size to 16 so every freed slot has room for
     * (next_pointer @ +0, integrity_magic @ +8). 16-byte minimum matches
     * the smallest entry in g_slab_sizes already. */
    {
        size_t min_obj = 16U;
        size_t requested = obj_size < sizeof(void*) ? sizeof(void*) : obj_size;
        cache->obj_size = requested < min_obj ? min_obj : requested;
    }
    cache->align = align;
    cache->slab_size = 1U;  /* One page per slab */
    cache->partial = NULL;
    cache->full = NULL;
    cache->empty = NULL;
    cache->alloc_count = 0U;
    cache->free_count = 0U;
    cache->lock = VOS3_SPINLOCK_INIT;

    return cache;
}

void vos3_slab_destroy(vos3_slab_cache_t* cache)
{
    if (cache == NULL) {
        return;
    }

    vos3_spinlock_acquire(&cache->lock);

    /* Free all slabs */
    vos3_slab_t* slab;

    slab = cache->partial;
    while (slab != NULL) {
        vos3_slab_t* next = slab->next;
        destroy_slab(slab);
        slab = next;
    }

    slab = cache->full;
    while (slab != NULL) {
        vos3_slab_t* next = slab->next;
        destroy_slab(slab);
        slab = next;
    }

    slab = cache->empty;
    while (slab != NULL) {
        vos3_slab_t* next = slab->next;
        destroy_slab(slab);
        slab = next;
    }

    vos3_spinlock_release(&cache->lock);

    /* Free cache structure */
    uintptr_t phys = vos3_virt_to_phys((const void*)cache);
    vos3_pmm_free(phys);
}

void* vos3_slab_alloc(vos3_slab_cache_t* cache)
{
    if (cache == NULL) {
        return NULL;
    }

    vos3_spinlock_acquire(&cache->lock);

    vos3_slab_t* slab = cache->partial;

    if (slab == NULL) {
        /* Try empty list */
        slab = cache->empty;
        if (slab != NULL) {
            cache->empty = slab->next;
            slab->next = cache->partial;
            cache->partial = slab;
        }
    }

    if (slab == NULL) {
        /* Create new slab */
        slab = create_slab(cache);
        if (slab == NULL) {
            vos3_spinlock_release(&cache->lock);
            return NULL;
        }
        slab->next = cache->partial;
        cache->partial = slab;
    }

    /* Allocate from slab (decode XOR-obfuscated freelist pointer).
     * K-CRIT-2: validate the per-object integrity magic at offset 8 BEFORE
     * dereferencing the next-pointer at offset 0. An attacker who corrupted
     * the freed slot's first 16 bytes is caught here instead of being
     * granted a forged-freelist primitive. */
    void* obj = slab->free_list;
    {
        uint64_t magic = *((uint64_t*)((uint8_t*)obj + sizeof(void*)));
        if (magic != SLAB_OBJ_FREE_MAGIC) {
            VOS3_ERROR("Heap: slab integrity violation at %p "
                       "(got 0x%lx, want 0x%lx)",
                       obj, (unsigned long)magic,
                       (unsigned long)SLAB_OBJ_FREE_MAGIC);
            vos3_spinlock_release(&cache->lock);
            return NULL;
        }
    }
    slab->free_list = freelist_decode(*(void**)obj, obj);
    slab->free_count--;

    /* Move to full list if needed */
    if (slab->free_count == 0U) {
        cache->partial = slab->next;
        slab->next = cache->full;
        cache->full = slab;
    }

    cache->alloc_count++;

    vos3_spinlock_release(&cache->lock);

    return obj;
}

void vos3_slab_free(vos3_slab_cache_t* cache, void* obj)
{
    if (cache == NULL || obj == NULL) {
        return;
    }

    vos3_spinlock_acquire(&cache->lock);

    /* Find which slab this object belongs to */
    uintptr_t obj_addr = (uintptr_t)obj;
    uintptr_t slab_addr = obj_addr & ~(VOS3_PAGE_SIZE - 1U);
    vos3_slab_t* slab = (vos3_slab_t*)slab_addr;

    /* Verify slab belongs to this cache */
    if (slab->cache != cache) {
        VOS3_ERROR("Heap: slab_free with wrong cache!");
        vos3_spinlock_release(&cache->lock);
        return;
    }

    /* K-HIGH-2: Validate object offset within slab to prevent UAF corruption */
    uintptr_t data_start = slab_addr + align_up(sizeof(vos3_slab_t), cache->align);
    if (obj_addr < data_start || obj_addr >= slab_addr + VOS3_PAGE_SIZE) {
        VOS3_ERROR("Heap: slab_free obj 0x%lx outside slab data region!",
                   (unsigned long)obj_addr);
        vos3_spinlock_release(&cache->lock);
        return;
    }

    /* K-HIGH-2: Double-free detection — walk freelist to check if obj is already free */
    {
        void* walk = slab->free_list;
        while (walk != NULL) {
            if (walk == obj) {
                VOS3_ERROR("Heap: slab double-free detected for obj 0x%lx!",
                           (unsigned long)obj_addr);
                vos3_spinlock_release(&cache->lock);
                return;
            }
            walk = freelist_decode(*(void**)walk, walk);
        }
    }

    /* Return object to XOR-encoded free list.
     * K-CRIT-2: re-stamp the integrity magic at offset 8 so the slot is
     * recognized as genuinely free on the next allocation. */
    *(void**)obj = freelist_encode(slab->free_list, obj);
    *((uint64_t*)((uint8_t*)obj + sizeof(void*))) = SLAB_OBJ_FREE_MAGIC;
    slab->free_list = obj;
    slab->free_count++;

    cache->free_count++;

    /* Move slab between lists if needed */
    if (slab->free_count == 1U) {
        /* Was full, now partial - remove from full list */
        vos3_slab_t** prev = &cache->full;
        while (*prev != NULL && *prev != slab) {
            prev = &(*prev)->next;
        }
        if (*prev == slab) {
            *prev = slab->next;
            slab->next = cache->partial;
            cache->partial = slab;
        }
    } else if (slab->free_count == slab->total_count) {
        /* Now empty - move to empty list */
        vos3_slab_t** prev = &cache->partial;
        while (*prev != NULL && *prev != slab) {
            prev = &(*prev)->next;
        }
        if (*prev == slab) {
            *prev = slab->next;
            slab->next = cache->empty;
            cache->empty = slab;
        }
    }

    vos3_spinlock_release(&cache->lock);
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_heap_init(void)
{
    if (g_heap_initialized != 0U) {
        return VOS3_HEAP_OK;
    }

    VOS3_INFO("Heap: Initializing kernel heap");

    /* Initialize freelist XOR cookie from hardware entropy */
    g_slab_freelist_cookie = (uintptr_t)vos3_entropy_get_u64();

    /* Initialize statistics */
    g_heap_stats.total_allocated = 0U;
    g_heap_stats.total_freed = 0U;
    g_heap_stats.alloc_count = 0U;
    g_heap_stats.free_count = 0U;
    g_heap_stats.pages_used = 0U;
    g_heap_stats.large_allocs = 0U;

    for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
        g_heap_stats.slab_allocs[i] = 0U;
    }

    /* Create slab caches for common sizes */
    for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
        char name[16];
        /* Simple name generation without snprintf */
        name[0] = 's'; name[1] = 'i'; name[2] = 'z';
        name[3] = 'e'; name[4] = '-';
        size_t sz = g_slab_sizes[i];
        size_t pos = 5U;
        if (sz >= 1000U) {
            name[pos++] = (char)('0' + (sz / 1000U));
            sz %= 1000U;
        }
        if (sz >= 100U || pos > 5U) {
            name[pos++] = (char)('0' + (sz / 100U));
            sz %= 100U;
        }
        if (sz >= 10U || pos > 5U) {
            name[pos++] = (char)('0' + (sz / 10U));
            sz %= 10U;
        }
        name[pos++] = (char)('0' + sz);
        name[pos] = '\0';

        g_size_caches[i] = vos3_slab_create(name, g_slab_sizes[i], VOS3_HEAP_ALIGN);
        if (g_size_caches[i] == NULL) {
            VOS3_ERROR("Heap: Failed to create size-%u cache", (unsigned)g_slab_sizes[i]);
            return VOS3_HEAP_ERR_NOMEM;
        }
    }

    g_heap_initialized = 1U;

    VOS3_INFO("Heap: Initialization complete (%u size classes)",
              (unsigned)VOS3_HEAP_SLAB_CLASSES);

    return VOS3_HEAP_OK;
}

void* vos3_kmalloc(size_t size)
{
    return vos3_kmalloc_flags(size, VOS3_HEAP_FLAG_NONE);
}

void* vos3_kzalloc(size_t size)
{
    return vos3_kmalloc_flags(size, VOS3_HEAP_FLAG_ZERO);
}

void* vos3_kmalloc_aligned(size_t size, size_t align)
{
    if (align == 0U || (align & (align - 1U)) != 0U) {
        return NULL;  /* Alignment must be power of 2 */
    }
    /* Minimum alignment must fit the raw pointer storage slot */
    if (align < sizeof(void*)) {
        align = sizeof(void*);
    }

    /* Overflow check before total computation */
    size_t overhead = align + sizeof(void*);
    if (size > SIZE_MAX - overhead) {
        return NULL;  /* Would overflow */
    }
    size_t total = size + overhead;

    void* raw = vos3_kmalloc(total);
    if (raw == NULL) {
        return NULL;
    }

    /* Always leave room for the raw pointer slot before the aligned address */
    uintptr_t raw_addr = (uintptr_t)raw + sizeof(void*);
    uintptr_t aligned = align_up(raw_addr, align);

    /* Store original pointer immediately before the aligned address.
     * This is always within the allocated region because we reserved
     * sizeof(void*) + align bytes of overhead. */
    ((void**)aligned)[-1] = raw;

    return (void*)aligned;
}

void vos3_kfree_aligned(void* ptr)
{
    if (ptr == NULL) {
        return;
    }
    /* Recover the raw pointer stored at ptr[-1] */
    void* raw = ((void**)ptr)[-1];
    vos3_kfree(raw);
}

void* vos3_kmalloc_flags(size_t size, vos3_heap_flags_t flags)
{
    if (g_heap_initialized == 0U || size == 0U) {
        return NULL;
    }

    void* ptr = NULL;

    /* Try slab allocator for small sizes */
    int class_idx = get_size_class(size);
    if (class_idx >= 0) {
        ptr = vos3_slab_alloc(g_size_caches[class_idx]);
        if (ptr != NULL) {
            vos3_atomic_fetch_add64(&g_heap_stats.slab_allocs[class_idx], 1ULL);
            vos3_atomic_fetch_add64(&g_heap_stats.total_allocated,
                                    (uint64_t)g_slab_sizes[class_idx]);
            vos3_atomic_fetch_add64(&g_heap_stats.alloc_count, 1ULL);

            if (flags & VOS3_HEAP_FLAG_ZERO) {
                uint8_t* p = (uint8_t*)ptr;
                for (size_t i = 0U; i < g_slab_sizes[class_idx]; i++) {
                    p[i] = 0U;
                }
            }
        }
    } else {
        /* Large allocation */
        ptr = alloc_pages(size);
        if (ptr != NULL) {
            vos3_atomic_fetch_add64(&g_heap_stats.total_allocated, (uint64_t)size);
            vos3_atomic_fetch_add64(&g_heap_stats.alloc_count, 1ULL);
        }
    }

    return ptr;
}

void* vos3_krealloc(void* ptr, size_t new_size)
{
    if (ptr == NULL) {
        return vos3_kmalloc(new_size);
    }

    if (new_size == 0U) {
        vos3_kfree(ptr);
        return NULL;
    }

    size_t old_size = vos3_ksize(ptr);
    if (old_size >= new_size) {
        return ptr;  /* Current allocation is big enough */
    }

    /* Allocate new block and copy */
    void* new_ptr = vos3_kmalloc(new_size);
    if (new_ptr == NULL) {
        return NULL;
    }

    /* Copy old data */
    uint8_t* src = (uint8_t*)ptr;
    uint8_t* dst = (uint8_t*)new_ptr;
    for (size_t i = 0U; i < old_size; i++) {
        dst[i] = src[i];
    }

    vos3_kfree(ptr);

    return new_ptr;
}

void vos3_kfree(void* ptr)
{
    if (ptr == NULL || g_heap_initialized == 0U) {
        return;
    }

    /* Check if it's a slab allocation */
    uintptr_t addr = (uintptr_t)ptr;
    uintptr_t slab_addr = addr & ~(VOS3_PAGE_SIZE - 1U);
    vos3_slab_t* slab = (vos3_slab_t*)slab_addr;

    /* Check if this looks like a slab (magic + cache pointer validation) */
    if (slab->magic == VOS3_SLAB_MAGIC && slab->cache != NULL) {
        /* Verify it's one of our caches */
        for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
            if (slab->cache == g_size_caches[i]) {
                vos3_slab_free(g_size_caches[i], ptr);
                vos3_atomic_fetch_add64(&g_heap_stats.total_freed,
                                        (uint64_t)g_slab_sizes[i]);
                vos3_atomic_fetch_add64(&g_heap_stats.free_count, 1ULL);
                return;
            }
        }
    }

    /* Large allocation - check header */
    vos3_alloc_header_t* header = ((vos3_alloc_header_t*)ptr) - 1;
    if (header->magic == VOS3_HEAP_MAGIC) {
        size_t size = header->size;
        free_pages(header);
        vos3_atomic_fetch_add64(&g_heap_stats.total_freed, (uint64_t)size);
        vos3_atomic_fetch_add64(&g_heap_stats.free_count, 1ULL);
    } else {
        VOS3_WARN("Heap: Invalid free at %p", ptr);
    }
}

size_t vos3_ksize(const void* ptr)
{
    if (ptr == NULL) {
        return 0U;
    }

    /* Check if it's a slab allocation */
    uintptr_t addr = (uintptr_t)ptr;
    uintptr_t slab_addr = addr & ~(VOS3_PAGE_SIZE - 1U);
    vos3_slab_t* slab = (vos3_slab_t*)slab_addr;

    if (slab->cache != NULL) {
        for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
            if (slab->cache == g_size_caches[i]) {
                return g_slab_sizes[i];
            }
        }
    }

    /* Large allocation */
    const vos3_alloc_header_t* header = ((const vos3_alloc_header_t*)ptr) - 1;
    if (header->magic == VOS3_HEAP_MAGIC) {
        return header->size;
    }

    return 0U;
}

void vos3_heap_get_stats(vos3_heap_stats_t* stats)
{
    if (stats == NULL) {
        return;
    }

    stats->total_allocated = vos3_atomic_load64(&g_heap_stats.total_allocated);
    stats->total_freed = vos3_atomic_load64(&g_heap_stats.total_freed);
    stats->alloc_count = vos3_atomic_load64(&g_heap_stats.alloc_count);
    stats->free_count = vos3_atomic_load64(&g_heap_stats.free_count);
    stats->pages_used = vos3_atomic_load64(&g_heap_stats.pages_used);
    stats->large_allocs = vos3_atomic_load64(&g_heap_stats.large_allocs);

    for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
        stats->slab_allocs[i] = vos3_atomic_load64(&g_heap_stats.slab_allocs[i]);
    }
}

void vos3_heap_print_stats(void)
{
    vos3_heap_stats_t stats;
    vos3_heap_get_stats(&stats);

    VOS3_INFO("Heap Statistics:");
    vos3_console_printf("  Allocated: %llu bytes (%llu allocs)\n",
                        (unsigned long long)stats.total_allocated,
                        (unsigned long long)stats.alloc_count);
    vos3_console_printf("  Freed: %llu bytes (%llu frees)\n",
                        (unsigned long long)stats.total_freed,
                        (unsigned long long)stats.free_count);
    vos3_console_printf("  In use: %llu bytes\n",
                        (unsigned long long)(stats.total_allocated - stats.total_freed));
    vos3_console_printf("  Pages: %llu (%llu KiB)\n",
                        (unsigned long long)stats.pages_used,
                        (unsigned long long)(stats.pages_used * 4ULL));
    vos3_console_printf("  Large allocs: %llu\n",
                        (unsigned long long)stats.large_allocs);
}

size_t vos3_heap_shrink(void)
{
    if (g_heap_initialized == 0U) {
        return 0U;
    }

    size_t reclaimed = 0U;

    for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
        vos3_slab_cache_t* cache = g_size_caches[i];
        if (cache == NULL) {
            continue;
        }

        vos3_spinlock_acquire(&cache->lock);

        /* Walk the empty slab list — destroy all but one (hot reserve) */
        vos3_slab_t* slab = cache->empty;
        vos3_slab_t* keep = NULL;
        uint32_t empty_count = 0U;

        /* Count empty slabs */
        vos3_slab_t* cursor = slab;
        while (cursor != NULL) {
            empty_count++;
            cursor = cursor->next;
        }

        if (empty_count > 1U) {
            /* Keep the first slab as hot reserve */
            keep = slab;
            slab = slab->next;
            keep->next = NULL;

            /* Destroy the rest */
            while (slab != NULL) {
                vos3_slab_t* next = slab->next;
                destroy_slab(slab);
                reclaimed++;
                slab = next;
            }

            cache->empty = keep;
        }

        vos3_spinlock_release(&cache->lock);
    }

    if (reclaimed > 0U) {
        VOS3_INFO("Heap: Shrink reclaimed %u slab pages", (unsigned)reclaimed);
    }

    return reclaimed;
}

int vos3_heap_check(void)
{
    /* Basic integrity check */
    if (g_heap_initialized == 0U) {
        return VOS3_HEAP_ERR_NOTINIT;
    }

    /* Verify slab caches exist */
    for (size_t i = 0U; i < VOS3_HEAP_SLAB_CLASSES; i++) {
        if (g_size_caches[i] == NULL) {
            return VOS3_HEAP_ERR_CORRUPT;
        }
    }

    return VOS3_HEAP_OK;
}
