/*
 * VOS3 Sovereign Enterprise Edition — Proprietary Components
 * ============================================================
 *
 * SPDX-License-Identifier: LicenseRef-VOS3-Pro-Proprietary
 * SPDX-FileCopyrightText: 2026 VOS3 Project (Sovereign Enterprise Edition)
 *
 * NOTE: this header marker is the PROPOSED licensing scheme for VOS3
 * Pro components. The actual repo-wide LICENSE remains MIT pending
 * legal review of the Open-Core split. Until that review completes,
 * this file is governed by the existing MIT LICENSE at the repo root.
 *
 * --------------------------------------------------------------
 * @file license_check.c
 * @brief VOS3 Pro — Boot-time license signature verification (STUB).
 *
 * SCOPE OF THIS FILE (HONEST):
 *
 * What this file DOES today:
 *   - Provides the API surface vos3_pro_license_check() that boot code
 *     (kernel/src/boot/boot_drivers.c, future hookup) can call to
 *     determine whether the running build has access to PRO features.
 *   - Returns a single source-of-truth boolean for "is this a PRO
 *     instance" so the rest of the kernel can branch defensively.
 *   - Logs the result via VOS3_INFO so operators can see in the boot
 *     log whether PRO was activated.
 *
 * What this file does NOT yet do (deliberate scope-down for v20.5.x):
 *   - Read a `vos3.lic` file from the boot partition (filesystem
 *     access during early boot is non-trivial; deferred to v20.6 once
 *     boot-time VFS access is stabilized).
 *   - Verify a cryptographic signature on the license blob (the
 *     signing CA + verifier public key are not yet provisioned; the
 *     CA setup is a legal/operational task, not engineering).
 *   - Disable any CORE primitives. Per the open-core charter:
 *     `vos3_vmm_cas_pte`, the W^X PTE enforcement, the slot ZOMBIE
 *     state machine, the MMR audit chain, and the VBus protocol are
 *     INFRASTRUCTURE. Gating them behind a license would brick the
 *     kernel for unlicensed users — that is the OPPOSITE of open-
 *     core. The only feature actually gated by VOS3_PRO is the
 *     10 GiB hugepage ceiling (see kernel/src/mm/pmm.c).
 *
 * Verification at boot:
 *   - VOS3_PRO defined at compile time: returns 1 (PRO build).
 *   - VOS3_PRO undefined: returns 0 (CORE build).
 *
 * v20.6 roadmap:
 *   - Read /boot/vos3.lic via early VFS
 *   - Ed25519 signature verify against an embedded VOS3 CA pubkey
 *   - Validate license expiry against TPM-rooted boot timestamp
 */

#include "../include/vos/console.h"
#include <stdint.h>

/* Set to 1 when vos3_pro_license_check() has run. Result cached in
 * g_pro_license_active so subsequent callers do not re-probe the
 * (eventual) signature path. */
static int g_pro_license_checked;
static int g_pro_license_active;

/**
 * Read the running build's license posture.
 *
 * Returns:
 *   1 — PRO build with valid license (today: just VOS3_PRO defined)
 *   0 — CORE build OR PRO build with invalid/missing license (today:
 *       just VOS3_PRO undefined)
 *
 * Safe to call multiple times. First call logs the result; subsequent
 * calls return the cached value.
 */
int vos3_pro_license_check(void)
{
    if (g_pro_license_checked) {
        return g_pro_license_active;
    }

#ifdef VOS3_PRO
    /* PRO build at compile time. The signature-verification path is
     * v20.6 work; today the compile-time flag IS the gate. */
    g_pro_license_active = 1;
    VOS3_INFO("[PRO] license_check: PRO build active (compile-time flag)");
#else
    g_pro_license_active = 0;
    VOS3_INFO("[CORE] license_check: CORE build (PRO features disabled — "
              "10 GB hugepage scaling capped at 512 MB)");
#endif

    g_pro_license_checked = 1;
    return g_pro_license_active;
}

/**
 * Convenience: returns the human-readable build label for log lines
 * and VBus banners. Always safe; never allocates.
 */
const char *vos3_pro_build_label(void)
{
#ifdef VOS3_PRO
    return "VOS3 Pro (Sovereign Enterprise)";
#else
    return "VOS3 Core (Open Source / MIT)";
#endif
}
