/**
 * @file model_download.c
 * @brief VOS3 Warp-Drive Stream Injection — HTTPS Model Download to ivshmem
 *
 * @details Downloads AI model files (GGUF, SafeTensors, ONNX) over HTTPS
 *          directly into ivshmem Warp-Drive shared memory zones, eliminating
 *          the host-to-guest copy bottleneck.
 *
 *          Data flow:
 *            HTTPS (TLS 1.3)  ->  HTTP body callback  ->  ivshmem zone memcpy
 *                                    |
 *                              SHA-256 incremental hash
 *                                    |
 *                              vos3_cache_flush() for host visibility
 *
 *          Security:
 *            1. Slot 0 (coordinator) permanently rejected — kernel-only zone
 *            2. SHA-256 integrity verification with constant-time comparison
 *            3. Overflow detection before every write (zone_offset + len check)
 *            4. Cache flush after download ensures ivshmem coherence (share=on)
 *            5. All error paths log and return negative error codes
 *
 *          The streaming callback writes directly into the ivshmem zone,
 *          avoiding a heap staging buffer entirely. Progress is reported
 *          every 1 MB via VOS3_INFO.
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.3 — Native HTTPS Model Acquisition (Warp-Drive Stream Injection)
 * @note Freestanding: no libc, no <stdio.h>
 */

#include "../../include/vos/http.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/ivshmem.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/crypto.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief Default progress report interval in bytes (1 MB) */
#define MODEL_DL_DEFAULT_REPORT_INTERVAL    (1U * 1024U * 1024U)

/** @brief Minimum valid slot ID for downloads (slot 0 is coordinator) */
#define MODEL_DL_SLOT_MIN                   1U

/** @brief Maximum valid slot ID for downloads */
#define MODEL_DL_SLOT_MAX                   3U

/** @brief Tag for log messages */
#define MODEL_DL_TAG                        "MODEL_DL"

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

/** @brief Invalid argument (NULL url, bad slot_id) */
#define MODEL_DL_ERR_INVAL                  (-1)

/** @brief ivshmem not available or zone_base is NULL */
#define MODEL_DL_ERR_NO_IVSHMEM            (-2)

/** @brief HTTP request failed (network, TLS, DNS) */
#define MODEL_DL_ERR_HTTP                   (-3)

/** @brief HTTP status was not 200 or 206 */
#define MODEL_DL_ERR_HTTP_STATUS            (-4)

/** @brief Zone capacity exceeded during download */
#define MODEL_DL_ERR_OVERFLOW               (-5)

/** @brief Callback signaled an error */
#define MODEL_DL_ERR_CALLBACK               (-6)

/** @brief SHA-256 mismatch after download */
#define MODEL_DL_ERR_SHA256_MISMATCH        (-7)

/* ============================================================================
 * DOWNLOAD CONTEXT STRUCTURE
 * ============================================================================ */

/**
 * @brief Context passed through the HTTP streaming body callback
 *
 * Tracks the ivshmem destination zone, running SHA-256 hash, progress
 * counters, and error flags. Allocated on stack by vos3_model_download().
 */
typedef struct vos3_model_download_ctx {
    /** @brief Warp-Drive ivshmem slot (1-3, never 0) */
    uint8_t             slot_id;

    /** @brief Mapped ivshmem zone base address (kernel virtual) */
    uint8_t            *zone_base;

    /** @brief Zone capacity in bytes (VOS3_WARP_ZONE_SIZE = 16 MB) */
    size_t              zone_size;

    /** @brief Current write offset within the zone */
    size_t              zone_offset;

    /** @brief Incremental SHA-256 context hashing all received bytes */
    vos3_sha256_ctx_t   sha256_ctx;

    /** @brief Expected total download size from Content-Length (-1 unknown) */
    int64_t             total_expected;

    /** @brief Total bytes received so far */
    uint64_t            total_received;

    /** @brief Set to 1 if a write would exceed zone capacity */
    uint8_t             overflow;

    /** @brief Non-zero if a non-overflow error occurred */
    uint8_t             error;

    /* --- Progress reporting --- */

    /** @brief total_received at the time of the last progress log */
    uint64_t            last_report_bytes;

    /** @brief Bytes between progress log lines (default 1 MB) */
    uint32_t            report_interval;
} vos3_model_download_ctx_t;

