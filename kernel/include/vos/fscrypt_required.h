/**
 * @file fscrypt_required.h
 * @brief Kernel-side fscrypt-required policy declarations (Sprint 16 / K5).
 *
 * Why this exists
 * ---------------
 *
 * From the 80-problem agent-era catalog, K5:
 *   "Encrypted-at-rest gap for model weights — even on LUKS/FileVault,
 *    model files are plaintext when read into memory; no per-file
 *    enforcement."
 *
 * fscrypt v2 (Linux ≥ 5.4, fs/crypto/) supports per-file encryption
 * with per-file keys. This header declares the vOS-side policy hook
 * that the model_loader path consults at mmap() time: if the file
 * resides under a policy-marked directory and is NOT fscrypt-protected,
 * vfs_open returns -EACCES with the new errno VOS3_E_FSCRYPT_REQUIRED.
 *
 * Public surface
 * --------------
 *
 *   vos3_fscrypt_policy_mark_dir(path, policy)
 *   vos3_fscrypt_policy_check(file_path) -> int
 *   vos3_fscrypt_policy_list() (debug)
 *
 * Errno
 * -----
 *
 *   VOS3_E_FSCRYPT_REQUIRED   — file under policy dir lacks fscrypt
 *
 * Honest scope ceiling
 * --------------------
 *
 *   - This header declares the policy hook. The kernel-side vfs_open
 *     integration that consults the policy lives in fs/vfs.c (touched
 *     in a Wave 4 follow-up). Userspace policy is exercised today via
 *     the backend/security/fscrypt_policy.py twin.
 *   - Policy granularity is per-directory subtree, not per-file. An
 *     operator marks /opt/vos3/models/ as fscrypt-required, and every
 *     descendant inherits the requirement.
 *   - Detection is best-effort: we check the file's fscrypt inode flag.
 *     A maliciously-crafted file that LIES about its flag is not
 *     defended against here — that's the kernel's fscrypt-verify path.
 */

#ifndef VOS_FSCRYPT_REQUIRED_H
#define VOS_FSCRYPT_REQUIRED_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Return codes
 * ============================================================================ */

#define VOS3_FSCRYPT_OK              0
#define VOS3_FSCRYPT_ERR_INVAL      -1
#define VOS3_FSCRYPT_ERR_REQUIRED   -2   /**< Policy demands fscrypt; file lacks it */
#define VOS3_FSCRYPT_ERR_NOTFOUND   -3
#define VOS3_FSCRYPT_ERR_FULL       -4   /**< Policy table at capacity */

#define VOS3_E_FSCRYPT_REQUIRED   200  /**< errno extension for vfs_open */

/* ============================================================================
 * Policy entry
 * ============================================================================ */

#define VOS3_FSCRYPT_PATH_MAX   1024U
#define VOS3_FSCRYPT_POLICY_MAX  64U   /**< Max marked subtrees per host */

typedef enum vos3_fscrypt_policy_kind {
    VOS3_FSCRYPT_POLICY_NONE = 0,
    VOS3_FSCRYPT_POLICY_REQUIRED = 1,
    VOS3_FSCRYPT_POLICY_PREFER = 2,    /**< Warn but don't block */
} vos3_fscrypt_policy_kind_t;

typedef struct vos3_fscrypt_policy_entry {
    char                       prefix[VOS3_FSCRYPT_PATH_MAX];
    vos3_fscrypt_policy_kind_t kind;
    uint64_t                   marker_id;
} vos3_fscrypt_policy_entry_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

int vos3_fscrypt_policy_mark_dir(const char *prefix,
                                  vos3_fscrypt_policy_kind_t kind);

int vos3_fscrypt_policy_unmark_dir(const char *prefix);

int vos3_fscrypt_policy_check(const char *file_path);

int vos3_fscrypt_policy_count(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS_FSCRYPT_REQUIRED_H */
