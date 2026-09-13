/**
 * @file virtio_vbus.c
 * @brief VOS3 VBus — Binary VirtIO-Serial Transport Driver
 *
 * @details Binary frame transport over QEMU virtio-serial-pci.
 *          Replaces hex-encoded UART protocol for ~18,000x throughput.
 *
 *          Uses Legacy VirtIO interface (I/O ports) with split virtqueues.
 *          Poll-driven (no IRQ) — same pattern as virtio_blk.c.
 *
 * @version 1.0.0
 * @date 2026-04-01
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1 — Binary Bridge
 */

#include "../../include/vos/virtio_vbus.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/ai_guard.h"     /* Phase 4.3: VOS3_PTE_AI_GUARD_PAGE */
#include "../../include/vos/sha256.h"       /* Phase 5: HMAC-SHA256 frame auth */
#include "../../include/vos/crc32c.h"       /* Shared CRC32C (Castagnoli) */
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * STATIC DMA BUFFERS (BSS — KBASE identity-mapped range)
 * ============================================================================ */

/**
 * VirtIO Legacy queue layout for 256 entries:
 *   Descriptors:  256 * 16 = 4096 bytes
 *   Available:    6 + 256*2 = 518  bytes
 *   [pad to 4K]
 *   Used:         6 + 256*8 = 2054 bytes
 *   Total:        ~12 KiB (3 pages)
 */
#define VBUS_QUEUE_MEM_SIZE  (3 * 4096)

static uint8_t g_vbus_rx_queue_mem[VBUS_QUEUE_MEM_SIZE]
    __attribute__((aligned(4096)));

static uint8_t g_vbus_tx_queue_mem[VBUS_QUEUE_MEM_SIZE]
    __attribute__((aligned(4096)));

/**
 * Phase 4.2.19: Guard Page Implementation
 * Each DMA buffer is extended by one page (4096 bytes). At init time,
 * the trailing page is unmapped (PRESENT=0) to catch speculative overflow.
 * Page-aligned (4096) to ensure guard sits on a clean page boundary.
 */

/** @brief RX bounce buffer (64KB usable + 4KB guard page) */
static uint8_t g_vbus_rx_bounce[65536 + 4096] __attribute__((aligned(4096)));

/** @brief Debug flag: set to 1 when streaming starts, for recv_frame diagnostics */
static int g_rx_debug_streaming = 0;

/**
 * @brief Frame reassembly ring buffer (4MB usable + 4KB guard page).
 *
 * Task 2.3: Replaced linear memmove buffer with ring buffer.
 * Uses head/tail pointers to avoid O(n) memmove on every frame consume.
 * The buffer is power-of-2 sized for fast modular arithmetic.
 *
 * Phase 4.3: Expanded from 128KB to 4MB to accommodate 2MB Jumbo Frames.
 * QEMU virtio-serial delivers socket data in 4096-byte chunks, not as
 * complete application-level frames.  We accumulate raw bytes here until
 * a full frame header + payload is available for parsing.
 */
#define VBUS_RX_RING_SIZE   4194304U  /* 4MB, must be power-of-2 */
#define VBUS_RX_RING_MASK   (VBUS_RX_RING_SIZE - 1U)
static uint8_t g_vbus_rx_reassembly[VBUS_RX_RING_SIZE + 4096] __attribute__((aligned(4096)));
static uint32_t g_vbus_rx_ring_head = 0;  /**< Consumer read index */
static uint32_t g_vbus_rx_ring_tail = 0;  /**< Producer write index */

/** @brief Helper: number of valid bytes in the ring buffer */
static inline uint32_t vbus_rx_ring_used(void)
{
    return g_vbus_rx_ring_tail - g_vbus_rx_ring_head;
}

/** @brief Helper: read a byte at offset from ring head without consuming */
static inline uint8_t vbus_rx_ring_peek(uint32_t offset)
{
    return g_vbus_rx_reassembly[(g_vbus_rx_ring_head + offset) & VBUS_RX_RING_MASK];
}

/** @brief Helper: read contiguous bytes from ring into linear buffer */
static inline void vbus_rx_ring_read(uint32_t offset, uint8_t *dst, uint32_t len)
{
    for (uint32_t i = 0; i < len; i++) {
        dst[i] = g_vbus_rx_reassembly[(g_vbus_rx_ring_head + offset + i) & VBUS_RX_RING_MASK];
    }
}

/** @brief Helper: advance head by n bytes (consume) */
static inline void vbus_rx_ring_consume(uint32_t n)
{
    g_vbus_rx_ring_head += n;
}

/** @brief Helper: append bytes to ring tail */
static inline void vbus_rx_ring_append(const uint8_t *src, uint32_t len)
{
    for (uint32_t i = 0; i < len; i++) {
        g_vbus_rx_reassembly[(g_vbus_rx_ring_tail + i) & VBUS_RX_RING_MASK] = src[i];
    }
    g_vbus_rx_ring_tail += len;
}

/* Backward-compat macro: g_vbus_rx_reassembly_len equivalent */
#define g_vbus_rx_reassembly_len  vbus_rx_ring_used()

/**
 * @brief Staleness counter for partial frames in the reassembly buffer.
 *
 * When recv_frame() finds a partial frame (header claims more bytes than
 * available) AND rx_drain_to_reassembly() added zero new bytes, this counter
 * increments.  After VBUS_RX_STALE_THRESHOLD consecutive stale polls, the
 * leading byte is discarded to re-sync the stream.  This handles malformed
 * frames (e.g. header claims 4096-byte payload but only 1024 bytes were sent)
 * that would otherwise block the reassembly buffer indefinitely.
 */
static uint32_t g_vbus_rx_stale_count = 0;
#define VBUS_RX_STALE_THRESHOLD 50

/** @brief TX bounce buffer for synchronous send_frame (64KB usable + 4KB guard) */
static uint8_t g_vbus_tx_bounce[65536 + 4096] __attribute__((aligned(4096)));

/** @brief TX bounce buffer for async send_frame_trylock (fire-and-forget).
 *  Only used for FEEDBACK frames now — ISR events go to event ring. */
static uint8_t g_vbus_tx_bounce_async[1024] __attribute__((aligned(64)));

/* ============================================================================
 * EVENT RING BUFFER — ISR-safe event deferral (VBus v2)
 * ============================================================================ */

static vbus_event_entry_t g_event_ring[VBUS_EVENT_RING_SIZE];
static volatile uint32_t  g_event_ring_head = 0;
static volatile uint32_t  g_event_ring_tail = 0;

/** @brief Global VBus device state */
static vbus_device_t g_vbus_dev;

/* ============================================================================
 * HMAC-SHA256 FRAME AUTHENTICATION STATE (Phase 5 — Tier 2 Hardening)
 * ============================================================================ */

/** @brief Shared HMAC key (set during HANDSHAKE, 32 bytes) */
static uint8_t  g_vbus_hmac_key[VBUS_HMAC_KEY_SIZE];

/** @brief 1 if HMAC authentication is active (key exchanged) */
static int      g_vbus_hmac_enabled = 0;

/** @brief Phase 10 Omega: HMAC violation counter for observability.
 *  Incremented on every frame that fails HMAC authentication.
 *  Logged but not enforced (no ban mechanism — see design rationale below).
 *
 *  Design rationale: The "10 violations in 60s triggers 60s ban" mechanism
 *  previously documented was never implemented. For a local Unix socket
 *  transport where the only client is the trusted desktop process, a ban
 *  mechanism adds complexity without security benefit. If the HMAC key is
 *  compromised, the attacker can reconnect, making bans ineffective.
 */
static uint64_t g_vbus_hmac_violation_count = 0;

/* Constant-time comparison: vos3_ct_equal() from sha256.h / crypto_helpers.c */

void vos3_vbus_set_hmac_key(const uint8_t *key, size_t len)
{
    if (len > VBUS_HMAC_KEY_SIZE) len = VBUS_HMAC_KEY_SIZE;
    memcpy(g_vbus_hmac_key, key, len);
    if (len < VBUS_HMAC_KEY_SIZE)
        memset(g_vbus_hmac_key + len, 0, VBUS_HMAC_KEY_SIZE - len);
    g_vbus_hmac_enabled = 1;
    VOS3_INFO("[VBUS] HMAC-SHA256 frame authentication ENABLED");
}

/**
 * @brief Query whether HMAC frame authentication is active.
 * v23.14 (D-CRIT1): Used by handshake handler to reject downgrades.
 */
