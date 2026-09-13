/**
 * @file capability.h
 * @brief vOS per-byte capability table (Sprint 16 / Item A3)
 *
 * Why this exists
 * ---------------
 *
 * From the 80-problem agent-era catalog, A3:
 *   "Per-byte ACL gap for shared model memory — shmem enforces
 *    page-granularity only; agents that need fast read access to
 *    common weight pages have no fine-grained per-byte gating."
 *
 * On CHERI hardware (Arm Morello, Microsoft CHERIoT) every pointer
 * carries a tag bit and a bounds descriptor — byte-granularity
 * capability is a hardware property. vOS uses the hardware path when
 * available (CONFIG_CHERI=y at kernel build) and a software-fallback
 * capability table when not, so the same API works on every host
 * the Sprint 16 Wave 1 codebase ships to.
 *
 * Public surface
 * --------------
 *
 *   - vos3_cap_table_init(table, max_caps)
 *   - vos3_cap_grant(table, owner, base, length, perms) → cap_id
 *   - vos3_cap_revoke(table, cap_id) → 0/err
 *   - vos3_cap_check(table, base, length, want_perms) → 1/0
 *   - vos3_cap_lookup(table, cap_id, *out)
 *   - vos3_cap_iter(table, callback, opaque)
 *
 * The "table" is per-process / per-slot (callers store one per slot
 * in the Sprint-15 ai_guard region descriptor; integration with
 * g_model_slots[] happens in Sprint 16 / Wave 2 / B3+B4).
 *
 * Honest scope ceiling
 * --------------------
 *   - The software-fallback path uses an open-addressed hash table
 *     keyed by (owner, base) hash; lookup is O(1) average, O(N)
 *     worst case under collision. For the Sprint 16 deployment
 *     target (≤ 1024 caps per slot) this is fast enough.
 *   - Software fallback DOES NOT defend against direct physical-memory
 *     read attacks (caller can still issue a wild read through the
 *     existing PTE map). It defends against the AGENT-attempting-
 *     cross-region-byte-read class, which is the Sprint-15 / C2 / C7
 *     untrusted-input threat model.
 *   - CHERI hardware path is stubbed today (CONFIG_CHERI is OFF on
 *     M-series Mac dev host); software fallback exercises the same
 *     API so the tests cover both ports.
 */

#ifndef VOS_CAPABILITY_H
#define VOS_CAPABILITY_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Permission bits (composable via bitwise-or)
 * ============================================================================ */

#define VOS3_CAP_PERM_READ      0x01U   /**< Granted byte may be read */
#define VOS3_CAP_PERM_WRITE     0x02U   /**< Granted byte may be written */
#define VOS3_CAP_PERM_EXEC      0x04U   /**< Granted byte may be executed */
#define VOS3_CAP_PERM_SHARE     0x08U   /**< Cap may be re-granted to siblings */

#define VOS3_CAP_PERM_RO        (VOS3_CAP_PERM_READ)
#define VOS3_CAP_PERM_RW        (VOS3_CAP_PERM_READ | VOS3_CAP_PERM_WRITE)
#define VOS3_CAP_PERM_RX        (VOS3_CAP_PERM_READ | VOS3_CAP_PERM_EXEC)
#define VOS3_CAP_PERM_RWX       (VOS3_CAP_PERM_RW   | VOS3_CAP_PERM_EXEC)

/* ============================================================================
 * Return / error codes
 * ============================================================================ */

#define VOS3_CAP_OK              0
#define VOS3_CAP_ERR_INVAL      -1
#define VOS3_CAP_ERR_FULL       -2   /**< Table at max_caps capacity */
#define VOS3_CAP_ERR_NOTFOUND   -3   /**< cap_id not in table */
#define VOS3_CAP_ERR_DENIED     -4   /**< Permission check failed */
#define VOS3_CAP_ERR_OVERLAP    -5   /**< Grant would overlap an existing cap
                                          with conflicting permissions */
#define VOS3_CAP_ERR_NOMEM      -6   /**< Allocation failure (software-fallback) */

/* ============================================================================
 * Capability descriptor
 *
 * 32 bytes — fits in half a cacheline so a small table is cache-resident.
 * ============================================================================ */

#define VOS3_CAP_ID_INVALID     0U

typedef uint32_t vos3_cap_id_t;
typedef uint16_t vos3_cap_owner_t;

