/**
 * @file vvfs_trusted_key.h
 * @brief M3 build-time trusted model-signing key provisioning.
 *
 * @details The Ed25519 public key that anchors model-file SecureBoot (M3) is
 *          provisioned at BUILD time, mirroring the Linux `.builtin_trusted_keys`
 *          model. It is deliberately NOT settable by runtime VBus transport — a
 *          runtime trust-anchor swap would let an attacker install their own key,
 *          self-sign a malicious model, and bypass the gate entirely.
 *
 *          DEFAULT: all-zero key, NOT provisioned → verification fails closed
 *          (no model can be activated under enforcement until a real key is
 *          baked in). A production/fortress build injects the real 32-byte
 *          Ed25519 model-signing public key by overriding the two macros below
 *          (e.g. via a generated header or -D flags), and sets
 *          VOS3_TRUSTED_MODEL_KEY_PROVISIONED to 1U.
 *
 *          The signature/bundle (verified against THIS anchor) may travel over
 *          VBus (see vvfs_register_model_signature); the anchor may not.
 */

#ifndef VOS3_VVFS_TRUSTED_KEY_H
#define VOS3_VVFS_TRUSTED_KEY_H

/* 32-byte Ed25519 public-key initializer. Default: all zero (no key). */
#ifndef VOS3_TRUSTED_MODEL_PUBKEY_INIT
#define VOS3_TRUSTED_MODEL_PUBKEY_INIT \
    { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, \
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 }
#endif

/* 1U once a real key has been baked in; 0U = unprovisioned (fail-closed). */
#ifndef VOS3_TRUSTED_MODEL_KEY_PROVISIONED
#define VOS3_TRUSTED_MODEL_KEY_PROVISIONED 0U
#endif

#endif /* VOS3_VVFS_TRUSTED_KEY_H */
