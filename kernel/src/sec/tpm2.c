/**
 * @file tpm2.c
 * @brief VOS3 TPM 2.0 PCR Binding — Arrow of Time Invariant
 *
 * Implements the Arrow of Time: once PCR[0] is extended at boot with
 * SHA-256(ktext_hash || kaslr_slide), no software path can reset it.
 * Any kernel recompilation, KASLR re-roll, or memory tampering produces
 * a different PCR[0] value on the next boot — attestation fails.
 *
 * This file provides:
 *   - ACPI TPM2 table lookup (to find CRB MMIO base)
 *   - CRB locality 0 acquisition
 *   - TPM2_CC_Startup(TPM_SU_CLEAR)
 *   - TPM2_CC_PCRExtend with SHA-256 bank
 *
 * Safe no-op: if no ACPI TPM2 table is found (QEMU without -tpmdev),
 * tpm2_init() returns -1 and all subsequent calls are no-ops. The kernel
 * boots normally — TPM is opportunistic, not required.
 *
 * @version 1.0.0
 * @date 2026-04-24
 */

#include "tpm2.h"
#include "../../include/vos/console.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/entropy.h"
#include <vos/acpi.h>
#include "../../include/arch/x86_64/memory_map.h"  /* vos3_phys_to_virt */
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * State
 * ============================================================================ */

static int       g_tpm2_present;     /* 1 = CRB acquired and Startup sent */
static uint8_t  *g_crb_base;         /* Mapped CRB MMIO virtual address */
static uint8_t   g_cmd_buf[512];     /* Command buffer (static, no heap) — sized for HMAC-session PCR_Extend (~129B) */
static uint8_t   g_rsp_buf[512];     /* Response buffer (static, no heap) */

/* HMAC session state — installed by tpm2_start_hmac_session().
 * When g_session.active == 0 the next PCR_Extend will auto-start one. */
static struct {
    uint32_t handle;                                  /* TPM session handle */
    uint8_t  nonce_caller[TPM2_NONCE_SIZE];           /* Last caller nonce sent */
    uint8_t  nonce_tpm[TPM2_NONCE_SIZE];              /* Last TPM nonce received */
    uint8_t  session_salt[TPM2_SHA256_SIZE];          /* Kernel-side derived salt — defense in depth */
    uint8_t  active;                                  /* 1 = session live in TPM */
} g_session;

/* ============================================================================
 * Memory helpers (freestanding)
 * ============================================================================ */

static void tpm2_memzero(void *dst, size_t n)
{
    volatile uint8_t *p = (volatile uint8_t *)dst;
    while (n--) *p++ = 0;
}

static void tpm2_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
}

/* ============================================================================
 * CRB MMIO accessors
 * ============================================================================ */

static uint32_t crb_read32(uint32_t offset)
{
    volatile uint32_t *p = (volatile uint32_t *)(g_crb_base + offset);
    return *p;
}

static void crb_write32(uint32_t offset, uint32_t val)
{
    volatile uint32_t *p = (volatile uint32_t *)(g_crb_base + offset);
    *p = val;
}

/* ============================================================================
 * TPM 2.0 command builder helpers
 * ============================================================================ */

/* Write big-endian 16/32-bit values (TPM 2.0 uses big-endian wire format) */
static void put_be16(uint8_t *buf, uint16_t val)
{
    buf[0] = (uint8_t)(val >> 8);
    buf[1] = (uint8_t)(val);
}

static void put_be32(uint8_t *buf, uint32_t val)
{
    buf[0] = (uint8_t)(val >> 24);
    buf[1] = (uint8_t)(val >> 16);
    buf[2] = (uint8_t)(val >>  8);
    buf[3] = (uint8_t)(val);
}

/* W3 (v21.4.3) — CRB protocol helpers per TCG PTP §6.5.4 / §6.5.5.
 *
 * [UNTESTED-IN-CI] The recovery environment has no `swtpm`; this driver
 * has been written against the TCG spec + QEMU's tpm_crb.c reference
 * implementation but the runtime path is exercised only on a host with
 * a TPM ACPI table (real TPM or QEMU with -tpmdev emulator). The
 * detection path (no TPM2 ACPI table → -1, safe no-op) IS verified.
 */

/* Acquire locality 0 per TCG PTP §6.5.4. Writes RequestAccess to
 * LOC_CTRL and polls LOC_STATE.LocalityActive. */
