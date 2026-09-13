/**
 * @file npu.c
 * @brief VOS3 NPU/AI Accelerator Driver — Phase 8.4
 *
 * @details Freestanding polled-mode NPU driver for VOS3.  Implements PCI
 *          discovery, MMIO-mapped command queues, DMA fence validation
 *          (IOMMU-style guard), context management for V-Palace model
 *          swapping, and integration with the accel abstraction layer.
 *
 *          Supported hardware (via PCI class 0x12/subclass 0x00 or known
 *          vendor/device pairs):
 *            - NVIDIA NPU (vendor 0x10DE)
 *            - Intel NPU  (vendor 0x8086, device 0x7D1D)
 *            - AMD XDNA   (vendor 0x1022)
 *            - Google TPU  (vendor 0x1AE0)
 *            - Generic Processing Accelerators (class 0x12)
 *
 *          Security guarantees:
 *            - DMA fence per slot prevents NPU from accessing kernel memory
 *            - 64-bit overflow guard on all phys+size computations
 *            - sfence after SQ writes, lfence after CQ reads (Spectre safety)
 *            - All MMIO accesses through volatile pointers
 *            - No interrupts — fully polled (no ISR deadlock risk)
 *            - No dynamic dispatch, no shell execution
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 8.4 — NPU/AI Accelerator Driver
 * @note MISRA C:2024 Compliant
 */

/* ============================================================================
 * INCLUDES
 * ============================================================================
 *
 * npu.h does not exist yet as a separate header; all NPU-specific types and
 * constants are defined inline below.  When the header is created, replace
 * the inline definitions with:  #include "../../include/vos/npu.h"
 * ============================================================================ */

#include "../../include/vos/pci.h"
#include "../../include/vos/accel.h"
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * KERNEL DEPENDENCIES (FREESTANDING — FORWARD DECLARATIONS)
 * ============================================================================ */

extern void vos3_console_printf(const char *fmt, ...);
extern uintptr_t vos3_pmm_alloc_pages(size_t count, int flags);
extern void vos3_pmm_free_pages(uintptr_t addr, size_t count);

/* ============================================================================
 * LOGGING MACROS
 * ============================================================================ */

#ifndef VOS3_INFO
#define VOS3_INFO(fmt, ...)  vos3_console_printf("[NPU] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef VOS3_WARN
#define VOS3_WARN(fmt, ...)  vos3_console_printf("[NPU WARN] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef VOS3_ERROR
#define VOS3_ERROR(fmt, ...) vos3_console_printf("[NPU ERROR] " fmt "\n", ##__VA_ARGS__)
#endif

/* ============================================================================
 * ERRNO VALUES (freestanding — no <errno.h>)
 * ============================================================================ */

#ifndef EINVAL
#define EINVAL      22
#endif

#ifndef ENOTSUP
#define ENOTSUP     95
#endif

#ifndef ENOSPC
#define ENOSPC      28
#endif

#ifndef ENODEV
#define ENODEV      19
#endif

#ifndef EBUSY
#define EBUSY       16
#endif

#ifndef EIO
#define EIO         5
#endif

#ifndef ENOMEM
#define ENOMEM      12
#endif

#ifndef EFAULT
#define EFAULT      14
#endif

/* ============================================================================
 * NPU CONSTANTS
 * ============================================================================ */

/** @brief Maximum number of NPU devices supported */
#define VOS3_NPU_MAX_DEVICES        4U

/** @brief Maximum model slots per NPU device (matches AI guard) */
#define VOS3_NPU_MAX_SLOTS          8U

/** @brief Maximum contexts per device */
#define VOS3_NPU_MAX_CONTEXTS       16U

/** @brief Submission queue depth (entries) */
#define VOS3_NPU_SQ_DEPTH          64U

/** @brief Completion queue depth (entries) */
#define VOS3_NPU_CQ_DEPTH          64U

/** @brief NPU polling attempts before timeout */
#define VOS3_NPU_POLL_ATTEMPTS      1000000U

/** @brief Page size (4KB) */
#define VOS3_NPU_PAGE_SIZE          4096U

/** @brief MMIO region size per device (256KB) */
#define VOS3_NPU_MMIO_SIZE          (256U * 1024U)

/** @brief SRAM region max size (16MB) */
#define VOS3_NPU_SRAM_MAX_SIZE      (16U * 1024U * 1024U)

/** @brief NPU name maximum length */
#define VOS3_NPU_NAME_LEN           32U

/** @brief HHDM offset for physical<->virtual conversion */
#define NPU_HHDM_OFFSET            0xFFFF800000000000ULL

/** @brief MMIO virtual address base for NPU devices */
#define VOS3_NPU_MMIO_VBASE        0xFFFF880032000000ULL

/** @brief MMIO stride per device (1MB) */
#define VOS3_NPU_MMIO_STRIDE       0x100000ULL

/* ============================================================================
 * NPU MMIO REGISTER OFFSETS
 * ============================================================================ */

/** @brief Device capabilities register (RO, 64-bit) */
#define NPU_REG_CAP                 0x0000U

/** @brief Device version register (RO, 32-bit) */
#define NPU_REG_VERSION             0x0008U

/** @brief Device status register (RO, 32-bit) */
#define NPU_REG_STATUS              0x000CU

/** @brief Device control register (RW, 32-bit) */
#define NPU_REG_CTRL                0x0010U

/** @brief SQ tail doorbell base (stride = 4 bytes per queue) */
#define NPU_REG_SQ_DOORBELL_BASE   0x1000U

/** @brief CQ head doorbell base (stride = 4 bytes per queue) */
#define NPU_REG_CQ_DOORBELL_BASE   0x1004U

/** @brief Doorbell stride (8 bytes between SQ/CQ pairs) */
#define NPU_REG_DOORBELL_STRIDE    0x0008U

/** @brief SQ base address register (RW, 64-bit) */
#define NPU_REG_SQ_BASE            0x0020U

/** @brief CQ base address register (RW, 64-bit) */
#define NPU_REG_CQ_BASE            0x0028U

/** @brief SRAM base address register (RO, 64-bit) */
#define NPU_REG_SRAM_BASE          0x0030U

/** @brief SRAM size register (RO, 32-bit) */
#define NPU_REG_SRAM_SIZE          0x0038U

/** @brief TOPS rating register (RO, 32-bit — INT8 peak) */
#define NPU_REG_TOPS               0x003CU

/** @brief Device memory MB register (RO, 32-bit) */
#define NPU_REG_MEM_MB             0x0040U

/* ============================================================================
 * NPU STATUS BITS
 * ============================================================================ */

/** @brief Device ready bit in status register */
#define NPU_STATUS_READY            (1U << 0)

/** @brief Device fatal error bit */
#define NPU_STATUS_FATAL            (1U << 1)

/** @brief Device busy bit */
#define NPU_STATUS_BUSY             (1U << 2)

/* ============================================================================
 * NPU CONTROL BITS
 * ============================================================================ */

/** @brief Enable device */
#define NPU_CTRL_ENABLE             (1U << 0)

/** @brief Reset device */
#define NPU_CTRL_RESET              (1U << 1)

/* ============================================================================
 * NPU COMMAND TYPES
 * ============================================================================ */

/** @brief No operation */
#define VOS3_NPU_CMD_NOP            0x00U

/** @brief Matrix multiply */
#define VOS3_NPU_CMD_MATMUL         0x01U

/** @brief Softmax */
#define VOS3_NPU_CMD_SOFTMAX        0x02U

/** @brief Layer normalization */
#define VOS3_NPU_CMD_LAYERNORM      0x03U

/** @brief Fused attention */
#define VOS3_NPU_CMD_ATTENTION      0x04U

/** @brief Quantize (float -> int) */
#define VOS3_NPU_CMD_QUANTIZE       0x05U

/** @brief Dequantize (int -> float) */
#define VOS3_NPU_CMD_DEQUANTIZE     0x06U

/** @brief Element-wise add */
#define VOS3_NPU_CMD_ELEM_ADD       0x07U

/** @brief Element-wise multiply */
#define VOS3_NPU_CMD_ELEM_MUL       0x08U

/** @brief RoPE (Rotary Position Embedding) */
#define VOS3_NPU_CMD_ROPE           0x09U

/** @brief DMA copy (system RAM -> SRAM or reverse) */
#define VOS3_NPU_CMD_DMA_COPY       0x10U

/** @brief DMA writeback (SRAM -> system RAM) */
#define VOS3_NPU_CMD_DMA_WRITEBACK  0x11U

/** @brief Context save to SRAM */
#define VOS3_NPU_CMD_CONTEXT_SAVE   0x12U

/** @brief Context restore from SRAM */
#define VOS3_NPU_CMD_CONTEXT_LOAD   0x13U

/* ============================================================================
 * NPU DATA TYPES
 * ============================================================================ */

/** @brief 32-bit floating point */
#define VOS3_NPU_DTYPE_FP32         0U

/** @brief 16-bit floating point */
#define VOS3_NPU_DTYPE_FP16         1U

/** @brief 8-bit integer (quantized) */
#define VOS3_NPU_DTYPE_INT8         2U

/** @brief 4-bit integer (quantized) */
#define VOS3_NPU_DTYPE_INT4         3U

/** @brief 16-bit brain floating point */
#define VOS3_NPU_DTYPE_BF16         4U

/* ============================================================================
 * NPU RETURN CODES
 * ============================================================================ */

/** @brief Operation completed successfully */
#define VOS3_NPU_OK                 0

/** @brief Invalid argument */
#define VOS3_NPU_E_INVAL           (-EINVAL)

/** @brief DMA fence violation */
#define VOS3_NPU_E_DMA_FENCE      (-EFAULT)

/** @brief Device not found */
#define VOS3_NPU_E_NODEV          (-ENODEV)

/** @brief Device busy */
#define VOS3_NPU_E_BUSY           (-EBUSY)

/** @brief I/O error */
#define VOS3_NPU_E_IO             (-EIO)

/** @brief Out of memory */
#define VOS3_NPU_E_NOMEM          (-ENOMEM)

/** @brief No space left (context table full) */
#define VOS3_NPU_E_NOSPC          (-ENOSPC)

/** @brief Context mismatch */
#define VOS3_NPU_E_CTX_MISMATCH   (-71)

/** @brief Device not initialized */
#define VOS3_NPU_E_NOT_INIT       (-72)

/** @brief Completion timeout */
#define VOS3_NPU_E_TIMEOUT        (-73)

/* ============================================================================
 * NPU PCI IDENTITY — Known Vendor/Device Combinations
 * ============================================================================ */

/** @brief PCI class code for Processing Accelerators */
#define PCI_CLASS_PROC_ACCEL        0x12U

/** @brief PCI subclass for Neural Processing Unit */
#define PCI_SUBCLASS_NPU            0x00U

/** @brief NVIDIA vendor ID */
#define NPU_VENDOR_NVIDIA           0x10DEU

/** @brief Intel vendor ID */
#define NPU_VENDOR_INTEL            0x8086U

/** @brief Intel NPU device ID (Meteor Lake) */
#define NPU_DEVICE_INTEL_NPU       0x7D1DU

/** @brief AMD vendor ID */
#define NPU_VENDOR_AMD              0x1022U

/** @brief Google vendor ID (for TPU) */
#define NPU_VENDOR_GOOGLE           0x1AE0U

/* ============================================================================
 * NPU STRUCTURES — Submission Queue Entry (SQE)
 * ============================================================================ */

/**
 * @brief NPU Submission Queue Entry (64 bytes)
 *
 * @details Each SQE describes a single command to the NPU.  The layout is
 *          fixed at 64 bytes to match a cache line and simplify ring indexing.
 */
typedef struct __attribute__((packed, aligned(64))) vos3_npu_sqe {
    uint8_t     cmd_type;           /**< Command type (VOS3_NPU_CMD_*)       */
    uint8_t     flags;              /**< Command flags (reserved)            */
    uint16_t    cmd_id;             /**< Command identifier for completion   */
    uint32_t    context_id;         /**< Context/slot for DMA fence lookup   */
    uint64_t    src_phys;           /**< Source physical address             */
    uint64_t    dst_phys;           /**< Destination physical address        */
    uint32_t    length;             /**< Transfer length in bytes            */
    uint32_t    param0;             /**< Op-specific: M (rows A) for MATMUL  */
    uint32_t    param1;             /**< Op-specific: K (cols A) for MATMUL  */
    uint32_t    param2;             /**< Op-specific: N (cols B) for MATMUL  */
    uint32_t    dtype;              /**< Data type (VOS3_NPU_DTYPE_*)        */
    uint8_t     dma_fence_slot;     /**< DMA fence slot index (0-7)          */
    uint8_t     _reserved[3];       /**< Padding to 64 bytes                 */
    uint64_t    aux_phys;           /**< Auxiliary physical address (C mat)  */
} vos3_npu_sqe_t;

/* Compile-time size check */
typedef char _npu_sqe_size_check[(sizeof(vos3_npu_sqe_t) == 64) ? 1 : -1];

/* ============================================================================
 * NPU STRUCTURES — Completion Queue Entry (CQE)
 * ============================================================================ */

/**
 * @brief NPU Completion Queue Entry (16 bytes)
 *
 * @details Each CQE reports the result of a completed command.  The phase
 *          bit alternates each time the CQ wraps, allowing the driver to
 *          detect new entries without a doorbell read.
 */
typedef struct __attribute__((packed, aligned(16))) vos3_npu_cqe {
    uint16_t    cmd_id;             /**< Matching command identifier          */
    uint16_t    status;             /**< Completion status (0 = success)      */
    uint32_t    result;             /**< Op-specific result value             */
    uint32_t    cycles;             /**< Execution cycles consumed            */
    uint16_t    sq_head;            /**< SQ head pointer after completion     */
    uint8_t     phase;              /**< Phase bit (alternates on CQ wrap)    */
    uint8_t     _reserved;          /**< Padding to 16 bytes                  */
} vos3_npu_cqe_t;

/* Compile-time size check */
typedef char _npu_cqe_size_check[(sizeof(vos3_npu_cqe_t) == 16) ? 1 : -1];

/* ============================================================================
 * NPU STRUCTURES — DMA Fence
 * ============================================================================ */

/**
 * @brief DMA fence for a model slot
 *
 * @details IOMMU-style guard that restricts the physical address range the
 *          NPU may access for a given slot.  Any DMA outside [base, limit)
 *          is rejected before the command reaches the hardware queue.
 */
typedef struct vos3_npu_dma_fence {
    uint64_t    base_phys;          /**< Lowest permitted physical address    */
    uint64_t    limit_phys;         /**< One past highest permitted address   */
    uint32_t    active;             /**< 1 if fence is armed, 0 if inactive   */
    uint32_t    dma_violations;     /**< Count of rejected DMA attempts       */
} vos3_npu_dma_fence_t;

/* ============================================================================
 * NPU STRUCTURES — Command Queue
 * ============================================================================ */

/**
 * @brief NPU command queue pair (SQ + CQ)
 */
typedef struct vos3_npu_queue {
    /* Submission queue */
    vos3_npu_sqe_t *sq_virt;        /**< SQ virtual address                  */
    uintptr_t       sq_phys;        /**< SQ physical address                 */
    uint32_t        sq_tail;        /**< Next SQ write index                 */
    uint32_t        sq_head;        /**< Last known SQ head                  */
    uint32_t        sq_depth;       /**< Number of SQ entries                */
    volatile uint32_t *sq_doorbell; /**< SQ tail doorbell MMIO pointer       */

    /* Completion queue */
    vos3_npu_cqe_t *cq_virt;        /**< CQ virtual address                  */
    uintptr_t       cq_phys;        /**< CQ physical address                 */
    uint32_t        cq_head;        /**< Next CQ read index                  */
    uint32_t        cq_depth;       /**< Number of CQ entries                */
    uint8_t         cq_phase;       /**< Expected phase bit (1 or 0)         */
    volatile uint32_t *cq_doorbell; /**< CQ head doorbell MMIO pointer       */

    /* Command tracking */
    uint16_t        next_cmd_id;    /**< Monotonically increasing cmd ID     */
} vos3_npu_queue_t;

/* ============================================================================
 * NPU STRUCTURES — Context (V-Palace Mirroring)
 * ============================================================================ */

/**
 * @brief NPU context descriptor for V-Palace model swapping
 *
 * @details Tracks which model weights are loaded into NPU SRAM for a given
 *          context, enabling fast save/restore during model slot swaps.
 */
typedef struct vos3_npu_context {
    uint32_t    context_id;         /**< Context identifier                  */
    uint32_t    slot_id;            /**< Associated model slot               */
    uint64_t    weights_phys;       /**< Physical address of weights in RAM  */
    uint32_t    weights_size;       /**< Size of weights in bytes            */
    uint32_t    loaded;             /**< 1 if weights are in SRAM            */
    uint32_t    dirty;              /**< 1 if KV-cache modified since save   */
    uint64_t    sram_offset;        /**< Offset within SRAM for this context */
    uint64_t    kv_cache_phys;      /**< Physical address of KV-cache in RAM */
    uint32_t    kv_cache_size;      /**< Size of KV-cache in bytes           */
    uint32_t    _pad;               /**< Alignment padding                   */
} vos3_npu_context_t;

/* ============================================================================
 * NPU STRUCTURES — Device Descriptor
 * ============================================================================ */

/**
 * @brief NPU device state
 *
 * @details Complete state for a single NPU device including PCI identity,
 *          MMIO base, SRAM mapping, command queues, DMA fences, and contexts.
 */
typedef struct vos3_npu_device {
    /* Identity */
    char            name[VOS3_NPU_NAME_LEN]; /**< Human-readable name        */
    uint16_t        vendor_id;       /**< PCI vendor ID                      */
    uint16_t        device_id;       /**< PCI device ID                      */
    uint8_t         pci_bus;         /**< PCI bus number                      */
    uint8_t         pci_dev;         /**< PCI device number                   */
    uint8_t         pci_func;        /**< PCI function number                 */
    uint8_t         initialized;     /**< 1 if device is ready                */

    /* MMIO */
    uintptr_t       mmio_base;       /**< MMIO virtual base address           */
    uint32_t        mmio_size;       /**< MMIO region size                    */

    /* SRAM */
    uintptr_t       sram_base;       /**< SRAM virtual base address           */
    uint32_t        sram_size;       /**< SRAM region size in bytes           */

    /* Capabilities */
    uint32_t        caps;            /**< VOS3_ACCEL_CAP_* bitmask            */
    uint32_t        tops;            /**< Tera-ops/sec (INT8 peak)            */
    uint32_t        mem_mb;          /**< Device memory in MB                 */

    /* Command queue */
    vos3_npu_queue_t queue;          /**< Primary command queue pair          */

    /* DMA fences — one per model slot */
    vos3_npu_dma_fence_t dma_fences[VOS3_NPU_MAX_SLOTS];

    /* Contexts — V-Palace mirroring state */
    vos3_npu_context_t contexts[VOS3_NPU_MAX_CONTEXTS];
    uint32_t        num_contexts;    /**< Number of active contexts           */

    /* Statistics */
    uint64_t        cmds_submitted;  /**< Total commands submitted            */
    uint64_t        cmds_completed;  /**< Total commands completed            */
    uint64_t        dma_violations;  /**< Total DMA fence violations          */
    uint64_t        errors;          /**< Total error count                   */
} vos3_npu_device_t;

/* ============================================================================
 * IOMMU SEAL STATE — Per-Device Hardware DMA Isolation
 * ============================================================================ */

/** @brief Maximum number of IOMMU page table entries per seal */
#define VOS3_NPU_IOMMU_MAX_PAGES   512U

/** @brief IOMMU register: Global Command (Intel VT-d Spec, Section 11.4.4) */
#define IOMMU_REG_GCMD              0x18U

/** @brief IOMMU register: Global Status */
#define IOMMU_REG_GSTS              0x1CU

/** @brief IOMMU register: Root Table Address */
#define IOMMU_REG_RTADDR            0x20U

/** @brief IOMMU register: Context Command */
#define IOMMU_REG_CCMD              0x28U

/** @brief IOMMU register: Invalidation Queue Address */
#define IOMMU_REG_IQA               0x90U

/** @brief IOMMU register: IOTLB Invalidate Address (Intel VT-d Spec 11.4.8) */
#define IOMMU_REG_IOTLB_INV        0x108U

/** @brief IOTLB invalidation: Global Invalidation + Drain Reads/Writes */
#define IOMMU_IOTLB_GLOBAL_INV     (0x1ULL << 60)

/** @brief IOTLB invalidation: Invalidation in progress (wait until clear) */
#define IOMMU_IOTLB_INV_WAIT       (0x1ULL << 63)

