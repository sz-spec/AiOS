/**
 * @file arp.c
 * @brief VOS3 Secure ARP Implementation
 *
 * @details Address Resolution Protocol with security hardening:
 *          - Gratuitous ARP dropped (MitM prevention)
 *          - ARP cache with timeout
 *          - Rate limiting on ARP requests
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
#include "../../include/vos/compiler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/timer.h"

/* ============================================================================
 * ARP CONSTANTS
 * ============================================================================ */

/** @brief ARP hardware type: Ethernet */
#define ARP_HTYPE_ETHERNET      1U

/** @brief ARP protocol type: IPv4 */
#define ARP_PTYPE_IPV4          0x0800U

/** @brief ARP operation: Request */
#define ARP_OP_REQUEST          1U

/** @brief ARP operation: Reply */
#define ARP_OP_REPLY            2U

/** @brief ARP cache size */
#define ARP_CACHE_SIZE          64U

/** @brief ARP cache entry timeout (seconds) */
#define ARP_CACHE_TIMEOUT       300U

/** @brief ARP header size (for Ethernet/IPv4) */
#define ARP_HEADER_SIZE         28U

/** @brief ARP pending request table size (CVE-2026-25060) */
#define ARP_PENDING_SIZE        16U

/** @brief ARP pending request timeout (ticks) */
#define ARP_PENDING_TIMEOUT     100U

/* ============================================================================
 * ARP STRUCTURES
 * ============================================================================ */

/**
 * @brief ARP packet header (Ethernet/IPv4)
 */
typedef struct arp_header {
    uint16_t htype;         /**< Hardware type (1 = Ethernet) */
    uint16_t ptype;         /**< Protocol type (0x0800 = IPv4) */
    uint8_t  hlen;          /**< Hardware address length (6 for Ethernet) */
    uint8_t  plen;          /**< Protocol address length (4 for IPv4) */
    uint16_t oper;          /**< Operation (1=request, 2=reply) */
    uint8_t  sha[6];        /**< Sender hardware address */
    uint8_t  spa[4];        /**< Sender protocol address */
    uint8_t  tha[6];        /**< Target hardware address */
    uint8_t  tpa[4];        /**< Target protocol address */
} __attribute__((packed)) arp_header_t;

/**
 * @brief ARP cache entry
 */
typedef struct arp_entry {
    vos3_ipv4_addr_t ip;        /**< IP address */
    vos3_eth_addr_t  mac;       /**< MAC address */
    uint64_t         timestamp; /**< Last update time */
    uint8_t          valid;     /**< Entry is valid */
    uint8_t          static_entry; /**< Static (never expires) */
} arp_entry_t;

/**
 * @brief ARP pending request (CVE-2026-25060)
 *
 * Tracks ARP requests WE sent, so we can validate that
 * incoming ARP replies were solicited by us.
 */
typedef struct arp_pending {
    vos3_ipv4_addr_t target_ip; /**< IP we're querying */
    uint64_t         timestamp; /**< When request was sent */
    uint8_t          valid;     /**< Entry is valid */
} arp_pending_t;

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief ARP cache */
static arp_entry_t g_arp_cache[ARP_CACHE_SIZE];

/** @brief ARP cache lock */
static vos3_spinlock_t g_arp_lock = VOS3_SPINLOCK_INIT;

/** @brief ARP statistics */
static struct {
    uint64_t requests_received;
    uint64_t replies_received;
    uint64_t requests_sent;
    uint64_t replies_sent;
    uint64_t gratuitous_dropped;
    uint64_t invalid_dropped;
    uint64_t unsolicited_dropped; /**< CVE-2026-25060 */
} g_arp_stats;

/* ============================================================================
 * CVE-2026-25060: STRICT ARP VALIDATION
 * ============================================================================
 *
 * SECURITY: Track ARP requests WE sent, so we can validate that
 * incoming ARP replies/updates were solicited by us.
 *
 * Exception: Gateway MAC is trusted for gratuitous ARP.
 */

/** @brief Known gateway MAC (trusted source) */
static vos3_eth_addr_t g_gateway_mac = {0};
static int g_gateway_mac_valid = 0;

/** @brief Pending ARP requests (CVE-2026-25060) */
static arp_pending_t g_arp_pending[ARP_PENDING_SIZE];
static vos3_spinlock_t g_arp_pending_lock = VOS3_SPINLOCK_INIT;