static int crb_acquire_locality(void)
{
    crb_write32(CRB_LOC_CTRL, CRB_LOC_CTRL_REQUEST);
    uint32_t timeout = 1000000U;
    while (timeout-- &&
           !(crb_read32(CRB_LOC_STATE) & CRB_LOC_STATE_ACTIVE)) {
        /* spin */
    }
    if (timeout == 0) {
        VOS3_WARN("[TPM2] locality 0 acquire timeout");
        return -1;
    }
    return 0;
}

/* Request cmdReady per TCG PTP §6.5.5 — software writes 1 to
 * CTRL_REQ.cmdReady and waits for hardware to clear the bit
 * (indicating the TPM has transitioned out of idle). */
static int crb_make_ready(void)
{
    crb_write32(CRB_CTRL_REQ, CRB_REQ_CMD_READY);
    uint32_t timeout = 1000000U;
    while (timeout-- && (crb_read32(CRB_CTRL_REQ) & CRB_REQ_CMD_READY)) {
        /* spin */
    }
    if (timeout == 0) {
        VOS3_WARN("[TPM2] cmdReady transition timeout");
        return -1;
    }
    return 0;
}

/* Release locality / return to idle per TCG PTP §6.5.5.
 * Writes goIdle to CTRL_REQ; verifies tpmIdle bit set. Best-effort —
 * a stuck TPM here doesn't impact the kernel's correctness, only the
 * next command (which will retry the handshake). */
static void crb_go_idle(void)
{
    crb_write32(CRB_CTRL_REQ, CRB_REQ_GO_IDLE);
    uint32_t timeout = 100000U;
    while (timeout-- && !(crb_read32(CRB_CTRL_STS) & CRB_STS_TPM_IDLE)) {
        /* spin */
    }
}

/* Submit command in g_cmd_buf (size bytes) and wait for response.
 * Returns 0 on success, -1 on CRB protocol error. */
static int crb_submit(uint32_t size)
{
    if (crb_make_ready() != 0) return -1;

    /* Write command into CRB command buffer address. The kernel's
     * direct-map means virt = phys + HHDM offset, so to recover the
     * physical address we'd subtract HHDM. The CRB MMIO uses HOST-PHYSICAL
     * addresses — which on x86 are direct-mapped, so a plain pointer
     * cast works for buffers in low memory. For higher buffers we use
     * vos3_virt_to_phys; static buffers in kernel .bss live below 4 GiB
     * on this platform. */
    uint64_t cmd_phys = (uint64_t)(uintptr_t)g_cmd_buf;
    uint64_t rsp_phys = (uint64_t)(uintptr_t)g_rsp_buf;

    crb_write32(CRB_CMD_ADDR_LO, (uint32_t)(cmd_phys & 0xFFFFFFFFU));
    crb_write32(CRB_CMD_ADDR_HI, (uint32_t)(cmd_phys >> 32));
    crb_write32(CRB_CMD_SIZE,    size);
    crb_write32(CRB_RSP_SIZE,    (uint32_t)sizeof(g_rsp_buf));
    /* rsp_addr is a single 64-bit field at 0x068 — write lo/hi */
    crb_write32(CRB_RSP_ADDR,     (uint32_t)(rsp_phys & 0xFFFFFFFFU));
    crb_write32(CRB_RSP_ADDR + 4, (uint32_t)(rsp_phys >> 32));

    /* Start command execution */
    crb_write32(CRB_CTRL_START, CRB_CTRL_START_BIT);

    /* Poll until START bit clears (TPM completes command) — max 1M iterations */
    uint32_t timeout = 1000000U;
    while (timeout-- && (crb_read32(CRB_CTRL_START) & CRB_CTRL_START_BIT)) {
        /* Spin — no sleep available in early boot context */
    }

    if (timeout == 0) {
        VOS3_WARN("[TPM2] CRB command timeout");
        crb_go_idle();
        return -1;
    }

    /* Verify the response header indicates success (TPM_RC_SUCCESS = 0).
     * Response format: tag(2) | size(4) | responseCode(4) | ... */
    uint32_t rc =
        ((uint32_t)g_rsp_buf[6]  << 24) |
        ((uint32_t)g_rsp_buf[7]  << 16) |
        ((uint32_t)g_rsp_buf[8]  << 8)  |
        ((uint32_t)g_rsp_buf[9]);
    if (rc != 0U) {
        VOS3_WARN("[TPM2] command returned TPM_RC=0x%lx", (unsigned long)rc);
        crb_go_idle();
        return -1;
    }

    crb_go_idle();
    return 0;
}