int vos3_vbus_hmac_is_enabled(void)
{
    return g_vbus_hmac_enabled;
}

/* ============================================================================
 * Phase 6.4.1-U: CHAINED-HMAC TOKEN PROVENANCE
 *
 * Each TOKEN_STREAM frame carries a 32-byte chain MAC:
 *   chain_mac[n] = HMAC_SHA256(session_key, payload || chain_mac[n-1])
 * where chain_mac[0] = all-zeros (chain genesis).
 *
 * Injecting a ghost token at position K breaks the chain for ALL subsequent
 * tokens (K+1, K+2, ...) because the receiver's running chain diverges.
 * ============================================================================ */

/** @brief Per-slot previous chain MAC (32 bytes × 8 slots) */
static uint8_t g_token_chain_mac[8][VOS3_SHA256_DIGEST_SIZE];

/**
 * @brief Compute chained token provenance MAC.
 *
 * Feeds HMAC(session_key, payload || prev_chain_mac[slot]) and stores
 * the result as the new chain state. Caller appends out_mac to the frame.
 *
 * @param slot_id   Model slot (0-7)
 * @param payload   Token payload bytes (header + text + NUL)
 * @param payload_len Payload length in bytes
 * @param out_mac   Output: 32-byte chained MAC
 */
void vos3_vbus_chain_token_mac(uint8_t slot_id,
                                const uint8_t *payload, uint32_t payload_len,
                                uint8_t out_mac[32])
{
    if (slot_id >= 8U || !g_vbus_hmac_enabled) {
        /* No chaining without HMAC session key — zero MAC (passthrough) */
        for (int i = 0; i < 32; i++)
            out_mac[i] = 0;
        return;
    }

    vos3_hmac_ctx_t ctx;
    vos3_hmac_sha256_init(&ctx, g_vbus_hmac_key, VBUS_HMAC_KEY_SIZE);
    vos3_hmac_sha256_update(&ctx, payload, payload_len);
    vos3_hmac_sha256_update(&ctx, g_token_chain_mac[slot_id],
                            VOS3_SHA256_DIGEST_SIZE);
    vos3_hmac_sha256_final(&ctx, out_mac);

    /* Advance chain state for this slot */
    for (int i = 0; i < (int)VOS3_SHA256_DIGEST_SIZE; i++)
        g_token_chain_mac[slot_id][i] = out_mac[i];
}

/**
 * @brief Reset token chain MAC for a slot (call on SLOT_RESET or SLOT_START).
 */
void vos3_vbus_reset_token_chain(uint8_t slot_id)
{
    if (slot_id < 8U) {
        for (int i = 0; i < (int)VOS3_SHA256_DIGEST_SIZE; i++)
            g_token_chain_mac[slot_id][i] = 0;
    }
}

/* ============================================================================
 * CRC32C — Delegates to shared kernel/src/core/crc32c.c
 * Phase 4.2.18: Switched from IEEE 802.3 to CRC32C for hardware acceleration.
 * ============================================================================ */

uint32_t vbus_crc32(const void *data, size_t len)
{
    return crc32c(data, len);
}

/* ============================================================================
 * FRAME HELPERS
 * ============================================================================ */

/**
 * @brief Build 64-byte frame header into buffer (VBus v3 unified)
 *
 * Layout (v3.0 — C/Rust aligned):
 *   [u16:magic 0x5642 LE][u8:version 0x03][u8:frame_type]
 *   [u16:tag LE][u16:payload_len LE]
 *   [u32:hdr_crc LE][u32:payload_crc LE]
 *   [u8:mac[32]][u8:flags][u8:reserved[15]]
 *
 * hdr_crc:     CRC32C over header[0:8] (magic+version+type+tag+len)
 * payload_crc: CRC32C(payload, seed=hdr_crc) — seeded chaining
 *
 * Note: slot_id is NOT in the wire header (Rust has no slot_id field).
 * The slot_id parameter is accepted for API compatibility but not encoded
 * in the header. Callers that need slot routing encode it in the payload.
 */
static void build_frame_header(uint8_t *buf, uint8_t type, uint8_t slot_id,
                                uint16_t tag, const void *payload, uint32_t len)
{
    (void)slot_id;  /* Not encoded in v3 wire header */

    /* [0:2] magic (little-endian 0x5642 = "VB") */
    buf[0] = (uint8_t)(VBUS_MAGIC & 0xFF);        /* 0x42 'B' */
    buf[1] = (uint8_t)((VBUS_MAGIC >> 8) & 0xFF); /* 0x56 'V' */
    /* [2] version */
    buf[2] = (uint8_t)VBUS_VERSION;
    /* [3] frame_type */
    buf[3] = type;
    /* [4:6] tag (little-endian 16-bit) */
    buf[4] = (uint8_t)(tag & 0xFF);
    buf[5] = (uint8_t)((tag >> 8) & 0xFF);
    /* [6:8] payload_len (little-endian 16-bit) */
    uint16_t plen = (uint16_t)(len & 0xFFFFU);
    buf[6] = (uint8_t)(plen & 0xFF);
    buf[7] = (uint8_t)((plen >> 8) & 0xFF);

    /* hdr_crc: CRC32C over header[0:8] */
    uint32_t hdr_crc = vbus_crc32(buf, 8);

    /* [8:12] hdr_crc (little-endian) */
    buf[8]  = (uint8_t)(hdr_crc & 0xFF);
    buf[9]  = (uint8_t)((hdr_crc >> 8) & 0xFF);
    buf[10] = (uint8_t)((hdr_crc >> 16) & 0xFF);
    buf[11] = (uint8_t)((hdr_crc >> 24) & 0xFF);

    /* payload_crc: CRC32C(payload, seed=hdr_crc) — seeded chaining
     * This matches Rust's crc32c_with_seed(payload, hdr_crc) exactly. */
    uint32_t payload_crc;
    {
        /* Re-invert hdr_crc to get the running state (undo crc32c_finish XOR) */
        uint32_t seed_state = hdr_crc ^ 0xFFFFFFFFU;
        uint32_t state = crc32c_update(seed_state, payload, len);
        payload_crc = crc32c_finish(state);
    }

    /* [12:16] payload_crc (little-endian) */
    buf[12] = (uint8_t)(payload_crc & 0xFF);
    buf[13] = (uint8_t)((payload_crc >> 8) & 0xFF);
    buf[14] = (uint8_t)((payload_crc >> 16) & 0xFF);
    buf[15] = (uint8_t)((payload_crc >> 24) & 0xFF);

    /* Zero-fill mac + flags + reserved (bytes 16-63) */
    memset(buf + 16, 0, 48);

    /* HMAC-SHA256 frame authentication */
    if (g_vbus_hmac_enabled) {
        /* HMAC covers header[0:16] (prefix + CRCs) + full payload */
        vos3_hmac_ctx_t hmac_ctx;
        vos3_hmac_sha256_init(&hmac_ctx, g_vbus_hmac_key, VBUS_HMAC_KEY_SIZE);
        vos3_hmac_sha256_update(&hmac_ctx, buf, 16);
        if (len > 0 && payload != NULL) {
            vos3_hmac_sha256_update(&hmac_ctx, payload, len);
        }
        vos3_hmac_sha256_final(&hmac_ctx, buf + VBUS_HMAC_OFFSET);
        buf[VBUS_FLAGS_OFFSET] = VBUS_FLAG_HMAC;
    }
}

/**
 * @brief Parse 64-byte frame header from buffer (VBus v3 unified)
 *
 * Validates magic/version, then hdr_crc (fast reject), then payload_crc,
 * then optional HMAC-SHA256 authentication.
 *
 * v3 wire layout:
 *   [0:2] magic 0x5642 LE
 *   [2]   version 0x03
 *   [3]   frame_type
 *   [4:6] tag LE
 *   [6:8] payload_len LE (u16)
 *   [8:12]  hdr_crc LE
 *   [12:16] payload_crc LE
 *   [16:48] mac
 *   [48]    flags
 *   [49:64] reserved
 *
 * @return 0 on success, -1 if insufficient data or validation fail, -3 HMAC fail
 */
