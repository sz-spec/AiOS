/**
 * @file vspace.h
 * @brief VOS3 vSpace — Sovereign Desktop Shell + PUD Sandboxing + Clipboard DLP
 *
 * @details First AI-native desktop shell. Window management driven by
 *          COORDINATOR agent (slot 0) through Action Bridge. Per-app
 *          sandboxing via Private User Domains (PUD) with semantic air-gap
 *          enforcement. 3-layer clipboard DLP: regex pattern scan + Shannon
 *          entropy heuristic + trust-tier boundary check.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.1: vSpace Desktop & Sovereign App Sandboxing
 */

#ifndef VOS3_VSPACE_H
#define VOS3_VSPACE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum windows in the desktop shell */
#define VSPACE_MAX_WINDOWS          16U

/** @brief Maximum virtual workspaces */
#define VSPACE_MAX_WORKSPACES        4U

/** @brief Maximum Private User Domains */
#define VSPACE_MAX_PUDS              8U

/** @brief Clipboard ring buffer capacity */
#define VSPACE_CLIPBOARD_RING       16U

/** @brief Maximum window name length */
#define VSPACE_WINDOW_NAME_LEN      32U

/** @brief Maximum PII scrub patterns */
#define VSPACE_SCRUB_MAX_PATTERNS   16U

/* PUD security levels */
#define PUD_LEVEL_PUBLIC             0U  /**< No restrictions */
#define PUD_LEVEL_PRIVATE            1U  /**< PII scrubbed on cross-PUD paste */
#define PUD_LEVEL_SOVEREIGN          2U  /**< No data exit permitted */

/* Window flags */
#define VSPACE_WIN_VISIBLE          (1U << 0)
#define VSPACE_WIN_FOCUSED          (1U << 1)
#define VSPACE_WIN_AGENT_MANAGED    (1U << 2)  /**< COORDINATOR can reposition */
#define VSPACE_WIN_FULLSCREEN       (1U << 3)

/* Clipboard scrub results */
#define SCRUB_CLEAN                  0U
#define SCRUB_PII_DETECTED           1U
#define SCRUB_BLOCKED                2U

/* PII pattern types for regex layer */
#define PII_PATTERN_EMAIL            0U
#define PII_PATTERN_PHONE            1U
#define PII_PATTERN_SSN              2U
#define PII_PATTERN_CREDIT_CARD      3U
#define PII_PATTERN_API_KEY          4U
#define PII_PATTERN_COUNT            5U

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Window descriptor.
 */
typedef struct vspace_window {
    uint8_t     id;                         /**< Window ID (0-15) */
    uint16_t    x;                          /**< X position */
    uint16_t    y;                          /**< Y position */
    uint16_t    w;                          /**< Width */
    uint16_t    h;                          /**< Height */
    uint16_t    z_order;                    /**< Stacking order (higher = on top) */
    uint8_t     pud_id;                     /**< Owning PUD */
    uint8_t     slot_id;                    /**< Owning model slot */
    uint8_t     workspace;                  /**< Workspace assignment (0-3) */
    uint8_t     flags;                      /**< VSPACE_WIN_* bitmask */
    uint8_t     active;                     /**< 1 = slot in use */
    char        name[VSPACE_WINDOW_NAME_LEN]; /**< Human-readable name */
    uintptr_t   fb_va;                      /**< Framebuffer virtual address */
    uint32_t    fb_size;                    /**< Framebuffer size */
} vspace_window_t;

/**
 * @brief Private User Domain descriptor.
 */
typedef struct vspace_pud {
    uint8_t     id;                         /**< PUD ID (0-7) */
    uint8_t     level;                      /**< PUD_LEVEL_* */
    uint8_t     owner_slot;                 /**< Owning model slot */
    uint8_t     active;                     /**< 1 = PUD in use */
    void       *guard_ctx;                  /**< AI Guard context (opaque) */
    uint8_t     window_count;               /**< Windows in this PUD */
    uint8_t     clipboard_isolated;         /**< 1 = clipboard isolated */
    char        name[VSPACE_WINDOW_NAME_LEN]; /**< Human-readable name */
} vspace_pud_t;

