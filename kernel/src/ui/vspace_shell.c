/**
 * @file vspace_shell.c
 * @brief VOS3 vSpace Desktop Shell — AI-Native Window Manager
 *
 * @details First AI-native desktop shell. COORDINATOR agent (slot 0) drives
 *          window layout through Action Bridge's UI_CLICK path. 4 virtual
 *          workspaces, 16 windows max, z-order stacking, PUD ownership.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.1: vSpace Desktop & Sovereign App Sandboxing
 */

#include "../../include/vos/vspace.h"
#include "../../include/vos/vscreen.h"
#include "../../include/vos/console.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/action_bridge.h"
#include "../../include/vos/agent_mesh.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

static vspace_state_t g_vspace;

/* External: model slot array for capability checks */
extern vos3_ai_model_slot_t g_model_slots[];

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void vs_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) p[i] = 0;
}

static void vs_strncpy(char *dst, const char *src, size_t max)
{
    size_t i;
    for (i = 0; i < max - 1 && src && src[i]; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
}

/* ============================================================================
 * vSpace Shell API
 * ============================================================================ */

/**
 * @brief Initialize the vSpace Desktop Shell.
 */
int vspace_init(void)
{
    vs_memzero(&g_vspace, sizeof(g_vspace));
    g_vspace.active_workspace = 0;
    g_vspace.initialized = 1;
    g_vspace.next_z_order = 1;

    VOS3_INFO("[vSpace] Desktop shell initialized (4 workspaces, 16 windows)");
    return 0;
}

/**
 * @brief Create a window in the vSpace desktop.
 *
 * @param pud_id  Owning Private User Domain
 * @param slot_id Model slot that owns this window
 * @param x       X position
 * @param y       Y position
 * @param w       Width in pixels
 * @param h       Height in pixels
 * @param flags   VSPACE_WIN_* bitmask
 * @param name    Human-readable name (up to 31 chars)
 * @return Window ID (0-15) on success, -ENOSPC if full, -EINVAL on bad args
 */
int vspace_window_create(uint8_t pud_id, uint8_t slot_id, uint16_t x, uint16_t y,
                         uint16_t w, uint16_t h, uint8_t flags, const char *name)
{
    if (!g_vspace.initialized) return -22; /* EINVAL */

    /* Validate PUD exists */
    if (pud_id >= VSPACE_MAX_PUDS || !g_vspace.puds[pud_id].active) {
        return -22; /* EINVAL */
    }

    /* Bounds check */
    if (w == 0 || h == 0) return -22; /* EINVAL */
    if ((uint32_t)x + (uint32_t)w > VSCREEN_MAX_WIDTH) return -22;
    if ((uint32_t)y + (uint32_t)h > VSCREEN_MAX_HEIGHT) return -22;

    /* Find free window slot */
    int free_slot = -1;
    for (int i = 0; i < (int)VSPACE_MAX_WINDOWS; i++) {
        if (!g_vspace.windows[i].active) {
            free_slot = i;
            break;
        }
    }

    if (free_slot < 0) return -28; /* ENOSPC */

    /* Initialize window */
    vspace_window_t *win = &g_vspace.windows[free_slot];
    vs_memzero(win, sizeof(*win));
    win->id = (uint8_t)free_slot;
    win->x = x;
    win->y = y;
    win->w = w;
    win->h = h;
    win->z_order = g_vspace.next_z_order++;
    win->pud_id = pud_id;
    win->slot_id = slot_id;
    win->workspace = g_vspace.active_workspace;
    win->flags = flags | VSPACE_WIN_VISIBLE;
    win->active = 1;
    vs_strncpy(win->name, name, VSPACE_WINDOW_NAME_LEN);

    /* Update PUD window count */
    g_vspace.puds[pud_id].window_count++;
    g_vspace.windows_created++;

    return free_slot;
}

/**
 * @brief Destroy a window.
 */
int vspace_window_destroy(uint8_t win_id)
{
    if (!g_vspace.initialized) return -22;
    if (win_id >= VSPACE_MAX_WINDOWS) return -22;
    if (!g_vspace.windows[win_id].active) return -22; /* EINVAL: already free */

    vspace_window_t *win = &g_vspace.windows[win_id];
    uint8_t pud_id = win->pud_id;

    /* Decrement PUD window count */
    if (pud_id < VSPACE_MAX_PUDS && g_vspace.puds[pud_id].active) {
        if (g_vspace.puds[pud_id].window_count > 0) {
            g_vspace.puds[pud_id].window_count--;
        }
    }

    /* Zero the slot */
    vs_memzero(win, sizeof(*win));
    g_vspace.windows_destroyed++;

    return 0;
}

/**
 * @brief Move a window to new coordinates.
 */
int vspace_window_move(uint8_t win_id, uint16_t x, uint16_t y)
{
    if (!g_vspace.initialized) return -22;
    if (win_id >= VSPACE_MAX_WINDOWS) return -22;
    if (!g_vspace.windows[win_id].active) return -22;

    vspace_window_t *win = &g_vspace.windows[win_id];

    /* Bounds check: x + w must fit within screen */
    if ((uint32_t)x + (uint32_t)win->w > VSCREEN_MAX_WIDTH) return -22;
    if ((uint32_t)y + (uint32_t)win->h > VSCREEN_MAX_HEIGHT) return -22;

    win->x = x;
    win->y = y;

    return 0;
}

/**
 * @brief Resize a window.
 */
int vspace_window_resize(uint8_t win_id, uint16_t w, uint16_t h)
{
    if (!g_vspace.initialized) return -22;
    if (win_id >= VSPACE_MAX_WINDOWS) return -22;
    if (!g_vspace.windows[win_id].active) return -22;
    if (w == 0 || h == 0) return -22;

    vspace_window_t *win = &g_vspace.windows[win_id];

    /* Bounds check */
    if ((uint32_t)win->x + (uint32_t)w > VSCREEN_MAX_WIDTH) return -22;
    if ((uint32_t)win->y + (uint32_t)h > VSCREEN_MAX_HEIGHT) return -22;

    win->w = w;
    win->h = h;

    return 0;
}

/**
 * @brief Focus a window (bring to top, set FOCUSED flag).
 */
int vspace_window_focus(uint8_t win_id)
{
    if (!g_vspace.initialized) return -22;
    if (win_id >= VSPACE_MAX_WINDOWS) return -22;
    if (!g_vspace.windows[win_id].active) return -22;

    /* Clear FOCUSED flag from all windows in current workspace */
    for (int i = 0; i < (int)VSPACE_MAX_WINDOWS; i++) {
        if (g_vspace.windows[i].active &&
            g_vspace.windows[i].workspace == g_vspace.active_workspace) {
            g_vspace.windows[i].flags &= (uint8_t)~VSPACE_WIN_FOCUSED;
        }
    }

    /* Set FOCUSED on target and bring to top */
    vspace_window_t *win = &g_vspace.windows[win_id];
    win->flags |= VSPACE_WIN_FOCUSED;
    win->z_order = g_vspace.next_z_order++;

    return 0;
}

/**
 * @brief Switch to a different workspace.
 */
int vspace_switch_workspace(uint8_t workspace_id)
{
    if (!g_vspace.initialized) return -22;
    if (workspace_id >= VSPACE_MAX_WORKSPACES) return -22;

    g_vspace.active_workspace = workspace_id;
    g_vspace.workspace_switches++;

    return 0;
}

/**
 * @brief Get aggregate vSpace statistics.
 */
void vspace_get_stats(vspace_stats_t *out)
{
    if (!out) return;

    out->windows_created = g_vspace.windows_created;
    out->windows_destroyed = g_vspace.windows_destroyed;
    out->workspace_switches = g_vspace.workspace_switches;
    out->clips_copied = g_vspace.clipboard.total_copies;
    out->clips_scrubbed = g_vspace.clipboard.total_scrubs;
    out->clips_blocked = g_vspace.clipboard.total_blocks;
    out->pud_violations = 0; /* Updated by PUD module */
}

/* ============================================================================
 * ACCESSORS (for test/external use)
 * ============================================================================ */

/**
 * @brief Get pointer to global vSpace state (for tests).
 */
vspace_state_t *vspace_get_state(void)
{
    return &g_vspace;
}
