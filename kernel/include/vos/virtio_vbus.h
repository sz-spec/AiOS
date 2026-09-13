/**
 * @file virtio_vbus.h
 * @brief VOS3 VBus — Binary VirtIO-Serial Transport for Bridge (v3.0)
 *
 * @details Tagged-frame binary transport over QEMU virtio-serial-pci.
 *
 *          Frame format (64-byte header, 512-bit aligned — v3.0 unified):
 *          [u16:magic 0x5642 LE][u8:version 0x03][u8:frame_type]
 *          [u16:tag LE][u16:payload_len LE]
 *          [u32:hdr_crc LE][u32:payload_crc LE]
 *          [u8:mac[32]][u8:flags][u8:reserved[15]]
 *
 *          hdr_crc:     CRC32C over header[0:8] (magic+version+type+tag+len)
 *          payload_crc: CRC32C(payload, seed=hdr_crc) — seeded/chained
 *
 * @version 3.0.0
 * @date 2026-04-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 10 — Unified Wire Protocol (C/Rust alignment)
 */

#ifndef VOS3_VIRTIO_VBUS_H
#define VOS3_VIRTIO_VBUS_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "virtio_core.h"

/* ============================================================================
 * VIRTIO-SERIAL DEVICE IDENTIFIERS
 * ============================================================================ */

/** @brief VirtIO Console/Serial transitional device ID */
#define VIRTIO_SERIAL_DEVICE_ID         0x1003U

/** @brief VirtIO Console/Serial subsystem ID */
#define VIRTIO_SERIAL_SUBSYSTEM_ID      3U

/* ============================================================================
 * VBUS QUEUE CONFIGURATION
 * ============================================================================ */

/** @brief RX virtqueue index (device → driver) */
#define VBUS_RX_QUEUE_IDX              0U

/** @brief TX virtqueue index (driver → device) */
#define VBUS_TX_QUEUE_IDX              1U

/** @brief Queue size (descriptors per queue) */
#define VBUS_QUEUE_SIZE                256U

/* ============================================================================
 * VBUS FRAME TYPES
 * ============================================================================ */

/** @brief Command frame (host → guest) */
#define VBUS_TYPE_CMD                  0x01U

/** @brief Response frame (guest → host) */
#define VBUS_TYPE_RESP                 0x02U

/** @brief Bulk data frame */
#define VBUS_TYPE_DATA                 0x03U

/** @brief Handshake frame */
#define VBUS_TYPE_HANDSHAKE            0x04U

/** @brief Ping/keepalive frame */
#define VBUS_TYPE_PING                 0x05U

/** @brief Async kernel→backend event (Phase 4.2.5) */
#define VBUS_TYPE_EVENT                0x06U

/** @brief Token stream frame — KIM inference tokens (Phase 6) */
#define VBUS_TYPE_TOKEN_STREAM         0x07U

/** @brief Deep diagnostic feedback frame (Phase 4.2.7) */
#define VBUS_TYPE_FEEDBACK             0x08U

/** @brief Atomic batch frame — multiple \n-delimited commands (Phase 4.2.7) */
#define VBUS_TYPE_BATCH                0x09U

/** @brief Real-time background push (KAIROS) */
#define VBUS_TYPE_NOTIFY               0x0AU

/** @brief Backend→kernel slot wake (Webhooks) */
#define VBUS_TYPE_INTERRUPT            0x0BU

/** @brief Event subscription management (Phase 4.2.7+) */
#define VBUS_TYPE_SUBSCRIBE            0x0CU

/** @brief Phase 8: Token-Aware Shorthand frame (V-AAAK) */
#define VBUS_TYPE_V_AAAK               0x0DU

/** @brief Phase 2.3: Speculative token stream (ghost/verified) */
#define VBUS_TYPE_STREAM_SPEC          0x0EU

/** @brief Keep-alive pong (reply to PING, desktop transport only) */
#define VBUS_TYPE_PONG                 0x0FU

/** @brief Error notification frame (desktop transport only) */
#define VBUS_TYPE_ERROR                0x10U

/** @brief Resilience-Matrix F2 — out-of-band BUSY/backpressure signal.
 *
 *  Emitted when the kernel-side congestion flag (g_congestion) is set.
 *  Carries no payload; the client interprets it as "back off and retry
 *  after Retry-After ms" (the retry hint is encoded in the tag field
 *  as little-endian milliseconds, capped to UINT16_MAX). Distinguished
 *  from VBUS_TYPE_ERROR which signals protocol-level failure. */
#define VBUS_TYPE_BUSY                 0x11U

/* ============================================================================
 * VBUS FRAME LIMITS
 * ============================================================================ */

