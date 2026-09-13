/**
 * @file tcp.c
 * @brief VOS3 TCP Protocol Implementation
 *
 * @details Reliable transport protocol with:
 *          - Full RFC 793 state machine
 *          - Sequence number randomization (RFC 6528)
 *          - Sliding window flow control
 *          - Retransmission with exponential backoff
 *
 * @version 1.0.0
 * @date 2026-02-19
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 6 - TCP Protocol & Reliable Transport
 */

#include "../../include/vos/tcp.h"
#include "../../include/vos/net.h"
#include "../../include/vos/socket.h"
#include "../../include/vos/compiler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/entropy.h"

/* ============================================================================
 * TCP PCB POOL
 * ============================================================================ */

/** @brief TCP PCB pool */
static vos3_tcp_pcb_t g_tcp_pcb_pool[TCP_PCB_POOL_SIZE];

/** @brief TCP PCB pool lock */
static vos3_spinlock_t g_tcp_lock = VOS3_SPINLOCK_INIT;

/** @brief TCP statistics */
static struct {
    uint64_t    segments_rx;
    uint64_t    segments_tx;
    uint64_t    connections_established;
    uint64_t    connections_reset;
    uint64_t    checksum_errors;
    uint64_t    out_of_window;
    uint64_t    retransmits;
} g_tcp_stats;

/** @brief ISN counter for sequence randomization */
static uint32_t g_isn_counter = 0;

/** @brief Our receive window scale factor (RFC 7323)
 *  Shift of 7 allows advertised windows up to 8MB (65535 << 7 = 8,388,480),
 *  enabling high-throughput GGUF model downloads over >1Gbps links. */
#define TCP_OUR_WSCALE  7U

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Swap bytes in 16-bit value (host <-> network order)
 */
static inline uint16_t tcp_htons(uint16_t val)
{
    return (uint16_t)((val >> 8) | (val << 8));
}

/**
 * @brief Swap bytes in 32-bit value (host <-> network order)
 */
static inline uint32_t tcp_htonl(uint32_t val)
{
    return ((val >> 24) & 0xFF) |
           ((val >> 8) & 0xFF00) |
           ((val << 8) & 0xFF0000) |
           ((val << 24) & 0xFF000000);
}

#define tcp_ntohs(x) tcp_htons(x)
#define tcp_ntohl(x) tcp_htonl(x)

/**
 * @brief Sequence number comparison (handles wraparound)
 * @return <0 if a < b, 0 if a == b, >0 if a > b
 */
static inline int32_t seq_cmp(uint32_t a, uint32_t b)
{
    return (int32_t)(a - b);
}

#define SEQ_LT(a, b)    (seq_cmp((a), (b)) < 0)
#define SEQ_LEQ(a, b)   (seq_cmp((a), (b)) <= 0)
#define SEQ_GT(a, b)    (seq_cmp((a), (b)) > 0)
#define SEQ_GEQ(a, b)   (seq_cmp((a), (b)) >= 0)

/**
 * @brief Get current timestamp (milliseconds)
 */
static uint32_t tcp_get_time_ms(void)
{
    extern uint64_t vos3_timer_get_ticks(void);
    return (uint32_t)(vos3_timer_get_ticks() * 10);  /* Assuming 100Hz timer */
}

/**
 * @brief Cryptographically secure random for ISN generation
 * Uses the entropy subsystem's ChaCha20 CSPRNG.
 */
static uint32_t tcp_random(void)
{
    return vos3_entropy_get_u32();
}

/* ============================================================================
 * TCP STATE NAMES
 * ============================================================================ */

static const char* g_tcp_state_names[] = {
    "CLOSED",
    "LISTEN",
    "SYN_SENT",
    "SYN_RECEIVED",
    "ESTABLISHED",
    "FIN_WAIT_1",
    "FIN_WAIT_2",
    "CLOSE_WAIT",
    "CLOSING",
    "LAST_ACK",
    "TIME_WAIT"
};

const char* vos3_tcp_state_name(vos3_tcp_state_t state)
{
    if (state <= TCP_STATE_TIME_WAIT) {
        return g_tcp_state_names[state];
    }
    return "UNKNOWN";
}

/* ============================================================================
 * TCP CHECKSUM
 * ============================================================================ */

/**
 * @brief Calculate 16-bit one's complement sum
 */
static uint32_t tcp_checksum_partial(const void* data, size_t len)
{
    const uint16_t* ptr = (const uint16_t*)data;
    uint32_t sum = 0;

    while (len > 1) {
        sum += *ptr++;
        len -= 2;
    }

    /* Add left-over byte if any */
    if (len > 0) {
        sum += *(const uint8_t*)ptr;
    }

    return sum;
}

uint16_t vos3_tcp_checksum(uint32_t src_ip, uint32_t dst_ip,
                           const vos3_tcp_header_t* tcp_header, uint16_t tcp_len)
{
    uint32_t sum = 0;

    /* Pseudo-header */
    vos3_tcp_pseudo_header_t pseudo;
    pseudo.src_ip = src_ip;
    pseudo.dst_ip = dst_ip;
    pseudo.zero = 0;
    pseudo.protocol = 6;  /* TCP */
    pseudo.tcp_length = tcp_htons(tcp_len);

    sum = tcp_checksum_partial(&pseudo, sizeof(pseudo));
    sum += tcp_checksum_partial(tcp_header, tcp_len);

    /* Fold 32-bit sum to 16 bits */
    while (sum >> 16) {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }

    return (uint16_t)(~sum);
}

/* ============================================================================
 * ISN GENERATION (RFC 6528 - Sequence Number Randomization)
 * ============================================================================ */

uint32_t vos3_tcp_generate_isn(void)
{
    /*
     * RFC 6528: ISN = M + F(local_ip, local_port, remote_ip, remote_port, secret)
     *
     * For simplicity, we use a combination of:
     * - Monotonically increasing counter (4us resolution equivalent)
     * - Random component
     * - Timestamp
     *
     * This prevents sequence number prediction attacks.
     */
    uint32_t time_component = tcp_get_time_ms();
    uint32_t random_component = tcp_random();

    g_isn_counter += 64000;  /* ~64KB increment per connection */

    return g_isn_counter + time_component + random_component;
}

/* ============================================================================
 * TCP OPTIONS PARSER (RFC 7323 Window Scaling, RFC 793 MSS)
 * ============================================================================ */

/**
 * @brief Parse TCP options from segment header
 *
 * Extracts Window Scale (RFC 7323) and MSS options from the TCP header.
 * Window Scale tells us the peer's shift factor for their advertised windows.
 * MSS clamps our send segment size to avoid IP fragmentation.
 *
 * @param[in]  tcp   TCP header with options
 * @param[out] pcb   PCB to update with parsed option values
 */
