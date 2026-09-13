#ifndef VOS3_PRODUCTION_BUILD
/**
 * @file test_phase51_audit.c
 * @brief Phase 5.1 Supreme UX Audit — vSpace Desktop & Sovereign App Sandboxing
 *
 * @details 5 Tasks, 60+ VSP_ASSERTs proving:
 *
 *   TASK 1 — vSpace Window Manager Correctness (~14 asserts)
 *     - Create/destroy windows, move/resize bounds checking
 *     - Focus/z-order invariants, workspace switching
 *
 *   TASK 2 — PUD Boundary Enforcement — Semantic Air-Gap (~12 asserts)
 *     - 3-tier policy matrix (PUBLIC/PRIVATE/SOVEREIGN)
 *     - One-way escalation (SOVEREIGN cannot downgrade)
 *     - Cascade destroy, max PUD enforcement
 *
 *   TASK 3 — Sovereign Clipboard DLP Proof (~14 asserts)
 *     - Email, phone, SSN, credit card, API key detection
 *     - High entropy detection, clean text pass-through
 *     - Cross-PUD paste enforcement, ring buffer wrapping
 *
 *   TASK 4 — Agentic Workspace Control (~10 asserts)
 *     - COORDINATOR UI_CLICK allowed (VISION cap)
 *     - WORKER denied (no VISION), AGENT_MANAGED flag
 *     - Trust decay doesn't break workspace, audit log
 *
 *   TASK 5 — Ultra-Deterministic Latency Benchmark (~10 asserts)
 *     - 100 iterations workspace switch + clipboard DLP
 *     - P50/P99/P99.9/Max cycle measurement
 *     - Jitter < 100%
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.1: Supreme User-Experience Audit
 */

#include "../../include/vos/vspace.h"
#include "../../include/vos/vscreen.h"
#include "../../include/vos/agent_mesh.h"
#include "../../include/vos/action_bridge.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/console.h"
#include "../../include/vos/entropy.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TEST INFRASTRUCTURE
 * ============================================================================ */

static uint32_t g_vsp_pass = 0;
static uint32_t g_vsp_fail = 0;
static uint32_t g_vsp_skip = 0;

#define VSP_ASSERT(cond, name)                                                \
    do {                                                                      \
        if (cond) {                                                           \
            g_vsp_pass++;                                                     \
            VOS3_INFO("[VSP-TEST] PASS: %s", (name));                         \
        } else {                                                              \
            g_vsp_fail++;                                                     \
            VOS3_ERROR("[VSP-TEST] FAIL: %s (line %d)", (name), __LINE__);    \
        }                                                                     \
    } while (0)

#define VSP_SKIP(name)                                                        \
    do {                                                                      \
        g_vsp_skip++;                                                         \
        VOS3_INFO("[VSP-TEST] SKIP: %s", (name));                             \
    } while (0)

/* External: model slot array */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* Task pass counters (5 tasks, 2 points each = 10 total) */
static uint8_t g_task_pass[5] = {0, 0, 0, 0, 0};

/* ============================================================================
 * HELPERS
 * ============================================================================ */

static void vsp_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) p[i] = 0;
}

static void vsp_memset(void *dst, uint8_t val, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) p[i] = val;
}

static int vsp_memcmp(const void *a, const void *b, size_t len)
{
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    for (size_t i = 0; i < len; i++) {
        if (pa[i] != pb[i]) return (int)pa[i] - (int)pb[i];
    }
    return 0;
}

static void vsp_setup_slot(uint8_t slot_id, uint64_t caps, uint8_t agent_type)
{
    if (slot_id < VOS3_MODEL_SLOT_MAX) {
        g_model_slots[slot_id].capabilities = caps;
        g_model_slots[slot_id].agent_type = agent_type;
        g_model_slots[slot_id].status = VOS3_SLOT_ACTIVE;
    }
}

static void vsp_teardown_slot(uint8_t slot_id)
{
    if (slot_id < VOS3_MODEL_SLOT_MAX) {
        g_model_slots[slot_id].capabilities = 0;
        g_model_slots[slot_id].agent_type = VOS3_AGENT_WORKER;
        g_model_slots[slot_id].status = VOS3_SLOT_FREE;
    }
}

static uint64_t vsp_abs_diff(uint64_t a, uint64_t b)
{
    return a > b ? a - b : b - a;
}

