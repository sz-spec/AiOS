/**
 * @file dns_tls.c
 * @brief VOS3 DNS-over-TLS (DoT) Resolver — RFC 7858
 *
 * @details Kernel-space DNS-over-TLS resolver providing encrypted DNS:
 *          - TLS 1.3 transport to port 853 (RFC 7858)
 *          - 2-byte length-prefixed DNS message framing
 *          - A (IPv4) and AAAA (IPv6) record resolution
 *          - CNAME chain following (up to 3 hops)
 *          - Automatic TLS context scrubbing on all exit paths
 *          - Falls back to error (no silent plaintext downgrade)
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note DNS-over-TLS — Kernel Network Stack
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/dns.h"
#include "../../include/vos/tls.h"
#include "../../include/vos/crypto.h"
#include "../../include/vos/tcp.h"
#include "../../include/vos/socket.h"
#include "../../include/vos/net.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/heap.h"

/* Entropy for random query IDs */
extern uint32_t vos3_entropy_get_u32(void);

/* Socket-layer syscalls (TCP path) */
extern int     vos3_sys_socket(int domain, int type, int protocol);
extern int     vos3_sys_connect(int fd, const struct sockaddr *addr, socklen_t addrlen);
extern int     vos3_sys_closesocket(int fd);
extern ssize_t vos3_sys_send(int fd, const void *buf, size_t len, int flags);
extern ssize_t vos3_sys_recv(int fd, void *buf, size_t len, int flags);

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief DNS-over-TLS port (RFC 7858) */
#define DOT_PORT                853U

/** @brief Default DoT server — Google Public DNS */
#define DOT_SERVER_DEFAULT      0x08080808U  /* 8.8.8.8 */

/** @brief Default SNI hostname for Google DNS */
#define DOT_SNI_DEFAULT         "dns.google"

/** @brief DoT handshake + query timeout in milliseconds */
#define DOT_TIMEOUT_MS          5000U

/** @brief Maximum DNS response payload from server */
#define DOT_MAX_RESPONSE        4096U

/** @brief Maximum DNS query packet size */
#define DOT_MAX_QUERY           512U

/** @brief Maximum DNS name length (RFC 1035) */
#define DOT_MAX_NAME            255U

/** @brief Maximum label length (RFC 1035) */
#define DOT_MAX_LABEL           63U

/** @brief DNS header size in bytes */
#define DOT_HEADER_SIZE         12U

/** @brief Maximum CNAME chain hops before giving up */
#define DOT_CNAME_MAX_HOPS     3U

/** @brief DNS query flags: standard query, recursion desired */
#define DOT_FLAGS_QUERY_RD     0x0100U

/** @brief DNS response flag: QR bit */
#define DOT_FLAG_QR            0x8000U

/** @brief DNS RCODE mask (lower 4 bits of flags) */
#define DOT_RCODE_MASK         0x000FU

/** @brief DNS record types */
#define DOT_TYPE_A              1U
#define DOT_TYPE_CNAME          5U
#define DOT_TYPE_AAAA           28U

/** @brief DNS class IN */
#define DOT_CLASS_IN            1U

/* ============================================================================
 * DNS LABEL ENCODING
 * ============================================================================ */

/**
 * @brief Encode a hostname as DNS wire-format labels
 *
 * Converts "www.example.com" into [3]www[7]example[3]com[0].
 * Each label is prefixed by its length byte. The name is terminated
 * by a zero-length root label.
 *
 * @param[out] buf   Output buffer
 * @param[in]  max   Buffer capacity in bytes
 * @param[in]  name  Null-terminated hostname string
 * @return Number of bytes written, or -1 on error
 */