static int parse_frame_header(const uint8_t *buf, uint32_t total_len,
                               uint8_t *type_out, uint8_t *slot_id_out,
                               uint16_t *tag_out, uint32_t *payload_len_out)
{
    if (total_len < VBUS_FRAME_HDR_SIZE) {
        return -1;
    }

    /* [0:2] Validate magic */
    uint16_t magic = (uint16_t)buf[0] | ((uint16_t)buf[1] << 8);
    if (magic != VBUS_MAGIC) {
        return -1;  /* Not a valid VBus frame */
    }

    /* [2] Validate version */
    if (buf[2] != VBUS_VERSION) {
        return -1;
    }

    /* [3] frame_type */
    *type_out = buf[3];
    /* slot_id is not in v3 wire header — set to 0xFF */
    *slot_id_out = 0xFF;
    /* [4:6] tag */
    *tag_out = (uint16_t)buf[4] | ((uint16_t)buf[5] << 8);
    /* [6:8] payload_len (u16) */
    *payload_len_out = (uint32_t)((uint16_t)buf[6] | ((uint16_t)buf[7] << 8));

    /* [8:12] Extract stored hdr_crc */
    uint32_t stored_hdr_crc = (uint32_t)buf[8]
                            | ((uint32_t)buf[9] << 8)
                            | ((uint32_t)buf[10] << 16)
                            | ((uint32_t)buf[11] << 24);

    /* Validate hdr_crc over header[0:8] (magic+version+type+tag+len) */
    uint32_t hdr_crc = vbus_crc32(buf, 8);

    if (hdr_crc != stored_hdr_crc) {
        return -1;
    }

    /* Verify payload fits in available data */
    if (VBUS_FRAME_HDR_SIZE + *payload_len_out > total_len) {
        return -1;
    }

    /* [12:16] Extract stored payload_crc */
    uint32_t stored_payload_crc = (uint32_t)buf[12]
                                | ((uint32_t)buf[13] << 8)
                                | ((uint32_t)buf[14] << 16)
                                | ((uint32_t)buf[15] << 24);

    /* Validate payload_crc: CRC32C(payload, seed=hdr_crc) — seeded chaining */
    const uint8_t *payload = buf + VBUS_FRAME_HDR_SIZE;
    uint32_t payload_crc;
    {
        uint32_t seed_state = hdr_crc ^ 0xFFFFFFFFU;  /* undo finalize XOR */
        uint32_t state = crc32c_update(seed_state, payload, *payload_len_out);
        payload_crc = crc32c_finish(state);
    }

    if (payload_crc != stored_payload_crc) {
        return -1;
    }

    /* HMAC-SHA256 authentication check */
    if (g_vbus_hmac_enabled) {
        uint8_t flags = buf[VBUS_FLAGS_OFFSET];
        if (!(flags & VBUS_FLAG_HMAC)) {
            __atomic_fetch_add(&g_vbus_hmac_violation_count, 1, __ATOMIC_RELAXED);
            VOS3_WARN("[VBUS] HMAC violation #%llu: flag not set on incoming frame",
                      (unsigned long long)g_vbus_hmac_violation_count);
            return -3;  /* HMAC required but flag not set */
        }
        uint8_t expected_hmac[VBUS_HMAC_SIZE];
        vos3_hmac_ctx_t hmac_ctx;
        vos3_hmac_sha256_init(&hmac_ctx, g_vbus_hmac_key, VBUS_HMAC_KEY_SIZE);
        vos3_hmac_sha256_update(&hmac_ctx, buf, 16);
        if (*payload_len_out > 0) {
            vos3_hmac_sha256_update(&hmac_ctx, payload, *payload_len_out);
        }
        vos3_hmac_sha256_final(&hmac_ctx, expected_hmac);
        if (!vos3_ct_equal(buf + VBUS_HMAC_OFFSET, expected_hmac, VBUS_HMAC_SIZE)) {
            __atomic_fetch_add(&g_vbus_hmac_violation_count, 1, __ATOMIC_RELAXED);
            VOS3_WARN("[VBUS] HMAC violation #%llu: MAC mismatch on incoming frame",
                      (unsigned long long)g_vbus_hmac_violation_count);
            return -3;  /* HMAC mismatch */
        }
    }

    return 0;
}

/* ============================================================================
 * RX DESCRIPTOR MANAGEMENT
 * ============================================================================ */

/**
 * @brief Pre-populate RX queue with device-writable descriptors
 *
 * Each descriptor points at a slice of g_vbus_rx_bounce.
 * We post one large descriptor covering the full bounce buffer
 * per available slot. For simplicity in v1, we post a single
 * descriptor pointing at the entire 64KB bounce buffer.
 */
static void rx_populate(void)
{
    virtio_queue_t *vq = &g_vbus_dev.rx_queue;

    /* Post one descriptor for receiving */
    if (vq->num_free == 0) {
        return;
    }

    uint16_t idx = vq->free_head;
    vq->desc[idx].addr = virtio_virt_to_phys(g_vbus_rx_bounce);
    vq->desc[idx].len = sizeof(g_vbus_rx_bounce);
    vq->desc[idx].flags = VIRTQ_DESC_F_WRITE;  /* Device writes here */

    vq->free_head = vq->desc[idx].next;
    vq->num_free--;

    /* Add to available ring */
    uint16_t avail_idx = vq->avail->idx % vq->num_desc;
    vq->avail->ring[avail_idx] = idx;
    virtio_mb();
    vq->avail->idx++;
    virtio_mb();

    /* Notify device */
    virtio_outw(g_vbus_dev.io_base + VIRTIO_PCI_QUEUE_NOTIFY,
                VBUS_RX_QUEUE_IDX);
}

/**
 * @brief Return a consumed RX descriptor back to the available ring
 */