/* ============================================================================
 * HELPERS: FORMAT UTILITIES (freestanding — no libc)
 * ============================================================================ */

/** @brief Hex digit lookup table */
static const char s_hex_digits[] = "0123456789abcdef";

/**
 * @brief Write a uint64_t as decimal into a buffer
 *
 * @param[out] buf   Destination (must have room for up to 20 digits + NUL)
 * @param[in]  val   Value to format
 * @return Number of characters written (excluding NUL)
 */
static uint32_t u64_to_dec(char *buf, uint64_t val)
{
    char     tmp[21];
    uint32_t len = 0U;

    if (val == 0ULL) {
        buf[0] = '0';
        buf[1] = '\0';
        return 1U;
    }

    while (val > 0ULL && len < 20U) {
        tmp[len++] = (char)('0' + (val % 10ULL));
        val /= 10ULL;
    }

    /* Reverse into output */
    for (uint32_t i = 0U; i < len; i++) {
        buf[i] = tmp[len - 1U - i];
    }
    buf[len] = '\0';
    return len;
}

/**
 * @brief Append a C string into buf at *pos, respecting buf_len
 *
 * @param[out]    buf      Destination buffer
 * @param[in,out] pos      Current position (advanced on write)
 * @param[in]     buf_len  Total buffer capacity
 * @param[in]     src      NUL-terminated source string
 */
static void buf_append(char *buf, size_t *pos, size_t buf_len, const char *src)
{
    while (*src != '\0' && *pos + 1U < buf_len) {
        buf[(*pos)++] = *src++;
    }
}

/**
 * @brief Convert a 32-byte SHA-256 digest to a 64-character hex string
 *
 * @param[in]  digest  32-byte SHA-256 hash
 * @param[out] hex     Output buffer (must be at least 65 bytes for NUL)
 */
static void sha256_to_hex(const uint8_t digest[32], char hex[65])
{
    uint32_t i;

    for (i = 0U; i < 32U; i++) {
        hex[i * 2U]       = s_hex_digits[(digest[i] >> 4U) & 0x0FU];
        hex[(i * 2U) + 1] = s_hex_digits[digest[i] & 0x0FU];
    }
    hex[64] = '\0';
}

/* ============================================================================
 * HELPER: CONSTANT-TIME SHA-256 COMPARISON
 * ============================================================================ */

/**
 * @brief Compare two 32-byte SHA-256 digests in constant time
 *
 * Uses a volatile accumulator to prevent the compiler from short-circuiting
 * the comparison loop. This prevents timing side-channels that could reveal
 * how many bytes of the expected hash match.
 *
 * @param[in] a  First 32-byte digest
 * @param[in] b  Second 32-byte digest
 * @return 0 if equal, non-zero if different
 */
static int sha256_ct_equal(const uint8_t a[32], const uint8_t b[32])
{
    volatile uint8_t diff = 0U;
    uint32_t i;

    for (i = 0U; i < 32U; i++) {
        diff |= (a[i] ^ b[i]);
    }

    return (diff == 0U) ? 0 : 1;
}

/* ============================================================================
 * STREAMING BODY CALLBACK
 * ============================================================================ */

/**
 * @brief HTTP body callback that streams data directly into ivshmem zone
 *
 * Called by vos3_https_get() for each chunk of the response body.
 * Writes data into the ivshmem Warp-Drive zone at the current offset,
 * updates the running SHA-256 hash, and logs progress periodically.
 *
 * @param[in] data       Pointer to received chunk data
 * @param[in] len        Length of this chunk in bytes
 * @param[in] total      Total bytes received so far (including this chunk)
 * @param[in] expected   Expected total size from Content-Length (-1 if unknown)
 * @param[in] user_ctx   Pointer to vos3_model_download_ctx_t
 * @return 0 to continue downloading, -1 to abort
 */
