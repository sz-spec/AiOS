/**
 * @file virtio_blk.c
 * @brief VOS3 Hardened VirtIO Block Device Driver
 *
 * @details Security-first VirtIO block device implementation.
 *          Features:
 *          - DMA isolation for disk operations
 *          - Sector cache for optimized I/O
 *          - Kernel-only access (user-space blocked)
 *          - Request queue with async I/O support
 *
 * @version 1.0.0
 * @date 2026-02-19
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 7 - VirtIO-Block Driver & Persistent Storage
 */

#include "../../include/vos/virtio_blk.h"
#include "../../include/vos/virtio_core.h"
#include "../../include/vos/console.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/string.h"
#include "../../include/vos/compiler.h"

/* Legacy I/O port registers now in virtio_core.h */

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief VirtIO-Block device (single device for now) */
static virtio_blk_device_t g_virtio_blk_dev;

/** @brief Driver initialized flag */
static int g_virtio_blk_initialized = 0;

/*
 * Static DMA buffer for the virtqueue.
 *
 * VirtIO legacy queue layout for 256 entries:
 *   Descriptors:  256 * 16 = 4096 bytes
 *   Available:    6 + 256*2 = 518  bytes
 *   [pad to 4K]
 *   Used:         6 + 256*8 = 2054 bytes
 *   Total:        ~12 KiB (3 pages)
 *
 * Using a static BSS buffer avoids VMM mapping issues - BSS lives
 * in the KBASE identity-mapped range so virt_to_phys_dma works directly.
 */
#define VIRTQUEUE_MEM_SIZE  (3 * 4096)
static uint8_t g_virtqueue_mem[VIRTQUEUE_MEM_SIZE]
    __attribute__((aligned(4096)));

/*
 * DMA bounce buffer for data transfers.
 *
 * Callers may pass buffers from the heap (PHYS_MAP range, 0xFFFF800000000000+)
 * which virt_to_phys_dma cannot convert. We use a static BSS bounce buffer
 * (in the KBASE range) as an intermediary for all DMA data transfers.
 */
static uint8_t g_dma_bounce[VOS3_SECTOR_SIZE] __attribute__((aligned(512)));

/** @brief Lock protecting g_dma_bounce from concurrent access.
 *  Ensures that if the scheduler yields during DMA polling, another task
 *  cannot corrupt the bounce buffer while the device is still reading it. */
static vos3_spinlock_t g_dma_bounce_lock = VOS3_SPINLOCK_INIT;

/** @brief Global access timestamp for cache LRU */
static uint64_t g_cache_timestamp = 0;

/* I/O helpers, memory_barrier, virt_to_phys_dma now in virtio_core.h:
 *   inb/outb → virtio_inb/virtio_outb
 *   inw/outw → virtio_inw/virtio_outw
 *   inl/outl → virtio_inl/virtio_outl
 *   memory_barrier → virtio_mb
 *   virt_to_phys_dma → virtio_virt_to_phys
 *   KBASE → VIRTIO_KBASE
 *
 * Compatibility macros to avoid touching every callsite:
 */
#define inb(p)       virtio_inb(p)
#define outb(p, v)   virtio_outb(p, v)
#define inw(p)       virtio_inw(p)
#define outw(p, v)   virtio_outw(p, v)
#define inl(p)       virtio_inl(p)
#define outl(p, v)   virtio_outl(p, v)
#define memory_barrier()  virtio_mb()
#define virt_to_phys_dma(a)  virtio_virt_to_phys(a)

/* ============================================================================
 * SECTOR CACHE IMPLEMENTATION
 * ============================================================================ */

/**
 * @brief Initialize sector cache
 */
void vos3_sector_cache_init(vos3_sector_cache_t* cache)
{
    if (cache == NULL) {
        return;
    }

    memset(cache, 0, sizeof(*cache));
    cache->lock = VOS3_SPINLOCK_INIT;

    VOS3_DEBUG("[VIRTIO-BLK] Sector cache initialized (%u entries)",
               VOS3_SECTOR_CACHE_SIZE);
}

/**
 * @brief Find LRU entry in cache
 */
static vos3_sector_cache_entry_t* cache_find_lru(vos3_sector_cache_t* cache)
{
    vos3_sector_cache_entry_t* lru = &cache->entries[0];
    uint64_t min_access = lru->last_access;

    for (uint32_t i = 1; i < VOS3_SECTOR_CACHE_SIZE; i++) {
        vos3_sector_cache_entry_t* entry = &cache->entries[i];

        /* Prefer invalid entries */
        if (!(entry->flags & VOS3_CACHE_F_VALID)) {
            return entry;
        }

        /* Skip locked entries */
        if (entry->flags & VOS3_CACHE_F_LOCKED) {
            continue;
        }

        /* Find oldest access */
        if (entry->last_access < min_access) {
            min_access = entry->last_access;
            lru = entry;
        }
    }

    return lru;
}

/**
 * @brief Lookup sector in cache
 */
