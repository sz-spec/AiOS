/**
 * @file http.c
 * @brief VOS3 Freestanding HTTP/1.1 Client -- Sovereign Model Acquisition
 *
 * @details Minimal, secure HTTP/1.1 client for kernel-space HTTPS downloads.
 *          Designed for downloading multi-gigabyte GGUF models over TLS 1.3.
 *
 *          Capabilities:
 *          - HTTPS-only enforcement (plaintext HTTP rejected at parse time)
 *          - HTTP/1.1 GET with streaming body callback
 *          - Range request support for resumable downloads (HTTP 206)
 *          - Content-Length and Chunked Transfer Encoding body parsing
 *          - Automatic redirect following (301, 302, 307) up to 5 hops
 *          - Fail-closed: TLS failure = immediate error, no plaintext fallback
 *          - Stack-allocated parser state (zero heap allocation for HTTP layer)
 *
 *          Security:
 *          - All TLS contexts scrubbed on every exit path (goto cleanup)
 *          - Header injection prevention (\\r\\n rejected in host component)
 *          - Bounded header parsing (max 8 KB response headers)
 *          - Content-Length overflow protection (64-bit tracking)
 *          - Chunked encoding size validated against 64-bit limits
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.3 -- Native HTTPS Model Acquisition (v36.0)
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/http.h"
#include "../../include/vos/dns.h"
#include "../../include/vos/tls.h"
#include "../../include/vos/crypto.h"
#include "../../include/vos/tcp.h"
#include "../../include/vos/socket.h"
#include "../../include/vos/net.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/heap.h"

/* Socket-layer syscalls (TCP path) */
extern int     vos3_sys_socket(int domain, int type, int protocol);
extern int     vos3_sys_connect(int fd, const struct sockaddr *addr, socklen_t addrlen);
extern int     vos3_sys_closesocket(int fd);

/* ============================================================================
 * INTERNAL CONSTANTS
 * ============================================================================ */

/** @brief Maximum size of a formatted HTTP request on the stack */
#define HTTP_REQUEST_BUF_SIZE       2048U

/** @brief Receive buffer for TLS data during header/body parsing */
#define HTTP_RECV_BUF_SIZE          VOS3_HTTP_RX_BUF_SIZE

/** @brief Maximum header buffer for accumulating response headers */
#define HTTP_HEADER_BUF_SIZE        VOS3_HTTP_MAX_HEADERS

/** @brief HTTPS scheme prefix */
#define HTTPS_SCHEME                "https://"

/** @brief HTTPS scheme prefix length */
#define HTTPS_SCHEME_LEN            8U

/** @brief HTTP scheme prefix (rejected) */
#define HTTP_SCHEME                 "http://"

/** @brief HTTP scheme prefix length */
#define HTTP_SCHEME_LEN             7U

/** @brief CRLF sequence */
#define CRLF                        "\r\n"

/** @brief Double-CRLF marking end of headers */
#define CRLFCRLF                    "\r\n\r\n"

/** @brief Error code: invalid argument */
#define E_INVAL                     (-22)

/** @brief Error code: I/O error */
#define E_IO                        (-5)

/** @brief Error code: out of memory */
#define E_NOMEM                     (-12)

/** @brief Error code: connection refused */
#define E_CONNREFUSED               (-111)

/** @brief Error code: timed out */
#define E_TIMEDOUT                  (-110)

/** @brief Error code: protocol error */
#define E_PROTO                     (-71)

/** @brief Error code: too many redirects */
#define E_LOOP                      (-40)

/* ============================================================================
 * HELPER: Case-Insensitive ASCII Comparison
 * ============================================================================ */

/**
 * @brief Case-insensitive comparison of two ASCII strings up to n bytes
 *
 * Performs comparison by OR-ing each byte with 0x20 to fold uppercase
 * ASCII letters (A-Z, 0x41-0x5A) into lowercase (a-z, 0x61-0x7A).
 * This is correct for the ASCII letter range used in HTTP header names.
 *
 * @param[in] a  First string
 * @param[in] b  Second string
 * @param[in] n  Maximum number of bytes to compare
 * @return 0 if equal (case-insensitive), nonzero otherwise
 *
 * @note Only valid for ASCII. Non-ASCII bytes may produce false matches
 *       for characters that differ only in bit 5, but HTTP headers are
 *       strictly ASCII per RFC 7230.
 */
static int strncasecmp_http(const char *a, const char *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char)a[i];
        unsigned char cb = (unsigned char)b[i];

        /* Fold ASCII uppercase to lowercase */
        if (ca >= 'A' && ca <= 'Z') {
            ca = (unsigned char)(ca | 0x20U);
        }
        if (cb >= 'A' && cb <= 'Z') {
            cb = (unsigned char)(cb | 0x20U);
        }

        if (ca != cb) {
            return (int)ca - (int)cb;
        }
        if (ca == '\0') {
            return 0;
        }
    }
    return 0;
}

/* ============================================================================
 * HELPER: Hexadecimal String to uint64_t
 * ============================================================================ */

/**
 * @brief Parse a hexadecimal string into a 64-bit unsigned integer
 *
 * Reads hexadecimal digits (0-9, a-f, A-F) from the input string
 * and converts them to a uint64_t value. Stops at the first non-hex
 * character.
 *
 * @param[in]  str       Input string starting with hex digits
 * @param[out] out_val   Parsed value
 * @param[out] out_len   Number of hex characters consumed
 * @return 0 on success (at least one digit consumed), -1 on error
 */
static int hex_to_uint64(const char *str, uint64_t *out_val, size_t *out_len)
{
    uint64_t val = 0;
    size_t i = 0;

    while (str[i] != '\0') {
        unsigned char ch = (unsigned char)str[i];
        uint64_t digit;

        if (ch >= '0' && ch <= '9') {
            digit = (uint64_t)(ch - '0');
        } else if (ch >= 'a' && ch <= 'f') {
            digit = (uint64_t)(ch - 'a') + 10U;
        } else if (ch >= 'A' && ch <= 'F') {
            digit = (uint64_t)(ch - 'A') + 10U;
        } else {
            break;  /* Non-hex character terminates parsing */
        }

        /* R1 (v36.1): Overflow check — reject if next shift would reach UINT64_MAX.
         * Using >= (not >) so that val == 0x0FFFFFFFFFFFFFFF is rejected:
         * (0x0FFFFFFFFFFFFFFF << 4) | 0xF == UINT64_MAX, which causes
         * downstream wrap-around in need_total arithmetic. */
        if (val >= (UINT64_MAX >> 4)) {
            return -1;  /* Would overflow or produce UINT64_MAX */
        }
        val = (val << 4) | digit;
        i++;
    }

    if (i == 0) {
        return -1;  /* No hex digits found */
    }

    *out_val = val;
    *out_len = i;
    return 0;
}

/* ============================================================================
 * HELPER: Decimal String to uint64_t
 * ============================================================================ */

/**
 * @brief Parse a decimal string into a 64-bit unsigned integer
 *
 * Reads decimal digits (0-9) and converts to uint64_t.
 * Stops at the first non-digit character.
 *
 * @param[in]  str       Input string starting with decimal digits
 * @param[out] out_val   Parsed value
 * @return Number of digits consumed, 0 if no digits found
 */
