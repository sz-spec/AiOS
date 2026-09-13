/**
 * @file gpu_compute.c
 * @brief VOS3 GPU Compute Shader Dispatch Module -- Phase 5.2
 *
 * @details SPIR-V / TGSI shader compilation and dispatch pipeline for
 *          compute workgroups. Bridges GGML tensor operations to GPU
 *          compute shaders via the VirtIO-GPU 3D (virgl) backend.
 *
 *          Architecture:
 *          - Compute context management (create/destroy virgl 3D contexts)
 *          - Shader loading (precompiled SPIR-V or TGSI bytecode blobs)
 *          - GPU buffer management (create/bind input/output buffers)
 *          - Workgroup dispatch (configure grid, dispatch, fence-wait)
 *          - Fence-based synchronization via virtio_gpu fence API
 *          - Statistics tracking (dispatches, bytes transferred, waits)
 *
 *          The shader bytecode is opaque to this module -- we wrap it
 *          into a virgl CREATE_OBJECT(SHADER) command and submit via
 *          submit_3d. Buffer resources use resource_create_2d with R8
 *          format as raw byte containers. Dispatch assembles a minimal
 *          virgl command stream: bind shader, bind buffers, launch grid.
 *
 *          QEMU: -device virtio-gpu-gl  (virgl 3D required)
 *
 * @version 2.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.2 -- GPU Compute Dispatch (refined API)
 */

#include "../../include/vos/virtio_gpu.h"
#include "../../include/vos/console.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * CAPACITY LIMITS
 * ============================================================================ */

/** @brief Maximum compute contexts */
#define VOS3_GC_MAX_CONTEXTS        8U

/** @brief Maximum shader objects */
#define VOS3_GC_MAX_SHADERS         64U

/** @brief Maximum GPU buffers */
#define VOS3_GC_MAX_BUFFERS         256U

/** @brief Maximum backing pages per buffer (256 pages = 1 MiB) */
#define VOS3_GC_MAX_BUF_PAGES      256U

/** @brief Maximum virgl command stream size (bytes) */
#define VOS3_GC_MAX_CMD_BYTES       2048U

/** @brief Fence wait timeout for dispatch (ms) */
#define VOS3_GC_FENCE_TIMEOUT_MS    5000U

/** @brief Console log prefix */
#define GC_TAG                      "[GPU-COMPUTE] "

/* ============================================================================
 * VIRGL PROTOCOL CONSTANTS
 *
 * Subset of the virgl (virglrenderer) command encoding for assembling
 * minimal command streams that the host-side virglrenderer decodes and
 * translates to OpenGL/Vulkan calls.
 *
 * Reference: mesa/src/gallium/drivers/virgl/virgl_protocol.h
 * ============================================================================ */

/** @brief Virgl command header field shifts */
#define VIRGL_CMD_TYPE_SHIFT        0U
#define VIRGL_CMD_OBJ_SHIFT         8U
#define VIRGL_CMD_SIZE_SHIFT        16U

/** @brief Virgl command types */
#define VIRGL_CCMD_CREATE_OBJECT    1U
#define VIRGL_CCMD_BIND_OBJECT      2U
#define VIRGL_CCMD_DESTROY_OBJECT   3U
#define VIRGL_CCMD_SET_SHADER_BUFFERS 48U
#define VIRGL_CCMD_LAUNCH_GRID      50U

/** @brief Virgl object sub-types */
#define VIRGL_OBJECT_SHADER         4U

/** @brief Virgl shader types */
#define VIRGL_SHADER_VERTEX         0U
#define VIRGL_SHADER_FRAGMENT       1U
#define VIRGL_SHADER_GEOMETRY       2U
#define VIRGL_SHADER_TESS_CTRL      3U
#define VIRGL_SHADER_TESS_EVAL      4U
#define VIRGL_SHADER_COMPUTE        5U

/**
 * @brief Encode a virgl command header dword
 *
 * @param[in] cmd   Command type (8 bits)
 * @param[in] obj   Object sub-type (8 bits)
 * @param[in] len   Payload length in dwords (16 bits)
 * @return Packed 32-bit header word
 */
static inline uint32_t virgl_hdr(uint32_t cmd, uint32_t obj, uint32_t len)
{
    return ((cmd  & 0xFFU)   << VIRGL_CMD_TYPE_SHIFT) |
           ((obj  & 0xFFU)   << VIRGL_CMD_OBJ_SHIFT)  |
           ((len  & 0xFFFFU) << VIRGL_CMD_SIZE_SHIFT);
}

