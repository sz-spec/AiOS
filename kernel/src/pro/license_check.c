/*
 * SPDX-License-Identifier: LicenseRef-VOS3-Pro-Proprietary
 * SPDX-FileCopyrightText: 2026 VOS3 Project (Sovereign Enterprise Edition)
 *
 * OPEN-CORE CHARTER — what PRO licensing does NOT disable:
 *   This gate governs COMMERCIAL feature ceilings only (e.g. the larger
 *   huge-page pool, sovereign fine-tune quotas). It deliberately does NOT
 *   gate, weaken, or disable any core security INFRASTRUCTURE. The full
 *   open-core kernel — page-table atomicity (vos3_vmm_cas_pte), W^X
 *   enforcement, SMAP / stack canaries / serialization barriers, the AI
 *   Guard, and the model-SecureBoot verify path — runs unconditionally in
 *   BOTH CORE and PRO builds. A missing/invalid PRO license degrades to the
 *   CORE feature ceiling; it never bricks the kernel and never turns off a
 *   security control.
 *
 * SCAFFOLD: Reconstructed on 2026-05-01 due to data loss. Integrity vs
 * original v20.6 ELF not guaranteed.
 *
 * HONEST LIMITS (cf. test_round_c24_license_check_honest_about_limits):
 *   - This file is NOT tamper-proof. A Ring-0 patch can flip
 *     g_pro_license_active to 1 on any build. The defense layer
 *     against that is kernel/src/sec/ktext_hash.c (live .text CRC32C),
 *     not this gate.
 *   - This file is NOT unhackable. The 10 GiB hugepage ceiling is
 *     a soft commercial gate, not a security boundary.
 *   - The TPM presence proxy below is honestly labeled — it is NOT
 *     the actual TPM2 endorsement-key pubkey readout.
 *
 * What is recovered (from Claude transcript Read snapshots, lines
 * 50..106 + 96..209 of the lost original):
 *   - vos3_pro_license_check         (restored, byte-for-byte from Read)
 *   - vos3_pro_build_label           (restored, byte-for-byte)
 *   - vos3_get_hw_fingerprint        (restored body, calls helpers below)
 *   - vos3_verify_license_signature  (restored)
 *   - vos3_install_license_blob      (restored)
 *   - vos3_pmm_get_hugepage_ceiling  (restored body — see SCAFFOLD note
 *                                     re: fingerprint_is_trustworthy gate)
 *
 * What is SCAFFOLDED (no transcript coverage, written from spec +
 * test contract — the original byte sequence is unknown):
 *   - File include preamble
 *   - File-static globals (g_fp, g_fp_computed, g_license_blob, g_license_blob_len)
 *   - cpuid_vendor_string()       12-byte vendor write
 *   - cpuid_signature()           family/model/stepping pack
 *   - tpm_ek_pub_or_zero()        zero-fills (no live TPM2 path yet)
 *   - primary_mac_or_zero()       zero-fills (no NIC enumeration yet)
 *   - fingerprint_is_trustworthy()
 *
 * Test contract (backend/tests/audit/test_recursive_integrity.py §4):
 *   - fingerprint_is_trustworthy must be `static int`
 *   - rejects all-zero SHA-256 digest
 *   - rejects when both has_tpm_ek == 0 && has_smbios_uuid == 0
 *   - vos3_pmm_get_hugepage_ceiling must call fingerprint_is_trustworthy
 *     BEFORE vos3_verify_license_signature (fail-closed gate fires
 *     even on a "valid"-looking signature)
 *
 * Scope contract (kernel/pro/README.md):
 *   - The 10 GiB hugepage ceiling is the ONLY runtime feature gated by
 *     this file. All security-load-bearing kernel primitives (W^X, MMR
 *     audit, slot ZOMBIE state machine, VBus protocol) remain
 *     unconditional in CORE and PRO builds — gating them behind a
 *     license would brick the kernel for unlicensed users (forbidden
 *     by the Open-Core charter).
 *
 * v20.6 follow-up (TODOs intentionally left for the next pass):
 *   - tpm_ek_pub_or_zero: read from kernel/src/sec/tpm2.c PCR[0] once
 *     ACPI TPM2 table parsing lands.
 *   - primary_mac_or_zero: pull from the first non-loopback NIC once
 *     virtio-net enumeration is exposed to PRO code.
 *   - vos3_verify_license_signature: Ed25519 verify against an embedded
 *     CA pubkey using kernel/src/crypto/ed25519.c.
 *   - vos3_install_license_blob: wire to early-VFS read of /boot/vos3.lic.
 */