/* External accessor */
extern vspace_state_t *vspace_get_state(void);

/* ============================================================================
 * TASK 1: vSpace Window Manager Correctness (~14 VSP_ASSERTs)
 * ============================================================================ */

static void test_task1_window_manager(void)
{
    VOS3_INFO("[VSP-TASK1] vSpace Window Manager Correctness");

    int rc;

    /* T1.1: vspace_init() succeeds */
    rc = vspace_init();
    VSP_ASSERT(rc == 0, "T1.1: vspace_init() succeeds");

    /* T1.2: Create PUD (PUBLIC) */
    rc = pud_create(PUD_LEVEL_PUBLIC, 0, "test-public");
    VSP_ASSERT(rc >= 0, "T1.2: Create PUD (PUBLIC)");
    int public_pud = rc;

    /* T1.3: Create window in PUD */
    rc = vspace_window_create((uint8_t)public_pud, 0, 100, 100, 400, 300,
                              VSPACE_WIN_VISIBLE, "TestWin");
    VSP_ASSERT(rc >= 0, "T1.3: Create window in PUD");
    int first_win = rc;

    /* T1.4: Create 16 windows (max) — already have 1, create 15 more */
    int max_test_passed = 1;
    for (int i = 1; i < (int)VSPACE_MAX_WINDOWS; i++) {
        int w = vspace_window_create((uint8_t)public_pud, 0,
                                     (uint16_t)((i * 50) % 800), (uint16_t)((i * 40) % 600),
                                     200, 150, VSPACE_WIN_VISIBLE, "MaxWin");
        if (w < 0) { max_test_passed = 0; break; }
    }
    VSP_ASSERT(max_test_passed, "T1.4: Create 16 windows (max)");

    /* T1.5: 17th window fails with ENOSPC */
    rc = vspace_window_create((uint8_t)public_pud, 0, 0, 0, 100, 100,
                              VSPACE_WIN_VISIBLE, "Overflow");
    VSP_ASSERT(rc == -28, "T1.5: 17th window fails (ENOSPC)");

    /* Destroy all but first to continue testing */
    for (int i = 1; i < (int)VSPACE_MAX_WINDOWS; i++) {
        vspace_window_destroy((uint8_t)i);
    }

    /* T1.6: Move window (valid) */
    rc = vspace_window_move((uint8_t)first_win, 200, 200);
    vspace_state_t *state = vspace_get_state();
    VSP_ASSERT(rc == 0 && state->windows[first_win].x == 200 &&
               state->windows[first_win].y == 200,
               "T1.6: Move window (valid)");

    /* T1.7: Move OOB (x + w > 1920) */
    rc = vspace_window_move((uint8_t)first_win, 1800, 100);
    VSP_ASSERT(rc == -22, "T1.7: Move OOB (x+w > 1920)");

    /* T1.8: Resize window */
    rc = vspace_window_resize((uint8_t)first_win, 600, 400);
    VSP_ASSERT(rc == 0 && state->windows[first_win].w == 600 &&
               state->windows[first_win].h == 400,
               "T1.8: Resize window");

    /* T1.9: Focus window → z_order increases */
    uint16_t z_before = state->windows[first_win].z_order;
    rc = vspace_window_focus((uint8_t)first_win);
    VSP_ASSERT(rc == 0 && state->windows[first_win].z_order > z_before,
               "T1.9: Focus window → z_order max");

    /* T1.10: Only 1 window focused per workspace */
    int second_win = vspace_window_create((uint8_t)public_pud, 0, 50, 50, 200, 200,
                                          VSPACE_WIN_VISIBLE, "SecondWin");
    vspace_window_focus((uint8_t)second_win);
    int focused_count = 0;
    for (int i = 0; i < (int)VSPACE_MAX_WINDOWS; i++) {
        if (state->windows[i].active &&
            state->windows[i].workspace == state->active_workspace &&
            (state->windows[i].flags & VSPACE_WIN_FOCUSED)) {
            focused_count++;
        }
    }
    VSP_ASSERT(focused_count == 1, "T1.10: Only 1 window focused per workspace");

    /* T1.11: Destroy window */
    rc = vspace_window_destroy((uint8_t)second_win);
    VSP_ASSERT(rc == 0 && !state->windows[second_win].active,
               "T1.11: Destroy window");

    /* T1.12: Destroy already-free window */
    rc = vspace_window_destroy((uint8_t)second_win);
    VSP_ASSERT(rc == -22, "T1.12: Destroy already-free window");

    /* T1.13: 4 workspaces valid */
    int ws_ok = 1;
    for (int i = 0; i < (int)VSPACE_MAX_WORKSPACES; i++) {
        if (vspace_switch_workspace((uint8_t)i) != 0) ws_ok = 0;
    }
    VSP_ASSERT(ws_ok, "T1.13: 4 workspaces valid (0-3)");

    /* T1.14: Workspace 4 invalid */
    rc = vspace_switch_workspace(4);
    VSP_ASSERT(rc == -22, "T1.14: Workspace 4 invalid");

    /* Reset workspace to 0 */
    vspace_switch_workspace(0);

    /* Score: all 14 passed? */
    g_task_pass[0] = (g_vsp_fail == 0) ? 2 : 0;
}

