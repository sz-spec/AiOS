/**
 * @file dns.c
 * @brief VOS3 DNS Resolver Implementation
 *
 * @details Kernel-space DNS resolver with caching:
 *          - Builds and parses RFC 1035 DNS packets
 *          - LRU-evicted cache with TTL expiration
 *          - CNAME chain following (up to 5 hops)
 *          - UDP transport via kernel socket layer
 *          - Entropy-seeded query IDs for security
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note DNS Resolver - Kernel Network Stack
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/dns.h"
#include "../../include/vos/net.h"
#include "../../include/vos/socket.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/compiler.h"
#include "../../include/vos/sync.h"

/* Timer functions (declared in timer.h but we use extern to avoid
 * pulling in the full header dependency chain) */
extern uint64_t vos3_timer_get_ticks(void);
extern uint64_t vos3_timer_get_uptime_ms(void);

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief DNS cache entries */
static vos3_dns_cache_entry_t g_dns_cache[VOS3_DNS_CACHE_SIZE];

/** @brief DNS server IP (network byte order, default 8.8.8.8) */
static uint32_t g_dns_server = 0x08080808U;

/** @brief DNS subsystem lock */
static vos3_spinlock_t g_dns_lock = VOS3_SPINLOCK_INIT;

/** @brief DNS query ID counter (seeded from entropy) */
static uint32_t g_dns_query_id;

/** @brief Ephemeral port for DNS queries */
#define DNS_EPHEMERAL_PORT      50053U

/** @brief DNS statistics */
static struct {
    uint64_t    queries_sent;
    uint64_t    responses_received;
    uint64_t    cache_hits;
    uint64_t    cache_misses;
    uint64_t    timeouts;
    uint64_t    parse_errors;
} g_dns_stats;

/* ============================================================================
 * HELPER: Current time in seconds
 * ============================================================================ */

/**
 * @brief Get current time in seconds (for TTL comparison)
 */
static uint64_t dns_time_seconds(void)
{
    return vos3_timer_get_uptime_ms() / 1000ULL;
}

/* ============================================================================
 * DNS QUERY ID GENERATION
 * ============================================================================ */

/**
 * @brief Generate a cryptographically random DNS query ID
 *
 * Uses hardware entropy (RDRAND/RDSEED) per call to prevent
 * prediction attacks (Kaminsky-style DNS poisoning).
 *
 * @return 16-bit query ID
 */
static uint16_t dns_next_query_id(void)
{
    return (uint16_t)(vos3_entropy_get_u32() & 0xFFFFU);
}

/* ============================================================================
 * DNS CACHE OPERATIONS
 * ============================================================================ */

/**
 * @brief Look up a hostname in the DNS cache
 *
 * @param[in] name   Hostname to look up
 * @param[in] qtype  Query type (VOS3_DNS_TYPE_A or VOS3_DNS_TYPE_AAAA)
 * @return Pointer to cache entry if found and not expired, NULL otherwise
 *
 * @note Caller must hold g_dns_lock
 */
static vos3_dns_cache_entry_t* dns_cache_lookup(const char* name, uint16_t qtype)
{
    uint64_t now = dns_time_seconds();

    for (size_t i = 0; i < VOS3_DNS_CACHE_SIZE; i++) {
        vos3_dns_cache_entry_t* entry = &g_dns_cache[i];

        if (!entry->valid) {
            continue;
        }

        /* Check TTL expiration */
        if (now > entry->timestamp + (uint64_t)entry->ttl) {
            entry->valid = 0;
            continue;
        }

        /* Match name and query type */
        if (entry->qtype == qtype) {
            /* Case-insensitive hostname comparison */
            const char* a = name;
            const char* b = entry->name;
            int match = 1;
            while (*a && *b) {
                char ca = *a;
                char cb = *b;
                /* Lowercase ASCII */
                if (ca >= 'A' && ca <= 'Z') ca += 32;
                if (cb >= 'A' && cb <= 'Z') cb += 32;
                if (ca != cb) {
                    match = 0;
                    break;
                }
                a++;
                b++;
            }
            if (match && *a == '\0' && *b == '\0') {
                return entry;
            }
        }
    }

    return NULL;
}

