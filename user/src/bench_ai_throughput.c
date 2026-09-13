/**
 * @file bench_ai_throughput.c
 * @brief AI Agent SHM Throughput Benchmark
 *
 * Spawns 8 agents communicating via shared memory.
 * Each agent writes to its designated slice of a large SHM segment.
 * Measures aggregate MB/s throughput across all agents.
 *
 * Uses RDTSC for cycle-accurate timing and SYS_SYSINFO for telemetry.
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* Syscall numbers */
#define SYS_READ        0
#define SYS_WRITE       1
#define SYS_FORK        57
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_GETTIME     40
#define SYS_SYSINFO     99
#define SYS_SHM_CREATE  410
#define SYS_SHM_DESTROY 411
#define SYS_SHM_MAP     412
#define VOS3_SHM_FLAG_PUBLIC (1U << 6)
#define SYS_SHM_UNMAP   413

/* Configuration */
#define NUM_AGENTS      8
#define SHM_SIZE        (4 * 1024 * 1024)  /* 4MB per SHM segment */
#define SLICE_SIZE      (SHM_SIZE / NUM_AGENTS)  /* 512KB per agent */
#define NUM_PASSES      8   /* 8 agents * 4MB * 8 passes = 256MB */
#define TOTAL_DATA_MB   ((unsigned long)(NUM_AGENTS) * SHM_SIZE / (1024*1024) * NUM_PASSES)

/* Test framework */
static int g_pass = 0;
static int g_fail = 0;
#define TEST_PASS(name) do { printf("[PASS] %s\n", name); g_pass++; } while(0)
#define TEST_FAIL(name, msg) do { printf("[FAIL] %s: %s\n", name, msg); g_fail++; } while(0)

/* Sysinfo struct */
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

static unsigned long get_uptime_ms(void)
{
    return (unsigned long)syscall0(SYS_GETTIME);
}

static int get_sysinfo(vos3_sysinfo_t* info)
{
    return (int)syscall1(SYS_SYSINFO, (long)info);
}

/**
 * Agent workload: write a pattern to its designated SHM slice.
 * Each pass writes SLICE_SIZE bytes with a unique pattern.
 */
static void agent_work(volatile unsigned char* base, int agent_id, int pass)
{
    volatile unsigned char* slice = base + (agent_id * SLICE_SIZE);
    unsigned char pattern = (unsigned char)((agent_id * 37 + pass * 13) & 0xFF);

    /* Write pattern to entire slice (200KB) */
    for (unsigned long i = 0; i < SLICE_SIZE; i += 8) {
        /* Write 8 bytes at a time for better throughput */
        volatile unsigned long long* p = (volatile unsigned long long*)(slice + i);
        unsigned long long val = pattern;
        val |= (val << 8) | (val << 16) | (val << 24);
        val |= (val << 32);
        *p = val;
    }
}

/**
 * Verify agent's data after all writes complete.
 */
static int verify_agent_data(volatile unsigned char* base, int agent_id, int pass)
{
    volatile unsigned char* slice = base + (agent_id * SLICE_SIZE);
    unsigned char expected = (unsigned char)((agent_id * 37 + pass * 13) & 0xFF);

    for (unsigned long i = 0; i < SLICE_SIZE; i += 64) {
        if (slice[i] != expected)
            return -1;
    }
    return 0;
}