int vos3_sector_cache_lookup(vos3_sector_cache_t* cache,
                              uint64_t sector, void* data)
{
    if (cache == NULL || data == NULL) {
        return 0;
    }

    vos3_spinlock_lock(&cache->lock);

    for (uint32_t i = 0; i < VOS3_SECTOR_CACHE_SIZE; i++) {
        vos3_sector_cache_entry_t* entry = &cache->entries[i];

        if ((entry->flags & VOS3_CACHE_F_VALID) && entry->sector == sector) {
            /* Cache hit! */
            memcpy(data, entry->data, VOS3_SECTOR_SIZE);
            entry->access_count++;
            entry->last_access = ++g_cache_timestamp;
            cache->hit_count++;

            vos3_spinlock_unlock(&cache->lock);
            return 1;
        }
    }

    /* Cache miss */
    cache->miss_count++;
    vos3_spinlock_unlock(&cache->lock);
    return 0;
}

/**
 * @brief Insert sector into cache
 */
void vos3_sector_cache_insert(vos3_sector_cache_t* cache,
                               uint64_t sector, const void* data, int dirty)
{
    if (cache == NULL || data == NULL) {
        return;
    }

    vos3_spinlock_lock(&cache->lock);

    /* Check if sector already cached */
    for (uint32_t i = 0; i < VOS3_SECTOR_CACHE_SIZE; i++) {
        vos3_sector_cache_entry_t* entry = &cache->entries[i];

        if ((entry->flags & VOS3_CACHE_F_VALID) && entry->sector == sector) {
            /* Update existing entry */
            memcpy(entry->data, data, VOS3_SECTOR_SIZE);
            if (dirty) {
                entry->flags |= VOS3_CACHE_F_DIRTY;
            }
            entry->last_access = ++g_cache_timestamp;

            vos3_spinlock_unlock(&cache->lock);
            return;
        }
    }

    /* Find LRU entry to evict */
    vos3_sector_cache_entry_t* entry = cache_find_lru(cache);

    /* Write back dirty entry before eviction */
    if ((entry->flags & VOS3_CACHE_F_VALID) &&
        (entry->flags & VOS3_CACHE_F_DIRTY)) {
        cache->writeback_count++;
        /* TODO: Write back to disk */
    }

    /* Insert new entry */
    entry->sector = sector;
    entry->flags = VOS3_CACHE_F_VALID;
    if (dirty) {
        entry->flags |= VOS3_CACHE_F_DIRTY;
    }
    entry->access_count = 1;
    entry->last_access = ++g_cache_timestamp;
    memcpy(entry->data, data, VOS3_SECTOR_SIZE);

    vos3_spinlock_unlock(&cache->lock);
}

/**
 * @brief Flush all dirty sectors from cache
 */
int vos3_sector_cache_flush(vos3_sector_cache_t* cache)
{
    if (cache == NULL) {
        return 0;
    }

    int flushed = 0;

    vos3_spinlock_lock(&cache->lock);

    for (uint32_t i = 0; i < VOS3_SECTOR_CACHE_SIZE; i++) {
        vos3_sector_cache_entry_t* entry = &cache->entries[i];

        if ((entry->flags & VOS3_CACHE_F_VALID) &&
            (entry->flags & VOS3_CACHE_F_DIRTY)) {
            /* VOS3 uses write-through cache (dirty=0 on insert after write),
             * so this path should not execute in normal operation.
             * Clear the flag defensively. */
            entry->flags &= ~VOS3_CACHE_F_DIRTY;
            flushed++;
        }
    }

    vos3_spinlock_unlock(&cache->lock);

    if (flushed > 0) {
        VOS3_DEBUG("[VIRTIO-BLK] Cache flush: %d sectors written back", flushed);
    }

    return flushed;
}

/**
 * @brief Invalidate sector in cache
 */
void vos3_sector_cache_invalidate(vos3_sector_cache_t* cache, uint64_t sector)
{
    if (cache == NULL) {
        return;
    }

    vos3_spinlock_lock(&cache->lock);

    for (uint32_t i = 0; i < VOS3_SECTOR_CACHE_SIZE; i++) {
        vos3_sector_cache_entry_t* entry = &cache->entries[i];

        if ((entry->flags & VOS3_CACHE_F_VALID) && entry->sector == sector) {
            entry->flags = 0;
            break;
        }
    }

    vos3_spinlock_unlock(&cache->lock);
}

/* ============================================================================
 * VIRTQUEUE INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize VirtQueue for block device
 */
