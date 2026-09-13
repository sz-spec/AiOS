/**
 * @file udp.c
 * @brief VOS3 UDP Protocol Implementation
 *
 * @details User Datagram Protocol with O(1) port demuxing:
 *          - Static port hash table for constant-time lookup
 *          - Branch prediction on all security checks
 *          - Integration with socket layer
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 5 - UDP Protocol & POSIX Socket API
 */

#include "../../include/vos/socket.h"
#include "../../include/vos/net.h"
#include "../../include/vos/net_security.h"
#include "../../include/vos/compiler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/atomic.h"

/* §5.1 (v20.6 → restored 2026-05-02): per-packet unique IPv4 identification.
 * Replaces hard-coded 0x1234 — RFC 791 requires the value be reasonably
 * unique across the lifetime of a packet's MSL window. Atomic increment is
 * SMP-safe; wraparound is harmless (16-bit space; reuse after ~65k packets
 * is well outside MSL even at high pps). */
static volatile uint32_t g_ip_id_counter = 0u;

/* Forward declaration */
extern void vos3_icmp_send_dest_unreach(vos3_netif_t* netif, vos3_netbuf_t* buf,
                                         const vos3_ipv4_header_t* ip_hdr);

/* ============================================================================
 * UDP HEADER
 * ============================================================================ */

/**
 * @brief UDP header structure
 */
typedef struct udp_header {
    uint16_t    src_port;       /**< Source port (network order) */
    uint16_t    dst_port;       /**< Destination port (network order) */
    uint16_t    length;         /**< UDP length (header + data) */
    uint16_t    checksum;       /**< UDP checksum */
} __attribute__((packed)) udp_header_t;

/** @brief UDP header size */
#define UDP_HEADER_SIZE     8U

/** @brief Minimum UDP packet size */
#define UDP_MIN_SIZE        8U

/** @brief Maximum UDP payload */
#define UDP_MAX_PAYLOAD     65507U

/* ============================================================================
 * O(1) PORT DEMUXING - Static Hash Table
 * ============================================================================
 *
 * Performance: Uses static array indexed by (port % HASH_SIZE).
 * For common ports (8080, 53, etc.), this provides O(1) lookup.
 *
 * Collision handling: Linear probing with max 8 steps.
 * For a mostly empty table, this is effectively O(1).
 */

/** @brief Port hash table size (power of 2 for fast modulo) */
#define UDP_PORT_HASH_SIZE      1024U
#define UDP_PORT_HASH_MASK      (UDP_PORT_HASH_SIZE - 1U)

/** @brief Maximum collision probe depth */
#define UDP_PORT_PROBE_MAX      8U

/** @brief Port hash table entry */
typedef struct udp_port_entry {
    uint16_t        port;       /**< Port number */
    uint8_t         in_use;     /**< Entry is occupied */
    uint8_t         reserved;
    vos3_socket_t*  socket;     /**< Owning socket */
} udp_port_entry_t;

/** @brief Static port hash table */
static udp_port_entry_t g_udp_port_map[UDP_PORT_HASH_SIZE];

/** @brief Port table lock */
static vos3_spinlock_t g_udp_port_lock = VOS3_SPINLOCK_INIT;

/** @brief UDP statistics */
static struct {
    uint64_t    packets_received;
    uint64_t    packets_sent;
    uint64_t    bytes_received;
    uint64_t    bytes_sent;
    uint64_t    no_port_drops;      /**< No socket for dest port */
    uint64_t    checksum_errors;
    uint64_t    truncated_drops;
    uint64_t    queue_full_drops;
} g_udp_stats;

/* ============================================================================
 * PORT HASH TABLE OPERATIONS
 * ============================================================================ */

/**
 * @brief Hash function for port lookup (O(1))
 */
VOS3_ALWAYS_INLINE size_t udp_port_hash(uint16_t port)
{
    /* Simple modulo hash - effective for mostly sequential ports */
    return (size_t)(port & UDP_PORT_HASH_MASK);
}

/**
 * @brief Lookup socket by UDP port (O(1) average)
 *
 * Uses static hash table with linear probing.
 * Branch prediction hint: unlikely to probe more than once.
 *
 * @param[in] port  Port number (host order)
 * @return Socket pointer or NULL if not bound
 */
