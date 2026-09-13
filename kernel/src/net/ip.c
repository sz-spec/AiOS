/**
 * @file ip.c
 * @brief VOS3 Hardened IPv4 Implementation
 *
 * @details IPv4 packet processing with security hardening:
 *          - Fragmented packets dropped (DoS prevention)
 *          - Header checksum validation
 *          - TTL validation
 *          - Source/destination address validation
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 4 - Secure Protocol Stack
 */

#include "../../include/vos/net.h"
#include "../../include/vos/net_security.h"
#include "../../include/vos/socket.h"
#include "../../include/vos/compiler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * IPv4 CONSTANTS
 * ============================================================================ */

/** @brief IPv4 version number */
#define IP_VERSION_4            4U

/** @brief Minimum IPv4 header length (in 32-bit words) */
#define IP_IHL_MIN              5U

/** @brief Maximum IPv4 header length (in 32-bit words) */
#define IP_IHL_MAX              15U

/** @brief IPv4 flags: Don't Fragment */
#define IP_FLAG_DF              0x4000U

/** @brief IPv4 flags: More Fragments */
#define IP_FLAG_MF              0x2000U

/** @brief IPv4 fragment offset mask */
#define IP_FRAG_OFFSET_MASK     0x1FFFU

/** @brief Minimum TTL we accept (security) */
#define IP_MIN_TTL              1U

/** @brief Loopback address (127.0.0.0/8) */
#define IP_LOOPBACK_NET         0x7F000000U
#define IP_LOOPBACK_MASK        0xFF000000U

/* Forward declarations */
int vos3_icmp_receive(vos3_netif_t* netif, vos3_netbuf_t* buf,
                       const vos3_ipv4_header_t* ip_hdr);
int vos3_tcp_input(vos3_netif_t* netif, vos3_netbuf_t* buf,
                    uint32_t src_ip, uint32_t dst_ip);

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief IPv4 statistics */
static struct {
    uint64_t packets_received;
    uint64_t packets_delivered;
    uint64_t fragments_dropped;
    uint64_t checksum_errors;
    uint64_t header_errors;
    uint64_t ttl_expired;
    uint64_t protocol_unknown;
} g_ip_stats;

/* ============================================================================
 * CHECKSUM CALCULATION (Performance Critical)
 * ============================================================================ */

/**
 * @brief Calculate IPv4 header checksum
 *
 * This is performance-critical code that runs on every packet.
 * Uses one's complement sum as per RFC 791.
 *
 * @param[in] data   Pointer to IP header
 * @param[in] len    Header length in bytes
 * @return Checksum (0 if valid)
 */
VOS3_ALWAYS_INLINE uint16_t ip_checksum(const void* data, size_t len)
{
    const uint16_t* ptr = (const uint16_t*)data;
    uint32_t sum = 0;

    /* Sum all 16-bit words */
    while (len > 1) {
        sum += *ptr++;
        len -= 2;
    }

    /* Add odd byte if present */
    if (len == 1) {
        sum += *(const uint8_t*)ptr;
    }

    /* Fold 32-bit sum to 16 bits */
    while (sum >> 16) {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }

    return (uint16_t)~sum;
}

/**
 * @brief Verify IPv4 header checksum
 *
 * @param[in] hdr    IP header
 * @param[in] ihl    IP header length in 32-bit words
 * @return 1 if valid, 0 if invalid
 */
VOS3_ALWAYS_INLINE int ip_verify_checksum(const vos3_ipv4_header_t* hdr,
                                           uint8_t ihl)
{
    /* Checksum over entire header should be 0 */
    return ip_checksum(hdr, ihl * 4) == 0;
}

/* ============================================================================
 * SECURITY VALIDATION (Inlined for Performance)
 * ============================================================================ */

/**
 * @brief Check if packet is fragmented
 *
 * SECURITY: We drop all fragmented packets to prevent:
 * - Fragment overlap attacks
 * - Teardrop attacks
 * - Resource exhaustion from fragment reassembly
 *
 * @param[in] flags_frag  IP flags + fragment offset field
 * @return 1 if fragmented, 0 if not
 */
VOS3_ALWAYS_INLINE int ip_is_fragmented(uint16_t flags_frag)
{
    uint16_t host_val = vos3_ntohs(flags_frag);
    /* Fragmented if MF flag set OR fragment offset != 0 */
    return (host_val & IP_FLAG_MF) || (host_val & IP_FRAG_OFFSET_MASK);
}

/**
 * @brief Check if source IP is valid (not broadcast/multicast)
 */
VOS3_ALWAYS_INLINE int ip_src_valid(vos3_ipv4_addr_t src)
{
    uint8_t first_octet = src & 0xFF;

    /* Reject 0.x.x.x (reserved) */
    if (first_octet == 0) return 0;

    /* Reject 224.x.x.x - 239.x.x.x (multicast) */
    if (first_octet >= 224 && first_octet <= 239) return 0;

    /* Reject 255.x.x.x (broadcast) */
    if (first_octet == 255) return 0;

    return 1;
}

/* ============================================================================
 * IPv4 PACKET HANDLING
 * ============================================================================ */

