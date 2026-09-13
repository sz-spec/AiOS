/**
 * @file capability_table.c
 * @brief Software-fallback implementation of the per-byte capability
 *        table API (Sprint 16 / Item A3).
 *
 * Hardware-CHERI path is selected via `#ifdef CONFIG_CHERI` and not
 * compiled on M-series Mac (CONFIG_CHERI undefined). When compiled on
 * Morello / CHERIoT silicon the same API will be backed by hardware
 * capability tags + bounds; the public surface in capability.h does
 * not change.
 *
 * Honest scope ceiling
 * --------------------
 * This implementation is the SOFTWARE FALLBACK. It defends against
 * agent code attempting to access bytes outside its granted caps
 * (the Sprint-15 / C2 / C7 threat model) but does NOT defend against
 * malicious code that directly issues a wild read via the PTE map.
 * Such defense requires either CHERI silicon or an additional MPK /
 * SMEP / SMAP enforcement layer (out of scope for Sprint 16 Wave 1).
 *
 * The "table" passed to every API call is per-slot / per-process —
 * we do NOT keep any global state in this file. That keeps the
 * implementation re-entrant and fork-safe.
 */

#include "../../include/vos/capability.h"

/* ============================================================================
 * Helpers
 * ============================================================================ */

static int ranges_overlap(uintptr_t a_base, size_t a_len,
                          uintptr_t b_base, size_t b_len)
{
    uintptr_t a_end = a_base + a_len;
    uintptr_t b_end = b_base + b_len;
    return (a_base < b_end) && (b_base < a_end);
}

static int range_contains(uintptr_t cap_base, size_t cap_len,
                          uintptr_t req_base, size_t req_len)
{
    if (req_base < cap_base) return 0;
    if (req_base + req_len > cap_base + cap_len) return 0;
    return 1;
}

static int perms_are_superset(uint16_t cap_perms, uint16_t want_perms)
{
    /* cap_perms must cover ALL bits set in want_perms */
    return (cap_perms & want_perms) == want_perms;
}

/* ============================================================================
 * vos3_cap_table_init
 * ============================================================================ */

int vos3_cap_table_init(vos3_cap_table_t *table,
                        vos3_cap_entry_t *entries,
                        uint32_t           max_caps)
{
    if (table == (void*)0 || entries == (void*)0 || max_caps == 0U) {
        return VOS3_CAP_ERR_INVAL;
    }

    table->entries     = entries;
    table->max_caps    = max_caps;
    table->used        = 0;
    table->next_cap_id = 1;  /* 0 reserved as INVALID */

    /* Zero the backing storage so cap_id==0 sentinel = empty slot. */
    for (uint32_t i = 0; i < max_caps; i++) {
        entries[i].cap_id     = VOS3_CAP_ID_INVALID;
        entries[i].owner      = 0;
        entries[i].perms      = 0;
        entries[i].base       = 0;
        entries[i].length     = 0;
        entries[i].generation = 0;
    }
    return VOS3_CAP_OK;
}

/* ============================================================================
 * vos3_cap_grant
 *
 * Linear-scan grant. Two passes:
 *   1. Validate no existing entry conflicts (overlap with strict-superset
 *      perms — that would be a privilege-escalation overlap).
 *   2. Find first free slot, write the entry, return its cap_id.
 *
 * O(N) where N=max_caps. For the expected per-slot cap count (~hundreds),
 * cache-friendly linear scan is faster than a hash-table indirection.
 * ============================================================================ */

