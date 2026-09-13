/**
 * @file npu.h
 * @brief VOS3 NPU/AI Accelerator Hardware Driver Interface
 *
 * @details Defines the low-level hardware interface for Neural Processing Unit
 *          (NPU) discovery, BAR mapping, command submission, DMA isolation,
 *          and execution context mirroring for V-Palace integration.
 *
 *          This driver sits BELOW the accelerator abstraction layer (accel.h)
 *          and provides hardware-specific implementation that accel.c dispatches
 *          to.  The dispatch chain is:
 *
 *            accel.c  (routing / capability match)
 *              -> npu.c  (PCI discovery, MMIO, queues, DMA fences)
 *                -> hardware  (NPU registers, SRAM, doorbell)
 *
 *          At init time the driver scans PCI bus for class 0x12 (Processing
 *          Accelerators), maps MMIO BARs, allocates submission/completion
 *          queue pairs, and registers the device with the accel layer.
 *
 *          DMA isolation is enforced per AI model slot: each slot has a
 *          fence descriptor that restricts DMA addresses to the slot's
 *          physical memory range.  Any out-of-bounds DMA is rejected before
 *          submission to hardware.
 *
 *          Context mirroring enables V-Palace swap: weights and KV-cache
 *          can be saved/loaded between system RAM and NPU SRAM without
 *          re-uploading from disk.
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 8.4 -- NPU/AI Accelerator Hardware Driver
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_NPU_H
#define VOS3_NPU_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * PCI CLASS / SUBCLASS FOR AI ACCELERATORS
 * ============================================================================ */

/** @brief PCI class 12 — Processing Accelerators                             */
#define PCI_CLASS_ACCEL             0x12U

/** @brief PCI subclass 00 — Neural Processing Unit                           */
#define PCI_SUBCLASS_ACCEL_NPU      0x00U

/* ============================================================================
 * KNOWN AI ACCELERATOR VENDOR IDs
 * ============================================================================ */

/** @brief NVIDIA GPU/NPU vendor ID                                           */
#define VOS3_NPU_VENDOR_NVIDIA      0x10DEU

/** @brief Intel NPU vendor ID                                                */
#define VOS3_NPU_VENDOR_INTEL       0x8086U

/** @brief AMD NPU vendor ID                                                  */
#define VOS3_NPU_VENDOR_AMD         0x1002U

/** @brief Qualcomm NPU vendor ID                                             */
#define VOS3_NPU_VENDOR_QUALCOMM    0x17CBU

/** @brief Google TPU vendor ID                                               */
#define VOS3_NPU_VENDOR_GOOGLE      0x1AE0U

/* ============================================================================
 * INTEL NPU DEVICE IDs
 * ============================================================================ */

/** @brief Intel Meteor Lake NPU device ID                                    */
#define VOS3_NPU_INTEL_MTL          0x7D1DU

/** @brief Intel Lunar Lake NPU device ID                                     */
#define VOS3_NPU_INTEL_LNL          0xAD1DU

/** @brief Intel Arrow Lake NPU device ID                                     */
#define VOS3_NPU_INTEL_ARL          0xB01DU

/* ============================================================================
 * NPU CONSTANTS
 * ============================================================================ */

/** @brief Maximum number of NPU devices tracked                              */
#define VOS3_NPU_MAX_DEVICES        4U

/** @brief Hardware submission queue depth (entries)                           */
#define VOS3_NPU_SQ_DEPTH           256U

/** @brief Hardware completion queue depth (entries)                           */
#define VOS3_NPU_CQ_DEPTH           256U

/** @brief Maximum DMA transfer size: 64 MiB                                  */
#define VOS3_NPU_MAX_DMA_SIZE       (64U * 1024U * 1024U)

/** @brief MMIO region size: 256 KB                                           */
#define VOS3_NPU_MMIO_SIZE          (256U * 1024U)

/** @brief Maximum on-die SRAM size: 16 MiB                                   */
#define VOS3_NPU_SRAM_MAX           (16U * 1024U * 1024U)

/** @brief Doorbell registers start offset within MMIO region                 */
#define VOS3_NPU_DOORBELL_OFFSET    0x1000U

/** @brief Maximum concurrent execution context slots                         */
#define VOS3_NPU_CONTEXT_SLOTS      8U

/* ============================================================================
 * NPU THERMAL SENSOR REGISTERS
 * ============================================================================ */

/** @brief Thermal sensor base offset within MMIO (CU 0-7 temps)             */
#define VOS3_NPU_REG_THERMAL_BASE   0x2000U

/** @brief Per-CU thermal register stride (4 bytes per CU)                   */
#define VOS3_NPU_REG_THERMAL_STRIDE 0x04U

/** @brief Maximum CUs per device for thermal monitoring                     */
#define VOS3_NPU_MAX_CUS            64U

/** @brief Thermal throttle threshold: 85 degrees Celsius                    */
#define VOS3_NPU_THERMAL_THROTTLE_C 85U

/** @brief Thermal critical threshold: 95 degrees Celsius (emergency halt)   */
#define VOS3_NPU_THERMAL_CRITICAL_C 95U