static void tcp_parse_options(const vos3_tcp_header_t* tcp, vos3_tcp_pcb_t* pcb)
{
    uint8_t hdr_len = (tcp->data_offset >> 4) * 4;
    if (hdr_len <= 20) {
        return;  /* No options present */
    }

    const uint8_t* opts = (const uint8_t*)tcp + 20;
    uint8_t opts_len = hdr_len - 20;
    uint8_t i = 0;

    while (i < opts_len) {
        uint8_t kind = opts[i];

        if (kind == 0) {        /* End of Option List */
            break;
        }
        if (kind == 1) {        /* NOP padding */
            i++;
            continue;
        }

        /* All other options: kind(1) + length(1) + data(length-2) */
        if (i + 1 >= opts_len) {
            break;              /* Truncated option — bail */
        }
        uint8_t opt_len = opts[i + 1];
        if (opt_len < 2 || (uint16_t)i + opt_len > opts_len) {
            break;              /* Malformed option — bail */
        }

        switch (kind) {
        case TCP_OPT_WSCALE:    /* Window Scale (kind=3, len=3) */
            if (opt_len == 3) {
                uint8_t shift = opts[i + 2];
                if (shift > TCP_WINDOW_SCALE_MAX) {
                    shift = TCP_WINDOW_SCALE_MAX;  /* Clamp per RFC 7323 */
                }
                pcb->snd_wscale = shift;    /* Peer's scale factor */
                pcb->wscale_ok = 1;
                VOS3_DEBUG("[TCP] Parsed WScale: peer shift=%u", shift);
            }
            break;

        case 2:                 /* MSS (kind=2, len=4) */
            if (opt_len == 4) {
                uint16_t mss = ((uint16_t)opts[i + 2] << 8) | opts[i + 3];
                if (mss > 0 && mss < pcb->mss) {
                    pcb->mss = mss;
                    VOS3_DEBUG("[TCP] Parsed MSS: %u", mss);
                }
            }
            break;

        case TCP_OPT_SACK_PERM: /* SACK Permitted (kind=4, len=2) — RFC 2018 */
            if (opt_len == 2) {
                pcb->sack_ok = 1;
                VOS3_DEBUG("[TCP] Parsed SACK Permitted");
            }
            break;

        case TCP_OPT_SACK:      /* SACK blocks (kind=5, len=variable) — RFC 2018 */
            if (opt_len >= 10 && pcb->sack_ok) {
                /* Each block is 8 bytes (left_edge:4 + right_edge:4).
                 * Max 4 blocks = 34 bytes total (2 header + 32 data). */
                uint8_t n_blocks = (opt_len - 2) / 8;
                if (n_blocks > TCP_MAX_SACK_BLOCKS)
                    n_blocks = TCP_MAX_SACK_BLOCKS;
                pcb->sack_count = n_blocks;
                for (uint8_t b = 0; b < n_blocks; b++) {
                    uint8_t off = (uint8_t)(i + 2 + b * 8);
                    pcb->sack_blocks[b].left =
                        ((uint32_t)opts[off]     << 24) |
                        ((uint32_t)opts[off + 1] << 16) |
                        ((uint32_t)opts[off + 2] << 8)  |
                        ((uint32_t)opts[off + 3]);
                    pcb->sack_blocks[b].right =
                        ((uint32_t)opts[off + 4] << 24) |
                        ((uint32_t)opts[off + 5] << 16) |
                        ((uint32_t)opts[off + 6] << 8)  |
                        ((uint32_t)opts[off + 7]);
                }
                VOS3_DEBUG("[TCP] Parsed %u SACK blocks", n_blocks);
            }
            break;

        default:
            break;              /* Skip unknown options */
        }

        i += opt_len;
    }
}

/* ============================================================================
 * PCB MANAGEMENT
 * ============================================================================ */

vos3_tcp_pcb_t* vos3_tcp_pcb_alloc(void)
{
    vos3_spinlock_lock(&g_tcp_lock);

    for (size_t i = 0; i < TCP_PCB_POOL_SIZE; i++) {
        if (!g_tcp_pcb_pool[i].allocated) {
            vos3_tcp_pcb_t* pcb = &g_tcp_pcb_pool[i];

            /* Zero the PCB */
            memset(pcb, 0, sizeof(vos3_tcp_pcb_t));

            /* Initialize defaults */
            pcb->allocated = 1;
            pcb->state = TCP_STATE_CLOSED;
            pcb->mss = TCP_MSS_DEFAULT;
            pcb->rcv_wnd = TCP_WINDOW_DEFAULT;
            pcb->snd_wnd = TCP_WINDOW_DEFAULT;
            pcb->cwnd = TCP_MSS_DEFAULT;  /* Start with 1 MSS */
            pcb->ssthresh = TCP_WINDOW_DEFAULT;
            pcb->rto = TCP_RTO_MIN_MS;
            pcb->flags = TCP_PCB_FLAG_ACTIVE;

            vos3_spinlock_unlock(&g_tcp_lock);

            VOS3_DEBUG("[TCP] Allocated PCB %zu", i);
            return pcb;
        }
    }

    vos3_spinlock_unlock(&g_tcp_lock);
    VOS3_WARN("[TCP] PCB pool exhausted");
    return NULL;
}

void vos3_tcp_pcb_free(vos3_tcp_pcb_t* pcb)
{
    if (unlikely(pcb == NULL)) {
        return;
    }

    vos3_spinlock_lock(&g_tcp_lock);

    /* Clear retransmission queue */
    for (int i = 0; i < 4; i++) {
        if (pcb->rexmit_queue[i].data != NULL) {
            /* In real implementation, free the data buffer */
            pcb->rexmit_queue[i].data = NULL;
        }
    }

    pcb->allocated = 0;
    pcb->state = TCP_STATE_CLOSED;

    vos3_spinlock_unlock(&g_tcp_lock);

    VOS3_DEBUG("[TCP] Freed PCB");
}

vos3_tcp_pcb_t* vos3_tcp_pcb_lookup(uint32_t local_ip, uint16_t local_port,
                                     uint32_t remote_ip, uint16_t remote_port)
{
    vos3_spinlock_lock(&g_tcp_lock);

    for (size_t i = 0; i < TCP_PCB_POOL_SIZE; i++) {
        vos3_tcp_pcb_t* pcb = &g_tcp_pcb_pool[i];

        if (pcb->allocated &&
            pcb->local_port == local_port &&
            pcb->remote_port == remote_port &&
            (pcb->local_ip == 0 || pcb->local_ip == local_ip) &&
            pcb->remote_ip == remote_ip) {

            vos3_spinlock_unlock(&g_tcp_lock);
            return pcb;
        }
    }

    vos3_spinlock_unlock(&g_tcp_lock);
    return NULL;
}

vos3_tcp_pcb_t* vos3_tcp_pcb_lookup_listen(uint16_t local_port)
{
    vos3_spinlock_lock(&g_tcp_lock);

    for (size_t i = 0; i < TCP_PCB_POOL_SIZE; i++) {
        vos3_tcp_pcb_t* pcb = &g_tcp_pcb_pool[i];

        if (pcb->allocated &&
            pcb->state == TCP_STATE_LISTEN &&
            pcb->local_port == local_port) {

            vos3_spinlock_unlock(&g_tcp_lock);
            return pcb;
        }
    }

    vos3_spinlock_unlock(&g_tcp_lock);
    return NULL;
}

/* ============================================================================
 * TCP OUTPUT
 * ============================================================================ */

/**
 * @brief Build and send a TCP segment
 */
