/**
 * @file vvfs_model_verify.c
 * @brief M3 model-file SecureBoot — trusted-key registry + verify decision.
 *
 * @details Pure, dependency-light (ed25519 + vfs error codes only, no console /
 *          no kernel I/O) so it is host-testable in isolation. Holds the
 *          per-slot OMS signature registry and the build-provisioned trusted
 *          Ed25519 anchor, and makes the fail-closed verify decision. The
 *          read-path gate (vvfs_transport.c) and the SLOT_FINISH hook
 *          (vbus_ai_cmds.c) consume this module.
 *
 *          Trusted key is build-time (vvfs_trusted_key.h) — never settable by
 *          untrusted runtime VBus. Default unprovisioned ⇒ fail-closed.
 *
 *          M3 STILL OPEN / moat 49/80: pending QEMU enforcement matrix (Phase 5)
 *          and external crypto audit (Phase 6). Enforcement gated OFF by default.
 *
 * @version 1.0.0
 * @date 2026-06-07
 * @copyright Copyright (c) 2026 VOS3 Project, MIT.
 */

#include "../../include/vos/vvfs_model_verify.h"
#include "../../include/vos/ed25519.h"
#include "../../include/vos/vvfs_trusted_key.h"
#include <stdint.h>

typedef struct vvfs_model_sig {
    uint8_t expected_digest[32]; /* OMS bundle digest.sha256            */
    uint8_t sig[64];             /* Ed25519 signature over the digest   */
    uint8_t signer_pk[32];       /* OMS bundle signer public key        */
    uint8_t present;             /* 1 once a signature is registered    */
    uint8_t verified;            /* 1 once vvfs_verify_model_slot passes */
} vvfs_model_sig_t;

static vvfs_model_sig_t g_model_sig[VVFS_MAX_MOUNTS];

/* Build-time trust anchor (see vvfs_trusted_key.h). NOT runtime-VBus settable. */
static uint8_t g_trusted_model_key[32] = VOS3_TRUSTED_MODEL_PUBKEY_INIT;
static uint8_t g_trusted_model_key_set = VOS3_TRUSTED_MODEL_KEY_PROVISIONED;

/* Constant-time equality (1 if equal). */
static int vvfs_ct_eq(const uint8_t *a, const uint8_t *b, uint32_t n)
{
    uint8_t d = 0U;
    uint32_t i;
    for (i = 0U; i < n; i++) d |= (uint8_t)(a[i] ^ b[i]);
    return d == 0U;
}

void vvfs_set_trusted_model_key(const uint8_t pk[32])
{
    uint32_t i;
    for (i = 0U; i < 32U; i++) g_trusted_model_key[i] = pk[i];
    g_trusted_model_key_set = 1U;
}

int vvfs_register_model_signature(uint8_t slot_id, const uint8_t digest[32],
                                  const uint8_t sig[64], const uint8_t signer_pk[32])
{
    uint32_t i;
    if (slot_id >= VVFS_MAX_MOUNTS) return VOS3_FS_ERR_INVAL;
    for (i = 0U; i < 32U; i++) g_model_sig[slot_id].expected_digest[i] = digest[i];
    for (i = 0U; i < 64U; i++) g_model_sig[slot_id].sig[i] = sig[i];
    for (i = 0U; i < 32U; i++) g_model_sig[slot_id].signer_pk[i] = signer_pk[i];
    g_model_sig[slot_id].present = 1U;
    g_model_sig[slot_id].verified = 0U;
    return 0;
}

int vvfs_verify_model_slot(uint8_t slot_id, const uint8_t computed_digest[32])
{
    vvfs_model_sig_t *r;
    if (slot_id >= VVFS_MAX_MOUNTS) return VOS3_FS_ERR_INVAL;
    r = &g_model_sig[slot_id];
    r->verified = 0U;

    if (r->present == 0U) return VOS3_FS_ERR_KEYREJECTED;           /* no signature */
    if (g_trusted_model_key_set == 0U) return VOS3_FS_ERR_KEYREJECTED; /* no anchor */
    if (!vvfs_ct_eq(r->signer_pk, g_trusted_model_key, 32U))
        return VOS3_FS_ERR_KEYREJECTED;                            /* untrusted signer */
    if (!vvfs_ct_eq(r->expected_digest, computed_digest, 32U))
        return VOS3_FS_ERR_KEYREJECTED;                            /* digest mismatch */
    if (vos3_ed25519_verify(r->sig, computed_digest, 32U, r->signer_pk) != 0)
        return VOS3_FS_ERR_KEYREJECTED;                            /* bad signature */

    r->verified = 1U;
    return 0;
}

int vvfs_model_slot_is_verified(uint8_t slot_id)
{
    if (slot_id >= VVFS_MAX_MOUNTS) return 0;
    return g_model_sig[slot_id].verified ? 1 : 0;
}