/* ============================================================================
 * TEMPORAL DMA SHIELD CONSTANTS
 * ============================================================================ */

/** @brief Maximum random micro-delay iterations (0-50 ns range)             */
#define VOS3_NPU_TEMPORAL_MAX_JITTER 50U

/** @brief Temporal shield enable flag (bit 0 of shield control)             */
#define VOS3_NPU_TEMPORAL_ENABLED   (1U << 0)

/* ============================================================================
 * PASID (Process Address Space ID) CONSTANTS
 * ============================================================================ */

/** @brief Scalable Mode root table entry size: 32 bytes                     */
#define VOS3_PASID_ROOT_ENTRY_SIZE  32U

/** @brief Scalable Mode context entry size: 64 bytes                        */
#define VOS3_PASID_CTX_ENTRY_SIZE   64U

/** @brief Maximum PASID entries (per-Wing allocation)                       */
#define VOS3_PASID_MAX_ENTRIES      8U

/** @brief PASID bits for Wing isolation (3 bits = 8 Wings)                  */
#define VOS3_PASID_WING_BITS        3U

/* ============================================================================
 * NPU COMMAND TYPES
 * ============================================================================ */

/**
 * @brief NPU hardware command type enumeration
 *
 * @details Defines the set of operations that can be submitted to the NPU
 *          via the hardware submission queue.  Compute commands (0x01-0x08)
 *          perform neural network operations, DMA commands (0x10-0x11)
 *          transfer data between system RAM and on-die SRAM, and context
 *          commands (0x20-0x21) save/restore execution state.
 */
typedef enum vos3_npu_cmd_type {
    VOS3_NPU_CMD_MATMUL         = 0x01,  /**< Matrix multiply (GEMM)         */
    VOS3_NPU_CMD_CONV2D         = 0x02,  /**< 2D convolution                 */
    VOS3_NPU_CMD_SOFTMAX        = 0x03,  /**< Softmax normalization           */
    VOS3_NPU_CMD_LAYERNORM      = 0x04,  /**< Layer normalization             */
    VOS3_NPU_CMD_ATTENTION      = 0x05,  /**< Fused self-attention            */
    VOS3_NPU_CMD_ROPE           = 0x06,  /**< Rotary Position Embedding       */
    VOS3_NPU_CMD_QUANTIZE       = 0x07,  /**< Float-to-int quantization       */
    VOS3_NPU_CMD_DEQUANTIZE     = 0x08,  /**< Int-to-float dequantization     */
    VOS3_NPU_CMD_DMA_COPY       = 0x10,  /**< DMA: system RAM -> SRAM         */
    VOS3_NPU_CMD_DMA_WRITEBACK  = 0x11,  /**< DMA: SRAM -> system RAM         */
    VOS3_NPU_CMD_CONTEXT_SAVE   = 0x20,  /**< Save execution context          */
    VOS3_NPU_CMD_CONTEXT_LOAD   = 0x21,  /**< Load execution context          */
    VOS3_NPU_CMD_NOP            = 0xFF,  /**< No-op / fence barrier           */
} vos3_npu_cmd_type_t;

/* ============================================================================
 * HARDWARE SUBMISSION QUEUE ENTRY (SQE) — 64 BYTES
 * ============================================================================ */

/**
 * @brief NPU hardware submission queue entry
 *
 * @details Packed 64-byte structure written to the submission queue ring
 *          buffer.  After writing, the driver rings the SQ doorbell to
 *          notify the NPU.  The cmd_id field is echoed in the completion
 *          queue entry for matching.  The dma_fence_slot field is checked
 *          against the active DMA fence before submission to prevent
 *          cross-slot memory access.
 *
 * @note For MATMUL: src_phys=A, dst_phys=B, aux_phys=C (output).
 *       src_rows=M, src_cols=K, dst_cols=N.
 */
typedef struct __attribute__((packed)) vos3_npu_sqe {
    uint8_t     cmd_type;       /**< Command type (vos3_npu_cmd_type_t)       */
    uint8_t     flags;          /**< Bit 0: interrupt on completion            */
    uint16_t    context_id;     /**< Execution context slot (0-7)              */
    uint32_t    cmd_id;         /**< Command ID for completion matching        */
    uint64_t    src_phys;       /**< Source DMA physical address               */
    uint64_t    dst_phys;       /**< Destination DMA physical address          */
    uint32_t    src_rows;       /**< Matrix A rows (M dimension)               */
    uint32_t    src_cols;       /**< Matrix A cols (K) / shared dimension      */
    uint32_t    dst_cols;       /**< Matrix B cols (N dimension)               */
    uint32_t    dtype;          /**< 0=FP32, 1=FP16, 2=INT8, 3=INT4           */
    uint64_t    aux_phys;       /**< Auxiliary buffer (bias, scale, output)    */
    uint32_t    dma_fence_slot; /**< DMA isolation: AI slot ID for fence check */
    uint32_t    reserved[3];    /**< Pad to 64 bytes                           */
} vos3_npu_sqe_t;

_Static_assert(sizeof(vos3_npu_sqe_t) == 64, "NPU SQE must be 64 bytes");

