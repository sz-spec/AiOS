/**
 * @file nvme.h
 * @brief VOS3 NVMe 1.4 Block Driver — Sovereign Storage
 *
 * @details Freestanding NVMe driver for VOS3.  Implements the NVMe 1.4
 *          specification subset needed for a polled-mode block driver:
 *          Admin queue (Identify, Create I/O Queue), I/O queues (Read,
 *          Write, Flush), and PRP-based DMA with boundary safety.
 *
 *          Security guarantees:
 *          - DMA boundary guard: rejects PRPs outside [1MB, 4GB)
 *          - PCI memory hole (3GB-4GB) rejected for all DMA addresses
 *          - Kernel stack region excluded from DMA targets
 *          - Controller fatal status checked before every command
 *          - No interrupts — fully polled (no ISR deadlock risk)
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_NVME_H
#define VOS3_NVME_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * NVMe QUEUE PARAMETERS
 * ============================================================================ */

/** @brief Admin queue depth (number of entries) */
#define VOS3_NVME_ADMIN_QUEUE_DEPTH     32U

/** @brief I/O queue depth (number of entries) */
#define VOS3_NVME_IO_QUEUE_DEPTH        64U

/** @brief Maximum I/O queues — one per CPU core (SMP-2) */
#define VOS3_NVME_MAX_IO_QUEUES         2U

/** @brief Maximum PRP entries per command (128KB max transfer) */
#define VOS3_NVME_MAX_PRP_PER_CMD       32U

/** @brief Maximum transfer size in bytes (128KB) */
#define VOS3_NVME_MAX_TRANSFER_SIZE     (128U * 1024U)

/** @brief Default LBA size in bytes */
#define VOS3_NVME_SECTOR_SIZE           512U

/* ============================================================================
 * NVMe TIMEOUTS
 * ============================================================================ */

/** @brief Controller timeout in milliseconds */
#define VOS3_NVME_TIMEOUT_MS            5000U

/** @brief Polling iterations before timeout */
#define VOS3_NVME_POLL_ATTEMPTS         500000U

/* ============================================================================
 * NVMe ADMIN OPCODES (NVMe 1.4 Spec, Figure 44)
 * ============================================================================ */

/** @brief Delete I/O Submission Queue */
#define VOS3_NVME_ADMIN_DELETE_SQ       0x00U

/** @brief Create I/O Submission Queue */
#define VOS3_NVME_ADMIN_CREATE_SQ       0x01U

/** @brief Create I/O Completion Queue */
#define VOS3_NVME_ADMIN_CREATE_CQ       0x05U

/** @brief Delete I/O Completion Queue */
#define VOS3_NVME_ADMIN_DELETE_CQ       0x06U

/** @brief Identify command (Controller or Namespace) */
#define VOS3_NVME_ADMIN_IDENTIFY        0x06U

/** @brief Set Features command */
#define VOS3_NVME_ADMIN_SET_FEATURES    0x09U

/** @brief Get Features command */
#define VOS3_NVME_ADMIN_GET_FEATURES    0x0AU

/* ============================================================================
 * NVMe I/O OPCODES (NVMe 1.4 Spec, Figure 346)
 * ============================================================================ */

/** @brief Flush command */
#define VOS3_NVME_IO_FLUSH              0x00U

/** @brief Write command */
#define VOS3_NVME_IO_WRITE              0x01U

/** @brief Read command */
#define VOS3_NVME_IO_READ               0x02U

/* ============================================================================
 * NVMe STATUS CODES (NVMe 1.4 Spec, Figure 126)
 * ============================================================================ */

/** @brief Successful completion */
#define VOS3_NVME_SC_SUCCESS            0x0000U

/** @brief Invalid command opcode */
#define VOS3_NVME_SC_INVALID_OPCODE     0x0001U

/** @brief Invalid field in command */
#define VOS3_NVME_SC_INVALID_FIELD      0x0002U

/** @brief Data transfer error */
#define VOS3_NVME_SC_DATA_XFER_ERROR    0x0004U

/** @brief Internal device error */
#define VOS3_NVME_SC_INTERNAL_ERROR     0x0006U

/* ============================================================================
 * NVMe IDENTIFY CNS VALUES (NVMe 1.4 Spec, Figure 247)
 * ============================================================================ */

/** @brief Identify Namespace data structure */
#define VOS3_NVME_IDENTIFY_NS           0x00U

/** @brief Identify Controller data structure */
#define VOS3_NVME_IDENTIFY_CTRL         0x01U