vos3_socket_t* vos3_udp_lookup_port(uint16_t port)
{
    size_t idx = udp_port_hash(port);

    /* Fast path: direct hit (most common) */
    udp_port_entry_t* entry = &g_udp_port_map[idx];
    if (likely(entry->in_use && entry->port == port)) {
        return entry->socket;
    }

    /* Slow path: linear probe (unlikely) */
    for (size_t i = 1; unlikely(i < UDP_PORT_PROBE_MAX); i++) {
        idx = (idx + 1) & UDP_PORT_HASH_MASK;
        entry = &g_udp_port_map[idx];

        if (!entry->in_use) {
            return NULL;  /* Empty slot = not found */
        }
        if (entry->port == port) {
            return entry->socket;
        }
    }

    return NULL;  /* Probe limit reached */
}

/**
 * @brief Register UDP port binding
 *
 * @param[in] port    Port number (host order)
 * @param[in] socket  Owning socket
 * @return 0 on success, -1 if port taken or table full
 */
int vos3_udp_bind_port(uint16_t port, vos3_socket_t* socket)
{
    if (unlikely(socket == NULL)) {
        return -1;
    }

    vos3_spinlock_lock(&g_udp_port_lock);

    size_t idx = udp_port_hash(port);

    /* Look for empty slot or existing entry */
    for (size_t i = 0; i < UDP_PORT_PROBE_MAX; i++) {
        udp_port_entry_t* entry = &g_udp_port_map[idx];

        if (!entry->in_use) {
            /* Found empty slot - bind here */
            entry->port = port;
            entry->socket = socket;
            entry->in_use = 1;

            vos3_spinlock_unlock(&g_udp_port_lock);
            VOS3_DEBUG("[UDP] Bound port %u to socket fd=%d", port, socket->fd);
            return 0;
        }

        if (entry->port == port) {
            /* Port already in use */
            vos3_spinlock_unlock(&g_udp_port_lock);
            return -1;
        }

        idx = (idx + 1) & UDP_PORT_HASH_MASK;
    }

    vos3_spinlock_unlock(&g_udp_port_lock);
    VOS3_WARN("[UDP] Port hash table congested for port %u", port);
    return -1;
}

/**
 * @brief Unregister UDP port binding
 *
 * @param[in] port  Port number (host order)
 */
void vos3_udp_unbind_port(uint16_t port)
{
    vos3_spinlock_lock(&g_udp_port_lock);

    size_t idx = udp_port_hash(port);

    for (size_t i = 0; i < UDP_PORT_PROBE_MAX; i++) {
        udp_port_entry_t* entry = &g_udp_port_map[idx];

        if (!entry->in_use) {
            break;  /* Not found */
        }

        if (entry->port == port) {
            entry->in_use = 0;
            entry->socket = NULL;
            entry->port = 0;
            VOS3_DEBUG("[UDP] Unbound port %u", port);
            break;
        }

        idx = (idx + 1) & UDP_PORT_HASH_MASK;
    }

    vos3_spinlock_unlock(&g_udp_port_lock);
}

/* ============================================================================
 * UDP CHECKSUM
 * ============================================================================ */

/**
 * @brief UDP pseudo-header for checksum calculation
 */
typedef struct udp_pseudo_header {
    uint32_t    src_ip;
    uint32_t    dst_ip;
    uint8_t     zero;
    uint8_t     protocol;
    uint16_t    udp_length;
} __attribute__((packed)) udp_pseudo_header_t;

/**
 * @brief Calculate UDP checksum
 */
static uint16_t udp_checksum(const udp_header_t* udp,
                              const void* data, size_t data_len,
                              uint32_t src_ip, uint32_t dst_ip)
{
    uint32_t sum = 0;

    /* Pseudo-header */
    udp_pseudo_header_t pseudo = {
        .src_ip = src_ip,
        .dst_ip = dst_ip,
        .zero = 0,
        .protocol = IPPROTO_UDP,
        .udp_length = udp->length
    };

    const uint16_t* ptr = (const uint16_t*)&pseudo;
    for (size_t i = 0; i < sizeof(pseudo) / 2; i++) {
        sum += ptr[i];
    }

    /* UDP header */
    ptr = (const uint16_t*)udp;
    for (size_t i = 0; i < UDP_HEADER_SIZE / 2; i++) {
        sum += ptr[i];
    }

    /* Data */
    ptr = (const uint16_t*)data;
    size_t words = data_len / 2;
    for (size_t i = 0; i < words; i++) {
        sum += ptr[i];
    }
    if (data_len & 1) {
        sum += ((const uint8_t*)data)[data_len - 1];
    }

    /* Fold 32-bit sum to 16 bits */
    while (sum >> 16) {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }

    return (uint16_t)~sum;
}