typedef struct vos3_cap_entry {
    vos3_cap_id_t      cap_id;     /**< Stable ID; 0 = unused slot */
    vos3_cap_owner_t   owner;      /**< Owning agent / slot ID */
    uint16_t           perms;      /**< Bitwise-OR of VOS3_CAP_PERM_* */
    uintptr_t          base;       /**< Byte-aligned base address */
    size_t             length;     /**< Bytes covered (>= 1) */
    uint64_t           generation; /**< Bumped on grant/revoke for ABA safety */
} vos3_cap_entry_t;

/* ============================================================================
 * Capability table
 *
 * Open-addressing hash table. `entries` array is owned by the caller and
 * passed at init time so the table can be statically allocated (kernel use)
 * or heap-allocated (test harnesses).
 * ============================================================================ */

typedef struct vos3_cap_table {
    vos3_cap_entry_t  *entries;    /**< Caller-provided storage */
    uint32_t           max_caps;   /**< Capacity (size of entries[]) */
    uint32_t           used;       /**< Currently-occupied slots */
    uint32_t           next_cap_id; /**< Monotonic ID counter (starts at 1) */
} vos3_cap_table_t;

/* ============================================================================
 * Iteration callback
 * ============================================================================ */

/**
 * @return 0 to continue iteration, non-zero to stop early.
 */
typedef int (*vos3_cap_visitor_t)(const vos3_cap_entry_t *entry, void *opaque);

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize a capability table over caller-provided storage.
 *
 * @param  table     Table struct to populate.
 * @param  entries   Backing storage; entries[0..max_caps-1] are zeroed.
 * @param  max_caps  Number of entries the table can hold.
 *
 * @return VOS3_CAP_OK / VOS3_CAP_ERR_INVAL on NULL or zero max_caps.
 */
int vos3_cap_table_init(vos3_cap_table_t *table,
                        vos3_cap_entry_t *entries,
                        uint32_t           max_caps);

/**
 * @brief Grant a capability over [base, base+length).
 *
 * Returns a stable cap_id that callers retain for later revoke/lookup.
 *
 * @param  table       Initialized table.
 * @param  owner       Owning agent / slot ID.
 * @param  base        Byte-aligned base address.
 * @param  length      Bytes covered (>= 1).
 * @param  perms       VOS3_CAP_PERM_* bitmask.
 * @param  out_cap_id  On success, the new cap_id (always > 0).
 *
 * @return VOS3_CAP_OK on success.
 *         VOS3_CAP_ERR_INVAL if length == 0 or out_cap_id is NULL.
 *         VOS3_CAP_ERR_FULL if table->used == table->max_caps.
 *         VOS3_CAP_ERR_OVERLAP if [base, base+length) overlaps an existing
 *           entry whose perms are a strict superset (catch privilege
 *           escalation via overlapping-grant).
 */
int vos3_cap_grant(vos3_cap_table_t *table,
                   vos3_cap_owner_t  owner,
                   uintptr_t         base,
                   size_t            length,
                   uint16_t          perms,
                   vos3_cap_id_t    *out_cap_id);

/**
 * @brief Revoke a capability by cap_id.
 *
 * @return VOS3_CAP_OK / VOS3_CAP_ERR_NOTFOUND.
 */
int vos3_cap_revoke(vos3_cap_table_t *table, vos3_cap_id_t cap_id);

/**
 * @brief Check whether the table permits an access to [base, base+length)
 *        with want_perms.
 *
 * @return 1 if any single existing capability covers the full request range
 *         and its perms are a superset of want_perms. 0 otherwise.
 *
 * Note: this is the HOT path — used on every shared-memory access from
 * the C7 (Wave 2) information-flow engine. Implementation deliberately
 * scans the table linearly because the expected per-slot cap count is
 * small (~tens to low hundreds) and branch-prediction wins beat the
 * hash-table indirection cost at that scale.
 */
int vos3_cap_check(const vos3_cap_table_t *table,
                   uintptr_t                base,
                   size_t                   length,
                   uint16_t                 want_perms);

/**
 * @brief Look up a capability by cap_id.
 *
 * @return VOS3_CAP_OK + out filled on success; VOS3_CAP_ERR_NOTFOUND if
 *         the cap_id was revoked or never granted.
 */
int vos3_cap_lookup(const vos3_cap_table_t *table,
                    vos3_cap_id_t            cap_id,
                    vos3_cap_entry_t        *out);

/**
 * @brief Visit every live capability in the table.
 *
 * @return 0 if visitor visited all entries; the non-zero value the visitor
 *         returned if it stopped early.
 */
int vos3_cap_iter(const vos3_cap_table_t *table,
                  vos3_cap_visitor_t       visitor,
                  void                    *opaque);

#ifdef __cplusplus
}
#endif

#endif /* VOS_CAPABILITY_H */