/* ============================================================================
 * HARDWARE COMPLETION QUEUE ENTRY (CQE) — 16 BYTES
 * ============================================================================ */

/**
 * @brief NPU hardware completion queue entry
 *
 * @details Packed 16-byte structure read from the completion queue.
 *          The phase bit toggles each time the queue wraps, allowing
 *          the driver to detect new completions without a separate
 *          interrupt.  The cycles field reports hardware execution
 *          time for performance accounting.
 */
typedef struct __attribute__((packed)) vos3_npu_cqe {
    uint32_t    cmd_id;         /**< Matching command ID from SQE              */
    uint16_t    status;         /**< 0 = success, non-zero = error code        */
    uint16_t    context_id;     /**< Context slot that completed               */
    uint32_t    cycles;         /**< Hardware cycle count for the operation     */
    uint32_t    phase_and_rsvd; /**< Bit 0: phase bit, bits 1-31: reserved     */
} vos3_npu_cqe_t;

_Static_assert(sizeof(vos3_npu_cqe_t) == 16, "NPU CQE must be 16 bytes");

/* ============================================================================
 * NPU QUEUE PAIR
 * ============================================================================ */

/**
 * @brief NPU submission/completion queue pair
 *
 * @details Manages a paired SQ/CQ ring buffer for command submission and
 *          completion polling.  The sq_tail advances on submit, cq_head
 *          advances on poll.  The phase bit alternates each CQ wrap to
 *          distinguish new completions.  The cmd_counter provides
 *          monotonically increasing command IDs.
 */
typedef struct vos3_npu_queue {
    volatile vos3_npu_sqe_t *sq;        /**< Submission queue (kernel VA)      */
    volatile vos3_npu_cqe_t *cq;        /**< Completion queue (kernel VA)      */
    uintptr_t   sq_phys;                /**< SQ physical address (for DMA)     */
    uintptr_t   cq_phys;                /**< CQ physical address (for DMA)     */
    uint32_t    depth;                  /**< Queue depth (entries)              */
    uint32_t    sq_tail;                /**< Next SQ slot to write             */
    uint32_t    cq_head;                /**< Next CQ slot to read              */
    uint8_t     cq_phase;               /**< Expected phase bit (0 or 1)       */
    uint32_t    cmd_counter;            /**< Monotonic command ID generator     */
    volatile uint32_t *sq_doorbell;     /**< SQ doorbell MMIO address          */
    volatile uint32_t *cq_doorbell;     /**< CQ doorbell MMIO address          */
} vos3_npu_queue_t;

/* ============================================================================
 * DMA ISOLATION DESCRIPTOR
 * ============================================================================ */

/**
 * @brief IOMMU-style per-slot DMA boundary enforcement
 *
 * @details Each AI model slot has a DMA fence that restricts the physical
 *          address range accessible by NPU DMA operations.  Before any
 *          SQE is submitted, the driver validates that src_phys, dst_phys,
 *          and aux_phys fall within the fence boundaries of the slot
 *          identified by dma_fence_slot.  Violations are rejected with
 *          VOS3_NPU_E_DMA_FENCE and counted in dma_violations.
 */
typedef struct vos3_npu_dma_fence {
    uint64_t    base_phys;      /**< Lowest allowed DMA address               */
    uint64_t    limit_phys;     /**< Highest allowed DMA address (exclusive)  */
    uint32_t    slot_id;        /**< AI slot that owns this fence             */
    uint8_t     active;         /**< 1 if fence is armed, 0 if inactive       */
    uint8_t     _pad[3];       /**< Padding for alignment                    */
} vos3_npu_dma_fence_t;

/* ============================================================================
 * NPU EXECUTION CONTEXT (V-PALACE MIRRORING)
 * ============================================================================ */

/**
 * @brief NPU execution context for V-Palace mirroring
 *
 * @details Tracks the state of a hardware execution context slot.
 *          Each context binds to an AI model slot and records the
 *          physical addresses of loaded weights and KV-cache.
 *          V-Palace swap saves the context (SRAM -> RAM), then loads
 *          another model's context (RAM -> SRAM), enabling rapid
 *          model switching without disk I/O.
 */
typedef struct vos3_npu_context {
    uint32_t    context_id;     /**< Hardware context slot (0-7)              */
    uint32_t    slot_id;        /**< AI model slot this context belongs to    */
    uint64_t    weights_phys;   /**< Physical address of loaded weights       */
    uint64_t    kv_cache_phys;  /**< Physical address of KV-cache             */
    uint32_t    weights_size;   /**< Weight buffer size in bytes              */
    uint32_t    kv_cache_size;  /**< KV-cache size in bytes                   */
    uint8_t     loaded;         /**< 1 if weights loaded into SRAM            */
    uint8_t     dirty;          /**< 1 if KV-cache modified since last save   */
    uint8_t     npu_slice_id;   /**< MIG-style hardware slice (CU partition)  */
    uint8_t     _pad;           /**< Padding for alignment                    */
} vos3_npu_context_t;

/* ============================================================================
 * NPU SPATIAL SLICING — MIG-Style Multi-Instance Isolation
 * ============================================================================ */

