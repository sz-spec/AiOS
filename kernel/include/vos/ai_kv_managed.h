/**
 * @file ai_kv_managed.h
 * @brief VOS3 Managed KV Cache — 3-Tier Hierarchical Cache with TurboQuant
 *
 * @details Extends the existing KV-cache HugePage pinning (Phase 6) into a
 *          3-tier hierarchical cache:
 *            Tier 0 (Hot):  HugePages — uncompressed, pinned, <100ns access
 *            Tier 1 (Warm): PMM pages — TQ4 compressed (3.8x), evicted from T0
 *            Tier 2 (Cold): vVFS blocks — TQ3 compressed (4.9x), demand-paged
 *          Eviction policy: LRU by sequence number (oldest pairs evicted first).
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.1: Managed KV Cache
 */

#ifndef VOS3_AI_KV_MANAGED_H
#define VOS3_AI_KV_MANAGED_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief KV cache tiers */
typedef enum vos3_kv_tier {
    VOS3_KV_TIER_HOT  = 0,  /**< Tier 0: HugePages — uncompressed, pinned */
    VOS3_KV_TIER_WARM = 1,  /**< Tier 1: PMM pages — TQ4 compressed */
    VOS3_KV_TIER_COLD = 2,  /**< Tier 2: vVFS blocks — TQ3 compressed */
} vos3_kv_tier_t;

/** @brief Maximum Tier-1 (warm) pages per slot: 32 x 4KB = 128KB compressed */
#define VOS3_KV_WARM_MAX_PAGES      32U

/** @brief Maximum Tier-2 (cold) blocks per slot: 4 x 2MB vVFS blocks */
#define VOS3_KV_COLD_MAX_BLOCKS     4U

/** @brief TQ4 compression ratio (approximate — 32-bit to 4-bit = ~3.8x effective) */
#define VOS3_KV_TQ4_RATIO_NUM      38U
#define VOS3_KV_TQ4_RATIO_DEN      10U

/** @brief TQ3 compression ratio (approximate — 32-bit to 3-bit = ~4.9x effective) */
#define VOS3_KV_TQ3_RATIO_NUM      49U
#define VOS3_KV_TQ3_RATIO_DEN      10U

/** @brief Per-channel scale header size for TQ4 */
#define VOS3_KV_TQ4_HEADER_SIZE    16U

/** @brief Per-channel scale header size for TQ3 */
#define VOS3_KV_TQ3_HEADER_SIZE    16U

/** @brief PMM page size for Tier-1 entries */
#define VOS3_KV_PAGE_SIZE           4096U

/* ============================================================================
 * TIER ENTRY TYPES
 * ============================================================================ */

/**
 * @brief Tier-1 (warm) entry — TQ4-compressed KV data in PMM pages
 */
typedef struct vos3_kv_tier1_entry {
    uint64_t    phys_page;      /**< Physical address of the PMM page */
    uint32_t    seq_start;      /**< Start sequence number of KV pairs in this page */
    uint32_t    seq_end;        /**< End sequence number (exclusive) */
    uint32_t    compressed_len; /**< Compressed data length in bytes */
    uint32_t    original_len;   /**< Original uncompressed length */
    uint8_t     quantized;      /**< 1 = TQ4 quantized */
    uint8_t     valid;          /**< 1 = entry in use */
} vos3_kv_tier1_entry_t;

/**
 * @brief Tier-2 (cold) entry — TQ3-compressed KV data in vVFS blocks
 */
typedef struct vos3_kv_tier2_entry {
    uint32_t    block_idx;      /**< vVFS block index */
    uint32_t    seq_start;      /**< Start sequence number */
    uint32_t    seq_end;        /**< End sequence number (exclusive) */
    uint32_t    compressed_len; /**< Compressed data length in bytes */
    uint32_t    original_len;   /**< Original uncompressed length */
    uint8_t     valid;          /**< 1 = entry in use */
} vos3_kv_tier2_entry_t;

