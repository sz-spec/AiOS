/**
 * @file gpu_mem.h
 * @brief VOS3 GPU Memory Manager -- DMA Buffer Pool with Reference Counting
 *
 * @details Provides DMA-safe memory allocation for GPU command rings,
 *          streaming data buffers (framebuffers, tensors), and device
 *          memory regions.
 *
 *          Four allocation modes via flags:
 *          - COHERENT:      Normal cached mapping (CPU+GPU cache-coherent)
 *          - WRITE_COMBINE: Write-combining for high-throughput bulk transfers
 *          - DEVICE:        Strong uncacheable for MMIO/device registers
 *          - HUGEPAGE:      2MB page allocation for large contiguous buffers
 *
 *          Backed by a fixed pool of 512 DMA buffer descriptors with
 *          spinlock protection for SMP-safe concurrent access. Physical
 *          memory sourced from the PMM; virtual mappings created via the
 *          VMM at a dedicated virtual base address.
 *
 * @version 2.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.3 -- GPU Memory Subsystem
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_GPU_MEM_H
#define VOS3_GPU_MEM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * GPU MEMORY CONFIGURATION
 * ============================================================================ */

/** @brief Maximum DMA buffer descriptors in the pool */
#define VOS3_GPU_MEM_MAX_BUFS       ((uint32_t)512U)

/** @brief Virtual base address for GPU DMA mappings (after ivshmem region) */
#define VOS3_GPU_MEM_VBASE          ((uintptr_t)0xFFFF880020000000ULL)

/** @brief Maximum GPU virtual address space (1 GiB) */
#define VOS3_GPU_MEM_VSIZE          ((size_t)(1024U * 1024U * 1024U))

/* ============================================================================
 * ALLOCATION FLAGS
 * ============================================================================ */

/** @brief Cache-coherent DMA mapping (normal cached) */
#define VOS3_GPU_MEM_COHERENT       (1U << 0)

/** @brief Write-combining mapping (GPU-preferred for bulk transfers) */
#define VOS3_GPU_MEM_WRITE_COMBINE  (1U << 1)

/** @brief Device / strong uncacheable mapping (for MMIO registers) */
#define VOS3_GPU_MEM_DEVICE         (1U << 2)

/** @brief Use 2MB HugePages for the allocation */
#define VOS3_GPU_MEM_HUGEPAGE       (1U << 3)

/** @brief Buffer backed by a physical PCI BAR (no PMM allocation) */
#define VOS3_GPU_MEM_BAR_MAPPED     (1U << 4)

/* ============================================================================
 * DMA BUFFER DESCRIPTOR
 * ============================================================================ */

/**
 * @brief DMA buffer descriptor
 *
 * Represents a single DMA-visible memory allocation. The caller uses
 * @c phys_addr for GPU/DMA descriptor programming and @c virt_addr
 * for CPU-side access. Descriptors are reference-counted and returned
 * to the pool when the last reference is released.
 */
typedef struct vos3_dma_buf {
    uint64_t    phys_addr;      /**< Physical address (DMA-visible) */
    void       *virt_addr;      /**< Kernel virtual address (CPU-side) */
    uint32_t    size;           /**< Allocated size in bytes */
    uint32_t    flags;          /**< Allocation flags (VOS3_GPU_MEM_*) */
    uint32_t    refcount;       /**< Reference count (free-on-zero) */
    uint8_t     in_use;         /**< 1 if descriptor is allocated, 0 if free */
} vos3_dma_buf_t;

/* ============================================================================
 * STATISTICS
 * ============================================================================ */

/**
 * @brief GPU memory subsystem statistics
 */
typedef struct vos3_gpu_mem_stats {
    uint32_t    total_buffers;      /**< Total buffer descriptors in pool */
    uint32_t    used_buffers;       /**< Currently allocated descriptors */
    uint64_t    total_bytes;        /**< Total bytes allocated across all buffers */
    uint64_t    used_bytes;         /**< Currently allocated bytes */
    uint32_t    coherent_allocs;    /**< Lifetime count of COHERENT allocations */
    uint32_t    wc_allocs;          /**< Lifetime count of WRITE_COMBINE allocations */
} vos3_gpu_mem_stats_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize the GPU memory subsystem
 *
 * Prepares the buffer descriptor pool and virtual address bump allocator.
 * Must be called after PMM and VMM initialization.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_gpu_mem_init(void);

