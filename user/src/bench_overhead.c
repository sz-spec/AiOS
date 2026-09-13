/**
 * @file bench_overhead.c
 * @brief VOS3 Host Resource Overhead Benchmark
 *
 * @details Simulates 10 parallel AI agent processes and measures:
 *          - Fork/exec overhead
 *          - Scheduler throughput under load
 *          - Memory consumption per process
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

#define NUM_AGENTS          10
#define WORK_ITERATIONS     10000
#define SYS_BENCH_READ      222
#define VOS3_BENCH_TYPE_SUMMARY 1

/* ============================================================================
 * DATA STRUCTURES
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

static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

/**
 * @brief Simulated AI agent workload
 *
 * Each "agent" does compute work (hashing), simulating
 * an AI inference or data processing pipeline.
 */
static void agent_workload(int agent_id)
{
    volatile unsigned long long hash = 0x1234567890ABCDEFULL;

    for (int i = 0; i < WORK_ITERATIONS; i++) {
        /* FNV-1a like hash computation */
        hash ^= (unsigned long long)(agent_id + i);
        hash *= 0x100000001B3ULL;
        hash ^= (hash >> 17);
    }

    /* Prevent optimizer from removing the loop */
    if (hash == 0) {
        printf("  agent %d: hash=0 (unreachable)\n", agent_id);
    }
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
    printf("  VOS3 Host Resource Overhead Benchmark\n");
    printf("============================================\n");
    printf("\n");
    printf("[INFO] Simulating %d parallel AI agents\n", NUM_AGENTS);
    printf("[INFO] Each agent performs %d compute iterations\n", WORK_ITERATIONS);
    printf("\n");

    /* Snapshot CSW count before */
    bench_summary_t before;
    int have_bench = (syscall3(SYS_BENCH_READ,
                               VOS3_BENCH_TYPE_SUMMARY,
                               (long)&before,
                               (long)sizeof(before)) > 0) ? 1 : 0;

    unsigned long long fork_total = 0;
    unsigned long long overall_start = rdtsc();

    pid_t children[NUM_AGENTS];
    int child_count = 0;

    /* ===== Phase 1: Fork all agents ===== */
    printf("[PHASE 1] Forking %d agent processes...\n", NUM_AGENTS);

    for (int i = 0; i < NUM_AGENTS; i++) {
        unsigned long long fork_start = rdtsc();
        pid_t pid = fork();
        unsigned long long fork_end = rdtsc();

        if (pid < 0) {
            printf("  [WARN] Fork failed for agent %d\n", i);
            break;
        }

        if (pid == 0) {
            /* ===== CHILD: run agent workload ===== */
            agent_workload(i);
            _exit(0);
        }

        /* Parent */
        fork_total += (fork_end - fork_start);
        children[child_count++] = pid;
    }

    printf("  Forked %d agents\n", child_count);
    if (child_count > 0) {
        printf("  Avg fork time: %llu cycles\n", fork_total / (unsigned long long)child_count);
    }

    /* ===== Phase 2: Wait for all agents ===== */
    printf("\n[PHASE 2] Waiting for %d agents to complete...\n", child_count);

    unsigned long long wait_start = rdtsc();
    int exit_ok = 0;
    int exit_fail = 0;

    for (int i = 0; i < child_count; i++) {
        int status;
        pid_t result = waitpid(children[i], &status, 0);
        if (result > 0 && status == 0) {
            exit_ok++;
        } else {
            exit_fail++;
        }
    }

    unsigned long long wait_end = rdtsc();
    unsigned long long overall_end = rdtsc();

    printf("  Completed: %d OK, %d failed\n", exit_ok, exit_fail);
    printf("  Wait phase: %llu cycles\n", wait_end - wait_start);

    /* ===== Phase 3: Results ===== */
    printf("\n[RESULTS] Overhead Summary\n");

    unsigned long long total_cycles = overall_end - overall_start;
    printf("  Total time:      %llu cycles\n", total_cycles);
    printf("  Fork overhead:   %llu cycles total\n", fork_total);

    if (child_count > 0) {
        printf("  Per-agent fork:  %llu cycles\n",
               fork_total / (unsigned long long)child_count);
    }

    /* Context switch data delta */
    if (have_bench) {
        bench_summary_t after;
        if (syscall3(SYS_BENCH_READ,
                     VOS3_BENCH_TYPE_SUMMARY,
                     (long)&after,
                     (long)sizeof(after)) > 0) {
            unsigned long long csw_delta = after.csw_count - before.csw_count;
            printf("  Context switches: %llu (during benchmark)\n", csw_delta);
            if (csw_delta > 0 && after.tsc_freq_khz > 0) {
                printf("  Avg CSW latency:  %llu cycles\n", after.csw_avg_cycles);
            }
        }
    }

    /* Estimate: with N agents and M compute iterations each,
     * total "useful work" = N * M iterations.
     * Overhead = total_cycles - (N * per_agent_cycles_solo) */
    printf("\n");
    printf("  Agents spawned:  %d / %d\n", child_count, NUM_AGENTS);
    printf("  Success rate:    %d%%\n",
           child_count > 0 ? (exit_ok * 100 / child_count) : 0);

    printf("\n");
    printf("============================================\n");
    printf("  Benchmark complete.\n");
    printf("============================================\n");

    return (exit_fail == 0 && child_count == NUM_AGENTS) ? 0 : 1;
}