/**
 * @brief Clipboard ring entry (metadata only — no content stored).
 */
typedef struct vspace_clip_entry {
    uint32_t    data_hash;                  /**< CRC32C of clipboard content */
    uint8_t     source_pud;                 /**< Source PUD ID */
    uint8_t     dest_pud;                   /**< Destination PUD ID */
    uint8_t     scrub_result;               /**< SCRUB_* result */
    uint8_t     _pad;
    uint32_t    size;                       /**< Original data size */
    uint64_t    timestamp_tsc;              /**< TSC at copy time */
} vspace_clip_entry_t;

/**
 * @brief Sovereign clipboard state.
 */
typedef struct vspace_clip_state {
    vspace_clip_entry_t ring[VSPACE_CLIPBOARD_RING]; /**< Circular ring */
    uint32_t    head;                       /**< Write index */
    uint32_t    tail;                       /**< Read index */
    uint32_t    total_copies;               /**< Lifetime copies */
    uint32_t    total_scrubs;               /**< Lifetime scrub triggers */
    uint32_t    total_blocks;               /**< Lifetime blocks */
} vspace_clip_state_t;

/**
 * @brief Global vSpace state.
 */
typedef struct vspace_state {
    vspace_window_t     windows[VSPACE_MAX_WINDOWS];
    vspace_pud_t        puds[VSPACE_MAX_PUDS];
    vspace_clip_state_t clipboard;
    uint8_t             active_workspace;   /**< Current workspace (0-3) */
    uint8_t             initialized;        /**< 1 = vSpace ready */
    uint16_t            next_z_order;       /**< Monotonic z-order counter */
    uint32_t            windows_created;
    uint32_t            windows_destroyed;
    uint32_t            workspace_switches;
} vspace_state_t;

/**
 * @brief Aggregate vSpace telemetry.
 */
typedef struct vspace_stats {
    uint32_t    windows_created;
    uint32_t    windows_destroyed;
    uint32_t    workspace_switches;
    uint32_t    clips_copied;
    uint32_t    clips_scrubbed;
    uint32_t    clips_blocked;
    uint32_t    pud_violations;
} vspace_stats_t;

/* ============================================================================
 * API — vSpace Shell (kernel/src/ui/vspace_shell.c)
 * ============================================================================ */

int  vspace_init(void);
int  vspace_window_create(uint8_t pud_id, uint8_t slot_id, uint16_t x, uint16_t y,
                          uint16_t w, uint16_t h, uint8_t flags, const char *name);
int  vspace_window_destroy(uint8_t win_id);
int  vspace_window_move(uint8_t win_id, uint16_t x, uint16_t y);
int  vspace_window_resize(uint8_t win_id, uint16_t w, uint16_t h);
int  vspace_window_focus(uint8_t win_id);
int  vspace_switch_workspace(uint8_t workspace_id);
void vspace_get_stats(vspace_stats_t *out);

/* ============================================================================
 * API — PUD Sandbox (kernel/src/sec/pud_sandbox.c)
 * ============================================================================ */

int  pud_create(uint8_t level, uint8_t owner_slot, const char *name);
int  pud_destroy(uint8_t pud_id);
int  pud_set_level(uint8_t pud_id, uint8_t level);
int  pud_check_boundary(uint8_t src_pud, uint8_t dst_pud, uint8_t action_type);
int  pud_index_content(uint8_t pud_id, const uint8_t *data, uint32_t len);

/* ============================================================================
 * API — Sovereign Clipboard (kernel/src/ai/sovereign_clipboard.c)
 * ============================================================================ */

int  sclip_init(void);
int  sclip_copy(uint8_t src_pud, const void *data, uint32_t len);
int  sclip_paste(uint8_t dst_pud, void *out_buf, uint32_t buf_size, uint32_t *out_len);
int  sclip_scrub_check(uint8_t src_pud, uint8_t dst_pud, const void *data, uint32_t len);
void sclip_get_stats(vspace_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VSPACE_H */
