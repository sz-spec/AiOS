/**
 * @file net_security.h
 * @brief VOS3 Network Security Validation Macros
 *
 * @details Security-first validation macros for network packet processing.
 *          Based on CVE analysis:
 *          - CVE-2023-6693: VirtIO buffer overflow
 *          - CVE-2021-3416: VirtIO descriptor index OOB
 *          - CVE-2020-10756: Out-of-bounds read in networking
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

#ifndef VOS3_NET_SECURITY_H
#define VOS3_NET_SECURITY_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "net.h"
#include "console.h"

/* ============================================================================
 * SECURITY POLICY: TRUST NO LENGTH
 * ============================================================================
 *
 * CVE-2023-6693: Buffer overflow due to unchecked length field.
 *
 * RULE: Never use a length field from a packet header without validation.
 *       All length values from external sources are HOSTILE.
 */

/**
 * @brief Validate packet length against maximum
 *
 * @param len       Length value to validate (potentially hostile)
 * @param max_len   Maximum allowed length
 * @return 1 if valid, 0 if invalid
 */
#define VOS3_NET_LEN_VALID(len, max_len) \
    ((size_t)(len) <= (size_t)(max_len))

/**
 * @brief Validate packet length is within range
 *
 * @param len       Length value to validate
 * @param min_len   Minimum required length
 * @param max_len   Maximum allowed length
 * @return 1 if valid, 0 if invalid
 */
#define VOS3_NET_LEN_IN_RANGE(len, min_len, max_len) \
    (((size_t)(len) >= (size_t)(min_len)) && ((size_t)(len) <= (size_t)(max_len)))

/**
 * @brief Validate and clamp length to safe maximum
 *
 * @param len       Length value to validate (modified in place)
 * @param max_len   Maximum allowed length
 */
#define VOS3_NET_LEN_CLAMP(len, max_len) \
    do { \
        if ((size_t)(len) > (size_t)(max_len)) { \
            (len) = (max_len); \
        } \
    } while (0)

/**
 * @brief Reject packet if length exceeds maximum (with logging)
 *
 * @param len       Length to check
 * @param max_len   Maximum allowed
 * @param label     Label for logging
 */
#define VOS3_NET_REJECT_IF_OVERSIZED(len, max_len, label) \
    do { \
        if ((size_t)(len) > (size_t)(max_len)) { \
            VOS3_WARN("[NET-SEC] %s: Oversized packet rejected (%zu > %zu)", \
                      (label), (size_t)(len), (size_t)(max_len)); \
            VOS3_NET_STAT_OVERSIZED(); \
            return -1; \
        } \
    } while (0)

/**
 * @brief Reject packet if length is below minimum (with logging)
 *
 * @param len       Length to check
 * @param min_len   Minimum required
 * @param label     Label for logging
 */
#define VOS3_NET_REJECT_IF_UNDERSIZED(len, min_len, label) \
    do { \
        if ((size_t)(len) < (size_t)(min_len)) { \
            VOS3_WARN("[NET-SEC] %s: Undersized packet rejected (%zu < %zu)", \
                      (label), (size_t)(len), (size_t)(min_len)); \
            VOS3_NET_STAT_UNDERSIZED(); \
            return -1; \
        } \
    } while (0)

/* ============================================================================
 * SECURITY POLICY: DESCRIPTOR VALIDATION
 * ============================================================================
 *
 * CVE-2021-3416: VirtIO descriptor index out-of-bounds access.
 *
 * RULE: Before processing any VirtQueue descriptor, verify idx < QUEUE_NUM.
 */

/**
 * @brief Validate VirtQueue descriptor index
 *
 * @param idx       Descriptor index to validate
 * @param queue_num Queue size (number of descriptors)
 * @return 1 if valid, 0 if out-of-bounds
 */
#define VOS3_VIRTQ_IDX_VALID(idx, queue_num) \
    ((uint16_t)(idx) < (uint16_t)(queue_num))

/**
 * @brief Reject if descriptor index is invalid (with logging)
 *
 * @param idx       Index to check
 * @param queue_num Queue size
 * @param label     Label for logging
 */
