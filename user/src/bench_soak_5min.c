/**
 * @file bench_soak_5min.c
 * @brief 5-Minute Soak Test — Chaos Level 2
 *
 * Runs mixed workload (fork+fileIO+mmap+SHM) for 5 minutes,
 * printing [TELEMETRY] every 30s. Tracks slow memory leaks,
 * internal fragmentation, and zombie accumulation over
 * thousands of task cycles.
 *
 * Uses SYS_SYSINFO (99) for kernel telemetry.
 * Run with: TEST_ONLY=bench_soak_5min bash run_tests.sh
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* Syscall numbers */
#define SYS_READ        0
#define SYS_WRITE       1
#define SYS_OPEN        2
#define SYS_CLOSE       3
#define SYS_MMAP        9
#define SYS_MUNMAP      11
#define SYS_FORK        57
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_GETTIME     40
#define SYS_MKDIR       83
#define SYS_UNLINK      87
#define SYS_SYSINFO     99
#define SYS_SHM_CREATE  410
#define SYS_SHM_DESTROY 411
#define SYS_SHM_MAP     412
#define SYS_SHM_UNMAP   413

/* Open flags */
#define O_RDONLY        0
#define O_WRONLY        1
#define O_CREAT         0x40
#define O_TRUNC         0x200

/* mmap flags */
#define PROT_READ       1
#define PROT_WRITE      2
#define MAP_PRIVATE     0x02
#define MAP_ANONYMOUS   0x20

/* Duration: 5 minutes = 300,000 ms */
#define TEST_DURATION_MS    300000
#define TELEMETRY_INTERVAL  30000   /* every 30 seconds */
#define MAX_TELEMETRY_PTS   12      /* 300s / 30s = 10, plus start + final = 12 */

/* Test framework */
static int g_pass = 0;
static int g_fail = 0;
#define TEST_PASS(name) do { printf("[PASS] %s\n", name); g_pass++; } while(0)
#define TEST_FAIL(name, msg) do { printf("[FAIL] %s: %s\n", name, msg); g_fail++; } while(0)

typedef struct {
    unsigned long free_pages;
    unsigned long total_pages;
    unsigned int  nr_tasks;
    unsigned int  nr_zombies;
    unsigned long uptime_ms;
} vos3_sysinfo_t;

/* Telemetry history for leak rate analysis */
typedef struct {
    unsigned long timestamp_s;
    unsigned long free_pages;
    unsigned int  nr_zombies;
    int           cumulative_errors;
} telemetry_pt_t;

static telemetry_pt_t g_telemetry[MAX_TELEMETRY_PTS];
static int g_telem_count = 0;

static int get_sysinfo(vos3_sysinfo_t* info)
{
    return (int)syscall1(SYS_SYSINFO, (long)info);
}

static unsigned long get_uptime_ms(void)
{
    return (unsigned long)syscall0(SYS_GETTIME);
}

static void record_telemetry(unsigned long t_sec, unsigned long fp,
                              unsigned int zomb, int errs)
{
    if (g_telem_count < MAX_TELEMETRY_PTS) {
        g_telemetry[g_telem_count].timestamp_s = t_sec;
        g_telemetry[g_telem_count].free_pages = fp;
        g_telemetry[g_telem_count].nr_zombies = zomb;
        g_telemetry[g_telem_count].cumulative_errors = errs;
        g_telem_count++;
    }
}

/* ============================================================================
 * WORKLOADS
 * ============================================================================ */

static int workload_fork_churn(int iterations)
{
    int errors = 0;
    for (int i = 0; i < iterations; i++) {
        long pid = syscall0(SYS_FORK);
        if (pid < 0) { errors++; continue; }
        if (pid == 0) {
            syscall1(SYS_EXIT, 0);
            for(;;);
        }
        int status = 0;
        long ret = syscall4(SYS_WAIT4, pid, (long)&status, 0, 0);
        if (ret < 0) errors++;
    }
    return errors;
}

