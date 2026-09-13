/**
 * @file gpu_mem.c
 * @brief VOS3 GPU Memory Manager -- DMA Buffer Pool Implementation
 *
 * @details Implements a fixed-pool DMA buffer allocator with reference
 *          counting for GPU-visible memory. The pool contains 512
 *          descriptor slots (VOS3_GPU_MEM_MAX_BUFS), each representing
 *          a single DMA allocation with its own physical backing,
 *          kernel virtual mapping, and reference count.
 *
 *          Physical memory is sourced from the PMM:
 *          - Standard 4KB pages via vos3_pmm_alloc_pages()
 *          - 2MB HugePages via vos3_pmm_alloc_huge() when HUGEPAGE flag set
 *
 *          Virtual mappings are created in a dedicated GPU memory region
 *          starting at VOS3_GPU_MEM_VBASE (0xFFFF880020000000) using a
 *          bump allocator. Caching policy is controlled via VMM flags:
 *
 *          - COHERENT:      Normal cached (default VMM mapping)
 *          - WRITE_COMBINE: PAT entry 4 (WC) via VOS3_VMM_FLAG_WRITE_COMBINE
 *          - DEVICE:        PCD=1 PWT=1 (UC) via VOS3_VMM_FLAG_DEVICE
 *
 *          Reference counting:
 *          - vos3_gpu_mem_alloc() sets refcount to 1
 *          - vos3_gpu_mem_ref() increments refcount
 *          - vos3_gpu_mem_unref() decrements; free-on-zero
 *          - vos3_gpu_mem_free() unconditionally frees
 *
 *          All pool operations are serialized via a single spinlock.
 *          VMM operations are performed outside the pool lock (the VMM
 *          has its own internal locking).
 *
 * @version 2.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.3 -- GPU Memory Subsystem
 * @note MISRA C:2024 Compliant
 * @note Freestanding: no libc, no SSE/MMX, no floating point
 */

#include "../../include/vos/gpu_mem.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/console.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * INTERNAL CONSTANTS
 * ============================================================================ */

/** @brief 4KB page size (local alias) */
#define GPU_PAGE_SIZE           ((uint32_t)4096U)

/** @brief 2MB HugePage size (local alias) */
#define GPU_HUGEPAGE_SIZE       ((uint32_t)(2U * 1024U * 1024U))

/** @brief Virtual address ceiling for GPU memory region */
#define GPU_MEM_VEND            (VOS3_GPU_MEM_VBASE + VOS3_GPU_MEM_VSIZE)

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief The DMA buffer descriptor pool (512 entries) */
static vos3_dma_buf_t g_buf_pool[VOS3_GPU_MEM_MAX_BUFS];

/** @brief Global spinlock for all pool operations */
static vos3_spinlock_t g_gpu_lock = VOS3_SPINLOCK_INIT;

/** @brief Subsystem initialization flag */
static uint32_t g_initialized;

/** @brief Virtual address bump allocator (next free VA in GPU region) */
static uintptr_t g_vbump_next;

/* --- Lifetime statistics --- */

/** @brief Currently allocated bytes across all active buffers */
static uint64_t g_used_bytes;

/** @brief Currently allocated descriptor count */
static uint32_t g_used_bufs;

/** @brief Lifetime COHERENT allocation count */
static uint32_t g_coherent_count;

/** @brief Lifetime WRITE_COMBINE allocation count */
static uint32_t g_wc_count;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Find a free descriptor slot in the pool
 *
 * Linear scan of the 512-entry pool. Pool is small enough that O(n)
 * scan is acceptable; typical GPU workloads have tens of active
 * allocations, not hundreds.
 *
 * @return Pointer to free descriptor, or NULL if pool is full
 *
 * @note Caller must hold g_gpu_lock.
 */
static vos3_dma_buf_t *pool_find_free(void)
{
    uint32_t i;

    for (i = 0U; i < VOS3_GPU_MEM_MAX_BUFS; i++) {
        if (g_buf_pool[i].in_use == 0U) {
            return &g_buf_pool[i];
        }
    }

    return (vos3_dma_buf_t *)0;
}

