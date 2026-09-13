/**
 * @file ai_isc.c
 * @brief VOS3 AI Guard — Inter-Slot Communication (ISC) and Event Pub/Sub
 *
 * Task 4.1: Split from ai_guard.c. Contains:
 *   - ISC mailbox send/recv
 *   - Kernel-internal ISC delivery (RBAC bypass)
 *   - Event subscription/unsubscription
 *   - Event delivery (full routing pipeline)
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "ai_guard_internal.h"

/* ============================================================================
 * ISC / Event Subscriptions — defined here, declared extern in ai_guard_internal.h
 * ============================================================================ */

uint8_t g_event_subscriptions[256];

/* ============================================================================
 * PHASE 4.2.7: AGENTIC FABRIC — Inter-Slot Communication (ISC)
 * ============================================================================ */

int vos3_ai_isc_send(uint8_t from_slot, uint8_t to_slot,
                      uint32_t context_id, const void *msg, uint16_t len)
{
    if (from_slot >= VOS3_MODEL_SLOT_MAX || to_slot >= VOS3_MODEL_SLOT_MAX) return -22;
    if (from_slot == to_slot) return -22;
    if (msg == NULL || len == 0 || len > 2048) return -22;

    vos3_ai_model_slot_t *src = &g_model_slots[from_slot];
    vos3_ai_model_slot_t *dst = &g_model_slots[to_slot];

    if (src->status < VOS3_SLOT_ACTIVE || dst->status < VOS3_SLOT_ACTIVE) return -22;

    /* RBAC check */
    if (!(src->io_perm_mask & (1ULL << to_slot))) return -1;  /* EPERM */

    vos3_spinlock_lock(&dst->lock);

    /* Phase 4.2.8: Priority Inheritance — elevate receiver if sender has higher priority */
    if (src->priority < dst->priority) {
        if (!dst->priority_inherited) {
            dst->inherited_priority = dst->priority;
        }
        dst->priority = src->priority;
        dst->priority_inherited = 1;
    }

    /* Mailbox format: [u32:context_id][u16:payload_len][payload...] */
    uint16_t msg_overhead = 6;
    uint16_t used = (dst->mb_tail >= dst->mb_head)
                  ? (uint16_t)(dst->mb_tail - dst->mb_head)
                  : (uint16_t)(4096U - dst->mb_head + dst->mb_tail);
    uint16_t free_space = (uint16_t)(4096U - used);
    if (free_space < (uint16_t)(len + msg_overhead)) {
        vos3_spinlock_unlock(&dst->lock);
        return -28;  /* ENOSPC */
    }

    /* Write 4-byte LE context_id, wrapping */
    for (int i = 0; i < 4; i++) {
        dst->mailbox[dst->mb_tail] = (uint8_t)((context_id >> (i * 8)) & 0xFF);
        dst->mb_tail++;
        if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;
    }

    /* Write 2-byte LE length prefix, wrapping */
    dst->mailbox[dst->mb_tail] = (uint8_t)(len & 0xFF);
    dst->mb_tail++;
    if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;
    dst->mailbox[dst->mb_tail] = (uint8_t)((len >> 8) & 0xFF);
    dst->mb_tail++;
    if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;

    /* Write message data, wrapping */
    const uint8_t *data = (const uint8_t *)msg;
    for (uint16_t i = 0; i < len; i++) {
        dst->mailbox[dst->mb_tail] = data[i];
        dst->mb_tail++;
        if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;
    }

    /* Phase 4.2.8: Swarm Barrier — track ISC arrival and check completion */
    int barrier_complete = 0;
    if (dst->sync_barrier_mask != 0) {
        dst->sync_barrier_received |= (uint8_t)(1U << from_slot);
        if ((dst->sync_barrier_received & dst->sync_barrier_mask) == dst->sync_barrier_mask) {
            dst->sync_barrier_mask     = 0;
            dst->sync_barrier_received = 0;
            barrier_complete = 1;
        }
    }

    vos3_spinlock_unlock(&dst->lock);

    /* Emit ISC event */
    vos3_vbus_signal_event_sync(to_slot, VOS3_EVENT_ISC_MESSAGE,
                                ((context_id & 0xFFFF) << 16) |
                                (uint32_t)((from_slot << 8) | to_slot));

    /* Wake barrier-blocked slot if all expected ISCs arrived */
    if (barrier_complete) {
        vos3_ai_model_slot_wake(to_slot);
        VOS3_INFO("[AI-GUARD] Barrier complete: slot %u woken by slot %u", to_slot, from_slot);
    }

    /* Phase 4.2.9: Yield-EX ISC wake */
    if (!barrier_complete && dst->status == VOS3_SLOT_DORMANT &&
        (dst->yield_event_mask & (1ULL << from_slot))) {
        int yield_wake = 0;
        vos3_spinlock_lock(&dst->lock);
        if (dst->status == VOS3_SLOT_DORMANT &&
            (dst->yield_event_mask & (1ULL << from_slot))) {
            dst->yield_event_mask   = 0;
            dst->yield_timeout_tick = 0;
            yield_wake = 1;
        }
        vos3_spinlock_unlock(&dst->lock);
        if (yield_wake) {
            vos3_ai_model_slot_wake(to_slot);
            VOS3_INFO("[AI-GUARD] Yield-EX: slot %u woken by ISC from slot %u",
                      to_slot, from_slot);
        }
    }

    return 0;
}