static int workload_file_io(int iterations, int batch_id)
{
    int errors = 0;
    char path[64];
    char wbuf[128];
    char rbuf[128];

    for (int i = 0; i < iterations; i++) {
        /* Build unique path: /tmp/sk<batch>_<iter> */
        int pos = 0;
        path[pos++] = '/'; path[pos++] = 't'; path[pos++] = 'm'; path[pos++] = 'p';
        path[pos++] = '/'; path[pos++] = 's'; path[pos++] = 'k';
        /* batch id */
        int n = batch_id;
        char digits[10]; int dpos = 0;
        if (n == 0) { digits[dpos++] = '0'; }
        else { while (n > 0 && dpos < 10) { digits[dpos++] = '0' + (n % 10); n /= 10; } }
        while (dpos > 0) path[pos++] = digits[--dpos];
        path[pos++] = '_';
        /* iter */
        n = i; dpos = 0;
        if (n == 0) { digits[dpos++] = '0'; }
        else { while (n > 0 && dpos < 10) { digits[dpos++] = '0' + (n % 10); n /= 10; } }
        while (dpos > 0) path[pos++] = digits[--dpos];
        path[pos] = '\0';

        for (int j = 0; j < 128; j++) wbuf[j] = (char)('A' + (j % 26));

        long fd = syscall3(SYS_OPEN, (long)path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) { errors++; continue; }
        long wr = syscall3(SYS_WRITE, fd, (long)wbuf, 128);
        if (wr != 128) errors++;
        syscall1(SYS_CLOSE, fd);

        fd = syscall3(SYS_OPEN, (long)path, O_RDONLY, 0);
        if (fd < 0) { errors++; continue; }
        long rd = syscall3(SYS_READ, fd, (long)rbuf, 128);
        if (rd != 128) errors++;
        syscall1(SYS_CLOSE, fd);

        for (int j = 0; j < 128 && rd == 128; j++) {
            if (rbuf[j] != wbuf[j]) { errors++; break; }
        }

        syscall1(SYS_UNLINK, (long)path);
    }
    return errors;
}

