/**
 * @file net.h
 * @brief VOS3 Network Stack Core Definitions
 *
 * @details Network buffer structures, Ethernet/IP headers, and constants.
 *          Security-first design based on CVE analysis of network drivers.
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 30 - Network Stack Foundation
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_NET_H
#define VOS3_NET_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * SECURITY CONSTANTS (CVE-derived limits)
 * ============================================================================ */

/** @brief Maximum Ethernet frame size (standard + VLAN + safety margin) */
#define VOS3_NET_MTU_MAX            1522U

/** @brief Minimum Ethernet frame size (header + minimum payload) */
#define VOS3_NET_FRAME_MIN          60U

/** @brief Maximum packets processed per interrupt (DoS prevention) */
#define VOS3_NET_IRQ_BUDGET         32U

/** @brief Maximum VirtQueue descriptor index */
#define VOS3_VIRTQ_MAX_DESC         256U

/** @brief Network buffer pool size */
#define VOS3_NET_BUFFER_POOL_SIZE   128U

/** @brief Maximum pending TX packets */
#define VOS3_NET_TX_QUEUE_SIZE      64U

/** @brief Maximum pending RX packets */
#define VOS3_NET_RX_QUEUE_SIZE      64U

/** @brief DMA buffer alignment requirement */
#define VOS3_NET_DMA_ALIGN          16U

/** @brief Ethernet header size */
#define VOS3_ETH_HEADER_SIZE        14U

/** @brief IPv4 header minimum size */
#define VOS3_IP4_HEADER_MIN         20U

/** @brief IPv4 header maximum size (with options) */
#define VOS3_IP4_HEADER_MAX         60U

/* ============================================================================
 * ETHERNET DEFINITIONS
 * ============================================================================ */

/** @brief Ethernet address (MAC) length */
#define VOS3_ETH_ADDR_LEN           6U

/** @brief EtherTypes */
#define VOS3_ETHERTYPE_IPV4         0x0800U
#define VOS3_ETHERTYPE_ARP          0x0806U
#define VOS3_ETHERTYPE_IPV6         0x86DDU
#define VOS3_ETHERTYPE_VLAN         0x8100U

/**
 * @brief Ethernet MAC address
 */
typedef struct vos3_eth_addr {
    uint8_t bytes[VOS3_ETH_ADDR_LEN];
} __attribute__((packed)) vos3_eth_addr_t;

/**
 * @brief Ethernet frame header
 *
 * @note 14 bytes total, no padding
 */
typedef struct vos3_eth_header {
    vos3_eth_addr_t dst;        /**< Destination MAC address */
    vos3_eth_addr_t src;        /**< Source MAC address */
    uint16_t        ethertype;  /**< EtherType (big-endian) */
} __attribute__((packed)) vos3_eth_header_t;

/* ============================================================================
 * IPv4 DEFINITIONS
 * ============================================================================ */

/** @brief IPv4 protocols */
#define VOS3_IPPROTO_ICMP           1U
#define VOS3_IPPROTO_TCP            6U
#define VOS3_IPPROTO_UDP            17U

/**
 * @brief IPv4 address (network byte order)
 */
typedef uint32_t vos3_ipv4_addr_t;

/**
 * @brief IPv4 header
 *
 * @note Minimum 20 bytes, maximum 60 bytes with options
 */
typedef struct vos3_ipv4_header {
    uint8_t     version_ihl;    /**< Version (4 bits) + IHL (4 bits) */
    uint8_t     tos;            /**< Type of Service */
    uint16_t    total_length;   /**< Total length (big-endian) */
    uint16_t    identification; /**< Identification */
    uint16_t    flags_fragment; /**< Flags (3 bits) + Fragment Offset (13 bits) */
    uint8_t     ttl;            /**< Time to Live */
    uint8_t     protocol;       /**< Protocol */
    uint16_t    checksum;       /**< Header checksum (big-endian) */
    vos3_ipv4_addr_t src_addr;  /**< Source IP address */
    vos3_ipv4_addr_t dst_addr;  /**< Destination IP address */
    /* Options may follow */
} __attribute__((packed)) vos3_ipv4_header_t;