static size_t dec_to_uint64(const char *str, uint64_t *out_val)
{
    uint64_t val = 0;
    size_t i = 0;

    while (str[i] >= '0' && str[i] <= '9') {
        uint64_t digit = (uint64_t)(str[i] - '0');

        /* Overflow protection */
        if (val > (UINT64_MAX - digit) / 10U) {
            break;
        }
        val = val * 10U + digit;
        i++;
    }

    *out_val = val;
    return i;
}

/* ============================================================================
 * HELPER: Integer to Decimal String
 * ============================================================================ */

/**
 * @brief Convert a uint64_t to a decimal ASCII string
 *
 * Writes the decimal representation of val into buf.
 * The caller must ensure buf has at least 21 bytes (20 digits + NUL).
 *
 * @param[out] buf   Output buffer (at least 21 bytes)
 * @param[in]  val   Value to convert
 * @return Number of characters written (not including NUL terminator)
 */
static size_t uint64_to_dec(char *buf, uint64_t val)
{
    if (val == 0) {
        buf[0] = '0';
        buf[1] = '\0';
        return 1;
    }

    char tmp[21];
    size_t i = 0;

    while (val > 0 && i < 20U) {
        tmp[i++] = (char)('0' + (int)(val % 10U));
        val /= 10U;
    }

    /* Reverse into output buffer */
    for (size_t j = 0; j < i; j++) {
        buf[j] = tmp[i - 1U - j];
    }
    buf[i] = '\0';

    return i;
}

/* ============================================================================
 * HELPER: Search for Substring in Buffer
 * ============================================================================ */

/**
 * @brief Find the first occurrence of needle in haystack (bounded)
 *
 * @param[in] haystack    Buffer to search
 * @param[in] haystack_len Length of haystack
 * @param[in] needle      Pattern to find (null-terminated)
 * @return Pointer to first occurrence, or NULL if not found
 */
static const char *memmem_str(const char *haystack, size_t haystack_len,
                              const char *needle)
{
    size_t needle_len = strlen(needle);
    if (needle_len == 0 || needle_len > haystack_len) {
        return NULL;
    }

    for (size_t i = 0; i <= haystack_len - needle_len; i++) {
        if (memcmp(&haystack[i], needle, needle_len) == 0) {
            return &haystack[i];
        }
    }
    return NULL;
}

/* ============================================================================
 * URL PARSER
 * ============================================================================ */

/**
 * @brief Parse an HTTPS URL into host, port, and path components
 *
 * Accepts only the "https://" scheme. Plaintext "http://" is rejected
 * with -EINVAL to enforce HTTPS-only operation.
 *
 * URL format: https://host[:port][/path]
 *
 * Examples:
 *   "https://example.com"              -> host="example.com", port=443, path="/"
 *   "https://example.com:8443"         -> host="example.com", port=8443, path="/"
 *   "https://example.com/model.gguf"   -> host="example.com", port=443, path="/model.gguf"
 *   "https://cdn.example.com:9443/a/b" -> host="cdn.example.com", port=9443, path="/a/b"
 *
 * @param[in]  url   Full URL string (null-terminated)
 * @param[out] host  Hostname buffer (must be VOS3_HTTP_MAX_HOST bytes)
 * @param[out] port  Parsed port number (default 443 if not specified)
 * @param[out] path  Path buffer (must be VOS3_HTTP_MAX_PATH bytes, "/" if empty)
 * @return 0 on success, -EINVAL (-22) on malformed or non-HTTPS URL
 */
int vos3_http_parse_url(const char *url, char *host, uint16_t *port, char *path)
{
    if (url == NULL || host == NULL || port == NULL || path == NULL) {
        return E_INVAL;
    }

    /* ----------------------------------------------------------------
     * Step 1: Validate scheme -- HTTPS only
     * ---------------------------------------------------------------- */
    if (strncmp(url, HTTP_SCHEME, HTTP_SCHEME_LEN) == 0) {
        VOS3_ERROR("HTTP: Plaintext http:// rejected (HTTPS-only enforcement)");
        return E_INVAL;
    }

    if (strncmp(url, HTTPS_SCHEME, HTTPS_SCHEME_LEN) != 0) {
        VOS3_ERROR("HTTP: URL must start with https://");
        return E_INVAL;
    }

    const char *cursor = url + HTTPS_SCHEME_LEN;

    /* ----------------------------------------------------------------
     * Step 2: Extract hostname
     *
     * The host portion ends at the first ':', '/', or NUL.
     * ---------------------------------------------------------------- */
    size_t host_len = 0;
    while (cursor[host_len] != '\0' &&
           cursor[host_len] != ':' &&
           cursor[host_len] != '/') {
        /* Header injection prevention: reject CR/LF in hostname */
        if (cursor[host_len] == '\r' || cursor[host_len] == '\n') {
            VOS3_ERROR("HTTP: Header injection character in hostname");
            return E_INVAL;
        }
        host_len++;
    }

    if (host_len == 0 || host_len >= VOS3_HTTP_MAX_HOST) {
        VOS3_ERROR("HTTP: Hostname empty or too long (%zu)", host_len);
        return E_INVAL;
    }

    memcpy(host, cursor, host_len);
    host[host_len] = '\0';
    cursor += host_len;

    /* ----------------------------------------------------------------
     * Step 3: Parse optional port (after ':')
     * ---------------------------------------------------------------- */
    *port = (uint16_t)VOS3_HTTPS_PORT;  /* Default: 443 */

    if (*cursor == ':') {
        cursor++;  /* Skip ':' */

        uint64_t port_val = 0;
        size_t digits = dec_to_uint64(cursor, &port_val);
        if (digits == 0 || port_val == 0 || port_val > 65535U) {
            VOS3_ERROR("HTTP: Invalid port number in URL");
            return E_INVAL;
        }
        *port = (uint16_t)port_val;
        cursor += digits;
    }

    /* ----------------------------------------------------------------
     * Step 4: Parse path (after host+port, or default to "/")
     * ---------------------------------------------------------------- */
    if (*cursor == '/') {
        size_t path_len = strlen(cursor);
        if (path_len >= VOS3_HTTP_MAX_PATH) {
            VOS3_ERROR("HTTP: Path too long (%zu)", path_len);
            return E_INVAL;
        }
        memcpy(path, cursor, path_len);
        path[path_len] = '\0';
    } else if (*cursor == '\0') {
        /* No path specified -- default to "/" */
        path[0] = '/';
        path[1] = '\0';
    } else {
        VOS3_ERROR("HTTP: Unexpected character after port: '%c'", *cursor);
        return E_INVAL;
    }

    VOS3_DEBUG("HTTP: Parsed URL -> host='%s' port=%u path='%s'",
               host, (unsigned)*port, path);

    return 0;
}

/* ============================================================================
 * HTTP REQUEST BUILDER
 * ============================================================================ */

/**
 * @brief Build an HTTP/1.1 GET request string
 *
 * Formats a complete HTTP/1.1 GET request into a stack buffer including
 * the mandatory Host, User-Agent, Connection, and Accept headers.
 * Optionally includes a Range header for partial content requests.
 *
 * @param[out] buf          Output buffer for the formatted request
 * @param[in]  buf_size     Size of the output buffer in bytes
 * @param[in]  host         Server hostname (for Host header)
 * @param[in]  port         Server port (included in Host header if non-443)
 * @param[in]  path         Request path (e.g., "/model.gguf")
 * @param[in]  range_start  Range start byte (only if has_range is nonzero)
 * @param[in]  range_end    Range end byte (-1 for open-ended; only if has_range)
 * @param[in]  has_range    Nonzero to include Range header
 * @return Number of bytes written to buf (not including NUL), or -1 on error
 */