/** @brief Maximum hardware compute slices per NPU device */
#define VOS3_NPU_MAX_SLICES         8U

/** @brief Slice type: full device (no partitioning) */
#define VOS3_NPU_SLICE_FULL         0U

/** @brief Slice type: half device (2 partitions) */
#define VOS3_NPU_SLICE_HALF         1U

/** @brief Slice type: quarter device (4 partitions) */
#define VOS3_NPU_SLICE_QUARTER      2U

/** @brief Slice type: eighth device (8 partitions) */
#define VOS3_NPU_SLICE_EIGHTH       3U

/**
 * @brief NPU hardware slice descriptor
 *
 * @details Each slice maps to a subset of Compute Units (CUs) on the NPU,
 *          enabling MIG-style isolation where different AI Wings execute
 *          on different hardware partitions simultaneously.  Slice SRAM
 *          is isolated: [sram_base_offset, sram_base_offset + sram_size).
 */
typedef struct vos3_npu_slice {
    uint8_t     slice_id;       /**< Hardware slice index (0 to MAX_SLICES-1) */
    uint8_t     slice_type;     /**< VOS3_NPU_SLICE_* partition type          */
    uint8_t     cu_first;       /**< First Compute Unit in this slice         */
    uint8_t     cu_count;       /**< Number of CUs assigned to this slice     */
    uint32_t    sram_base_offset;  /**< SRAM offset for this slice (bytes)    */
    uint32_t    sram_size;      /**< SRAM capacity for this slice (bytes)     */
    uint32_t    owner_slot_id;  /**< AI model slot that owns this slice (0xFF=free) */
} vos3_npu_slice_t;

/* ============================================================================
 * NPU DEVICE STATE
 * ============================================================================ */

/**
 * @brief NPU device descriptor and runtime state
 *
 * @details Holds all state for a single NPU device: PCI identity, mapped
 *          MMIO regions, queue pair, DMA fences, execution contexts,
 *          capability flags, performance counters, and health status.
 *          Up to VOS3_NPU_MAX_DEVICES instances are maintained in the
 *          driver's internal device table.
 */
typedef struct vos3_npu_device {
    /* --- PCI identity ---------------------------------------------------- */
    uint16_t    vendor_id;      /**< PCI vendor ID                            */
    uint16_t    device_id;      /**< PCI device ID                            */
    uint8_t     pci_bus;        /**< PCI bus number                           */
    uint8_t     pci_dev;        /**< PCI device number                        */
    uint8_t     pci_func;       /**< PCI function number                      */
    uint8_t     _pad;           /**< Alignment padding                        */

    /* --- MMIO ------------------------------------------------------------ */
    uintptr_t   mmio_base;      /**< Mapped MMIO virtual address              */
    uintptr_t   sram_base;      /**< Mapped SRAM virtual address              */
    uint32_t    sram_size;      /**< SRAM size in bytes                       */

    /* --- Queues ---------------------------------------------------------- */
    vos3_npu_queue_t cmd_q;     /**< Command submission/completion queue       */

    /* --- DMA isolation --------------------------------------------------- */
    vos3_npu_dma_fence_t fences[8]; /**< Per-slot DMA fences                  */

    /* --- Execution contexts ---------------------------------------------- */
    vos3_npu_context_t contexts[VOS3_NPU_CONTEXT_SLOTS]; /**< Context slots   */

    /* --- Capabilities ---------------------------------------------------- */
    uint32_t    tops;           /**< Tera-ops/sec (INT8 peak)                 */
    uint32_t    caps;           /**< VOS3_ACCEL_CAP_* capability bitmask      */
    char        name[32];       /**< Human-readable device name               */

    /* --- Statistics ------------------------------------------------------ */
    uint64_t    total_ops;      /**< Lifetime operations submitted            */
    uint64_t    total_cycles;   /**< Lifetime hardware cycles consumed        */
    uint64_t    total_errors;   /**< Lifetime error count                     */
    uint64_t    dma_violations; /**< DMA fence violations detected            */

    /* --- State ----------------------------------------------------------- */
    uint8_t     initialized;    /**< 1 if device is initialized and ready     */
    uint8_t     fatal;          /**< 1 if device is in fatal/unrecoverable state */
    uint8_t     _pad2[2];      /**< Alignment padding                        */
} vos3_npu_device_t;

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

/** @brief Operation completed successfully                                   */
#define VOS3_NPU_OK             0

/** @brief No NPU device found during PCI scan                               */
#define VOS3_NPU_E_NO_DEVICE    (-19)

/** @brief Hardware timeout waiting for completion                            */
#define VOS3_NPU_E_TIMEOUT      (-110)

/** @brief Device in fatal/unrecoverable state                                */
#define VOS3_NPU_E_FATAL        (-5)

/** @brief Memory allocation failed (PMM or heap)                             */
#define VOS3_NPU_E_NO_MEM       (-12)

/** @brief Invalid parameter (NULL pointer, out-of-range ID, etc.)            */
#define VOS3_NPU_E_INVALID      (-22)

/** @brief DMA boundary violation (address outside slot fence)                */
#define VOS3_NPU_E_DMA_FENCE    (-1)

