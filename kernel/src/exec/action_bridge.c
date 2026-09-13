/**
 * @file action_bridge.c
 * @brief VOS3 Sovereign Action Bridge — 5-Layer Verification Pipeline
 *
 * @details Every AI-initiated action passes through:
 *          1. Allowlist check (shell commands)
 *          2. Capability validation (VOS3_CAP_* bits)
 *          3. Trust threshold gate (mesh trust score)
 *          4. Consensus requirement (WORKER → COORDINATOR approval)
 *          5. Audit logging (circular ring buffer)
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1: Agentic Mesh & vScreen Perception
 */

#include "../../include/vos/action_bridge.h"
#include "../../include/vos/agent_mesh.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/console.h"
#include "../../include/vos/virtio_vbus.h"
#include "../../include/vos/audit_ring.h"      /* Stage 10.2: HALLUCINATION_BLOCK */
#include "../../include/vos/tee.h"             /* Stage 10.2: VOS3_INTENT_MAX_CONFIDENCE_SCORE */
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * STATIC STATE (~10KB BSS)
 * ============================================================================ */

static action_desc_t            g_actions[ACTION_MAX_PENDING];         /* ~5KB */
static action_audit_entry_t     g_audit_log[ACTION_AUDIT_LOG_SIZE];    /* ~3.5KB */
static action_allowlist_entry_t g_allowlist[ACTION_ALLOWLIST_MAX];     /* ~1.2KB */
static action_stats_t           g_action_stats;
static uint32_t                 g_next_action_id;
static uint32_t                 g_audit_head;   /* Circular write index */
static uint32_t                 g_audit_count;  /* Total entries (up to size) */
static uint8_t                  g_action_initialized;

/* Stage 10.2 — per-slot hallucination guardrail.
 * Indexed by slot_id; default 0 (no guardrail). Populated by
 * cmd_intent_submit on a successful INTENT_BOUND with a v2 manifest. */
static uint16_t                 g_slot_min_confidence[VOS3_MODEL_SLOT_MAX];

/* Stage 10.2.2 — global force-permit (Safe-Rollout mode).
 * Volatile so a backend toggle from one CPU is observed on another
 * without an explicit barrier; correctness does not depend on which
 * value is observed during a flip — both old and new behaviour are
 * safe (permit-with-log vs. block-with-log), so a transient race is
 * benign. */
static volatile int             g_force_permit = 0;

/* External: model slot array */
extern vos3_ai_model_slot_t g_model_slots[];

/* ============================================================================
 * Resilience-Matrix F10 — idempotency ring.
 *
 * Defends against rapid-double-submit (e.g. PAY-button double-click,
 * webhook retry, dropped-and-resent payment flow). Each submission's
 * (slot, type, command, args) is hashed to a 64-bit fingerprint;
 * recent fingerprints are kept in a small ring with their submit TSC.
 * A re-submission within ACTION_IDEMPOTENCY_WINDOW_TSC of an identical
 * fingerprint is rejected with -EEXIST without allocating a slot or
 * running the 5-layer pipeline.
 *
 * The ring is intentionally tiny (16 entries). Goal: catch accidental
 * replays in the seconds-window. A determined attacker can outlive
 * the window — at which point the trust+capability+consensus layers
 * are the defense.
 * ============================================================================ */
#define ACTION_IDEMPOTENCY_RING_SIZE  16U
/* ~200ms window on commodity x86_64 hardware (1 GHz TSC ⇒ 2e8 cycles).
 * Conservative: false-rejection is cheap (caller resubmits with new
 * args), false-accept of a real double-click is the harm we prevent. */
#define ACTION_IDEMPOTENCY_WINDOW_TSC 200000000ULL

typedef struct {
    uint64_t fingerprint;
    uint64_t submit_tsc;
} action_idem_entry_t;

static action_idem_entry_t      g_idem_ring[ACTION_IDEMPOTENCY_RING_SIZE];
static uint32_t                 g_idem_head;

