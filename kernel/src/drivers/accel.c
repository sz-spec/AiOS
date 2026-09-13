/**
 * @file accel.c
 * @brief VOS3 NPU/Accelerator Abstraction Layer -- Implementation
 *
 * @details Maintains a static registry of up to VOS3_ACCEL_MAX_DEVICES (8)
 *          accelerator devices.  At init time the subsystem:
 *            1. Always registers the CPU scalar fallback.
 *            2. Probes VirtIO-GPU (virgl 3D) and registers it if present.
 *            3. (Future) Probes dedicated NPU hardware via PCI scan.
 *
 *          The dispatch path selects the device with the highest TOPS rating
 *          whose capability bitmask covers the requested operation.  If no
 *          hardware accelerator qualifies, the CPU fallback handles the
 *          request with lightweight stub implementations (sufficient for
 *          functional testing; real CPU tensor ops arrive in Phase 6 GGML).
 *
 *          CPU fallback stubs:
 *            - MATMUL:          copy first input to output (identity stub)
 *            - SOFTMAX:         copy input to output
 *            - LAYERNORM:       copy input to output
 *            - ELEMENTWISE_ADD: element-wise add (FP32 via x87)
 *            - ELEMENTWISE_MUL: element-wise multiply (FP32 via x87)
 *            - All others:      return -ENOTSUP
 *
 *          All mutable state is protected by a spinlock for SMP safety.
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

#include "../../include/vos/accel.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/virtio_gpu.h"

/* --- NPU driver forward declarations (primitive-type API only) ----------- */
extern int      vos3_npu_init(void);
extern uint32_t vos3_npu_device_count(void);
extern int      vos3_npu_is_healthy(uint32_t dev_id);

/* ============================================================================
 * ERRNO VALUES (freestanding -- no <errno.h>)
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

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief Registered accelerator device table */
static vos3_accel_device_t g_devices[VOS3_ACCEL_MAX_DEVICES];

/** @brief Number of registered devices */
static uint32_t g_num_devices = 0;

/** @brief Set to 1 once vos3_accel_init() completes successfully */
static int g_initialized = 0;

/** @brief Spinlock protecting all mutable state in this module */
static vos3_spinlock_t g_accel_lock = VOS3_SPINLOCK_INIT;

/** @brief Subsystem-wide dispatch statistics */
static vos3_accel_stats_t g_stats;

/* ============================================================================
 * INTERNAL HELPERS — OP-TO-CAPABILITY MAPPING
 * ============================================================================ */

/**
 * @brief Map an operation type to the required capability flag
 *
 * @param[in] op  Operation type
 * @return Capability bitmask required, or 0 if no specific cap needed
 */
static uint32_t accel_op_to_cap(vos3_accel_op_t op)
{
    switch (op) {
    case VOS3_ACCEL_OP_MATMUL:
        return VOS3_ACCEL_CAP_MATMUL;
    case VOS3_ACCEL_OP_SOFTMAX:         /* fall through */
    case VOS3_ACCEL_OP_LAYERNORM:
        return VOS3_ACCEL_CAP_COMPUTE;
    case VOS3_ACCEL_OP_ATTENTION:
        return VOS3_ACCEL_CAP_ATTENTION;
    case VOS3_ACCEL_OP_QUANTIZE:        /* fall through */
    case VOS3_ACCEL_OP_DEQUANTIZE:
        return VOS3_ACCEL_CAP_QUANTIZED;
    case VOS3_ACCEL_OP_ELEMENTWISE_ADD: /* fall through */
    case VOS3_ACCEL_OP_ELEMENTWISE_MUL:
        return VOS3_ACCEL_CAP_COMPUTE;
    case VOS3_ACCEL_OP_ROPE:
        return VOS3_ACCEL_CAP_COMPUTE;
    case VOS3_ACCEL_OP_CUSTOM:          /* fall through */
    default:
        return VOS3_ACCEL_CAP_COMPUTE;
    }
}