static int http_build_request(char *buf, size_t buf_size,
                              const char *host, uint16_t port,
                              const char *path,
                              uint64_t range_start, int64_t range_end,
                              int has_range)
{
    size_t pos = 0;
    size_t remaining;

    /* Macro-like helper: append a string literal safely */
#define APPEND_STR(s) do {                          \
    size_t _slen = strlen(s);                       \
    if (pos + _slen >= buf_size) { return -1; }     \
    memcpy(&buf[pos], (s), _slen);                  \
    pos += _slen;                                   \
} while (0)

#define APPEND_CHAR(c) do {                         \
    if (pos + 1U >= buf_size) { return -1; }        \
    buf[pos++] = (c);                               \
} while (0)

    /* Request line: GET /path HTTP/1.1\r\n */
    APPEND_STR("GET ");
    APPEND_STR(path);
    APPEND_STR(" HTTP/1.1\r\n");

    /* Host header (include port only if non-default) */
    APPEND_STR("Host: ");
    APPEND_STR(host);
    if (port != VOS3_HTTPS_PORT) {
        APPEND_CHAR(':');
        char port_str[8];
        size_t plen = uint64_to_dec(port_str, (uint64_t)port);
        remaining = buf_size - pos;
        if (plen >= remaining) { return -1; }
        memcpy(&buf[pos], port_str, plen);
        pos += plen;
    }
    APPEND_STR("\r\n");

    /* User-Agent header */
    APPEND_STR("User-Agent: ");
    APPEND_STR(VOS3_HTTP_USER_AGENT);
    APPEND_STR("\r\n");

    /* Connection header */
    APPEND_STR("Connection: close\r\n");

    /* Accept header */
    APPEND_STR("Accept: */*\r\n");

    /* Range header (optional) */
    if (has_range) {
        APPEND_STR("Range: bytes=");

        char num_str[21];
        size_t nlen;

        /* Start byte */
        nlen = uint64_to_dec(num_str, range_start);
        remaining = buf_size - pos;
        if (nlen >= remaining) { return -1; }
        memcpy(&buf[pos], num_str, nlen);
        pos += nlen;

        APPEND_CHAR('-');

        /* End byte (omit if -1 to request "bytes=START-" meaning to EOF) */
        if (range_end >= 0) {
            nlen = uint64_to_dec(num_str, (uint64_t)range_end);
            remaining = buf_size - pos;
            if (nlen >= remaining) { return -1; }
            memcpy(&buf[pos], num_str, nlen);
            pos += nlen;
        }

        APPEND_STR("\r\n");
    }

    /* End of headers */
    APPEND_STR("\r\n");

    buf[pos] = '\0';

#undef APPEND_STR
#undef APPEND_CHAR

    return (int)pos;
}

/* ============================================================================
 * HTTP STATUS LINE PARSER
 * ============================================================================ */

/**
 * @brief Parse the HTTP/1.1 status line from a response
 *
 * Expects format: "HTTP/1.1 STATUS REASON\\r\\n"
 * Extracts the 3-digit numeric status code.
 *
 * @param[in]  buf          Response buffer
 * @param[in]  buf_len      Length of data in buffer
 * @param[out] status_code  Parsed HTTP status code (e.g., 200, 301, 404)
 * @return Number of bytes consumed (including the trailing \\r\\n),
 *         or -1 on parse error
 */
static int http_parse_status_line(const char *buf, size_t buf_len,
                                  int *status_code)
{
    /* Minimum: "HTTP/1.1 200\r\n" = 14 bytes */
    if (buf_len < 14U) {
        return -1;
    }

    /* Verify HTTP version prefix */
    if (strncmp(buf, "HTTP/1.1 ", 9) != 0 &&
        strncmp(buf, "HTTP/1.0 ", 9) != 0) {
        VOS3_WARN("HTTP: Unexpected status line prefix");
        return -1;
    }

    /* Parse 3-digit status code at offset 9 */
    const char *code_start = &buf[9];
    if (code_start[0] < '1' || code_start[0] > '5' ||
        code_start[1] < '0' || code_start[1] > '9' ||
        code_start[2] < '0' || code_start[2] > '9') {
        VOS3_WARN("HTTP: Malformed status code");
        return -1;
    }

    *status_code = (int)(code_start[0] - '0') * 100 +
                   (int)(code_start[1] - '0') * 10 +
                   (int)(code_start[2] - '0');

    /* Find the end of the status line (\r\n) */
    const char *crlf = memmem_str(buf, buf_len, CRLF);
    if (crlf == NULL) {
        return -1;  /* Incomplete status line */
    }

    size_t consumed = (size_t)(crlf - buf) + 2U;  /* +2 for \r\n */

    VOS3_DEBUG("HTTP: Status line -> %d (consumed %zu bytes)",
               *status_code, consumed);

    return (int)consumed;
}

/* ============================================================================
 * HTTP HEADER PARSER
 * ============================================================================ */

/**
 * @brief Parse HTTP response headers and populate response metadata
 *
 * Reads headers from the buffer starting after the status line.
 * Searches for the double-CRLF (\\r\\n\\r\\n) marking the end of headers.
 *
 * Extracts the following headers (case-insensitive matching):
 * - Content-Length: sets response->content_length
 * - Transfer-Encoding: chunked -> sets response->encoding
 * - Content-Type: copies into response->content_type
 * - Location: copies into response->location (for redirects)
 * - Connection: keep-alive -> sets response->keep_alive
 *
 * @param[in]  buf       Response buffer positioned at first header line
 * @param[in]  buf_len   Length of data in buffer
 * @param[out] response  Response structure to populate
 * @return Number of bytes consumed (through the final \\r\\n\\r\\n),
 *         or -1 if headers are incomplete or malformed
 */