/** @brief Device or queue is busy (SQ full)                                  */
#define VOS3_NPU_E_BUSY         (-16)

/** @brief Device not initialized (call vos3_npu_init first)                  */
#define VOS3_NPU_E_NOT_READY    (-6)

/** @brief Context synchronization mismatch (stale context data)              */
#define VOS3_NPU_E_CTX_MISMATCH (-74)

/* ============================================================================
 * PUBLIC API — DISCOVERY AND INITIALIZATION
 * ============================================================================ */

/**
 * @brief Scan PCI bus for NPU devices, map BARs, and allocate queues
 *
 * @details Enumerates PCI bus for devices with class PCI_CLASS_ACCEL and
 *          subclass PCI_SUBCLASS_ACCEL_NPU.  For each discovered device:
 *            1. Maps MMIO BAR into kernel virtual address space
 *            2. Allocates SQ/CQ ring buffers from PMM
 *            3. Initializes doorbell pointers
 *            4. Registers the device with the accel abstraction layer
 *
 *          Must be called after PCI scan (vos3_pci_bus_scan) and PMM init.
 *
 * @return Number of NPU devices found (>=0), or negative errno on failure
 */
int vos3_npu_init(void);

/**
 * @brief Reset an NPU device to initial state
 *
 * @details Performs a software reset of the NPU: drains pending commands,
 *          resets queue head/tail pointers, clears all DMA fences, and
 *          invalidates all execution contexts.  The device remains
 *          initialized after reset.
 *
 * @param[in] dev_id  Device index (0 to VOS3_NPU_MAX_DEVICES-1)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_reset(uint32_t dev_id);

/* ============================================================================
 * PUBLIC API — DMA FENCE MANAGEMENT (IOMMU-STYLE ISOLATION)
 * ============================================================================ */

/**
 * @brief Arm a DMA fence for an AI model slot
 *
 * @details Sets the allowed physical address range for DMA operations
 *          associated with the given slot.  Once armed, any SQE with
 *          dma_fence_slot matching this slot_id will have its src_phys,
 *          dst_phys, and aux_phys validated against [base_phys, limit_phys).
 *
 * @param[in] dev_id      Device index
 * @param[in] slot_id     AI model slot (0-7)
 * @param[in] base_phys   Lowest allowed physical address (inclusive)
 * @param[in] limit_phys  Highest allowed physical address (exclusive)
 * @return 0 on success, VOS3_NPU_E_INVALID if parameters out of range
 */
int vos3_npu_dma_fence_set(uint32_t dev_id, uint32_t slot_id,
                            uint64_t base_phys, uint64_t limit_phys);

/**
 * @brief Disarm a DMA fence for an AI model slot
 *
 * @details Clears the DMA fence, preventing any further DMA submissions
 *          for the given slot until a new fence is set.
 *
 * @param[in] dev_id   Device index
 * @param[in] slot_id  AI model slot (0-7)
 * @return 0 on success, VOS3_NPU_E_INVALID if parameters out of range
 */
int vos3_npu_dma_fence_clear(uint32_t dev_id, uint32_t slot_id);

/**
 * @brief Validate a DMA address range against a slot's fence
 *
 * @details Checks that [phys, phys + size) falls entirely within the
 *          armed fence for the given slot.  This is called internally
 *          before every SQE submission but is also exposed for external
 *          validation (e.g., by the accel dispatch layer).
 *
 * @param[in] dev_id   Device index
 * @param[in] slot_id  AI model slot (0-7)
 * @param[in] phys     Physical address to validate
 * @param[in] size     Size of the region in bytes
 * @return 0 if valid, VOS3_NPU_E_DMA_FENCE if out of bounds
 */
int vos3_npu_dma_validate(uint32_t dev_id, uint32_t slot_id,
                           uint64_t phys, uint32_t size);

/* ============================================================================
 * PUBLIC API — COMMAND SUBMISSION
 * ============================================================================ */

/**
 * @brief Submit a matrix multiply operation to the NPU
 *
 * @details Convenience wrapper that constructs an SQE for MATMUL and
 *          submits it.  Computes C = A * B where A is MxK and B is KxN.
 *          All physical addresses are validated against the DMA fence for
 *          the active context's slot before submission.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Execution context slot (0-7)
 * @param[in] a_phys      Physical address of matrix A (MxK)
 * @param[in] b_phys      Physical address of matrix B (KxN)
 * @param[in] c_phys      Physical address of output matrix C (MxN)
 * @param[in] M           Rows of A / rows of C
 * @param[in] K           Cols of A / rows of B (shared dimension)
 * @param[in] N           Cols of B / cols of C
 * @param[in] dtype       Data type: 0=FP32, 1=FP16, 2=INT8, 3=INT4
 * @return Command ID (>0) on success, negative error code on failure
 */
int vos3_npu_submit_matmul(uint32_t dev_id, uint32_t context_id,
                            uint64_t a_phys, uint64_t b_phys, uint64_t c_phys,
                            uint32_t M, uint32_t K, uint32_t N, uint32_t dtype);