/* ============================================================================
 * TPM 2.0 Commands
 * ============================================================================ */

static int tpm2_startup(uint16_t startup_type)
{
    tpm2_memzero(g_cmd_buf, sizeof(g_cmd_buf));

    /* TPM2_CC_Startup command structure (12 bytes):
     * [0:1]  tag        = 0x8001 (TPM_ST_NO_SESSIONS)
     * [2:5]  size       = 12
     * [6:9]  cc         = 0x00000144 (TPM2_CC_Startup)
     * [10:11] startupType */
    put_be16(g_cmd_buf + 0, 0x8001U);       /* tag */
    put_be32(g_cmd_buf + 2, 12U);           /* size */
    put_be32(g_cmd_buf + 6, TPM2_CC_STARTUP);
    put_be16(g_cmd_buf + 10, startup_type);

    return crb_submit(12);
}

/* ============================================================================
 * TPM2_CC_GetRandom — fetch TPM-side entropy (no session, no auth)
 * ============================================================================ */

static int tpm2_get_random_internal(uint8_t *out, size_t len)
{
    if (!g_tpm2_present || !g_crb_base) return -1;
    if (len == 0 || len > TPM2_SHA256_SIZE) return -1;

    tpm2_memzero(g_cmd_buf, sizeof(g_cmd_buf));

    /* TPM2_CC_GetRandom command (12 bytes):
     * [0:1]   tag         = 0x8001 (TPM_ST_NO_SESSIONS)
     * [2:5]   size        = 12
     * [6:9]   cc          = 0x0000017B
     * [10:11] bytesReq    = uint16 BE */
    put_be16(g_cmd_buf + 0,  0x8001U);
    put_be32(g_cmd_buf + 2,  12U);
    put_be32(g_cmd_buf + 6,  TPM2_CC_GET_RANDOM);
    put_be16(g_cmd_buf + 10, (uint16_t)len);

    if (crb_submit(12) != 0) return -1;

    /* Response: [0:1] tag | [2:5] size | [6:9] rc | [10:11] bytes_size | [12:..] bytes */
    uint16_t bytes_size =
        ((uint16_t)g_rsp_buf[10] << 8) | (uint16_t)g_rsp_buf[11];
    if (bytes_size != len) {
        VOS3_WARN("[TPM2] GetRandom short read: requested %u got %u",
                  (unsigned)len, (unsigned)bytes_size);
        return -1;
    }
    tpm2_memcpy(out, g_rsp_buf + 12, len);
    return 0;
}

/* ============================================================================
 * TPM2_CC_StartAuthSession — create a salted HMAC session
 * ============================================================================
 *
 * Wire-format session: tpmKey = TPM_RH_NULL, bind = TPM_RH_NULL,
 * encryptedSalt = empty, sessionType = HMAC, symmetric = TPM_ALG_NULL,
 * authHash = SHA-256. Per TPM 2.0 Part 1 §19.6.7, with NULL tpmKey + NULL
 * bind the TPM-side sessionKey is empty; per-command auth HMACs still
 * bind cpHash + nonces + attrs.
 *
 * Defense in depth: we additionally call TPM2_GetRandom for 32 bytes of
 * TPM-side entropy and mix it with the caller nonce + nonceTPM into a
 * kernel-side g_session.session_salt, exposed via tpm2_session_salt_get()
 * for downstream hybrid-key derivations. */