static int http_parse_headers(const char *buf, size_t buf_len,
                              vos3_http_response_t *response)
{
    /* Find the end-of-headers marker */
    const char *eoh = memmem_str(buf, buf_len, CRLFCRLF);
    if (eoh == NULL) {
        return -1;  /* Headers not yet complete in buffer */
    }

    size_t headers_end = (size_t)(eoh - buf) + 4U;  /* Include \r\n\r\n */

    /* Initialize response header fields */
    response->content_length = -1;
    response->encoding = VOS3_HTTP_ENCODING_IDENTITY;
    response->keep_alive = 0;
    response->content_type[0] = '\0';
    response->location[0] = '\0';

    /* Parse line by line */
    const char *line = buf;
    const char *end = eoh;  /* Points to the final \r\n\r\n */

    while (line < end) {
        /* Find end of this header line */
        const char *line_end = memmem_str(line, (size_t)(end - line) + 2U, CRLF);
        if (line_end == NULL) {
            break;
        }

        size_t line_len = (size_t)(line_end - line);

        /* Find the colon separating name from value */
        const char *colon = NULL;
        for (size_t i = 0; i < line_len; i++) {
            if (line[i] == ':') {
                colon = &line[i];
                break;
            }
        }

        if (colon != NULL) {
            size_t name_len = (size_t)(colon - line);
            const char *value = colon + 1;

            /* Skip leading whitespace in value (OWS per RFC 7230) */
            while (value < line_end && (*value == ' ' || *value == '\t')) {
                value++;
            }

            size_t value_len = (size_t)(line_end - value);

            /* --- Content-Length --- */
            if (name_len == 14U &&
                strncasecmp_http(line, "Content-Length", 14) == 0) {
                uint64_t clen = 0;
                size_t digits = dec_to_uint64(value, &clen);
                if (digits > 0) {
                    response->content_length = (int64_t)clen;
                    VOS3_DEBUG("HTTP: Content-Length: %llu",
                               (unsigned long long)clen);
                }
            }

            /* --- Transfer-Encoding --- */
            else if (name_len == 17U &&
                     strncasecmp_http(line, "Transfer-Encoding", 17) == 0) {
                if (value_len >= 7U &&
                    strncasecmp_http(value, "chunked", 7) == 0) {
                    response->encoding = VOS3_HTTP_ENCODING_CHUNKED;
                    VOS3_DEBUG("HTTP: Transfer-Encoding: chunked");
                }
            }

            /* --- Content-Type --- */
            else if (name_len == 12U &&
                     strncasecmp_http(line, "Content-Type", 12) == 0) {
                size_t copy_len = value_len;
                if (copy_len >= VOS3_HTTP_MAX_HEADER_VAL) {
                    copy_len = VOS3_HTTP_MAX_HEADER_VAL - 1U;
                }
                memcpy(response->content_type, value, copy_len);
                response->content_type[copy_len] = '\0';
                VOS3_DEBUG("HTTP: Content-Type: %s", response->content_type);
            }

            /* --- Location (redirect target) --- */
            else if (name_len == 8U &&
                     strncasecmp_http(line, "Location", 8) == 0) {
                size_t copy_len = value_len;
                if (copy_len >= VOS3_HTTP_MAX_URL) {
                    copy_len = VOS3_HTTP_MAX_URL - 1U;
                }
                memcpy(response->location, value, copy_len);
                response->location[copy_len] = '\0';
                VOS3_DEBUG("HTTP: Location: %s", response->location);
            }

            /* --- Connection --- */
            else if (name_len == 10U &&
                     strncasecmp_http(line, "Connection", 10) == 0) {
                if (value_len >= 10U &&
                    strncasecmp_http(value, "keep-alive", 10) == 0) {
                    response->keep_alive = 1;
                }
            }
        }

        /* Advance to next line (past \r\n) */
        line = line_end + 2U;
    }

    return (int)headers_end;
}

/* ============================================================================
 * HTTP BODY RECEIVER: Content-Length Mode
 * ============================================================================ */

/**
 * @brief Receive HTTP body using Content-Length
 *
 * Reads exactly content_length bytes from the TLS connection, calling
 * the body callback for each received chunk. Handles the case where
 * some body data was already read during header parsing (leftover).
 *
 * @param[in]     tls_ctx         Active TLS context
 * @param[in,out] response        Response metadata (body_received updated)
 * @param[in]     content_length  Expected body size in bytes
 * @param[in]     body_cb         Streaming callback (may be NULL)
 * @param[in]     user_ctx        Opaque context for body_cb
 * @param[in]     leftover        Any body bytes already read during header parsing
 * @param[in]     leftover_len    Length of leftover data
 * @return 0 on success, negative on error (TLS failure or callback abort)
 */
static int http_recv_body_content_length(vos3_tls_ctx_t *tls_ctx,
                                         vos3_http_response_t *response,
                                         uint64_t content_length,
                                         vos3_http_body_cb body_cb,
                                         void *user_ctx,
                                         const uint8_t *leftover,
                                         size_t leftover_len)
{
    uint64_t received = 0;

    /* ----------------------------------------------------------------
     * Step 1: Process any leftover bytes from header parsing
     * ---------------------------------------------------------------- */
    if (leftover_len > 0) {
        size_t use_len = leftover_len;
        if ((uint64_t)use_len > content_length) {
            use_len = (size_t)content_length;
        }

        received += (uint64_t)use_len;
        response->body_received = received;

        if (body_cb != NULL) {
            int cb_ret = body_cb(leftover, use_len, received,
                                 (int64_t)content_length, user_ctx);
            if (cb_ret < 0) {
                VOS3_WARN("HTTP: Body callback aborted download");
                return cb_ret;
            }
        }
    }

    /* ----------------------------------------------------------------
     * Step 2: Read remaining body data from TLS
     * ---------------------------------------------------------------- */
    uint8_t recv_buf[HTTP_RECV_BUF_SIZE];

    while (received < content_length) {
        uint64_t remaining = content_length - received;
        size_t want = (remaining < (uint64_t)HTTP_RECV_BUF_SIZE)
                      ? (size_t)remaining
                      : HTTP_RECV_BUF_SIZE;

        int n = vos3_tls_recv(tls_ctx, recv_buf, want);
        if (n <= 0) {
            VOS3_WARN("HTTP: TLS recv returned %d at offset %llu/%llu",
                      n, (unsigned long long)received,
                      (unsigned long long)content_length);
            return (n == 0) ? E_IO : n;
        }

        received += (uint64_t)n;
        response->body_received = received;

        if (body_cb != NULL) {
            int cb_ret = body_cb(recv_buf, (size_t)n, received,
                                 (int64_t)content_length, user_ctx);
            if (cb_ret < 0) {
                VOS3_WARN("HTTP: Body callback aborted download at %llu/%llu",
                          (unsigned long long)received,
                          (unsigned long long)content_length);
                return cb_ret;
            }
        }
    }

    VOS3_INFO("HTTP: Body complete: %llu bytes received",
              (unsigned long long)received);

    return 0;
}

/* ============================================================================
 * HTTP BODY RECEIVER: Chunked Transfer Encoding
 * ============================================================================ */

/**
 * @brief Receive HTTP body using Chunked Transfer Encoding
 *
 * Parses the chunked encoding format:
 *   SIZE_HEX\\r\\n
 *   DATA (SIZE_HEX bytes)\\r\\n
 *   ...
 *   0\\r\\n
 *   \\r\\n
 *
 * Handles chunks split across TLS recv boundaries by maintaining a
 * leftover buffer. Each complete chunk's data is delivered to the
 * body callback.
 *
 * @param[in]     tls_ctx       Active TLS context
 * @param[in,out] response      Response metadata (body_received updated)
 * @param[in]     body_cb       Streaming callback (may be NULL)
 * @param[in]     user_ctx      Opaque context for body_cb
 * @param[in]     leftover      Any body bytes already read during header parsing
 * @param[in]     leftover_len  Length of leftover data
 * @return 0 on success, negative on error
 */