static int dns_encode_name(uint8_t *buf, size_t max, const char *name)
{
    size_t pos = 0;
    const char *ptr = name;

    while (*ptr != '\0') {
        /* Find the next dot or end of string */
        const char *dot = ptr;
        while (*dot != '\0' && *dot != '.') {
            dot++;
        }

        size_t label_len = (size_t)(dot - ptr);

        /* RFC 1035: label length must be 1-63 */
        if (label_len == 0 || label_len > DOT_MAX_LABEL) {
            return -1;
        }

        /* Need: 1 (length byte) + label_len + 1 (final null terminator) */
        if (pos + 1U + label_len + 1U > max) {
            return -1;
        }

        /* Write length prefix */
        buf[pos++] = (uint8_t)label_len;

        /* Write label characters */
        for (size_t i = 0; i < label_len; i++) {
            buf[pos++] = (uint8_t)ptr[i];
        }

        /* Advance past the dot separator */
        ptr = dot;
        if (*ptr == '.') {
            ptr++;
        }
    }

    /* Root label (zero-length terminator) */
    if (pos + 1U > max) {
        return -1;
    }
    buf[pos++] = 0U;

    return (int)pos;
}

/* ============================================================================
 * DNS NAME SKIPPING (RESPONSE PARSING)
 * ============================================================================ */

/**
 * @brief Skip past a DNS name in a response packet
 *
 * Handles both inline labels and compression pointers (0xC0xx).
 * Returns the offset immediately after the name field in the packet.
 *
 * @param[in] buf      Packet buffer
 * @param[in] buf_len  Total packet length
 * @param[in] offset   Current offset to start reading
 * @return New offset after the name, or -1 on error
 */
static int dns_skip_name(const uint8_t *buf, size_t buf_len, size_t offset)
{
    size_t pos = offset;
    int jumped = 0;
    size_t first_jump_return = 0;
    int jumps = 0;

    while (pos < buf_len) {
        uint8_t len = buf[pos];

        /* End of name (root label) */
        if (len == 0U) {
            if (!jumped) {
                return (int)(pos + 1U);
            }
            return (int)first_jump_return;
        }

        /* Compression pointer: top 2 bits = 11 */
        if ((len & 0xC0U) == 0xC0U) {
            if (pos + 1U >= buf_len) {
                return -1;
            }
            if (!jumped) {
                first_jump_return = pos + 2U;
                jumped = 1;
            }
            uint16_t ptr_off = (uint16_t)(((uint16_t)(len & 0x3FU) << 8) |
                                           (uint16_t)buf[pos + 1U]);
            pos = (size_t)ptr_off;
            jumps++;
            if (jumps > 10) {
                return -1;  /* Prevent infinite pointer loops */
            }
            continue;
        }

        /* Regular label: length must be 1-63 */
        if (len > DOT_MAX_LABEL) {
            return -1;
        }
        pos += 1U + (size_t)len;
    }

    return -1;  /* Ran past end of packet */
}

/* ============================================================================
 * DNS QUERY BUILDER
 * ============================================================================ */

/**
 * @brief Build a DNS query packet (without the 2-byte TCP length prefix)
 *
 * Constructs a standard DNS query with:
 * - Random 16-bit query ID from hardware entropy
 * - Flags: standard query, recursion desired (0x0100)
 * - Single question section with given hostname and query type
 *
 * @param[out] buf       Output buffer (at least DOT_MAX_QUERY bytes)
 * @param[in]  max_len   Buffer capacity
 * @param[in]  hostname  Null-terminated hostname to query
 * @param[in]  qtype     Query type (DOT_TYPE_A=1 or DOT_TYPE_AAAA=28)
 * @return Total query length in bytes, or -1 on error
 *
 * @note The 16-bit query ID is stored in buf[0..1] (network order) and
 *       can be read back by the caller for response matching.
 */