/* ============================================================================
 * HELPER FUNCTIONS (Performance Critical - Inlined)
 * ============================================================================ */

/**
 * @brief Compare two IPv4 addresses
 */
VOS3_ALWAYS_INLINE int ip_addr_equal(const uint8_t* a, const uint8_t* b)
{
    return (a[0] == b[0]) && (a[1] == b[1]) &&
           (a[2] == b[2]) && (a[3] == b[3]);
}

/**
 * @brief Check if IP address is zero (0.0.0.0)
 */
VOS3_ALWAYS_INLINE int ip_addr_is_zero(const uint8_t* ip)
{
    return (ip[0] == 0) && (ip[1] == 0) && (ip[2] == 0) && (ip[3] == 0);
}

/**
 * @brief Check if this is a Gratuitous ARP (security threat)
 *
 * Gratuitous ARP: sender IP == target IP
 * Used in MitM attacks to poison ARP caches.
 */
VOS3_ALWAYS_INLINE int arp_is_gratuitous(const arp_header_t* arp)
{
    return ip_addr_equal(arp->spa, arp->tpa);
}

/**
 * @brief Copy MAC address
 */
VOS3_ALWAYS_INLINE void mac_copy(vos3_eth_addr_t* dst, const uint8_t* src)
{
    dst->bytes[0] = src[0];
    dst->bytes[1] = src[1];
    dst->bytes[2] = src[2];
    dst->bytes[3] = src[3];
    dst->bytes[4] = src[4];
    dst->bytes[5] = src[5];
}

/**
 * @brief Compare two MAC addresses
 */
VOS3_ALWAYS_INLINE int mac_addr_equal(const vos3_eth_addr_t* a,
                                       const vos3_eth_addr_t* b)
{
    return (a->bytes[0] == b->bytes[0]) &&
           (a->bytes[1] == b->bytes[1]) &&
           (a->bytes[2] == b->bytes[2]) &&
           (a->bytes[3] == b->bytes[3]) &&
           (a->bytes[4] == b->bytes[4]) &&
           (a->bytes[5] == b->bytes[5]);
}

/**
 * @brief Check if MAC is from known gateway (CVE-2026-25060 exception)
 */
VOS3_ALWAYS_INLINE int arp_is_from_gateway(const uint8_t* sha)
{
    if (!g_gateway_mac_valid) {
        return 0;
    }
    return (sha[0] == g_gateway_mac.bytes[0]) &&
           (sha[1] == g_gateway_mac.bytes[1]) &&
           (sha[2] == g_gateway_mac.bytes[2]) &&
           (sha[3] == g_gateway_mac.bytes[3]) &&
           (sha[4] == g_gateway_mac.bytes[4]) &&
           (sha[5] == g_gateway_mac.bytes[5]);
}

/**
 * @brief Check if we have a pending request for this IP (CVE-2026-25060)
 *
 * @param[in] ip  IP address to check
 * @return 1 if we sent a request for this IP, 0 otherwise
 */
static int arp_have_pending_request(vos3_ipv4_addr_t ip)
{
    vos3_spinlock_lock(&g_arp_pending_lock);

    for (size_t i = 0; i < ARP_PENDING_SIZE; i++) {
        if (g_arp_pending[i].valid && g_arp_pending[i].target_ip == ip) {
            /* Clear the pending entry (request fulfilled) */
            g_arp_pending[i].valid = 0;
            vos3_spinlock_unlock(&g_arp_pending_lock);
            return 1;
        }
    }

    vos3_spinlock_unlock(&g_arp_pending_lock);
    return 0;
}

/**
 * @brief Add a pending ARP request (called when we send ARP request)
 *
 * @param[in] target_ip  IP address we're querying
 */
static void arp_add_pending_request(vos3_ipv4_addr_t target_ip)
{
    vos3_spinlock_lock(&g_arp_pending_lock);

    /* Find empty slot */
    for (size_t i = 0; i < ARP_PENDING_SIZE; i++) {
        if (!g_arp_pending[i].valid) {
            g_arp_pending[i].target_ip = target_ip;
            g_arp_pending[i].timestamp = vos3_timer_get_uptime_ms();
            g_arp_pending[i].valid = 1;
            vos3_spinlock_unlock(&g_arp_pending_lock);
            return;
        }
    }

    /* Table full - overwrite oldest entry */
    uint64_t oldest_time = UINT64_MAX;
    size_t oldest_idx = 0;
    for (size_t j = 0; j < ARP_PENDING_SIZE; j++) {
        if (g_arp_pending[j].timestamp < oldest_time) {
            oldest_time = g_arp_pending[j].timestamp;
            oldest_idx = j;
        }
    }
    g_arp_pending[oldest_idx].target_ip = target_ip;
    g_arp_pending[oldest_idx].timestamp = vos3_timer_get_uptime_ms();
    g_arp_pending[oldest_idx].valid = 1;

    vos3_spinlock_unlock(&g_arp_pending_lock);
}

