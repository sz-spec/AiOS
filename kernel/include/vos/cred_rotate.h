/**
 * @file cred_rotate.h
 * @brief Kernel credential rotation hook (Sprint 16 / Item F5).
 *
 * Why this exists
 * ---------------
 *
 * From the 80-problem agent-era catalog, F5:
 *   "Long-lived agent tokens vs JIT credentials — no kernel rotation
 *    hook. Industry moving to short-lived JIT (Vault + SPIFFE) but
 *    OS-level rotation hooks don't exist."
 *
 * When the userspace Vault JIT bridge rotates a credential, every open
 * file descriptor that holds the prior token (e.g. an HTTP session, a
 * database connection, an MCP channel) MUST be invalidated atomically
 * — otherwise the agent keeps using the stale token until the next I/O
 * happens to fail authentication, leaking a race window where the
 * rotated-out token still works.
 *
 * This header declares the kernel-side hook the userspace bridge calls
 * via the vos3_cred_rotate syscall. The kernel walks the process's fd
 * table, finds every fd whose private metadata holds the rotated token
 * hash, and either:
 *
 *   - Marks the fd "stale" so the next read/write returns EAUTH_ROTATED,
 *     letting the userspace caller obtain a fresh token + reissue.
 *   - Closes the fd outright (if the per-fd policy is "no graceful
 *     drain").
 *
 * Public surface
 * --------------
 *
 *   vos3_cred_rotate(token_hash, expiry_ns) → 0/err
 *
 * Token hashes are 32 bytes (SHA-256 of the raw token; never the raw
 * token itself in kernel APIs).
 *
 * Integration with userspace
 * --------------------------
 *
 * The userspace bridge (backend/services/vault_jit_bridge.py) computes
 * the SHA-256 of the rotated token, calls this syscall, and synchronously
 * waits for the kernel walk to complete before declaring the rotation
 * "applied". The bridge's VaultJITBridge.rotate_credential() returns a
 * RotationOutcome with the per-fd disposition counts.
 *
 * Honest scope ceiling
 * --------------------
 *
 *   - Today (Sprint 16 Wave 2), this header DECLARES the API. The
 *     kernel-side walker that consumes it is a Wave 3 follow-up that
 *     touches kernel/src/exec/fd_table.c. The userspace bridge ships
 *     today against a simulator that mimics the walker's effects so
 *     the integration is end-to-end testable.
 *
 *   - The token hash is not authenticated by the kernel — any caller
 *     with CAP_VOS3_CRED_ROTATE can invalidate any fd's bound token.
 *     Confine the capability to the JIT bridge process at deployment.
 */

#ifndef VOS_CRED_ROTATE_H
#define VOS_CRED_ROTATE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Return codes
 * ============================================================================ */

#define VOS3_CRED_ROTATE_OK           0
#define VOS3_CRED_ROTATE_ERR_INVAL   -1
#define VOS3_CRED_ROTATE_ERR_NOPERM  -2   /**< Caller lacks CAP_VOS3_CRED_ROTATE */
#define VOS3_CRED_ROTATE_ERR_NOTFOUND -3  /**< No fd holds the given token hash */

/* ============================================================================
 * Disposition counts (returned to userspace via shared-memory result page)
 * ============================================================================ */

typedef struct vos3_cred_rotate_outcome {
    uint32_t fds_marked_stale;     /**< Fds with policy=graceful_drain */
    uint32_t fds_closed;           /**< Fds with policy=immediate_close */
    uint32_t fds_skipped;          /**< Fds whose owner had CAP_RETAIN */
    uint64_t walk_duration_ns;     /**< Time spent walking the fd table */
} vos3_cred_rotate_outcome_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Rotate a credential: invalidate every fd that carries the given
 *        token hash.
 *
 * @param  token_sha256    32-byte SHA-256 of the rotated raw token.
 * @param  new_expiry_ns   Absolute wall-clock expiry of the REPLACEMENT
 *                          token. Kernel uses this as the new "valid
 *                          until" hint for any subsequent fd that binds
 *                          to the replacement.
 * @param  out             Disposition counts; can be NULL if caller
 *                          doesn't want them.
 *
 * @return VOS3_CRED_ROTATE_OK on success (even if no fds matched).
 *         ERR_INVAL if token_sha256 is NULL.
 *         ERR_NOPERM if caller lacks CAP_VOS3_CRED_ROTATE.
 */
int vos3_cred_rotate(const uint8_t              *token_sha256,
                     uint64_t                    new_expiry_ns,
                     vos3_cred_rotate_outcome_t *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS_CRED_ROTATE_H */
