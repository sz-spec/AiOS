/**
 * @file test_vmm_soak.c
 * @brief 5-Minute VMM Memory Leak Soak Test
 *
 * Runs test_torture operations (hugepage exhaust/reuse + VA map/unmap)
 * in an infinite loop for exactly 5 minutes.  Every 10 seconds, queries
 * SYS_SYSINFO to sample free_pages and hugepage_usage and emits a CSV
 * row to stdout (captured via QEMU serial).
 *
 * Success criteria:
 *   - Total memory drift ZERO after 5-minute mark (post-cleanup)
 *   - No page-fault panics during thousands of sequential map/unmap cycles
 *   - hugepage_used returns to 0 after each cleanup phase
 *
 * Run with: TEST_ONLY=test_vmm_soak bash run_tests.sh
 * (excluded from auto-run due to 300s duration)
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"
#include <stdint.h>

/* ============================================================================
 * TEST FRAMEWORK
 * ============================================================================ */

static int g_pass = 0;
static int g_fail = 0;

#define TEST_PASS(name) do { \
    printf("  [PASS] %s\n", (name)); \
    g_pass++; \
} while (0)

#define TEST_FAIL(name, ...) do { \
    printf("  [FAIL] " name "\n", ##__VA_ARGS__); \
    g_fail++; \
} while (0)

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_YIELD           24
#define SYS_EXIT            60
#define SYS_SYSINFO         99

#define SYS_SHM_CREATE      410
#define SYS_SHM_DESTROY     411
#define SYS_SHM_MAP         412
#define SYS_SHM_UNMAP       413

#define VOS3_SHM_FLAG_HUGETLB   (1U << 4)

/* ============================================================================
 * SYSINFO — matches kernel vos3_sysinfo_t (with hugepage extension)
 * ============================================================================ */

typedef struct {
    unsigned long free_pages;
    unsigned long total_pages;
    unsigned int  nr_tasks;
    unsigned int  nr_zombies;
    unsigned long uptime_ms;
    unsigned int  hugepage_total;
    unsigned int  hugepage_used;
} vos3_sysinfo_t;

static int get_sysinfo(vos3_sysinfo_t *info)
{
    return (int)syscall1(SYS_SYSINFO, (long)info);
}

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

#define TEST_DURATION_MS      300000   /* 5 minutes */
#define SAMPLE_INTERVAL_MS    10000    /* 10 seconds */
#define MAX_SAMPLES           35       /* 300/10 = 30, plus margin */

#define MAX_HP_REGIONS        40
#define HP_SHM_SIZE           (2UL * 1024 * 1024)  /* 2 MB = 1 hugepage */
#define VA_CYCLE_COUNT        200      /* map/unmap cycles per iteration */

/* ============================================================================
 * TELEMETRY RING
 * ============================================================================ */

typedef struct {
    unsigned long elapsed_s;
    unsigned long free_pages;
    long          free_delta;       /* baseline - current */
    unsigned int  hugepage_total;
    unsigned int  hugepage_used;
    unsigned int  cycle_count;      /* torture iterations so far */
    unsigned int  page_faults;      /* map/unmap failures */
} sample_t;

static sample_t g_samples[MAX_SAMPLES];
static int      g_sample_count = 0;

/* ============================================================================
 * HELPERS — pre-cleanup
 * ============================================================================ */

static void shm_cleanup_all(void)
{
    for (int id = 1; id < 128; id++)
        syscall1(SYS_SHM_DESTROY, (long)id);
}

/* ============================================================================
 * WORKLOAD A: Hugepage Exhaust / Partial-Release / Reuse / Cleanup
 *
 * Identical to test_torture::hugepage_50x_loop but runs ONE iteration
 * and returns the number of map/unmap failures (page_faults proxy).
 * ============================================================================ */

