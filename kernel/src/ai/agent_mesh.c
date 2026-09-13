/**
 * @file agent_mesh.c
 * @brief VOS3 Agentic Mesh — Multi-Agent Orchestration Implementation
 *
 * @details Trust-scored multi-agent dispatch with capability-based routing.
 *          Tasks are dispatched via ISC and tracked on a Vector VFS blackboard.
 *          COORDINATOR (slot 0) arbitrates routing and consensus.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.1: Agentic Mesh & vScreen Perception
 */

#include "../../include/vos/agent_mesh.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/vector_vfs.h"
#include "../../include/vos/console.h"
#include "../../include/vos/virtio_vbus.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * STATIC STATE (~6KB BSS)
 * ============================================================================ */

static mesh_state_t g_mesh;
static mesh_stats_t g_mesh_stats;

/** @brief SMP spinlock protecting all g_mesh and g_mesh_stats mutations.
 *  Phase 10 fix: the original code had no synchronization on the global
 *  mesh state, making concurrent mesh_dispatch() from multiple cores
 *  a data race (UB). This lock serializes all mesh operations. */
static vos3_spinlock_t g_mesh_lock;

/* External: model slot array */
extern vos3_ai_model_slot_t g_model_slots[];

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

static void mesh_memzero(void *dst, size_t len)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < len; i++) {
        p[i] = 0;
    }
}

static void mesh_memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; i++) {
        d[i] = s[i];
    }
}

/**
 * @brief Map task type to required capability bit.
 */
static uint64_t task_type_to_cap(uint8_t type)
{
    switch (type) {
    case MESH_TASK_INFERENCE:    return VOS3_CAP_INFERENCE;
    case MESH_TASK_TOOL_CALL:    return VOS3_CAP_TOOL_USE;
    case MESH_TASK_CODE_GEN:     return VOS3_CAP_CODE_GEN;
    case MESH_TASK_VISION:       return VOS3_CAP_VISION;
    case MESH_TASK_MEMORY_QUERY: return VOS3_CAP_MEMORY;
    case MESH_TASK_PEER:         return VOS3_CAP_WORKER;
    default:                     return 0;
    }
}

/**
 * @brief Find the best target slot for a task (capability + trust + load).
 *
 * @param type          Task type
 * @param exclude_slot  Slot to exclude (source)
 * @return Slot ID or 0xFF if none suitable
 */
static uint8_t mesh_route_task(uint8_t type, uint8_t exclude_slot)
{
    uint64_t required_cap = task_type_to_cap(type);
    uint8_t best_slot = 0xFF;
    uint32_t best_trust = 0;

    for (uint8_t s = 0; s < MESH_MAX_SLOTS; s++) {
        if (s == exclude_slot) continue;
        if (s >= VOS3_MODEL_SLOT_MAX) continue;

        /* Check capability */
        uint64_t caps = g_model_slots[s].capabilities;
        if (required_cap != 0 && !(caps & required_cap)) continue;

        /* Check minimum trust */
        if (g_mesh.trust[s].score < MESH_TRUST_MIN_DISPATCH) continue;

        /* Check slot is loaded and active */
        if (g_model_slots[s].status != VOS3_SLOT_ACTIVE) continue;

        /* Pick highest trust (ties broken by lower slot ID) */
        if (g_mesh.trust[s].score > best_trust) {
            best_trust = g_mesh.trust[s].score;
            best_slot = s;
        }
    }

    return best_slot;
}

/**
 * @brief Find a free pending task slot.
 * @return Index or 0xFFFF if full
 */
static uint32_t mesh_find_free_pending(void)
{
    for (uint32_t i = 0; i < MESH_MAX_PENDING_TASKS; i++) {
        if (g_mesh.pending[i].status == MESH_STATUS_FREE) {
            return i;
        }
    }
    return 0xFFFF;
}

/**
 * @brief Find a pending task by ID.
 * @return Index or 0xFFFF if not found
 */