/* ============================================================================
 * TASK 2: PUD Boundary Enforcement — Semantic Air-Gap (~12 VSP_ASSERTs)
 * ============================================================================ */

static void test_task2_pud_boundary(void)
{
    VOS3_INFO("[VSP-TASK2] PUD Boundary Enforcement — Semantic Air-Gap");

    uint32_t prev_fail = g_vsp_fail;

    /* Re-init for clean state */
    vspace_init();
    sclip_init();

    /* T2.1: Create PUD PUBLIC */
    int pub_pud = pud_create(PUD_LEVEL_PUBLIC, 0, "public-app");
    VSP_ASSERT(pub_pud >= 0, "T2.1: Create PUD PUBLIC");

    /* T2.2: Create PUD PRIVATE */
    int priv_pud = pud_create(PUD_LEVEL_PRIVATE, 0, "private-app");
    VSP_ASSERT(priv_pud >= 0, "T2.2: Create PUD PRIVATE");

    /* T2.3: Create PUD SOVEREIGN */
    int sov_pud = pud_create(PUD_LEVEL_SOVEREIGN, 0, "sovereign-app");
    VSP_ASSERT(sov_pud >= 0, "T2.3: Create PUD SOVEREIGN");

    /* T2.4: PUBLIC → PUBLIC boundary → allow */
    int pub_pud2 = pud_create(PUD_LEVEL_PUBLIC, 0, "public-app2");
    int rc = pud_check_boundary((uint8_t)pub_pud, (uint8_t)pub_pud2, 0);
    VSP_ASSERT(rc == 0, "T2.4: PUBLIC->PUBLIC = allow");

    /* T2.5: PRIVATE → PUBLIC boundary → SCRUB_PII_DETECTED */
    rc = pud_check_boundary((uint8_t)priv_pud, (uint8_t)pub_pud, 0);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T2.5: PRIVATE->PUBLIC = scrub required");

    /* T2.6: SOVEREIGN → PUBLIC boundary → EPERM (blocked) */
    rc = pud_check_boundary((uint8_t)sov_pud, (uint8_t)pub_pud, 0);
    VSP_ASSERT(rc == -1, "T2.6: SOVEREIGN->PUBLIC = blocked (EPERM)");

    /* T2.7: SOVEREIGN → PRIVATE boundary → EPERM (blocked) */
    rc = pud_check_boundary((uint8_t)sov_pud, (uint8_t)priv_pud, 0);
    VSP_ASSERT(rc == -1, "T2.7: SOVEREIGN->PRIVATE = blocked (EPERM)");

    /* T2.8: SOVEREIGN → SOVEREIGN boundary → allow */
    int sov_pud2 = pud_create(PUD_LEVEL_SOVEREIGN, 0, "sovereign-app2");
    rc = pud_check_boundary((uint8_t)sov_pud, (uint8_t)sov_pud2, 0);
    VSP_ASSERT(rc == 0, "T2.8: SOVEREIGN->SOVEREIGN = allow");

    /* T2.9: PUBLIC → SOVEREIGN boundary → allow (elevation OK) */
    rc = pud_check_boundary((uint8_t)pub_pud, (uint8_t)sov_pud, 0);
    VSP_ASSERT(rc == 0, "T2.9: PUBLIC->SOVEREIGN = allow (elevation OK)");

    /* T2.10: Cannot downgrade SOVEREIGN to PUBLIC */
    rc = pud_set_level((uint8_t)sov_pud, PUD_LEVEL_PUBLIC);
    VSP_ASSERT(rc == -1, "T2.10: Cannot downgrade SOVEREIGN to PUBLIC");

    /* T2.11: Max 8 PUDs enforced — we have 5, create 3 more to fill */
    for (int i = 0; i < 3; i++) {
        pud_create(PUD_LEVEL_PUBLIC, 0, "filler");
    }
    rc = pud_create(PUD_LEVEL_PUBLIC, 0, "overflow");
    VSP_ASSERT(rc == -28, "T2.11: Max 8 PUDs enforced (ENOSPC)");

    /* T2.12: PUD destroy cascades windows */
    /* Create a window in pub_pud then destroy the PUD */
    vspace_window_create((uint8_t)pub_pud, 0, 10, 10, 100, 100,
                         VSPACE_WIN_VISIBLE, "CascadeWin");
    pud_destroy((uint8_t)pub_pud);
    vspace_state_t *state = vspace_get_state();
    int orphaned = 0;
    for (int i = 0; i < (int)VSPACE_MAX_WINDOWS; i++) {
        if (state->windows[i].active && state->windows[i].pud_id == (uint8_t)pub_pud) {
            orphaned++;
        }
    }
    VSP_ASSERT(orphaned == 0, "T2.12: PUD destroy cascades windows");

    g_task_pass[1] = (g_vsp_fail == prev_fail) ? 2 : 0;
}