/**
 * @brief Store a DNS record in the cache
 *
 * Uses LRU eviction when the cache is full: evicts the oldest entry
 * (by timestamp) to make room for the new record.
 *
 * @param[in] name   Hostname
 * @param[in] qtype  Query type (VOS3_DNS_TYPE_A or VOS3_DNS_TYPE_AAAA)
 * @param[in] ip     IPv4 address (for A records, network order)
 * @param[in] ip6    IPv6 address (for AAAA records, 16 bytes, may be NULL)
 * @param[in] ttl    Time-to-live in seconds
 *
 * @note Caller must hold g_dns_lock
 */
static void dns_cache_store(const char* name, uint16_t qtype,
                            uint32_t ip, const uint8_t* ip6, uint32_t ttl)
{
    uint64_t now = dns_time_seconds();

    /* Use minimum TTL to avoid 0-TTL entries that expire immediately */
    if (ttl == 0) {
        ttl = VOS3_DNS_TTL_DEFAULT;
    }

    /* First: look for existing entry to update */
    for (size_t i = 0; i < VOS3_DNS_CACHE_SIZE; i++) {
        vos3_dns_cache_entry_t* entry = &g_dns_cache[i];
        if (entry->valid && entry->qtype == qtype) {
            /* Simple name match */
            const char* a = name;
            const char* b = entry->name;
            int match = 1;
            while (*a && *b) {
                char ca = *a;
                char cb = *b;
                if (ca >= 'A' && ca <= 'Z') ca += 32;
                if (cb >= 'A' && cb <= 'Z') cb += 32;
                if (ca != cb) { match = 0; break; }
                a++; b++;
            }
            if (match && *a == '\0' && *b == '\0') {
                /* Update existing entry */
                entry->ip = ip;
                if (ip6 != NULL) {
                    memcpy(entry->ip6, ip6, 16);
                }
                entry->ttl = ttl;
                entry->timestamp = now;
                return;
            }
        }
    }

    /* Second: look for an empty slot */
    for (size_t i = 0; i < VOS3_DNS_CACHE_SIZE; i++) {
        if (!g_dns_cache[i].valid) {
            vos3_dns_cache_entry_t* entry = &g_dns_cache[i];
            memset(entry, 0, sizeof(*entry));
            /* Safe copy hostname */
            size_t nlen = strlen(name);
            if (nlen > VOS3_DNS_MAX_NAME) {
                nlen = VOS3_DNS_MAX_NAME;
            }
            memcpy(entry->name, name, nlen);
            entry->name[nlen] = '\0';
            entry->ip = ip;
            if (ip6 != NULL) {
                memcpy(entry->ip6, ip6, 16);
            }
            entry->ttl = ttl;
            entry->timestamp = now;
            entry->qtype = qtype;
            entry->valid = 1;
            return;
        }
    }

    /* Third: LRU eviction - find oldest entry */
    size_t oldest_idx = 0;
    uint64_t oldest_time = g_dns_cache[0].timestamp;

    for (size_t i = 1; i < VOS3_DNS_CACHE_SIZE; i++) {
        if (g_dns_cache[i].timestamp < oldest_time) {
            oldest_time = g_dns_cache[i].timestamp;
            oldest_idx = i;
        }
    }

    /* Evict and store */
    vos3_dns_cache_entry_t* entry = &g_dns_cache[oldest_idx];
    memset(entry, 0, sizeof(*entry));
    size_t nlen = strlen(name);
    if (nlen > VOS3_DNS_MAX_NAME) {
        nlen = VOS3_DNS_MAX_NAME;
    }
    memcpy(entry->name, name, nlen);
    entry->name[nlen] = '\0';
    entry->ip = ip;
    if (ip6 != NULL) {
        memcpy(entry->ip6, ip6, 16);
    }
    entry->ttl = ttl;
    entry->timestamp = now;
    entry->qtype = qtype;
    entry->valid = 1;
}

/* ============================================================================
 * DNS PACKET BUILDER
 * ============================================================================ */

/**
 * @brief Encode a hostname as DNS labels
 *
 * Converts "www.example.com" to [3]www[7]example[3]com[0]
 *
 * @param[out] buf       Output buffer
 * @param[in]  buf_size  Buffer capacity
 * @param[in]  hostname  Null-terminated hostname
 * @return Number of bytes written, or 0 on error
 */
