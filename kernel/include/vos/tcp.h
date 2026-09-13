/**
 * @file tcp.h
 * @brief VOS3 TCP Protocol Definitions
 *
 * @details Transmission Control Protocol implementation with:
 *          - Full state machine (RFC 793)
 *          - Sequence number randomization (2026 security)
 *          - Sliding window flow control
 *
 * @version 1.0.0
 * @date 2026-02-19
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 6 - TCP Protocol & Reliable Transport
 */

#ifndef VOS3_TCP_H
#define VOS3_TCP_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "net.h"
#include "socket.h"
#include "compiler.h"

/* ============================================================================
 * TCP CONSTANTS
 * ============================================================================ */

/** @brief TCP header flags */
#define TCP_FLAG_FIN    0x01    /**< No more data from sender */
#define TCP_FLAG_SYN    0x02    /**< Synchronize sequence numbers */
#define TCP_FLAG_RST    0x04    /**< Reset the connection */
#define TCP_FLAG_PSH    0x08    /**< Push function */
#define TCP_FLAG_ACK    0x10    /**< Acknowledgment field valid */
#define TCP_FLAG_URG    0x20    /**< Urgent pointer valid */
#define TCP_FLAG_ECE    0x40    /**< ECN-Echo */
#define TCP_FLAG_CWR    0x80    /**< Congestion Window Reduced */

/** @brief TCP connection states (RFC 793) */
typedef enum vos3_tcp_state {
    TCP_STATE_CLOSED        = 0,    /**< No connection */
    TCP_STATE_LISTEN        = 1,    /**< Waiting for connection */
    TCP_STATE_SYN_SENT      = 2,    /**< SYN sent, awaiting SYN-ACK */
    TCP_STATE_SYN_RECEIVED  = 3,    /**< SYN received, sent SYN-ACK */
    TCP_STATE_ESTABLISHED   = 4,    /**< Connection established */
    TCP_STATE_FIN_WAIT_1    = 5,    /**< FIN sent, awaiting ACK */
    TCP_STATE_FIN_WAIT_2    = 6,    /**< FIN acked, awaiting FIN */
    TCP_STATE_CLOSE_WAIT    = 7,    /**< FIN received, awaiting close */
    TCP_STATE_CLOSING       = 8,    /**< Both sides closing */
    TCP_STATE_LAST_ACK      = 9,    /**< Awaiting final ACK */
    TCP_STATE_TIME_WAIT     = 10    /**< Waiting for stale packets */
} vos3_tcp_state_t;

/** @brief TCP configuration constants */
#define TCP_MSS_DEFAULT         1460U   /**< Default Maximum Segment Size */
#define TCP_WINDOW_DEFAULT      65535U  /**< Default receive window */
#define TCP_WINDOW_SCALE_MAX    14U     /**< Maximum window scale */
#define TCP_MAX_RETRIES         5U      /**< Maximum retransmission attempts */
#define TCP_RTO_MIN_MS          200U    /**< Minimum retransmission timeout */
#define TCP_RTO_MAX_MS          60000U  /**< Maximum retransmission timeout */
#define TCP_TIME_WAIT_MS        120000U /**< TIME_WAIT duration (2*MSL) */
#define TCP_KEEPALIVE_MS        7200000U /**< Keepalive interval (2 hours) */

/** @brief TCP Protocol Control Block pool size */
#define TCP_PCB_POOL_SIZE       128U

/** @brief TCP receive buffer size */
#define TCP_RX_BUFFER_SIZE      8192U

/** @brief TCP send buffer size */
#define TCP_TX_BUFFER_SIZE      8192U

/** @brief TCP backlog queue size (for listen) */
#define TCP_BACKLOG_MAX         16U

/* ============================================================================
 * TCP HEADER STRUCTURE
 * ============================================================================ */

/**
 * @brief TCP header (20 bytes minimum, up to 60 with options)
 */
typedef struct __attribute__((packed)) vos3_tcp_header {
    uint16_t    src_port;       /**< Source port */
    uint16_t    dst_port;       /**< Destination port */
    uint32_t    seq_num;        /**< Sequence number */
    uint32_t    ack_num;        /**< Acknowledgment number */
    uint8_t     data_offset;    /**< Data offset (4 bits) + reserved (4 bits) */
    uint8_t     flags;          /**< Control flags */
    uint16_t    window;         /**< Receive window size */
    uint16_t    checksum;       /**< Header + data checksum */
    uint16_t    urgent_ptr;     /**< Urgent pointer (if URG set) */
    /* Options follow if data_offset > 5 */
} vos3_tcp_header_t;