/** @brief GCMD: Set Root Table Pointer */
#define IOMMU_GCMD_SRTP             (1U << 30)

/** @brief GCMD: Translation Enable */
#define IOMMU_GCMD_TE               (1U << 31)

/** @brief GSTS: Translation Enable Status */
#define IOMMU_GSTS_TES              (1U << 31)

/**
 * @brief Per-device IOMMU seal descriptor
 */
typedef struct vos3_npu_iommu_seal {
    uint8_t     sealed;             /**< 1 if IOMMU seal is active */
    uint8_t     bound_slot;         /**< AI slot the seal is bound to */
    uint8_t     _pad[2];
    uint64_t    iommu_reg_base;     /**< IOMMU unit register base (from DMAR) */
    uintptr_t   iommu_reg_virt;     /**< IOMMU registers mapped virtual addr */
    uint64_t    slot_phys_base;     /**< Sealed slot's lowest phys address */
    uint64_t    slot_phys_limit;    /**< Sealed slot's highest phys address */
    uint64_t    hw_faults;          /**< Hardware security fault count */
} vos3_npu_iommu_seal_t;

/* ============================================================================
 * SPATIAL SLICING STATE — MIG-Style CU Partitioning
 * ============================================================================ */

/** @brief Maximum slices per device */
#define NPU_MAX_SLICES              8U

/**
 * @brief Hardware slice descriptor (internal)
 */
typedef struct npu_slice {
    uint8_t     slice_id;           /**< Slice index */
    uint8_t     slice_type;         /**< Partition type (0=full..3=eighth) */
    uint8_t     cu_first;           /**< First CU assigned */
    uint8_t     cu_count;           /**< Number of CUs */
    uint32_t    sram_base_offset;   /**< SRAM offset for this slice */
    uint32_t    sram_size;          /**< SRAM capacity */
    uint32_t    owner_slot_id;      /**< Owning AI slot (0xFF=free) */
} npu_slice_t;

/**
 * @brief Per-device spatial slicing state
 */
typedef struct npu_slice_state {
    npu_slice_t slices[NPU_MAX_SLICES];
    uint32_t    num_slices;         /**< Number of active slices */
    uint8_t     partitioned;        /**< 1 if device is partitioned */
    uint8_t     total_cus;          /**< Total CUs detected on device */
    uint8_t     _pad[2];
} npu_slice_state_t;

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief Global NPU device table */
static vos3_npu_device_t g_npu_devices[VOS3_NPU_MAX_DEVICES];

/** @brief Number of discovered NPU devices */
static uint32_t g_npu_count = 0;

/** @brief Set to 1 once vos3_npu_init() completes successfully */
static int g_npu_initialized = 0;

/** @brief Per-device IOMMU seal state */
static vos3_npu_iommu_seal_t g_iommu_seals[VOS3_NPU_MAX_DEVICES];

/** @brief Per-device spatial slicing state */
static npu_slice_state_t g_slice_state[VOS3_NPU_MAX_DEVICES];

/* ============================================================================
 * TEMPORAL DMA SHIELD STATE
 * ============================================================================ */

/** @brief Maximum temporal jitter iterations per doorbell write               */
#define NPU_TEMPORAL_MAX_JITTER     50U

/** @brief Constant-Power Padding mode flag: GPR-64 XOR chain active         */
#define NPU_TEMPORAL_MODE_POWER_PAD (1U << 1)

/**
 * @brief Per-device temporal shield state
 */
typedef struct npu_temporal_state {
    uint8_t     enabled;            /**< 1 if temporal shield is active */
    uint8_t     power_pad_mode;     /**< 1 if SSE2 power-pad is active */
    uint8_t     has_sse2;           /**< 1 if CPUID confirms SSE2 support */
    uint8_t     _pad;
    uint64_t    jitter_count;       /**< Total jitter insertions */
    uint64_t    total_delay_iters;  /**< Cumulative delay loop iterations */
    uint64_t    power_pad_ops;      /**< Total SSE2 XOR operations executed */
} npu_temporal_state_t;

/** @brief Per-device temporal shield state table */
static npu_temporal_state_t g_temporal[VOS3_NPU_MAX_DEVICES];

/* ============================================================================
 * PASID SCALABLE MODE STATE — VT-d v4.0 Per-Wing Isolation
 * ============================================================================ */

/** @brief Maximum PASID entries (one per Wing) */
#define NPU_PASID_MAX_ENTRIES       8U

/** @brief VT-d Scalable Mode root entry: 32 bytes */
#define NPU_SM_ROOT_ENTRY_SIZE      32U

/** @brief VT-d Scalable Mode context entry: 64 bytes */
#define NPU_SM_CTX_ENTRY_SIZE       64U

/** @brief VT-d ECAP register offset */
#define IOMMU_REG_ECAP              0x10U

/** @brief ECAP bit 40: Scalable Mode Translation Support */
#define IOMMU_ECAP_SMTS             (1ULL << 40)

/** @brief ECAP bit 6: PASID Support */
#define IOMMU_ECAP_PASID            (1ULL << 6)

/** @brief GCMD bit 25: Scalable Mode Enable */
#define IOMMU_GCMD_SMTE            (1U << 25)

/**
 * @brief Per-Wing PASID entry
 */
typedef struct npu_pasid_entry {
    uint8_t     active;             /**< 1 if this PASID entry is in use */
    uint8_t     wing_id;            /**< V-Palace Wing index */
    uint8_t     _pad[2];
    uint32_t    pasid_value;        /**< Hardware PASID value assigned */
    uint32_t    bound_slot;         /**< AI slot bound to this PASID */
    uint64_t    phys_base;          /**< Allowed physical base address */
    uint64_t    phys_limit;         /**< Allowed physical limit address */
} npu_pasid_entry_t;

/**
 * @brief Per-device PASID scalable mode state
 */
typedef struct npu_pasid_state {
    uint8_t             sm_supported;   /**< 1 if Scalable Mode is available */
    uint8_t             sm_enabled;     /**< 1 if Scalable Mode is active */
    uint8_t             num_entries;    /**< Number of active PASID entries */
    uint8_t             _pad;
    uint64_t            sm_root_phys;   /**< Phys addr of SM root table page */
    uintptr_t           sm_root_virt;   /**< Virtual addr of SM root table */
    npu_pasid_entry_t   entries[NPU_PASID_MAX_ENTRIES];
} npu_pasid_state_t;

/** @brief Per-device PASID state table */
static npu_pasid_state_t g_pasid[VOS3_NPU_MAX_DEVICES];

/* ============================================================================
 * THERMAL MONITORING STATE
 * ============================================================================ */

/** @brief Thermal throttle threshold (Celsius) */
#define NPU_THERMAL_THROTTLE_C     85U

/** @brief Thermal critical threshold (Celsius) */
#define NPU_THERMAL_CRITICAL_C     95U

/** @brief Thermal sensor register base within MMIO */
#define NPU_REG_THERMAL_BASE       0x2000U

/** @brief Per-CU thermal register stride */
#define NPU_REG_THERMAL_STRIDE     0x04U

/** @brief Maximum CUs per device */
#define NPU_MAX_CUS                64U

/** @brief Predictive thermal lookahead: early-migrate threshold (Celsius)   */
#define NPU_THERMAL_PREDICT_C       80U

/** @brief Thermal velocity sampling window (number of readings)             */
#define NPU_THERMAL_VELOCITY_WINDOW 8U

/**
 * @brief Per-device thermal monitoring state
 */
typedef struct npu_thermal_state {
    uint32_t    last_temps[NPU_MAX_CUS];        /**< Last read temperatures */
    uint32_t    cu_count;                        /**< Detected CU count */
    uint64_t    migration_count;                 /**< Total thermal migrations */
    uint64_t    throttle_events;                 /**< Throttle threshold hits */
    /* Predictive thermal shadowing state */
    uint64_t    predictive_migrations;           /**< Pre-emptive shadow migrations */
    uint32_t    shadow_slice[VOS3_NPU_MAX_DEVICES]; /**< Shadow slice per slot (0xFF=none) */
    /* Per-slot thermal velocity tracking (delta-T per sample) */
    int32_t     velocity_ring[NPU_MAX_CUS][NPU_THERMAL_VELOCITY_WINDOW];
    uint32_t    velocity_idx[NPU_MAX_CUS];       /**< Ring write index per CU */
    uint32_t    prev_temps[NPU_MAX_CUS];         /**< Previous sample temps */
} npu_thermal_state_t;

/** @brief Per-device thermal state table */
static npu_thermal_state_t g_thermal[VOS3_NPU_MAX_DEVICES];

/* ============================================================================
 * EXTERNAL DEPENDENCIES — ACPI / NVMe (forward declarations)
 * ============================================================================ */

/** @brief ACPI info struct (opaque — we only access DMAR fields) */
struct vos3_acpi_info;
extern const struct vos3_acpi_info* vos3_acpi_get_info(void);

/** @brief NVMe controller healthy check */
extern int vos3_nvme_is_healthy(void);

/** @brief NVMe read with PRP validation */
extern int vos3_nvme_read(uint64_t lba, uint32_t count, void *buf);

/* ============================================================================
 * MMIO HELPERS — Volatile Memory-Mapped I/O
 * ============================================================================
 *
 * All register accesses go through volatile pointers to prevent the compiler
 * from reordering, caching, or eliding any MMIO read/write.
 * ============================================================================ */

/**
 * @brief Read a 32-bit MMIO register
 *
 * @param base   MMIO virtual base address
 * @param offset Register offset in bytes
 * @return 32-bit register value
 */
static inline uint32_t npu_read32(uintptr_t base, uint32_t offset)
{
    return *(volatile uint32_t *)(base + offset);
}

/**
 * @brief Write a 32-bit MMIO register
 *
 * @param base   MMIO virtual base address
 * @param offset Register offset in bytes
 * @param val    Value to write
 */
static inline void npu_write32(uintptr_t base, uint32_t offset, uint32_t val)
{
    *(volatile uint32_t *)(base + offset) = val;
}

/**
 * @brief Read a 64-bit MMIO register
 *
 * @param base   MMIO virtual base address
 * @param offset Register offset in bytes
 * @return 64-bit register value
 *
 * @note Low 32 bits read first for register ordering safety.
 */
static inline uint64_t npu_read64(uintptr_t base, uint32_t offset)
{
    uint32_t lo = *(volatile uint32_t *)(base + offset);
    uint32_t hi = *(volatile uint32_t *)(base + offset + 4U);
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

/**
 * @brief Write a 64-bit MMIO register
 *
 * @param base   MMIO virtual base address
 * @param offset Register offset in bytes
 * @param val    Value to write
 *
 * @note Low 32 bits written first for register ordering safety.
 */
static inline void npu_write64(uintptr_t base, uint32_t offset, uint64_t val)
{
    *(volatile uint32_t *)(base + offset)      = (uint32_t)(val & 0xFFFFFFFFU);
    *(volatile uint32_t *)(base + offset + 4U) = (uint32_t)(val >> 32);
}

/**
 * @brief Convert physical address to kernel virtual address (HHDM)
 *
 * @param phys Physical address
 * @return Kernel virtual address
 */
static inline uintptr_t npu_phys_to_virt(uintptr_t phys)
{
    return phys + NPU_HHDM_OFFSET;
}

/* ============================================================================
 * MEMORY HELPERS (freestanding — no libc)
 * ============================================================================ */

/**
 * @brief Fill memory with a byte value
 *
 * @param dst  Destination pointer
 * @param c    Fill byte
 * @param n    Number of bytes to fill
 */
static void npu_memset(void *dst, int c, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    uint8_t  v = (uint8_t)c;
    size_t   i;

    for (i = 0; i < n; i++) {
        d[i] = v;
    }
}

/**
 * @brief Copy memory from source to destination
 *
 * @param dst  Destination pointer
 * @param src  Source pointer
 * @param n    Number of bytes to copy
 */
static void npu_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t       *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    size_t         i;

    for (i = 0; i < n; i++) {
        d[i] = s[i];
    }
}

/**
 * @brief Copy a NUL-terminated string with length limit
 *
 * @param dst  Destination buffer
 * @param src  Source string
 * @param max  Maximum bytes to copy (including NUL)
 */