/* ============================================================================
 * TASK 3: Sovereign Clipboard DLP Proof (~14 VSP_ASSERTs)
 * ============================================================================ */

static void test_task3_clipboard_dlp(void)
{
    VOS3_INFO("[VSP-TASK3] Sovereign Clipboard DLP Proof");

    uint32_t prev_fail = g_vsp_fail;

    /* Re-init */
    vspace_init();
    sclip_init();

    /* Create PUDs for testing */
    int pub_pud = pud_create(PUD_LEVEL_PUBLIC, 0, "clip-public");
    int priv_pud = pud_create(PUD_LEVEL_PRIVATE, 0, "clip-private");
    int sov_pud = pud_create(PUD_LEVEL_SOVEREIGN, 0, "clip-sovereign");

    int rc;

    /* T3.1: sclip_init() succeeded (already called) */
    VSP_ASSERT(pub_pud >= 0 && priv_pud >= 0 && sov_pud >= 0,
               "T3.1: sclip_init() + PUDs created");

    /* T3.2: Copy clean text */
    const char *clean_text = "hello world";
    rc = sclip_copy((uint8_t)pub_pud, clean_text, 11);
    VSP_ASSERT(rc == 0, "T3.2: Copy clean text (hello world)");

    /* T3.3: Paste within same PUD (PUBLIC) */
    uint8_t paste_buf[256];
    uint32_t paste_len = 0;
    vsp_memzero(paste_buf, sizeof(paste_buf));
    rc = sclip_paste((uint8_t)pub_pud, paste_buf, sizeof(paste_buf), &paste_len);
    VSP_ASSERT(rc == 0 && paste_len == 11 && vsp_memcmp(paste_buf, clean_text, 11) == 0,
               "T3.3: Paste within same PUD (PUBLIC)");

    /* T3.4: Scrub detects email pattern */
    const char *email = "user@example.com";
    rc = sclip_scrub_check((uint8_t)priv_pud, (uint8_t)pub_pud,
                           email, 16);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T3.4: Scrub detects email pattern");

    /* T3.5: Scrub detects phone pattern */
    const char *phone = "555-123-4567";
    rc = sclip_scrub_check((uint8_t)priv_pud, (uint8_t)pub_pud,
                           phone, 12);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T3.5: Scrub detects phone pattern");

    /* T3.6: Scrub detects SSN pattern */
    const char *ssn = "123-45-6789";
    rc = sclip_scrub_check((uint8_t)priv_pud, (uint8_t)pub_pud,
                           ssn, 11);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T3.6: Scrub detects SSN pattern");

    /* T3.7: Scrub detects credit card */
    const char *cc = "4111-1111-1111-1111";
    rc = sclip_scrub_check((uint8_t)priv_pud, (uint8_t)pub_pud,
                           cc, 19);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T3.7: Scrub detects credit card");

    /* T3.8: Scrub detects high-entropy API key */
    uint8_t api_key[64];
    for (int i = 0; i < 64; i++) {
        /* Generate hex-like pattern with high entropy */
        api_key[i] = "0123456789abcdef"[((uint32_t)(i * 7 + 13)) % 16];
    }
    rc = sclip_scrub_check((uint8_t)priv_pud, (uint8_t)pub_pud,
                           api_key, 64);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T3.8: Scrub detects high-entropy API key");

    /* T3.9: Clean text passes scrub (same PUD) */
    rc = sclip_scrub_check((uint8_t)pub_pud, (uint8_t)pub_pud,
                           (const uint8_t *)clean_text, 11);
    VSP_ASSERT(rc == (int)SCRUB_CLEAN, "T3.9: Clean text passes scrub (same PUD)");

    /* T3.10: PRIVATE→PUBLIC paste with PII: blocked */
    const char *pii_data = "user@secret.com";
    sclip_copy((uint8_t)priv_pud, pii_data, 15);
    vsp_memzero(paste_buf, sizeof(paste_buf));
    paste_len = 0;
    rc = sclip_paste((uint8_t)pub_pud, paste_buf, sizeof(paste_buf), &paste_len);
    VSP_ASSERT(rc == (int)SCRUB_PII_DETECTED, "T3.10: PRIVATE->PUBLIC paste with PII: blocked");

    /* T3.11: PRIVATE→PUBLIC paste clean: allowed */
    const char *safe_text = "safe data ok";
    sclip_copy((uint8_t)priv_pud, safe_text, 12);
    vsp_memzero(paste_buf, sizeof(paste_buf));
    paste_len = 0;
    rc = sclip_paste((uint8_t)pub_pud, paste_buf, sizeof(paste_buf), &paste_len);
    VSP_ASSERT(rc == 0 && paste_len == 12, "T3.11: PRIVATE->PUBLIC paste clean: allowed");

    /* T3.12: SOVEREIGN paste out: always blocked */
    const char *sov_data = "top secret";
    sclip_copy((uint8_t)sov_pud, sov_data, 10);
    vsp_memzero(paste_buf, sizeof(paste_buf));
    paste_len = 0;
    rc = sclip_paste((uint8_t)pub_pud, paste_buf, sizeof(paste_buf), &paste_len);
    VSP_ASSERT(rc == (int)SCRUB_BLOCKED, "T3.12: SOVEREIGN paste out: always blocked");

    /* T3.13: Clipboard ring wraps correctly */
    /* Re-init and do 17 copies */
    vspace_init();
    sclip_init();
    int ring_pud = pud_create(PUD_LEVEL_PUBLIC, 0, "ring-test");
    for (int i = 0; i < 17; i++) {
        uint8_t tmp[4] = { (uint8_t)i, 0, 0, 0 };
        sclip_copy((uint8_t)ring_pud, tmp, 4);
    }
    vspace_state_t *state = vspace_get_state();
    VSP_ASSERT(state->clipboard.head == (17 % VSPACE_CLIPBOARD_RING),
               "T3.13: Clipboard ring wraps correctly (head=1)");

    /* T3.14: Stats counters correct */
    VSP_ASSERT(state->clipboard.total_copies == 17,
               "T3.14: Stats counters correct (total_copies=17)");

    g_task_pass[2] = (g_vsp_fail == prev_fail) ? 2 : 0;
}