static void rx_repost(uint16_t desc_idx)
{
    virtio_queue_t *vq = &g_vbus_dev.rx_queue;

    /* Reset descriptor */
    vq->desc[desc_idx].addr = virtio_virt_to_phys(g_vbus_rx_bounce);
    vq->desc[desc_idx].len = sizeof(g_vbus_rx_bounce);
    vq->desc[desc_idx].flags = VIRTQ_DESC_F_WRITE;

    /* Add back to available ring */
    uint16_t avail_idx = vq->avail->idx % vq->num_desc;
    vq->avail->ring[avail_idx] = desc_idx;
    virtio_mb();
    vq->avail->idx++;
    virtio_mb();

    /* Notify device */
    virtio_outw(g_vbus_dev.io_base + VIRTIO_PCI_QUEUE_NOTIFY,
                VBUS_RX_QUEUE_IDX);
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

int vos3_vbus_init(void)
{
    if (g_vbus_dev.initialized) {
        return 0;
    }

    VOS3_INFO("[VBUS] Probing for virtio-serial device...");

    /* Phase 4.2.18: Initialize shared CRC32C module (table + SSE4.2 detection) */
    crc32c_init();
    VOS3_INFO("[VBUS] SSE4.2 CRC32C hardware: %s",
              crc32c_has_hw() ? "ENABLED" : "software fallback");

    memset(&g_vbus_dev, 0, sizeof(g_vbus_dev));
    g_vbus_dev.tx_lock = VOS3_SPINLOCK_INIT;
    g_vbus_dev.rx_lock = VOS3_SPINLOCK_INIT;

    /* ===== PCI Discovery ===== */
    uint8_t pci_bus, pci_dev, pci_func;
    uint16_t io_base;
    if (virtio_pci_find_device(VIRTIO_SERIAL_SUBSYSTEM_ID,
                               &pci_bus, &pci_dev, &pci_func, &io_base) != 0) {
        VOS3_DEBUG("[VBUS] No virtio-serial device found");
        return -1;
    }
    g_vbus_dev.io_base = io_base;

    /* Enable PCI bus mastering */
    uint16_t pci_cmd = virtio_pci_read16(pci_bus, pci_dev, pci_func, 0x04);
    pci_cmd |= (1U << 2) | (1U << 0);  /* Bus Master + I/O Space */
    virtio_pci_write16(pci_bus, pci_dev, pci_func, 0x04, pci_cmd);

    /* ===== VirtIO Legacy Handshake ===== */

    /* Reset */
    virtio_outb(io_base + VIRTIO_PCI_STATUS, 0);

    /* ACK */
    virtio_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_ACK);

    /* DRIVER */
    virtio_outb(io_base + VIRTIO_PCI_STATUS,
                VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER);

    /* Feature negotiation — skip MULTIPORT for simplicity */
    uint32_t host_features = virtio_inl(io_base + VIRTIO_PCI_HOST_FEATURES);
    uint32_t guest_features = 0;  /* Accept no optional features */
    virtio_outl(io_base + VIRTIO_PCI_GUEST_FEATURES, guest_features);
    g_vbus_dev.features = guest_features;

    VOS3_DEBUG("[VBUS] Host features: 0x%08x, negotiated: 0x%08x",
               host_features, guest_features);

    /* ===== Initialize RX Queue (index 0) ===== */
    virtio_outw(io_base + VIRTIO_PCI_QUEUE_SEL, VBUS_RX_QUEUE_IDX);
    uint16_t rx_max = virtio_inw(io_base + VIRTIO_PCI_QUEUE_SIZE);
    if (rx_max == 0) {
        VOS3_WARN("[VBUS] RX queue not available");
        virtio_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }
    if (rx_max > VBUS_QUEUE_SIZE) {
        rx_max = VBUS_QUEUE_SIZE;
    }

    if (virtio_queue_init(&g_vbus_dev.rx_queue, VBUS_RX_QUEUE_IDX, rx_max,
                          io_base, g_vbus_rx_queue_mem,
                          sizeof(g_vbus_rx_queue_mem)) != 0) {
        VOS3_ERROR("[VBUS] Failed to init RX queue");
        virtio_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }

    /* ===== Initialize TX Queue (index 1) ===== */
    virtio_outw(io_base + VIRTIO_PCI_QUEUE_SEL, VBUS_TX_QUEUE_IDX);
    uint16_t tx_max = virtio_inw(io_base + VIRTIO_PCI_QUEUE_SIZE);
    if (tx_max == 0) {
        VOS3_WARN("[VBUS] TX queue not available");
        virtio_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }
    if (tx_max > VBUS_QUEUE_SIZE) {
        tx_max = VBUS_QUEUE_SIZE;
    }

    if (virtio_queue_init(&g_vbus_dev.tx_queue, VBUS_TX_QUEUE_IDX, tx_max,
                          io_base, g_vbus_tx_queue_mem,
                          sizeof(g_vbus_tx_queue_mem)) != 0) {
        VOS3_ERROR("[VBUS] Failed to init TX queue");
        virtio_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_FAILED);
        return -1;
    }

    /* ===== Mark Driver OK ===== */
    virtio_outb(io_base + VIRTIO_PCI_STATUS,
                VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER |
                VIRTIO_STATUS_DRIVER_OK);

    /* Pre-populate RX queue with device-writable descriptors */
    rx_populate();

    g_vbus_dev.initialized = 1;
    VOS3_INFO("[VBUS] Initialized: io=0x%04x, RX/TX queues ready", io_base);

    /* Phase 4.2.19: Diamond Shield — poison guard zones after DMA buffers.
     * BSS is mapped via 2MB large pages, so we can't unmap individual 4KB pages.
     * Instead, fill guard zones with a detectable poison pattern (0xFE).
     * The VBus recv/send paths never touch beyond usable buffer sizes.
     * A periodic canary check detects any speculative overflow corruption. */
    {
        memset(g_vbus_rx_bounce + 65536, 0xFE, 4096);
        memset(g_vbus_rx_reassembly + 4194304, 0xFE, 4096);
        memset(g_vbus_tx_bounce + 65536, 0xFE, 4096);

        VOS3_INFO("[VBUS] Guard zones: rx=0x%llx rea=0x%llx tx=0x%llx (poison 0xFE)",
                  (unsigned long long)(uintptr_t)(g_vbus_rx_bounce + 65536),
                  (unsigned long long)(uintptr_t)(g_vbus_rx_reassembly + 4194304),
                  (unsigned long long)(uintptr_t)(g_vbus_tx_bounce + 65536));
    }

    return 0;
}

int vos3_vbus_available(void)
{
    return g_vbus_dev.initialized;
}

/* ============================================================================
 * SEND FRAME
 * ============================================================================ */

int vos3_vbus_send_frame(uint8_t type, uint8_t slot_id, uint16_t tag,
                          const void *payload, uint32_t len)
{
    if (!g_vbus_dev.initialized) {
        return -1;
    }

    /* Phase 4.3: TX limited by bounce buffer (64KB), not jumbo RX max (2MB) */
    if (len > VBUS_MAX_TX_PAYLOAD) {
        VOS3_WARN("[VBUS] TX payload too large: %u > %u", len, VBUS_MAX_TX_PAYLOAD);
        return -1;
    }

    uint32_t frame_len = VBUS_FRAME_HDR_SIZE + len;
    virtio_queue_t *vq = &g_vbus_dev.tx_queue;

    /* v23.5: Build frame directly into DMA bounce buffer under tx_lock.
     * The previous approach used a 65536-byte stack buffer (frame_buf) to
     * build outside the lock, but send_ok() already uses 8KB+ of stack
     * for vbuf — combined 73KB exceeded the 64KB vmap kernel stack.
     *
     * CRC computation + payload memcpy under tx_lock is fast enough;
     * the real bottleneck is QEMU's synchronous virtio notify, which runs
     * after the lock is released anyway. Zero stack pressure now. */

    /* === BEGIN CRITICAL SECTION: tx_lock === */
    vos3_spinlock_lock(&g_vbus_dev.tx_lock);

    /* Build frame header + payload directly into DMA bounce buffer */
    build_frame_header(g_vbus_tx_bounce, type, slot_id, tag, payload, len);
    if (len > 0) {
        memcpy(g_vbus_tx_bounce + VBUS_FRAME_HDR_SIZE, payload, len);
    }

    vos3_spinlock_lock(&vq->lock);

    if (vq->num_free == 0) {
        vos3_spinlock_unlock(&vq->lock);
        vos3_spinlock_unlock(&g_vbus_dev.tx_lock);
        VOS3_WARN("[VBUS] TX queue full");
        return -1;
    }

    /* Allocate one descriptor */
    uint16_t desc_idx = vq->free_head;
    vq->free_head = vq->desc[desc_idx].next;
    vq->num_free--;

    /* Set up descriptor: device-readable */
    vq->desc[desc_idx].addr = virtio_virt_to_phys(g_vbus_tx_bounce);
    vq->desc[desc_idx].len = frame_len;
    vq->desc[desc_idx].flags = 0;  /* Device-readable, no chain */

    /* Add to available ring */
    uint16_t avail_idx = vq->avail->idx % vq->num_desc;
    vq->avail->ring[avail_idx] = desc_idx;
    virtio_mb();
    vq->avail->idx++;
    virtio_mb();

    /* Notify device — QEMU processes this synchronously, so the bounce
     * buffer contents are DMA'd before this PCI write returns. */
    virtio_outw(g_vbus_dev.io_base + VIRTIO_PCI_QUEUE_NOTIFY,
                VBUS_TX_QUEUE_IDX);

    vos3_spinlock_unlock(&vq->lock);

    /* === END CRITICAL SECTION: tx_lock ===
     * The device has already read the bounce buffer data (synchronous PCI
     * notify).  Release tx_lock so other CPUs can submit TX frames while
     * we wait for the used ring entry to appear. */
    vos3_spinlock_unlock(&g_vbus_dev.tx_lock);

    /* Poll for completion — reclaim ALL used descriptors including those
     * from prior send_frame_trylock calls to prevent free-list corruption.
     * Only vq->lock is acquired briefly per iteration (no tx_lock held).
     *
     * Resilience-Matrix F5 — wall-clock deadline.
     * The legacy 100k spin-iteration ceiling is retained as an inner
     * bound (catches busy-loop pathologies on PIT-less platforms), but
     * a 2000 ms wall-clock deadline tied to vos3_timer_get_ticks()
     * forms the outer bound. The first to fire wins.
     *
     * VBUS_TX_DEADLINE_MS is the hard ceiling; tunable by recompile,
     * not by env, because it's a kernel-side correctness gate. */
    extern uint64_t vos3_timer_get_ticks(void);
    /* timer ticks are 1ms units on x86_64 PIT/HPET — see kernel/src/drivers/timer.c. */
    #define VBUS_TX_DEADLINE_MS  2000ULL
    const uint64_t deadline_tick = vos3_timer_get_ticks() + VBUS_TX_DEADLINE_MS;
    int timeout = 100000;
    while (timeout > 0 && vos3_timer_get_ticks() < deadline_tick) {
        for (int y = 0; y < 10; y++) {
            virtio_yield();
        }

        /* Check ISR */
        (void)virtio_inb(g_vbus_dev.io_base + VIRTIO_PCI_ISR);
        virtio_mb();

        vos3_spinlock_lock(&vq->lock);

        int found_ours = 0;
        while (vq->last_used_idx != vq->used->idx) {
            uint16_t uidx = vq->last_used_idx % vq->num_desc;
            uint16_t uid  = (uint16_t)vq->used->ring[uidx].id;
            vq->last_used_idx++;

            /* Return the ACTUAL used descriptor to free list */
            vq->desc[uid].next = vq->free_head;
            vq->free_head = uid;
            vq->num_free++;

            if (uid == desc_idx) {
                found_ours = 1;
            }
        }

        if (found_ours) {
            vos3_spinlock_unlock(&vq->lock);

            g_vbus_dev.stats.tx_frames++;
            g_vbus_dev.stats.tx_bytes += frame_len;
            return 0;
        }

        vos3_spinlock_unlock(&vq->lock);
        timeout--;
    }

    /* Timeout — return descriptor anyway. Distinguish wall-clock vs
     * iter-count exhaustion in the log so a soak run can tell whether
     * the kernel is hitting the new 2 s ceiling. */
    vos3_spinlock_lock(&vq->lock);
    vq->desc[desc_idx].next = vq->free_head;
    vq->free_head = desc_idx;
    vq->num_free++;
    vos3_spinlock_unlock(&vq->lock);

    if (vos3_timer_get_ticks() >= deadline_tick) {
        VOS3_ERROR("[VBUS] TX wall-clock deadline (2000 ms) hit");
    } else {
        VOS3_ERROR("[VBUS] TX iter-count timeout");
    }
    return -1;
}

