/**
 * @file socket.h
 * @brief VOS3 POSIX Socket API Definitions
 *
 * @details Socket structures and syscall declarations for user-space
 *          network access. Security-first design with Privacy Shield.
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 5 - UDP Protocol & POSIX Socket API
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_SOCKET_H
#define VOS3_SOCKET_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "net.h"
#include "compiler.h"

/* POSIX ssize_t for return values */
typedef long ssize_t;

/* ============================================================================
 * SOCKET CONSTANTS
 * ============================================================================ */

/** @brief Address families */
#define AF_UNSPEC       0       /**< Unspecified */
#define AF_INET         2       /**< IPv4 Internet protocols */

/** @brief Socket types */
#define SOCK_STREAM     1       /**< TCP stream socket */
#define SOCK_DGRAM      2       /**< UDP datagram socket */
#define SOCK_RAW        3       /**< Raw socket */

/** @brief Protocols */
#define IPPROTO_IP      0       /**< Dummy protocol */
#define IPPROTO_ICMP    1       /**< ICMP */
#define IPPROTO_TCP     6       /**< TCP */
#define IPPROTO_UDP     17      /**< UDP */

/** @brief Socket flags */
#define MSG_PEEK        0x02    /**< Peek at incoming message */
#define MSG_DONTWAIT    0x40    /**< Nonblocking operation */
#define MSG_TRUNC       0x20    /**< Data truncated */

/** @brief Socket options */
#define SOL_SOCKET      1       /**< Socket level */
#define SO_REUSEADDR    2       /**< Reuse local address */
#define SO_BROADCAST    6       /**< Permit broadcast */
#define SO_RCVBUF       8       /**< Receive buffer size */
#define SO_SNDBUF       7       /**< Send buffer size */

/** @brief Special addresses */
#define INADDR_ANY      0x00000000U     /**< 0.0.0.0 */
#define INADDR_LOOPBACK 0x7F000001U     /**< 127.0.0.1 (host order) */
#define INADDR_BROADCAST 0xFFFFFFFFU    /**< 255.255.255.255 */

/* ============================================================================
 * SECURITY CONSTANTS (Privacy Shield)
 * ============================================================================ */

/** @brief Privileged port threshold (requires VOS3_CAP_NET_ADMIN) */
#define VOS3_PRIVILEGED_PORT_MAX    1024U

/** @brief Capability: Network administration */
#define VOS3_CAP_NET_ADMIN          0x00001000U

/** @brief Maximum sockets per process */
#define VOS3_MAX_SOCKETS_PER_PROC   64U

/** @brief Maximum UDP port number */
#define VOS3_UDP_PORT_MAX           65535U

/** @brief UDP port hash table size (O(1) lookup) */
#define VOS3_UDP_PORT_HASH_SIZE     1024U

/** @brief Socket receive queue max packets */
#define VOS3_SOCKET_RX_QUEUE_MAX    32U

/** @brief Socket receive buffer size */
#define VOS3_SOCKET_RXBUF_SIZE      65536U

/* ============================================================================
 * SOCKET ADDRESS STRUCTURES (POSIX Compatible)
 * ============================================================================ */

/**
 * @brief Generic socket address (sa_family + 14 bytes data)
 */
struct sockaddr {
    uint16_t    sa_family;      /**< Address family */
    char        sa_data[14];    /**< Address data */
};

/**
 * @brief IPv4 socket address
 */
struct sockaddr_in {
    uint16_t            sin_family;     /**< AF_INET */
    uint16_t            sin_port;       /**< Port (network byte order) */
    uint32_t            sin_addr;       /**< IPv4 address (network byte order) */
    uint8_t             sin_zero[8];    /**< Padding to 16 bytes */
} __attribute__((packed));

/**
 * @brief Socket address storage (large enough for any address type)
 */
struct sockaddr_storage {
    uint16_t    ss_family;
    char        __ss_pad[126];  /**< Padding */
};

/** @brief Socket address length type */
typedef uint32_t socklen_t;

/* ============================================================================
 * VOS3 SOCKET STRUCTURE
 * ============================================================================ */

/** @brief Socket state */
typedef enum vos3_socket_state {
    VOS3_SOCK_UNBOUND = 0,      /**< Created but not bound */
    VOS3_SOCK_BOUND,            /**< Bound to local address */
    VOS3_SOCK_LISTENING,        /**< Listening (TCP only) */
    VOS3_SOCK_CONNECTED,        /**< Connected (TCP/connected UDP) */
    VOS3_SOCK_CLOSED            /**< Closed */
} vos3_socket_state_t;

/* ============================================================================
 * SOCKET OPERATIONS VTABLE (Protocol Dispatch)
 * ============================================================================ */

