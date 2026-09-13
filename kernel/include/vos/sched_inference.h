/**
 * @file sched_inference.h
 * @brief vOS SCHED_INFERENCE class — inference-latency-aware scheduling
 *        (Sprint 16 / Item J1).
 *
 * Why this exists
 * ---------------
 *
 * From the 80-problem agent-era catalog, J1:
 *   "CFS doesn't model 'inference latency SLA' — P99 ≤ X ms for token
 *    generation is not expressible as a CFS class."
 *
 * Inference workloads have two distinct latency SLOs:
 *
 *   1. TTFT (Time To First Token) — wall time from request arrival to
 *      first output token. User-facing perceived latency. Typical SLO
 *      target: P95 ≤ 300 ms.
 *
 *   2. TBT (Time Between Tokens) — wall time between consecutive output
 *      tokens during streaming. Determines perceived "smoothness".
 *      Typical SLO target: P99 ≤ 80 ms.
 *
 * Neither maps onto CFS vruntime fairness. SCHED_FIFO / SCHED_RR don't
 * scale because every request would be RT priority. We need a class that
 * orders requests by **earliest deadline first** (EDF) over a sliding
 * deadline window, while still being preemptable by SCHED_FIFO for true
 * RT tasks (boot, IPI handlers).
 *
 * Public surface
 * --------------
 *
 *   - vos3_sched_inference_init(queue, max_requests)
 *   - vos3_sched_inference_submit(queue, req_id, ttft_deadline_ns,
 *                                  tbt_deadline_ns, opaque)
 *   - vos3_sched_inference_pick_next(queue, *out_req)  — EDF picker
 *   - vos3_sched_inference_complete(queue, req_id, completed_ns)
 *   - vos3_sched_inference_stats(queue, *stats) — slo_violations,
 *                                  total_admitted, total_completed,
 *                                  p50/p95/p99 latency
 *
 * The "queue" is per-CPU (kernel callers allocate one per CPU and
 * pass requests in via vos3_sched_inference_submit on the request-
 * arrival path; the scheduler tick calls pick_next).
 *
 * Honest scope ceiling
 * --------------------
 *
 *   - This header defines the API + the EDF picker logic. Actual
 *     integration with kernel/src/sched/scheduler.c happens in Wave 2
 *     once the LSM hooks from B3 land — until then this is exercised
 *     end-to-end via the Python twin in backend/services/sched_inference.py.
 *
 *   - The "deadline" is a wall-clock absolute time in nanoseconds since
 *     boot. We do NOT compute deadline-budget remaining (that requires
 *     hooking the clock-tick path); we just pick the request with the
 *     smallest absolute deadline.
 *
 *   - SLO-violation accounting is best-effort: if a request misses
 *     its deadline, we increment slo_violations but do NOT cancel the
 *     request (cancel would orphan partial output tokens, worse UX).
 *     The next-priority effect comes from the EDF picker preferring
 *     newer, non-missed requests.
 *
 * References:
 *   - SLAI: SLO-aware LLM inference scheduler (arxiv 2508.01002)
 *   - TempoNet: Slack-Quantized Transformer-Guided RL Scheduler (arxiv 2602.18109)
 *   - Linux sched_ext docs.kernel.org/scheduler/sched-ext.html
 */

#ifndef VOS_SCHED_INFERENCE_H
#define VOS_SCHED_INFERENCE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Return codes
 * ============================================================================ */

#define VOS3_SCHED_OK              0
#define VOS3_SCHED_ERR_INVAL      -1
#define VOS3_SCHED_ERR_FULL       -2  /**< Queue at max_requests capacity */
#define VOS3_SCHED_ERR_NOTFOUND   -3  /**< req_id not in queue */
#define VOS3_SCHED_ERR_EMPTY      -4  /**< pick_next on empty queue */

/* ============================================================================
 * Request descriptor
 *
 * 64 bytes — cacheline-friendly for EDF scans.
 * ============================================================================ */

typedef enum vos3_sched_request_status {
    VOS3_SCHED_REQ_FREE       = 0U,
    VOS3_SCHED_REQ_QUEUED     = 1U,
    VOS3_SCHED_REQ_RUNNING    = 2U,
    VOS3_SCHED_REQ_COMPLETED  = 3U,
    VOS3_SCHED_REQ_MISSED_SLO = 4U,  /**< Completed past deadline */
} vos3_sched_request_status_t;

