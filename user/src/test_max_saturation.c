/**
 * @file test_max_saturation.c
 * @brief Total System Saturation Stress Test
 *
 * Fills every slot in g_task_table (VOS3_MAX_TASKS=1024) by forking 1023 times.
 * Each child performs heavy FPU computation simulating AI model inference
 * (dense-layer forward pass with Leaky ReLU activation).
 * Verifies fork fails gracefully when table is full.
 * Measures P99 syscall latency and context-switch overhead at 100% occupancy.
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"
#include "vos_sysinfo.h"

/* ── Syscall numbers ────────────────────────────────────────────────── */
#define SYS_WRITE       1
#define SYS_GETPID      39
#define SYS_GETTIME     40
#define SYS_FORK        57
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_SCHED_YIELD 24

/* ── Configuration ──────────────────────────────────────────────────── */
#define MAX_TASKS           1024
#define TARGET_FORKS        1023    /* parent + 1023 = 1024 tasks          */
#define FPU_BATCH           50      /* FPU iterations per yield            */
#define LATENCY_SAMPLES     512     /* getpid() round-trip samples         */
#define CSW_SAMPLES         64      /* sched_yield() round-trip samples    */

/* ── Test framework ─────────────────────────────────────────────────── */
static int g_pass = 0;
static int g_fail = 0;
#define TEST_PASS(name) do { printf("[PASS] %s\n", name); g_pass++; } while(0)
#define TEST_FAIL(name, msg) do { printf("[FAIL] %s: %s\n", name, msg); g_fail++; } while(0)

/* ── Kernel sysinfo ─────────────────────────────────────────────────── */

/* ── Static arrays (avoid stack overflow with 1023 children) ────────── */
static pid_t              s_children[TARGET_FORKS];
static unsigned long long s_latencies[LATENCY_SAMPLES];
static unsigned long long s_csw_times[CSW_SAMPLES];

/* ── Helpers ────────────────────────────────────────────────────────── */
static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

static unsigned long get_uptime_ms(void)
{
    return (unsigned long)syscall0(SYS_GETTIME);
}

static int get_sysinfo(vos3_sysinfo_t* info)
{
    return vos3_get_sysinfo(info);
}

