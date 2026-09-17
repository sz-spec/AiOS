/**
 * @file bench_csw_1ms.c
 * @brief Context Switch Audit — Minimum Quantum Benchmark
 *
 * Runs context switch measurement with the system's minimum quantum
 * (1 tick = 10ms on VOS3's 100Hz timer). Tracks per-switch cycle cost,
 * syscall overhead, and scheduler latency via RDTSC.
 *
 * Reports:
 *   - Cycle Cost per Switch (user-space measured)
 *   - Kernel-instrumented switch latency
 *   - Syscall overhead (null syscall round-trip)
 *   - Switch-to-switch jitter (min/max/stddev)
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* Syscall numbers */
#define SYS_FORK        57
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_GETTIME     40
#define SYS_SYSINFO     99
#define SYS_BENCH_READ  222
#define SYS_GETPID      39
#define SYS_YIELD       24    /* VOS3_SYS_YIELD */

#define VOS3_BENCH_TYPE_SUMMARY 1

/* Test framework */
static int g_pass = 0;
static int g_fail = 0;
#define TEST_PASS(name) do { printf("[PASS] %s\n", name); g_pass++; } while(0)
#define TEST_FAIL(name, msg) do { printf("[FAIL] %s: %s\n", name, msg); g_fail++; } while(0)

/* Kernel bench structs */
typedef struct {
    unsigned long long csw_count;
    unsigned long long csw_min_cycles;
    unsigned long long csw_max_cycles;
    unsigned long long csw_avg_cycles;
    unsigned long long csw_total_cycles;
    unsigned long long tsc_freq_khz;
} bench_summary_t;

typedef struct {
    unsigned long free_pages;
    unsigned long total_pages;
    unsigned int  nr_tasks;
    unsigned int  nr_zombies;
    unsigned long uptime_ms;
} vos3_sysinfo_t;

static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

/* ============================================================================
 * TEST 1: Null syscall overhead (baseline)
 * ============================================================================ */

static void test_syscall_overhead(void)
{
    printf("\n--- Syscall Overhead Baseline ---\n");

    /* Warm up */
    for (int i = 0; i < 100; i++)
        syscall0(SYS_GETPID);

    unsigned long long total = 0;
    unsigned long long min_c = (unsigned long long)-1;
    unsigned long long max_c = 0;
    int iters = 10000;

    for (int i = 0; i < iters; i++) {
        unsigned long long s = rdtsc();
        syscall0(SYS_GETPID);
        unsigned long long e = rdtsc();
        unsigned long long d = e - s;
        total += d;
        if (d < min_c) min_c = d;
        if (d > max_c) max_c = d;
    }

    unsigned long long avg = total / (unsigned long long)iters;
    printf("[PERF] Null syscall (getpid): min=%llu avg=%llu max=%llu cycles\n",
           min_c, avg, max_c);

    if (avg < 50000) {
        TEST_PASS("syscall_overhead_reasonable");
    } else {
        TEST_FAIL("syscall_overhead_reasonable", "avg > 50000 cycles");
    }
}

/* ============================================================================
 * TEST 2: Yield-based context switch cost
 * ============================================================================ */

