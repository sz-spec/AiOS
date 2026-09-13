/**
 * @file test_model_load.c
 * @brief Phase 2.5: Frontier Filesystem & Model-Load Audit
 *
 * @details 3-test suite targeting AI weight-loading workloads:
 *   1. model_burst_shm    — 8-agent concurrent HugeTLB SHM weight loading
 *   2. vfs_thread_stress   — 16-thread concurrent ramfs file stress
 *   3. hugepage_dirty_audit — HugePage dirty-bit gap documentation
 *
 * @version 1.0.0
 * @date 2026-03-23
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2.5 — Model-Load Audit
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

static int g_tests_passed = 0;
static int g_tests_failed = 0;

#define TEST_PASS(name) do { \
    printf("  [PASS] %s\n", (name)); \
    g_tests_passed++; \
} while (0)

#define TEST_FAIL(name, ...) do { \
    printf("  [FAIL] " name "\n", ##__VA_ARGS__); \
    g_tests_failed++; \
} while (0)

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_READ            0
#define SYS_WRITE           1
#define SYS_OPEN            2
#define SYS_CLOSE           3
#define SYS_YIELD           24
#define SYS_GETPID          39
#define SYS_GETTIME         40
#define SYS_CLONE           56
#define SYS_FORK            57
#define SYS_EXIT            60
#define SYS_WAIT4           61
#define SYS_MKDIR           83
#define SYS_UNLINK          87
#define SYS_SYSINFO         99

/* SHM syscalls */
#define SYS_SHM_CREATE      410
#define SYS_SHM_DESTROY     411
#define SYS_SHM_MAP         412
#define VOS3_SHM_FLAG_PUBLIC (1U << 6)
#define SYS_SHM_UNMAP       413

/* Clone flags */
#define CLONE_VM            0x00000100UL
#define CLONE_FS            0x00000200UL
#define CLONE_FILES         0x00000400UL
#define CLONE_SIGHAND       0x00000800UL
#define CLONE_THREAD        0x00010000UL

/* File flags */
#define O_RDONLY            0
#define O_WRONLY            1
#define O_RDWR              2
#define O_CREAT             0100
#define O_TRUNC             01000

/* SHM flags */
#define VOS3_SHM_FLAG_HUGETLB   (1U << 4)

/* ============================================================================
 * HELPERS
 * ============================================================================ */

static inline unsigned long get_uptime_ms(void)
{
    return (unsigned long)syscall0(SYS_GETTIME);
}

typedef struct {
    unsigned long free_pages;
    unsigned long total_pages;
    unsigned int  nr_tasks;
    unsigned int  nr_zombies;
    unsigned long uptime_ms;
} vos3_sysinfo_t;

static int get_sysinfo(vos3_sysinfo_t* info)
{
    return (int)syscall1(SYS_SYSINFO, (long)info);
}

static uint64_t checksum(const void* buf, unsigned long len)
{
    const uint8_t* p = (const uint8_t*)buf;
    uint64_t ck = 0;
    for (unsigned long i = 0; i < len; i++)
        ck ^= ((uint64_t)p[i]) << ((i & 7) * 8);
    return ck;
}

/* Simple int to decimal string */
static void itoa_simple(int val, char* buf)
{
    if (val < 0) { *buf++ = '-'; val = -val; }
    char tmp[12];
    int i = 0;
    if (val == 0) { tmp[i++] = '0'; }
    while (val > 0) {
        tmp[i++] = (char)('0' + val % 10);
        val /= 10;
    }
    for (int j = i - 1; j >= 0; j--)
        *buf++ = tmp[j];
    *buf = '\0';
}

/* ============================================================================
 * TEST 1: MODEL BURST SHM
 *
 * 8 concurrent "AI agents" (forked children) map the same HugeTLB SHM
 * region, verify data integrity via checksum, and report latency.
 * ============================================================================ */

#define BURST_AGENTS    8
#define MODEL_SIZE      (2UL * 1024 * 1024)  /* 2MB = 1 HugePage */

