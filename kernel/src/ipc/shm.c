/**
 * @file shm.c
 * @brief VOS3 Shared Memory Implementation
 *
 * @details Shared memory regions for inter-process communication.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/ipc.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/task.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/ai_guard.h"

/** @brief Base address for SHM user-space mappings */
#define VOS3_SHM_USER_BASE      0x0000000020000000ULL

/* ============================================================================
 * SHM VA FREE-LIST ALLOCATOR
 *
 * Replaces bump-only allocator so that unmapped VA ranges are recycled.
 * First-fit search through sorted free list; falls back to bump when empty.
 * Adjacent free blocks are coalesced on free().
 *
 * Uses a static node pool (no kmalloc/kfree under spinlock) to avoid
 * deadlocking with the heap allocator's own lock.
 * ============================================================================ */

/** @brief Free VA block for the SHM user-space allocator */
typedef struct shm_va_block {
    uint64_t start;
    uint64_t size;
    struct shm_va_block* next;
} shm_va_block_t;

/** @brief Static pool size — 2x max SHM regions handles fragmentation */
#define SHM_VA_POOL_SIZE    128

/** @brief Static pool of VA block nodes */
static shm_va_block_t g_shm_va_pool[SHM_VA_POOL_SIZE];

/** @brief Pool free-list (nodes available for allocation) */
static shm_va_block_t* g_shm_va_pool_free = NULL;

/** @brief Pool initialization flag */
static int g_shm_va_pool_inited = 0;

/** @brief Free-list head (sorted by start address) */
static shm_va_block_t* g_shm_free_list = NULL;

/** @brief High-water mark for bump fallback */
static uint64_t g_shm_user_next = VOS3_SHM_USER_BASE;

/** @brief Protects all VA allocator state from concurrent access */
static vos3_spinlock_t g_shm_va_lock = VOS3_SPINLOCK_INIT;

/** @brief Initialize the static node pool (called under lock) */
static void shm_va_pool_init(void)
{
    for (uint32_t i = 0; i < SHM_VA_POOL_SIZE; i++) {
        g_shm_va_pool[i].next = g_shm_va_pool_free;
        g_shm_va_pool_free = &g_shm_va_pool[i];
    }
    g_shm_va_pool_inited = 1;
}

/** @brief Grab a node from the static pool (called under lock) */
static shm_va_block_t* shm_va_node_alloc(void)
{
    shm_va_block_t* blk = g_shm_va_pool_free;
    if (blk != NULL) {
        g_shm_va_pool_free = blk->next;
    }
    return blk;
}

/** @brief Return a node to the static pool (called under lock) */
static void shm_va_node_free(shm_va_block_t* blk)
{
    blk->next = g_shm_va_pool_free;
    g_shm_va_pool_free = blk;
}

/**
 * @brief Allocate a VA range from the SHM user-space allocator.
 * @param size  Requested size (must be page-aligned).
 * @param align Required alignment (0 or power-of-2; 0 uses page alignment).
 * @return Virtual address, or 0 on failure.
 */
static uint64_t shm_va_alloc(size_t size, uint64_t align)
{
    if (align == 0) {
        align = VOS3_PAGE_SIZE;
    }

    vos3_spinlock_lock(&g_shm_va_lock);

    if (!g_shm_va_pool_inited) {
        shm_va_pool_init();
    }

    /* First-fit search in free list */
    shm_va_block_t** prev = &g_shm_free_list;
    shm_va_block_t*  cur  = g_shm_free_list;

    while (cur != NULL) {
        uint64_t aligned_start = (cur->start + align - 1) & ~(align - 1);
        uint64_t waste = aligned_start - cur->start;

        if (cur->size >= waste + size) {
            uint64_t result = aligned_start;

            if (waste == 0 && cur->size == size) {
                /* Exact fit — remove block entirely */
                *prev = cur->next;
                shm_va_node_free(cur);
            } else if (waste == 0) {
                /* Trim from front */
                cur->start += size;
                cur->size  -= size;
            } else {
                /* Split: keep prefix as waste block, trim from aligned region */
                uint64_t remaining = cur->size - waste - size;
                cur->size = waste;  /* front fragment */

                if (remaining > 0) {
                    /* Create tail fragment */
                    shm_va_block_t* tail = shm_va_node_alloc();
                    if (tail != NULL) {
                        tail->start = result + size;
                        tail->size  = remaining;
                        tail->next  = cur->next;
                        cur->next   = tail;
                    }
                    /* If pool exhausted, we just lose the tail fragment — not fatal */
                }
            }

            vos3_spinlock_unlock(&g_shm_va_lock);
            return result;
        }

        prev = &cur->next;
        cur  = cur->next;
    }

    /* No free block found — bump allocator fallback */
    uint64_t bump_aligned = (g_shm_user_next + align - 1) & ~(align - 1);
    g_shm_user_next = bump_aligned + size;
    vos3_spinlock_unlock(&g_shm_va_lock);
    return bump_aligned;
}

