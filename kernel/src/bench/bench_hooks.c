/**
 * @file bench_hooks.c
 * @brief VOS3 Benchmark Instrumentation Hooks
 *
 * @details RDTSC-based context switch latency measurement with
 *          ring buffer storage and syscall-based data export.
 *
 * @version 1.0.0
 * @date 2026-02-25
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/bench.h"
#include "../../include/vos/console.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/user.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * STATE
 * ============================================================================ */

/** @brief Ring buffer of CSW samples */
static vos3_bench_csw_sample_t g_csw_ring[VOS3_BENCH_CSW_SAMPLES];

/** @brief Write index into ring buffer */
static volatile size_t g_csw_write_idx = 0U;

/** @brief Total samples recorded (may exceed ring size) */
static volatile uint64_t g_csw_total = 0ULL;

/** @brief Running statistics */
static volatile uint64_t g_csw_min = UINT64_MAX;
static volatile uint64_t g_csw_max = 0ULL;
static volatile uint64_t g_csw_sum = 0ULL;

/** @brief Pending measurement: TSC at switch start */
static uint64_t g_pending_tsc = 0ULL;
static uint32_t g_pending_from = 0U;
static uint32_t g_pending_to = 0U;

/** @brief TSC frequency estimate (kHz), calibrated at init */
static uint64_t g_tsc_freq_khz = 0ULL;

/* ============================================================================
 * CALIBRATION
 * ============================================================================ */

/**
 * @brief Estimate TSC frequency using PIT
 *
 * Uses a 10ms PIT delay to calibrate. Result in kHz.
 */
static uint64_t calibrate_tsc(void)
{
    /* Use known timer: PIT channel 0 at 100 Hz = 10ms per tick.
     * Measure TSC across 1 tick (~10ms) for rough estimate. */
    uint64_t start = vos3_rdtsc();

    /* Busy-wait roughly 10ms using port 0x61 PIT spin */
    for (volatile int i = 0; i < 1000000; i++) {
        __asm__ volatile("pause");
    }

    uint64_t end = vos3_rdtsc();
    uint64_t elapsed = end - start;

    /* Assume the spin loop took ~10ms.
     * freq_khz = elapsed_cycles / 10 (since 10ms = 0.01s, freq = cycles/0.01) */
    uint64_t freq_khz = elapsed / 10U;

    if (freq_khz == 0U) {
        freq_khz = 2000000ULL;  /* Default: 2 GHz */
    }

    return freq_khz;
}

/* ============================================================================
 * BENCHMARK HOOKS
 * ============================================================================ */

void vos3_bench_init(void)
{
    g_csw_write_idx = 0U;
    g_csw_total = 0ULL;
    g_csw_min = UINT64_MAX;
    g_csw_max = 0ULL;
    g_csw_sum = 0ULL;
    g_pending_tsc = 0ULL;

    for (size_t i = 0U; i < VOS3_BENCH_CSW_SAMPLES; i++) {
        g_csw_ring[i].tsc_start = 0ULL;
        g_csw_ring[i].tsc_end = 0ULL;
        g_csw_ring[i].latency = 0ULL;
        g_csw_ring[i].from_tid = 0U;
        g_csw_ring[i].to_tid = 0U;
    }

    g_tsc_freq_khz = calibrate_tsc();

    VOS3_INFO("Benchmark: TSC freq ~%llu kHz (%llu MHz)",
              (unsigned long long)g_tsc_freq_khz,
              (unsigned long long)(g_tsc_freq_khz / 1000ULL));
}

void vos3_bench_csw_start(uint32_t from_tid, uint32_t to_tid)
{
    g_pending_from = from_tid;
    g_pending_to = to_tid;
    g_pending_tsc = vos3_rdtsc();
}

void vos3_bench_csw_end(void)
{
    if (g_pending_tsc == 0ULL) {
        return;
    }

    uint64_t end_tsc = vos3_rdtsc();
    uint64_t latency = end_tsc - g_pending_tsc;

    /* Store in ring buffer */
    size_t idx = g_csw_write_idx % VOS3_BENCH_CSW_SAMPLES;
    g_csw_ring[idx].tsc_start = g_pending_tsc;
    g_csw_ring[idx].tsc_end = end_tsc;
    g_csw_ring[idx].latency = latency;
    g_csw_ring[idx].from_tid = g_pending_from;
    g_csw_ring[idx].to_tid = g_pending_to;

    g_csw_write_idx++;
    g_csw_total++;
    g_csw_sum += latency;

    if (latency < g_csw_min) {
        g_csw_min = latency;
    }
    if (latency > g_csw_max) {
        g_csw_max = latency;
    }

    g_pending_tsc = 0ULL;
}