/* ============================================================================
 * RECEIVE FRAME
 * ============================================================================ */

/**
 * @brief Drain all available descriptors from the used ring into the
 *        reassembly buffer.  Returns the number of bytes appended.
 *
 * Must be called with g_vbus_dev.rx_lock held.
 */
static uint32_t rx_drain_to_reassembly(void)
{
    virtio_queue_t *vq = &g_vbus_dev.rx_queue;
    uint32_t total_appended = 0;

    vos3_spinlock_lock(&vq->lock);

    /* Phase 4.3: Anti-Stall Loop — during Jumbo Frame bursts, QEMU may post
     * new descriptors between our last read and exit. The do-while with a
     * memory barrier catches late arrivals without waiting for the next poll. */
    do {
        /* Acknowledge any pending interrupt */
        (void)virtio_inb(g_vbus_dev.io_base + VIRTIO_PCI_ISR);
        virtio_mb();

        while (vq->last_used_idx != vq->used->idx) {
            uint16_t used_slot = vq->last_used_idx % vq->num_desc;
            uint32_t desc_id   = vq->used->ring[used_slot].id;
            uint32_t written   = vq->used->ring[used_slot].len;
            vq->last_used_idx++;

            /* Append bounce buffer contents to reassembly ring buffer */
            if (written > 0 &&
                vbus_rx_ring_used() + written <= VBUS_RX_RING_SIZE) {
                vbus_rx_ring_append(g_vbus_rx_bounce, written);
                total_appended += written;
            }

            /* Re-post descriptor immediately so QEMU can deliver more data */
            rx_repost((uint16_t)desc_id);
        }

        virtio_mb();  /* Barrier before re-checking for late arrivals */
    } while (vq->last_used_idx != vq->used->idx);

    vos3_spinlock_unlock(&vq->lock);
    return total_appended;
}

int vos3_vbus_recv_frame(uint8_t *type_out, uint8_t *slot_id_out,
                          uint16_t *tag_out, void *payload_buf,
                          uint32_t max_len, uint32_t *len_out)
{
    if (!g_vbus_dev.initialized) {
        return -1;
    }

    vos3_spinlock_lock(&g_vbus_dev.rx_lock);

    /* Step 1: Drain any new descriptor data into reassembly buffer */
    uint32_t drained = rx_drain_to_reassembly();

    /* Reset stale counter whenever we got new data */
    if (drained > 0) {
        g_vbus_rx_stale_count = 0;
    }

    /* Step 2: Try to extract a complete frame from the reassembly ring buffer.
     * Task 2.3: Ring buffer replaces linear memmove — O(1) consume. */
    uint32_t avail = vbus_rx_ring_used();
    if (avail < VBUS_FRAME_HDR_SIZE) {
        vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
        return -1;  /* Not enough data for a header */
    }

    /* v3: Quick-validate magic at ring offset 0-1 before trusting rest */
    uint16_t peek_magic = (uint16_t)vbus_rx_ring_peek(0) |
                          ((uint16_t)vbus_rx_ring_peek(1) << 8);
    if (peek_magic != VBUS_MAGIC) {
        /* Not a valid frame start — discard one byte and re-sync */
        vbus_rx_ring_consume(1);
        g_vbus_dev.stats.crc_errors++;
        g_vbus_rx_stale_count = 0;
        vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
        return -2;
    }

    /* Quick-extract payload_len from bytes 6-7 (u16 LE in v3) */
    uint32_t payload_len = (uint32_t)((uint16_t)vbus_rx_ring_peek(6)
                         | ((uint16_t)vbus_rx_ring_peek(7) << 8));

    /* Fast hdr_crc check before trusting payload_len.
     * Linearize the 8-byte prefix + stored hdr_crc (4 bytes at offset 8). */
    uint8_t hdr_linear[16];
    vbus_rx_ring_read(0, hdr_linear, 16);

    uint32_t stored_hdr_crc = (uint32_t)hdr_linear[8]
                            | ((uint32_t)hdr_linear[9] << 8)
                            | ((uint32_t)hdr_linear[10] << 16)
                            | ((uint32_t)hdr_linear[11] << 24);
    /* Hardware-accelerated CRC32C check over header[0:8] */
    uint32_t hdr_crc_check = vbus_crc32(hdr_linear, 8);
    if (hdr_crc_check != stored_hdr_crc) {
        /* Header CRC mismatch — discard one byte and re-sync */
        vbus_rx_ring_consume(1);
        g_vbus_dev.stats.crc_errors++;
        g_vbus_rx_stale_count = 0;
        vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
        return -2;
    }

    uint32_t frame_total = VBUS_FRAME_HDR_SIZE + payload_len;

    if (payload_len > VBUS_MAX_PAYLOAD || frame_total > VBUS_RX_RING_SIZE) {
        /* Corrupt data — discard one byte and let caller retry */
        vbus_rx_ring_consume(1);
        g_vbus_dev.stats.crc_errors++;
        g_vbus_rx_stale_count = 0;
        vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
        return -2;
    }

    if (avail < frame_total) {
        /* Partial frame — need more data */
        if (drained == 0) {
            g_vbus_rx_stale_count++;
            if (g_vbus_rx_stale_count >= VBUS_RX_STALE_THRESHOLD) {
                vbus_rx_ring_consume(1);
                g_vbus_dev.stats.crc_errors++;
                g_vbus_rx_stale_count = 0;
                vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
                return -2;
            }
        }
        vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
        return -1;
    }

    /* Step 3: Linearize the full frame for dual-CRC validation.
     * Task 2.3: We linearize only once here instead of memmove on every consume.
     * v23.6: Replaced stack-allocated frame_stack[65536] with static buffer.
     * The old 65KB stack allocation consumed the entire 64KB vmap kernel stack,
     * risking overflow when recv_frame is called from bridge_loop (which has
     * its own locals). Static is safe because rx_lock serializes all callers.
     * For jumbo frames (>64KB), we fall back to the RX bounce buffer. */
    g_vbus_rx_stale_count = 0;  /* Have full frame — not stale */
    uint8_t *frame_linear;
    static uint8_t g_vbus_rx_frame[65536];  /* Static linearization buffer */
    if (frame_total <= sizeof(g_vbus_rx_frame)) {
        frame_linear = g_vbus_rx_frame;
    } else {
        /* Jumbo frame: use RX bounce buffer (safe because we hold rx_lock) */
        frame_linear = g_vbus_rx_bounce;
    }
    vbus_rx_ring_read(0, frame_linear, frame_total);

    uint8_t frame_type;
    uint8_t frame_slot_id;
    uint16_t frame_tag;
    int rc = parse_frame_header(frame_linear, frame_total,
                                 &frame_type, &frame_slot_id, &frame_tag,
                                 &payload_len);
    if (rc != 0) {
        /* v23.14 (D-HIGH1): Separate HMAC error (-3) from CRC error (-2) */
        if (rc == -3) {
            g_vbus_dev.stats.crc_errors++;  /* reuse counter for frame errors */
            VOS3_WARN("[VBUS] RX HMAC auth failure (frame %u bytes)", frame_total);
            vbus_rx_ring_consume(frame_total);
            vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
            return -3;  /* propagate HMAC error distinctly */
        }
        /* CRC failed — discard this frame's worth of data */
        g_vbus_dev.stats.crc_errors++;
        VOS3_WARN("[VBUS] RX CRC error (frame %u bytes)", frame_total);
        vbus_rx_ring_consume(frame_total);
        vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
        return -2;
    }

    /* Step 4: Copy payload to caller and return tag/slot_id */
    *type_out = frame_type;
    *slot_id_out = frame_slot_id;
    *tag_out = frame_tag;
    uint32_t copy_len = payload_len;
    if (copy_len > max_len) {
        copy_len = max_len;
    }
    if (copy_len > 0) {
        memcpy(payload_buf, frame_linear + VBUS_FRAME_HDR_SIZE, copy_len);
    }
    *len_out = copy_len;

    /* Step 5: Consume frame from ring buffer — O(1), no memmove */
    vbus_rx_ring_consume(frame_total);

    g_vbus_dev.stats.rx_frames++;
    g_vbus_dev.stats.rx_bytes += frame_total;

    /* Debug: log first few frames received during streaming */
    if (g_rx_debug_streaming) {
        static uint32_t rx_ok_dbg = 0;
        if (rx_ok_dbg < 5) {
            VOS3_INFO("[VBUS] RX OK: type=0x%02x slot=%u tag=0x%04x len=%u",
                      (unsigned)frame_type, (unsigned)frame_slot_id,
                      (unsigned)frame_tag, (unsigned)payload_len);
            rx_ok_dbg++;
        }
    }

    vos3_spinlock_unlock(&g_vbus_dev.rx_lock);
    return 0;
}