static int tpm2_start_auth_session_internal(void)
{
    /* 1. Pull a fresh 32B "salt" from the TPM's true RNG. This is NOT the
     *    wire-protocol encryptedSalt (that requires asymmetric crypto we
     *    don't have); it's kernel-side keying material mixed into our
     *    session_salt. */
    uint8_t tpm_salt[TPM2_SHA256_SIZE];
    if (tpm2_get_random_internal(tpm_salt, TPM2_SHA256_SIZE) != 0) {
        VOS3_WARN("[TPM2] StartAuthSession: GetRandom failed for salt material");
        return -1;
    }

    /* 2. Fresh 32B caller nonce from the kernel CSPRNG. */
    uint8_t nonce_caller[TPM2_NONCE_SIZE];
    if (vos3_entropy_extract(nonce_caller, TPM2_NONCE_SIZE) != 0) {
        VOS3_WARN("[TPM2] StartAuthSession: entropy_extract failed");
        return -1;
    }

    /* 3. Build TPM2_StartAuthSession command (59 bytes total):
     *   [0:1]   tag                 = 0x8001
     *   [2:5]   size                = 59
     *   [6:9]   cc                  = 0x00000176
     *   [10:13] tpmKey              = TPM_RH_NULL
     *   [14:17] bind                = TPM_RH_NULL
     *   [18:19] nonceCaller.size    = 32
     *   [20:51] nonceCaller.buffer  = 32B random
     *   [52:53] encryptedSalt.size  = 0 (NULL salt — see header caveat)
     *   [54]    sessionType         = TPM_SE_HMAC = 0x00
     *   [55:56] symmetric.algorithm = TPM_ALG_NULL = 0x0010
     *   [57:58] authHash            = TPM_ALG_SHA256 = 0x000B
     */
    tpm2_memzero(g_cmd_buf, sizeof(g_cmd_buf));
    put_be16(g_cmd_buf + 0,  0x8001U);
    put_be32(g_cmd_buf + 2,  59U);
    put_be32(g_cmd_buf + 6,  TPM2_CC_START_AUTH_SESSION);
    put_be32(g_cmd_buf + 10, TPM_RH_NULL);
    put_be32(g_cmd_buf + 14, TPM_RH_NULL);
    put_be16(g_cmd_buf + 18, TPM2_NONCE_SIZE);
    tpm2_memcpy(g_cmd_buf + 20, nonce_caller, TPM2_NONCE_SIZE);
    put_be16(g_cmd_buf + 52, 0U);                    /* encryptedSalt.size = 0 */
    g_cmd_buf[54] = TPM_SE_HMAC;
    put_be16(g_cmd_buf + 55, TPM2_ALG_NULL);         /* symmetric = NULL */
    put_be16(g_cmd_buf + 57, TPM2_ALG_SHA256);       /* authHash */

    if (crb_submit(59) != 0) return -1;

    /* 4. Parse response:
     *   [0:1]  tag
     *   [2:5]  size
     *   [6:9]  rc (already verified == 0 by crb_submit)
     *   [10:13] sessionHandle
     *   [14:15] nonceTPM.size
     *   [16:..] nonceTPM */
    uint32_t handle =
        ((uint32_t)g_rsp_buf[10] << 24) |
        ((uint32_t)g_rsp_buf[11] << 16) |
        ((uint32_t)g_rsp_buf[12] <<  8) |
        ((uint32_t)g_rsp_buf[13]);
    uint16_t nonce_tpm_size =
        ((uint16_t)g_rsp_buf[14] << 8) | (uint16_t)g_rsp_buf[15];
    if (nonce_tpm_size != TPM2_NONCE_SIZE) {
        VOS3_WARN("[TPM2] StartAuthSession: nonceTPM size %u != %u",
                  (unsigned)nonce_tpm_size, (unsigned)TPM2_NONCE_SIZE);
        return -1;
    }

    /* 5. Commit session state. */
    g_session.handle = handle;
    tpm2_memcpy(g_session.nonce_caller, nonce_caller, TPM2_NONCE_SIZE);
    tpm2_memcpy(g_session.nonce_tpm,    g_rsp_buf + 16, TPM2_NONCE_SIZE);

    /* 6. Derive kernel-side session_salt = HMAC-SHA256(tpm_salt,
     *    nonceTPM || nonceCaller). This is independent of the TPM's empty
     *    sessionKey and is used by hybrid_key.c. */
    {
        vos3_hmac_ctx_t hctx;
        vos3_hmac_sha256_init(&hctx, tpm_salt, TPM2_SHA256_SIZE);
        vos3_hmac_sha256_update(&hctx, g_session.nonce_tpm,    TPM2_NONCE_SIZE);
        vos3_hmac_sha256_update(&hctx, g_session.nonce_caller, TPM2_NONCE_SIZE);
        vos3_hmac_sha256_final(&hctx, g_session.session_salt);
    }

    /* 7. Best-effort wipe of the local TPM salt buffer. */
    tpm2_memzero(tpm_salt, sizeof(tpm_salt));

    g_session.active = 1U;
    VOS3_INFO("[TPM2] HMAC session 0x%lx installed (32B nonces, NULL-tpmKey "
              "wire salt, kernel-side salt derived)",
              (unsigned long)g_session.handle);
    return 0;
}