/** @brief Frame header size (v3.0: 64 bytes, 512-bit aligned).
 *  Layout: magic(2)+version(1)+type(1)+tag(2)+len(2)+hdr_crc(4)+payload_crc(4)+mac(32)+flags(1)+reserved(15) = 64.
 *  Payloads start on a 512-bit (AVX-512) boundary, eliminating vector-copy penalty. */
#define VBUS_FRAME_HDR_SIZE            64U

/** @brief Maximum payload per frame (v3.0: u16 payload_len, max 65535) */
#define VBUS_MAX_PAYLOAD               65535U

/** @brief Maximum TX payload (bounded by TX bounce buffer and u16 payload_len) */
#define VBUS_MAX_TX_PAYLOAD            (65536U - VBUS_FRAME_HDR_SIZE)

/** @brief v3.0 frame magic: 0x5642 ("VB" in little-endian) */
#define VBUS_MAGIC                     0x5642U

/* ============================================================================
 * VBUS TAG CONSTANTS
 * ============================================================================ */

/** @brief Tag for unsolicited async frames (EVENT/FEEDBACK/NOTIFY) */
#define VBUS_TAG_ASYNC                 0xFFFFU

/** @brief Tag for DATA frames (no response expected) */
#define VBUS_TAG_STREAM                0x0000U

/** @brief Protocol version (Phase 6.6: bumped to v3 for AAAK semantic dialect) */
#define VBUS_VERSION                   3U

/* ============================================================================
 * VBUS HMAC FRAME AUTHENTICATION (Phase 5 — Tier 2 Hardening)
 * ============================================================================ */

/** @brief Offset of HMAC-SHA256 within 64-byte header (bytes 16-47) */
#define VBUS_HMAC_OFFSET               16U

/** @brief HMAC-SHA256 digest size */
#define VBUS_HMAC_SIZE                 32U

/** @brief Offset of flags byte within 64-byte header */
#define VBUS_FLAGS_OFFSET              48U

/** @brief Flag bit 0: HMAC present in this frame */
#define VBUS_FLAG_HMAC                 0x01U

/** @brief Flag bit 1: V-AAAK semantic compression supported (Phase 6.6) */
#define VBUS_FLAG_AAAK                 0x02U

/** @brief Flag bit 2: Salted AAAK dictionary (Phase 6.6-U v3.1) */
#define VBUS_CAP_SALTED                0x04U

/** @brief Flag bit 3: Semantic jitter engine active (Phase 6.6-U v3.1) */
#define VBUS_CAP_JITTER                0x08U

/** @brief HMAC key size (256-bit) */
#define VBUS_HMAC_KEY_SIZE             32U

/** @brief AAAK session salt size (128-bit — Phase 6.6-U) */
#define VBUS_AAAK_SALT_SIZE            16U

/* ============================================================================
 * VBUS v3/v3.1 HANDSHAKE CONSTANTS (Phase 6.6 / 6.6-U)
 * ============================================================================ */

/** @brief v3 handshake magic (5 bytes) — enables AAAK semantic pipeline */
#define VBUS_HANDSHAKE_V3_MAGIC        "VBUS3"
#define VBUS_HANDSHAKE_V3_MAGIC_LEN    5U

/** @brief v3.1 handshake response salt offset:
 *  response = "VOS3-VBUS31-AAAK-HMAC-SALT" + 16-byte salt
 *  Salt enables per-session dictionary code XOR masking. */
#define VBUS_HANDSHAKE_V31_SALT_OFF    27U

/* ============================================================================
 * VBUS STATISTICS
 * ============================================================================ */

typedef struct vbus_stats {
    uint64_t tx_frames;
    uint64_t rx_frames;
    uint64_t tx_bytes;
    uint64_t rx_bytes;
    uint64_t crc_errors;
} vbus_stats_t;

/* ============================================================================
 * VBUS DEVICE STATE
 * ============================================================================ */

typedef struct vbus_device {
    int              initialized;
    uint16_t         io_base;
    uint64_t         features;
    virtio_queue_t   rx_queue;
    virtio_queue_t   tx_queue;
    vos3_spinlock_t  tx_lock;
    vos3_spinlock_t  rx_lock;
    vbus_stats_t     stats;
} vbus_device_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize VBus driver
 *
 * Probes for virtio-serial-pci device, negotiates features,
 * initializes RX/TX queues, pre-populates RX descriptors.
 *
 * @return 0 on success, -1 if no device found or init failed
 */
int vos3_vbus_init(void);

/**
 * @brief Check if VBus transport is available
 *
 * @return 1 if initialized and ready, 0 otherwise
 */
int vos3_vbus_available(void);

/**
 * @brief Send a framed message over VBus (v3 unified protocol)
 *
 * Builds 64-byte frame header with magic, version, dual-CRC, optional HMAC.
 * Copies into TX bounce buffer, submits to TX virtqueue, polls for completion.
 *
 * @param[in] type     Frame type (VBUS_TYPE_*)
 * @param[in] slot_id  Model slot ID (0xFF for unslotted) — encoded in payload
 * @param[in] tag      Correlation tag (VBUS_TAG_ASYNC for events)
 * @param[in] payload  Payload bytes
 * @param[in] len      Payload length (must be <= VBUS_MAX_PAYLOAD)
 * @return 0 on success, -1 on failure
 */
