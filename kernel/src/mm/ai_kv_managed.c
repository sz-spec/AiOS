/**
 * @file ai_kv_managed.c
 * @brief 3-Tier Managed KV Cache — Eviction, Promotion, LRU
 *
 * @details Manages the 3-tier KV cache hierarchy:
 *          Tier 0 (Hot):  Existing HugePage pinning (kv_hp_phys[])
 *          Tier 1 (Warm): TQ4-compressed in PMM pages
 *          Tier 2 (Cold): TQ3-compressed in vVFS blocks
 *
 *          Eviction: Hot->Warm (TQ4 compress), Warm->Cold (TQ3 re-compress)
 *          Promotion: Cold->Warm (TQ3 decompress + TQ4 compress),
 *                     Warm->Hot (TQ4 decompress into HugePage)
 *          Policy: LRU by sequence number — oldest KV pairs evicted first.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.1: Managed KV Cache
 */

#include "ai_guard_internal.h"
#include "../../include/vos/ai_kv_managed.h"
#include "../../include/vos/vvfs.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

int vos3_kv_managed_init(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_kv_managed_t *mgd = &slot->kv_managed;

    /* Zero all managed state */
    memset(mgd, 0, sizeof(vos3_kv_managed_t));
    mgd->initialized = 1;

    /* Reset prefix sharing state */
    slot->kv_prefix_refcount = 0;
    slot->kv_prefix_shared   = 0;
    slot->kv_prefix_src_slot = 0xFF; /* invalid sentinel */

    VOS3_INFO("[KV-MGD] Slot %u managed KV cache initialized "
              "(T1 max=%u pages, T2 max=%u blocks)",
              slot_id, VOS3_KV_WARM_MAX_PAGES, VOS3_KV_COLD_MAX_BLOCKS);

    return 0;
}

/* ============================================================================
 * EVICTION: HOT -> WARM (TQ4 compress)
 * ============================================================================ */

int vos3_kv_evict_to_warm(uint8_t slot_id, uint32_t hp_index)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22;
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_kv_managed_t *mgd = &slot->kv_managed;

    if (mgd->initialized == 0U) {
        return -38; /* ENOSYS — not initialized */
    }
    if (hp_index >= slot->kv_hp_count) {
        return -22; /* EINVAL — invalid HugePage index */
    }

    /* Check Tier-1 capacity */
    if (mgd->tier1_count >= VOS3_KV_WARM_MAX_PAGES) {
        VOS3_WARN("[KV-MGD] Slot %u: Tier-1 full (%u/%u), cannot evict T0->T1",
                  slot_id, mgd->tier1_count, VOS3_KV_WARM_MAX_PAGES);
        return -28; /* ENOSPC */
    }

    /*
     * The HugePage at kv_hp_phys[hp_index] contains 2MB of raw KV data.
     * In a real system we'd read from the physical page. Here we simulate
     * the compression by recording metadata and allocating a PMM page
     * for the compressed output.
     */
    uint32_t src_len = VVFS_BLOCK_SIZE; /* 2MB HugePage worth of KV data */

    /* Allocate a PMM page for the compressed output */
    uintptr_t phys_page = vos3_pmm_alloc(0);
    if (phys_page == 0U) {
        VOS3_WARN("[KV-MGD] Slot %u: PMM alloc failed for T1 entry", slot_id);
        return -12; /* ENOMEM */
    }

    /*
     * TQ4 compress: 2MB -> ~524KB.
     * We estimate the compressed size based on ratio.
     * The actual compress API works on byte buffers, but in kernel context
     * we work with physical page metadata tracking rather than full
     * 2MB buffer operations to avoid stack overflow.
     */
    uint32_t compressed_est = (src_len * VOS3_KV_TQ4_RATIO_DEN) / VOS3_KV_TQ4_RATIO_NUM;

    /* Fill Tier-1 entry */
    vos3_kv_tier1_entry_t *entry = &mgd->tier1[mgd->tier1_count];
    entry->phys_page      = phys_page;
    entry->seq_start      = mgd->evict_seq_warm;
    entry->seq_end        = mgd->evict_seq_warm + (src_len / 64U); /* ~32K seq positions per HP */
    entry->compressed_len = compressed_est;
    entry->original_len   = src_len;
    entry->quantized      = 1;
    entry->valid          = 1;

    mgd->tier1_count++;
    mgd->evictions_to_warm++;
    mgd->total_compressed += src_len;
    mgd->evict_seq_warm = entry->seq_end;

    VOS3_INFO("[KV-MGD] Slot %u: Evicted HP[%u] to T1[%u] "
              "(seq %u..%u, %uKB -> ~%uKB TQ4)",
              slot_id, hp_index, mgd->tier1_count - 1U,
              entry->seq_start, entry->seq_end,
              src_len / 1024U, compressed_est / 1024U);

    return 0;
}

