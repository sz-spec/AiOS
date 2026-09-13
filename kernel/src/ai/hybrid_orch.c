/**
 * @file hybrid_orch.c
 * @brief Phase 2.3: Hybrid Orchestration Engine — NPU/GPU/CPU Device Routing
 *
 * @details Routes draft model to NPU and target model to GPU for maximum
 *          hardware parallelism. Streams unverified "ghost tokens" to the
 *          frontend immediately via STREAM_SPEC VBus frames. On verification,
 *          sends corrected tokens as replacements.
 *
 *          Device routing priority:
 *            Draft  → NPU (type==3) preferred → CPU (type==1) fallback
 *            Target → GPU (type==2) preferred → CPU (type==1) fallback
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2.3: Hybrid Orchestration & Speculative VBus
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/ai_orch.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/accel.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"
#include <stdint.h>
#include <stddef.h>

#define ORCH_TAG "[ORCH] "

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/** @brief Global model slot array (defined in ai_slots.c) */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/** @brief Ghost/verified token VBus senders (defined in vbus_ai_cmds.c) */
extern void vos3_vbus_send_ghost_tokens(uint8_t slot_id, const uint32_t *tokens,
                                         uint32_t count, uint32_t batch_id);
extern void vos3_vbus_send_verified_tokens(uint8_t slot_id, const uint32_t *tokens,
                                            uint32_t count, uint32_t batch_id);

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

static vos3_orch_session_t g_sessions[VOS3_ORCH_MAX_SESSIONS];
static vos3_orch_stats_t   g_orch_stats;

/* ============================================================================
 * INTERNAL: DEVICE ROUTING
 * ============================================================================ */

/**
 * @brief Route devices for an orchestration session
 *
 * Scans the accel registry for NPU (draft) and GPU (target) devices.
 * Falls back to CPU if the preferred accelerator is not found.
 */
static void route_devices(vos3_orch_session_t *session)
{
    uint32_t num = vos3_accel_num_devices();
    uint32_t npu_id  = 0;  /* 0 = CPU fallback */
    uint32_t gpu_id  = 0;
    uint32_t cpu_id  = 0;

    vos3_accel_type_t npu_type = VOS3_ACCEL_CPU;
    vos3_accel_type_t gpu_type = VOS3_ACCEL_CPU;

    for (uint32_t i = 0; i < num; i++) {
        const vos3_accel_device_t *dev = vos3_accel_get_device(i);
        if (dev == NULL || !dev->online) {
            continue;
        }
        if (dev->type == VOS3_ACCEL_NPU) {
            npu_id   = dev->id;
            npu_type = VOS3_ACCEL_NPU;
        } else if (dev->type == VOS3_ACCEL_GPU) {
            gpu_id   = dev->id;
            gpu_type = VOS3_ACCEL_GPU;
        } else if (dev->type == VOS3_ACCEL_CPU) {
            cpu_id = dev->id;
        }
    }

    /* Draft → NPU preferred, CPU fallback */
    if (npu_type == VOS3_ACCEL_NPU) {
        session->draft_device    = VOS3_ACCEL_NPU;
        session->draft_device_id = npu_id;
    } else {
        session->draft_device    = VOS3_ACCEL_CPU;
        session->draft_device_id = cpu_id;
    }

    /* Target → GPU preferred, CPU fallback */
    if (gpu_type == VOS3_ACCEL_GPU) {
        session->target_device    = VOS3_ACCEL_GPU;
        session->target_device_id = gpu_id;
    } else {
        session->target_device    = VOS3_ACCEL_CPU;
        session->target_device_id = cpu_id;
    }

    VOS3_INFO(ORCH_TAG "route: draft→%s (dev %u), target→%s (dev %u)",
              session->draft_device == VOS3_ACCEL_NPU ? "NPU" :
              session->draft_device == VOS3_ACCEL_GPU ? "GPU" : "CPU",
              session->draft_device_id,
              session->target_device == VOS3_ACCEL_GPU ? "GPU" :
              session->target_device == VOS3_ACCEL_NPU ? "NPU" : "CPU",
              session->target_device_id);
}

/* ============================================================================
 * PUBLIC API: INIT
 * ============================================================================ */

