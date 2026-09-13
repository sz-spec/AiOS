/* SPDX-License-Identifier: MIT
 *
 * vos3_sdk.h — VOS3 AI Subsystem SDK Header (Phase 4.8-H)
 *
 * Self-contained header for user-space and external tooling.
 * No kernel-internal includes required.
 *
 * Heartbeat Page Layout (4096 bytes at VOS3_HEARTBEAT_PAGE_VADDR):
 *   [0x000..0x00F]  CQ header  (head/tail, 8 bytes + 8 reserved)
 *   [0x010..0x1FF]  CQ entries (62 × 8 = 496 bytes)
 *   [0x400..0x40F]  SQ header  (head/tail/sentinel/reserved, 16 bytes)
 *   [0x410..0x7FF]  SQ entries (31 × 32 = 992 bytes)
 *   [0x800..0x80F]  Doorbell   (4-byte atomic bitmap + 12 reserved)
 */

#ifndef VOS3_SDK_H
#define VOS3_SDK_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Constants
 * ============================================================================ */

/** Heartbeat page virtual address (kernel-mapped, user-readable) */
#define VOS3_SDK_HEARTBEAT_VADDR  0xFFFFFFFFFFFFF000ULL

/** Layout offsets within the heartbeat page */
#define VOS3_SDK_CQ_OFFSET        0U       /**< Completion Queue header */
#define VOS3_SDK_SQ_OFFSET        1024U    /**< Submission Queue header */
#define VOS3_SDK_DOORBELL_OFFSET  2048U    /**< Doorbell matrix */

/** Ring sizes */
#define VOS3_SDK_CQ_ENTRIES       62U
#define VOS3_SDK_SQ_ENTRIES       31U

/** Sentinel — "SNTL" in ASCII (little-endian) */
#define VOS3_SDK_SNTL             0x534E544CU

/** SQ command types */
#define VOS3_SDK_SQ_CMD_NOP       0x00U
#define VOS3_SDK_SQ_CMD_DATA      0x01U
#define VOS3_SDK_SQ_CMD_SYNC      0x02U
#define VOS3_SDK_SQ_CMD_FINISH    0x03U
#define VOS3_SDK_SQ_CMD_WARP_DATA 0x04U  /**< Zero-copy ivshmem warp read (Phase 4.9b) */

/** Maximum model slots */
#define VOS3_SDK_MODEL_SLOT_MAX   4U

/* ============================================================================
 * Submission Queue Structures
 * ============================================================================ */

/** SQ entry: 32 bytes */
typedef struct vos3_sdk_sq_entry {
    uint32_t    sentinel;       /**< VOS3_SDK_SNTL ^ (uint32_t)tag */
    uint8_t     slot_id;        /**< Target slot (0..3) */
    uint8_t     cmd;            /**< VOS3_SDK_SQ_CMD_* */
    uint16_t    length;         /**< Payload length (DATA frames, max 49152) */
    uint64_t    offset;         /**< Write offset in slot's HugePage region */
    uint64_t    payload_addr;   /**< Physical address of payload (0 = inline) */
    uint64_t    tag;            /**< Sequence tag for CQ correlation */
} __attribute__((packed)) vos3_sdk_sq_entry_t;

/** SQ header: 16 bytes at heartbeat+1024 */
typedef struct vos3_sdk_sq_header {
    volatile uint32_t   head;       /**< Consumer read pointer (kernel) */
    volatile uint32_t   tail;       /**< Producer write pointer */
    uint32_t            sentinel;   /**< VOS3_SDK_SNTL — ring integrity */
    uint32_t            reserved;
} __attribute__((packed)) vos3_sdk_sq_header_t;

/* ============================================================================
 * Completion Ring Structures
 * ============================================================================ */

/** CQ entry: 8 bytes */
typedef struct vos3_sdk_cring_entry {
    uint8_t     slot_id;        /**< Slot that completed */
    uint8_t     flags;          /**< 0x01=DATA, 0x02=CMD, 0x80=overflow */
    uint16_t    frame_seq;      /**< Monotonic frame sequence number */
    uint32_t    tick_lo;        /**< Lower 32 bits of completion tick */
} __attribute__((packed)) vos3_sdk_cring_entry_t;