/** Forward declaration */
struct vos3_socket;

/**
 * @brief Protocol-specific socket operations.
 *
 * Each protocol (UDP, TCP, future UNIX/raw) implements this interface.
 * Syscall handlers dispatch through sock->ops after common validation.
 */
typedef struct vos3_socket_ops {
    /** @brief Send data (protocol-specific path) */
    ssize_t (*send)(struct vos3_socket* sock,
                    const void* kbuf, size_t len, int flags);
    /** @brief Receive data (protocol-specific path) */
    ssize_t (*recv)(struct vos3_socket* sock,
                    void* kbuf, size_t len, int flags);
    /** @brief Initiate connection */
    int     (*connect)(struct vos3_socket* sock,
                       uint32_t ip, uint16_t port);
    /** @brief Start listening for connections */
    int     (*listen)(struct vos3_socket* sock, int backlog);
    /** @brief Accept a pending connection (returns new FD) */
    int     (*accept)(struct vos3_socket* sock,
                      struct sockaddr* addr, socklen_t* addrlen);
    /** @brief Protocol-specific close/cleanup */
    int     (*close)(struct vos3_socket* sock);
} vos3_socket_ops_t;

/** @brief UDP protocol operations */
extern const vos3_socket_ops_t g_udp_socket_ops;
/** @brief TCP protocol operations */
extern const vos3_socket_ops_t g_tcp_socket_ops;

/**
 * @brief Receive queue entry
 */
typedef struct vos3_rx_packet {
    vos3_netbuf_t*      buf;            /**< Network buffer */
    struct sockaddr_in  from_addr;      /**< Source address */
    uint16_t            data_len;       /**< Payload length */
    uint8_t             valid;          /**< Entry is valid */
    uint8_t             reserved;
} vos3_rx_packet_t;

/**
 * @brief VOS3 Socket Structure
 *
 * Holds all state for a user-space socket including local/remote
 * addresses and receive queue.
 */
typedef struct vos3_socket {
    /** @brief Socket identifier */
    int32_t             fd;             /**< File descriptor */

    /** @brief Protocol dispatch */
    const vos3_socket_ops_t* ops;       /**< Protocol operations vtable */

    /** @brief Socket properties */
    uint16_t            domain;         /**< Address family (AF_INET) */
    uint16_t            type;           /**< Socket type (SOCK_DGRAM) */
    uint16_t            protocol;       /**< Protocol (IPPROTO_UDP) */
    uint16_t            state;          /**< Socket state */

    /** @brief Local address (after bind) */
    uint32_t            local_ip;       /**< Local IP (network order) */
    uint16_t            local_port;     /**< Local port (host order) */

    /** @brief Remote address (for connected sockets) */
    uint32_t            remote_ip;      /**< Remote IP (network order) */
    uint16_t            remote_port;    /**< Remote port (host order) */

    /** @brief Owning task */
    uint32_t            owner_pid;      /**< Owner process ID */
    uint32_t            owner_caps;     /**< Owner capabilities */

    /** @brief Receive queue (circular buffer) */
    vos3_rx_packet_t    rx_queue[VOS3_SOCKET_RX_QUEUE_MAX];
    uint8_t             rx_head;        /**< Queue head index */
    uint8_t             rx_tail;        /**< Queue tail index */
    uint8_t             rx_count;       /**< Packets in queue */
    uint8_t             rx_overflow;    /**< Overflow counter */

    /** @brief Socket options */
    uint32_t            flags;          /**< Socket flags */
    uint32_t            rx_bufsize;     /**< Receive buffer size */
    uint32_t            tx_bufsize;     /**< Send buffer size */

    /** @brief Statistics */
    uint64_t            rx_packets;     /**< Received packets */
    uint64_t            tx_packets;     /**< Transmitted packets */
    uint64_t            rx_bytes;       /**< Received bytes */
    uint64_t            tx_bytes;       /**< Transmitted bytes */

    /** @brief Blocking recv support */
    volatile uint8_t    rx_waiting;     /**< 1 = a task is blocked waiting for data */
    void*               rx_waiter;      /**< Task pointer blocked on recv (vos3_task_t*) */

    /** @brief Allocation tracking */
    uint8_t             allocated;      /**< Socket is allocated */
    uint8_t             reserved[5];    /**< Padding */
} vos3_socket_t;

/* ============================================================================
 * UDP PORT MAP (O(1) Demuxing)
 * ============================================================================ */

/**
 * @brief UDP port map entry
 */
typedef struct vos3_udp_port_entry {
    vos3_socket_t*      socket;         /**< Owning socket */
    uint16_t            port;           /**< Port number */
    uint8_t             in_use;         /**< Entry is in use */
    uint8_t             reserved;
} vos3_udp_port_entry_t;

