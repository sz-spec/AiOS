/*
 * SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Common Types — ABI Anchor for Open-Core
 * ==============================================
 *
 * Single source of truth for types and tags that BOTH the CORE codebase
 * and the PRO codebase reference. The file is intentionally kept in
 * the CORE include tree (kernel/include/vos/) — duplicating these
 * definitions in PRO would create ABI drift and silently break struct
 * layout compatibility between flavors.
 *
 * Charter Rule (Open-Core v20.5.2): cross-flavor types live HERE.
 * Pro-only structures (e.g. license_blob_t) belong in kernel/src/pro/
 * headers, NOT in this file.
 */

#ifndef VOS3_COMMON_TYPES_H
#define VOS3_COMMON_TYPES_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Build-flavor identification ---- */

typedef enum vos3_build_flavor {
    VOS3_FLAVOR_CORE = 0,   /* MIT open source */
    VOS3_FLAVOR_PRO  = 1,   /* Sovereign Enterprise */
} vos3_build_flavor_t;

/* ---- License posture (returned from kernel/src/pro/license_check.c) ---- */

typedef enum vos3_license_state {
    VOS3_LICENSE_NONE     = 0,  /* CORE build OR PRO build w/o license */
    VOS3_LICENSE_VALID    = 1,  /* PRO build w/ verified signature */
    VOS3_LICENSE_EXPIRED  = 2,  /* PRO build w/ expired license */
    VOS3_LICENSE_INVALID  = 3,  /* PRO build w/ tampered/wrong signature */
} vos3_license_state_t;

/* ---- Hardware fingerprint binding (v20.5.2 stub) ---- */

/* The hardware fingerprint binds a PRO license claim to a specific host.
 * Composition (designed; see license_check.c for the live implementation
 * status):
 *
 *   fp = SHA-256( cpuid_brand_string ‖ cpuid_family_model_stepping
 *                 ‖ tpm_endorsement_key_pubkey ‖ smbios_uuid )
 *
 * 32-byte SHA-256 output. Compared against the `host_fingerprint` field
 * of a signed `vos3.lic` blob at boot. Mismatch → license treated as
 * INVALID and PRO features fall back to CORE behavior (does NOT brick
 * the kernel — see open-core charter Rule 1).
 *
 * Deferred to v20.6: actual TPM EK extraction + SMBIOS read. Today the
 * fingerprint is computed from CPUID alone (live in license_check.c).
 */
#define VOS3_HW_FINGERPRINT_BYTES   32

typedef struct vos3_hw_fingerprint {
    uint8_t bytes[VOS3_HW_FINGERPRINT_BYTES];
    uint64_t cpuid_signature;          /* family/model/stepping packed */
    uint8_t  has_tpm_ek;               /* 1 if TPM EK contributed; 0 otherwise */
    uint8_t  has_smbios_uuid;          /* 1 if SMBIOS UUID contributed */
    uint8_t  _pad[6];
} vos3_hw_fingerprint_t;

/* ---- Slot capability — re-export from include/ipc/slots.h for backward
 *      compat. Pro components should #include this for the canonical
 *      definitions; CORE components can either include this or slots.h
 *      directly. The two MUST stay in lockstep. ---- */

/* (do NOT redefine VOS3_CAP_* here — that would silently fork the ABI
 *  if anyone modifies one copy. Include slots.h to access them.) */

#ifdef __cplusplus
}
#endif

#endif /* VOS3_COMMON_TYPES_H */
