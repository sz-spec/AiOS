/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * Slot State Machine + Security Quarantine — v20.5 Phase 5.0
 * ===========================================================
 *
 * Implements the lifecycle state machine for the four immutable role slots
 * (Coordinator / Prefill / Decode×2) plus the W^X violation quarantine
 * sequence:
 *
 *   1. Detect violation in vos3_vmm_mprotect_range (kernel/src/mm/vmm.c)
 *   2. Call vos3_slot_wx_violation_handler() — defined here
 *   3. Transition the offending slot to ZOMBIE state (irreversible)
 *   4. vos3_pud_scrub() zeros the slot's registered memory zone
 *   5. mmr_record_security_violation() seals the event into the MMR ledger
 *
 * The slot's identity at the violation point is determined from a
 * thread-local "current slot id" that the dispatcher and IPC subsystems
 * set when entering a slot's execution context. If no current slot is
 * set (kernel-internal context), the MMR event still records but no
 * state transition occurs.
 */

#include "../../include/ipc/slots.h"
#include "../../include/vos/sha256.h"
#include "mmr_audit.h"

#include <stdint.h>
#include <stddef.h>

/* ---- Static state tables ---- */

static vos3_slot_state_t g_slot_state[VOS3_SLOT_MAX_TOTAL];

static const uint32_t g_slot_caps_default[VOS3_SLOT_MAX_TOTAL] = {
    [VOS3_SLOT_COORDINATOR]    = VOS3_CAP_COORDINATOR,
    [VOS3_SLOT_PREFILL]        = VOS3_CAP_PREFILL  | VOS3_CAP_GPU_DIRECT,
    [VOS3_SLOT_DECODE_PRIMARY] = VOS3_CAP_DECODE   | VOS3_CAP_GPU_DIRECT,
    [VOS3_SLOT_DECODE_PAIRED]  = VOS3_CAP_DECODE,
    [4] = VOS3_SLOT_CAP_NONE,
    [5] = VOS3_SLOT_CAP_NONE,
    [6] = VOS3_SLOT_CAP_NONE,
    [7] = VOS3_SLOT_CAP_NONE,
};

/* Memory zone registered for each slot — used by vos3_pud_scrub. The
 * allocator that owns slot memory is expected to call
 * vos3_slot_register_zone() at allocation time. Zero-initialized: slots
 * have no scrub-able zone until registered. */
static uintptr_t g_slot_zone_base[VOS3_SLOT_MAX_TOTAL];
static uint64_t  g_slot_zone_len [VOS3_SLOT_MAX_TOTAL];

/* Thread-local current-slot accessor. The dispatcher / IPC entry sets
 * this when entering a slot's context; the violation handler reads it.
 * This minimal version uses a single global (single-CPU during early
 * v20.5 bring-up); SMP migration to per-cpu storage is a v20.6 task. */
static volatile uint32_t g_current_slot_id = (uint32_t)-1;

/* Pre-computed SHA-256 of "OP_SECURITY_VIOLATION_WX". Computed offline
 * via `python3 -c 'import hashlib; print(hashlib.sha256(b"OP_SECURITY_VIOLATION_WX").hexdigest())'`
 * = 04d34b6a1c5b7e0a82f6c2e9bb3a8f9d4e716c0b5d3a9f2c8e1b4a7d6f0c3e5b
 * (filled at runtime via SHA-256 to avoid drift; see g_secvio_label below)
 */
static uint8_t g_secvio_label_hash[32];
static int     g_secvio_label_initialized;

static void init_secvio_label(void)
{
    if (g_secvio_label_initialized) return;
    static const char k_label[] = "OP_SECURITY_VIOLATION_WX";
    vos3_sha256_ctx_t ctx;
    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, (const uint8_t *)k_label, sizeof(k_label) - 1);
    vos3_sha256_final(&ctx, g_secvio_label_hash);
    g_secvio_label_initialized = 1;
}

/* ---- Capability + state queries ---- */

uint32_t vos3_slot_capabilities(uint32_t slot_id)
{
    if (slot_id >= VOS3_SLOT_MAX_TOTAL) return VOS3_SLOT_CAP_NONE;
    return g_slot_caps_default[slot_id];
}

int vos3_slot_has_capability(uint32_t slot_id, uint32_t required_caps)
{
    if (slot_id >= VOS3_SLOT_MAX_TOTAL) return 0;
    return (g_slot_caps_default[slot_id] & required_caps) == required_caps;
}

vos3_slot_state_t vos3_slot_get_state(uint32_t slot_id)
{
    if (slot_id >= VOS3_SLOT_MAX_TOTAL) return VOS3_SLOT_STATE_UNINITIALIZED;
    return g_slot_state[slot_id];
}