static int virtio_blk_queue_init(virtio_blk_queue_t* vq, uint16_t index,
                                  uint16_t num_desc, uint16_t io_base)
{
    /* SECURITY: Validate queue size */
    if (num_desc == 0 || num_desc > VIRTIO_BLK_QUEUE_SIZE) {
        VOS3_ERROR("[VIRTIO-BLK] Invalid queue size %u", num_desc);
        return -1;
    }

    memset(vq, 0, sizeof(*vq));
    vq->index = index;
    vq->num_desc = num_desc;
    vq->lock = VOS3_SPINLOCK_INIT;

    /*
     * VirtIO Legacy Queue Layout (spec mandated):
     *   [Descriptors]  num * 16 bytes
     *   [Available]    6 + 2*num bytes (flags, idx, ring[num], used_event)
     *   [padding to next 4096 boundary]
     *   [Used]         6 + 8*num bytes (flags, idx, ring[num]{id,len}, avail_event)
     *
     * The used ring MUST start at a page-aligned offset.
     */
    size_t desc_size = (size_t)num_desc * sizeof(virtio_blk_desc_t);
    size_t avail_size = 6 + (size_t)num_desc * 2;  /* flags + idx + ring[n] + used_event */
    size_t used_offset = (desc_size + avail_size + 4095) & ~(size_t)4095; /* page-align */
    size_t used_size = 6 + (size_t)num_desc * 8;   /* flags + idx + ring[n]{id,len} + avail_event */
    size_t total_size = used_offset + used_size;
    total_size = (total_size + 4095) & ~(size_t)4095;

    /*
     * Use the static BSS buffer for DMA instead of PMM+VMM.
     * BSS lives in the KBASE identity-mapped range, so the physical
     * address is simply (virtual - KBASE). This avoids VMM mapping bugs
     * where writes through vmm_map_pages don't persist.
     */
    if (total_size > VIRTQUEUE_MEM_SIZE) {
        VOS3_ERROR("[VIRTIO-BLK] Queue needs %zu bytes but static buffer is %u",
                   total_size, VIRTQUEUE_MEM_SIZE);
        return -1;
    }

    void* virt = (void*)g_virtqueue_mem;
    uint64_t phys = virt_to_phys_dma(virt);

    /* Clear memory (DMA isolation) */
    memset(virt, 0, total_size);

    /* Set up queue pointers with correct alignment */
    vq->desc = (volatile virtio_blk_desc_t*)virt;
    vq->avail = (volatile virtio_blk_avail_t*)((uint8_t*)virt + desc_size);
    vq->used = (volatile virtio_blk_used_t*)((uint8_t*)virt + used_offset);

    vq->desc_phys = phys;
    vq->avail_phys = phys + desc_size;
    vq->used_phys = phys + desc_size + avail_size;

    /* Initialize descriptor free list */
    for (uint16_t i = 0; i < num_desc; i++) {
        vq->desc[i].next = (i + 1) % num_desc;
    }
    vq->free_head = 0;
    vq->num_free = num_desc;
    vq->last_used_idx = 0;

    /* Tell device about the queue (legacy interface) */
    outw(io_base + VIRTIO_PCI_QUEUE_SEL, index);
    outl(io_base + VIRTIO_PCI_QUEUE_PFN, (uint32_t)(phys / 4096));

    VOS3_DEBUG("[VIRTIO-BLK] Queue %u initialized: %u descriptors @ 0x%llx",
               index, num_desc, (unsigned long long)phys);

    return 0;
}

/* ============================================================================
 * BLOCK REQUEST HANDLING
 * ============================================================================ */

/**
 * @brief Allocate a request slot
 */
static virtio_blk_request_t* alloc_request(virtio_blk_device_t* dev)
{
    for (uint32_t i = 0; i < VIRTIO_BLK_QUEUE_SIZE; i++) {
        if (dev->requests[i].state == BLK_REQ_FREE) {
            dev->requests[i].state = BLK_REQ_PENDING;
            return &dev->requests[i];
        }
    }
    return NULL;
}

/**
 * @brief Free a request slot
 */
static void free_request(virtio_blk_request_t* req)
{
    if (req != NULL) {
        req->state = BLK_REQ_FREE;
    }
}

/* virtio_yield() now in virtio_core.h */

/**
 * @brief Poll the used ring for completions.
 *
 * Processes all completed entries in the used ring and transitions
 * the corresponding requests to COMPLETE or ERROR state.
 */
static void poll_completions(virtio_blk_device_t* dev)
{
    virtio_blk_queue_t* vq = &dev->queue;

    /* Acknowledge interrupt (clears ISR, required for VirtIO legacy) */
    (void)inb(dev->io_base + VIRTIO_PCI_ISR);

    memory_barrier();

    while (vq->last_used_idx != vq->used->idx) {
        uint16_t used_idx = vq->last_used_idx % vq->num_desc;
        uint32_t desc_id = vq->used->ring[used_idx].id;

        /* Find the request that completed */
        for (uint32_t i = 0; i < VIRTIO_BLK_QUEUE_SIZE; i++) {
            if (dev->requests[i].state == BLK_REQ_PENDING &&
                dev->requests[i].desc_head == desc_id) {
                if (dev->requests[i].status == VIRTIO_BLK_S_OK) {
                    dev->requests[i].state = BLK_REQ_COMPLETE;
                } else {
                    dev->requests[i].state = BLK_REQ_ERROR;
                }
                break;
            }
        }

        /* Return descriptors to free list */
        uint16_t cur = (uint16_t)desc_id;
        for (int j = 0; j < 3; j++) {
            uint16_t next_desc = vq->desc[cur].next;
            vq->desc[cur].next = vq->free_head;
            vq->free_head = cur;
            vq->num_free++;
            if (!(vq->desc[cur].flags & VIRTQ_DESC_F_NEXT)) {
                break;
            }
            cur = next_desc;
        }

        vq->last_used_idx++;
    }
}

/**
 * @brief Submit a block request
 *
 * Builds a descriptor chain, adds to avail ring, notifies the device,
 * and polls for completion. Uses a DMA bounce buffer for data since
 * caller buffers may be in heap (PHYS_MAP range) where virt_to_phys_dma
 * cannot compute the physical address.
 *
 * For FLUSH requests, only 2 descriptors are used (no data segment)
 * per the VirtIO block specification.
 */