/**
 * @brief Process received IPv4 packet (Hardened)
 *
 * Security measures (all using unlikely() for performance):
 * - Header checksum validation
 * - Fragmented packets dropped (DoS prevention)
 * - TTL validation
 * - Source address validation
 *
 * @param[in] netif  Receiving interface
 * @param[in] buf    Network buffer containing IPv4 packet
 * @return 0 on success, negative on error
 */
VOS3_HOT int vos3_ip_receive(vos3_netif_t* netif, vos3_netbuf_t* buf)
{
    /* SECURITY: Basic validation (unlikely to fail) */
    if (unlikely(netif == NULL || buf == NULL)) {
        return -1;
    }

    /* Calculate IP header offset */
    size_t ip_offset = buf->data_offset;
    size_t ip_len = buf->len - ip_offset;

    /* SECURITY: Minimum size check */
    if (unlikely(ip_len < VOS3_IP4_HEADER_MIN)) {
        VOS3_DEBUG("[IP-SEC] Undersized packet (%zu < %u)", ip_len, VOS3_IP4_HEADER_MIN);
        g_ip_stats.header_errors++;
        return -1;
    }

    const vos3_ipv4_header_t* ip = (const vos3_ipv4_header_t*)(buf->data + ip_offset);

    /* Extract version and IHL */
    uint8_t version = (ip->version_ihl >> 4) & 0x0F;
    uint8_t ihl = ip->version_ihl & 0x0F;

    /* SECURITY: Version check */
    if (unlikely(version != IP_VERSION_4)) {
        VOS3_DEBUG("[IP-SEC] Invalid version %u (expected 4)", version);
        g_ip_stats.header_errors++;
        return -1;
    }

    /* SECURITY: IHL bounds check */
    if (unlikely(ihl < IP_IHL_MIN || ihl > IP_IHL_MAX)) {
        VOS3_DEBUG("[IP-SEC] Invalid IHL %u (valid: 5-15)", ihl);
        g_ip_stats.header_errors++;
        return -1;
    }

    size_t header_len = (size_t)ihl * 4;

    /* SECURITY: Verify header fits in packet */
    if (unlikely(header_len > ip_len)) {
        VOS3_DEBUG("[IP-SEC] Header length %zu > packet length %zu",
                   header_len, ip_len);
        g_ip_stats.header_errors++;
        return -1;
    }

    /* SECURITY: Checksum validation */
    if (unlikely(!ip_verify_checksum(ip, ihl))) {
        VOS3_WARN("[IP-SEC] Checksum FAILED (dropped)");
        g_ip_stats.checksum_errors++;
        return -1;
    }

    /* SECURITY: Total length validation */
    uint16_t total_len = vos3_ntohs(ip->total_length);
    if (unlikely(total_len < header_len || total_len > ip_len)) {
        VOS3_DEBUG("[IP-SEC] Invalid total length %u (header=%zu, avail=%zu)",
                   total_len, header_len, ip_len);
        g_ip_stats.header_errors++;
        return -1;
    }

    /* SECURITY: Drop fragmented packets (DoS prevention) */
    if (unlikely(ip_is_fragmented(ip->flags_fragment))) {
        VOS3_WARN("[IP-SEC] Fragmented packet DROPPED (DoS prevention)");
        g_ip_stats.fragments_dropped++;
        return -1;
    }

    /* SECURITY: TTL check */
    if (unlikely(ip->ttl < IP_MIN_TTL)) {
        VOS3_DEBUG("[IP-SEC] TTL expired (ttl=%u)", ip->ttl);
        g_ip_stats.ttl_expired++;
        return -1;
    }

    /* SECURITY: Source address validation */
    if (unlikely(!ip_src_valid(ip->src_addr))) {
        VOS3_DEBUG("[IP-SEC] Invalid source address");
        g_ip_stats.header_errors++;
        return -1;
    }

    /* All security checks passed - update stats */
    g_ip_stats.packets_received++;

    /* Update buffer offset for payload */
    buf->data_offset = (uint16_t)(ip_offset + header_len);

    VOS3_DEBUG("[IP] Received %u.%u.%u.%u -> %u.%u.%u.%u proto=%u len=%u",
               (ip->src_addr) & 0xFF, (ip->src_addr >> 8) & 0xFF,
               (ip->src_addr >> 16) & 0xFF, (ip->src_addr >> 24) & 0xFF,
               (ip->dst_addr) & 0xFF, (ip->dst_addr >> 8) & 0xFF,
               (ip->dst_addr >> 16) & 0xFF, (ip->dst_addr >> 24) & 0xFF,
               ip->protocol, total_len);

    /* Dispatch based on protocol */
    switch (ip->protocol) {
        case VOS3_IPPROTO_ICMP:
            g_ip_stats.packets_delivered++;
            return vos3_icmp_receive(netif, buf, ip);

        case VOS3_IPPROTO_TCP:
            g_ip_stats.packets_delivered++;
            return vos3_tcp_input(netif, buf, ip->src_addr, ip->dst_addr);

        case VOS3_IPPROTO_UDP:
            g_ip_stats.packets_delivered++;
            return vos3_udp_receive(netif, buf, ip);

        default:
            VOS3_DEBUG("[IP-SEC] Unknown protocol %u", ip->protocol);
            g_ip_stats.protocol_unknown++;
            return -1;
    }
}