static int model_download_body_cb(const uint8_t *data, size_t len,
                                  uint64_t total, int64_t expected,
                                  void *user_ctx)
{
    vos3_model_download_ctx_t *ctx = (vos3_model_download_ctx_t *)user_ctx;

    if (ctx == NULL || data == NULL || len == 0U) {
        return 0;   /* Nothing to do — not an error */
    }

    /* ----------------------------------------------------------------
     * 1. Overflow guard: check before writing
     * ---------------------------------------------------------------- */
    /* R4 (v36.1): Pre-addition bounds check prevents integer wrap-around.
     * If len alone exceeds zone_size, the (zone_offset + len) addition
     * could wrap past SIZE_MAX on 64-bit, bypassing the > comparison.
     * Check len independently first, then check the sum. */
    if (len > ctx->zone_size ||
        (ctx->zone_offset + len) > ctx->zone_size) {
        ctx->overflow = 1U;
        VOS3_ERROR("[%s] Zone overflow: offset=%zu + chunk=%zu > capacity=%zu (slot %u)",
                   MODEL_DL_TAG, ctx->zone_offset, len, ctx->zone_size,
                   (unsigned)ctx->slot_id);
        return -1;  /* Abort download */
    }

    /* ----------------------------------------------------------------
     * 2. Copy chunk directly into ivshmem zone (zero-copy to shared mem)
     * ---------------------------------------------------------------- */
    memcpy(ctx->zone_base + ctx->zone_offset, data, len);

    /* ----------------------------------------------------------------
     * 3. Update running SHA-256 hash
     * ---------------------------------------------------------------- */
    vos3_sha256_update(&ctx->sha256_ctx, data, len);

    /* ----------------------------------------------------------------
     * 4. Advance offset and counters
     * ---------------------------------------------------------------- */
    ctx->zone_offset    += len;
    ctx->total_received  = total;
    ctx->total_expected  = expected;

    /* ----------------------------------------------------------------
     * 5. Progress logging (every report_interval bytes)
     * ---------------------------------------------------------------- */
    if ((ctx->total_received - ctx->last_report_bytes) >= ctx->report_interval) {
        ctx->last_report_bytes = ctx->total_received;

        if (expected > 0) {
            uint32_t pct = (uint32_t)((ctx->total_received * 100ULL) /
                                       (uint64_t)expected);
            VOS3_INFO("[%s] Slot %u: %llu / %lld bytes (%u%%)",
                      MODEL_DL_TAG, (unsigned)ctx->slot_id,
                      (unsigned long long)ctx->total_received,
                      (long long)expected, pct);
        } else {
            VOS3_INFO("[%s] Slot %u: %llu bytes received (size unknown)",
                      MODEL_DL_TAG, (unsigned)ctx->slot_id,
                      (unsigned long long)ctx->total_received);
        }
    }

    return 0;   /* Continue downloading */
}

/* ============================================================================
 * PUBLIC API: vos3_model_download()
 * ============================================================================ */