typedef struct vos3_sched_request {
    uint64_t                    req_id;
    uint64_t                    arrival_ns;        /**< Wall time at submit */
    uint64_t                    ttft_deadline_ns;  /**< Absolute TTFT deadline */
    uint64_t                    tbt_deadline_ns;   /**< Absolute next-TBT deadline */
    uint64_t                    completed_ns;      /**< 0 until complete() called */
    uint64_t                    opaque;            /**< Caller cookie (e.g. slot_id) */
    vos3_sched_request_status_t status;
    uint32_t                    _pad;              /**< 4 bytes for 8-byte align */
} vos3_sched_request_t;

/* ============================================================================
 * Queue descriptor + stats
 * ============================================================================ */

typedef struct vos3_sched_inference_stats {
    uint64_t total_admitted;       /**< Cumulative submit() count */
    uint64_t total_completed;      /**< Cumulative complete() count */
    uint64_t slo_violations;       /**< Completions past deadline */
    uint64_t p50_latency_ns;       /**< Median observed latency */
    uint64_t p95_latency_ns;       /**< 95th percentile */
    uint64_t p99_latency_ns;       /**< 99th percentile */
} vos3_sched_inference_stats_t;

typedef struct vos3_sched_inference_queue {
    vos3_sched_request_t        *requests;     /**< Caller-provided storage */
    uint32_t                     max_requests;
    uint32_t                     active;       /**< Currently QUEUED + RUNNING */
    uint64_t                     next_req_id;  /**< Monotonic counter */
    vos3_sched_inference_stats_t stats;
} vos3_sched_inference_queue_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * @brief Initialize an inference scheduling queue.
 *
 * @param  queue          Queue struct to populate.
 * @param  requests       Caller-provided backing storage.
 * @param  max_requests   Capacity of requests[].
 *
 * @return VOS3_SCHED_OK / VOS3_SCHED_ERR_INVAL.
 */
int vos3_sched_inference_init(vos3_sched_inference_queue_t *queue,
                              vos3_sched_request_t        *requests,
                              uint32_t                     max_requests);

/**
 * @brief Submit a new inference request to the queue.
 *
 * @param  queue              Initialized queue.
 * @param  arrival_ns         Wall time at submit (caller supplies — lets
 *                            tests inject deterministic time).
 * @param  ttft_deadline_ns   Absolute deadline for first output token.
 * @param  tbt_deadline_ns    Absolute deadline for the next token after
 *                            first-token emission.
 * @param  opaque             Caller cookie returned by pick_next/lookup.
 * @param  out_req_id         On success, the assigned request ID.
 *
 * @return VOS3_SCHED_OK / VOS3_SCHED_ERR_INVAL / VOS3_SCHED_ERR_FULL.
 */
int vos3_sched_inference_submit(vos3_sched_inference_queue_t *queue,
                                uint64_t  arrival_ns,
                                uint64_t  ttft_deadline_ns,
                                uint64_t  tbt_deadline_ns,
                                uint64_t  opaque,
                                uint64_t *out_req_id);

/**
 * @brief Pick the next request to run using earliest-deadline-first.
 *
 * Scans QUEUED requests and returns the one whose ttft_deadline_ns is
 * smallest. Updates that request's status to RUNNING. The caller is
 * responsible for actually scheduling it onto a CPU.
 *
 * @param  queue    Initialized queue.
 * @param  out_req  On success, written with a COPY of the picked request.
 *
 * @return VOS3_SCHED_OK / VOS3_SCHED_ERR_EMPTY / VOS3_SCHED_ERR_INVAL.
 */
int vos3_sched_inference_pick_next(vos3_sched_inference_queue_t *queue,
                                   vos3_sched_request_t        *out_req);

/**
 * @brief Mark a request as completed.
 *
 * If completed_ns > the request's original deadline, slo_violations is
 * bumped and status is set to MISSED_SLO; otherwise status is set to
 * COMPLETED.
 *
 * @return VOS3_SCHED_OK / VOS3_SCHED_ERR_NOTFOUND.
 */
int vos3_sched_inference_complete(vos3_sched_inference_queue_t *queue,
                                  uint64_t req_id,
                                  uint64_t completed_ns);

/**
 * @brief Snapshot current statistics.
 *
 * @return VOS3_SCHED_OK + out filled on success.
 */
int vos3_sched_inference_stats(const vos3_sched_inference_queue_t *queue,
                               vos3_sched_inference_stats_t       *out);

#ifdef __cplusplus
}
#endif

#endif /* VOS_SCHED_INFERENCE_H */
