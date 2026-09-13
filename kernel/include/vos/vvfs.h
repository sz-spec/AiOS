/**
 * @file vvfs.h
 * @brief VOS3 Virtual VFS (vVFS) — Privacy Moat for AI Agent Isolation
 *
 * @details Provides a privacy-preserving virtual filesystem layer between
 *          AI agents and the real VFS. Ensures semantic isolation via:
 *          - Per-slot ACL enforcement (owner_tid gating)
 *          - Constant-time 2MB block codec with noise padding
 *          - CRC32C integrity on every block
 *          Mount points: /ai/<slot_id>/ (e.g., /ai/0/, /ai/1/)
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.1: Privacy Moat
 */

#ifndef VOS3_VVFS_H
#define VOS3_VVFS_H

#include <stdint.h>
#include <stddef.h>
#include "vfs.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief vVFS block size — matches HugePage (2MB) for zero-copy DMA semantics */
#define VVFS_BLOCK_SIZE         0x200000U   /* 2 MiB */

/** @brief Noise padding percentage — hides true payload size at block level */
#define VVFS_NOISE_PERCENT      15U

/** @brief vVFS magic number ("VVFS" in ASCII) */
#define VVFS_MAGIC              0x56564653U

/** @brief Maximum blocks per slot (4 x 2MB = 8MB raw capacity) */
#define VVFS_MAX_BLOCKS         4U

/** @brief Maximum vVFS mount points (one per model slot) */
#define VVFS_MAX_MOUNTS         4U

/** @brief Maximum inodes per vVFS instance */
#define VVFS_MAX_INODES         64U

/* ============================================================================
 * BLOCK CODEC TYPES
 * ============================================================================ */

/**
 * @brief vVFS block — constant-size 2MB container with integrity footer
 *
 * Every I/O operation reads/writes exactly one full block.
 * Noise padding beyond payload_len prevents payload size inference.
 */
typedef struct vvfs_block {
    uint8_t     data[VVFS_BLOCK_SIZE];  /**< Payload + noise fill (always 2MB) */
    uint32_t    payload_len;            /**< Real payload length within data[] */
    uint32_t    crc32c;                 /**< CRC32C of data[0..payload_len-1] */
    uint8_t     noise_seed[16];         /**< PRNG seed used for noise generation */
} vvfs_block_t;

/**
 * @brief Per-inode extension for vVFS — tracks ownership and access telemetry
 */
typedef struct vvfs_inode_ext {
    uint8_t     owner_slot;             /**< Model slot that owns this inode */
    uint32_t    access_count;           /**< Total accesses (reads + writes) */
    uint64_t    last_access_tick;       /**< System tick of last access */
    uint32_t    block_count;            /**< Number of blocks backing this inode */
} vvfs_inode_ext_t;

/**
 * @brief vVFS mount data — binds a vVFS instance to a backing path and slot
 */
typedef struct vvfs_mount_data {
    uint8_t     slot_id;                /**< Bound model slot (0..3) */
    uint32_t    owner_tid;              /**< Task ID that owns the slot at mount time */
    char        backing_path[VOS3_PATH_MAX]; /**< Backing VFS path (ramfs/vos3fs) */
    uint32_t    block_count;            /**< Available blocks for this slot */
    uint8_t     mounted;                /**< 1 = actively mounted */
} vvfs_mount_data_t;

/**
 * @brief Per-slot vVFS backing store — in-memory block buffers
 *
 * Each slot gets VVFS_MAX_BLOCKS x VVFS_BLOCK_SIZE bytes of backing storage.
 * Blocks are always read/written in full — no partial block I/O.
 */
typedef struct vvfs_slot_store {
    uint8_t     blocks[VVFS_MAX_BLOCKS][VVFS_BLOCK_SIZE]; /**< Raw block storage */
    uint32_t    block_used[VVFS_MAX_BLOCKS];  /**< Payload len per block (0=free) */
    uint32_t    block_crc[VVFS_MAX_BLOCKS];   /**< CRC32C per block */
    uint8_t     allocated;                     /**< 1 = store initialized */
} vvfs_slot_store_t;

/* ============================================================================
 * vVFS CORE API
 * ============================================================================ */

/**
 * @brief Initialize vVFS subsystem
 * @return 0 on success, negative on error
 */
int vvfs_init(void);

/**
 * @brief Mount vVFS for a specific model slot
 *
 * Creates mount at /ai/<slot_id>/ with ACL bound to the slot's owner_tid.
 * Sets VOS3_FS_NO_DCACHE to prevent stale dentry caching across slot resets.
 *
 * @param[in] slot_id Model slot (0..VOS3_MODEL_SLOT_MAX-1)
 * @return 0 on success, negative on error
 */
int vvfs_mount_for_slot(uint8_t slot_id);

/**
 * @brief Unmount vVFS for a specific model slot
 *
 * Unmounts /ai/<slot_id>/ and scrubs all backing blocks (zero-fill).
 *
 * @param[in] slot_id Model slot (0..VOS3_MODEL_SLOT_MAX-1)
 * @return 0 on success, negative on error
 */
int vvfs_unmount_slot(uint8_t slot_id);

/* ============================================================================
 * BLOCK CODEC API (constant-time)
 * ============================================================================ */

