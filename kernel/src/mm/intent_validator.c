/**
 * @file kernel/src/mm/intent_validator.c
 * @brief v20.5-SINGULARITY — In-kernel IntentManifest validator.
 *
 * Honest framing
 * ==============
 *
 * The v20.5 brief calls this a "TDX Trusted Applet." That phrasing
 * overstates what TDX actually offers — TDX runs whole Trust Domains
 * (TDs), not isolated applets within a TD. There is no production
 * primitive in 2026-Q2 for "load this small piece of code into a
 * sub-domain inside the TDX Module."
 *
 * What we DO ship is the architecturally-correct equivalent: the
 * VOS-Cyber kernel itself runs *inside* the TD. Moving validation
 * logic from Python (which sits outside the TD) into this C file
 * (which runs inside the TD as part of the kernel image hashed into
 * RTMR[0]) achieves the security property the brief is reaching for —
 * **the validator is part of the measured boot chain and cannot be
 * tampered with without invalidating the platform attestation**.
 *
 * Calling this a "TDX applet" in marketing material is fine; calling
 * it that in a technical due-diligence document would fail.
 *
 * What this validator does
 * ========================
 *
 * Parses an IntentManifest from a userspace buffer and enforces the
 * structural invariants that the Python schema previously checked,
 * but at the kernel/TD trust boundary so a compromised Python runtime
 * cannot smuggle a malformed manifest into AI slot activation.
 *
 *   1. Bounded-length acceptance — total bytes ≤
 *      VOS3_INTENT_MAX_BYTES (16 KiB; matches IntentManifest envelope
 *      constants in vos/intent.h).
 *   2. Magic-string anchor — first 8 bytes must be the VOS3 manifest
 *      magic so a wild buffer pointer is rejected before any further
 *      parsing.
 *   3. Field-count caps — authorised_models ≤ VOS3_INTENT_MAX_MODELS,
 *      authorised_tools ≤ VOS3_INTENT_MAX_TOOLS, etc. Prevents a
 *      manifest from containing a quadratic-blow-up array.
 *   4. UTF-8 validity check (pure ASCII subset for now — the v20.5
 *      manifest schema does not yet need code-points > 0x7E).
 *   5. SHA-384 of the bytes — emitted to the caller so the
 *      measurement-ring extend can use the same digest the validator
 *      saw, eliminating a TOCTOU window between hash-and-extend.
 *
 * Returns a structured error code on rejection so the syscall layer
 * can surface a precise reason to userspace WITHOUT echoing untrusted
 * manifest bytes back in the error path.
 *
 * Trust-base note (TCB compression)
 * =================================
 *
 * Before v20.5: a hostile Python runtime that had bypassed the auth
 * gate could submit a malformed manifest to vos3_tee_slot_activate_bound
 * and rely on Python's own schema check having been trustworthy at
 * the time of submission — i.e., the validation was *outside the
 * measured trust base*.
 *
 * After v20.5: the validator below is in the kernel image whose
 * SHA-384 sits in RTMR[0]. Tampering with it changes RTMR[0], which
 * any attestation verifier sees as a different platform measurement.
 * The validator's behaviour is therefore part of the cryptographic
 * surface an auditor reviews, not part of the unverified userspace
 * substrate.
 */

#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/tee.h"

/* Forward decl from kernel/src/crypto/sha384.c — already linked in
 * the kernel image and used by vos3_tee_model_measure(). */
extern void vos3_sha384(const void *data, size_t len, uint8_t out[48]);

/* ============================================================================
 * Schema constants
 *
 * These caps are deliberately tight; tighter than the v20.4 Python schema
 * because the kernel-side validator is the conservative gate and any
 * legitimate manifest comfortably fits these bounds.
 * ============================================================================ */

#define VOS3_INTENT_MAX_BYTES        (16U * 1024U)   /* 16 KiB envelope */
#define VOS3_INTENT_MAX_MODELS       8U
#define VOS3_INTENT_MAX_TOOLS        32U
#define VOS3_INTENT_MAX_ROLES        8U

/* 8-byte magic — "VOS3IM01" — anchors a wild pointer. */
static const uint8_t INTENT_MAGIC[8] = {
    'V', 'O', 'S', '3', 'I', 'M', '0', '1'
};

/* ============================================================================
 * Return codes (negative = rejection; zero = accept)
 * ============================================================================ */

