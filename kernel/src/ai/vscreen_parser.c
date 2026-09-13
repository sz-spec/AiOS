/**
 * @file vscreen_parser.c
 * @brief VOS3 vScreen — Zero-Copy Framebuffer Perception Implementation
 *
 * @details Captures GPU scanout framebuffers into TENSOR guard regions,
 *          performs integer-only bilinear downscaling, and dispatches
 *          patch tiles to NPU SRAM.
 *
 *          All arithmetic is integer-only (CFLAGS: -mno-sse -mno-sse2).
 *          Bilinear downscale uses 16.16 fixed-point.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1: Agentic Mesh & vScreen Perception
 */

#include "../../include/vos/vscreen.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/console.h"
#include "../../include/vos/dma_warp.h"
#include "../../include/vos/npu.h"
#include "../../include/vos/virtio_vbus.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * STATIC STATE (~2KB BSS)
 * ============================================================================ */

static vscreen_capture_t g_captures[VSCREEN_MAX_CAPTURES];
static vscreen_stats_t   g_vscreen_stats;
static uint8_t           g_vscreen_initialized;

/* External: model slot array for capability checks */
extern vos3_ai_model_slot_t g_model_slots[];

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void vs_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) {
        p[i] = 0;
    }
}

static void vs_memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; i++) {
        d[i] = s[i];
    }
}

/**
 * @brief Integer-only bilinear downscale of one tile.
 *
 * @details Uses 16.16 fixed-point arithmetic. Reads BGRA source,
 *          writes RGB output. No FPU instructions.
 *
 *          For each output pixel (ox, oy), compute the corresponding
 *          source coordinate in fixed-point, then bilinear interpolate
 *          the four surrounding source pixels.
 *
 * @param src        Source BGRA framebuffer
 * @param src_w      Source width
 * @param src_h      Source height
 * @param src_stride Source bytes per row
 * @param dst        Destination RGB buffer
 * @param dst_w      Destination width (224 or 384)
 * @param dst_h      Destination height (224 or 384)
 * @param src_x0     Source X origin (tile offset)
 * @param src_y0     Source Y origin (tile offset)
 * @param tile_w     Tile width in source pixels
 * @param tile_h     Tile height in source pixels
 */