int vos3_tcp_output(vos3_tcp_pcb_t* pcb, const void* data, size_t len, uint8_t flags)
{
    if (unlikely(pcb == NULL)) {
        return -1;
    }

    /* Get network interface */
    extern vos3_netif_t* vos3_net_get_default_interface(void);
    vos3_netif_t* netif = vos3_net_get_default_interface();
    if (unlikely(netif == NULL)) {
        return -1;
    }

    /* Calculate TCP options length:
     * SYN/SYN-ACK: Window Scale (4B) + SACK Permitted (4B) = 8 bytes
     * Data ACK with SACK blocks: 2B header + 8B per block (up to 4)
     * All option fields must be 4-byte aligned. */
    uint8_t opt_len = 0;
    if (flags & TCP_FLAG_SYN) {
        opt_len = 8;  /* NOP+WScale(4) + NOP+NOP+SACK_PERM(4) = 8 bytes */
    } else if ((flags & TCP_FLAG_ACK) && pcb->sack_ok && pcb->sack_count > 0) {
        /* SACK option: NOP+NOP + kind(1)+len(1) + 8*n_blocks, aligned to 4B */
        uint8_t sack_data = (uint8_t)(2 + 2 + pcb->sack_count * 8);
        opt_len = (sack_data + 3) & ~3U;  /* Round up to 4-byte boundary */
    }

    /* Calculate total TCP length (header + options + payload) */
    uint16_t tcp_len = (uint16_t)(sizeof(vos3_tcp_header_t) + opt_len + len);

    /* Allocate network buffer */
    vos3_netbuf_t* buf = vos3_netbuf_alloc(tcp_len + 40);  /* +IP+ETH headers */
    if (unlikely(buf == NULL)) {
        return -12;  /* -ENOMEM */
    }

    /* Reserve space for IP and Ethernet headers */
    buf->data_offset = 34;  /* 14 ETH + 20 IP */

    /* Build TCP header */
    vos3_tcp_header_t* tcp = (vos3_tcp_header_t*)(buf->data + buf->data_offset);

    tcp->src_port = tcp_htons(pcb->local_port);
    tcp->dst_port = tcp_htons(pcb->remote_port);
    tcp->seq_num = tcp_htonl(pcb->snd_nxt);
    tcp->ack_num = tcp_htonl(pcb->rcv_nxt);
    tcp->data_offset = (uint8_t)((5 + (opt_len / 4)) << 4);
    tcp->flags = flags;

    /* Window advertisement — RFC 7323: SYN window is NEVER scaled;
     * post-handshake windows are right-shifted by our rcv_wscale. */
    if (pcb->wscale_ok && !(flags & TCP_FLAG_SYN)) {
        uint32_t scaled = pcb->rcv_wnd >> pcb->rcv_wscale;
        tcp->window = tcp_htons((uint16_t)(scaled > 0xFFFF ? 0xFFFF : scaled));
    } else {
        tcp->window = tcp_htons((uint16_t)pcb->rcv_wnd);
    }

    tcp->urgent_ptr = 0;
    tcp->checksum = 0;

    /* Write TCP options */
    if (opt_len > 0) {
        uint8_t* opts = (uint8_t*)tcp + sizeof(vos3_tcp_header_t);
        /* Zero-fill option space to avoid leaking stack data */
        for (uint8_t z = 0; z < opt_len; z++) opts[z] = 0;

        if (flags & TCP_FLAG_SYN) {
            /* SYN/SYN-ACK options: Window Scale + SACK Permitted */
            opts[0] = TCP_OPT_NOP;            /* NOP padding */
            opts[1] = TCP_OPT_WSCALE;         /* Kind = 3 */
            opts[2] = 3;                       /* Len = 3 */
            opts[3] = TCP_OUR_WSCALE;         /* Shift 7 → 8MB windows */
            opts[4] = TCP_OPT_NOP;            /* NOP padding */
            opts[5] = TCP_OPT_NOP;            /* NOP padding */
            opts[6] = TCP_OPT_SACK_PERM;     /* Kind = 4 (SACK Permitted) */
            opts[7] = 2;                       /* Len = 2 */
        } else if (pcb->sack_ok && pcb->sack_count > 0) {
            /* Data ACK with SACK blocks (RFC 2018 §3) */
            opts[0] = TCP_OPT_NOP;            /* NOP padding */
            opts[1] = TCP_OPT_NOP;            /* NOP padding */
            opts[2] = TCP_OPT_SACK;          /* Kind = 5 */
            opts[3] = (uint8_t)(2 + pcb->sack_count * 8);  /* Len */
            uint8_t off = 4;
            for (uint8_t b = 0; b < pcb->sack_count && b < TCP_MAX_SACK_BLOCKS; b++) {
                uint32_t le = pcb->sack_blocks[b].left;
                uint32_t re = pcb->sack_blocks[b].right;
                opts[off++] = (uint8_t)(le >> 24);
                opts[off++] = (uint8_t)(le >> 16);
                opts[off++] = (uint8_t)(le >> 8);
                opts[off++] = (uint8_t)(le);
                opts[off++] = (uint8_t)(re >> 24);
                opts[off++] = (uint8_t)(re >> 16);
                opts[off++] = (uint8_t)(re >> 8);
                opts[off++] = (uint8_t)(re);
            }
            /* Clear SACK state after sending */
            pcb->sack_count = 0;
        }
    }

    /* Copy data if any (after header + options) */
    if (data != NULL && len > 0) {
        memcpy((uint8_t*)tcp + sizeof(vos3_tcp_header_t) + opt_len, data, len);
    }

    /* Calculate checksum */
    uint32_t src_ip = pcb->local_ip;
    uint32_t dst_ip = pcb->remote_ip;
    if (src_ip == 0) {
        src_ip = netif->ipv4_addr;
    }

    tcp->checksum = vos3_tcp_checksum(src_ip, dst_ip, tcp, tcp_len);

    /* Update sequence number for data segments */
    if (len > 0) {
        pcb->snd_nxt += len;
    }
    if (flags & TCP_FLAG_SYN) {
        pcb->snd_nxt++;
    }
    if (flags & TCP_FLAG_FIN) {
        pcb->snd_nxt++;
    }

    /* Send via IP layer */
    buf->len = tcp_len;
    extern int vos3_ip_output(vos3_netif_t* netif, vos3_netbuf_t* buf,
                              uint32_t dst_ip, uint8_t protocol);
    int ret = vos3_ip_output(netif, buf, pcb->remote_ip, 6);  /* Protocol 6 = TCP */

    if (ret == 0) {
        pcb->segments_sent++;
        g_tcp_stats.segments_tx++;
        pcb->last_tx_time = tcp_get_time_ms();
    }

    return (ret == 0) ? (int)len : ret;
}

/**
 * @brief Send RST segment
 */
static void tcp_send_rst(vos3_netif_t* netif, uint32_t src_ip, uint16_t src_port,
                         uint32_t dst_ip, uint16_t dst_port,
                         uint32_t seq_num, uint32_t ack_num)
{
    /* Allocate temporary PCB-like structure for sending */
    vos3_tcp_pcb_t temp_pcb;
    memset(&temp_pcb, 0, sizeof(temp_pcb));

    temp_pcb.local_ip = src_ip;
    temp_pcb.local_port = src_port;
    temp_pcb.remote_ip = dst_ip;
    temp_pcb.remote_port = dst_port;
    temp_pcb.snd_nxt = seq_num;
    temp_pcb.rcv_nxt = ack_num;
    temp_pcb.rcv_wnd = 0;

    vos3_tcp_output(&temp_pcb, NULL, 0, TCP_FLAG_RST | TCP_FLAG_ACK);

    g_tcp_stats.connections_reset++;
    VOS3_DEBUG("[TCP] Sent RST to %u.%u.%u.%u:%u",
               dst_ip & 0xFF, (dst_ip >> 8) & 0xFF,
               (dst_ip >> 16) & 0xFF, (dst_ip >> 24) & 0xFF,
               dst_port);
}

/* ============================================================================
 * TCP INPUT PROCESSING
 * ============================================================================ */