int vos3_orch_init(void)
{
    /* Zero all sessions */
    for (uint32_t i = 0; i < VOS3_ORCH_MAX_SESSIONS; i++) {
        vos3_orch_session_t *s = &g_sessions[i];
        s->draft_slot          = 0;
        s->target_slot         = 0;
        s->draft_device        = VOS3_ACCEL_NONE;
        s->target_device       = VOS3_ACCEL_NONE;
        s->draft_device_id     = 0;
        s->target_device_id    = 0;
        s->active              = 0;
        s->ghost_streaming     = 0;
        s->ghost_tokens_sent     = 0;
        s->ghost_tokens_replaced = 0;
        s->batches_dispatched    = 0;
        s->last_dispatch_tsc     = 0;
    }

    /* Zero global stats */
    g_orch_stats.sessions_started    = 0;
    g_orch_stats.sessions_completed  = 0;
    g_orch_stats.npu_dispatches      = 0;
    g_orch_stats.gpu_dispatches      = 0;
    g_orch_stats.cpu_fallbacks       = 0;
    g_orch_stats.ghost_total_sent    = 0;
    g_orch_stats.ghost_total_replaced = 0;
    g_orch_stats.total_cycles        = 0;

    uint32_t ndev = vos3_accel_num_devices();
    VOS3_INFO(ORCH_TAG "init: %u accelerator(s) available", ndev);

    return 0;
}

/* ============================================================================
 * PUBLIC API: START SESSION
 * ============================================================================ */

int vos3_orch_start(uint8_t draft_slot, uint8_t target_slot)
{
    /* Validate slots */
    if (draft_slot >= VOS3_MODEL_SLOT_MAX || target_slot >= VOS3_MODEL_SLOT_MAX) {
        VOS3_WARN(ORCH_TAG "start: invalid slot (%u, %u)", draft_slot, target_slot);
        return -22; /* EINVAL */
    }
    if (draft_slot == target_slot) {
        VOS3_WARN(ORCH_TAG "start: draft == target (%u)", draft_slot);
        return -22; /* EINVAL */
    }

    /* Find free session */
    int sid = -1;
    for (uint32_t i = 0; i < VOS3_ORCH_MAX_SESSIONS; i++) {
        if (!g_sessions[i].active) {
            sid = (int)i;
            break;
        }
    }
    if (sid < 0) {
        VOS3_WARN(ORCH_TAG "start: no free session (max=%u)", VOS3_ORCH_MAX_SESSIONS);
        return -12; /* ENOMEM */
    }

    vos3_orch_session_t *session = &g_sessions[sid];
    session->draft_slot   = draft_slot;
    session->target_slot  = target_slot;
    session->active       = 1;
    session->ghost_streaming     = 0;
    session->ghost_tokens_sent     = 0;
    session->ghost_tokens_replaced = 0;
    session->batches_dispatched    = 0;
    session->last_dispatch_tsc     = 0;

    /* Route devices */
    route_devices(session);

    /* Configure speculative engine (default K=4) */
    int rc = vos3_spec_configure(draft_slot, target_slot, VOS3_SPEC_DEFAULT_K);
    if (rc != 0) {
        VOS3_WARN(ORCH_TAG "start: spec_configure failed (%d)", rc);
        session->active = 0;
        return rc;
    }

    g_orch_stats.sessions_started++;

    VOS3_INFO(ORCH_TAG "start: session %d — draft=%u target=%u", sid, draft_slot, target_slot);
    return sid;
}

/* ============================================================================
 * PUBLIC API: DISPATCH
 * ============================================================================ */