/**
 * @brief Allocate a DMA buffer for GPU use
 *
 * Finds a free descriptor in the pool, allocates physical pages from the
 * PMM, maps them into kernel virtual space with the appropriate caching
 * attributes, and returns a pointer to the descriptor.
 *
 * The initial reference count is set to 1.
 *
 * Caching policy is determined by @p flags:
 * - VOS3_GPU_MEM_COHERENT:      Normal cached mapping
 * - VOS3_GPU_MEM_WRITE_COMBINE: Write-combining (PAT entry 4 = WC)
 * - VOS3_GPU_MEM_DEVICE:        Strong uncacheable (PCD=1, PWT=1)
 * - VOS3_GPU_MEM_HUGEPAGE:      Use 2MB HugePages (combined with above)
 *
 * @param[in]  size     Requested size in bytes (rounded up to page boundary)
 * @param[in]  flags    Allocation flags (VOS3_GPU_MEM_*)
 * @param[out] buf_out  Pointer to descriptor pointer, set on success
 * @return 0 on success, negative error code on failure
 */
int vos3_gpu_mem_alloc(uint32_t size, uint32_t flags, vos3_dma_buf_t **buf_out);

/**
 * @brief Free a DMA buffer descriptor
 *
 * Unmaps the kernel virtual mapping, returns physical pages to the PMM,
 * and resets the descriptor. The descriptor is returned to the free pool.
 *
 * @warning This immediately frees the buffer regardless of reference count.
 *          Prefer vos3_gpu_mem_unref() for reference-counted release.
 *
 * @param[in] buf  Buffer descriptor to free
 */
void vos3_gpu_mem_free(vos3_dma_buf_t *buf);

/**
 * @brief Increment the reference count on a DMA buffer
 *
 * @param[in] buf  Buffer descriptor to reference
 * @return 0 on success, -1 if buf is NULL or not in use
 */
int vos3_gpu_mem_ref(vos3_dma_buf_t *buf);

/**
 * @brief Decrement the reference count on a DMA buffer
 *
 * If the reference count reaches zero, the buffer is automatically freed
 * (physical pages returned to PMM, virtual mapping torn down, descriptor
 * returned to pool).
 *
 * @param[in] buf  Buffer descriptor to unreference
 */
void vos3_gpu_mem_unref(vos3_dma_buf_t *buf);

/**
 * @brief Map a PCI BAR physical address into the GPU memory pool
 *
 * Phase 5 GPU Warp: Maps physical VRAM directly from a PCI device BAR
 * into the kernel's GPU memory virtual address space. Unlike alloc(),
 * this does NOT allocate pages from the PMM — it maps pre-existing
 * device memory (VRAM, NPU SRAM, etc.) at a known physical address.
 *
 * Zero-copy path: NVMe DMA → ivshmem → GPU BAR (no PMM intermediate).
 *
 * The returned descriptor is flagged VOS3_GPU_MEM_BAR_MAPPED so that
 * free/unref will unmap the VA but NOT call pmm_free_pages.
 *
 * @param[in]  bar_phys  Physical base address of the PCI BAR region
 * @param[in]  size      Size in bytes to map (rounded up to page boundary)
 * @param[in]  flags     Caching flags (DEVICE, WRITE_COMBINE, or COHERENT)
 * @param[out] buf_out   Pointer to descriptor pointer, set on success
 * @return 0 on success, negative error code on failure
 */
int vos3_gpu_mem_map_bar(uint64_t bar_phys, uint32_t size, uint32_t flags,
                          vos3_dma_buf_t **buf_out);

/**
 * @brief Retrieve GPU memory subsystem statistics
 *
 * Returns a snapshot of current allocation state taken under the pool
 * lock for consistency.
 *
 * @param[out] stats  Statistics structure to populate
 * @return 0 on success, -1 if stats is NULL
 */
int vos3_gpu_mem_get_stats(vos3_gpu_mem_stats_t *stats);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_GPU_MEM_OK             (0)
#define VOS3_GPU_MEM_ERR_INVALID    (-1)    /**< Invalid argument */
#define VOS3_GPU_MEM_ERR_NOMEM      (-2)    /**< Pool exhausted / no memory */
#define VOS3_GPU_MEM_ERR_NOTINIT    (-3)    /**< Subsystem not initialized */
#define VOS3_GPU_MEM_ERR_POOL_FULL  (-4)    /**< All 512 descriptors in use */
#define VOS3_GPU_MEM_ERR_MAP        (-5)    /**< VMM mapping failure */

#ifdef __cplusplus
}
#endif

#endif /* VOS3_GPU_MEM_H */
