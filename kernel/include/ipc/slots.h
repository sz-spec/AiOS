/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Slot Capability Model — v20.5 Phase 5.0
 * =============================================
 *
 * Defines the four immutable slot roles for the multi-role dispatcher.
 * Roles are encoded as orthogonal capability bits so a single slot can
 * carry more than one capability if needed (defensive, even though the
 * default mapping is one-cap-per-slot today).
 *
 * Default mapping (immutable at boot):
 *   Slot 0:  VOS3_CAP_COORDINATOR  (kernel-only, immune to user dispatch)
 *   Slot 1:  VOS3_CAP_PREFILL      (heavy compute, large model loads)
 *   Slot 2:  VOS3_CAP_DECODE       (high-throughput continuation)
 *   Slot 3:  VOS3_CAP_DECODE       (paired with slot 2 for affinity)
 *
 * The state machine (vos3_slot_state_t) describes the lifecycle of
 * any one slot. ZOMBIE is the quarantine state entered on a security
 * violation (e.g. W^X policy breach in mprotect). A ZOMBIE slot has its
 * memory scrubbed (`vos3_pud_scrub`) and the event recorded in the MMR
 * audit ledger (`mmr_record_security_violation`).
 */

#ifndef VOS3_IPC_SLOTS_H
#define VOS3_IPC_SLOTS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ----- Capability flags (orthogonal bits) ----- */

/* NOTE: VOS3_CAP_NETWORK and other agent-level capability flags live in
 * include/vos/ai_guard.h. The slot-role capabilities below are a separate
 * namespace (slot dispatch), not agent capabilities (model authority).
 * Do not collide. */
#define VOS3_SLOT_CAP_NONE         0x00000000U
#define VOS3_CAP_COORDINATOR       0x00000001U   /* Slot 0 only — kernel scheduler */
#define VOS3_CAP_PREFILL           0x00000002U   /* Slot 1 — heavy compute / model load */
#define VOS3_CAP_DECODE            0x00000004U   /* Slots 2-3 — autoregressive decode */
#define VOS3_CAP_GPU_DIRECT        0x00000008U   /* May map GPU buffers via vfio_core */

#define VOS3_SLOT_CAP_MASK_USER    \
    (VOS3_CAP_PREFILL | VOS3_CAP_DECODE | VOS3_CAP_GPU_DIRECT)

/* ----- Slot identifiers (immutable; do not renumber) ----- */

#define VOS3_SLOT_COORDINATOR     0U
#define VOS3_SLOT_PREFILL         1U
#define VOS3_SLOT_DECODE_PRIMARY  2U
#define VOS3_SLOT_DECODE_PAIRED   3U
#define VOS3_SLOT_MAX_IMMUTABLE   4U

/* The kernel statically allocates 8 model slots (legacy from v20.0).
 * Slots 4-7 remain available for future role expansion. The first 4 are
 * reserved by capability and must not be reassigned at runtime. */
#define VOS3_SLOT_MAX_TOTAL       8U

/* ----- Slot lifecycle state machine ----- */

typedef enum vos3_slot_state {
    VOS3_SLOT_STATE_UNINITIALIZED = 0,
    VOS3_SLOT_STATE_LOADING       = 1,   /* Model weights being loaded */
    VOS3_SLOT_STATE_READY         = 2,   /* Idle, ready to accept work */
    VOS3_SLOT_STATE_RUNNING       = 3,   /* Currently executing inference */
    VOS3_SLOT_STATE_SUSPENDED     = 4,   /* Paused (KV cache preserved) */
    VOS3_SLOT_STATE_ZOMBIE        = 5,   /* Security quarantine — terminal */
    VOS3_SLOT_STATE_RECLAIMED     = 6,   /* Scrubbed and freed */
} vos3_slot_state_t;

/* ----- API ----- */

/* Return the capability bitmask for a given slot. Returns VOS3_CAP_NONE
 * for an out-of-range slot id. The mapping is immutable at boot. */
uint32_t vos3_slot_capabilities(uint32_t slot_id);

/* Return non-zero if `slot_id` is currently authorized to perform any of
 * the actions encoded in `required_caps`. */
int vos3_slot_has_capability(uint32_t slot_id, uint32_t required_caps);

/* Read / write the lifecycle state of a slot. Transitioning to ZOMBIE
 * is irreversible — the slot must go through RECLAIMED to be reused. */
vos3_slot_state_t vos3_slot_get_state(uint32_t slot_id);
int vos3_slot_transition_state(uint32_t slot_id, vos3_slot_state_t new_state);

/* Zero-fill the slot's PUD (page-upper-directory) backing memory. Called
 * on transition to ZOMBIE state. Operates on full 1 GiB regions covered
 * by the slot's PUD entries. Returns number of bytes scrubbed. */
uint64_t vos3_pud_scrub(uint32_t slot_id);

/* Record a security violation in the MMR audit ledger. Wraps
 * `mmr_record_event` with the canonical SHA-256 label
 * `OP_SECURITY_VIOLATION_WX`. Safe to call from any context including
 * a fault handler. Never raises. */
void mmr_record_security_violation(uint32_t slot_id, uint32_t reason_code);

/* W^X-violation entry point invoked from `vos3_vmm_mprotect_range` when
 * a page is requested with PROT_WRITE | PROT_EXEC. Performs the full
 * quarantine sequence: state transition + scrub + MMR record. */
void vos3_slot_wx_violation_handler(uintptr_t addr, uint64_t len, int prot);

/* Reason codes for `mmr_record_security_violation`. */
#define VOS3_SECVIO_REASON_WX             0x01
#define VOS3_SECVIO_REASON_SLOT_ESCAPE    0x02
#define VOS3_SECVIO_REASON_DMA_BREACH     0x03
#define VOS3_SECVIO_REASON_OWNER_MISMATCH 0x04

#ifdef __cplusplus
}
#endif

#endif /* VOS3_IPC_SLOTS_H */