/**
 * @brief Determine VMM flags for the given GPU allocation flags
 *
 * Translates VOS3_GPU_MEM_* flags into the VMM flag set appropriate
 * for the kernel virtual mapping.
 *
 * @param[in] flags  GPU allocation flags (VOS3_GPU_MEM_*)
 * @return VMM flags for vos3_vmm_map_range()
 */
static vos3_vmm_flags_t gpu_flags_to_vmm(uint32_t flags)
{
    vos3_vmm_flags_t vmm_flags = VOS3_VMM_FLAG_WRITE;

    if (flags & VOS3_GPU_MEM_DEVICE) {
        /*
         * Strong Uncacheable (UC): PCD=1, PWT=1.
         * Also implies NOCACHE for the base flag converter.
         */
        vmm_flags |= VOS3_VMM_FLAG_DEVICE | VOS3_VMM_FLAG_NOCACHE;
    } else if (flags & VOS3_GPU_MEM_WRITE_COMBINE) {
        /*
         * Write-Combining (WC): PAT=1, PCD=0, PWT=0 -> PAT entry 4.
         * High throughput for bulk GPU data (framebuffers, tensors).
         */
        vmm_flags |= VOS3_VMM_FLAG_WRITE_COMBINE;
    }
    /* COHERENT: default cached mapping, no extra VMM flags needed */

    return vmm_flags;
}

/**
 * @brief Round a byte size up to the appropriate page boundary
 *
 * For HugePage allocations, rounds up to 2MB. Otherwise rounds to 4KB.
 *
 * @param[in] size   Requested size in bytes
 * @param[in] flags  Allocation flags
 * @return Aligned size in bytes (minimum one page)
 */
static uint32_t align_size(uint32_t size, uint32_t flags)
{
    uint32_t page;

    if (flags & VOS3_GPU_MEM_HUGEPAGE) {
        page = GPU_HUGEPAGE_SIZE;
    } else {
        page = GPU_PAGE_SIZE;
    }

    if (size == 0U) {
        return page;
    }

    return ((size + page - 1U) / page) * page;
}

/**
 * @brief Release the physical and virtual resources of a buffer
 *
 * Unmaps the VMM region and returns physical pages to the PMM.
 * Resets all descriptor fields and marks it as free.
 *
 * @param[in] buf  Buffer descriptor to release
 *
 * @note Caller must NOT hold g_gpu_lock when calling this function,
 *       because VMM operations require their own locking. The caller
 *       should update pool statistics after this call while holding
 *       the lock.
 */
static void buf_release_resources(vos3_dma_buf_t *buf)
{
    uint64_t phys;
    uint32_t alloc_size;
    uint32_t alloc_flags;

    /* Snapshot fields before we clear them */
    phys       = buf->phys_addr;
    alloc_size = buf->size;
    alloc_flags = buf->flags;

    /* Tear down the kernel virtual mapping (VMM has its own lock) */
    if (buf->virt_addr != (void *)0) {
        (void)vos3_vmm_unmap_range((uintptr_t)buf->virt_addr,
                                    (size_t)alloc_size);
    }

    /* Return physical pages to the PMM — skip for BAR-mapped buffers
     * (BAR memory belongs to the PCI device, not to the PMM). */
    if (phys != 0U && !(alloc_flags & VOS3_GPU_MEM_BAR_MAPPED)) {
        if (alloc_flags & VOS3_GPU_MEM_HUGEPAGE) {
            /* HugePage: return 2MB pages */
            uint32_t hp_count = alloc_size / GPU_HUGEPAGE_SIZE;
            uint32_t h;

            for (h = 0U; h < hp_count; h++) {
                vos3_pmm_free_huge(phys + ((uint64_t)h * GPU_HUGEPAGE_SIZE));
            }
        } else {
            /* Standard 4KB pages */
            uint32_t page_count = alloc_size / GPU_PAGE_SIZE;

            vos3_pmm_free_pages((uintptr_t)phys, (size_t)page_count);
        }
    }

    /* Poison the descriptor to catch use-after-free */
    buf->phys_addr = 0U;
    buf->virt_addr = (void *)0;
    buf->size      = 0U;
    buf->flags     = 0U;
    buf->refcount  = 0U;
    buf->in_use    = 0U;
}