/**
 * @brief Return a VA range to the SHM free-list with coalescing.
 * @param addr  Start of VA range (must be page-aligned).
 * @param size  Size of VA range (must be page-aligned).
 */
void shm_va_free(uint64_t addr, size_t size)
{
    if (addr == 0 || size == 0) {
        return;
    }

    vos3_spinlock_lock(&g_shm_va_lock);

    if (!g_shm_va_pool_inited) {
        shm_va_pool_init();
    }

    /* Walk list to find insertion point (sorted by start address).
     * Track predecessor explicitly for coalescing. */
    shm_va_block_t*  pred = NULL;
    shm_va_block_t** prev = &g_shm_free_list;
    shm_va_block_t*  cur  = g_shm_free_list;

    while (cur != NULL && cur->start < addr) {
        pred = cur;
        prev = &cur->next;
        cur  = cur->next;
    }

    /* Try to coalesce with predecessor */
    int merged_with_pred = 0;
    if (pred != NULL && pred->start + pred->size == addr) {
        pred->size += size;
        merged_with_pred = 1;
    }

    /* Try to coalesce with successor */
    if (cur != NULL && addr + size == cur->start) {
        if (merged_with_pred) {
            /* Merge pred + cur into pred */
            pred->size += cur->size;
            pred->next  = cur->next;
            shm_va_node_free(cur);
        } else {
            /* Extend cur backwards */
            cur->start = addr;
            cur->size += size;
        }
        vos3_spinlock_unlock(&g_shm_va_lock);
        return;
    }

    if (merged_with_pred) {
        vos3_spinlock_unlock(&g_shm_va_lock);
        return;  /* Already merged with predecessor */
    }

    /* No merge possible — allocate new free block from static pool */
    shm_va_block_t* blk = shm_va_node_alloc();
    if (blk == NULL) {
        vos3_spinlock_unlock(&g_shm_va_lock);
        return;  /* Pool exhausted — lost VA range, not fatal */
    }

    blk->start = addr;
    blk->size  = size;
    blk->next  = cur;
    *prev      = blk;
    vos3_spinlock_unlock(&g_shm_va_lock);
}

/* ============================================================================
 * SHARED MEMORY TABLE
 * ============================================================================ */

/** @brief Shared memory table */
static vos3_shm_region_t* g_shm_table[VOS3_SHM_MAX_REGIONS];

/** @brief Shared memory table lock */
static vos3_spinlock_t g_shm_lock = VOS3_SPINLOCK_INIT;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Get shared memory region by ID
 */
static vos3_shm_region_t* shm_get(vos3_ipc_id_t id)
{
    if (id == VOS3_IPC_INVALID || id >= VOS3_SHM_MAX_REGIONS) {
        return NULL;
    }

    vos3_shm_region_t* shm = g_shm_table[id];
    if (shm == NULL || shm->magic != VOS3_SHM_MAGIC) {
        return NULL;
    }

    return shm;
}

/**
 * @brief Allocate shared memory ID
 */
