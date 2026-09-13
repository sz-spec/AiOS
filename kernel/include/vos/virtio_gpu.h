/**
 * @file virtio_gpu.h
 * @brief VOS3 VirtIO-GPU 3D Driver — Guest GPU for QEMU virgl/Venus
 *
 * @details Implements the VirtIO-GPU device specification (OASIS virtio-v1.2,
 *          §5.7). Provides 2D scanout, 3D resource management, and GPU
 *          command submission for compute shader dispatch via SPIR-V.
 *
 *          QEMU flags: -device virtio-gpu-gl (virgl) or -device virtio-gpu
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.1 — GPU + Compute Foundation
 */

#ifndef VOS3_VIRTIO_GPU_H
#define VOS3_VIRTIO_GPU_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "virtio_core.h"

/* ============================================================================
 * VIRTIO-GPU PCI IDENTITY
 * ============================================================================ */

/** @brief VirtIO-GPU PCI device ID (non-transitional) */
#define VIRTIO_GPU_DEVICE_ID            0x1050U

/** @brief VirtIO-GPU PCI device ID (transitional, legacy) */
#define VIRTIO_GPU_DEVICE_ID_LEGACY     0x1040U

/** @brief VirtIO-GPU subsystem ID (for transitional lookup) */
#define VIRTIO_GPU_SUBSYS_ID            16U

/* ============================================================================
 * VIRTIO-GPU FEATURE BITS (spec §5.7.3)
 * ============================================================================ */

/** @brief Device supports virgl 3D mode */
#define VIRTIO_GPU_F_VIRGL              (1U << 0)

/** @brief Device supports EDID queries */
#define VIRTIO_GPU_F_EDID               (1U << 1)

/** @brief Device supports resource UUID assignment */
#define VIRTIO_GPU_F_RESOURCE_UUID      (1U << 2)

/** @brief Device supports resource blob objects */
#define VIRTIO_GPU_F_RESOURCE_BLOB      (1U << 3)

/** @brief Device supports context init feature */
#define VIRTIO_GPU_F_CONTEXT_INIT       (1U << 4)

/* ============================================================================
 * VIRTIO-GPU COMMAND TYPES (spec §5.7.6.7)
 * ============================================================================ */

typedef enum virtio_gpu_ctrl_type {
    /* 2D commands */
    VIRTIO_GPU_CMD_GET_DISPLAY_INFO       = 0x0100,
    VIRTIO_GPU_CMD_RESOURCE_CREATE_2D     = 0x0101,
    VIRTIO_GPU_CMD_RESOURCE_UNREF         = 0x0102,
    VIRTIO_GPU_CMD_SET_SCANOUT            = 0x0103,
    VIRTIO_GPU_CMD_RESOURCE_FLUSH         = 0x0104,
    VIRTIO_GPU_CMD_TRANSFER_TO_HOST_2D    = 0x0105,
    VIRTIO_GPU_CMD_RESOURCE_ATTACH_BACKING= 0x0106,
    VIRTIO_GPU_CMD_RESOURCE_DETACH_BACKING= 0x0107,
    VIRTIO_GPU_CMD_GET_CAPSET_INFO        = 0x0108,
    VIRTIO_GPU_CMD_GET_CAPSET             = 0x0109,
    VIRTIO_GPU_CMD_GET_EDID               = 0x010A,
    VIRTIO_GPU_CMD_RESOURCE_ASSIGN_UUID   = 0x010B,
    VIRTIO_GPU_CMD_RESOURCE_CREATE_BLOB   = 0x010C,
    VIRTIO_GPU_CMD_SET_SCANOUT_BLOB       = 0x010D,

    /* 3D commands */
    VIRTIO_GPU_CMD_CTX_CREATE             = 0x0200,
    VIRTIO_GPU_CMD_CTX_DESTROY            = 0x0201,
    VIRTIO_GPU_CMD_CTX_ATTACH_RESOURCE    = 0x0202,
    VIRTIO_GPU_CMD_CTX_DETACH_RESOURCE    = 0x0203,
    VIRTIO_GPU_CMD_SUBMIT_3D              = 0x0204,
    VIRTIO_GPU_CMD_TRANSFER_TO_HOST_3D    = 0x0205,
    VIRTIO_GPU_CMD_TRANSFER_FROM_HOST_3D  = 0x0206,

    /* Cursor commands */
    VIRTIO_GPU_CMD_UPDATE_CURSOR          = 0x0300,
    VIRTIO_GPU_CMD_MOVE_CURSOR            = 0x0301,

    /* Success/error responses */
    VIRTIO_GPU_RESP_OK_NODATA             = 0x1100,
    VIRTIO_GPU_RESP_OK_DISPLAY_INFO       = 0x1101,
    VIRTIO_GPU_RESP_OK_CAPSET_INFO        = 0x1102,
    VIRTIO_GPU_RESP_OK_CAPSET             = 0x1103,
    VIRTIO_GPU_RESP_OK_EDID               = 0x1104,
    VIRTIO_GPU_RESP_OK_RESOURCE_UUID      = 0x1105,
    VIRTIO_GPU_RESP_OK_MAP_INFO           = 0x1106,

    VIRTIO_GPU_RESP_ERR_UNSPEC            = 0x1200,
    VIRTIO_GPU_RESP_ERR_OUT_OF_MEMORY     = 0x1201,
    VIRTIO_GPU_RESP_ERR_INVALID_SCANOUT   = 0x1202,
    VIRTIO_GPU_RESP_ERR_INVALID_RESOURCE  = 0x1203,
    VIRTIO_GPU_RESP_ERR_INVALID_CONTEXT   = 0x1204,
    VIRTIO_GPU_RESP_ERR_INVALID_PARAMETER = 0x1205
} virtio_gpu_ctrl_type_t;