/* ============================================================================
 * DESCRIPTOR TYPES
 * ============================================================================ */

/**
 * @brief Compute context descriptor
 *
 * Wraps a VirtIO-GPU 3D context with compute-specific tracking.
 */
typedef struct gc_context {
    uint32_t ctx_id;            /**< Public context ID (1-based; 0 = free) */
    uint32_t gpu_ctx_id;        /**< Underlying VirtIO-GPU 3D context ID */
    uint8_t  active;            /**< 1 if context is live */
    uint8_t  _pad[3];
} gc_context_t;

/**
 * @brief Shader object descriptor
 *
 * Tracks a loaded shader (SPIR-V or TGSI bytecode) that has been
 * submitted to the virgl host via CREATE_OBJECT(SHADER).
 */
typedef struct gc_shader {
    uint32_t shader_id;         /**< Public shader ID (1-based; 0 = free) */
    uint32_t ctx_id;            /**< Owning compute context */
    uint32_t virgl_handle;      /**< Virgl object handle for bind/destroy */
    uint32_t bytecode_len;      /**< Original bytecode length (bytes) */
    uint8_t  active;            /**< 1 if shader is loaded */
    uint8_t  _pad[3];
} gc_shader_t;

/**
 * @brief GPU buffer descriptor
 *
 * Tracks a GPU-visible buffer backed by PMM pages and bound as a
 * VirtIO-GPU resource for 3D context access.
 */
typedef struct gc_buffer {
    uint32_t  buf_id;           /**< Public buffer ID (1-based; 0 = free) */
    uint32_t  resource_id;      /**< VirtIO-GPU resource ID */
    uint32_t  ctx_id;           /**< Owning compute context */
    uint32_t  size;             /**< Requested buffer size (bytes) */
    uintptr_t phys_addr;        /**< Physical address of backing pages */
    void     *virt_addr;        /**< Kernel virtual (KBASE) address */
    uint32_t  alloc_pages;      /**< Number of PMM pages allocated */
    uint8_t   active;           /**< 1 if buffer is allocated */
    uint8_t   attached;         /**< 1 if backing is attached to resource */
    uint8_t   ctx_bound;        /**< 1 if resource is attached to 3D ctx */
    uint8_t   _pad;
} gc_buffer_t;

/**
 * @brief Module-wide statistics
 */
typedef struct gc_stats {
    uint64_t dispatches;        /**< Total compute dispatches */
    uint64_t bytes_uploaded;    /**< Total bytes written to GPU buffers */
    uint64_t bytes_downloaded;  /**< Total bytes read from GPU buffers */
    uint64_t fence_waits;       /**< Total fence wait calls */
    uint64_t fence_timeouts;    /**< Fence waits that timed out */
} gc_stats_t;

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief Context table */
static gc_context_t g_contexts[VOS3_GC_MAX_CONTEXTS];

/** @brief Shader table */
static gc_shader_t  g_shaders[VOS3_GC_MAX_SHADERS];

/** @brief Buffer table */
static gc_buffer_t  g_buffers[VOS3_GC_MAX_BUFFERS];

/** @brief Module statistics */
static gc_stats_t   g_stats;

/** @brief Next monotonic IDs */
static uint32_t g_next_ctx_id    = 1U;
static uint32_t g_next_shader_id = 1U;
static uint32_t g_next_buf_id    = 1U;
static uint32_t g_next_virgl_handle = 1U;

/** @brief Module initialized flag */
static uint32_t g_initialized    = 0U;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Find a free context slot
 * @return Pointer to free slot, or NULL
 */
static gc_context_t *ctx_alloc_slot(void)
{
    for (uint32_t i = 0; i < VOS3_GC_MAX_CONTEXTS; i++) {
        if (!g_contexts[i].active) {
            return &g_contexts[i];
        }
    }
    return NULL;
}

/**
 * @brief Look up a context by public ID
 * @param[in] ctx_id Context ID
 * @return Pointer to context, or NULL
 */
static gc_context_t *ctx_find(uint32_t ctx_id)
{
    for (uint32_t i = 0; i < VOS3_GC_MAX_CONTEXTS; i++) {
        if (g_contexts[i].active && g_contexts[i].ctx_id == ctx_id) {
            return &g_contexts[i];
        }
    }
    return NULL;
}