void vos3_bench_get_summary(vos3_bench_summary_t* summary)
{
    if (summary == NULL) {
        return;
    }

    summary->csw_count = g_csw_total;
    summary->csw_min_cycles = (g_csw_total > 0U) ? g_csw_min : 0ULL;
    summary->csw_max_cycles = g_csw_max;
    summary->csw_total_cycles = g_csw_sum;
    summary->csw_avg_cycles = (g_csw_total > 0U) ?
                               (g_csw_sum / g_csw_total) : 0ULL;
    summary->tsc_freq_khz = g_tsc_freq_khz;
}

size_t vos3_bench_get_csw_samples(vos3_bench_csw_sample_t* buf,
                                   size_t max_samples)
{
    if (buf == NULL || max_samples == 0U) {
        return 0U;
    }

    size_t available = (g_csw_total < VOS3_BENCH_CSW_SAMPLES) ?
                       (size_t)g_csw_total : VOS3_BENCH_CSW_SAMPLES;
    size_t count = (max_samples < available) ? max_samples : available;

    /* Copy from ring buffer, newest first */
    for (size_t i = 0U; i < count; i++) {
        size_t idx;
        if (g_csw_write_idx >= (i + 1U)) {
            idx = (g_csw_write_idx - 1U - i) % VOS3_BENCH_CSW_SAMPLES;
        } else {
            break;
        }
        buf[i] = g_csw_ring[idx];
    }

    return count;
}

void vos3_bench_reset(void)
{
    g_csw_write_idx = 0U;
    g_csw_total = 0ULL;
    g_csw_min = UINT64_MAX;
    g_csw_max = 0ULL;
    g_csw_sum = 0ULL;
    g_pending_tsc = 0ULL;
}

/* ============================================================================
 * SYSCALL HANDLER
 * ============================================================================ */

/**
 * @brief SYS_BENCH_READ syscall handler
 *
 * arg1 (rdi): bench type (VOS3_BENCH_TYPE_CSW or VOS3_BENCH_TYPE_SUMMARY)
 * arg2 (rsi): user buffer pointer
 * arg3 (rdx): buffer size
 *
 * Returns: bytes written, or negative error
 */
static int64_t bench_syscall_handler(vos3_syscall_frame_t* frame)
{
    uint64_t bench_type = frame->rdi;
    void* user_buf = (void*)frame->rsi;
    size_t buf_size = (size_t)frame->rdx;

    if (user_buf == NULL) {
        return -14;  /* EFAULT */
    }

    switch (bench_type) {
        case VOS3_BENCH_TYPE_SUMMARY: {
            if (buf_size < sizeof(vos3_bench_summary_t)) {
                return -22;  /* EINVAL */
            }
            vos3_bench_summary_t summary;
            vos3_bench_get_summary(&summary);
            if (vos3_copy_to_user(user_buf, &summary, sizeof(summary)) != 0) {
                return -14;  /* EFAULT */
            }
            return (int64_t)sizeof(summary);
        }

        case VOS3_BENCH_TYPE_CSW: {
            size_t max_samples = buf_size / sizeof(vos3_bench_csw_sample_t);
            if (max_samples == 0U) {
                return -22;  /* EINVAL */
            }
            /* Use kernel buffer for copy */
            size_t copy_count = (max_samples > 64U) ? 64U : max_samples;
            vos3_bench_csw_sample_t kbuf[64];
            size_t got = vos3_bench_get_csw_samples(kbuf, copy_count);
            if (got > 0U) {
                size_t bytes = got * sizeof(vos3_bench_csw_sample_t);
                if (vos3_copy_to_user(user_buf, kbuf, bytes) != 0) {
                    return -14;  /* EFAULT */
                }
                return (int64_t)bytes;
            }
            return 0;
        }

        default:
            return -22;  /* EINVAL */
    }
}

/**
 * @brief Register benchmark syscall
 */
void vos3_bench_syscalls_init(void)
{
    VOS3_INFO("Registering benchmark syscall (SYS_BENCH_READ=%d)", SYS_BENCH_READ);
    vos3_syscall_register(SYS_BENCH_READ, bench_syscall_handler);
}
