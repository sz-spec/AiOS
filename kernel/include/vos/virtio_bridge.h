/**
 * @file virtio_bridge.h
 * @brief VOS3 Serial Bridge (COM2) - Kernel <-> Python Backend
 *
 * @details Bidirectional serial bridge over QEMU's second serial port.
 *          Enables the Python/FastAPI backend to read/write the kernel's
 *          persistent VFS via a simple text protocol.
 *
 * @version 1.0.0
 * @date 2026-02-23
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_VIRTIO_BRIDGE_H
#define VOS3_VIRTIO_BRIDGE_H

#include <stdint.h>

/**
 * @brief Initialize COM2 serial port for bridge communication
 * @return 0 on success, negative on error
 */
int vos3_bridge_init(void);

/**
 * @brief Non-blocking poll: read chars from COM2, dispatch complete commands
 */
void vos3_bridge_poll(void);

/**
 * @brief Kernel task entry point for bridge polling loop
 * @param[in] arg Unused
 */
void vos3_bridge_task(void* arg);

/* ============================================================================
 * TOKEN BUCKET RATE LIMITER — VBus frame-level congestion control
 *
 * Replaces the simple boolean g_congestion flag with a proper token bucket
 * that enforces sustained throughput limits and bans on CRC-flood attacks.
 * ============================================================================ */

/**
 * @brief Token bucket state for VBus rate limiting.
 *
 * Tokens are stored in fixed-point (scaled by 1000) to allow sub-token
 * refill rates without floating point.
 */
typedef struct vos3_token_bucket {
    uint64_t tokens;          /**< Current tokens (fixed-point, scaled by 1000) */
    uint64_t capacity;        /**< Max tokens (scaled) */
    uint64_t rate;            /**< Tokens per tick (scaled) */
    uint64_t last_tick;       /**< Last refill tick count */
    uint32_t bad_crc_count;   /**< Bad CRC counter within current window */
    uint64_t bad_crc_window;  /**< Window start tick */
    uint64_t ban_until;       /**< Ban expiry tick (0 = not banned) */
} vos3_token_bucket_t;

/**
 * @brief Initialize a token bucket with default VBus parameters.
 * @param[in] tb  Token bucket to initialize
 */
void vos3_token_bucket_init(vos3_token_bucket_t *tb);

/**
 * @brief Try to consume tokens from the bucket.
 *
 * Refills tokens based on elapsed scheduler ticks, then subtracts cost.
 * @param[in] tb    Token bucket
 * @param[in] cost  Number of tokens to consume (fixed-point, scaled by 1000)
 * @return 1 if sufficient tokens (consumed), 0 if depleted or banned
 */
int vos3_token_bucket_consume(vos3_token_bucket_t *tb, uint32_t cost);

/**
 * @brief Record a bad CRC event for flood detection.
 *
 * If 50+ bad CRCs occur within a 6000-tick (~60s) window, bans the
 * source for 6000 ticks.
 * @param[in] tb  Token bucket
 */
void vos3_token_bucket_bad_crc(vos3_token_bucket_t *tb);

#endif /* VOS3_VIRTIO_BRIDGE_H */