int vos3_tcp_input(vos3_netif_t* netif, vos3_netbuf_t* buf,
                   uint32_t src_ip, uint32_t dst_ip)
{
    if (unlikely(netif == NULL || buf == NULL)) {
        return -1;
    }

    /* Get TCP header */
    vos3_tcp_header_t* tcp = (vos3_tcp_header_t*)(buf->data + buf->data_offset);

    /* Validate minimum length */
    if (unlikely(buf->len < sizeof(vos3_tcp_header_t))) {
        VOS3_WARN("[TCP] Segment too short: %u bytes", buf->len);
        vos3_netbuf_free(buf);
        return -1;
    }

    /* Extract fields */
    uint16_t src_port = tcp_ntohs(tcp->src_port);
    uint16_t dst_port = tcp_ntohs(tcp->dst_port);
    uint32_t seq_num = tcp_ntohl(tcp->seq_num);
    uint32_t ack_num = tcp_ntohl(tcp->ack_num);
    uint8_t flags = tcp->flags;
    uint16_t window = tcp_ntohs(tcp->window);
    uint8_t data_offset = (tcp->data_offset >> 4) * 4;

    /* Validate data offset */
    if (unlikely(data_offset < 20 || data_offset > 60)) {
        VOS3_WARN("[TCP] Invalid data offset: %u", data_offset);
        vos3_netbuf_free(buf);
        return -1;
    }

    /* Calculate data length */
    uint16_t tcp_len = buf->len;
    uint16_t data_len = tcp_len - data_offset;

    /* Verify checksum */
    uint16_t recv_checksum = tcp->checksum;
    tcp->checksum = 0;
    uint16_t calc_checksum = vos3_tcp_checksum(src_ip, dst_ip, tcp, tcp_len);

    if (unlikely(recv_checksum != calc_checksum)) {
        VOS3_WARN("[TCP] Checksum mismatch: recv=0x%04X calc=0x%04X",
                  recv_checksum, calc_checksum);
        g_tcp_stats.checksum_errors++;
        vos3_netbuf_free(buf);
        return -1;
    }

    g_tcp_stats.segments_rx++;

    VOS3_DEBUG("[TCP] Rx: %u.%u.%u.%u:%u -> :%u flags=0x%02X seq=%u ack=%u len=%u",
               src_ip & 0xFF, (src_ip >> 8) & 0xFF,
               (src_ip >> 16) & 0xFF, (src_ip >> 24) & 0xFF,
               src_port, dst_port, flags, seq_num, ack_num, data_len);

    /* Look up PCB */
    vos3_tcp_pcb_t* pcb = vos3_tcp_pcb_lookup(dst_ip, dst_port, src_ip, src_port);

    /* If no exact match, check for listening socket */
    if (pcb == NULL) {
        pcb = vos3_tcp_pcb_lookup_listen(dst_port);
    }

    /* No PCB found - send RST if not RST */
    if (pcb == NULL) {
        if (!(flags & TCP_FLAG_RST)) {
            tcp_send_rst(netif, dst_ip, dst_port, src_ip, src_port,
                         ack_num, seq_num + data_len + ((flags & TCP_FLAG_SYN) ? 1 : 0));
        }
        vos3_netbuf_free(buf);
        return 0;
    }

    pcb->last_rx_time = tcp_get_time_ms();
    pcb->segments_recv++;

    /* RST processing - RFC 793 Section 3.4: RST Validation
     * In SYN_SENT: RST is valid if ACK field acknowledges our SYN.
     * In all other states: RST seq must be within receive window.
     * This prevents blind RST injection attacks (RFC 5961). */
    if (flags & TCP_FLAG_RST) {
        int rst_valid = 0;

        if (pcb->state == TCP_STATE_SYN_SENT) {
            /* In SYN_SENT, RST is acceptable if it acks our SYN */
            if ((flags & TCP_FLAG_ACK) && ack_num == pcb->iss + 1) {
                rst_valid = 1;
            }
        } else if (pcb->state == TCP_STATE_LISTEN) {
            /* RST on LISTEN is always ignored */
            rst_valid = 0;
        } else {
            /* RFC 5961: RST seq must exactly equal rcv_nxt for acceptance.
             * If within window but not exact match, send a challenge ACK. */
            if (seq_num == pcb->rcv_nxt) {
                rst_valid = 1;
            } else if (SEQ_GEQ(seq_num, pcb->rcv_nxt) &&
                       SEQ_LT(seq_num, pcb->rcv_nxt + pcb->rcv_wnd)) {
                /* In-window but not exact match: send challenge ACK per RFC 5961 */
                VOS3_DEBUG("[TCP] RST in-window but seq != rcv_nxt, sending challenge ACK");
                vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
                rst_valid = 0;
            }
        }

        if (rst_valid) {
            VOS3_INFO("[TCP] Connection reset by peer (validated RST)");
            pcb->state = TCP_STATE_CLOSED;
            g_tcp_stats.connections_reset++;
        } else {
            VOS3_DEBUG("[TCP] Ignoring invalid RST (seq=%u, expected=%u)",
                       seq_num, pcb->rcv_nxt);
        }
        vos3_netbuf_free(buf);
        return 0;
    }

    /* State machine processing */
    switch (pcb->state) {

    case TCP_STATE_CLOSED:
        /* Should not receive segments in CLOSED state */
        if (!(flags & TCP_FLAG_RST)) {
            tcp_send_rst(netif, dst_ip, dst_port, src_ip, src_port,
                         ack_num, seq_num + data_len);
        }
        break;

    case TCP_STATE_LISTEN:
        /* Passive open - expect SYN */
        if (flags & TCP_FLAG_SYN) {
            /* Check backlog capacity */
            if (pcb->backlog_count >= pcb->backlog_max) {
                VOS3_WARN("[TCP] Backlog full, dropping SYN");
                break;
            }

            /* Add to backlog */
            vos3_tcp_backlog_entry_t* entry = NULL;
            for (int i = 0; i < (int)TCP_BACKLOG_MAX; i++) {
                if (!pcb->backlog[i].valid) {
                    entry = &pcb->backlog[i];
                    break;
                }
            }

            if (entry != NULL) {
                entry->remote_ip = src_ip;
                entry->remote_port = src_port;
                entry->irs = seq_num;
                entry->iss = vos3_tcp_generate_isn();
                entry->valid = 1;
                pcb->backlog_count++;

                /* Parse incoming SYN options (Window Scale from peer) */
                vos3_tcp_pcb_t temp_pcb;
                memset(&temp_pcb, 0, sizeof(temp_pcb));
                temp_pcb.mss = TCP_MSS_DEFAULT;
                tcp_parse_options(tcp, &temp_pcb);

                /* Send SYN-ACK with our Window Scale option */
                temp_pcb.local_ip = dst_ip;
                temp_pcb.local_port = dst_port;
                temp_pcb.remote_ip = src_ip;
                temp_pcb.remote_port = src_port;
                temp_pcb.snd_nxt = entry->iss;
                temp_pcb.rcv_nxt = seq_num + 1;
                temp_pcb.rcv_wnd = TCP_WINDOW_DEFAULT;

                vos3_tcp_output(&temp_pcb, NULL, 0, TCP_FLAG_SYN | TCP_FLAG_ACK);

                /* Store peer's wscale in backlog entry for accept() */
                entry->wscale = temp_pcb.wscale_ok ? temp_pcb.snd_wscale : 0;
                entry->wscale_ok = temp_pcb.wscale_ok;

                VOS3_INFO("[TCP] LISTEN -> SYN received, sent SYN-ACK (ISS=%u, peer WS=%u)",
                          entry->iss, entry->wscale);
            }
        }
        break;

    case TCP_STATE_SYN_SENT:
        /* Active open - expect SYN-ACK */
        if ((flags & (TCP_FLAG_SYN | TCP_FLAG_ACK)) == (TCP_FLAG_SYN | TCP_FLAG_ACK)) {
            /* Verify ACK is for our SYN */
            if (ack_num != pcb->iss + 1) {
                VOS3_WARN("[TCP] Invalid SYN-ACK: expected ACK=%u got=%u",
                          pcb->iss + 1, ack_num);
                tcp_send_rst(netif, dst_ip, dst_port, src_ip, src_port,
                             ack_num, seq_num + 1);
                break;
            }

            /* Parse TCP options — extract peer's Window Scale (RFC 7323) */
            tcp_parse_options(tcp, pcb);
            if (pcb->wscale_ok) {
                pcb->rcv_wscale = TCP_OUR_WSCALE;  /* Set our scale factor */
            }

            /* Connection established */
            pcb->irs = seq_num;
            pcb->rcv_nxt = seq_num + 1;
            pcb->snd_una = ack_num;
            pcb->snd_wnd = window;  /* SYN-ACK window is unscaled (RFC 7323 §2.2) */
            pcb->state = TCP_STATE_ESTABLISHED;

            /* Send ACK */
            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);

            g_tcp_stats.connections_established++;
            VOS3_INFO("[TCP] SYN_SENT -> ESTABLISHED (IRS=%u, WScale=%s)",
                      pcb->irs, pcb->wscale_ok ? "ON" : "OFF");
        }
        else if (flags & TCP_FLAG_SYN) {
            /* Simultaneous open - rare but valid */
            pcb->irs = seq_num;
            pcb->rcv_nxt = seq_num + 1;
            pcb->state = TCP_STATE_SYN_RECEIVED;

            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_SYN | TCP_FLAG_ACK);
            VOS3_INFO("[TCP] SYN_SENT -> SYN_RECEIVED (simultaneous open)");
        }
        break;

    case TCP_STATE_SYN_RECEIVED:
        /* Awaiting ACK of our SYN-ACK */
        if (flags & TCP_FLAG_ACK) {
            if (ack_num == pcb->iss + 1) {
                pcb->snd_una = ack_num;
                pcb->snd_wnd = window;
                pcb->state = TCP_STATE_ESTABLISHED;

                g_tcp_stats.connections_established++;
                VOS3_INFO("[TCP] SYN_RECEIVED -> ESTABLISHED");
            }
        }
        break;

    case TCP_STATE_ESTABLISHED:
        /* Data transfer state */

        /* Process ACK — Reno congestion control (RFC 5681) */
        if (flags & TCP_FLAG_ACK) {
            if (SEQ_GT(ack_num, pcb->snd_una) && SEQ_LEQ(ack_num, pcb->snd_nxt)) {
                /* === New ACK — advances send window === */
                pcb->snd_una = ack_num;

                /* Apply window scaling (RFC 7323) to peer's advertised window */
                pcb->snd_wnd = pcb->wscale_ok ?
                    ((uint32_t)window << pcb->snd_wscale) : window;

                /* Reno: exit fast recovery on new ACK */
                if (pcb->in_fast_recovery) {
                    pcb->cwnd = pcb->ssthresh;
                    pcb->in_fast_recovery = 0;
                    pcb->dup_ack_count = 0;
                    VOS3_DEBUG("[TCP] Reno: Exit fast recovery (cwnd=%u)", pcb->cwnd);
                } else {
                    /* Normal congestion control */
                    if (pcb->cwnd < pcb->ssthresh) {
                        /* Slow Start: cwnd += MSS per ACK */
                        pcb->cwnd += pcb->mss;
                    } else {
                        /* Congestion Avoidance: cwnd += MSS^2/cwnd (additive increase) */
                        pcb->cwnd += (pcb->mss * pcb->mss) / pcb->cwnd;
                        if (pcb->cwnd < pcb->mss) {
                            pcb->cwnd = pcb->mss;  /* Floor at 1 MSS */
                        }
                    }
                }

                /* Clear dup ACK state + reset retransmit timer */
                pcb->dup_ack_count = 0;
                pcb->retransmits = 0;
                pcb->rto = TCP_RTO_MIN_MS;

            } else if (ack_num == pcb->snd_una && data_len == 0 &&
                       SEQ_LT(pcb->snd_una, pcb->snd_nxt)) {
                /* === Duplicate ACK — Reno fast retransmit/recovery (RFC 5681 §3.2) ===
                 * Conditions: same ACK number, no data, unacked data outstanding */
                pcb->dup_ack_count++;

                if (pcb->dup_ack_count == 3 && !pcb->in_fast_recovery) {
                    /* 3 dup ACKs → Fast Retransmit + enter Fast Recovery */
                    pcb->ssthresh = pcb->cwnd / 2;
                    if (pcb->ssthresh < pcb->mss * 2) {
                        pcb->ssthresh = pcb->mss * 2;
                    }
                    pcb->cwnd = pcb->ssthresh + 3 * pcb->mss;
                    pcb->in_fast_recovery = 1;

                    /* Retransmit lost segments — SACK-aware (RFC 2018 §4):
                     * Skip retransmission queue entries whose sequence ranges
                     * are fully covered by peer's SACK blocks. */
                    int rexmit_done = 0;
                    for (int qi = 0; qi < 4; qi++) {
                        vos3_tcp_rexmit_entry_t* re = &pcb->rexmit_queue[qi];
                        if (!re->valid || re->data == NULL ||
                            !SEQ_GEQ(re->seq, pcb->snd_una)) {
                            continue;
                        }

                        /* SACK skip: if this segment is fully covered by a
                         * SACK block, the peer already has it — skip. */
                        if (pcb->sack_ok && pcb->sack_count > 0) {
                            int sacked = 0;
                            uint32_t seg_end = re->seq + re->len;
                            for (uint8_t sb = 0; sb < pcb->sack_count &&
                                 sb < TCP_MAX_SACK_BLOCKS; sb++) {
                                if (SEQ_GEQ(re->seq, pcb->sack_blocks[sb].left) &&
                                    SEQ_LEQ(seg_end, pcb->sack_blocks[sb].right)) {
                                    sacked = 1;
                                    break;
                                }
                            }
                            if (sacked) continue;
                        }

                        uint32_t saved_nxt = pcb->snd_nxt;
                        pcb->snd_nxt = re->seq;
                        vos3_tcp_output(pcb, re->data, re->len,
                                        TCP_FLAG_ACK | TCP_FLAG_PSH);
                        pcb->snd_nxt = saved_nxt;
                        re->retries++;
                        rexmit_done = 1;
                        break;
                    }
                    if (!rexmit_done) {
                        /* No queued data — send dup ACK to probe */
                        vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
                    }

                    g_tcp_stats.retransmits++;
                    VOS3_DEBUG("[TCP] Reno: Fast retransmit (3 dup ACKs, ssthresh=%u)",
                               pcb->ssthresh);

                } else if (pcb->dup_ack_count > 3 && pcb->in_fast_recovery) {
                    /* Additional dup ACKs during recovery: inflate cwnd */
                    pcb->cwnd += pcb->mss;
                }
            }
        }

        /* Process incoming data */
        if (data_len > 0) {
            /* Check sequence number is in window */
            if (SEQ_LT(seq_num, pcb->rcv_nxt) ||
                SEQ_GEQ(seq_num, pcb->rcv_nxt + pcb->rcv_wnd)) {
                VOS3_DEBUG("[TCP] Out of window: seq=%u rcv_nxt=%u wnd=%u",
                           seq_num, pcb->rcv_nxt, pcb->rcv_wnd);
                g_tcp_stats.out_of_window++;
                /* Send duplicate ACK */
                vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
                break;
            }

            /* Copy data to receive buffer */
            if (seq_num == pcb->rcv_nxt) {
                uint16_t space = TCP_RX_BUFFER_SIZE - pcb->rx_count;
                uint16_t copy_len = (data_len > space) ? space : data_len;

                uint8_t* data_ptr = (uint8_t*)tcp + data_offset;
                for (uint16_t i = 0; i < copy_len; i++) {
                    pcb->rx_buffer[pcb->rx_tail] = data_ptr[i];
                    pcb->rx_tail = (pcb->rx_tail + 1) % TCP_RX_BUFFER_SIZE;
                }
                pcb->rx_count += copy_len;
                pcb->rcv_nxt += copy_len;
                pcb->rcv_wnd = TCP_RX_BUFFER_SIZE - pcb->rx_count;

                /* Send ACK */
                vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
            } else {
                /* Out of order — record SACK block so the peer knows
                 * which data we have (RFC 2018 §3). The next ACK will
                 * carry these blocks, allowing SACK-aware retransmit. */
                pcb->out_of_order++;

                if (pcb->sack_ok) {
                    /* Insert this segment as a new SACK block.
                     * Shift existing blocks right, newest at index 0. */
                    uint32_t new_left  = seq_num;
                    uint32_t new_right = seq_num + data_len;

                    /* Shift existing blocks (drop oldest if full) */
                    uint8_t max_shift = (pcb->sack_count < TCP_MAX_SACK_BLOCKS - 1)
                                        ? pcb->sack_count
                                        : (uint8_t)(TCP_MAX_SACK_BLOCKS - 1);
                    for (int8_t s = (int8_t)(max_shift - 1); s >= 0; s--) {
                        pcb->sack_blocks[s + 1] = pcb->sack_blocks[s];
                    }
                    pcb->sack_blocks[0].left  = new_left;
                    pcb->sack_blocks[0].right = new_right;
                    if (pcb->sack_count < TCP_MAX_SACK_BLOCKS)
                        pcb->sack_count++;

                    VOS3_DEBUG("[TCP] SACK block: [%u, %u) (%u blocks)",
                               new_left, new_right, pcb->sack_count);
                }

                /* Send dup ACK (with SACK blocks if negotiated) */
                vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
            }
        }

        /* Process FIN */
        if (flags & TCP_FLAG_FIN) {
            pcb->rcv_nxt++;
            pcb->state = TCP_STATE_CLOSE_WAIT;
            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
            VOS3_INFO("[TCP] ESTABLISHED -> CLOSE_WAIT (FIN received)");
        }
        break;

    case TCP_STATE_FIN_WAIT_1:
        /* Waiting for ACK of our FIN.
         * Per RFC 793, we may still receive data in this state (data that
         * was sent before the peer received our FIN). Process it. */

        /* Process ACK */
        if (flags & TCP_FLAG_ACK) {
            if (SEQ_GT(ack_num, pcb->snd_una) && SEQ_LEQ(ack_num, pcb->snd_nxt)) {
                pcb->snd_una = ack_num;
                pcb->snd_wnd = pcb->wscale_ok ?
                    ((uint32_t)window << pcb->snd_wscale) : window;
            }
        }

        /* Process incoming data (peer may not have seen our FIN yet) */
        if (data_len > 0 && seq_num == pcb->rcv_nxt) {
            uint16_t space = TCP_RX_BUFFER_SIZE - pcb->rx_count;
            uint16_t copy_len = (data_len > space) ? space : data_len;

            if (copy_len > 0) {
                uint8_t* data_ptr = (uint8_t*)tcp + data_offset;
                for (uint16_t i = 0; i < copy_len; i++) {
                    pcb->rx_buffer[pcb->rx_tail] = data_ptr[i];
                    pcb->rx_tail = (pcb->rx_tail + 1) % TCP_RX_BUFFER_SIZE;
                }
                pcb->rx_count += copy_len;
                pcb->rcv_nxt += copy_len;
                pcb->rcv_wnd = TCP_RX_BUFFER_SIZE - pcb->rx_count;
            }
        }

        /* Check if our FIN has been ACKed */
        if ((flags & TCP_FLAG_ACK) && ack_num == pcb->snd_nxt) {
            if (flags & TCP_FLAG_FIN) {
                /* Simultaneous close + FIN ACK: FIN_WAIT_1 -> TIME_WAIT */
                pcb->rcv_nxt++;
                pcb->state = TCP_STATE_TIME_WAIT;
                pcb->last_rx_time = tcp_get_time_ms(); /* Reset TIME_WAIT timer */
                vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
                VOS3_INFO("[TCP] FIN_WAIT_1 -> TIME_WAIT");
            } else {
                pcb->state = TCP_STATE_FIN_WAIT_2;
                pcb->last_rx_time = tcp_get_time_ms(); /* Start FIN_WAIT_2 timer */
                VOS3_INFO("[TCP] FIN_WAIT_1 -> FIN_WAIT_2");
            }
        }
        else if (flags & TCP_FLAG_FIN) {
            /* Simultaneous close: peer also sent FIN but hasn't ACKed ours */
            pcb->rcv_nxt++;
            pcb->state = TCP_STATE_CLOSING;
            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
            VOS3_INFO("[TCP] FIN_WAIT_1 -> CLOSING");
        } else if (data_len > 0) {
            /* We received data, send ACK */
            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
        }
        break;

    case TCP_STATE_FIN_WAIT_2:
        /* Waiting for FIN from peer.
         * Per RFC 793, we can still receive data in this state. */

        /* Process incoming data */
        if (data_len > 0 && seq_num == pcb->rcv_nxt) {
            uint16_t space = TCP_RX_BUFFER_SIZE - pcb->rx_count;
            uint16_t copy_len = (data_len > space) ? space : data_len;

            if (copy_len > 0) {
                uint8_t* data_ptr = (uint8_t*)tcp + data_offset;
                for (uint16_t i = 0; i < copy_len; i++) {
                    pcb->rx_buffer[pcb->rx_tail] = data_ptr[i];
                    pcb->rx_tail = (pcb->rx_tail + 1) % TCP_RX_BUFFER_SIZE;
                }
                pcb->rx_count += copy_len;
                pcb->rcv_nxt += copy_len;
                pcb->rcv_wnd = TCP_RX_BUFFER_SIZE - pcb->rx_count;
                vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
            }
        }

        /* Check for FIN */
        if (flags & TCP_FLAG_FIN) {
            pcb->rcv_nxt++;
            pcb->state = TCP_STATE_TIME_WAIT;
            pcb->last_rx_time = tcp_get_time_ms(); /* Reset TIME_WAIT timer */
            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
            VOS3_INFO("[TCP] FIN_WAIT_2 -> TIME_WAIT");
        }
        break;

    case TCP_STATE_CLOSE_WAIT:
        /* Waiting for application to close */
        /* Process any remaining ACKs */
        if (flags & TCP_FLAG_ACK) {
            if (SEQ_GT(ack_num, pcb->snd_una)) {
                pcb->snd_una = ack_num;
            }
        }
        break;

    case TCP_STATE_CLOSING:
        /* Simultaneous close - waiting for ACK */
        if (flags & TCP_FLAG_ACK) {
            if (ack_num == pcb->snd_nxt) {
                pcb->state = TCP_STATE_TIME_WAIT;
                VOS3_INFO("[TCP] CLOSING -> TIME_WAIT");
            }
        }
        break;

    case TCP_STATE_LAST_ACK:
        /* Waiting for final ACK */
        if (flags & TCP_FLAG_ACK) {
            if (ack_num == pcb->snd_nxt) {
                pcb->state = TCP_STATE_CLOSED;
                VOS3_INFO("[TCP] LAST_ACK -> CLOSED");
            }
        }
        break;

    case TCP_STATE_TIME_WAIT:
        /* Waiting for stale segments to expire */
        /* Re-ACK any FIN received */
        if (flags & TCP_FLAG_FIN) {
            vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
        }
        break;

    default:
        VOS3_WARN("[TCP] Unknown state: %d", pcb->state);
        break;
    }

    vos3_netbuf_free(buf);
    return 0;
}