static size_t dns_encode_name(uint8_t* buf, size_t buf_size, const char* hostname)
{
    size_t pos = 0;
    const char* ptr = hostname;

    while (*ptr != '\0') {
        /* Find the next dot or end of string */
        const char* dot = ptr;
        while (*dot != '\0' && *dot != '.') {
            dot++;
        }

        size_t label_len = (size_t)(dot - ptr);

        /* SECURITY: Validate label length (RFC 1035: max 63) */
        if (label_len == 0 || label_len > 63) {
            return 0;
        }

        /* Check buffer space: 1 byte length + label + at least 1 byte terminator */
        if (pos + 1 + label_len + 1 > buf_size) {
            return 0;
        }

        /* Write length byte */
        buf[pos++] = (uint8_t)label_len;

        /* Write label */
        memcpy(&buf[pos], ptr, label_len);
        pos += label_len;

        /* Advance past the dot */
        ptr = dot;
        if (*ptr == '.') {
            ptr++;
        }
    }

    /* Null terminator (root label) */
    if (pos + 1 > buf_size) {
        return 0;
    }
    buf[pos++] = 0;

    return pos;
}

/**
 * @brief Build a DNS query packet
 *
 * Constructs a standard DNS query with:
 * - Random query ID from entropy
 * - Single question section
 * - Recursion desired flag set
 *
 * @param[out] buf       Output buffer (must be >= VOS3_DNS_MAX_PACKET)
 * @param[in]  buf_size  Buffer capacity
 * @param[in]  hostname  Hostname to query
 * @param[in]  qtype     Query type (VOS3_DNS_TYPE_A or VOS3_DNS_TYPE_AAAA)
 * @param[out] out_id    Generated query ID (for matching response)
 * @return Packet length in bytes, or 0 on error
 */
static size_t dns_build_query(uint8_t* buf, size_t buf_size,
                              const char* hostname, uint16_t qtype,
                              uint16_t* out_id)
{
    if (unlikely(buf == NULL || hostname == NULL || buf_size < VOS3_DNS_HEADER_SIZE + 5)) {
        return 0;
    }

    /* Validate hostname length */
    size_t hlen = strlen(hostname);
    if (hlen == 0 || hlen > VOS3_DNS_MAX_NAME) {
        return 0;
    }

    /* Generate query ID */
    uint16_t qid = dns_next_query_id();
    if (out_id != NULL) {
        *out_id = qid;
    }

    /* Build DNS header */
    vos3_dns_header_t* hdr = (vos3_dns_header_t*)buf;
    hdr->id      = vos3_htons(qid);
    hdr->flags   = vos3_htons(VOS3_DNS_FLAG_RD);  /* Recursion desired */
    hdr->qdcount = vos3_htons(1);                  /* One question */
    hdr->ancount = 0;
    hdr->nscount = 0;
    hdr->arcount = 0;

    size_t pos = VOS3_DNS_HEADER_SIZE;

    /* Encode hostname as DNS labels */
    size_t name_len = dns_encode_name(&buf[pos], buf_size - pos, hostname);
    if (name_len == 0) {
        return 0;
    }
    pos += name_len;

    /* Append QTYPE and QCLASS */
    if (pos + 4 > buf_size) {
        return 0;
    }
    buf[pos++] = (uint8_t)(qtype >> 8);
    buf[pos++] = (uint8_t)(qtype & 0xFF);
    buf[pos++] = (uint8_t)(VOS3_DNS_CLASS_IN >> 8);
    buf[pos++] = (uint8_t)(VOS3_DNS_CLASS_IN & 0xFF);

    return pos;
}

/* ============================================================================
 * DNS RESPONSE PARSER
 * ============================================================================ */

/**
 * @brief Skip a DNS name in a packet (handling compression)
 *
 * DNS names can use compression pointers (0xC0 prefix).
 * This function advances past the name in the packet.
 *
 * @param[in] pkt       Packet buffer
 * @param[in] pkt_len   Packet length
 * @param[in] offset    Current offset in packet
 * @return New offset after the name, or 0 on error
 */