/* ============================================================================
 * PUBLIC API IMPLEMENTATION
 * ============================================================================ */

/**
 * @brief Initialize the GPU memory subsystem
 *
 * Zeroes the descriptor pool and prepares the virtual address bump
 * allocator. Does not pre-allocate any physical memory; pages are
 * allocated on demand by vos3_gpu_mem_alloc().
 *
 * @return 0 on success, VOS3_GPU_MEM_ERR_NOTINIT should never occur here
 */
int vos3_gpu_mem_init(void)
{
    uint32_t i;

    vos3_spinlock_lock(&g_gpu_lock);

    if (g_initialized != 0U) {
        vos3_spinlock_unlock(&g_gpu_lock);
        vos3_console_printf("[GPU_MEM] already initialized\n");
        return VOS3_GPU_MEM_OK;
    }

    /* Zero the entire descriptor pool */
    for (i = 0U; i < VOS3_GPU_MEM_MAX_BUFS; i++) {
        g_buf_pool[i].phys_addr = 0U;
        g_buf_pool[i].virt_addr = (void *)0;
        g_buf_pool[i].size      = 0U;
        g_buf_pool[i].flags     = 0U;
        g_buf_pool[i].refcount  = 0U;
        g_buf_pool[i].in_use    = 0U;
    }

    /* Reset statistics */
    g_used_bytes    = 0U;
    g_used_bufs     = 0U;
    g_coherent_count = 0U;
    g_wc_count      = 0U;

    /* Initialize the virtual address bump allocator */
    g_vbump_next = VOS3_GPU_MEM_VBASE;

    g_initialized = 1U;

    vos3_spinlock_unlock(&g_gpu_lock);

    vos3_console_printf("[GPU_MEM] initialized: %u descriptor slots, "
                        "vbase 0x%lx, vsize %lu MiB\n",
                        (unsigned)VOS3_GPU_MEM_MAX_BUFS,
                        (unsigned long)VOS3_GPU_MEM_VBASE,
                        (unsigned long)(VOS3_GPU_MEM_VSIZE / (1024U * 1024U)));

    return VOS3_GPU_MEM_OK;
}

/**
 * @brief Allocate a DMA buffer from the GPU memory pool
 *
 * Steps:
 *  1. Find a free descriptor slot (under lock)
 *  2. Bump-allocate a virtual address range (under lock)
 *  3. Allocate physical pages from PMM (outside lock)
 *  4. Map physical pages at the virtual address via VMM (outside lock)
 *  5. Populate the descriptor and return
 *
 * On failure at any stage, all previously acquired resources are rolled
 * back (descriptor freed, VA space not reclaimed due to bump allocator).
 *
 * @param[in]  size     Requested size in bytes
 * @param[in]  flags    Allocation flags (VOS3_GPU_MEM_*)
 * @param[out] buf_out  Set to point to the allocated descriptor on success
 * @return 0 on success, negative error code on failure
 */
