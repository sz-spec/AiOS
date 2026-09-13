/*
 * SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 vOS Project
 *
 * kernel/include/vos/profile.h — VOS_PROFILE compile-time selector.
 *
 * Three deployment profiles, corresponding 1:1 to backend/profile.py.
 * The active profile is selected at build time via the VOS3_PROFILE make
 * variable in kernel/Makefile, which sets -DVOS3_PROFILE=<n>:
 *
 *   make kernel-community   -> -DVOS3_PROFILE=0
 *   make kernel-enterprise  -> -DVOS3_PROFILE=1
 *   make kernel-fortress    -> -DVOS3_PROFILE=2  (also implies -DVOS3_TARGET_HYPERV)
 *
 * Source files conditionalise behaviour with the macros below. The
 * compile-time constant means the profile cannot drift between the
 * kernel image and the shipped userspace by a misconfigured env var
 * — what's compiled in is what's running.
 */

#ifndef VOS_PROFILE_H
#define VOS_PROFILE_H

#define VOS3_PROFILE_COMMUNITY  0
#define VOS3_PROFILE_ENTERPRISE 1
#define VOS3_PROFILE_FORTRESS   2

#ifndef VOS3_PROFILE
#define VOS3_PROFILE VOS3_PROFILE_COMMUNITY
#endif

#define VOS3_PROFILE_IS_COMMUNITY  (VOS3_PROFILE == VOS3_PROFILE_COMMUNITY)
#define VOS3_PROFILE_IS_ENTERPRISE (VOS3_PROFILE == VOS3_PROFILE_ENTERPRISE)
#define VOS3_PROFILE_IS_FORTRESS   (VOS3_PROFILE == VOS3_PROFILE_FORTRESS)

/* Capability gates — single source of truth for kernel-side conditionals.
 * Backend mirror lives in backend/profile.py. Keep these in lockstep. */
#define VOS3_REQUIRES_PQ_SIG                 VOS3_PROFILE_IS_FORTRESS
#define VOS3_REQUIRES_TDX_ATTESTATION        VOS3_PROFILE_IS_FORTRESS
#define VOS3_REQUIRES_SIGSTORE_VERIFY        VOS3_PROFILE_IS_FORTRESS
#define VOS3_REQUIRES_ENCRYPTED_COMPLIANCE   VOS3_PROFILE_IS_FORTRESS
#define VOS3_REQUIRES_HYBRID_CLASSICAL       (VOS3_PROFILE_IS_ENTERPRISE || VOS3_PROFILE_IS_FORTRESS)

#ifdef __cplusplus
extern "C" {
#endif

static inline int vos3_profile_active(void) { return VOS3_PROFILE; }

static inline const char *vos3_profile_name(void)
{
#if VOS3_PROFILE_IS_FORTRESS
    return "fortress";
#elif VOS3_PROFILE_IS_ENTERPRISE
    return "enterprise";
#else
    return "community";
#endif
}

#ifdef __cplusplus
}
#endif

#endif /* VOS_PROFILE_H */