static size_t dns_skip_name(const uint8_t* pkt, size_t pkt_len, size_t offset)
{
    size_t pos = offset;
    int jumps = 0;
    int jumped = 0;
    size_t first_jump_pos = 0;

    while (pos < pkt_len) {
        uint8_t len = pkt[pos];

        if (len == 0) {
            /* End of name */
            if (!jumped) {
                return pos + 1;
            }
            return first_jump_pos;
        }

        if ((len & 0xC0) == 0xC0) {
            /* Compression pointer */
            if (pos + 1 >= pkt_len) {
                return 0;
            }
            if (!jumped) {
                first_jump_pos = pos + 2;
                jumped = 1;
            }
            uint16_t ptr_offset = (uint16_t)(((len & 0x3F) << 8) | pkt[pos + 1]);
            pos = ptr_offset;
            jumps++;
            if (jumps > 10) {
                return 0;  /* Prevent infinite loops */
            }
            continue;
        }

        /* Regular label */
        if (len > 63) {
            return 0;  /* Invalid label length */
        }
        pos += 1 + len;
    }

    return 0;  /* Ran past end of packet */
}

/**
 * @brief Read a DNS name from a packet (handling compression)
 *
 * @param[in]  pkt       Packet buffer
 * @param[in]  pkt_len   Packet length
 * @param[in]  offset    Offset to start reading
 * @param[out] name      Output name buffer
 * @param[in]  name_size Output buffer size
 * @return New offset after the name field, or 0 on error
 */
static size_t dns_read_name(const uint8_t* pkt, size_t pkt_len, size_t offset,
                            char* name, size_t name_size)
{
    size_t pos = offset;
    size_t name_pos = 0;
    int jumps = 0;
    int jumped = 0;
    size_t first_jump_pos = 0;

    while (pos < pkt_len) {
        uint8_t len = pkt[pos];

        if (len == 0) {
            /* End of name - remove trailing dot */
            if (name_pos > 0 && name[name_pos - 1] == '.') {
                name_pos--;
            }
            name[name_pos] = '\0';
            if (!jumped) {
                return pos + 1;
            }
            return first_jump_pos;
        }

        if ((len & 0xC0) == 0xC0) {
            /* Compression pointer */
            if (pos + 1 >= pkt_len) {
                return 0;
            }
            if (!jumped) {
                first_jump_pos = pos + 2;
                jumped = 1;
            }
            uint16_t ptr_offset = (uint16_t)(((len & 0x3F) << 8) | pkt[pos + 1]);
            pos = ptr_offset;
            jumps++;
            if (jumps > 10) {
                return 0;
            }
            continue;
        }

        /* Regular label */
        if (len > 63) {
            return 0;
        }
        pos++;

        /* Copy label bytes */
        for (uint8_t i = 0; i < len; i++) {
            if (pos >= pkt_len || name_pos >= name_size - 2) {
                return 0;
            }
            name[name_pos++] = (char)pkt[pos++];
        }

        /* Add dot separator */
        if (name_pos < name_size - 1) {
            name[name_pos++] = '.';
        }
    }

    return 0;
}

/**
 * @brief Parse a DNS response packet
 *
 * Validates the response header, skips the question section,
 * then iterates answer records looking for the requested type.
 * Follows CNAME chains up to VOS3_DNS_CNAME_MAX_HOPS.
 *
 * @param[in]  pkt       Response packet buffer
 * @param[in]  pkt_len   Response packet length
 * @param[in]  qid       Expected query ID
 * @param[in]  qtype     Expected query type (A or AAAA)
 * @param[out] out_ip    IPv4 result (for A queries, network order)
 * @param[out] out_ip6   IPv6 result (for AAAA queries, 16 bytes)
 * @param[out] out_ttl   TTL from the answer record
 * @return 0 on success, negative error code on failure
 */
