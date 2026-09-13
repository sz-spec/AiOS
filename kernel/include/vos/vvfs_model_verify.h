/**
 * @file vvfs_model_verify.h
 * @brief M3 model-SecureBoot registry + verify decision — public ABI.
 *
 * @details Deliberately dependency-light (stdint only) so the pure verify core
 *          (vvfs_model_verify.c) can be built + KAT-tested on the host WITHOUT
 *          dragging in the kernel atomic/sync chain (which carries x86-only
 *          inline asm). The few vVFS/VFS constants the module needs are mirrored
 *          here behind #ifndef guards: in the kernel build vvfs.h is included
 *          first and its real values win; on the host the fallbacks apply. Keep
 *          these in sync with vfs.h / vvfs.h.
 */

#ifndef VOS3_VVFS_MODEL_VERIFY_H
#define VOS3_VVFS_MODEL_VERIFY_H

#include <stdint.h>

#ifndef VVFS_MAX_MOUNTS
#define VVFS_MAX_MOUNTS 4U          /* mirrors vvfs.h */
#endif
#ifndef VOS3_FS_ERR_INVAL
#define VOS3_FS_ERR_INVAL (-22)     /* mirrors vfs.h  */
#endif
#ifndef VOS3_FS_ERR_KEYREJECTED
#define VOS3_FS_ERR_KEYREJECTED (-129) /* mirrors vfs.h */
#endif

/** @brief Provision the BUILD-time trusted Ed25519 model-signing key. NOT
 *  settable by untrusted runtime VBus (a runtime anchor swap would bypass M3).
 *  Exposed for the host E2E test; production bakes the key via vvfs_trusted_key.h. */
void vvfs_set_trusted_model_key(const uint8_t pk[32]);

/** @brief Register a slot's OMS signature (digest + Ed25519 sig + signer pk).
 *  @return 0 on success, VOS3_FS_ERR_INVAL on bad slot. */
int vvfs_register_model_signature(uint8_t slot_id, const uint8_t digest[32],
                                  const uint8_t sig[64], const uint8_t signer_pk[32]);

/** @brief Fail-closed verify at SLOT_FINISH: signature present, signer == trust
 *  anchor, registered digest == computed digest, Ed25519 verify. Caches verdict.
 *  @return 0 if verified, VOS3_FS_ERR_KEYREJECTED otherwise. */
int vvfs_verify_model_slot(uint8_t slot_id, const uint8_t computed_digest[32]);

/** @brief 1 iff slot's model signature has been verified (read-path gate). */
int vvfs_model_slot_is_verified(uint8_t slot_id);

#endif /* VOS3_VVFS_MODEL_VERIFY_H */
