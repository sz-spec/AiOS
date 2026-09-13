/**
 * @file pud_templates.h
 * @brief VOS3 PUD Templates — Pre-Hardened PUD Configuration Profiles
 *
 * @details Provides 4 pre-configured PUD templates (Browser, IDE, Comms,
 *          Sovereign) with pre-set security levels, clipboard isolation,
 *          and capability bitmasks. During quarantine, all PUDs are forced
 *          to SOVEREIGN template settings.
 *
 * @version 1.0.0
 * @date 2026-04-13
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 6.1: Immutable Source & Business Continuity Hardening
 */

#ifndef VOS3_PUD_TEMPLATES_H
#define VOS3_PUD_TEMPLATES_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "vspace.h"  /* PUD_LEVEL_* constants */

/* ============================================================================
 * TEMPLATE IDs
 * ============================================================================ */

#define PUD_TEMPLATE_BROWSER     0U
#define PUD_TEMPLATE_IDE         1U
#define PUD_TEMPLATE_COMMS       2U
#define PUD_TEMPLATE_SOVEREIGN   3U
#define PUD_TEMPLATE_COUNT       4U

/* ============================================================================
 * CAPABILITY BITMASK
 * ============================================================================ */

#define PUD_CAP_NETWORK    (1U << 0)
#define PUD_CAP_FILE_READ  (1U << 1)
#define PUD_CAP_FILE_WRITE (1U << 2)
#define PUD_CAP_CLIPBOARD  (1U << 3)
#define PUD_CAP_UI         (1U << 4)
#define PUD_CAP_EXEC       (1U << 5)

/* ============================================================================
 * TEMPLATE DESCRIPTOR
 * ============================================================================ */

typedef struct pud_template {
    uint8_t      template_id;
    uint8_t      level;
    uint8_t      clipboard_isolated;
    uint8_t      _pad;
    uint32_t     capabilities;
    const char  *name_prefix;
} pud_template_t;

/* ============================================================================
 * PRE-DEFINED TEMPLATES
 * ============================================================================ */

static const pud_template_t g_pud_templates[PUD_TEMPLATE_COUNT] = {
    [PUD_TEMPLATE_BROWSER]   = { 0, PUD_LEVEL_PRIVATE,   1, 0,
                                 PUD_CAP_NETWORK | PUD_CAP_UI,
                                 "Browser" },
    [PUD_TEMPLATE_IDE]       = { 1, PUD_LEVEL_PRIVATE,   0, 0,
                                 PUD_CAP_FILE_READ | PUD_CAP_FILE_WRITE |
                                 PUD_CAP_EXEC | PUD_CAP_UI,
                                 "IDE" },
    [PUD_TEMPLATE_COMMS]     = { 2, PUD_LEVEL_PRIVATE,   1, 0,
                                 PUD_CAP_NETWORK | PUD_CAP_UI,
                                 "Comms" },
    [PUD_TEMPLATE_SOVEREIGN] = { 3, PUD_LEVEL_SOVEREIGN, 1, 0,
                                 0,
                                 "Sovereign" },
};

/* ============================================================================
 * API
 * ============================================================================ */

int pud_create_from_template(uint8_t template_id, uint8_t owner_slot,
                             const char *name);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_PUD_TEMPLATES_H */