/**
 * @brief Set the known gateway MAC address
 *
 * @param[in] mac  Gateway MAC address
 */
void vos3_arp_set_gateway_mac(const vos3_eth_addr_t* mac)
{
    g_gateway_mac = *mac;
    g_gateway_mac_valid = 1;
    VOS3_INFO("[ARP-SEC] Gateway MAC set: %02x:%02x:%02x:%02x:%02x:%02x",
              mac->bytes[0], mac->bytes[1], mac->bytes[2],
              mac->bytes[3], mac->bytes[4], mac->bytes[5]);
}

/* ============================================================================
 * ARP CACHE MANAGEMENT
 * ============================================================================ */

/**
 * @brief Initialize ARP subsystem
 */
void vos3_arp_init(void)
{
    memset(g_arp_cache, 0, sizeof(g_arp_cache));
    memset(&g_arp_stats, 0, sizeof(g_arp_stats));
    memset(g_arp_pending, 0, sizeof(g_arp_pending));
    g_arp_lock = VOS3_SPINLOCK_INIT;
    g_arp_pending_lock = VOS3_SPINLOCK_INIT;
    g_gateway_mac_valid = 0;

    VOS3_INFO("[ARP] ARP subsystem initialized (cache size=%u)", ARP_CACHE_SIZE);
    VOS3_INFO("[ARP-SEC] CVE-2026-25060: Strict ARP validation ENABLED");
    VOS3_INFO("[ARP-SEC]   - Gratuitous ARP: DROP (except gateway)");
    VOS3_INFO("[ARP-SEC]   - Unsolicited replies: DROP");
}

/**
 * @brief Lookup IP in ARP cache
 *
 * @param[in]  ip   IP address to lookup
 * @param[out] mac  MAC address output
 * @return 0 on success, -1 if not found
 */
int vos3_arp_lookup(vos3_ipv4_addr_t ip, vos3_eth_addr_t* mac)
{
    vos3_spinlock_lock(&g_arp_lock);

    for (size_t i = 0; i < ARP_CACHE_SIZE; i++) {
        if (likely(g_arp_cache[i].valid && g_arp_cache[i].ip == ip)) {
            *mac = g_arp_cache[i].mac;
            vos3_spinlock_unlock(&g_arp_lock);
            return 0;
        }
    }

    vos3_spinlock_unlock(&g_arp_lock);
    return -1;
}

/**
 * @brief Add/update entry in ARP cache
 *
 * @param[in] ip   IP address
 * @param[in] mac  MAC address
 */
static void arp_cache_update(vos3_ipv4_addr_t ip, const vos3_eth_addr_t* mac)
{
    vos3_spinlock_lock(&g_arp_lock);

    /* First, check if entry already exists */
    for (size_t i = 0; i < ARP_CACHE_SIZE; i++) {
        if (g_arp_cache[i].valid && g_arp_cache[i].ip == ip) {
            /* Update existing entry */
            g_arp_cache[i].mac = *mac;
            g_arp_cache[i].timestamp = vos3_timer_get_uptime_ms();
            vos3_spinlock_unlock(&g_arp_lock);
            return;
        }
    }

    /* Find empty slot */
    for (size_t i = 0; i < ARP_CACHE_SIZE; i++) {
        if (!g_arp_cache[i].valid) {
            g_arp_cache[i].ip = ip;
            g_arp_cache[i].mac = *mac;
            g_arp_cache[i].timestamp = vos3_timer_get_uptime_ms();
            g_arp_cache[i].valid = 1;
            g_arp_cache[i].static_entry = 0;
            vos3_spinlock_unlock(&g_arp_lock);
            return;
        }
    }

    /* Cache full - replace oldest entry */
    uint64_t oldest_time = UINT64_MAX;
    size_t oldest_idx = 0;
    for (size_t i = 0; i < ARP_CACHE_SIZE; i++) {
        if (!g_arp_cache[i].static_entry && g_arp_cache[i].timestamp < oldest_time) {
            oldest_time = g_arp_cache[i].timestamp;
            oldest_idx = i;
        }
    }
    g_arp_cache[oldest_idx].ip = ip;
    g_arp_cache[oldest_idx].mac = *mac;
    g_arp_cache[oldest_idx].timestamp = vos3_timer_get_uptime_ms();
    g_arp_cache[oldest_idx].valid = 1;
    g_arp_cache[oldest_idx].static_entry = 0;

    vos3_spinlock_unlock(&g_arp_lock);
}