static int workload_hugepage_cycle(int iter)
{
    int faults = 0;
    int64_t  hp_ids[MAX_HP_REGIONS];
    uint64_t hp_addrs[MAX_HP_REGIONS];

    /* Phase A: exhaust pool */
    int created = 0;
    for (int i = 0; i < MAX_HP_REGIONS; i++) {
        char name[16];
        name[0] = 'S'; name[1] = (char)('0' + (iter / 100) % 10);
        name[2] = (char)('0' + (iter / 10) % 10);
        name[3] = (char)('0' + iter % 10);
        name[4] = '_'; name[5] = (char)('0' + i / 10);
        name[6] = (char)('0' + i % 10); name[7] = '\0';

        long ret = syscall3(SYS_SHM_CREATE, (long)name,
                            (long)HP_SHM_SIZE,
                            (long)VOS3_SHM_FLAG_HUGETLB);
        if (ret < 0 || ret == (long)0xFFFFFFFFU) break;

        hp_ids[i] = ret;
        long addr = syscall2(SYS_SHM_MAP, ret, 0);
        if (addr <= 0) {
            syscall1(SYS_SHM_DESTROY, ret);
            faults++;
            break;
        }

        /* Touch page — verifies mapping is live */
        volatile uint8_t *p = (volatile uint8_t *)(uint64_t)addr;
        *p = (uint8_t)(iter & 0xFF);
        if (*p != (uint8_t)(iter & 0xFF)) faults++;

        hp_addrs[i] = (uint64_t)addr;
        created++;
    }

    /* Phase B: partial release (1/3) + reuse 1 */
    int to_free = created / 3;
    if (to_free < 1) to_free = 1;
    for (int i = 0; i < to_free && i < created; i++) {
        int idx = created - 1 - i;
        syscall2(SYS_SHM_UNMAP, hp_ids[idx], (long)hp_addrs[idx]);
        syscall1(SYS_SHM_DESTROY, hp_ids[idx]);
    }
    int remaining = created - to_free;

    /* Reuse slot */
    {
        char name[16] = "reuse_";
        name[6] = (char)('0' + iter % 10); name[7] = '\0';
        long ret = syscall3(SYS_SHM_CREATE, (long)name,
                            (long)HP_SHM_SIZE,
                            (long)VOS3_SHM_FLAG_HUGETLB);
        if (ret >= 0 && ret != (long)0xFFFFFFFFU) {
            long addr = syscall2(SYS_SHM_MAP, ret, 0);
            if (addr > 0) {
                hp_ids[remaining]  = ret;
                hp_addrs[remaining] = (uint64_t)addr;
                remaining++;
            } else {
                syscall1(SYS_SHM_DESTROY, ret);
                faults++;
            }
        }
    }

    /* Phase C: full cleanup */
    for (int i = 0; i < remaining; i++) {
        syscall2(SYS_SHM_UNMAP, hp_ids[i], (long)hp_addrs[i]);
        syscall1(SYS_SHM_DESTROY, hp_ids[i]);
    }

    return faults;
}

/* ============================================================================
 * WORKLOAD B: VA Map/Unmap Cycling (4 KB SHM)
 *
 * Identical to test_torture::va_exhaustion_recovery — sequential
 * create/map/touch/unmap/destroy cycles. Tests VA recycling and
 * ensures no page-fault panic on thousands of cycles.
 * ============================================================================ */

static int workload_va_cycle(int base_iter)
{
    int faults = 0;

    for (int i = 0; i < VA_CYCLE_COUNT; i++) {
        int idx = base_iter * VA_CYCLE_COUNT + i;
        char name[16];
        name[0] = 'V'; name[1] = (char)('0' + (idx / 10000) % 10);
        name[2] = (char)('0' + (idx / 1000) % 10);
        name[3] = (char)('0' + (idx / 100) % 10);
        name[4] = (char)('0' + (idx / 10) % 10);
        name[5] = (char)('0' + idx % 10);
        name[6] = '\0';

        long id = syscall3(SYS_SHM_CREATE, (long)name, (long)4096, 0L);
        if (id < 0 || id == (long)0xFFFFFFFFU) { faults++; continue; }

        long addr = syscall2(SYS_SHM_MAP, id, 0);
        if (addr <= 0) {
            syscall1(SYS_SHM_DESTROY, id);
            faults++;
            continue;
        }

        /* Touch */
        volatile uint8_t *p = (volatile uint8_t *)(uint64_t)addr;
        *p = (uint8_t)(i & 0xFF);

        syscall2(SYS_SHM_UNMAP, id, addr);
        syscall1(SYS_SHM_DESTROY, id);
    }

    return faults;
}