/* ============================================================================
 * TCP API FUNCTIONS
 * ============================================================================ */

int vos3_tcp_connect(vos3_tcp_pcb_t* pcb, uint32_t remote_ip, uint16_t remote_port)
{
    if (unlikely(pcb == NULL || pcb->state != TCP_STATE_CLOSED)) {
        return -22;  /* -EINVAL */
    }

    /* Set remote endpoint */
    pcb->remote_ip = remote_ip;
    pcb->remote_port = remote_port;

    /* Generate ISN (with randomization for security) */
    pcb->iss = vos3_tcp_generate_isn();
    pcb->snd_una = pcb->iss;
    pcb->snd_nxt = pcb->iss;

    /* Transition to SYN_SENT */
    pcb->state = TCP_STATE_SYN_SENT;

    /* Send SYN */
    int ret = vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_SYN);
    if (ret < 0) {
        pcb->state = TCP_STATE_CLOSED;
        return ret;
    }

    VOS3_INFO("[TCP] CLOSED -> SYN_SENT (ISS=%u) to %u.%u.%u.%u:%u",
              pcb->iss,
              remote_ip & 0xFF, (remote_ip >> 8) & 0xFF,
              (remote_ip >> 16) & 0xFF, (remote_ip >> 24) & 0xFF,
              remote_port);

    return 0;
}