/** CQ header: 8 bytes at heartbeat+0 */
typedef struct vos3_sdk_cring_header {
    volatile uint32_t   head;       /**< Kernel write pointer */
    volatile uint32_t   tail;       /**< Consumer read pointer */
} __attribute__((packed)) vos3_sdk_cring_header_t;

/* ============================================================================
 * Doorbell Matrix
 * ============================================================================ */

/** Doorbell: 4 bytes at heartbeat+2048 */
typedef struct vos3_sdk_doorbell {
    volatile uint32_t bits;     /**< Bit N = slot N has pending work */
} vos3_sdk_doorbell_t;

/* ============================================================================
 * Inline Macros
 * ============================================================================ */

/** Compute current SQ depth (entries pending) */
#define VOS3_SQ_DEPTH(hdr) \
    ((uint32_t)((hdr)->tail - (hdr)->head))

/**
 * VOS3_SQ_POST — Post an entry to the Submission Queue.
 *
 * Rolling sentinel: SNTL ^ (uint32_t)(tag) — backward compatible (tag=0 → SNTL).
 * Double-fence: sfence → tail update → mfence (no clflushopt in ring 3).
 *
 * @param hdr       Pointer to vos3_sdk_sq_header_t
 * @param entries   Pointer to first vos3_sdk_sq_entry_t
 * @param sid       Slot ID (uint8_t)
 * @param cmd       Command (uint8_t, VOS3_SDK_SQ_CMD_*)
 * @param len       Payload length (uint16_t)
 * @param off       Write offset (uint64_t)
 * @param tg        Sequence tag (uint64_t)
 * @param rc        int variable — set to 0 on success, -12 (ENOMEM) if full
 */
#define VOS3_SQ_POST(hdr, entries, sid, cmd, len, off, tg, rc) \
    do { \
        uint32_t _tail = (hdr)->tail; \
        uint32_t _head = (hdr)->head; \
        if ((_tail - _head) >= VOS3_SDK_SQ_ENTRIES) { \
            (rc) = -12; \
        } else { \
            uint32_t _idx = _tail % VOS3_SDK_SQ_ENTRIES; \
            volatile vos3_sdk_sq_entry_t *_e = &(entries)[_idx]; \
            _e->sentinel     = VOS3_SDK_SNTL ^ (uint32_t)(tg); \
            _e->slot_id      = (sid); \
            _e->cmd          = (cmd); \
            _e->length       = (len); \
            _e->offset       = (off); \
            _e->payload_addr = 0; \
            _e->tag          = (tg); \
            __asm__ volatile("sfence" ::: "memory"); \
            (hdr)->tail = _tail + 1; \
            __asm__ volatile("mfence" ::: "memory"); \
            (rc) = 0; \
        } \
    } while (0)

/**
 * VOS3_DOORBELL_RING — Set doorbell bit for a slot.
 *
 * @param db    Pointer to vos3_sdk_doorbell_t
 * @param sid   Slot ID (uint8_t)
 */
#define VOS3_DOORBELL_RING(db, sid) \
    do { \
        __atomic_or_fetch(&(db)->bits, (1U << (sid)), __ATOMIC_RELEASE); \
    } while (0)

/**
 * VOS3_CQ_READ — Read a completion entry if available.
 *
 * @param hdr       Pointer to vos3_sdk_cring_header_t
 * @param entries   Pointer to first vos3_sdk_cring_entry_t
 * @param out       Pointer to vos3_sdk_cring_entry_t to fill
 * @param avail     int variable — set to 1 if entry read, 0 if empty
 */
#define VOS3_CQ_READ(hdr, entries, out, avail) \
    do { \
        uint32_t _t = (hdr)->tail; \
        uint32_t _h = __atomic_load_n( \
            (volatile uint32_t *)&(hdr)->head, __ATOMIC_ACQUIRE); \
        if (_t == _h) { \
            (avail) = 0; \
        } else { \
            uint32_t _ci = _t % VOS3_SDK_CQ_ENTRIES; \
            *(out) = (entries)[_ci]; \
            __asm__ volatile("" ::: "memory"); \
            (hdr)->tail = _t + 1; \
            (avail) = 1; \
        } \
    } while (0)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SDK_H */