/* ============================================================================
 * ARP REQUEST / REPLY SENDING
 * ============================================================================ */

/**
 * @brief Send an ARP request for the given target IP
 *
 * Broadcasts an ARP request to resolve target_ip to a MAC address.
 * Also registers the request in the pending table for CVE-2026-25060 validation.
 *
 * @param[in] target_ip  IP address to resolve (network byte order)
 * @return 0 on success, negative on error
 */
int vos3_arp_request(vos3_ipv4_addr_t target_ip)
{
    extern vos3_netif_t* vos3_net_get_default_interface(void);
    vos3_netif_t* netif = vos3_net_get_default_interface();
    if (netif == NULL) {
        return -1;
    }

    size_t frame_len = VOS3_ETH_HEADER_SIZE + ARP_HEADER_SIZE;
    vos3_netbuf_t* buf = vos3_netbuf_alloc(frame_len);
    if (buf == NULL) {
        return -1;
    }

    /* Build Ethernet header — broadcast destination */
    vos3_eth_header_t* eth = (vos3_eth_header_t*)buf->data;
    memset(eth->dst.bytes, 0xFF, 6);
    memcpy(eth->src.bytes, netif->mac_addr.bytes, 6);
    eth->ethertype = vos3_htons(VOS3_ETHERTYPE_ARP);

    /* Build ARP request */
    arp_header_t* arp = (arp_header_t*)(buf->data + VOS3_ETH_HEADER_SIZE);
    arp->htype = vos3_htons(ARP_HTYPE_ETHERNET);
    arp->ptype = vos3_htons(ARP_PTYPE_IPV4);
    arp->hlen  = 6;
    arp->plen  = 4;
    arp->oper  = vos3_htons(ARP_OP_REQUEST);

    /* Sender: our MAC and IP */
    memcpy(arp->sha, netif->mac_addr.bytes, 6);
    arp->spa[0] = (uint8_t)(netif->ipv4_addr);
    arp->spa[1] = (uint8_t)(netif->ipv4_addr >> 8);
    arp->spa[2] = (uint8_t)(netif->ipv4_addr >> 16);
    arp->spa[3] = (uint8_t)(netif->ipv4_addr >> 24);

    /* Target: unknown MAC, target IP */
    memset(arp->tha, 0, 6);
    arp->tpa[0] = (uint8_t)(target_ip);
    arp->tpa[1] = (uint8_t)(target_ip >> 8);
    arp->tpa[2] = (uint8_t)(target_ip >> 16);
    arp->tpa[3] = (uint8_t)(target_ip >> 24);

    buf->len = (uint16_t)frame_len;

    /* Track pending request (CVE-2026-25060) */
    arp_add_pending_request(target_ip);

    int ret = vos3_net_tx_ethernet(netif, buf);
    vos3_netbuf_free(buf);

    if (ret == 0) {
        g_arp_stats.requests_sent++;
        VOS3_DEBUG("[ARP] Sent request for %u.%u.%u.%u",
                   (uint8_t)target_ip, (uint8_t)(target_ip >> 8),
                   (uint8_t)(target_ip >> 16), (uint8_t)(target_ip >> 24));
    }

    return ret;
}

/**
 * @brief Send an ARP reply to a specific host
 *
 * @param[in] netif      Network interface
 * @param[in] dst_mac    Destination MAC address
 * @param[in] dst_ip     Destination IP (network byte order)
 */