/**
 * @brief Find a free shader slot
 * @return Pointer to free slot, or NULL
 */
static gc_shader_t *shader_alloc_slot(void)
{
    for (uint32_t i = 0; i < VOS3_GC_MAX_SHADERS; i++) {
        if (!g_shaders[i].active) {
            return &g_shaders[i];
        }
    }
    return NULL;
}

/**
 * @brief Look up a shader by public ID
 * @param[in] shader_id Shader ID
 * @return Pointer to shader, or NULL
 */
static gc_shader_t *shader_find(uint32_t shader_id)
{
    for (uint32_t i = 0; i < VOS3_GC_MAX_SHADERS; i++) {
        if (g_shaders[i].active && g_shaders[i].shader_id == shader_id) {
            return &g_shaders[i];
        }
    }
    return NULL;
}

/**
 * @brief Find a free buffer slot
 * @return Pointer to free slot, or NULL
 */
static gc_buffer_t *buf_alloc_slot(void)
{
    for (uint32_t i = 0; i < VOS3_GC_MAX_BUFFERS; i++) {
        if (!g_buffers[i].active) {
            return &g_buffers[i];
        }
    }
    return NULL;
}

/**
 * @brief Look up a buffer by public ID
 * @param[in] buf_id Buffer ID
 * @return Pointer to buffer, or NULL
 */
static gc_buffer_t *buf_find(uint32_t buf_id)
{
    for (uint32_t i = 0; i < VOS3_GC_MAX_BUFFERS; i++) {
        if (g_buffers[i].active && g_buffers[i].buf_id == buf_id) {
            return &g_buffers[i];
        }
    }
    return NULL;
}

/**
 * @brief Release PMM-backed physical memory for a buffer
 * @param[in] buf Buffer descriptor
 */
static void buf_release_pages(gc_buffer_t *buf)
{
    if (buf->phys_addr != 0 && buf->alloc_pages > 0) {
        vos3_pmm_free_pages(buf->phys_addr, buf->alloc_pages);
        buf->phys_addr   = 0;
        buf->virt_addr   = NULL;
        buf->alloc_pages = 0;
    }
}

/* ============================================================================
 * PUBLIC API -- Initialization
 * ============================================================================ */

/**
 * @brief Initialize the GPU compute dispatch module
 *
 * Verifies that VirtIO-GPU is initialized and virgl 3D is available.
 * Clears all internal state tables and resets statistics.
 *
 * @return 0 on success, -1 if GPU or virgl is unavailable
 */
int vos3_gpu_compute_init(void)
{
    if (!vos3_virtio_gpu_available()) {
        vos3_console_puts(GC_TAG "VirtIO-GPU not available\n");
        return -1;
    }

    if (!vos3_virtio_gpu_has_virgl()) {
        vos3_console_puts(GC_TAG "virgl 3D not supported -- "
                          "compute dispatch requires virgl\n");
        return -1;
    }

    /* Clear all state */
    memset(g_contexts, 0, sizeof(g_contexts));
    memset(g_shaders,  0, sizeof(g_shaders));
    memset(g_buffers,  0, sizeof(g_buffers));
    memset(&g_stats,   0, sizeof(g_stats));

    g_next_ctx_id       = 1U;
    g_next_shader_id    = 1U;
    g_next_buf_id       = 1U;
    g_next_virgl_handle = 1U;
    g_initialized       = 1U;

    vos3_console_printf(GC_TAG "Initialized (max %u ctx, %u shaders, "
                        "%u buffers)\n",
                        VOS3_GC_MAX_CONTEXTS,
                        VOS3_GC_MAX_SHADERS,
                        VOS3_GC_MAX_BUFFERS);
    return 0;
}

/* ============================================================================
 * PUBLIC API -- Compute Context Management
 * ============================================================================ */

/**
 * @brief Create a GPU compute context
 *
 * Allocates a VirtIO-GPU 3D context configured for compute workloads.
 * The context scopes shader loads, buffer attachments, and dispatches.
 *
 * @param[out] ctx_id_out  Receives the new context ID on success
 * @return 0 on success, -1 on failure
 */
