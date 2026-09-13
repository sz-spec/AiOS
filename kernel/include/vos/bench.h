/**
 * @file bench.h
 * @brief VOS3 Kernel Benchmark Instrumentation
 *
 * @details RDTSC-based context switch latency measurement and
 *          benchmark data export via syscall.
 *
 * @version 1.0.0
 * @date 2026-02-25
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_BENCH_H
#define VOS3_BENCH_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief Number of context switch samples to keep in ring buffer */
#define VOS3_BENCH_CSW_SAMPLES      ((size_t)1024U)

/** @brief Syscall number for reading benchmark data */
#define SYS_BENCH_READ              222

/** @brief Benchmark data types (arg1 to SYS_BENCH_READ) */
#define VOS3_BENCH_TYPE_CSW         0   /**< Context switch latency data */
#define VOS3_BENCH_TYPE_SUMMARY     1   /**< Summary statistics */

/* ============================================================================
 * DATA STRUCTURES
 * ============================================================================ */

/**
 * @brief Context switch latency sample
 */
typedef struct vos3_bench_csw_sample {
    uint64_t tsc_start;     /**< RDTSC before switch */
    uint64_t tsc_end;       /**< RDTSC after switch (on new task) */
    uint64_t latency;       /**< tsc_end - tsc_start */
    uint32_t from_tid;      /**< Task switched from */
    uint32_t to_tid;        /**< Task switched to */
} vos3_bench_csw_sample_t;

/**
 * @brief Summary statistics for benchmark data
 */
typedef struct vos3_bench_summary {
    uint64_t csw_count;         /**< Total context switches measured */
    uint64_t csw_min_cycles;    /**< Minimum latency in TSC cycles */
    uint64_t csw_max_cycles;    /**< Maximum latency in TSC cycles */
    uint64_t csw_avg_cycles;    /**< Average latency in TSC cycles */
    uint64_t csw_total_cycles;  /**< Sum of all latencies */
    uint64_t tsc_freq_khz;      /**< Estimated TSC frequency (kHz) */
} vos3_bench_summary_t;

/* ============================================================================
 * KERNEL API
 * ============================================================================ */

/**
 * @brief Initialize benchmark subsystem
 */
void vos3_bench_init(void);

/**
 * @brief Record context switch start (called before switch)
 * @param[in] from_tid Task ID being switched from
 * @param[in] to_tid   Task ID being switched to
 */
void vos3_bench_csw_start(uint32_t from_tid, uint32_t to_tid);

/**
 * @brief Record context switch end (called after switch completes)
 */
void vos3_bench_csw_end(void);

/**
 * @brief Get summary statistics
 * @param[out] summary Output structure
 */
void vos3_bench_get_summary(vos3_bench_summary_t* summary);

/**
 * @brief Get raw CSW samples
 * @param[out] buf Output buffer for samples
 * @param[in]  max_samples Maximum number of samples to return
 * @return Number of samples copied
 */
size_t vos3_bench_get_csw_samples(vos3_bench_csw_sample_t* buf,
                                   size_t max_samples);

/**
 * @brief Reset all benchmark data
 */
void vos3_bench_reset(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_BENCH_H */