/* ============================================================================
 * UDP RECEIVE PATH
 * ============================================================================ */

/**
 * @brief Process received UDP packet
 *
 * Called from IP layer when protocol is IPPROTO_UDP.
 * Uses O(1) port lookup to find owning socket.
 *
 * @param[in] netif   Receiving interface
 * @param[in] buf     Network buffer (data starts at UDP header)
 * @param[in] ip_hdr  IP header
 * @return 0 on success, negative on error
 */
int vos3_udp_receive(vos3_netif_t* netif, vos3_netbuf_t* buf,
                     const vos3_ipv4_header_t* ip_hdr)
{
    (void)netif;

    /* SECURITY: Validate parameters */
    if (unlikely(buf == NULL || ip_hdr == NULL)) {
        return -1;
    }

    /* Get UDP header */
    size_t udp_offset = buf->data_offset;
    size_t remaining = buf->len - udp_offset;

    /* SECURITY: Minimum size check */
    if (unlikely(remaining < UDP_HEADER_SIZE)) {
        VOS3_DEBUG("[UDP-SEC] Undersized packet (%zu < %u)", remaining, UDP_HEADER_SIZE);
        g_udp_stats.truncated_drops++;
        return -1;
    }

    const udp_header_t* udp = (const udp_header_t*)(buf->data + udp_offset);

    /* Validate UDP length field */
    uint16_t udp_len = vos3_ntohs(udp->length);
    if (unlikely(udp_len < UDP_HEADER_SIZE || udp_len > remaining)) {
        VOS3_DEBUG("[UDP-SEC] Invalid length field: %u (remaining=%zu)",
                   udp_len, remaining);
        g_udp_stats.truncated_drops++;
        return -1;
    }

    /* Extract ports */
    uint16_t src_port = vos3_ntohs(udp->src_port);
    uint16_t dst_port = vos3_ntohs(udp->dst_port);

    /* SECURITY: Validate UDP length against IP total length.
     * The UDP length field must not exceed what the IP layer delivered.
     * This defends against crafted packets with inflated UDP length. */
    {
        uint16_t ip_total = vos3_ntohs(ip_hdr->total_length);
        uint16_t ip_hdr_len = (uint16_t)((ip_hdr->version_ihl & 0x0F) * 4);
        uint16_t ip_payload = (ip_total > ip_hdr_len) ? (ip_total - ip_hdr_len) : 0;
        if (unlikely(udp_len > ip_payload)) {
            VOS3_DEBUG("[UDP-SEC] UDP length %u exceeds IP payload %u",
                       udp_len, ip_payload);
            g_udp_stats.truncated_drops++;
            return -1;
        }
    }

    /* SECURITY: Validate checksum.
     * RFC 768: For IPv4, checksum field of 0 means "no checksum computed"
     * and the packet should be accepted without verification.
     * For non-zero checksums, verify using pseudo-header computation.
     * A correct checksum yields 0x0000 or 0xFFFF (both valid due to
     * one's complement arithmetic). */
    if (udp->checksum != 0) {
        size_t data_len = udp_len - UDP_HEADER_SIZE;
        const void* data = (const uint8_t*)udp + UDP_HEADER_SIZE;

        uint16_t calc_cksum = udp_checksum(udp, data, data_len,
                                            ip_hdr->src_addr, ip_hdr->dst_addr);
        if (unlikely(calc_cksum != 0 && calc_cksum != 0xFFFF)) {
            VOS3_DEBUG("[UDP-SEC] Checksum error for port %u (computed=0x%04X)",
                       dst_port, calc_cksum);
            g_udp_stats.checksum_errors++;
            return -1;
        }
    }

    /* O(1) PORT DEMUXING: Lookup socket by destination port */
    vos3_socket_t* sock = vos3_udp_lookup_port(dst_port);

    if (unlikely(sock == NULL)) {
        /* No socket listening on this port */
        VOS3_DEBUG("[UDP] No socket for port %u (from %u.%u.%u.%u:%u)",
                   dst_port,
                   (ip_hdr->src_addr) & 0xFF,
                   (ip_hdr->src_addr >> 8) & 0xFF,
                   (ip_hdr->src_addr >> 16) & 0xFF,
                   (ip_hdr->src_addr >> 24) & 0xFF,
                   src_port);
        g_udp_stats.no_port_drops++;
        /* Send ICMP Destination Unreachable (Port Unreachable) */
        vos3_icmp_send_dest_unreach(netif, buf, ip_hdr);
        return -1;
    }

    /* Build source address for socket queue */
    struct sockaddr_in from_addr = {
        .sin_family = AF_INET,
        .sin_port = vos3_htons(src_port),
        .sin_addr = ip_hdr->src_addr
    };

    /* Calculate payload length */
    size_t payload_len = udp_len - UDP_HEADER_SIZE;

    /* Enqueue to socket receive queue */
    /* Note: We need to clone the buffer since the original will be freed */
    vos3_netbuf_t* clone = vos3_netbuf_alloc(buf->len);
    if (unlikely(clone == NULL)) {
        VOS3_DEBUG("[UDP] Failed to allocate buffer for socket queue");
        g_udp_stats.queue_full_drops++;
        return -1;
    }

    memcpy(clone->data, buf->data, buf->len);
    clone->len = buf->len;
    clone->data_offset = buf->data_offset;
    clone->flags = buf->flags;

    /* External declaration */
    extern int vos3_socket_enqueue_packet(vos3_socket_t* sock, vos3_netbuf_t* buf,
                                           const struct sockaddr_in* from, size_t data_len);

    int ret = vos3_socket_enqueue_packet(sock, clone, &from_addr, payload_len);
    if (unlikely(ret != 0)) {
        vos3_netbuf_free(clone);
        g_udp_stats.queue_full_drops++;
        return -1;
    }

    /* Update statistics */
    g_udp_stats.packets_received++;
    g_udp_stats.bytes_received += payload_len;

    VOS3_DEBUG("[UDP] Received %zu bytes on port %u from %u.%u.%u.%u:%u",
               payload_len, dst_port,
               (ip_hdr->src_addr) & 0xFF,
               (ip_hdr->src_addr >> 8) & 0xFF,
               (ip_hdr->src_addr >> 16) & 0xFF,
               (ip_hdr->src_addr >> 24) & 0xFF,
               src_port);

    return 0;
}