/* ============================================================================
 * NETWORK BUFFER
 * ============================================================================ */

/** @brief Network buffer flags */
#define VOS3_NETBUF_F_RX            0x0001U  /**< Received packet */
#define VOS3_NETBUF_F_TX            0x0002U  /**< Packet for transmission */
#define VOS3_NETBUF_F_ALLOCATED     0x0004U  /**< Buffer is allocated */
#define VOS3_NETBUF_F_DMA           0x0008U  /**< DMA-capable buffer */
#define VOS3_NETBUF_F_VALIDATED     0x0010U  /**< Packet passed validation */
#define VOS3_NETBUF_F_HOSTILE       0x0020U  /**< Untrusted external data */

/**
 * @brief Network buffer structure
 *
 * @note Contains metadata and actual packet data.
 *       All external data is marked hostile until validated.
 */
typedef struct vos3_netbuf {
    /** @brief Buffer flags */
    uint32_t        flags;

    /** @brief Actual data length (validated, <= capacity) */
    uint16_t        len;

    /** @brief Buffer capacity */
    uint16_t        capacity;

    /** @brief Data offset from start of buffer */
    uint16_t        data_offset;

    /** @brief Protocol (after parsing) */
    uint16_t        protocol;

    /** @brief Physical address for DMA */
    uint64_t        phys_addr;

    /** @brief Timestamp (receive time) */
    uint64_t        timestamp;

    /** @brief Interface index */
    uint8_t         ifindex;

    /** @brief Reserved for alignment */
    uint8_t         reserved[7];

    /** @brief Packet data (flexible array) */
    uint8_t         data[];
} __attribute__((aligned(VOS3_NET_DMA_ALIGN))) vos3_netbuf_t;

/* ============================================================================
 * NETWORK INTERFACE
 * ============================================================================ */

/** @brief Interface flags */
#define VOS3_IFF_UP                 0x0001U  /**< Interface is up */
#define VOS3_IFF_RUNNING            0x0002U  /**< Interface is running */
#define VOS3_IFF_PROMISC            0x0004U  /**< Promiscuous mode */
#define VOS3_IFF_MULTICAST          0x0008U  /**< Supports multicast */

/** @brief Maximum interface name length */
#define VOS3_IFNAMSIZ               16U

/** @brief Forward declaration */
struct vos3_netif;

/**
 * @brief Network interface operations
 */
typedef struct vos3_netif_ops {
    /** @brief Start interface */
    int (*start)(struct vos3_netif* netif);

    /** @brief Stop interface */
    int (*stop)(struct vos3_netif* netif);

    /** @brief Transmit packet */
    int (*transmit)(struct vos3_netif* netif, vos3_netbuf_t* buf);

    /** @brief Set MAC address */
    int (*set_mac)(struct vos3_netif* netif, const vos3_eth_addr_t* mac);

    /** @brief Set promiscuous mode */
    int (*set_promisc)(struct vos3_netif* netif, int enable);
} vos3_netif_ops_t;

/**
 * @brief Network interface structure
 */
typedef struct vos3_netif {
    /** @brief Interface name */
    char            name[VOS3_IFNAMSIZ];

    /** @brief Interface index */
    uint8_t         index;

    /** @brief Interface flags */
    uint32_t        flags;

    /** @brief Hardware MAC address */
    vos3_eth_addr_t mac_addr;

    /** @brief IPv4 address */
    vos3_ipv4_addr_t ipv4_addr;

    /** @brief IPv4 netmask */
    vos3_ipv4_addr_t ipv4_netmask;

    /** @brief IPv4 gateway */
    vos3_ipv4_addr_t ipv4_gateway;

    /** @brief MTU */
    uint16_t        mtu;

    /** @brief Operations */
    const vos3_netif_ops_t* ops;

    /** @brief Driver private data */
    void*           priv;

    /** @brief Statistics */
    struct {
        uint64_t    rx_packets;
        uint64_t    tx_packets;
        uint64_t    rx_bytes;
        uint64_t    tx_bytes;
        uint64_t    rx_errors;
        uint64_t    tx_errors;
        uint64_t    rx_dropped;
        uint64_t    tx_dropped;
        uint64_t    rx_invalid;     /**< Security: packets failed validation */
        uint64_t    rx_oversized;   /**< Security: oversized packets dropped */
        uint64_t    rx_ipv6_dropped; /**< IPv6 packets dropped (not implemented) */
    } stats;
} vos3_netif_t;