int vos3_tcp_listen(vos3_tcp_pcb_t* pcb, int backlog)
{
    if (unlikely(pcb == NULL || pcb->state != TCP_STATE_CLOSED)) {
        return -22;  /* -EINVAL */
    }

    if (backlog <= 0) {
        backlog = 1;
    }
    if (backlog > (int)TCP_BACKLOG_MAX) {
        backlog = TCP_BACKLOG_MAX;
    }

    pcb->backlog_max = (uint8_t)backlog;
    pcb->backlog_count = 0;
    pcb->state = TCP_STATE_LISTEN;

    VOS3_INFO("[TCP] CLOSED -> LISTEN on port %u (backlog=%d)",
              pcb->local_port, backlog);

    return 0;
}

vos3_tcp_pcb_t* vos3_tcp_accept(vos3_tcp_pcb_t* listen_pcb)
{
    if (unlikely(listen_pcb == NULL || listen_pcb->state != TCP_STATE_LISTEN)) {
        return NULL;
    }

    /* Find a completed connection in backlog */
    for (int i = 0; i < (int)TCP_BACKLOG_MAX; i++) {
        vos3_tcp_backlog_entry_t* entry = &listen_pcb->backlog[i];
        if (!entry->valid) {
            continue;
        }

        /* Allocate new PCB for this connection */
        vos3_tcp_pcb_t* new_pcb = vos3_tcp_pcb_alloc();
        if (new_pcb == NULL) {
            return NULL;
        }

        /* Initialize the new PCB */
        new_pcb->local_ip = listen_pcb->local_ip;
        new_pcb->local_port = listen_pcb->local_port;
        new_pcb->remote_ip = entry->remote_ip;
        new_pcb->remote_port = entry->remote_port;
        new_pcb->iss = entry->iss;
        new_pcb->irs = entry->irs;
        new_pcb->snd_una = entry->iss + 1;
        new_pcb->snd_nxt = entry->iss + 1;
        new_pcb->rcv_nxt = entry->irs + 1;
        new_pcb->state = TCP_STATE_ESTABLISHED;

        /* Propagate Window Scale from handshake (RFC 7323) */
        if (entry->wscale_ok) {
            new_pcb->snd_wscale = entry->wscale;
            new_pcb->rcv_wscale = TCP_OUR_WSCALE;
            new_pcb->wscale_ok = 1;
        }

        /* Remove from backlog */
        entry->valid = 0;
        listen_pcb->backlog_count--;

        VOS3_INFO("[TCP] Accepted connection from %u.%u.%u.%u:%u",
                  entry->remote_ip & 0xFF, (entry->remote_ip >> 8) & 0xFF,
                  (entry->remote_ip >> 16) & 0xFF, (entry->remote_ip >> 24) & 0xFF,
                  entry->remote_port);

        return new_pcb;
    }

    return NULL;  /* No pending connections */
}

