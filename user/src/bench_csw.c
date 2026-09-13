/**
 * @file bench_csw.c
 * @brief VOS3 Context Switch Latency Benchmark
 *
 * @details Measures context switch latency using pipe ping-pong between
 *          parent and child processes. Also reads kernel RDTSC data
 *          via SYS_BENCH_READ syscall.
 *
 * @version 1.0.0
 * @date 2026-02-25
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define SYS_BENCH_READ          222
#define VOS3_BENCH_TYPE_CSW     0
#define VOS3_BENCH_TYPE_SUMMARY 1

#define PING_PONG_ITERATIONS    1000
#define PIPE_TOKEN              0x42

/* ============================================================================
 * BENCHMARK DATA STRUCTURES (must match kernel bench.h)
 * ============================================================================ */

typedef struct {
    unsigned long long csw_count;
    unsigned long long csw_min_cycles;
    unsigned long long csw_max_cycles;
    unsigned long long csw_avg_cycles;
    unsigned long long csw_total_cycles;
    unsigned long long tsc_freq_khz;
} bench_summary_t;

/* ============================================================================
 * HELPERS
 * ============================================================================ */

/**
 * @brief Read benchmark summary from kernel
 */
static int read_bench_summary(bench_summary_t* summary)
{
    long ret = syscall3(SYS_BENCH_READ,
                        VOS3_BENCH_TYPE_SUMMARY,
                        (long)summary,
                        (long)sizeof(bench_summary_t));
    return (ret > 0) ? 0 : -1;
}

/**
 * @brief Read TSC via inline rdtsc
 */
static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    printf("\n");
    printf("============================================\n");
    printf("  VOS3 Context Switch Latency Benchmark\n");
    printf("============================================\n");
    printf("\n");

    /* Create two pipes for ping-pong */
    int pipe_a[2];  /* parent writes, child reads */
    int pipe_b[2];  /* child writes, parent reads */

    if (pipe(pipe_a) < 0 || pipe(pipe_b) < 0) {
        printf("[ERROR] Failed to create pipes\n");
        return 1;
    }

    printf("[INFO] Pipes created for ping-pong test\n");
    printf("[INFO] Running %d iterations...\n", PING_PONG_ITERATIONS);

    pid_t pid = fork();
    if (pid < 0) {
        printf("[ERROR] Fork failed\n");
        return 1;
    }

    if (pid == 0) {
        /* ===== CHILD: echo back ===== */
        close(pipe_a[1]);  /* close write end of pipe_a */
        close(pipe_b[0]);  /* close read end of pipe_b */

        char buf;
        for (int i = 0; i < PING_PONG_ITERATIONS; i++) {
            if (read(pipe_a[0], &buf, 1) != 1) {
                break;
            }
            buf++;  /* modify to prove round-trip */
            if (write(pipe_b[1], &buf, 1) != 1) {
                break;
            }
        }

        close(pipe_a[0]);
        close(pipe_b[1]);
        _exit(0);
    }

    /* ===== PARENT: measure round-trip ===== */
    close(pipe_a[0]);  /* close read end of pipe_a */
    close(pipe_b[1]);  /* close write end of pipe_b */

    unsigned long long total_cycles = 0;
    unsigned long long min_cycles = (unsigned long long)-1;
    unsigned long long max_cycles = 0;
    int success_count = 0;

    for (int i = 0; i < PING_PONG_ITERATIONS; i++) {
        char token = PIPE_TOKEN;
        char reply;

        unsigned long long start = rdtsc();
        if (write(pipe_a[1], &token, 1) != 1) {
            break;
        }
        if (read(pipe_b[0], &reply, 1) != 1) {
            break;
        }
        unsigned long long end = rdtsc();

        unsigned long long elapsed = end - start;
        total_cycles += elapsed;
        success_count++;

        if (elapsed < min_cycles) {
            min_cycles = elapsed;
        }
        if (elapsed > max_cycles) {
            max_cycles = elapsed;
        }

        /* Verify data integrity */
        if (reply != (char)(PIPE_TOKEN + 1)) {
            printf("[WARN] Data mismatch at iteration %d\n", i);
        }
    }

    close(pipe_a[1]);
    close(pipe_b[0]);

    /* Wait for child */
    int status;
    waitpid(pid, &status, 0);

    /* ===== RESULTS ===== */
    printf("\n");
    printf("[RESULTS] User-Space Pipe Ping-Pong\n");
    printf("  Iterations:     %d\n", success_count);

    if (success_count > 0) {
        unsigned long long avg = total_cycles / (unsigned long long)success_count;
        /* Round-trip = 2 context switches, so divide by 2 for per-switch */
        printf("  Round-trip min: %llu cycles\n", min_cycles);
        printf("  Round-trip max: %llu cycles\n", max_cycles);
        printf("  Round-trip avg: %llu cycles\n", avg);
        printf("  Per-switch avg: ~%llu cycles (round-trip / 2)\n", avg / 2);
    }

    /* ===== KERNEL RDTSC DATA ===== */
    printf("\n");
    printf("[RESULTS] Kernel RDTSC Instrumentation\n");

    bench_summary_t summary;
    if (read_bench_summary(&summary) == 0) {
        printf("  Total switches: %llu\n", summary.csw_count);
        printf("  Min latency:    %llu cycles\n", summary.csw_min_cycles);
        printf("  Max latency:    %llu cycles\n", summary.csw_max_cycles);
        printf("  Avg latency:    %llu cycles\n", summary.csw_avg_cycles);
        printf("  TSC frequency:  ~%llu MHz\n", summary.tsc_freq_khz / 1000);

        if (summary.tsc_freq_khz > 0 && summary.csw_avg_cycles > 0) {
            /* Convert cycles to microseconds: us = cycles / (freq_khz / 1000) */
            unsigned long long avg_us =
                (summary.csw_avg_cycles * 1000ULL) / summary.tsc_freq_khz;
            printf("  Avg latency:    ~%llu us\n", avg_us);
        }
    } else {
        printf("  (kernel bench data unavailable)\n");
    }

    printf("\n");
    printf("============================================\n");
    printf("  Benchmark complete.\n");
    printf("============================================\n");

    return 0;
}