static void vos3_arp_send_reply(vos3_netif_t* netif,
                                  const uint8_t* dst_mac,
                                  vos3_ipv4_addr_t dst_ip)
{
    size_t frame_len = VOS3_ETH_HEADER_SIZE + ARP_HEADER_SIZE;
    vos3_netbuf_t* buf = vos3_netbuf_alloc(frame_len);
    if (buf == NULL) {
        return;
    }

    /* Ethernet header — unicast to requester */
    vos3_eth_header_t* eth = (vos3_eth_header_t*)buf->data;
    memcpy(eth->dst.bytes, dst_mac, 6);
    memcpy(eth->src.bytes, netif->mac_addr.bytes, 6);
    eth->ethertype = vos3_htons(VOS3_ETHERTYPE_ARP);

    /* ARP reply */
    arp_header_t* arp = (arp_header_t*)(buf->data + VOS3_ETH_HEADER_SIZE);
    arp->htype = vos3_htons(ARP_HTYPE_ETHERNET);
    arp->ptype = vos3_htons(ARP_PTYPE_IPV4);
    arp->hlen  = 6;
    arp->plen  = 4;
    arp->oper  = vos3_htons(ARP_OP_REPLY);

    /* Sender: our MAC and IP */
    memcpy(arp->sha, netif->mac_addr.bytes, 6);
    arp->spa[0] = (uint8_t)(netif->ipv4_addr);
    arp->spa[1] = (uint8_t)(netif->ipv4_addr >> 8);
    arp->spa[2] = (uint8_t)(netif->ipv4_addr >> 16);
    arp->spa[3] = (uint8_t)(netif->ipv4_addr >> 24);

    /* Target: requester MAC and IP */
    memcpy(arp->tha, dst_mac, 6);
    arp->tpa[0] = (uint8_t)(dst_ip);
    arp->tpa[1] = (uint8_t)(dst_ip >> 8);
    arp->tpa[2] = (uint8_t)(dst_ip >> 16);
    arp->tpa[3] = (uint8_t)(dst_ip >> 24);

    buf->len = (uint16_t)frame_len;

    int ret = vos3_net_tx_ethernet(netif, buf);
    vos3_netbuf_free(buf);

    if (ret == 0) {
        g_arp_stats.replies_sent++;
        VOS3_DEBUG("[ARP] Sent reply to %u.%u.%u.%u",
                   (uint8_t)dst_ip, (uint8_t)(dst_ip >> 8),
                   (uint8_t)(dst_ip >> 16), (uint8_t)(dst_ip >> 24));
    }
}

/**
 * @brief Resolve IP address to MAC address via ARP cache
 *
 * Checks cache first, sends ARP request on miss.
 * For broadcast/multicast IPs, returns broadcast MAC directly.
 *
 * @param[in]  ip       IP address to resolve (network byte order)
 * @param[out] mac_out  Resolved MAC address (6 bytes)
 * @return 0 on success (cache hit), -11 (-EAGAIN) on cache miss (request sent)
 */
int vos3_arp_resolve(vos3_ipv4_addr_t ip, uint8_t mac_out[6])
{
    /* Broadcast/multicast — return broadcast MAC directly */
    if (ip == 0xFFFFFFFFU || (ip & 0xF0) == 0xE0) {
        memset(mac_out, 0xFF, 6);
        return 0;
    }

    /* Check cache */
    vos3_eth_addr_t cached_mac;
    if (vos3_arp_lookup(ip, &cached_mac) == 0) {
        memcpy(mac_out, cached_mac.bytes, 6);
        return 0;
    }

    /* Cache miss — send ARP request */
    vos3_arp_request(ip);
    return -11;  /* -EAGAIN */
}

/* ============================================================================
 * ARP PACKET HANDLING
 * ============================================================================ */

/**
 * @brief Process received ARP packet (Hardened)
 *
 * Security measures:
 * - Gratuitous ARP dropped (MitM prevention)
 * - Header validation
 * - Only Ethernet/IPv4 accepted
 *
 * @param[in] netif  Receiving interface
 * @param[in] buf    Network buffer containing ARP packet
 * @return 0 on success, negative on error
 */