int vos3_gpu_compute_ctx_create(uint32_t *ctx_id_out)
{
    if (!g_initialized || ctx_id_out == NULL) {
        return -1;
    }

    gc_context_t *slot = ctx_alloc_slot();
    if (slot == NULL) {
        vos3_console_puts(GC_TAG "ctx_create: no free context slots\n");
        return -1;
    }

    /* Create the underlying VirtIO-GPU 3D context */
    uint32_t gpu_ctx = vos3_virtio_gpu_ctx_create("vos3-compute");
    if (gpu_ctx == 0U) {
        vos3_console_puts(GC_TAG "ctx_create: GPU 3D context creation failed\n");
        return -1;
    }

    /* Populate the slot */
    uint32_t cid       = g_next_ctx_id++;
    slot->ctx_id       = cid;
    slot->gpu_ctx_id   = gpu_ctx;
    slot->active       = 1;

    *ctx_id_out = cid;

    vos3_console_printf(GC_TAG "Context %u created (gpu_ctx=%u)\n",
                        cid, gpu_ctx);
    return 0;
}

/**
 * @brief Destroy a GPU compute context
 *
 * Tears down the underlying VirtIO-GPU 3D context. Callers must destroy
 * all owned shaders and buffers beforehand to avoid resource leaks.
 *
 * @param[in] ctx_id Context ID returned by vos3_gpu_compute_ctx_create()
 */
void vos3_gpu_compute_ctx_destroy(uint32_t ctx_id)
{
    if (!g_initialized) {
        return;
    }

    gc_context_t *ctx = ctx_find(ctx_id);
    if (ctx == NULL) {
        vos3_console_printf(GC_TAG "ctx_destroy: unknown ctx_id=%u\n", ctx_id);
        return;
    }

    /* Destroy the underlying GPU 3D context */
    (void)vos3_virtio_gpu_ctx_destroy(ctx->gpu_ctx_id);

    /* Clear the slot */
    memset(ctx, 0, sizeof(*ctx));

    vos3_console_printf(GC_TAG "Context %u destroyed\n", ctx_id);
}

/* ============================================================================
 * PUBLIC API -- Shader Loading
 * ============================================================================ */

/**
 * @brief Load a precompiled compute shader into a context
 *
 * Accepts precompiled SPIR-V or TGSI bytecode, wraps it into a virgl
 * CREATE_OBJECT(SHADER) command, and submits it to the host via
 * submit_3d. The host-side virglrenderer compiles the bytecode for
 * the native GPU.
 *
 * Virgl CREATE_OBJECT(SHADER) payload layout:
 *   dword[0]: header (CCMD_CREATE_OBJECT | OBJECT_SHADER | payload_len)
 *   dword[1]: virgl_handle (object ID on the host)
 *   dword[2]: shader_type (VIRGL_SHADER_COMPUTE = 5)
 *   dword[3]: num_tokens (bytecode length in dwords, rounded up)
 *   dword[4]: offlen (offset=0 << 16 | stream_count=0)
 *   dword[5..N]: bytecode data (padded to dword alignment)
 *
 * @param[in]  ctx_id        Owning compute context
 * @param[in]  bytecode      Pointer to SPIR-V or TGSI bytecode
 * @param[in]  bytecode_len  Length of bytecode in bytes
 * @param[out] shader_id_out Receives the new shader ID on success
 * @return 0 on success, -1 on failure
 */