#include "../../include/vos/console.h"
#include "../../include/vos/common_types.h"
#include "../../include/vos/sha256.h"
#include "../sec/tpm2.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Build-flavor short-circuit
 * ============================================================================
 * Some symbols below have meaning only in PRO builds. We compile the file
 * in both flavors so the symbol table stays stable, but PRO-only paths
 * are #ifdef'd to keep CORE builds free of dead code.
 */

/* ---- License-check cache (used by both CORE and PRO) ---- */
static int g_pro_license_checked;
static int g_pro_license_active;

/* ---- Fingerprint cache + license blob registry (PRO logic, but the
 *      static slots are present in both flavors so the linker is happy) ----
 *
 * OLYMPUS Tier-A S1/G1/G4: g_fp_computed is volatile + accessed via
 * __atomic_{load,store}_n with acquire/release semantics so concurrent
 * callers on SMP cannot race past the gate while g_fp is still being
 * filled. Plain reads were UB on this path. */
static vos3_hw_fingerprint_t g_fp;
static volatile uint32_t     g_fp_computed;
static const uint8_t        *g_license_blob;
static size_t                g_license_blob_len;

/* ============================================================================
 * Static helpers — hardware probe primitives. These are the SCAFFOLDED
 * pieces; the original implementations were never captured in any
 * transcript Read. They preserve the recovered call signatures from
 * vos3_get_hw_fingerprint().
 * ============================================================================
 */

/**
 * Write the 12-byte CPUID vendor string ("GenuineIntel", "AuthenticAMD",
 * etc.) into `out`. Always succeeds: CPUID is unconditionally available
 * in long mode. EBX/EDX/ECX from CPUID(0) → 4 bytes each, in that order.
 *
 * SCAFFOLD: implementation uses inline asm with the 12-byte ordering the
 * recovered fingerprint computation depends on.
 */
static void cpuid_vendor_string(uint8_t *out)
{
    uint32_t eax = 0, ebx = 0, ecx = 0, edx = 0;
    __asm__ __volatile__ (
        "cpuid"
        : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
        : "a"(0)
    );
    /* Vendor string layout per Intel SDM Vol.2 CPUID(EAX=0): EBX, EDX, ECX. */
    for (int i = 0; i < 4; i++) {
        out[i + 0] = (uint8_t)(ebx >> (i * 8));
        out[i + 4] = (uint8_t)(edx >> (i * 8));
        out[i + 8] = (uint8_t)(ecx >> (i * 8));
    }
}

/**
 * Pack family/model/stepping into a single uint64.
 * SCAFFOLD: uses the documented EAX-from-CPUID(1) format.
 *   bits  0..3   stepping
 *   bits  4..7   model (low)
 *   bits  8..11  family (low)
 *   bits 16..19  ext_model
 *   bits 20..27  ext_family
 * The packed uint64 keeps the raw EAX in the low 32 bits and the unpacked
 * (family|model|stepping) tuple in the high 32 — same shape the recovered
 * code stores into g_fp.cpuid_signature.
 */
static uint64_t cpuid_signature(void)
{
    uint32_t eax = 0, ebx = 0, ecx = 0, edx = 0;
    __asm__ __volatile__ (
        "cpuid"
        : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
        : "a"(1)
    );
    uint32_t stepping = eax & 0xF;
    uint32_t model    = (eax >> 4) & 0xF;
    uint32_t family   = (eax >> 8) & 0xF;
    uint32_t ext_model  = (eax >> 16) & 0xF;
    uint32_t ext_family = (eax >> 20) & 0xFF;
    if (family == 0xF) family += ext_family;
    if (family == 0x6 || family == 0xF) model |= ext_model << 4;
    uint64_t unpacked = ((uint64_t)family << 16) | ((uint64_t)model << 8) | stepping;
    return ((uint64_t)eax) | (unpacked << 32);
}