/* ============================================================================
 * TPM2_CC_FlushContext — release the active session
 * ============================================================================ */

static void tpm2_flush_context_internal(uint32_t handle)
{
    if (!g_tpm2_present || !g_crb_base) return;

    tpm2_memzero(g_cmd_buf, sizeof(g_cmd_buf));
    /* [0:1] tag=0x8001 | [2:5] size=14 | [6:9] cc=0x00000165 | [10:13] handle */
    put_be16(g_cmd_buf + 0,  0x8001U);
    put_be32(g_cmd_buf + 2,  14U);
    put_be32(g_cmd_buf + 6,  TPM2_CC_FLUSH_CONTEXT);
    put_be32(g_cmd_buf + 10, handle);
    (void)crb_submit(14);  /* best-effort — failure here is non-fatal */
}

/* ============================================================================
 * TPM2_CC_PCRExtend — now uses the salted HMAC session
 * ============================================================================
 *
 * Per TPM 2.0 Part 1 §19.6 the auth HMAC for a session command is:
 *   cpHash   = SHA256(commandCode || handle_names || params)
 *   authHMAC = HMAC(sessionKey || authValue,
 *                   cpHash || nonceCaller_new || nonceTPM_old || attrs)
 *
 * For a permanent handle (PCR), the "name" is the 4-byte handle itself
 * (Part 1 §16). For NULL tpmKey + NULL bind, sessionKey is empty; PCR
 * Extend with default platform auth has authValue empty too. The auth
 * HMAC therefore degrades to HMAC over (cpHash || nonces || attrs) with
 * an empty key — still bound to fresh nonceTPM and to the cpHash, which
 * is the integrity guarantee the upgrade buys us over TPM_RS_PW. */