/**
 * @brief Encode payload into a constant-size 2MB block with noise padding
 *
 * Always touches the full 2MB block — no early returns, no data-dependent branches.
 * Noise is seeded from kernel entropy (vos3_entropy_extract).
 *
 * @param[in]  src      Source payload
 * @param[in]  src_len  Payload length (must be <= VVFS_BLOCK_SIZE)
 * @param[out] out      Output block (must point to VVFS_BLOCK_SIZE bytes)
 * @param[out] out_crc  CRC32C of the real payload
 * @param[out] out_seed Noise seed used (16 bytes)
 * @return 0 on success, -EINVAL if src_len > VVFS_BLOCK_SIZE
 */
int vvfs_encode_block(const void *src, uint32_t src_len,
                      void *out, uint32_t *out_crc, uint8_t out_seed[16]);

/**
 * @brief Decode a 2MB block back to payload
 *
 * Verifies CRC32C integrity. Verification loop always runs over full block
 * for constant-time behavior.
 *
 * @param[in]  block        Encoded block data (VVFS_BLOCK_SIZE bytes)
 * @param[in]  payload_len  Expected payload length
 * @param[in]  expected_crc Expected CRC32C
 * @param[out] out_buf      Output buffer (must be >= payload_len)
 * @param[out] out_len      Actual bytes decoded
 * @return 0 on success, -EIO on CRC mismatch, -EINVAL on bad params
 */
int vvfs_decode_block(const void *block, uint32_t payload_len,
                      uint32_t expected_crc, void *out_buf, uint32_t *out_len);

/* ============================================================================
 * BLOCK TRANSPORT API (ACL-gated)
 * ============================================================================ */

/**
 * @brief Read a block from vVFS backing store
 *
 * ACL check: caller's task tid must match g_model_slots[slot_id].owner_tid.
 * Always reads exactly 2MB — timing-invariant at block granularity.
 *
 * @param[in]  slot_id   Model slot (0..3)
 * @param[in]  block_idx Block index within slot (0..VVFS_MAX_BLOCKS-1)
 * @param[out] out_buf   Output buffer (must be >= payload length)
 * @param[in]  buf_cap   Buffer capacity
 * @param[out] out_len   Actual payload bytes read
 * @return 0 on success, -EACCES on ACL failure, -EINVAL on bad params
 */
int vvfs_read_block(uint8_t slot_id, uint32_t block_idx,
                    void *out_buf, uint32_t buf_cap, uint32_t *out_len);

/* ---------------------------------------------------------------------------
 * M3 — Model-file SecureBoot (roadmap row M3). STILL OPEN / moat unchanged
 * (49/80): Phase 1+2 shipped the verify-only Ed25519 + SHA-512 primitives
 * (KAT + malleability-hardened); Phase 4 (below) wires them into the slot
 * ingestion path. Enforcement is gated OFF by default via
 * VOS3_VVFS_REQUIRE_MODEL_SIG so existing model loading is unchanged. The
 * trusted-key + OMS-bundle TRANSPORT (Phase 3) and the QEMU accept/forged/
 * missing matrix + external audit (Phase 5/6) are still pending, so M3 is NOT
 * counted yet.
 * ------------------------------------------------------------------------- */

/** @brief Enforcement flag. 0 = OFF (default, unchanged loading). 1 = ON
 *  (fail-closed: only signature-verified slots may be read / activated). */
#ifndef VOS3_VVFS_REQUIRE_MODEL_SIG
#define VOS3_VVFS_REQUIRE_MODEL_SIG 0
#endif

/* Registry + verify decision (host-testable pure core). VVFS_MAX_MOUNTS +
 * VOS3_FS_ERR_* are already defined above (vvfs.h / vfs.h), so this include's
 * #ifndef fallbacks are skipped and the real kernel values are used. */
#include "vvfs_model_verify.h"

/**
 * @brief Read-path SecureBoot gate (M3). When enforcement is enabled, returns
 *        success only for slots whose model signature has been verified
 *        (vvfs_verify_model_slot); otherwise fail-closed. A no-op (0) when
 *        enforcement is disabled.
 */
int vvfs_model_sig_verify(uint8_t slot_id, const void *buf, uint32_t len);

/**
 * @brief Write a block to vVFS backing store
 *
 * Encodes through codec (pad + noise + CRC) then writes full 2MB.
 * ACL check enforced.
 *
 * @param[in] slot_id   Model slot (0..3)
 * @param[in] block_idx Block index within slot (0..VVFS_MAX_BLOCKS-1)
 * @param[in] data      Payload to write
 * @param[in] len       Payload length
 * @return 0 on success, -EACCES on ACL failure, -EINVAL on bad params
 */
int vvfs_write_block(uint8_t slot_id, uint32_t block_idx,
                     const void *data, uint32_t len);

/**
 * @brief Get block count for a slot's vVFS instance
 *
 * @param[in] slot_id Model slot (0..3)
 * @return Block count, or 0 if slot not mounted
 */
uint32_t vvfs_block_count(uint8_t slot_id);

/**
 * @brief Query vVFS mount status for a slot
 *
 * @param[in]  slot_id  Model slot (0..3)
 * @param[out] mounted  1 if mounted, 0 otherwise
 * @param[out] blocks_used Number of blocks with data
 * @param[out] blocks_total Total blocks available
 * @return 0 on success, -EINVAL on bad slot_id
 */
int vvfs_stat(uint8_t slot_id, uint8_t *mounted,
              uint32_t *blocks_used, uint32_t *blocks_total);

/* ============================================================================
 * FILESYSTEM TYPE (for VFS registration)
 * ============================================================================ */

/** @brief Global vVFS filesystem type descriptor */
extern vos3_fs_type_t g_vvfs_type;

#endif /* VOS3_VVFS_H */