#define VOS3_VIRTQ_REJECT_BAD_IDX(idx, queue_num, label) \
    do { \
        if (!VOS3_VIRTQ_IDX_VALID((idx), (queue_num))) { \
            VOS3_ERROR("[NET-SEC] %s: Invalid descriptor index %u >= %u", \
                       (label), (unsigned)(idx), (unsigned)(queue_num)); \
            return -1; \
        } \
    } while (0)

/**
 * @brief Validate descriptor chain doesn't exceed limit
 *
 * @param chain_len Current chain length
 * @param max_chain Maximum allowed chain length
 * @return 1 if valid, 0 if chain too long
 */
#define VOS3_VIRTQ_CHAIN_VALID(chain_len, max_chain) \
    ((size_t)(chain_len) < (size_t)(max_chain))

/* ============================================================================
 * SECURITY POLICY: DMA ISOLATION
 * ============================================================================
 *
 * RULE: Treat DMA buffers as 'volatile' and 'hostile'.
 *       Copy data out to a safe kernel buffer immediately.
 *       Never trust data in DMA buffers after initial validation.
 */

/**
 * @brief Mark a buffer as containing hostile (external) data
 *
 * @param buf   Network buffer to mark
 */
#define VOS3_NET_MARK_HOSTILE(buf) \
    do { \
        (buf)->flags |= VOS3_NETBUF_F_HOSTILE; \
        (buf)->flags &= ~VOS3_NETBUF_F_VALIDATED; \
    } while (0)

/**
 * @brief Mark a buffer as validated (safe for processing)
 *
 * @param buf   Network buffer to mark
 */
#define VOS3_NET_MARK_VALIDATED(buf) \
    do { \
        (buf)->flags |= VOS3_NETBUF_F_VALIDATED; \
        (buf)->flags &= ~VOS3_NETBUF_F_HOSTILE; \
    } while (0)

/**
 * @brief Check if buffer contains hostile data
 *
 * @param buf   Buffer to check
 * @return 1 if hostile, 0 if safe
 */
#define VOS3_NET_IS_HOSTILE(buf) \
    (((buf)->flags & VOS3_NETBUF_F_HOSTILE) != 0U)

/**
 * @brief Check if buffer has been validated
 *
 * @param buf   Buffer to check
 * @return 1 if validated, 0 if not
 */
#define VOS3_NET_IS_VALIDATED(buf) \
    (((buf)->flags & VOS3_NETBUF_F_VALIDATED) != 0U)

/**
 * @brief Safely copy from DMA buffer (volatile read)
 *
 * @param dst       Destination (kernel buffer)
 * @param src       Source (DMA buffer, treated as volatile)
 * @param len       Number of bytes to copy
 */
#define VOS3_NET_DMA_COPY_IN(dst, src, len) \
    do { \
        volatile const uint8_t* _src = (volatile const uint8_t*)(src); \
        uint8_t* _dst = (uint8_t*)(dst); \
        size_t _len = (len); \
        for (size_t _i = 0; _i < _len; _i++) { \
            _dst[_i] = _src[_i]; \
        } \
    } while (0)

/**
 * @brief Safely copy to DMA buffer (volatile write)
 *
 * @param dst       Destination (DMA buffer)
 * @param src       Source (kernel buffer)
 * @param len       Number of bytes to copy
 */
#define VOS3_NET_DMA_COPY_OUT(dst, src, len) \
    do { \
        volatile uint8_t* _dst = (volatile uint8_t*)(dst); \
        const uint8_t* _src = (const uint8_t*)(src); \
        size_t _len = (len); \
        for (size_t _i = 0; _i < _len; _i++) { \
            _dst[_i] = _src[_i]; \
        } \
    } while (0)

/* ============================================================================
 * SECURITY POLICY: INTERRUPT BUDGET
 * ============================================================================
 *
 * RULE: Limit ISR loop to max N packets per interrupt to prevent DoS.
 *       A malicious guest could flood the network with packets.
 */

/**
 * @brief Check if interrupt budget is exhausted
 *
 * @param count     Current packet count
 * @param budget    Maximum allowed per interrupt
 * @return 1 if budget exhausted, 0 if more work allowed
 */