static uint32_t mesh_find_task(uint32_t task_id)
{
    for (uint32_t i = 0; i < MESH_MAX_PENDING_TASKS; i++) {
        if (g_mesh.pending[i].task_id == task_id &&
            g_mesh.pending[i].status != MESH_STATUS_FREE) {
            return i;
        }
    }
    return 0xFFFF;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int mesh_init(void)
{
    vos3_spinlock_init(&g_mesh_lock);
    mesh_memzero(&g_mesh, sizeof(g_mesh));
    mesh_memzero(&g_mesh_stats, sizeof(g_mesh_stats));

    /* Set initial trust scores */
    for (uint8_t i = 0; i < MESH_MAX_SLOTS; i++) {
        g_mesh.trust[i].score = MESH_TRUST_INITIAL;
    }

    g_mesh.next_task_id = 1;
    g_mesh.initialized = 1;

    VOS3_INFO("[MESH] Agentic Mesh initialized (%u slots, trust=%u initial, SMP-safe)",
              (unsigned)MESH_MAX_SLOTS, (unsigned)MESH_TRUST_INITIAL);
    return 0;
}

int mesh_dispatch(const mesh_task_t *task)
{
    if (!g_mesh.initialized) return -22; /* EINVAL */
    if (task == NULL) return -22;
    if (task->source_slot >= MESH_MAX_SLOTS) return -22;

    /* Phase 6.1: Quarantine gate — deny all dispatch during quarantine */
    extern int guardian_is_quarantined(void);
    if (guardian_is_quarantined()) return -1;

    uint64_t tsc_start = vos3_rdtsc();

    vos3_spinlock_lock(&g_mesh_lock);

    /* Validate source has SUPERVISOR or is COORDINATOR */
    uint64_t src_caps = 0;
    if (task->source_slot < VOS3_MODEL_SLOT_MAX) {
        src_caps = g_model_slots[task->source_slot].capabilities;
    }
    uint8_t is_coordinator = (g_model_slots[task->source_slot].agent_type ==
                              VOS3_AGENT_COORDINATOR);
    if (!is_coordinator && !(src_caps & VOS3_CAP_SUPERVISOR)) {
        /* Workers can dispatch PEER tasks only */
        if (task->type != MESH_TASK_PEER) {
            g_mesh_stats.tasks_rejected++;
            vos3_spinlock_unlock(&g_mesh_lock);
            return -1; /* EPERM */
        }
    }

    /* Resolve target */
    uint8_t target = task->target_slot;
    if (target == 0xFF) {
        target = mesh_route_task(task->type, task->source_slot);
        if (target == 0xFF) {
            g_mesh_stats.tasks_rejected++;
            vos3_spinlock_unlock(&g_mesh_lock);
            return -28; /* ENOSPC: no suitable target */
        }
    }

    /* Validate target capabilities */
    if (target < VOS3_MODEL_SLOT_MAX) {
        uint64_t required = task_type_to_cap(task->type);
        uint64_t tgt_caps = g_model_slots[target].capabilities;
        if (required != 0 && !(tgt_caps & required)) {
            g_mesh_stats.tasks_rejected++;
            g_mesh.trust[target].rejections++;
            vos3_spinlock_unlock(&g_mesh_lock);
            return -1; /* EPERM: target lacks capability */
        }
    }

    /* Check target trust */
    if (g_mesh.trust[target].score < MESH_TRUST_MIN_DISPATCH) {
        g_mesh_stats.tasks_rejected++;
        vos3_spinlock_unlock(&g_mesh_lock);
        return -13; /* EACCES: trust too low */
    }

    /* Find free pending slot */
    uint32_t idx = mesh_find_free_pending();
    if (idx == 0xFFFF) {
        vos3_spinlock_unlock(&g_mesh_lock);
        return -28; /* ENOSPC */
    }

    /* Store task */
    mesh_task_t *stored = &g_mesh.pending[idx];
    mesh_memcpy(stored, task, sizeof(mesh_task_t));
    stored->task_id = g_mesh.next_task_id++;
    stored->target_slot = target;
    stored->status = MESH_STATUS_DISPATCHED;

    g_mesh.active_tasks++;
    g_mesh_stats.tasks_dispatched++;

    vos3_spinlock_unlock(&g_mesh_lock);

    /* Send via ISC (best-effort) — outside lock to avoid deadlock with VBus */
    vos3_ai_isc_send(task->source_slot, target, stored->task_id,
                     stored, sizeof(mesh_task_t));

    /* Store embedding on blackboard (slot 0) for coordination */
    vecvfs_insert(0, task->payload_embedding, (const void *)&stored->task_id,
                  sizeof(stored->task_id));

    /* Emit event */
    vos3_vbus_event_ring_push(VOS3_EVENT_MESH_DISPATCH, task->source_slot,
                       stored->task_id);

    uint64_t tsc_end = vos3_rdtsc();

    vos3_spinlock_lock(&g_mesh_lock);
    g_mesh_stats.total_dispatch_cycles += (tsc_end - tsc_start);
    if (task->type == MESH_TASK_PEER) {
        g_mesh_stats.peer_routes++;
    }
    vos3_spinlock_unlock(&g_mesh_lock);

    return 0;
}

int mesh_result(uint32_t task_id, const mesh_result_t *result)
{
    if (!g_mesh.initialized) return -22;
    if (result == NULL) return -22;

    vos3_spinlock_lock(&g_mesh_lock);

    uint32_t idx = mesh_find_task(task_id);
    if (idx == 0xFFFF) {
        vos3_spinlock_unlock(&g_mesh_lock);
        return -22; /* EINVAL: not found */
    }

    mesh_task_t *task = &g_mesh.pending[idx];
    uint8_t target_slot = task->target_slot;

    if (result->result_code == 0) {
        task->status = MESH_STATUS_COMPLETED;
        task->result_code = 0;
        g_mesh_stats.tasks_completed++;

        /* Reward target trust */
        if (target_slot < MESH_MAX_SLOTS) {
            mesh_trust_reward(target_slot, MESH_TRUST_REWARD);
            g_mesh.trust[target_slot].completions++;
        }
    } else {
        task->status = MESH_STATUS_FAILED;
        task->result_code = result->result_code;
        g_mesh_stats.tasks_failed++;

        /* Penalize target trust */
        if (target_slot < MESH_MAX_SLOTS) {
            mesh_trust_penalize(target_slot, MESH_TRUST_PENALTY);
            g_mesh.trust[target_slot].violations++;
        }
    }

    /* Free task slot */
    if (g_mesh.active_tasks > 0) g_mesh.active_tasks--;
    task->status = MESH_STATUS_FREE;

    vos3_spinlock_unlock(&g_mesh_lock);

    /* Store result embedding on blackboard — outside lock */
    vecvfs_insert(0, result->result_embedding,
                  result->result_payload,
                  result->payload_len > VECVFS_PAYLOAD_SIZE ?
                      VECVFS_PAYLOAD_SIZE : result->payload_len);

    /* Emit event */
    vos3_vbus_event_ring_push(VOS3_EVENT_MESH_RESULT, target_slot, task_id);

    return 0;
}

int mesh_trust_score(uint8_t slot_id, uint32_t *out_score)
{
    if (slot_id >= MESH_MAX_SLOTS) return -22;
    if (out_score == NULL) return -22;
    vos3_spinlock_lock(&g_mesh_lock);
    *out_score = g_mesh.trust[slot_id].score;
    vos3_spinlock_unlock(&g_mesh_lock);
    return 0;
}

int mesh_trust_penalize(uint8_t slot_id, uint32_t amount)
{
    if (slot_id >= MESH_MAX_SLOTS) return -22;
    /* Note: called from mesh_result() which already holds g_mesh_lock,
     * so we must NOT re-acquire here. The lock is held by the caller. */
    if (g_mesh.trust[slot_id].score >= amount) {
        g_mesh.trust[slot_id].score -= amount;
    } else {
        g_mesh.trust[slot_id].score = 0;
    }
    return 0;
}

int mesh_trust_reward(uint8_t slot_id, uint32_t amount)
{
    if (slot_id >= MESH_MAX_SLOTS) return -22;
    /* Note: called from mesh_result() which already holds g_mesh_lock. */
    g_mesh.trust[slot_id].score += amount;
    if (g_mesh.trust[slot_id].score > MESH_TRUST_MAX) {
        g_mesh.trust[slot_id].score = MESH_TRUST_MAX;
    }
    return 0;
}

void mesh_trust_tick(uint64_t current_tick)
{
    vos3_spinlock_lock(&g_mesh_lock);
    for (uint8_t i = 0; i < MESH_MAX_SLOTS; i++) {
        if (g_mesh.trust[i].score >= MESH_TRUST_DECAY_RATE) {
            g_mesh.trust[i].score -= MESH_TRUST_DECAY_RATE;
        }
        g_mesh.trust[i].last_decay_tick = current_tick;
    }
    vos3_spinlock_unlock(&g_mesh_lock);
}

void mesh_get_stats(mesh_stats_t *out)
{
    if (out != NULL) {
        vos3_spinlock_lock(&g_mesh_lock);
        mesh_memcpy(out, &g_mesh_stats, sizeof(g_mesh_stats));
        vos3_spinlock_unlock(&g_mesh_lock);
    }
}