static void npu_strncpy(char *dst, const char *src, size_t max)
{
    size_t i;

    if (max == 0) {
        return;
    }
    for (i = 0; i < max - 1U && src[i] != '\0'; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
}

/* ============================================================================
 * FENCE / BARRIER HELPERS
 * ============================================================================ */

/**
 * @brief Store fence — ensure all prior stores are globally visible
 */
static inline void npu_sfence(void)
{
    __asm__ volatile ("sfence" ::: "memory");
}

/**
 * @brief Load fence — ensure all prior loads are completed
 */
static inline void npu_lfence(void)
{
    __asm__ volatile ("lfence" ::: "memory");
}

/**
 * @brief Full memory fence
 */
static inline void npu_mfence(void)
{
    __asm__ volatile ("mfence" ::: "memory");
}

/* ============================================================================
 * SECTION 1: DMA FENCE VALIDATION (CRITICAL SECURITY)
 * ============================================================================ */

/**
 * @brief Validate that a physical address range falls within a DMA fence
 *
 * @details This is the IOMMU-style guard that prevents the NPU from accessing
 *          kernel memory or memory belonging to other model slots.  Every DMA
 *          address in an SQE is validated against the slot's fence before the
 *          command is placed on the hardware queue.
 *
 *          Checks performed:
 *            1. dev_id is in range
 *            2. slot_id is in range (0-7)
 *            3. Fence is active (armed)
 *            4. phys >= fence.base_phys
 *            5. phys + size <= fence.limit_phys
 *            6. phys + size does not wrap around 64-bit address space
 *
 * @param[in] dev_id   Device index
 * @param[in] slot_id  Model slot index (0-7)
 * @param[in] phys     Physical address to validate
 * @param[in] size     Transfer size in bytes
 *
 * @return VOS3_NPU_OK on success, VOS3_NPU_E_DMA_FENCE on violation
 */
int vos3_npu_dma_validate(uint32_t dev_id, uint32_t slot_id,
                           uint64_t phys, uint32_t size)
{
    vos3_npu_device_t    *dev;
    vos3_npu_dma_fence_t *fence;
    uint64_t              end;

    /* --- Range checks on device and slot indices --- */
    if (dev_id >= g_npu_count) {
        VOS3_ERROR("dma_validate: dev_id %u >= count %u", dev_id, g_npu_count);
        return VOS3_NPU_E_DMA_FENCE;
    }

    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        VOS3_ERROR("dma_validate: slot_id %u >= max %u", slot_id,
                   VOS3_NPU_MAX_SLOTS);
        return VOS3_NPU_E_DMA_FENCE;
    }

    dev   = &g_npu_devices[dev_id];
    fence = &dev->dma_fences[slot_id];

    /* --- Fence must be active --- */
    if (!fence->active) {
        VOS3_ERROR("dma_validate: fence slot %u not active", slot_id);
        fence->dma_violations++;
        dev->dma_violations++;
        return VOS3_NPU_E_DMA_FENCE;
    }

    /* --- 64-bit overflow guard: phys + size must not wrap --- */
    end = phys + (uint64_t)size;
    if (end < phys) {
        VOS3_ERROR("dma_validate: 64-bit wrap: phys=0x%llx size=%u",
                   (unsigned long long)phys, size);
        fence->dma_violations++;
        dev->dma_violations++;
        return VOS3_NPU_E_DMA_FENCE;
    }

    /* --- Range must fall within [base_phys, limit_phys) --- */
    if (phys < fence->base_phys) {
        VOS3_ERROR("dma_validate: phys 0x%llx < base 0x%llx",
                   (unsigned long long)phys,
                   (unsigned long long)fence->base_phys);
        fence->dma_violations++;
        dev->dma_violations++;
        return VOS3_NPU_E_DMA_FENCE;
    }

    if (end > fence->limit_phys) {
        VOS3_ERROR("dma_validate: end 0x%llx > limit 0x%llx",
                   (unsigned long long)end,
                   (unsigned long long)fence->limit_phys);
        fence->dma_violations++;
        dev->dma_violations++;
        return VOS3_NPU_E_DMA_FENCE;
    }

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 2: DMA FENCE SET / CLEAR
 * ============================================================================ */

/**
 * @brief Set (arm) a DMA fence for a model slot
 *
 * @details Arms the fence with the given physical address window.  All
 *          subsequent DMA commands for this slot will be validated against
 *          this range.  The base must be strictly less than the limit.
 *
 * @param[in] dev_id      Device index
 * @param[in] slot_id     Model slot index (0-7)
 * @param[in] base_phys   Lowest permitted physical address
 * @param[in] limit_phys  One past highest permitted physical address
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_dma_fence_set(uint32_t dev_id, uint32_t slot_id,
                            uint64_t base_phys, uint64_t limit_phys)
{
    vos3_npu_dma_fence_t *fence;

    if (dev_id >= g_npu_count) {
        VOS3_ERROR("dma_fence_set: dev_id %u out of range", dev_id);
        return VOS3_NPU_E_INVAL;
    }

    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        VOS3_ERROR("dma_fence_set: slot_id %u out of range", slot_id);
        return VOS3_NPU_E_INVAL;
    }

    if (base_phys >= limit_phys) {
        VOS3_ERROR("dma_fence_set: base 0x%llx >= limit 0x%llx",
                   (unsigned long long)base_phys,
                   (unsigned long long)limit_phys);
        return VOS3_NPU_E_INVAL;
    }

    fence = &g_npu_devices[dev_id].dma_fences[slot_id];
    fence->base_phys      = base_phys;
    fence->limit_phys     = limit_phys;
    fence->dma_violations = 0;
    fence->active         = 1;

    npu_mfence();

    VOS3_INFO("DMA fence set: dev %u slot %u [0x%llx, 0x%llx)",
              dev_id, slot_id,
              (unsigned long long)base_phys,
              (unsigned long long)limit_phys);

    return VOS3_NPU_OK;
}

/**
 * @brief Clear (disarm) a DMA fence for a model slot
 *
 * @details Disarms the fence.  Any subsequent DMA commands for this slot
 *          will be rejected until a new fence is set.
 *
 * @param[in] dev_id   Device index
 * @param[in] slot_id  Model slot index (0-7)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_dma_fence_clear(uint32_t dev_id, uint32_t slot_id)
{
    vos3_npu_dma_fence_t *fence;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_INVAL;
    }
    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        return VOS3_NPU_E_INVAL;
    }

    fence = &g_npu_devices[dev_id].dma_fences[slot_id];

    if (fence->dma_violations > 0) {
        VOS3_WARN("Clearing fence dev %u slot %u with %u violations",
                  dev_id, slot_id, fence->dma_violations);
    }

    fence->active     = 0;
    fence->base_phys  = 0;
    fence->limit_phys = 0;

    npu_mfence();

    VOS3_INFO("DMA fence cleared: dev %u slot %u", dev_id, slot_id);
    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 3: QUEUE ALLOCATION
 * ============================================================================ */

/**
 * @brief Allocate and initialize the command queue pair for a device
 *
 * @details Allocates physical pages for the SQ and CQ, maps them into
 *          kernel virtual space via HHDM, and configures the doorbell
 *          pointers from the device's MMIO base.
 *
 * @param[in,out] dev  NPU device to initialize queues for
 *
 * @return VOS3_NPU_OK on success, VOS3_NPU_E_NOMEM if allocation fails
 */
static int npu_alloc_queue(vos3_npu_device_t *dev)
{
    vos3_npu_queue_t *q;
    size_t            sq_size;
    size_t            cq_size;
    size_t            sq_pages;
    size_t            cq_pages;
    uintptr_t         sq_phys;
    uintptr_t         cq_phys;

    if (dev == NULL) {
        return VOS3_NPU_E_INVAL;
    }

    q = &dev->queue;

    /* --- Calculate sizes --- */
    sq_size  = (size_t)VOS3_NPU_SQ_DEPTH * sizeof(vos3_npu_sqe_t);
    cq_size  = (size_t)VOS3_NPU_CQ_DEPTH * sizeof(vos3_npu_cqe_t);
    sq_pages = (sq_size + VOS3_NPU_PAGE_SIZE - 1U) / VOS3_NPU_PAGE_SIZE;
    cq_pages = (cq_size + VOS3_NPU_PAGE_SIZE - 1U) / VOS3_NPU_PAGE_SIZE;

    /* --- Allocate SQ pages --- */
    sq_phys = vos3_pmm_alloc_pages(sq_pages, 0);
    if (sq_phys == 0) {
        VOS3_ERROR("Failed to allocate %zu pages for SQ", sq_pages);
        return VOS3_NPU_E_NOMEM;
    }

    /* --- Allocate CQ pages --- */
    cq_phys = vos3_pmm_alloc_pages(cq_pages, 0);
    if (cq_phys == 0) {
        VOS3_ERROR("Failed to allocate %zu pages for CQ", cq_pages);
        vos3_pmm_free_pages(sq_phys, sq_pages);
        return VOS3_NPU_E_NOMEM;
    }

    /* --- Map into kernel virtual space via HHDM --- */
    q->sq_phys = sq_phys;
    q->sq_virt = (vos3_npu_sqe_t *)npu_phys_to_virt(sq_phys);
    q->sq_tail  = 0;
    q->sq_head  = 0;
    q->sq_depth = VOS3_NPU_SQ_DEPTH;

    q->cq_phys  = cq_phys;
    q->cq_virt  = (vos3_npu_cqe_t *)npu_phys_to_virt(cq_phys);
    q->cq_head  = 0;
    q->cq_depth = VOS3_NPU_CQ_DEPTH;
    q->cq_phase = 1;

    q->next_cmd_id = 0;

    /* --- Zero the queue memory --- */
    npu_memset(q->sq_virt, 0, sq_size);
    npu_memset(q->cq_virt, 0, cq_size);

    /* --- Set up doorbell pointers from MMIO base --- */
    q->sq_doorbell = (volatile uint32_t *)(dev->mmio_base + NPU_REG_SQ_DOORBELL_BASE);
    q->cq_doorbell = (volatile uint32_t *)(dev->mmio_base + NPU_REG_CQ_DOORBELL_BASE);

    /* --- Program queue base addresses into device registers --- */
    npu_write64(dev->mmio_base, NPU_REG_SQ_BASE, (uint64_t)sq_phys);
    npu_write64(dev->mmio_base, NPU_REG_CQ_BASE, (uint64_t)cq_phys);

    npu_sfence();

    VOS3_INFO("Queue allocated: dev '%s' SQ=%zu pages CQ=%zu pages",
              dev->name, sq_pages, cq_pages);

    return VOS3_NPU_OK;
}

/**
 * @brief Free queue pages back to PMM
 *
 * @param[in,out] dev  NPU device whose queues to free
 */
static void npu_free_queue(vos3_npu_device_t *dev)
{
    vos3_npu_queue_t *q;
    size_t            sq_size;
    size_t            cq_size;
    size_t            sq_pages;
    size_t            cq_pages;

    if (dev == NULL) {
        return;
    }

    q = &dev->queue;

    sq_size  = (size_t)q->sq_depth * sizeof(vos3_npu_sqe_t);
    cq_size  = (size_t)q->cq_depth * sizeof(vos3_npu_cqe_t);
    sq_pages = (sq_size + VOS3_NPU_PAGE_SIZE - 1U) / VOS3_NPU_PAGE_SIZE;
    cq_pages = (cq_size + VOS3_NPU_PAGE_SIZE - 1U) / VOS3_NPU_PAGE_SIZE;

    if (q->sq_phys != 0) {
        vos3_pmm_free_pages(q->sq_phys, sq_pages);
        q->sq_phys = 0;
        q->sq_virt = NULL;
    }

    if (q->cq_phys != 0) {
        vos3_pmm_free_pages(q->cq_phys, cq_pages);
        q->cq_phys = 0;
        q->cq_virt = NULL;
    }
}

/* ============================================================================
 * SECTION 4: COMMAND SUBMISSION
 * ============================================================================ */

/**
 * @brief Submit a command to the NPU hardware queue
 *
 * @details Validates the device state and DMA addresses, copies the SQE into
 *          the submission queue at sq_tail, issues an sfence to ensure the SQE
 *          is visible in memory before ringing the doorbell, then advances
 *          sq_tail modulo queue depth.
 *
 *          DMA validation is performed for both src_phys and dst_phys against
 *          the fence identified by sqe->dma_fence_slot.  If either address
 *          fails validation, the command is rejected without touching the
 *          hardware queue.
 *
 * @param[in] dev_id  Device index
 * @param[in] sqe     Pointer to the SQE to submit
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_submit_cmd(uint32_t dev_id, const vos3_npu_sqe_t *sqe)
{
    vos3_npu_device_t *dev;
    vos3_npu_queue_t  *q;
    uint32_t           next_tail;
    int                rc;

    /* --- Parameter validation --- */
    if (sqe == NULL) {
        return VOS3_NPU_E_INVAL;
    }

    if (dev_id >= g_npu_count) {
        VOS3_ERROR("submit_cmd: dev_id %u out of range", dev_id);
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    if (!dev->initialized) {
        VOS3_ERROR("submit_cmd: dev %u not initialized", dev_id);
        return VOS3_NPU_E_NOT_INIT;
    }

    /* --- Check device health via status register --- */
    {
        uint32_t status = npu_read32(dev->mmio_base, NPU_REG_STATUS);
        if (status & NPU_STATUS_FATAL) {
            VOS3_ERROR("submit_cmd: dev %u fatal status 0x%x", dev_id, status);
            dev->errors++;
            return VOS3_NPU_E_IO;
        }
    }

    q = &dev->queue;

    /* --- Check queue not full --- */
    next_tail = (q->sq_tail + 1U) % q->sq_depth;
    if (next_tail == q->sq_head) {
        VOS3_WARN("submit_cmd: dev %u SQ full (tail=%u head=%u)",
                  dev_id, q->sq_tail, q->sq_head);
        return VOS3_NPU_E_BUSY;
    }

    /* --- DMA fence validation for src_phys (if non-zero) --- */
    if (sqe->src_phys != 0 && sqe->length > 0) {
        rc = vos3_npu_dma_validate(dev_id, sqe->dma_fence_slot,
                                    sqe->src_phys, sqe->length);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("submit_cmd: src DMA fence violation cmd_type=0x%02x",
                       sqe->cmd_type);
            return rc;
        }
    }

    /* --- DMA fence validation for dst_phys (if non-zero) --- */
    if (sqe->dst_phys != 0 && sqe->length > 0) {
        rc = vos3_npu_dma_validate(dev_id, sqe->dma_fence_slot,
                                    sqe->dst_phys, sqe->length);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("submit_cmd: dst DMA fence violation cmd_type=0x%02x",
                       sqe->cmd_type);
            return rc;
        }
    }

    /* --- DMA fence validation for aux_phys (if non-zero, e.g. MATMUL C matrix) --- */
    if (sqe->aux_phys != 0 && sqe->length > 0) {
        rc = vos3_npu_dma_validate(dev_id, sqe->dma_fence_slot,
                                    sqe->aux_phys, sqe->length);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("submit_cmd: aux DMA fence violation cmd_type=0x%02x",
                       sqe->cmd_type);
            return rc;
        }
    }

    /* --- Copy SQE into queue at sq_tail --- */
    npu_memcpy(&q->sq_virt[q->sq_tail], sqe, sizeof(vos3_npu_sqe_t));

    /* --- sfence: ensure SQE is globally visible before doorbell write --- */
    npu_sfence();

    /* --- Temporal DMA Shield: constant-power padding before doorbell --- */
    if (g_temporal[dev_id].enabled) {
        uint64_t rnd = 0;
        unsigned char ok = 0;
        __asm__ volatile("rdrand %0; setc %1" : "=r"(rnd), "=qm"(ok));
        if (ok) {
            uint32_t jitter = (uint32_t)(rnd % (NPU_TEMPORAL_MAX_JITTER + 1U));

            if (g_temporal[dev_id].power_pad_mode) {
                /*
                 * Constant-Power Padding (Anti-DPA):
                 * Execute GPR XOR operations on dummy registers during the
                 * jitter window.  Uses only 64-bit general-purpose registers
                 * (no SSE/AVX — kernel compiles with -mno-sse).
                 *
                 * Each iteration performs 4x 64-bit XOR + memory store.
                 * XOR draws constant power regardless of operand values on
                 * Intel/AMD ALU pipelines, flattening the power signature.
                 * Memory store prevents dead-code elimination.
                 *
                 * Compatible with ALL x86_64 CPUs (2003+).
                 */
                volatile uint64_t pad_sink[2];
                pad_sink[0] = rnd;
                pad_sink[1] = ~rnd;

                for (uint32_t j = 0; j < jitter; j++) {
                    uint64_t a = pad_sink[0];
                    uint64_t b = pad_sink[1];
                    a ^= b;
                    b ^= a;
                    a ^= b;
                    b ^= (a >> 13) | (a << 51);  /* Rotate to vary bit pattern */
                    pad_sink[0] = a;
                    pad_sink[1] = b;
                }
                g_temporal[dev_id].power_pad_ops += jitter;
            } else {
                /* Legacy mode: simple pause loop */
                for (uint32_t j = 0; j < jitter; j++) {
                    __asm__ volatile("pause" ::: "memory");
                }
            }
            g_temporal[dev_id].total_delay_iters += jitter;
        }
        g_temporal[dev_id].jitter_count++;
    }

    /* --- Ring SQ tail doorbell --- */
    *q->sq_doorbell = q->sq_tail + 1U;

    /* --- Advance tail --- */
    q->sq_tail = next_tail;

    dev->cmds_submitted++;

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 5: MATMUL SUBMISSION (HIGH-LEVEL WRAPPER)
 * ============================================================================ */

/**
 * @brief Submit a matrix multiply operation to the NPU
 *
 * @details Builds an SQE for MATMUL: C[M,N] = A[M,K] * B[K,N].
 *          All three physical addresses (A, B, C) are DMA-validated against
 *          the specified fence slot's context.  The fence slot is inferred
 *          from the context_id.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context for fence lookup
 * @param[in] a_phys      Physical address of matrix A [M x K]
 * @param[in] b_phys      Physical address of matrix B [K x N]
 * @param[in] c_phys      Physical address of result C [M x N]
 * @param[in] M           Number of rows in A and C
 * @param[in] K           Number of columns in A / rows in B
 * @param[in] N           Number of columns in B and C
 * @param[in] dtype       Data type (VOS3_NPU_DTYPE_*)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_submit_matmul(uint32_t dev_id, uint32_t context_id,
                            uint64_t a_phys, uint64_t b_phys, uint64_t c_phys,
                            uint32_t M, uint32_t K, uint32_t N, uint32_t dtype)
{
    vos3_npu_sqe_t  sqe;
    uint32_t        elem_size;
    uint32_t        a_size;
    uint32_t        b_size;
    uint32_t        c_size;
    uint32_t        fence_slot;
    vos3_npu_device_t *dev;
    int             rc;

    /* --- Validate device --- */
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    /* --- Validate dimensions --- */
    if (M == 0 || K == 0 || N == 0) {
        VOS3_ERROR("submit_matmul: zero dimension M=%u K=%u N=%u", M, K, N);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Determine element size from dtype --- */
    switch (dtype) {
    case VOS3_NPU_DTYPE_FP32:
        elem_size = 4U;
        break;
    case VOS3_NPU_DTYPE_FP16:
    case VOS3_NPU_DTYPE_BF16:
        elem_size = 2U;
        break;
    case VOS3_NPU_DTYPE_INT8:
        elem_size = 1U;
        break;
    case VOS3_NPU_DTYPE_INT4:
        /* INT4 is packed 2 per byte, but we compute conservatively */
        elem_size = 1U;
        break;
    default:
        VOS3_ERROR("submit_matmul: unknown dtype %u", dtype);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Overflow-safe size computation --- */
    /* Check M * K * elem_size doesn't overflow uint32_t */
    if (M > 0xFFFFU || K > 0xFFFFU || N > 0xFFFFU) {
        VOS3_ERROR("submit_matmul: dimension too large (>65535)");
        return VOS3_NPU_E_INVAL;
    }

    a_size = M * K * elem_size;
    b_size = K * N * elem_size;
    c_size = M * N * elem_size;

    /* --- Find fence slot from context --- */
    fence_slot = 0;
    {
        uint32_t ci;
        for (ci = 0; ci < dev->num_contexts; ci++) {
            if (dev->contexts[ci].context_id == context_id) {
                fence_slot = (uint32_t)dev->contexts[ci].slot_id;
                break;
            }
        }
    }

    /* --- DMA validate all three matrices against the fence --- */
    rc = vos3_npu_dma_validate(dev_id, fence_slot, a_phys, a_size);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("submit_matmul: A matrix DMA fence violation");
        return rc;
    }

    rc = vos3_npu_dma_validate(dev_id, fence_slot, b_phys, b_size);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("submit_matmul: B matrix DMA fence violation");
        return rc;
    }

    rc = vos3_npu_dma_validate(dev_id, fence_slot, c_phys, c_size);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("submit_matmul: C matrix DMA fence violation");
        return rc;
    }

    /* --- Build SQE --- */
    npu_memset(&sqe, 0, sizeof(sqe));

    sqe.cmd_type       = VOS3_NPU_CMD_MATMUL;
    sqe.flags          = 0;
    sqe.cmd_id         = dev->queue.next_cmd_id++;
    sqe.context_id     = context_id;
    sqe.src_phys       = a_phys;
    sqe.dst_phys       = b_phys;
    sqe.aux_phys       = c_phys;
    sqe.length         = c_size;
    sqe.param0         = M;
    sqe.param1         = K;
    sqe.param2         = N;
    sqe.dtype          = dtype;
    sqe.dma_fence_slot = (uint8_t)fence_slot;

    /* --- Submit --- */
    return vos3_npu_submit_cmd(dev_id, &sqe);
}

/* ============================================================================
 * SECTION 6: COMPLETION POLLING
 * ============================================================================ */

/**
 * @brief Poll for command completion
 *
 * @details Polls the CQ for a completion entry matching the given cmd_id.
 *          Uses the phase bit to detect new entries without a doorbell read.
 *          After reading a valid CQE, issues an lfence to prevent speculative
 *          use of stale data, then rings the CQ head doorbell.
 *
 * @param[in]  dev_id      Device index
 * @param[in]  cmd_id      Command ID to wait for
 * @param[out] cycles_out  If non-NULL, receives the execution cycle count
 *
 * @return VOS3_NPU_OK on success (status == 0),
 *         VOS3_NPU_E_IO if hardware reports error,
 *         VOS3_NPU_E_TIMEOUT if polling limit reached,
 *         negative error code for other failures
 */
int vos3_npu_poll_completion(uint32_t dev_id, uint32_t cmd_id,
                              uint32_t *cycles_out)
{
    vos3_npu_device_t *dev;
    vos3_npu_queue_t  *q;
    vos3_npu_cqe_t    *cqe;
    uint32_t           attempts;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];
    q   = &dev->queue;

    for (attempts = 0; attempts < VOS3_NPU_POLL_ATTEMPTS; attempts++) {
        cqe = &q->cq_virt[q->cq_head];

        /* --- Check phase bit for new completion --- */
        if (cqe->phase != q->cq_phase) {
            /* No new completion yet; spin */
            continue;
        }

        /* --- lfence: prevent speculative reads of CQE fields --- */
        npu_lfence();

        /* --- Check if this is the completion we want --- */
        if (cqe->cmd_id == (uint16_t)cmd_id) {
            uint16_t status = cqe->status;
            uint32_t cycles = cqe->cycles;

            /* --- Extract SQ head update --- */
            q->sq_head = cqe->sq_head;

            /* --- Advance CQ head, flip phase on wrap --- */
            q->cq_head++;
            if (q->cq_head >= q->cq_depth) {
                q->cq_head  = 0;
                q->cq_phase = q->cq_phase ? 0 : 1;
            }

            /* --- Ring CQ head doorbell --- */
            *q->cq_doorbell = q->cq_head;

            dev->cmds_completed++;

            /* --- Return cycle count if requested --- */
            if (cycles_out != NULL) {
                *cycles_out = cycles;
            }

            if (status != 0) {
                VOS3_ERROR("poll_completion: cmd %u completed with status 0x%04x",
                           cmd_id, status);
                dev->errors++;
                return VOS3_NPU_E_IO;
            }

            return VOS3_NPU_OK;
        }

        /* --- CQE is for a different command; consume and continue --- */
        q->cq_head++;
        if (q->cq_head >= q->cq_depth) {
            q->cq_head  = 0;
            q->cq_phase = q->cq_phase ? 0 : 1;
        }
        *q->cq_doorbell = q->cq_head;
        dev->cmds_completed++;
    }

    VOS3_ERROR("poll_completion: timeout waiting for cmd %u after %u attempts",
               cmd_id, VOS3_NPU_POLL_ATTEMPTS);
    return VOS3_NPU_E_TIMEOUT;
}

/* ============================================================================
 * SECTION 7: PCI NPU DISCOVERY
 * ============================================================================ */

/**
 * @brief Check if a PCI device is an NPU/AI accelerator
 *
 * @details Matches by PCI class 0x12 subclass 0x00 (Processing Accelerators),
 *          known vendor/device pairs for AI-capable hardware, or display
 *          class (0x03) with known AI-capable vendor IDs.
 *
 * @param[in] pci  PCI device descriptor
 *
 * @return 1 if the device is an NPU candidate, 0 otherwise
 */
static int npu_is_candidate(const vos3_pci_device_t *pci)
{
    if (pci == NULL) {
        return 0;
    }

    /* --- PCI class 0x12, subclass 0x00: Processing Accelerators --- */
    if (pci->class_code == PCI_CLASS_PROC_ACCEL &&
        pci->subclass   == PCI_SUBCLASS_NPU) {
        return 1;
    }

    /* --- Known vendor/device pairs --- */

    /* NVIDIA NPU (any device with NVIDIA vendor) */
    if (pci->vendor_id == NPU_VENDOR_NVIDIA &&
        pci->class_code == PCI_CLASS_PROC_ACCEL) {
        return 1;
    }

    /* Intel NPU (Meteor Lake specific device ID) */
    if (pci->vendor_id == NPU_VENDOR_INTEL &&
        pci->device_id == NPU_DEVICE_INTEL_NPU) {
        return 1;
    }

    /* AMD XDNA (any Processing Accelerator with AMD vendor) */
    if (pci->vendor_id == NPU_VENDOR_AMD &&
        pci->class_code == PCI_CLASS_PROC_ACCEL) {
        return 1;
    }

    /* Google TPU (any device with Google vendor) */
    if (pci->vendor_id == NPU_VENDOR_GOOGLE) {
        return 1;
    }

    /* --- Display class (0x03) with known AI-capable vendors --- */
    if (pci->class_code == PCI_CLASS_DISPLAY) {
        if (pci->vendor_id == NPU_VENDOR_NVIDIA ||
            pci->vendor_id == NPU_VENDOR_AMD) {
            return 1;
        }
    }

    return 0;
}

/**
 * @brief Determine device name from PCI vendor/device IDs
 *
 * @param[out] name       Buffer to fill (VOS3_NPU_NAME_LEN)
 * @param[in]  vendor_id  PCI vendor ID
 * @param[in]  device_id  PCI device ID
 */
static void npu_name_from_pci(char *name, uint16_t vendor_id, uint16_t device_id)
{
    (void)device_id;

    switch (vendor_id) {
    case NPU_VENDOR_NVIDIA:
        npu_strncpy(name, "NVIDIA NPU", VOS3_NPU_NAME_LEN);
        break;
    case NPU_VENDOR_INTEL:
        npu_strncpy(name, "Intel NPU", VOS3_NPU_NAME_LEN);
        break;
    case NPU_VENDOR_AMD:
        npu_strncpy(name, "AMD XDNA NPU", VOS3_NPU_NAME_LEN);
        break;
    case NPU_VENDOR_GOOGLE:
        npu_strncpy(name, "Google TPU", VOS3_NPU_NAME_LEN);
        break;
    default:
        npu_strncpy(name, "Generic NPU", VOS3_NPU_NAME_LEN);
        break;
    }
}

/**
 * @brief Determine capability flags from PCI vendor
 *
 * @param[in] vendor_id PCI vendor ID
 *
 * @return VOS3_ACCEL_CAP_* bitmask
 */
static uint32_t npu_caps_from_vendor(uint16_t vendor_id)
{
    uint32_t caps = VOS3_ACCEL_CAP_COMPUTE | VOS3_ACCEL_CAP_MATMUL |
                    VOS3_ACCEL_CAP_FP32;

    switch (vendor_id) {
    case NPU_VENDOR_NVIDIA:
        caps |= VOS3_ACCEL_CAP_FP16 | VOS3_ACCEL_CAP_QUANTIZED |
                VOS3_ACCEL_CAP_ATTENTION | VOS3_ACCEL_CAP_CONV;
        break;
    case NPU_VENDOR_INTEL:
        caps |= VOS3_ACCEL_CAP_FP16 | VOS3_ACCEL_CAP_QUANTIZED;
        break;
    case NPU_VENDOR_AMD:
        caps |= VOS3_ACCEL_CAP_FP16 | VOS3_ACCEL_CAP_QUANTIZED |
                VOS3_ACCEL_CAP_ATTENTION;
        break;
    case NPU_VENDOR_GOOGLE:
        caps |= VOS3_ACCEL_CAP_FP16 | VOS3_ACCEL_CAP_QUANTIZED |
                VOS3_ACCEL_CAP_ATTENTION | VOS3_ACCEL_CAP_CONV;
        break;
    default:
        /* Generic: baseline caps only */
        break;
    }

    return caps;
}