/* ============================================================================
 * BYTE ORDER UTILITIES
 * ============================================================================ */

/** @brief Convert 16-bit value from network to host byte order */
static inline uint16_t vos3_ntohs(uint16_t netshort)
{
    return (uint16_t)((netshort >> 8) | (netshort << 8));
}

/** @brief Convert 16-bit value from host to network byte order */
static inline uint16_t vos3_htons(uint16_t hostshort)
{
    return (uint16_t)((hostshort >> 8) | (hostshort << 8));
}

/** @brief Convert 32-bit value from network to host byte order */
static inline uint32_t vos3_ntohl(uint32_t netlong)
{
    return ((netlong >> 24) & 0xFFU) |
           ((netlong >> 8) & 0xFF00U) |
           ((netlong << 8) & 0xFF0000U) |
           ((netlong << 24) & 0xFF000000U);
}

/** @brief Convert 32-bit value from host to network byte order */
static inline uint32_t vos3_htonl(uint32_t hostlong)
{
    return ((hostlong >> 24) & 0xFFU) |
           ((hostlong >> 8) & 0xFF00U) |
           ((hostlong << 8) & 0xFF0000U) |
           ((hostlong << 24) & 0xFF000000U);
}

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize network subsystem
 * @return 0 on success, negative error code on failure
 */
int vos3_net_init(void);

/**
 * @brief Allocate a network buffer
 *
 * @param[in] size  Required data capacity
 * @return Pointer to network buffer, or NULL on failure
 */
vos3_netbuf_t* vos3_netbuf_alloc(size_t size);

/**
 * @brief Free a network buffer
 *
 * @param[in] buf  Buffer to free
 */
void vos3_netbuf_free(vos3_netbuf_t* buf);

/**
 * @brief Register a network interface
 *
 * @param[in] netif  Interface to register
 * @return 0 on success, negative error code on failure
 */
int vos3_netif_register(vos3_netif_t* netif);

/**
 * @brief Process received Ethernet frame
 *
 * @param[in] netif  Receiving interface
 * @param[in] buf    Network buffer containing frame
 * @return 0 on success, negative error code on failure
 */
int vos3_net_rx_ethernet(vos3_netif_t* netif, vos3_netbuf_t* buf);

/**
 * @brief Transmit Ethernet frame
 *
 * @param[in] netif  Transmitting interface
 * @param[in] buf    Network buffer containing frame
 * @return 0 on success, negative error code on failure
 */
int vos3_net_tx_ethernet(vos3_netif_t* netif, vos3_netbuf_t* buf);

/**
 * @brief Print network security statistics
 */
void vos3_net_print_security_stats(void);

/**
 * @brief Run network security self-tests
 */
void vos3_net_run_security_tests(void);

/**
 * @brief Simulate flood attack for security testing
 *
 * @param[in] num_packets  Number of packets to simulate
 * @return Number of throttling events (>0 means throttling worked)
 */
int vos3_net_test_flood_attack(size_t num_packets);

/**
 * @brief Initialize VirtIO-Net driver
 * @return 0 on success, negative error code on failure
 */
int vos3_virtio_net_init(void);

/**
 * @brief Run network driver stress test suite
 *
 * Executes 4 attack scenarios:
 * - Scenario A: Giants (oversized packets)
 * - Scenario B: Dwarves (undersized packets)
 * - Scenario C: Storm (5000 packet flood)
 * - Scenario D: Spoof (MAC address spoofing)
 *
 * @return 1 if all tests passed, 0 if any failed
 */
