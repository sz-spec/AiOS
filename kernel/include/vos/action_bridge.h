/**
 * @file action_bridge.h
 * @brief VOS3 Sovereign Action Bridge — Guardrailed AI Execution
 *
 * @details 5-layer verification pipeline for AI-initiated actions:
 *          1. Allowlist — reject unlisted shell commands
 *          2. Capability — slot must hold required VOS3_CAP_* bits
 *          3. Trust — mesh trust score >= tier threshold
 *          4. Consensus — WORKER actions need COORDINATOR approval
 *          5. Audit — every submission logged to circular audit ring
 *
 *          Implements OWASP Agentic Top 10 mitigations for Tool Misuse
 *          (#2) and Delegated Trust (#3).
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1: Agentic Mesh & vScreen Perception
 */

#ifndef VOS3_ACTION_BRIDGE_H
#define VOS3_ACTION_BRIDGE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum pending actions */
#define ACTION_MAX_PENDING      16U

/** @brief Maximum command string length */
#define ACTION_CMD_MAX_LEN      256U

/** @brief Circular audit log capacity */
#define ACTION_AUDIT_LOG_SIZE   64U

/* Action types */
#define ACTION_TYPE_SHELL_CMD       0U  /**< Shell command execution */
#define ACTION_TYPE_FILE_OP         1U  /**< File read/write/delete */
#define ACTION_TYPE_UI_CLICK        2U  /**< UI interaction */
#define ACTION_TYPE_MEMORY_WRITE    3U  /**< Kernel memory write */
#define ACTION_TYPE_NETWORK_REQ     4U  /**< Network request */
/* Q2-2026 Hardening: VBus EXEC/APPLOAD redirect through Action Bridge */
#define ACTION_TYPE_EXEC            0x10U /**< VBus EXEC — binary execution */
#define ACTION_TYPE_APPLOAD         0x11U /**< VBus APPLOAD — app loading */

/* Action states */
#define ACTION_STATE_FREE           0U  /**< Slot empty */
#define ACTION_STATE_SUBMITTED      1U  /**< Awaiting verification */
#define ACTION_STATE_APPROVED       2U  /**< Passed all checks */
#define ACTION_STATE_PENDING        3U  /**< Awaiting COORDINATOR approval */
#define ACTION_STATE_DENIED         4U  /**< Rejected */
#define ACTION_STATE_EXECUTED       5U  /**< Successfully executed */
#define ACTION_STATE_FAILED         6U  /**< Execution failed */

/* Trust tier thresholds */
#define ACTION_TRUST_TIER_LOW       200U    /**< Reads, info queries */
#define ACTION_TRUST_TIER_MED       500U    /**< Allowlisted commands */
#define ACTION_TRUST_TIER_HIGH      800U    /**< Network, writes */

/* Allowlist */
#define ACTION_ALLOWLIST_MAX        32U     /**< Max allowlisted command prefixes */
#define ACTION_ALLOWLIST_CMD_LEN    32U     /**< Max prefix length */

/* ============================================================================
 * TYPES
 * ============================================================================ */

/**
 * @brief Action descriptor.
 */
typedef struct action_desc {
    uint32_t    action_id;                  /**< Unique action ID */
    uint8_t     type;                       /**< ACTION_TYPE_* */
    uint8_t     submitter_slot;             /**< Slot that submitted */
    uint8_t     approver_slot;              /**< Slot that approved (0xFF=none) */
    uint8_t     state;                      /**< ACTION_STATE_* */
    char        command[ACTION_CMD_MAX_LEN]; /**< Command / description */
    char        args[ACTION_CMD_MAX_LEN];   /**< Arguments (EXEC/APPLOAD) */
    uint32_t    trust_required;             /**< Trust score needed */
    int32_t     result_code;                /**< 0=ok, else errno */
    uint64_t    submit_tsc;                 /**< TSC at submission */
    uint64_t    execute_tsc;                /**< TSC at execution */
} action_desc_t;

/**
 * @brief Condensed audit entry (circular log).
 */