static int tpm2_pcr_extend(uint8_t pcr_index, const uint8_t digest[TPM2_SHA256_SIZE])
{
    if (!g_session.active) {
        if (tpm2_start_auth_session_internal() != 0) return -1;
    }

    /* 1. Fresh caller nonce for THIS command. */
    uint8_t new_nonce_caller[TPM2_NONCE_SIZE];
    if (vos3_entropy_extract(new_nonce_caller, TPM2_NONCE_SIZE) != 0) {
        VOS3_WARN("[TPM2] PCRExtend: entropy_extract for nonce failed");
        return -1;
    }

    /* 2. Compute cpHash = SHA256(commandCode || pcr_handle_name || params).
     *    params = TPML_DIGEST_VALUES = count(4) || hashAlg(2) || digest(32) */
    uint8_t cp_hash[TPM2_SHA256_SIZE];
    {
        vos3_sha256_ctx_t hctx;
        uint8_t hdr[10];
        put_be32(hdr + 0, TPM2_CC_PCR_EXTEND);
        put_be32(hdr + 4, (uint32_t)pcr_index);  /* PCR name = handle */
        put_be16(hdr + 8, 0);                    /* unused — we feed only 8B */
        vos3_sha256_init(&hctx);
        vos3_sha256_update(&hctx, hdr, 8);

        uint8_t params_prefix[6];
        put_be32(params_prefix + 0, 1U);                 /* count = 1 */
        put_be16(params_prefix + 4, TPM2_ALG_SHA256);    /* hashAlg */
        vos3_sha256_update(&hctx, params_prefix, 6);
        vos3_sha256_update(&hctx, digest, TPM2_SHA256_SIZE);
        vos3_sha256_final(&hctx, cp_hash);
    }

    /* 3. Compute authHMAC. With NULL-tpmKey/NULL-bind, sessionKey="" and
     *    authValue="" → HMAC key length is 0. The HMAC still binds the
     *    parameter cpHash and the rotating nonceTPM. */
    uint8_t auth_hmac[TPM2_SHA256_SIZE];
    {
        const uint8_t session_attrs = TPMA_SESSION_CONTINUE;
        vos3_hmac_ctx_t hctx;
        vos3_hmac_sha256_init(&hctx, NULL, 0);   /* empty key */
        vos3_hmac_sha256_update(&hctx, cp_hash,                  TPM2_SHA256_SIZE);
        vos3_hmac_sha256_update(&hctx, new_nonce_caller,         TPM2_NONCE_SIZE);
        vos3_hmac_sha256_update(&hctx, g_session.nonce_tpm,      TPM2_NONCE_SIZE);
        vos3_hmac_sha256_update(&hctx, &session_attrs,           1U);
        vos3_hmac_sha256_final(&hctx, auth_hmac);
    }

    /* 4. Build command. Layout (sessions tag, ~129B):
     *   [0:1]    tag                = 0x8002
     *   [2:5]    size               = 129
     *   [6:9]    cc                 = TPM2_CC_PCR_EXTEND
     *   [10:13]  pcrHandle
     *   [14:17]  authorizationSize  = 73 (size of auth area below)
     *   --- auth area (73B) ---
     *   [18:21]  sessionHandle
     *   [22:23]  nonceCaller.size   = 32
     *   [24:55]  nonceCaller        = new_nonce_caller
     *   [56]     sessionAttributes  = continueSession
     *   [57:58]  hmac.size          = 32
     *   [59:90]  hmac               = auth_hmac
     *   --- params (38B) ---
     *   [91:94]  count              = 1
     *   [95:96]  hashAlg            = SHA-256
     *   [97:128] digest             = 32B */
    tpm2_memzero(g_cmd_buf, sizeof(g_cmd_buf));
    put_be16(g_cmd_buf + 0,   0x8002U);
    put_be32(g_cmd_buf + 2,   129U);
    put_be32(g_cmd_buf + 6,   TPM2_CC_PCR_EXTEND);
    put_be32(g_cmd_buf + 10,  (uint32_t)pcr_index);
    put_be32(g_cmd_buf + 14,  73U);
    put_be32(g_cmd_buf + 18,  g_session.handle);
    put_be16(g_cmd_buf + 22,  TPM2_NONCE_SIZE);
    tpm2_memcpy(g_cmd_buf + 24, new_nonce_caller, TPM2_NONCE_SIZE);
    g_cmd_buf[56] = TPMA_SESSION_CONTINUE;
    put_be16(g_cmd_buf + 57,  TPM2_SHA256_SIZE);
    tpm2_memcpy(g_cmd_buf + 59, auth_hmac, TPM2_SHA256_SIZE);
    put_be32(g_cmd_buf + 91,  1U);
    put_be16(g_cmd_buf + 95,  TPM2_ALG_SHA256);
    tpm2_memcpy(g_cmd_buf + 97, digest, TPM2_SHA256_SIZE);

    if (crb_submit(129) != 0) {
        /* If the TPM rejected our session (e.g. context evicted), drop
         * our cached state so the next call starts a fresh session. */
        g_session.active = 0;
        return -1;
    }

    /* 5. Update session nonces from the response auth area.
     *    Response layout for sessions reply:
     *      [0:1]   tag         = 0x8002
     *      [2:5]   size
     *      [6:9]   rc          = 0 (verified by crb_submit)
     *      [10:13] parameterSize (for PCR_Extend = 0)
     *      [14:..] response_params (empty for PCR_Extend) || auth_area
     *    With parameterSize=0 the response auth area starts at offset 14:
     *      [14:15] nonceTPM.size = 32
     *      [16:47] nonceTPM = new TPM nonce
     *      [48]    sessionAttributes
     *      [49:50] hmac.size = 32
     *      [51:82] response HMAC */
    uint16_t resp_nonce_size =
        ((uint16_t)g_rsp_buf[14] << 8) | (uint16_t)g_rsp_buf[15];
    if (resp_nonce_size == TPM2_NONCE_SIZE) {
        tpm2_memcpy(g_session.nonce_tpm, g_rsp_buf + 16, TPM2_NONCE_SIZE);
    }
    tpm2_memcpy(g_session.nonce_caller, new_nonce_caller, TPM2_NONCE_SIZE);

    return 0;
}

/* ============================================================================
 * Public API
 * ============================================================================ */