static void model_burst_shm(void)
{
    printf("\n--- Test: model_burst_shm ---\n");

    /* Phase A: Create and fill "model weights" SHM */
    long shm_id = syscall3(SYS_SHM_CREATE, (long)"model_weights",
                           (long)MODEL_SIZE,
                           (long)(VOS3_SHM_FLAG_HUGETLB | VOS3_SHM_FLAG_PUBLIC));
    if (shm_id < 0 || shm_id == (long)0xFFFFFFFFU) {
        TEST_FAIL("model_burst_shm: shm_create failed (ret=%ld)", shm_id);
        return;
    }

    long base_addr = syscall2(SYS_SHM_MAP, shm_id, 0);
    if (base_addr <= 0) {
        TEST_FAIL("model_burst_shm: shm_map failed (ret=%ld)", base_addr);
        syscall1(SYS_SHM_DESTROY, shm_id);
        return;
    }

    /* Fill with deterministic pattern */
    volatile uint8_t* base = (volatile uint8_t*)base_addr;
    for (unsigned long i = 0; i < MODEL_SIZE; i++)
        base[i] = (uint8_t)(i & 0xFF);

    /* Compute reference checksum */
    uint64_t ref_cksum = checksum((const void*)base_addr, MODEL_SIZE);
    printf("  Phase A: SHM created, pattern written, ref_cksum=0x%lx\n",
           (unsigned long)ref_cksum);

    /* Unmap from parent */
    syscall2(SYS_SHM_UNMAP, shm_id, base_addr);

    /* Record baseline sysinfo */
    vos3_sysinfo_t info_before;
    get_sysinfo(&info_before);

    /* Phase B: Fork 8 agent processes */
    unsigned long t_start = get_uptime_ms();
    pid_t children[BURST_AGENTS];
    int fork_ok = 1;

    for (int i = 0; i < BURST_AGENTS; i++) {
        pid_t pid = fork();
        if (pid < 0) {
            printf("  Fork %d failed\n", i);
            fork_ok = 0;
            /* Wait for already-forked children */
            for (int j = 0; j < i; j++) {
                int s;
                waitpid(children[j], &s, 0);
            }
            break;
        }

        if (pid == 0) {
            /* Child: AI agent */
            long addr = syscall2(SYS_SHM_MAP, shm_id, 0);
            if (addr <= 0)
                _exit(1);

            /* Read through entire region, compute checksum */
            uint64_t ck = checksum((const void*)addr, MODEL_SIZE);

            syscall2(SYS_SHM_UNMAP, shm_id, addr);

            /* Exit 0 if match, 1 if mismatch */
            _exit(ck == ref_cksum ? 0 : 1);
        }

        children[i] = pid;
    }

    if (!fork_ok) {
        syscall1(SYS_SHM_DESTROY, shm_id);
        TEST_FAIL("model_burst_shm: fork failed");
        return;
    }

    /* Wait for all children */
    int successes = 0;
    int failures = 0;
    for (int i = 0; i < BURST_AGENTS; i++) {
        int status = 0;
        waitpid(children[i], &status, 0);
        if (WIFEXITED(status) && WEXITSTATUS(status) == 0)
            successes++;
        else
            failures++;
    }

    unsigned long t_end = get_uptime_ms();
    unsigned long wall_ms = t_end - t_start;

    /* Phase C: Report metrics */
    syscall1(SYS_SHM_DESTROY, shm_id);

    vos3_sysinfo_t info_after;
    get_sysinfo(&info_after);
    long page_delta = (long)info_before.free_pages - (long)info_after.free_pages;
    if (page_delta < 0) page_delta = -page_delta;

    printf("  Phase B: %d/%d agents checksum OK, %d failed\n",
           successes, BURST_AGENTS, failures);
    printf("  burst_agents=%d region_size=2MB wall_time=%lu ms\n",
           BURST_AGENTS, wall_ms);
    printf("  Phase C: page_delta=%ld (should be small)\n", page_delta);

    if (successes == BURST_AGENTS && failures == 0 && page_delta <= 300) {
        TEST_PASS("model_burst_shm");
    } else {
        TEST_FAIL("model_burst_shm: ok=%d fail=%d delta=%ld",
                  successes, failures, page_delta);
    }
}

/* ============================================================================
 * TEST 2: VFS THREAD STRESS
 *
 * 16 threads each create/write/read/verify/delete 62 files (992 total)
 * in /tmp/vfs_stress directory. Tests ramfs under concurrent load.
 * ============================================================================ */

#define VFS_NUM_THREADS     16
#define VFS_FILES_PER_THREAD 62
#define VFS_TOTAL_FILES     (VFS_NUM_THREADS * VFS_FILES_PER_THREAD)