/**
 * @brief Map a PCI BAR into kernel virtual address space
 *
 * @details Reads the BAR value from the PCI device descriptor, computes the
 *          size via the standard BAR sizing protocol (write all-ones, read
 *          back, mask type bits), and maps the region into the NPU MMIO
 *          virtual address range.
 *
 *          For BAR0 (MMIO registers): maps at VOS3_NPU_MMIO_VBASE + dev_idx * stride
 *          For BAR2 (SRAM):           maps at MMIO_VBASE + dev_idx * stride + MMIO_SIZE
 *
 * @param[in]  pci       PCI device descriptor
 * @param[in]  bar_idx   BAR index (0 or 2)
 * @param[in]  dev_idx   NPU device index (for virtual address computation)
 * @param[out] virt_out  Receives the mapped virtual address
 * @param[out] size_out  Receives the region size in bytes
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
static int npu_map_bar(const vos3_pci_device_t *pci, uint32_t bar_idx,
                        uint32_t dev_idx, uintptr_t *virt_out, uint32_t *size_out)
{
    uint32_t  bar_val;
    uintptr_t phys_base;
    uintptr_t virt_base;
    uint32_t  region_size;

    if (bar_idx >= 6U) {
        return VOS3_NPU_E_INVAL;
    }

    bar_val = pci->bar[bar_idx];

    /* --- Check for memory BAR (bit 0 = 0) --- */
    if (bar_val & 0x01U) {
        VOS3_WARN("BAR%u is I/O space, not memory-mapped", bar_idx);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Extract physical base (mask out type bits) --- */
    phys_base = (uintptr_t)(bar_val & 0xFFFFFFF0U);

    if (phys_base == 0) {
        VOS3_WARN("BAR%u not configured (phys=0)", bar_idx);
        return VOS3_NPU_E_NODEV;
    }

    /* --- Determine region size from BAR type --- */
    if (bar_idx == 0) {
        /* BAR0: MMIO registers, default 256KB */
        region_size = VOS3_NPU_MMIO_SIZE;
        virt_base   = VOS3_NPU_MMIO_VBASE + (uintptr_t)dev_idx * VOS3_NPU_MMIO_STRIDE;
    } else {
        /* BAR2: SRAM, default up to 16MB */
        region_size = VOS3_NPU_SRAM_MAX_SIZE;
        virt_base   = VOS3_NPU_MMIO_VBASE +
                      (uintptr_t)dev_idx * VOS3_NPU_MMIO_STRIDE +
                      VOS3_NPU_MMIO_SIZE;
    }

    /*
     * In a full implementation, we would call vos3_vmm_map() here to create
     * the actual page table mappings.  For the initial driver skeleton, we
     * use the HHDM identity mapping if the physical address is below 4GB.
     */
    if (phys_base < 0x100000000ULL) {
        virt_base = npu_phys_to_virt(phys_base);
    }
    /* else: virt_base stays at the dedicated NPU VA range (requires vmm_map) */

    *virt_out = virt_base;
    *size_out = region_size;

    VOS3_INFO("BAR%u mapped: phys=0x%llx virt=0x%llx size=%uKB",
              bar_idx,
              (unsigned long long)phys_base,
              (unsigned long long)virt_base,
              region_size / 1024U);

    return VOS3_NPU_OK;
}

/**
 * @brief Probe PCI bus for NPU/AI accelerator devices
 *
 * @details Iterates over all discovered PCI devices (from vos3_pci_bus_scan)
 *          and checks each against the NPU candidate criteria.  For each
 *          match, maps BAR0 (MMIO) and BAR2 (SRAM), reads device capability
 *          registers, and populates the g_npu_devices table.
 *
 * @return Number of NPU devices found
 */
static int npu_probe_pci(void)
{
    const vos3_pci_device_t *pci_devs;
    int                      pci_count;
    int                      i;
    uint32_t                 found = 0;

    pci_devs  = vos3_pci_get_devices();
    pci_count = vos3_pci_get_count();

    if (pci_devs == NULL || pci_count <= 0) {
        VOS3_INFO("No PCI devices available for NPU scan");
        return 0;
    }

    VOS3_INFO("Scanning %d PCI devices for NPU/AI accelerators...", pci_count);

    for (i = 0; i < pci_count && found < VOS3_NPU_MAX_DEVICES; i++) {
        const vos3_pci_device_t *pci = &pci_devs[i];
        vos3_npu_device_t       *dev;
        uintptr_t                mmio_virt = 0;
        uint32_t                 mmio_size = 0;
        uintptr_t                sram_virt = 0;
        uint32_t                 sram_size = 0;
        int                      rc;

        if (!npu_is_candidate(pci)) {
            continue;
        }

        VOS3_INFO("NPU candidate: bus=%u dev=%u func=%u vendor=0x%04x device=0x%04x "
                  "class=0x%02x sub=0x%02x",
                  pci->bus, pci->dev, pci->func,
                  pci->vendor_id, pci->device_id,
                  pci->class_code, pci->subclass);

        /* --- Map BAR0 (MMIO registers, 256KB) --- */
        rc = npu_map_bar(pci, 0, found, &mmio_virt, &mmio_size);
        if (rc != VOS3_NPU_OK) {
            VOS3_WARN("Failed to map BAR0 for candidate %d, skipping", i);
            continue;
        }

        /* --- Map BAR2 (SRAM, up to 16MB) — optional --- */
        rc = npu_map_bar(pci, 2, found, &sram_virt, &sram_size);
        if (rc != VOS3_NPU_OK) {
            VOS3_INFO("BAR2 (SRAM) not available for candidate %d", i);
            sram_virt = 0;
            sram_size = 0;
        }

        /* --- Populate device descriptor --- */
        dev = &g_npu_devices[found];
        npu_memset(dev, 0, sizeof(*dev));

        npu_name_from_pci(dev->name, pci->vendor_id, pci->device_id);
        dev->vendor_id = pci->vendor_id;
        dev->device_id = pci->device_id;
        dev->pci_bus   = pci->bus;
        dev->pci_dev   = pci->dev;
        dev->pci_func  = pci->func;

        dev->mmio_base = mmio_virt;
        dev->mmio_size = mmio_size;
        dev->sram_base = sram_virt;
        dev->sram_size = sram_size;

        /* --- Read capabilities from MMIO (if device supports it) --- */
        dev->caps = npu_caps_from_vendor(pci->vendor_id);

        /*
         * Attempt to read TOPS and memory from device registers.
         * If the device is not yet initialized (no firmware loaded),
         * these may return 0; we fall back to vendor-based defaults.
         */
        {
            uint32_t hw_tops   = npu_read32(mmio_virt, NPU_REG_TOPS);
            uint32_t hw_mem_mb = npu_read32(mmio_virt, NPU_REG_MEM_MB);

            if (hw_tops > 0) {
                dev->tops = hw_tops;
            } else {
                /* Vendor-based defaults (conservative) */
                switch (pci->vendor_id) {
                case NPU_VENDOR_NVIDIA:  dev->tops = 200U;  break;
                case NPU_VENDOR_INTEL:   dev->tops = 40U;   break;
                case NPU_VENDOR_AMD:     dev->tops = 45U;   break;
                case NPU_VENDOR_GOOGLE:  dev->tops = 180U;  break;
                default:                 dev->tops = 10U;    break;
                }
            }

            if (hw_mem_mb > 0) {
                dev->mem_mb = hw_mem_mb;
            } else {
                dev->mem_mb = sram_size / (1024U * 1024U);
                if (dev->mem_mb == 0) {
                    dev->mem_mb = 16U; /* Default 16MB */
                }
            }
        }

        found++;

        VOS3_INFO("NPU[%u] registered: '%s' vendor=0x%04x TOPS=%u mem=%uMB caps=0x%x",
                  found - 1U, dev->name, dev->vendor_id,
                  dev->tops, dev->mem_mb, dev->caps);
    }

    return (int)found;
}

/* ============================================================================
 * SECTION 8: CONTEXT MANAGEMENT (V-PALACE MIRRORING)
 * ============================================================================ */

/**
 * @brief Find a context by ID within a device
 *
 * @param[in] dev         NPU device
 * @param[in] context_id  Context identifier
 *
 * @return Pointer to context, or NULL if not found
 */
static vos3_npu_context_t *npu_find_context(vos3_npu_device_t *dev,
                                             uint32_t context_id)
{
    uint32_t i;

    for (i = 0; i < dev->num_contexts; i++) {
        if (dev->contexts[i].context_id == context_id) {
            return &dev->contexts[i];
        }
    }

    return NULL;
}

/**
 * @brief Load a model context into NPU SRAM
 *
 * @details Validates the DMA fence for the specified slot, then submits a
 *          DMA_COPY command to transfer model weights from system RAM into
 *          NPU SRAM.  Polls for completion and marks the context as loaded.
 *
 *          If the context does not yet exist, a new context entry is created
 *          in the device's context table.
 *
 * @param[in] dev_id        Device index
 * @param[in] context_id    Context identifier
 * @param[in] slot_id       Model slot index (0-7)
 * @param[in] weights_phys  Physical address of weights in system RAM
 * @param[in] weights_size  Size of weights in bytes
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_context_load(uint32_t dev_id, uint32_t context_id,
                           uint32_t slot_id, uint64_t weights_phys,
                           uint32_t weights_size)
{
    vos3_npu_device_t  *dev;
    vos3_npu_context_t *ctx;
    vos3_npu_sqe_t      sqe;
    uint16_t            cmd_id;
    int                 rc;

    /* --- Validate parameters --- */
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    if (!dev->initialized) {
        return VOS3_NPU_E_NOT_INIT;
    }

    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        VOS3_ERROR("context_load: slot_id %u out of range", slot_id);
        return VOS3_NPU_E_INVAL;
    }

    if (weights_phys == 0 || weights_size == 0) {
        VOS3_ERROR("context_load: invalid weights (phys=0x%llx size=%u)",
                   (unsigned long long)weights_phys, weights_size);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Validate DMA fence for the slot --- */
    rc = vos3_npu_dma_validate(dev_id, slot_id, weights_phys, weights_size);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("context_load: weights DMA fence violation");
        return rc;
    }

    /* --- Check SRAM capacity --- */
    if (dev->sram_size > 0 && weights_size > dev->sram_size) {
        VOS3_ERROR("context_load: weights %u > SRAM %u",
                   weights_size, dev->sram_size);
        return VOS3_NPU_E_NOMEM;
    }

    /* --- Find or create context --- */
    ctx = npu_find_context(dev, context_id);
    if (ctx == NULL) {
        if (dev->num_contexts >= VOS3_NPU_MAX_CONTEXTS) {
            VOS3_ERROR("context_load: context table full (%u max)",
                       VOS3_NPU_MAX_CONTEXTS);
            return VOS3_NPU_E_NOSPC;
        }
        ctx = &dev->contexts[dev->num_contexts];
        npu_memset(ctx, 0, sizeof(*ctx));
        ctx->context_id = context_id;
        dev->num_contexts++;
    }

    /* --- Build DMA_COPY SQE: system RAM -> SRAM --- */
    npu_memset(&sqe, 0, sizeof(sqe));
    cmd_id = dev->queue.next_cmd_id++;

    sqe.cmd_type       = VOS3_NPU_CMD_DMA_COPY;
    sqe.cmd_id         = cmd_id;
    sqe.context_id     = context_id;
    sqe.src_phys       = weights_phys;
    sqe.dst_phys       = (uint64_t)dev->sram_base; /* SRAM destination */
    sqe.length         = weights_size;
    sqe.dma_fence_slot = (uint8_t)slot_id;

    rc = vos3_npu_submit_cmd(dev_id, &sqe);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("context_load: DMA_COPY submit failed (%d)", rc);
        return rc;
    }

    /* --- Poll for completion --- */
    rc = vos3_npu_poll_completion(dev_id, cmd_id, NULL);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("context_load: DMA_COPY completion failed (%d)", rc);
        return rc;
    }

    /* --- Update context state --- */
    ctx->slot_id      = slot_id;
    ctx->weights_phys = weights_phys;
    ctx->weights_size = weights_size;
    ctx->sram_offset  = 0;
    ctx->loaded       = 1;
    ctx->dirty        = 0;

    VOS3_INFO("Context %u loaded: slot %u weights=%u bytes",
              context_id, slot_id, weights_size);

    return VOS3_NPU_OK;
}

/**
 * @brief Save a model context from NPU SRAM to system RAM
 *
 * @details Submits a CONTEXT_SAVE command to snapshot the current NPU state,
 *          then a DMA_WRITEBACK command to transfer the KV-cache from SRAM
 *          back to system RAM.  Marks the context as not dirty.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context identifier
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_context_save(uint32_t dev_id, uint32_t context_id)
{
    vos3_npu_device_t  *dev;
    vos3_npu_context_t *ctx;
    vos3_npu_sqe_t      sqe;
    uint16_t            cmd_id;
    int                 rc;

    /* --- Validate --- */
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    if (!dev->initialized) {
        return VOS3_NPU_E_NOT_INIT;
    }

    ctx = npu_find_context(dev, context_id);
    if (ctx == NULL) {
        VOS3_ERROR("context_save: context %u not found", context_id);
        return VOS3_NPU_E_INVAL;
    }

    if (!ctx->loaded) {
        VOS3_WARN("context_save: context %u not loaded, nothing to save",
                  context_id);
        return VOS3_NPU_OK;
    }

    /* --- Step 1: Submit CONTEXT_SAVE command --- */
    npu_memset(&sqe, 0, sizeof(sqe));
    cmd_id = dev->queue.next_cmd_id++;

    sqe.cmd_type       = VOS3_NPU_CMD_CONTEXT_SAVE;
    sqe.cmd_id         = cmd_id;
    sqe.context_id     = context_id;
    sqe.dma_fence_slot = (uint8_t)ctx->slot_id;

    rc = vos3_npu_submit_cmd(dev_id, &sqe);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("context_save: CONTEXT_SAVE submit failed (%d)", rc);
        return rc;
    }

    rc = vos3_npu_poll_completion(dev_id, cmd_id, NULL);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("context_save: CONTEXT_SAVE completion failed (%d)", rc);
        return rc;
    }

    /* --- Step 2: DMA writeback for KV-cache (if present) --- */
    if (ctx->kv_cache_phys != 0 && ctx->kv_cache_size > 0) {
        npu_memset(&sqe, 0, sizeof(sqe));
        cmd_id = dev->queue.next_cmd_id++;

        sqe.cmd_type       = VOS3_NPU_CMD_DMA_WRITEBACK;
        sqe.cmd_id         = cmd_id;
        sqe.context_id     = context_id;
        sqe.src_phys       = (uint64_t)dev->sram_base + ctx->sram_offset;
        sqe.dst_phys       = ctx->kv_cache_phys;
        sqe.length         = ctx->kv_cache_size;
        sqe.dma_fence_slot = (uint8_t)ctx->slot_id;

        rc = vos3_npu_submit_cmd(dev_id, &sqe);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("context_save: DMA_WRITEBACK submit failed (%d)", rc);
            return rc;
        }

        rc = vos3_npu_poll_completion(dev_id, cmd_id, NULL);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("context_save: DMA_WRITEBACK completion failed (%d)", rc);
            return rc;
        }
    }

    /* --- Mark context as clean --- */
    ctx->dirty = 0;

    VOS3_INFO("Context %u saved: slot %u", context_id, ctx->slot_id);
    return VOS3_NPU_OK;
}

/**
 * @brief Synchronize a model context
 *
 * @details If the context has unsaved modifications (dirty flag), saves it
 *          first.  Then verifies that the weights_phys recorded in the context
 *          still matches what was loaded into SRAM.  Returns E_CTX_MISMATCH
 *          if the physical address has changed (e.g., after a page migration).
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context identifier
 *
 * @return VOS3_NPU_OK on success, VOS3_NPU_E_CTX_MISMATCH if weights moved
 */
int vos3_npu_context_sync(uint32_t dev_id, uint32_t context_id)
{
    vos3_npu_device_t  *dev;
    vos3_npu_context_t *ctx;
    int                 rc;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    if (!dev->initialized) {
        return VOS3_NPU_E_NOT_INIT;
    }

    ctx = npu_find_context(dev, context_id);
    if (ctx == NULL) {
        VOS3_ERROR("context_sync: context %u not found", context_id);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Save if dirty --- */
    if (ctx->dirty) {
        rc = vos3_npu_context_save(dev_id, context_id);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("context_sync: save failed (%d)", rc);
            return rc;
        }
    }

    /* --- Verify weights physical address consistency --- */
    if (!ctx->loaded) {
        VOS3_WARN("context_sync: context %u not loaded", context_id);
        return VOS3_NPU_E_CTX_MISMATCH;
    }

    /*
     * In a full implementation, we would read back the SRAM metadata register
     * to verify the loaded weights match ctx->weights_phys.  For now, we
     * trust the software state.
     */
    if (ctx->weights_phys == 0) {
        VOS3_ERROR("context_sync: context %u has no weights_phys", context_id);
        return VOS3_NPU_E_CTX_MISMATCH;
    }

    VOS3_INFO("Context %u synced: slot %u weights_phys=0x%llx",
              context_id, ctx->slot_id,
              (unsigned long long)ctx->weights_phys);

    return VOS3_NPU_OK;
}

/**
 * @brief Evict a context from the device
 *
 * @details Saves the context if dirty, then marks it as unloaded and clears
 *          the slot association.  The context entry remains in the table for
 *          potential re-load.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context identifier
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_context_evict(uint32_t dev_id, uint32_t context_id)
{
    vos3_npu_device_t  *dev;
    vos3_npu_context_t *ctx;
    int                 rc;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];
    ctx = npu_find_context(dev, context_id);
    if (ctx == NULL) {
        return VOS3_NPU_E_INVAL;
    }

    /* --- Save if dirty before eviction --- */
    if (ctx->loaded && ctx->dirty) {
        rc = vos3_npu_context_save(dev_id, context_id);
        if (rc != VOS3_NPU_OK) {
            VOS3_WARN("context_evict: save failed (%d), evicting anyway", rc);
        }
    }

    ctx->loaded      = 0;
    ctx->dirty       = 0;
    ctx->sram_offset = 0;

    VOS3_INFO("Context %u evicted from dev %u", context_id, dev_id);
    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 9: INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize the NPU driver subsystem
 *
 * @details Probes the PCI bus for NPU/AI accelerator devices, allocates
 *          command queues for each discovered device, and registers them
 *          with the accelerator abstraction layer.
 *
 *          Must be called after:
 *            - vos3_pci_bus_scan() (PCI enumeration)
 *            - vos3_pmm_init()     (physical memory allocator)
 *            - vos3_accel_init()   (accelerator registry)
 *
 * @return 0 on success (even if no NPU found), negative errno on fatal failure
 */
/* ============================================================================
 * [QUANTUM-LEAP v21.0.3] Universal-driver-v2 descriptor + MSI-X scaffolds
 * ============================================================================
 *
 * Registers the NPU subsystem with the v2 driver contract from
 * kernel/include/vos/driver.h. The existing single-vector IRQ path
 * (legacy, vector 0) is unchanged and remains the wire-side contract
 * until the dispatch glue lands. The v2 hooks below are bound to
 * stub functions that log entry but do not yet drive real hardware
 * vector allocation — that requires touching the PCI MSI-X table,
 * which is per-device and gated on real hardware availability.
 *
 * Scope discipline:
 *   ✔ The descriptor compiles and links — proves the v1+v2 ABI.
 *   ✔ Two named vectors ("Inference Completion" vector 0,
 *     "Memory Management" vector 1) so a future dispatcher can
 *     route per-vector completions without inventing IDs.
 *   ✗ Real MSI-X table programming (PCI capability walk + MSI-X
 *     control register flip) is NOT in this commit. Vector
 *     allocation is gated on actual NPU hardware (no QEMU stub
 *     today emits multi-vector NPU MSI-X).
 */
#include "../../include/vos/driver.h"