static int workload_mmap(int iterations)
{
    int errors = 0;
    for (int i = 0; i < iterations; i++) {
        long addr = syscall6(SYS_MMAP, 0, 4096,
                            PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (addr < 0 || addr < 0x10000) { errors++; continue; }
        volatile char* p = (volatile char*)addr;
        *p = 0x42;
        if (*p != 0x42) errors++;
        long ret = syscall2(SYS_MUNMAP, addr, 4096);
        if (ret < 0) errors++;
    }
    return errors;
}

static int workload_shm_cycle(int batch_id)
{
    int errors = 0;
    /* Create small SHM, map, write, verify, unmap, destroy */
    long id = syscall3(SYS_SHM_CREATE, (long)"soak_shm", 4096, 0);
    if (id < 0) return 1;

    long addr = syscall2(SYS_SHM_MAP, id, 0);
    if (addr <= 0 || addr < 0x10000) {
        syscall1(SYS_SHM_DESTROY, id);
        return 1;
    }

    volatile unsigned int* p = (volatile unsigned int*)addr;
    *p = (unsigned int)(0xCAFE0000 | batch_id);
    if (*p != (unsigned int)(0xCAFE0000 | batch_id)) errors++;

    syscall2(SYS_SHM_UNMAP, id, addr);
    syscall1(SYS_SHM_DESTROY, id);
    return errors;
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char* argv[])
{
    (void)argc; (void)argv;

    printf("=== 5-Minute Soak Test (Chaos Level 2) ===\n");
    printf("  Duration:  300s (5 minutes)\n");
    printf("  Telemetry: every 30s\n");
    printf("  Workloads: fork+fileIO+mmap+SHM per iteration\n");

    syscall2(SYS_MKDIR, (long)"/tmp", 0755);

    vos3_sysinfo_t info;
    if (get_sysinfo(&info) != 0) {
        TEST_FAIL("sysinfo_available", "SYS_SYSINFO returned error");
        return 1;
    }

    unsigned long start_ms    = info.uptime_ms;
    unsigned long baseline_fp = info.free_pages;

    printf("[TELEMETRY] T=0 free=%lu total=%lu tasks=%u zombies=%u\n",
           info.free_pages, info.total_pages, info.nr_tasks, info.nr_zombies);
    record_telemetry(0, info.free_pages, info.nr_zombies, 0);

    unsigned long next_telemetry = start_ms + TELEMETRY_INTERVAL;
    int total_errors = 0;
    int iteration = 0;

    while (1) {
        unsigned long now = get_uptime_ms();
        unsigned long elapsed = now - start_ms;
        if (elapsed >= TEST_DURATION_MS) break;

        /* Mixed workload batch */
        int errs = 0;
        errs += workload_fork_churn(5);
        errs += workload_file_io(10, iteration);
        errs += workload_mmap(10);
        errs += workload_shm_cycle(iteration);
        total_errors += errs;
        iteration++;

        /* Telemetry check */
        now = get_uptime_ms();
        if (now >= next_telemetry) {
            elapsed = now - start_ms;
            unsigned long t_sec = elapsed / 1000;
            if (get_sysinfo(&info) == 0) {
                long delta = (long)baseline_fp - (long)info.free_pages;
                printf("[TELEMETRY] T=%lu free=%lu delta=%ld tasks=%u zombies=%u errors=%d\n",
                       t_sec, info.free_pages, delta,
                       info.nr_tasks, info.nr_zombies, total_errors);
                record_telemetry(t_sec, info.free_pages, info.nr_zombies, total_errors);
            }
            next_telemetry += TELEMETRY_INTERVAL;
        }
    }

    /* Final telemetry */
    if (get_sysinfo(&info) == 0) {
        long delta = (long)baseline_fp - (long)info.free_pages;
        printf("[TELEMETRY] T=final free=%lu delta=%ld tasks=%u zombies=%u errors=%d\n",
               info.free_pages, delta, info.nr_tasks, info.nr_zombies, total_errors);
        record_telemetry(999, info.free_pages, info.nr_zombies, total_errors);
    }

    /* ============================================================================
     * ANALYSIS
     * ============================================================================ */

    unsigned long final_fp = info.free_pages;
    long fp_delta = (long)baseline_fp - (long)final_fp;
    unsigned long pct_x100 = 0;
    if (baseline_fp > 0 && fp_delta > 0)
        pct_x100 = ((unsigned long)fp_delta * 10000) / baseline_fp;

    /* Leak rate analysis: check if memory loss is accelerating */
    int leak_accelerating = 0;
    if (g_telem_count >= 4) {
        /* Compare first-half delta vs second-half delta */
        int mid = g_telem_count / 2;
        long first_half = (long)g_telemetry[0].free_pages - (long)g_telemetry[mid].free_pages;
        long second_half = (long)g_telemetry[mid].free_pages - (long)g_telemetry[g_telem_count-1].free_pages;
        if (second_half > first_half * 2 && second_half > 100)
            leak_accelerating = 1;
    }

    /* Max zombie seen */
    unsigned int max_zombies = 0;
    for (int i = 0; i < g_telem_count; i++) {
        if (g_telemetry[i].nr_zombies > max_zombies)
            max_zombies = g_telemetry[i].nr_zombies;
    }

    printf("\n=== 5-Minute Soak Results ===\n");
    printf("  Duration:        300s (%d iterations)\n", iteration);
    printf("  Baseline free:   %lu pages\n", baseline_fp);
    printf("  Final free:      %lu pages\n", final_fp);
    printf("  Delta:           %ld pages (%lu.%02lu%%)\n",
           fp_delta, pct_x100 / 100, pct_x100 % 100);
    printf("  Max zombies:     %u\n", max_zombies);
    printf("  Total errors:    %d\n", total_errors);
    printf("  Telemetry pts:   %d\n", g_telem_count);
    printf("  Leak accel:      %s\n", leak_accelerating ? "YES (WARNING)" : "no");
    printf("  Iterations/sec:  %d\n", iteration / 300);

    /* Verdicts */
    if (pct_x100 > 200) {  /* > 2% over 5 minutes */
        TEST_FAIL("soak_memory_stable", "free pages dropped >2% over 5min");
    } else {
        TEST_PASS("soak_memory_stable");
    }

    if (leak_accelerating) {
        TEST_FAIL("soak_no_accelerating_leak", "memory loss accelerating in second half");
    } else {
        TEST_PASS("soak_no_accelerating_leak");
    }

    if (max_zombies > 5) {
        TEST_FAIL("soak_zombie_bounded", "peak zombie count >5");
    } else {
        TEST_PASS("soak_zombie_bounded");
    }

    if (total_errors > 0) {
        TEST_FAIL("soak_zero_errors", "workload errors detected");
    } else {
        TEST_PASS("soak_zero_errors");
    }

    if (g_telem_count < 8) {
        TEST_FAIL("soak_telemetry_coverage", "expected 8+ telemetry points");
    } else {
        TEST_PASS("soak_telemetry_coverage");
    }

    printf("\n");
    if (g_fail == 0) {
        printf("[SOAK] VERDICT: STABLE — no slow leaks or fragmentation\n");
    } else {
        printf("[SOAK] VERDICT: ISSUES DETECTED — review telemetry\n");
    }

    return (g_fail > 0) ? 1 : 0;
}