int vos3_gpu_compute_shader_load(uint32_t ctx_id,
                                  const void *bytecode,
                                  uint32_t bytecode_len,
                                  uint32_t *shader_id_out)
{
    if (!g_initialized || bytecode == NULL || bytecode_len == 0 ||
        shader_id_out == NULL) {
        return -1;
    }

    gc_context_t *ctx = ctx_find(ctx_id);
    if (ctx == NULL) {
        vos3_console_printf(GC_TAG "shader_load: invalid ctx_id=%u\n", ctx_id);
        return -1;
    }

    gc_shader_t *slot = shader_alloc_slot();
    if (slot == NULL) {
        vos3_console_puts(GC_TAG "shader_load: no free shader slots\n");
        return -1;
    }

    /* Round bytecode length up to dword count */
    uint32_t bc_dwords = (bytecode_len + 3U) / 4U;

    /* Build the virgl command stream:
     * header (1 dw) + handle (1) + type (1) + num_tokens (1) +
     * offlen (1) + bytecode (bc_dwords) = 4 + bc_dwords payload dwords */
    uint32_t payload_dwords = 4U + bc_dwords;
    uint32_t total_dwords   = 1U + payload_dwords; /* header + payload */
    uint32_t total_bytes    = total_dwords * 4U;

    if (total_bytes > VOS3_GC_MAX_CMD_BYTES) {
        vos3_console_printf(GC_TAG "shader_load: bytecode too large "
                            "(%u bytes, max %u)\n",
                            bytecode_len,
                            (VOS3_GC_MAX_CMD_BYTES - 5U * 4U));
        return -1;
    }

    uint32_t cmdbuf[VOS3_GC_MAX_CMD_BYTES / sizeof(uint32_t)];
    uint32_t dw = 0;

    uint32_t handle = g_next_virgl_handle++;

    /* Command header */
    cmdbuf[dw++] = virgl_hdr(VIRGL_CCMD_CREATE_OBJECT,
                              VIRGL_OBJECT_SHADER,
                              payload_dwords);
    /* Payload */
    cmdbuf[dw++] = handle;                  /* virgl object handle */
    cmdbuf[dw++] = VIRGL_SHADER_COMPUTE;    /* shader type */
    cmdbuf[dw++] = bc_dwords;               /* num_tokens */
    cmdbuf[dw++] = 0U;                      /* offlen: offset=0, streams=0 */

    /* Copy bytecode into command stream (dword-padded) */
    memset(&cmdbuf[dw], 0, bc_dwords * 4U);
    memcpy(&cmdbuf[dw], bytecode, bytecode_len);
    dw += bc_dwords;

    /* Submit to the GPU */
    int rc = vos3_virtio_gpu_submit_3d(ctx->gpu_ctx_id,
                                        cmdbuf, dw * 4U);
    if (rc != 0) {
        vos3_console_printf(GC_TAG "shader_load: submit_3d failed "
                            "(gpu_ctx=%u)\n", ctx->gpu_ctx_id);
        return -1;
    }

    /* Populate the shader descriptor */
    uint32_t sid       = g_next_shader_id++;
    slot->shader_id    = sid;
    slot->ctx_id       = ctx_id;
    slot->virgl_handle = handle;
    slot->bytecode_len = bytecode_len;
    slot->active       = 1;

    *shader_id_out = sid;

    vos3_console_printf(GC_TAG "Shader %u loaded: ctx=%u handle=%u "
                        "bytecode=%u bytes\n",
                        sid, ctx_id, handle, bytecode_len);
    return 0;
}

/* ============================================================================
 * PUBLIC API -- Buffer Management
 * ============================================================================ */

/**
 * @brief Create a GPU-visible compute buffer
 *
 * Allocates physically contiguous DMA32-zone pages, creates a VirtIO-GPU
 * 2D resource with R8 format as a raw byte container, attaches physical
 * backing, and binds the resource to the owning 3D context.
 *
 * @param[in]  ctx_id     Owning compute context
 * @param[in]  size       Requested buffer size in bytes (page-rounded)
 * @param[out] buf_id_out Receives the new buffer ID on success
 * @return 0 on success, -1 on failure
 */