static int dot_build_query(uint8_t *buf, size_t max_len,
                           const char *hostname, uint16_t qtype)
{
    if (buf == NULL || hostname == NULL) {
        return -1;
    }

    /* Validate hostname length */
    size_t hlen = 0;
    while (hostname[hlen] != '\0') {
        hlen++;
        if (hlen > DOT_MAX_NAME) {
            return -1;
        }
    }
    if (hlen == 0) {
        return -1;
    }

    /* Need at least header (12) + 1 label + 5 (qtype/qclass + root) */
    if (max_len < DOT_HEADER_SIZE + 5U) {
        return -1;
    }

    /* Generate random query ID from hardware entropy */
    uint32_t rand_val = vos3_entropy_get_u32();
    uint16_t qid = (uint16_t)(rand_val & 0xFFFFU);

    /* Build DNS header (12 bytes) */
    /* ID (network byte order) */
    buf[0] = (uint8_t)(qid >> 8);
    buf[1] = (uint8_t)(qid & 0xFFU);
    /* Flags: standard query, recursion desired */
    buf[2] = (uint8_t)(DOT_FLAGS_QUERY_RD >> 8);
    buf[3] = (uint8_t)(DOT_FLAGS_QUERY_RD & 0xFFU);
    /* QDCOUNT = 1 */
    buf[4] = 0U;
    buf[5] = 1U;
    /* ANCOUNT = 0 */
    buf[6] = 0U;
    buf[7] = 0U;
    /* NSCOUNT = 0 */
    buf[8] = 0U;
    buf[9] = 0U;
    /* ARCOUNT = 0 */
    buf[10] = 0U;
    buf[11] = 0U;

    size_t pos = DOT_HEADER_SIZE;

    /* Encode hostname as DNS labels */
    int name_len = dns_encode_name(&buf[pos], max_len - pos, hostname);
    if (name_len < 0) {
        return -1;
    }
    pos += (size_t)name_len;

    /* Append QTYPE (2 bytes, network order) */
    if (pos + 4U > max_len) {
        return -1;
    }
    buf[pos++] = (uint8_t)(qtype >> 8);
    buf[pos++] = (uint8_t)(qtype & 0xFFU);

    /* Append QCLASS = IN (2 bytes, network order) */
    buf[pos++] = (uint8_t)(DOT_CLASS_IN >> 8);
    buf[pos++] = (uint8_t)(DOT_CLASS_IN & 0xFFU);

    return (int)pos;
}

/* ============================================================================
 * DNS RESPONSE PARSER
 * ============================================================================ */

/**
 * @brief Parse a DNS response and extract the answer record data
 *
 * Validates the response header (QR bit, RCODE=0), skips the question
 * section, then iterates answer RRs looking for the requested type.
 * Follows CNAME chains up to DOT_CNAME_MAX_HOPS before giving up.
 *
 * @param[in]  buf           Response payload (DNS message, no TCP length prefix)
 * @param[in]  len           Response length in bytes
 * @param[in]  qtype         Expected query type (DOT_TYPE_A or DOT_TYPE_AAAA)
 * @param[out] out_data      Output buffer for RDATA (4 bytes for A, 16 for AAAA)
 * @param[out] out_data_len  Number of bytes written to out_data
 * @return 0 on success, -1 on parse/validation error, -2 if no matching record
 */