int vos3_orch_dispatch(uint8_t session_id, uint32_t input_token)
{
    if (session_id >= VOS3_ORCH_MAX_SESSIONS) {
        return -22; /* EINVAL */
    }

    vos3_orch_session_t *session = &g_sessions[session_id];
    if (!session->active) {
        return -22; /* EINVAL */
    }

    uint64_t t0 = vos3_rdtsc();

    /* Step 1: Draft generates K speculative tokens */
    int rc = vos3_spec_generate(session->draft_slot, input_token);
    if (rc != 0) {
        VOS3_WARN(ORCH_TAG "dispatch: generate failed (%d)", rc);
        return rc;
    }

    /* Update device dispatch stats */
    if (session->draft_device == VOS3_ACCEL_NPU) {
        g_orch_stats.npu_dispatches++;
    } else if (session->draft_device == VOS3_ACCEL_GPU) {
        g_orch_stats.gpu_dispatches++;
    } else {
        g_orch_stats.cpu_fallbacks++;
    }

    /* Step 2: Ghost streaming — track ghost token stats from draft slot */
    vos3_ai_model_slot_t *ds = &g_model_slots[session->draft_slot];
    uint32_t k = ds->spec_k;
    uint32_t batch_id = ds->spec_batch_id;

    if (session->ghost_streaming && k > 0U) {
        /*
         * Ghost tokens were drafted by spec_generate. The actual draft token
         * values are inside the ISC mailbox (sent from draft → target).
         * Stream ghost notification with the batch_id and count so the
         * frontend knows unverified tokens are in-flight.
         */
        uint32_t ghost_placeholder[VOS3_SPEC_MAX_K];
        for (uint32_t i = 0; i < k && i < VOS3_SPEC_MAX_K; i++) {
            ghost_placeholder[i] = 0; /* Actual tokens in ISC pipeline */
        }
        vos3_vbus_send_ghost_tokens(session->draft_slot, ghost_placeholder,
                                     k, batch_id);
        session->ghost_tokens_sent += k;
        g_orch_stats.ghost_total_sent += k;
    }

    /* Step 3: Build synthetic batch header for verification */
    vos3_spec_batch_t batch;
    batch.count    = k;
    batch.seq_pos  = ds->ctx_window.total_generated;
    batch.batch_id = batch_id;
    for (uint32_t i = 0; i < VOS3_SPEC_MAX_K; i++) {
        batch.draft_tokens[i] = 0;
        batch.draft_logits[i] = 0;
    }

    vos3_spec_result_t result;
    result.accepted     = 0;
    result.total_tokens = 0;
    result.batch_id     = 0;

    rc = vos3_spec_verify(session->target_slot, &batch, &result);
    if (rc != 0) {
        VOS3_WARN(ORCH_TAG "dispatch: verify failed (%d)", rc);
        return rc;
    }

    /* Update target device dispatch stats */
    if (session->target_device == VOS3_ACCEL_GPU) {
        g_orch_stats.gpu_dispatches++;
    } else if (session->target_device == VOS3_ACCEL_NPU) {
        g_orch_stats.npu_dispatches++;
    } else {
        g_orch_stats.cpu_fallbacks++;
    }

    /* Step 4: If ghost streaming, send verified replacement tokens */
    if (session->ghost_streaming && result.total_tokens > 0) {
        vos3_vbus_send_verified_tokens(session->target_slot, result.verified_tokens,
                                        result.total_tokens, result.batch_id);
        uint32_t replaced = k > result.accepted ? k - result.accepted : 0;
        session->ghost_tokens_replaced += replaced;
        g_orch_stats.ghost_total_replaced += replaced;
    }

    session->batches_dispatched++;
    session->last_dispatch_tsc = vos3_rdtsc();

    uint64_t elapsed = session->last_dispatch_tsc - t0;
    g_orch_stats.total_cycles += elapsed;

    return 0;
}

/* ============================================================================
 * PUBLIC API: STOP SESSION
 * ============================================================================ */

int vos3_orch_stop(uint8_t session_id)
{
    if (session_id >= VOS3_ORCH_MAX_SESSIONS) {
        return -22; /* EINVAL */
    }

    vos3_orch_session_t *session = &g_sessions[session_id];
    if (!session->active) {
        return -22; /* EINVAL */
    }

    /* Reset speculative state */
    vos3_spec_reset(session->draft_slot);

    session->active = 0;
    g_orch_stats.sessions_completed++;

    VOS3_INFO(ORCH_TAG "stop: session %u — %u batches, %u ghost sent, %u replaced",
              session_id, session->batches_dispatched,
              session->ghost_tokens_sent, session->ghost_tokens_replaced);

    return 0;
}

/* ============================================================================
 * PUBLIC API: GET STATS
 * ============================================================================ */

void vos3_orch_get_stats(vos3_orch_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    *out = g_orch_stats;
}

/* ============================================================================
 * PUBLIC API: GHOST STREAMING TOGGLE
 * ============================================================================ */

int vos3_orch_set_ghost(uint8_t session_id, uint8_t enable)
{
    if (session_id >= VOS3_ORCH_MAX_SESSIONS) {
        return -22; /* EINVAL */
    }

    vos3_orch_session_t *session = &g_sessions[session_id];
    if (!session->active) {
        return -22; /* EINVAL */
    }

    session->ghost_streaming = enable ? 1U : 0U;

    VOS3_INFO(ORCH_TAG "ghost: session %u — streaming %s",
              session_id, enable ? "ENABLED" : "DISABLED");

    return 0;
}
