/**
 * @file heap.h
 * @brief VOS3 Kernel Heap Allocator
 *
 * @details Simple slab-based kernel heap allocator with support for
 *          various object sizes. Uses PMM for backing pages.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_HEAP_H
#define VOS3_HEAP_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * HEAP CONFIGURATION
 * ============================================================================ */

/** @brief Minimum allocation size */
#define VOS3_HEAP_MIN_SIZE      ((size_t)16U)

/** @brief Maximum slab object size */
#define VOS3_HEAP_MAX_SLAB_SIZE ((size_t)2048U)

/** @brief Slab size classes */
#define VOS3_HEAP_SLAB_CLASSES  ((size_t)8U)

/** @brief Heap alignment */
#define VOS3_HEAP_ALIGN         ((size_t)16U)

/** @brief Heap magic for validation */
#define VOS3_HEAP_MAGIC         ((uint32_t)0x48454150U)  /* "HEAP" */

/** @brief Slab free-slot sentinel — written into every free slot at offset 8;
 *         validated on alloc to detect forged freelist entries (K-CRIT-2). */
#define VOS3_HEAP_SLAB_OBJ_FREE_MAGIC  ((uint64_t)0xDEADBEEFCAFEBABEULL)

/* ============================================================================
 * ALLOCATION FLAGS
 * ============================================================================ */

/** @brief Allocation flags */
typedef enum vos3_heap_flags {
    VOS3_HEAP_FLAG_NONE     = 0U,
    VOS3_HEAP_FLAG_ZERO     = (1U << 0),    /**< Zero memory */
    VOS3_HEAP_FLAG_NOWAIT   = (1U << 1),    /**< Don't wait if unavailable */
    VOS3_HEAP_FLAG_DMA      = (1U << 2),    /**< DMA-compatible memory */
} vos3_heap_flags_t;

/* ============================================================================
 * HEAP STATISTICS
 * ============================================================================ */

/**
 * @brief Heap statistics
 */
typedef struct vos3_heap_stats {
    uint64_t total_allocated;   /**< Total bytes currently allocated */
    uint64_t total_freed;       /**< Total bytes freed (lifetime) */
    uint64_t alloc_count;       /**< Current allocation count */
    uint64_t free_count;        /**< Free count (lifetime) */
    uint64_t pages_used;        /**< Pages used by heap */
    uint64_t slab_allocs[VOS3_HEAP_SLAB_CLASSES];  /**< Per-class allocations */
    uint64_t large_allocs;      /**< Large (page-based) allocations */
} vos3_heap_stats_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the kernel heap
 * @return 0 on success, negative error code on failure
 */
int vos3_heap_init(void);

/**
 * @brief Allocate memory from the heap
 * @param[in] size Size in bytes
 * @return Pointer to allocated memory, or NULL on failure
 */
void* vos3_kmalloc(size_t size);

/**
 * @brief Allocate zeroed memory from the heap
 * @param[in] size Size in bytes
 * @return Pointer to zeroed memory, or NULL on failure
 */
void* vos3_kzalloc(size_t size);

/**
 * @brief Allocate aligned memory from the heap
 * @param[in] size Size in bytes
 * @param[in] align Alignment (must be power of 2)
 * @return Pointer to aligned memory, or NULL on failure
 */
void* vos3_kmalloc_aligned(size_t size, size_t align);

/**
 * @brief Allocate memory with flags
 * @param[in] size Size in bytes
 * @param[in] flags Allocation flags
 * @return Pointer to allocated memory, or NULL on failure
 */
void* vos3_kmalloc_flags(size_t size, vos3_heap_flags_t flags);

/**
 * @brief Reallocate memory
 * @param[in] ptr Original pointer (may be NULL)
 * @param[in] new_size New size
 * @return Pointer to reallocated memory, or NULL on failure
 */
void* vos3_krealloc(void* ptr, size_t new_size);

/**
 * @brief Free allocated memory
 * @param[in] ptr Pointer to free (may be NULL)
 */
void vos3_kfree(void* ptr);

/**
 * @brief Get size of allocation
 * @param[in] ptr Pointer to allocation
 * @return Size of allocation, or 0 if invalid
 */
size_t vos3_ksize(const void* ptr);

/**
 * @brief Get heap statistics
 * @param[out] stats Statistics output
 */
void vos3_heap_get_stats(vos3_heap_stats_t* stats);

/**
 * @brief Print heap statistics
 */
void vos3_heap_print_stats(void);

/**
 * @brief Reclaim empty slab pages back to PMM
 *
 * Scans all 8 size classes and destroys empty slabs, keeping one
 * per class as a hot reserve.  Should be called from the PMM slow
 * path when free pages drop below 10%.
 *
 * @return Number of pages reclaimed
 */
size_t vos3_heap_shrink(void);

/**
 * @brief Check heap integrity
 * @return 0 if healthy, negative error code if corrupted
 */
int vos3_heap_check(void);

/* ============================================================================
 * SLAB ALLOCATOR
 * ============================================================================ */

/**
 * @brief Slab cache structure (opaque)
 */
typedef struct vos3_slab_cache vos3_slab_cache_t;

/**
 * @brief Create a slab cache
 * @param[in] name Cache name (for debugging)
 * @param[in] obj_size Object size
 * @param[in] align Object alignment
 * @return Cache pointer, or NULL on failure
 */
vos3_slab_cache_t* vos3_slab_create(const char* name, size_t obj_size, size_t align);

/**
 * @brief Destroy a slab cache
 * @param[in] cache Cache to destroy
 */
void vos3_slab_destroy(vos3_slab_cache_t* cache);

/**
 * @brief Allocate object from slab cache
 * @param[in] cache Slab cache
 * @return Pointer to object, or NULL on failure
 */
void* vos3_slab_alloc(vos3_slab_cache_t* cache);

/**
 * @brief Free object to slab cache
 * @param[in] cache Slab cache
 * @param[in] obj Object to free
 */
void vos3_slab_free(vos3_slab_cache_t* cache, void* obj);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_HEAP_OK            (0)
#define VOS3_HEAP_ERR_NOMEM     (-1)    /**< Out of memory */
#define VOS3_HEAP_ERR_INVALID   (-2)    /**< Invalid argument */
#define VOS3_HEAP_ERR_CORRUPT   (-3)    /**< Heap corruption detected */
#define VOS3_HEAP_ERR_NOTINIT   (-4)    /**< Heap not initialized */
#define VOS3_HEAP_ERR_DOUBLE    (-5)    /**< Double free detected */

#ifdef __cplusplus
}
#endif

#endif /* VOS3_HEAP_H */