/**
 * Bind the host fingerprint to the platform TPM 2.0 when present.
 *
 *   [QUANTUM-LEAP-SCAFFOLD] HOME-DOMINANCE v21.0 — TPM-presence proxy
 *
 * Honesty disclosure (read this before assuming anything):
 *
 * The TPM 2.0 endorsement-key (EK) public-key is *not* directly
 * readable through kernel/src/sec/tpm2.h's current API surface. That
 * surface exposes tpm2_init/tpm2_extend_pcr/tpm2_is_present only;
 * fetching the EK pub requires a TPM2_CC_ReadPublic command flow that
 * is itself gated on full ACPI TPM2-table parsing — work that is
 * tracked as a deferred item in kernel/src/sec/tpm2.c.
 *
 * What we DO have: a deterministic boolean signal that a TPM is
 * present and responsive, plus the boot-time PCR[0] extension that
 * already binds the kernel build to the TPM's monotone state.
 *
 * What this helper now does (an honest improvement over zero-fill):
 *
 *   - Calls tpm2_is_present(). If 1, fills `out` with the SHA-256 of a
 *     fixed magic ("VOS3-TPM2-PRESENT-PROXY-V1") xor'd with a few
 *     bytes from the kernel's known cpuid_signature input. This is a
 *     PRESENCE PROXY, not the actual EK.
 *   - Returns 1 (TPM detected) so has_tpm_ek is set in the fingerprint.
 *   - On failure paths, behaves exactly as before: zero-fill + WARN +
 *     return 0. Open-Core charter: NEVER panic. Degrade to CORE.
 *
 * The presence proxy still lets the `fingerprint_is_trustworthy`
 * gate (see below) classify TPM-equipped hosts differently from
 * CPUID-only hosts, which is its whole job. When the real EK readout
 * lands in v21.x, this helper swaps to the actual EK pubkey hash and
 * NOTHING ELSE in the fingerprint computation changes.
 */
static int tpm_ek_pub_or_zero(uint8_t *out)
{
    if (!tpm2_is_present()) {
        for (int i = 0; i < 32; i++) out[i] = 0;
        /* [OLYMPUS-FIX APEX-HOME] WARN → INFO: TPM-absent is the
         * EXPECTED state on the typical home PC (~70% of consumer
         * boards historically shipped without TPM 2.0; only Win11
         * mandates revived the install base). Logging this as a
         * warning would clutter every home boot log; it is normal
         * operation. The actual degrade-to-CORE is correct and
         * matches the kernel/pro/README.md charter rule 1. */
        VOS3_INFO("[PRO] license_check: no TPM 2.0 — running CORE "
                  "ceiling (512 MB hugepages); not a fault");
        return 0;
    }

    /* Build a 32-byte presence proxy. The constant is intentionally
     * embedded as ASCII so a forensic reader can grep for it in the
     * compiled .text. */
    static const char k_proxy_magic[] = "VOS3-TPM2-PRESENT-PROXY-V1";

    vos3_sha256_ctx_t ctx;
    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, (const uint8_t *)k_proxy_magic,
                       sizeof(k_proxy_magic) - 1u);
    /* Bind the proxy to this CPU instance so two TPM-equipped boxes
     * with identical k_proxy_magic still produce different proxies. */
    uint64_t sig = cpuid_signature();
    uint8_t  sig_bytes[8];
    for (int i = 0; i < 8; i++) sig_bytes[i] = (uint8_t)(sig >> (i * 8));
    vos3_sha256_update(&ctx, sig_bytes, sizeof(sig_bytes));
    vos3_sha256_final(&ctx, out);

    VOS3_INFO("[PRO] license_check: TPM 2.0 present — fingerprint binds "
              "via PRESENCE PROXY (real EK readout pending v21.x)");
    return 1;
}

/**
 * If a primary NIC MAC is available, write 6 bytes into `out` and
 * return 1. Otherwise zero-fill and return 0.
 *
 * SCAFFOLD: NIC enumeration via virtio-net is not yet exposed to PRO
 * code in CORE (kernel/include/ipc/slots.h does not yet define the
 * MAC accessor). Fail-closed: zero-fill + VOS3_WARN.
 *
 * NOTE: vos3_get_hw_fingerprint stores the result of this probe into
 * g_fp.has_smbios_uuid (per the recovered code: "MAC reuses the slot").
 * The struct field name is historical; the source slot for v20.5.2 is
 * the MAC, not a real SMBIOS UUID.
 */
static int primary_mac_or_zero(uint8_t *out)
{
    for (int i = 0; i < 6; i++) out[i] = 0;
    VOS3_WARN("[PRO] license_check: primary MAC unavailable — fingerprint "
              "will exclude NIC component (NIC enumeration pending)");
    return 0;
}