/**
 * @brief Download a model file via HTTPS directly into Warp-Drive ivshmem zone
 *
 * Performs a streaming HTTPS GET, writing each received chunk into the
 * specified ivshmem zone. After the download completes, the SHA-256 hash
 * of the received data is computed and (optionally) compared against an
 * expected digest in constant time.
 *
 * The ivshmem zone is flushed from CPU cache after download so that the
 * QEMU host process can observe the data via shared mapping (share=on).
 *
 * @param[in]  url              HTTPS URL of the model file
 *                              (e.g., "https://huggingface.co/.../model.gguf")
 * @param[in]  slot_id          Warp-Drive ivshmem slot (1-3).
 *                              Slot 0 is the coordinator zone and is rejected.
 * @param[in]  expected_sha256  Expected SHA-256 digest (32 bytes).
 *                              Pass NULL to skip integrity verification.
 * @param[out] out_size         If non-NULL, receives the total bytes downloaded.
 *
 * @return  0                            on success (SHA-256 verified if provided)
 * @return  MODEL_DL_ERR_INVAL          invalid arguments (NULL url, bad slot)
 * @return  MODEL_DL_ERR_NO_IVSHMEM     ivshmem device not available
 * @return  MODEL_DL_ERR_HTTP           HTTPS request failed
 * @return  MODEL_DL_ERR_HTTP_STATUS    non-200/206 HTTP status
 * @return  MODEL_DL_ERR_OVERFLOW       model exceeded zone capacity (16 MB)
 * @return  MODEL_DL_ERR_CALLBACK       body callback error
 * @return  MODEL_DL_ERR_SHA256_MISMATCH SHA-256 digest does not match
 */
