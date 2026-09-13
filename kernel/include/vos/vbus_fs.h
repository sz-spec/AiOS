/**
 * @file vbus_fs.h
 * @brief vBus FS provenance — cryptographic metadata attached to inodes.
 *
 * Sprint 15 / Item K4
 * ====================
 *
 * vOS agents produce a lot of small artifacts: chat transcripts, model
 * outputs, code patches, audit-trail records. The May-2026 industry
 * survey of agent-era OS problems (docs/AGENT_ERA_OS_PROBLEMS.md item K4)
 * notes that filesystem inodes today carry no native provenance — xattr
 * support is per-FS and not portable.
 *
 * This module is the kernel-side counterpart to backend's MAIF format
 * (https://arxiv.org/pdf/2511.15097): every artifact that traverses a
 * vOS filesystem boundary can be tagged with:
 *
 *   - sha256_digest      Content hash (32 B)
 *   - signer_fingerprint First 8 B of SHA-256 of the signing public key
 *   - signed_at_tick     Kernel-tick at attach time
 *   - producer_slot      AI-slot id of the producer agent (0xFFFF if N/A)
 *   - flags              VBUS_FS_PROVENANCE_{SIGNED, VERIFIED, ...}
 *
 * The provenance metadata is held in a kernel-side side table keyed by
 * inode pointer, so adding K4 doesn't require modifying vos3_inode_t
 * (which is on hot paths). Lookups are O(1) average via a small open-
 * addressing hash table.
 *
 * Cross-references:
 *   - backend/services/maif_envelope.py  — userspace MAIF wire format
 *   - infra/security/model_signer.py     — OMS (Sprint 15 / I3) for the
 *                                          signing-key model
 *   - kernel/src/mm/audit_ring.c         — emits a provenance-attached
 *                                          audit event whenever the
 *                                          signer fingerprint changes
 *
 * Honest-scope ceiling
 * --------------------
 *
 *   - The provenance table is per-mount-instance, not durable across
 *     reboots. Persistence is the Stage 14.B.5 deliverable that ports
 *     the table to the SQLCipher compliance store. The in-memory table
 *     is sufficient for the EU AI Act Article 73 incident-reporting
 *     pathway (which queries within a single boot session).
 *
 *   - We do NOT verify signatures inside the kernel — that's userspace's
 *     job via verify_oms_bundle(). The kernel just records the claim
 *     (signer fingerprint + digest) so an auditor can correlate.
 *
 *   - The hash table size is fixed at 1024 slots. Above that fill rate,
 *     attach calls return -ENOSPC and the caller is expected to GC older
 *     entries. The compliance store drains a snapshot on every audit
 *     ring drain, so the table is bounded by GC cadence.
 *
 * @date 2026-05-23
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#ifndef VOS3_VBUS_FS_H
#define VOS3_VBUS_FS_H

#include <stdint.h>
#include <stddef.h>

/* Forward decl to avoid pulling vfs.h into every consumer. */
struct vos3_inode;

/* ============================================================================
 * Constants
 * ============================================================================ */

#define VBUS_FS_PROVENANCE_TABLE_SLOTS  1024U
#define VBUS_FS_PROVENANCE_DIGEST_BYTES   32U
#define VBUS_FS_PROVENANCE_FP_BYTES        8U

#define VBUS_FS_PROVENANCE_SIGNED         (1U << 0)  /**< Has a real signature attached */
#define VBUS_FS_PROVENANCE_VERIFIED       (1U << 1)  /**< Userspace OMS verify succeeded */
#define VBUS_FS_PROVENANCE_AGENT_PRODUCED (1U << 2)  /**< Producer was an AI slot, not a human */
#define VBUS_FS_PROVENANCE_GUARDED        (1U << 3)  /**< Read-only after attach */

/** "No slot" sentinel — used when the producer is not an AI slot. */
#define VBUS_FS_PRODUCER_SLOT_NONE        0xFFFFU

/* Error codes (negative for failure, 0 for success — matches the rest of
 * the kernel's convention). */
#define VBUS_FS_OK                         0
#define VBUS_FS_ERR_INVAL                 -1
#define VBUS_FS_ERR_NOSPC                 -2
#define VBUS_FS_ERR_NOTFOUND              -3
#define VBUS_FS_ERR_GUARDED               -4