/* ============================================================================
 * EVICTION: WARM -> COLD (TQ3 re-compress via vVFS)
 * ============================================================================ */

int vos3_kv_evict_to_cold(uint8_t slot_id, uint32_t tier1_idx)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22;
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_kv_managed_t *mgd = &slot->kv_managed;

    if (mgd->initialized == 0U) {
        return -38;
    }
    if (tier1_idx >= mgd->tier1_count) {
        return -22;
    }

    vos3_kv_tier1_entry_t *t1 = &mgd->tier1[tier1_idx];
    if (t1->valid == 0U) {
        return -22; /* already invalid */
    }

    /* Check Tier-2 capacity */
    if (mgd->tier2_count >= VOS3_KV_COLD_MAX_BLOCKS) {
        VOS3_WARN("[KV-MGD] Slot %u: Tier-2 full (%u/%u), cannot evict T1->T2",
                  slot_id, mgd->tier2_count, VOS3_KV_COLD_MAX_BLOCKS);
        return -28; /* ENOSPC */
    }

    /*
     * Re-compress from TQ4 to TQ3 for deeper compression.
     * TQ3 provides ~4.9x vs TQ4's ~3.8x.
     * Write the result to a vVFS block.
     */
    uint32_t tq3_compressed_est =
        (t1->original_len * VOS3_KV_TQ3_RATIO_DEN) / VOS3_KV_TQ3_RATIO_NUM;

    /* Allocate vVFS block index */
    uint32_t block_idx = mgd->tier2_count;

    /* Write compressed data to vVFS (the transport layer handles encoding) */
    /* In production, we'd read the TQ4 data, decompress, re-compress with TQ3,
     * and write to vVFS. Here we record the metadata. */

    /* Fill Tier-2 entry */
    vos3_kv_tier2_entry_t *t2 = &mgd->tier2[mgd->tier2_count];
    t2->block_idx       = block_idx;
    t2->seq_start       = t1->seq_start;
    t2->seq_end         = t1->seq_end;
    t2->compressed_len  = tq3_compressed_est;
    t2->original_len    = t1->original_len;
    t2->valid           = 1;

    mgd->tier2_count++;
    mgd->evictions_to_cold++;
    mgd->evict_seq_cold = t2->seq_end;

    /* Free the Tier-1 entry's PMM page */
    if (t1->phys_page != 0U) {
        vos3_pmm_free(t1->phys_page);
    }
    t1->valid = 0;
    t1->phys_page = 0;

    VOS3_INFO("[KV-MGD] Slot %u: Evicted T1[%u] to T2[%u] "
              "(seq %u..%u, TQ4 ~%uKB -> TQ3 ~%uKB)",
              slot_id, tier1_idx, mgd->tier2_count - 1U,
              t2->seq_start, t2->seq_end,
              t1->compressed_len / 1024U, tq3_compressed_est / 1024U);

    return 0;
}

/* ============================================================================
 * PROMOTION
 * ============================================================================ */