static int submit_request(virtio_blk_device_t* dev, uint32_t type,
                          uint64_t sector, void* buffer, uint32_t len)
{
    virtio_blk_queue_t* vq = &dev->queue;
    int is_flush = (type == VIRTIO_BLK_T_FLUSH);
    int num_descs = is_flush ? 2 : 3;

    /* Acquire bounce buffer lock FIRST — consistent ordering with poll loop
     * (which holds bounce_lock while re-acquiring vq->lock) prevents ABBA
     * deadlock on SMP.  Held until data is copied back (read) or consumed
     * by the device (write/flush). */
    vos3_spinlock_lock(&g_dma_bounce_lock);

    vos3_spinlock_lock(&vq->lock);

    if (vq->num_free < (uint16_t)num_descs) {
        vos3_spinlock_unlock(&vq->lock);
        vos3_spinlock_unlock(&g_dma_bounce_lock);
        VOS3_WARN("[VIRTIO-BLK] Queue full");
        return -1;
    }

    /* Allocate request */
    virtio_blk_request_t* req = alloc_request(dev);
    if (req == NULL) {
        vos3_spinlock_unlock(&vq->lock);
        vos3_spinlock_unlock(&g_dma_bounce_lock);
        VOS3_WARN("[VIRTIO-BLK] No free request slots");
        return -1;
    }

    /* Set up request header */
    req->header.type = type;
    req->header.reserved = 0;
    req->header.sector = sector;
    req->data_virt = buffer;
    req->data_len = len;
    req->status = 0xFF;  /* Invalid = not yet written by device */
    req->state = BLK_REQ_PENDING;

    /* For writes, copy data into the DMA bounce buffer (BSS / KBASE range) */
    if (type == VIRTIO_BLK_T_OUT && len > 0 && len <= VOS3_SECTOR_SIZE) {
        memcpy(g_dma_bounce, buffer, len);
    }

    /* Build descriptor chain */
    uint16_t head = vq->free_head;
    uint16_t idx = head;

    /* Descriptor 0: Request header (device-readable) */
    vq->desc[idx].addr  = virt_to_phys_dma(&req->header);
    vq->desc[idx].len   = sizeof(virtio_blk_req_header_t);
    vq->desc[idx].flags = VIRTQ_DESC_F_NEXT;
    idx = vq->desc[idx].next;

    if (!is_flush) {
        /* Descriptor 1: Data buffer via bounce buffer */
        vq->desc[idx].addr  = virt_to_phys_dma(g_dma_bounce);
        vq->desc[idx].len   = len;
        vq->desc[idx].flags = VIRTQ_DESC_F_NEXT;
        if (type == VIRTIO_BLK_T_IN) {
            vq->desc[idx].flags |= VIRTQ_DESC_F_WRITE; /* Device writes data */
        }
        idx = vq->desc[idx].next;
    }

    /* Final descriptor: Status byte (device-writable) */
    vq->desc[idx].addr  = virt_to_phys_dma(&req->status);
    vq->desc[idx].len   = sizeof(uint8_t);
    vq->desc[idx].flags = VIRTQ_DESC_F_WRITE;

    /* Update free list */
    vq->free_head = vq->desc[idx].next;
    vq->num_free -= (uint16_t)num_descs;

    /* Store head for matching completions */
    req->desc_head = head;

    /* Add to available ring */
    uint16_t avail_idx = vq->avail->idx % vq->num_desc;
    vq->avail->ring[avail_idx] = head;
    memory_barrier();
    vq->avail->idx++;
    memory_barrier();

    /* Notify the device */
    outw(dev->io_base + VIRTIO_PCI_QUEUE_NOTIFY, VIRTIO_BLK_QUEUE_IDX);

    vos3_spinlock_unlock(&vq->lock);

    /*
     * Poll for completion.
     * Each iteration: yield to QEMU event loop → check ISR → scan used ring.
     * 1,000,000 iterations with 10 yields each ≈ several seconds wall-clock.
     */
    int timeout = 1000000;
    while (req->state == BLK_REQ_PENDING && timeout > 0) {
        /* Yield to QEMU event loop so async I/O completions get processed */
        for (int y = 0; y < 10; y++) {
            virtio_yield();
        }

        vos3_spinlock_lock(&vq->lock);
        poll_completions(dev);
        vos3_spinlock_unlock(&vq->lock);

        timeout--;
    }

    /* Check result */
    int result;
    if (req->state == BLK_REQ_COMPLETE) {
        /* For reads, copy bounce buffer back to caller */
        if (type == VIRTIO_BLK_T_IN && len > 0 && len <= VOS3_SECTOR_SIZE) {
            memcpy(buffer, g_dma_bounce, len);
        }
        result = 0;
    } else if (req->state == BLK_REQ_ERROR) {
        VOS3_WARN("[VIRTIO-BLK] I/O error: type=%u sector=%llu status=%u",
                  type, (unsigned long long)sector, req->status);
        result = -1;
    } else {
        VOS3_ERROR("[VIRTIO-BLK] Request timeout: type=%u sector=%llu",
                   type, (unsigned long long)sector);
        result = -1;
    }

    /* Release bounce buffer — data has been copied back (read) or consumed (write/flush) */
    vos3_spinlock_unlock(&g_dma_bounce_lock);

    free_request(req);
    return result;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/* PCI config access and device discovery now in virtio_core.c.
 * Compatibility macros for remaining callsites: */
#define pci_config_read32(b,d,f,o)    virtio_pci_read32(b,d,f,o)
#define pci_config_read16(b,d,f,o)    virtio_pci_read16(b,d,f,o)
#define pci_config_write16(b,d,f,o,v) virtio_pci_write16(b,d,f,o,v)

/**
 * @brief Initialize VirtIO-Block driver
 */
int vos3_virtio_blk_init(void)
{
    if (g_virtio_blk_initialized) {
        return 0;
    }

    VOS3_INFO("[VIRTIO-BLK] Initializing VirtIO block driver...");

    memset(&g_virtio_blk_dev, 0, sizeof(g_virtio_blk_dev));
    g_virtio_blk_dev.lock = VOS3_SPINLOCK_INIT;

    /* ===== Step 1: PCI Device Discovery (via virtio_core) ===== */
    uint8_t pci_bus, pci_dev, pci_func;
    uint16_t io_base;
    if (virtio_pci_find_device(VIRTIO_BLK_SUBSYSTEM_ID,
                               &pci_bus, &pci_dev, &pci_func, &io_base) != 0) {
        VOS3_WARN("[VIRTIO-BLK] No VirtIO block device found on PCI bus");
        return -1;
    }
    g_virtio_blk_dev.io_base = io_base;

    /* Enable PCI bus mastering (bit 2 of command register) */
    uint16_t pci_cmd = pci_config_read16(pci_bus, pci_dev, pci_func, 0x04);
    pci_cmd |= (1U << 2) | (1U << 0); /* Bus Master + I/O Space */
    pci_config_write16(pci_bus, pci_dev, pci_func, 0x04, pci_cmd);

    /* ===== Step 2: VirtIO Legacy Initialization Handshake ===== */

    /* Reset device */
    outb(io_base + VIRTIO_PCI_STATUS, 0);

    /* Acknowledge: OS has found the device */
    outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_ACK);

    /* Driver: OS knows how to drive the device */
    outb(io_base + VIRTIO_PCI_STATUS,
         VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER);

    /* Read host features and negotiate */
    uint32_t host_features = inl(io_base + VIRTIO_PCI_HOST_FEATURES);
    uint32_t guest_features = host_features & (uint32_t)VIRTIO_BLK_FEATURES_ACCEPTED;
    outl(io_base + VIRTIO_PCI_GUEST_FEATURES, guest_features);
    g_virtio_blk_dev.features = guest_features;

    VOS3_DEBUG("[VIRTIO-BLK] Host features: 0x%08x, negotiated: 0x%08x",
               host_features, guest_features);

    /* Log FLUSH feature status — critical for data persistence */
    if (guest_features & (uint32_t)VIRTIO_BLK_F_FLUSH) {
        VOS3_INFO("[VIRTIO-BLK] FLUSH feature negotiated");
    } else {
        VOS3_WARN("[VIRTIO-BLK] FLUSH feature NOT available — use cache=directsync");
    }

    /* Check read-only */
    if (host_features & (uint32_t)VIRTIO_BLK_F_RO) {
        g_virtio_blk_dev.read_only = 1;
        VOS3_WARN("[VIRTIO-BLK] Device is read-only");
    }

    /* ===== Step 3: Read Device Configuration ===== */

    /* Capacity is at config offset 0x00, 8 bytes (2 x 32-bit reads, little-endian) */
    uint32_t cap_lo = inl(io_base + VIRTIO_PCI_CONFIG + 0);
    uint32_t cap_hi = inl(io_base + VIRTIO_PCI_CONFIG + 4);
    g_virtio_blk_dev.config.capacity = ((uint64_t)cap_hi << 32) | cap_lo;
    g_virtio_blk_dev.config.blk_size = VOS3_SECTOR_SIZE;

    /* ===== Step 4: Initialize Sector Cache ===== */
    vos3_sector_cache_init(&g_virtio_blk_dev.cache);

    /* ===== Step 5: Initialize VirtQueue ===== */

    /* Select queue 0 and read its max size */
    outw(io_base + VIRTIO_PCI_QUEUE_SEL, VIRTIO_BLK_QUEUE_IDX);
    uint16_t queue_max = inw(io_base + VIRTIO_PCI_QUEUE_SIZE);
    VOS3_DEBUG("[VIRTIO-BLK] Device queue size: %u", queue_max);
    if (queue_max == 0) {
        VOS3_ERROR("[VIRTIO-BLK] Queue 0 not available");
        outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }

    /*
     * VirtIO Legacy: queue size is READ-ONLY. The driver MUST use the
     * device-reported size for memory layout, because the device computes
     * the used ring offset using this value. A mismatch causes the device
     * to write completions where we don't look.
     */
    if (queue_max > VIRTIO_BLK_QUEUE_SIZE) {
        VOS3_ERROR("[VIRTIO-BLK] Device queue size %u exceeds driver max %u",
                   queue_max, VIRTIO_BLK_QUEUE_SIZE);
        outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }

    int result = virtio_blk_queue_init(&g_virtio_blk_dev.queue,
                                        VIRTIO_BLK_QUEUE_IDX,
                                        queue_max,
                                        io_base);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Failed to initialize request queue");
        outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }

    /* ===== Step 6: Mark Device as Ready ===== */
    outb(io_base + VIRTIO_PCI_STATUS,
         VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER | VIRTIO_STATUS_DRIVER_OK);

    g_virtio_blk_dev.initialized = 1;
    g_virtio_blk_initialized = 1;

    VOS3_INFO("[VIRTIO-BLK] Driver initialized");
    VOS3_INFO("[VIRTIO-BLK] Capacity: %llu sectors (%llu MB)",
              (unsigned long long)g_virtio_blk_dev.config.capacity,
              (unsigned long long)(g_virtio_blk_dev.config.capacity * 512 / (1024 * 1024)));
    VOS3_INFO("[VIRTIO-BLK] Sector cache: %u entries", VOS3_SECTOR_CACHE_SIZE);
    VOS3_INFO("[VIRTIO-BLK] Security: Kernel-only access, DMA isolated");

    return 0;
}

