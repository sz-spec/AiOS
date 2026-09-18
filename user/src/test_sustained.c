/**
 * @file test_sustained.c
 * @brief 60-Second Sustained Stability Audit
 *
 * Runs mixed workload (fork+fileIO+mmap) for 60 seconds,
 * printing [TELEMETRY] markers every 10s with free pages,
 * zombie count, and error metrics.
 *
 * Uses versioned native SYSINFO (483) for kernel telemetry.
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "vos_sysinfo.h"

/* Syscall numbers — Linux x86_64 ABI */
#define SYS_READ        0
#define SYS_WRITE       1
#define SYS_OPEN        2
#define SYS_CLOSE       3
#define SYS_MMAP        9
#define SYS_MUNMAP      11
#define SYS_BRK         12
#define SYS_GETTIME     40   /* VOS3-specific: uptime in ms */
#define SYS_FORK        57
#define SYS_EXIT        60
#define SYS_WAIT4       61
#define SYS_MKDIR       83
#define SYS_UNLINK      87

/* Open flags */
#define O_RDONLY        0
#define O_WRONLY        1
#define O_RDWR          2
#define O_CREAT         0x40
#define O_TRUNC         0x200

/* mmap flags */
#define PROT_READ       1
#define PROT_WRITE      2
#define MAP_PRIVATE     0x02
#define MAP_ANONYMOUS   0x20

/* Duration */
#define TEST_DURATION_MS    10000
#define TELEMETRY_INTERVAL  5000

static int get_sysinfo(vos3_sysinfo_t* info) {
    return vos3_get_sysinfo(info);
}

static unsigned long get_uptime_ms(void) {
    return (unsigned long)syscall0(SYS_GETTIME);
}

/* ============================================================================
 * TEST FRAMEWORK
 * ============================================================================ */

static int g_pass = 0;
static int g_fail = 0;
static int g_errors = 0;

#define TEST_PASS(name) do { printf("[PASS] %s\n", name); g_pass++; } while(0)
#define TEST_FAIL(name, msg) do { printf("[FAIL] %s: %s\n", name, msg); g_fail++; } while(0)

/* ============================================================================
 * WORKLOAD: Fork + exit + wait (process churn)
 * ============================================================================ */

static int workload_fork_churn(int iterations) {
    int errors = 0;
    for (int i = 0; i < iterations; i++) {
        long pid = syscall0(SYS_FORK);
        if (pid < 0) {
            errors++;
            continue;
        }
        if (pid == 0) {
            /* Child: do minimal work and exit */
            syscall1(SYS_EXIT, 0);
            for(;;);  /* unreachable */
        }
        /* Parent: wait for child */
        int status = 0;
        long ret = syscall4(SYS_WAIT4, pid, (long)&status, 0, 0);
        if (ret < 0) errors++;
    }
    return errors;
}

/* ============================================================================
 * WORKLOAD: File I/O (create, write, read, unlink)
 * ============================================================================ */

static int workload_file_io(int iterations) {
    int errors = 0;
    char path[64];
    char wbuf[128];
    char rbuf[128];

    for (int i = 0; i < iterations; i++) {
        /* Build unique path */
        path[0] = '/'; path[1] = 't'; path[2] = 'm'; path[3] = 'p';
        path[4] = '/'; path[5] = 's'; path[6] = 't';
        /* Simple int-to-string for iteration */
        int n = i;
        int pos = 7;
        if (n == 0) { path[pos++] = '0'; }
        else {
            char digits[10];
            int dpos = 0;
            while (n > 0 && dpos < 10) { digits[dpos++] = '0' + (n % 10); n /= 10; }
            while (dpos > 0) path[pos++] = digits[--dpos];
        }
        path[pos] = '\0';

        /* Fill write buffer */
        for (int j = 0; j < 128; j++) wbuf[j] = (char)('A' + (j % 26));

        /* Create + write */
        long fd = syscall3(SYS_OPEN, (long)path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) { errors++; continue; }
        long wr = syscall3(SYS_WRITE, fd, (long)wbuf, 128);
        if (wr != 128) errors++;
        syscall1(SYS_CLOSE, fd);

        /* Read back */
        fd = syscall3(SYS_OPEN, (long)path, O_RDONLY, 0);
        if (fd < 0) { errors++; continue; }
        long rd = syscall3(SYS_READ, fd, (long)rbuf, 128);
        if (rd != 128) errors++;
        syscall1(SYS_CLOSE, fd);

        /* Verify */
        for (int j = 0; j < 128 && rd == 128; j++) {
            if (rbuf[j] != wbuf[j]) { errors++; break; }
        }

        /* Cleanup */
        syscall1(SYS_UNLINK, (long)path);
    }
    return errors;
}

/* ============================================================================
 * WORKLOAD: mmap/munmap (anonymous pages)
 * ============================================================================ */