/* ============================================================================
 * POLL
 * ============================================================================ */

void vos3_vbus_poll(void)
{
    /* Non-blocking check — just acknowledges ISR to clear interrupt line */
    if (g_vbus_dev.initialized) {
        (void)virtio_inb(g_vbus_dev.io_base + VIRTIO_PCI_ISR);
    }
}

/* ============================================================================
 * Phase 4.2: STREAMING MODEL RX (Bounce-Buffer + Memcpy)
 *
 * Uses the normal bounce buffer for all frame types during streaming.
 * DATA frames: payload is copied from bounce buffer to HugePage memory.
 * CMD frames:  handled directly from bounce buffer (no model corruption).
 *
 * This approach is compatible with QEMU's virtconsole single-descriptor
 * model and eliminates CMD/DATA interleaving issues.
 * ============================================================================ */

/** @brief Streaming state */
static int       g_stream_mode       = 0;
static size_t    g_stream_chunk_size = 0;
static size_t    g_stream_total_size = 0;
static size_t    g_stream_offset     = 0;

/** @brief HugePage virtual base (set by caller for memcpy target) */
static uintptr_t g_stream_virt_base  = 0;

/** @brief Phase 4.3: Zero-copy DMA state */
static int       g_stream_zero_copy  = 0;
static const uint64_t *g_stream_phys_pages = NULL;
static uint32_t  g_stream_num_pages  = 0;
static uint8_t   g_stream_slot_id    = 0xFF;

int vos3_vbus_start_streaming(const uint64_t *phys_pages, uint32_t num_pages,
                               size_t chunk_size, size_t total_size,
                               uintptr_t virt_base)
{
    if (!g_vbus_dev.initialized) {
        return -1;
    }

    g_stream_chunk_size  = chunk_size;
    g_stream_total_size  = total_size;
    g_stream_offset      = 0;
    g_stream_virt_base   = virt_base;
    g_stream_phys_pages  = phys_pages;   /* Phase 4.3: store for zero-copy DMA */
    g_stream_num_pages   = num_pages;
    g_stream_mode        = 1;
    g_rx_debug_streaming = 1;

    VOS3_INFO("[VBUS] Streaming started: total=%lu chunk=%lu",
              (unsigned long)total_size, (unsigned long)chunk_size);

    return 0;
}

void vos3_vbus_stream_advance(size_t bytes)
{
    g_stream_offset += bytes;
}

void vos3_vbus_rewind_streaming(size_t new_offset)
{
    g_stream_offset = new_offset;
    VOS3_INFO("[VBUS] Stream rewind to offset %lu", (unsigned long)new_offset);
}

void vos3_vbus_stop_streaming(void)
{
    if (!g_stream_mode) {
        return;
    }

    /* Phase 4.3: Release zero-copy DMA if still active */
    if (g_stream_zero_copy) {
        vos3_vbus_unmap_stream_direct();
    }

    g_stream_mode       = 0;
    g_stream_phys_pages = NULL;
    g_stream_num_pages  = 0;
    VOS3_INFO("[VBUS] Streaming stopped at offset %lu / %lu",
              (unsigned long)g_stream_offset, (unsigned long)g_stream_total_size);
}

int vos3_vbus_is_streaming(void)
{
    return g_stream_mode;
}

size_t vos3_vbus_streaming_offset(void)
{
    return g_stream_offset;
}

uintptr_t vos3_vbus_streaming_virt_base(void)
{
    return g_stream_virt_base;
}

/* ============================================================================
 * Phase 4.3: Zero-Copy DMA with PTE Safety
 * ============================================================================ */

int vos3_vbus_map_stream_direct(uint8_t slot_id, const uint64_t *phys_pages,
                                 uint32_t num_pages, uintptr_t virt_base)
{
    if (!g_stream_mode || phys_pages == NULL || num_pages == 0) {
        return -1;
    }

    /* Protect HugePages: clear PRESENT, set AI_GUARD_PAGE (bit 11).
     * Iterate at 2MB granularity since these are HugePages (PDE entries). */
    for (uint32_t i = 0; i < num_pages; i++) {
        uintptr_t vaddr = virt_base + (uintptr_t)i * 0x200000ULL;  /* 2MB */
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            vos3_pte_t desired = pte;
            desired &= ~VOS3_PTE_PRESENT;
            desired |= VOS3_PTE_AI_GUARD_PAGE;
            /* K-C5: Atomic CAS */
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired &= ~VOS3_PTE_PRESENT;
                desired |= VOS3_PTE_AI_GUARD_PAGE;
            }
            vos3_vmm_invlpg(vaddr);
        }
    }
    __asm__ volatile("sfence" ::: "memory");

    g_stream_zero_copy = 1;
    g_stream_slot_id   = slot_id;

    VOS3_INFO("[VBUS] Zero-copy DMA active: slot=%u pages=%u base=0x%lx",
              slot_id, num_pages, (unsigned long)virt_base);
    return 0;
}

int vos3_vbus_unmap_stream_direct(void)
{
    if (!g_stream_zero_copy) {
        return -1;
    }

    /* Phase 4.3: Atomic Cache Coherence — full memory fence + cache flush
     * before restoring PRESENT. Ensures all DMA writes are globally visible
     * and cache-coherent before the agent can read the data. */
    __asm__ volatile("mfence" ::: "memory");

    /* Flush cache lines for each HugePage's first 64KB (hot region) */
    for (uint32_t i = 0; i < g_stream_num_pages; i++) {
        if (g_stream_phys_pages == NULL) break;
        uintptr_t kva = VOS3_PHYS_MAP_OFFSET + g_stream_phys_pages[i];
        uintptr_t flush_end = kva + 65536;  /* First 64KB per page */
        for (uintptr_t a = kva; a < flush_end; a += 64) {
            __asm__ volatile("clflushopt (%0)" :: "r"(a) : "memory");
        }
    }
    __asm__ volatile("sfence" ::: "memory");

    /* Unprotect HugePages: restore PRESENT, clear AI_GUARD_PAGE */
    for (uint32_t i = 0; i < g_stream_num_pages; i++) {
        uintptr_t vaddr = g_stream_virt_base + (uintptr_t)i * 0x200000ULL;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            vos3_pte_t desired = pte;
            desired |= VOS3_PTE_PRESENT;
            desired &= ~VOS3_PTE_AI_GUARD_PAGE;
            /* K-C5: Atomic CAS */
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired |= VOS3_PTE_PRESENT;
                desired &= ~VOS3_PTE_AI_GUARD_PAGE;
            }
            vos3_vmm_invlpg(vaddr);
        }
    }
    __asm__ volatile("sfence" ::: "memory");

    VOS3_INFO("[VBUS] Zero-copy DMA released: slot=%u", g_stream_slot_id);
    g_stream_zero_copy = 0;
    g_stream_slot_id   = 0xFF;
    return 0;
}