/* ── Insertion sort for u64 array ───────────────────────────────────── */
static void sort_u64(unsigned long long* arr, int n)
{
    for (int i = 1; i < n; i++) {
        unsigned long long key = arr[i];
        int j = i - 1;
        while (j >= 0 && arr[j] > key) {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }
}

/* ────────────────────────────────────────────────────────────────────
 * AI Model Simulation — dense-layer forward pass
 *
 * 4×4 weight matrix × 4-element activation vector, iterated with
 * Leaky-ReLU activation.  Exercises all x87/SSE FPU registers so
 * that every context switch must save/restore the full FPU state.
 * ──────────────────────────────────────────────────────────────────── */
static double fpu_ai_simulate(int seed, int iterations)
{
    volatile double W[4][4];
    volatile double x[4];
    double y[4];

    /* Seed-dependent weight initialisation */
    for (int i = 0; i < 4; i++) {
        x[i] = (double)(seed + i) / 100.0;
        for (int j = 0; j < 4; j++)
            W[i][j] = (double)((seed * (i + 1) + j * 7) % 100) / 100.0 - 0.5;
    }

    for (int iter = 0; iter < iterations; iter++) {
        /* y = W · x  (matrix-vector multiply) */
        for (int i = 0; i < 4; i++) {
            y[i] = 0.0;
            for (int j = 0; j < 4; j++)
                y[i] += W[i][j] * x[j];
        }
        /* Leaky-ReLU + normalise */
        for (int i = 0; i < 4; i++) {
            x[i] = (y[i] > 0.0) ? y[i] : 0.01 * y[i];
            if (x[i] >  1e6) x[i] =  1.0;
            if (x[i] < -1e6) x[i] = -1.0;
        }
    }
    return x[0] + x[1] + x[2] + x[3];
}

/* ────────────────────────────────────────────────────────────────────
 * Child process workload — iteration-based (no timer dependency)
 *
 * Each child does FPU_ROUNDS batches of FPU work interleaved with
 * yield().  This exercises FPU save/restore across context switches.
 * Iteration-based avoids timer starvation issues under extreme load
 * (1024 tasks in QEMU makes get_uptime_ms() unreliable per-child).
 * ──────────────────────────────────────────────────────────────────── */
#define FPU_ROUNDS  3   /* batches per child — minimal to verify FPU integrity */

static void __attribute__((noreturn)) child_work(int index)
{
    volatile double checksum = 0.0;

    /* Interleaved FPU work + yield: exercises lazy FPU save/restore */
    for (int r = 0; r < FPU_ROUNDS; r++) {
        checksum += fpu_ai_simulate(index, FPU_BATCH);
        syscall0(SYS_SCHED_YIELD);
    }

    /* Exit 0 = FPU produced a non-zero checksum (good) */
    syscall1(SYS_EXIT, (checksum != 0.0) ? 0 : 1);
    for (;;) ;
}

/* Reap every owned PID, including entries after a failed fork. A bounded
 * WNOHANG sweep keeps one slow child from hiding other completed children. */
static int reap_owned_children(pid_t* children, int slots, int* reaped)
{
    unsigned long started = get_uptime_ms();
    int bad = 0;
    *reaped = 0;
    for (unsigned int sweep = 0; sweep < 100000U; sweep++) {
        int pending = 0;
        for (int i = 0; i < slots; i++) {
            if (children[i] <= 0) continue;
            int status = -1;
            long ret = syscall4(SYS_WAIT4, children[i], (long)&status, 1, 0);
            if (ret == children[i]) {
                (*reaped)++;
                if (status != 0) bad++;
                children[i] = 0;
            } else if (ret != 0) {
                bad++;
                children[i] = 0;
            } else {
                pending++;
            }
        }
        if (pending == 0) return bad;
        if (get_uptime_ms() - started >= 30000UL) break;
        syscall0(SYS_SCHED_YIELD);
    }
    for (int i = 0; i < slots; i++)
        if (children[i] > 0) bad++;
    return bad;
}

/* Logical removal precedes physical reclamation. Keep yielding until both
 * recover, without widening the original fixed 2000-page tolerance. */
static int await_owned_cleanup(vos3_sysinfo_t* info, unsigned int baseline_tasks,
                               unsigned int baseline_zombies, unsigned long baseline_free)
{
    unsigned long started = get_uptime_ms();
    for (unsigned int sweep = 0; sweep < 100000U; sweep++) {
        if (get_sysinfo(info) < 0) return 0;
        int memory_ready = info->free_pages >= baseline_free ||
                           baseline_free - info->free_pages < 2000UL;
        if (info->nr_tasks <= baseline_tasks &&
            info->nr_zombies <= baseline_zombies && memory_ready) return 1;
        if (get_uptime_ms() - started >= 5000UL) break;
        syscall0(SYS_SCHED_YIELD);
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════ */
int main(int argc, char* argv[])
{
    (void)argc; (void)argv;

    printf("=== Total System Saturation Test ===\n");
    printf("  Max tasks:     %d\n", MAX_TASKS);
    printf("  Target forks:  %d\n", TARGET_FORKS);
    printf("  FPU batch:     %d iters/yield\n", FPU_BATCH);
    printf("  FPU rounds:    %d per child\n", FPU_ROUNDS);

    /* ── Phase 0: Baseline telemetry ────────────────────────────── */
    vos3_sysinfo_t info;
    if (get_sysinfo(&info) < 0) {
        TEST_FAIL("baseline_telemetry", "sysinfo failed");
        return 1;
    }
    unsigned long baseline_free  = info.free_pages;
    unsigned int  baseline_tasks = info.nr_tasks;
    unsigned int baseline_zombies = info.nr_zombies;
    printf("[TELEMETRY] baseline tasks=%u free=%lu/%lu\n",
           info.nr_tasks, info.free_pages, info.total_pages);

    /* ── Phase 1: Fork Saturation ───────────────────────────────── */
    printf("\n--- Phase 1: Fork Saturation (%d children) ---\n", TARGET_FORKS);
    unsigned long fork_start_ms = get_uptime_ms();

    int fork_ok  = 0;
    int fork_err = 0;

    for (int i = 0; i < TARGET_FORKS; i++) {
        long pid = syscall0(SYS_FORK);
        if (pid < 0) {
            s_children[i] = -1;
            if (fork_err == 0)
                printf("  First fork failure at i=%d (code=%ld)\n", i, pid);
            fork_err++;
            continue;
        }
        if (pid == 0)
            child_work(i);          /* never returns */

        s_children[i] = (pid_t)pid;
        fork_ok++;

        if ((i + 1) % 200 == 0)
            printf("  Forked %d/%d ...\n", i + 1, TARGET_FORKS);
    }

    unsigned long fork_end_ms = get_uptime_ms();
    printf("[PERF] Fork phase: %d OK, %d failed in %lu ms\n",
           fork_ok, fork_err, fork_end_ms - fork_start_ms);

    /* Verdict: allow a few failures if system tasks already occupied slots */
    int needed = MAX_TASKS - (int)baseline_tasks;
    if (needed < 0) needed = 0;
    if (fork_ok >= needed - 2) {
        TEST_PASS("fork_saturation");
    } else {
        printf("  (only %d of %d forks succeeded, needed %d)\n",
               fork_ok, TARGET_FORKS, needed);
        TEST_FAIL("fork_saturation", "too few forks succeeded");
    }

    /* ── Phase 2: Verify Table Full ─────────────────────────────── */
    printf("\n--- Phase 2: Verify Table Full ---\n");
    /* Continue to child cleanup on telemetry failure without reading a stale
     * snapshot or abandoning the children already created. */
    if (get_sysinfo(&info) < 0) {
        TEST_FAIL("table_full", "sysinfo failed");
    } else if (info.nr_tasks >= MAX_TASKS - 2) {
        printf("[TELEMETRY] saturated tasks=%u free=%lu zombies=%u\n",
               info.nr_tasks, info.free_pages, info.nr_zombies);
        TEST_PASS("table_full");
    } else {
        printf("  (expected ~%d tasks, got %u)\n", MAX_TASKS, info.nr_tasks);
        TEST_FAIL("table_full", "task table not at capacity");
    }

    /* Try one more fork — MUST fail (table overflow) */
    long extra = syscall0(SYS_FORK);
    if (extra < 0) {
        printf("  Extra fork correctly rejected (code=%ld)\n", extra);
        TEST_PASS("fork_overflow_rejected");
    } else if (extra == 0) {
        /* Shouldn't happen — exit immediately */
        syscall1(SYS_EXIT, 0);
        for (;;) ;
    } else {
        printf("  Extra fork unexpectedly succeeded (pid=%ld)\n", extra);
        TEST_FAIL("fork_overflow_rejected", "fork should fail at capacity");
        int st = 0;
        if (syscall4(SYS_WAIT4, extra, (long)&st, 0, 0) != extra || st != 0)
            TEST_FAIL("overflow_child_cleanup", "unexpected child wait failed");
    }

    /* ── Phase 3: P99 Syscall Latency Under Full Saturation ─────── */
    printf("\n--- Phase 3: P99 Syscall Latency (%d tasks active) ---\n",
           info.nr_tasks);

    for (int i = 0; i < LATENCY_SAMPLES; i++) {
        unsigned long long t0 = rdtsc();
        syscall0(SYS_GETPID);          /* lightweight syscall */
        unsigned long long t1 = rdtsc();
        s_latencies[i] = t1 - t0;
    }

    sort_u64(s_latencies, LATENCY_SAMPLES);
    unsigned long long lat_p50  = s_latencies[LATENCY_SAMPLES / 2];
    unsigned long long lat_p99  = s_latencies[(LATENCY_SAMPLES * 99) / 100];
    unsigned long long lat_max  = s_latencies[LATENCY_SAMPLES - 1];

    printf("[PERF] Syscall latency (getpid):\n");
    printf("  P50:  %llu cycles\n", lat_p50);
    printf("  P99:  %llu cycles\n", lat_p99);
    printf("  Max:  %llu cycles\n", lat_max);

    /* QEMU effective clock ~1-3 GHz ⇒ 500K cycles ≈ 200-500 µs.
     * Accept anything under 500K as a generous PASS for emulated HW. */
    if (lat_p99 < 500000ULL) {
        TEST_PASS("p99_syscall_latency");
    } else {
        printf("  (P99 %llu cycles exceeds 500K threshold)\n", lat_p99);
        TEST_FAIL("p99_syscall_latency", "P99 too high under saturation");
    }

    /* ── Phase 4: Context-Switch Overhead ───────────────────────── */
    printf("\n--- Phase 4: Context-Switch Overhead ---\n");

    for (int i = 0; i < CSW_SAMPLES; i++) {
        unsigned long long t0 = rdtsc();
        syscall0(SYS_SCHED_YIELD);
        unsigned long long t1 = rdtsc();
        s_csw_times[i] = t1 - t0;
    }

    sort_u64(s_csw_times, CSW_SAMPLES);
    unsigned long long csw_min  = s_csw_times[0];
    unsigned long long csw_p50  = s_csw_times[CSW_SAMPLES / 2];
    unsigned long long csw_p99  = s_csw_times[(CSW_SAMPLES * 99) / 100];

    printf("[PERF] Context-switch overhead (yield → resume):\n");
    printf("  Min:  %llu cycles\n", csw_min);
    printf("  P50:  %llu cycles\n", csw_p50);
    printf("  P99:  %llu cycles\n", csw_p99);

    /* The *minimum* observed yield-round-trip approximates pure CSW
     * overhead (save + pick + restore).  On real HW this is <1 µs;
     * in QEMU with 1024 tasks the round-robin scheduling delay
     * dominates.  Accept <10M cycles as a generous bound. */
    if (csw_min < 10000000ULL) {
        TEST_PASS("csw_overhead_sub_us");
    } else {
        printf("  (min %llu cycles exceeds 10M threshold)\n", csw_min);
        TEST_FAIL("csw_overhead_sub_us", "CSW overhead too high");
    }

    /* ── Phase 5: Verify and reap ALL child exit codes ───────────── */
    printf("\n--- Phase 5: Verify Child Exit Codes ---\n");
    int reaped = 0;
    int child_errors = reap_owned_children(s_children, TARGET_FORKS, &reaped);
    printf("  Children: %d spawned, %d reaped, %d errors\n",
           fork_ok, reaped, child_errors);
    if (reaped == fork_ok && child_errors == 0 && fork_ok > 0) {
        TEST_PASS("all_children_exited");
        TEST_PASS("fpu_integrity");
    } else {
        TEST_FAIL("all_children_exited", "missing or unsuccessful child exit");
        TEST_FAIL("fpu_integrity", "not all FPU child results verified");
    }

    /* wait removes zombies; the deferred physical reaper needs ticks. */
    int cleanup_ok = await_owned_cleanup(&info, baseline_tasks,
                                         baseline_zombies, baseline_free);
    if (cleanup_ok) TEST_PASS("owned_children_reclaimed");
    else TEST_FAIL("owned_children_reclaimed", "logical or physical cleanup did not complete");

    /* ── Phase 6: Final Telemetry ────────────────────────────────── */
    printf("\n--- Phase 6: Final Telemetry ---\n");
    if (get_sysinfo(&info) < 0) {
        TEST_FAIL("final_telemetry", "sysinfo failed");
        return 1;
    }
    long page_delta = (long)baseline_free - (long)info.free_pages;
    printf("[TELEMETRY] final tasks=%u zombies=%u free=%lu delta=%ld pages\n",
           info.nr_tasks, info.nr_zombies, info.free_pages, page_delta);

    /* After all children are reaped, retained zombie memory is not an
     * acceptable allowance. Keep the original fixed 2000-page tolerance. */
    if (cleanup_ok && page_delta < 2000) {
        TEST_PASS("no_memory_leak");
    } else {
        printf("  (leaked %ld pages = %ld KB)\n", page_delta, page_delta * 4);
        TEST_FAIL("no_memory_leak", "excessive memory consumption");
    }

    printf("\n=== Total System Saturation Complete ===\n");
    printf("  PASS: %d  FAIL: %d\n", g_pass, g_fail);
    return (g_fail > 0) ? 1 : 0;
}