/**
 * @brief Safe string copy into a fixed-size name buffer
 *
 * @details Copies up to (VOS3_ACCEL_NAME_LEN - 1) bytes from @p src into
 *          @p dst and guarantees NUL termination.
 *
 * @param[out] dst  Destination buffer (VOS3_ACCEL_NAME_LEN bytes)
 * @param[in]  src  Source string (may be NULL)
 */
static void accel_copy_name(char *dst, const char *src)
{
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }

    uint32_t i = 0;
    while (i < (VOS3_ACCEL_NAME_LEN - 1U) && src[i] != '\0') {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/**
 * @brief Compute the total number of elements in a tensor
 *
 * @param[in] t  Tensor descriptor
 * @return Product of shape dimensions, or 0 if tensor is NULL or ndim is 0
 */
static uint32_t accel_tensor_numel(const vos3_accel_tensor_t *t)
{
    if (t == NULL || t->ndim == 0 || t->ndim > 4) {
        return 0;
    }

    uint32_t n = 1;
    for (uint32_t i = 0; i < t->ndim; i++) {
        n *= t->shape[i];
    }
    return n;
}

/* ============================================================================
 * CPU FALLBACK STUBS
 * ============================================================================ */

/**
 * @brief CPU fallback: copy input[0] to output (identity stub)
 *
 * @details Used as a placeholder for operations that will be replaced by
 *          real GGML tensor ops in Phase 6.  Simply copies the first input
 *          tensor's data to the output tensor.
 *
 * @param[in]  req  Dispatch request
 * @return 0 on success, -EINVAL if pointers are invalid
 */
static int cpu_stub_copy(const vos3_accel_dispatch_t *req)
{
    if (req->num_inputs < 1 || req->inputs[0] == NULL || req->output == NULL) {
        return -EINVAL;
    }

    const vos3_accel_tensor_t *src = req->inputs[0];
    vos3_accel_tensor_t *dst = req->output;

    if (src->data == NULL || dst->data == NULL) {
        return -EINVAL;
    }

    uint32_t n = accel_tensor_numel(src);
    if (n == 0) {
        return 0;
    }

    /* FP32 assumed (dtype 0) — 4 bytes per element */
    uint32_t bytes = n * 4U;
    memcpy(dst->data, src->data, bytes);

    return 0;
}

/**
 * @brief CPU fallback: element-wise addition (FP32, x87)
 *
 * @details Adds corresponding elements of input[0] and input[1], storing
 *          the result in output.  Uses float pointers cast from void*.
 *          GCC will emit x87 FPU instructions since SSE is disabled.
 *
 * @param[in]  req  Dispatch request
 * @return 0 on success, -EINVAL if parameters are invalid
 */
static int cpu_elementwise_add(const vos3_accel_dispatch_t *req)
{
    if (req->num_inputs < 2 || req->inputs[0] == NULL ||
        req->inputs[1] == NULL || req->output == NULL) {
        return -EINVAL;
    }

    const vos3_accel_tensor_t *a = req->inputs[0];
    const vos3_accel_tensor_t *b = req->inputs[1];
    vos3_accel_tensor_t *out = req->output;

    if (a->data == NULL || b->data == NULL || out->data == NULL) {
        return -EINVAL;
    }

    uint32_t na = accel_tensor_numel(a);
    uint32_t nb = accel_tensor_numel(b);
    uint32_t no = accel_tensor_numel(out);

    /* All tensors must have the same number of elements */
    if (na == 0 || na != nb || na != no) {
        return -EINVAL;
    }

    /* FP32 element-wise add via x87 */
    const float *fa = (const float *)a->data;
    const float *fb = (const float *)b->data;
    float *fo = (float *)out->data;

    for (uint32_t i = 0; i < na; i++) {
        fo[i] = fa[i] + fb[i];
    }

    return 0;
}

/**
 * @brief CPU fallback: element-wise multiplication (FP32, x87)
 *
 * @details Multiplies corresponding elements of input[0] and input[1],
 *          storing the result in output.
 *
 * @param[in]  req  Dispatch request
 * @return 0 on success, -EINVAL if parameters are invalid
 */
static int cpu_elementwise_mul(const vos3_accel_dispatch_t *req)
{
    if (req->num_inputs < 2 || req->inputs[0] == NULL ||
        req->inputs[1] == NULL || req->output == NULL) {
        return -EINVAL;
    }

    const vos3_accel_tensor_t *a = req->inputs[0];
    const vos3_accel_tensor_t *b = req->inputs[1];
    vos3_accel_tensor_t *out = req->output;

    if (a->data == NULL || b->data == NULL || out->data == NULL) {
        return -EINVAL;
    }

    uint32_t na = accel_tensor_numel(a);
    uint32_t nb = accel_tensor_numel(b);
    uint32_t no = accel_tensor_numel(out);

    if (na == 0 || na != nb || na != no) {
        return -EINVAL;
    }

    /* FP32 element-wise multiply via x87 */
    const float *fa = (const float *)a->data;
    const float *fb = (const float *)b->data;
    float *fo = (float *)out->data;

    for (uint32_t i = 0; i < na; i++) {
        fo[i] = fa[i] * fb[i];
    }

    return 0;
}

/* Phase 6.3: GGML Core dispatch wrappers (defined in kernel/src/ai/ggml_core.c) */
extern int vos3_ggml_cpu_matmul(const vos3_accel_dispatch_t *req);
extern int vos3_ggml_cpu_softmax(const vos3_accel_dispatch_t *req);
extern int vos3_ggml_cpu_layernorm(const vos3_accel_dispatch_t *req);

/**
 * @brief CPU fallback dispatch router
 *
 * @details Routes an operation to the appropriate CPU implementation.
 *          Phase 6.3: MATMUL, SOFTMAX, and LAYERNORM now use real GGML
 *          tensor operations (quantized matmul, fused softmax, RMSNorm).
 *          ELEMENTWISE_ADD and ELEMENTWISE_MUL use x87 arithmetic.
 *          All other operations return -ENOTSUP.
 *
 * @param[in,out] req  Dispatch request
 * @return 0 on success, negative errno on failure
 */
static int cpu_fallback_dispatch(vos3_accel_dispatch_t *req)
{
    switch (req->op) {
    case VOS3_ACCEL_OP_MATMUL:
        /* Phase 6.3: Real quantized/FP32 matmul via GGML core */
        return vos3_ggml_cpu_matmul(req);

    case VOS3_ACCEL_OP_SOFTMAX:
        /* Phase 6.3: Fused Flash-Softmax with numerical stability */
        return vos3_ggml_cpu_softmax(req);

    case VOS3_ACCEL_OP_LAYERNORM:
        /* Phase 6.3: RMSNorm (LLaMA-style) via GGML core */
        return vos3_ggml_cpu_layernorm(req);

    case VOS3_ACCEL_OP_ELEMENTWISE_ADD:
        return cpu_elementwise_add(req);

    case VOS3_ACCEL_OP_ELEMENTWISE_MUL:
        return cpu_elementwise_mul(req);

    case VOS3_ACCEL_OP_ATTENTION:       /* fall through */
    case VOS3_ACCEL_OP_QUANTIZE:        /* fall through */
    case VOS3_ACCEL_OP_DEQUANTIZE:      /* fall through */
    case VOS3_ACCEL_OP_ROPE:            /* fall through */
    case VOS3_ACCEL_OP_CUSTOM:          /* fall through */
    default:
        return -ENOTSUP;
    }
}

/* ============================================================================
 * PUBLIC API -- REGISTRATION
 * ============================================================================ */

/**
 * @brief Register a new accelerator device
 */
int vos3_accel_register(vos3_accel_type_t type, const char *name,
                        uint32_t caps, uint32_t tops, uint32_t mem_mb)
{
    if (name == NULL) {
        VOS3_ERROR("[ACCEL] register: NULL name pointer");
        return -1;
    }

    if (type == VOS3_ACCEL_NONE) {
        VOS3_ERROR("[ACCEL] register: ACCEL_NONE is not a valid type");
        return -1;
    }

    vos3_spinlock_lock(&g_accel_lock);

    if (g_num_devices >= VOS3_ACCEL_MAX_DEVICES) {
        vos3_spinlock_unlock(&g_accel_lock);
        VOS3_ERROR("[ACCEL] register: table full (%u/%u)",
                   (unsigned)g_num_devices, (unsigned)VOS3_ACCEL_MAX_DEVICES);
        return -1;
    }

    uint32_t slot = g_num_devices;
    vos3_accel_device_t *dev = &g_devices[slot];

    memset(dev, 0, sizeof(*dev));

    dev->id             = slot;
    dev->type           = type;
    dev->caps           = caps;
    dev->tops           = tops;
    dev->mem_mb         = mem_mb;
    dev->online         = 1U;
    dev->ops_dispatched = 0;
    dev->ops_failed     = 0;

    accel_copy_name(dev->name, name);

    g_num_devices++;

    vos3_spinlock_unlock(&g_accel_lock);

    VOS3_INFO("[ACCEL] Registered [%u]: \"%s\" type=%u caps=0x%x tops=%u mem=%uMB",
              (unsigned)slot, dev->name, (unsigned)type,
              (unsigned)caps, (unsigned)tops, (unsigned)mem_mb);

    return 0;
}

/* ============================================================================
 * HARDWARE PROBES
 * ============================================================================ */

/**
 * @brief Register the CPU scalar fallback device
 *
 * @details The CPU device always supports COMPUTE, MATMUL, FP32, and
 *          QUANTIZED operations.  TOPS is 0 (lowest priority for routing).
 *          Memory is reported as 0 since the CPU shares system RAM.
 *
 * @return 0 on success
 */
static int accel_register_cpu(void)
{
    uint32_t cpu_caps = VOS3_ACCEL_CAP_COMPUTE
                      | VOS3_ACCEL_CAP_MATMUL
                      | VOS3_ACCEL_CAP_FP32
                      | VOS3_ACCEL_CAP_QUANTIZED;

    return vos3_accel_register(VOS3_ACCEL_CPU, "VOS3 CPU (scalar)",
                               cpu_caps, 0U, 0U);
}

/**
 * @brief Probe and register VirtIO-GPU with virgl 3D
 *
 * @details If the VirtIO-GPU driver reports that the device is present and
 *          supports virgl 3D, this function registers it as a GPU accelerator
 *          with COMPUTE, MATMUL, FP16, FP32, and ATTENTION capabilities.
 *          If no GPU is present the function returns silently.
 *
 * @return 0 if GPU registered, 1 if not available (not an error)
 */
static int accel_probe_gpu(void)
{
    if (vos3_virtio_gpu_available() == 0) {
        VOS3_DEBUG("[ACCEL] VirtIO-GPU not available");
        return 1;
    }

    if (vos3_virtio_gpu_has_virgl() == 0) {
        VOS3_DEBUG("[ACCEL] VirtIO-GPU present but virgl not supported -- skipping");
        return 1;
    }

    uint32_t gpu_caps = VOS3_ACCEL_CAP_COMPUTE
                      | VOS3_ACCEL_CAP_MATMUL
                      | VOS3_ACCEL_CAP_FP16
                      | VOS3_ACCEL_CAP_FP32
                      | VOS3_ACCEL_CAP_ATTENTION;

    int rc = vos3_accel_register(VOS3_ACCEL_GPU, "VirtIO-GPU (virgl 3D)",
                                  gpu_caps, 2U, 0U);
    if (rc == 0) {
        VOS3_INFO("[ACCEL] GPU registered: VirtIO-GPU with virgl 3D");
    }
    return rc;
}

/**
 * @brief Probe for dedicated NPU hardware via PCI scan
 *
 * @details Calls the NPU driver to scan PCI bus for class 0x12 (Processing
 *          Accelerators) and known vendor/device pairs.  If at least one
 *          NPU is found, registers it with the accel layer.
 *
 * @return 0 if NPU registered, 1 if not available (not an error)
 */
static int accel_probe_npu(void)
{
    int rc = vos3_npu_init();
    if (rc < 0) {
        VOS3_DEBUG("[ACCEL] NPU init failed: %d", rc);
        return 1;
    }

    uint32_t count = vos3_npu_device_count();
    if (count == 0U) {
        VOS3_DEBUG("[ACCEL] NPU probe: no hardware detected");
        return 1;
    }

    /* Register discovered NPU with full capability set */
    uint32_t npu_caps = VOS3_ACCEL_CAP_COMPUTE
                      | VOS3_ACCEL_CAP_MATMUL
                      | VOS3_ACCEL_CAP_FP16
                      | VOS3_ACCEL_CAP_FP32
                      | VOS3_ACCEL_CAP_QUANTIZED
                      | VOS3_ACCEL_CAP_ATTENTION;

    rc = vos3_accel_register(VOS3_ACCEL_NPU, "Hardware NPU",
                              npu_caps, 40U, 0U);
    if (rc == 0) {
        VOS3_INFO("[ACCEL] NPU registered: %u device(s) discovered",
                  (unsigned)count);
    }
    return rc;
}

/* ============================================================================
 * PUBLIC API -- INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize the accelerator subsystem
 */
int vos3_accel_init(void)
{
    vos3_spinlock_lock(&g_accel_lock);

    if (g_initialized != 0) {
        vos3_spinlock_unlock(&g_accel_lock);
        VOS3_WARN("[ACCEL] Already initialized");
        return 0;
    }

    /* Clear device table and statistics */
    g_num_devices = 0;
    memset(g_devices, 0, sizeof(g_devices));
    memset(&g_stats, 0, sizeof(g_stats));

    vos3_spinlock_unlock(&g_accel_lock);

    VOS3_INFO("[ACCEL] Initializing accelerator subsystem...");

    /* 1. CPU fallback -- must succeed */
    int rc = accel_register_cpu();
    if (rc != 0) {
        VOS3_ERROR("[ACCEL] Failed to register CPU fallback");
        return -1;
    }

    /* 2. GPU probe (optional) */
    (void)accel_probe_gpu();

    /* 3. NPU probe (future, optional) */
    (void)accel_probe_npu();

    vos3_spinlock_lock(&g_accel_lock);
    g_initialized = 1;
    vos3_spinlock_unlock(&g_accel_lock);

    VOS3_INFO("[ACCEL] Subsystem ready: %u device(s) registered",
              (unsigned)vos3_accel_num_devices());

    return 0;
}

/* ============================================================================
 * PUBLIC API -- QUERIES
 * ============================================================================ */

/**
 * @brief Get the number of registered accelerator devices
 */
uint32_t vos3_accel_num_devices(void)
{
    vos3_spinlock_lock(&g_accel_lock);
    uint32_t n = g_num_devices;
    vos3_spinlock_unlock(&g_accel_lock);
    return n;
}

/**
 * @brief Get a read-only pointer to a device descriptor
 */
const vos3_accel_device_t *vos3_accel_get_device(uint32_t id)
{
    vos3_spinlock_lock(&g_accel_lock);

    if (id >= g_num_devices) {
        vos3_spinlock_unlock(&g_accel_lock);
        return NULL;
    }

    const vos3_accel_device_t *dev = &g_devices[id];
    vos3_spinlock_unlock(&g_accel_lock);

    return dev;
}

/**
 * @brief Get the preferred device ID for a given operation
 *
 * @details Scans all online devices and returns the one with the highest
 *          TOPS rating whose capability bitmask includes the flag required
 *          for the given operation.  Ties are broken by device ID (lower
 *          ID wins, which means hardware accelerators registered after CPU
 *          are preferred at equal TOPS due to being checked last).
 *
 *          If no device matches, returns 0 (CPU fallback, always index 0).
 */
int vos3_accel_get_preferred(vos3_accel_op_t op)
{
    uint32_t required_cap = accel_op_to_cap(op);
    int best_id = 0;               /* Default: CPU at index 0 */
    uint32_t best_tops = 0;

    vos3_spinlock_lock(&g_accel_lock);

    for (uint32_t i = 0; i < g_num_devices; i++) {
        const vos3_accel_device_t *dev = &g_devices[i];

        if (dev->online == 0) {
            continue;
        }

        /* Check if this device has the required capability */
        if ((dev->caps & required_cap) == 0) {
            continue;
        }

        /* Prefer higher TOPS; on tie, prefer higher ID (hardware over CPU) */
        if (dev->tops > best_tops ||
            (dev->tops == best_tops && i > (uint32_t)best_id)) {
            best_tops = dev->tops;
            best_id = (int)i;
        }
    }

    vos3_spinlock_unlock(&g_accel_lock);

    return best_id;
}

/**
 * @brief Get subsystem-wide statistics
 */
int vos3_accel_get_stats(vos3_accel_stats_t *stats)
{
    if (stats == NULL) {
        return -1;
    }

    vos3_spinlock_lock(&g_accel_lock);

    memcpy(stats, &g_stats, sizeof(vos3_accel_stats_t));
    stats->num_devices = g_num_devices;

    /* Compute preferred device for generic operations */
    vos3_spinlock_unlock(&g_accel_lock);

    stats->preferred_device = (uint32_t)vos3_accel_get_preferred(
                                            VOS3_ACCEL_OP_MATMUL);

    return 0;
}

/* ============================================================================
 * PUBLIC API -- DISPATCH
 * ============================================================================ */

/**
 * @brief Dispatch an operation to the best available accelerator
 */
int vos3_accel_dispatch(vos3_accel_dispatch_t *req)
{
    if (req == NULL) {
        return -EINVAL;
    }

    if ((uint32_t)req->op >= (uint32_t)VOS3_ACCEL_OP_COUNT) {
        return -EINVAL;
    }

    /* Find the best device for this operation */
    int dev_id = vos3_accel_get_preferred(req->op);
    int is_fallback = 0;
    int rc = 0;

    vos3_spinlock_lock(&g_accel_lock);
    vos3_accel_type_t dev_type = g_devices[dev_id].type;
    vos3_spinlock_unlock(&g_accel_lock);

    /*
     * Route to the selected device.
     *
     * For GPU and NPU: real hardware dispatch is Phase 6.  For now, if the
     * preferred device is GPU or NPU, we still call the CPU fallback but
     * record it as a fallback event.  This ensures functional correctness
     * while the hardware dispatch paths are under development.
     */
    if (dev_type == VOS3_ACCEL_GPU || dev_type == VOS3_ACCEL_NPU) {
        /*
         * TODO(Phase 6): Replace with real GPU/NPU dispatch.
         * For now, fall back to CPU and record fallback stats.
         */
        is_fallback = 1;
        rc = cpu_fallback_dispatch(req);
    } else {
        /* CPU path */
        rc = cpu_fallback_dispatch(req);
    }

    /* Update statistics */
    vos3_spinlock_lock(&g_accel_lock);

    g_stats.total_dispatches++;

    if (is_fallback) {
        g_stats.fallback_count++;
        g_stats.cpu_dispatches++;
    } else {
        switch (dev_type) {
        case VOS3_ACCEL_CPU:
            g_stats.cpu_dispatches++;
            break;
        case VOS3_ACCEL_GPU:
            g_stats.gpu_dispatches++;
            break;
        case VOS3_ACCEL_NPU:
            g_stats.npu_dispatches++;
            break;
        default:
            g_stats.cpu_dispatches++;
            break;
        }
    }

    /* Update per-device counters */
    if (dev_id >= 0 && (uint32_t)dev_id < g_num_devices) {
        if (rc == 0) {
            g_devices[dev_id].ops_dispatched++;
        } else {
            g_devices[dev_id].ops_failed++;
        }
    }

    vos3_spinlock_unlock(&g_accel_lock);

    return rc;
}