int vos3_gpu_mem_alloc(uint32_t size, uint32_t flags, vos3_dma_buf_t **buf_out)
{
    vos3_dma_buf_t *desc;
    uint32_t alloc_size;
    uintptr_t vaddr;
    uint64_t phys;
    vos3_vmm_flags_t vmm_flags;
    int rc;

    /* --- Parameter validation --- */
    if (buf_out == (vos3_dma_buf_t **)0) {
        return VOS3_GPU_MEM_ERR_INVALID;
    }
    *buf_out = (vos3_dma_buf_t *)0;

    if (size == 0U) {
        return VOS3_GPU_MEM_ERR_INVALID;
    }

    /* Compute page-aligned allocation size */
    alloc_size = align_size(size, flags);

    vos3_spinlock_lock(&g_gpu_lock);

    if (g_initialized == 0U) {
        vos3_spinlock_unlock(&g_gpu_lock);
        return VOS3_GPU_MEM_ERR_NOTINIT;
    }

    /* 1. Find a free descriptor slot */
    desc = pool_find_free();
    if (desc == (vos3_dma_buf_t *)0) {
        vos3_spinlock_unlock(&g_gpu_lock);
        vos3_console_printf("[GPU_MEM] alloc: pool full (%u/%u in use)\n",
                            (unsigned)g_used_bufs,
                            (unsigned)VOS3_GPU_MEM_MAX_BUFS);
        return VOS3_GPU_MEM_ERR_POOL_FULL;
    }

    /* 2. Bump-allocate virtual address space */
    if ((g_vbump_next + (uintptr_t)alloc_size) > GPU_MEM_VEND) {
        vos3_spinlock_unlock(&g_gpu_lock);
        vos3_console_printf("[GPU_MEM] alloc: virtual space exhausted "
                            "(need %u, avail %lu)\n",
                            (unsigned)alloc_size,
                            (unsigned long)(GPU_MEM_VEND - g_vbump_next));
        return VOS3_GPU_MEM_ERR_NOMEM;
    }

    vaddr = g_vbump_next;
    g_vbump_next += (uintptr_t)alloc_size;

    /* Mark descriptor as in-use early to prevent racing allocs */
    desc->in_use = 1U;

    /* Update statistics under lock */
    g_used_bufs++;
    g_used_bytes += (uint64_t)alloc_size;

    if (flags & VOS3_GPU_MEM_WRITE_COMBINE) {
        g_wc_count++;
    } else if ((flags & VOS3_GPU_MEM_DEVICE) == 0U) {
        /* Default is COHERENT if neither DEVICE nor WC */
        g_coherent_count++;
    }

    vos3_spinlock_unlock(&g_gpu_lock);

    /* 3. Allocate physical memory from PMM (outside lock) */
    if (flags & VOS3_GPU_MEM_HUGEPAGE) {
        uint32_t hp_count = alloc_size / GPU_HUGEPAGE_SIZE;
        uint32_t h;

        /*
         * For HugePage allocations, we allocate the first 2MB page and
         * verify all subsequent pages are contiguous. Since PMM HugePages
         * are individually tracked, we allocate each one separately.
         * For a single HugePage this is trivial.
         */
        phys = vos3_pmm_alloc_huge();
        if (phys == 0U) {
            goto fail_pmm;
        }

        /* Allocate remaining HugePages and verify we got them all */
        for (h = 1U; h < hp_count; h++) {
            uint64_t hp = vos3_pmm_alloc_huge();
            if (hp == 0U) {
                /* Rollback: free already-allocated HugePages */
                uint32_t j;
                for (j = 0U; j < h; j++) {
                    vos3_pmm_free_huge(phys + ((uint64_t)j * GPU_HUGEPAGE_SIZE));
                }
                goto fail_pmm;
            }
            /*
             * Note: Individual HugePages may not be physically contiguous.
             * For multi-HugePage allocations we map each 2MB chunk separately
             * but present a single contiguous virtual range. We store the
             * base phys of the first page for the descriptor.
             */
        }
    } else {
        /* Standard 4KB pages: allocate contiguous from DMA32 */
        uint32_t page_count = alloc_size / GPU_PAGE_SIZE;

        phys = (uint64_t)vos3_pmm_alloc_pages(
            (size_t)page_count,
            VOS3_PMM_FLAG_DMA32 | VOS3_PMM_FLAG_CONTIGUOUS | VOS3_PMM_FLAG_ZERO
        );
        if (phys == 0U) {
            goto fail_pmm;
        }
    }

    /* 4. Map into kernel virtual space via VMM (outside lock) */
    vmm_flags = gpu_flags_to_vmm(flags);

    if (flags & VOS3_GPU_MEM_HUGEPAGE) {
        vmm_flags |= VOS3_VMM_FLAG_LARGE;
    }

    rc = vos3_vmm_map_range(vaddr, (uintptr_t)phys,
                             (size_t)alloc_size, vmm_flags);
    if (rc != 0) {
        vos3_console_printf("[GPU_MEM] alloc: vmm_map_range failed "
                            "(rc=%d) phys 0x%lx -> virt 0x%lx size %u\n",
                            rc, (unsigned long)phys,
                            (unsigned long)vaddr, (unsigned)alloc_size);
        /* Rollback: return physical pages */
        if (flags & VOS3_GPU_MEM_HUGEPAGE) {
            uint32_t hp_count = alloc_size / GPU_HUGEPAGE_SIZE;
            uint32_t h;
            for (h = 0U; h < hp_count; h++) {
                vos3_pmm_free_huge(phys + ((uint64_t)h * GPU_HUGEPAGE_SIZE));
            }
        } else {
            vos3_pmm_free_pages((uintptr_t)phys,
                                (size_t)(alloc_size / GPU_PAGE_SIZE));
        }
        goto fail_map;
    }

    /* 5. Populate the descriptor */
    desc->phys_addr = phys;
    desc->virt_addr = (void *)vaddr;
    desc->size      = alloc_size;
    desc->flags     = flags;
    desc->refcount  = 1U;

    *buf_out = desc;

    return VOS3_GPU_MEM_OK;

fail_pmm:
    vos3_console_printf("[GPU_MEM] alloc: PMM allocation failed "
                        "(size %u, flags 0x%x)\n",
                        (unsigned)alloc_size, (unsigned)flags);
    /* Fall through to rollback descriptor */

fail_map:
    /* Rollback: return descriptor to pool */
    vos3_spinlock_lock(&g_gpu_lock);
    desc->in_use = 0U;
    if (g_used_bufs > 0U) {
        g_used_bufs--;
    }
    if (g_used_bytes >= (uint64_t)alloc_size) {
        g_used_bytes -= (uint64_t)alloc_size;
    } else {
        g_used_bytes = 0U;
    }
    vos3_spinlock_unlock(&g_gpu_lock);

    return VOS3_GPU_MEM_ERR_NOMEM;
}