/* FNV-1a-64 over (slot, type, command, args). */
static uint64_t action_fingerprint(uint8_t slot_id,
                                   const action_desc_t *a)
{
    uint64_t h = 0xcbf29ce484222325ULL;          /* FNV-1a-64 offset basis */
    const uint64_t prime = 0x00000100000001B3ULL;
    h = (h ^ (uint64_t)slot_id) * prime;
    h = (h ^ (uint64_t)a->type)  * prime;
    for (size_t i = 0; i < ACTION_CMD_MAX_LEN; i++) {
        uint8_t c = (uint8_t)a->command[i];
        h = (h ^ (uint64_t)c) * prime;
        if (c == 0) break;
    }
    for (size_t i = 0; i < ACTION_CMD_MAX_LEN; i++) {
        uint8_t c = (uint8_t)a->args[i];
        h = (h ^ (uint64_t)c) * prime;
        if (c == 0) break;
    }
    return h;
}

/* Returns 1 if (fp, now) is a duplicate of a recent ring entry within
 * the window; 0 otherwise. Side effect on non-dup: records into ring. */
static int action_idem_check_and_record(uint64_t fp, uint64_t now)
{
    for (uint32_t i = 0; i < ACTION_IDEMPOTENCY_RING_SIZE; i++) {
        if (g_idem_ring[i].fingerprint == fp &&
            g_idem_ring[i].submit_tsc != 0 &&
            now - g_idem_ring[i].submit_tsc < ACTION_IDEMPOTENCY_WINDOW_TSC) {
            return 1;
        }
    }
    g_idem_ring[g_idem_head].fingerprint = fp;
    g_idem_ring[g_idem_head].submit_tsc  = now;
    g_idem_head = (g_idem_head + 1U) % ACTION_IDEMPOTENCY_RING_SIZE;
    return 0;
}

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void ab_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) {
        p[i] = 0;
    }
}

static void ab_memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; i++) {
        d[i] = s[i];
    }
}

static size_t ab_strlen(const char *s)
{
    size_t n = 0;
    while (s[n]) n++;
    return n;
}

static void ab_strncpy(char *dst, const char *src, size_t max)
{
    size_t i;
    for (i = 0; i < max - 1 && src[i]; i++) {
        dst[i] = src[i];
    }
    dst[i] = '\0';
}

/**
 * @brief Check if string a starts with prefix b.
 */
static int ab_starts_with(const char *a, const char *b)
{
    while (*b) {
        if (*a != *b) return 0;
        a++;
        b++;
    }
    return 1;
}

/**
 * @brief Map action type to required capability.
 */
static uint64_t action_type_to_cap(uint8_t type)
{
    switch (type) {
    case ACTION_TYPE_SHELL_CMD:     return VOS3_CAP_TOOL_USE;
    case ACTION_TYPE_FILE_OP:       return VOS3_CAP_TOOL_USE;
    case ACTION_TYPE_UI_CLICK:      return VOS3_CAP_VISION;
    case ACTION_TYPE_MEMORY_WRITE:  return VOS3_CAP_CODE_GEN;
    case ACTION_TYPE_NETWORK_REQ:   return VOS3_CAP_NETWORK;
    /* Q2-2026 Hardening: EXEC/APPLOAD require VOS3_CAP_EXEC */
    case ACTION_TYPE_EXEC:          return VOS3_CAP_EXEC;
    case ACTION_TYPE_APPLOAD:       return VOS3_CAP_EXEC;
    default:                        return 0;
    }
}

/**
 * @brief Map action type to trust tier threshold.
 */
static uint32_t action_trust_tier(uint8_t type)
{
    switch (type) {
    case ACTION_TYPE_FILE_OP:       return ACTION_TRUST_TIER_LOW;
    case ACTION_TYPE_UI_CLICK:      return ACTION_TRUST_TIER_LOW;
    case ACTION_TYPE_SHELL_CMD:     return ACTION_TRUST_TIER_MED;
    case ACTION_TYPE_MEMORY_WRITE:  return ACTION_TRUST_TIER_HIGH;
    case ACTION_TYPE_NETWORK_REQ:   return ACTION_TRUST_TIER_HIGH;
    /* Q2-2026: EXEC/APPLOAD require HIGH trust — binary execution is dangerous */
    case ACTION_TYPE_EXEC:          return ACTION_TRUST_TIER_HIGH;
    case ACTION_TYPE_APPLOAD:       return ACTION_TRUST_TIER_HIGH;
    default:                        return ACTION_TRUST_TIER_MED;
    }
}

