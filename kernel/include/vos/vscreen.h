/**
 * @file vscreen.h
 * @brief VOS3 vScreen — Zero-Copy Framebuffer Perception Layer
 *
 * @details Captures GPU scanout framebuffers into TENSOR guard regions,
 *          performs integer-only bilinear downscaling into vision-model
 *          patch tiles (224x224 or 384x384), and dispatches DMA copies
 *          to NPU SRAM for VLA inference.
 *
 *          Privacy model: pixel data lives only in kernel VA (TENSOR
 *          region, > 0xFFFF800000000000) and NPU SRAM. No
 *          TRANSFER_TO_HOST_2D call — backing_phys read directly.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1: Agentic Mesh & vScreen Perception
 */

#ifndef VOS3_VSCREEN_H
#define VOS3_VSCREEN_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief One capture state per model slot */
#define VSCREEN_MAX_CAPTURES    4U

/** @brief ViT-B/16 input tile size */
#define VSCREEN_PATCH_224       224U

/** @brief ViT-L/14 input tile size */
#define VSCREEN_PATCH_384       384U

/** @brief Framebuffer bytes per pixel (BGRA) */
#define VSCREEN_BPP             4U

/** @brief Output bytes per pixel (RGB) */
#define VSCREEN_RGB_BPP         3U

/** @brief Maximum patches per capture frame */
#define VSCREEN_MAX_PATCHES     16U

/** @brief Maximum supported framebuffer width */
#define VSCREEN_MAX_WIDTH       1920U

/** @brief Maximum supported framebuffer height */
#define VSCREEN_MAX_HEIGHT      1080U

/* Flags for vscreen_start() */
#define VSCREEN_FLAG_RGB            (1U << 0)   /**< Convert BGRA to RGB */
#define VSCREEN_FLAG_DOWNSCALE      (1U << 1)   /**< Enable bilinear downscale */
#define VSCREEN_FLAG_NPU_DISPATCH   (1U << 2)   /**< Auto-dispatch to NPU SRAM */

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Per-tile patch descriptor.
 */
typedef struct vscreen_patch {
    uintptr_t   phys_addr;      /**< Physical address of patch data */
    void       *virt_addr;      /**< Kernel VA of patch data */
    uint32_t    width;          /**< Patch width in pixels */
    uint32_t    height;         /**< Patch height in pixels */
    uint32_t    stride;         /**< Bytes per row */
    uint32_t    size_bytes;     /**< Total patch size */
    uint8_t     dispatched;     /**< 1 = dispatched to NPU */
} vscreen_patch_t;

/**
 * @brief Per-slot capture state.
 */
typedef struct vscreen_capture {
    uint8_t         slot_id;        /**< Owning model slot */
    uint8_t         active;         /**< 1 = capture running */
    uint8_t         flags;          /**< VSCREEN_FLAG_* bitmask */
    uint8_t         patch_size;     /**< 224 or 384 */
    uint32_t        scanout_id;     /**< GPU scanout resource ID */

    /* Framebuffer source */
    uint32_t        fb_width;       /**< Scanout width */
    uint32_t        fb_height;      /**< Scanout height */
    uintptr_t       fb_phys;        /**< Scanout backing_phys */
    size_t          fb_size;        /**< Framebuffer size in bytes */

    /* TENSOR region for capture data */
    void           *tensor_base;    /**< Kernel VA of TENSOR alloc */
    uintptr_t       tensor_phys;    /**< Physical addr of TENSOR alloc */
    size_t          tensor_size;    /**< TENSOR region size */
    void           *guard_ctx;      /**< AI Guard context (opaque) */

    /* Patch tiles */
    vscreen_patch_t patches[VSCREEN_MAX_PATCHES];
    uint32_t        patch_count;    /**< Active patches */

    /* Statistics */
    uint32_t        captures_done;  /**< Total captures performed */
    uint64_t        total_cycles;   /**< Cumulative TSC for captures */
    uint64_t        last_cycles;    /**< Last capture latency (TSC) */
} vscreen_capture_t;

/**
 * @brief Global vScreen statistics.
 */
typedef struct vscreen_stats {
    uint32_t    total_captures;     /**< Frames captured */
    uint32_t    total_patches;      /**< Patches extracted */
    uint32_t    total_dispatches;   /**< NPU dispatches */
    uint32_t    alloc_failures;     /**< TENSOR alloc failures */
    uint64_t    total_cycles;       /**< Aggregate capture cycles */
} vscreen_stats_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize the vScreen subsystem (zero all capture state).
 * @return 0 on success
 */
int vscreen_init(void);

/**
 * @brief Start capture for a model slot.
 *
 * @param slot_id     Model slot (must have VOS3_CAP_VISION)
 * @param scanout_id  GPU resource ID (stored for reference)
 * @param fb_phys     Physical address of framebuffer backing
 * @param fb_width    Framebuffer width in pixels
 * @param fb_height   Framebuffer height in pixels
 * @param patch_size  Target patch dimension (224 or 384)
 * @param flags       VSCREEN_FLAG_* bitmask
 * @return 0 on success, -EINVAL, -EPERM, -ENOMEM on failure
 */
int vscreen_start(uint8_t slot_id, uint32_t scanout_id,
                  uintptr_t fb_phys, uint32_t fb_width, uint32_t fb_height,
                  uint8_t patch_size, uint8_t flags);

/**
 * @brief Capture one frame from the scanout into TENSOR region.
 *
 * @param slot_id  Model slot with active capture
 * @return 0 on success, -EINVAL if not active
 */
int vscreen_capture(uint8_t slot_id);

/**
 * @brief Read a patch tile into user buffer.
 *
 * @param slot_id    Model slot
 * @param patch_idx  Patch index (0..patch_count-1)
 * @param out_buf    Destination buffer
 * @param buf_size   Buffer capacity
 * @return 0 on success, -EINVAL on bad index, -ENOBUFS if too small
 */
int vscreen_get_frame(uint8_t slot_id, uint32_t patch_idx,
                      void *out_buf, size_t buf_size);

/**
 * @brief Stop capture and free TENSOR region.
 *
 * @param slot_id  Model slot
 * @return 0 on success, -EINVAL if not active
 */
int vscreen_stop(uint8_t slot_id);

/**
 * @brief Get aggregate vScreen statistics.
 * @param out  Output stats structure
 */
void vscreen_get_stats(vscreen_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VSCREEN_H */