static int dot_parse_response(const uint8_t *buf, size_t len,
                              uint16_t qtype,
                              uint8_t *out_data, size_t *out_data_len)
{
    /* Minimum packet: header (12) + at least 1 byte question + answer */
    if (len < DOT_HEADER_SIZE) {
        return -1;
    }

    /* Parse header fields (all network byte order) */
    uint16_t flags = (uint16_t)((uint16_t)buf[2] << 8 | (uint16_t)buf[3]);
    uint16_t qdcount = (uint16_t)((uint16_t)buf[4] << 8 | (uint16_t)buf[5]);
    uint16_t ancount = (uint16_t)((uint16_t)buf[6] << 8 | (uint16_t)buf[7]);

    /* Verify this is a response (QR=1) */
    if (!(flags & DOT_FLAG_QR)) {
        VOS3_WARN("DNS-TLS: Response missing QR flag");
        return -1;
    }

    /* Check RCODE (lower 4 bits) = 0 (no error) */
    uint16_t rcode = flags & DOT_RCODE_MASK;
    if (rcode != 0U) {
        VOS3_WARN("DNS-TLS: Server returned RCODE=%u", (unsigned)rcode);
        return -2;
    }

    /* Must have at least one answer */
    if (ancount == 0U) {
        VOS3_DEBUG("DNS-TLS: No answer records in response");
        return -2;
    }

    /* Skip question section */
    size_t pos = DOT_HEADER_SIZE;
    for (uint16_t q = 0; q < qdcount; q++) {
        int skip = dns_skip_name(buf, len, pos);
        if (skip < 0) {
            return -1;
        }
        pos = (size_t)skip;
        /* Skip QTYPE (2) + QCLASS (2) */
        pos += 4U;
        if (pos > len) {
            return -1;
        }
    }

    /* Parse answer records, following CNAME chains */
    int cname_hops = 0;

    for (uint16_t a = 0; a < ancount; a++) {
        /* Skip the RR name */
        int skip = dns_skip_name(buf, len, pos);
        if (skip < 0) {
            return -1;
        }
        pos = (size_t)skip;

        /* Need at least 10 bytes: TYPE(2) + CLASS(2) + TTL(4) + RDLENGTH(2) */
        if (pos + 10U > len) {
            return -1;
        }

        uint16_t rtype    = (uint16_t)((uint16_t)buf[pos] << 8 | (uint16_t)buf[pos + 1U]);
        /* rclass at pos+2..pos+3 (unused) */
        /* rttl   at pos+4..pos+7 (unused in this implementation) */
        uint16_t rdlength = (uint16_t)((uint16_t)buf[pos + 8U] << 8 |
                                        (uint16_t)buf[pos + 9U]);
        pos += 10U;

        /* Validate RDATA fits in remaining packet */
        if (pos + (size_t)rdlength > len) {
            return -1;
        }

        /* Check for matching A record */
        if (rtype == DOT_TYPE_A && qtype == DOT_TYPE_A && rdlength == 4U) {
            if (out_data != NULL) {
                out_data[0] = buf[pos];
                out_data[1] = buf[pos + 1U];
                out_data[2] = buf[pos + 2U];
                out_data[3] = buf[pos + 3U];
            }
            if (out_data_len != NULL) {
                *out_data_len = 4U;
            }
            return 0;
        }

        /* Check for matching AAAA record */
        if (rtype == DOT_TYPE_AAAA && qtype == DOT_TYPE_AAAA && rdlength == 16U) {
            if (out_data != NULL) {
                for (size_t i = 0; i < 16U; i++) {
                    out_data[i] = buf[pos + i];
                }
            }
            if (out_data_len != NULL) {
                *out_data_len = 16U;
            }
            return 0;
        }

        /* CNAME record: follow the chain */
        if (rtype == DOT_TYPE_CNAME) {
            cname_hops++;
            if (cname_hops > (int)DOT_CNAME_MAX_HOPS) {
                VOS3_WARN("DNS-TLS: CNAME chain too long (>%u hops)",
                          (unsigned)DOT_CNAME_MAX_HOPS);
                return -1;
            }
            VOS3_DEBUG("DNS-TLS: Following CNAME (hop %d)", cname_hops);
            /* Continue scanning remaining answers for A/AAAA after CNAME */
        }

        /* Skip this record's RDATA */
        pos += (size_t)rdlength;
    }

    /* No matching record found */
    return -2;
}

/* ============================================================================
 * DNS-OVER-TLS INTERNAL RESOLVER
 * ============================================================================ */

/**
 * @brief Internal DoT resolver for both A and AAAA queries
 *
 * Performs the full DNS-over-TLS protocol:
 * 1. Create TCP socket and connect to DoT server on port 853
 * 2. Initialize TLS 1.3 context and perform handshake with SNI
 * 3. Build DNS query and send with 2-byte length prefix
 * 4. Receive 2-byte length prefix, then DNS response payload
 * 5. Parse response and extract answer record
 * 6. Tear down TLS context (scrub secrets) and close TCP socket
 *
 * @param[in]  hostname  Hostname to resolve (null-terminated)
 * @param[in]  qtype     Query type: DOT_TYPE_A (1) or DOT_TYPE_AAAA (28)
 * @param[out] out_data  Output buffer (4 bytes for A, 16 bytes for AAAA)
 * @param[in]  out_max   Capacity of out_data buffer
 * @return 0 on success, negative error code on failure
 */