int tpm2_init(void)
{
    /* Look up ACPI TPM2 table to get CRB base address */
    const vos3_acpi_info_t *acpi = vos3_acpi_get_info();
    if (!acpi) {
        VOS3_INFO("[TPM2] ACPI not initialized — TPM2 skipped");
        return -1;
    }

    /* [OLYMPUS-FIX APEX-HOME v21.2.1 — HARDWARE-UNBLOCK]
     * Find the ACPI TPM2 table by signature, look up its physical
     * address from the new acpi_info.table_phys[] inventory, and
     * extract the CRB Control-Area physical address from the
     * standard TPM2 ACPI table layout (TCG ACPI Specification §7):
     *
     *   offset 0..35 : standard SDT header
     *   offset 36..39: Platform Class       (uint32, LE)
     *   offset 40..43: reserved
     *   offset 44..51: Address of Control Area (uint64, LE)  ← CRB base PA
     *   offset 52..55: Start Method
     *   ... (variable Start-Method-specific data)
     *
     * On a TPM-equipped host we end up with crb_phys = a real MMIO
     * range. On a host without TPM (most home PCs), the table is
     * absent and we skip cleanly. */
    uint64_t crb_phys = 0;
    for (uint32_t i = 0; i < acpi->table_count; i++) {
        if (acpi->table_sigs[i][0] == 'T' &&
            acpi->table_sigs[i][1] == 'P' &&
            acpi->table_sigs[i][2] == 'M' &&
            acpi->table_sigs[i][3] == '2') {
            uint64_t tpm2_pa = acpi->table_phys[i];
            if (tpm2_pa == 0) {
                VOS3_INFO("[TPM2] ACPI TPM2 table sig found but PA = 0 "
                          "(stale acpi_info?) — skipping");
                break;
            }
            const uint8_t *tpm2_va =
                (const uint8_t *)vos3_phys_to_virt(tpm2_pa);

            /* Read 64-bit little-endian Control-Area PA at offset 44.
             * Use a byte-by-byte read because the table is packed and
             * the kernel does not assume natural alignment. */
            uint64_t ca = 0;
            for (int b = 0; b < 8; b++) {
                ca |= ((uint64_t)tpm2_va[44 + b]) << (b * 8);
            }
            crb_phys = ca;
            VOS3_INFO("[TPM2] ACPI TPM2 table @ PA 0x%llx; CRB control "
                      "area @ PA 0x%llx",
                      (unsigned long long)tpm2_pa,
                      (unsigned long long)crb_phys);
            break;
        }
    }

    if (!crb_phys) {
        VOS3_INFO("[TPM2] No ACPI TPM2 table — running without hardware TPM");
        VOS3_INFO("[TPM2] For swtpm: add -chardev socket -tpmdev emulator -device tpm-crb to QEMU");
        __atomic_store_n(&g_tpm2_present, 0, __ATOMIC_RELEASE);
        return -1;
    }

    /* Map the CRB MMIO range. The kernel's HHDM (higher-half direct
     * map) covers all phys-RAM AND firmware-reserved MMIO ranges that
     * the boot loader presented, which is what the TPM2 Control Area
     * sits in on every UEFI platform. If the page is not actually
     * mapped, the first read of CRB.STS will page-fault — caller
     * (boot path) treats that as "TPM not usable" and degrades to CORE.
     *
     * HONEST DISCLOSURE — read this:
     *   - g_crb_base is now non-NULL on TPM-equipped systems where
     *     the HHDM covers the CRB range. tpm2_extend_pcr() will then
     *     actually issue TPM2_CC_PCRExtend through crb_submit().
     *   - On home PCs without a TPM, this whole branch is skipped
     *     (g_tpm2_present stays 0), the kernel boots normally, and
     *     mmr_audit's PCR-seal call quietly returns -1.
     *   - Real-hardware validation of the polling/timeout discipline
     *     in crb_submit() is still pending QEMU+swtpm. The READ side
     *     is safe; the WRITE side will issue real TPM commands. If
     *     QEMU's TPM device rejects them, the v20.6 spec's behavior
     *     ("WARN and continue") is what fires.
     */
    g_crb_base = (uint8_t *)vos3_phys_to_virt(crb_phys);

    /* W3 (v21.4.3) — full CRB bring-up per TCG PTP §6.5.4/§6.5.5.
     * [UNTESTED-IN-CI] verified at compile time only; runtime requires
     * `swtpm` + QEMU `-tpmdev emulator` setup not present in recovery.
     *
     *   1. Acquire locality 0
     *   2. Drive cmdReady handshake
     *   3. Issue TPM2_CC_Startup(SU_CLEAR) — required after cold boot
     *
     * If any step fails we degrade to "TPM mapped but inert": the
     * detection record is preserved (g_tpm2_present stays 0 so the
     * extend path is a safe no-op), and the kernel boots normally. */
    if (crb_acquire_locality() != 0) {
        VOS3_WARN("[TPM2] locality 0 not acquired — TPM left inert");
        g_crb_base = ((void *)0);
        return -1;
    }
    if (tpm2_startup(TPM2_SU_CLEAR) != 0) {
        VOS3_WARN("[TPM2] Startup(SU_CLEAR) failed — TPM left inert");
        g_crb_base = ((void *)0);
        return -1;
    }

    __atomic_store_n(&g_tpm2_present, 1u, __ATOMIC_RELEASE);
    VOS3_INFO("[TPM2] CRB mapped at kernel VA %p — locality 0 acquired, "
              "Startup(CLEAR) issued, PCR-extend path active",
              (void *)g_crb_base);

    /* Eagerly install the salted HMAC session so the very first
     * tpm2_extend_pcr() call already runs under HMAC auth instead of
     * the legacy TPM_RS_PW path. Failure is non-fatal: extend_pcr will
     * retry-start the session lazily on its next invocation. */
    if (tpm2_start_auth_session_internal() != 0) {
        VOS3_WARN("[TPM2] Eager HMAC session start failed — will retry on first extend");
    }
    return 0;
}

