/**
 * @file ed25519.h
 * @brief Ed25519 detached signature VERIFY (RFC 8032) — verify-only.
 *
 * @details Freestanding, GPR-only verifier for the M3 model-file SecureBoot
 *          gate. There is intentionally NO signing and NO secret-key handling
 *          in the kernel — only public-key verification of an OMS / RFC-8032
 *          Ed25519 signature over a message (in M3, the message is the 32-byte
 *          SHA-256 digest of the model file). Uses vos3_sha512() internally.
 *
 *          Implementation is a faithful port of the public-domain TweetNaCl
 *          `crypto_sign_open` verify path (D. J. Bernstein et al.), validated
 *          against RFC 8032 §7.1 known-answer vectors. Operates only on public
 *          data (signature, public key, message), so it carries no secret-
 *          dependent branches.
 */

#ifndef VOS3_ED25519_H
#define VOS3_ED25519_H

#include <stdint.h>
#include <stddef.h>

#define VOS3_ED25519_SIG_SIZE     64U
#define VOS3_ED25519_PUBKEY_SIZE  32U

/**
 * @brief Verify a detached Ed25519 signature (RFC 8032).
 *
 * @param[in] sig      64-byte signature (R || S).
 * @param[in] msg      message that was signed (M3: SHA-256(model) digest).
 * @param[in] msg_len  length of @p msg in bytes.
 * @param[in] pubkey   32-byte Ed25519 public key (A, compressed).
 * @return 0 if the signature is valid; -1 otherwise (invalid signature,
 *         malformed/non-canonical public key, or rejected point).
 */
int vos3_ed25519_verify(const uint8_t sig[VOS3_ED25519_SIG_SIZE],
                        const uint8_t *msg, size_t msg_len,
                        const uint8_t pubkey[VOS3_ED25519_PUBKEY_SIZE]);

#endif /* VOS3_ED25519_H */