/* ============================================================================
 * NVMe CONTROLLER CONFIGURATION (CC) REGISTER BITS
 * ============================================================================ */

/** @brief Controller Enable */
#define VOS3_NVME_CC_EN                 (1U << 0)

/** @brief Command Set Selected — NVM Command Set */
#define VOS3_NVME_CC_CSS_NVM            (0U << 4)

/** @brief Memory Page Size — 4KB (MPS=0 -> 2^(12+0) = 4096) */
#define VOS3_NVME_CC_MPS_4K             (0U << 7)

/** @brief I/O Submission Queue Entry Size (log2) */
#define VOS3_NVME_CC_IOSQES(n)          ((uint32_t)(n) << 16)

/** @brief I/O Completion Queue Entry Size (log2) */
#define VOS3_NVME_CC_IOCQES(n)          ((uint32_t)(n) << 20)

/* ============================================================================
 * NVMe CONTROLLER STATUS (CSTS) REGISTER BITS
 * ============================================================================ */

/** @brief Controller Ready */
#define VOS3_NVME_CSTS_RDY              (1U << 0)

/** @brief Controller Fatal Status */
#define VOS3_NVME_CSTS_CFS              (1U << 1)

/* ============================================================================
 * NVMe REGISTER OFFSETS (NVMe 1.4 Spec, Figure 36)
 * ============================================================================ */

/** @brief Controller Capabilities (64-bit) */
#define VOS3_NVME_REG_CAP               0x00U

/** @brief Version */
#define VOS3_NVME_REG_VS                0x08U

/** @brief Interrupt Mask Set */
#define VOS3_NVME_REG_INTMS             0x0CU

/** @brief Interrupt Mask Clear */
#define VOS3_NVME_REG_INTMC             0x10U

/** @brief Controller Configuration */
#define VOS3_NVME_REG_CC                0x14U

/** @brief Controller Status */
#define VOS3_NVME_REG_CSTS              0x1CU

/** @brief Admin Queue Attributes */
#define VOS3_NVME_REG_AQA               0x24U

/** @brief Admin Submission Queue Base Address (64-bit) */
#define VOS3_NVME_REG_ASQ               0x28U

/** @brief Admin Completion Queue Base Address (64-bit) */
#define VOS3_NVME_REG_ACQ               0x30U

/* ============================================================================
 * DMA BOUNDARY GUARD
 * ============================================================================ */

/** @brief DMA lower bound — below 1MB is BIOS/legacy territory */
#define VOS3_NVME_DMA_LOWER_BOUND       0x100000ULL

/** @brief DMA upper bound — 4GB physical max (VOS3 cap) */
#define VOS3_NVME_DMA_UPPER_BOUND       0x100000000ULL

/* ============================================================================
 * NVMe ERROR CODES
 * ============================================================================ */

/** @brief Success */
#define VOS3_NVME_OK                     0

/** @brief Controller timeout (-ETIMEDOUT) */
#define VOS3_NVME_E_TIMEOUT             (-110)

/** @brief Controller fatal status (-EIO) */
#define VOS3_NVME_E_FATAL               (-5)

/** @brief DMA buffer allocation failed (-ENOMEM) */
#define VOS3_NVME_E_NO_MEM             (-12)

/** @brief Invalid parameter (-EINVAL) */
#define VOS3_NVME_E_INVALID            (-22)

/** @brief DMA address out of safe bounds (-EPERM) */
#define VOS3_NVME_E_DMA_BOUNDARY       (-1)

/** @brief Command completion error (-EPROTO) */
#define VOS3_NVME_E_CMD_FAILED         (-71)

/** @brief Controller not initialized (-ENXIO) */
#define VOS3_NVME_E_NOT_READY          (-6)

/* ============================================================================
 * NVMe SUBMISSION QUEUE ENTRY (SQE) — 64 bytes (NVMe 1.4 Spec, Figure 104)
 * ============================================================================ */

/**
 * @brief NVMe Submission Queue Entry — 64-byte command descriptor
 *
 * Common format for both Admin and I/O commands.  The opcode field
 * determines the command type; cdw10-cdw15 are command-specific.
 */