static char g_vfs_stacks[VFS_NUM_THREADS][65536] __attribute__((aligned(16)));
static volatile int g_thread_done[VFS_NUM_THREADS];
static volatile int g_thread_errors[VFS_NUM_THREADS];
static volatile int g_thread_files[VFS_NUM_THREADS];
static volatile int g_next_thread_id = 0;

static void vfs_worker(void)
{
    int t = __atomic_fetch_add(&g_next_thread_id, 1, __ATOMIC_SEQ_CST);

    char pattern[512];
    char readbuf[512];

    /* Fill write pattern with thread index */
    for (int i = 0; i < 512; i++)
        pattern[i] = (char)(t & 0xFF);

    for (int f = 0; f < VFS_FILES_PER_THREAD; f++) {
        /* Build filename: /tmp/vfs_stress/tXX_YYY */
        char fname[40];
        char tbuf[4], fbuf[4];
        itoa_simple(t, tbuf);
        itoa_simple(f, fbuf);

        /* Manual string concat */
        int pos = 0;
        const char* prefix = "/tmp/vfs_stress/t";
        for (int i = 0; prefix[i]; i++)
            fname[pos++] = prefix[i];
        for (int i = 0; tbuf[i]; i++)
            fname[pos++] = tbuf[i];
        fname[pos++] = '_';
        for (int i = 0; fbuf[i]; i++)
            fname[pos++] = fbuf[i];
        fname[pos] = '\0';

        int err = 0;

        /* Create + write */
        long fd = syscall3(SYS_OPEN, (long)fname,
                           (long)(O_CREAT | O_WRONLY | O_TRUNC), 0644L);
        if (fd < 0) { err = 1; goto next; }

        long wr = syscall3(SYS_WRITE, fd, (long)pattern, 512L);
        syscall1(SYS_CLOSE, fd);
        if (wr != 512) { err = 1; goto next; }

        /* Reopen + read */
        fd = syscall3(SYS_OPEN, (long)fname, (long)O_RDONLY, 0L);
        if (fd < 0) { err = 1; goto next; }

        long rd = syscall3(SYS_READ, fd, (long)readbuf, 512L);
        syscall1(SYS_CLOSE, fd);
        if (rd != 512) { err = 1; goto next; }

        /* Verify */
        for (int i = 0; i < 512; i++) {
            if (readbuf[i] != pattern[i]) { err = 1; break; }
        }

        /* Unlink */
        syscall1(SYS_UNLINK, (long)fname);

next:
        if (err)
            g_thread_errors[t]++;
        else
            g_thread_files[t]++;
    }

    g_thread_done[t] = 1;
    syscall1(SYS_EXIT, 0);
}

static void vfs_thread_stress(void)
{
    printf("\n--- Test: vfs_thread_stress ---\n");

    /* Setup: create directory */
    syscall2(SYS_MKDIR, (long)"/tmp/vfs_stress", 0755L);

    /* Clear state */
    g_next_thread_id = 0;
    for (int i = 0; i < VFS_NUM_THREADS; i++) {
        g_thread_done[i] = 0;
        g_thread_errors[i] = 0;
        g_thread_files[i] = 0;
    }

    /* Clone 16 threads */
    long flags = (long)(CLONE_VM | CLONE_FS | CLONE_FILES |
                        CLONE_SIGHAND | CLONE_THREAD);

    int spawned = 0;
    for (int t = 0; t < VFS_NUM_THREADS; t++) {
        void* stack_top = &g_vfs_stacks[t][65536];
        long ret = syscall5(SYS_CLONE, flags, (long)stack_top, 0L, 0L, 0L);
        if (ret == 0) {
            /* Child thread */
            vfs_worker();
            /* Never returns */
        }
        if (ret > 0)
            spawned++;
        else
            printf("  clone failed for thread %d: %ld\n", t, ret);
    }

    printf("  Spawned %d/%d threads\n", spawned, VFS_NUM_THREADS);

    /* Wait loop: poll g_thread_done[] with yield, timeout 30s */
    unsigned long deadline = get_uptime_ms() + 30000;
    int all_done = 0;
    while (get_uptime_ms() < deadline) {
        all_done = 1;
        for (int i = 0; i < spawned; i++) {
            if (!g_thread_done[i]) { all_done = 0; break; }
        }
        if (all_done) break;
        syscall0(SYS_YIELD);
    }

    /* Sum results */
    int total_files = 0;
    int total_errors = 0;
    int threads_done = 0;
    for (int i = 0; i < spawned; i++) {
        total_files += g_thread_files[i];
        total_errors += g_thread_errors[i];
        if (g_thread_done[i]) threads_done++;
    }

    printf("  Threads completed: %d/%d\n", threads_done, spawned);
    printf("  Total files: %d (expected %d)\n", total_files, VFS_TOTAL_FILES);
    printf("  Total errors: %d\n", total_errors);

    if (threads_done == spawned && total_files >= VFS_TOTAL_FILES &&
        total_errors == 0) {
        TEST_PASS("vfs_thread_stress");
    } else {
        TEST_FAIL("vfs_thread_stress: done=%d/%d files=%d errs=%d",
                  threads_done, spawned, total_files, total_errors);
    }
}