/* Q2-2026 Hardening: EXEC path allowlist — only trusted directories */
static const char *g_exec_allowlist[] = {
    "/apps/",           /* VPK-deployed applications only */
    "/bin/vos3_",       /* VOS3-signed system binaries */
    NULL
};

static int exec_path_allowed(const char *path)
{
    if (path == NULL) return 0;
    for (int i = 0; g_exec_allowlist[i] != NULL; i++) {
        if (ab_starts_with(path, g_exec_allowlist[i]))
            return 1;
    }
    return 0;
}

/**
 * @brief Record an audit entry.
 */
static void action_audit_record(const action_desc_t *a, uint32_t trust_score)
{
    action_audit_entry_t *entry = &g_audit_log[g_audit_head % ACTION_AUDIT_LOG_SIZE];
    entry->action_id      = a->action_id;
    entry->type           = a->type;
    entry->submitter_slot = a->submitter_slot;
    entry->approver_slot  = a->approver_slot;
    entry->result         = a->state;
    entry->trust_at_submit = trust_score;
    entry->result_code    = a->result_code;
    entry->timestamp      = vos3_rdtsc();

    /* Copy first 31 chars of command */
    ab_strncpy(entry->command_prefix, a->command, 32);

    g_audit_head++;
    if (g_audit_count < ACTION_AUDIT_LOG_SIZE) {
        g_audit_count++;
    }
}

/**
 * @brief Find a free action slot.
 */
static int action_find_free(void)
{
    for (int i = 0; i < (int)ACTION_MAX_PENDING; i++) {
        if (g_actions[i].state == ACTION_STATE_FREE) {
            return i;
        }
    }
    return -1;
}

/**
 * @brief Find an action by ID.
 */
static int action_find_by_id(uint32_t action_id)
{
    for (int i = 0; i < (int)ACTION_MAX_PENDING; i++) {
        if (g_actions[i].action_id == action_id &&
            g_actions[i].state != ACTION_STATE_FREE) {
            return i;
        }
    }
    return -1;
}

/* ============================================================================
 * DEFAULT ALLOWLIST
 * ============================================================================ */

static const char *g_default_allowlist[] = {
    "ls", "cat", "head", "tail", "wc", "sort", "grep", "find",
    "stat", "echo", "date", "uname", "pwd", "whoami", "env",
    NULL
};

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int action_bridge_init(void)
{
    ab_memzero(g_actions, sizeof(g_actions));
    ab_memzero(g_audit_log, sizeof(g_audit_log));
    ab_memzero(g_allowlist, sizeof(g_allowlist));
    ab_memzero(&g_action_stats, sizeof(g_action_stats));
    ab_memzero(g_slot_min_confidence, sizeof(g_slot_min_confidence));
    g_next_action_id = 1;
    g_audit_head = 0;
    g_audit_count = 0;

    /* Populate default allowlist */
    for (int i = 0; g_default_allowlist[i] != NULL; i++) {
        if (i >= (int)ACTION_ALLOWLIST_MAX) break;
        ab_strncpy(g_allowlist[i].cmd, g_default_allowlist[i],
                    ACTION_ALLOWLIST_CMD_LEN);
        g_allowlist[i].active = 1;
    }

    g_action_initialized = 1;
    VOS3_INFO("[ACTION-BRIDGE] Sovereign Action Bridge initialized "
              "(%u slots, %u audit ring, 15 default allowlist entries)",
              (unsigned)ACTION_MAX_PENDING, (unsigned)ACTION_AUDIT_LOG_SIZE);
    return 0;
}