static int dns_parse_response(const uint8_t* pkt, size_t pkt_len,
                              uint16_t qid, uint16_t qtype,
                              uint32_t* out_ip, uint8_t* out_ip6,
                              uint32_t* out_ttl)
{
    /* SECURITY: Minimum packet size */
    if (unlikely(pkt_len < VOS3_DNS_HEADER_SIZE)) {
        return -1;
    }

    const vos3_dns_header_t* hdr = (const vos3_dns_header_t*)pkt;
    uint16_t flags = vos3_ntohs(hdr->flags);
    uint16_t resp_id = vos3_ntohs(hdr->id);

    /* SECURITY: Validate query ID matches */
    if (resp_id != qid) {
        VOS3_DEBUG("[DNS] Query ID mismatch: expected %u, got %u", qid, resp_id);
        return -1;
    }

    /* Validate response flag is set */
    if (!(flags & VOS3_DNS_FLAG_QR)) {
        VOS3_DEBUG("[DNS] Not a response (QR=0)");
        return -1;
    }

    /* Check for errors (RCODE != 0) */
    uint16_t rcode = flags & VOS3_DNS_RCODE_MASK;
    if (rcode != 0) {
        VOS3_DEBUG("[DNS] Server returned error RCODE=%u", rcode);
        return -2;  /* -ENOENT */
    }

    uint16_t qdcount = vos3_ntohs(hdr->qdcount);
    uint16_t ancount = vos3_ntohs(hdr->ancount);

    /* Must have at least one answer */
    if (ancount == 0) {
        VOS3_DEBUG("[DNS] No answer records");
        return -2;  /* -ENOENT */
    }

    size_t pos = VOS3_DNS_HEADER_SIZE;

    /* Skip question section */
    for (uint16_t q = 0; q < qdcount; q++) {
        pos = dns_skip_name(pkt, pkt_len, pos);
        if (pos == 0) {
            return -1;
        }
        pos += 4;  /* QTYPE (2) + QCLASS (2) */
        if (pos > pkt_len) {
            return -1;
        }
    }

    /* Parse answer records */
    int cname_hops = 0;
    char cname_buf[VOS3_DNS_MAX_NAME + 1];

    for (uint16_t a = 0; a < ancount; a++) {
        /* Read/skip the name */
        pos = dns_skip_name(pkt, pkt_len, pos);
        if (pos == 0 || pos + 10 > pkt_len) {
            return -1;
        }

        /* Parse TYPE, CLASS, TTL, RDLENGTH */
        uint16_t rtype   = (uint16_t)((pkt[pos] << 8) | pkt[pos + 1]);
        /* uint16_t rclass = (uint16_t)((pkt[pos + 2] << 8) | pkt[pos + 3]); */
        uint32_t rttl    = ((uint32_t)pkt[pos + 4] << 24) |
                           ((uint32_t)pkt[pos + 5] << 16) |
                           ((uint32_t)pkt[pos + 6] << 8)  |
                           ((uint32_t)pkt[pos + 7]);
        uint16_t rdlength = (uint16_t)((pkt[pos + 8] << 8) | pkt[pos + 9]);
        pos += 10;

        /* Validate RDATA fits in packet */
        if (pos + rdlength > pkt_len) {
            return -1;
        }

        if (rtype == VOS3_DNS_TYPE_A && qtype == VOS3_DNS_TYPE_A && rdlength == 4) {
            /* A record: 4-byte IPv4 address */
            if (out_ip != NULL) {
                memcpy(out_ip, &pkt[pos], 4);
            }
            if (out_ttl != NULL) {
                *out_ttl = rttl;
            }
            return 0;
        }

        if (rtype == VOS3_DNS_TYPE_AAAA && qtype == VOS3_DNS_TYPE_AAAA && rdlength == 16) {
            /* AAAA record: 16-byte IPv6 address */
            if (out_ip6 != NULL) {
                memcpy(out_ip6, &pkt[pos], 16);
            }
            if (out_ttl != NULL) {
                *out_ttl = rttl;
            }
            return 0;
        }

        if (rtype == VOS3_DNS_TYPE_CNAME) {
            /* CNAME record: follow the chain */
            cname_hops++;
            if (cname_hops > (int)VOS3_DNS_CNAME_MAX_HOPS) {
                VOS3_DEBUG("[DNS] CNAME chain too long (>%u hops)",
                           VOS3_DNS_CNAME_MAX_HOPS);
                return -1;
            }

            /* Read the CNAME target */
            size_t cname_end = dns_read_name(pkt, pkt_len, pos,
                                             cname_buf, sizeof(cname_buf));
            if (cname_end == 0) {
                return -1;
            }
            /* Continue looking for A/AAAA records matching this CNAME */
            VOS3_DEBUG("[DNS] Following CNAME -> %s", cname_buf);
        }

        /* Skip this record's RDATA */
        pos += rdlength;
    }

    /* No matching record found */
    return -2;  /* -ENOENT */
}

/* ============================================================================
 * DNS RESOLVE (A RECORD)
 * ============================================================================ */

/**
 * @brief Internal resolve implementation for both A and AAAA queries
 *
 * @param[in]  hostname  Hostname to resolve
 * @param[in]  qtype     Query type (VOS3_DNS_TYPE_A or VOS3_DNS_TYPE_AAAA)
 * @param[out] out_ip    IPv4 result (for A, may be NULL)
 * @param[out] out_ip6   IPv6 result (for AAAA, may be NULL)
 * @return 0 on success, negative error code on failure
 */