static void vscreen_downscale_tile(const uint8_t *src,
                                   uint32_t src_w, uint32_t src_h,
                                   uint32_t src_stride,
                                   uint8_t *dst,
                                   uint32_t dst_w, uint32_t dst_h,
                                   uint32_t src_x0, uint32_t src_y0,
                                   uint32_t tile_w, uint32_t tile_h)
{
    /* 16.16 fixed-point scale factors */
    uint32_t x_ratio = (tile_w << 16) / dst_w;
    uint32_t y_ratio = (tile_h << 16) / dst_h;

    (void)src_w;
    (void)src_h;

    for (uint32_t oy = 0; oy < dst_h; oy++) {
        uint32_t sy_fp = oy * y_ratio;
        uint32_t sy    = (sy_fp >> 16) + src_y0;
        uint32_t fy    = sy_fp & 0xFFFF;  /* fractional part */

        /* Clamp to tile boundary */
        uint32_t sy1 = sy + 1;
        if (sy1 >= src_y0 + tile_h) {
            sy1 = sy;
        }

        for (uint32_t ox = 0; ox < dst_w; ox++) {
            uint32_t sx_fp = ox * x_ratio;
            uint32_t sx    = (sx_fp >> 16) + src_x0;
            uint32_t fx    = sx_fp & 0xFFFF;

            uint32_t sx1 = sx + 1;
            if (sx1 >= src_x0 + tile_w) {
                sx1 = sx;
            }

            /* Read four BGRA neighbors */
            const uint8_t *p00 = src + sy  * src_stride + sx  * VSCREEN_BPP;
            const uint8_t *p10 = src + sy  * src_stride + sx1 * VSCREEN_BPP;
            const uint8_t *p01 = src + sy1 * src_stride + sx  * VSCREEN_BPP;
            const uint8_t *p11 = src + sy1 * src_stride + sx1 * VSCREEN_BPP;

            /* Bilinear interpolation for each RGB channel.
             * Source is BGRA: [0]=B, [1]=G, [2]=R.
             * Output is RGB: [0]=R, [1]=G, [2]=B.
             *
             * Weight: w00 = (65536-fx)*(65536-fy) >> 16
             *         Shift by 16 again when combining to get 8-bit result.
             */
            uint32_t w00 = ((65536U - fx) >> 8) * ((65536U - fy) >> 8);
            uint32_t w10 = (fx >> 8) * ((65536U - fy) >> 8);
            uint32_t w01 = ((65536U - fx) >> 8) * (fy >> 8);
            uint32_t w11 = (fx >> 8) * (fy >> 8);
            uint32_t wsum = w00 + w10 + w01 + w11;
            if (wsum == 0) wsum = 1;

            /* R channel (source index 2) */
            uint32_t r = (p00[2] * w00 + p10[2] * w10 +
                          p01[2] * w01 + p11[2] * w11) / wsum;
            /* G channel (source index 1) */
            uint32_t g = (p00[1] * w00 + p10[1] * w10 +
                          p01[1] * w01 + p11[1] * w11) / wsum;
            /* B channel (source index 0) */
            uint32_t b = (p00[0] * w00 + p10[0] * w10 +
                          p01[0] * w01 + p11[0] * w11) / wsum;

            uint8_t *out = dst + (oy * dst_w + ox) * VSCREEN_RGB_BPP;
            out[0] = (uint8_t)(r > 255 ? 255 : r);
            out[1] = (uint8_t)(g > 255 ? 255 : g);
            out[2] = (uint8_t)(b > 255 ? 255 : b);
        }
    }
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vscreen_init(void)
{
    vs_memzero(g_captures, sizeof(g_captures));
    vs_memzero(&g_vscreen_stats, sizeof(g_vscreen_stats));
    g_vscreen_initialized = 1;
    VOS3_INFO("[VSCREEN] Perception layer initialized (%u capture slots)",
              (unsigned)VSCREEN_MAX_CAPTURES);
    return 0;
}

int vscreen_start(uint8_t slot_id, uint32_t scanout_id,
                  uintptr_t fb_phys, uint32_t fb_width, uint32_t fb_height,
                  uint8_t patch_size, uint8_t flags)
{
    if (!g_vscreen_initialized) return -22; /* EINVAL */
    if (slot_id >= VSCREEN_MAX_CAPTURES) return -22;

    /* Validate slot has VOS3_CAP_VISION */
    if (slot_id < VOS3_MODEL_SLOT_MAX) {
        uint64_t caps = g_model_slots[slot_id].capabilities;
        if (!(caps & VOS3_CAP_VISION)) {
            VOS3_WARN("[VSCREEN] Slot %u lacks VOS3_CAP_VISION", slot_id);
            return -1; /* EPERM */
        }
    }

    /* Validate patch size */
    if (patch_size != 224 && patch_size != 128 && patch_size != 0) {
        /* Accept 224 or 384 (stored as patch_size=224 or 384) */
    }
    uint32_t ps = (patch_size == 0) ? VSCREEN_PATCH_224 : (uint32_t)patch_size;
    if (ps != VSCREEN_PATCH_224 && ps != VSCREEN_PATCH_384) {
        ps = VSCREEN_PATCH_224;
    }

    /* Validate framebuffer dimensions */
    if (fb_width == 0 || fb_width > VSCREEN_MAX_WIDTH ||
        fb_height == 0 || fb_height > VSCREEN_MAX_HEIGHT) {
        return -22; /* EINVAL */
    }

    if (fb_phys == 0) return -22; /* EINVAL */

    vscreen_capture_t *cap = &g_captures[slot_id];
    if (cap->active) {
        VOS3_WARN("[VSCREEN] Slot %u already active, stopping first", slot_id);
        vscreen_stop(slot_id);
    }

    /* Calculate TENSOR region size:
     * Full framebuffer copy + patch tiles.
     * FB: width * height * BGRA_BPP
     * Patches: grid_x * grid_y * ps * ps * RGB_BPP
     */
    size_t fb_size = (size_t)fb_width * fb_height * VSCREEN_BPP;
    uint32_t grid_x = fb_width / ps;
    uint32_t grid_y = fb_height / ps;
    if (grid_x == 0) grid_x = 1;
    if (grid_y == 0) grid_y = 1;
    uint32_t n_patches = grid_x * grid_y;
    if (n_patches > VSCREEN_MAX_PATCHES) n_patches = VSCREEN_MAX_PATCHES;

    size_t patch_total = (size_t)n_patches * ps * ps * VSCREEN_RGB_BPP;
    size_t tensor_size = fb_size + patch_total;

    /* Align to page boundary */
    tensor_size = (tensor_size + 0xFFF) & ~(size_t)0xFFF;

    /* Allocate TENSOR region via AI Guard.
     * Create a dedicated guard context for this capture session.
     */
    vos3_ai_guard_ctx_t *ctx = vos3_ai_guard_ctx_create();
    if (ctx == NULL) {
        g_vscreen_stats.alloc_failures++;
        return -12; /* ENOMEM */
    }

    void *tensor_base = vos3_ai_guard_alloc(ctx, tensor_size,
        VOS3_AI_GUARD_TENSOR, VOS3_AI_FLAG_CHECKSUMMED);
    if (tensor_base == NULL) {
        vos3_ai_guard_ctx_destroy(ctx);
        g_vscreen_stats.alloc_failures++;
        VOS3_WARN("[VSCREEN] TENSOR alloc failed for slot %u (%zu bytes)",
                  slot_id, tensor_size);
        return -12; /* ENOMEM */
    }

    /* Populate capture state */
    cap->slot_id     = slot_id;
    cap->active      = 1;
    cap->flags       = flags;
    cap->patch_size  = (uint8_t)ps;
    cap->scanout_id  = scanout_id;
    cap->fb_width    = fb_width;
    cap->fb_height   = fb_height;
    cap->fb_phys     = fb_phys;
    cap->fb_size     = fb_size;
    cap->tensor_base = tensor_base;
    cap->tensor_phys = (uintptr_t)tensor_base; /* Kernel VA = identity map in higher half */
    cap->tensor_size = tensor_size;
    cap->guard_ctx   = (void *)ctx;
    cap->patch_count = n_patches;

    /* Initialize patch descriptors */
    uint8_t *patch_base = (uint8_t *)tensor_base + fb_size;
    size_t per_patch = (size_t)ps * ps * VSCREEN_RGB_BPP;
    for (uint32_t i = 0; i < n_patches; i++) {
        cap->patches[i].virt_addr  = patch_base + i * per_patch;
        cap->patches[i].phys_addr  = (uintptr_t)(patch_base + i * per_patch);
        cap->patches[i].width      = ps;
        cap->patches[i].height     = ps;
        cap->patches[i].stride     = ps * VSCREEN_RGB_BPP;
        cap->patches[i].size_bytes = (uint32_t)per_patch;
        cap->patches[i].dispatched = 0;
    }

    VOS3_INFO("[VSCREEN] Slot %u started: %ux%u fb, %u patches of %ux%u, "
              "TENSOR=%p (%zu bytes)",
              slot_id, fb_width, fb_height, n_patches, ps, ps,
              tensor_base, tensor_size);
    return 0;
}

int vscreen_capture(uint8_t slot_id)
{
    if (!g_vscreen_initialized) return -22;
    if (slot_id >= VSCREEN_MAX_CAPTURES) return -22;

    vscreen_capture_t *cap = &g_captures[slot_id];
    if (!cap->active) return -22; /* EINVAL: not active */

    uint64_t tsc_start = vos3_rdtsc();

    /* Step 1: Copy framebuffer from backing_phys to TENSOR region.
     * In kernel, fb_phys is identity-mapped in higher half. Use NT copy
     * for cache-friendliness on large framebuffers.
     */
    vos3_dma_nt_copy(cap->tensor_base, (const void *)cap->fb_phys, cap->fb_size);

    /* Step 2: Downscale into patch tiles */
    uint32_t ps = (uint32_t)cap->patch_size;
    uint32_t grid_x = cap->fb_width / ps;
    uint32_t grid_y = cap->fb_height / ps;
    if (grid_x == 0) grid_x = 1;
    if (grid_y == 0) grid_y = 1;

    uint32_t src_stride = cap->fb_width * VSCREEN_BPP;
    uint32_t pi = 0;

    for (uint32_t gy = 0; gy < grid_y && pi < cap->patch_count; gy++) {
        for (uint32_t gx = 0; gx < grid_x && pi < cap->patch_count; gx++) {
            uint32_t sx0 = gx * ps;
            uint32_t sy0 = gy * ps;

            /* Tile size in source: clamp to framebuffer edge */
            uint32_t tw = ps;
            uint32_t th = ps;
            if (sx0 + tw > cap->fb_width) tw = cap->fb_width - sx0;
            if (sy0 + th > cap->fb_height) th = cap->fb_height - sy0;

            if (cap->flags & VSCREEN_FLAG_DOWNSCALE) {
                vscreen_downscale_tile(
                    (const uint8_t *)cap->tensor_base,
                    cap->fb_width, cap->fb_height, src_stride,
                    (uint8_t *)cap->patches[pi].virt_addr,
                    ps, ps,
                    sx0, sy0, tw, th);
            } else {
                /* Direct copy (RGB conversion only) */
                const uint8_t *src_row = (const uint8_t *)cap->tensor_base
                                         + sy0 * src_stride + sx0 * VSCREEN_BPP;
                uint8_t *dst_row = (uint8_t *)cap->patches[pi].virt_addr;
                for (uint32_t y = 0; y < th && y < ps; y++) {
                    for (uint32_t x = 0; x < tw && x < ps; x++) {
                        const uint8_t *px = src_row + x * VSCREEN_BPP;
                        uint8_t *out = dst_row + x * VSCREEN_RGB_BPP;
                        out[0] = px[2]; /* R */
                        out[1] = px[1]; /* G */
                        out[2] = px[0]; /* B */
                    }
                    src_row += src_stride;
                    dst_row += ps * VSCREEN_RGB_BPP;
                }
            }
            cap->patches[pi].dispatched = 0;
            pi++;
        }
    }

    uint64_t tsc_end = vos3_rdtsc();
    uint64_t delta = tsc_end - tsc_start;

    cap->captures_done++;
    cap->total_cycles += delta;
    cap->last_cycles = delta;
    g_vscreen_stats.total_captures++;
    g_vscreen_stats.total_patches += pi;
    g_vscreen_stats.total_cycles += delta;

    /* Emit event */
    vos3_vbus_event_ring_push(VOS3_EVENT_VSCREEN_CAPTURE, slot_id, (uint32_t)delta);

    return 0;
}

int vscreen_get_frame(uint8_t slot_id, uint32_t patch_idx,
                      void *out_buf, size_t buf_size)
{
    if (!g_vscreen_initialized) return -22;
    if (slot_id >= VSCREEN_MAX_CAPTURES) return -22;

    vscreen_capture_t *cap = &g_captures[slot_id];
    if (!cap->active) return -22;
    if (patch_idx >= cap->patch_count) return -22;

    vscreen_patch_t *p = &cap->patches[patch_idx];
    if (buf_size < p->size_bytes) return -105; /* ENOBUFS */

    vs_memcpy(out_buf, p->virt_addr, p->size_bytes);
    return 0;
}

int vscreen_stop(uint8_t slot_id)
{
    if (!g_vscreen_initialized) return -22;
    if (slot_id >= VSCREEN_MAX_CAPTURES) return -22;

    vscreen_capture_t *cap = &g_captures[slot_id];
    if (!cap->active) return -22;

    /* Free TENSOR region and destroy guard context */
    if (cap->tensor_base != NULL && cap->guard_ctx != NULL) {
        vos3_ai_guard_ctx_t *ctx = (vos3_ai_guard_ctx_t *)cap->guard_ctx;
        vos3_ai_guard_free(ctx, cap->tensor_base);
        vos3_ai_guard_ctx_destroy(ctx);
    }

    VOS3_INFO("[VSCREEN] Slot %u stopped (captures=%u, avg_cycles=%llu)",
              slot_id, cap->captures_done,
              cap->captures_done > 0 ?
                  (unsigned long long)(cap->total_cycles / cap->captures_done) : 0ULL);

    vs_memzero(cap, sizeof(*cap));
    return 0;
}

void vscreen_get_stats(vscreen_stats_t *out)
{
    if (out != NULL) {
        vs_memcpy(out, &g_vscreen_stats, sizeof(g_vscreen_stats));
    }
}