int vos3_net_stress_test(void);

/* ============================================================================
 * PROTOCOL HANDLERS (Day 4)
 * ============================================================================ */

/**
 * @brief Initialize ARP subsystem
 */
void vos3_arp_init(void);

/**
 * @brief Process received ARP packet
 *
 * @param[in] netif  Receiving interface
 * @param[in] buf    Network buffer containing ARP packet
 * @return 0 on success, negative on error
 */
int vos3_arp_receive(vos3_netif_t* netif, vos3_netbuf_t* buf);

/**
 * @brief Lookup IP address in ARP cache
 *
 * @param[in]  ip   IP address to lookup
 * @param[out] mac  MAC address output
 * @return 0 on success, -1 if not found
 */
int vos3_arp_lookup(vos3_ipv4_addr_t ip, vos3_eth_addr_t* mac);

/**
 * @brief Send an ARP request for the given target IP
 *
 * @param[in] target_ip  IP address to resolve (network byte order)
 * @return 0 on success, negative on error
 */
int vos3_arp_request(vos3_ipv4_addr_t target_ip);

/**
 * @brief Resolve IP to MAC via ARP cache, sending request on miss
 *
 * @param[in]  ip       IP address to resolve (network byte order)
 * @param[out] mac_out  Resolved MAC address (6 bytes)
 * @return 0 on cache hit, -11 (-EAGAIN) on cache miss (request sent)
 */
int vos3_arp_resolve(vos3_ipv4_addr_t ip, uint8_t mac_out[6]);

/**
 * @brief Initialize IPv4 subsystem
 */
void vos3_ip_init(void);

/**
 * @brief Process received IPv4 packet
 *
 * @param[in] netif  Receiving interface
 * @param[in] buf    Network buffer containing IPv4 packet
 * @return 0 on success, negative on error
 */
int vos3_ip_receive(vos3_netif_t* netif, vos3_netbuf_t* buf);

/**
 * @brief Initialize ICMP subsystem
 */
void vos3_icmp_init(void);

/**
 * @brief Process received ICMP packet
 *
 * @param[in] netif   Receiving interface
 * @param[in] buf     Network buffer containing ICMP packet
 * @param[in] ip_hdr  IP header (for generating replies)
 * @return 0 on success, negative on error
 */
int vos3_icmp_receive(vos3_netif_t* netif, vos3_netbuf_t* buf,
                       const vos3_ipv4_header_t* ip_hdr);

/**
 * @brief Test ICMP ping functionality
 *
 * @param[in] netif  Network interface
 * @return 0 if ping reply generated, -1 on error
 */
int vos3_icmp_test_ping(vos3_netif_t* netif);

/**
 * @brief Test ICMP rate limiting
 *
 * @param[in] netif  Network interface
 * @param[in] count  Number of pings to send
 * @return Number of rate-limited pings
 */
int vos3_icmp_test_rate_limit(vos3_netif_t* netif, int count);

/**
 * @brief Reset ICMP rate limiter for testing
 *
 * Resets the token bucket to full capacity.
 */
void vos3_icmp_reset_bucket(void);

/* ============================================================================
 * DAY 4 SECURITY TESTS (February 2026 CVE Mitigations)
 * ============================================================================ */

/**
 * @brief Run Day 4 security test suite
 *
 * Verifies mitigations for:
 * - CVE-2026-23086: Resource DoS (TX Truncation)
 * - CVE-2026-25060: ARP MitM (Strict Validation)
 * - CVE-2026-23057: Info Leak (Buffer Sanitization)
 *
 * @return 1 if all tests passed, 0 if any failed
 */
int vos3_day4_security_test(void);

/**
 * @brief Set gateway MAC for ARP validation
 *
 * @param[in] mac  Gateway MAC address (trusted source)
 */
void vos3_arp_set_gateway_mac(const vos3_eth_addr_t* mac);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_NET_H */