int action_submit(uint8_t slot_id, const action_desc_t *action)
{
    if (!g_action_initialized) return -22; /* EINVAL */
    if (action == NULL) return -22;
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;

    /* Phase 6.1: Quarantine gate — deny all submissions during quarantine */
    extern int guardian_is_quarantined(void);
    if (guardian_is_quarantined()) return -1;

    /* Resilience-Matrix F10 — idempotency check.
     * Reject a perfect duplicate (same slot/type/command/args) within
     * the recent window without doing any further work. Caller can
     * re-submit after the window expires, or with a different action. */
    {
        const uint64_t fp  = action_fingerprint(slot_id, action);
        const uint64_t now = vos3_rdtsc();
        if (action_idem_check_and_record(fp, now)) {
            return -17; /* EEXIST — duplicate within idempotency window */
        }
    }

    g_action_stats.submitted++;

    /* Find free slot */
    int idx = action_find_free();
    if (idx < 0) return -28; /* ENOSPC */

    /* Copy action descriptor */
    action_desc_t *a = &g_actions[idx];
    ab_memcpy(a, action, sizeof(action_desc_t));
    a->action_id = g_next_action_id++;
    a->submitter_slot = slot_id;
    a->submit_tsc = vos3_rdtsc();
    a->state = ACTION_STATE_SUBMITTED;

    /* Get submitter trust score */
    uint32_t trust = 0;
    mesh_trust_score(slot_id, &trust);

    /* === Layer 1: Allowlist check (shell commands + EXEC path) === */
    if (a->type == ACTION_TYPE_SHELL_CMD) {
        if (!action_allowlist_check(a->command)) {
            a->state = ACTION_STATE_DENIED;
            a->result_code = -13; /* EACCES */
            g_action_stats.denied_allowlist++;
            action_audit_record(a, trust);
            vos3_vbus_event_ring_push(slot_id,
                (uint8_t)VOS3_EVENT_ACTION_DENIED, a->action_id);
            a->state = ACTION_STATE_FREE; /* release slot */
            return -13; /* EACCES */
        }
    }
    /* Q2-2026 Hardening: EXEC/APPLOAD path allowlist */
    if (a->type == ACTION_TYPE_EXEC || a->type == ACTION_TYPE_APPLOAD) {
        if (!exec_path_allowed(a->command)) {
            a->state = ACTION_STATE_DENIED;
            a->result_code = -13; /* EACCES */
            g_action_stats.denied_allowlist++;
            VOS3_WARN("[ACTION-BRIDGE] EXEC denied: path '%s' not in exec allowlist",
                      a->command);
            action_audit_record(a, trust);
            vos3_vbus_event_ring_push(slot_id,
                (uint8_t)VOS3_EVENT_ACTION_DENIED, a->action_id);
            a->state = ACTION_STATE_FREE;
            return -13; /* EACCES */
        }
    }

    /* === Layer 2: Capability check === */
    uint64_t required_cap = action_type_to_cap(a->type);
    uint64_t caps = g_model_slots[slot_id].capabilities;
    if (required_cap != 0 && !(caps & required_cap)) {
        a->state = ACTION_STATE_DENIED;
        a->result_code = -1; /* EPERM */
        g_action_stats.denied_caps++;
        action_audit_record(a, trust);
        vos3_vbus_event_ring_push(slot_id,
            (uint8_t)VOS3_EVENT_ACTION_DENIED, a->action_id);
        a->state = ACTION_STATE_FREE;
        return -1; /* EPERM */
    }

    /* === Layer 3: Trust threshold === */
    uint32_t required_trust = action_trust_tier(a->type);
    if (trust < required_trust) {
        a->state = ACTION_STATE_DENIED;
        a->result_code = -13; /* EACCES */
        g_action_stats.denied_trust++;
        action_audit_record(a, trust);
        vos3_vbus_event_ring_push(slot_id,
            (uint8_t)VOS3_EVENT_ACTION_DENIED, a->action_id);
        a->state = ACTION_STATE_FREE;
        return -13; /* EACCES */
    }
    a->trust_required = required_trust;

    /* === Layer 4: Consensus gate for WORKER slots === */
    uint8_t agent_type = g_model_slots[slot_id].agent_type;
    if (agent_type == VOS3_AGENT_WORKER) {
        /* WORKER needs COORDINATOR approval */
        a->state = ACTION_STATE_PENDING;
        a->approver_slot = 0xFF;
        action_audit_record(a, trust);
        vos3_vbus_event_ring_push(slot_id,
            (uint8_t)VOS3_EVENT_ACTION_SUBMITTED, a->action_id);
        return 0; /* Pending — needs action_approve() */
    }

    /* === Layer 5: Audit + approve === */
    a->state = ACTION_STATE_APPROVED;
    g_action_stats.approved++;
    action_audit_record(a, trust);
    vos3_vbus_event_ring_push(slot_id,
        (uint8_t)VOS3_EVENT_ACTION_SUBMITTED, a->action_id);
    return 0;
}