static int http_recv_body_chunked(vos3_tls_ctx_t *tls_ctx,
                                  vos3_http_response_t *response,
                                  vos3_http_body_cb body_cb,
                                  void *user_ctx,
                                  const uint8_t *leftover,
                                  size_t leftover_len)
{
    /*
     * Accumulation buffer: holds leftover data from previous reads plus
     * new TLS recv data. We parse chunk headers and data from this buffer.
     *
     * We use a generously sized buffer (2x recv) to handle chunk headers
     * that span recv boundaries.
     */
    uint8_t accum[HTTP_RECV_BUF_SIZE * 2U];
    size_t accum_len = 0;

    /* Seed the accumulator with any leftover bytes */
    if (leftover_len > 0) {
        size_t copy = leftover_len;
        if (copy > sizeof(accum)) {
            copy = sizeof(accum);
        }
        memcpy(accum, leftover, copy);
        accum_len = copy;
    }

    uint64_t total_received = 0;
    int done = 0;

    while (!done) {
        /* ----------------------------------------------------------------
         * Refill: Read more data from TLS if accumulator is getting low
         * ---------------------------------------------------------------- */
        if (accum_len < sizeof(accum) / 2U) {
            size_t space = sizeof(accum) - accum_len;
            if (space > HTTP_RECV_BUF_SIZE) {
                space = HTTP_RECV_BUF_SIZE;
            }

            int n = vos3_tls_recv(tls_ctx, &accum[accum_len], space);
            if (n > 0) {
                accum_len += (size_t)n;
            } else if (n == 0) {
                /* R5 (v36.1): EOF without terminal 0\r\n\r\n is a protocol error.
                 * A truncated chunked stream MUST NOT be treated as success —
                 * partial data may have been written to the Warp-Drive zone
                 * and the SHA-256 digest would be over an incomplete file. */
                VOS3_ERROR("HTTP: Chunked EOF before terminator "
                          "(received %llu bytes) — protocol error",
                          (unsigned long long)total_received);
                return E_IO;
            } else {
                VOS3_WARN("HTTP: TLS recv error during chunked read: %d", n);
                return n;
            }
        }

        /* ----------------------------------------------------------------
         * Parse chunk header: "SIZE_HEX\r\n"
         * ---------------------------------------------------------------- */
        const char *crlf_pos = memmem_str((const char *)accum, accum_len, CRLF);
        if (crlf_pos == NULL) {
            /* Need more data to find chunk header CRLF */
            if (accum_len >= sizeof(accum)) {
                VOS3_ERROR("HTTP: Chunked header too large (>%zu bytes)",
                           sizeof(accum));
                return E_PROTO;
            }
            continue;  /* Read more data */
        }

        size_t header_len = (size_t)(crlf_pos - (const char *)accum);

        /* Parse the hex chunk size */
        uint64_t chunk_size = 0;
        size_t hex_consumed = 0;

        if (header_len == 0) {
            VOS3_ERROR("HTTP: Empty chunk header line");
            return E_PROTO;
        }

        int hex_ret = hex_to_uint64((const char *)accum, &chunk_size,
                                    &hex_consumed);
        if (hex_ret < 0 || hex_consumed == 0) {
            VOS3_ERROR("HTTP: Invalid chunk size hex");
            return E_PROTO;
        }

        /* R2 (v36.1): Hard sanity cap — no HTTP chunk can exceed 4 GiB.
         * Prevents absurd chunk sizes from causing downstream arithmetic
         * overflows in need_total and body callback len parameters. */
        if (chunk_size > 0x100000000ULL) {
            VOS3_ERROR("HTTP: Chunk size %llu exceeds 4GB sanity cap",
                       (unsigned long long)chunk_size);
            return E_PROTO;
        }

        /* Skip chunk extensions (after hex size, before CRLF -- RFC 7230 4.1.1) */
        size_t line_total = header_len + 2U;  /* header + \r\n */

        /* ----------------------------------------------------------------
         * Terminal chunk: size == 0
         * ---------------------------------------------------------------- */
        if (chunk_size == 0) {
            VOS3_DEBUG("HTTP: Chunked terminator received");

            /* Consume "0\r\n" and expect trailing "\r\n" (empty trailer) */
            if (accum_len >= line_total + 2U) {
                /* Verify trailing \r\n */
                if (accum[line_total] == '\r' && accum[line_total + 1U] == '\n') {
                    /* Complete: 0\r\n\r\n consumed */
                }
            }
            done = 1;
            break;
        }

        /* ----------------------------------------------------------------
         * Data chunk: consume header, then read chunk_size bytes + \r\n
         * ---------------------------------------------------------------- */

        /* R3 (v36.1): Overflow guard — prevent need_total wrap-around.
         * Even with R2's 4GB cap this belt-and-suspenders check ensures
         * (line_total + chunk_size + 2) can never wrap SIZE_MAX. */
        if ((size_t)chunk_size > (SIZE_MAX - line_total - 2U)) {
            VOS3_ERROR("HTTP: Chunk size %llu would overflow accumulator arithmetic",
                       (unsigned long long)chunk_size);
            return E_PROTO;
        }

        size_t need_total = line_total + (size_t)chunk_size + 2U; /* data + \r\n */

        /* Ensure we have enough data in the accumulator */
        while (accum_len < need_total) {
            size_t space = sizeof(accum) - accum_len;
            if (space == 0) {
                /*
                 * Chunk is larger than our accumulator. We need to deliver
                 * data in pieces. First, deliver what we have past the header.
                 */
                break;
            }
            if (space > HTTP_RECV_BUF_SIZE) {
                space = HTTP_RECV_BUF_SIZE;
            }

            int n = vos3_tls_recv(tls_ctx, &accum[accum_len], space);
            if (n <= 0) {
                VOS3_WARN("HTTP: TLS recv error in chunk body: %d", n);
                return (n == 0) ? E_IO : n;
            }
            accum_len += (size_t)n;
        }

        /*
         * If the chunk fits in the accumulator, deliver it in one shot.
         * Otherwise, we stream it in pieces.
         */
        if (accum_len >= need_total) {
            /* Entire chunk is in the accumulator */
            const uint8_t *chunk_data = &accum[line_total];

            total_received += chunk_size;
            response->body_received = total_received;

            if (body_cb != NULL) {
                int cb_ret = body_cb(chunk_data, (size_t)chunk_size,
                                     total_received, -1, user_ctx);
                if (cb_ret < 0) {
                    VOS3_WARN("HTTP: Body callback aborted chunked download");
                    return cb_ret;
                }
            }

            /* Consume: header + data + trailing \r\n */
            size_t consumed = need_total;
            size_t remain = accum_len - consumed;
            if (remain > 0) {
                memmove(accum, &accum[consumed], remain);
            }
            accum_len = remain;
        } else {
            /*
             * Large chunk: deliver available data past the header, then
             * stream directly from TLS until chunk_size bytes delivered.
             */
            size_t avail_data = accum_len - line_total;
            uint64_t chunk_delivered = 0;

            /* Deliver what we have in the accumulator */
            if (avail_data > 0) {
                size_t deliver = avail_data;
                if ((uint64_t)deliver > chunk_size) {
                    deliver = (size_t)chunk_size;
                }

                total_received += (uint64_t)deliver;
                chunk_delivered += (uint64_t)deliver;
                response->body_received = total_received;

                if (body_cb != NULL) {
                    int cb_ret = body_cb(&accum[line_total], deliver,
                                         total_received, -1, user_ctx);
                    if (cb_ret < 0) {
                        return cb_ret;
                    }
                }
            }

            /* Stream remaining chunk data directly */
            uint8_t stream_buf[HTTP_RECV_BUF_SIZE];

            while (chunk_delivered < chunk_size) {
                uint64_t rem = chunk_size - chunk_delivered;
                size_t want = (rem < (uint64_t)HTTP_RECV_BUF_SIZE)
                              ? (size_t)rem
                              : HTTP_RECV_BUF_SIZE;

                int n = vos3_tls_recv(tls_ctx, stream_buf, want);
                if (n <= 0) {
                    VOS3_WARN("HTTP: TLS recv error streaming large chunk: %d",
                              n);
                    return (n == 0) ? E_IO : n;
                }

                total_received += (uint64_t)n;
                chunk_delivered += (uint64_t)n;
                response->body_received = total_received;

                if (body_cb != NULL) {
                    int cb_ret = body_cb(stream_buf, (size_t)n,
                                         total_received, -1, user_ctx);
                    if (cb_ret < 0) {
                        return cb_ret;
                    }
                }
            }

            /* Read and discard the trailing \r\n after chunk data */
            uint8_t trail[2];
            size_t trail_got = 0;
            while (trail_got < 2U) {
                int n = vos3_tls_recv(tls_ctx, &trail[trail_got],
                                      2U - trail_got);
                if (n <= 0) {
                    VOS3_WARN("HTTP: Failed to read chunk trailer CRLF");
                    return E_IO;
                }
                trail_got += (size_t)n;
            }

            /* Reset accumulator for next chunk */
            accum_len = 0;
        }
    }

    VOS3_INFO("HTTP: Chunked body complete: %llu bytes received",
              (unsigned long long)total_received);

    return 0;
}