/* ============================================================================
 * TEST 3: HUGEPAGE DIRTY-BIT AUDIT
 *
 * Verifies data integrity on HugePage SHM writes, then reports the
 * kernel's dirty-bit tracking status (informational audit).
 * ============================================================================ */

static void hugepage_dirty_audit(void)
{
    printf("\n--- Test: hugepage_dirty_audit ---\n");

    /* Phase A: Map and write */
    long shm_id = syscall3(SYS_SHM_CREATE, (long)"dirty_test",
                           (long)MODEL_SIZE, (long)VOS3_SHM_FLAG_HUGETLB);
    if (shm_id < 0 || shm_id == (long)0xFFFFFFFFU) {
        TEST_FAIL("hugepage_dirty_audit: shm_create failed (ret=%ld)", shm_id);
        return;
    }

    long addr = syscall2(SYS_SHM_MAP, shm_id, 0);
    if (addr <= 0) {
        TEST_FAIL("hugepage_dirty_audit: shm_map failed (ret=%ld)", addr);
        syscall1(SYS_SHM_DESTROY, shm_id);
        return;
    }

    vos3_sysinfo_t info_before;
    get_sysinfo(&info_before);

    /* Write patterns to first and last bytes */
    volatile uint8_t* p = (volatile uint8_t*)addr;
    p[0] = 0xAA;
    p[MODEL_SIZE - 1] = 0xBB;

    /* Phase B: Verify data integrity */
    int integrity_ok = (p[0] == 0xAA && p[MODEL_SIZE - 1] == 0xBB);

    printf("  Phase A: Write 0xAA at offset 0, 0xBB at offset %lu\n",
           MODEL_SIZE - 1);
    printf("  Phase B: Read-back: addr[0]=0x%02x addr[%lu]=0x%02x — %s\n",
           p[0], MODEL_SIZE - 1, p[MODEL_SIZE - 1],
           integrity_ok ? "OK" : "MISMATCH");

    /* Phase C: Audit report */
    printf("  [AUDIT] HugePage dirty-bit status:\n");
    printf("  [AUDIT]   VOS3_PTE_DIRTY (bit 6) defined in vmm.h — YES\n");
    printf("  [AUDIT]   Kernel reads/checks dirty bit — NO\n");
    printf("  [AUDIT]   Dirty page writeback mechanism — NO\n");
    printf("  [AUDIT]   Impact: Cannot track modified HugePages for snapshots\n");
    printf("  [AUDIT]   Recommendation: Add dirty-bit scan for model-checkpoint\n");

    /* Phase D: Cleanup */
    syscall2(SYS_SHM_UNMAP, shm_id, addr);
    syscall1(SYS_SHM_DESTROY, shm_id);

    vos3_sysinfo_t info_after;
    get_sysinfo(&info_after);
    long page_delta = (long)info_before.free_pages - (long)info_after.free_pages;
    if (page_delta < 0) page_delta = -page_delta;

    printf("  Phase D: Cleanup done, page_delta=%ld\n", page_delta);

    if (integrity_ok && page_delta <= 200) {
        TEST_PASS("hugepage_dirty_audit");
    } else {
        TEST_FAIL("hugepage_dirty_audit: integrity=%d delta=%ld",
                  integrity_ok, page_delta);
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("=== Phase 2.5: Model-Load Audit ===\n");

    model_burst_shm();
    vfs_thread_stress();
    hugepage_dirty_audit();

    printf("\n=== Phase 2.5 Complete ===\n");
    printf("  RESULTS: %d PASS, %d FAIL\n", g_tests_passed, g_tests_failed);
    printf("========================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
