/**
 * @file accel.h
 * @brief VOS3 NPU/Accelerator Abstraction Layer
 *
 * @details Provides a hardware-agnostic API for AI inference acceleration.
 *          At boot time the subsystem probes available hardware and registers
 *          each accelerator with its capabilities.  Higher-level code queries
 *          the optimal accelerator for a given operation type; the layer
 *          transparently routes to GPU (VirtIO-GPU), a future NPU, or the
 *          CPU fallback.
 *
 *          Priority order (highest first):
 *            1. NPU  -- dedicated neural processing unit (future hardware)
 *            2. GPU  -- VirtIO-GPU with virgl 3D (matrix / attention ops)
 *            3. CPU  -- always-available fallback (scalar / integer)
 *
 *          Dispatch routing selects the device with the highest TOPS rating
 *          that advertises the required capability for the requested operation.
 *          If no accelerator matches, the CPU fallback is always available.
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.4 -- NPU/Accelerator Abstraction
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ACCEL_H
#define VOS3_ACCEL_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * ACCELERATOR TYPES
 * ============================================================================ */

/**
 * @brief Accelerator device type enumeration
 */
typedef enum vos3_accel_type {
    VOS3_ACCEL_NONE = 0,    /**< No accelerator / invalid                    */
    VOS3_ACCEL_CPU  = 1,    /**< CPU fallback (always available)              */
    VOS3_ACCEL_GPU  = 2,    /**< VirtIO-GPU / real GPU                       */
    VOS3_ACCEL_NPU  = 3,    /**< Neural Processing Unit                      */
} vos3_accel_type_t;

/* ============================================================================
 * CAPABILITY FLAGS
 * ============================================================================ */

/** @brief General compute shaders                                            */
#define VOS3_ACCEL_CAP_COMPUTE      (1U << 0)

/** @brief Matrix multiply acceleration                                       */
#define VOS3_ACCEL_CAP_MATMUL       (1U << 1)

/** @brief INT4/INT8 quantized operations                                     */
#define VOS3_ACCEL_CAP_QUANTIZED    (1U << 2)

/** @brief Half-precision (FP16) support                                      */
#define VOS3_ACCEL_CAP_FP16         (1U << 3)

/** @brief Single-precision (FP32) support                                    */
#define VOS3_ACCEL_CAP_FP32         (1U << 4)

/** @brief Fused attention kernels                                            */
#define VOS3_ACCEL_CAP_ATTENTION    (1U << 5)

/** @brief Convolution support                                                */
#define VOS3_ACCEL_CAP_CONV         (1U << 6)

/* ============================================================================
 * OPERATION TYPES
 * ============================================================================ */

/**
 * @brief Operation types for dispatch routing
 */
typedef enum vos3_accel_op {
    VOS3_ACCEL_OP_MATMUL = 0,           /**< Matrix multiplication           */
    VOS3_ACCEL_OP_SOFTMAX,              /**< Softmax normalization           */
    VOS3_ACCEL_OP_LAYERNORM,            /**< Layer normalization             */
    VOS3_ACCEL_OP_ATTENTION,            /**< Fused self-attention            */
    VOS3_ACCEL_OP_QUANTIZE,             /**< Float-to-int quantization       */
    VOS3_ACCEL_OP_DEQUANTIZE,           /**< Int-to-float dequantization     */
    VOS3_ACCEL_OP_ELEMENTWISE_ADD,      /**< Element-wise addition           */
    VOS3_ACCEL_OP_ELEMENTWISE_MUL,      /**< Element-wise multiplication     */
    VOS3_ACCEL_OP_ROPE,                 /**< Rotary Position Embedding       */
    VOS3_ACCEL_OP_CUSTOM,               /**< Custom / user-defined           */
    VOS3_ACCEL_OP_COUNT                 /**< Sentinel (number of op types)   */
} vos3_accel_op_t;

/* ============================================================================
 * ACCELERATOR LIMITS
 * ============================================================================ */

/** @brief Maximum number of registered accelerator devices                   */
#define VOS3_ACCEL_MAX_DEVICES      8U

/** @brief Maximum length of an accelerator name (including NUL terminator)   */
#define VOS3_ACCEL_NAME_LEN         32U

/* ============================================================================
 * DEVICE DESCRIPTOR
 * ============================================================================ */

/**
 * @brief Accelerator device descriptor
 *
 * @details Each registered accelerator has a descriptor that tracks its type,
 *          capabilities, performance rating, memory size, online status, and
 *          lifetime operation counters.
 */
typedef struct vos3_accel_device {
    uint32_t            id;                         /**< Device index (0-based)         */
    vos3_accel_type_t   type;                       /**< Device type                    */
    char                name[VOS3_ACCEL_NAME_LEN];  /**< Human-readable name            */
    uint32_t            caps;                       /**< Capability bitmask             */
    uint32_t            tops;                       /**< Tera-ops/sec (INT8 peak)       */
    uint32_t            mem_mb;                     /**< Device memory in MB            */
    uint8_t             online;                     /**< 1 if device is ready           */
    uint64_t            ops_dispatched;             /**< Lifetime operation count       */
    uint64_t            ops_failed;                 /**< Lifetime failure count         */
} vos3_accel_device_t;

/* ============================================================================
 * TENSOR DESCRIPTOR
 * ============================================================================ */