typedef struct action_audit_entry {
    uint32_t    action_id;                  /**< Action ID */
    uint8_t     type;                       /**< ACTION_TYPE_* */
    uint8_t     submitter_slot;             /**< Submitting slot */
    uint8_t     approver_slot;              /**< Approving slot (0xFF=N/A) */
    uint8_t     result;                     /**< Final state */
    uint32_t    trust_at_submit;            /**< Submitter's trust at time */
    int32_t     result_code;                /**< Errno or 0 */
    uint64_t    timestamp;                  /**< TSC at recording */
    char        command_prefix[32];         /**< First 31 chars of command */
} action_audit_entry_t;

/**
 * @brief Allowlist entry.
 */
typedef struct action_allowlist_entry {
    char        cmd[ACTION_ALLOWLIST_CMD_LEN];  /**< Command prefix */
    uint8_t     active;                         /**< 1 = entry in use */
} action_allowlist_entry_t;

/**
 * @brief Aggregate Action Bridge statistics.
 */
typedef struct action_stats {
    uint32_t    submitted;          /**< Total submissions */
    uint32_t    approved;           /**< Passed all checks */
    uint32_t    denied_trust;       /**< Denied: insufficient trust */
    uint32_t    denied_caps;        /**< Denied: missing capability */
    uint32_t    denied_allowlist;   /**< Denied: not on allowlist */
    uint32_t    denied_consensus;   /**< Denied: consensus rejected */
    uint32_t    executed;           /**< Successfully executed */
    uint32_t    failed;             /**< Execution failures */
} action_stats_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize the Action Bridge (populate default allowlist).
 * @return 0 on success
 */
int action_bridge_init(void);

/**
 * @brief Submit an action through the 5-layer verification pipeline.
 *
 * @param slot_id  Submitting slot
 * @param action   Action descriptor (command, type must be set)
 * @return 0 on success (approved/pending), -EPERM/-EACCES/-EINVAL on denial
 */
int action_submit(uint8_t slot_id, const action_desc_t *action);

/**
 * @brief COORDINATOR approves a pending action.
 *
 * @param approver_slot  Approving slot (must be COORDINATOR)
 * @param action_id      ID of pending action
 * @return 0 on success, -EPERM if not coordinator, -EINVAL if not pending
 */
int action_approve(uint8_t approver_slot, uint32_t action_id);

/**
 * @brief Execute an approved action.
 *
 * @param action_id  ID of approved action
 * @return 0 on success, -EINVAL if not approved
 */
int action_execute(uint32_t action_id);

/**
 * @brief Read audit log entries.
 *
 * @param out          Output array
 * @param max_entries  Array capacity
 * @param out_count    Output: entries written
 * @return 0 on success
 */
int action_audit(action_audit_entry_t *out, uint32_t max_entries,
                 uint32_t *out_count);

/**
 * @brief Get aggregate Action Bridge statistics.
 * @param out  Output stats
 */
void action_get_stats(action_stats_t *out);

/**
 * @brief Add a command prefix to the allowlist.
 *
 * @param cmd_prefix  Command prefix string (max ACTION_ALLOWLIST_CMD_LEN-1)
 * @return 0 on success, -ENOSPC if full
 */
int action_allowlist_add(const char *cmd_prefix);

/**
 * @brief Check if a command passes the allowlist.
 *
 * @param command  Full command string
 * @return 1 if allowed, 0 if not on allowlist
 */
int action_allowlist_check(const char *command);

/* ============================================================================
 * Stage 10.2 — Hallucination guardrail (per-slot min_confidence_score)
 *
 * The IntentManifest v2 envelope carries a minimum confidence score the
 * Action Bridge will honour for any action originating from a slot bound
 * to that manifest. The LLM emits a self-reported confidence with each
 * generation step; if the reported value falls below the slot's gate,
 * the action is BLOCKED and a VOS3_AUDIT_CAT_HALLUCINATION_BLOCK event
 * is recorded into the same audit ring as IntentManifest rejections.
 *
 * Range: 0..1000 (fixed-point representing 0.000..1.000). Higher = more
 * restrictive. The default for any slot whose manifest predates v2 is 0
 * (no guardrail, behaviour unchanged from Stage 10.1).
 *
 * Hot-path properties — required for the Stage 10's Asynchronous
 * Integrity Streaming sub-task:
 *   - O(1) lookup: a single uint16 read from the per-slot table.
 *   - No allocations, no logs above DEBUG, no kernel→userspace round-trip.
 *   - Audit-ring emit on block is a bounded uint64 store (audit_ring.c).
 *   This means the gate can run inline on every LLM token without adding
 *   measurable latency to the optimistic-render token stream.
 * ============================================================================ */

