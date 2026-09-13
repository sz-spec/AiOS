/**
 * @file virtio_blk.h
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
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_VIRTIO_BLK_H
#define VOS3_VIRTIO_BLK_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "sync.h"
#include "compiler.h"
#include "virtio_core.h"

/* ============================================================================
 * VIRTIO-BLK CONSTANTS (from VirtIO 1.1 Specification)
 * ============================================================================ */

/** @brief VirtIO-Block PCI device ID (transitional) */
#define VIRTIO_BLK_DEVICE_ID            0x1001U

/** @brief VirtIO-Block PCI device ID (modern) */
#define VIRTIO_BLK_DEVICE_ID_MODERN     0x1042U

/** @brief VirtIO-Block subsystem ID */
#define VIRTIO_BLK_SUBSYSTEM_ID         2U

/* ============================================================================
 * VIRTIO-BLK FEATURE BITS
 * ============================================================================ */

/** @brief Maximum size of any single segment is in size_max */
#define VIRTIO_BLK_F_SIZE_MAX           (1ULL << 1)

/** @brief Maximum number of segments in a request is in seg_max */
#define VIRTIO_BLK_F_SEG_MAX            (1ULL << 2)

/** @brief Disk-style geometry specified in geometry */
#define VIRTIO_BLK_F_GEOMETRY           (1ULL << 4)

/** @brief Device is read-only */
#define VIRTIO_BLK_F_RO                 (1ULL << 5)

/** @brief Block size of disk is in blk_size */
#define VIRTIO_BLK_F_BLK_SIZE           (1ULL << 6)

/** @brief Cache flush command support */
#define VIRTIO_BLK_F_FLUSH              (1ULL << 9)

/** @brief Device exports information on topology */
#define VIRTIO_BLK_F_TOPOLOGY           (1ULL << 10)

/** @brief Device can toggle cache between writeback and writethrough */
#define VIRTIO_BLK_F_CONFIG_WCE         (1ULL << 11)

/** @brief Device can support discard command */
#define VIRTIO_BLK_F_DISCARD            (1ULL << 13)

/** @brief Device can support write zeroes command */
#define VIRTIO_BLK_F_WRITE_ZEROES       (1ULL << 14)

/**
 * @brief Features we ACCEPT (minimal attack surface)
 *
 * Security Policy: Accept only essential features for basic disk I/O.
 * Reject advanced features like DISCARD, WRITE_ZEROES to reduce attack surface.
 */
#define VIRTIO_BLK_FEATURES_ACCEPTED \
    (VIRTIO_BLK_F_SIZE_MAX | VIRTIO_BLK_F_SEG_MAX | \
     VIRTIO_BLK_F_BLK_SIZE | VIRTIO_BLK_F_FLUSH | VIRTIO_BLK_F_RO)

/* ============================================================================
 * VIRTIO-BLK REQUEST TYPES
 * ============================================================================ */

/** @brief Read sectors from disk */
#define VIRTIO_BLK_T_IN                 0U

/** @brief Write sectors to disk */
#define VIRTIO_BLK_T_OUT                1U

/** @brief Flush volatile caches */
#define VIRTIO_BLK_T_FLUSH              4U

/** @brief Get device ID (optional) */
#define VIRTIO_BLK_T_GET_ID             8U

/** @brief Discard sectors (optional) */
#define VIRTIO_BLK_T_DISCARD            11U

/** @brief Write zeroes (optional) */
#define VIRTIO_BLK_T_WRITE_ZEROES       13U

/* ============================================================================
 * VIRTIO-BLK STATUS VALUES
 * ============================================================================ */

/** @brief Request completed successfully */
#define VIRTIO_BLK_S_OK                 0U

/** @brief I/O error occurred */
#define VIRTIO_BLK_S_IOERR              1U

/** @brief Request unsupported by device */
#define VIRTIO_BLK_S_UNSUPP             2U

/* VIRTIO_STATUS_* and VIRTQ_DESC_F_* now in virtio_core.h */

