/**
 * @file kvcache.h
 * @brief vOS KV-cache OS-primitive API (Sprint 16 / Item A2)
 *
 * What this header adds on top of the existing infrastructure
 * -----------------------------------------------------------
 *
 * Phase 6 (vos3_ai_kv_cache_alloc/free in ai_guard.h) ships HugePage-pinned
 * KV-cache regions; Phase 4.2.7+ (g_model_slots[].context_*) ships per-slot
 * context page tracking with shadow-PTE checkpoint support.
 *
 * What was MISSING for the "KV-cache as OS primitive" claim (Sprint 16 A2):
 * a callable, process-state-shaped API that treats a KV-cache region the way
 * the kernel treats a process address space — checkpoint to opaque blob,
 * restore from blob, fork (copy-on-write between slots).
 *
 * This header is the OS-primitive surface. Callers (backend services,
 * MAGI sandboxes, the future SCHED_INFERENCE class in J1) speak only to
 * the three functions here; the underlying physical-page lifecycle stays
 * inside ai_slots.c.
 *
 * References
 * ----------
 *   - ProbeLogits / Anima OS — Kernel-Level LLM Inference Primitives
 *     (arxiv.org/abs/2604.11943, April 2026). The paper treats KV-cache
 *     as first-class process state with checkpoint/restore/fork; vOS
 *     ships the same concept on top of its existing slot infrastructure.
 *   - SLA-Constrained Dynamic Batching for LLM Inference
 *     (arxiv.org/abs/2503.05248) — argues for OS-level KV-cache tiering.
 *
 * Honest scope ceiling
 * --------------------
 * The checkpoint blob is opaque to userspace: callers MUST NOT introspect
 * or modify it. Format is `vos3_kvcache_blob_v1_t` (defined below) but
 * field semantics are implementation-defined; only `vos3_kvcache_restore`
 * is sanctioned to read a blob. Across kernel version upgrades the blob
 * format may change; callers should re-checkpoint after any kernel rev.
 */

#ifndef VOS_KVCACHE_H
#define VOS_KVCACHE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Versioning + blob magic
 * ============================================================================ */

/** Wire-format version. Bumped on any layout-incompatible change. */
#define VOS3_KVCACHE_BLOB_VERSION    1U

/** ASCII "VKVC" — sanity-check the blob is ours before any deserialization. */
#define VOS3_KVCACHE_BLOB_MAGIC      0x564B5643U   /* 'V' 'K' 'V' 'C' */

/* ============================================================================
 * Return / error codes
 *
 * Sentinel-style negative errnos so callers can `if (rc < 0) ...`. We don't
 * reuse the existing VOS3_AI_GUARD_ERR_* set because those values describe
 * region-protection failures; KV-cache operations need their own taxonomy
 * so a logging operator can tell "checkpoint failed because slot was empty"
 * from "set_protection failed because permission denied".
 * ============================================================================ */

#define VOS3_KVCACHE_OK              0
#define VOS3_KVCACHE_ERR_INVAL      -1   /**< Bad arg (NULL, out-of-range slot) */
#define VOS3_KVCACHE_ERR_NOSLOT     -2   /**< slot_id outside [0, VOS3_MODEL_SLOT_MAX) */
#define VOS3_KVCACHE_ERR_EMPTY      -3   /**< Slot has no context_configured KV-cache */
#define VOS3_KVCACHE_ERR_BUFSMALL   -4   /**< User buffer smaller than required blob */
#define VOS3_KVCACHE_ERR_BUFLARGE   -5   /**< User buffer absurdly larger than any slot */
#define VOS3_KVCACHE_ERR_MAGIC      -6   /**< Blob magic mismatch — wrong format / kernel rev */
#define VOS3_KVCACHE_ERR_VERSION    -7   /**< Blob version mismatch — re-checkpoint required */
#define VOS3_KVCACHE_ERR_CSUM       -8   /**< Blob checksum failed — corrupted */
#define VOS3_KVCACHE_ERR_DSTBUSY    -9   /**< fork target slot already configured */
#define VOS3_KVCACHE_ERR_SAMESLOT   -10  /**< fork src == dst */
#define VOS3_KVCACHE_ERR_LOCKED     -11  /**< Slot lock contended (caller should retry) */
#define VOS3_KVCACHE_ERR_NOMEM      -12  /**< HugePage allocation failed during restore/fork */

/* ============================================================================
 * Blob layout (opaque to callers; documented for kernel + auditors)
 *
 * Layout is fixed-size header + variable-length payload (context pages).
 * Total size never exceeds VOS3_CONTEXT_PAGE_MAX * 4096 + header.
 * ============================================================================ */

/**
 * @brief Blob header that precedes every KV-cache checkpoint payload.
 *
 * Field order is wire-format stable for VOS3_KVCACHE_BLOB_VERSION=1.
 * Any field added/removed/reordered MUST bump the version constant.
 */
typedef struct vos3_kvcache_blob_v1_hdr {
    uint32_t magic;            /**< == VOS3_KVCACHE_BLOB_MAGIC */
    uint32_t version;          /**< == VOS3_KVCACHE_BLOB_VERSION */
    uint8_t  src_slot_id;      /**< slot that produced this blob (for audit) */
    uint8_t  reserved[3];      /**< padding — must be zero */
    uint32_t page_count;       /**< number of 4KB context pages following */
    uint32_t model_id;         /**< model_id at checkpoint time (compat check) */
    uint32_t model_epoch;      /**< model_epoch at checkpoint time */
    uint64_t checkpoint_tick;  /**< scheduler tick when blob was produced */
    uint64_t payload_crc64;    /**< XXH3-64 over the page payload only */
} vos3_kvcache_blob_v1_hdr_t;