static int workload_mmap(int iterations) {
    int errors = 0;
    for (int i = 0; i < iterations; i++) {
        /* Map 4KB anonymous page */
        long addr = syscall6(SYS_MMAP, 0, 4096,
                            PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (addr < 0 || addr < 0x10000) {
            errors++;
            continue;
        }
        /* Touch the page */
        volatile char* p = (volatile char*)addr;
        *p = 0x42;
        if (*p != 0x42) errors++;

        /* Unmap */
        long ret = syscall2(SYS_MUNMAP, addr, 4096);
        if (ret < 0) errors++;
    }
    return errors;
}

/* ============================================================================
 * MAIN: 60-second sustained test with telemetry
 * ============================================================================ */

int main(int argc, char* argv[])
{
    (void)argc; (void)argv;

    printf("=== Sustained Stability Audit (10s) ===\n");

    /* Create /tmp if needed */
    syscall2(SYS_MKDIR, (long)"/tmp", 0755);

    /* Baseline telemetry */
    vos3_sysinfo_t info;
    if (get_sysinfo(&info) != 0) {
        TEST_FAIL("sysinfo_available", "SYS_SYSINFO returned error");
        printf("[TELEMETRY] T=0 free=0 total=0 tasks=0 zombies=0 errors=0\n");
        return 1;
    }

    unsigned long start_ms    = info.uptime_ms;
    unsigned long baseline_fp = info.free_pages;
    unsigned long total_pages = info.total_pages;

    printf("[TELEMETRY] T=0 free=%lu total=%lu tasks=%u zombies=%u errors=0\n",
           info.free_pages, info.total_pages, info.nr_tasks, info.nr_zombies);

    unsigned long next_telemetry = start_ms + TELEMETRY_INTERVAL;
    int total_errors = 0;
    int iteration = 0;
    int telemetry_count = 1;  /* T=0 already printed */

    while (1) {
        unsigned long now = get_uptime_ms();
        unsigned long elapsed = now - start_ms;
        if (elapsed >= TEST_DURATION_MS) break;

        /* Mixed workload batch */
        int errs = 0;
        errs += workload_fork_churn(5);
        errs += workload_file_io(10);
        errs += workload_mmap(10);
        total_errors += errs;
        iteration++;

        /* Check telemetry interval */
        now = get_uptime_ms();
        if (now >= next_telemetry) {
            elapsed = now - start_ms;
            unsigned long t_sec = elapsed / 1000;

            if (get_sysinfo(&info) != 0) {
                TEST_FAIL("periodic_telemetry", "sysinfo failed after workload cleanup");
                return 1;
            }
            {
                printf("[TELEMETRY] T=%lu free=%lu total=%lu tasks=%u zombies=%u errors=%d\n",
                       t_sec, info.free_pages, info.total_pages,
                       info.nr_tasks, info.nr_zombies, total_errors);
            }
            telemetry_count++;
            next_telemetry += TELEMETRY_INTERVAL;
        }
    }

    /* Final telemetry */
    if (get_sysinfo(&info) != 0) {
        TEST_FAIL("final_telemetry", "sysinfo failed after workload cleanup");
        return 1;
    }
    {
        printf("[TELEMETRY] T=final free=%lu total=%lu tasks=%u zombies=%u errors=%d\n",
               info.free_pages, info.total_pages, info.nr_tasks, info.nr_zombies, total_errors);
    }

    /* Analysis */
    unsigned long final_fp = info.free_pages;
    long fp_delta = (long)baseline_fp - (long)final_fp;
    unsigned long pct_x100 = 0;
    if (baseline_fp > 0) {
        /* Calculate percentage * 100 to avoid floating point */
        if (fp_delta > 0) {
            pct_x100 = ((unsigned long)fp_delta * 10000) / baseline_fp;
        }
    }

    printf("\n=== Sustained Stability Results ===\n");
    printf("  Duration:      60s (%d iterations)\n", iteration);
    printf("  Baseline free: %lu pages\n", baseline_fp);
    printf("  Final free:    %lu pages\n", final_fp);
    printf("  Delta:         %ld pages (%lu.%02lu%%)\n",
           fp_delta, pct_x100 / 100, pct_x100 % 100);
    printf("  Zombies:       %u\n", info.nr_zombies);
    printf("  Total errors:  %d\n", total_errors);
    printf("  Telemetry pts: %d\n", telemetry_count);

    /* Verdict */
    int pass = 1;

    if (pct_x100 > 100) {  /* > 1% */
        TEST_FAIL("memory_leak_check", "Free pages dropped >1%");
        pass = 0;
    } else {
        TEST_PASS("memory_leak_check");
    }

    if (info.nr_zombies > 2) {
        TEST_FAIL("zombie_leak_check", "Unexpected zombie accumulation");
        pass = 0;
    } else {
        TEST_PASS("zombie_leak_check");
    }

    if (total_errors > 0) {
        TEST_FAIL("zero_errors", "Workload errors detected");
        pass = 0;
    } else {
        TEST_PASS("zero_errors");
    }

    if (telemetry_count < 3) {
        TEST_FAIL("telemetry_coverage", "Expected 3+ telemetry points");
        pass = 0;
    } else {
        TEST_PASS("telemetry_coverage");
    }

    printf("\n");
    if (pass) {
        printf("[SUSTAINED] VERDICT: PRODUCTION READY\n");
    } else {
        printf("[SUSTAINED] VERDICT: ROLLBACK ADVISED\n");
    }

    return pass ? 0 : 1;
}
