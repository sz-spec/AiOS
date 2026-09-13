/**
 * @file pud_sandbox.c
 * @brief VOS3 Private User Domain (PUD) Sandbox — Semantic Air-Gap Enforcement
 *
 * @details Per-application security boundary with 3-tier policy:
 *          - PUBLIC: no restrictions
 *          - PRIVATE: PII scrubbed on cross-PUD paste
 *          - SOVEREIGN: no data exit permitted (unconditional block)
 *
 *          Integrates with AI Guard for per-PUD context isolation and
 *          Vector VFS for PUD-scoped semantic search.
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
#include "../../include/vos/console.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/vector_vfs.h"
#include "../../include/vos/action_bridge.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * EXTERNAL STATE
 * ============================================================================ */

/* Global vSpace state lives in vspace_shell.c */
extern vspace_state_t *vspace_get_state(void);

/* PUD violation counter */
static uint32_t g_pud_violations = 0;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void pud_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) p[i] = 0;
}

static void pud_strncpy(char *dst, const char *src, size_t max)
{
    size_t i;
    for (i = 0; i < max - 1 && src && src[i]; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
}

/* ============================================================================
 * PUD API
 * ============================================================================ */

/**
 * @brief Create a new Private User Domain.
 *
 * @param level      Security level (PUD_LEVEL_PUBLIC/PRIVATE/SOVEREIGN)
 * @param owner_slot Owning model slot
 * @param name       Human-readable name
 * @return PUD ID (0-7) on success, -ENOSPC if full, -EINVAL on bad args
 */
int pud_create(uint8_t level, uint8_t owner_slot, const char *name)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22; /* EINVAL */
    if (level > PUD_LEVEL_SOVEREIGN) return -22;

    /* Find free PUD slot */
    int free_slot = -1;
    for (int i = 0; i < (int)VSPACE_MAX_PUDS; i++) {
        if (!state->puds[i].active) {
            free_slot = i;
            break;
        }
    }

    if (free_slot < 0) return -28; /* ENOSPC */

    /* Initialize PUD */
    vspace_pud_t *pud = &state->puds[free_slot];
    pud_memzero(pud, sizeof(*pud));
    pud->id = (uint8_t)free_slot;
    pud->level = level;
    pud->owner_slot = owner_slot;
    pud->active = 1;
    pud->window_count = 0;
    pud->clipboard_isolated = (level == PUD_LEVEL_SOVEREIGN) ? 1 : 0;
    pud_strncpy(pud->name, name, VSPACE_WINDOW_NAME_LEN);

    /* Create AI Guard context for this PUD */
    pud->guard_ctx = (void *)vos3_ai_guard_ctx_create();

    VOS3_INFO("[PUD] Created PUD %d: level=%u owner=%u name=%s",
              free_slot, level, owner_slot, pud->name);

    return free_slot;
}

/**
 * @brief Destroy a PUD and cascade-destroy all its windows.
 */
int pud_destroy(uint8_t pud_id)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (pud_id >= VSPACE_MAX_PUDS) return -22;
    if (!state->puds[pud_id].active) return -22;

    /* Cascade: destroy all windows owned by this PUD */
    for (int i = 0; i < (int)VSPACE_MAX_WINDOWS; i++) {
        if (state->windows[i].active && state->windows[i].pud_id == pud_id) {
            state->windows[i].active = 0;
            pud_memzero(&state->windows[i], sizeof(state->windows[i]));
            state->windows_destroyed++;
        }
    }

    /* Destroy AI Guard context */
    if (state->puds[pud_id].guard_ctx) {
        vos3_ai_guard_ctx_destroy((vos3_ai_guard_ctx_t *)state->puds[pud_id].guard_ctx);
    }

    /* Zero the PUD slot */
    pud_memzero(&state->puds[pud_id], sizeof(state->puds[pud_id]));

    return 0;
}

/**
 * @brief Set PUD security level.
 *
 * @details Cannot downgrade SOVEREIGN to lower level (one-way escalation).
 */
int pud_set_level(uint8_t pud_id, uint8_t level)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (pud_id >= VSPACE_MAX_PUDS) return -22;
    if (!state->puds[pud_id].active) return -22;
    if (level > PUD_LEVEL_SOVEREIGN) return -22;

    /* Cannot downgrade SOVEREIGN (one-way escalation) */
    if (state->puds[pud_id].level == PUD_LEVEL_SOVEREIGN && level < PUD_LEVEL_SOVEREIGN) {
        g_pud_violations++;
        return -1; /* EPERM */
    }

    state->puds[pud_id].level = level;

    /* Update clipboard isolation if elevated to SOVEREIGN */
    if (level == PUD_LEVEL_SOVEREIGN) {
        state->puds[pud_id].clipboard_isolated = 1;
    }

    return 0;
}