int vos3_vbus_send_frame(uint8_t type, uint8_t slot_id, uint16_t tag,
                          const void *payload, uint32_t len);

/**
 * @brief Receive a framed message from VBus (v3 unified protocol)
 *
 * Checks the RX used ring for completed descriptors, parses 64-byte
 * frame header with magic/version validation, verifies dual-CRC, copies
 * payload to caller buffer.
 *
 * @param[out] type_out     Received frame type
 * @param[out] slot_id_out  Slot ID (always 0xFF in v3 — slot routing is in payload)
 * @param[out] tag_out      Correlation tag from frame header
 * @param[out] payload_buf  Output buffer for payload
 * @param[in]  max_len      Size of payload_buf
 * @param[out] len_out      Actual payload length received
 * @return 0 on success, -1 if no frame available, -2 on CRC error, -3 HMAC fail
 */
int vos3_vbus_recv_frame(uint8_t *type_out, uint8_t *slot_id_out,
                          uint16_t *tag_out, void *payload_buf,
                          uint32_t max_len, uint32_t *len_out);

/**
 * @brief Poll VBus for incoming frames (non-blocking)
 *
 * Checks used ring and re-posts consumed RX descriptors.
 * Called from bridge poll loop.
 */
void vos3_vbus_poll(void);

/**
 * @brief Compute CRC32 (IEEE 802.3)
 *
 * @param[in] data   Data to checksum
 * @param[in] len    Data length
 * @return CRC32 value
 */
uint32_t vbus_crc32(const void *data, size_t len);

/* ============================================================================
 * PHASE 4.2.5: ASYNC EVENT SIGNALING
 * ============================================================================ */

/**
 * @brief Send async event frame to backend (fire-and-forget)
 *
 * Builds 14-byte EVENT payload [slot_id|event_code|value|timestamp]
 * and sends via TX queue. Uses trylock — silently drops if TX busy.
 * ISR-safe: no blocking spinlock acquisition.
 *
 * @param[in] slot_id     Model slot ID (0-3)
 * @param[in] event_code  Event code (vos3_vbus_event_code_t)
 * @param[in] value       Event-specific value
 */
void vos3_vbus_signal_event(uint8_t slot_id, uint8_t event_code, uint32_t value);

/**
 * @brief Signal an event using blocking send (guaranteed delivery).
 *
 * Use from bridge dispatch context (non-ISR) when event delivery must be
 * guaranteed (e.g., ISC_MESSAGE events). Do NOT use from ISR context.
 */
void vos3_vbus_signal_event_sync(uint8_t slot_id, uint8_t event_code, uint32_t value);

/**
 * @brief Send a frame with trylock (ISR-safe, fire-and-forget)
 *
 * Same as vos3_vbus_send_frame() but uses trylock on tx_lock.
 * Uses v3 unified wire format with magic/version header.
 * Returns -2 if lock not available (instead of blocking).
 *
 * @return 0 on success, -1 on error, -2 if TX locked (dropped)
 */
int vos3_vbus_send_frame_trylock(uint8_t type, uint8_t slot_id, uint16_t tag,
                                  const void *payload, uint32_t len);

/* ============================================================================
 * PHASE 4.2.7: DEEP DIAGNOSTIC FEEDBACK
 * ============================================================================ */

/**
 * @brief Send deep diagnostic feedback frame to backend
 *
 * Payload: [slot_id][fault_addr LE][fault_rip LE][fault_rsp LE]
 *          [stack_len][stack_capture...][tail_len][rx_tail...]
 * Uses trylock — fire-and-forget, ISR-safe.
 */
int  vos3_vbus_send_feedback(uint8_t slot_id, uintptr_t fault_addr,
                             uintptr_t fault_rip, uintptr_t fault_rsp,
                             const uint8_t *stack_capture, uint8_t stack_len,
                             const uint8_t *rx_tail, uint8_t tail_len);

/**
 * @brief Get last N bytes from RX reassembly buffer (for feedback context)
 */
void vos3_vbus_get_rx_tail(uint8_t *out, uint8_t max_len, uint8_t *actual_len);

/* ============================================================================
 * VBUS EVENT RING BUFFER (ISR deferral)
 * ============================================================================ */

/** @brief Event ring buffer depth */
#define VBUS_EVENT_RING_SIZE           64U

/**
 * @brief Event ring entry — deferred ISR events
 */
typedef struct vbus_event_entry {
    uint8_t  slot_id;
    uint8_t  event_code;
    uint32_t value;
    uint64_t timestamp;
} vbus_event_entry_t;