int vos3_model_download(const char *url, uint8_t slot_id,
                        const uint8_t expected_sha256[32],
                        size_t *out_size)
{
    vos3_model_download_ctx_t   dl_ctx;
    vos3_http_response_t        response;
    uint8_t                     computed_sha256[VOS3_SHA256_DIGEST_SIZE];
    char                        hex_str[65];
    int                         ret;

    /* ----------------------------------------------------------------
     * 1. Parameter validation
     * ---------------------------------------------------------------- */
    if (url == NULL) {
        VOS3_ERROR("[%s] NULL url", MODEL_DL_TAG);
        return MODEL_DL_ERR_INVAL;
    }

    if (slot_id < MODEL_DL_SLOT_MIN || slot_id > MODEL_DL_SLOT_MAX) {
        VOS3_ERROR("[%s] Invalid slot_id %u (must be %u-%u, slot 0 is coordinator)",
                   MODEL_DL_TAG, (unsigned)slot_id,
                   MODEL_DL_SLOT_MIN, MODEL_DL_SLOT_MAX);
        return MODEL_DL_ERR_INVAL;
    }

    /* ----------------------------------------------------------------
     * 2. Verify ivshmem is available
     * ---------------------------------------------------------------- */
    if (!vos3_ivshmem_available()) {
        VOS3_ERROR("[%s] ivshmem device not available — cannot download to Warp zone",
                   MODEL_DL_TAG);
        return MODEL_DL_ERR_NO_IVSHMEM;
    }

    /* ----------------------------------------------------------------
     * 3. Obtain zone base (kernel-internal, no ownership check for DL)
     * ---------------------------------------------------------------- */
    void *zone = vos3_ivshmem_zone_base_unchecked(slot_id);
    if (zone == NULL) {
        VOS3_ERROR("[%s] Failed to get zone base for slot %u",
                   MODEL_DL_TAG, (unsigned)slot_id);
        return MODEL_DL_ERR_NO_IVSHMEM;
    }

    /* ----------------------------------------------------------------
     * 4. Initialize download context
     * ---------------------------------------------------------------- */
    memset(&dl_ctx, 0, sizeof(dl_ctx));
    dl_ctx.slot_id          = slot_id;
    dl_ctx.zone_base        = (uint8_t *)zone;
    dl_ctx.zone_size        = VOS3_WARP_ZONE_SIZE;
    dl_ctx.zone_offset      = 0U;
    dl_ctx.total_expected    = -1;
    dl_ctx.total_received    = 0ULL;
    dl_ctx.overflow          = 0U;
    dl_ctx.error             = 0U;
    dl_ctx.last_report_bytes = 0ULL;
    dl_ctx.report_interval   = MODEL_DL_DEFAULT_REPORT_INTERVAL;

    vos3_sha256_init(&dl_ctx.sha256_ctx);

    /* ----------------------------------------------------------------
     * 5. Clear HTTP response structure
     * ---------------------------------------------------------------- */
    memset(&response, 0, sizeof(response));
    response.content_length = -1;

    VOS3_INFO("[%s] Starting download: slot=%u, zone_base=%p, capacity=%zu",
              MODEL_DL_TAG, (unsigned)slot_id, zone,
              (size_t)VOS3_WARP_ZONE_SIZE);
    VOS3_INFO("[%s] URL: %s", MODEL_DL_TAG, url);

    /* ----------------------------------------------------------------
     * 6. Execute HTTPS GET with streaming callback
     * ---------------------------------------------------------------- */
    ret = vos3_https_get(url, &response, model_download_body_cb, &dl_ctx);

    if (ret < 0) {
        VOS3_ERROR("[%s] HTTPS GET failed: ret=%d", MODEL_DL_TAG, ret);
        ret = MODEL_DL_ERR_HTTP;
        goto zone_wipe;
    }

    /* ----------------------------------------------------------------
     * 7. Validate HTTP status code
     * ---------------------------------------------------------------- */
    if (response.status_code != VOS3_HTTP_STATUS_OK &&
        response.status_code != VOS3_HTTP_STATUS_PARTIAL) {
        VOS3_ERROR("[%s] Unexpected HTTP status: %d (expected 200 or 206)",
                   MODEL_DL_TAG, response.status_code);
        ret = MODEL_DL_ERR_HTTP_STATUS;
        goto zone_wipe;
    }

    /* ----------------------------------------------------------------
     * 8. Check for overflow / callback error
     * ---------------------------------------------------------------- */
    if (dl_ctx.overflow) {
        VOS3_ERROR("[%s] Download aborted: zone overflow at %zu bytes (slot %u, cap %zu)",
                   MODEL_DL_TAG, dl_ctx.zone_offset, (unsigned)slot_id,
                   dl_ctx.zone_size);
        ret = MODEL_DL_ERR_OVERFLOW;
        goto zone_wipe;
    }

    if (dl_ctx.error) {
        VOS3_ERROR("[%s] Download aborted: callback error (slot %u)",
                   MODEL_DL_TAG, (unsigned)slot_id);
        ret = MODEL_DL_ERR_CALLBACK;
        goto zone_wipe;
    }

    /* ----------------------------------------------------------------
     * 9. Finalize SHA-256 hash
     * ---------------------------------------------------------------- */
    vos3_sha256_final(&dl_ctx.sha256_ctx, computed_sha256);
    sha256_to_hex(computed_sha256, hex_str);

    VOS3_INFO("[%s] Download complete: slot=%u, size=%zu bytes, SHA-256=%s",
              MODEL_DL_TAG, (unsigned)slot_id, dl_ctx.zone_offset, hex_str);

    /* ----------------------------------------------------------------
     * 10. SHA-256 integrity verification (constant-time)
     * ---------------------------------------------------------------- */
    if (expected_sha256 != NULL) {
        if (sha256_ct_equal(computed_sha256, expected_sha256) != 0) {
            char expected_hex[65];
            sha256_to_hex(expected_sha256, expected_hex);
            VOS3_ERROR("[%s] SHA-256 MISMATCH: expected=%s, computed=%s",
                       MODEL_DL_TAG, expected_hex, hex_str);
            ret = MODEL_DL_ERR_SHA256_MISMATCH;
            goto zone_wipe;
        }
        VOS3_INFO("[%s] SHA-256 verification PASSED", MODEL_DL_TAG);
    }

    /* ----------------------------------------------------------------
     * 11. Flush zone from CPU cache for ivshmem host visibility
     *
     *     The ivshmem device uses memory-backend-file with share=on,
     *     so QEMU maps the same physical pages. CPU cache lines must
     *     be flushed (CLFLUSHOPT/CLFLUSH) so the host process sees
     *     the written data without stale cache interference.
     * ---------------------------------------------------------------- */
    vos3_cache_flush(dl_ctx.zone_base, dl_ctx.zone_offset);

    /* ----------------------------------------------------------------
     * 12. Return download size to caller
     * ---------------------------------------------------------------- */
    if (out_size != NULL) {
        *out_size = dl_ctx.zone_offset;
    }

    VOS3_INFO("[%s] Model ready in Warp zone %u (%zu bytes)",
              MODEL_DL_TAG, (unsigned)slot_id, dl_ctx.zone_offset);

    return 0;

    /* ==================================================================
     * R6+R7 (v36.1): Atomic Zero-Purity — wipe FULL zone on ANY error
     *
     * Every error path after vos3_https_get() (HTTP failure, bad status,
     * overflow, callback error, SHA-256 mismatch) jumps here to scrub
     * the entire ivshmem zone. We wipe zone_size (not zone_offset) to
     * guarantee no "zombie model" fragments survive, even if prior
     * stale data existed in the zone beyond what this download wrote.
     * ================================================================== */
zone_wipe:
    if (dl_ctx.zone_offset > 0U) {
        VOS3_WARN("[%s] Wiping FULL zone %u (%zu capacity) on error — "
                  "%zu bytes were written",
                  MODEL_DL_TAG, (unsigned)slot_id,
                  dl_ctx.zone_size, dl_ctx.zone_offset);
        vos3_cache_wipe(dl_ctx.zone_base, dl_ctx.zone_size);
    }
    return ret;
}