/* Sanity: keep the header packed-friendly + word-aligned. */
#define VOS3_KVCACHE_BLOB_HDR_SIZE  40U

/* ============================================================================
 * Public API — the three OS primitives
 * ============================================================================ */

/**
 * @brief Snapshot a slot's KV-cache into an opaque blob.
 *
 * Treats the KV-cache the way the kernel treats a process address space
 * during ptrace/coredump: serialize the full state to a caller-supplied
 * buffer so the slot can be torn down and later restored bit-identically.
 *
 * @param  slot_id         Source slot (must be one of VOS3_SLOT_ACTIVE /
 *                         VOS3_SLOT_WARM / VOS3_SLOT_SUSPENDED, with
 *                         context_configured=1).
 * @param  out_buf         Caller-supplied buffer to receive the blob.
 * @param  buflen          Capacity of out_buf in bytes.
 * @param  out_blob_size   On success, written with the actual blob size.
 *
 * @return VOS3_KVCACHE_OK on success.
 *         VOS3_KVCACHE_ERR_NOSLOT if slot_id is out of range.
 *         VOS3_KVCACHE_ERR_EMPTY if slot has no configured context.
 *         VOS3_KVCACHE_ERR_BUFSMALL if buflen is too small (out_blob_size
 *           is still written with the required size so caller can retry).
 *         VOS3_KVCACHE_ERR_INVAL on NULL pointer arguments.
 *
 * Atomicity: acquires slot lock for the duration of the copy; cycle_count
 * is bumped by 1 on success (so this is observable in the slot's metrics
 * regression-tests can assert against).
 */
int vos3_kvcache_checkpoint(uint8_t  slot_id,
                            void    *out_buf,
                            size_t   buflen,
                            size_t  *out_blob_size);

/**
 * @brief Restore a slot's KV-cache from a blob previously produced by
 *        vos3_kvcache_checkpoint().
 *
 * Treats the input blob the way exec() treats an ELF image: it MUST have
 * been produced by this kernel version (matching magic + version), and the
 * target slot MUST have a matching model_id (we don't allow restoring a
 * llama-3 KV-cache into a phi-4 slot).
 *
 * @param  dst_slot_id   Target slot. Must be one of VOS3_SLOT_ACTIVE /
 *                       VOS3_SLOT_WARM with context_configured=1 and
 *                       matching model_id.
 * @param  blob          Pointer to the blob bytes.
 * @param  blob_size     Length of the blob in bytes.
 *
 * @return VOS3_KVCACHE_OK on success.
 *         VOS3_KVCACHE_ERR_MAGIC if blob is not a vOS KV-cache blob.
 *         VOS3_KVCACHE_ERR_VERSION if blob is from a wire-incompatible kernel.
 *         VOS3_KVCACHE_ERR_CSUM if the payload checksum doesn't match.
 *         VOS3_KVCACHE_ERR_NOSLOT / ERR_EMPTY / ERR_INVAL as for checkpoint.
 *
 * Atomicity: acquires slot lock; on failure the slot's prior context is
 * preserved (we copy into a staging buffer first, then memcpy + barrier).
 */
int vos3_kvcache_restore(uint8_t      dst_slot_id,
                         const void  *blob,
                         size_t       blob_size);

/**
 * @brief Copy-on-write fork the KV-cache from src into dst.
 *
 * Analogous to the kernel's COW fork() for address spaces: dst becomes
 * a logical copy of src's KV-cache without immediate physical copy. The
 * first write to any page in either slot triggers the page-fault-driven
 * physical split (handled inside the existing ai_slots.c VDEV_WRITE path
 * via the COW-bit on the per-slot shadow PTEs).
 *
 * @param  src_slot_id   Source slot (must be one of VOS3_SLOT_ACTIVE /
 *                       VOS3_SLOT_WARM with context_configured=1).
 * @param  dst_slot_id   Destination slot (must be VOS3_SLOT_FREE).
 *
 * @return VOS3_KVCACHE_OK on success.
 *         VOS3_KVCACHE_ERR_SAMESLOT if src == dst.
 *         VOS3_KVCACHE_ERR_DSTBUSY if dst is not VOS3_SLOT_FREE.
 *         VOS3_KVCACHE_ERR_EMPTY if src has no configured context.
 *         VOS3_KVCACHE_ERR_NOSLOT if either slot is out of range.
 *         VOS3_KVCACHE_ERR_NOMEM if shadow-PTE table cannot be allocated.
 *
 * Atomicity: acquires both slot locks in slot_id order (deadlock-free);
 * COW bits are set under those locks before either lock is released.
 *
 * Honest scope ceiling: the COW page-fault handler that splits physical
 * pages on first write is the existing PHASE 4.2.7+ context-persistence
 * path; if that path's COW bit handling regresses, fork() observably
 * "succeeds" but writes to dst overwrite src. The included test
 * test_kvcache_fork_cow_isolation verifies isolation actually holds.
 */
int vos3_kvcache_fork(uint8_t src_slot_id,
                      uint8_t dst_slot_id);

/* ============================================================================
 * Convenience: compute the blob size for a slot without actually
 * checkpointing. Lets callers size their buffer correctly on first call.
 * ============================================================================ */

/**
 * @brief Return the blob size that vos3_kvcache_checkpoint() would write.
 *
 * @param  slot_id          Slot to query.
 * @param  out_blob_size    On success, written with required blob size.
 *
 * @return VOS3_KVCACHE_OK / ERR_NOSLOT / ERR_EMPTY / ERR_INVAL.
 */
int vos3_kvcache_blob_size(uint8_t   slot_id,
                           size_t   *out_blob_size);

#ifdef __cplusplus
}
#endif

#endif /* VOS_KVCACHE_H */