/**
 * @brief Submit a raw SQE to the NPU
 *
 * @details Low-level submission interface.  The caller constructs a
 *          complete SQE and passes it to the driver.  The driver validates
 *          DMA addresses against the fence, copies the SQE into the ring
 *          buffer, advances sq_tail, and rings the doorbell.
 *
 * @param[in] dev_id  Device index
 * @param[in] sqe     Pointer to a fully populated SQE (must not be NULL)
 * @return Command ID (>0) on success, negative error code on failure
 */
int vos3_npu_submit_cmd(uint32_t dev_id, const vos3_npu_sqe_t *sqe);

/**
 * @brief Poll for completion of a submitted command
 *
 * @details Checks the completion queue for a CQE matching the given
 *          cmd_id.  If found, the CQE is consumed (cq_head advanced)
 *          and the hardware cycle count is written to cycles_out.
 *
 * @param[in]  dev_id      Device index
 * @param[in]  cmd_id      Command ID to wait for
 * @param[out] cycles_out  Hardware cycles consumed (may be NULL)
 * @return 0 on success (completion found), VOS3_NPU_E_TIMEOUT if not
 *         yet complete, negative error code on failure
 */
int vos3_npu_poll_completion(uint32_t dev_id, uint32_t cmd_id,
                              uint32_t *cycles_out);

/* ============================================================================
 * PUBLIC API — V-PALACE CONTEXT MIRRORING
 * ============================================================================ */

/**
 * @brief Load model weights into an NPU execution context
 *
 * @details Associates the given weights buffer with a context slot and
 *          initiates DMA transfer from system RAM into NPU SRAM.
 *          The context is bound to the specified AI model slot for
 *          DMA fence enforcement.
 *
 * @param[in] dev_id        Device index
 * @param[in] context_id    Hardware context slot (0-7)
 * @param[in] slot_id       AI model slot that owns the weights
 * @param[in] weights_phys  Physical address of weights in system RAM
 * @param[in] weights_size  Size of weights buffer in bytes
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_context_load(uint32_t dev_id, uint32_t context_id,
                           uint32_t slot_id, uint64_t weights_phys,
                           uint32_t weights_size);

/**
 * @brief Save an NPU execution context to system RAM
 *
 * @details Writes back dirty KV-cache and execution state from NPU SRAM
 *          to the physical addresses recorded in the context descriptor.
 *          After save, the context's dirty flag is cleared.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Hardware context slot (0-7)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_context_save(uint32_t dev_id, uint32_t context_id);

/**
 * @brief Synchronize context metadata with hardware state
 *
 * @details Reads hardware registers to update the context descriptor's
 *          dirty flag and cycle counters.  Used by V-Palace before swap
 *          decisions to check if a save is needed.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Hardware context slot (0-7)
 * @return 0 on success, VOS3_NPU_E_CTX_MISMATCH if stale
 */
int vos3_npu_context_sync(uint32_t dev_id, uint32_t context_id);

/**
 * @brief Clear an NPU execution context slot
 *
 * @details Invalidates the context, zeroes the SRAM region used by the
 *          context, and clears all metadata.  The context slot becomes
 *          available for reuse.
 *
 * @param[in] dev_id      Device index
 * @param[in] context_id  Hardware context slot (0-7)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_context_clear(uint32_t dev_id, uint32_t context_id);

/* ============================================================================
 * PUBLIC API — QUERY
 * ============================================================================ */

/**
 * @brief Get the number of discovered NPU devices
 *
 * @return Number of NPU devices found (0 if none or not yet initialized)
 */
uint32_t vos3_npu_device_count(void);

/**
 * @brief Get a read-only pointer to an NPU device descriptor
 *
 * @param[in] dev_id  Device index (0 to VOS3_NPU_MAX_DEVICES-1)
 * @return Pointer to device descriptor, or NULL if dev_id is out of range
 *
 * @warning The returned pointer is into the internal device table and
 *          must not be modified.  It remains valid until the device is
 *          reset or the driver is torn down.
 */
const vos3_npu_device_t *vos3_npu_get_device(uint32_t dev_id);

/**
 * @brief Check if an NPU device is healthy and ready for commands
 *
 * @details Returns 1 if the device is initialized and not in a fatal
 *          error state.  Returns 0 otherwise.
 *
 * @param[in] dev_id  Device index
 * @return 1 if healthy, 0 if unhealthy or invalid dev_id
 */
int vos3_npu_is_healthy(uint32_t dev_id);

/* ============================================================================
 * PUBLIC API — IOMMU DMA SEAL
 * ============================================================================ */

/**
 * @brief Seal NPU DMA via IOMMU hardware isolation
 *
 * @details Programs the IOMMU translation unit (from ACPI DMAR) to restrict
 *          the NPU's DMA address range to the current AI model slot's physical
 *          page set.  The NPU is assigned a virtual device identity bound to
 *          AI_HALL_BASE.  Any DMA attempt outside the slot's phys[] map:
 *            1. Triggers a HARDWARE_SECURITY_FAULT event
 *            2. Increments dma_violations counter
 *            3. Resets the PCI link to the NPU device
 *
 *          The seal is layered on top of the software DMA fence: both must
 *          agree for a DMA transfer to proceed.
 *
 * @param[in] dev_id   NPU device index
 * @param[in] slot_id  AI model slot to bind (determines allowed phys range)
 * @return 0 on success, negative error code on failure
 *
 * @note Requires ACPI DMAR table (vos3_acpi_init must have found IOMMU units)
 * @note Idempotent: can be re-invoked to re-bind to a different slot
 */