_Static_assert(sizeof(vos3_tcp_header_t) == 20, "TCP header must be 20 bytes");

/* ============================================================================
 * SACK BLOCK TYPE (RFC 2018 — Phase 7.4)
 * ============================================================================ */

/** @brief Maximum SACK blocks per segment (RFC 2018 §3) */
#define TCP_MAX_SACK_BLOCKS 4U

/**
 * @brief SACK block — contiguous received byte range
 * @details Left edge = first seq of received block,
 *          Right edge = seq after last byte of block.
 */
typedef struct vos3_tcp_sack_block {
    uint32_t    left;           /**< Left edge (first seq) */
    uint32_t    right;          /**< Right edge (seq past last byte) */
} vos3_tcp_sack_block_t;

/* ============================================================================
 * TCP PROTOCOL CONTROL BLOCK
 * ============================================================================ */

/**
 * @brief TCP retransmission queue entry
 */
typedef struct vos3_tcp_rexmit_entry {
    uint8_t*    data;           /**< Segment data */
    uint16_t    len;            /**< Data length */
    uint32_t    seq;            /**< Sequence number */
    uint32_t    tx_time;        /**< Transmission timestamp */
    uint8_t     retries;        /**< Retransmission count */
    uint8_t     valid;          /**< Entry valid flag */
} vos3_tcp_rexmit_entry_t;

/**
 * @brief TCP connection backlog entry (for listening sockets)
 */
typedef struct vos3_tcp_backlog_entry {
    uint32_t    remote_ip;      /**< Remote IP address */
    uint16_t    remote_port;    /**< Remote port */
    uint32_t    irs;            /**< Initial receive sequence */
    uint32_t    iss;            /**< Initial send sequence */
    uint8_t     valid;          /**< Entry valid flag */
    uint8_t     wscale;         /**< Peer's window scale factor (from SYN options) */
    uint8_t     wscale_ok;      /**< Peer supports window scaling */
} vos3_tcp_backlog_entry_t;

/**
 * @brief TCP Protocol Control Block (PCB)
 *
 * Tracks the complete state of a TCP connection including:
 * - Connection endpoints (local/remote IP:port)
 * - State machine state
 * - Sequence number management
 * - Sliding window state
 * - Retransmission queue
 */
typedef struct vos3_tcp_pcb {
    /* === Connection Identity === */
    uint32_t            local_ip;       /**< Local IP address */
    uint16_t            local_port;     /**< Local port */
    uint32_t            remote_ip;      /**< Remote IP address */
    uint16_t            remote_port;    /**< Remote port */

    /* === State Machine === */
    vos3_tcp_state_t    state;          /**< Current TCP state */
    vos3_socket_t*      socket;         /**< Associated socket */

    /* === Send Sequence Variables === */
    uint32_t            snd_una;        /**< Oldest unacknowledged seq */
    uint32_t            snd_nxt;        /**< Next sequence to send */
    uint32_t            snd_wnd;        /**< Send window size */
    uint32_t            snd_wl1;        /**< Seq for last window update */
    uint32_t            snd_wl2;        /**< Ack for last window update */
    uint32_t            iss;            /**< Initial send sequence number */

    /* === Receive Sequence Variables === */
    uint32_t            rcv_nxt;        /**< Next sequence to receive */
    uint32_t            rcv_wnd;        /**< Receive window size */
    uint32_t            irs;            /**< Initial receive sequence number */

    /* === Congestion Control (Reno — Phase 7.1) === */
    uint32_t            cwnd;           /**< Congestion window */
    uint32_t            ssthresh;       /**< Slow start threshold */
    uint16_t            mss;            /**< Maximum segment size */
    uint8_t             dup_ack_count;  /**< Duplicate ACK counter (fast retransmit at 3) */
    uint8_t             in_fast_recovery;/**< 1 = fast recovery active */

    /* === Window Scaling (RFC 7323 — Phase 7.1) === */
    uint8_t             snd_wscale;     /**< Send window scale factor (peer's) */
    uint8_t             rcv_wscale;     /**< Receive window scale factor (ours) */
    uint8_t             wscale_ok;      /**< Both sides negotiated window scaling */
    uint8_t             _wscale_pad;

    /* === SACK — Selective Acknowledgements (RFC 2018 — Phase 7.4) === */
    uint8_t             sack_ok;        /**< Both sides negotiated SACK */
    uint8_t             sack_count;     /**< SACK blocks to report in next ACK */
    uint8_t             _sack_pad[2];
    vos3_tcp_sack_block_t sack_blocks[TCP_MAX_SACK_BLOCKS]; /**< Out-of-order blocks received */

    /* === Timers === */
    uint32_t            rto;            /**< Retransmission timeout (ms) */
    uint32_t            srtt;           /**< Smoothed round-trip time */
    uint32_t            rttvar;         /**< RTT variance */
    uint32_t            last_rx_time;   /**< Last receive timestamp */
    uint32_t            last_tx_time;   /**< Last transmit timestamp */

    /* === Receive Buffer === */
    uint8_t             rx_buffer[TCP_RX_BUFFER_SIZE];
    uint16_t            rx_head;        /**< Read pointer */
    uint16_t            rx_tail;        /**< Write pointer */
    uint16_t            rx_count;       /**< Bytes in buffer */

    /* === Send Buffer === */
    uint8_t             tx_buffer[TCP_TX_BUFFER_SIZE];
    uint16_t            tx_head;        /**< Read pointer */
    uint16_t            tx_tail;        /**< Write pointer */
    uint16_t            tx_count;       /**< Bytes in buffer */

    /* === Retransmission Queue === */
    vos3_tcp_rexmit_entry_t rexmit_queue[4];
    uint8_t             rexmit_count;

    /* === Listen Backlog (for LISTEN state) === */
    vos3_tcp_backlog_entry_t backlog[TCP_BACKLOG_MAX];
    uint8_t             backlog_count;
    uint8_t             backlog_max;

    /* === Flags === */
    uint8_t             flags;          /**< PCB flags */
    #define TCP_PCB_FLAG_ACTIVE     0x01
    #define TCP_PCB_FLAG_NODELAY    0x02
    #define TCP_PCB_FLAG_KEEPALIVE  0x04

    /* === Statistics === */
    uint32_t            segments_sent;
    uint32_t            segments_recv;
    uint32_t            retransmits;
    uint32_t            out_of_order;

    /* === Allocation tracking === */
    uint8_t             allocated;
    uint8_t             _padding[3];
} vos3_tcp_pcb_t;