int main(int argc, char* argv[])
{
    (void)argc; (void)argv;

    printf("=== AI Agent SHM Throughput Benchmark ===\n");
    printf("  Agents:     %d\n", NUM_AGENTS);
    printf("  SHM Size:   %d MB\n", SHM_SIZE / (1024*1024));
    printf("  Passes:     %d\n", NUM_PASSES);
    printf("  Total Data: ~%lu MB\n", TOTAL_DATA_MB);

    /* Baseline telemetry */
    vos3_sysinfo_t info;
    get_sysinfo(&info);
    unsigned long baseline_free = info.free_pages;
    printf("[TELEMETRY] baseline free=%lu total=%lu\n",
           info.free_pages, info.total_pages);

    /* Create SHM segment */
    long shm_id = syscall3(SYS_SHM_CREATE, (long)"ai_bench", SHM_SIZE,
                           (long)VOS3_SHM_FLAG_PUBLIC);
    if (shm_id < 0) {
        TEST_FAIL("shm_create", "failed to create SHM segment");
        return 1;
    }

    /* Map in parent */
    long shm_addr = syscall2(SYS_SHM_MAP, shm_id, 0);
    if (shm_addr <= 0 || shm_addr < 0x10000) {
        TEST_FAIL("shm_map", "failed to map SHM segment");
        syscall1(SYS_SHM_DESTROY, shm_id);
        return 1;
    }
    TEST_PASS("shm_setup");

    volatile unsigned char* shm_base = (volatile unsigned char*)shm_addr;

    /* Phase 1: Sequential throughput baseline (single-agent) */
    unsigned long long t_seq_start = rdtsc();
    unsigned long ms_seq_start = get_uptime_ms();

    for (int pass = 0; pass < NUM_PASSES; pass++) {
        for (unsigned long i = 0; i < SHM_SIZE; i += 8) {
            volatile unsigned long long* p = (volatile unsigned long long*)(shm_base + i);
            *p = 0xDEADBEEFCAFEBABEULL;
        }
    }

    unsigned long long t_seq_end = rdtsc();
    unsigned long ms_seq_end = get_uptime_ms();
    unsigned long seq_elapsed = ms_seq_end - ms_seq_start;
    unsigned long seq_data_mb = (unsigned long)SHM_SIZE / (1024*1024) * NUM_PASSES;

    if (seq_elapsed > 0) {
        unsigned long seq_mbps = (seq_data_mb * 1000) / seq_elapsed;
        printf("[PERF] Sequential:  %lu MB in %lu ms = %lu MB/s\n",
               seq_data_mb, seq_elapsed, seq_mbps);
        printf("[PERF] Sequential:  %llu cycles total, %llu cycles/MB\n",
               t_seq_end - t_seq_start,
               (t_seq_end - t_seq_start) / seq_data_mb);
    } else {
        printf("[PERF] Sequential:  %lu MB in <1 ms (too fast to measure)\n", seq_data_mb);
    }

    /* Phase 2: Concurrent throughput (20 agents) */
    unsigned long long t_conc_start = rdtsc();
    unsigned long ms_conc_start = get_uptime_ms();
    int errors = 0;

    for (int pass = 0; pass < NUM_PASSES; pass++) {
        /* Fork all agents */
        pid_t children[NUM_AGENTS];
        int fork_ok = 1;

        for (int a = 0; a < NUM_AGENTS; a++) {
            long pid = syscall0(SYS_FORK);
            if (pid < 0) {
                errors++;
                children[a] = -1;
                fork_ok = 0;
                continue;
            }
            if (pid == 0) {
                /* Child: map SHM, write to slice, exit */
                long caddr = syscall2(SYS_SHM_MAP, shm_id, 0);
                if (caddr > 0x10000) {
                    agent_work((volatile unsigned char*)caddr, a, pass);
                    syscall2(SYS_SHM_UNMAP, shm_id, caddr);
                }
                syscall1(SYS_EXIT, 0);
                for(;;);
            }
            children[a] = (pid_t)pid;
        }

        /* Wait for all agents */
        for (int a = 0; a < NUM_AGENTS; a++) {
            if (children[a] > 0) {
                int status = 0;
                syscall4(SYS_WAIT4, children[a], (long)&status, 0, 0);
            }
        }

        /* Verify last pass data integrity.
         * Re-map SHM to get fresh PTEs (fork marks parent's PTEs COW,
         * which may cause stale reads of pre-fork data instead of
         * children's writes to the real SHM physical pages). */
        if (pass == NUM_PASSES - 1) {
            syscall2(SYS_SHM_UNMAP, shm_id, (long)shm_base);
            long verify_addr = syscall2(SYS_SHM_MAP, shm_id, 0);
            volatile unsigned char* verify_base = (verify_addr > 0x10000)
                ? (volatile unsigned char*)verify_addr : shm_base;
            for (int a = 0; a < NUM_AGENTS; a++) {
                if (verify_agent_data(verify_base, a, pass) < 0) {
                    errors++;
                }
            }
            shm_base = verify_base;
            shm_addr = (long)verify_base;
        }
    }

    unsigned long long t_conc_end = rdtsc();
    unsigned long ms_conc_end = get_uptime_ms();
    unsigned long conc_elapsed = ms_conc_end - ms_conc_start;

    printf("[PERF] Concurrent: %lu MB in %lu ms",
           TOTAL_DATA_MB, conc_elapsed);
    if (conc_elapsed > 0) {
        unsigned long conc_mbps = (TOTAL_DATA_MB * 1000) / conc_elapsed;
        printf(" = %lu MB/s", conc_mbps);
    }
    printf("\n");
    printf("[PERF] Concurrent: %llu total cycles, %llu cycles/MB\n",
           t_conc_end - t_conc_start,
           TOTAL_DATA_MB > 0 ? (t_conc_end - t_conc_start) / TOTAL_DATA_MB : 0);

    /* Phase 3: Read-back verification throughput */
    unsigned long long t_read_start = rdtsc();
    unsigned long ms_read_start = get_uptime_ms();

    volatile unsigned long long chk = 0;
    for (unsigned long i = 0; i < SHM_SIZE; i += 8) {
        chk += *(volatile unsigned long long*)(shm_base + i);
    }

    unsigned long long t_read_end = rdtsc();
    unsigned long ms_read_end = get_uptime_ms();
    unsigned long read_elapsed = ms_read_end - ms_read_start;
    unsigned long read_mb = SHM_SIZE / (1024*1024);

    printf("[PERF] Read-back:  %lu MB in %lu ms", read_mb, read_elapsed);
    if (read_elapsed > 0) {
        printf(" = %lu MB/s", (read_mb * 1000) / read_elapsed);
    }
    printf(" (checksum=0x%llx)\n", chk);

    /* Final telemetry */
    get_sysinfo(&info);
    long page_delta = (long)baseline_free - (long)info.free_pages;
    printf("[TELEMETRY] final free=%lu delta=%ld pages tasks=%u zombies=%u\n",
           info.free_pages, page_delta, info.nr_tasks, info.nr_zombies);

    /* Cleanup */
    syscall2(SYS_SHM_UNMAP, shm_id, shm_addr);
    syscall1(SYS_SHM_DESTROY, shm_id);

    /* Verdicts */
    if (errors == 0) {
        TEST_PASS("concurrent_throughput");
    } else {
        TEST_FAIL("concurrent_throughput", "data errors detected");
    }

    if (info.nr_zombies <= 2) {
        TEST_PASS("no_zombie_leak");
    } else {
        TEST_FAIL("no_zombie_leak", "zombie accumulation");
    }

    get_sysinfo(&info);
    long final_delta = (long)baseline_free - (long)info.free_pages;
    if (final_delta < 500) {  /* allow 500 pages (~2MB) for fork+SHM overhead */
        TEST_PASS("no_memory_leak");
    } else {
        printf("  (leaked %ld pages)\n", final_delta);
        TEST_FAIL("no_memory_leak", "excessive page consumption");
    }

    printf("\n=== Throughput Benchmark Complete ===\n");
    return (g_fail > 0) ? 1 : 0;
}