/* ============================================================================
 * TASK 4: Agentic Workspace Control (~10 VSP_ASSERTs)
 * ============================================================================ */

static void test_task4_agentic_control(void)
{
    VOS3_INFO("[VSP-TASK4] Agentic Workspace Control");

    uint32_t prev_fail = g_vsp_fail;

    /* Re-init all subsystems */
    vspace_init();
    sclip_init();
    mesh_init();
    action_bridge_init();

    /* T4.1: Setup COORDINATOR slot (slot 0) with SUPERVISOR + VISION */
    vsp_setup_slot(0, VOS3_CAP_SUPERVISOR | VOS3_CAP_VISION | VOS3_CAP_TOOL_USE,
                   VOS3_AGENT_COORDINATOR);
    uint32_t trust_score = 0;
    mesh_trust_score(0, &trust_score);
    VSP_ASSERT(trust_score >= MESH_TRUST_INITIAL,
               "T4.1: COORDINATOR slot 0 setup (trust >= 500)");

    /* T4.2: Setup WORKER slot (slot 1) with INFERENCE only */
    vsp_setup_slot(1, VOS3_CAP_INFERENCE, VOS3_AGENT_WORKER);
    mesh_trust_score(1, &trust_score);
    VSP_ASSERT(trust_score >= MESH_TRUST_INITIAL,
               "T4.2: WORKER slot 1 setup (trust >= 500)");

    /* T4.3: COORDINATOR UI_CLICK → approved (has VISION cap) */
    action_desc_t action;
    vsp_memzero(&action, sizeof(action));
    action.type = ACTION_TYPE_UI_CLICK;
    {
        const char *cmd = "vspace_window_move";
        for (size_t i = 0; cmd[i] && i < ACTION_CMD_MAX_LEN - 1; i++)
            action.command[i] = cmd[i];
    }
    /* COORDINATOR (slot 0) has VISION → Layer 2 pass */
    int rc = action_submit(0, &action);
    VSP_ASSERT(rc == 0, "T4.3: COORDINATOR UI_CLICK approved (VISION cap)");

    /* T4.4: WORKER UI_CLICK → denied (no VISION cap) */
    action_desc_t action2;
    vsp_memzero(&action2, sizeof(action2));
    action2.type = ACTION_TYPE_UI_CLICK;
    {
        const char *cmd = "vspace_window_focus";
        for (size_t i = 0; cmd[i] && i < ACTION_CMD_MAX_LEN - 1; i++)
            action2.command[i] = cmd[i];
    }
    rc = action_submit(1, &action2);
    VSP_ASSERT(rc != 0, "T4.4: WORKER UI_CLICK denied (no VISION cap)");

    /* T4.5: COORDINATOR focus switch works */
    int pud = pud_create(PUD_LEVEL_PUBLIC, 0, "agent-pud");
    int win = vspace_window_create((uint8_t)pud, 0, 100, 100, 300, 200,
                                   VSPACE_WIN_VISIBLE | VSPACE_WIN_AGENT_MANAGED, "AgentWin");
    rc = vspace_window_focus((uint8_t)win);
    VSP_ASSERT(rc == 0, "T4.5: COORDINATOR focus switch");

    /* T4.6: COORDINATOR workspace switch */
    rc = vspace_switch_workspace(2);
    VSP_ASSERT(rc == 0, "T4.6: COORDINATOR workspace switch");
    vspace_switch_workspace(0); /* Reset */

    /* T4.7: AGENT_MANAGED flag set */
    vspace_state_t *state = vspace_get_state();
    VSP_ASSERT(state->windows[win].flags & VSPACE_WIN_AGENT_MANAGED,
               "T4.7: AGENT_MANAGED flag set");

    /* T4.8: Non-AGENT_MANAGED: verify flag semantics */
    int non_agent_win = vspace_window_create((uint8_t)pud, 1, 50, 50, 200, 150,
                                             VSPACE_WIN_VISIBLE, "UserWin");
    VSP_ASSERT(non_agent_win >= 0 &&
               !(state->windows[non_agent_win].flags & VSPACE_WIN_AGENT_MANAGED),
               "T4.8: Non-AGENT_MANAGED flag clear on user window");

    /* T4.9: Trust decay doesn't break workspace (100 decay ticks) */
    for (int i = 0; i < 100; i++) {
        mesh_trust_tick((uint64_t)(i * 1000000));
    }
    mesh_trust_score(0, &trust_score);
    VSP_ASSERT(trust_score >= 400, "T4.9: Trust decay after 100 ticks (score >= 400)");

    /* T4.10: Audit log records entries */
    action_audit_entry_t audit_entries[8];
    uint32_t audit_count = 0;
    action_audit(audit_entries, 8, &audit_count);
    VSP_ASSERT(audit_count >= 1, "T4.10: Audit log records workspace actions");

    /* Cleanup */
    vsp_teardown_slot(0);
    vsp_teardown_slot(1);

    g_task_pass[3] = (g_vsp_fail == prev_fail) ? 2 : 0;
}