typedef struct __attribute__((packed)) vos3_nvme_sqe {
    uint8_t     opcode;         /**< Command opcode */
    uint8_t     flags;          /**< Fused operation flags */
    uint16_t    cid;            /**< Command identifier */
    uint32_t    nsid;           /**< Namespace identifier */
    uint64_t    rsvd2;          /**< Reserved */
    uint64_t    mptr;           /**< Metadata pointer */
    uint64_t    prp1;           /**< PRP Entry 1 (first data page) */
    uint64_t    prp2;           /**< PRP Entry 2 or PRP List pointer */
    uint32_t    cdw10;          /**< Command dword 10 */
    uint32_t    cdw11;          /**< Command dword 11 */
    uint32_t    cdw12;          /**< Command dword 12 */
    uint32_t    cdw13;          /**< Command dword 13 */
    uint32_t    cdw14;          /**< Command dword 14 */
    uint32_t    cdw15;          /**< Command dword 15 */
} vos3_nvme_sqe_t;

_Static_assert(sizeof(vos3_nvme_sqe_t) == 64,
               "NVMe SQE must be exactly 64 bytes");

/* ============================================================================
 * NVMe COMPLETION QUEUE ENTRY (CQE) — 16 bytes (NVMe 1.4 Spec, Figure 124)
 * ============================================================================ */

/**
 * @brief NVMe Completion Queue Entry — 16-byte completion descriptor
 *
 * Bit 0 of the status field is the Phase Tag, used to detect new
 * completions in a polled-mode driver without interrupts.
 */
typedef struct __attribute__((packed)) vos3_nvme_cqe {
    uint32_t    result;         /**< Command-specific result (DW0) */
    uint32_t    rsvd;           /**< Reserved (DW1) */
    uint16_t    sq_head;        /**< SQ Head pointer */
    uint16_t    sq_id;          /**< SQ Identifier */
    uint16_t    cid;            /**< Command Identifier */
    uint16_t    status;         /**< Status Field (bit 0 = Phase Tag) */
} vos3_nvme_cqe_t;

_Static_assert(sizeof(vos3_nvme_cqe_t) == 16,
               "NVMe CQE must be exactly 16 bytes");

/* ============================================================================
 * NVMe QUEUE PAIR STRUCTURE
 * ============================================================================ */

/**
 * @brief NVMe queue pair — paired submission and completion queues
 *
 * Each queue pair holds the SQ/CQ virtual and physical addresses,
 * doorbell pointers, and ring buffer state.  Queue ID 0 is the
 * Admin queue; IDs 1+ are I/O queues.
 */
typedef struct vos3_nvme_queue {
    volatile vos3_nvme_sqe_t *sq;       /**< Submission queue (virtual addr) */
    volatile vos3_nvme_cqe_t *cq;       /**< Completion queue (virtual addr) */
    uintptr_t       sq_phys;            /**< SQ physical address (for DMA) */
    uintptr_t       cq_phys;            /**< CQ physical address (for DMA) */
    volatile uint32_t *sq_doorbell;     /**< SQ tail doorbell MMIO register */
    volatile uint32_t *cq_doorbell;     /**< CQ head doorbell MMIO register */
    uint16_t        depth;              /**< Queue depth (number of entries) */
    uint16_t        sq_tail;            /**< Current SQ tail index */
    uint16_t        cq_head;            /**< Current CQ head index */
    uint16_t        cid_counter;        /**< Monotonic command ID generator */
    uint8_t         cq_phase;           /**< Expected CQ phase bit (0 or 1) */
    uint8_t         id;                 /**< Queue ID (0 = admin, 1+ = I/O) */
    uint8_t         _pad[2];            /**< Padding for alignment */
} vos3_nvme_queue_t;

/* ============================================================================
 * NVMe PRP LIST STRUCTURE (DMA Boundary Safety)
 * ============================================================================ */

/**
 * @brief PRP (Physical Region Page) list for multi-page DMA transfers
 *
 * Pre-allocated per I/O queue.  Each entry is a physical page address
 * that has been validated against DMA boundary guards before use.
 */
typedef struct vos3_nvme_prp_list {
    uint64_t    entries[VOS3_NVME_MAX_PRP_PER_CMD]; /**< Physical page addresses */
    uintptr_t   list_phys;              /**< Physical address of this PRP list page */
    uint32_t    count;                  /**< Number of valid entries */
    uint32_t    _pad;                   /**< Padding for alignment */
} vos3_nvme_prp_list_t;

/* ============================================================================
 * NVMe CONTROLLER STATE
 * ============================================================================ */

/**
 * @brief NVMe controller state — complete driver context
 *
 * Holds MMIO base, cached CAP register, admin and I/O queue pairs,
 * pre-allocated PRP lists, namespace geometry, and controller identity
 * strings from Identify Controller.
 */
