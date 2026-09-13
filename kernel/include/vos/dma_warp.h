/**
 * @file dma_warp.h
 * @brief Phase 9: Warp-Drive DMA -- Direct model-to-slot zero-copy transfers
 *
 * @details Bypasses CPU cache hierarchy for bulk data movement between
 *          ivshmem Warp Drive zones and AI model HugePage slots.
 *          Uses non-temporal stores (movnti) and cache-line aligned
 *          transfers for maximum write-combining throughput.
 *
 *          Transfer modes:
 *          - ivshmem-to-slot: Host writes model data to ivshmem zone,
 *            DMA engine copies directly to HugePage-backed AI slot
 *          - phys-to-slot: Copy from any physical address to AI slot
 *
 *          All transfers use sfence for store ordering and CRC32C
 *          for integrity verification.
 *
 * @version 1.0.0
 * @date 2026-04-09
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 9: Bare-Metal Peak (v23.0)
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_DMA_WARP_H
#define VOS3_DMA_WARP_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * DMA WARP CONFIGURATION
 * ============================================================================ */

/** @brief Transfer chunk size for non-temporal copy (64 KB) */
#define VOS3_DMA_WARP_CHUNK_SIZE    (64U * 1024U)

/** @brief Cache line size (x86_64 standard) */
#define VOS3_DMA_WARP_CL_SIZE       64U

/** @brief Maximum concurrent DMA operations in the ring */
#define VOS3_DMA_WARP_MAX_PENDING   8U

/* ============================================================================
 * DMA TRANSFER STATUS
 * ============================================================================ */

/**
 * @brief DMA transfer lifecycle states
 */
typedef enum vos3_dma_status {
    VOS3_DMA_IDLE      = 0,     /**< Descriptor slot is free          */
    VOS3_DMA_ACTIVE    = 1,     /**< Transfer in progress             */
    VOS3_DMA_COMPLETE  = 2,     /**< Transfer finished successfully   */
    VOS3_DMA_ERROR     = 3      /**< Transfer failed                  */
} vos3_dma_status_t;

/* ============================================================================
 * DMA TRANSFER DESCRIPTOR
 * ============================================================================ */

/**
 * @brief DMA transfer descriptor
 *
 * @details Describes a single DMA transfer operation. The ring buffer
 *          holds up to VOS3_DMA_WARP_MAX_PENDING concurrent descriptors.
 *          Completed descriptors are recycled for new transfers.
 */
typedef struct vos3_dma_xfer {
    uintptr_t           src_phys;       /**< Source physical address          */
    uintptr_t           dst_phys;       /**< Destination physical address     */
    size_t              length;         /**< Transfer size in bytes           */
    vos3_dma_status_t   status;         /**< Current lifecycle status         */
    uint32_t            slot_id;        /**< Target AI model slot             */
    uint32_t            hp_index;       /**< Target HugePage index in slot    */
    uint64_t            start_tick;     /**< TSC at transfer start            */
    uint64_t            end_tick;       /**< TSC at transfer completion       */
    uint32_t            crc32c;         /**< CRC32C of transferred data       */
} vos3_dma_xfer_t;

/* ============================================================================
 * DMA ENGINE STATISTICS
 * ============================================================================ */

/**
 * @brief DMA engine cumulative statistics
 */
typedef struct vos3_dma_stats {
    uint64_t            total_xfers;        /**< Lifetime transfer count      */
    uint64_t            total_bytes;        /**< Lifetime bytes transferred   */
    uint64_t            total_ticks;        /**< Cumulative transfer ticks    */
    uint64_t            peak_throughput;    /**< Peak bytes/tick ratio        */
    uint32_t            active_xfers;       /**< Currently in-flight xfers    */
    uint32_t            errors;             /**< Lifetime error count         */
} vos3_dma_stats_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize the DMA warp engine
 *
 * @details Clears the transfer ring and statistics. Idempotent --
 *          subsequent calls return 0 without re-initialization.
 *          Must be called after PMM, VMM, and ivshmem init.
 *
 * @return 0 on success
 */
int vos3_dma_warp_init(void);

/**
 * @brief Transfer data from ivshmem zone to AI slot HugePage(s)
 *
 * @details Reads from the ivshmem warp zone for the given slot_id,
 *          starting at @p offset bytes into the zone, and copies
 *          @p length bytes using non-temporal stores into the
 *          HugePage range [hp_start .. hp_start+hp_count-1].
 *
 *          The destination PTEs are temporarily made writable for
 *          the copy and restored to read-only + AI_PROTECTED + NX
 *          after completion. A CRC32C of the first 4KB is computed
 *          for integrity verification.
 *
 * @param[in] slot_id    Target AI model slot (1..VOS3_MODEL_SLOT_MAX-1)
 * @param[in] hp_start   First HugePage index in the slot
 * @param[in] hp_count   Number of HugePages to fill
 * @param[in] offset     Byte offset into the ivshmem zone
 * @param[in] length     Total bytes to transfer
 * @return 0 on success, negative errno on failure
 */
int vos3_dma_warp_ivshmem_to_slot(uint32_t slot_id, uint32_t hp_start,
                                   uint32_t hp_count, size_t offset,
                                   size_t length);

/**
 * @brief Transfer data from physical address to AI slot HugePage
 *
 * @details Copies up to 2 MB (one HugePage) from @p src_phys into
 *          the slot's HugePage at index @p hp_index. Source physical
 *          address is mapped via the kernel direct map.
 *
 * @param[in] slot_id    Target AI model slot (1..VOS3_MODEL_SLOT_MAX-1)
 * @param[in] hp_index   Destination HugePage index in the slot
 * @param[in] src_phys   Source physical address (kernel-accessible)
 * @param[in] length     Bytes to transfer (max 2 MB)
 * @return 0 on success, negative errno on failure
 */
int vos3_dma_warp_phys_to_slot(uint32_t slot_id, uint32_t hp_index,
                                uintptr_t src_phys, size_t length);

/**
 * @brief Non-temporal bulk memory copy (cache-bypass)
 *
 * @details Uses movnti (64-bit non-temporal store) for the bulk of
 *          the transfer, with byte-level head/tail alignment handling.
 *          Issues sfence after completion to ensure global visibility.
 *
 * @param[out] dst  Destination buffer (should be cache-line aligned for best perf)
 * @param[in]  src  Source buffer
 * @param[in]  len  Bytes to copy
 */
void vos3_dma_nt_copy(void *dst, const void *src, size_t len);

/**
 * @brief Get DMA engine statistics snapshot
 *
 * @param[out] out  Statistics structure to populate
 * @return 0 on success, negative errno on failure
 */
int vos3_dma_warp_stats(vos3_dma_stats_t *out);

/**
 * @brief Fence: ensure all pending DMA stores are globally visible
 *
 * @details Issues sfence + mfence to guarantee all non-temporal stores
 *          have drained to memory before returning.
 */
void vos3_dma_warp_fence(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_DMA_WARP_H */