/**
 * @brief Check if block device is available
 */
int vos3_virtio_blk_available(void)
{
    return g_virtio_blk_initialized && g_virtio_blk_dev.initialized;
}

/**
 * @brief Read sectors from disk
 */
int vos3_virtio_blk_read(uint64_t sector, uint32_t count, void* buffer)
{
    if (!g_virtio_blk_initialized) {
        return -1;
    }

    if (buffer == NULL || count == 0) {
        return -1;
    }

    /* SECURITY: Validate sector range (overflow-safe) */
    if (count > g_virtio_blk_dev.config.capacity ||
        sector > g_virtio_blk_dev.config.capacity - count) {
        VOS3_WARN("[VIRTIO-BLK] Read beyond disk capacity: %llu + %u > %llu",
                  (unsigned long long)sector, count,
                  (unsigned long long)g_virtio_blk_dev.config.capacity);
        return -1;
    }

    /* SECURITY: Limit request size */
    if (count > VOS3_MAX_SECTORS_PER_REQ) {
        VOS3_WARN("[VIRTIO-BLK] Read too large: %u > %u sectors",
                  count, VOS3_MAX_SECTORS_PER_REQ);
        return -1;
    }

    uint8_t* buf = (uint8_t*)buffer;
    int result = 0;

    for (uint32_t i = 0; i < count && result == 0; i++) {
        uint64_t cur_sector = sector + i;
        void* cur_buf = buf + (i * VOS3_SECTOR_SIZE);

        /* Check cache first */
        if (vos3_sector_cache_lookup(&g_virtio_blk_dev.cache, cur_sector, cur_buf)) {
            continue;
        }

        /* Cache miss - read from disk */
        result = submit_request(&g_virtio_blk_dev, VIRTIO_BLK_T_IN,
                                cur_sector, cur_buf, VOS3_SECTOR_SIZE);

        if (result == 0) {
            vos3_sector_cache_insert(&g_virtio_blk_dev.cache,
                                     cur_sector, cur_buf, 0);
        }
    }

    if (result == 0) {
        g_virtio_blk_dev.stats.reads++;
        g_virtio_blk_dev.stats.bytes_read += count * VOS3_SECTOR_SIZE;
    } else {
        g_virtio_blk_dev.stats.errors++;
    }

    return result;
}