int vos3_tcp_send(vos3_tcp_pcb_t* pcb, const void* data, size_t len)
{
    if (unlikely(pcb == NULL || pcb->state != TCP_STATE_ESTABLISHED)) {
        return -107;  /* -ENOTCONN */
    }

    if (len == 0) {
        return 0;
    }

    /* Queue data in send buffer */
    uint16_t space = TCP_TX_BUFFER_SIZE - pcb->tx_count;
    if (space == 0) {
        return -11;  /* -EAGAIN */
    }

    size_t copy_len = (len > space) ? space : len;
    const uint8_t* src = (const uint8_t*)data;

    for (size_t i = 0; i < copy_len; i++) {
        pcb->tx_buffer[pcb->tx_tail] = src[i];
        pcb->tx_tail = (pcb->tx_tail + 1) % TCP_TX_BUFFER_SIZE;
    }
    pcb->tx_count += (uint16_t)copy_len;

    /* Send immediately if we have data */
    if (pcb->tx_count > 0) {
        /* For simplicity, send all buffered data as one segment */
        size_t send_len = (pcb->tx_count > pcb->mss) ? pcb->mss : pcb->tx_count;

        uint8_t send_buf[TCP_MSS_DEFAULT];
        for (size_t i = 0; i < send_len; i++) {
            send_buf[i] = pcb->tx_buffer[pcb->tx_head];
            pcb->tx_head = (pcb->tx_head + 1) % TCP_TX_BUFFER_SIZE;
        }
        pcb->tx_count -= (uint16_t)send_len;

        vos3_tcp_output(pcb, send_buf, send_len, TCP_FLAG_ACK | TCP_FLAG_PSH);
    }

    return (int)copy_len;
}

int vos3_tcp_recv(vos3_tcp_pcb_t* pcb, void* buf, size_t len)
{
    if (unlikely(pcb == NULL)) {
        return -9;  /* -EBADF */
    }

    if (pcb->state != TCP_STATE_ESTABLISHED &&
        pcb->state != TCP_STATE_FIN_WAIT_1 &&
        pcb->state != TCP_STATE_FIN_WAIT_2 &&
        pcb->state != TCP_STATE_CLOSE_WAIT) {
        return -107;  /* -ENOTCONN */
    }

    if (pcb->rx_count == 0) {
        return -11;  /* -EAGAIN */
    }

    size_t copy_len = (len > pcb->rx_count) ? pcb->rx_count : len;
    uint8_t* dst = (uint8_t*)buf;

    for (size_t i = 0; i < copy_len; i++) {
        dst[i] = pcb->rx_buffer[pcb->rx_head];
        pcb->rx_head = (pcb->rx_head + 1) % TCP_RX_BUFFER_SIZE;
    }
    pcb->rx_count -= (uint16_t)copy_len;
    pcb->rcv_wnd = TCP_RX_BUFFER_SIZE - pcb->rx_count;

    return (int)copy_len;
}

int vos3_tcp_close(vos3_tcp_pcb_t* pcb)
{
    if (unlikely(pcb == NULL)) {
        return -9;  /* -EBADF */
    }

    switch (pcb->state) {
    case TCP_STATE_CLOSED:
    case TCP_STATE_LISTEN:
        pcb->state = TCP_STATE_CLOSED;
        break;

    case TCP_STATE_SYN_SENT:
        pcb->state = TCP_STATE_CLOSED;
        break;

    case TCP_STATE_SYN_RECEIVED:
    case TCP_STATE_ESTABLISHED:
        /* Send FIN */
        vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_FIN | TCP_FLAG_ACK);
        pcb->state = TCP_STATE_FIN_WAIT_1;
        VOS3_INFO("[TCP] %s -> FIN_WAIT_1",
                  vos3_tcp_state_name(TCP_STATE_ESTABLISHED));
        break;

    case TCP_STATE_CLOSE_WAIT:
        /* Send FIN */
        vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_FIN | TCP_FLAG_ACK);
        pcb->state = TCP_STATE_LAST_ACK;
        VOS3_INFO("[TCP] CLOSE_WAIT -> LAST_ACK");
        break;

    default:
        /* Already closing */
        break;
    }

    return 0;
}

