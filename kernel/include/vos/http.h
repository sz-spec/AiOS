/**
 * @file http.h
 * @brief VOS3 Freestanding HTTP/1.1 Client — Sovereign Model Acquisition
 *
 * @details Minimal, secure HTTP/1.1 client for kernel-space HTTPS downloads.
 *          Designed for downloading multi-gigabyte GGUF models over TLS 1.3.
 *
 *          Capabilities:
 *          - GET and POST request formatting
 *          - HTTP/1.1 response parsing (status, headers, body)
 *          - Chunked Transfer Encoding for streaming large payloads
 *          - Content-Length based downloads with progress tracking
 *          - Range request support for resumable downloads
 *          - HTTPS-only enforcement (TLS 1.3 mandatory)
 *          - Fail-closed on TLS handshake failure
 *
 *          Security:
 *          - No plaintext HTTP allowed (HTTPS-only)
 *          - All TLS contexts scrubbed on exit paths
 *          - Header injection prevention (no \r\n in user-supplied values)
 *          - Bounded header parsing (max 8KB response headers)
 *          - Content-Length overflow protection (64-bit size tracking)
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.3 — Native HTTPS Model Acquisition (v36.0)
 */

#ifndef VOS3_HTTP_H
#define VOS3_HTTP_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief HTTPS port (TLS 1.3 mandatory) */
#define VOS3_HTTPS_PORT             443U

/** @brief HTTP port (rejected — fail-closed) */
#define VOS3_HTTP_PORT              80U

/** @brief Maximum URL length */
#define VOS3_HTTP_MAX_URL           2048U

/** @brief Maximum hostname length */
#define VOS3_HTTP_MAX_HOST          256U

/** @brief Maximum path length */
#define VOS3_HTTP_MAX_PATH          1024U

/** @brief Maximum header value length */
#define VOS3_HTTP_MAX_HEADER_VAL    512U

/** @brief Maximum response headers total size */
#define VOS3_HTTP_MAX_HEADERS       8192U

/** @brief Receive buffer size for streaming */
#define VOS3_HTTP_RX_BUF_SIZE       8192U

/** @brief User-Agent header value */
#define VOS3_HTTP_USER_AGENT        "VOS3-Kernel/1.0 (Sovereign-Crypto-Shield)"

/** @brief Maximum number of HTTP redirects to follow */
#define VOS3_HTTP_MAX_REDIRECTS     5U

/* ============================================================================
 * HTTP STATUS CODES
 * ============================================================================ */

#define VOS3_HTTP_STATUS_OK                  200
#define VOS3_HTTP_STATUS_PARTIAL             206
#define VOS3_HTTP_STATUS_MOVED_PERMANENTLY   301
#define VOS3_HTTP_STATUS_FOUND               302
#define VOS3_HTTP_STATUS_TEMPORARY_REDIRECT  307
#define VOS3_HTTP_STATUS_BAD_REQUEST         400
#define VOS3_HTTP_STATUS_UNAUTHORIZED        401
#define VOS3_HTTP_STATUS_FORBIDDEN           403
#define VOS3_HTTP_STATUS_NOT_FOUND           404
#define VOS3_HTTP_STATUS_SERVER_ERROR        500

/* ============================================================================
 * HTTP TRANSFER ENCODING
 * ============================================================================ */

/** @brief Transfer encoding type */
typedef enum vos3_http_encoding {
    VOS3_HTTP_ENCODING_IDENTITY     = 0,    /**< Content-Length based */
    VOS3_HTTP_ENCODING_CHUNKED      = 1     /**< Chunked Transfer Encoding */
} vos3_http_encoding_t;

/* ============================================================================
 * HTTP RESPONSE STRUCTURE
 * ============================================================================ */

/**
 * @brief Parsed HTTP response metadata
 *
 * Populated by vos3_http_get() after receiving and parsing
 * the response status line and headers.
 */