int action_approve(uint8_t approver_slot, uint32_t action_id)
{
    if (!g_action_initialized) return -22;
    if (approver_slot >= VOS3_MODEL_SLOT_MAX) return -22;

    /* Only COORDINATOR can approve */
    if (g_model_slots[approver_slot].agent_type != VOS3_AGENT_COORDINATOR) {
        return -1; /* EPERM */
    }

    int idx = action_find_by_id(action_id);
    if (idx < 0) return -22; /* EINVAL: not found */

    action_desc_t *a = &g_actions[idx];
    if (a->state != ACTION_STATE_PENDING) return -22;

    a->state = ACTION_STATE_APPROVED;
    a->approver_slot = approver_slot;
    g_action_stats.approved++;

    /* Update audit */
    uint32_t trust = 0;
    mesh_trust_score(a->submitter_slot, &trust);
    action_audit_record(a, trust);

    return 0;
}

int action_execute(uint32_t action_id)
{
    if (!g_action_initialized) return -22;

    int idx = action_find_by_id(action_id);
    if (idx < 0) return -22;

    action_desc_t *a = &g_actions[idx];
    if (a->state != ACTION_STATE_APPROVED) return -22;

    a->state = ACTION_STATE_EXECUTED;
    a->execute_tsc = vos3_rdtsc();
    a->result_code = 0;
    g_action_stats.executed++;

    /* Emit execution event */
    vos3_vbus_event_ring_push(a->submitter_slot,
        (uint8_t)VOS3_EVENT_ACTION_EXECUTED, action_id);

    /* Update audit */
    uint32_t trust = 0;
    mesh_trust_score(a->submitter_slot, &trust);
    action_audit_record(a, trust);

    /* Free slot */
    a->state = ACTION_STATE_FREE;
    return 0;
}

int action_audit(action_audit_entry_t *out, uint32_t max_entries,
                 uint32_t *out_count)
{
    if (out == NULL || out_count == NULL) return -22;

    uint32_t count = g_audit_count;
    if (count > max_entries) count = max_entries;

    /* Read from oldest to newest */
    uint32_t start;
    if (g_audit_count >= ACTION_AUDIT_LOG_SIZE) {
        start = g_audit_head; /* Wrapped — oldest is at head */
    } else {
        start = 0;
    }

    for (uint32_t i = 0; i < count; i++) {
        uint32_t idx = (start + i) % ACTION_AUDIT_LOG_SIZE;
        ab_memcpy(&out[i], &g_audit_log[idx], sizeof(action_audit_entry_t));
    }

    *out_count = count;
    return 0;
}

void action_get_stats(action_stats_t *out)
{
    if (out != NULL) {
        ab_memcpy(out, &g_action_stats, sizeof(g_action_stats));
    }
}

int action_allowlist_add(const char *cmd_prefix)
{
    if (cmd_prefix == NULL) return -22;
    if (ab_strlen(cmd_prefix) == 0) return -22;

    for (uint32_t i = 0; i < ACTION_ALLOWLIST_MAX; i++) {
        if (!g_allowlist[i].active) {
            ab_strncpy(g_allowlist[i].cmd, cmd_prefix, ACTION_ALLOWLIST_CMD_LEN);
            g_allowlist[i].active = 1;
            return 0;
        }
    }
    return -28; /* ENOSPC */
}