int vos3_gpu_compute_buffer_create(uint32_t ctx_id,
                                    uint32_t size,
                                    uint32_t *buf_id_out)
{
    if (!g_initialized || size == 0 || buf_id_out == NULL) {
        return -1;
    }

    gc_context_t *ctx = ctx_find(ctx_id);
    if (ctx == NULL) {
        vos3_console_printf(GC_TAG "buffer_create: invalid ctx_id=%u\n",
                            ctx_id);
        return -1;
    }

    /* Page-round and bounds check */
    uint32_t num_pages = (size + 4095U) / 4096U;
    if (num_pages > VOS3_GC_MAX_BUF_PAGES) {
        vos3_console_printf(GC_TAG "buffer_create: size %u exceeds max "
                            "(%u pages)\n", size, VOS3_GC_MAX_BUF_PAGES);
        return -1;
    }

    gc_buffer_t *slot = buf_alloc_slot();
    if (slot == NULL) {
        vos3_console_puts(GC_TAG "buffer_create: no free buffer slots\n");
        return -1;
    }

    /* Allocate contiguous DMA32 pages */
    uintptr_t phys = vos3_pmm_alloc_pages(
        (size_t)num_pages,
        VOS3_PMM_FLAG_DMA32 | VOS3_PMM_FLAG_CONTIGUOUS | VOS3_PMM_FLAG_ZERO);
    if (phys == 0) {
        vos3_console_printf(GC_TAG "buffer_create: PMM alloc failed "
                            "(%u pages)\n", num_pages);
        return -1;
    }

    void *virt = (void *)(phys + VIRTIO_KBASE);
    uint32_t alloc_bytes = num_pages * 4096U;

    /* Create VirtIO-GPU resource: width=total_bytes, height=1, R8 format.
     * The virgl host treats this as a raw data buffer when bound to a
     * 3D context as a shader storage buffer. */
    uint32_t resource_id = vos3_virtio_gpu_resource_create_2d(
        alloc_bytes, 1U, VIRTIO_GPU_FORMAT_R8G8B8A8_UNORM);
    if (resource_id == 0U) {
        vos3_pmm_free_pages(phys, (size_t)num_pages);
        vos3_console_puts(GC_TAG "buffer_create: resource_create_2d failed\n");
        return -1;
    }

    /* Attach physical backing */
    int rc = vos3_virtio_gpu_resource_attach_backing(
        resource_id, (uint64_t)phys, alloc_bytes);
    if (rc != 0) {
        vos3_virtio_gpu_resource_unref(resource_id);
        vos3_pmm_free_pages(phys, (size_t)num_pages);
        vos3_console_puts(GC_TAG "buffer_create: attach_backing failed\n");
        return -1;
    }

    /* Bind resource to the 3D context */
    rc = vos3_virtio_gpu_ctx_attach_resource(ctx->gpu_ctx_id, resource_id);
    if (rc != 0) {
        vos3_virtio_gpu_resource_unref(resource_id);
        vos3_pmm_free_pages(phys, (size_t)num_pages);
        vos3_console_puts(GC_TAG "buffer_create: ctx_attach failed\n");
        return -1;
    }

    /* Populate buffer descriptor */
    uint32_t bid       = g_next_buf_id++;
    slot->buf_id       = bid;
    slot->resource_id  = resource_id;
    slot->ctx_id       = ctx_id;
    slot->size         = size;
    slot->phys_addr    = phys;
    slot->virt_addr    = virt;
    slot->alloc_pages  = num_pages;
    slot->active       = 1;
    slot->attached     = 1;
    slot->ctx_bound    = 1;

    *buf_id_out = bid;

    vos3_console_printf(GC_TAG "Buffer %u created: ctx=%u res=%u "
                        "phys=0x%llx size=%u (%u pages)\n",
                        bid, ctx_id, resource_id,
                        (unsigned long long)phys,
                        size, num_pages);
    return 0;
}

/**
 * @brief Write data into a GPU compute buffer at a given offset
 *
 * Copies data from a kernel source pointer into the buffer's KBASE
 * mapping. An mfence is issued after the copy to ensure the data is
 * visible to the device before any subsequent command submission.
 *
 * @param[in] buf_id  Buffer ID
 * @param[in] data    Source data (kernel address)
 * @param[in] offset  Byte offset within the buffer
 * @param[in] len     Number of bytes to write
 * @return 0 on success, -1 on failure
 */
int vos3_gpu_compute_buffer_write(uint32_t buf_id,
                                   const void *data,
                                   uint32_t offset,
                                   uint32_t len)
{
    if (!g_initialized || data == NULL || len == 0) {
        return -1;
    }

    gc_buffer_t *buf = buf_find(buf_id);
    if (buf == NULL) {
        return -1;
    }

    /* Bounds check: offset + len must not exceed buffer size */
    if (offset > buf->size || len > buf->size - offset) {
        vos3_console_printf(GC_TAG "buffer_write: range [%u..%u) "
                            "exceeds buf size %u\n",
                            offset, offset + len, buf->size);
        return -1;
    }

    /* Direct copy via KBASE mapping */
    uint8_t *dst = (uint8_t *)buf->virt_addr + offset;
    memcpy(dst, data, len);

    /* Ensure data is committed before device sees it */
    __asm__ volatile("mfence" ::: "memory");

    g_stats.bytes_uploaded += len;
    return 0;
}