/* ============================================================================
 * Provenance record
 * ============================================================================ */

/**
 * @brief Cryptographic provenance metadata attached to an inode.
 *
 * Stored in the side table keyed by inode pointer. Caller-supplied
 * (kernel does NOT compute the digest — userspace owns that since it
 * already does so in maif_envelope.py and model_signer.py).
 */
typedef struct vos3_vbus_fs_provenance {
    uint8_t  digest[VBUS_FS_PROVENANCE_DIGEST_BYTES];     /**< SHA-256 of artifact bytes */
    uint8_t  signer_fingerprint[VBUS_FS_PROVENANCE_FP_BYTES]; /**< First 8 B of SHA-256(signer pubkey) */
    uint64_t signed_at_tick;                              /**< Kernel-tick when attached */
    uint16_t producer_slot;                               /**< AI slot id, or VBUS_FS_PRODUCER_SLOT_NONE */
    uint16_t flags;                                       /**< VBUS_FS_PROVENANCE_{SIGNED, ...} */
    uint32_t _reserved;                                   /**< Padding to keep struct 8-byte aligned */
} vos3_vbus_fs_provenance_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Initialize the provenance side table. Idempotent.
 *
 * Called once during vOS boot from vos3_fs_init().
 * @return 0 on success, negative on error.
 */
int vos3_vbus_fs_init(void);

/**
 * @brief Attach provenance metadata to an inode.
 *
 * @param[in] inode  Inode the provenance applies to. MUST be non-NULL.
 * @param[in] prov   Provenance record to attach. The struct is copied
 *                   into the table; the caller's storage is free to be
 *                   reused after return.
 * @return 0 on success, VBUS_FS_ERR_INVAL on NULL inode, VBUS_FS_ERR_NOSPC
 *         on table full, VBUS_FS_ERR_GUARDED if the existing entry has
 *         VBUS_FS_PROVENANCE_GUARDED set.
 *
 * If the inode already has provenance attached, this REPLACES the entry
 * (unless GUARDED is set). Use vos3_vbus_fs_lookup() first if you need
 * the prior value.
 */
int vos3_vbus_fs_attach(struct vos3_inode* inode,
                        const vos3_vbus_fs_provenance_t* prov);

/**
 * @brief Look up provenance attached to an inode.
 *
 * @param[in]  inode    Inode to query.
 * @param[out] out_prov Caller-supplied struct to receive the metadata.
 * @return 0 on success (out_prov populated), VBUS_FS_ERR_NOTFOUND if no
 *         provenance is attached, VBUS_FS_ERR_INVAL on NULL args.
 */
int vos3_vbus_fs_lookup(struct vos3_inode* inode,
                        vos3_vbus_fs_provenance_t* out_prov);

/**
 * @brief Detach provenance from an inode (called from inode->free path).
 *
 * @param[in] inode  Inode whose provenance to drop.
 * @return 0 on success or if no entry existed, VBUS_FS_ERR_GUARDED if
 *         the entry is locked.
 */
int vos3_vbus_fs_detach(struct vos3_inode* inode);

/**
 * @brief Total number of attached provenance entries (diagnostic).
 *
 * Used by the audit-ring drain logic to estimate side-table pressure.
 * @return 0..VBUS_FS_PROVENANCE_TABLE_SLOTS
 */
uint32_t vos3_vbus_fs_count(void);

/**
 * @brief Iterate every attached provenance entry.
 *
 * @param[in] cb        Callback invoked once per entry. Must not call
 *                      back into vos3_vbus_fs_* (lock recursion).
 * @param[in] user_data Opaque pointer forwarded to the callback.
 * @return Number of entries visited.
 *
 * Used by:
 *   - the audit-ring drain logic
 *   - the EU AI Act Annex IV export pipeline
 *   - the silicon CI evidence bundle generator
 */
typedef void (*vos3_vbus_fs_iter_cb)(
    const struct vos3_inode* inode,
    const vos3_vbus_fs_provenance_t* prov,
    void* user_data);

uint32_t vos3_vbus_fs_iterate(vos3_vbus_fs_iter_cb cb, void* user_data);

#endif /* VOS3_VBUS_FS_H */