/** @brief Maximum number of descriptors in queue (must match QEMU default: 256) */
#define VIRTIO_BLK_QUEUE_SIZE           256U

/** @brief Request queue index (only one queue for block devices) */
#define VIRTIO_BLK_QUEUE_IDX            0U

/* ============================================================================
 * SECTOR CACHE CONSTANTS
 * ============================================================================ */

/** @brief Standard sector size in bytes */
#define VOS3_SECTOR_SIZE                512U

/** @brief Maximum sectors per request */
#define VOS3_MAX_SECTORS_PER_REQ        256U

/** @brief Sector cache size (number of cached sectors) */
#define VOS3_SECTOR_CACHE_SIZE          64U

/** @brief Cache entry flags: Valid data */
#define VOS3_CACHE_F_VALID              0x01U

/** @brief Cache entry flags: Dirty (needs writeback) */
#define VOS3_CACHE_F_DIRTY              0x02U

/** @brief Cache entry flags: Locked (in use) */
#define VOS3_CACHE_F_LOCKED             0x04U

/* ============================================================================
 * VIRTQUEUE STRUCTURES
 * ============================================================================ */

/**
 * @brief VirtQueue descriptor (16 bytes)
 */
typedef struct virtio_blk_desc {
    uint64_t addr;      /**< Physical address of buffer */
    uint32_t len;       /**< Length of buffer */
    uint16_t flags;     /**< Descriptor flags */
    uint16_t next;      /**< Next descriptor index (if NEXT flag set) */
} __attribute__((packed)) virtio_blk_desc_t;

/**
 * @brief VirtQueue available ring
 */
typedef struct virtio_blk_avail {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[VIRTIO_BLK_QUEUE_SIZE];
    uint16_t used_event;
} __attribute__((packed)) virtio_blk_avail_t;

/**
 * @brief VirtQueue used ring element
 */
typedef struct virtio_blk_used_elem {
    uint32_t id;        /**< Descriptor head index */
    uint32_t len;       /**< Number of bytes written */
} __attribute__((packed)) virtio_blk_used_elem_t;

/**
 * @brief VirtQueue used ring
 */
typedef struct virtio_blk_used {
    uint16_t flags;
    uint16_t idx;
    virtio_blk_used_elem_t ring[VIRTIO_BLK_QUEUE_SIZE];
    uint16_t avail_event;
} __attribute__((packed)) virtio_blk_used_t;

/* ============================================================================
 * VIRTIO-BLK REQUEST STRUCTURE
 * ============================================================================ */

/**
 * @brief VirtIO block request header
 *
 * This structure precedes the data in every request.
 */
typedef struct virtio_blk_req_header {
    uint32_t type;      /**< Request type (IN/OUT/FLUSH/etc) */
    uint32_t reserved;  /**< Reserved (must be 0) */
    uint64_t sector;    /**< Starting sector number */
} __attribute__((packed)) virtio_blk_req_header_t;

/**
 * @brief VirtIO block request status
 *
 * This structure follows the data in every response.
 */
typedef struct virtio_blk_req_status {
    uint8_t status;     /**< Status code (OK/IOERR/UNSUPP) */
} __attribute__((packed)) virtio_blk_req_status_t;

/**
 * @brief Block request state
 */
typedef enum {
    BLK_REQ_FREE = 0,       /**< Request slot is free */
    BLK_REQ_PENDING,        /**< Request submitted, waiting */
    BLK_REQ_COMPLETE,       /**< Request completed */
    BLK_REQ_ERROR           /**< Request failed */
} virtio_blk_req_state_t;

/**
 * @brief Full block request structure (for tracking)
 */
typedef struct virtio_blk_request {
    /** @brief Request header (16 bytes) */
    virtio_blk_req_header_t header;

    /** @brief Data buffer physical address */
    uint64_t data_phys;

    /** @brief Data buffer virtual address */
    void* data_virt;

    /** @brief Data length in bytes */
    uint32_t data_len;

    /** @brief Status byte (written by device) */
    uint8_t status;

    /** @brief Request state */
    volatile virtio_blk_req_state_t state;

    /** @brief Descriptor head index */
    uint16_t desc_head;

    /** @brief Padding for alignment */
    uint8_t _pad[5];
} __attribute__((aligned(64))) virtio_blk_request_t;