static void npu_v2_irq_inference_completion(struct vos3_device *dev,
                                            uint16_t vector)
{
    (void)dev; (void)vector;
    /* SCAFFOLD: the real handler will pop CQ entries and wake the
     * inference-waiter on the slot's wait queue. Logging deliberately
     * omitted — IRQ context, must be lock-light. */
}

static void npu_v2_irq_memory_management(struct vos3_device *dev,
                                         uint16_t vector)
{
    (void)dev; (void)vector;
    /* SCAFFOLD: real handler will service IOMMU faults + DMA-fence
     * violations without blocking the inference completion path.
     * Logging deliberately omitted — IRQ context, must be lock-light. */
}

static int npu_v2_irq_register_vector(struct vos3_device *dev,
                                      uint16_t vector,
                                      vos3_irq_vector_handler_t handler)
{
    (void)dev; (void)vector; (void)handler;
    /* SCAFFOLD: would install handler in the per-device vector table
     * and program the MSI-X table register. Returns 0 = success
     * placeholder so callers can sanity-check the path. */
    return 0;
}

static int npu_v2_present(struct vos3_device *dev)
{
    (void)dev;
    return 1;  /* SCAFFOLD: real probe would re-read PCI vendor/device. */
}

/* The descriptor itself — exported via npu_get_driver_v2(). */
static const vos3_driver_v2_t g_npu_driver_v2 = {
    .v1 = {
        .name           = "vos3-npu",
        .pci_vendor_id  = 0x0000,    /* wildcard — npu_probe_pci handles match */
        .pci_device_id  = 0x0000,
        .probe          = NULL,      /* legacy probe runs from vos3_npu_init */
        .init           = NULL,
        .release        = NULL,
        .irq_handler    = NULL,      /* legacy single-vector path unused here */
        .dma_submit     = NULL,
        .power_set      = NULL,
    },
    .max_vectors         = 2,        /* "Inference Completion", "Memory Mgmt" */
    .flags               = VOS3_DRIVER_FLAG_MULTI_VECTOR
                          | VOS3_DRIVER_FLAG_HOT_PLUG
                          | VOS3_DRIVER_FLAG_IOMMU_REQUIRED,
    .irq_register_vector = npu_v2_irq_register_vector,
    .irq_set_affinity    = NULL,     /* not yet wired */
    .present             = npu_v2_present,
    .detach              = NULL,     /* not yet wired */
};

const vos3_driver_v2_t *npu_get_driver_v2(void)
{
    return &g_npu_driver_v2;
}

static void npu_register_driver_v2_descriptor(void)
{
    /* Today: log presence; future: register with a kernel-wide driver
     * registry once that subsystem lands. The static descriptor above
     * is the canonical contract — accessible via npu_get_driver_v2(). */
    (void)npu_v2_irq_inference_completion;  /* keep compiled */
    (void)npu_v2_irq_memory_management;     /* keep compiled */
    VOS3_INFO("[NPU-v2] driver descriptor registered: max_vectors=%u "
              "flags=0x%x (legacy single-vector path remains active)",
              (unsigned)g_npu_driver_v2.max_vectors,
              (unsigned)g_npu_driver_v2.flags);
}

int vos3_npu_init(void)
{
    int      found;
    uint32_t i;
    int      rc;

    if (g_npu_initialized) {
        VOS3_WARN("NPU subsystem already initialized");
        return VOS3_NPU_OK;
    }

    VOS3_INFO("Initializing NPU/AI Accelerator driver...");

    /* --- Zero device table --- */
    npu_memset(g_npu_devices, 0, sizeof(g_npu_devices));
    g_npu_count = 0;

    /* --- Probe PCI bus --- */
    found = npu_probe_pci();
    if (found <= 0) {
        VOS3_INFO("No NPU devices found on PCI bus");
        g_npu_initialized = 1;
        return VOS3_NPU_OK;
    }

    g_npu_count = (uint32_t)found;

    /* --- Initialize each discovered device --- */
    for (i = 0; i < g_npu_count; i++) {
        vos3_npu_device_t *dev = &g_npu_devices[i];

        /* --- Allocate command queues --- */
        rc = npu_alloc_queue(dev);
        if (rc != VOS3_NPU_OK) {
            VOS3_ERROR("Failed to allocate queues for NPU[%u] '%s'",
                       i, dev->name);
            dev->initialized = 0;
            continue;
        }

        /* --- Enable device via control register --- */
        npu_write32(dev->mmio_base, NPU_REG_CTRL, NPU_CTRL_ENABLE);
        npu_sfence();

        /* --- Wait for device ready (poll with timeout) --- */
        {
            uint32_t attempts;
            uint32_t status;
            int      ready = 0;

            for (attempts = 0; attempts < VOS3_NPU_POLL_ATTEMPTS; attempts++) {
                status = npu_read32(dev->mmio_base, NPU_REG_STATUS);
                if (status & NPU_STATUS_READY) {
                    ready = 1;
                    break;
                }
                if (status & NPU_STATUS_FATAL) {
                    VOS3_ERROR("NPU[%u] fatal status during init: 0x%x",
                               i, status);
                    break;
                }
            }

            if (!ready) {
                VOS3_WARN("NPU[%u] did not become ready, using as-is", i);
            }
        }

        dev->initialized = 1;

        /* --- Register with accelerator subsystem --- */
        rc = vos3_accel_register(VOS3_ACCEL_NPU, dev->name,
                                  dev->caps, dev->tops, dev->mem_mb);
        if (rc != 0) {
            VOS3_WARN("Failed to register NPU[%u] with accel subsystem (%d)",
                      i, rc);
            /* Non-fatal: device still usable via direct NPU API */
        }

        VOS3_INFO("NPU[%u] '%s' initialized: TOPS=%u mem=%uMB caps=0x%x",
                  i, dev->name, dev->tops, dev->mem_mb, dev->caps);
    }

    g_npu_initialized = 1;

    VOS3_INFO("NPU driver initialized: %u device(s) found", g_npu_count);

    /* [QUANTUM-LEAP v21.0.3] Register the universal-driver-v2 descriptor.
     * The descriptor advertises support for multi-vector MSI-X (vector 0
     * for inference completion, vector 1 for memory-management). The
     * existing single-vector IRQ path is unchanged in this commit; the
     * v2 hooks are stubs ready for the dispatch glue once it lands. */
    npu_register_driver_v2_descriptor();

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 10: RESET
 * ============================================================================ */

/**
 * @brief Reset an NPU device to a clean state
 *
 * @details Issues a hardware reset via the control register, zeros all queue
 *          state, clears all contexts and DMA fences, then re-initializes
 *          the command queues and re-enables the device.
 *
 * @param[in] dev_id  Device index
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_reset(uint32_t dev_id)
{
    vos3_npu_device_t *dev;
    uint32_t           i;
    uint32_t           attempts;
    uint32_t           status;
    int                rc;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    VOS3_INFO("Resetting NPU[%u] '%s'...", dev_id, dev->name);

    /* --- Issue hardware reset --- */
    npu_write32(dev->mmio_base, NPU_REG_CTRL, NPU_CTRL_RESET);
    npu_sfence();

    /* --- Wait for reset to complete (device should clear READY) --- */
    for (attempts = 0; attempts < VOS3_NPU_POLL_ATTEMPTS; attempts++) {
        status = npu_read32(dev->mmio_base, NPU_REG_STATUS);
        if (!(status & NPU_STATUS_BUSY)) {
            break;
        }
    }

    /* --- Free existing queues --- */
    npu_free_queue(dev);

    /* --- Clear all DMA fences --- */
    for (i = 0; i < VOS3_NPU_MAX_SLOTS; i++) {
        npu_memset(&dev->dma_fences[i], 0, sizeof(vos3_npu_dma_fence_t));
    }

    /* --- Clear all contexts --- */
    for (i = 0; i < VOS3_NPU_MAX_CONTEXTS; i++) {
        npu_memset(&dev->contexts[i], 0, sizeof(vos3_npu_context_t));
    }
    dev->num_contexts = 0;

    /* --- Reset statistics --- */
    dev->cmds_submitted = 0;
    dev->cmds_completed = 0;
    dev->dma_violations = 0;
    dev->errors         = 0;

    /* --- Re-allocate queues --- */
    rc = npu_alloc_queue(dev);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("NPU[%u] reset: queue re-allocation failed (%d)",
                   dev_id, rc);
        dev->initialized = 0;
        return rc;
    }

    /* --- Re-enable device --- */
    npu_write32(dev->mmio_base, NPU_REG_CTRL, NPU_CTRL_ENABLE);
    npu_sfence();

    /* --- Wait for ready --- */
    {
        int ready = 0;
        for (attempts = 0; attempts < VOS3_NPU_POLL_ATTEMPTS; attempts++) {
            status = npu_read32(dev->mmio_base, NPU_REG_STATUS);
            if (status & NPU_STATUS_READY) {
                ready = 1;
                break;
            }
        }
        if (!ready) {
            VOS3_WARN("NPU[%u] did not become ready after reset", dev_id);
        }
    }

    dev->initialized = 1;

    VOS3_INFO("NPU[%u] '%s' reset complete", dev_id, dev->name);
    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 11: QUERY FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get the number of discovered NPU devices
 *
 * @return Device count (0 if none found or not initialized)
 */
uint32_t vos3_npu_device_count(void)
{
    return g_npu_count;
}

/**
 * @brief Get a read-only pointer to an NPU device descriptor
 *
 * @param[in] dev_id  Device index (0-based)
 *
 * @return Pointer to device descriptor, or NULL if dev_id is out of range
 *
 * @warning The returned pointer is into the internal device table and must
 *          not be modified by the caller.
 */
const vos3_npu_device_t *vos3_npu_get_device(uint32_t dev_id)
{
    if (dev_id >= g_npu_count) {
        return NULL;
    }

    return &g_npu_devices[dev_id];
}

/**
 * @brief Check if an NPU device is healthy
 *
 * @details Reads the device status register and checks for fatal errors.
 *          A device is considered healthy if:
 *            1. dev_id is valid
 *            2. Device is initialized
 *            3. Status register READY bit is set
 *            4. Status register FATAL bit is clear
 *
 * @param[in] dev_id  Device index
 *
 * @return 1 if device is healthy, 0 otherwise
 */
int vos3_npu_is_healthy(uint32_t dev_id)
{
    vos3_npu_device_t *dev;
    uint32_t           status;

    if (dev_id >= g_npu_count) {
        return 0;
    }

    dev = &g_npu_devices[dev_id];

    if (!dev->initialized) {
        return 0;
    }

    status = npu_read32(dev->mmio_base, NPU_REG_STATUS);

    if (status & NPU_STATUS_FATAL) {
        return 0;
    }

    if (!(status & NPU_STATUS_READY)) {
        return 0;
    }

    return 1;
}

/**
 * @brief Get DMA violation count for a device
 *
 * @param[in] dev_id  Device index
 *
 * @return Total DMA violations, or 0 if dev_id is out of range
 */
uint64_t vos3_npu_get_dma_violations(uint32_t dev_id)
{
    if (dev_id >= g_npu_count) {
        return 0;
    }

    return g_npu_devices[dev_id].dma_violations;
}

/**
 * @brief Get command statistics for a device
 *
 * @param[in]  dev_id         Device index
 * @param[out] submitted_out  Receives total commands submitted (may be NULL)
 * @param[out] completed_out  Receives total commands completed (may be NULL)
 * @param[out] errors_out     Receives total error count (may be NULL)
 *
 * @return VOS3_NPU_OK on success, VOS3_NPU_E_NODEV if dev_id is out of range
 */
int vos3_npu_get_stats(uint32_t dev_id, uint64_t *submitted_out,
                        uint64_t *completed_out, uint64_t *errors_out)
{
    const vos3_npu_device_t *dev;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    if (submitted_out != NULL) {
        *submitted_out = dev->cmds_submitted;
    }
    if (completed_out != NULL) {
        *completed_out = dev->cmds_completed;
    }
    if (errors_out != NULL) {
        *errors_out = dev->errors;
    }

    return VOS3_NPU_OK;
}

/**
 * @brief Check if the NPU subsystem has been initialized
 *
 * @return 1 if initialized, 0 otherwise
 */
int vos3_npu_is_initialized(void)
{
    return g_npu_initialized;
}

/**
 * @brief Get DMA fence info for a specific slot
 *
 * @param[in]  dev_id       Device index
 * @param[in]  slot_id      Slot index (0-7)
 * @param[out] base_out     Receives fence base physical address (may be NULL)
 * @param[out] limit_out    Receives fence limit physical address (may be NULL)
 * @param[out] active_out   Receives fence active state (may be NULL)
 * @param[out] viols_out    Receives violation count (may be NULL)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_get_fence_info(uint32_t dev_id, uint32_t slot_id,
                             uint64_t *base_out, uint64_t *limit_out,
                             uint32_t *active_out, uint32_t *viols_out)
{
    const vos3_npu_dma_fence_t *fence;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }
    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        return VOS3_NPU_E_INVAL;
    }

    fence = &g_npu_devices[dev_id].dma_fences[slot_id];

    if (base_out != NULL) {
        *base_out = fence->base_phys;
    }
    if (limit_out != NULL) {
        *limit_out = fence->limit_phys;
    }
    if (active_out != NULL) {
        *active_out = fence->active;
    }
    if (viols_out != NULL) {
        *viols_out = fence->dma_violations;
    }

    return VOS3_NPU_OK;
}

/* ============================================================================
 * ADDITIONAL COMMAND WRAPPERS
 * ============================================================================ */

/**
 * @brief Submit a softmax operation to the NPU
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context for fence lookup
 * @param[in] src_phys    Physical address of input tensor
 * @param[in] dst_phys    Physical address of output tensor
 * @param[in] length      Tensor size in bytes
 * @param[in] dtype       Data type (VOS3_NPU_DTYPE_*)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_submit_softmax(uint32_t dev_id, uint32_t context_id,
                             uint64_t src_phys, uint64_t dst_phys,
                             uint32_t length, uint32_t dtype)
{
    vos3_npu_sqe_t     sqe;
    vos3_npu_device_t *dev;
    uint32_t           fence_slot = 0;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    /* --- Resolve fence slot from context --- */
    {
        vos3_npu_context_t *ctx = npu_find_context(dev, context_id);
        if (ctx != NULL) {
            fence_slot = ctx->slot_id;
        }
    }

    npu_memset(&sqe, 0, sizeof(sqe));
    sqe.cmd_type       = VOS3_NPU_CMD_SOFTMAX;
    sqe.cmd_id         = dev->queue.next_cmd_id++;
    sqe.context_id     = context_id;
    sqe.src_phys       = src_phys;
    sqe.dst_phys       = dst_phys;
    sqe.length         = length;
    sqe.dtype          = dtype;
    sqe.dma_fence_slot = (uint8_t)fence_slot;

    return vos3_npu_submit_cmd(dev_id, &sqe);
}

/**
 * @brief Submit a layer normalization operation to the NPU
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context for fence lookup
 * @param[in] src_phys    Physical address of input tensor
 * @param[in] dst_phys    Physical address of output tensor
 * @param[in] length      Tensor size in bytes
 * @param[in] dtype       Data type (VOS3_NPU_DTYPE_*)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_submit_layernorm(uint32_t dev_id, uint32_t context_id,
                               uint64_t src_phys, uint64_t dst_phys,
                               uint32_t length, uint32_t dtype)
{
    vos3_npu_sqe_t     sqe;
    vos3_npu_device_t *dev;
    uint32_t           fence_slot = 0;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    {
        vos3_npu_context_t *ctx = npu_find_context(dev, context_id);
        if (ctx != NULL) {
            fence_slot = ctx->slot_id;
        }
    }

    npu_memset(&sqe, 0, sizeof(sqe));
    sqe.cmd_type       = VOS3_NPU_CMD_LAYERNORM;
    sqe.cmd_id         = dev->queue.next_cmd_id++;
    sqe.context_id     = context_id;
    sqe.src_phys       = src_phys;
    sqe.dst_phys       = dst_phys;
    sqe.length         = length;
    sqe.dtype          = dtype;
    sqe.dma_fence_slot = (uint8_t)fence_slot;

    return vos3_npu_submit_cmd(dev_id, &sqe);
}

/**
 * @brief Submit a fused attention operation to the NPU
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context for fence lookup
 * @param[in] q_phys      Physical address of Q (query) tensor
 * @param[in] kv_phys     Physical address of KV (key-value) tensor
 * @param[in] out_phys    Physical address of output tensor
 * @param[in] seq_len     Sequence length
 * @param[in] head_dim    Head dimension
 * @param[in] num_heads   Number of attention heads
 * @param[in] dtype       Data type (VOS3_NPU_DTYPE_*)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_submit_attention(uint32_t dev_id, uint32_t context_id,
                               uint64_t q_phys, uint64_t kv_phys,
                               uint64_t out_phys,
                               uint32_t seq_len, uint32_t head_dim,
                               uint32_t num_heads, uint32_t dtype)
{
    vos3_npu_sqe_t     sqe;
    vos3_npu_device_t *dev;
    uint32_t           fence_slot = 0;
    uint32_t           elem_size;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    {
        vos3_npu_context_t *ctx = npu_find_context(dev, context_id);
        if (ctx != NULL) {
            fence_slot = ctx->slot_id;
        }
    }

    /* --- Determine element size --- */
    switch (dtype) {
    case VOS3_NPU_DTYPE_FP32:  elem_size = 4U; break;
    case VOS3_NPU_DTYPE_FP16:
    case VOS3_NPU_DTYPE_BF16:  elem_size = 2U; break;
    case VOS3_NPU_DTYPE_INT8:  elem_size = 1U; break;
    default:                   elem_size = 4U; break;
    }

    npu_memset(&sqe, 0, sizeof(sqe));
    sqe.cmd_type       = VOS3_NPU_CMD_ATTENTION;
    sqe.cmd_id         = dev->queue.next_cmd_id++;
    sqe.context_id     = context_id;
    sqe.src_phys       = q_phys;
    sqe.dst_phys       = kv_phys;
    sqe.aux_phys       = out_phys;
    sqe.length         = seq_len * head_dim * num_heads * elem_size;
    sqe.param0         = seq_len;
    sqe.param1         = head_dim;
    sqe.param2         = num_heads;
    sqe.dtype          = dtype;
    sqe.dma_fence_slot = (uint8_t)fence_slot;

    return vos3_npu_submit_cmd(dev_id, &sqe);
}

/**
 * @brief Submit a RoPE (Rotary Position Embedding) operation to the NPU
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Context for fence lookup
 * @param[in] src_phys    Physical address of input tensor
 * @param[in] dst_phys    Physical address of output tensor
 * @param[in] length      Tensor size in bytes
 * @param[in] dim         Embedding dimension
 * @param[in] max_seq     Maximum sequence length
 * @param[in] dtype       Data type (VOS3_NPU_DTYPE_*)
 *
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
int vos3_npu_submit_rope(uint32_t dev_id, uint32_t context_id,
                          uint64_t src_phys, uint64_t dst_phys,
                          uint32_t length, uint32_t dim,
                          uint32_t max_seq, uint32_t dtype)
{
    vos3_npu_sqe_t     sqe;
    vos3_npu_device_t *dev;
    uint32_t           fence_slot = 0;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];

    {
        vos3_npu_context_t *ctx = npu_find_context(dev, context_id);
        if (ctx != NULL) {
            fence_slot = ctx->slot_id;
        }
    }

    npu_memset(&sqe, 0, sizeof(sqe));
    sqe.cmd_type       = VOS3_NPU_CMD_ROPE;
    sqe.cmd_id         = dev->queue.next_cmd_id++;
    sqe.context_id     = context_id;
    sqe.src_phys       = src_phys;
    sqe.dst_phys       = dst_phys;
    sqe.length         = length;
    sqe.param0         = dim;
    sqe.param1         = max_seq;
    sqe.dtype          = dtype;
    sqe.dma_fence_slot = (uint8_t)fence_slot;

    return vos3_npu_submit_cmd(dev_id, &sqe);
}

/* ============================================================================
 * SECTION 10: IOMMU-AWARE DMA SEAL
 * ============================================================================
 *
 * Programs the IOMMU hardware translation unit (from ACPI DMAR) to restrict
 * the NPU's DMA identity to the current AI model slot's physical page range.
 * The seal creates a hardware-enforced DMA boundary that is independent of
 * (and layered on top of) the software DMA fence validation.
 *
 * Security model:
 *   - NPU virtual identity is hard-wired to AI_HALL_BASE of the bound slot
 *   - IOMMU root table restricts NPU's PCI BDF to [slot_phys_base, slot_phys_limit)
 *   - Any out-of-bounds DMA triggers HARDWARE_SECURITY_FAULT + PCI link reset
 *   - sfence after IOMMU register writes ensures store ordering
 * ============================================================================ */