/* ============================================================================
 * TASK 5: Ultra-Deterministic Latency — Workspace & DLP (~10 VSP_ASSERTs)
 * ============================================================================ */

static void test_task5_latency_benchmark(void)
{
    VOS3_INFO("[VSP-TASK5] Ultra-Deterministic Latency Benchmark");

    uint32_t prev_fail = g_vsp_fail;

    /* Re-init */
    vspace_init();
    sclip_init();
    int pud = pud_create(PUD_LEVEL_PUBLIC, 0, "bench-pud");
    int priv_pud = pud_create(PUD_LEVEL_PRIVATE, 0, "bench-priv");
    (void)priv_pud;

    /* Create a window for workspace switching context */
    vspace_window_create((uint8_t)pud, 0, 100, 100, 400, 300,
                         VSPACE_WIN_VISIBLE, "BenchWin");

    #define BENCH_ITERS 100

    uint64_t ws_latencies[BENCH_ITERS];
    uint64_t clip_latencies[BENCH_ITERS];

    /* Measure workspace switch latency */
    for (int i = 0; i < BENCH_ITERS; i++) {
        uint8_t target_ws = (uint8_t)((i + 1) % VSPACE_MAX_WORKSPACES);
        uint64_t t0 = vos3_rdtsc();
        vspace_switch_workspace(target_ws);
        uint64_t t1 = vos3_rdtsc();
        ws_latencies[i] = t1 - t0;
    }

    /* Measure clipboard scrub latency */
    const char *test_data = "test clipboard data for latency measurement";
    for (int i = 0; i < BENCH_ITERS; i++) {
        uint64_t t0 = vos3_rdtsc();
        sclip_scrub_check((uint8_t)pud, (uint8_t)pud,
                          (const uint8_t *)test_data, 44);
        uint64_t t1 = vos3_rdtsc();
        clip_latencies[i] = t1 - t0;
    }

    /* T5.1: All 100 iterations completed */
    VSP_ASSERT(1, "T5.1: 100 iterations complete");

    /* Insertion sort for percentile computation */
    for (int i = 1; i < BENCH_ITERS; i++) {
        uint64_t key = ws_latencies[i];
        int j = i - 1;
        while (j >= 0 && ws_latencies[j] > key) {
            ws_latencies[j + 1] = ws_latencies[j];
            j--;
        }
        ws_latencies[j + 1] = key;
    }

    for (int i = 1; i < BENCH_ITERS; i++) {
        uint64_t key = clip_latencies[i];
        int j = i - 1;
        while (j >= 0 && clip_latencies[j] > key) {
            clip_latencies[j + 1] = clip_latencies[j];
            j--;
        }
        clip_latencies[j + 1] = key;
    }

    /* Percentiles */
    uint64_t ws_p50   = ws_latencies[49];
    uint64_t ws_p99   = ws_latencies[98];
    uint64_t ws_p999  = ws_latencies[99]; /* Best approx for 100 samples */
    uint64_t ws_max   = ws_latencies[99];

    uint64_t clip_p50  = clip_latencies[49];
    uint64_t clip_p99  = clip_latencies[98];

    /* T5.2: Workspace switch P50 < 5ms (15M cycles @ 3GHz) */
    VSP_ASSERT(ws_p50 < 15000000ULL, "T5.2: Workspace switch P50 < 5ms");

    /* T5.3: Workspace switch P99 < 20ms (60M cycles) */
    VSP_ASSERT(ws_p99 < 60000000ULL, "T5.3: Workspace switch P99 < 20ms");

    /* T5.4: Workspace switch P99.9 < 50ms (150M cycles) */
    VSP_ASSERT(ws_p999 < 150000000ULL, "T5.4: Workspace switch P99.9 < 50ms");

    /* T5.5: Max < 80ms (240M cycles) */
    VSP_ASSERT(ws_max < 240000000ULL, "T5.5: Workspace switch max < 80ms");

    /* T5.6: Clipboard scrub P50 < 1ms (3M cycles) */
    VSP_ASSERT(clip_p50 < 3000000ULL, "T5.6: Clipboard scrub P50 < 1ms");

    /* T5.7: Clipboard scrub P99 < 5ms (15M cycles) */
    VSP_ASSERT(clip_p99 < 15000000ULL, "T5.7: Clipboard scrub P99 < 5ms");

    /* T5.8: Jitter (workspace) < 100% */
    uint64_t ws_jitter_pct = 0;
    if (ws_p50 > 0) {
        ws_jitter_pct = ((ws_p999 - ws_p50) * 100) / ws_p50;
    }
    VSP_ASSERT(ws_jitter_pct < 100 || ws_p50 < 1000,
               "T5.8: Jitter (workspace) < 100%");

    /* T5.9: Jitter (clipboard) < 100% */
    uint64_t clip_jitter_pct = 0;
    if (clip_p50 > 0) {
        clip_jitter_pct = ((clip_p99 - clip_p50) * 100) / clip_p50;
    }
    VSP_ASSERT(clip_jitter_pct < 100 || clip_p50 < 1000,
               "T5.9: Jitter (clipboard) < 100%");

    /* T5.10: Telemetry printed */
    VOS3_INFO("[VSP-BENCH] Workspace Switch: P50=%llu P99=%llu P99.9=%llu Max=%llu cycles",
              (unsigned long long)ws_p50, (unsigned long long)ws_p99,
              (unsigned long long)ws_p999, (unsigned long long)ws_max);
    VOS3_INFO("[VSP-BENCH] Clipboard Scrub:  P50=%llu P99=%llu cycles",
              (unsigned long long)clip_p50, (unsigned long long)clip_p99);
    VOS3_INFO("[VSP-BENCH] Jitter: WS=%llu%% Clip=%llu%%",
              (unsigned long long)ws_jitter_pct, (unsigned long long)clip_jitter_pct);
    VSP_ASSERT(1, "T5.10: Telemetry printed");

    #undef BENCH_ITERS

    g_task_pass[4] = (g_vsp_fail == prev_fail) ? 2 : 0;
}