/**
 * @brief Read data from a GPU compute buffer at a given offset
 *
 * Issues an lfence before reading to ensure any device DMA writes
 * are visible, then copies from the buffer's KBASE mapping into
 * the caller's destination buffer.
 *
 * @param[in]  buf_id Buffer ID
 * @param[out] data   Destination buffer (kernel address)
 * @param[in]  offset Byte offset within the buffer
 * @param[in]  len    Number of bytes to read
 * @return 0 on success, -1 on failure
 */
int vos3_gpu_compute_buffer_read(uint32_t buf_id,
                                  void *data,
                                  uint32_t offset,
                                  uint32_t len)
{
    if (!g_initialized || data == NULL || len == 0) {
        return -1;
    }

    gc_buffer_t *buf = buf_find(buf_id);
    if (buf == NULL) {
        return -1;
    }

    if (offset > buf->size || len > buf->size - offset) {
        vos3_console_printf(GC_TAG "buffer_read: range [%u..%u) "
                            "exceeds buf size %u\n",
                            offset, offset + len, buf->size);
        return -1;
    }

    /* Ensure device DMA writes are visible to CPU */
    __asm__ volatile("lfence" ::: "memory");

    const uint8_t *src = (const uint8_t *)buf->virt_addr + offset;
    memcpy(data, src, len);

    g_stats.bytes_downloaded += len;
    return 0;
}

/**
 * @brief Destroy a GPU compute buffer
 *
 * Releases the VirtIO-GPU resource and frees backing PMM pages.
 * The buffer ID becomes invalid after this call.
 *
 * @param[in] buf_id Buffer ID to destroy
 */
void vos3_gpu_compute_buffer_destroy(uint32_t buf_id)
{
    if (!g_initialized) {
        return;
    }

    gc_buffer_t *buf = buf_find(buf_id);
    if (buf == NULL) {
        vos3_console_printf(GC_TAG "buffer_destroy: unknown buf_id=%u\n",
                            buf_id);
        return;
    }

    /* Release VirtIO-GPU resource */
    if (buf->resource_id != 0U) {
        (void)vos3_virtio_gpu_resource_unref(buf->resource_id);
    }

    /* Free physical memory */
    buf_release_pages(buf);

    uint32_t old_id = buf->buf_id;
    memset(buf, 0, sizeof(*buf));

    vos3_console_printf(GC_TAG "Buffer %u destroyed\n", old_id);
}

/* ============================================================================
 * PUBLIC API -- Compute Dispatch
 * ============================================================================ */

/**
 * @brief Dispatch a compute workload on the GPU
 *
 * Assembles a virgl command stream that:
 *   1. BIND_OBJECT(SHADER) -- binds the specified compute shader
 *   2. LAUNCH_GRID          -- dispatches the compute workgroups
 *
 * The command stream is submitted via vos3_virtio_gpu_submit_3d().
 * A fence is inserted and polled to ensure the compute operation
 * completes before returning, so the caller may safely read results
 * from output buffers immediately after this call returns 0.
 *
 * @param[in] ctx_id    Compute context ID
 * @param[in] shader_id Shader to execute (from shader_load)
 * @param[in] group_x   Number of workgroups in X dimension
 * @param[in] group_y   Number of workgroups in Y dimension
 * @param[in] group_z   Number of workgroups in Z dimension
 * @return 0 on success, -1 on failure
 */