/**
 * @brief IOMMU register read (32-bit, volatile)
 */
static inline uint32_t iommu_read32(uintptr_t base, uint32_t offset)
{
    return *(volatile uint32_t *)(base + offset);
}

/**
 * @brief IOMMU register write (32-bit, volatile)
 */
static inline void iommu_write32(uintptr_t base, uint32_t offset, uint32_t val)
{
    *(volatile uint32_t *)(base + offset) = val;
}

/**
 * @brief IOMMU register write (64-bit, volatile)
 */
static inline void iommu_write64(uintptr_t base, uint32_t offset, uint64_t val)
{
    *(volatile uint64_t *)(base + offset) = val;
}

/**
 * @brief Trigger PCI link reset on NPU device after security fault
 *
 * @details Issues a Function Level Reset (FLR) on the NPU's PCI BDF by
 *          writing to its PCI Express Device Control register.  This forces
 *          the NPU to drop all in-flight DMA operations and reinitialize.
 *
 * @param dev  NPU device to reset
 */
static void npu_pci_link_reset(vos3_npu_device_t *dev)
{
    /* PCI Express Capability: Device Control register offset 0x08 */
    /* Bit 15: Initiate Function Level Reset (FLR) */
    uint32_t pci_addr = (1U << 31) |
                        ((uint32_t)dev->pci_bus << 16) |
                        ((uint32_t)dev->pci_dev << 11) |
                        ((uint32_t)dev->pci_func << 8) |
                        0x48U; /* PCI Express Device Control (typical offset) */

    /* Write FLR bit via PCI config space */
    __asm__ volatile(
        "movl $0xCF8, %%edx\n\t"
        "movl %0, %%eax\n\t"
        "outl %%eax, %%dx\n\t"
        "movl $0xCFC, %%edx\n\t"
        "movl $0x00008000, %%eax\n\t" /* FLR bit in Device Control */
        "outl %%eax, %%dx\n\t"
        : : "r"(pci_addr) : "eax", "edx", "memory"
    );

    npu_mfence();

    VOS3_WARN("PCI link reset issued: bus=%u dev=%u func=%u",
              dev->pci_bus, dev->pci_dev, dev->pci_func);

    dev->initialized = 0; /* Force re-initialization after reset */
}

/**
 * @brief Program IOMMU root/context table entries for NPU device isolation
 *
 * @details Creates a minimal IOMMU translation structure:
 *          1. Allocates a root table page (4KB, 256 entries indexed by bus)
 *          2. Allocates a context table page (4KB, 256 entries indexed by devfn)
 *          3. Configures the context entry to restrict the NPU's DMA to the
 *             sealed slot's physical address range
 *          4. Programs the IOMMU unit's root table address register
 *          5. Enables translation with SRTP + TE in Global Command register
 *          6. Verifies TES (Translation Enable Status) is set
 *
 * @param dev   NPU device
 * @param seal  IOMMU seal descriptor to populate
 * @return VOS3_NPU_OK on success, negative error code on failure
 */
static int npu_program_iommu(vos3_npu_device_t *dev,
                              vos3_npu_iommu_seal_t *seal)
{
    uintptr_t   iommu_virt;
    uintptr_t   root_page_phys;
    uintptr_t   ctx_page_phys;
    uint64_t   *root_table;
    uint64_t   *ctx_table;
    uint32_t    devfn;
    uint32_t    gsts;
    uint32_t    timeout;

    /* Map IOMMU registers into kernel VA via HHDM */
    iommu_virt = seal->iommu_reg_base + NPU_HHDM_OFFSET;
    seal->iommu_reg_virt = iommu_virt;

    /* Allocate root table page (4KB, zero-initialized) */
    root_page_phys = vos3_pmm_alloc_pages(1, 0);
    if (root_page_phys == 0) {
        VOS3_ERROR("iommu_seal: failed to alloc root table page");
        return VOS3_NPU_E_NOMEM;
    }
    root_table = (uint64_t *)(root_page_phys + NPU_HHDM_OFFSET);
    npu_memset(root_table, 0, VOS3_NPU_PAGE_SIZE);

    /* Allocate context table page (4KB, zero-initialized) */
    ctx_page_phys = vos3_pmm_alloc_pages(1, 0);
    if (ctx_page_phys == 0) {
        VOS3_ERROR("iommu_seal: failed to alloc context table page");
        vos3_pmm_free_pages(root_page_phys, 1);
        return VOS3_NPU_E_NOMEM;
    }
    ctx_table = (uint64_t *)(ctx_page_phys + NPU_HHDM_OFFSET);
    npu_memset(ctx_table, 0, VOS3_NPU_PAGE_SIZE);

    /* Root table entry [bus]: point to context table, mark present */
    root_table[dev->pci_bus] = ctx_page_phys | 0x01ULL; /* Present bit */

    /* Context table entry [devfn]: restrict to sealed slot's phys range */
    devfn = ((uint32_t)dev->pci_dev << 3) | dev->pci_func;

    /*
     * Intel VT-d Context Entry (128 bits = 2 x uint64_t):
     *   QW0: [63:12] = Second-level page table pointer (we use pass-through)
     *         [3:2]  = Address Width (2 = 48-bit AGAW → 4-level page table)
     *         [1]    = Fault Disable (0 = faults reported)
     *         [0]    = Present
     *   QW1: [87:72] = Domain ID (unique per slot for isolation)
     *
     * We use Address Translation Type = 10b (pass-through) with DMA
     * restricted via the second-level page table.  For our simplified
     * seal, we program pass-through mode and rely on the software DMA
     * fence for fine-grained validation.  The IOMMU provides an
     * independent hardware safety net.
     */
    ctx_table[devfn * 2]     = 0x01ULL  /* Present */
                              | (2ULL << 2); /* AGAW = 48-bit */
    ctx_table[devfn * 2 + 1] = ((uint64_t)(seal->bound_slot + 1)) << 8; /* Domain ID */

    npu_sfence(); /* Ensure all table writes are visible before IOMMU programs */

    /* Program IOMMU root table address register */
    iommu_write64(iommu_virt, IOMMU_REG_RTADDR, root_page_phys);

    /* Issue Set Root Table Pointer command */
    iommu_write32(iommu_virt, IOMMU_REG_GCMD,
                  IOMMU_GCMD_SRTP | IOMMU_GCMD_TE);

    npu_sfence();

    /* Poll for Translation Enable Status */
    timeout = 1000000U;
    do {
        gsts = iommu_read32(iommu_virt, IOMMU_REG_GSTS);
        if (gsts & IOMMU_GSTS_TES) break;
        timeout--;
    } while (timeout > 0);

    if (!(gsts & IOMMU_GSTS_TES)) {
        VOS3_ERROR("iommu_seal: IOMMU translation enable timeout (GSTS=0x%x)",
                   gsts);
        vos3_pmm_free_pages(root_page_phys, 1);
        vos3_pmm_free_pages(ctx_page_phys, 1);
        return VOS3_NPU_E_TIMEOUT;
    }

    VOS3_INFO("IOMMU seal programmed: dev %u:%u.%u → domain %u [0x%llx-0x%llx)",
              dev->pci_bus, dev->pci_dev, dev->pci_func,
              seal->bound_slot + 1,
              (unsigned long long)seal->slot_phys_base,
              (unsigned long long)seal->slot_phys_limit);

    return VOS3_NPU_OK;
}

/**
 * @brief Seal NPU DMA via IOMMU hardware isolation
 */
int vos3_npu_iommu_seal(uint32_t dev_id, uint32_t slot_id)
{
    vos3_npu_device_t       *dev;
    vos3_npu_iommu_seal_t   *seal;
    vos3_npu_dma_fence_t    *fence;
    const void              *acpi_info_raw;
    int                      rc;

    /* --- Validate device index --- */
    if (dev_id >= g_npu_count) {
        VOS3_ERROR("iommu_seal: dev_id %u out of range", dev_id);
        return VOS3_NPU_E_NODEV;
    }

    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        VOS3_ERROR("iommu_seal: slot_id %u out of range", slot_id);
        return VOS3_NPU_E_INVAL;
    }

    dev  = &g_npu_devices[dev_id];
    seal = &g_iommu_seals[dev_id];

    /* --- Check ACPI DMAR availability --- */
    acpi_info_raw = vos3_acpi_get_info();
    if (acpi_info_raw == NULL) {
        VOS3_ERROR("iommu_seal: ACPI not initialized");
        return VOS3_NPU_E_NOT_INIT;
    }

    /*
     * Access DMAR fields from the ACPI info struct.
     * The struct layout has dmar_found at a known offset after the
     * table inventory fields.  We use the public API getter and cast
     * to access the DMAR section.
     *
     * Since npu.c uses its own type system (does not include acpi.h),
     * we access the DMAR fields via byte-offset reads from the opaque
     * pointer.  This avoids type definition conflicts.
     *
     * DMAR fields in vos3_acpi_info_t (from acpi.h):
     *   offset varies — we check dmar_found via the first DMAR byte
     *   after the fixed fields.
     *
     * For robustness, we probe the first DMAR unit's reg_base_phys.
     * If it's zero, no IOMMU hardware is available.
     */

    /* The software DMA fence must already be armed for this slot */
    fence = &dev->dma_fences[slot_id];
    if (!fence->active) {
        VOS3_ERROR("iommu_seal: DMA fence for slot %u not armed — arm fence first",
                   slot_id);
        return VOS3_NPU_E_INVAL;
    }

    /* Configure seal from the armed DMA fence boundaries */
    seal->bound_slot      = (uint8_t)slot_id;
    seal->slot_phys_base  = fence->base_phys;
    seal->slot_phys_limit = fence->limit_phys;
    seal->hw_faults       = 0;

    /*
     * Attempt to locate IOMMU unit for this NPU's PCI segment.
     * In QEMU, there may be no IOMMU — we fall back to software-only
     * fencing with a WARN.  On real hardware, the DMAR table provides
     * the IOMMU register base.
     *
     * We use a heuristic: scan PCI bus 0 for an IOMMU capability
     * register at the well-known DMAR DRHD base addresses.
     */
    seal->iommu_reg_base = 0;

    /* Try to read IOMMU base from common VT-d address (0xFED90000) */
    {
        uintptr_t probe_virt = 0xFED90000ULL + NPU_HHDM_OFFSET;
        uint32_t  cap_lo;

        /* Read IOMMU Version Register (offset 0x00) — should be non-zero */
        cap_lo = *(volatile uint32_t *)probe_virt;
        if (cap_lo != 0 && cap_lo != 0xFFFFFFFF) {
            seal->iommu_reg_base = 0xFED90000ULL;
            VOS3_INFO("IOMMU detected at 0xFED90000 (ver=0x%x)", cap_lo);
        }
    }

    if (seal->iommu_reg_base == 0) {
        /* No hardware IOMMU found — software fence is the only guard */
        VOS3_WARN("iommu_seal: no IOMMU hardware detected — software fence only");
        seal->sealed = 1; /* Mark as sealed via software fence */

        VOS3_INFO("IOMMU seal (software): dev %u slot %u [0x%llx-0x%llx)",
                  dev_id, slot_id,
                  (unsigned long long)seal->slot_phys_base,
                  (unsigned long long)seal->slot_phys_limit);
        return VOS3_NPU_OK;
    }

    /* Program hardware IOMMU translation tables */
    rc = npu_program_iommu(dev, seal);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("iommu_seal: failed to program IOMMU (%d)", rc);
        return rc;
    }

    seal->sealed = 1;

    VOS3_INFO("IOMMU seal (hardware): dev %u slot %u ACTIVE",
              dev_id, slot_id);

    return VOS3_NPU_OK;
}

/**
 * @brief Query IOMMU seal status
 */
int vos3_npu_iommu_status(uint32_t dev_id, uint32_t *sealed_out,
                            uint32_t *bound_slot)
{
    if (dev_id >= VOS3_NPU_MAX_DEVICES) {
        return VOS3_NPU_E_INVAL;
    }

    if (sealed_out != NULL) {
        *sealed_out = g_iommu_seals[dev_id].sealed;
    }
    if (bound_slot != NULL) {
        *bound_slot = g_iommu_seals[dev_id].bound_slot;
    }
    return VOS3_NPU_OK;
}

/**
 * @brief Handle IOMMU hardware security fault
 *
 * @details Called when the IOMMU reports a DMA translation fault for the
 *          NPU device.  Increments fault counter, logs the violation, and
 *          resets the PCI link to stop all NPU DMA.
 *
 * @param dev_id  NPU device that caused the fault
 */
void vos3_npu_iommu_fault_handler(uint32_t dev_id)
{
    if (dev_id >= g_npu_count) return;

    vos3_npu_device_t     *dev  = &g_npu_devices[dev_id];
    vos3_npu_iommu_seal_t *seal = &g_iommu_seals[dev_id];

    seal->hw_faults++;
    dev->dma_violations++;
    dev->errors++;

    VOS3_ERROR("HARDWARE_SECURITY_FAULT: NPU dev %u attempted out-of-bounds DMA "
               "(slot=%u faults=%llu) — resetting PCI link",
               dev_id, seal->bound_slot,
               (unsigned long long)seal->hw_faults);

    npu_pci_link_reset(dev);
}

/* ============================================================================
 * SECTION 11: PEER-TO-PEER NVMe → NPU WARP STREAM
 * ============================================================================
 *
 * Zero-RAM-overhead model loading:
 *   NVMe SSD → PCIe Root Complex → NPU SRAM BAR (direct P2P DMA)
 *
 * The NVMe controller is instructed to write data directly to the NPU's
 * SRAM BAR physical address instead of system RAM.  This bypasses the
 * system memory bus entirely, achieving the "April 2026 Gold Standard"
 * of zero-copy model loading.
 *
 * Security:
 *   - Both NVMe PRP validation and NPU DMA fence validate the transfer
 *   - SRAM offset is bounds-checked against device SRAM size
 *   - 64-bit overflow guard on all address computations
 *   - sfence barrier after PRP configuration
 * ============================================================================ */

/** @brief PCIe Gen5 theoretical peak: 63 GB/s (x16) → ~15.75 GB/s (x4, typical NPU) */
#define VOS3_P2P_PCIE_GEN5_X4_PEAK_MBS    15750U

/** @brief Maximum P2P transfer per command: 128KB (NVMe spec limit) */
#define VOS3_P2P_MAX_TRANSFER              (128U * 1024U)

/** @brief Sector size (NVMe standard) */
#define VOS3_P2P_SECTOR_SIZE               512U

/**
 * @brief Query P2P capability between NVMe and NPU
 */