#define VOS3_NET_BUDGET_EXHAUSTED(count, budget) \
    ((size_t)(count) >= (size_t)(budget))

/**
 * @brief Interrupt budget tracking structure
 */
typedef struct vos3_net_irq_budget {
    uint32_t packets_processed;
    uint32_t budget_max;
    uint64_t budget_exhausted_count;
} vos3_net_irq_budget_t;

/**
 * @brief Initialize IRQ budget tracker
 *
 * @param budget    Budget structure to initialize
 * @param max       Maximum packets per interrupt
 */
#define VOS3_NET_BUDGET_INIT(budget, max) \
    do { \
        (budget)->packets_processed = 0; \
        (budget)->budget_max = (max); \
        (budget)->budget_exhausted_count = 0; \
    } while (0)

/**
 * @brief Reset budget for new interrupt
 *
 * @param budget    Budget structure to reset
 */
#define VOS3_NET_BUDGET_RESET(budget) \
    do { \
        (budget)->packets_processed = 0; \
    } while (0)

/**
 * @brief Consume budget for one packet
 *
 * @param budget    Budget structure
 * @return 1 if packet can be processed, 0 if budget exhausted
 */
static inline int vos3_net_budget_consume(vos3_net_irq_budget_t* budget)
{
    if (budget->packets_processed >= budget->budget_max) {
        budget->budget_exhausted_count++;
        return 0;
    }
    budget->packets_processed++;
    return 1;
}

/* ============================================================================
 * SECURITY POLICY: HEADER VALIDATION
 * ============================================================================
 *
 * RULE: Validate all protocol headers before trusting any field.
 */

/**
 * @brief Validate Ethernet header
 *
 * @param buf   Network buffer to validate
 * @return 1 if valid, 0 if invalid
 */
static inline int vos3_net_validate_eth_header(const vos3_netbuf_t* buf)
{
    /* Minimum Ethernet frame size */
    if (buf->len < VOS3_NET_FRAME_MIN) {
        return 0;
    }

    /* Maximum frame size */
    if (buf->len > VOS3_NET_MTU_MAX) {
        return 0;
    }

    return 1;
}

/**
 * @brief Validate IPv4 header
 *
 * @param hdr       IPv4 header to validate
 * @param buf_len   Remaining buffer length
 * @return 1 if valid, 0 if invalid
 */
static inline int vos3_net_validate_ipv4_header(const vos3_ipv4_header_t* hdr,
                                                 size_t buf_len)
{
    /* Check version */
    uint8_t version = (hdr->version_ihl >> 4) & 0x0FU;
    if (version != 4U) {
        return 0;
    }

    /* Check IHL (Internet Header Length) */
    uint8_t ihl = hdr->version_ihl & 0x0FU;
    if (ihl < 5U || ihl > 15U) {
        return 0;
    }

    size_t header_len = (size_t)ihl * 4U;

    /* Verify header fits in buffer */
    if (header_len > buf_len) {
        return 0;
    }

    /* Verify total length is sane */
    uint16_t total_len = vos3_ntohs(hdr->total_length);
    if (total_len < header_len) {
        return 0;
    }

    if (total_len > buf_len) {
        return 0;
    }

    return 1;
}

/* ============================================================================
 * SECURITY POLICY: ATOMIC BUFFER SANITIZATION (CVE-2026-23057)
 * ============================================================================
 *
 * CVE-2026-23057: Information Leak via Uninitialized Network Buffer Padding.
 *
 * RULE: Every network buffer MUST be zeroed on allocation AND before TX.
 *       No uninitialized kernel memory can ever touch the NIC.
 *       This prevents leaking kernel heap data, stack data, or
 *       sensitive information to the network.
 */

/**
 * @brief Sanitize a network buffer (zero all data)
 *
 * @param buf   Network buffer to sanitize
 * @param len   Length to sanitize (typically capacity)
 */
#define VOS3_NET_SANITIZE_BUFFER(buf, len) \
    do { \
        volatile uint8_t* _ptr = (volatile uint8_t*)(buf)->data; \
        size_t _len = (len); \
        for (size_t _i = 0; _i < _len; _i++) { \
            _ptr[_i] = 0; \
        } \
    } while (0)