/* ============================================================================
 * PER-SLOT MANAGED STATE
 * ============================================================================ */

/**
 * @brief Per-slot managed KV cache state — embedded in vos3_ai_model_slot_t
 */
typedef struct vos3_kv_managed {
    /* Tier-1 (warm) entries */
    vos3_kv_tier1_entry_t   tier1[VOS3_KV_WARM_MAX_PAGES];
    uint32_t                tier1_count;        /**< Active tier-1 entries */

    /* Tier-2 (cold) entries */
    vos3_kv_tier2_entry_t   tier2[VOS3_KV_COLD_MAX_BLOCKS];
    uint32_t                tier2_count;        /**< Active tier-2 entries */

    /* Eviction watermarks */
    uint32_t                evict_seq_warm;     /**< Lowest seq in tier-1 (for LRU) */
    uint32_t                evict_seq_cold;     /**< Lowest seq in tier-2 (for LRU) */

    /* Statistics */
    uint64_t                evictions_to_warm;  /**< Total T0->T1 evictions */
    uint64_t                evictions_to_cold;  /**< Total T1->T2 evictions */
    uint64_t                promotions;         /**< Total promotions (T2->T1 or T1->T0) */
    uint64_t                total_compressed;   /**< Total bytes compressed */
    uint64_t                total_decompressed; /**< Total bytes decompressed */

    /* Initialization flag */
    uint8_t                 initialized;        /**< 1 = managed state active */
} vos3_kv_managed_t;

/* ============================================================================
 * MANAGED KV API
 * ============================================================================ */

/**
 * @brief Initialize managed KV state for a slot
 * @param[in] slot_id Model slot (0..3)
 * @return 0 on success
 */
int vos3_kv_managed_init(uint8_t slot_id);

/**
 * @brief Evict a HugePage from Tier-0 (hot) to Tier-1 (warm)
 *
 * Compresses via TQ4, stores in PMM pages, frees the HugePage.
 *
 * @param[in] slot_id   Model slot
 * @param[in] hp_index  HugePage index within slot's kv_hp_phys[] (0..3)
 * @return 0 on success, negative on error
 */
int vos3_kv_evict_to_warm(uint8_t slot_id, uint32_t hp_index);

/**
 * @brief Evict a Tier-1 entry to Tier-2 (cold)
 *
 * Re-compresses with TQ3 for deeper compression, writes to vVFS block.
 *
 * @param[in] slot_id    Model slot
 * @param[in] tier1_idx  Index into tier1[] array
 * @return 0 on success, negative on error
 */
int vos3_kv_evict_to_cold(uint8_t slot_id, uint32_t tier1_idx);

/**
 * @brief Promote KV data toward hotter tier for the given sequence position
 *
 * If in Tier-2: decompress TQ3 -> recompress TQ4 -> Tier-1
 * If in Tier-1: decompress TQ4 -> uncompressed -> Tier-0 HugePage
 * Triggers LRU eviction if target tier is full.
 *
 * @param[in] slot_id Model slot
 * @param[in] seq_pos Sequence position to promote
 * @return 0 on success, -ENOENT if seq_pos not found
 */
int vos3_kv_promote(uint8_t slot_id, uint32_t seq_pos);

/**
 * @brief Lookup which tier contains a given sequence position
 *
 * @param[in]  slot_id  Model slot
 * @param[in]  seq_pos  Sequence position to find
 * @param[out] tier     Tier containing the seq_pos
 * @param[out] index    Index within the tier's entry array
 * @return 0 if found, -ENOENT if not found
 */
int vos3_kv_lookup_seq(uint8_t slot_id, uint32_t seq_pos,
                       vos3_kv_tier_t *tier, uint32_t *index);

/* ============================================================================
 * TQ4/TQ3 COMPRESSION API (constant-time)
 * ============================================================================ */