typedef struct vos3_http_response {
    /** @brief HTTP status code (200, 301, 404, etc.) */
    int                     status_code;

    /** @brief Content-Length value (-1 if not present) */
    int64_t                 content_length;

    /** @brief Transfer encoding type */
    vos3_http_encoding_t    encoding;

    /** @brief 1 if connection should be kept alive */
    uint8_t                 keep_alive;

    /** @brief Content-Type header value */
    char                    content_type[VOS3_HTTP_MAX_HEADER_VAL];

    /** @brief Location header value (for redirects) */
    char                    location[VOS3_HTTP_MAX_URL];

    /** @brief Total bytes of body received so far */
    uint64_t                body_received;
} vos3_http_response_t;

/* ============================================================================
 * STREAMING CALLBACK
 * ============================================================================ */

/**
 * @brief Callback for streaming HTTP response body data
 *
 * Called repeatedly as body chunks arrive from the network.
 * The callback must process the data and return 0 to continue
 * or a negative value to abort the download.
 *
 * @param[in] data       Pointer to received chunk data
 * @param[in] len        Length of the chunk in bytes
 * @param[in] total      Total bytes received so far (including this chunk)
 * @param[in] expected   Expected total size (-1 if chunked/unknown)
 * @param[in] user_ctx   Opaque user context pointer
 * @return 0 to continue downloading, negative to abort
 */
typedef int (*vos3_http_body_cb)(const uint8_t *data, size_t len,
                                  uint64_t total, int64_t expected,
                                  void *user_ctx);

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Perform an HTTPS GET request with streaming body callback
 *
 * Resolves the hostname via DNS, connects via TCP, performs TLS 1.3
 * handshake, sends HTTP/1.1 GET request, and streams the response
 * body to the callback function.
 *
 * Follows up to VOS3_HTTP_MAX_REDIRECTS HTTP redirects (301, 302, 307).
 *
 * @param[in]  url        Full HTTPS URL (e.g., "https://example.com/model.gguf")
 * @param[out] response   Parsed response metadata (may be NULL if not needed)
 * @param[in]  body_cb    Callback for body data (may be NULL to discard body)
 * @param[in]  user_ctx   Opaque context passed to body_cb
 * @return 0 on success (HTTP 200/206), negative on error
 *
 * @note Only HTTPS URLs are accepted. HTTP URLs return -EINVAL immediately.
 * @note If TLS handshake fails, returns error (fail-closed, no plaintext fallback).
 */
int vos3_https_get(const char *url,
                   vos3_http_response_t *response,
                   vos3_http_body_cb body_cb,
                   void *user_ctx);

/**
 * @brief Perform an HTTPS GET request with Range header (resumable)
 *
 * Same as vos3_https_get() but sends a Range header for partial content.
 * Expects HTTP 206 (Partial Content) response.
 *
 * @param[in]  url            Full HTTPS URL
 * @param[in]  range_start    First byte position
 * @param[in]  range_end      Last byte position (-1 for end of file)
 * @param[out] response       Parsed response metadata
 * @param[in]  body_cb        Callback for body data
 * @param[in]  user_ctx       Opaque context
 * @return 0 on success (HTTP 206), negative on error
 */
int vos3_https_get_range(const char *url,
                         uint64_t range_start, int64_t range_end,
                         vos3_http_response_t *response,
                         vos3_http_body_cb body_cb,
                         void *user_ctx);

/**
 * @brief Parse a URL into host, port, and path components
 *
 * Only accepts "https://" scheme. HTTP URLs are rejected.
 *
 * @param[in]  url   Full URL string
 * @param[out] host  Hostname buffer (VOS3_HTTP_MAX_HOST bytes)
 * @param[out] port  Port number (default 443)
 * @param[out] path  Path buffer (VOS3_HTTP_MAX_PATH bytes, "/" if empty)
 * @return 0 on success, -EINVAL on malformed or non-HTTPS URL
 */
int vos3_http_parse_url(const char *url,
                        char *host, uint16_t *port, char *path);

#endif /* VOS3_HTTP_H */