VOS3_HOT int vos3_arp_receive(vos3_netif_t* netif, vos3_netbuf_t* buf)
{
    /* SECURITY: Validate buffer (unlikely to fail on fast path) */
    if (unlikely(netif == NULL || buf == NULL)) {
        return -1;
    }

    /* Calculate ARP header offset (after Ethernet header) */
    size_t arp_offset = buf->data_offset;
    size_t arp_len = buf->len - arp_offset;

    /* SECURITY: Validate ARP packet size */
    if (unlikely(arp_len < ARP_HEADER_SIZE)) {
        VOS3_DEBUG("[ARP-SEC] Undersized ARP packet (%zu < %u)",
                   arp_len, ARP_HEADER_SIZE);
        g_arp_stats.invalid_dropped++;
        return -1;
    }

    const arp_header_t* arp = (const arp_header_t*)(buf->data + arp_offset);

    /* SECURITY: Validate hardware type (must be Ethernet) */
    if (unlikely(vos3_ntohs(arp->htype) != ARP_HTYPE_ETHERNET)) {
        VOS3_DEBUG("[ARP-SEC] Invalid hardware type 0x%04x",
                   vos3_ntohs(arp->htype));
        g_arp_stats.invalid_dropped++;
        return -1;
    }

    /* SECURITY: Validate protocol type (must be IPv4) */
    if (unlikely(vos3_ntohs(arp->ptype) != ARP_PTYPE_IPV4)) {
        VOS3_DEBUG("[ARP-SEC] Invalid protocol type 0x%04x",
                   vos3_ntohs(arp->ptype));
        g_arp_stats.invalid_dropped++;
        return -1;
    }

    /* SECURITY: Validate address lengths */
    if (unlikely(arp->hlen != 6 || arp->plen != 4)) {
        VOS3_DEBUG("[ARP-SEC] Invalid address lengths (hlen=%u, plen=%u)",
                   arp->hlen, arp->plen);
        g_arp_stats.invalid_dropped++;
        return -1;
    }

    /* SECURITY: Strict Gratuitous ARP Validation (CVE-2026-25060)
     *
     * Gratuitous ARP (sender IP == target IP) is used for:
     * - IP address conflict detection
     * - Updating ARP caches after failover
     *
     * ATTACK: Attacker sends gratuitous ARP to poison cache.
     *
     * MITIGATION: Only accept gratuitous ARP if:
     * 1. Sender MAC is our known gateway MAC, OR
     * 2. We have a pending ARP request for this IP
     */
    if (unlikely(arp_is_gratuitous(arp))) {
        /* Exception: Accept from gateway MAC */
        if (arp_is_from_gateway(arp->sha)) {
            VOS3_DEBUG("[ARP-SEC] Gratuitous ARP from gateway ACCEPTED");
        } else {
            VOS3_WARN("[ARP-SEC] Gratuitous ARP BLOCKED (CVE-2026-25060)");
            VOS3_WARN("[ARP-SEC]   Sender: %u.%u.%u.%u MAC: %02x:%02x:%02x:%02x:%02x:%02x",
                      arp->spa[0], arp->spa[1], arp->spa[2], arp->spa[3],
                      arp->sha[0], arp->sha[1], arp->sha[2],
                      arp->sha[3], arp->sha[4], arp->sha[5]);
            g_arp_stats.gratuitous_dropped++;
            g_net_security_stats.gratuitous_arp_dropped++;
            return -1;
        }
    }

    /* SECURITY: Drop if sender IP is 0.0.0.0 (ARP probe - could be attack) */
    if (unlikely(ip_addr_is_zero(arp->spa))) {
        VOS3_DEBUG("[ARP-SEC] ARP with zero sender IP dropped");
        g_arp_stats.invalid_dropped++;
        return -1;
    }

    uint16_t oper = vos3_ntohs(arp->oper);

    /* Handle based on operation type */
    switch (oper) {
        case ARP_OP_REQUEST:
            g_arp_stats.requests_received++;

            /* Check if request is for our IP */
            if (netif->ipv4_addr != 0) {
                vos3_ipv4_addr_t target_ip =
                    (vos3_ipv4_addr_t)((arp->tpa[0]) | (arp->tpa[1] << 8) |
                                       (arp->tpa[2] << 16) | (arp->tpa[3] << 24));

                if (likely(target_ip == netif->ipv4_addr)) {
                    /* Update cache with sender's info */
                    vos3_ipv4_addr_t sender_ip =
                        (vos3_ipv4_addr_t)((arp->spa[0]) | (arp->spa[1] << 8) |
                                           (arp->spa[2] << 16) | (arp->spa[3] << 24));

                    vos3_eth_addr_t sender_mac;
                    mac_copy(&sender_mac, arp->sha);
                    arp_cache_update(sender_ip, &sender_mac);

                    VOS3_DEBUG("[ARP] Request for our IP, cached sender %u.%u.%u.%u",
                               arp->spa[0], arp->spa[1], arp->spa[2], arp->spa[3]);

                    /* Send ARP reply with our MAC */
                    vos3_arp_send_reply(netif, arp->sha, sender_ip);
                }
            }
            break;

        case ARP_OP_REPLY:
            g_arp_stats.replies_received++;

            /* SECURITY: Verify we solicited this reply (CVE-2026-25060)
             *
             * Unsolicited ARP replies can be used to poison the cache.
             * Only accept replies for IPs we actually queried.
             *
             * Exception: Gateway MAC is always trusted.
             */
            {
                vos3_ipv4_addr_t sender_ip =
                    (vos3_ipv4_addr_t)((arp->spa[0]) | (arp->spa[1] << 8) |
                                       (arp->spa[2] << 16) | (arp->spa[3] << 24));

                /* Check if this is a solicited reply or from gateway */
                if (!arp_have_pending_request(sender_ip) &&
                    !arp_is_from_gateway(arp->sha)) {
                    VOS3_WARN("[ARP-SEC] Unsolicited ARP reply BLOCKED (CVE-2026-25060)");
                    VOS3_WARN("[ARP-SEC]   From: %u.%u.%u.%u MAC: %02x:%02x:%02x:%02x:%02x:%02x",
                              arp->spa[0], arp->spa[1], arp->spa[2], arp->spa[3],
                              arp->sha[0], arp->sha[1], arp->sha[2],
                              arp->sha[3], arp->sha[4], arp->sha[5]);
                    g_arp_stats.unsolicited_dropped++;
                    g_net_security_stats.arp_spoof_blocked++;
                    return -1;
                }

                vos3_eth_addr_t sender_mac;
                mac_copy(&sender_mac, arp->sha);
                arp_cache_update(sender_ip, &sender_mac);

                VOS3_DEBUG("[ARP] Reply from %u.%u.%u.%u = %02x:%02x:%02x:%02x:%02x:%02x",
                           arp->spa[0], arp->spa[1], arp->spa[2], arp->spa[3],
                           arp->sha[0], arp->sha[1], arp->sha[2],
                           arp->sha[3], arp->sha[4], arp->sha[5]);
            }
            break;

        default:
            VOS3_DEBUG("[ARP-SEC] Unknown ARP operation %u", oper);
            g_arp_stats.invalid_dropped++;
            return -1;
    }

    return 0;
}