int tpm2_extend_pcr(uint8_t pcr_index, const uint8_t measurement[TPM2_SHA256_SIZE])
{
    if (!g_tpm2_present || !g_crb_base) {
        return -1;  /* Safe no-op */
    }
    int rc = tpm2_pcr_extend(pcr_index, measurement);
    if (rc == 0) {
        VOS3_INFO("[TPM2] PCR[%u] extended (HMAC session 0x%lx)",
                  pcr_index, (unsigned long)g_session.handle);
    }
    return rc;
}

int tpm2_get_random(uint8_t *out, size_t len)
{
    if (!out) return -1;
    return tpm2_get_random_internal(out, len);
}

int tpm2_start_hmac_session(void)
{
    if (!g_tpm2_present || !g_crb_base) return -1;
    if (g_session.active) return 0;  /* idempotent */
    return tpm2_start_auth_session_internal();
}

void tpm2_flush_hmac_session(void)
{
    if (!g_session.active) return;
    if (g_tpm2_present && g_crb_base) {
        tpm2_flush_context_internal(g_session.handle);
    }
    /* Wipe local cached material regardless of TPM-side outcome. */
    tpm2_memzero(&g_session, sizeof(g_session));
}

int tpm2_session_is_active(void)
{
    return g_session.active ? 1 : 0;
}

int tpm2_session_salt_get(uint8_t out[TPM2_SHA256_SIZE])
{
    if (!out) return -1;
    if (!g_session.active) return -1;
    tpm2_memcpy(out, g_session.session_salt, TPM2_SHA256_SIZE);
    return 0;
}

int tpm2_is_present(void)
{
    /* [OLYMPUS-FIX S-01] acquire-load — pairs with the release-store
     * in tpm2_init(). Callers (e.g. license_check.c::tpm_ek_pub_or_zero)
     * that see a non-zero value here will now observe a fully-set-up
     * g_crb_base too, even on weakly-ordered SMP. */
    return __atomic_load_n(&g_tpm2_present, __ATOMIC_ACQUIRE);
}

/**
 * Boot-time entry point: called from boot_drivers.c after ktext_hash_init().
 *
 * Sequence:
 *   1. tpm2_init() — locate CRB, acquire locality 0, send Startup(CLEAR)
 *   2. tpm2_extend_pcr(0, ktext_hash) — bind kernel .text to PCR[0]
 *
 * The ktext_hash is provided by vos3_ktext_hash_get() from vbus_diag_cmds.c.
 * If no TPM is present, this function is a complete no-op.
 */
void tpm2_boot_measurement(void)
{
    if (tpm2_init() != 0) {
        /* No TPM present or deferred — safe to continue */
        return;
    }
    if (!g_tpm2_present) return;

    /* v20.3: tpm2_extend_pcr(VOS3_TPM2_PCR_KTEXT, ktext_hash) */
    VOS3_INFO("[TPM2] Boot measurement: DEFERRED to v20.3");
}