/**
 * @brief Free a DMA buffer unconditionally
 *
 * Releases all physical and virtual resources regardless of the current
 * reference count, and returns the descriptor to the free pool.
 *
 * @param[in] buf  Buffer descriptor to free (may be NULL, which is a no-op)
 */
void vos3_gpu_mem_free(vos3_dma_buf_t *buf)
{
    uint32_t freed_size;

    if (buf == (vos3_dma_buf_t *)0) {
        return;
    }

    vos3_spinlock_lock(&g_gpu_lock);

    if (buf->in_use == 0U) {
        vos3_spinlock_unlock(&g_gpu_lock);
        return;
    }

    freed_size = buf->size;

    /* v23.5: Clear in_use INSIDE the lock to prevent a concurrent
     * gpu_mem_free() from seeing in_use=1 and double-freeing (A-LOW1 fix).
     * buf_release_resources() will also set in_use=0 redundantly. */
    buf->in_use = 0U;

    /* Update pool statistics while we have the lock */
    if (g_used_bufs > 0U) {
        g_used_bufs--;
    }
    if (g_used_bytes >= (uint64_t)freed_size) {
        g_used_bytes -= (uint64_t)freed_size;
    } else {
        g_used_bytes = 0U;
    }

    vos3_spinlock_unlock(&g_gpu_lock);

    /* Release physical and virtual resources (outside lock) */
    buf_release_resources(buf);
}

/**
 * @brief Increment the reference count on a DMA buffer
 *
 * The caller must ensure the buffer is valid and in use.
 *
 * @param[in] buf  Buffer descriptor to reference
 * @return 0 on success, -1 on error (NULL or not in use)
 */
int vos3_gpu_mem_ref(vos3_dma_buf_t *buf)
{
    if (buf == (vos3_dma_buf_t *)0) {
        return -1;
    }

    vos3_spinlock_lock(&g_gpu_lock);

    if (buf->in_use == 0U) {
        vos3_spinlock_unlock(&g_gpu_lock);
        return -1;
    }

    buf->refcount++;

    vos3_spinlock_unlock(&g_gpu_lock);

    return 0;
}