/**
 * @brief Initialize IPv4 subsystem
 */
void vos3_ip_init(void)
{
    memset(&g_ip_stats, 0, sizeof(g_ip_stats));
    VOS3_INFO("[IP] IPv4 subsystem initialized");
    VOS3_INFO("[IP] Security: Fragments=DROP, Checksum=VERIFY");
}

/**
 * @brief Send IPv4 packet
 *
 * @param[in] netif     Network interface
 * @param[in] buf       Network buffer (data starts at IP payload)
 * @param[in] dst_ip    Destination IP (network order)
 * @param[in] protocol  Protocol number (6=TCP, 17=UDP)
 * @return 0 on success, negative error
 */
int vos3_ip_output(vos3_netif_t* netif, vos3_netbuf_t* buf,
                   uint32_t dst_ip, uint8_t protocol)
{
    if (unlikely(netif == NULL || buf == NULL)) {
        return -1;
    }

    /* Calculate payload length from buffer */
    uint16_t payload_len = buf->len;

    /* Reserve space for IP and Ethernet headers */
    size_t ip_offset = VOS3_ETH_HEADER_SIZE;
    size_t total_len = ip_offset + VOS3_IP4_HEADER_MIN + payload_len;

    /* Create new buffer with headers */
    vos3_netbuf_t* outbuf = vos3_netbuf_alloc(total_len);
    if (unlikely(outbuf == NULL)) {
        vos3_netbuf_free(buf);
        return -12;  /* -ENOMEM */
    }

    /* Build Ethernet header */
    vos3_eth_header_t* eth = (vos3_eth_header_t*)outbuf->data;
    uint8_t dst_mac[6];

    /* Gateway routing: if destination is not on our local subnet,
     * ARP-resolve the gateway address instead of the final destination.
     * This is how IP routing works — packets for non-local hosts go
     * through the gateway (next hop). */
    uint32_t next_hop = dst_ip;
    if (netif->ipv4_netmask != 0 && netif->ipv4_gateway != 0) {
        if ((dst_ip & netif->ipv4_netmask) != (netif->ipv4_addr & netif->ipv4_netmask)) {
            next_hop = netif->ipv4_gateway;
        }
    }

    if (vos3_arp_resolve(next_hop, dst_mac) == 0) {
        memcpy(eth->dst.bytes, dst_mac, 6);
    } else {
        memset(eth->dst.bytes, 0xFF, 6);  /* ARP miss — broadcast fallback */
    }
    memcpy(eth->src.bytes, netif->mac_addr.bytes, 6);
    eth->ethertype = vos3_htons(VOS3_ETHERTYPE_IPV4);

    /* Build IP header */
    vos3_ipv4_header_t* ip = (vos3_ipv4_header_t*)(outbuf->data + ip_offset);
    ip->version_ihl = 0x45;  /* IPv4, 5*4=20 bytes header */
    ip->tos = 0;
    ip->total_length = vos3_htons((uint16_t)(VOS3_IP4_HEADER_MIN + payload_len));
    static uint16_t ip_id = 0;
    ip->identification = vos3_htons(ip_id++);
    ip->flags_fragment = 0;
    ip->ttl = 64;
    ip->protocol = protocol;
    ip->checksum = 0;
    ip->src_addr = netif->ipv4_addr;
    ip->dst_addr = dst_ip;

    /* Calculate IP checksum */
    ip->checksum = ip_checksum(ip, VOS3_IP4_HEADER_MIN);

    /* Copy payload (TCP segment) */
    uint8_t* payload_dst = (uint8_t*)ip + VOS3_IP4_HEADER_MIN;
    memcpy(payload_dst, buf->data + buf->data_offset, payload_len);

    /* Free original buffer */
    vos3_netbuf_free(buf);

    /* Send packet via Ethernet */
    outbuf->len = (uint16_t)total_len;
    extern int vos3_net_tx_ethernet(vos3_netif_t* netif, vos3_netbuf_t* buf);
    int ret = vos3_net_tx_ethernet(netif, outbuf);
    vos3_netbuf_free(outbuf);
    return ret;
}

/**
 * @brief Print IPv4 statistics
 */
void vos3_ip_print_stats(void)
{
    VOS3_INFO("[IP] Statistics:");
    VOS3_INFO("  Packets received:    %llu",
              (unsigned long long)g_ip_stats.packets_received);
    VOS3_INFO("  Packets delivered:   %llu",
              (unsigned long long)g_ip_stats.packets_delivered);
    VOS3_INFO("  Fragments dropped:   %llu",
              (unsigned long long)g_ip_stats.fragments_dropped);
    VOS3_INFO("  Checksum errors:     %llu",
              (unsigned long long)g_ip_stats.checksum_errors);
    VOS3_INFO("  Header errors:       %llu",
              (unsigned long long)g_ip_stats.header_errors);
}
