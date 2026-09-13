/**
 * @file dns.h
 * @brief VOS3 DNS Resolver Definitions
 *
 * @details Kernel-space DNS resolver with caching:
 *          - A (IPv4) and AAAA (IPv6) record resolution
 *          - LRU-evicted cache with configurable TTL
 *          - CNAME chain following (up to 5 hops)
 *          - UDP-based queries to configurable DNS server
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

#ifndef VOS3_DNS_H
#define VOS3_DNS_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * DNS CONSTANTS
 * ============================================================================ */

/** @brief DNS server port */
#define VOS3_DNS_PORT               53U

/** @brief Maximum DNS name length (RFC 1035) */
#define VOS3_DNS_MAX_NAME           255U

/** @brief DNS cache size (number of entries) */
#define VOS3_DNS_CACHE_SIZE         64U

/** @brief Default TTL in seconds when response has TTL=0 */
#define VOS3_DNS_TTL_DEFAULT        300U

/** @brief DNS query timeout in milliseconds */
#define VOS3_DNS_TIMEOUT_MS         3000U

/** @brief Maximum CNAME chain hops */
#define VOS3_DNS_CNAME_MAX_HOPS     5U

/** @brief DNS header size in bytes */
#define VOS3_DNS_HEADER_SIZE        12U

/** @brief Maximum DNS packet size */
#define VOS3_DNS_MAX_PACKET         512U

/* ============================================================================
 * DNS QUERY TYPES (RFC 1035)
 * ============================================================================ */

/** @brief A record (IPv4 address) */
#define VOS3_DNS_TYPE_A             1U

/** @brief CNAME record (canonical name) */
#define VOS3_DNS_TYPE_CNAME         5U

/** @brief AAAA record (IPv6 address) */
#define VOS3_DNS_TYPE_AAAA          28U

/** @brief DNS class IN (Internet) */
#define VOS3_DNS_CLASS_IN           1U

/* ============================================================================
 * DNS HEADER FLAGS
 * ============================================================================ */

/** @brief QR bit: response */
#define VOS3_DNS_FLAG_QR            0x8000U

/** @brief RD bit: recursion desired */
#define VOS3_DNS_FLAG_RD            0x0100U

/** @brief RA bit: recursion available */
#define VOS3_DNS_FLAG_RA            0x0080U

/** @brief RCODE mask (last 4 bits) */
#define VOS3_DNS_RCODE_MASK         0x000FU

/* ============================================================================
 * DNS HEADER STRUCTURE
 * ============================================================================ */

/**
 * @brief DNS packet header (RFC 1035 Section 4.1.1)
 *
 * @note 12 bytes, all fields in network byte order (big-endian)
 */
typedef struct vos3_dns_header {
    uint16_t    id;         /**< Query identification */
    uint16_t    flags;      /**< Flags and codes */
    uint16_t    qdcount;    /**< Number of questions */
    uint16_t    ancount;    /**< Number of answer RRs */
    uint16_t    nscount;    /**< Number of authority RRs */
    uint16_t    arcount;    /**< Number of additional RRs */
} __attribute__((packed)) vos3_dns_header_t;

/* ============================================================================
 * DNS CACHE ENTRY
 * ============================================================================ */

/**
 * @brief DNS cache entry
 *
 * Stores resolved DNS records with TTL-based expiration.
 * LRU eviction is used when the cache is full.
 */
typedef struct vos3_dns_cache_entry {
    char        name[VOS3_DNS_MAX_NAME + 1]; /**< Hostname */
    uint32_t    ip;                          /**< IPv4 address (A record, network order) */
    uint8_t     ip6[16];                     /**< IPv6 address (AAAA record) */
    uint32_t    ttl;                         /**< Time-to-live in seconds */
    uint64_t    timestamp;                   /**< Time when entry was stored (seconds) */
    uint16_t    qtype;                       /**< Query type (A=1, AAAA=28) */
    uint8_t     valid;                       /**< Entry is valid */
    uint8_t     reserved;                    /**< Padding */
} vos3_dns_cache_entry_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize DNS resolver subsystem
 *
 * Seeds the query ID generator from hardware entropy and
 * clears the DNS cache. Must be called after entropy and
 * UDP subsystems are initialized.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_dns_init(void);

/**
 * @brief Resolve hostname to IPv4 address (A record)
 *
 * Checks cache first, then sends a DNS query to the configured
 * server. Blocks up to VOS3_DNS_TIMEOUT_MS milliseconds.
 *
 * @param[in]  hostname  Null-terminated hostname string
 * @param[out] out_ip    Resolved IPv4 address (network byte order)
 * @return 0 on success, -ETIMEDOUT on timeout, -ENOENT if not found
 */
int vos3_dns_resolve(const char* hostname, uint32_t* out_ip);

/**
 * @brief Resolve hostname to IPv6 address (AAAA record)
 *
 * Checks cache first, then sends a DNS query to the configured
 * server. Blocks up to VOS3_DNS_TIMEOUT_MS milliseconds.
 *
 * @param[in]  hostname  Null-terminated hostname string
 * @param[out] out_ip6   Resolved IPv6 address (16 bytes)
 * @return 0 on success, -ETIMEDOUT on timeout, -ENOENT if not found
 */
int vos3_dns_resolve6(const char* hostname, uint8_t out_ip6[16]);

/**
 * @brief Set the DNS server IP address
 *
 * @param[in] server_ip  DNS server IPv4 address (network byte order)
 */
void vos3_dns_set_server(uint32_t server_ip);

/**
 * @brief Flush all entries from the DNS cache
 */
void vos3_dns_cache_flush(void);

/**
 * @brief Print DNS cache contents for debugging
 */
void vos3_dns_print_cache(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_DNS_H */