void *vos3_vbus_stream_write_addr(size_t offset)
{
    if (g_stream_zero_copy && g_stream_phys_pages != NULL) {
        /* Compute kernel VA via physical identity map (bypasses slot PTE) */
        size_t page_idx = offset >> 21;   /* offset / 2MB */
        size_t page_off = offset & 0x1FFFFFULL;  /* offset % 2MB */
        if (page_idx < g_stream_num_pages) {
            return (void *)(VOS3_PHYS_MAP_OFFSET +
                            g_stream_phys_pages[page_idx] + page_off);
        }
    }
    /* Fallback: slot virtual address (non-zero-copy path) */
    return (void *)(g_stream_virt_base + offset);
}

void vos3_vbus_reset_rx(void)
{
    g_vbus_rx_ring_head = 0;
    g_vbus_rx_ring_tail = 0;
    VOS3_INFO("[VBUS] RX ring buffer flushed (client reconnect)");
}

void vos3_vbus_hardware_reset(void)
{
    if (!g_vbus_dev.initialized) return;

    uint16_t io_base = g_vbus_dev.io_base;

    /* Phase 4.2.11: Full VirtIO device reset — clears all DMA state.
     * Prevents "Ghost in the VBus" stale descriptor attacks on reconnect. */

    /* Step 1: Reset device (Status = 0) */
    virtio_outb(io_base + VIRTIO_PCI_STATUS, 0);

    /* Step 2: ACKNOWLEDGE — guest has found the device */
    virtio_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_ACK);

    /* Step 3: DRIVER — guest knows how to drive it */
    virtio_outb(io_base + VIRTIO_PCI_STATUS,
                VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER);

    /* Step 4: Re-negotiate features (accept none, as before) */
    virtio_outl(io_base + VIRTIO_PCI_GUEST_FEATURES, 0);

    /* Phase 4.2.13: Active Ring Purge — zero avail/used ring memory BEFORE
     * queue_init to prevent stale descriptors from the previous session.
     * virtio_queue_init() does memset(dma_mem, 0, total_size) but we also
     * need a memory fence to ensure QEMU sees the cleared rings before
     * we re-register the queue physical address. */
    memset(g_vbus_rx_queue_mem, 0, sizeof(g_vbus_rx_queue_mem));
    memset(g_vbus_tx_queue_mem, 0, sizeof(g_vbus_tx_queue_mem));
    __asm__ volatile("mfence" ::: "memory");

    /* Step 5: Re-initialize RX queue */
    virtio_outw(io_base + VIRTIO_PCI_QUEUE_SEL, VBUS_RX_QUEUE_IDX);
    uint16_t rx_max = virtio_inw(io_base + VIRTIO_PCI_QUEUE_SIZE);
    if (rx_max > VBUS_QUEUE_SIZE) rx_max = VBUS_QUEUE_SIZE;
    virtio_queue_init(&g_vbus_dev.rx_queue, VBUS_RX_QUEUE_IDX, rx_max,
                      io_base, g_vbus_rx_queue_mem, sizeof(g_vbus_rx_queue_mem));
    g_vbus_dev.rx_queue.last_used_idx = 0;

    /* Step 6: Re-initialize TX queue */
    virtio_outw(io_base + VIRTIO_PCI_QUEUE_SEL, VBUS_TX_QUEUE_IDX);
    uint16_t tx_max = virtio_inw(io_base + VIRTIO_PCI_QUEUE_SIZE);
    if (tx_max > VBUS_QUEUE_SIZE) tx_max = VBUS_QUEUE_SIZE;
    virtio_queue_init(&g_vbus_dev.tx_queue, VBUS_TX_QUEUE_IDX, tx_max,
                      io_base, g_vbus_tx_queue_mem, sizeof(g_vbus_tx_queue_mem));
    g_vbus_dev.tx_queue.last_used_idx = 0;

    /* Step 7: DRIVER_OK — device is live again */
    virtio_outb(io_base + VIRTIO_PCI_STATUS,
                VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER |
                VIRTIO_STATUS_DRIVER_OK);

    /* Step 8: Re-populate RX queue with fresh descriptors */
    rx_populate();

    VOS3_INFO("[VBUS] Hardware reset complete — VirtIO device re-initialized");
}

/* ============================================================================
 * Phase 4.2.5: ASYNC EVENT SIGNALING (ISR-safe, fire-and-forget)
 * ============================================================================ */

static uint64_t g_vbus_event_dropped_count = 0;

int vos3_vbus_send_frame_trylock(uint8_t type, uint8_t slot_id, uint16_t tag,
                                  const void *payload, uint32_t len)
{
    if (!g_vbus_dev.initialized) {
        return -1;
    }
    if (len > VBUS_MAX_TX_PAYLOAD) {
        return -1;
    }

    /* Try to acquire TX lock — if busy, increment drop counter and return */
    if (!vos3_spinlock_try_acquire(&g_vbus_dev.tx_lock)) {
        __atomic_fetch_add(&g_vbus_event_dropped_count, 1, __ATOMIC_RELAXED);
        return -2;  /* TX busy — dropped */
    }

    uint32_t frame_len = VBUS_FRAME_HDR_SIZE + len;
    if (frame_len > sizeof(g_vbus_tx_bounce_async)) {
        vos3_spinlock_unlock(&g_vbus_dev.tx_lock);
        return -1;  /* Too large for async bounce buffer */
    }
    virtio_queue_t *vq = &g_vbus_dev.tx_queue;

    /* Use dedicated async bounce buffer for FEEDBACK frames */
    build_frame_header(g_vbus_tx_bounce_async, type, slot_id, tag, payload, len);
    if (len > 0) {
        memcpy(g_vbus_tx_bounce_async + VBUS_FRAME_HDR_SIZE, payload, len);
    }

    vos3_spinlock_lock(&vq->lock);

    if (vq->num_free == 0) {
        vos3_spinlock_unlock(&vq->lock);
        vos3_spinlock_unlock(&g_vbus_dev.tx_lock);
        __atomic_fetch_add(&g_vbus_event_dropped_count, 1, __ATOMIC_RELAXED);
        return -1;
    }

    /* Reclaim any previously completed descriptors first */
    while (vq->last_used_idx != vq->used->idx) {
        uint16_t used_slot = vq->used->ring[vq->last_used_idx % vq->num_desc].id;
        vq->desc[used_slot].next = vq->free_head;
        vq->free_head = used_slot;
        vq->num_free++;
        vq->last_used_idx++;
    }

    uint16_t desc_idx = vq->free_head;
    vq->free_head = vq->desc[desc_idx].next;
    vq->num_free--;

    vq->desc[desc_idx].addr = virtio_virt_to_phys(g_vbus_tx_bounce_async);
    vq->desc[desc_idx].len = frame_len;
    vq->desc[desc_idx].flags = 0;

    uint16_t avail_idx = vq->avail->idx % vq->num_desc;
    vq->avail->ring[avail_idx] = desc_idx;
    virtio_mb();
    vq->avail->idx++;
    virtio_mb();

    virtio_outw(g_vbus_dev.io_base + VIRTIO_PCI_QUEUE_NOTIFY,
                VBUS_TX_QUEUE_IDX);

    vos3_spinlock_unlock(&vq->lock);

    /* Truly non-blocking: kick and return immediately.
     * QEMU processes virtio notifications synchronously on PCI write,
     * so the bounce buffer data is already DMA'd by the time we return.
     * Descriptor will be reclaimed on the NEXT call (see reclaim above). */
    g_vbus_dev.stats.tx_frames++;
    g_vbus_dev.stats.tx_bytes += frame_len;

    vos3_spinlock_unlock(&g_vbus_dev.tx_lock);
    return 0;
}

/* ============================================================================
 * Phase 4.2.7: DEEP DIAGNOSTIC FEEDBACK
 * ============================================================================ */