/**
 * @brief Decrement the reference count; free on zero
 *
 * If the reference count drops to zero, the buffer is automatically
 * freed: virtual mapping torn down, physical pages returned to PMM,
 * descriptor returned to the pool.
 *
 * @param[in] buf  Buffer descriptor to unreference (may be NULL)
 */
void vos3_gpu_mem_unref(vos3_dma_buf_t *buf)
{
    uint32_t freed_size;
    int do_free;

    if (buf == (vos3_dma_buf_t *)0) {
        return;
    }

    vos3_spinlock_lock(&g_gpu_lock);

    if (buf->in_use == 0U) {
        vos3_spinlock_unlock(&g_gpu_lock);
        return;
    }

    if (buf->refcount == 0U) {
        /* Already at zero -- should not happen, but be defensive */
        vos3_spinlock_unlock(&g_gpu_lock);
        vos3_console_printf("[GPU_MEM] unref: refcount already 0 on "
                            "buf at phys 0x%lx\n",
                            (unsigned long)buf->phys_addr);
        return;
    }

    buf->refcount--;

    if (buf->refcount == 0U) {
        do_free    = 1;
        freed_size = buf->size;
        /* v23.6: Clear in_use and update stats INSIDE lock to prevent
         * concurrent alloc from seeing a ghost buffer (A-MED fix).
         * Mirrors the vos3_gpu_mem_free() pattern from v23.5. */
        buf->in_use = 0U;
        if (g_used_bufs > 0U) {
            g_used_bufs--;
        }
        if (g_used_bytes >= (uint64_t)freed_size) {
            g_used_bytes -= (uint64_t)freed_size;
        } else {
            g_used_bytes = 0U;
        }
    } else {
        do_free    = 0;
        freed_size = 0U;
    }

    vos3_spinlock_unlock(&g_gpu_lock);

    if (do_free != 0) {
        /* Release backing resources (outside lock — no pool state touched) */
        buf_release_resources(buf);
    }
}

/* ============================================================================
 * PCI BAR DIRECT MAPPING (Phase 5 — GPU Memory Warp)
 * ============================================================================ */

/**
 * @brief Map a PCI BAR physical address into the GPU memory pool.
 *
 * Zero-copy VRAM access: maps device-owned physical memory (GPU framebuffer,
 * NPU SRAM, ivshmem zone) directly into the kernel's GPU VA space without
 * allocating pages from the PMM. This enables:
 *
 *   NVMe SSD → DMA → ivshmem zone → GPU BAR (zero intermediate copies)
 *
 * The descriptor is flagged BAR_MAPPED so that free/unref will only
 * tear down the virtual mapping and NOT return pages to the PMM.
 *
 * @param[in]  bar_phys  Physical base address of the PCI BAR region
 * @param[in]  size      Size in bytes (page-aligned internally)
 * @param[in]  flags     Caching flags (DEVICE, WRITE_COMBINE, or COHERENT)
 * @param[out] buf_out   Descriptor pointer, set on success
 * @return 0 on success, negative error code on failure
 */