/* ============================================================================
 * VIRTIO-GPU PIXEL FORMATS
 * ============================================================================ */

typedef enum virtio_gpu_formats {
    VIRTIO_GPU_FORMAT_B8G8R8A8_UNORM  = 1,
    VIRTIO_GPU_FORMAT_B8G8R8X8_UNORM  = 2,
    VIRTIO_GPU_FORMAT_A8R8G8B8_UNORM  = 3,
    VIRTIO_GPU_FORMAT_X8R8G8B8_UNORM  = 4,
    VIRTIO_GPU_FORMAT_R8G8B8A8_UNORM  = 67,
    VIRTIO_GPU_FORMAT_X8B8G8R8_UNORM  = 68,
    VIRTIO_GPU_FORMAT_A8B8G8R8_UNORM  = 121,
    VIRTIO_GPU_FORMAT_R8G8B8X8_UNORM  = 134
} virtio_gpu_formats_t;

/* ============================================================================
 * VIRTIO-GPU STRUCTURES (spec §5.7.6)
 * ============================================================================ */

/** @brief Common GPU control header (24 bytes) */
typedef struct virtio_gpu_ctrl_hdr {
    uint32_t type;          /**< Command type (virtio_gpu_ctrl_type_t) */
    uint32_t flags;         /**< Command flags (VIRTIO_GPU_FLAG_FENCE = 1) */
    uint64_t fence_id;      /**< Fence ID for synchronization */
    uint32_t ctx_id;        /**< 3D rendering context */
    uint8_t  ring_idx;      /**< Ring index (for multi-ring) */
    uint8_t  padding[3];
} __attribute__((packed)) virtio_gpu_ctrl_hdr_t;

/** @brief Display rectangle */
typedef struct virtio_gpu_rect {
    uint32_t x;
    uint32_t y;
    uint32_t width;
    uint32_t height;
} __attribute__((packed)) virtio_gpu_rect_t;

/** @brief Display information for a single scanout */
typedef struct virtio_gpu_display_one {
    virtio_gpu_rect_t r;
    uint32_t enabled;
    uint32_t flags;
} __attribute__((packed)) virtio_gpu_display_one_t;

/** @brief Maximum number of scanouts */
#define VIRTIO_GPU_MAX_SCANOUTS     16

/** @brief Response to GET_DISPLAY_INFO */
typedef struct virtio_gpu_resp_display_info {
    virtio_gpu_ctrl_hdr_t hdr;
    virtio_gpu_display_one_t pmodes[VIRTIO_GPU_MAX_SCANOUTS];
} __attribute__((packed)) virtio_gpu_resp_display_info_t;

/** @brief RESOURCE_CREATE_2D command */
typedef struct virtio_gpu_resource_create_2d {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t resource_id;
    uint32_t format;        /**< virtio_gpu_formats_t */
    uint32_t width;
    uint32_t height;
} __attribute__((packed)) virtio_gpu_resource_create_2d_t;