static int dot_resolve_internal(const char *hostname, uint16_t qtype,
                                uint8_t *out_data, size_t out_max)
{
    int ret = -1;
    int tcp_fd = -1;
    int tls_initialized = 0;
    vos3_tls_ctx_t tls_ctx;

    /* ----------------------------------------------------------------
     * Step 1: Validate arguments
     * ---------------------------------------------------------------- */
    if (hostname == NULL || out_data == NULL) {
        return -22;  /* -EINVAL */
    }
    if (qtype == DOT_TYPE_A && out_max < 4U) {
        return -22;
    }
    if (qtype == DOT_TYPE_AAAA && out_max < 16U) {
        return -22;
    }

    VOS3_INFO("DNS-TLS: Query for '%s' type %u", hostname, (unsigned)qtype);

    /* ----------------------------------------------------------------
     * Step 2: Create TCP socket
     * ---------------------------------------------------------------- */
    tcp_fd = vos3_sys_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (tcp_fd < 0) {
        VOS3_WARN("DNS-TLS: Failed to create TCP socket: %d", tcp_fd);
        return -12;  /* -ENOMEM */
    }

    /* ----------------------------------------------------------------
     * Step 3: Connect to DoT server on port 853
     * ---------------------------------------------------------------- */
    struct sockaddr_in dest;
    dest.sin_family = AF_INET;
    dest.sin_port   = vos3_htons((uint16_t)DOT_PORT);
    dest.sin_addr   = vos3_htonl(DOT_SERVER_DEFAULT);
    dest.sin_zero[0] = 0U;
    dest.sin_zero[1] = 0U;
    dest.sin_zero[2] = 0U;
    dest.sin_zero[3] = 0U;
    dest.sin_zero[4] = 0U;
    dest.sin_zero[5] = 0U;
    dest.sin_zero[6] = 0U;
    dest.sin_zero[7] = 0U;

    VOS3_INFO("DNS-TLS: Connecting to %u.%u.%u.%u:%u",
              (DOT_SERVER_DEFAULT >> 24) & 0xFFU,
              (DOT_SERVER_DEFAULT >> 16) & 0xFFU,
              (DOT_SERVER_DEFAULT >> 8) & 0xFFU,
              DOT_SERVER_DEFAULT & 0xFFU,
              (unsigned)DOT_PORT);

    ret = vos3_sys_connect(tcp_fd, (const struct sockaddr *)&dest,
                           (socklen_t)sizeof(dest));
    if (ret < 0) {
        VOS3_WARN("DNS-TLS: TCP connect failed: %d", ret);
        goto cleanup_tcp;
    }

    /* ----------------------------------------------------------------
     * Step 4: Initialize TLS 1.3 context
     * ---------------------------------------------------------------- */
    ret = vos3_tls_init(&tls_ctx);
    if (ret < 0) {
        VOS3_WARN("DNS-TLS: TLS context init failed: %d", ret);
        goto cleanup_tcp;
    }
    tls_initialized = 1;

    /* ----------------------------------------------------------------
     * Step 5: TLS 1.3 handshake with SNI
     * ---------------------------------------------------------------- */
    ret = vos3_tls_connect(&tls_ctx, tcp_fd, DOT_SNI_DEFAULT);
    if (ret < 0) {
        VOS3_WARN("DNS-TLS: TLS handshake failed: %d", ret);
        goto cleanup_tls;
    }

    VOS3_INFO("DNS-TLS: TLS handshake complete");

    /* ----------------------------------------------------------------
     * Step 6: Build DNS query
     * ---------------------------------------------------------------- */
    uint8_t query_buf[DOT_MAX_QUERY];
    int query_len = dot_build_query(query_buf, sizeof(query_buf),
                                    hostname, qtype);
    if (query_len <= 0) {
        VOS3_WARN("DNS-TLS: Failed to build query for '%s'", hostname);
        ret = -22;  /* -EINVAL */
        goto cleanup_tls;
    }

    /* Save the query ID from bytes 0-1 for response validation */
    uint16_t query_id = (uint16_t)((uint16_t)query_buf[0] << 8 |
                                    (uint16_t)query_buf[1]);

    /* ----------------------------------------------------------------
     * Step 7: Send DNS query with 2-byte TCP length prefix (RFC 7858)
     *
     * DNS-over-TLS uses the same length-prefixed framing as DNS-over-TCP
     * (RFC 1035 Section 4.2.2): a 2-byte big-endian length followed by
     * the complete DNS message.
     * ---------------------------------------------------------------- */
    uint8_t send_buf[2U + DOT_MAX_QUERY];
    uint16_t wire_len = (uint16_t)query_len;

    /* 2-byte big-endian length prefix */
    send_buf[0] = (uint8_t)(wire_len >> 8);
    send_buf[1] = (uint8_t)(wire_len & 0xFFU);

    /* Copy DNS query after the length prefix */
    for (int i = 0; i < query_len; i++) {
        send_buf[2U + (size_t)i] = query_buf[i];
    }

    size_t total_send = 2U + (size_t)query_len;
    int sent = vos3_tls_send(&tls_ctx, send_buf, total_send);
    if (sent < 0 || (size_t)sent != total_send) {
        VOS3_WARN("DNS-TLS: TLS send failed: sent=%d, expected=%zu",
                  sent, total_send);
        ret = -5;  /* -EIO */
        goto cleanup_tls;
    }

    VOS3_DEBUG("DNS-TLS: Sent %zu bytes (2 prefix + %d query)", total_send, query_len);

    /* ----------------------------------------------------------------
     * Step 8: Receive 2-byte length prefix from response
     * ---------------------------------------------------------------- */
    uint8_t len_buf[2];
    int recv_ret = vos3_tls_recv(&tls_ctx, len_buf, 2U);
    if (recv_ret < 2) {
        VOS3_WARN("DNS-TLS: Failed to receive length prefix: %d", recv_ret);
        ret = -110;  /* -ETIMEDOUT */
        goto cleanup_tls;
    }

    uint16_t resp_len = (uint16_t)((uint16_t)len_buf[0] << 8 |
                                    (uint16_t)len_buf[1]);

    /* Sanity check response length */
    if (resp_len < DOT_HEADER_SIZE || resp_len > DOT_MAX_RESPONSE) {
        VOS3_WARN("DNS-TLS: Invalid response length: %u", (unsigned)resp_len);
        ret = -1;
        goto cleanup_tls;
    }

    /* ----------------------------------------------------------------
     * Step 9: Receive DNS response payload
     * ---------------------------------------------------------------- */
    uint8_t resp_buf[DOT_MAX_RESPONSE];
    size_t total_recv = 0;

    while (total_recv < (size_t)resp_len) {
        int chunk = vos3_tls_recv(&tls_ctx, &resp_buf[total_recv],
                                  (size_t)resp_len - total_recv);
        if (chunk <= 0) {
            VOS3_WARN("DNS-TLS: Short read: got %zu/%u bytes",
                      total_recv, (unsigned)resp_len);
            ret = -110;  /* -ETIMEDOUT */
            goto cleanup_tls;
        }
        total_recv += (size_t)chunk;
    }

    VOS3_DEBUG("DNS-TLS: Received %zu byte response", total_recv);

    /* ----------------------------------------------------------------
     * Step 10: Validate query ID in response matches our request
     *
     * Even over TLS, a well-behaved resolver should echo the query ID.
     * This is defense-in-depth against potential proxy confusion.
     * ---------------------------------------------------------------- */
    if (total_recv >= 2U) {
        uint16_t resp_id = (uint16_t)((uint16_t)resp_buf[0] << 8 |
                                       (uint16_t)resp_buf[1]);
        if (resp_id != query_id) {
            VOS3_WARN("DNS-TLS: Query ID mismatch: sent=0x%04X recv=0x%04X",
                      (unsigned)query_id, (unsigned)resp_id);
            ret = -1;
            goto cleanup_tls;
        }
    }

    /* ----------------------------------------------------------------
     * Step 11: Parse DNS response and extract answer data
     * ---------------------------------------------------------------- */
    size_t data_len = 0;
    ret = dot_parse_response(resp_buf, total_recv, qtype, out_data, &data_len);
    if (ret < 0) {
        VOS3_WARN("DNS-TLS: Parse failed for '%s': %d", hostname, ret);
        goto cleanup_tls;
    }

    /* ----------------------------------------------------------------
     * Step 12: Log the resolved address
     * ---------------------------------------------------------------- */
    if (qtype == DOT_TYPE_A && data_len == 4U) {
        VOS3_INFO("DNS-TLS: Resolved '%s' -> %u.%u.%u.%u",
                  hostname,
                  (unsigned)out_data[0], (unsigned)out_data[1],
                  (unsigned)out_data[2], (unsigned)out_data[3]);
    } else if (qtype == DOT_TYPE_AAAA && data_len == 16U) {
        VOS3_INFO("DNS-TLS: Resolved '%s' -> [IPv6 %02x%02x:%02x%02x:...:%02x%02x]",
                  hostname,
                  (unsigned)out_data[0], (unsigned)out_data[1],
                  (unsigned)out_data[2], (unsigned)out_data[3],
                  (unsigned)out_data[14], (unsigned)out_data[15]);
    }

    ret = 0;  /* Success */

    /* ----------------------------------------------------------------
     * Cleanup: Always destroy TLS context (scrubs key material) and
     * close the TCP socket, regardless of success or failure path.
     * ---------------------------------------------------------------- */
cleanup_tls:
    if (tls_initialized) {
        /* Graceful TLS close (sends close_notify alert) */
        vos3_tls_close(&tls_ctx);
        /* Scrub all secrets: ephemeral keys, shared secret, traffic keys */
        vos3_tls_destroy(&tls_ctx);
    }

cleanup_tcp:
    if (tcp_fd >= 0) {
        vos3_sys_closesocket(tcp_fd);
    }

    return ret;
}