int vos3_cap_grant(vos3_cap_table_t *table,
                   vos3_cap_owner_t  owner,
                   uintptr_t         base,
                   size_t            length,
                   uint16_t          perms,
                   vos3_cap_id_t    *out_cap_id)
{
    if (table == (void*)0 || out_cap_id == (void*)0) {
        return VOS3_CAP_ERR_INVAL;
    }
    if (length == 0U) return VOS3_CAP_ERR_INVAL;
    if (perms == 0U)  return VOS3_CAP_ERR_INVAL;
    *out_cap_id = VOS3_CAP_ID_INVALID;

    if (table->used >= table->max_caps) return VOS3_CAP_ERR_FULL;

    /* Pass 1: privilege-escalation overlap check. We reject grants whose
     * range overlaps an existing entry from a DIFFERENT owner with perms
     * that the new grant tries to relax. Same-owner overlaps are allowed
     * (covers contiguous-region sub-grants). */
    for (uint32_t i = 0; i < table->max_caps; i++) {
        const vos3_cap_entry_t *e = &table->entries[i];
        if (e->cap_id == VOS3_CAP_ID_INVALID) continue;
        if (e->owner == owner) continue;
        if (!ranges_overlap(base, length, e->base, e->length)) continue;
        /* Different-owner overlap. Reject if the new grant gives MORE
         * permissions than the existing one in the overlapping region. */
        uint16_t extra = (uint16_t)(perms & ~e->perms);
        if (extra != 0U) return VOS3_CAP_ERR_OVERLAP;
    }

    /* Pass 2: write into the first empty slot. */
    for (uint32_t i = 0; i < table->max_caps; i++) {
        vos3_cap_entry_t *e = &table->entries[i];
        if (e->cap_id != VOS3_CAP_ID_INVALID) continue;
        e->cap_id     = table->next_cap_id;
        e->owner      = owner;
        e->perms      = perms;
        e->base       = base;
        e->length     = length;
        e->generation = e->generation + 1U;  /* ABA-safe re-use signal */
        *out_cap_id   = e->cap_id;
        table->next_cap_id++;
        if (table->next_cap_id == 0U) {
            /* 32-bit cap_id space wrapped — bump past 0 reserved value. */
            table->next_cap_id = 1U;
        }
        table->used++;
        return VOS3_CAP_OK;
    }
    /* Should be unreachable because used < max_caps was checked above. */
    return VOS3_CAP_ERR_FULL;
}

/* ============================================================================
 * vos3_cap_revoke
 * ============================================================================ */

int vos3_cap_revoke(vos3_cap_table_t *table, vos3_cap_id_t cap_id)
{
    if (table == (void*)0) return VOS3_CAP_ERR_INVAL;
    if (cap_id == VOS3_CAP_ID_INVALID) return VOS3_CAP_ERR_NOTFOUND;

    for (uint32_t i = 0; i < table->max_caps; i++) {
        vos3_cap_entry_t *e = &table->entries[i];
        if (e->cap_id != cap_id) continue;
        e->cap_id     = VOS3_CAP_ID_INVALID;
        e->owner      = 0;
        e->perms      = 0;
        e->base       = 0;
        e->length     = 0;
        e->generation = e->generation + 1U;
        table->used--;
        return VOS3_CAP_OK;
    }
    return VOS3_CAP_ERR_NOTFOUND;
}

/* ============================================================================
 * vos3_cap_check — HOT PATH
 *
 * Returns 1 if any live capability covers the full [base, base+length)
 * range AND the cap's perms are a superset of want_perms.
 *
 * We intentionally scan linearly — for small N (cap counts in the low
 * hundreds), branch prediction + cache locality beats hash indirection.
 * ============================================================================ */

int vos3_cap_check(const vos3_cap_table_t *table,
                   uintptr_t                base,
                   size_t                   length,
                   uint16_t                 want_perms)
{
    if (table == (void*)0) return 0;
    if (length == 0U)      return 0;
    if (want_perms == 0U)  return 1;  /* empty want = vacuously permitted */

    for (uint32_t i = 0; i < table->max_caps; i++) {
        const vos3_cap_entry_t *e = &table->entries[i];
        if (e->cap_id == VOS3_CAP_ID_INVALID) continue;
        if (!range_contains(e->base, e->length, base, length)) continue;
        if (!perms_are_superset(e->perms, want_perms)) continue;
        return 1;
    }
    return 0;
}

/* ============================================================================
 * vos3_cap_lookup
 * ============================================================================ */

int vos3_cap_lookup(const vos3_cap_table_t *table,
                    vos3_cap_id_t            cap_id,
                    vos3_cap_entry_t        *out)
{
    if (table == (void*)0 || out == (void*)0) return VOS3_CAP_ERR_INVAL;
    if (cap_id == VOS3_CAP_ID_INVALID)        return VOS3_CAP_ERR_NOTFOUND;

    for (uint32_t i = 0; i < table->max_caps; i++) {
        const vos3_cap_entry_t *e = &table->entries[i];
        if (e->cap_id != cap_id) continue;
        *out = *e;
        return VOS3_CAP_OK;
    }
    return VOS3_CAP_ERR_NOTFOUND;
}

/* ============================================================================
 * vos3_cap_iter
 * ============================================================================ */

int vos3_cap_iter(const vos3_cap_table_t *table,
                  vos3_cap_visitor_t       visitor,
                  void                    *opaque)
{
    if (table == (void*)0 || visitor == (void*)0) return VOS3_CAP_ERR_INVAL;

    for (uint32_t i = 0; i < table->max_caps; i++) {
        const vos3_cap_entry_t *e = &table->entries[i];
        if (e->cap_id == VOS3_CAP_ID_INVALID) continue;
        int rc = visitor(e, opaque);
        if (rc != 0) return rc;
    }
    return 0;
}