/* ============================================================================
 * SOCKET SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_SOCKET      430     /**< Create socket */
#define SYS_BIND        431     /**< Bind socket */
#define SYS_LISTEN      432     /**< Listen for connections (TCP) */
#define SYS_ACCEPT      433     /**< Accept connection (TCP) */
#define SYS_CONNECT     434     /**< Connect to remote (TCP/UDP) */
#define SYS_SENDTO      435     /**< Send datagram */
#define SYS_RECVFROM    436     /**< Receive datagram */
#define SYS_SEND        437     /**< Send on connected socket */
#define SYS_RECV        438     /**< Receive on connected socket */
#define SYS_SHUTDOWN    439     /**< Shutdown socket */
#define SYS_GETSOCKOPT  440     /**< Get socket option */
#define SYS_SETSOCKOPT  441     /**< Set socket option */

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize socket subsystem
 * @return 0 on success, negative on error
 */
int vos3_socket_init(void);

/**
 * @brief Create a new socket
 *
 * @param[in] domain    Address family (AF_INET)
 * @param[in] type      Socket type (SOCK_DGRAM)
 * @param[in] protocol  Protocol (0 or IPPROTO_UDP)
 * @return File descriptor on success, negative error code on failure
 */
int vos3_sys_socket(int domain, int type, int protocol);

/**
 * @brief Bind socket to local address
 *
 * @param[in] fd        Socket file descriptor
 * @param[in] addr      Local address (user pointer)
 * @param[in] addrlen   Address length
 * @return 0 on success, negative error code on failure
 *
 * @note Ports < 1024 require VOS3_CAP_NET_ADMIN capability
 */
int vos3_sys_bind(int fd, const struct sockaddr* addr, socklen_t addrlen);

/**
 * @brief Send datagram to specified address
 *
 * @param[in] fd        Socket file descriptor
 * @param[in] buf       Data buffer (user pointer)
 * @param[in] len       Data length
 * @param[in] flags     Send flags
 * @param[in] dest_addr Destination address (user pointer)
 * @param[in] addrlen   Address length
 * @return Bytes sent on success, negative error code on failure
 */
ssize_t vos3_sys_sendto(int fd, const void* buf, size_t len, int flags,
                        const struct sockaddr* dest_addr, socklen_t addrlen);

/**
 * @brief Receive datagram and source address
 *
 * @param[in]  fd        Socket file descriptor
 * @param[out] buf       Data buffer (user pointer)
 * @param[in]  len       Buffer length
 * @param[in]  flags     Receive flags
 * @param[out] src_addr  Source address (user pointer, may be NULL)
 * @param[in,out] addrlen Address length (user pointer, may be NULL)
 * @return Bytes received on success, negative error code on failure
 *
 * @note Buffer is sanitized (zeroed beyond data) per CVE-2026-23057
 */
ssize_t vos3_sys_recvfrom(int fd, void* buf, size_t len, int flags,
                          struct sockaddr* src_addr, socklen_t* addrlen);

/**
 * @brief Close socket
 *
 * @param[in] fd  Socket file descriptor
 * @return 0 on success, negative error code on failure
 */
int vos3_sys_closesocket(int fd);

/* ============================================================================
 * UDP LAYER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize UDP subsystem
 */
void vos3_udp_init(void);

/**
 * @brief Process received UDP packet
 *
 * @param[in] netif   Receiving interface
 * @param[in] buf     Network buffer
 * @param[in] ip_hdr  IP header
 * @return 0 on success, negative on error
 */
int vos3_udp_receive(vos3_netif_t* netif, vos3_netbuf_t* buf,
                     const vos3_ipv4_header_t* ip_hdr);

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
                      uint32_t dest_ip, uint16_t dest_port);

/**
 * @brief Register UDP port binding
 *
 * @param[in] port    Port number (host order)
 * @param[in] socket  Owning socket
 * @return 0 on success, -EADDRINUSE if port taken
 */
int vos3_udp_bind_port(uint16_t port, vos3_socket_t* socket);

/**
 * @brief Unregister UDP port binding
 *
 * @param[in] port  Port number (host order)
 */
void vos3_udp_unbind_port(uint16_t port);

/**
 * @brief Lookup socket by UDP port (O(1))
 *
 * @param[in] port  Port number (host order)
 * @return Socket pointer or NULL if not bound
 */
vos3_socket_t* vos3_udp_lookup_port(uint16_t port);

/**
 * @brief Print UDP statistics
 */
void vos3_udp_print_stats(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SOCKET_H */