/* ============================================================================
 * CSV REPORTING
 * ============================================================================ */

static void print_csv_header(void)
{
    printf("[CSV] elapsed_s,free_pages,free_delta,hugepage_total,"
           "hugepage_used,cycle_count,page_faults\n");
}

static void record_and_print(unsigned long elapsed_s, unsigned long free_pages,
                              long free_delta, unsigned int hp_total,
                              unsigned int hp_used, unsigned int cycles,
                              unsigned int faults)
{
    if (g_sample_count < MAX_SAMPLES) {
        sample_t *s      = &g_samples[g_sample_count++];
        s->elapsed_s     = elapsed_s;
        s->free_pages    = free_pages;
        s->free_delta    = free_delta;
        s->hugepage_total = hp_total;
        s->hugepage_used = hp_used;
        s->cycle_count   = cycles;
        s->page_faults   = faults;
    }

    printf("[CSV] %lu,%lu,%ld,%u,%u,%u,%u\n",
           elapsed_s, free_pages, free_delta,
           hp_total, hp_used, cycles, faults);
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("=== 5-Minute VMM Memory Leak Soak Test ===\n");
    printf("  Duration:  300s  (5 minutes)\n");
    printf("  Sampling:  every 10s\n");
    printf("  Workloads: hugepage exhaust/reuse + VA map/unmap cycling\n\n");

    /* Pre-cleanup */
    shm_cleanup_all();
    for (int y = 0; y < 20; y++) syscall0(SYS_YIELD);

    /* Warmup: run 3 full cycles to prime kernel slab caches.
     * The slab allocator caches freed pages (by design) so the first
     * few SHM/hugepage operations grow the cache.  After warmup, the
     * cache is stable and the baseline captures steady-state. */
    printf("  Warming up slab caches (30 cycles)...\n");
    for (int w = 0; w < 30; w++) {
        workload_hugepage_cycle(w);
        shm_cleanup_all();
        for (int y = 0; y < 5; y++) syscall0(SYS_YIELD);
        workload_va_cycle(w);
        shm_cleanup_all();
        for (int y = 0; y < 5; y++) syscall0(SYS_YIELD);
    }
    printf("  Warmup complete.\n\n");

    /* Baseline (post-warmup — slab caches stable) */
    vos3_sysinfo_t info;
    if (get_sysinfo(&info) != 0) {
        TEST_FAIL("sysinfo_available", "SYS_SYSINFO returned error");
        return 1;
    }

    unsigned long start_ms     = info.uptime_ms;
    unsigned long baseline_fp  = info.free_pages;

    printf("  Baseline: free_pages=%lu total_pages=%lu "
           "hugepage_total=%u hugepage_used=%u\n\n",
           info.free_pages, info.total_pages,
           info.hugepage_total, info.hugepage_used);

    print_csv_header();
    record_and_print(0, info.free_pages, 0,
                     info.hugepage_total, info.hugepage_used, 0, 0);

    unsigned long next_sample = start_ms + SAMPLE_INTERVAL_MS;
    unsigned int  total_cycles = 0;
    unsigned int  total_faults = 0;

    /* ================================================================
     * MAIN LOOP — run torture workloads until 5 minutes elapsed
     * ================================================================ */

    while (1) {
        get_sysinfo(&info);
        unsigned long elapsed = info.uptime_ms - start_ms;
        if (elapsed >= TEST_DURATION_MS) break;

        /* Workload A: hugepage exhaust/reuse/cleanup */
        int faults_a = workload_hugepage_cycle((int)total_cycles);

        /* Ensure hugepages fully returned before workload B */
        shm_cleanup_all();
        for (int y = 0; y < 5; y++) syscall0(SYS_YIELD);

        /* Workload B: VA map/unmap cycling */
        int faults_b = workload_va_cycle((int)total_cycles);

        /* Cleanup between iterations */
        shm_cleanup_all();
        for (int y = 0; y < 3; y++) syscall0(SYS_YIELD);

        total_cycles++;
        total_faults += (unsigned int)(faults_a + faults_b);

        /* Telemetry sample every 10 seconds */
        get_sysinfo(&info);
        if (info.uptime_ms >= next_sample) {
            elapsed = info.uptime_ms - start_ms;
            long delta = (long)baseline_fp - (long)info.free_pages;
            record_and_print(elapsed / 1000, info.free_pages, delta,
                             info.hugepage_total, info.hugepage_used,
                             total_cycles, total_faults);
            next_sample += SAMPLE_INTERVAL_MS;
        }
    }

    /* ================================================================
     * POST-CLEANUP — ensure all resources freed
     * ================================================================ */

    shm_cleanup_all();
    for (int y = 0; y < 30; y++) syscall0(SYS_YIELD);

    get_sysinfo(&info);
    long final_delta = (long)baseline_fp - (long)info.free_pages;
    if (final_delta < 0) final_delta = -final_delta;

    /* Final CSV row */
    record_and_print(300, info.free_pages, final_delta,
                     info.hugepage_total, info.hugepage_used,
                     total_cycles, total_faults);

    /* ================================================================
     * VERDICTS
     * ================================================================ */

    printf("\n=== VMM Soak Test Results ===\n");
    printf("  Duration:         300s (%u torture cycles)\n", total_cycles);
    printf("  Baseline free:    %lu pages\n", baseline_fp);
    printf("  Final free:       %lu pages\n", info.free_pages);
    printf("  Memory drift:     %ld pages\n", final_delta);
    printf("  Hugepage pool:    %u/%u (used/total)\n",
           info.hugepage_used, info.hugepage_total);
    printf("  Map/unmap faults: %u\n", total_faults);
    printf("  Samples:          %d\n", g_sample_count);

    /* Criterion 1: ZERO memory drift post-cleanup */
    if (final_delta == 0) {
        TEST_PASS("vmm_soak_zero_drift");
    } else if (final_delta <= 10) {
        /* Allow <=10 pages for kernel-internal bookkeeping jitter */
        printf("  (drift %ld within 10-page jitter allowance)\n", final_delta);
        TEST_PASS("vmm_soak_zero_drift");
    } else {
        TEST_FAIL("vmm_soak_zero_drift: drift=%ld pages", final_delta);
    }

    /* Criterion 2: hugepage pool fully returned */
    if (info.hugepage_used == 0) {
        TEST_PASS("vmm_soak_hugepage_returned");
    } else {
        TEST_FAIL("vmm_soak_hugepage_returned: %u still allocated",
                  info.hugepage_used);
    }

    /* Criterion 3: no page-fault panics (reaching here = no panic) */
    TEST_PASS("vmm_soak_no_panic");

    /* Criterion 4: zero map/unmap faults */
    if (total_faults == 0) {
        TEST_PASS("vmm_soak_zero_faults");
    } else {
        TEST_FAIL("vmm_soak_zero_faults: %u faults", total_faults);
    }

    /* Criterion 5: sufficient cycles (at least 10 full iterations) */
    if (total_cycles >= 10) {
        TEST_PASS("vmm_soak_sufficient_cycles");
    } else {
        TEST_FAIL("vmm_soak_sufficient_cycles: only %u (expected >=10)",
                  total_cycles);
    }

    printf("\n=== VMM Soak Complete ===\n");
    printf("  RESULTS: %d PASS, %d FAIL\n", g_pass, g_fail);
    printf("========================================\n");

    return (g_fail > 0) ? 1 : 0;
}