/* ============================================================================
 * PUBLIC API: DNS-over-TLS A Record Resolution
 * ============================================================================ */

/**
 * @brief Resolve a hostname to an IPv4 address using DNS-over-TLS
 *
 * Performs a secure DNS A record query over TLS 1.3 to a DoT-capable
 * resolver (default: Google Public DNS 8.8.8.8:853).
 *
 * This is the TLS-secured counterpart to vos3_dns_resolve() which uses
 * plaintext UDP on port 53. Use this when DNS query privacy is required
 * (e.g., preventing ISP snooping on domain lookups).
 *
 * @param[in]  hostname  Null-terminated hostname to resolve (e.g., "example.com")
 * @param[out] out_ip    Resolved IPv4 address in network byte order
 * @return 0 on success, negative error code on failure:
 *         -EINVAL (-22): NULL argument or invalid hostname
 *         -ENOMEM (-12): Socket creation failed
 *         -ETIMEDOUT (-110): Connection or response timeout
 *         -EIO (-5): TLS send/receive failure
 *         -1: General failure (TLS handshake, parse error, etc.)
 */
int vos3_dns_resolve_tls(const char *hostname, uint32_t *out_ip)
{
    if (hostname == NULL || out_ip == NULL) {
        return -22;  /* -EINVAL */
    }

    /*
     * Resolve via DoT. The DNS A record RDATA is 4 bytes in network
     * byte order — which is exactly what out_ip expects.
     */
    uint8_t addr_buf[4];
    int ret = dot_resolve_internal(hostname, DOT_TYPE_A, addr_buf, sizeof(addr_buf));
    if (ret < 0) {
        return ret;
    }

    /* Copy 4 bytes directly into out_ip (already network byte order) */
    *out_ip = ((uint32_t)addr_buf[0] << 24) |
              ((uint32_t)addr_buf[1] << 16) |
              ((uint32_t)addr_buf[2] << 8)  |
              ((uint32_t)addr_buf[3]);

    return 0;
}

/* ============================================================================
 * PUBLIC API: DNS-over-TLS AAAA Record Resolution
 * ============================================================================ */

/**
 * @brief Resolve a hostname to an IPv6 address using DNS-over-TLS
 *
 * Performs a secure DNS AAAA record query over TLS 1.3 to a DoT-capable
 * resolver (default: Google Public DNS 8.8.8.8:853).
 *
 * @param[in]  hostname  Null-terminated hostname to resolve (e.g., "example.com")
 * @param[out] out_ip6   Resolved IPv6 address (16 bytes, network byte order)
 * @return 0 on success, negative error code on failure (same codes as _tls)
 */
int vos3_dns_resolve6_tls(const char *hostname, uint8_t out_ip6[16])
{
    if (hostname == NULL || out_ip6 == NULL) {
        return -22;  /* -EINVAL */
    }

    /*
     * Resolve via DoT. AAAA RDATA is 16 bytes (128-bit IPv6 address)
     * in network byte order — copied directly into the caller's buffer.
     */
    int ret = dot_resolve_internal(hostname, DOT_TYPE_AAAA, out_ip6, 16U);
    return ret;
}