/**
 * @brief Push event to ring buffer (ISR-safe, no locks)
 *
 * Overwrites oldest entry on full ring.
 */
void vos3_vbus_event_ring_push(uint8_t slot_id, uint8_t code, uint32_t value);

/**
 * @brief Drain all pending events from ring to VBus frames
 *
 * Called from bridge poll (non-ISR context). Sends each queued event
 * via blocking send_frame(VBUS_TYPE_EVENT, ...).
 *
 * Resilience-Matrix F2 — vos3_vbus_send_busy(slot_id, retry_after_ms)
 * emits an out-of-band VBUS_TYPE_BUSY frame when g_congestion==1U.
 * The retry hint is carried in the frame's tag field (uint16 ms).
 * No-op when congestion is clear; trylock semantics so it cannot
 * deadlock the dispatcher.
 *
 * extern int vos3_vbus_send_busy(uint8_t slot_id, uint16_t retry_after_ms);
 *
 * @return Number of events drained
 */
int vos3_vbus_event_ring_drain(void);

/* ============================================================================
 * PHASE 4.2: ZERO-COPY STREAMING RX
 * ============================================================================ */

/**
 * @brief Start streaming mode for model weight loading
 *
 * Uses bounce buffer for all frames. DATA frame payloads are copied to
 * HugePage memory by the bridge poll handler (single-copy, no chaining).
 *
 * @param[in] phys_pages  HugePage physical addresses (stored for reference)
 * @param[in] num_pages   Number of HugePages
 * @param[in] chunk_size  Per-frame payload chunk size (e.g., 16384)
 * @param[in] total_size  Total model size in bytes
 * @param[in] virt_base   Virtual base address of model HugePage region
 * @return 0 on success, -1 on failure
 */
int vos3_vbus_start_streaming(const uint64_t *phys_pages, uint32_t num_pages,
                               size_t chunk_size, size_t total_size,
                               uintptr_t virt_base);

/**
 * @brief Advance streaming offset after DATA frame processed
 * @param[in] bytes  Number of bytes to advance
 */
void vos3_vbus_stream_advance(size_t bytes);

/**
 * @brief Rewind streaming offset for Smart-Retry (MODEL_REWIND)
 * @param[in] new_offset  Byte offset to rewind to
 */
void vos3_vbus_rewind_streaming(size_t new_offset);

/**
 * @brief Stop streaming mode
 */
void vos3_vbus_stop_streaming(void);
void vos3_vbus_reset_rx(void);

/** Phase 4.2.11: Full VirtIO device reset + re-initialization.
 *  Clears all DMA state for absolute stream synchronization on reconnect. */
void vos3_vbus_hardware_reset(void);

/**
 * @brief Check if streaming mode is active
 * @return 1 if streaming, 0 otherwise
 */
int vos3_vbus_is_streaming(void);

/**
 * @brief Get current streaming offset (bytes received)
 * @return Current offset in bytes
 */
size_t vos3_vbus_streaming_offset(void);

/**
 * @brief Get virtual base address of streaming HugePage region
 * @return Virtual base address
 */
uintptr_t vos3_vbus_streaming_virt_base(void);

/* ============================================================================
 * PHASE 4.3: ZERO-COPY DMA WITH PTE SAFETY
 * ============================================================================ */

/**
 * @brief Activate zero-copy DMA mode for streaming.
 *
 * Protects the target HugePages (clears PRESENT bit) so no agent can read
 * partially-written data during transfer. Kernel writes through the
 * PHYS_MAP_OFFSET identity map, bypassing the slot's page tables.
 *
 * @param[in] slot_id    Model slot ID (0-3)
 * @param[in] phys_pages Array of HugePage physical addresses
 * @param[in] num_pages  Number of HugePages
 * @param[in] virt_base  Slot virtual base address (for PTE manipulation)
 * @return 0 on success, -1 on failure
 */
int vos3_vbus_map_stream_direct(uint8_t slot_id, const uint64_t *phys_pages,
                                 uint32_t num_pages, uintptr_t virt_base);

/**
 * @brief Deactivate zero-copy DMA mode after CRC validation.
 *
 * Restores PRESENT bit on HugePages so the agent can read the loaded data.
 *
 * @return 0 on success, -1 if not in zero-copy mode
 */
int vos3_vbus_unmap_stream_direct(void);

/**
 * @brief Get kernel-writable address for streaming write at given offset.
 *
 * When zero-copy DMA is active, returns VOS3_PHYS_MAP_OFFSET + physical
 * address (bypasses slot VA). Otherwise returns slot VA + offset.
 *
 * @param[in] offset  Byte offset into model region
 * @return Kernel-writable virtual address
 */
void *vos3_vbus_stream_write_addr(size_t offset);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VIRTIO_VBUS_H */