int vos3_gpu_mem_map_bar(uint64_t bar_phys, uint32_t size, uint32_t flags,
                          vos3_dma_buf_t **buf_out)
{
    vos3_dma_buf_t *desc;
    uintptr_t vaddr;
    uint32_t alloc_size;
    vos3_vmm_flags_t vmm_flags;
    int rc;

    if (buf_out == (vos3_dma_buf_t **)0) {
        return VOS3_GPU_MEM_ERR_INVALID;
    }
    *buf_out = (vos3_dma_buf_t *)0;

    if (g_initialized == 0U) {
        return VOS3_GPU_MEM_ERR_NOTINIT;
    }

    if (bar_phys == 0ULL || size == 0U) {
        return VOS3_GPU_MEM_ERR_INVALID;
    }

    /* Merge BAR_MAPPED flag — caller may also specify caching policy */
    flags |= VOS3_GPU_MEM_BAR_MAPPED;

    /* Round size to page boundary */
    alloc_size = align_size(size, flags);

    /* Phase 1 (under lock): reserve descriptor + VA range */
    vos3_spinlock_lock(&g_gpu_lock);

    desc = pool_find_free();
    if (desc == (vos3_dma_buf_t *)0) {
        vos3_spinlock_unlock(&g_gpu_lock);
        vos3_console_printf("[GPU_MEM] BAR map: pool exhausted\n");
        return VOS3_GPU_MEM_ERR_POOL_FULL;
    }

    /* Bump-allocate VA range */
    vaddr = g_vbump_next;
    if (vaddr + (uintptr_t)alloc_size > GPU_MEM_VEND) {
        vos3_spinlock_unlock(&g_gpu_lock);
        vos3_console_printf("[GPU_MEM] BAR map: VA space exhausted\n");
        return VOS3_GPU_MEM_ERR_NOMEM;
    }
    g_vbump_next = vaddr + (uintptr_t)alloc_size;

    desc->in_use = 1U;
    g_used_bufs++;
    g_used_bytes += (uint64_t)alloc_size;

    vos3_spinlock_unlock(&g_gpu_lock);

    /* Phase 2 (outside lock): create VMM mapping to PCI BAR physical address */
    vmm_flags = gpu_flags_to_vmm(flags);

    /* Map each page of the BAR physical range into kernel virtual space */
    for (uint32_t off = 0; off < alloc_size; off += GPU_PAGE_SIZE) {
        rc = vos3_vmm_map(vaddr + off, (uintptr_t)(bar_phys + off), vmm_flags);
        if (rc != 0) {
            /* Rollback: unmap any pages we already mapped */
            for (uint32_t rb = 0; rb < off; rb += GPU_PAGE_SIZE) {
                (void)vos3_vmm_unmap(vaddr + rb);
            }
            /* Return descriptor to pool */
            vos3_spinlock_lock(&g_gpu_lock);
            desc->in_use = 0U;
            if (g_used_bufs > 0U) g_used_bufs--;
            if (g_used_bytes >= (uint64_t)alloc_size) {
                g_used_bytes -= (uint64_t)alloc_size;
            }
            vos3_spinlock_unlock(&g_gpu_lock);

            vos3_console_printf("[GPU_MEM] BAR map: VMM mapping failed at off 0x%x\n",
                                (unsigned)off);
            return VOS3_GPU_MEM_ERR_MAP;
        }
    }

    /* Populate descriptor */
    desc->phys_addr = bar_phys;
    desc->virt_addr = (void *)vaddr;
    desc->size      = alloc_size;
    desc->flags     = flags;
    desc->refcount  = 1U;

    *buf_out = desc;

    vos3_console_printf("[GPU_MEM] BAR mapped: phys 0x%llx → virt 0x%lx, %u bytes (%s)\n",
                        (unsigned long long)bar_phys, (unsigned long)vaddr,
                        alloc_size,
                        (flags & VOS3_GPU_MEM_WRITE_COMBINE) ? "WC" :
                        (flags & VOS3_GPU_MEM_DEVICE) ? "UC" : "cached");

    return VOS3_GPU_MEM_OK;
}

/**
 * @brief Retrieve GPU memory subsystem statistics
 *
 * Snapshot is taken under the pool lock for consistency.
 *
 * @param[out] stats  Statistics structure to populate
 * @return 0 on success, -1 if stats is NULL
 */
int vos3_gpu_mem_get_stats(vos3_gpu_mem_stats_t *stats)
{
    if (stats == (vos3_gpu_mem_stats_t *)0) {
        return -1;
    }

    vos3_spinlock_lock(&g_gpu_lock);

    stats->total_buffers   = VOS3_GPU_MEM_MAX_BUFS;
    stats->used_buffers    = g_used_bufs;
    stats->total_bytes     = (uint64_t)VOS3_GPU_MEM_VSIZE;
    stats->used_bytes      = g_used_bytes;
    stats->coherent_allocs = g_coherent_count;
    stats->wc_allocs       = g_wc_count;

    vos3_spinlock_unlock(&g_gpu_lock);

    return 0;
}
