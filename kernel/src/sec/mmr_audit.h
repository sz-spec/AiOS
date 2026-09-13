/**
 * @file mmr_audit.h
 * @brief VOS3 Merkle Mountain Range — Append-Only Audit Ledger
 *
 * An MMR is an append-only structure where each leaf is an irreversible
 * event (syscall, model switch, or boot measurement). The root hash
 * changes monotonically: it can only grow, never shrink or revert.
 *
 * MMR properties:
 *  - O(log N) append (peak merge)
 *  - O(log N) inclusion proof
 *  - O(1) root hash query (Bagging-the-Peaks)
 *  - Zero heap allocation: static peak array for 2^64 leaves
 *
 * @version 1.0.0
 * @date 2026-04-24
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_MMR_AUDIT_H
#define VOS3_MMR_AUDIT_H

#include <stdint.h>
#include <stddef.h>

/* Maximum height of the MMR binary tree = 64 → supports 2^64 leaves */
#define MMR_MAX_PEAKS    64
#define MMR_HASH_SIZE    32      /* SHA-256 output bytes */
#define MMR_PROOF_MAX    64      /* Max siblings in an inclusion proof */

/* ============================================================================
 * Data Structures
 * ============================================================================ */

/**
 * A single MMR leaf — represents one auditable event.
 *
 * Entropy bytes are XORed into the leaf hash before it enters the tree,
 * binding each event to the hardware RNG state at the moment it occurred.
 */
typedef struct mmr_leaf {
    uint64_t timestamp;      /* rdtsc() at event time */
    uint64_t syscall_nr;     /* syscall number (0 = boot/non-syscall event) */
    uint8_t  entropy[8];     /* RDSEED/RDRAND bytes at event time */
    uint8_t  data[32];       /* caller-supplied context (e.g. first arg hash) */
} mmr_leaf_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * Initialize the MMR. Must be called once before mmr_append().
 * Safe to call from kernel init before scheduler starts.
 */
void mmr_init(void);

/**
 * Append one leaf to the MMR.
 *
 * Computes SHA-256(leaf) XOR entropy, merges peaks bottom-up.
 * O(log N) worst case; O(1) amortized.
 *
 * @param leaf   Caller-populated leaf struct.
 */
void mmr_append(const mmr_leaf_t *leaf);

/**
 * Compute the current MMR root hash using Bagging-the-Peaks.
 *
 * The root is: SHA-256(peak[highest] || peak[...] || peak[0] || leaf_count)
 * Empty MMR → all-zero root.
 *
 * @param out    32-byte buffer to receive the root hash.
 */
void mmr_root(uint8_t out[MMR_HASH_SIZE]);

/**
 * Return the total number of leaves appended so far.
 */
uint64_t mmr_leaf_count(void);

/**
 * Convenience: append a syscall event with RDRAND entropy automatically.
 *
 * @param syscall_nr   Syscall number.
 * @param arg0         First syscall argument (for traceability).
 */
void mmr_record_syscall(uint64_t syscall_nr, uint64_t arg0);

/**
 * Convenience: append a boot/measurement event (no syscall context).
 *
 * @param label_hash   SHA-256 of an ASCII label (e.g. "ktext_integrity").
 */
void mmr_record_event(const uint8_t label_hash[MMR_HASH_SIZE]);

/**
 * OLYMPUS Tier-A (S2): pin the ledger as finalized. After this call,
 * any further mmr_append() returns without modifying state. Use after
 * exporting the root hash to a transparency log so a Ring-0 attacker
 * cannot post-tamper the audit trail. Idempotent.
 */
void mmr_finalize(void);

/**
 * @return non-zero iff mmr_finalize() has been called since boot.
 */
int mmr_is_finalized(void);

#endif /* VOS3_MMR_AUDIT_H */