/**
 * @brief Sanitize buffer before TX (CVE-2026-23057 mitigation)
 *
 * Zeros the entire buffer capacity to ensure no uninitialized
 * padding bytes leak to the network.
 *
 * @param buf   Network buffer to sanitize
 */
#define VOS3_NET_SANITIZE_TX(buf) \
    do { \
        VOS3_NET_SANITIZE_BUFFER((buf), (buf)->capacity); \
        g_net_security_stats.tx_sanitized++; \
    } while (0)

/**
 * @brief Verify buffer is sanitized (all zeros beyond data)
 *
 * @param buf   Buffer to check
 * @param data_len  Actual data length
 * @return 1 if sanitized (no info leak), 0 if padding contains data
 */
static inline int vos3_net_verify_sanitized(const vos3_netbuf_t* buf,
                                             size_t data_len)
{
    /* Check padding beyond actual data is zeroed */
    if (data_len >= buf->capacity) {
        return 1;  /* No padding to check */
    }

    const uint8_t* padding = buf->data + data_len;
    size_t padding_len = buf->capacity - data_len;

    for (size_t i = 0; i < padding_len; i++) {
        if (padding[i] != 0) {
            return 0;  /* Info leak detected */
        }
    }

    return 1;
}

/* ============================================================================
 * SECURITY POLICY: TX LENGTH TRUNCATION (CVE-2026-23086)
 * ============================================================================
 *
 * CVE-2026-23086: Resource DoS via oversized TX buffer requests.
 *
 * RULE: If a TX request exceeds MTU_MAX, TRUNCATE and LOG security alert.
 *       Never allow oversized buffers to reach the NIC.
 */

/**
 * @brief Truncate TX length to MTU_MAX with security alert
 *
 * @param len       Length to check (modified in place)
 * @param max_len   Maximum allowed (typically VOS3_NET_MTU_MAX)
 */
#define VOS3_NET_TX_TRUNCATE(len, max_len) \
    do { \
        if ((size_t)(len) > (size_t)(max_len)) { \
            VOS3_WARN("[NET-SEC] CVE-2026-23086: TX truncated %zu -> %zu", \
                      (size_t)(len), (size_t)(max_len)); \
            g_net_security_stats.tx_truncated++; \
            (len) = (max_len); \
        } \
    } while (0)

/* ============================================================================
 * SECURITY STATISTICS
 * ============================================================================ */

/**
 * @brief Network security statistics
 */
typedef struct vos3_net_security_stats {
    uint64_t oversized_dropped;
    uint64_t undersized_dropped;
    uint64_t invalid_header_dropped;
    uint64_t invalid_descriptor_dropped;
    uint64_t budget_exceeded_events;
    uint64_t hostile_packets_received;
    uint64_t validation_failures;
    /* CVE-2026-23057: Info leak prevention */
    uint64_t tx_sanitized;
    /* CVE-2026-23086: TX truncation */
    uint64_t tx_truncated;
    /* CVE-2026-25060: ARP MitM prevention */
    uint64_t gratuitous_arp_dropped;
    uint64_t arp_spoof_blocked;
} vos3_net_security_stats_t;

/** @brief Global network security statistics */
extern vos3_net_security_stats_t g_net_security_stats;

/**
 * @brief Increment oversized packet counter
 */
#define VOS3_NET_STAT_OVERSIZED() \
    do { g_net_security_stats.oversized_dropped++; } while (0)

/**
 * @brief Increment undersized packet counter
 */
#define VOS3_NET_STAT_UNDERSIZED() \
    do { g_net_security_stats.undersized_dropped++; } while (0)

/**
 * @brief Increment invalid header counter
 */
#define VOS3_NET_STAT_INVALID_HEADER() \
    do { g_net_security_stats.invalid_header_dropped++; } while (0)

/**
 * @brief Increment invalid descriptor counter
 */
#define VOS3_NET_STAT_INVALID_DESC() \
    do { g_net_security_stats.invalid_descriptor_dropped++; } while (0)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_NET_SECURITY_H */