/**
 * @brief Check boundary policy between two PUDs.
 *
 * Boundary policy matrix:
 *   Source\Dest  | PUBLIC  | PRIVATE | SOVEREIGN
 *   ------------|---------|---------|----------
 *   PUBLIC      | Allow   | Allow   | Allow
 *   PRIVATE     | Scrub   | Allow   | Allow
 *   SOVEREIGN   | BLOCK   | BLOCK   | Allow
 *
 * @param src_pud    Source PUD ID
 * @param dst_pud    Destination PUD ID
 * @param action_type Unused (reserved for future granularity)
 * @return 0 = allow, SCRUB_PII_DETECTED = scrub required, -EPERM = blocked
 */
int pud_check_boundary(uint8_t src_pud, uint8_t dst_pud, uint8_t action_type)
{
    (void)action_type;

    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (src_pud >= VSPACE_MAX_PUDS || !state->puds[src_pud].active) return -22;
    if (dst_pud >= VSPACE_MAX_PUDS || !state->puds[dst_pud].active) return -22;

    uint8_t src_level = state->puds[src_pud].level;
    uint8_t dst_level = state->puds[dst_pud].level;

    /* Same PUD or same level SOVEREIGN: always allow */
    if (src_pud == dst_pud) return 0;

    /* SOVEREIGN source → anything other than SOVEREIGN dest = BLOCK */
    if (src_level == PUD_LEVEL_SOVEREIGN && dst_level != PUD_LEVEL_SOVEREIGN) {
        g_pud_violations++;
        return -1; /* EPERM — blocked */
    }

    /* PRIVATE source → PUBLIC dest = scrub required */
    if (src_level == PUD_LEVEL_PRIVATE && dst_level == PUD_LEVEL_PUBLIC) {
        return (int)SCRUB_PII_DETECTED; /* Scrub required before allowing */
    }

    /* All other cases: allow (PUBLIC→anything, PRIVATE→PRIVATE, etc.) */
    return 0;
}

/**
 * @brief Index content into PUD-scoped Vector VFS.
 *
 * @details Creates a simple XOR hash embedding from data and inserts
 *          into Vector VFS tagged with PUD namespace.
 */
int pud_index_content(uint8_t pud_id, const uint8_t *data, uint32_t len)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (pud_id >= VSPACE_MAX_PUDS || !state->puds[pud_id].active) return -22;
    if (!data || len == 0) return -22;

    /* Generate a simple XOR-folded embedding from data */
    uint8_t embedding[64];
    pud_memzero(embedding, 64);

    for (uint32_t i = 0; i < len && i < 256; i++) {
        embedding[i % 64] ^= data[i];
    }

    /* Insert into Vector VFS with pud_id as the slot namespace */
    int rc = vecvfs_insert(pud_id, embedding, data, len > 128 ? 128 : len);
    return rc;
}

/**
 * @brief Get PUD violation count.
 */
uint32_t pud_get_violations(void)
{
    return g_pud_violations;
}

/* ============================================================================
 * PUD TEMPLATES (Phase 6.1)
 * ============================================================================ */

#include "../../include/vos/pud_templates.h"

/**
 * @brief Create a PUD from a pre-defined template.
 *
 * @param template_id  PUD_TEMPLATE_BROWSER/IDE/COMMS/SOVEREIGN
 * @param owner_slot   Owning model slot
 * @param name         Optional name override (NULL uses template prefix)
 * @return PUD ID on success, negative errno on failure
 */
int pud_create_from_template(uint8_t template_id, uint8_t owner_slot,
                             const char *name)
{
    if (template_id >= PUD_TEMPLATE_COUNT) return -22; /* EINVAL */

    const pud_template_t *tmpl = &g_pud_templates[template_id];

    int pud_id = pud_create(tmpl->level, owner_slot,
                            name ? name : tmpl->name_prefix);
    if (pud_id >= 0) {
        vspace_state_t *state = vspace_get_state();
        if (state && state->initialized) {
            state->puds[pud_id].clipboard_isolated = tmpl->clipboard_isolated;
        }
    }

    return pud_id;
}