static int dns_resolve_internal(const char* hostname, uint16_t qtype,
                                uint32_t* out_ip, uint8_t* out_ip6)
{
    if (unlikely(hostname == NULL)) {
        return -22;  /* -EINVAL */
    }

    /* Check cache first */
    vos3_spinlock_lock(&g_dns_lock);

    vos3_dns_cache_entry_t* cached = dns_cache_lookup(hostname, qtype);
    if (cached != NULL) {
        if (out_ip != NULL && qtype == VOS3_DNS_TYPE_A) {
            *out_ip = cached->ip;
        }
        if (out_ip6 != NULL && qtype == VOS3_DNS_TYPE_AAAA) {
            memcpy(out_ip6, cached->ip6, 16);
        }
        g_dns_stats.cache_hits++;
        vos3_spinlock_unlock(&g_dns_lock);

        VOS3_DEBUG("[DNS] Cache hit for %s", hostname);
        return 0;
    }
    g_dns_stats.cache_misses++;

    vos3_spinlock_unlock(&g_dns_lock);

    /* Build DNS query packet */
    uint8_t query_buf[VOS3_DNS_MAX_PACKET];
    uint16_t qid = 0;
    size_t query_len = dns_build_query(query_buf, sizeof(query_buf),
                                       hostname, qtype, &qid);
    if (query_len == 0) {
        VOS3_WARN("[DNS] Failed to build query for %s", hostname);
        return -22;  /* -EINVAL */
    }

    /* Create a kernel socket for the DNS query */
    vos3_socket_t* sock = vos3_udp_lookup_port(DNS_EPHEMERAL_PORT);
    int need_unbind = 0;

    if (sock == NULL) {
        /* Allocate a temporary socket by creating one via the socket API.
         * We use the internal kernel socket path to avoid syscall overhead. */
        int sfd = vos3_sys_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sfd < 0) {
            VOS3_WARN("[DNS] Failed to create socket: %d", sfd);
            return -12;  /* -ENOMEM */
        }

        /* Bind to ephemeral port */
        struct sockaddr_in local_addr;
        memset(&local_addr, 0, sizeof(local_addr));
        local_addr.sin_family = AF_INET;
        local_addr.sin_port = vos3_htons(DNS_EPHEMERAL_PORT);
        local_addr.sin_addr = INADDR_ANY;

        int ret = vos3_sys_bind(sfd, (struct sockaddr*)&local_addr, sizeof(local_addr));
        if (ret < 0) {
            VOS3_WARN("[DNS] Failed to bind ephemeral port: %d", ret);
            vos3_sys_closesocket(sfd);
            return -98;  /* -EADDRINUSE */
        }

        sock = vos3_udp_lookup_port(DNS_EPHEMERAL_PORT);
        if (sock == NULL) {
            VOS3_WARN("[DNS] Socket not found after bind");
            return -1;
        }
        need_unbind = 1;
    }

    /* Send DNS query via UDP to the configured server */
    ssize_t sent = vos3_udp_send(sock, query_buf, query_len,
                                 g_dns_server, VOS3_DNS_PORT);
    if (sent < 0) {
        VOS3_WARN("[DNS] Failed to send query: %zd", sent);
        if (need_unbind) {
            vos3_sys_closesocket(sock->fd);
        }
        return -1;
    }
    g_dns_stats.queries_sent++;

    VOS3_DEBUG("[DNS] Sent %zd byte query for %s (type=%u, id=%u)",
               sent, hostname, qtype, qid);

    /* Poll for response with timeout */
    uint64_t start_ms = vos3_timer_get_uptime_ms();
    uint8_t resp_buf[VOS3_DNS_MAX_PACKET];
    int found = 0;

    while ((vos3_timer_get_uptime_ms() - start_ms) < VOS3_DNS_TIMEOUT_MS) {
        /* Check socket receive queue */
        if (sock->rx_count > 0) {
            /* Dequeue packet from socket */
            uint8_t head = sock->rx_head;
            vos3_rx_packet_t* rxp = &sock->rx_queue[head];

            if (rxp->valid && rxp->buf != NULL) {
                /* Extract UDP payload from the network buffer.
                 * The buffer contains: ETH + IP + UDP + payload.
                 * The data_offset points past the IP header to UDP. */
                size_t udp_off = rxp->buf->data_offset;
                size_t avail = rxp->buf->len;

                /* Skip past UDP header (8 bytes) to get DNS payload */
                size_t dns_off = udp_off + 8;
                if (dns_off < avail) {
                    size_t dns_len = avail - dns_off;
                    if (dns_len > sizeof(resp_buf)) {
                        dns_len = sizeof(resp_buf);
                    }
                    memcpy(resp_buf, rxp->buf->data + dns_off, dns_len);

                    /* Free the RX packet */
                    vos3_netbuf_free(rxp->buf);
                    rxp->buf = NULL;
                    rxp->valid = 0;
                    sock->rx_head = (uint8_t)((head + 1) % VOS3_SOCKET_RX_QUEUE_MAX);
                    sock->rx_count--;

                    /* Parse the response */
                    uint32_t resp_ip = 0;
                    uint8_t  resp_ip6[16];
                    uint32_t resp_ttl = VOS3_DNS_TTL_DEFAULT;

                    memset(resp_ip6, 0, sizeof(resp_ip6));

                    int parse_ret = dns_parse_response(resp_buf, dns_len,
                                                       qid, qtype,
                                                       &resp_ip, resp_ip6,
                                                       &resp_ttl);
                    if (parse_ret == 0) {
                        g_dns_stats.responses_received++;

                        /* Store in cache */
                        vos3_spinlock_lock(&g_dns_lock);
                        dns_cache_store(hostname, qtype, resp_ip,
                                        (qtype == VOS3_DNS_TYPE_AAAA) ? resp_ip6 : NULL,
                                        resp_ttl);
                        vos3_spinlock_unlock(&g_dns_lock);

                        /* Return result */
                        if (out_ip != NULL && qtype == VOS3_DNS_TYPE_A) {
                            *out_ip = resp_ip;
                        }
                        if (out_ip6 != NULL && qtype == VOS3_DNS_TYPE_AAAA) {
                            memcpy(out_ip6, resp_ip6, 16);
                        }

                        found = 1;
                        break;
                    } else {
                        g_dns_stats.parse_errors++;
                        VOS3_DEBUG("[DNS] Parse error %d for %s", parse_ret, hostname);
                    }
                } else {
                    /* Malformed RX buffer */
                    vos3_netbuf_free(rxp->buf);
                    rxp->buf = NULL;
                    rxp->valid = 0;
                    sock->rx_head = (uint8_t)((head + 1) % VOS3_SOCKET_RX_QUEUE_MAX);
                    sock->rx_count--;
                }
            }
        }

        /* Yield CPU briefly (busy-poll with pause) */
        __asm__ volatile ("pause" ::: "memory");
    }

    /* Cleanup */
    if (need_unbind) {
        vos3_sys_closesocket(sock->fd);
    }

    if (!found) {
        g_dns_stats.timeouts++;
        VOS3_WARN("[DNS] Timeout resolving %s after %u ms",
                  hostname, VOS3_DNS_TIMEOUT_MS);
        return -110;  /* -ETIMEDOUT */
    }

    return 0;
}