/* ============================================================================
 * INTERNAL: Full HTTPS GET Implementation
 * ============================================================================ */

/**
 * @brief Internal implementation for HTTPS GET (with optional Range header)
 *
 * Performs the complete HTTPS request lifecycle:
 * 1. Parse URL into host, port, path components
 * 2. Resolve hostname to IPv4 via DNS
 * 3. Create TCP socket and connect to server
 * 4. Initialize TLS 1.3 context and perform handshake with SNI
 * 5. Build and send HTTP/1.1 GET request
 * 6. Receive and parse response status line and headers
 * 7. Stream response body to callback (Content-Length or Chunked)
 * 8. Follow HTTP redirects (301, 302, 307) up to MAX_REDIRECTS
 * 9. Cleanup: always close TLS and TCP, even on error paths
 *
 * Uses the goto-cleanup pattern to ensure TLS context scrubbing and
 * TCP socket closure on every exit path including errors.
 *
 * @param[in]  url          Full HTTPS URL
 * @param[in]  range_start  First byte for Range header (only if has_range)
 * @param[in]  range_end    Last byte for Range header (-1 for EOF)
 * @param[in]  has_range    Nonzero to send Range header
 * @param[out] response     Parsed response metadata (may be NULL)
 * @param[in]  body_cb      Streaming body callback (may be NULL)
 * @param[in]  user_ctx     Opaque context for body_cb
 * @return 0 on success (HTTP 200/206), negative on error
 */