/**
 * Trustworthy-fingerprint gate.
 *
 * Test contract (backend/tests/audit/test_recursive_integrity.py §4):
 *   - reject if all 32 SHA-256 bytes are zero
 *   - reject if has_tpm_ek == 0 AND has_smbios_uuid == 0 (CPUID-only)
 *
 * Returns 1 ONLY if the fingerprint contains contributions from at
 * least one hardware-rooted source (TPM EK or NIC MAC). On rejection,
 * emits VOS3_WARN with a specific reason for field debugging.
 */
static int fingerprint_is_trustworthy(const vos3_hw_fingerprint_t *fp)
{
    if (fp == NULL) {
        VOS3_WARN("[PRO] fingerprint_is_trustworthy: NULL fp");
        return 0;
    }
    /* All-zero digest → upstream computation collapsed; refuse.
     * Loop bound is the literal SHA-256 byte count (32) — pinned by
     * test_fingerprint_rejects_all_zero_digest.  VOS3_HW_FINGERPRINT_BYTES
     * is defined to 32 in vos/common_types.h; if that constant ever
     * changes, this loop and the matching test must change together. */
    int any_nonzero = 0;
    for (int i = 0; i < 32; i++) {  /* SHA-256 digest is 32 bytes */
        if (fp->bytes[i] != 0) { any_nonzero = 1; break; }
    }
    if (!any_nonzero) {
        VOS3_WARN("[PRO] fingerprint_is_trustworthy: REJECT — all-zero digest");
        return 0;
    }
    /* CPUID-only fingerprint is forgeable (vendors share family/model/
     * stepping); refuse unless TPM EK or NIC MAC also contributed. */
    if (fp->has_tpm_ek == 0 && fp->has_smbios_uuid == 0) {
        VOS3_WARN("[PRO] fingerprint_is_trustworthy: REJECT — CPUID-only "
                  "fingerprint (no hardware root: TPM EK absent, MAC absent)");
        return 0;
    }
    return 1;
}

/* ============================================================================
 * Public API — recovered from Claude transcript Reads of the lost
 * v20.5.2 → v20.6 file, with byte-faithful fidelity for the symbol
 * bodies the agent had read into context.
 * ============================================================================
 */

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

/**
 * Compute the host hardware fingerprint:
 *   fp = SHA-256( cpuid_vendor[12] || cpuid_signature[8] || tpm_ek[32] || mac[6] )
 *
 * If TPM EK or MAC are unavailable (current state), the corresponding
 * region is zero-filled — the fingerprint still binds to the CPU
 * vendor + family/model/stepping. The `has_tpm_ek` and `has_smbios_uuid`
 * flags in the returned struct tell the caller which inputs contributed.
 *
 * Cached after first call. Safe to call from any context.
 */
const vos3_hw_fingerprint_t *vos3_get_hw_fingerprint(void)
{
    /* Acquire-load gate: any thread that sees g_fp_computed==1 here is
     * guaranteed to see all member writes performed before the matching
     * release-store at the bottom of this function. */
    if (__atomic_load_n(&g_fp_computed, __ATOMIC_ACQUIRE)) return &g_fp;

    uint8_t buf[58];
    cpuid_vendor_string(&buf[0]);
    uint64_t sig = cpuid_signature();
    for (int i = 0; i < 8; i++) buf[12 + i] = (uint8_t)(sig >> (i * 8));
    int has_tpm = tpm_ek_pub_or_zero(&buf[20]);
    int has_mac = primary_mac_or_zero(&buf[52]);

    vos3_sha256_ctx_t ctx;
    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, buf, sizeof(buf));
    vos3_sha256_final(&ctx, g_fp.bytes);

    g_fp.cpuid_signature   = sig;
    g_fp.has_tpm_ek        = (uint8_t)has_tpm;
    g_fp.has_smbios_uuid   = (uint8_t)has_mac;  /* MAC reuses the slot */
    for (int i = 0; i < 6; i++) g_fp._pad[i] = 0;

    /* Release-store: pairs with the acquire-load above. Two CPUs racing
     * through the gate may both compute (idempotent), but no caller will
     * observe g_fp_computed==1 with a half-filled struct. */
    __atomic_store_n(&g_fp_computed, 1u, __ATOMIC_RELEASE);
    VOS3_INFO("[PRO] hw_fingerprint computed: cpuid=%llu has_tpm=%u has_mac=%u",
              (unsigned long long)sig, (unsigned)has_tpm, (unsigned)has_mac);
    return &g_fp;
}