/* ============================================================================
 * PUBLIC API: vos3_model_download_status()
 * ============================================================================ */

/**
 * @brief Format a human-readable status string for a download context
 *
 * Writes a summary of the download progress into the provided buffer,
 * including slot ID, bytes received, expected total, and percentage.
 * Useful for bridge STATUS commands and diagnostic logging.
 *
 * @param[in]  ctx      Download context (may be in-progress or completed)
 * @param[out] buf      Destination buffer for the status string
 * @param[in]  buf_len  Size of the destination buffer in bytes
 *
 * @note If ctx is NULL or buf_len < 2, the buffer is set to an empty string.
 * @note The output is always NUL-terminated and never exceeds buf_len bytes.
 */
void vos3_model_download_status(const vos3_model_download_ctx_t *ctx,
                                char *buf, size_t buf_len)
{
    size_t pos = 0U;
    char   num[21];

    if (buf == NULL || buf_len == 0U) {
        return;
    }

    buf[0] = '\0';
    if (ctx == NULL || buf_len < 2U) {
        return;
    }

    /* "slot=N " */
    buf_append(buf, &pos, buf_len, "slot=");
    num[0] = (char)('0' + ctx->slot_id);
    num[1] = '\0';
    buf_append(buf, &pos, buf_len, num);
    buf_append(buf, &pos, buf_len, " ");

    /* State flag */
    if (ctx->overflow) {
        buf_append(buf, &pos, buf_len, "OVERFLOW ");
    } else if (ctx->error) {
        buf_append(buf, &pos, buf_len, "ERROR ");
    } else {
        buf_append(buf, &pos, buf_len, "OK ");
    }

    /* "rx=NNNNN/NNNNN" or "rx=NNNNN/?" */
    buf_append(buf, &pos, buf_len, "rx=");
    u64_to_dec(num, ctx->total_received);
    buf_append(buf, &pos, buf_len, num);
    buf_append(buf, &pos, buf_len, "/");

    if (ctx->total_expected > 0) {
        u64_to_dec(num, (uint64_t)ctx->total_expected);
        buf_append(buf, &pos, buf_len, num);
    } else {
        buf_append(buf, &pos, buf_len, "?");
    }

    /* " (NN%)" percentage if expected is known */
    if (ctx->total_expected > 0) {
        uint32_t pct = (uint32_t)((ctx->total_received * 100ULL) /
                                   (uint64_t)ctx->total_expected);
        buf_append(buf, &pos, buf_len, " (");
        u64_to_dec(num, (uint64_t)pct);
        buf_append(buf, &pos, buf_len, num);
        buf_append(buf, &pos, buf_len, "%)");
    }

    /* NUL-terminate */
    buf[pos < buf_len ? pos : buf_len - 1U] = '\0';
}