void vos3_vbus_get_rx_tail(uint8_t *out, uint8_t max_len, uint8_t *actual_len)
{
    uint32_t used = vbus_rx_ring_used();
    uint8_t copy_len = max_len;
    if (copy_len > (uint8_t)used) {
        copy_len = (uint8_t)used;
    }
    if (copy_len > 0) {
        /* Read the last copy_len bytes from the ring buffer */
        vbus_rx_ring_read(used - copy_len, out, copy_len);
    }
    *actual_len = copy_len;
}

int vos3_vbus_send_feedback(uint8_t slot_id, uintptr_t fault_addr,
                            uintptr_t fault_rip, uintptr_t fault_rsp,
                            const uint8_t *stack_capture, uint8_t stack_len,
                            const uint8_t *rx_tail, uint8_t tail_len)
{
    /* Build payload: max 219 bytes
     * [u8:slot_id][u64:fault_addr LE][u64:fault_rip LE][u64:fault_rsp LE]
     * [u8:stack_len][stack_capture...][u8:tail_len][rx_tail...] */
    uint8_t payload[220];
    uint32_t off = 0;

    payload[off++] = slot_id;

    /* fault_addr LE */
    for (int i = 0; i < 8; i++) {
        payload[off++] = (uint8_t)((fault_addr >> (i * 8)) & 0xFF);
    }
    /* fault_rip LE */
    for (int i = 0; i < 8; i++) {
        payload[off++] = (uint8_t)((fault_rip >> (i * 8)) & 0xFF);
    }
    /* fault_rsp LE */
    for (int i = 0; i < 8; i++) {
        payload[off++] = (uint8_t)((fault_rsp >> (i * 8)) & 0xFF);
    }

    /* stack capture */
    if (stack_len > 128) stack_len = 128;
    payload[off++] = stack_len;
    if (stack_len > 0 && stack_capture != NULL) {
        memcpy(payload + off, stack_capture, stack_len);
        off += stack_len;
    }

    /* rx tail */
    if (tail_len > 64) tail_len = 64;
    payload[off++] = tail_len;
    if (tail_len > 0 && rx_tail != NULL) {
        memcpy(payload + off, rx_tail, tail_len);
        off += tail_len;
    }

    return vos3_vbus_send_frame_trylock(VBUS_TYPE_FEEDBACK, slot_id,
                                        VBUS_TAG_ASYNC, payload, off);
}

/* ============================================================================
 * Resilience-Matrix F2 — out-of-band BUSY frame
 *
 * When kernel-side congestion is asserted (g_congestion == 1U), emit a
 * VBUS_TYPE_BUSY frame so the client receives explicit backpressure
 * rather than a silent send failure. The retry hint (milliseconds) is
 * encoded in the frame's `tag` field — capped to UINT16_MAX (~65 s).
 *
 * This is `_trylock` to avoid blocking when the dispatcher is already
 * mid-frame; if the lock cannot be acquired, the BUSY signal is
 * dropped (the client will retry naturally on its own backoff timer).
 * ============================================================================ */
extern volatile uint32_t g_congestion;

int vos3_vbus_send_busy(uint8_t slot_id, uint16_t retry_after_ms)
{
    if (g_congestion == 0U) {
        return 0; /* No need to signal */
    }
    /* Empty payload — the BUSY semantics are entirely in the type byte
     * + retry_after_ms in the tag. */
    uint8_t payload[1];
    payload[0] = 0U;
    return vos3_vbus_send_frame_trylock(VBUS_TYPE_BUSY,
                                        slot_id,
                                        retry_after_ms,
                                        payload,
                                        0U);
}

/* ============================================================================
 * Phase 4.2.5: ASYNC EVENT SIGNALING (ISR-safe, fire-and-forget)
 * ============================================================================ */

void vos3_vbus_signal_event(uint8_t slot_id, uint8_t event_code, uint32_t value)
{
    if (!g_vbus_dev.initialized) {
        return;
    }

    /* Defer to event ring — ISR-safe, never lost, drained from bridge poll */
    vos3_vbus_event_ring_push(slot_id, event_code, value);
}

void vos3_vbus_signal_event_sync(uint8_t slot_id, uint8_t event_code, uint32_t value)
{
    if (!g_vbus_dev.initialized) {
        return;
    }

    /* Blocking send from bridge dispatch context (non-ISR).
     * Guaranteed delivery for ISC_MESSAGE events etc. */
    uint8_t payload[14];
    payload[0] = slot_id;
    payload[1] = event_code;
    payload[2] = (uint8_t)(value & 0xFF);
    payload[3] = (uint8_t)((value >> 8) & 0xFF);
    payload[4] = (uint8_t)((value >> 16) & 0xFF);
    payload[5] = (uint8_t)((value >> 24) & 0xFF);

    extern uint64_t vos3_timer_get_ticks(void);
    uint64_t ts = vos3_timer_get_ticks();
    payload[6]  = (uint8_t)(ts & 0xFF);
    payload[7]  = (uint8_t)((ts >> 8) & 0xFF);
    payload[8]  = (uint8_t)((ts >> 16) & 0xFF);
    payload[9]  = (uint8_t)((ts >> 24) & 0xFF);
    payload[10] = (uint8_t)((ts >> 32) & 0xFF);
    payload[11] = (uint8_t)((ts >> 40) & 0xFF);
    payload[12] = (uint8_t)((ts >> 48) & 0xFF);
    payload[13] = (uint8_t)((ts >> 56) & 0xFF);

    vos3_vbus_send_frame(VBUS_TYPE_EVENT, slot_id, VBUS_TAG_ASYNC, payload, 14);
}

/* ============================================================================
 * EVENT RING BUFFER IMPLEMENTATION (VBus v2)
 * ============================================================================ */

void vos3_vbus_event_ring_push(uint8_t slot_id, uint8_t code, uint32_t value)
{
    extern uint64_t vos3_timer_get_ticks(void);

    uint32_t tail = __atomic_load_n(&g_event_ring_tail, __ATOMIC_RELAXED);
    uint32_t idx = tail % VBUS_EVENT_RING_SIZE;

    g_event_ring[idx].slot_id    = slot_id;
    g_event_ring[idx].event_code = code;
    g_event_ring[idx].value      = value;
    g_event_ring[idx].timestamp  = vos3_timer_get_ticks();

    __atomic_store_n(&g_event_ring_tail, tail + 1, __ATOMIC_RELEASE);

    /* Overwrites oldest on full — head advances if needed */
    uint32_t head = __atomic_load_n(&g_event_ring_head, __ATOMIC_RELAXED);
    if (tail + 1 - head > VBUS_EVENT_RING_SIZE) {
        __atomic_store_n(&g_event_ring_head, tail + 1 - VBUS_EVENT_RING_SIZE,
                         __ATOMIC_RELEASE);
    }
}

int vos3_vbus_event_ring_drain(void)
{
    int count = 0;
    uint32_t head = __atomic_load_n(&g_event_ring_head, __ATOMIC_ACQUIRE);
    uint32_t tail = __atomic_load_n(&g_event_ring_tail, __ATOMIC_ACQUIRE);

    while (head != tail) {
        uint32_t idx = head % VBUS_EVENT_RING_SIZE;
        vbus_event_entry_t *e = &g_event_ring[idx];

        /* Build 14-byte event payload */
        uint8_t payload[14];
        payload[0] = e->slot_id;
        payload[1] = e->event_code;
        payload[2] = (uint8_t)(e->value & 0xFF);
        payload[3] = (uint8_t)((e->value >> 8) & 0xFF);
        payload[4] = (uint8_t)((e->value >> 16) & 0xFF);
        payload[5] = (uint8_t)((e->value >> 24) & 0xFF);
        payload[6]  = (uint8_t)(e->timestamp & 0xFF);
        payload[7]  = (uint8_t)((e->timestamp >> 8) & 0xFF);
        payload[8]  = (uint8_t)((e->timestamp >> 16) & 0xFF);
        payload[9]  = (uint8_t)((e->timestamp >> 24) & 0xFF);
        payload[10] = (uint8_t)((e->timestamp >> 32) & 0xFF);
        payload[11] = (uint8_t)((e->timestamp >> 40) & 0xFF);
        payload[12] = (uint8_t)((e->timestamp >> 48) & 0xFF);
        payload[13] = (uint8_t)((e->timestamp >> 56) & 0xFF);

        vos3_vbus_send_frame(VBUS_TYPE_EVENT, e->slot_id, VBUS_TAG_ASYNC,
                              payload, 14);
        head++;
        count++;
    }

    __atomic_store_n(&g_event_ring_head, head, __ATOMIC_RELEASE);
    return count;
}