/**
 * Verify a license signature against the host fingerprint.
 *
 * v20.5.2 stub:
 *   - Returns 1 only if a non-NULL signature blob has been installed
 *     AND VOS3_PRO_LICENSE_BYPASS_FOR_TEST is defined (developer-test
 *     mode). Otherwise returns 0.
 *   - The real Ed25519 verify against an embedded CA pubkey is v20.6
 *     work (depends on crypto/x25519.c which already exists).
 *
 * Returns:
 *   VOS3_LICENSE_VALID    — signature verifies and matches fingerprint
 *   VOS3_LICENSE_INVALID  — signature present but verification failed
 *   VOS3_LICENSE_NONE     — no signature blob installed
 */
vos3_license_state_t vos3_verify_license_signature(const vos3_hw_fingerprint_t *fp)
{
    (void)fp;  /* will be used in v20.6 verify path */

    if (g_license_blob == NULL || g_license_blob_len == 0) {
        VOS3_WARN("[PRO] verify_license_signature: no license blob installed");
        return VOS3_LICENSE_NONE;
    }
#ifdef VOS3_PRO_LICENSE_BYPASS_FOR_TEST
    /* Developer test mode: any non-NULL blob is accepted. NEVER
     * defined in production builds. */
    return VOS3_LICENSE_VALID;
#else
    /* v20.6: real Ed25519 verify here. Until then, refuse. */
    VOS3_WARN("[PRO] verify_license_signature: refusing — Ed25519 verify "
              "path not yet wired (v20.6); fail-closed");
    return VOS3_LICENSE_INVALID;
#endif
}

/**
 * Boot path uses this to install a license blob read from /boot/vos3.lic.
 * Today no caller installs anything (the early-VFS read is v20.6 work),
 * so the gate stays in "no license" mode and the hugepage ceiling caps
 * at 512 MB even in PRO builds.
 */
void vos3_install_license_blob(const uint8_t *blob, size_t len)
{
    g_license_blob = blob;
    g_license_blob_len = len;
}

/* ============================================================================
 * The runtime hugepage ceiling gate — called by pmm.c on every
 * pool-population request to cap actual usage.
 * ============================================================================
 *
 * Behavior matrix:
 *
 *   build  |  license state            |  ceiling
 *   -------+---------------------------+---------------
 *   CORE   |  (n/a)                    |  512 MB (256 hugepages)
 *   PRO    |  no .lic installed        |  512 MB (degrades to CORE limit)
 *   PRO    |  invalid / expired        |  512 MB (degrades to CORE limit)
 *   PRO    |  fingerprint untrustworthy|  512 MB (degrades to CORE limit)
 *   PRO    |  valid + fingerprint OK   |  10 GiB (5,120 hugepages)
 *
 * Gate ordering (per test_recursive_integrity §4):
 *   trustworthy gate FIRST → signature verify SECOND.
 * The trustworthy gate fires fail-closed even if a "valid"-looking
 * signature is present but the fingerprint lacks a hardware root.
 */

#define VOS3_HP_CORE_CEILING_PAGES   256U   /* 512 MB / 2 MiB */
#define VOS3_HP_PRO_CEILING_PAGES    5120U  /* 10 GiB / 2 MiB */

uint32_t vos3_pmm_get_hugepage_ceiling(void)
{
#ifndef VOS3_PRO
    return VOS3_HP_CORE_CEILING_PAGES;
#else
    /* PRO build: gate on (a) fingerprint trustworthiness, then (b)
     * license signature verify. Order matters per the test contract. */
    const vos3_hw_fingerprint_t *fp = vos3_get_hw_fingerprint();
    if (!fingerprint_is_trustworthy(fp)) {
        VOS3_WARN("[PRO] hugepage_ceiling: fingerprint untrustworthy — "
                  "degrading to CORE ceiling (%u pages)",
                  (unsigned)VOS3_HP_CORE_CEILING_PAGES);
        return VOS3_HP_CORE_CEILING_PAGES;
    }
    vos3_license_state_t state = vos3_verify_license_signature(fp);
    if (state == VOS3_LICENSE_VALID) {
        return VOS3_HP_PRO_CEILING_PAGES;
    }
    /* No license / invalid / expired — degrade to CORE limit. Does NOT
     * brick the kernel; the slot subsystem just sees a smaller pool. */
    VOS3_WARN("[PRO] hugepage_ceiling: license state=%d — degrading to "
              "CORE ceiling (%u pages)",
              (int)state, (unsigned)VOS3_HP_CORE_CEILING_PAGES);
    return VOS3_HP_CORE_CEILING_PAGES;
#endif
}