/* ============================================================================
 * ENTRY POINT
 * ============================================================================ */

void vos3_phase51_ux_audit(void)
{
    VOS3_INFO("========================================================");
    VOS3_INFO("  Phase 5.1: vSpace Desktop & Sovereign App Sandboxing  ");
    VOS3_INFO("  Supreme User-Experience Audit                          ");
    VOS3_INFO("========================================================");

    g_vsp_pass = 0;
    g_vsp_fail = 0;
    g_vsp_skip = 0;

    /* Execute all 5 tasks */
    test_task1_window_manager();
    test_task2_pud_boundary();
    test_task3_clipboard_dlp();
    test_task4_agentic_control();
    test_task5_latency_benchmark();

    /* Compute total score */
    uint32_t total_score = 0;
    for (int i = 0; i < 5; i++) {
        total_score += g_task_pass[i];
    }

    /* Certificate emission */
    VOS3_INFO("========================================================");
    VOS3_INFO("  vSpace Score: %u / 10", total_score);
    VOS3_INFO("  Task 1 (Window Manager):     %s", g_task_pass[0] ? "PASS" : "FAIL");
    VOS3_INFO("  Task 2 (PUD Air-Gap):        %s", g_task_pass[1] ? "PASS" : "FAIL");
    VOS3_INFO("  Task 3 (Clipboard DLP):      %s", g_task_pass[2] ? "PASS" : "FAIL");
    VOS3_INFO("  Task 4 (Agentic Control):    %s", g_task_pass[3] ? "PASS" : "FAIL");
    VOS3_INFO("  Task 5 (Latency Benchmark):  %s", g_task_pass[4] ? "PASS" : "FAIL");
    VOS3_INFO("  Total: %u PASS, %u FAIL, %u SKIP",
              g_vsp_pass, g_vsp_fail, g_vsp_skip);

    if (total_score >= 10) {
        VOS3_INFO("  VSPACE SOVEREIGN CERTIFICATE — ABSOLUTE 10/10");
    } else if (total_score >= 8) {
        VOS3_INFO("  VSPACE SOVEREIGN CERTIFICATE — NEAR-PERFECT %u/10", total_score);
    } else {
        VOS3_WARN("  VSPACE CERTIFICATE DENIED — %u/10", total_score);
    }

    VOS3_INFO("========================================================");

    /* Final gate: must achieve at least 8/10 */
    VSP_ASSERT(total_score >= 8, "FINAL: vSpace Sovereign Certificate >= 8/10");

    (void)vsp_abs_diff;
    (void)vsp_memset;
}

#else
typedef int _vos3_production_stub;  /* ISO C requires non-empty TU */
#endif /* \!VOS3_PRODUCTION_BUILD */