/**
 * @brief Lightweight tensor descriptor for dispatch
 *
 * @details Describes a tensor's memory layout, shape, and data type.
 *          Supports up to 4 dimensions (batch, channels, height, width).
 */
typedef struct vos3_accel_tensor {
    void        *data;          /**< Pointer to tensor data                   */
    uint32_t     shape[4];      /**< Shape: [batch, channels, height, width]  */
    uint32_t     ndim;          /**< Number of active dimensions (1-4)        */
    uint32_t     dtype;         /**< 0=FP32, 1=FP16, 2=INT8, 3=INT4          */
    uint32_t     stride[4];     /**< Strides in elements per dimension        */
} vos3_accel_tensor_t;

/* ============================================================================
 * DISPATCH REQUEST
 * ============================================================================ */

/**
 * @brief Dispatch request structure
 *
 * @details Describes a single operation to be dispatched to an accelerator.
 *          Contains the operation type, up to 4 input tensors, one output
 *          tensor, and reserved flags for future extensions.
 */
typedef struct vos3_accel_dispatch {
    vos3_accel_op_t         op;             /**< Operation type               */
    vos3_accel_tensor_t    *inputs[4];      /**< Up to 4 input tensors        */
    uint32_t                num_inputs;     /**< Number of valid input ptrs   */
    vos3_accel_tensor_t    *output;         /**< Output tensor                */
    uint32_t                flags;          /**< Reserved for future use      */
} vos3_accel_dispatch_t;

/* ============================================================================
 * STATISTICS
 * ============================================================================ */

/**
 * @brief Accelerator subsystem statistics
 */
typedef struct vos3_accel_stats {
    uint32_t    num_devices;        /**< Total registered devices             */
    uint32_t    preferred_device;   /**< Default preferred device ID          */
    uint64_t    total_dispatches;   /**< Lifetime total dispatches            */
    uint64_t    cpu_dispatches;     /**< Dispatches handled by CPU            */
    uint64_t    gpu_dispatches;     /**< Dispatches handled by GPU            */
    uint64_t    npu_dispatches;     /**< Dispatches handled by NPU            */
    uint64_t    fallback_count;     /**< Times fell back to slower device     */
} vos3_accel_stats_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize the accelerator subsystem
 *
 * @details Probes available hardware (GPU via VirtIO-GPU, future NPU) and
 *          registers the CPU scalar fallback.  Must be called after SMP and
 *          PMM initialization.
 *
 * @return 0 on success, negative errno on failure
 */
int vos3_accel_init(void);

/**
 * @brief Register a new accelerator device
 *
 * @details Adds a device to the registry with the given type, name,
 *          capabilities, performance rating, and memory size.  The device
 *          is marked online upon successful registration.
 *
 * @param[in] type    Accelerator type (CPU, GPU, or NPU)
 * @param[in] name    Human-readable name (up to VOS3_ACCEL_NAME_LEN-1 chars)
 * @param[in] caps    Capability bitmask (VOS3_ACCEL_CAP_*)
 * @param[in] tops    Tera-ops per second (INT8 peak), 0 for CPU
 * @param[in] mem_mb  Device memory in megabytes
 * @return 0 on success, -1 if table full or name is NULL
 */
int vos3_accel_register(vos3_accel_type_t type, const char *name,
                        uint32_t caps, uint32_t tops, uint32_t mem_mb);

/**
 * @brief Dispatch an operation to the best available accelerator
 *
 * @details Routes the operation to the highest-TOPS device that supports
 *          the requested operation's capability.  If no hardware accelerator
 *          matches, falls back to CPU.  CPU fallback provides stub
 *          implementations for MATMUL, SOFTMAX, LAYERNORM, and
 *          ELEMENTWISE_ADD/MUL.  Other operations return -ENOTSUP.
 *
 * @param[in,out] req  Dispatch request (inputs read, output written)
 * @return 0 on success, negative errno on failure (-EINVAL, -ENOTSUP)
 */
int vos3_accel_dispatch(vos3_accel_dispatch_t *req);

/**
 * @brief Get the preferred device ID for a given operation
 *
 * @details Returns the device ID with the highest TOPS rating that
 *          advertises the capability required for the operation.
 *
 * @param[in] op  Operation type
 * @return Device ID (0-based), or 0 (CPU fallback) if no match
 */
int vos3_accel_get_preferred(vos3_accel_op_t op);

/**
 * @brief Get subsystem-wide statistics
 *
 * @param[out] stats  Statistics structure to fill
 * @return 0 on success, -1 if stats is NULL
 */
int vos3_accel_get_stats(vos3_accel_stats_t *stats);

/**
 * @brief Get the number of registered accelerator devices
 *
 * @return Device count (always >= 1 after init, for the CPU fallback)
 */
uint32_t vos3_accel_num_devices(void);

/**
 * @brief Get a read-only pointer to a device descriptor
 *
 * @param[in] id  Device ID (0-based)
 * @return Pointer to device descriptor, or NULL if id is out of range
 *
 * @warning The returned pointer is into the internal device table and
 *          must not be modified.  It remains valid until the device is
 *          unregistered.
 */
const vos3_accel_device_t *vos3_accel_get_device(uint32_t id);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ACCEL_H */