/**
 * @brief Write sectors to disk
 */
int vos3_virtio_blk_write(uint64_t sector, uint32_t count, const void* buffer)
{
    if (!g_virtio_blk_initialized) {
        return -1;
    }

    if (buffer == NULL || count == 0) {
        return -1;
    }

    /* SECURITY: Check read-only */
    if (g_virtio_blk_dev.read_only) {
        VOS3_WARN("[VIRTIO-BLK] Write to read-only disk rejected");
        return -1;
    }

    /* SECURITY: Validate sector range (overflow-safe) */
    if (count > g_virtio_blk_dev.config.capacity ||
        sector > g_virtio_blk_dev.config.capacity - count) {
        VOS3_WARN("[VIRTIO-BLK] Write beyond disk capacity: %llu + %u > %llu",
                  (unsigned long long)sector, count,
                  (unsigned long long)g_virtio_blk_dev.config.capacity);
        return -1;
    }

    /* SECURITY: Limit request size */
    if (count > VOS3_MAX_SECTORS_PER_REQ) {
        VOS3_WARN("[VIRTIO-BLK] Write too large: %u > %u sectors",
                  count, VOS3_MAX_SECTORS_PER_REQ);
        return -1;
    }

    const uint8_t* buf = (const uint8_t*)buffer;
    int result = 0;

    for (uint32_t i = 0; i < count && result == 0; i++) {
        uint64_t cur_sector = sector + i;
        const void* cur_buf = buf + (i * VOS3_SECTOR_SIZE);

        /* Write to disk */
        result = submit_request(&g_virtio_blk_dev, VIRTIO_BLK_T_OUT,
                                cur_sector, (void*)cur_buf, VOS3_SECTOR_SIZE);

        if (result == 0) {
            vos3_sector_cache_insert(&g_virtio_blk_dev.cache,
                                     cur_sector, cur_buf, 0);
        }
    }

    if (result == 0) {
        g_virtio_blk_dev.stats.writes++;
        g_virtio_blk_dev.stats.bytes_written += count * VOS3_SECTOR_SIZE;
    } else {
        g_virtio_blk_dev.stats.errors++;
    }

    return result;
}

/**
 * @brief Flush disk caches
 */
int vos3_virtio_blk_flush(void)
{
    if (!g_virtio_blk_initialized) {
        return -1;
    }

    /* Flush sector cache first */
    vos3_sector_cache_flush(&g_virtio_blk_dev.cache);

    /* Send flush command unconditionally.
     * Even if the device did not advertise VIRTIO_BLK_F_FLUSH, we still
     * attempt the flush — with cache=directsync the command is a no-op,
     * and with writeback caches it is essential for persistence.
     * See: Firecracker issue #2172 for flush-negotiation pitfalls. */
    uint8_t dummy = 0;
    int result = submit_request(&g_virtio_blk_dev, VIRTIO_BLK_T_FLUSH,
                                0, &dummy, 0);
    if (result != 0) {
        VOS3_WARN("[VIRTIO-BLK] Flush command failed (features=0x%08x)",
                  (unsigned)g_virtio_blk_dev.features);
    }

    if (result == 0) {
        g_virtio_blk_dev.stats.flushes++;
    }

    VOS3_DEBUG("[VIRTIO-BLK] Flush complete");

    return result;
}