/* ============================================================================
 * UDP SEND PATH
 * ============================================================================ */

/**
 * @brief Send UDP packet
 *
 * @param[in] socket    Source socket
 * @param[in] data      Payload data
 * @param[in] len       Payload length
 * @param[in] dest_ip   Destination IP (network order)
 * @param[in] dest_port Destination port (host order)
 * @return Bytes sent on success, negative on error
 */
ssize_t vos3_udp_send(vos3_socket_t* socket, const void* data, size_t len,
                      uint32_t dest_ip, uint16_t dest_port)
{
    if (unlikely(socket == NULL || (len > 0 && data == NULL))) {
        return -1;
    }

    /* SECURITY: Validate payload size */
    if (unlikely(len > UDP_MAX_PAYLOAD)) {
        VOS3_DEBUG("[UDP] Payload too large: %zu > %u", len, UDP_MAX_PAYLOAD);
        return -90;  /* -EMSGSIZE */
    }

    /* Allocate network buffer */
    size_t total_len = VOS3_ETH_HEADER_SIZE + VOS3_IP4_HEADER_MIN + UDP_HEADER_SIZE + len;
    vos3_netbuf_t* buf = vos3_netbuf_alloc(total_len);
    if (unlikely(buf == NULL)) {
        return -12;  /* -ENOMEM */
    }

    /* Build Ethernet header */
    vos3_eth_header_t* eth = (vos3_eth_header_t*)buf->data;

    /* Resolve destination MAC via ARP */
    uint8_t dst_mac[6];
    if (vos3_arp_resolve(dest_ip, dst_mac) == 0) {
        memcpy(eth->dst.bytes, dst_mac, 6);
    } else {
        /* ARP request sent; use broadcast as fallback for this packet */
        memset(eth->dst.bytes, 0xFF, 6);
    }

    /* Get interface - use first available for now */
    extern vos3_netif_t* vos3_net_get_default_interface(void);
    vos3_netif_t* netif = vos3_net_get_default_interface();
    if (netif != NULL) {
        memcpy(eth->src.bytes, netif->mac_addr.bytes, 6);
    } else {
        memset(eth->src.bytes, 0, 6);
    }
    eth->ethertype = vos3_htons(VOS3_ETHERTYPE_IPV4);

    /* Build IP header */
    vos3_ipv4_header_t* ip = (vos3_ipv4_header_t*)(buf->data + VOS3_ETH_HEADER_SIZE);
    ip->version_ihl = 0x45;  /* IPv4, 5*4=20 bytes header */
    ip->tos = 0;
    ip->total_length = vos3_htons((uint16_t)(VOS3_IP4_HEADER_MIN + UDP_HEADER_SIZE + len));
    /* §5.1: per-packet unique 16-bit identification (RFC 791). */
    {
        uint32_t ip_id = vos3_atomic_fetch_add32(&g_ip_id_counter, 1u);
        ip->identification = vos3_htons((uint16_t)(ip_id & 0xFFFFu));
    }
    ip->flags_fragment = 0;
    ip->ttl = 64;
    ip->protocol = IPPROTO_UDP;
    ip->checksum = 0;
    ip->src_addr = socket->local_ip ? socket->local_ip :
                   (netif ? netif->ipv4_addr : 0);
    ip->dst_addr = dest_ip;

    /* Calculate IP checksum */
    uint32_t sum = 0;
    const uint16_t* ip_words = (const uint16_t*)ip;
    for (int i = 0; i < 10; i++) {
        sum += ip_words[i];
    }
    while (sum >> 16) {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }
    ip->checksum = (uint16_t)~sum;

    /* Build UDP header */
    udp_header_t* udp = (udp_header_t*)(buf->data + VOS3_ETH_HEADER_SIZE + VOS3_IP4_HEADER_MIN);
    udp->src_port = vos3_htons(socket->local_port);
    udp->dst_port = vos3_htons(dest_port);
    udp->length = vos3_htons((uint16_t)(UDP_HEADER_SIZE + len));
    udp->checksum = 0;  /* Optional for UDP over IPv4 */

    /* Copy payload */
    if (len > 0) {
        memcpy((uint8_t*)udp + UDP_HEADER_SIZE, data, len);
    }

    /* Calculate UDP checksum */
    udp->checksum = udp_checksum(udp, data, len, ip->src_addr, ip->dst_addr);
    if (udp->checksum == 0) {
        udp->checksum = 0xFFFF;  /* RFC 768: 0 means no checksum */
    }

    /* Set buffer metadata */
    buf->len = (uint16_t)total_len;

    /* Transmit */
    int ret = vos3_net_tx_ethernet(netif, buf);

    if (likely(ret == 0)) {
        g_udp_stats.packets_sent++;
        g_udp_stats.bytes_sent += len;

        VOS3_DEBUG("[UDP] Sent %zu bytes to %u.%u.%u.%u:%u",
                   len,
                   dest_ip & 0xFF,
                   (dest_ip >> 8) & 0xFF,
                   (dest_ip >> 16) & 0xFF,
                   (dest_ip >> 24) & 0xFF,
                   dest_port);
    }

    vos3_netbuf_free(buf);

    return (ret == 0) ? (ssize_t)len : -1;
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize UDP subsystem
 */
void vos3_udp_init(void)
{
    memset(g_udp_port_map, 0, sizeof(g_udp_port_map));
    memset(&g_udp_stats, 0, sizeof(g_udp_stats));
    g_udp_port_lock = VOS3_SPINLOCK_INIT;

    VOS3_INFO("[UDP] UDP subsystem initialized");
    VOS3_INFO("[UDP] Port hash table: %u entries (O(1) demux)", UDP_PORT_HASH_SIZE);
    VOS3_INFO("[UDP] Max probe depth: %u", UDP_PORT_PROBE_MAX);
}

/**
 * @brief Print UDP statistics
 */
void vos3_udp_print_stats(void)
{
    VOS3_INFO("[UDP] Statistics:");
    VOS3_INFO("  Packets received:  %llu",
              (unsigned long long)g_udp_stats.packets_received);
    VOS3_INFO("  Packets sent:      %llu",
              (unsigned long long)g_udp_stats.packets_sent);
    VOS3_INFO("  Bytes received:    %llu",
              (unsigned long long)g_udp_stats.bytes_received);
    VOS3_INFO("  Bytes sent:        %llu",
              (unsigned long long)g_udp_stats.bytes_sent);
    VOS3_INFO("  No-port drops:     %llu",
              (unsigned long long)g_udp_stats.no_port_drops);
    VOS3_INFO("  Checksum errors:   %llu",
              (unsigned long long)g_udp_stats.checksum_errors);
    VOS3_INFO("  Truncated drops:   %llu",
              (unsigned long long)g_udp_stats.truncated_drops);
    VOS3_INFO("  Queue full drops:  %llu",
              (unsigned long long)g_udp_stats.queue_full_drops);
}