#define VOS3_INTENT_OK                       0
#define VOS3_INTENT_E_NULL                  -1   /* NULL buffer */
#define VOS3_INTENT_E_TOO_SHORT             -2   /* < magic+header */
#define VOS3_INTENT_E_TOO_LONG              -3   /* > VOS3_INTENT_MAX_BYTES */
#define VOS3_INTENT_E_BAD_MAGIC             -4
#define VOS3_INTENT_E_BAD_VERSION           -5
#define VOS3_INTENT_E_BAD_UTF8              -6   /* non-ASCII outside whitelist */
#define VOS3_INTENT_E_TOO_MANY_MODELS       -7
#define VOS3_INTENT_E_TOO_MANY_TOOLS        -8
#define VOS3_INTENT_E_TOO_MANY_ROLES        -9
#define VOS3_INTENT_E_FIELD_TRUNCATED      -10
#define VOS3_INTENT_E_NULL_DIGEST_OUT      -11

/* ============================================================================
 * Manifest envelope (binary form the kernel accepts)
 *
 * The Python side serialises its YAML/JSON manifest into this fixed
 * binary envelope before the syscall — keeping the kernel parser
 * trivial. JSON/YAML parsing in kernel space is a much bigger TCB
 * surface than this fixed-width envelope and is deliberately avoided.
 *
 * Layout (little-endian; total ≤ 16 KiB):
 *
 *   offset  size   field
 *   ------  ----   -----
 *      0     8     MAGIC ("VOS3IM01")
 *      8     2     version (uint16; v1 = 1, v2 = 2)
 *     10     2     flags   (uint16, reserved; must be 0)
 *     12     2     model_count   (uint16, ≤ VOS3_INTENT_MAX_MODELS)
 *     14     2     tool_count    (uint16, ≤ VOS3_INTENT_MAX_TOOLS)
 *     16     2     role_count    (uint16, ≤ VOS3_INTENT_MAX_ROLES)
 *     18     2     v1: reserved (must be 0); v2: min_confidence_score (0..1000)
 *     20    var    body — concatenated NUL-terminated ASCII strings,
 *                   model_count + tool_count + role_count of them, in
 *                   that order. Each string ≤ 256 bytes incl. NUL.
 *
 * The body strings are NOT inspected for semantic content here — that's
 * a policy-engine concern. The kernel validator's role is structural:
 * does this byte buffer fit the envelope, do the counts agree, is each
 * string properly terminated and ASCII-clean.
 * ============================================================================ */

#define VOS3_INTENT_HDR_SIZE     20U
#define VOS3_INTENT_STR_MAX_LEN  256U

/* ============================================================================
 * Helpers
 * ============================================================================ */

static int is_ascii_clean(uint8_t b)
{
    /* Printable ASCII + tab/LF/CR. We deliberately reject control
     * characters and high-bit bytes — manifest IDs are tokens, not
     * free-form Unicode. */
    if (b == 0x09U || b == 0x0AU || b == 0x0DU) return 1;
    if (b >= 0x20U && b <= 0x7EU) return 1;
    return 0;
}

/* Validate one NUL-terminated ASCII string starting at offset *off.
 * On success advances *off past the NUL. On failure returns negative. */
static int validate_string(const uint8_t *buf, size_t len, size_t *off)
{
    if (*off >= len) {
        return VOS3_INTENT_E_FIELD_TRUNCATED;
    }
    size_t start = *off;
    while (*off < len && (*off - start) < VOS3_INTENT_STR_MAX_LEN) {
        uint8_t b = buf[*off];
        if (b == 0U) {
            (*off)++;
            return VOS3_INTENT_OK;
        }
        if (!is_ascii_clean(b)) {
            return VOS3_INTENT_E_BAD_UTF8;
        }
        (*off)++;
    }
    return VOS3_INTENT_E_FIELD_TRUNCATED;
}

/* Read a little-endian uint16 from buf[off]; assumes off+2 <= len. */
static uint16_t read_le16(const uint8_t *buf, size_t off)
{
    return (uint16_t)((uint16_t)buf[off] | ((uint16_t)buf[off + 1] << 8));
}

/* ============================================================================
 * Public entry point
 *
 * Validates ``buf[0..len)`` as an IntentManifest envelope and writes
 * SHA-384(buf) into ``digest_out48``. Returns 0 on accept, negative
 * VOS3_INTENT_E_* on reject.
 *
 * The caller (typically vos3_tee_slot_activate_bound dispatcher) MUST
 * NOT echo any portion of ``buf`` back in error paths — error messages
 * surface only the return code. The whole point of this function is
 * to be the only kernel-trusted reader of these bytes.
 * ============================================================================ */