int vos3_npu_iommu_seal(uint32_t dev_id, uint32_t slot_id);

/**
 * @brief Query IOMMU seal status for an NPU device
 *
 * @param[in]  dev_id       NPU device index
 * @param[out] sealed_out   Receives 1 if sealed, 0 if not (may be NULL)
 * @param[out] bound_slot   Receives the sealed slot_id (may be NULL)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_iommu_status(uint32_t dev_id, uint32_t *sealed_out,
                            uint32_t *bound_slot);

/* ============================================================================
 * PUBLIC API — PEER-TO-PEER NVMe → NPU WARP STREAM
 * ============================================================================ */

/**
 * @brief Transfer data directly from NVMe to NPU SRAM via PCIe P2P DMA
 *
 * @details Implements zero-RAM-overhead model loading by directing the NVMe
 *          controller to write directly into the NPU's SRAM BAR instead of
 *          system RAM.  The transfer path is:
 *
 *            NVMe SSD → PCIe Root Complex → NPU SRAM BAR
 *
 *          Both the NVMe PRP validation and NPU DMA fence validate the
 *          transfer endpoints.  The NPU SRAM address is derived from the
 *          device's BAR2 mapping.
 *
 * @param[in] npu_dev_id   NPU device index
 * @param[in] slot_id      AI model slot (for DMA fence validation)
 * @param[in] nvme_lba     Starting LBA on NVMe device
 * @param[in] sector_count Number of 512-byte sectors to transfer
 * @param[in] sram_offset  Destination offset within NPU SRAM (bytes)
 * @return 0 on success, negative error code on failure
 *
 * @note Requires both NVMe and NPU to be initialized
 * @note Maximum transfer: min(NVMe max transfer, NPU SRAM size)
 */
int vos3_p2p_nvme_to_npu(uint32_t npu_dev_id, uint32_t slot_id,
                           uint64_t nvme_lba, uint32_t sector_count,
                           uint32_t sram_offset);

/**
 * @brief Query P2P capability between NVMe and NPU
 *
 * @param[in]  npu_dev_id     NPU device index
 * @param[out] supported_out  Receives 1 if P2P is supported, 0 if not
 * @param[out] max_bytes_out  Receives maximum P2P transfer size (bytes)
 * @return 0 on success, negative error code on failure
 */
int vos3_p2p_query_capability(uint32_t npu_dev_id, uint32_t *supported_out,
                                uint32_t *max_bytes_out);

/* ============================================================================
 * PUBLIC API — SPATIAL SLICING (MIG-STYLE)
 * ============================================================================ */

/**
 * @brief Partition NPU into spatial slices for multi-Wing isolation
 *
 * @param[in] dev_id      NPU device index
 * @param[in] slice_type  VOS3_NPU_SLICE_* partition type
 * @return Number of slices created (>0), or negative error code
 */
int vos3_npu_slice_create(uint32_t dev_id, uint8_t slice_type);

/**
 * @brief Assign a spatial slice to an AI model slot
 *
 * @param[in] dev_id    NPU device index
 * @param[in] slice_id  Slice index (0 to MAX_SLICES-1)
 * @param[in] slot_id   AI model slot to assign
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_slice_assign(uint32_t dev_id, uint8_t slice_id, uint32_t slot_id);

/**
 * @brief Get slice descriptor for a given slice
 *
 * @param[in]  dev_id    NPU device index
 * @param[in]  slice_id  Slice index
 * @param[out] out       Receives slice descriptor (must not be NULL)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_slice_query(uint32_t dev_id, uint8_t slice_id,
                          vos3_npu_slice_t *out);

/* ============================================================================
 * PUBLIC API — TEMPORAL DMA SHIELD (Side-Channel Defense)
 * ============================================================================ */

/**
 * @brief Enable or disable temporal DMA shielding on doorbell writes
 *
 * @details Inserts RDRAND-seeded random micro-delays (0-50 ns) between
 *          non-consecutive doorbell writes to defeat power-signature analysis
 *          side-channel attacks.  When enabled, each doorbell ring is preceded
 *          by a variable-iteration pause loop whose count is derived from
 *          hardware entropy (RDRAND).  This randomizes the timing signature
 *          of DMA submission patterns, neutralizing MIT-style bleed attacks.
 *
 * @param[in] dev_id  NPU device index
 * @param[in] enable  1 to enable, 0 to disable
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_temporal_shield(uint32_t dev_id, int enable);

/**
 * @brief Query temporal shield status for an NPU device
 *
 * @param[in]  dev_id        NPU device index
 * @param[out] enabled_out   Receives 1 if enabled, 0 if disabled (may be NULL)
 * @param[out] jitter_count  Receives total jitter insertions since enable (may be NULL)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_temporal_status(uint32_t dev_id, uint32_t *enabled_out,
                               uint64_t *jitter_count);

/* ============================================================================
 * PUBLIC API — PASID HARDWARE ISOLATION (VT-d v4.0 Scalable Mode)
 * ============================================================================ */