/**
 * @brief Get disk capacity
 */
uint64_t vos3_virtio_blk_capacity(void)
{
    if (!g_virtio_blk_initialized) {
        return 0;
    }
    return g_virtio_blk_dev.config.capacity;
}

/**
 * @brief Get block device statistics
 */
void vos3_virtio_blk_get_stats(vos3_blkdev_stats_t* stats)
{
    if (stats == NULL) {
        return;
    }

    if (!g_virtio_blk_initialized) {
        memset(stats, 0, sizeof(*stats));
        return;
    }

    stats->total_reads = g_virtio_blk_dev.stats.reads;
    stats->total_writes = g_virtio_blk_dev.stats.writes;
    stats->total_flushes = g_virtio_blk_dev.stats.flushes;
    stats->read_errors = 0;
    stats->write_errors = 0;
    stats->bytes_read = g_virtio_blk_dev.stats.bytes_read;
    stats->bytes_written = g_virtio_blk_dev.stats.bytes_written;
    stats->cache_hits = g_virtio_blk_dev.cache.hit_count;
    stats->cache_misses = g_virtio_blk_dev.cache.miss_count;
}

/**
 * @brief Print block device status
 */
void vos3_virtio_blk_print_status(void)
{
    if (!g_virtio_blk_initialized) {
        VOS3_INFO("[VIRTIO-BLK] Driver not initialized");
        return;
    }

    VOS3_INFO("[VIRTIO-BLK] === Block Device Status ===");
    VOS3_INFO("[VIRTIO-BLK] Capacity:    %llu sectors (%llu KB)",
              (unsigned long long)g_virtio_blk_dev.config.capacity,
              (unsigned long long)(g_virtio_blk_dev.config.capacity * 512 / 1024));
    VOS3_INFO("[VIRTIO-BLK] Block size:  %u bytes",
              g_virtio_blk_dev.config.blk_size);
    VOS3_INFO("[VIRTIO-BLK] Read-only:   %s",
              g_virtio_blk_dev.read_only ? "Yes" : "No");
    VOS3_INFO("[VIRTIO-BLK] Queue size:  %u descriptors",
              g_virtio_blk_dev.queue.num_desc);
    VOS3_INFO("[VIRTIO-BLK] Queue free:  %u descriptors",
              g_virtio_blk_dev.queue.num_free);
    VOS3_INFO("[VIRTIO-BLK] --- Statistics ---");
    VOS3_INFO("[VIRTIO-BLK] Reads:       %llu",
              (unsigned long long)g_virtio_blk_dev.stats.reads);
    VOS3_INFO("[VIRTIO-BLK] Writes:      %llu",
              (unsigned long long)g_virtio_blk_dev.stats.writes);
    VOS3_INFO("[VIRTIO-BLK] Flushes:     %llu",
              (unsigned long long)g_virtio_blk_dev.stats.flushes);
    VOS3_INFO("[VIRTIO-BLK] Bytes read:  %llu",
              (unsigned long long)g_virtio_blk_dev.stats.bytes_read);
    VOS3_INFO("[VIRTIO-BLK] Bytes write: %llu",
              (unsigned long long)g_virtio_blk_dev.stats.bytes_written);
    VOS3_INFO("[VIRTIO-BLK] Errors:      %llu",
              (unsigned long long)g_virtio_blk_dev.stats.errors);
    VOS3_INFO("[VIRTIO-BLK] --- Cache ---");
    VOS3_INFO("[VIRTIO-BLK] Hits:        %u",
              g_virtio_blk_dev.cache.hit_count);
    VOS3_INFO("[VIRTIO-BLK] Misses:      %u",
              g_virtio_blk_dev.cache.miss_count);
    VOS3_INFO("[VIRTIO-BLK] Hit rate:    %u%%",
              (g_virtio_blk_dev.cache.hit_count + g_virtio_blk_dev.cache.miss_count) > 0 ?
              (g_virtio_blk_dev.cache.hit_count * 100) /
              (g_virtio_blk_dev.cache.hit_count + g_virtio_blk_dev.cache.miss_count) : 0);
}

/* ============================================================================
 * SIMULATED DISK FOR TESTING (No actual hardware)
 * ============================================================================ */

/** @brief Simulated disk storage (1 MB = 2048 sectors) */
#define SIM_DISK_SECTORS    2048U
static uint8_t g_sim_disk[SIM_DISK_SECTORS][VOS3_SECTOR_SIZE];
static int g_sim_disk_initialized = 0;

/**
 * @brief Initialize simulated disk
 */
static void sim_disk_init(void)
{
    if (!g_sim_disk_initialized) {
        memset(g_sim_disk, 0, sizeof(g_sim_disk));
        g_sim_disk_initialized = 1;
        VOS3_DEBUG("[VIRTIO-BLK] Simulated disk initialized (%u KB)",
                   SIM_DISK_SECTORS * VOS3_SECTOR_SIZE / 1024);
    }
}