typedef struct vos3_nvme_ctrl {
    uintptr_t           mmio_base;      /**< MMIO virtual address */
    uint32_t            dstrd;          /**< Doorbell Stride (from CAP) */
    uint32_t            mqes;           /**< Max Queue Entries Supported */
    uint64_t            cap;            /**< Cached CAP register */
    uint32_t            version;        /**< Controller version (VS register) */

    /* Admin queue */
    vos3_nvme_queue_t   admin_q;        /**< Admin queue pair (ID 0) */

    /* I/O queues (one per CPU) */
    vos3_nvme_queue_t   io_q[VOS3_NVME_MAX_IO_QUEUES]; /**< I/O queue pairs */
    uint32_t            io_queue_count; /**< Number of active I/O queues */

    /* PRP lists (pre-allocated, one per I/O queue) */
    vos3_nvme_prp_list_t prp_lists[VOS3_NVME_MAX_IO_QUEUES]; /**< Per-queue PRP lists */

    /* Namespace info (from Identify Namespace) */
    uint64_t            ns_size;        /**< Namespace size in logical blocks */
    uint32_t            ns_lba_size;    /**< LBA data size in bytes */
    uint32_t            nsid;           /**< Active namespace ID */

    /* Controller info (from Identify Controller) */
    char                serial[21];     /**< Serial number (null-terminated) */
    char                model[41];      /**< Model number (null-terminated) */
    char                firmware[9];    /**< Firmware revision (null-terminated) */

    /* I/O statistics */
    uint64_t            total_reads;    /**< Total successful read commands */
    uint64_t            total_writes;   /**< Total successful write commands */
    uint64_t            total_errors;   /**< Total I/O errors */

    /* State flags */
    uint8_t             initialized;    /**< 1 = fully operational */
    uint8_t             fatal;          /**< 1 = controller in fatal state */
    uint8_t             _pad[2];        /**< Padding for alignment */
} vos3_nvme_ctrl_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize NVMe controller — full init from probe state
 *
 * Steps: disable controller -> create admin queue -> identify controller ->
 * identify namespace -> create I/O queues -> enable controller
 *
 * @param mmio_base  Kernel-mapped MMIO base address (from storage_hal probe)
 * @param bar0_phys  Physical BAR0 address (for doorbell calculation)
 * @return 0 on success, negative error code on failure
 *
 * @note Called from storage_hal after successful NVMe probe
 * @note Allocates DMA memory via vos3_pmm_alloc_pages()
 */
int vos3_nvme_init(uintptr_t mmio_base, uint32_t bar0_phys);

/**
 * @brief Read sectors from NVMe namespace
 *
 * @param lba    Starting Logical Block Address
 * @param count  Number of sectors to read
 * @param buf    Destination buffer (must be page-aligned for DMA)
 * @return 0 on success, negative error code on failure
 *
 * @note DMA boundary guard: rejects buf addresses outside [1MB, 4GB)
 * @note PRP list constructed with bounds checking per entry
 * @note Maximum transfer: VOS3_NVME_MAX_TRANSFER_SIZE (128KB)
 */
int vos3_nvme_read(uint64_t lba, uint32_t count, void *buf);

/**
 * @brief Write sectors to NVMe namespace
 *
 * @param lba    Starting Logical Block Address
 * @param count  Number of sectors to write
 * @param buf    Source buffer (must be page-aligned for DMA)
 * @return 0 on success, negative error code on failure
 */
int vos3_nvme_write(uint64_t lba, uint32_t count, const void *buf);

/**
 * @brief Check if NVMe controller is healthy
 * @return 1 if healthy, 0 if fatal or uninitialized
 */
int vos3_nvme_is_healthy(void);

/**
 * @brief Get NVMe controller info (read-only)
 * @return Pointer to controller state, or NULL if not initialized
 */
const vos3_nvme_ctrl_t *vos3_nvme_get_ctrl(void);

/**
 * @brief Reset NVMe controller after fatal error
 * @return 0 on success, negative error code on failure
 *
 * @note Wipes all in-flight I/O — no zombie requests after reset
 */
int vos3_nvme_reset(void);

/**
 * @brief Validate a PRP entry against DMA safety bounds
 *
 * @param phys_addr  Physical address to validate
 * @return 0 if safe, -EPERM if out of bounds
 *
 * @note Rejects addresses < 1MB (BIOS/legacy)
 * @note Rejects addresses >= 4GB (beyond VOS3 physical cap)
 * @note Rejects kernel stack region addresses
 * @note Rejects addresses in PCI memory hole (3GB-4GB)
 */
int vos3_nvme_validate_prp(uint64_t phys_addr);

#endif /* VOS3_NVME_H */