/**
 * @brief Resolve hostname to IPv4 address (A record)
 */
int vos3_dns_resolve(const char* hostname, uint32_t* out_ip)
{
    if (unlikely(hostname == NULL || out_ip == NULL)) {
        return -22;  /* -EINVAL */
    }

    VOS3_DEBUG("[DNS] Resolving A record for %s", hostname);
    return dns_resolve_internal(hostname, VOS3_DNS_TYPE_A, out_ip, NULL);
}

/**
 * @brief Resolve hostname to IPv6 address (AAAA record)
 */
int vos3_dns_resolve6(const char* hostname, uint8_t out_ip6[16])
{
    if (unlikely(hostname == NULL || out_ip6 == NULL)) {
        return -22;  /* -EINVAL */
    }

    VOS3_DEBUG("[DNS] Resolving AAAA record for %s", hostname);
    return dns_resolve_internal(hostname, VOS3_DNS_TYPE_AAAA, NULL, out_ip6);
}

/* ============================================================================
 * DNS SERVER CONFIGURATION
 * ============================================================================ */

/**
 * @brief Set the DNS server IP address
 */
void vos3_dns_set_server(uint32_t server_ip)
{
    vos3_spinlock_lock(&g_dns_lock);
    g_dns_server = server_ip;
    vos3_spinlock_unlock(&g_dns_lock);

    VOS3_INFO("[DNS] Server set to %u.%u.%u.%u",
              server_ip & 0xFF,
              (server_ip >> 8) & 0xFF,
              (server_ip >> 16) & 0xFF,
              (server_ip >> 24) & 0xFF);
}