/* ============================================================================
 * TCP PSEUDO-HEADER (for checksum calculation)
 * ============================================================================ */

/**
 * @brief TCP pseudo-header for checksum calculation
 */
typedef struct __attribute__((packed)) vos3_tcp_pseudo_header {
    uint32_t    src_ip;
    uint32_t    dst_ip;
    uint8_t     zero;
    uint8_t     protocol;       /**< Always 6 for TCP */
    uint16_t    tcp_length;     /**< TCP header + data length */
} vos3_tcp_pseudo_header_t;

/* ============================================================================
 * TCP OPTIONS
 * ============================================================================ */

/** @brief TCP option kinds */
#define TCP_OPT_END         0   /**< End of options */
#define TCP_OPT_NOP         1   /**< No operation (padding) */
#define TCP_OPT_MSS         2   /**< Maximum segment size */
#define TCP_OPT_WSCALE      3   /**< Window scale */
#define TCP_OPT_SACK_PERM   4   /**< SACK permitted */
#define TCP_OPT_SACK        5   /**< Selective ACK */
#define TCP_OPT_TIMESTAMP   8   /**< Timestamps */

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize TCP subsystem
 * @return 0 on success, negative error on failure
 */
int vos3_tcp_init(void);

/**
 * @brief Process incoming TCP segment
 * @param[in] netif Network interface
 * @param[in] buf Network buffer containing TCP segment
 * @param[in] src_ip Source IP address (network order)
 * @param[in] dst_ip Destination IP address (network order)
 * @return 0 on success, negative error on failure
 */
int vos3_tcp_input(vos3_netif_t* netif, vos3_netbuf_t* buf,
                   uint32_t src_ip, uint32_t dst_ip);

/**
 * @brief Send TCP segment
 * @param[in] pcb TCP PCB
 * @param[in] data Data to send (may be NULL for control segments)
 * @param[in] len Data length
 * @param[in] flags TCP flags
 * @return Bytes sent, or negative error
 */
int vos3_tcp_output(vos3_tcp_pcb_t* pcb, const void* data, size_t len, uint8_t flags);

/**
 * @brief Allocate a new TCP PCB
 * @return PCB pointer, or NULL if pool exhausted
 */
vos3_tcp_pcb_t* vos3_tcp_pcb_alloc(void);

/**
 * @brief Free a TCP PCB
 * @param[in] pcb PCB to free
 */
void vos3_tcp_pcb_free(vos3_tcp_pcb_t* pcb);

