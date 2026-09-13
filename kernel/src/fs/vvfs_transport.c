/**
 * @file vvfs_transport.c
 * @brief vVFS Block Transport — 2MB Aligned Reads/Writes Through AI Guard ACL
 *
 * @details All block I/O operations pass through the ACL gate (owner_tid check)
 *          and the constant-time block codec (noise padding + CRC32C).
 *          Every read/write touches exactly 2MB — timing-invariant at block level.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.1: Privacy Moat
 */

#include "../../include/vos/vvfs.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/task.h"         /* vos3_task_current() for ACL check */
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include <stdint.h>

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/* Model slot array (defined in ai_slots.c) */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* Backing store accessors (defined in vvfs.c) */
extern vvfs_slot_store_t *vvfs_get_store(uint8_t slot_id);
extern vvfs_mount_data_t *vvfs_get_mount(uint8_t slot_id);

/* ============================================================================
 * ACL CHECK
 * ============================================================================ */

/**
 * @brief Verify the caller is authorized to access the given slot's vVFS
 *
 * Checks that:
 * 1. slot_id is valid
 * 2. The vVFS is mounted for this slot
 * 3. The slot has a valid owner_tid (non-zero)
 *
 * @param[in] slot_id Model slot to check
 * @return 0 on success, -EACCES on failure
 */
static int vvfs_transport_acl(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -13; /* EACCES */
    }

    vvfs_mount_data_t *mdata = vvfs_get_mount(slot_id);
    if (mdata == NULL || mdata->mounted == 0U) {
        return -13; /* EACCES — not mounted */
    }

    /* Verify the slot has an active owner AND the caller matches */
    uint32_t owner = g_model_slots[slot_id].owner_tid;
    if (owner == 0U) {
        return -13; /* EACCES — no owner */
    }

    vos3_task_t *cur = vos3_task_current();
    if (cur == NULL || cur->tid != owner) {
        return -13; /* EACCES — caller is not the slot owner */
    }

    return 0;
}

/* ============================================================================
 * M3 — MODEL-FILE SECUREBOOT READ-PATH GATE (fail-closed; enforcement OFF by
 * default). The trusted-key registry + verify decision live in the pure,
 * host-testable vvfs_model_verify.c; this gate just consumes the cached
 * verdict (vvfs_model_slot_is_verified). The verdict is set at SLOT_FINISH
 * (vbus_ai_cmds.c) via vvfs_verify_model_slot(). M3 STILL OPEN; moat 49/80.
 * ============================================================================ */

int vvfs_model_sig_verify(uint8_t slot_id, const void *buf, uint32_t len)
{
    (void)buf; (void)len;
#if VOS3_VVFS_REQUIRE_MODEL_SIG
    if (vvfs_model_slot_is_verified(slot_id)) return 0;
    VOS3_WARN("[vVFS-M3] read gate: slot %u not signature-verified — "
              "reject (fail-closed)", slot_id);
    return VOS3_FS_ERR_KEYREJECTED;
#else
    (void)slot_id;
    /* Enforcement disabled by default: M3 not active; moat unchanged 49/80. */
    return 0;
#endif
}

/* ============================================================================
 * READ BLOCK
 * ============================================================================ */

int vvfs_read_block(uint8_t slot_id, uint32_t block_idx,
                    void *out_buf, uint32_t buf_cap, uint32_t *out_len)
{
    if (out_buf == NULL || out_len == NULL) {
        return -22; /* EINVAL */
    }

    /* ACL gate */
    int rc = vvfs_transport_acl(slot_id);
    if (rc != 0) {
        VOS3_WARN("[vVFS-XPORT] Read ACL denied for slot %u", slot_id);
        return rc;
    }

    /* M3 SecureBoot gate (fail-closed when VOS3_VVFS_REQUIRE_MODEL_SIG=1; a
     * no-op in default builds — see vvfs_model_sig_verify above). Scaffold
     * placement: production verifies the slot's model signature once at
     * SLOT_START/ingestion, not per block read. */
    rc = vvfs_model_sig_verify(slot_id, NULL, 0U);
    if (rc != 0) {
        VOS3_WARN("[vVFS-XPORT] M3 signature gate rejected slot %u (rc=%d)",
                  slot_id, rc);
        return rc;
    }

    if (block_idx >= VVFS_MAX_BLOCKS) {
        return -22; /* EINVAL */
    }

    vvfs_slot_store_t *store = vvfs_get_store(slot_id);
    if (store == NULL) {
        return -5; /* EIO */
    }

    /* Check if block has data */
    uint32_t payload_len = store->block_used[block_idx];
    if (payload_len == 0U) {
        *out_len = 0;
        return 0; /* Empty block — valid read of zero bytes */
    }

    if (buf_cap < payload_len) {
        return -22; /* EINVAL — buffer too small */
    }

    /* Decode through constant-time codec */
    rc = vvfs_decode_block(store->blocks[block_idx], payload_len,
                           store->block_crc[block_idx],
                           out_buf, out_len);

    if (rc != 0) {
        VOS3_WARN("[vVFS-XPORT] Decode failed for slot %u block %u (err=%d)",
                  slot_id, block_idx, rc);
    } else {
        VOS3_DEBUG("[vVFS-XPORT] Read slot %u block %u: %u bytes",
                   slot_id, block_idx, *out_len);
    }

    return rc;
}

/* ============================================================================
 * WRITE BLOCK
 * ============================================================================ */

int vvfs_write_block(uint8_t slot_id, uint32_t block_idx,
                     const void *data, uint32_t len)
{
    if (data == NULL && len > 0U) {
        return -22; /* EINVAL */
    }

    /* ACL gate */
    int rc = vvfs_transport_acl(slot_id);
    if (rc != 0) {
        VOS3_WARN("[vVFS-XPORT] Write ACL denied for slot %u", slot_id);
        return rc;
    }

    if (block_idx >= VVFS_MAX_BLOCKS) {
        return -22; /* EINVAL */
    }
    if (len > VVFS_BLOCK_SIZE) {
        return -22; /* EINVAL — payload exceeds block */
    }

    vvfs_slot_store_t *store = vvfs_get_store(slot_id);
    if (store == NULL) {
        return -5; /* EIO */
    }

    /* Encode through constant-time codec into backing store */
    uint32_t crc = 0;
    uint8_t noise_seed[16];
    rc = vvfs_encode_block(data, len, store->blocks[block_idx],
                           &crc, noise_seed);
    if (rc != 0) {
        VOS3_WARN("[vVFS-XPORT] Encode failed for slot %u block %u (err=%d)",
                  slot_id, block_idx, rc);
        return rc;
    }

    /* Update block metadata */
    store->block_used[block_idx] = len;
    store->block_crc[block_idx]  = crc;

    VOS3_DEBUG("[vVFS-XPORT] Write slot %u block %u: %u bytes (crc=0x%08x)",
               slot_id, block_idx, len, crc);

    return 0;
}

/* ============================================================================
 * BLOCK COUNT
 * ============================================================================ */

uint32_t vvfs_block_count(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return 0;
    }

    vvfs_mount_data_t *mdata = vvfs_get_mount(slot_id);
    if (mdata == NULL || mdata->mounted == 0U) {
        return 0;
    }

    return VVFS_MAX_BLOCKS;
}