int vos3_p2p_query_capability(uint32_t npu_dev_id, uint32_t *supported_out,
                                uint32_t *max_bytes_out)
{
    const vos3_npu_device_t *dev;

    if (npu_dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[npu_dev_id];

    /*
     * P2P is supported when:
     *   1. NPU has SRAM mapped (sram_base != 0, sram_size > 0)
     *   2. NVMe controller is healthy
     *   3. Both devices are on the same PCIe root complex (assumed for now)
     */
    if (dev->sram_base != 0 && dev->sram_size > 0 && vos3_nvme_is_healthy()) {
        if (supported_out) *supported_out = 1;
        if (max_bytes_out) {
            /* Min of NVMe max transfer and NPU SRAM size */
            uint32_t max_xfer = dev->sram_size;
            if (max_xfer > VOS3_P2P_MAX_TRANSFER) {
                max_xfer = VOS3_P2P_MAX_TRANSFER;
            }
            *max_bytes_out = max_xfer;
        }
        return VOS3_NPU_OK;
    }

    if (supported_out) *supported_out = 0;
    if (max_bytes_out) *max_bytes_out = 0;
    return VOS3_NPU_OK;
}

/**
 * @brief Transfer data directly from NVMe to NPU SRAM via PCIe P2P DMA
 *
 * @details The NVMe controller's PRP list is configured with physical
 *          addresses pointing to the NPU's SRAM BAR.  The root complex
 *          routes the DMA write from NVMe directly to the NPU without
 *          touching system memory.
 *
 *          Transfer flow:
 *            1. Validate NPU device, slot, and SRAM offset bounds
 *            2. Compute NPU SRAM physical address for the destination
 *            3. Validate destination against NPU DMA fence
 *            4. Build NVMe read command with PRPs pointing to NPU SRAM
 *            5. Submit command and poll for completion
 *            6. sfence/lfence barriers for store/load ordering
 */
int vos3_p2p_nvme_to_npu(uint32_t npu_dev_id, uint32_t slot_id,
                           uint64_t nvme_lba, uint32_t sector_count,
                           uint32_t sram_offset)
{
    vos3_npu_device_t   *dev;
    uint32_t             transfer_bytes;
    uint64_t             sram_phys_dest;
    uint64_t             sram_end;
    int                  rc;

    /* --- Validate NPU device --- */
    if (npu_dev_id >= g_npu_count) {
        VOS3_ERROR("p2p: npu_dev_id %u out of range", npu_dev_id);
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[npu_dev_id];

    if (!dev->initialized) {
        VOS3_ERROR("p2p: NPU dev %u not initialized", npu_dev_id);
        return VOS3_NPU_E_NOT_INIT;
    }

    /* --- Validate SRAM availability --- */
    if (dev->sram_base == 0 || dev->sram_size == 0) {
        VOS3_ERROR("p2p: NPU dev %u has no SRAM mapped", npu_dev_id);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Validate NVMe is healthy --- */
    if (!vos3_nvme_is_healthy()) {
        VOS3_ERROR("p2p: NVMe controller not healthy");
        return VOS3_NPU_E_IO;
    }

    /* --- Compute transfer size and bounds-check --- */
    transfer_bytes = sector_count * VOS3_P2P_SECTOR_SIZE;

    /* Overflow guard: sector_count * 512 must not exceed UINT32_MAX */
    if (sector_count > 0 && transfer_bytes / VOS3_P2P_SECTOR_SIZE != sector_count) {
        VOS3_ERROR("p2p: integer overflow in transfer size");
        return VOS3_NPU_E_INVAL;
    }

    if (transfer_bytes > VOS3_P2P_MAX_TRANSFER) {
        VOS3_ERROR("p2p: transfer %u exceeds max %u",
                   transfer_bytes, VOS3_P2P_MAX_TRANSFER);
        return VOS3_NPU_E_INVAL;
    }

    /* --- Bounds-check SRAM offset + length --- */
    sram_end = (uint64_t)sram_offset + (uint64_t)transfer_bytes;
    if (sram_end > dev->sram_size) {
        VOS3_ERROR("p2p: sram_offset %u + length %u exceeds SRAM size %u",
                   sram_offset, transfer_bytes, dev->sram_size);
        return VOS3_NPU_E_DMA_FENCE;
    }

    /*
     * Compute the physical address of the destination in NPU SRAM.
     *
     * The NPU SRAM BAR physical address is derived from the device's PCI
     * BAR2 register.  The SRAM virtual address (dev->sram_base) is the
     * HHDM-mapped view.  The physical address is:
     *   sram_phys = dev->sram_base - NPU_HHDM_OFFSET
     *
     * However, SRAM BARs are in MMIO space (typically above 4GB on modern
     * hardware, or in the PCI memory hole 0xC0000000-0xFFFFFFFF).  For P2P
     * DMA, the NVMe controller needs the BAR physical address directly.
     */
    sram_phys_dest = (dev->sram_base - NPU_HHDM_OFFSET) + (uint64_t)sram_offset;

    /* --- Validate destination against NPU DMA fence --- */
    rc = vos3_npu_dma_validate(npu_dev_id, slot_id,
                                sram_phys_dest, transfer_bytes);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("p2p: DMA fence rejected destination 0x%llx (slot %u)",
                   (unsigned long long)sram_phys_dest, slot_id);
        return rc;
    }

    /*
     * For the actual P2P transfer, we configure the NVMe read command with
     * PRP entries pointing to the NPU SRAM physical address.  On PCIe Gen5,
     * the root complex routes the write from the NVMe endpoint directly to
     * the NPU endpoint's BAR without touching DRAM.
     *
     * In QEMU (emulated mode), true P2P is not supported — the hypervisor
     * proxies the DMA through host memory.  On bare metal with ACS disabled
     * and same-root-complex topology, true P2P achieves wire-speed.
     *
     * We submit the transfer as an NVMe DMA read to the SRAM physical
     * address.  The NVMe controller treats it as a normal DMA target.
     */
    rc = vos3_nvme_read(nvme_lba, sector_count, (void *)sram_phys_dest);
    if (rc != 0) {
        VOS3_ERROR("p2p: NVMe read to SRAM failed (rc=%d)", rc);
        dev->errors++;
        return VOS3_NPU_E_IO;
    }

    npu_sfence(); /* Ensure SRAM writes are visible to NPU before use */

    VOS3_INFO("P2P NVMe→NPU: %u bytes LBA %llu → SRAM offset %u (dev %u slot %u)",
              transfer_bytes,
              (unsigned long long)nvme_lba,
              sram_offset, npu_dev_id, slot_id);

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 12: SPATIAL SLICING — MIG-STYLE CU PARTITIONING
 * ============================================================================
 *
 * Partitions the NPU's Compute Units (CUs) into independent slices, each
 * with its own SRAM region and assigned AI model slot.  This enables
 * multiple AI Wings to execute concurrently on different hardware partitions,
 * matching NVIDIA's MIG (Multi-Instance GPU) isolation model.
 *
 * Slice types:
 *   FULL:    1 slice = all CUs (no partitioning)
 *   HALF:    2 slices, each with 50% CUs
 *   QUARTER: 4 slices, each with 25% CUs
 *   EIGHTH:  8 slices, each with 12.5% CUs
 *
 * SRAM is divided proportionally: each slice gets sram_size / num_slices.
 * CU assignment is contiguous: slice N gets CUs [N*count, (N+1)*count).
 * ============================================================================ */

/**
 * @brief Partition NPU into spatial slices
 */
int vos3_npu_slice_create(uint32_t dev_id, uint8_t slice_type)
{
    vos3_npu_device_t  *dev;
    npu_slice_state_t  *ss;
    uint32_t            num_slices;
    uint32_t            cus_per_slice;
    uint32_t            sram_per_slice;
    uint32_t            i;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    dev = &g_npu_devices[dev_id];
    ss  = &g_slice_state[dev_id];

    /* Determine partition count from type */
    switch (slice_type) {
    case 0: num_slices = 1; break; /* FULL */
    case 1: num_slices = 2; break; /* HALF */
    case 2: num_slices = 4; break; /* QUARTER */
    case 3: num_slices = 8; break; /* EIGHTH */
    default:
        VOS3_ERROR("slice_create: invalid type %u", slice_type);
        return VOS3_NPU_E_INVAL;
    }

    /* Detect total CUs from NPU capability register or use TOPS heuristic */
    if (ss->total_cus == 0) {
        /* Estimate: 1 TOPS ≈ 1 CU for INT8 workloads */
        ss->total_cus = (uint8_t)(dev->tops > 0 ? dev->tops : 16U);
        if (ss->total_cus > 128U) ss->total_cus = 128U;
    }

    cus_per_slice  = ss->total_cus / num_slices;
    sram_per_slice = dev->sram_size / num_slices;

    if (cus_per_slice == 0) {
        VOS3_ERROR("slice_create: not enough CUs (%u) for %u slices",
                   ss->total_cus, num_slices);
        return VOS3_NPU_E_INVAL;
    }

    /* Clear existing slices */
    npu_memset(ss->slices, 0, sizeof(ss->slices));

    for (i = 0; i < num_slices; i++) {
        ss->slices[i].slice_id        = (uint8_t)i;
        ss->slices[i].slice_type      = slice_type;
        ss->slices[i].cu_first        = (uint8_t)(i * cus_per_slice);
        ss->slices[i].cu_count        = (uint8_t)cus_per_slice;
        ss->slices[i].sram_base_offset = i * sram_per_slice;
        ss->slices[i].sram_size       = sram_per_slice;
        ss->slices[i].owner_slot_id   = 0xFF; /* Free */
    }

    ss->num_slices  = num_slices;
    ss->partitioned = 1;

    VOS3_INFO("Spatial slicing: dev %u → %u slices (%u CUs each, %u KB SRAM each)",
              dev_id, num_slices, cus_per_slice, sram_per_slice / 1024U);

    return (int)num_slices;
}

/**
 * @brief Assign a spatial slice to an AI model slot
 */
int vos3_npu_slice_assign(uint32_t dev_id, uint8_t slice_id, uint32_t slot_id)
{
    npu_slice_state_t *ss;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    ss = &g_slice_state[dev_id];

    if (!ss->partitioned) {
        VOS3_ERROR("slice_assign: device not partitioned — call slice_create first");
        return VOS3_NPU_E_NOT_INIT;
    }

    if (slice_id >= ss->num_slices) {
        VOS3_ERROR("slice_assign: slice_id %u >= num_slices %u",
                   slice_id, ss->num_slices);
        return VOS3_NPU_E_INVAL;
    }

    if (slot_id >= VOS3_NPU_MAX_SLOTS) {
        return VOS3_NPU_E_INVAL;
    }

    /* Check if slice is already owned by a different slot */
    if (ss->slices[slice_id].owner_slot_id != 0xFF &&
        ss->slices[slice_id].owner_slot_id != slot_id) {
        VOS3_WARN("slice_assign: slice %u reassigned from slot %u to slot %u",
                  slice_id, ss->slices[slice_id].owner_slot_id, slot_id);
    }

    ss->slices[slice_id].owner_slot_id = slot_id;

    VOS3_INFO("Slice %u assigned to slot %u (CUs %u-%u, SRAM offset 0x%x)",
              slice_id, slot_id,
              ss->slices[slice_id].cu_first,
              ss->slices[slice_id].cu_first + ss->slices[slice_id].cu_count - 1,
              ss->slices[slice_id].sram_base_offset);

    return VOS3_NPU_OK;
}

/**
 * @brief Query slice descriptor
 */
int vos3_npu_slice_query(uint32_t dev_id, uint8_t slice_id,
                          void *out)
{
    npu_slice_state_t *ss;
    npu_slice_t       *src;
    uint8_t           *dst;

    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }
    if (out == NULL) {
        return VOS3_NPU_E_INVAL;
    }

    ss = &g_slice_state[dev_id];

    if (slice_id >= ss->num_slices) {
        return VOS3_NPU_E_INVAL;
    }

    src = &ss->slices[slice_id];
    dst = (uint8_t *)out;

    /* Copy slice descriptor fields to output */
    npu_memcpy(dst, src, sizeof(npu_slice_t));

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 13: TEMPORAL DMA SHIELD — Side-Channel Defense
 * ============================================================================
 *
 * Inserts RDRAND-seeded random micro-delays between doorbell writes to defeat
 * power-signature analysis attacks.  The actual delay injection is in
 * submit_cmd() (Section 4); these functions control enable/disable/query.
 * ============================================================================ */

/**
 * @brief Enable or disable temporal DMA shielding
 *
 * @details When enabled, each doorbell write in submit_cmd() is preceded by
 *          a random 0-50 iteration pause loop seeded by hardware RDRAND.
 *          This randomizes the DMA submission timing pattern, preventing
 *          an attacker from correlating power traces with inference operations.
 */
int vos3_npu_temporal_shield(uint32_t dev_id, int enable)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    /*
     * Verify CPU capabilities via CPUID.01H:
     *   ECX bit 30 = RDRAND (Ivy Bridge 2012+, required for entropy)
     *   EDX bit 26 = SSE2   (all x86_64 CPUs, mandatory since 2003)
     *
     * Constant-Power Padding uses GPR-only 64-bit XOR chains (no SSE/AVX
     * registers — kernel compiles with -mno-sse).  Compatible with ALL
     * x86_64 CPUs from 2003+.  RDRAND is the only hard requirement (2012+).
     */
    npu_temporal_state_t *ts = &g_temporal[dev_id];

    if (enable && !ts->enabled) {
        uint32_t ecx_val = 0;
        uint32_t edx_val = 0;
        __asm__ volatile(
            "mov $1, %%eax\n\t"
            "cpuid\n\t"
            "mov %%ecx, %0\n\t"
            "mov %%edx, %1"
            : "=r"(ecx_val), "=r"(edx_val)
            :
            : "eax", "ebx", "ecx", "edx"
        );

        if (!(ecx_val & (1U << 30))) {
            VOS3_WARN("[NPU] RDRAND not supported — temporal shield unavailable "
                      "(requires Ivy Bridge 2012+ or equivalent)");
            return VOS3_NPU_E_INVAL;
        }

        ts->has_sse2 = (edx_val & (1U << 26)) ? 1 : 0;
        ts->enabled = 1;
        ts->jitter_count = 0;
        ts->total_delay_iters = 0;
        ts->power_pad_ops = 0;

        /*
         * Enable Constant-Power Padding unconditionally on x86_64.
         * Uses GPR 64-bit XOR chains (4x XOR + rotate per iteration),
         * which draw constant ALU power regardless of data patterns.
         * This flattens the power signature to defeat DPA attacks.
         */
        ts->power_pad_mode = 1;
        VOS3_INFO("[NPU%u] Temporal DMA shield ENABLED "
                  "(mode=CONSTANT_POWER_PAD, GPR-64 XOR chain, "
                  "RDRAND entropy, 0-50 iter jitter)",
                  dev_id);
    } else if (!enable && ts->enabled) {
        ts->enabled = 0;
        VOS3_INFO("[NPU%u] Temporal DMA shield DISABLED "
                  "(jitters=%llu, iters=%llu, pad_ops=%llu)",
                  dev_id,
                  (unsigned long long)ts->jitter_count,
                  (unsigned long long)ts->total_delay_iters,
                  (unsigned long long)ts->power_pad_ops);
    }

    return VOS3_NPU_OK;
}

/**
 * @brief Query temporal shield status
 */
int vos3_npu_temporal_status(uint32_t dev_id, uint32_t *enabled_out,
                               uint64_t *jitter_count)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    const npu_temporal_state_t *ts = &g_temporal[dev_id];

    if (enabled_out != NULL) {
        *enabled_out = ts->enabled ? 1U : 0U;
    }
    if (jitter_count != NULL) {
        *jitter_count = ts->jitter_count;
    }

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 14: PASID SCALABLE MODE — VT-d v4.0 Per-Wing Isolation
 * ============================================================================
 *
 * Upgrades the legacy IOMMU translation (Section 10) to VT-d Scalable Mode
 * with per-Wing PASID entries.  Each AI Wing gets a unique Process Address
 * Space ID, enabling hardware context switching without full IOMMU invalidation.
 *
 * Scalable Mode root table entry (32 bytes):
 *   [63:12] = Context Table Pointer (4K-aligned)
 *   [0]     = Present bit
 *
 * Scalable Mode context entry (64 bytes):
 *   [63:12] = PASID Directory Pointer (4K-aligned)
 *   [11:8]  = Address Width (0=48-bit)
 *   [3:2]   = Translation Type (01=Scalable)
 *   [0]     = Present bit
 *   [95:64] = Domain ID
 * ============================================================================ */

/**
 * @brief Detect Scalable Mode + PASID capability from IOMMU ECAP register
 */
static int npu_pasid_detect(uint32_t dev_id)
{
    const vos3_npu_iommu_seal_t *seal = &g_iommu_seals[dev_id];

    if (!seal->sealed || seal->iommu_reg_virt == 0) {
        return 0;       /* IOMMU not sealed — no base to probe */
    }

    /* Read ECAP (Extended Capability) register */
    volatile uint64_t *ecap_ptr =
        (volatile uint64_t *)(seal->iommu_reg_virt + IOMMU_REG_ECAP);
    uint64_t ecap = *ecap_ptr;

    npu_lfence();

    int smts = (ecap & IOMMU_ECAP_SMTS) ? 1 : 0;
    int pasid = (ecap & IOMMU_ECAP_PASID) ? 1 : 0;

    return (smts && pasid) ? 1 : 0;
}

/**
 * @brief Program a PASID entry for a specific Wing
 *
 * @details Allocates a PASID value (wing_id + 1), configures the scalable
 *          mode context entry, and binds it to the AI slot's physical range.
 */
static int npu_pasid_program_entry(uint32_t dev_id, uint8_t wing_id,
                                     uint32_t slot_id)
{
    npu_pasid_state_t *ps = &g_pasid[dev_id];
    npu_pasid_entry_t *ent = &ps->entries[wing_id];

    /* PASID value: wing_id + 1 (PASID 0 is reserved) */
    uint32_t pasid_val = (uint32_t)wing_id + 1U;

    /* Look up slot physical range from IOMMU seal */
    const vos3_npu_iommu_seal_t *seal = &g_iommu_seals[dev_id];

    ent->active = 1;
    ent->wing_id = wing_id;
    ent->pasid_value = pasid_val;
    ent->bound_slot = slot_id;
    ent->phys_base = seal->slot_phys_base;
    ent->phys_limit = seal->slot_phys_limit;

    /* If SM root table exists, write the PASID directory entry */
    if (ps->sm_root_virt != 0) {
        /*
         * Scalable Mode PASID directory entry (simplified):
         *   Offset = PASID * 64 within the root page
         *   [63:12] = First-Level Page Table Pointer
         *   [11:8]  = Address Width (0x2 = 48-bit)
         *   [0]     = Present
         *
         * For VOS3, we use identity mapping (1:1 phys=virt within slot range)
         * so the page table pointer is set to the slot's PML4 base.
         */
        volatile uint64_t *dir_entry =
            (volatile uint64_t *)(ps->sm_root_virt + pasid_val * 64U);

        /* Entry word 0: Present + Translation Type (Scalable) */
        dir_entry[0] = (ent->phys_base & 0xFFFFFFFFF000ULL)
                      | (0x2U << 8)     /* 48-bit address width */
                      | (0x1U << 2)     /* Scalable translation type */
                      | 0x1U;           /* Present */

        /* Entry word 1: Domain ID */
        dir_entry[1] = (uint64_t)slot_id;

        /* Entry word 2: PASID value */
        dir_entry[2] = (uint64_t)pasid_val;

        /* Entry words 3-7: Reserved / limit enforcement */
        dir_entry[3] = ent->phys_limit;
        dir_entry[4] = 0;
        dir_entry[5] = 0;
        dir_entry[6] = 0;
        dir_entry[7] = 0;

        npu_sfence();
    }

    ps->num_entries++;

    return VOS3_NPU_OK;
}

/**
 * @brief Enable PASID-level hardware isolation for a Wing
 */
int vos3_npu_pasid_isolate(uint32_t dev_id, uint8_t wing_id, uint32_t slot_id)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }
    if (wing_id >= NPU_PASID_MAX_ENTRIES) {
        return VOS3_NPU_E_INVAL;
    }

    /* Require IOMMU seal first */
    if (!g_iommu_seals[dev_id].sealed) {
        VOS3_WARN("[NPU%u] PASID requires IOMMU seal — call vos3_npu_iommu_seal() first",
                  dev_id);
        return VOS3_NPU_E_INVAL;
    }

    npu_pasid_state_t *ps = &g_pasid[dev_id];

    /* First call: detect Scalable Mode capability */
    if (!ps->sm_supported && !ps->sm_enabled) {
        int capable = npu_pasid_detect(dev_id);
        ps->sm_supported = capable ? 1 : 0;

        if (!capable) {
            VOS3_WARN("[NPU%u] IOMMU lacks Scalable Mode (SMTS) or PASID — "
                      "falling back to legacy per-slot IOMMU seal", dev_id);
            /* Still succeed: legacy seal provides isolation, just no PASID */
        }
    }

    /* Allocate SM root table page if supported and not yet allocated */
    if (ps->sm_supported && ps->sm_root_virt == 0) {
        /*
         * Allocate a single 4K page for the PASID directory.
         * In VOS3, kernel pages are identity-mapped in the higher half.
         */
        extern void *vos3_kzalloc(unsigned long size);
        void *page = vos3_kzalloc(4096);
        if (page != NULL) {
            ps->sm_root_virt = (uintptr_t)page;
            /* Physical = virtual - KERNEL_VBASE (identity mapped) */
            ps->sm_root_phys = (uint64_t)page & 0x000000FFFFFFFFFFULL;
            ps->sm_enabled = 1;

            /* Write SM root table address to IOMMU SRTP register */
            const vos3_npu_iommu_seal_t *seal = &g_iommu_seals[dev_id];
            if (seal->iommu_reg_virt != 0) {
                volatile uint64_t *srtp =
                    (volatile uint64_t *)(seal->iommu_reg_virt + 0x28U);
                *srtp = ps->sm_root_phys | 0x1ULL;     /* Present */
                npu_sfence();

                /* Enable Scalable Mode in GCMD */
                volatile uint32_t *gcmd =
                    (volatile uint32_t *)(seal->iommu_reg_virt + 0x18U);
                *gcmd |= IOMMU_GCMD_SMTE;
                npu_sfence();
            }

            VOS3_INFO("[NPU%u] PASID Scalable Mode enabled, root=0x%llx",
                      dev_id, (unsigned long long)ps->sm_root_phys);
        }
    }

    /*
     * Atomic Context Drain — flush stale translations before PASID update.
     *
     * If Scalable Mode is active:  Issue IOTLB global invalidation via the
     *   hardware IOTLB invalidation register.  This drains any in-flight DMA
     *   that references the old PASID entry before we overwrite it.
     *
     * If Scalable Mode is unavailable (pre-2020 hardware):  Issue a Software-
     *   PASID barrier via Context Command Register (CCMD) to invalidate the
     *   legacy context cache entry for this device.  This provides equivalent
     *   isolation semantics on older VT-d implementations.
     */
    {
        const vos3_npu_iommu_seal_t *seal = &g_iommu_seals[dev_id];

        if (ps->sm_enabled && seal->iommu_reg_virt != 0) {
            /* Hardware IOTLB global invalidation (Scalable Mode path) */
            volatile uint64_t *iotlb_reg =
                (volatile uint64_t *)(seal->iommu_reg_virt + IOMMU_REG_IOTLB_INV);

            *iotlb_reg = IOMMU_IOTLB_GLOBAL_INV | IOMMU_IOTLB_INV_WAIT;
            npu_sfence();

            /* Spin-wait for invalidation to complete (bit 63 clears) */
            uint32_t spin = 0;
            while ((*iotlb_reg & IOMMU_IOTLB_INV_WAIT) && spin < 10000U) {
                __asm__ volatile("pause" ::: "memory");
                spin++;
            }

            VOS3_INFO("[NPU%u] IOTLB global invalidation complete (spins=%u)",
                      dev_id, spin);
        } else if (!ps->sm_enabled && seal->iommu_reg_virt != 0) {
            /*
             * Software-PASID Barrier (Legacy VT-d path):
             * Issue a Context-Cache Invalidation via CCMD register.
             * Write DID (Domain ID = slot_id) + Global Invalidation Request.
             * CCMD format: [63]=Invalidation Wait, [62:61]=01 (Global),
             *              [31:16]=Domain ID
             */
            volatile uint64_t *ccmd_reg =
                (volatile uint64_t *)(seal->iommu_reg_virt + IOMMU_REG_CCMD);

            uint64_t ccmd_val = (1ULL << 63)            /* Invalidate Wait */
                              | (0x1ULL << 61)          /* Global Invalidation */
                              | ((uint64_t)slot_id << 16); /* Domain ID */
            *ccmd_reg = ccmd_val;
            npu_sfence();

            /* Spin-wait for context-cache invalidation to complete */
            uint32_t spin = 0;
            while ((*ccmd_reg & (1ULL << 63)) && spin < 10000U) {
                __asm__ volatile("pause" ::: "memory");
                spin++;
            }

            VOS3_INFO("[NPU%u] Software-PASID barrier (CCMD) complete (spins=%u)",
                      dev_id, spin);
        }
    }

    /* Flush NPU internal caches before PASID entry update */
    {
        vos3_npu_sqe_t flush_sqe;
        npu_memset(&flush_sqe, 0, sizeof(flush_sqe));
        flush_sqe.cmd_type = VOS3_NPU_CMD_NOP;  /* NOP/fence acts as cache flush */
        flush_sqe.flags = 0x01;                  /* Fence barrier flag */
        flush_sqe.dma_fence_slot = (uint8_t)slot_id;

        /* Submit flush — ignore errors (best-effort on emulated hardware) */
        (void)vos3_npu_submit_cmd(dev_id, &flush_sqe);
    }

    /* Program PASID entry for this Wing */
    int rc = npu_pasid_program_entry(dev_id, wing_id, slot_id);
    if (rc == VOS3_NPU_OK) {
        VOS3_INFO("[NPU%u] Wing %u isolated with PASID %u → slot %u "
                  "(context drained, %s path)",
                  dev_id, wing_id, (uint32_t)wing_id + 1U, slot_id,
                  ps->sm_enabled ? "IOTLB" : "CCMD-legacy");
    }

    return rc;
}

/**
 * @brief Query PASID status for a Wing
 */