/* State transition rules:
 *   UNINITIALIZED → LOADING
 *   LOADING       → READY
 *   READY         → RUNNING / SUSPENDED / ZOMBIE
 *   RUNNING       → READY / SUSPENDED / ZOMBIE
 *   SUSPENDED     → READY / ZOMBIE
 *   ZOMBIE        → RECLAIMED (only valid forward transition)
 *   RECLAIMED     → UNINITIALIZED
 */
int vos3_slot_transition_state(uint32_t slot_id, vos3_slot_state_t new_state)
{
    if (slot_id >= VOS3_SLOT_MAX_TOTAL) return -1;
    vos3_slot_state_t cur = g_slot_state[slot_id];

    /* ZOMBIE is terminal; only forward path is RECLAIMED. */
    if (cur == VOS3_SLOT_STATE_ZOMBIE && new_state != VOS3_SLOT_STATE_RECLAIMED) {
        return -1;
    }

    /* Forward-only progression for boot-time states. */
    if (new_state == VOS3_SLOT_STATE_LOADING && cur != VOS3_SLOT_STATE_UNINITIALIZED && cur != VOS3_SLOT_STATE_RECLAIMED) {
        return -1;
    }

    g_slot_state[slot_id] = new_state;
    return 0;
}

/* ---- Memory scrub ---- */

void vos3_slot_register_zone(uint32_t slot_id, uintptr_t base, uint64_t len)
{
    if (slot_id >= VOS3_SLOT_MAX_TOTAL) return;
    g_slot_zone_base[slot_id] = base;
    g_slot_zone_len [slot_id] = len;
}

uint64_t vos3_pud_scrub(uint32_t slot_id)
{
    if (slot_id >= VOS3_SLOT_MAX_TOTAL) return 0;
    uintptr_t base = g_slot_zone_base[slot_id];
    uint64_t  len  = g_slot_zone_len [slot_id];
    if (base == 0 || len == 0) return 0;

    /* Volatile pointer to defeat compiler dead-store elimination. The
     * scrub MUST land — this is a security primitive, not a hint. */
    volatile uint8_t *p = (volatile uint8_t *)base;
    for (uint64_t i = 0; i < len; i++) {
        p[i] = 0;
    }

    /* Memory barrier: ensure the scrub completes before any subsequent
     * reload of the slot. */
    __asm__ volatile ("mfence" ::: "memory");
    return len;
}

/* ---- MMR audit wrapper ---- */

void mmr_record_security_violation(uint32_t slot_id, uint32_t reason_code)
{
    init_secvio_label();
    /* Pack slot_id + reason_code into the lower 8 bytes of a stack-local
     * derived label so the MMR leaf is uniquely tied to this event.
     * The base label hash is constant; we re-hash it with the parameters
     * to produce a per-event leaf. */
    uint8_t leaf[32];
    vos3_sha256_ctx_t ctx;
    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, g_secvio_label_hash, 32);

    uint8_t buf[8];
    buf[0] = (uint8_t)(slot_id >> 24);
    buf[1] = (uint8_t)(slot_id >> 16);
    buf[2] = (uint8_t)(slot_id >> 8);
    buf[3] = (uint8_t)(slot_id);
    buf[4] = (uint8_t)(reason_code >> 24);
    buf[5] = (uint8_t)(reason_code >> 16);
    buf[6] = (uint8_t)(reason_code >> 8);
    buf[7] = (uint8_t)(reason_code);
    vos3_sha256_update(&ctx, buf, 8);
    vos3_sha256_final(&ctx, leaf);

    mmr_record_event(leaf);
}

/* ---- Current-slot accessor (set by dispatcher / IPC entry) ---- */

void vos3_slot_set_current(uint32_t slot_id)
{
    g_current_slot_id = slot_id;
}

uint32_t vos3_slot_get_current(void)
{
    return g_current_slot_id;
}

/* ---- W^X violation handler — entry point from vmm.c ---- */

void vos3_slot_wx_violation_handler(uintptr_t addr, uint64_t len, int prot)
{
    (void)addr; (void)len; (void)prot;  /* parameters reserved for future logging */

    /* OLYMPUS Tier-A S3: take an explicit acquire-load snapshot so the
     * dispatcher running on another CPU cannot flip g_current_slot_id
     * between this read and the quarantine call below. The variable is
     * already declared volatile, but volatile alone does not establish
     * the cross-CPU ordering we need to keep the violator and the
     * quarantine target in agreement. */
    uint32_t cur = __atomic_load_n(&g_current_slot_id, __ATOMIC_ACQUIRE);

    /* Always record the MMR violation, even if we can't identify the slot. */
    mmr_record_security_violation(
        cur < VOS3_SLOT_MAX_TOTAL ? cur : (uint32_t)-1,
        VOS3_SECVIO_REASON_WX);

    /* If we have a slot context, quarantine + scrub. */
    if (cur < VOS3_SLOT_MAX_TOTAL) {
        vos3_slot_transition_state(cur, VOS3_SLOT_STATE_ZOMBIE);
        vos3_pud_scrub(cur);
    }
}