static vos3_ipc_id_t shm_alloc_id(void)
{
    for (vos3_ipc_id_t i = 1U; i < VOS3_SHM_MAX_REGIONS; i++) {
        if (g_shm_table[i] == NULL) {
            return i;
        }
    }
    return VOS3_IPC_INVALID;
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

vos3_ipc_id_t vos3_shm_create(const char* name, size_t size, uint32_t flags)
{
    if (size == 0U) {
        return VOS3_IPC_INVALID;
    }

    /* Allocate shared memory structure */
    vos3_shm_region_t* shm = (vos3_shm_region_t*)vos3_kzalloc(sizeof(vos3_shm_region_t));
    if (shm == NULL) {
        return VOS3_IPC_INVALID;
    }

    uint64_t phys_addr;
    size_t page_count;

    if (flags & VOS3_SHM_FLAG_HUGETLB) {
        /* HugePage path: round up to 2MB, allocate from HugePage pool */
        size_t huge_count = (size + VOS3_LARGE_PAGE_SIZE - 1) / VOS3_LARGE_PAGE_SIZE;
        size = huge_count * VOS3_LARGE_PAGE_SIZE;
        page_count = size / VOS3_PAGE_SIZE;

        /* Allocate hugepages into temp array, then sort for contiguity */
        uint64_t hp_addrs[16] = {0};  /* max 16 hugepages = 32MB per SHM */
        if (huge_count > 16) {
            vos3_kfree(shm);
            return VOS3_IPC_INVALID;
        }

        for (size_t i = 0; i < huge_count; i++) {
            hp_addrs[i] = vos3_pmm_alloc_huge();
            if (hp_addrs[i] == 0) {
                for (size_t j = 0; j < i; j++)
                    vos3_pmm_free_huge(hp_addrs[j]);
                vos3_kfree(shm);
                return VOS3_IPC_INVALID;
            }
        }

        /* Sort by physical address (insertion sort, tiny array) */
        for (size_t i = 1; i < huge_count; i++) {
            uint64_t key = hp_addrs[i];
            size_t j = i;
            while (j > 0 && hp_addrs[j - 1] > key) {
                hp_addrs[j] = hp_addrs[j - 1];
                j--;
            }
            hp_addrs[j] = key;
        }

        /* Verify contiguity after sorting */
        phys_addr = hp_addrs[0];
        int contiguous = 1;
        for (size_t i = 1; i < huge_count; i++) {
            if (hp_addrs[i] != phys_addr + i * VOS3_LARGE_PAGE_SIZE) {
                contiguous = 0;
                break;
            }
        }

        if (!contiguous) {
            for (size_t i = 0; i < huge_count; i++)
                vos3_pmm_free_huge(hp_addrs[i]);
            vos3_kfree(shm);
            return VOS3_IPC_INVALID;
        }
    } else {
        /* Standard 4KB path (unchanged) */
        size = (size + VOS3_PAGE_SIZE - 1U) & ~(VOS3_PAGE_SIZE - 1U);
        page_count = size / VOS3_PAGE_SIZE;
        phys_addr = vos3_pmm_alloc_pages(page_count, 0);
        if (phys_addr == 0ULL) {
            vos3_kfree(shm);
            return VOS3_IPC_INVALID;
        }
    }

    /* Map into kernel virtual address space */
    void* kernel_addr = vos3_vmm_map_pages(phys_addr, size,
                                            VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE);
    if (kernel_addr == NULL) {
        if (flags & VOS3_SHM_FLAG_HUGETLB) {
            size_t huge_count = size / VOS3_LARGE_PAGE_SIZE;
            for (size_t i = 0; i < huge_count; i++)
                vos3_pmm_free_huge(phys_addr + i * VOS3_LARGE_PAGE_SIZE);
        } else {
            vos3_pmm_free_pages(phys_addr, page_count);
        }
        vos3_kfree(shm);
        return VOS3_IPC_INVALID;
    }

    /* Clear the memory */
    memset(kernel_addr, 0, size);

    vos3_spinlock_lock(&g_shm_lock);

    /* Check for duplicate name */
    if (name != NULL) {
        for (uint32_t i = 0; i < VOS3_SHM_MAX_REGIONS; i++) {
            if (g_shm_table[i] != NULL &&
                strcmp(g_shm_table[i]->name, name) == 0) {
                vos3_spinlock_unlock(&g_shm_lock);
                vos3_vmm_unmap_pages(kernel_addr, size);
                if (flags & VOS3_SHM_FLAG_HUGETLB) {
                    size_t hc = size / VOS3_LARGE_PAGE_SIZE;
                    for (size_t j = 0; j < hc; j++)
                        vos3_pmm_free_huge(phys_addr + j * VOS3_LARGE_PAGE_SIZE);
                } else {
                    vos3_pmm_free_pages(phys_addr, page_count);
                }
                vos3_kfree(shm);
                return VOS3_IPC_EEXIST;
            }
        }
    }

    vos3_ipc_id_t id = shm_alloc_id();
    if (id == VOS3_IPC_INVALID) {
        vos3_spinlock_unlock(&g_shm_lock);
        vos3_vmm_unmap_pages(kernel_addr, size);
        if (flags & VOS3_SHM_FLAG_HUGETLB) {
            size_t huge_count = size / VOS3_LARGE_PAGE_SIZE;
            for (size_t i = 0; i < huge_count; i++)
                vos3_pmm_free_huge(phys_addr + i * VOS3_LARGE_PAGE_SIZE);
        } else {
            vos3_pmm_free_pages(phys_addr, page_count);
        }
        vos3_kfree(shm);
        return VOS3_IPC_INVALID;
    }

    /* Initialize shared memory region */
    shm->magic = VOS3_SHM_MAGIC;
    shm->id = id;

    if (name != NULL) {
        size_t len = strlen(name);
        if (len >= sizeof(shm->name)) {
            len = sizeof(shm->name) - 1U;
        }
        memcpy(shm->name, name, len);
        shm->name[len] = '\0';
    }

    shm->kernel_addr = kernel_addr;
    shm->phys_addr = phys_addr;
    shm->size = size;
    shm->ref_count = 1U;
    shm->flags = flags;

    vos3_mutex_init(&shm->lock, "shm_lock");

    vos3_task_t* current = vos3_sched_current();
    shm->owner = current ? current->tid : 0U;

    g_shm_table[id] = shm;

    vos3_spinlock_unlock(&g_shm_lock);

    VOS3_DEBUG("Created shared memory '%s' (id=%u, size=%zu, phys=0x%llx)",
               shm->name, id, size, (unsigned long long)phys_addr);

    return id;
}

vos3_ipc_id_t vos3_shm_create_device(const char* name, uint64_t phys_addr,
                                       size_t size, uint32_t flags)
{
    if (size == 0U || !(flags & VOS3_SHM_FLAG_DEVICE)) {
        return VOS3_IPC_INVALID;
    }

    /* Validate physical address alignment (must be page-aligned) */
    if ((phys_addr & (VOS3_PAGE_SIZE - 1U)) != 0U) {
        VOS3_ERROR("SHM: create_device: phys 0x%llx not page-aligned",
                   (unsigned long long)phys_addr);
        return VOS3_IPC_INVALID;
    }

    /* Page-align size early for overflow check */
    size_t aligned_size = (size + VOS3_PAGE_SIZE - 1U) & ~(VOS3_PAGE_SIZE - 1U);

    /* Check for integer overflow: phys_addr + aligned_size must not wrap */
    if (aligned_size < size || phys_addr + aligned_size < phys_addr) {
        VOS3_ERROR("SHM: create_device: overflow phys=0x%llx size=0x%zx",
                   (unsigned long long)phys_addr, size);
        return VOS3_IPC_INVALID;
    }

    /* Validate: ENTIRE range must be device MMIO, not managed RAM */
    if (!vos3_pmm_is_device_range((uintptr_t)phys_addr) ||
        !vos3_pmm_is_device_range((uintptr_t)(phys_addr + aligned_size - 1U))) {
        VOS3_ERROR("SHM: create_device: phys 0x%llx+0x%zx overlaps managed RAM",
                   (unsigned long long)phys_addr, aligned_size);
        return VOS3_IPC_INVALID;
    }

    /* AI Guard: check hardware access authorization */
    if (vos3_ai_guard_check_hardware_access((uintptr_t)phys_addr, aligned_size) != 0) {
        return VOS3_IPC_INVALID;
    }

    /* Use pre-computed page-aligned size */
    size = aligned_size;

    /* Allocate SHM descriptor */
    vos3_shm_region_t* shm = (vos3_shm_region_t*)vos3_kzalloc(sizeof(vos3_shm_region_t));
    if (shm == NULL) {
        return VOS3_IPC_INVALID;
    }

    /* Map device MMIO into kernel virtual space with UC cache policy */
    void* kernel_addr = vos3_vmm_map_pages(phys_addr, size,
                            VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                            VOS3_PTE_NO_EXECUTE | VOS3_PTE_CACHE_DISABLE |
                            VOS3_PTE_WRITE_THROUGH);
    if (kernel_addr == NULL) {
        vos3_kfree(shm);
        return VOS3_IPC_INVALID;
    }

    /* Do NOT zero device memory — it's MMIO, not RAM */

    vos3_spinlock_lock(&g_shm_lock);

    vos3_ipc_id_t id = shm_alloc_id();
    if (id == VOS3_IPC_INVALID) {
        vos3_spinlock_unlock(&g_shm_lock);
        vos3_vmm_unmap_pages(kernel_addr, size);
        vos3_kfree(shm);
        return VOS3_IPC_INVALID;
    }

    shm->magic = VOS3_SHM_MAGIC;
    shm->id = id;

    if (name != NULL) {
        size_t len = strlen(name);
        if (len >= sizeof(shm->name)) {
            len = sizeof(shm->name) - 1U;
        }
        memcpy(shm->name, name, len);
        shm->name[len] = '\0';
    }

    shm->kernel_addr = kernel_addr;
    shm->phys_addr = phys_addr;
    shm->size = size;
    shm->ref_count = 1U;
    shm->flags = flags;

    vos3_mutex_init(&shm->lock, "shm_dev");

    vos3_task_t* current = vos3_sched_current();
    shm->owner = current ? current->tid : 0U;

    g_shm_table[id] = shm;

    vos3_spinlock_unlock(&g_shm_lock);

    VOS3_DEBUG("Created device SHM '%s' (id=%u, size=%zu, phys=0x%llx)",
               shm->name, id, size, (unsigned long long)phys_addr);

    return id;
}

int vos3_shm_destroy(vos3_ipc_id_t id)
{
    vos3_spinlock_lock(&g_shm_lock);

    vos3_shm_region_t* shm = shm_get(id);
    if (shm == NULL) {
        vos3_spinlock_unlock(&g_shm_lock);
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Decrement reference count */
    uint32_t new_rc = __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);

    if (new_rc > 0U) {
        vos3_spinlock_unlock(&g_shm_lock);
        return VOS3_IPC_OK;
    }

    /* Phase 1 (under lock): Invalidate the region so it cannot be found
     * by shm_get() or duplicate-name checks, but keep g_shm_table[id]
     * non-NULL so the slot is NOT recycled until physical cleanup is done. */
    shm->magic = 0U;
    shm->name[0] = '\0';

    vos3_spinlock_unlock(&g_shm_lock);

    /* Phase 2 (outside lock): Free all virtual and physical mappings */
    vos3_vmm_unmap_pages(shm->kernel_addr, shm->size);
    if (shm->flags & VOS3_SHM_FLAG_DEVICE) {
        /* Device MMIO: do NOT free physical pages (not PMM-managed) */
    } else if (shm->flags & VOS3_SHM_FLAG_HUGETLB) {
        /* Free each HugePage back to the pool */
        size_t huge_count = shm->size / VOS3_LARGE_PAGE_SIZE;
        for (size_t i = 0; i < huge_count; i++) {
            vos3_pmm_free_huge(shm->phys_addr + i * VOS3_LARGE_PAGE_SIZE);
        }
    } else {
        size_t page_count = shm->size / VOS3_PAGE_SIZE;
        vos3_pmm_free_pages(shm->phys_addr, page_count);
    }

    /* Destroy mutex */
    vos3_mutex_destroy(&shm->lock);

    /* Phase 3 (under lock): Return slot to the pool now that all
     * virtual/physical resources have been fully released. */
    vos3_spinlock_lock(&g_shm_lock);
    g_shm_table[id] = NULL;
    vos3_spinlock_unlock(&g_shm_lock);

    vos3_kfree(shm);

    VOS3_DEBUG("Destroyed shared memory (id=%u)", id);

    return VOS3_IPC_OK;
}

void* vos3_shm_map(vos3_ipc_id_t id, uint32_t flags)
{
    (void)flags;

    /* Hold g_shm_lock across shm_get() + ref_count bump to prevent
     * use-after-free: another CPU could shm_destroy() and kfree(shm)
     * between shm_get() returning and mutex_lock(&shm->lock). */
    vos3_spinlock_lock(&g_shm_lock);
    vos3_shm_region_t* shm = shm_get(id);
    if (shm == NULL) {
        vos3_spinlock_unlock(&g_shm_lock);
        return NULL;
    }
    __atomic_add_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);  /* Pin before releasing global lock */
    vos3_spinlock_unlock(&g_shm_lock);

    /* Ownership enforcement: user tasks can only map SHM they created,
     * unless the region was created with VOS3_SHM_FLAG_PUBLIC. */
    {
        vos3_task_t* caller = vos3_sched_current();
        if (caller != NULL && caller->address_space != NULL &&
            caller->address_space != vos3_vmm_get_kernel_space() &&
            shm->owner != 0U &&
            !(shm->flags & VOS3_SHM_FLAG_PUBLIC) &&
            caller->tid != shm->owner) {
            /* Undo the pin — safe because ref_count > 0 prevents destroy */
            __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);
            return NULL;
        }
    }

    vos3_mutex_lock(&shm->lock);

    /* ref_count already incremented above under g_shm_lock */

    /* Check if caller is a user task with its own address space (not kernel space) */
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL && current->address_space != NULL &&
        current->address_space != vos3_vmm_get_kernel_space()) {
        /* Allocate user virtual address range (free-list + bump fallback) */
        uint64_t va_align = (shm->flags & VOS3_SHM_FLAG_HUGETLB)
                            ? VOS3_LARGE_PAGE_SIZE : VOS3_PAGE_SIZE;
        uint64_t user_vaddr = shm_va_alloc(shm->size, va_align);
        if (user_vaddr == 0) {
            __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);
            vos3_mutex_unlock(&shm->lock);
            return NULL;
        }

        /* Map SHM into user space */
        if (shm->flags & VOS3_SHM_FLAG_DEVICE) {
            /* Device MMIO: 4KB mapping with Write-Combine cache policy */
            for (size_t off = 0; off < shm->size; off += VOS3_PAGE_SIZE) {
                int ret = vos3_vmm_map(user_vaddr + off, shm->phys_addr + off,
                                       VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_USER |
                                       VOS3_VMM_FLAG_WRITE_COMBINE);
                if (ret != VOS3_VMM_OK) {
                    if (off > 0) {
                        vos3_vmm_unmap_range((uintptr_t)user_vaddr, off);
                    }
                    shm_va_free(user_vaddr, shm->size);
                    __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);
                    vos3_mutex_unlock(&shm->lock);
                    return NULL;
                }
            }
        } else if (shm->flags & VOS3_SHM_FLAG_HUGETLB) {
            /* HugePage mapping: 2MB pages */
            for (size_t off = 0; off < shm->size; off += VOS3_LARGE_PAGE_SIZE) {
                int ret = vos3_vmm_map(user_vaddr + off, shm->phys_addr + off,
                                       VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_USER | VOS3_VMM_FLAG_LARGE);
                if (ret != VOS3_VMM_OK) {
                    /* Unmap any pages we already mapped */
                    for (size_t prev = 0; prev < off; prev += VOS3_LARGE_PAGE_SIZE) {
                        vos3_vmm_unmap_large(user_vaddr + prev);
                    }
                    shm_va_free(user_vaddr, shm->size);
                    __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);
                    vos3_mutex_unlock(&shm->lock);
                    return NULL;
                }
            }
        } else {
            /* Standard 4KB mapping */
            for (size_t off = 0; off < shm->size; off += VOS3_PAGE_SIZE) {
                uint64_t phys = shm->phys_addr + off;
                int ret = vos3_vmm_map_user(user_vaddr + off, phys,
                                             VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE | VOS3_PTE_USER);
                if (ret != 0) {
                    /* Unmap any pages we already mapped */
                    if (off > 0) {
                        vos3_vmm_unmap_range((uintptr_t)user_vaddr, off);
                    }
                    shm_va_free(user_vaddr, shm->size);
                    __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);
                    vos3_mutex_unlock(&shm->lock);
                    return NULL;
                }
            }
        }

        vos3_mutex_unlock(&shm->lock);

        /* Register in task's SHM tracking table for exit cleanup */
        if (current->shm_count < VOS3_TASK_MAX_SHM) {
            uint32_t idx = current->shm_count++;
            current->shm_mappings[idx].id = id;
            current->shm_mappings[idx].user_addr = user_vaddr;
            current->shm_mappings[idx].size = shm->size;
        }

        VOS3_DEBUG("SHM mapped id=%u to user addr 0x%llx (size=%zu)",
                   id, (unsigned long long)user_vaddr, shm->size);
        return (void*)user_vaddr;
    }

    /* Fallback: return kernel address for kernel tasks */
    void* addr = shm->kernel_addr;
    vos3_mutex_unlock(&shm->lock);

    return addr;
}