static int https_get_internal(const char *url,
                              uint64_t range_start, int64_t range_end,
                              int has_range,
                              vos3_http_response_t *response,
                              vos3_http_body_cb body_cb,
                              void *user_ctx)
{
    int ret;
    int tcp_fd = -1;
    int tls_initialized = 0;
    vos3_tls_ctx_t tls_ctx;

    /* Stack-allocated response if caller doesn't need one */
    vos3_http_response_t local_response;
    if (response == NULL) {
        response = &local_response;
    }
    memset(response, 0, sizeof(*response));
    response->content_length = -1;

    /* Current URL for redirect following */
    char current_url[VOS3_HTTP_MAX_URL];
    size_t url_len = strlen(url);
    if (url_len >= VOS3_HTTP_MAX_URL) {
        VOS3_ERROR("HTTP: URL too long (%zu bytes)", url_len);
        return E_INVAL;
    }
    memcpy(current_url, url, url_len);
    current_url[url_len] = '\0';

    /* ================================================================
     * Redirect loop (up to VOS3_HTTP_MAX_REDIRECTS hops)
     * ================================================================ */
    for (unsigned int redirect = 0; redirect <= VOS3_HTTP_MAX_REDIRECTS; redirect++) {

        /* Reset connection state for each attempt */
        tcp_fd = -1;
        tls_initialized = 0;
        memset(response, 0, sizeof(*response));
        response->content_length = -1;

        /* ----------------------------------------------------------------
         * Step 1: Parse URL
         * ---------------------------------------------------------------- */
        char host[VOS3_HTTP_MAX_HOST];
        uint16_t port;
        char path[VOS3_HTTP_MAX_PATH];

        ret = vos3_http_parse_url(current_url, host, &port, path);
        if (ret < 0) {
            VOS3_ERROR("HTTP: URL parse failed for '%s': %d", current_url, ret);
            return ret;
        }

        VOS3_INFO("HTTP: GET https://%s:%u%s%s",
                  host, (unsigned)port, path,
                  has_range ? " (Range)" : "");

        /* ----------------------------------------------------------------
         * Step 2: DNS resolution
         * ---------------------------------------------------------------- */
        uint32_t server_ip;
        ret = vos3_dns_resolve(host, &server_ip);
        if (ret < 0) {
            VOS3_ERROR("HTTP: DNS resolution failed for '%s': %d", host, ret);
            return ret;
        }

        VOS3_DEBUG("HTTP: Resolved '%s' -> %u.%u.%u.%u",
                   host,
                   (unsigned)((server_ip >> 24) & 0xFFU),
                   (unsigned)((server_ip >> 16) & 0xFFU),
                   (unsigned)((server_ip >> 8) & 0xFFU),
                   (unsigned)(server_ip & 0xFFU));

        /* ----------------------------------------------------------------
         * Step 3: Create TCP socket
         * ---------------------------------------------------------------- */
        tcp_fd = vos3_sys_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (tcp_fd < 0) {
            VOS3_ERROR("HTTP: Failed to create TCP socket: %d", tcp_fd);
            return E_NOMEM;
        }

        /* ----------------------------------------------------------------
         * Step 4: TCP connect
         *
         * NOTE: vos3_dns_resolve() returns IP in network byte order.
         *       Do NOT apply vos3_htonl() again.
         * ---------------------------------------------------------------- */
        struct sockaddr_in dest;
        dest.sin_family = AF_INET;
        dest.sin_port   = vos3_htons(port);
        dest.sin_addr   = server_ip;  /* Already network byte order from DNS */
        memset(dest.sin_zero, 0, sizeof(dest.sin_zero));

        ret = vos3_sys_connect(tcp_fd, (const struct sockaddr *)&dest,
                               (socklen_t)sizeof(dest));
        if (ret < 0) {
            VOS3_ERROR("HTTP: TCP connect to %s:%u failed: %d",
                      host, (unsigned)port, ret);
            goto cleanup;
        }

        VOS3_DEBUG("HTTP: TCP connected to %s:%u", host, (unsigned)port);

        /* ----------------------------------------------------------------
         * Step 5: TLS 1.3 handshake with SNI
         *
         * Fail-closed: any TLS error = abort, no plaintext fallback.
         * ---------------------------------------------------------------- */
        ret = vos3_tls_init(&tls_ctx);
        if (ret < 0) {
            VOS3_ERROR("HTTP: TLS context init failed: %d", ret);
            goto cleanup;
        }
        tls_initialized = 1;

        ret = vos3_tls_connect(&tls_ctx, tcp_fd, host);
        if (ret < 0) {
            VOS3_ERROR("HTTP: TLS handshake failed for '%s': %d "
                      "(fail-closed, no plaintext fallback)", host, ret);
            goto cleanup;
        }

        VOS3_INFO("HTTP: TLS handshake complete with '%s'", host);

        /* ----------------------------------------------------------------
         * Step 6: Build and send HTTP/1.1 GET request
         * ---------------------------------------------------------------- */
        char request_buf[HTTP_REQUEST_BUF_SIZE];
        int req_len = http_build_request(request_buf, sizeof(request_buf),
                                         host, port, path,
                                         range_start, range_end, has_range);
        if (req_len <= 0) {
            VOS3_ERROR("HTTP: Failed to build request (buffer overflow?)");
            ret = E_INVAL;
            goto cleanup;
        }

        VOS3_DEBUG("HTTP: Sending %d-byte request", req_len);

        int sent = vos3_tls_send(&tls_ctx, request_buf, (size_t)req_len);
        if (sent < 0 || sent != req_len) {
            VOS3_ERROR("HTTP: TLS send failed: sent=%d, expected=%d", sent, req_len);
            ret = E_IO;
            goto cleanup;
        }

        /* ----------------------------------------------------------------
         * Step 7: Receive response (status line + headers + body start)
         *
         * We accumulate data in a header buffer until we find the
         * \r\n\r\n end-of-headers marker, then parse.
         * ---------------------------------------------------------------- */
        char hdr_buf[HTTP_HEADER_BUF_SIZE];
        size_t hdr_len = 0;
        int headers_complete = 0;

        while (!headers_complete && hdr_len < HTTP_HEADER_BUF_SIZE) {
            size_t space = HTTP_HEADER_BUF_SIZE - hdr_len;
            if (space > HTTP_RECV_BUF_SIZE) {
                space = HTTP_RECV_BUF_SIZE;
            }

            int n = vos3_tls_recv(&tls_ctx, &hdr_buf[hdr_len], space);
            if (n <= 0) {
                VOS3_ERROR("HTTP: TLS recv failed during header read: %d", n);
                ret = (n == 0) ? E_IO : n;
                goto cleanup;
            }
            hdr_len += (size_t)n;

            /* Check if we have complete headers */
            if (memmem_str(hdr_buf, hdr_len, CRLFCRLF) != NULL) {
                headers_complete = 1;
            }
        }

        if (!headers_complete) {
            VOS3_ERROR("HTTP: Response headers exceed %u bytes",
                      (unsigned)HTTP_HEADER_BUF_SIZE);
            ret = E_PROTO;
            goto cleanup;
        }

        /* ----------------------------------------------------------------
         * Step 8: Parse status line
         * ---------------------------------------------------------------- */
        int status_consumed = http_parse_status_line(hdr_buf, hdr_len,
                                                     &response->status_code);
        if (status_consumed < 0) {
            VOS3_ERROR("HTTP: Failed to parse status line");
            ret = E_PROTO;
            goto cleanup;
        }

        VOS3_INFO("HTTP: Response status: %d", response->status_code);

        /* ----------------------------------------------------------------
         * Step 9: Parse response headers
         * ---------------------------------------------------------------- */
        const char *headers_start = &hdr_buf[status_consumed];
        size_t headers_avail = hdr_len - (size_t)status_consumed;

        int hdrs_consumed = http_parse_headers(headers_start, headers_avail,
                                               response);
        if (hdrs_consumed < 0) {
            VOS3_ERROR("HTTP: Failed to parse response headers");
            ret = E_PROTO;
            goto cleanup;
        }

        /* Calculate how many body bytes are already in the header buffer */
        size_t total_hdr_bytes = (size_t)status_consumed + (size_t)hdrs_consumed;
        const uint8_t *body_leftover = (const uint8_t *)&hdr_buf[total_hdr_bytes];
        size_t body_leftover_len = hdr_len - total_hdr_bytes;

        /* ----------------------------------------------------------------
         * Step 10: Handle redirects (301, 302, 307)
         * ---------------------------------------------------------------- */
        if (response->status_code == VOS3_HTTP_STATUS_MOVED_PERMANENTLY ||
            response->status_code == VOS3_HTTP_STATUS_FOUND ||
            response->status_code == VOS3_HTTP_STATUS_TEMPORARY_REDIRECT) {

            if (response->location[0] == '\0') {
                VOS3_ERROR("HTTP: Redirect %d without Location header",
                          response->status_code);
                ret = E_PROTO;
                goto cleanup;
            }

            if (redirect >= VOS3_HTTP_MAX_REDIRECTS) {
                VOS3_ERROR("HTTP: Too many redirects (>%u)",
                          (unsigned)VOS3_HTTP_MAX_REDIRECTS);
                ret = E_LOOP;
                goto cleanup;
            }

            VOS3_INFO("HTTP: Redirect %d -> %s",
                      response->status_code, response->location);

            /* Close current connection before following redirect */
            if (tls_initialized) {
                vos3_tls_close(&tls_ctx);
                vos3_tls_destroy(&tls_ctx);
                tls_initialized = 0;
            }
            if (tcp_fd >= 0) {
                vos3_sys_closesocket(tcp_fd);
                tcp_fd = -1;
            }

            /* Update current URL for next iteration */
            size_t loc_len = strlen(response->location);
            if (loc_len >= VOS3_HTTP_MAX_URL) {
                VOS3_ERROR("HTTP: Redirect URL too long");
                return E_INVAL;
            }
            memcpy(current_url, response->location, loc_len);
            current_url[loc_len] = '\0';

            continue;  /* Follow the redirect */
        }

        /* ----------------------------------------------------------------
         * Step 11: Verify expected status code
         * ---------------------------------------------------------------- */
        if (has_range) {
            /* Range request expects 206 Partial Content (but accept 200 too) */
            if (response->status_code != VOS3_HTTP_STATUS_PARTIAL &&
                response->status_code != VOS3_HTTP_STATUS_OK) {
                VOS3_WARN("HTTP: Expected 206/200 for range request, got %d",
                          response->status_code);
                ret = E_PROTO;
                goto cleanup;
            }
        } else {
            /* Normal GET expects 200 OK */
            if (response->status_code != VOS3_HTTP_STATUS_OK) {
                VOS3_WARN("HTTP: Expected 200, got %d",
                          response->status_code);
                /* Don't fail on non-200 -- let caller inspect status_code.
                 * But still read the body if present. For error responses
                 * (4xx, 5xx), return an error after body processing. */
            }
        }

        /* ----------------------------------------------------------------
         * Step 12: Stream response body
         * ---------------------------------------------------------------- */
        if (response->encoding == VOS3_HTTP_ENCODING_CHUNKED) {
            VOS3_DEBUG("HTTP: Receiving chunked body");
            ret = http_recv_body_chunked(&tls_ctx, response,
                                         body_cb, user_ctx,
                                         body_leftover, body_leftover_len);
        } else if (response->content_length > 0) {
            VOS3_DEBUG("HTTP: Receiving body (Content-Length: %lld)",
                       (long long)response->content_length);
            ret = http_recv_body_content_length(
                &tls_ctx, response,
                (uint64_t)response->content_length,
                body_cb, user_ctx,
                body_leftover, body_leftover_len);
        } else if (response->content_length == 0) {
            /* Empty body -- nothing to receive */
            VOS3_DEBUG("HTTP: Empty body (Content-Length: 0)");
            ret = 0;
        } else {
            /*
             * No Content-Length and not chunked: read until EOF.
             * This is common for HTTP/1.0 responses.
             */
            VOS3_DEBUG("HTTP: Receiving body until EOF (no Content-Length)");

            uint64_t eof_received = 0;
            uint8_t eof_buf[HTTP_RECV_BUF_SIZE];

            /* Deliver leftover first */
            if (body_leftover_len > 0) {
                eof_received += (uint64_t)body_leftover_len;
                response->body_received = eof_received;

                if (body_cb != NULL) {
                    int cb_ret = body_cb(body_leftover, body_leftover_len,
                                         eof_received, -1, user_ctx);
                    if (cb_ret < 0) {
                        ret = cb_ret;
                        goto cleanup;
                    }
                }
            }

            /* Read until TLS returns 0 (EOF) */
            for (;;) {
                int n = vos3_tls_recv(&tls_ctx, eof_buf, HTTP_RECV_BUF_SIZE);
                if (n == 0) {
                    break;  /* EOF */
                }
                if (n < 0) {
                    VOS3_WARN("HTTP: TLS recv error during EOF read: %d", n);
                    ret = n;
                    goto cleanup;
                }

                eof_received += (uint64_t)n;
                response->body_received = eof_received;

                if (body_cb != NULL) {
                    int cb_ret = body_cb(eof_buf, (size_t)n,
                                         eof_received, -1, user_ctx);
                    if (cb_ret < 0) {
                        ret = cb_ret;
                        goto cleanup;
                    }
                }
            }

            VOS3_INFO("HTTP: EOF body complete: %llu bytes",
                      (unsigned long long)eof_received);
            ret = 0;
        }

        if (ret < 0) {
            goto cleanup;
        }

        /* ----------------------------------------------------------------
         * Step 13: Determine final return value based on status code
         * ---------------------------------------------------------------- */
        if (response->status_code == VOS3_HTTP_STATUS_OK ||
            response->status_code == VOS3_HTTP_STATUS_PARTIAL) {
            ret = 0;  /* Success */
        } else if (response->status_code >= 400 &&
                   response->status_code < 500) {
            VOS3_WARN("HTTP: Client error %d", response->status_code);
            ret = E_INVAL;
        } else if (response->status_code >= 500) {
            VOS3_WARN("HTTP: Server error %d", response->status_code);
            ret = E_IO;
        } else {
            /* Unexpected status (1xx, 3xx not handled above) */
            VOS3_WARN("HTTP: Unexpected status %d", response->status_code);
            ret = E_PROTO;
        }

        goto cleanup;
    }

    /* Should not reach here (loop always breaks via goto cleanup or continue) */
    ret = E_LOOP;

    /* ================================================================
     * Cleanup: Always close TLS (scrub secrets) and TCP socket
     * ================================================================ */
cleanup:
    if (tls_initialized) {
        vos3_tls_close(&tls_ctx);
        vos3_tls_destroy(&tls_ctx);
    }
    if (tcp_fd >= 0) {
        vos3_sys_closesocket(tcp_fd);
    }

    if (ret == 0) {
        VOS3_INFO("HTTP: Request complete (status %d, %llu bytes)",
                  response->status_code,
                  (unsigned long long)response->body_received);
    } else {
        VOS3_WARN("HTTP: Request failed with error %d", ret);
    }

    return ret;
}