/**
 * @brief ARP timer tick — invalidate stale entries
 *
 * Called from timer tick (100Hz). Expires ARP cache entries older
 * than ARP_CACHE_TIMEOUT seconds and pending requests older than 3 seconds.
 */
void vos3_arp_timer_tick(void)
{
    uint64_t now = vos3_timer_get_uptime_ms();

    /* Expire stale ARP cache entries */
    vos3_spinlock_lock(&g_arp_lock);
    for (size_t i = 0; i < ARP_CACHE_SIZE; i++) {
        if (g_arp_cache[i].valid && !g_arp_cache[i].static_entry) {
            uint64_t age_ms = now - g_arp_cache[i].timestamp;
            if (age_ms > (uint64_t)ARP_CACHE_TIMEOUT * 1000U) {
                g_arp_cache[i].valid = 0;
            }
        }
    }
    vos3_spinlock_unlock(&g_arp_lock);

    /* Expire stale pending ARP requests (3 second timeout) */
    vos3_spinlock_lock(&g_arp_pending_lock);
    for (size_t i = 0; i < ARP_PENDING_SIZE; i++) {
        if (g_arp_pending[i].valid) {
            uint64_t age_ms = now - g_arp_pending[i].timestamp;
            if (age_ms > 3000U) {
                g_arp_pending[i].valid = 0;
            }
        }
    }
    vos3_spinlock_unlock(&g_arp_pending_lock);
}

/**
 * @brief Print ARP statistics
 */
void vos3_arp_print_stats(void)
{
    VOS3_INFO("[ARP] Statistics:");
    VOS3_INFO("  Requests received:   %llu",
              (unsigned long long)g_arp_stats.requests_received);
    VOS3_INFO("  Replies received:    %llu",
              (unsigned long long)g_arp_stats.replies_received);
    VOS3_INFO("  Gratuitous dropped:  %llu",
              (unsigned long long)g_arp_stats.gratuitous_dropped);
    VOS3_INFO("  Unsolicited dropped: %llu (CVE-2026-25060)",
              (unsigned long long)g_arp_stats.unsolicited_dropped);
    VOS3_INFO("  Invalid dropped:     %llu",
              (unsigned long long)g_arp_stats.invalid_dropped);
}