/**
 * @brief Lookup TCP PCB by connection tuple
 * @param[in] local_ip Local IP
 * @param[in] local_port Local port
 * @param[in] remote_ip Remote IP
 * @param[in] remote_port Remote port
 * @return PCB pointer, or NULL if not found
 */
vos3_tcp_pcb_t* vos3_tcp_pcb_lookup(uint32_t local_ip, uint16_t local_port,
                                     uint32_t remote_ip, uint16_t remote_port);

/**
 * @brief Lookup listening PCB by local port
 * @param[in] local_port Local port
 * @return PCB pointer, or NULL if not found
 */
vos3_tcp_pcb_t* vos3_tcp_pcb_lookup_listen(uint16_t local_port);

/**
 * @brief Initiate TCP connection (active open)
 * @param[in] pcb TCP PCB
 * @param[in] remote_ip Remote IP address
 * @param[in] remote_port Remote port
 * @return 0 on success (SYN sent), negative error on failure
 */
int vos3_tcp_connect(vos3_tcp_pcb_t* pcb, uint32_t remote_ip, uint16_t remote_port);

/**
 * @brief Start listening on PCB (passive open)
 * @param[in] pcb TCP PCB
 * @param[in] backlog Maximum pending connections
 * @return 0 on success, negative error on failure
 */
int vos3_tcp_listen(vos3_tcp_pcb_t* pcb, int backlog);

/**
 * @brief Accept incoming connection from listen queue
 * @param[in] listen_pcb Listening PCB
 * @return New PCB for accepted connection, or NULL if none pending
 */
vos3_tcp_pcb_t* vos3_tcp_accept(vos3_tcp_pcb_t* listen_pcb);

/**
 * @brief Send data on established connection
 * @param[in] pcb TCP PCB
 * @param[in] data Data buffer
 * @param[in] len Data length
 * @return Bytes queued for sending, or negative error
 */
int vos3_tcp_send(vos3_tcp_pcb_t* pcb, const void* data, size_t len);

/**
 * @brief Receive data from connection
 * @param[in] pcb TCP PCB
 * @param[out] buf Buffer to receive into
 * @param[in] len Maximum bytes to receive
 * @return Bytes received, 0 if no data, negative error
 */
int vos3_tcp_recv(vos3_tcp_pcb_t* pcb, void* buf, size_t len);

/**
 * @brief Close TCP connection (graceful)
 * @param[in] pcb TCP PCB
 * @return 0 on success, negative error on failure
 */
int vos3_tcp_close(vos3_tcp_pcb_t* pcb);

/**
 * @brief Abort TCP connection (immediate RST)
 * @param[in] pcb TCP PCB
 */
void vos3_tcp_abort(vos3_tcp_pcb_t* pcb);

/**
 * @brief Calculate TCP checksum
 * @param[in] src_ip Source IP (network order)
 * @param[in] dst_ip Destination IP (network order)
 * @param[in] tcp_header TCP header
 * @param[in] tcp_len Total TCP length (header + data)
 * @return Checksum value
 */
uint16_t vos3_tcp_checksum(uint32_t src_ip, uint32_t dst_ip,
                           const vos3_tcp_header_t* tcp_header, uint16_t tcp_len);

/**
 * @brief Generate random initial sequence number (ISN)
 * @return Random ISN for security
 *
 * Uses RFC 6528 recommendations for ISN generation to prevent
 * TCP sequence prediction attacks.
 */
uint32_t vos3_tcp_generate_isn(void);

/**
 * @brief TCP timer tick (called periodically)
 *
 * Handles retransmission timeouts, TIME_WAIT cleanup, etc.
 */
void vos3_tcp_timer_tick(void);

/**
 * @brief Get TCP state name string
 * @param[in] state TCP state
 * @return State name string
 */
const char* vos3_tcp_state_name(vos3_tcp_state_t state);

/* ============================================================================
 * SOCKET API INTEGRATION
 * ============================================================================ */

/**
 * @brief sys_listen implementation for TCP
 */
int vos3_sys_listen(int fd, int backlog);

/**
 * @brief sys_accept implementation for TCP
 */
int vos3_sys_accept(int fd, struct sockaddr* addr, socklen_t* addrlen);

/**
 * @brief sys_connect implementation for TCP
 */
int vos3_sys_connect(int fd, const struct sockaddr* addr, socklen_t addrlen);

/**
 * @brief sys_send implementation for TCP (connected socket)
 */
ssize_t vos3_sys_send(int fd, const void* buf, size_t len, int flags);

/**
 * @brief sys_recv implementation for TCP (connected socket)
 */
ssize_t vos3_sys_recv(int fd, void* buf, size_t len, int flags);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_TCP_H */