/** @brief RESOURCE_UNREF command */
typedef struct virtio_gpu_resource_unref {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t resource_id;
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_resource_unref_t;

/** @brief Memory entry for resource backing */
typedef struct virtio_gpu_mem_entry {
    uint64_t addr;          /**< Physical address */
    uint32_t length;        /**< Length in bytes */
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_mem_entry_t;

/** @brief RESOURCE_ATTACH_BACKING command */
typedef struct virtio_gpu_resource_attach_backing {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t resource_id;
    uint32_t nr_entries;
    /* Followed by nr_entries virtio_gpu_mem_entry_t */
} __attribute__((packed)) virtio_gpu_resource_attach_backing_t;

/** @brief SET_SCANOUT command */
typedef struct virtio_gpu_set_scanout {
    virtio_gpu_ctrl_hdr_t hdr;
    virtio_gpu_rect_t r;
    uint32_t scanout_id;
    uint32_t resource_id;
} __attribute__((packed)) virtio_gpu_set_scanout_t;

/** @brief TRANSFER_TO_HOST_2D command */
typedef struct virtio_gpu_transfer_to_host_2d {
    virtio_gpu_ctrl_hdr_t hdr;
    virtio_gpu_rect_t r;
    uint64_t offset;
    uint32_t resource_id;
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_transfer_to_host_2d_t;

/** @brief RESOURCE_FLUSH command */
typedef struct virtio_gpu_resource_flush {
    virtio_gpu_ctrl_hdr_t hdr;
    virtio_gpu_rect_t r;
    uint32_t resource_id;
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_resource_flush_t;

/* ============================================================================
 * 3D RENDERING STRUCTURES
 * ============================================================================ */

/** @brief CTX_CREATE command */
typedef struct virtio_gpu_ctx_create {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t nlen;          /**< Context name length */
    uint32_t context_init;  /**< Initialization flags */
    char     debug_name[64];/**< Context name for debugging */
} __attribute__((packed)) virtio_gpu_ctx_create_t;

/** @brief CTX_DESTROY command */
typedef struct virtio_gpu_ctx_destroy {
    virtio_gpu_ctrl_hdr_t hdr;
} __attribute__((packed)) virtio_gpu_ctx_destroy_t;

/** @brief CTX_ATTACH_RESOURCE command */
typedef struct virtio_gpu_ctx_attach_resource {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t resource_id;
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_ctx_attach_resource_t;

/** @brief SUBMIT_3D command */
typedef struct virtio_gpu_cmd_submit {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t size;          /**< Size of command buffer in bytes */
    uint32_t padding;
    /* Followed by `size` bytes of 3D command buffer data */
} __attribute__((packed)) virtio_gpu_cmd_submit_t;

/** @brief TRANSFER_TO_HOST_3D command */
typedef struct virtio_gpu_transfer_host_3d {
    virtio_gpu_ctrl_hdr_t hdr;
    virtio_gpu_rect_t box;          /**< Bounding box (x, y, w, h used as 3D) */
    uint64_t offset;
    uint32_t resource_id;
    uint32_t level;
    uint32_t stride;
    uint32_t layer_stride;
} __attribute__((packed)) virtio_gpu_transfer_host_3d_t;

/** @brief GET_CAPSET_INFO command */
typedef struct virtio_gpu_get_capset_info {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t capset_index;
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_get_capset_info_t;

/** @brief Response to GET_CAPSET_INFO */
typedef struct virtio_gpu_resp_capset_info {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t capset_id;
    uint32_t capset_max_version;
    uint32_t capset_max_size;
    uint32_t padding;
} __attribute__((packed)) virtio_gpu_resp_capset_info_t;

/** @brief GET_CAPSET command */
typedef struct virtio_gpu_get_capset {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t capset_id;
    uint32_t capset_version;
} __attribute__((packed)) virtio_gpu_get_capset_t;

/** @brief RESOURCE_CREATE_BLOB command */
typedef struct virtio_gpu_resource_create_blob {
    virtio_gpu_ctrl_hdr_t hdr;
    uint32_t resource_id;
    uint32_t blob_mem;      /**< Memory type (guest/host3d/host3d-guest) */
    uint32_t blob_flags;    /**< Mapping flags */
    uint32_t nr_entries;
    uint64_t blob_id;
    uint64_t size;
    /* Followed by nr_entries virtio_gpu_mem_entry_t */
} __attribute__((packed)) virtio_gpu_resource_create_blob_t;

/* ============================================================================
 * VIRTIO-GPU DEVICE CONFIGURATION (spec §5.7.4)
 * ============================================================================ */

typedef struct virtio_gpu_config {
    uint32_t events_read;   /**< Pending events bitmap */
    uint32_t events_clear;  /**< Events to clear */
    uint32_t num_scanouts;  /**< Number of scanouts (1-16) */
    uint32_t num_capsets;   /**< Number of capability sets */
} __attribute__((packed)) virtio_gpu_config_t;

/** @brief Display configuration changed event */
#define VIRTIO_GPU_EVENT_DISPLAY    (1U << 0)

/* ============================================================================
 * FENCE FLAG
 * ============================================================================ */

/** @brief Command requests fence signaling on completion */
#define VIRTIO_GPU_FLAG_FENCE       (1U << 0)

/** @brief Info_ring_idx field is valid */
#define VIRTIO_GPU_FLAG_INFO_RING_IDX (1U << 1)

/* ============================================================================
 * GPU RESOURCE TRACKING
 * ============================================================================ */

/** @brief Maximum tracked GPU resources */
#define VOS3_GPU_MAX_RESOURCES      256U

/** @brief Maximum 3D rendering contexts */
#define VOS3_GPU_MAX_CONTEXTS       16U

/** @brief GPU resource descriptor */
typedef struct vos3_gpu_resource {
    uint32_t id;            /**< Resource ID (0 = unused) */
    uint32_t format;        /**< Pixel format */
    uint32_t width;
    uint32_t height;
    uintptr_t backing_phys; /**< Physical address of backing pages */
    size_t   backing_size;  /**< Size of backing memory */
    uint8_t  attached;      /**< 1 if backing is attached */
    uint8_t  in_use;        /**< 1 if resource is allocated */
    uint8_t  _pad[2];
} vos3_gpu_resource_t;

/** @brief GPU 3D context descriptor */
typedef struct vos3_gpu_context {
    uint32_t id;            /**< Context ID (0 = unused) */
    uint32_t active;        /**< 1 if context is created */
    char     name[64];      /**< Debug name */
} vos3_gpu_context_t;

/* ============================================================================
 * DEVICE STATE
 * ============================================================================ */

/** @brief VirtIO-GPU device state */
typedef struct vos3_virtio_gpu {
    /* PCI location */
    uint8_t  bus;
    uint8_t  dev;
    uint8_t  func;
    uint8_t  _pad0;

    /* I/O base (legacy) or MMIO base */
    uint16_t io_base;
    uint16_t _pad1;

    /* Feature negotiation */
    uint32_t host_features;
    uint32_t guest_features;

    /* Configuration */
    uint32_t num_scanouts;
    uint32_t num_capsets;

    /* Virtqueues: 0=controlq, 1=cursorq */
    virtio_queue_t controlq;
    virtio_queue_t cursorq;

    /* DMA memory for queues */
    void    *controlq_dma;
    void    *cursorq_dma;

    /* Command/response bounce buffers (DMA-visible) */
    void    *cmd_buf;       /**< Command buffer (page-aligned, 4KB) */
    void    *resp_buf;      /**< Response buffer (page-aligned, 4KB) */
    uint64_t cmd_buf_phys;
    uint64_t resp_buf_phys;

    /* Resource tracking */
    vos3_gpu_resource_t resources[VOS3_GPU_MAX_RESOURCES];
    uint32_t next_resource_id;

    /* 3D context tracking */
    vos3_gpu_context_t contexts[VOS3_GPU_MAX_CONTEXTS];
    uint32_t next_context_id;

    /* Fence tracking */
    uint64_t next_fence_id;

    /* Status */
    uint32_t initialized;
    uint32_t virgl_supported;   /**< 1 if 3D virgl is available */
} vos3_virtio_gpu_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize VirtIO-GPU device
 *
 * Scans PCI bus for VirtIO-GPU device, negotiates features,
 * sets up control and cursor virtqueues, and queries display info.
 *
 * @return 0 on success, -1 if device not found or init failed
 */
int vos3_virtio_gpu_init(void);

/**
 * @brief Check if VirtIO-GPU is available and initialized
 *
 * @return 1 if GPU is ready, 0 otherwise
 */
int vos3_virtio_gpu_available(void);

/**
 * @brief Check if 3D virgl rendering is supported
 *
 * @return 1 if virgl is available, 0 otherwise
 */
int vos3_virtio_gpu_has_virgl(void);

/**
 * @brief Get display information for all scanouts
 *
 * @param[out] info Array of display_one structures (VIRTIO_GPU_MAX_SCANOUTS)
 * @return Number of enabled scanouts, or -1 on error
 */
int vos3_virtio_gpu_get_display_info(virtio_gpu_display_one_t *info);

/* ---- 2D Resource Management ---- */

/**
 * @brief Create a 2D resource (framebuffer or texture)
 *
 * @param[in] width  Width in pixels
 * @param[in] height Height in pixels
 * @param[in] format Pixel format (virtio_gpu_formats_t)
 * @return Resource ID (>0) on success, 0 on failure
 */
uint32_t vos3_virtio_gpu_resource_create_2d(uint32_t width, uint32_t height,
                                             uint32_t format);

/**
 * @brief Attach physical memory backing to a resource
 *
 * @param[in] resource_id Resource to attach backing to
 * @param[in] phys_addr   Physical address of backing pages
 * @param[in] size        Size of backing memory in bytes
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_resource_attach_backing(uint32_t resource_id,
                                             uint64_t phys_addr,
                                             uint32_t size);

/**
 * @brief Release a GPU resource
 *
 * @param[in] resource_id Resource to release
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_resource_unref(uint32_t resource_id);

/**
 * @brief Set a resource as the scanout source
 *
 * @param[in] scanout_id  Scanout index (0-based)
 * @param[in] resource_id Resource to display
 * @param[in] width       Display width
 * @param[in] height      Display height
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_set_scanout(uint32_t scanout_id, uint32_t resource_id,
                                 uint32_t width, uint32_t height);

/**
 * @brief Transfer resource data from guest to host
 *
 * @param[in] resource_id Resource to transfer
 * @param[in] x           Transfer region X offset
 * @param[in] y           Transfer region Y offset
 * @param[in] width       Transfer region width
 * @param[in] height      Transfer region height
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_transfer_to_host_2d(uint32_t resource_id,
                                         uint32_t x, uint32_t y,
                                         uint32_t width, uint32_t height);

/**
 * @brief Flush resource to display
 *
 * @param[in] resource_id Resource to flush
 * @param[in] x           Flush region X offset
 * @param[in] y           Flush region Y offset
 * @param[in] width       Flush region width
 * @param[in] height      Flush region height
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_flush(uint32_t resource_id,
                           uint32_t x, uint32_t y,
                           uint32_t width, uint32_t height);

/* ---- 3D Context Management ---- */

/**
 * @brief Create a 3D rendering context (requires virgl)
 *
 * @param[in] name Debug name for the context
 * @return Context ID (>0) on success, 0 on failure
 */
uint32_t vos3_virtio_gpu_ctx_create(const char *name);

/**
 * @brief Destroy a 3D rendering context
 *
 * @param[in] ctx_id Context to destroy
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_ctx_destroy(uint32_t ctx_id);

/**
 * @brief Attach a resource to a 3D context
 *
 * @param[in] ctx_id      Context ID
 * @param[in] resource_id Resource to attach
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_ctx_attach_resource(uint32_t ctx_id,
                                         uint32_t resource_id);

/**
 * @brief Submit a 3D command buffer for execution
 *
 * Submits virgl/gallium command stream to the host GPU via
 * VIRTIO_GPU_CMD_SUBMIT_3D. Used for both rendering and compute.
 *
 * @param[in] ctx_id  Context ID
 * @param[in] cmdbuf  Command buffer data
 * @param[in] size    Size of command buffer in bytes
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_submit_3d(uint32_t ctx_id, const void *cmdbuf,
                               uint32_t size);

/* ---- Capability Sets ---- */

/**
 * @brief Query capability set information
 *
 * @param[in]  index            Capset index (0-based)
 * @param[out] capset_id        Capset identifier
 * @param[out] capset_max_ver   Maximum version
 * @param[out] capset_max_size  Maximum data size
 * @return 0 on success, -1 on failure
 */
int vos3_virtio_gpu_get_capset_info(uint32_t index,
                                     uint32_t *capset_id,
                                     uint32_t *capset_max_ver,
                                     uint32_t *capset_max_size);

/* ---- Fence Synchronization ---- */

/**
 * @brief Wait for a GPU fence to be signaled
 *
 * @param[in] fence_id Fence to wait for
 * @param[in] timeout_ms Maximum wait time in milliseconds (0 = poll)
 * @return 0 if signaled, -1 on timeout
 */
int vos3_virtio_gpu_fence_wait(uint64_t fence_id, uint32_t timeout_ms);

/* ---- Statistics ---- */

/**
 * @brief Get GPU statistics for diagnostics
 *
 * @param[out] resources_used  Number of allocated resources
 * @param[out] contexts_used   Number of active contexts
 * @param[out] cmds_submitted  Total commands submitted
 */
void vos3_virtio_gpu_stats(uint32_t *resources_used,
                            uint32_t *contexts_used,
                            uint64_t *cmds_submitted);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VIRTIO_GPU_H */