/* ============================================================================
 * SECTOR CACHE STRUCTURE
 * ============================================================================ */

/**
 * @brief Sector cache entry
 */
typedef struct vos3_sector_cache_entry {
    uint64_t sector;        /**< Sector number */
    uint32_t flags;         /**< Cache flags */
    uint32_t access_count;  /**< Access counter for LRU */
    uint64_t last_access;   /**< Last access timestamp */
    uint8_t data[VOS3_SECTOR_SIZE]; /**< Cached sector data */
} __attribute__((aligned(64))) vos3_sector_cache_entry_t;

/**
 * @brief Sector cache
 */
typedef struct vos3_sector_cache {
    vos3_sector_cache_entry_t entries[VOS3_SECTOR_CACHE_SIZE];
    uint32_t hit_count;
    uint32_t miss_count;
    uint32_t writeback_count;
    vos3_spinlock_t lock;
} vos3_sector_cache_t;

/* ============================================================================
 * VIRTIO-BLK DEVICE CONFIG STRUCTURE
 * ============================================================================ */

/**
 * @brief VirtIO block device configuration space
 */
typedef struct virtio_blk_config {
    uint64_t capacity;      /**< Number of 512-byte sectors */
    uint32_t size_max;      /**< Maximum segment size */
    uint32_t seg_max;       /**< Maximum number of segments */
    struct {
        uint16_t cylinders;
        uint8_t heads;
        uint8_t sectors;
    } geometry;
    uint32_t blk_size;      /**< Block size (512 typical) */
    /* Additional fields for topology, etc. */
} __attribute__((packed)) virtio_blk_config_t;

/* ============================================================================
 * VIRTIO-BLK DEVICE STRUCTURE
 * ============================================================================ */

/**
 * @brief VirtQueue structure for block device
 */
typedef struct virtio_blk_queue {
    /** @brief Queue index */
    uint16_t index;

    /** @brief Number of descriptors */
    uint16_t num_desc;

    /** @brief Descriptors (DMA memory) */
    volatile virtio_blk_desc_t* desc;

    /** @brief Available ring (DMA memory) */
    volatile virtio_blk_avail_t* avail;

    /** @brief Used ring (DMA memory) */
    volatile virtio_blk_used_t* used;

    /** @brief Physical addresses */
    uint64_t desc_phys;
    uint64_t avail_phys;
    uint64_t used_phys;

    /** @brief Free descriptor head */
    uint16_t free_head;

    /** @brief Number of free descriptors */
    uint16_t num_free;

    /** @brief Last seen used index */
    uint16_t last_used_idx;

    /** @brief Lock for queue access */
    vos3_spinlock_t lock;
} virtio_blk_queue_t;

/**
 * @brief VirtIO block device structure
 */
typedef struct virtio_blk_device {
    /** @brief Device initialized flag */
    int initialized;

    /** @brief Device is read-only */
    int read_only;

    /** @brief Negotiated features */
    uint64_t features;

    /** @brief MMIO base address (for modern devices) */
    volatile void* mmio_base;

    /** @brief I/O port base (for legacy devices) */
    uint16_t io_base;

    /** @brief Device configuration */
    virtio_blk_config_t config;

    /** @brief Request queue */
    virtio_blk_queue_t queue;

    /** @brief Request pool */
    virtio_blk_request_t requests[VIRTIO_BLK_QUEUE_SIZE];

    /** @brief Sector cache */
    vos3_sector_cache_t cache;

    /** @brief Statistics */
    struct {
        uint64_t reads;
        uint64_t writes;
        uint64_t flushes;
        uint64_t errors;
        uint64_t bytes_read;
        uint64_t bytes_written;
    } stats;

    /** @brief Device lock */
    vos3_spinlock_t lock;
} virtio_blk_device_t;