int action_allowlist_check(const char *command)
{
    if (command == NULL) return 0;

    for (uint32_t i = 0; i < ACTION_ALLOWLIST_MAX; i++) {
        if (!g_allowlist[i].active) continue;
        if (ab_starts_with(command, g_allowlist[i].cmd)) {
            return 1; /* Allowed */
        }
    }
    return 0; /* Not on allowlist */
}

/* ============================================================================
 * Stage 10.2 — Hallucination guardrail (per-slot min_confidence_score)
 *
 * Hot-path safety analysis (relevant to Stage 10's Asynchronous Integrity
 * Streaming sub-task — the gate must not add measurable latency to the
 * optimistic-render token stream):
 *
 *   set_min_confidence:   single uint16 store, branch-free past bounds check.
 *   check_confidence:     single uint16 load + compare; on the BLOCK branch
 *                         a bounded write into the audit ring (also O(1)).
 *   get_min_confidence:   single uint16 load, branch-free past bounds check.
 *
 * No allocations, no logs above DEBUG, no kernel→userspace round-trip.
 * The audit emit on block is the same cost as the Stage-10.1 ring writes
 * already in cmd_intent_submit. Each call is therefore safely usable on
 * every output token from the LLM without blocking the UI thread.
 * ============================================================================ */

int vos3_action_bridge_set_min_confidence(uint8_t slot_id, uint16_t score)
{
    if (slot_id >= (uint8_t)VOS3_MODEL_SLOT_MAX) {
        return -1;
    }
    /* Clamp to documented max (1000). Out-of-range values from a v2
     * manifest would already have been rejected by vos3_intent_validate;
     * the clamp here is belt-and-braces against a future caller path
     * that doesn't go through the validator. */
    const uint16_t clamped =
        (score > (uint16_t)VOS3_INTENT_MAX_CONFIDENCE_SCORE)
            ? (uint16_t)VOS3_INTENT_MAX_CONFIDENCE_SCORE
            : score;
    g_slot_min_confidence[slot_id] = clamped;
    return 0;
}

int vos3_action_bridge_check_confidence(uint8_t slot_id, uint16_t score)
{
    if (slot_id >= (uint8_t)VOS3_MODEL_SLOT_MAX) {
        return -2;
    }
    const uint16_t gate = g_slot_min_confidence[slot_id];
    if (score >= gate) {
        return 0;  /* permitted (gate==0 short-circuits to permit) */
    }

    /* Would-block path. rc encoding: -(observed_score). Score is 0..1000
     * so the negated value fits int16_t and a reader knows "the action
     * arrived with confidence=|rc|". The slot's gate is recoverable via
     * vos3_action_bridge_get_min_confidence(slot_id) at quote time. */
    const int16_t observed_neg = -(int16_t)score;

    /* Stage 10.2.2 — force-permit / Safe-Rollout decision.
     *
     * Either way the audit-ring entry IS emitted; the only difference is
     * the category (so a downstream filter can split "real blocks" from
     * "would-have-blocked, permitted under Safe-Rollout") and the return
     * value (the caller treats permit=0 as "execute the action"). */
    if (g_force_permit) {
        vos3_audit_emit_failure(
            (uint16_t)VOS3_AUDIT_CAT_FORCE_PERMIT_OVERRIDE,
            slot_id,
            observed_neg,
            /*digest48=*/(const uint8_t *)0);
        return 0;  /* Safe-Rollout: permit + telemetry */
    }

    vos3_audit_emit_failure((uint16_t)VOS3_AUDIT_CAT_HALLUCINATION_BLOCK,
                            slot_id,
                            observed_neg,
                            /*digest48=*/(const uint8_t *)0);
    return -1;
}

void vos3_action_bridge_set_force_permit(int enabled)
{
    g_force_permit = enabled ? 1 : 0;
}

int vos3_action_bridge_get_force_permit(void)
{
    return g_force_permit;
}

uint16_t vos3_action_bridge_get_min_confidence(uint8_t slot_id)
{
    if (slot_id >= (uint8_t)VOS3_MODEL_SLOT_MAX) {
        return 0;
    }
    return g_slot_min_confidence[slot_id];
}