int vos3_kv_promote(uint8_t slot_id, uint32_t seq_pos)
{
    vos3_kv_tier_t tier;
    uint32_t index;

    int rc = vos3_kv_lookup_seq(slot_id, seq_pos, &tier, &index);
    if (rc != 0) {
        return rc; /* -ENOENT */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_kv_managed_t *mgd = &slot->kv_managed;

    if (tier == VOS3_KV_TIER_COLD) {
        /*
         * Promote Cold -> Warm: Decompress TQ3, re-compress TQ4, store in PMM.
         * If Tier-1 is full, evict oldest T1 entry to T2 first.
         */
        if (mgd->tier1_count >= VOS3_KV_WARM_MAX_PAGES) {
            /* LRU eviction: evict the oldest (lowest seq) T1 entry */
            uint32_t oldest_idx = 0;
            uint32_t oldest_seq = 0xFFFFFFFFU;
            for (uint32_t i = 0; i < mgd->tier1_count; i++) {
                if (mgd->tier1[i].valid != 0U && mgd->tier1[i].seq_start < oldest_seq) {
                    oldest_seq = mgd->tier1[i].seq_start;
                    oldest_idx = i;
                }
            }
            rc = vos3_kv_evict_to_cold(slot_id, oldest_idx);
            if (rc != 0) {
                return rc;
            }
        }

        /* Now promote from T2 to T1 */
        vos3_kv_tier2_entry_t *t2 = &mgd->tier2[index];
        uintptr_t phys_page = vos3_pmm_alloc(0);
        if (phys_page == 0U) {
            return -12; /* ENOMEM */
        }

        uint32_t tq4_est = (t2->original_len * VOS3_KV_TQ4_RATIO_DEN) /
                            VOS3_KV_TQ4_RATIO_NUM;

        /* Create T1 entry in first available slot (Fix 9: guard PMM leak) */
        int placed = 0;
        for (uint32_t i = 0; i < VOS3_KV_WARM_MAX_PAGES; i++) {
            if (mgd->tier1[i].valid == 0U) {
                mgd->tier1[i].phys_page      = phys_page;
                mgd->tier1[i].seq_start      = t2->seq_start;
                mgd->tier1[i].seq_end        = t2->seq_end;
                mgd->tier1[i].compressed_len = tq4_est;
                mgd->tier1[i].original_len   = t2->original_len;
                mgd->tier1[i].quantized      = 1;
                mgd->tier1[i].valid          = 1;
                mgd->tier1_count++;
                placed = 1;
                break;
            }
        }
        if (!placed) {
            vos3_pmm_free(phys_page);
            return -28; /* ENOSPC — no free T1 slot despite eviction */
        }

        /* Invalidate T2 entry */
        t2->valid = 0;
        mgd->promotions++;
        mgd->total_decompressed += t2->original_len;

        VOS3_INFO("[KV-MGD] Slot %u: Promoted seq %u from T2->T1", slot_id, seq_pos);

    } else if (tier == VOS3_KV_TIER_WARM) {
        /*
         * Promote Warm -> Hot: Decompress TQ4 into a HugePage.
         * In production, we'd alloc a HugePage and decompress into it.
         * For now, record the promotion metadata.
         */
        vos3_kv_tier1_entry_t *t1 = &mgd->tier1[index];

        /* Free the T1 PMM page (data would be decompressed into HugePage) */
        if (t1->phys_page != 0U) {
            vos3_pmm_free(t1->phys_page);
        }
        t1->valid = 0;
        t1->phys_page = 0;

        mgd->promotions++;
        mgd->total_decompressed += t1->original_len;

        VOS3_INFO("[KV-MGD] Slot %u: Promoted seq %u from T1->T0", slot_id, seq_pos);

    } else {
        /* Already in Hot tier — nothing to do */
        return 0;
    }

    return 0;
}

/* ============================================================================
 * SEQUENCE LOOKUP
 * ============================================================================ */

int vos3_kv_lookup_seq(uint8_t slot_id, uint32_t seq_pos,
                       vos3_kv_tier_t *tier, uint32_t *index)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || tier == NULL || index == NULL) {
        return -22;
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_kv_managed_t *mgd = &slot->kv_managed;

    if (mgd->initialized == 0U) {
        return -38;
    }

    /* Search Tier-0 (Hot): The current KV HugePages.
     * Tier-0 data is in the existing kv_hp_phys[] — we check if seq_pos
     * falls within the active (non-evicted) range. */
    /* For Tier-0 we don't track seq ranges explicitly — it's "current head".
     * If seq_pos is beyond evict_seq_warm, it's in Tier-0. */
    if (seq_pos >= mgd->evict_seq_warm) {
        *tier = VOS3_KV_TIER_HOT;
        *index = 0;
        return 0;
    }

    /* Search Tier-1 (Warm) */
    for (uint32_t i = 0; i < VOS3_KV_WARM_MAX_PAGES; i++) {
        if (mgd->tier1[i].valid != 0U &&
            seq_pos >= mgd->tier1[i].seq_start &&
            seq_pos < mgd->tier1[i].seq_end) {
            *tier = VOS3_KV_TIER_WARM;
            *index = i;
            return 0;
        }
    }

    /* Search Tier-2 (Cold) */
    for (uint32_t i = 0; i < VOS3_KV_COLD_MAX_BLOCKS; i++) {
        if (mgd->tier2[i].valid != 0U &&
            seq_pos >= mgd->tier2[i].seq_start &&
            seq_pos < mgd->tier2[i].seq_end) {
            *tier = VOS3_KV_TIER_COLD;
            *index = i;
            return 0;
        }
    }

    return -2; /* ENOENT — seq_pos not found in any tier */
}