int vos3_ai_isc_recv(uint8_t slot_id, void *buf, uint16_t buf_len,
                      uint16_t *out_len, uint32_t *context_id_out)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || buf == NULL || out_len == NULL) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status < VOS3_SLOT_ACTIVE) return -22;

    vos3_spinlock_lock(&slot->lock);

    if (slot->mb_head == slot->mb_tail) {
        vos3_spinlock_unlock(&slot->lock);
        return -11;  /* EAGAIN */
    }

    uint16_t saved_head = slot->mb_head;

    /* Read 4-byte LE context_id */
    uint32_t ctx_id = 0;
    for (int i = 0; i < 4; i++) {
        ctx_id |= (uint32_t)slot->mailbox[slot->mb_head] << (i * 8);
        slot->mb_head++;
        if (slot->mb_head >= 4096) slot->mb_head -= 4096;
    }

    /* Read 2-byte LE length */
    uint16_t msg_len = (uint16_t)slot->mailbox[slot->mb_head];
    slot->mb_head++;
    if (slot->mb_head >= 4096) slot->mb_head -= 4096;
    msg_len |= (uint16_t)((uint16_t)slot->mailbox[slot->mb_head] << 8);
    slot->mb_head++;
    if (slot->mb_head >= 4096) slot->mb_head -= 4096;

    if (msg_len > buf_len) {
        slot->mb_head = saved_head;
        vos3_spinlock_unlock(&slot->lock);
        return -75;  /* EOVERFLOW */
    }

    /* Copy message data */
    uint8_t *out = (uint8_t *)buf;
    for (uint16_t i = 0; i < msg_len; i++) {
        out[i] = slot->mailbox[slot->mb_head];
        slot->mb_head++;
        if (slot->mb_head >= 4096) slot->mb_head -= 4096;
    }

    /* Phase 4.2.8: Priority Inheritance — restore original priority on recv */
    if (slot->priority_inherited) {
        slot->priority = slot->inherited_priority;
        slot->priority_inherited = 0;
    }

    /* Phase 4.2.12: Watchdog-ISC Integration */
    slot->drift_timestamp = vos3_timer_get_ticks();

    vos3_spinlock_unlock(&slot->lock);
    *out_len = msg_len;
    if (context_id_out != NULL) {
        *context_id_out = ctx_id;
    }
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7+: EVENT PUB/SUB (External Triggers)
 * ============================================================================ */

