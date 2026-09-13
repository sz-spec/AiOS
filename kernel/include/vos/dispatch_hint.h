/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Dispatch Hint — Two-Plane Scheduler scaffold
 * ==================================================
 *
 *   [QUANTUM-LEAP-SCAFFOLD]  Operation HOME-DOMINANCE v21.0  2026-05-02
 *
 * This header defines the *taxonomy* the future Two-Plane Scheduler
 * uses to classify tasks. It is intentionally implementation-light:
 *
 *   - Plane 1 (REAL-TIME) — low-latency NPU command streams + UI tasks.
 *     Goal: 0 ms perceptible UI lag on home PCs even when background
 *     inference is saturating an NPU.
 *
 *   - Plane 2 (THROUGHPUT) — background agent house-keeping, batch
 *     training, telemetry. Yields freely to Plane 1.
 *
 * No code path consumes these hints yet. The current scheduler in
 * kernel/src/sched/scheduler.c is unchanged. The framework lands here
 * so subsequent commits (RFC follow-ups) can wire it up incrementally
 * without further ABI churn.
 *
 * Companion structures:
 *   - vos3_task_t.latency_class  (kernel/include/vos/task.h)
 *   - vos3_hardware_class_t      (this file)  — runtime CPU vs NUT detect
 *
 * NUT == Neural Unit Terminal: a home-class device whose primary
 * compute is an NPU (CPU is auxiliary). vs PC: traditional CPU-heavy
 * device with optional discrete NPU/GPU.
 */

#ifndef VOS3_DISPATCH_HINT_H
#define VOS3_DISPATCH_HINT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ----- Latency class taxonomy (2-bit field on vos3_task_t) -----
 *
 * APEX-VERIFY G-07: VOS3_LC_BATCH is value 0 so that legacy tasks
 * (created before v21.0.1, which never set latency_class) fall into
 * the throughput plane by default — preserving the v20.x behavior
 * documented in task.h. Earlier draft used 0=INTERACTIVE which would
 * have silently routed every legacy task through the new fast-path
 * queue, contradicting the "behavior unchanged" promise. New-style
 * agents that want the interactive path must set latency_class
 * explicitly to VOS3_LC_INTERACTIVE.
 */
typedef enum vos3_latency_class {
    /* Plane 2 — THROUGHPUT (DEFAULT for legacy callers) */
    VOS3_LC_BATCH       = 0u,  /* Training, cost-optimized, yieldable    */
    VOS3_LC_CONTROL     = 1u,  /* Kernel/dispatcher tasks — never preempted */
    /* Plane 1 — REAL-TIME */
    VOS3_LC_STREAMING   = 2u,  /* Decode tokens, jitter-sensitive        */
    VOS3_LC_INTERACTIVE = 3u,  /* User-facing, ≤30ms p99 (chat reply, UI) */
} vos3_latency_class_t;

#define VOS3_LC_PLANE1(lc)  ((lc) == VOS3_LC_INTERACTIVE || (lc) == VOS3_LC_STREAMING)
#define VOS3_LC_PLANE2(lc)  ((lc) == VOS3_LC_BATCH       || (lc) == VOS3_LC_CONTROL)

/* ----- Hybrid hardware class — set ONCE at boot ----- */

typedef enum vos3_hardware_class {
    VOS3_HW_UNKNOWN  = 0u,
    VOS3_HW_PC       = 1u,  /* CPU-heavy: most cycles in CPU, NPU optional   */
    VOS3_HW_NUT      = 2u,  /* NPU-heavy: dispatcher prioritizes NPU streams */
    VOS3_HW_HYBRID   = 3u,  /* Both significant — apply per-class routing    */
} vos3_hardware_class_t;

/**
 * Boot-time detector. Today returns VOS3_HW_PC unless an NPU is
 * present at probe time; future versions will inspect ACPI DSAR
 * + benchmark a tiny sample workload. Idempotent.
 */
vos3_hardware_class_t vos3_hardware_class_detect(void);

/**
 * Cached accessor — safe to call from any context after boot.
 * Returns VOS3_HW_UNKNOWN if called before vos3_hardware_class_detect().
 */
vos3_hardware_class_t vos3_hardware_class_get(void);

/* ----- Token-bucket backpressure (Plane 2 → Plane 1 protection) ----- */

/* Per-agent (or per-slot) cap on inflight Plane-2 tasks. The dispatcher
 * decrements `tokens` on enqueue and refills at `refill_per_tick`. When
 * tokens hit 0, new Plane-2 enqueues return -EAGAIN and the caller
 * yields. Plane-1 enqueues bypass the bucket entirely. */
typedef struct vos3_token_bucket {
    uint32_t tokens;            /* Current available (atomic-mutated)        */
    uint32_t capacity;          /* Maximum tokens                            */
    uint32_t refill_per_tick;   /* Tokens added per scheduler tick           */
    uint32_t _pad;
} vos3_token_bucket_t;

#ifdef __cplusplus
}
#endif

#endif /* VOS3_DISPATCH_HINT_H */