int vos3_intent_validate(const uint8_t *buf,
                         size_t          len,
                         uint8_t         digest_out48[48])
{
    if (buf == NULL) {
        return VOS3_INTENT_E_NULL;
    }
    if (digest_out48 == NULL) {
        return VOS3_INTENT_E_NULL_DIGEST_OUT;
    }
    if (len < VOS3_INTENT_HDR_SIZE) {
        return VOS3_INTENT_E_TOO_SHORT;
    }
    if (len > (size_t)VOS3_INTENT_MAX_BYTES) {
        return VOS3_INTENT_E_TOO_LONG;
    }

    /* (1) Magic check — anchors a wild pointer before any deeper parse. */
    for (uint32_t i = 0U; i < 8U; i++) {
        if (buf[i] != INTENT_MAGIC[i]) {
            return VOS3_INTENT_E_BAD_MAGIC;
        }
    }

    /* (2) Header fields. */
    const uint16_t version    = read_le16(buf,  8U);
    const uint16_t flags      = read_le16(buf, 10U);
    const uint16_t n_models   = read_le16(buf, 12U);
    const uint16_t n_tools    = read_le16(buf, 14U);
    const uint16_t n_roles    = read_le16(buf, 16U);
    const uint16_t off18      = read_le16(buf, 18U);

    /* Stage 10.2 — accept both schema versions:
     *
     *   v1 (legacy): offset 18 is "reserved" and MUST be zero.
     *   v2: offset 18 is min_confidence_score; range 0..1000.
     *
     * flags must remain zero in both schemas (no flag bits defined yet).
     */
    if (version != VOS3_INTENT_VERSION_V1 &&
        version != VOS3_INTENT_VERSION_V2) {
        return VOS3_INTENT_E_BAD_VERSION;
    }
    if (flags != 0U) {
        return VOS3_INTENT_E_BAD_VERSION;
    }
    if (version == VOS3_INTENT_VERSION_V1) {
        /* Legacy: reserved-must-be-zero invariant. */
        if (off18 != 0U) {
            return VOS3_INTENT_E_BAD_VERSION;
        }
    } else {
        /* v2: range-check min_confidence_score. */
        if ((uint32_t)off18 > VOS3_INTENT_MAX_CONFIDENCE_SCORE) {
            return VOS3_INTENT_E_BAD_CONFIDENCE;
        }
    }
    if ((uint32_t)n_models > VOS3_INTENT_MAX_MODELS) {
        return VOS3_INTENT_E_TOO_MANY_MODELS;
    }
    if ((uint32_t)n_tools > VOS3_INTENT_MAX_TOOLS) {
        return VOS3_INTENT_E_TOO_MANY_TOOLS;
    }
    if ((uint32_t)n_roles > VOS3_INTENT_MAX_ROLES) {
        return VOS3_INTENT_E_TOO_MANY_ROLES;
    }

    /* (3) Body strings. Each is a NUL-terminated ASCII token of
     * length 1..VOS3_INTENT_STR_MAX_LEN. Empty strings are accepted
     * (NUL-only) so a manifest can declare zero of a given category
     * without omitting the count. */
    size_t off = VOS3_INTENT_HDR_SIZE;
    const uint32_t total_strings =
        (uint32_t)n_models + (uint32_t)n_tools + (uint32_t)n_roles;
    for (uint32_t i = 0U; i < total_strings; i++) {
        const int rc = validate_string(buf, len, &off);
        if (rc != VOS3_INTENT_OK) {
            return rc;
        }
    }

    /* (4) SHA-384 of the validated bytes. The caller will use this
     * digest for the RTMR[2] (or composed-commitment) extend, so
     * the digest the kernel signs is over the same bytes the kernel
     * validated — no TOCTOU between validation and measurement. */
    vos3_sha384(buf, len, digest_out48);

    VOS3_DEBUG("[INTENT] validated v=%u len=%llu min_conf=%u "
               "n_models=%u n_tools=%u n_roles=%u digest=%02x%02x%02x%02x…",
               (unsigned)version,
               (unsigned long long)len,
               (unsigned)(version == VOS3_INTENT_VERSION_V2 ? off18 : 0U),
               (unsigned)n_models, (unsigned)n_tools, (unsigned)n_roles,
               digest_out48[0], digest_out48[1],
               digest_out48[2], digest_out48[3]);

    return VOS3_INTENT_OK;
}
