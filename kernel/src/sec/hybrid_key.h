/**
 * @file hybrid_key.h
 * @brief VOS3 Hybrid Identity Key — TPM secret × keyboard entropy
 *
 * @details Last-Fortress hardware identity primitive. The hybrid identity
 *          key is derived as
 *
 *              K_id  =  HMAC-SHA256( K_tpm_secret , K_keyboard_entropy )
 *
 *          where:
 *            - K_tpm_secret comes from the TPM's true RNG via
 *              tpm2_get_random() (TPM2_CC_GetRandom). It is fetched under
 *              an active salted HMAC session, so a passive attacker cannot
 *              forge or replay the GetRandom command.
 *            - K_keyboard_entropy is supplied by the caller and is sourced
 *              from PS/2 scancode + RDTSC timing at the keyboard ISR
 *              (vos3_keyboard_handle_scancode). It binds the resulting
 *              identity to a human-present boot rather than to a
 *              fully-headless attacker who controls firmware.
 *
 *          Composition rationale: HMAC-SHA256 is a PRF, so the output is
 *          indistinguishable from random as long as EITHER input is
 *          unknown to the attacker. This realises a defence-in-depth
 *          against (a) a compromised TPM that yields predictable randoms
 *          and (b) a compromised firmware/RDRAND that yields predictable
 *          kernel entropy: an attacker would need to control BOTH the
 *          hardware TPM state AND the user's keyboard timing to predict
 *          K_id.
 *
 * @version 1.0.0
 * @date 2026-05-04
 */

#ifndef VOS3_HYBRID_KEY_H
#define VOS3_HYBRID_KEY_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

#define VOS3_HYBRID_KEY_SIZE          32U
#define VOS3_HYBRID_KEY_MIN_KBD_BYTES 16U  /* Refuse derivation under this; not enough entropy */

/**
 * Derive the hybrid identity key.
 *
 *   out  ←  HMAC-SHA256( K_tpm_secret , K_keyboard_entropy )
 *
 * Internally pulls 32B of K_tpm_secret from TPM2_CC_GetRandom (under the
 * salted HMAC session installed by tpm2_init()). The TPM-side bytes are
 * wiped from the stack before return regardless of success or failure.
 *
 * @param kbd_entropy  Raw bytes captured from the keyboard ISR (mix of
 *                     scancodes and RDTSC deltas). Caller owns the buffer.
 * @param kbd_len      Length in bytes; must be >= VOS3_HYBRID_KEY_MIN_KBD_BYTES.
 * @param out          Output buffer for the 32-byte derived key.
 *
 * @return 0 on success; -1 if TPM unavailable, kbd_len < min, or any
 *         underlying primitive failed. On failure *out is left zeroed.
 */
int vos3_hybrid_key_derive(const uint8_t *kbd_entropy,
                           size_t kbd_len,
                           uint8_t out[VOS3_HYBRID_KEY_SIZE]);

/**
 * Derive AND cache the hybrid identity key for later retrieval via
 * vos3_hybrid_key_get(). Idempotent across boot — second call overwrites.
 *
 * @return 0 on success, -1 on failure (cache is wiped on failure).
 */
int vos3_hybrid_key_install(const uint8_t *kbd_entropy, size_t kbd_len);

/**
 * Copy the cached hybrid identity key.
 *
 * @return 0 if cache valid, -1 if no key has been installed yet.
 */
int vos3_hybrid_key_get(uint8_t out[VOS3_HYBRID_KEY_SIZE]);

/**
 * Wipe the cached key from memory (volatile zero-fill). Subsequent
 * vos3_hybrid_key_get() will return -1 until a fresh install is done.
 */
void vos3_hybrid_key_clear(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_HYBRID_KEY_H */