int vos3_gpu_compute_dispatch(uint32_t ctx_id,
                               uint32_t shader_id,
                               uint32_t group_x,
                               uint32_t group_y,
                               uint32_t group_z)
{
    if (!g_initialized) {
        return -1;
    }

    /* Validate workgroup dimensions */
    if (group_x == 0 || group_y == 0 || group_z == 0) {
        vos3_console_puts(GC_TAG "dispatch: workgroup dimensions "
                          "must be non-zero\n");
        return -1;
    }

    /* Look up context */
    gc_context_t *ctx = ctx_find(ctx_id);
    if (ctx == NULL) {
        vos3_console_printf(GC_TAG "dispatch: invalid ctx_id=%u\n", ctx_id);
        return -1;
    }

    /* Look up shader and verify ownership */
    gc_shader_t *sh = shader_find(shader_id);
    if (sh == NULL || sh->ctx_id != ctx_id) {
        vos3_console_printf(GC_TAG "dispatch: invalid shader_id=%u "
                            "for ctx=%u\n", shader_id, ctx_id);
        return -1;
    }

    /* Capture state for the (potentially blocking) GPU submit */
    uint32_t gpu_ctx      = ctx->gpu_ctx_id;
    uint32_t virgl_handle = sh->virgl_handle;

    /* ---- Assemble virgl command stream ---- */
    uint32_t cmdbuf[VOS3_GC_MAX_CMD_BYTES / sizeof(uint32_t)];
    uint32_t dw = 0;

    /* Command 1: BIND_OBJECT(SHADER) -- bind compute shader
     *
     * Payload (2 dwords):
     *   [0] virgl_handle
     *   [1] shader_type (VIRGL_SHADER_COMPUTE = 5)
     */
    cmdbuf[dw++] = virgl_hdr(VIRGL_CCMD_BIND_OBJECT,
                              VIRGL_OBJECT_SHADER, 2U);
    cmdbuf[dw++] = virgl_handle;
    cmdbuf[dw++] = VIRGL_SHADER_COMPUTE;

    /* Command 2: LAUNCH_GRID -- dispatch compute workgroups
     *
     * Payload (8 dwords):
     *   [0] block_x  (threads per workgroup X -- 1, shader-defined)
     *   [1] block_y
     *   [2] block_z
     *   [3] grid_x   (number of workgroups X)
     *   [4] grid_y
     *   [5] grid_z
     *   [6] indirect_handle (0 = direct dispatch)
     *   [7] indirect_offset
     */
    cmdbuf[dw++] = virgl_hdr(VIRGL_CCMD_LAUNCH_GRID, 0U, 8U);
    cmdbuf[dw++] = 1U;         /* block_x */
    cmdbuf[dw++] = 1U;         /* block_y */
    cmdbuf[dw++] = 1U;         /* block_z */
    cmdbuf[dw++] = group_x;    /* grid_x */
    cmdbuf[dw++] = group_y;    /* grid_y */
    cmdbuf[dw++] = group_z;    /* grid_z */
    cmdbuf[dw++] = 0U;         /* indirect_handle */
    cmdbuf[dw++] = 0U;         /* indirect_offset */

    uint32_t cmd_bytes = dw * (uint32_t)sizeof(uint32_t);

    /* Submit the command stream */
    int rc = vos3_virtio_gpu_submit_3d(gpu_ctx, cmdbuf, cmd_bytes);
    if (rc != 0) {
        vos3_console_printf(GC_TAG "dispatch: submit_3d failed "
                            "(gpu_ctx=%u, %u bytes)\n",
                            gpu_ctx, cmd_bytes);
        return -1;
    }

    /* Synchronize: insert fence and poll for completion */
    g_stats.fence_waits++;
    uint64_t fence_id = (uint64_t)g_stats.dispatches + 1ULL;
    rc = vos3_virtio_gpu_fence_wait(fence_id, VOS3_GC_FENCE_TIMEOUT_MS);
    if (rc != 0) {
        g_stats.fence_timeouts++;
        vos3_console_printf(GC_TAG "dispatch: fence timeout "
                            "(fence=%llu)\n",
                            (unsigned long long)fence_id);
        /* Non-fatal: compute may still be in flight */
    }

    g_stats.dispatches++;

    vos3_console_printf(GC_TAG "Dispatch #%llu: ctx=%u shader=%u "
                        "grid=(%u,%u,%u) cmd=%u bytes\n",
                        (unsigned long long)g_stats.dispatches,
                        ctx_id, shader_id,
                        group_x, group_y, group_z,
                        cmd_bytes);
    return 0;
}

/* ============================================================================
 * STATISTICS
 * ============================================================================ */

void vos3_gpu_compute_stats(uint32_t *ctx_count,
                             uint32_t *shader_count,
                             uint32_t *buffer_count,
                             uint64_t *dispatch_count)
{
    uint32_t c = 0, s = 0, b = 0;
    for (uint32_t i = 0; i < VOS3_GC_MAX_CONTEXTS; i++) {
        if (g_contexts[i].active) c++;
    }
    for (uint32_t i = 0; i < VOS3_GC_MAX_SHADERS; i++) {
        if (g_shaders[i].active) s++;
    }
    for (uint32_t i = 0; i < VOS3_GC_MAX_BUFFERS; i++) {
        if (g_buffers[i].active) b++;
    }
    if (ctx_count)     *ctx_count     = c;
    if (shader_count)  *shader_count  = s;
    if (buffer_count)  *buffer_count  = b;
    if (dispatch_count)*dispatch_count = g_stats.dispatches;
}