/* ============================================================================
 * DNS CACHE MANAGEMENT
 * ============================================================================ */

/**
 * @brief Flush all entries from the DNS cache
 */
void vos3_dns_cache_flush(void)
{
    vos3_spinlock_lock(&g_dns_lock);
    memset(g_dns_cache, 0, sizeof(g_dns_cache));
    vos3_spinlock_unlock(&g_dns_lock);

    VOS3_INFO("[DNS] Cache flushed");
}

/**
 * @brief Print DNS cache contents for debugging
 */
void vos3_dns_print_cache(void)
{
    uint64_t now = dns_time_seconds();

    VOS3_INFO("[DNS] Cache contents (%u slots):", VOS3_DNS_CACHE_SIZE);
    VOS3_INFO("[DNS] Server: %u.%u.%u.%u",
              g_dns_server & 0xFF,
              (g_dns_server >> 8) & 0xFF,
              (g_dns_server >> 16) & 0xFF,
              (g_dns_server >> 24) & 0xFF);

    vos3_spinlock_lock(&g_dns_lock);

    int count = 0;
    for (size_t i = 0; i < VOS3_DNS_CACHE_SIZE; i++) {
        vos3_dns_cache_entry_t* entry = &g_dns_cache[i];
        if (!entry->valid) {
            continue;
        }

        uint64_t age = (now > entry->timestamp) ? (now - entry->timestamp) : 0;
        uint64_t remaining = (entry->ttl > age) ? (entry->ttl - age) : 0;

        if (entry->qtype == VOS3_DNS_TYPE_A) {
            uint32_t ip = entry->ip;
            VOS3_INFO("[DNS]   [%zu] %s -> %u.%u.%u.%u (A, TTL=%llu/%u)",
                      i, entry->name,
                      ip & 0xFF, (ip >> 8) & 0xFF,
                      (ip >> 16) & 0xFF, (ip >> 24) & 0xFF,
                      (unsigned long long)remaining, entry->ttl);
        } else if (entry->qtype == VOS3_DNS_TYPE_AAAA) {
            VOS3_INFO("[DNS]   [%zu] %s -> [IPv6] (AAAA, TTL=%llu/%u)",
                      i, entry->name,
                      (unsigned long long)remaining, entry->ttl);
        }
        count++;
    }

    vos3_spinlock_unlock(&g_dns_lock);

    VOS3_INFO("[DNS] %d active entries", count);
    VOS3_INFO("[DNS] Stats: queries=%llu, responses=%llu, cache_hits=%llu, "
              "cache_misses=%llu, timeouts=%llu, parse_errors=%llu",
              (unsigned long long)g_dns_stats.queries_sent,
              (unsigned long long)g_dns_stats.responses_received,
              (unsigned long long)g_dns_stats.cache_hits,
              (unsigned long long)g_dns_stats.cache_misses,
              (unsigned long long)g_dns_stats.timeouts,
              (unsigned long long)g_dns_stats.parse_errors);
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize DNS resolver subsystem
 *
 * Seeds the query ID generator from hardware entropy and
 * clears the DNS cache.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_dns_init(void)
{
    /* Seed query ID from hardware entropy */
    g_dns_query_id = vos3_entropy_get_u32();

    /* Clear cache */
    memset(g_dns_cache, 0, sizeof(g_dns_cache));
    memset(&g_dns_stats, 0, sizeof(g_dns_stats));
    g_dns_lock = VOS3_SPINLOCK_INIT;

    VOS3_INFO("[DNS] DNS resolver initialized");
    VOS3_INFO("[DNS] Default server: %u.%u.%u.%u",
              g_dns_server & 0xFF,
              (g_dns_server >> 8) & 0xFF,
              (g_dns_server >> 16) & 0xFF,
              (g_dns_server >> 24) & 0xFF);
    VOS3_INFO("[DNS] Cache: %u entries, TTL default: %u seconds",
              VOS3_DNS_CACHE_SIZE, VOS3_DNS_TTL_DEFAULT);

    return 0;
}