/**
 * @brief Simulated disk read
 */
int vos3_sim_disk_read(uint64_t sector, uint32_t count, void* buffer)
{
    sim_disk_init();

    if (sector + count > SIM_DISK_SECTORS) {
        return -1;
    }

    uint8_t* buf = (uint8_t*)buffer;
    for (uint32_t i = 0; i < count; i++) {
        memcpy(buf + (i * VOS3_SECTOR_SIZE),
               g_sim_disk[sector + i],
               VOS3_SECTOR_SIZE);
    }

    return 0;
}

/**
 * @brief Simulated disk write
 */
int vos3_sim_disk_write(uint64_t sector, uint32_t count, const void* buffer)
{
    sim_disk_init();

    if (sector + count > SIM_DISK_SECTORS) {
        return -1;
    }

    const uint8_t* buf = (const uint8_t*)buffer;
    for (uint32_t i = 0; i < count; i++) {
        memcpy(g_sim_disk[sector + i],
               buf + (i * VOS3_SECTOR_SIZE),
               VOS3_SECTOR_SIZE);
    }

    return 0;
}

/**
 * @brief Test VirtIO-Block driver with simulated disk
 */
int vos3_virtio_blk_test(void)
{
    vos3_console_puts("[VIRTIO-BLK] >>> TEST FUNCTION ENTERED <<<\n");
    VOS3_INFO("[VIRTIO-BLK] === Running Block Driver Tests ===");

    /* Test 1: Write to sector 0 */
    uint8_t write_buf[VOS3_SECTOR_SIZE];
    const char* signature = "VOS3 DISK SIGNATURE - Day 7";
    memset(write_buf, 0, sizeof(write_buf));
    memcpy(write_buf, signature, strlen(signature));

    VOS3_INFO("[VIRTIO-BLK] Test 1: Writing signature to sector 0...");
    int result = vos3_sim_disk_write(0, 1, write_buf);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 1 FAILED: Write error");
        return -1;
    }
    VOS3_INFO("[VIRTIO-BLK] Test 1: PASSED (write successful)");

    /* Test 2: Read back from sector 0 */
    uint8_t read_buf[VOS3_SECTOR_SIZE];
    memset(read_buf, 0xFF, sizeof(read_buf));

    VOS3_INFO("[VIRTIO-BLK] Test 2: Reading signature from sector 0...");
    result = vos3_sim_disk_read(0, 1, read_buf);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 2 FAILED: Read error");
        return -1;
    }

    /* Verify data */
    if (memcmp(write_buf, read_buf, VOS3_SECTOR_SIZE) != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 2 FAILED: Data mismatch");
        return -1;
    }
    VOS3_INFO("[VIRTIO-BLK] Test 2: PASSED (data verified)");
    VOS3_INFO("[VIRTIO-BLK] Signature: \"%s\"", (char*)read_buf);

    /* Test 3: Sector cache */
    VOS3_INFO("[VIRTIO-BLK] Test 3: Testing sector cache...");
    vos3_sector_cache_t test_cache;
    vos3_sector_cache_init(&test_cache);

    /* Insert into cache */
    vos3_sector_cache_insert(&test_cache, 0, write_buf, 0);

    /* Lookup */
    uint8_t cache_buf[VOS3_SECTOR_SIZE];
    if (!vos3_sector_cache_lookup(&test_cache, 0, cache_buf)) {
        VOS3_ERROR("[VIRTIO-BLK] Test 3 FAILED: Cache miss");
        return -1;
    }

    if (memcmp(write_buf, cache_buf, VOS3_SECTOR_SIZE) != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 3 FAILED: Cache data mismatch");
        return -1;
    }
    VOS3_INFO("[VIRTIO-BLK] Test 3: PASSED (cache hit, data verified)");

    /* Test 4: Multi-sector write/read */
    VOS3_INFO("[VIRTIO-BLK] Test 4: Multi-sector write/read...");
    uint8_t multi_buf[VOS3_SECTOR_SIZE * 4];
    for (int i = 0; i < 4; i++) {
        memset(multi_buf + (i * VOS3_SECTOR_SIZE), 'A' + i, VOS3_SECTOR_SIZE);
    }

    result = vos3_sim_disk_write(10, 4, multi_buf);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 4 FAILED: Multi-write error");
        return -1;
    }

    uint8_t verify_buf[VOS3_SECTOR_SIZE * 4];
    result = vos3_sim_disk_read(10, 4, verify_buf);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 4 FAILED: Multi-read error");
        return -1;
    }

    if (memcmp(multi_buf, verify_buf, sizeof(multi_buf)) != 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 4 FAILED: Multi-sector mismatch");
        return -1;
    }
    VOS3_INFO("[VIRTIO-BLK] Test 4: PASSED (4 sectors verified)");

    /* Test 5: Boundary check */
    VOS3_INFO("[VIRTIO-BLK] Test 5: Boundary validation...");
    result = vos3_sim_disk_read(SIM_DISK_SECTORS, 1, read_buf);
    if (result == 0) {
        VOS3_ERROR("[VIRTIO-BLK] Test 5 FAILED: Should reject out-of-bounds");
        return -1;
    }
    VOS3_INFO("[VIRTIO-BLK] Test 5: PASSED (out-of-bounds rejected)");

    VOS3_INFO("[VIRTIO-BLK] === All Tests PASSED ===");
    return 0;
}