static void test_yield_csw_cost(void)
{
    printf("\n--- Yield Context Switch Cost ---\n");

    int pipe_a[2], pipe_b[2];
    if (pipe(pipe_a) < 0 || pipe(pipe_b) < 0) {
        TEST_FAIL("yield_csw", "pipe creation failed");
        return;
    }

    long pid = syscall0(SYS_FORK);
    if (pid < 0) {
        TEST_FAIL("yield_csw", "fork failed");
        return;
    }

    enum { ITERS = 2000, WARMUP = 50 };

    if (pid == 0) {
        /* Child: echo back via pipes */
        close(pipe_a[1]);
        close(pipe_b[0]);
        char buf;
        int exchanged = 0;
        for (int i = 0; i < ITERS + WARMUP; i++) {
            if (read(pipe_a[0], &buf, 1) != 1) break;
            buf++;
            if (write(pipe_b[1], &buf, 1) != 1) break;
            exchanged++;
        }
        close(pipe_a[0]);
        close(pipe_b[1]);
        syscall1(SYS_EXIT, exchanged == ITERS + WARMUP ? 0 : 1);
        for(;;);
    }

    /* Parent: measure round-trip */
    close(pipe_a[0]);
    close(pipe_b[1]);

    /* Warm up */
    int warmup_ok = 1;
    for (int i = 0; i < WARMUP; i++) {
        char tok = 0x42;
        char rpl;
        if (write(pipe_a[1], &tok, 1) != 1 ||
            read(pipe_b[0], &rpl, 1) != 1 || rpl != tok + 1) {
            warmup_ok = 0;
            break;
        }
    }

    unsigned long long total = 0;
    unsigned long long min_rt = (unsigned long long)-1;
    unsigned long long max_rt = 0;
    int success = 0;

    /* Collect samples for jitter analysis */
    #define MAX_SAMPLES 500
    unsigned long long samples[MAX_SAMPLES];

    for (int i = 0; warmup_ok && i < ITERS; i++) {
        char tok = 0x42;
        char rpl;

        unsigned long long s = rdtsc();
        if (write(pipe_a[1], &tok, 1) != 1 ||
            read(pipe_b[0], &rpl, 1) != 1 || rpl != tok + 1) break;
        unsigned long long e = rdtsc();

        unsigned long long d = e - s;
        total += d;
        if (d < min_rt) min_rt = d;
        if (d > max_rt) max_rt = d;
        if (success < MAX_SAMPLES)
            samples[success] = d;
        success++;
    }

    close(pipe_a[1]);
    close(pipe_b[0]);
    int status = -1;
    long waited = syscall4(SYS_WAIT4, pid, (long)&status, 0, 0);

    if (warmup_ok && success == ITERS && waited == pid && status == 0) {
        unsigned long long avg = total / (unsigned long long)success;
        unsigned long long per_switch = avg / 2;

        printf("[PERF] Pipe ping-pong (%d iters):\n", success);
        printf("[PERF]   Round-trip:  min=%llu avg=%llu max=%llu cycles\n",
               min_rt, avg, max_rt);
        printf("[PERF]   Per-switch:  ~%llu cycles\n", per_switch);

        /* Calculate jitter (standard deviation approximation) */
        unsigned long long sum_sq_diff = 0;
        int n = success < MAX_SAMPLES ? success : MAX_SAMPLES;
        for (int i = 0; i < n; i++) {
            long long diff = (long long)samples[i] - (long long)avg;
            sum_sq_diff += (unsigned long long)(diff * diff / 1000);
        }
        unsigned long long variance_k = sum_sq_diff / (unsigned long long)n;
        /* Approximate sqrt: just report variance for now */
        printf("[PERF]   Jitter:      variance=%llu (x1000 cycles^2)\n", variance_k);
        printf("[PERF]   Spread:      max/min ratio = %llu.%02llux\n",
               max_rt / min_rt, (max_rt * 100 / min_rt) % 100);

        TEST_PASS("yield_csw_measured");
    } else {
        TEST_FAIL("yield_csw_measured", "incomplete exchange or unsuccessful child");
    }
}

/* ============================================================================
 * TEST 3: Multi-task preemption with minimum quantum
 * ============================================================================ */