/**
 * @brief Enable PASID-level hardware isolation for per-Wing IOMMU context
 *
 * @details Upgrades the IOMMU seal from legacy translation to VT-d v4.0
 *          Scalable Mode with per-Wing PASID entries.  Each AI Wing gets
 *          a unique PASID, enabling hardware-enforced address space isolation
 *          without full IOMMU flush on context switch (< 10 us latency).
 *
 * @param[in] dev_id    NPU device index
 * @param[in] wing_id   V-Palace Wing ID (0-7)
 * @param[in] slot_id   AI model slot to bind to this PASID
 * @return 0 on success, negative error code on failure
 *
 * @pre  vos3_npu_iommu_seal() must have been called first (DMAR required)
 * @note Idempotent: re-binding to a different slot updates the PASID entry
 */
int vos3_npu_pasid_isolate(uint32_t dev_id, uint8_t wing_id, uint32_t slot_id);

/**
 * @brief Query PASID isolation status for a Wing
 *
 * @param[in]  dev_id       NPU device index
 * @param[in]  wing_id      V-Palace Wing ID
 * @param[out] active_out   Receives 1 if PASID is active (may be NULL)
 * @param[out] pasid_out    Receives the assigned PASID value (may be NULL)
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_pasid_status(uint32_t dev_id, uint8_t wing_id,
                             uint32_t *active_out, uint32_t *pasid_out);

/* ============================================================================
 * PUBLIC API — THERMAL-AWARE DYNAMIC SPATIAL SLICING (DSS)
 * ============================================================================ */

/**
 * @brief Read temperature of a specific Compute Unit
 *
 * @details Reads the NPU's per-CU thermal sensor register.  Temperature is
 *          returned in degrees Celsius.  Returns 0 if the CU does not have
 *          a sensor or the device does not support thermal monitoring.
 *
 * @param[in]  dev_id     NPU device index
 * @param[in]  cu_id      Compute Unit index
 * @param[out] temp_out   Receives temperature in degrees Celsius
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_thermal_read(uint32_t dev_id, uint32_t cu_id,
                             uint32_t *temp_out);

/**
 * @brief Read temperatures of all CUs on a device
 *
 * @param[in]  dev_id     NPU device index
 * @param[out] temps      Array of at least VOS3_NPU_MAX_CUS entries
 * @param[out] count_out  Receives actual number of CUs with valid temps
 * @return 0 on success, negative error code on failure
 */
int vos3_npu_thermal_read_all(uint32_t dev_id, uint32_t *temps,
                                uint32_t *count_out);

/**
 * @brief Migrate an AI slot from a hot slice to a cooler one
 *
 * @details If the CUs assigned to the given slot's slice exceed the thermal
 *          throttle threshold (85 C), this function:
 *            1. Identifies the coolest available slice on the same device
 *            2. Copies SRAM data from source to destination slice (via P2P)
 *            3. Atomically rebinds the slot to the new slice
 *            4. Releases the old slice
 *
 *          If no cooler slice is available, returns -EAGAIN.  If the slot is
 *          not assigned to any slice, returns -ENOENT.
 *
 * @param[in] dev_id   NPU device index
 * @param[in] slot_id  AI model slot to migrate
 * @return 0 on success (migrated), 1 if already cool (no action), negative error
 */
int vos3_npu_thermal_migrate(uint32_t dev_id, uint32_t slot_id);

/* ============================================================================
 * PUBLIC API — PREDICTIVE THERMAL SHADOWING (Shadow DSS)
 * ============================================================================ */

/**
 * @brief Predictive thermal lookahead with shadow slice pre-allocation
 *
 * @details Calculates thermal trajectory based on rolling delta-T velocity.
 *          If projected temperature exceeds 80 C, pre-allocates a shadow
 *          slice and pre-copies SRAM data.  When actual temperature hits 85 C,
 *          migration is an instant pointer swap (~0 latency).
 *
 *          Compatible with all PCs from 2012+ (uses SSE2 for power padding,
 *          no AVX512 or NPU-specific features required for the prediction).
 *
 * @param[in] dev_id   NPU device index
 * @param[in] slot_id  AI model slot to evaluate
 * @return 0 if shadow allocated, 1 if cool (no action), 2 if shadow exists,
 *         negative error code on failure
 */
int npu_thermal_predictive_lookahead(uint32_t dev_id, uint32_t slot_id);

/**
 * @brief Instantly activate a pre-warmed shadow slice
 *
 * @details If predictive_lookahead() previously prepared a shadow, this
 *          performs an instant rebind with ~0 migration latency (SRAM data
 *          was already pre-copied).  Falls back to reactive migration if
 *          no shadow exists.
 *
 * @param[in] dev_id   NPU device index
 * @param[in] slot_id  AI model slot to migrate
 * @return 0 on instant migration, 1 if no shadow, negative error
 */
int npu_thermal_activate_shadow(uint32_t dev_id, uint32_t slot_id);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_NPU_H */