int vos3_ai_event_subscribe(uint8_t event_type, uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    g_event_subscriptions[event_type] = slot_id;
    VOS3_INFO("[AI-GUARD] Event %u subscribed to slot %u", event_type, slot_id);
    return 0;
}

int vos3_ai_event_unsubscribe(uint8_t event_type)
{
    g_event_subscriptions[event_type] = 0xFF;
    VOS3_INFO("[AI-GUARD] Event %u unsubscribed", event_type);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7+: KERNEL-INTERNAL ISC (RBAC Bypass)
 * ============================================================================ */

int vos3_ai_isc_deliver_kernel(uint8_t to_slot, uint32_t context_id,
                                const void *msg, uint16_t len)
{
    if (to_slot >= VOS3_MODEL_SLOT_MAX) return -22;
    if (msg == NULL || len == 0 || len > 2048) return -22;

    vos3_ai_model_slot_t *dst = &g_model_slots[to_slot];
    if (dst->status < VOS3_SLOT_ACTIVE && dst->status != VOS3_SLOT_DORMANT) return -22;

    /* No RBAC check — kernel is always authorized */

    vos3_spinlock_lock(&dst->lock);

    uint16_t msg_overhead = 6;
    uint16_t used = (dst->mb_tail >= dst->mb_head)
                  ? (uint16_t)(dst->mb_tail - dst->mb_head)
                  : (uint16_t)(4096U - dst->mb_head + dst->mb_tail);
    uint16_t free_space = (uint16_t)(4096U - used);
    if (free_space < (uint16_t)(len + msg_overhead)) {
        vos3_spinlock_unlock(&dst->lock);
        return -28;  /* ENOSPC */
    }

    /* Write 4-byte LE context_id, wrapping */
    for (int i = 0; i < 4; i++) {
        dst->mailbox[dst->mb_tail] = (uint8_t)((context_id >> (i * 8)) & 0xFF);
        dst->mb_tail++;
        if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;
    }

    /* Write 2-byte LE length prefix, wrapping */
    dst->mailbox[dst->mb_tail] = (uint8_t)(len & 0xFF);
    dst->mb_tail++;
    if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;
    dst->mailbox[dst->mb_tail] = (uint8_t)((len >> 8) & 0xFF);
    dst->mb_tail++;
    if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;

    /* Write message data, wrapping */
    const uint8_t *data = (const uint8_t *)msg;
    for (uint16_t i = 0; i < len; i++) {
        dst->mailbox[dst->mb_tail] = data[i];
        dst->mb_tail++;
        if (dst->mb_tail >= 4096) dst->mb_tail -= 4096;
    }

    vos3_spinlock_unlock(&dst->lock);

    /* Signal ISC event with from_slot=0xFF (kernel pseudo-slot) */
    vos3_vbus_signal_event_sync(to_slot, VOS3_EVENT_ISC_MESSAGE,
                                ((context_id & 0xFFFF) << 16) |
                                (uint32_t)((0xFF << 8) | to_slot));
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7+: EVENT DELIVERY (Full Routing Pipeline)
 * ============================================================================ */

int vos3_ai_event_deliver(uint8_t event_type, const void *payload, uint16_t len)
{
    uint8_t target = g_event_subscriptions[event_type];
    if (target == 0xFF) return -2;  /* ENOENT — no subscriber */

    if (target >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[target];
    if (slot->status < VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_DORMANT) return -22;

    /* Deliver to ISC mailbox via kernel-internal path */
    int rc = vos3_ai_isc_deliver_kernel(target, (uint32_t)event_type, payload, len);
    if (rc != 0) return rc;

    /* Wake dormant slot */
    vos3_spinlock_lock(&slot->lock);
    int is_dormant = (slot->status == VOS3_SLOT_DORMANT);
    vos3_spinlock_unlock(&slot->lock);
    if (is_dormant) {
        vos3_ai_model_slot_wake(target);
    }

    VOS3_INFO("[AI-GUARD] Event %u delivered to slot %u (len=%u)",
              event_type, target, len);
    return 0;
}