static void test_preemption_cost(void)
{
    printf("\n--- Preemption Cost (min quantum) ---\n");

    /* Fork 4 CPU-bound children + parent all competing */
    #define PREEMPT_CHILDREN 4
    pid_t children[PREEMPT_CHILDREN];

    unsigned long long t_start = rdtsc();
    unsigned long ms_start = (unsigned long)syscall0(SYS_GETTIME);

    for (int c = 0; c < PREEMPT_CHILDREN; c++) {
        long pid = syscall0(SYS_FORK);
        if (pid < 0) {
            children[c] = -1;
            continue;
        }
        if (pid == 0) {
            /* CPU-bound child: spin for ~500ms worth of cycles */
            volatile unsigned long long counter = 0;
            unsigned long long child_start = rdtsc();
            while (rdtsc() - child_start < 500000000ULL) { /* ~500M cycles */
                counter++;
            }
            syscall1(SYS_EXIT, 0);
            for(;;);
        }
        children[c] = (pid_t)pid;
    }

    /* Parent also does CPU work while children run */
    volatile unsigned long long parent_work = 0;
    unsigned long long work_start = rdtsc();
    while (rdtsc() - work_start < 200000000ULL) { /* ~200M cycles */
        parent_work++;
    }

    /* Wait for all children */
    for (int c = 0; c < PREEMPT_CHILDREN; c++) {
        if (children[c] > 0) {
            int st = 0;
            syscall4(SYS_WAIT4, children[c], (long)&st, 0, 0);
        }
    }

    unsigned long long t_end = rdtsc();
    unsigned long ms_end = (unsigned long)syscall0(SYS_GETTIME);
    unsigned long elapsed = ms_end - ms_start;

    printf("[PERF] %d children + parent competed for %lu ms\n",
           PREEMPT_CHILDREN, elapsed);
    printf("[PERF] Parent completed %llu work units\n", parent_work);
    printf("[PERF] Total wall cycles: %llu\n", t_end - t_start);

    /* Read kernel CSW stats */
    bench_summary_t summary;
    long ret = syscall3(SYS_BENCH_READ, VOS3_BENCH_TYPE_SUMMARY,
                        (long)&summary, (long)sizeof(summary));
    if (ret > 0) {
        printf("[PERF] Kernel CSW stats after preemption test:\n");
        printf("[PERF]   Total switches:  %llu\n", summary.csw_count);
        printf("[PERF]   Cycle cost:      min=%llu avg=%llu max=%llu\n",
               summary.csw_min_cycles, summary.csw_avg_cycles,
               summary.csw_max_cycles);
        if (summary.tsc_freq_khz > 0) {
            unsigned long long avg_us =
                (summary.csw_avg_cycles * 1000ULL) / summary.tsc_freq_khz;
            printf("[PERF]   Avg latency:    ~%llu us (%llu.%03llu MHz TSC)\n",
                   avg_us, summary.tsc_freq_khz / 1000,
                   summary.tsc_freq_khz % 1000);
        }
    }

    if (elapsed > 0 && elapsed < 10000) {
        TEST_PASS("preemption_under_load");
    } else {
        TEST_FAIL("preemption_under_load", "took too long or zero time");
    }
}

/* ============================================================================
 * TEST 4: Fork/exit churn rate
 * ============================================================================ */

static void test_fork_churn_rate(void)
{
    printf("\n--- Fork/Exit Churn Rate ---\n");

    int iters = 200;
    unsigned long long t_start = rdtsc();
    unsigned long ms_start = (unsigned long)syscall0(SYS_GETTIME);
    int errors = 0;

    for (int i = 0; i < iters; i++) {
        long pid = syscall0(SYS_FORK);
        if (pid < 0) { errors++; continue; }
        if (pid == 0) {
            syscall1(SYS_EXIT, 0);
            for(;;);
        }
        int st = 0;
        syscall4(SYS_WAIT4, pid, (long)&st, 0, 0);
    }

    unsigned long long t_end = rdtsc();
    unsigned long ms_end = (unsigned long)syscall0(SYS_GETTIME);
    unsigned long elapsed = ms_end - ms_start;

    unsigned long long cycles_per_forkwait = (t_end - t_start) / (unsigned long long)iters;

    printf("[PERF] %d fork+exit+wait cycles in %lu ms\n", iters, elapsed);
    printf("[PERF] %llu cycles per fork+exit+wait\n", cycles_per_forkwait);
    if (elapsed > 0) {
        unsigned long rate = (unsigned long)iters * 1000 / elapsed;
        printf("[PERF] Fork/exit rate: %lu /sec\n", rate);
    }
    printf("[PERF] Errors: %d\n", errors);

    if (errors == 0) {
        TEST_PASS("fork_churn_rate");
    } else {
        TEST_FAIL("fork_churn_rate", "fork errors detected");
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char* argv[])
{
    (void)argc; (void)argv;

    printf("=== Context Switch Audit — Performance Baseline ===\n");
    printf("  Timer:   100 Hz (10ms tick)\n");
    printf("  Quantum: 1 tick minimum (10ms)\n");

    test_syscall_overhead();
    test_yield_csw_cost();
    test_preemption_cost();
    test_fork_churn_rate();

    printf("\n=== Context Switch Audit Complete ===\n");
    printf("  PASS: %d  FAIL: %d\n", g_pass, g_fail);
    return (g_fail > 0) ? 1 : 0;
}