/**
 * @brief Stash a slot's min_confidence_score (called from cmd_intent_submit
 *        on successful INTENT_BOUND).
 *
 * Idempotent. Out-of-range scores are clamped to
 * VOS3_INTENT_MAX_CONFIDENCE_SCORE (defined in vos/tee.h, value 1000).
 *
 * @param slot_id  AI slot index (must be < VOS3_MODEL_SLOT_MAX)
 * @param score    Minimum confidence score (0..1000)
 * @return 0 on success, -1 on invalid slot_id
 */
int vos3_action_bridge_set_min_confidence(uint8_t slot_id, uint16_t score);

/**
 * @brief Check whether an LLM-reported confidence score passes the slot's gate.
 *
 * Hot-path safe: O(1), no allocations, no logging, no round-trip. Designed
 * to run inline on every output token without blocking the UI thread.
 *
 * @param slot_id  AI slot the action originates from
 * @param score    LLM-reported confidence (0..1000)
 * @return 0  if score >= slot's gate (action permitted)
 *         -1 if score <  slot's gate (action BLOCKED — also emits a
 *            VOS3_AUDIT_CAT_HALLUCINATION_BLOCK event into the audit ring)
 *         -2 if slot_id is out of range (audit not emitted)
 */
int vos3_action_bridge_check_confidence(uint8_t slot_id, uint16_t score);

/**
 * @brief Read back a slot's currently-stashed min_confidence_score.
 *
 * Read-only accessor; useful for the userspace JSON-LD compliance endpoint
 * to surface "this slot's guardrail is set at <n>" without re-parsing
 * the manifest. Returns 0 if slot_id is out of range or unset.
 */
uint16_t vos3_action_bridge_get_min_confidence(uint8_t slot_id);

/* ============================================================================
 * Stage 10.2.2 — Management override / Safe-Rollout kill switch
 *
 * Goal: high-throughput security that is fully reversible by management.
 * Three orthogonal controls, all backend-pushed via VBus, none requiring
 * a kernel rebuild:
 *
 *   (1) Per-agent override.   vos3_action_bridge_set_min_confidence is
 *       already callable by anyone holding a slot id — backend exposes it
 *       through a POLICY_OVERRIDE VBus command (separate from INTENT_SUBMIT)
 *       so the CEO can dial a single agent up or down without re-issuing
 *       a manifest.
 *
 *   (2) Global force-permit (Safe-Rollout mode). When enabled,
 *       check_confidence still fires its audit-ring entry for every
 *       would-block but returns 0 (permit). Lets the org collect
 *       telemetry on what WOULD be blocked before flipping enforcement
 *       on. Audit category for these events is
 *       VOS3_AUDIT_CAT_FORCE_PERMIT_OVERRIDE so a downstream JSON-LD
 *       endpoint can show "would-block | observed_score" separately
 *       from real blocks.
 *
 *   (3) Status read-back so the management dashboard reflects the
 *       current global mode without polling N slots.
 *
 * Reversibility contract: every flip is single-call, single-frame, no
 * persistent kernel state beyond the live setting. Restart of the
 * backend re-asserts whatever VOS_FORCE_PERMIT says; the kernel never
 * remembers a previous override across reboots.
 * ============================================================================ */

/**
 * @brief Toggle global force-permit (Safe-Rollout mode).
 *
 * @param enabled  Non-zero turns force-permit ON (every would-block becomes
 *                 a permit, but the audit-ring entry is still emitted, now
 *                 under category VOS3_AUDIT_CAT_FORCE_PERMIT_OVERRIDE).
 *                 Zero turns it OFF (default; check_confidence enforces).
 */
void vos3_action_bridge_set_force_permit(int enabled);

/**
 * @brief Read current force-permit setting.
 *
 * @return 0 if enforcement is active (default), non-zero if Safe-Rollout
 *         mode is on.
 */
int vos3_action_bridge_get_force_permit(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ACTION_BRIDGE_H */