void vos3_tcp_abort(vos3_tcp_pcb_t* pcb)
{
    if (unlikely(pcb == NULL)) {
        return;
    }

    if (pcb->state != TCP_STATE_CLOSED &&
        pcb->state != TCP_STATE_LISTEN &&
        pcb->state != TCP_STATE_TIME_WAIT) {
        /* Send RST */
        vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_RST);
    }

    pcb->state = TCP_STATE_CLOSED;
    VOS3_INFO("[TCP] Connection aborted");
}

/* ============================================================================
 * TCP TIMER
 * ============================================================================ */

/** @brief FIN_WAIT_2 timeout: 60 seconds (prevent infinite orphan connections) */
#define TCP_FIN_WAIT_2_TIMEOUT_MS   60000U

void vos3_tcp_timer_tick(void)
{
    uint32_t now = tcp_get_time_ms();

    vos3_spinlock_lock(&g_tcp_lock);

    for (size_t i = 0; i < TCP_PCB_POOL_SIZE; i++) {
        vos3_tcp_pcb_t* pcb = &g_tcp_pcb_pool[i];
        if (!pcb->allocated) {
            continue;
        }

        /* TIME_WAIT timeout (2*MSL) */
        if (pcb->state == TCP_STATE_TIME_WAIT) {
            if (now - pcb->last_rx_time > TCP_TIME_WAIT_MS) {
                VOS3_INFO("[TCP] TIME_WAIT expired");
                pcb->state = TCP_STATE_CLOSED;
            }
        }

        /* FIN_WAIT_2 timeout - prevent orphaned half-closed connections.
         * RFC 793 doesn't specify a timeout here, but Linux uses
         * tcp_fin_timeout (default 60s) to prevent resource leaks. */
        if (pcb->state == TCP_STATE_FIN_WAIT_2) {
            if (now - pcb->last_rx_time > TCP_FIN_WAIT_2_TIMEOUT_MS) {
                VOS3_INFO("[TCP] FIN_WAIT_2 timeout (60s), closing");
                pcb->state = TCP_STATE_CLOSED;
            }
        }

        /* SYN retransmission timeout */
        if (pcb->state == TCP_STATE_SYN_SENT ||
            pcb->state == TCP_STATE_SYN_RECEIVED) {
            if (now - pcb->last_tx_time > pcb->rto) {
                if (pcb->retransmits < TCP_MAX_RETRIES) {
                    /* Retransmit SYN */
                    pcb->snd_nxt = pcb->iss;
                    vos3_tcp_output(pcb, NULL, 0,
                                    (pcb->state == TCP_STATE_SYN_SENT) ?
                                    TCP_FLAG_SYN : (TCP_FLAG_SYN | TCP_FLAG_ACK));
                    pcb->retransmits++;
                    pcb->rto *= 2;  /* Exponential backoff */
                    if (pcb->rto > TCP_RTO_MAX_MS) {
                        pcb->rto = TCP_RTO_MAX_MS;
                    }
                    g_tcp_stats.retransmits++;
                    VOS3_DEBUG("[TCP] Retransmit SYN (attempt %u)", pcb->retransmits);
                } else {
                    VOS3_WARN("[TCP] SYN connection timed out after %u retries",
                              TCP_MAX_RETRIES);
                    pcb->state = TCP_STATE_CLOSED;
                }
            }
        }

        /* Data retransmission timeout for ESTABLISHED / FIN_WAIT_1 states.
         * If we have unacknowledged data (snd_una < snd_nxt) and the RTO
         * has elapsed since last transmit, retransmit from snd_una. */
        if ((pcb->state == TCP_STATE_ESTABLISHED ||
             pcb->state == TCP_STATE_FIN_WAIT_1) &&
            SEQ_LT(pcb->snd_una, pcb->snd_nxt)) {
            if (now - pcb->last_tx_time > pcb->rto) {
                if (pcb->retransmits < TCP_MAX_RETRIES) {
                    /* Retransmit unacked data from retransmission queue */
                    int rexmit_done = 0;
                    for (int qi = 0; qi < 4; qi++) {
                        vos3_tcp_rexmit_entry_t* entry = &pcb->rexmit_queue[qi];
                        if (entry->valid && entry->data != NULL &&
                            SEQ_GEQ(entry->seq, pcb->snd_una)) {
                            /* Retransmit this segment */
                            uint32_t saved_nxt = pcb->snd_nxt;
                            pcb->snd_nxt = entry->seq;
                            vos3_tcp_output(pcb, entry->data, entry->len,
                                            TCP_FLAG_ACK | TCP_FLAG_PSH);
                            pcb->snd_nxt = saved_nxt;
                            entry->retries++;
                            rexmit_done = 1;
                            break;
                        }
                    }

                    /* If no queued segment found, send a zero-window probe
                     * or duplicate ACK to elicit a response */
                    if (!rexmit_done) {
                        vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_ACK);
                    }

                    pcb->retransmits++;
                    pcb->rto *= 2;  /* Exponential backoff */
                    if (pcb->rto > TCP_RTO_MAX_MS) {
                        pcb->rto = TCP_RTO_MAX_MS;
                    }

                    /* Congestion control: reduce cwnd on RTO timeout (Reno §3.1) */
                    pcb->ssthresh = pcb->cwnd / 2;
                    pcb->in_fast_recovery = 0;
                    pcb->dup_ack_count = 0;
                    if (pcb->ssthresh < pcb->mss * 2) {
                        pcb->ssthresh = pcb->mss * 2;
                    }
                    pcb->cwnd = pcb->mss;

                    g_tcp_stats.retransmits++;
                    VOS3_DEBUG("[TCP] Data retransmit (attempt %u, rto=%u ms)",
                               pcb->retransmits, pcb->rto);
                } else {
                    VOS3_WARN("[TCP] Data retransmit limit reached, aborting");
                    vos3_tcp_output(pcb, NULL, 0, TCP_FLAG_RST);
                    pcb->state = TCP_STATE_CLOSED;
                    g_tcp_stats.connections_reset++;
                }
            }
        }

        /* Reset retransmit counter when all data is ACKed */
        if ((pcb->state == TCP_STATE_ESTABLISHED ||
             pcb->state == TCP_STATE_FIN_WAIT_1 ||
             pcb->state == TCP_STATE_CLOSE_WAIT) &&
            pcb->snd_una == pcb->snd_nxt) {
            if (pcb->retransmits > 0) {
                pcb->retransmits = 0;
                pcb->rto = TCP_RTO_MIN_MS; /* Reset RTO */
            }
        }
    }

    vos3_spinlock_unlock(&g_tcp_lock);
}

/* ============================================================================
 * TCP INITIALIZATION
 * ============================================================================ */

int vos3_tcp_init(void)
{
    /* Clear PCB pool */
    memset(g_tcp_pcb_pool, 0, sizeof(g_tcp_pcb_pool));

    /* Clear statistics */
    memset(&g_tcp_stats, 0, sizeof(g_tcp_stats));

    /* Initialize ISN counter from CSPRNG */
    g_isn_counter = vos3_entropy_get_u32();

    VOS3_INFO("[TCP] TCP subsystem initialized");
    VOS3_INFO("[TCP] PCB pool: %u connections", TCP_PCB_POOL_SIZE);
    VOS3_INFO("[TCP] MSS: %u bytes, Window: %u bytes", TCP_MSS_DEFAULT, TCP_WINDOW_DEFAULT);
    VOS3_INFO("[TCP-SEC] ISN randomization: ENABLED (RFC 6528)");

    return 0;
}