/**
 * @brief TQ4 compress (4-bit quantization, ~3.8x compression)
 *
 * Per-channel min/max scan, 4-bit uniform quantization, nibble packing.
 * Constant-time: no data-dependent branches.
 *
 * @param[in]  src      Source data
 * @param[in]  src_len  Source length (must be > 0)
 * @param[out] dst      Destination buffer
 * @param[in]  dst_cap  Destination capacity
 * @param[out] out_len  Compressed output length
 * @return 0 on success, negative on error
 */
int vos3_kv_tq4_compress(const void *src, uint32_t src_len,
                         void *dst, uint32_t dst_cap, uint32_t *out_len);

/**
 * @brief TQ4 decompress
 *
 * @param[in]  src      Compressed data
 * @param[in]  src_len  Compressed length
 * @param[out] dst      Destination buffer
 * @param[in]  dst_cap  Destination capacity
 * @param[out] out_len  Decompressed output length
 * @return 0 on success, negative on error
 */
int vos3_kv_tq4_decompress(const void *src, uint32_t src_len,
                           void *dst, uint32_t dst_cap, uint32_t *out_len);

/**
 * @brief TQ3 compress (3-bit quantization, ~4.9x compression)
 *
 * Packs 8 values into 3 bytes (24 bits). Higher compression for cold tier.
 * Constant-time: no data-dependent branches.
 *
 * @param[in]  src      Source data
 * @param[in]  src_len  Source length
 * @param[out] dst      Destination buffer
 * @param[in]  dst_cap  Destination capacity
 * @param[out] out_len  Compressed output length
 * @return 0 on success, negative on error
 */
int vos3_kv_tq3_compress(const void *src, uint32_t src_len,
                         void *dst, uint32_t dst_cap, uint32_t *out_len);

/**
 * @brief TQ3 decompress
 *
 * @param[in]  src      Compressed data
 * @param[in]  src_len  Compressed length
 * @param[out] dst      Destination buffer
 * @param[in]  dst_cap  Destination capacity
 * @param[out] out_len  Decompressed output length
 * @return 0 on success, negative on error
 */
int vos3_kv_tq3_decompress(const void *src, uint32_t src_len,
                           void *dst, uint32_t dst_cap, uint32_t *out_len);

/* ============================================================================
 * PREFIX SHARING API (COW HugePages)
 * ============================================================================ */

/**
 * @brief Share prefix HugePages from src_slot to dst_slot via COW mapping
 *
 * Maps src's KV HugePage physical addresses into dst's page table as read-only.
 * Requires src_slot to have prefix_immutable == 1.
 *
 * @param[in] src_slot Source slot (must have locked prefix)
 * @param[in] dst_slot Destination slot
 * @param[in] count    Number of HugePages to share (1..prefix_locked_count)
 * @return 0 on success, negative on error
 */
int vos3_kv_prefix_share(uint8_t src_slot, uint8_t dst_slot, uint32_t count);

/**
 * @brief Unshare prefix pages from a slot
 *
 * Decrements refcount on shared pages. If refcount reaches 0, returns
 * HugePages to PMM. Clears slot's prefix mapping.
 *
 * @param[in] slot_id Slot to unshare prefix from
 * @return 0 on success
 */
int vos3_kv_prefix_unshare(uint8_t slot_id);

/* ============================================================================
 * Phase 10: COW PAGE FAULT HANDLER
 * ============================================================================ */

/**
 * @brief Handle a write-fault on a COW-shared KV prefix page.
 *
 * Called from the page fault handler when a slot writes to a read-only
 * page marked with VOS3_PTE_COW. Allocates a new physical page, copies
 * the shared page contents, and remaps the faulting slot's PTE to the
 * new page with write permission restored.
 *
 * @param[in] slot_id    Slot that triggered the write fault
 * @param[in] fault_addr Virtual address of the faulting access
 * @return 0 on success (page copied and remapped), -1 on error
 */
int vos3_kv_cow_page_fault(uint8_t slot_id, uintptr_t fault_addr);

#endif /* VOS3_AI_KV_MANAGED_H */