int vos3_shm_unmap(vos3_ipc_id_t id, void* addr)
{
    vos3_shm_region_t* shm = shm_get(id);
    if (shm == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    vos3_mutex_lock(&shm->lock);

    /* If a user-space address was provided, unmap those pages and recycle VA */
    vos3_task_t* current = vos3_sched_current();
    if (addr != NULL && current != NULL && current->address_space != NULL &&
        current->address_space != vos3_vmm_get_kernel_space()) {
        if (shm->flags & VOS3_SHM_FLAG_HUGETLB) {
            /* HugePage SHM: unmap each 2MB page individually */
            for (size_t off = 0; off < shm->size; off += VOS3_LARGE_PAGE_SIZE) {
                vos3_vmm_unmap_large((uintptr_t)addr + off);
            }
        } else {
            vos3_vmm_unmap_range((uintptr_t)addr, shm->size);
        }
        /* Recycle the user VA range for future SHM mappings */
        shm_va_free((uint64_t)(uintptr_t)addr, shm->size);

        /* Remove from task's SHM tracking table */
        for (uint32_t si = 0; si < current->shm_count; si++) {
            if (current->shm_mappings[si].id == id &&
                current->shm_mappings[si].user_addr == (uint64_t)(uintptr_t)addr) {
                /* Swap with last entry */
                current->shm_count--;
                if (si < current->shm_count) {
                    current->shm_mappings[si] = current->shm_mappings[current->shm_count];
                }
                break;
            }
        }
    }

    {
        uint32_t old_rc = __atomic_load_n(&shm->ref_count, __ATOMIC_ACQUIRE);
        if (old_rc > 0U) {
            __atomic_sub_fetch(&shm->ref_count, 1U, __ATOMIC_ACQ_REL);
        }
    }

    vos3_mutex_unlock(&shm->lock);

    return VOS3_IPC_OK;
}

/**
 * @brief Atomically decrement SHM ref_count during task exit cleanup.
 *
 * Called from vos3_task_reap() when a dying task has SHM mappings.
 * Uses atomic operations instead of mutex to avoid deadlock — the
 * reaper can run in any task context, including one that holds shm->lock.
 */
void vos3_shm_dec_refcount(vos3_ipc_id_t id)
{
    vos3_shm_region_t* shm = shm_get(id);
    if (shm == NULL) {
        return;
    }
    /* CAS loop: eliminates TOCTOU between load and sub */
    uint32_t old;
    do {
        old = __atomic_load_n(&shm->ref_count, __ATOMIC_ACQUIRE);
        if (old == 0U) return;
    } while (!__atomic_compare_exchange_n(&shm->ref_count, &old, old - 1U,
             0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE));
}

vos3_ipc_id_t vos3_shm_find(const char* name)
{
    if (name == NULL) {
        return VOS3_IPC_INVALID;
    }

    vos3_spinlock_lock(&g_shm_lock);

    for (vos3_ipc_id_t i = 1U; i < VOS3_SHM_MAX_REGIONS; i++) {
        vos3_shm_region_t* shm = g_shm_table[i];
        if (shm != NULL && shm->magic == VOS3_SHM_MAGIC) {
            if (strcmp(shm->name, name) == 0) {
                vos3_spinlock_unlock(&g_shm_lock);
                return i;
            }
        }
    }

    vos3_spinlock_unlock(&g_shm_lock);
    return VOS3_IPC_INVALID;
}

size_t vos3_shm_size(vos3_ipc_id_t id)
{
    vos3_shm_region_t* shm = shm_get(id);
    if (shm == NULL) {
        return 0U;
    }

    return shm->size;
}