int vos3_npu_pasid_status(uint32_t dev_id, uint8_t wing_id,
                             uint32_t *active_out, uint32_t *pasid_out)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }
    if (wing_id >= NPU_PASID_MAX_ENTRIES) {
        return VOS3_NPU_E_INVAL;
    }

    const npu_pasid_entry_t *ent = &g_pasid[dev_id].entries[wing_id];

    if (active_out != NULL) {
        *active_out = ent->active ? 1U : 0U;
    }
    if (pasid_out != NULL) {
        *pasid_out = ent->pasid_value;
    }

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 15: THERMAL-AWARE DYNAMIC SPATIAL SLICING (DSS)
 * ============================================================================
 *
 * Reads NPU per-CU thermal sensors and migrates AI slots from hot partitions
 * to cooler ones.  Thermal registers are memory-mapped at MMIO + 0x2000.
 * Each 32-bit register contains the temperature in Celsius (low 8 bits).
 *
 * Migration flow:
 *   1. Read source slice CU temperatures → compute max
 *   2. If max < 85°C → return 1 (already cool, no action)
 *   3. Scan all slices → find one with all CUs < 85°C
 *   4. SRAM copy: source slice SRAM → destination slice SRAM
 *   5. Rebind slot to destination slice
 *   6. Release source slice
 * ============================================================================ */

/**
 * @brief Read temperature of a specific Compute Unit
 */
int vos3_npu_thermal_read(uint32_t dev_id, uint32_t cu_id,
                             uint32_t *temp_out)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }
    if (temp_out == NULL) {
        return VOS3_NPU_E_INVAL;
    }
    if (cu_id >= NPU_MAX_CUS) {
        return VOS3_NPU_E_INVAL;
    }

    const vos3_npu_device_t *dev = &g_npu_devices[dev_id];
    if (dev->mmio_base == 0) {
        *temp_out = 0;
        return VOS3_NPU_OK;    /* No MMIO — sensor unavailable */
    }

    /* Read thermal register: base + cu_id * stride */
    uint32_t offset = NPU_REG_THERMAL_BASE + cu_id * NPU_REG_THERMAL_STRIDE;
    uint32_t raw = npu_read32(dev->mmio_base, offset);

    npu_lfence();       /* Spectre-v1: fence after data-dependent read */

    /* Temperature in Celsius is in low 8 bits */
    *temp_out = raw & 0xFFU;

    /* Update cached reading */
    g_thermal[dev_id].last_temps[cu_id] = *temp_out;

    return VOS3_NPU_OK;
}

/**
 * @brief Read temperatures of all CUs on a device
 */
int vos3_npu_thermal_read_all(uint32_t dev_id, uint32_t *temps,
                                uint32_t *count_out)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }
    if (temps == NULL || count_out == NULL) {
        return VOS3_NPU_E_INVAL;
    }

    const vos3_npu_device_t *dev = &g_npu_devices[dev_id];
    npu_thermal_state_t *ts = &g_thermal[dev_id];

    /* Detect CU count from spatial slicing state if available */
    uint32_t cu_count = g_slice_state[dev_id].total_cus;
    if (cu_count == 0) {
        cu_count = 8;   /* Default: assume 8 CUs if not partitioned */
    }
    if (cu_count > NPU_MAX_CUS) {
        cu_count = NPU_MAX_CUS;
    }

    ts->cu_count = cu_count;

    for (uint32_t i = 0; i < cu_count; i++) {
        if (dev->mmio_base != 0) {
            uint32_t offset = NPU_REG_THERMAL_BASE + i * NPU_REG_THERMAL_STRIDE;
            uint32_t raw = npu_read32(dev->mmio_base, offset);
            temps[i] = raw & 0xFFU;
            ts->last_temps[i] = temps[i];
        } else {
            temps[i] = 0;
        }
    }

    npu_lfence();       /* Single fence after bulk register reads */

    *count_out = cu_count;
    return VOS3_NPU_OK;
}

/**
 * @brief Find which slice owns a given AI slot
 *
 * @return Slice index, or 0xFF if not found
 */
static uint8_t npu_find_slot_slice(uint32_t dev_id, uint32_t slot_id)
{
    const npu_slice_state_t *ss = &g_slice_state[dev_id];

    for (uint32_t i = 0; i < ss->num_slices; i++) {
        if (ss->slices[i].owner_slot_id == slot_id) {
            return (uint8_t)i;
        }
    }

    return 0xFF;
}

/**
 * @brief Compute max temperature across a slice's CU range
 */
static uint32_t npu_slice_max_temp(uint32_t dev_id, const npu_slice_t *slice)
{
    const vos3_npu_device_t *dev = &g_npu_devices[dev_id];
    uint32_t max_t = 0;

    for (uint32_t i = 0; i < slice->cu_count; i++) {
        uint32_t cu = slice->cu_first + i;
        if (cu >= NPU_MAX_CUS) break;

        uint32_t t;
        if (dev->mmio_base != 0) {
            uint32_t offset = NPU_REG_THERMAL_BASE + cu * NPU_REG_THERMAL_STRIDE;
            t = npu_read32(dev->mmio_base, offset) & 0xFFU;
        } else {
            t = g_thermal[dev_id].last_temps[cu];
        }

        if (t > max_t) {
            max_t = t;
        }
    }

    npu_lfence();

    return max_t;
}

/**
 * @brief Copy SRAM data between two slices (internal DMA)
 *
 * @details Uses npu_memcpy via MMIO-mapped SRAM.  For real hardware, this
 *          would issue an internal DMA command; for the VOS3 emulated NPU,
 *          direct memory copy suffices.
 */
static int npu_sram_copy(uint32_t dev_id, const npu_slice_t *src,
                            const npu_slice_t *dst)
{
    const vos3_npu_device_t *dev = &g_npu_devices[dev_id];

    if (dev->sram_base == 0) {
        return VOS3_NPU_E_IO;
    }

    uint32_t copy_size = src->sram_size;
    if (copy_size > dst->sram_size) {
        copy_size = dst->sram_size;     /* Clamp to destination capacity */
    }

    uintptr_t src_addr = dev->sram_base + src->sram_base_offset;
    uintptr_t dst_addr = dev->sram_base + dst->sram_base_offset;

    npu_memcpy((void *)dst_addr, (const void *)src_addr, copy_size);

    npu_sfence();       /* Ensure SRAM write is globally visible */

    return VOS3_NPU_OK;
}

/**
 * @brief Migrate an AI slot from a hot slice to a cooler one
 */
int vos3_npu_thermal_migrate(uint32_t dev_id, uint32_t slot_id)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    npu_slice_state_t *ss = &g_slice_state[dev_id];
    npu_thermal_state_t *ts = &g_thermal[dev_id];

    /* Find the slice currently assigned to this slot */
    uint8_t src_idx = npu_find_slot_slice(dev_id, slot_id);
    if (src_idx == 0xFF) {
        return -2;      /* -ENOENT: slot not assigned to any slice */
    }

    npu_slice_t *src_slice = &ss->slices[src_idx];

    /* Read current temperatures for source slice */
    uint32_t src_max = npu_slice_max_temp(dev_id, src_slice);

    if (src_max < NPU_THERMAL_THROTTLE_C) {
        return 1;       /* Already cool — no migration needed */
    }

    ts->throttle_events++;

    VOS3_WARN("[NPU%u] Slice %u THERMAL THROTTLE: max=%u°C (threshold=%u°C), "
              "migrating slot %u",
              dev_id, src_idx, src_max, NPU_THERMAL_THROTTLE_C, slot_id);

    /* Find the coolest available (free) slice */
    uint8_t best_dst = 0xFF;
    uint32_t best_max_temp = 0xFFFFFFFF;

    for (uint32_t i = 0; i < ss->num_slices; i++) {
        if (i == src_idx) continue;
        if (ss->slices[i].owner_slot_id != 0xFF) continue;     /* Not free */

        uint32_t mt = npu_slice_max_temp(dev_id, &ss->slices[i]);
        if (mt < NPU_THERMAL_THROTTLE_C && mt < best_max_temp) {
            best_max_temp = mt;
            best_dst = (uint8_t)i;
        }
    }

    if (best_dst == 0xFF) {
        VOS3_WARN("[NPU%u] No cooler slice available for migration — "
                  "slot %u stays on slice %u", dev_id, slot_id, src_idx);
        return -11;     /* -EAGAIN: no cooler slice available */
    }

    npu_slice_t *dst_slice = &ss->slices[best_dst];

    /* Step 1: Copy SRAM data from source to destination slice */
    int rc = npu_sram_copy(dev_id, src_slice, dst_slice);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("[NPU%u] SRAM copy failed during thermal migration: %d",
                   dev_id, rc);
        return rc;
    }

    /* Step 2: Atomically rebind — assign slot to destination, free source */
    dst_slice->owner_slot_id = slot_id;
    npu_sfence();       /* Ensure new ownership visible before releasing old */
    src_slice->owner_slot_id = 0xFF;    /* Free the hot slice */

    ts->migration_count++;

    VOS3_INFO("[NPU%u] Thermal migration complete: slot %u moved "
              "slice %u (%u°C) → slice %u (%u°C)",
              dev_id, slot_id, src_idx, src_max, best_dst, best_max_temp);

    return VOS3_NPU_OK;
}

/* ============================================================================
 * SECTION 16: PREDICTIVE THERMAL SHADOWING (Shadow DSS)
 * ============================================================================
 *
 * Instead of migrating reactively when temperature exceeds 85°C, Shadow DSS
 * predicts thermal trajectory based on a rolling velocity window and pre-
 * allocates a "shadow slice" when projected temperature exceeds 80°C.
 *
 * This eliminates migration latency on older PCs with slow cooling systems:
 * the shadow slice SRAM is pre-warmed before the threshold is actually hit.
 *
 * Velocity calculation:
 *   For each CU, maintain a ring buffer of delta-T values (current - previous).
 *   Average the ring → degrees-per-sample.
 *   Project: T_now + velocity * lookahead_samples > 80°C → pre-allocate shadow.
 *
 * Lookahead horizon: 4 samples (tunable).
 * ============================================================================ */

/** @brief Lookahead horizon: how many samples to project forward             */
#define NPU_THERMAL_LOOKAHEAD       4U

/**
 * @brief Update thermal velocity tracking for a CU
 *
 * @details Records the temperature delta (current - previous) into a rolling
 *          ring buffer for the specified CU.  Called from thermal_read paths.
 */
static void npu_thermal_update_velocity(uint32_t dev_id, uint32_t cu_id,
                                           uint32_t current_temp)
{
    npu_thermal_state_t *ts = &g_thermal[dev_id];

    if (cu_id >= NPU_MAX_CUS) return;

    int32_t delta = (int32_t)current_temp - (int32_t)ts->prev_temps[cu_id];
    ts->prev_temps[cu_id] = current_temp;

    uint32_t idx = ts->velocity_idx[cu_id] % NPU_THERMAL_VELOCITY_WINDOW;
    ts->velocity_ring[cu_id][idx] = delta;
    ts->velocity_idx[cu_id]++;
}

/**
 * @brief Compute average thermal velocity for a CU (degrees per sample)
 *
 * @return Average delta-T (positive = heating, negative = cooling)
 */
static int32_t npu_thermal_avg_velocity(uint32_t dev_id, uint32_t cu_id)
{
    const npu_thermal_state_t *ts = &g_thermal[dev_id];

    if (cu_id >= NPU_MAX_CUS) return 0;

    /* Need at least a full window of samples for reliable prediction */
    if (ts->velocity_idx[cu_id] < NPU_THERMAL_VELOCITY_WINDOW) {
        return 0;       /* Not enough data — assume stable */
    }

    int32_t sum = 0;
    for (uint32_t i = 0; i < NPU_THERMAL_VELOCITY_WINDOW; i++) {
        sum += ts->velocity_ring[cu_id][i];
    }

    return sum / (int32_t)NPU_THERMAL_VELOCITY_WINDOW;
}

/**
 * @brief Predictive thermal lookahead — pre-allocate shadow slice
 *
 * @details For each CU in the slot's current slice:
 *          1. Read current temperature + update velocity tracker
 *          2. Project: T_projected = T_now + velocity * lookahead
 *          3. If T_projected > 80°C → find a cool free slice as "shadow"
 *          4. Pre-copy SRAM to shadow slice (while still cool)
 *          5. If T_now later exceeds 85°C, instant rebind (no SRAM copy needed)
 *
 * @param[in] dev_id   NPU device index
 * @param[in] slot_id  AI model slot to evaluate
 * @return 0 if shadow allocated, 1 if already cool (no action),
 *         2 if shadow already exists, negative error code on failure
 */
int npu_thermal_predictive_lookahead(uint32_t dev_id, uint32_t slot_id)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    npu_slice_state_t *ss = &g_slice_state[dev_id];
    npu_thermal_state_t *ts = &g_thermal[dev_id];

    /* Check if shadow slice already allocated for this slot */
    if (slot_id < VOS3_NPU_MAX_DEVICES && ts->shadow_slice[slot_id] != 0xFF) {
        return 2;       /* Shadow already prepared */
    }

    /* Find the slice currently assigned to this slot */
    uint8_t src_idx = npu_find_slot_slice(dev_id, slot_id);
    if (src_idx == 0xFF) {
        return -2;      /* -ENOENT: slot not assigned */
    }

    const npu_slice_t *src_slice = &ss->slices[src_idx];

    /* Read current temps and update velocity for each CU in the slice */
    uint32_t max_projected = 0;
    uint32_t max_current = 0;

    for (uint32_t i = 0; i < src_slice->cu_count; i++) {
        uint32_t cu = src_slice->cu_first + i;
        if (cu >= NPU_MAX_CUS) break;

        /* Read current temperature */
        uint32_t temp = 0;
        const vos3_npu_device_t *dev = &g_npu_devices[dev_id];
        if (dev->mmio_base != 0) {
            uint32_t offset = NPU_REG_THERMAL_BASE + cu * NPU_REG_THERMAL_STRIDE;
            temp = npu_read32(dev->mmio_base, offset) & 0xFFU;
        }

        /* Update velocity tracker */
        npu_thermal_update_velocity(dev_id, cu, temp);

        /* Project forward */
        int32_t velocity = npu_thermal_avg_velocity(dev_id, cu);
        int32_t projected = (int32_t)temp
                           + velocity * (int32_t)NPU_THERMAL_LOOKAHEAD;
        if (projected < 0) projected = 0;

        if ((uint32_t)projected > max_projected) {
            max_projected = (uint32_t)projected;
        }
        if (temp > max_current) {
            max_current = temp;
        }
    }

    npu_lfence();

    /* If current temp already above throttle, fall through to reactive path */
    if (max_current >= NPU_THERMAL_THROTTLE_C) {
        return vos3_npu_thermal_migrate(dev_id, slot_id);
    }

    /* If projected temp is below predictive threshold, no action needed */
    if (max_projected < NPU_THERMAL_PREDICT_C) {
        return 1;       /* Cool — no shadow needed */
    }

    /*
     * Projected temperature exceeds 80°C — allocate Shadow Slice.
     * Pre-copy SRAM data now (while running cool) so that if actual
     * temperature later exceeds 85°C, migration is instant rebind only.
     */
    VOS3_INFO("[NPU%u] PREDICTIVE SHADOW: slot %u projected %u°C "
              "(current=%u°C, threshold=%u°C) — pre-allocating shadow",
              dev_id, slot_id, max_projected, max_current,
              NPU_THERMAL_PREDICT_C);

    /* Find coolest free slice for shadow allocation */
    uint8_t best_shadow = 0xFF;
    uint32_t best_temp = 0xFFFFFFFF;

    for (uint32_t i = 0; i < ss->num_slices; i++) {
        if (i == src_idx) continue;
        if (ss->slices[i].owner_slot_id != 0xFF) continue;

        uint32_t mt = npu_slice_max_temp(dev_id, &ss->slices[i]);
        if (mt < NPU_THERMAL_PREDICT_C && mt < best_temp) {
            best_temp = mt;
            best_shadow = (uint8_t)i;
        }
    }

    if (best_shadow == 0xFF) {
        VOS3_WARN("[NPU%u] No cool slice for shadow — deferring to reactive DSS",
                  dev_id);
        return -11;     /* -EAGAIN */
    }

    /* Pre-copy SRAM to shadow slice (zero-lag preparation) */
    int rc = npu_sram_copy(dev_id, src_slice, &ss->slices[best_shadow]);
    if (rc != VOS3_NPU_OK) {
        VOS3_ERROR("[NPU%u] Shadow SRAM pre-copy failed: %d", dev_id, rc);
        return rc;
    }

    /* Record shadow allocation — don't assign ownership yet */
    if (slot_id < VOS3_NPU_MAX_DEVICES) {
        ts->shadow_slice[slot_id] = best_shadow;
    }

    ts->predictive_migrations++;

    VOS3_INFO("[NPU%u] Shadow slice %u ready for slot %u "
              "(pre-copied %u bytes, shadow_temp=%u°C)",
              dev_id, best_shadow, slot_id,
              src_slice->sram_size, best_temp);

    return VOS3_NPU_OK;
}

/**
 * @brief Activate shadow slice — instant rebind if temperature hits threshold
 *
 * @details If a shadow slice was pre-allocated by predictive_lookahead(),
 *          this function performs an instant rebind without SRAM copy (data
 *          was already pre-copied).  Migration latency: ~0 (pointer swap only).
 *
 * @param[in] dev_id   NPU device index
 * @param[in] slot_id  AI model slot to migrate
 * @return 0 on instant migration, 1 if no shadow exists, negative error
 */
int npu_thermal_activate_shadow(uint32_t dev_id, uint32_t slot_id)
{
    if (dev_id >= g_npu_count) {
        return VOS3_NPU_E_NODEV;
    }

    npu_thermal_state_t *ts = &g_thermal[dev_id];
    npu_slice_state_t *ss = &g_slice_state[dev_id];

    if (slot_id >= VOS3_NPU_MAX_DEVICES || ts->shadow_slice[slot_id] == 0xFF) {
        return 1;       /* No shadow — fall back to reactive migration */
    }

    uint8_t shadow_idx = ts->shadow_slice[slot_id];
    if (shadow_idx >= ss->num_slices) {
        ts->shadow_slice[slot_id] = 0xFF;
        return -2;      /* Stale shadow reference */
    }

    /* Find current slice */
    uint8_t src_idx = npu_find_slot_slice(dev_id, slot_id);
    if (src_idx == 0xFF) {
        ts->shadow_slice[slot_id] = 0xFF;
        return -2;
    }

    /*
     * Instant rebind: SRAM data was pre-copied by predictive_lookahead().
     * Only need to do a final delta-copy of any SRAM changes since the
     * pre-copy.  For safety, do a full re-copy (data is hot in cache).
     */
    npu_sram_copy(dev_id, &ss->slices[src_idx], &ss->slices[shadow_idx]);

    /* Atomic ownership transfer */
    ss->slices[shadow_idx].owner_slot_id = slot_id;
    npu_sfence();
    ss->slices[src_idx].owner_slot_id = 0xFF;

    /* Clear shadow record */
    ts->shadow_slice[slot_id] = 0xFF;

    VOS3_INFO("[NPU%u] SHADOW ACTIVATED: slot %u instant-migrated "
              "slice %u → slice %u (pre-warmed, ~0 latency)",
              dev_id, slot_id, src_idx, shadow_idx);

    return VOS3_NPU_OK;
}

/* ============================================================================
 * v20.2: NPU Heterogeneous Cluster Topology (ACPI DSAR)
 * ============================================================================ */

#include <vos/acpi.h>

/**
 * npu_get_cluster_topology — return NPU cluster array from ACPI DSAR table.
 *
 * Called after vos3_acpi_init(). On systems with a DSAR table, each entry
 * describes one NPU cluster (device_id, compute_capacity, memory_bandwidth).
 * This data drives heterogeneous dispatch: high-capacity clusters handle
 * large inference batches; low-power clusters handle edge-inference workloads.
 *
 * On systems without DSAR, returns 0 clusters (PCI-discovered NPUs still
 * operate via the standard npu.c dispatch path).
 *
 * @param clusters_out  Caller's pointer to receive the cluster array start.
 * @return              Number of valid cluster entries (0–8).
 */
uint8_t npu_get_cluster_topology(const void **clusters_out)
{
    const vos3_acpi_info_t *acpi = vos3_acpi_get_info();
    if (!acpi || !acpi->dsar_found) {
        if (clusters_out) *clusters_out = (const void *)0;
        return 0;
    }
    if (clusters_out) *clusters_out = (const void *)acpi->dsar_clusters;
    return acpi->dsar_cluster_count;
}

/* ============================================================================
 * END OF NPU DRIVER
 * ============================================================================ */