/* ============================================================================
 * PUBLIC API: HTTPS GET
 * ============================================================================ */

/**
 * @brief Perform an HTTPS GET request with streaming body callback
 *
 * Resolves the hostname via DNS, connects via TCP, performs TLS 1.3
 * handshake, sends HTTP/1.1 GET request, and streams the response
 * body to the callback function.
 *
 * Follows up to VOS3_HTTP_MAX_REDIRECTS HTTP redirects (301, 302, 307).
 * Only HTTPS URLs are accepted; HTTP URLs return -EINVAL immediately.
 * If the TLS handshake fails, returns error (fail-closed, no plaintext
 * fallback).
 *
 * @param[in]  url        Full HTTPS URL (e.g., "https://example.com/model.gguf")
 * @param[out] response   Parsed response metadata (may be NULL if not needed)
 * @param[in]  body_cb    Callback for body data (may be NULL to discard body)
 * @param[in]  user_ctx   Opaque context passed to body_cb
 * @return 0 on success (HTTP 200/206), negative on error:
 *         -EINVAL (-22): NULL URL, malformed URL, or HTTP (non-HTTPS) URL
 *         -ENOMEM (-12): Socket creation failed
 *         -EIO (-5): TLS send/recv failure or unexpected EOF
 *         -ECONNREFUSED (-111): TCP connect refused
 *         -ETIMEDOUT (-110): Connection or response timeout
 *         -EPROTO (-71): Malformed HTTP response
 *         -ELOOP (-40): Too many redirects
 */
int vos3_https_get(const char *url,
                   vos3_http_response_t *response,
                   vos3_http_body_cb body_cb,
                   void *user_ctx)
{
    if (url == NULL) {
        VOS3_ERROR("HTTP: vos3_https_get() called with NULL URL");
        return E_INVAL;
    }

    VOS3_INFO("HTTP: HTTPS GET %s", url);

    return https_get_internal(url,
                              0,    /* range_start (unused) */
                              -1,   /* range_end (unused) */
                              0,    /* has_range = false */
                              response, body_cb, user_ctx);
}

/* ============================================================================
 * PUBLIC API: HTTPS GET with Range
 * ============================================================================ */

/**
 * @brief Perform an HTTPS GET request with Range header (resumable)
 *
 * Same as vos3_https_get() but sends a Range header for partial content.
 * Expects HTTP 206 (Partial Content) response, though HTTP 200 is also
 * accepted (server may ignore Range).
 *
 * The Range header is formatted as:
 *   "Range: bytes=START-END"     (if range_end >= 0)
 *   "Range: bytes=START-"        (if range_end == -1, meaning to EOF)
 *
 * @param[in]  url            Full HTTPS URL
 * @param[in]  range_start    First byte position (0-based)
 * @param[in]  range_end      Last byte position (-1 for end of file)
 * @param[out] response       Parsed response metadata (may be NULL)
 * @param[in]  body_cb        Callback for body data (may be NULL)
 * @param[in]  user_ctx       Opaque context for body_cb
 * @return 0 on success (HTTP 206/200), negative on error
 */
int vos3_https_get_range(const char *url,
                         uint64_t range_start, int64_t range_end,
                         vos3_http_response_t *response,
                         vos3_http_body_cb body_cb,
                         void *user_ctx)
{
    if (url == NULL) {
        VOS3_ERROR("HTTP: vos3_https_get_range() called with NULL URL");
        return E_INVAL;
    }

    VOS3_INFO("HTTP: HTTPS GET Range bytes=%llu-%s %s",
              (unsigned long long)range_start,
              (range_end >= 0) ? "" : "(EOF)",
              url);

    return https_get_internal(url,
                              range_start,
                              range_end,
                              1,    /* has_range = true */
                              response, body_cb, user_ctx);
}