/* ============================================================================
 * BLOCK DEVICE STATISTICS
 * ============================================================================ */

/**
 * @brief Block device statistics
 */
typedef struct vos3_blkdev_stats {
    uint64_t total_reads;
    uint64_t total_writes;
    uint64_t total_flushes;
    uint64_t read_errors;
    uint64_t write_errors;
    uint64_t bytes_read;
    uint64_t bytes_written;
    uint64_t cache_hits;
    uint64_t cache_misses;
} vos3_blkdev_stats_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize VirtIO-Block driver
 *
 * Scans for VirtIO block devices and initializes the first one found.
 *
 * @return 0 on success, negative error on failure
 */
int vos3_virtio_blk_init(void);

/**
 * @brief Check if block device is available
 *
 * @return 1 if available, 0 if not
 */
int vos3_virtio_blk_available(void);

/**
 * @brief Read sectors from disk
 *
 * SECURITY: This function is kernel-only. User-space must use VFS.
 *
 * @param[in]  sector   Starting sector number
 * @param[in]  count    Number of sectors to read
 * @param[out] buffer   Output buffer (must be count * 512 bytes)
 * @return 0 on success, negative error on failure
 */
int vos3_virtio_blk_read(uint64_t sector, uint32_t count, void* buffer);

/**
 * @brief Write sectors to disk
 *
 * SECURITY: This function is kernel-only. User-space must use VFS.
 *
 * @param[in] sector   Starting sector number
 * @param[in] count    Number of sectors to write
 * @param[in] buffer   Input buffer (must be count * 512 bytes)
 * @return 0 on success, negative error on failure
 */
int vos3_virtio_blk_write(uint64_t sector, uint32_t count, const void* buffer);

/**
 * @brief Flush disk caches
 *
 * Ensures all pending writes are committed to disk.
 *
 * @return 0 on success, negative error on failure
 */
int vos3_virtio_blk_flush(void);

/**
 * @brief Get disk capacity
 *
 * @return Number of sectors, or 0 if no disk
 */
uint64_t vos3_virtio_blk_capacity(void);

/**
 * @brief Get block device statistics
 *
 * @param[out] stats   Statistics output
 */
void vos3_virtio_blk_get_stats(vos3_blkdev_stats_t* stats);

/**
 * @brief Print block device status
 */
void vos3_virtio_blk_print_status(void);

/* ============================================================================
 * SECTOR CACHE API
 * ============================================================================ */

/**
 * @brief Initialize sector cache
 *
 * @param[in] cache   Cache to initialize
 */
void vos3_sector_cache_init(vos3_sector_cache_t* cache);

/**
 * @brief Lookup sector in cache
 *
 * @param[in]  cache   Sector cache
 * @param[in]  sector  Sector number to find
 * @param[out] data    Output buffer (512 bytes)
 * @return 1 if found (hit), 0 if not found (miss)
 */
int vos3_sector_cache_lookup(vos3_sector_cache_t* cache,
                              uint64_t sector, void* data);

/**
 * @brief Insert sector into cache
 *
 * @param[in] cache   Sector cache
 * @param[in] sector  Sector number
 * @param[in] data    Sector data (512 bytes)
 * @param[in] dirty   Mark as dirty (needs writeback)
 */
void vos3_sector_cache_insert(vos3_sector_cache_t* cache,
                               uint64_t sector, const void* data, int dirty);

/**
 * @brief Flush all dirty sectors from cache
 *
 * @param[in] cache   Sector cache
 * @return Number of sectors flushed
 */
int vos3_sector_cache_flush(vos3_sector_cache_t* cache);

/**
 * @brief Invalidate sector in cache
 *
 * @param[in] cache   Sector cache
 * @param[in] sector  Sector number to invalidate
 */
void vos3_sector_cache_invalidate(vos3_sector_cache_t* cache, uint64_t sector);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VIRTIO_BLK_H */
