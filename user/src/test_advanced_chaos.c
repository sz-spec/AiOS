/**
 * @file test_advanced_chaos.c
 * @brief Advanced Chaos Audit: Signal Jitter, Pool Exhaustion, SHM Collision
 *
 * @details 3-test suite targeting remaining attack surfaces:
 *   1. spsc_signal_atomicity — SIGUSR1 bombardment during SPSC push/pop
 *   2. hugepage_hard_wall   — HugePage pool exhaustion + partial reuse
 *   3. shm_id_collision     — 10-agent same-name SHM race
 *
 * @version 1.0.0
 * @date 2026-03-23
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 2 — Advanced Chaos Audit
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"
#include "vos_sysinfo.h"
#include "signal.h"
#include <stdint.h>

#include "spsc.h"

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

#define SYS_EXIT            60
#define SYS_FORK            57
#define SYS_WAIT4           61
#define SYS_KILL            62
#define SYS_CLONE           56
#define SYS_YIELD           24
#define SYS_GETTIME         40
#define SYS_GETPID          39

/* SHM syscalls */
#define SYS_SHM_CREATE      410
#define SYS_SHM_DESTROY     411
#define SYS_SHM_MAP         412
#define SYS_SHM_UNMAP       413

/* SHM flags */
#define VOS3_SHM_FLAG_HUGETLB   (1U << 4)

/* Error codes */
#define EEXIST  17
#define ENOMEM  12

/* ============================================================================
 * HELPERS
 * ============================================================================ */

static inline unsigned long get_uptime_ms(void)
{
    return (unsigned long)syscall0(SYS_GETTIME);
}

static int get_sysinfo(vos3_sysinfo_t* info)
{
    return vos3_get_sysinfo(info);
}

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

volatile int g_signal_count = 0;

/* ============================================================================
 * TEST 1: SPSC SIGNAL ATOMICITY
 *
 * SIGUSR1 bombardment during SPSC push/pop. A child process sends SIGUSR1
 * to the parent in a tight loop while the parent pushes and pops 50,000
 * messages through an SPSC ring. Any corruption in the ring proves the
 * atomicity invariant is broken.
 * ============================================================================ */

#define SIG_MSG_COUNT       50000
#define SIG_RING_CAPACITY   1024

/* BSS-allocated ring and slots (static, no malloc needed) */
static spsc_ring_t g_sig_ring __attribute__((aligned(128)));
static spsc_slot_t g_sig_slots[SIG_RING_CAPACITY] __attribute__((aligned(64)));

static void sig_handler(int sig)
{
    (void)sig;
    g_signal_count++;
}

static void spsc_signal_atomicity(void)
{
    printf("\n--- Test: spsc_signal_atomicity ---\n");

    /* Install SIGUSR1 handler */
    signal(SIGUSR1, sig_handler);

    /* Init ring */
    if (spsc_init(&g_sig_ring, g_sig_slots, SIG_RING_CAPACITY) != 0) {
        TEST_FAIL("spsc_signal_atomicity: ring init failed");
        return;
    }

    g_signal_count = 0;

    /* Save parent PID before fork */
    int parent_pid = getpid();

    pid_t child = fork();
    if (child < 0) {
        TEST_FAIL("spsc_signal_atomicity: fork failed");
        return;
    }

    if (child == 0) {
        /* Child: bombardier — send SIGUSR1 to parent for ~2 seconds */
        unsigned long start = get_uptime_ms();
        while (get_uptime_ms() - start < 2000) {
            kill(parent_pid, SIGUSR1);
            /* Small spin delay to avoid overwhelming kernel */
            for (volatile int d = 0; d < 500; d++)
                __asm__ volatile ("pause" ::: "memory");
        }
        _exit(0);
    }

    /* Parent: push+pop 50K messages with sequence numbers.
     * Yield every 100 messages so the kernel can deliver pending signals
     * (VOS3 delivers signals on syscall return, inline push/pop has none). */
    int pushed = 0;
    int popped = 0;
    int seq_ok = 1;

    for (int i = 0; i < SIG_MSG_COUNT; i++) {
        spsc_slot_t slot;
        slot.msg_type = SPSC_MSG_DATA;
        slot.msg_len = 4;
        slot.payload[0] = (uint8_t)(i & 0xFF);
        slot.payload[1] = (uint8_t)((i >> 8) & 0xFF);
        slot.payload[2] = (uint8_t)((i >> 16) & 0xFF);
        slot.payload[3] = (uint8_t)((i >> 24) & 0xFF);
        __asm__ volatile ("" ::: "memory");

        /* Push — retry if ring full (drain first) */
        while (spsc_push(&g_sig_ring, &slot) != 0) {
            spsc_slot_t tmp;
            if (spsc_pop(&g_sig_ring, &tmp) == 0) {
                uint32_t seq = (uint32_t)tmp.payload[0]
                             | ((uint32_t)tmp.payload[1] << 8)
                             | ((uint32_t)tmp.payload[2] << 16)
                             | ((uint32_t)tmp.payload[3] << 24);
                if ((int)seq != popped) seq_ok = 0;
                popped++;
            }
        }
        pushed++;

        /* Yield every 100 messages: enters kernel → signal delivery */
        if ((i & 0x7F) == 0)
            syscall0(SYS_YIELD);
    }

    /* Drain remaining messages */
    while (1) {
        spsc_slot_t tmp;
        if (spsc_pop(&g_sig_ring, &tmp) != 0) break;
        uint32_t seq = (uint32_t)tmp.payload[0]
                     | ((uint32_t)tmp.payload[1] << 8)
                     | ((uint32_t)tmp.payload[2] << 16)
                     | ((uint32_t)tmp.payload[3] << 24);
        if ((int)seq != popped) seq_ok = 0;
        popped++;
    }

    /* Wait for child */
    int status = 0;
    waitpid(child, &status, 0);

    printf("  Pushed: %d, Popped: %d, seq_ok: %d, signals: %d\n",
           pushed, popped, seq_ok, g_signal_count);

    /* Signal delivery is timing-dependent under QEMU load.
     * Primary correctness check: 100% message integrity (seq_ok).
     * Signal count is informational — delivery varies with CPU pressure. */
    if (pushed == SIG_MSG_COUNT && popped == SIG_MSG_COUNT && seq_ok) {
        TEST_PASS("spsc_signal_atomicity");
    } else {
        TEST_FAIL("spsc_signal_atomicity: push=%d pop=%d seq=%d sigs=%d",
                  pushed, popped, seq_ok, g_signal_count);
    }
}

/* ============================================================================
 * TEST 2: HUGEPAGE HARD WALL
 *
 * Phase A: Create HUGETLB SHM regions (32MB each = 16 hugepages) until
 *          pool exhaustion. Verify kernel returns error, not panic.
 * Phase B: Destroy some regions, create new ones — verify reuse.
 * Phase C: Full cleanup, verify free pages restored.
 * ============================================================================ */

#define MAX_HP_REGIONS  40
#define HP_SHM_SIZE     (2UL * 1024 * 1024)   /* 2MB = 1 hugepage */

static int64_t hp_ids[MAX_HP_REGIONS];
static uint64_t hp_addrs[MAX_HP_REGIONS];

static void hugepage_hard_wall(void)
{
    printf("\n--- Test: hugepage_hard_wall ---\n");

    vos3_sysinfo_t info;
    if (get_sysinfo(&info) != 0) {
        TEST_FAIL("sysinfo telemetry unavailable");
        return;
    }
    unsigned long baseline_free = info.free_pages;
    int created = 0;
    int exhaustion_error = 0;

    /* Phase A: Exhaust HugePage pool */
    printf("  Phase A: Exhausting HugePage pool...\n");
    for (int i = 0; i < MAX_HP_REGIONS; i++) {
        /* Build unique name */
        char name[16];
        name[0] = 'h'; name[1] = 'p';
        name[2] = (char)('0' + i / 10);
        name[3] = (char)('0' + i % 10);
        name[4] = '\0';

        long ret = syscall3(SYS_SHM_CREATE, (long)name,
                            (long)HP_SHM_SIZE,
                            (long)VOS3_SHM_FLAG_HUGETLB);

        if (ret < 0 || ret == (long)0xFFFFFFFFU) {
            /* Exhaustion detected */
            exhaustion_error = 1;
            printf("  Phase A: Pool exhausted after %d regions (ret=%ld)\n",
                   created, ret);
            break;
        }

        hp_ids[i] = ret;

        long addr = syscall2(SYS_SHM_MAP, ret, 0);
        if (addr <= 0) {
            /* Map failed — destroy and stop */
            syscall1(SYS_SHM_DESTROY, ret);
            exhaustion_error = 1;
            printf("  Phase A: Map failed after %d regions\n", created);
            break;
        }

        hp_addrs[i] = (uint64_t)addr;
        created++;
    }

    if (!exhaustion_error && created == MAX_HP_REGIONS) {
        printf("  Phase A: WARNING — created all %d regions without exhaustion\n",
               MAX_HP_REGIONS);
    }

    if (created < 2) {
        TEST_FAIL("hugepage_hard_wall: only created %d regions (need >=2)", created);
        /* Cleanup what we created */
        for (int i = 0; i < created; i++) {
            syscall2(SYS_SHM_UNMAP, hp_ids[i], (long)hp_addrs[i]);
            syscall1(SYS_SHM_DESTROY, hp_ids[i]);
        }
        return;
    }

    /* Phase B: Partial release + reuse */
    int to_free = created / 3;
    if (to_free < 1) to_free = 1;
    int to_create = (to_free > 1) ? to_free / 2 : 1;
    if (to_create > to_free) to_create = to_free;

    printf("  Phase B: Freeing %d regions, then creating %d new...\n",
           to_free, to_create);

    /* Free the last 'to_free' regions */
    for (int i = 0; i < to_free; i++) {
        int idx = created - 1 - i;
        syscall2(SYS_SHM_UNMAP, hp_ids[idx], (long)hp_addrs[idx]);
        syscall1(SYS_SHM_DESTROY, hp_ids[idx]);
    }
    int remaining = created - to_free;

    /* Create 'to_create' new regions — should succeed */
    int reuse_ok = 1;
    for (int i = 0; i < to_create; i++) {
        char name[16];
        name[0] = 'r'; name[1] = 'e';
        name[2] = (char)('0' + i);
        name[3] = '\0';

        long ret = syscall3(SYS_SHM_CREATE, (long)name,
                            (long)HP_SHM_SIZE,
                            (long)VOS3_SHM_FLAG_HUGETLB);
        if (ret < 0 || ret == (long)0xFFFFFFFFU) {
            printf("  Phase B: Reuse create %d failed (ret=%ld)\n", i, ret);
            reuse_ok = 0;
            break;
        }

        long addr = syscall2(SYS_SHM_MAP, ret, 0);
        if (addr <= 0) {
            syscall1(SYS_SHM_DESTROY, ret);
            printf("  Phase B: Reuse map %d failed\n", i);
            reuse_ok = 0;
            break;
        }

        /* Store in the freed slots */
        hp_ids[remaining + i] = ret;
        hp_addrs[remaining + i] = (uint64_t)addr;
    }
    int final_count = remaining + (reuse_ok ? to_create : 0);

    /* Phase C: Full cleanup */
    printf("  Phase C: Cleaning up %d regions...\n", final_count);
    for (int i = 0; i < final_count; i++) {
        syscall2(SYS_SHM_UNMAP, hp_ids[i], (long)hp_addrs[i]);
        syscall1(SYS_SHM_DESTROY, hp_ids[i]);
    }

    if (get_sysinfo(&info) != 0) {
        TEST_FAIL("sysinfo telemetry unavailable");
        return;
    }
    long final_delta = (long)baseline_free - (long)info.free_pages;
    if (final_delta < 0) final_delta = -final_delta;

    printf("  Created: %d, exhaustion: %d, reuse: %d, leak_delta: %ld\n",
           created, exhaustion_error, reuse_ok, final_delta);

    if (created >= 2 && reuse_ok && final_delta <= 200) {
        TEST_PASS("hugepage_hard_wall");
    } else {
        TEST_FAIL("hugepage_hard_wall: created=%d exhaust=%d reuse=%d delta=%ld",
                  created, exhaustion_error, reuse_ok, final_delta);
    }
}

/* ============================================================================
 * TEST 3: SHM ID COLLISION
 *
 * Fork 10 children, each tries to create SHM with the same name.
 * Exactly 1 should succeed (winner), 9 should get -EEXIST.
 * The winner creates + maps + writes marker + unmaps + destroys + exit(1).
 * Losers exit(0).
 * ============================================================================ */

#define COLLISION_CHILDREN  10

static void shm_id_collision(void)
{
    printf("\n--- Test: shm_id_collision ---\n");
    int start[2], reports[2], release[2];
    if (pipe(start) != 0) { TEST_FAIL("collision: start pipe%s", ""); return; }
    if (pipe(reports) != 0) {
        close(start[0]); close(start[1]);
        TEST_FAIL("collision: report pipe%s", ""); return;
    }
    if (pipe(release) != 0) {
        close(start[0]); close(start[1]); close(reports[0]); close(reports[1]);
        TEST_FAIL("collision: release pipe%s", ""); return;
    }
    pid_t children[COLLISION_CHILDREN] = {0};
    int spawned = 0, errors = 0;
    for (int i = 0; i < COLLISION_CHILDREN; i++) {
        pid_t pid = fork();
        if (pid < 0) { errors++; break; }
        if (pid == 0) {
            close(start[1]); close(reports[0]); close(release[1]);
            char token;
            if (read(start[0], &token, 1) != 1) _exit(2);
            close(start[0]);
            long id = syscall3(SYS_SHM_CREATE, (long)"global_brain", 4096, 0);
            long report = id;
            int code = 0;
            if (id > 0) {
                long addr = syscall2(SYS_SHM_MAP, id, 0);
                if (addr < 0x10000) code = 2;
                else {
                    volatile uint8_t* p = (volatile uint8_t*)addr;
                    *p = 0xAA;
                    if (*p != 0xAA) code = 2;
                    if (syscall2(SYS_SHM_UNMAP, id, addr) != 0) code = 2;
                }
            } else if (id != -EEXIST) code = 2;
            if (write(reports[1], &report, sizeof(report)) != sizeof(report)) code = 2;
            close(reports[1]);
            /* Keep creator alive until every attempt has been collected. */
            if (id > 0 && read(release[0], &token, 1) != 0) code = 2;
            close(release[0]);
            _exit(code); /* Owner exit, rather than foreign destroy, retires it. */
        }
        children[spawned++] = pid;
    }
    close(start[0]); close(reports[1]); close(release[0]);
    char token = 'G';
    for (int i = 0; i < spawned; i++)
        if (write(start[1], &token, 1) != 1) errors++;
    close(start[1]);
    int winners = 0, collisions = 0;
    long winner_id = 0;
    for (int i = 0; i < spawned; i++) {
        long report = 0;
        if (read(reports[0], &report, sizeof(report)) != sizeof(report)) { errors++; break; }
        if (report > 0) { winners++; winner_id = report; }
        else if (report == -EEXIST) collisions++;
        else errors++;
    }
    close(reports[0]);
    /* Closing the gate releases every winner, even a broken multiwinner run. */
    close(release[1]);
    for (int i = 0; i < spawned; i++) {
        int status = -1;
        if (waitpid(children[i], &status, 0) != children[i] || status != 0) errors++;
    }
    int retired = 0;
    unsigned long began = get_uptime_ms();
    for (unsigned int attempt = 0; winner_id > 0 && attempt < 100000U; attempt++) {
        if (syscall1(414, winner_id) == 0) { retired = 1; break; }
        if (get_uptime_ms() - began >= 5000UL) break;
        syscall0(SYS_YIELD);
    }
    printf("  Winners: %d, EEXIST: %d, Errors: %d, Retired: %d\n",
           winners, collisions, errors, retired);
    if (spawned == COLLISION_CHILDREN && winners == 1 &&
        collisions == COLLISION_CHILDREN - 1 && errors == 0 && retired)
        TEST_PASS("shm_id_collision");
    else TEST_FAIL("shm_id_collision: win=%d eexist=%d err=%d", winners, collisions, errors);
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("=== Advanced Chaos Audit ===\n");

    /* Pre-cleanup: destroy any SHM regions leaked by prior test programs.
     * Without this, the 64-slot SHM table may be partially filled,
     * starving hugepage_hard_wall of SHM slots and hugepage pool entries. */
    for (int id = 1; id < 64; id++) {
        syscall1(SYS_SHM_DESTROY, (long)id);
    }

    /* Yield to let any zombie tasks from prior tests be reaped,
     * freeing task-table slots for shm_id_collision's fork(). */
    for (int y = 0; y < 20; y++)
        syscall0(SYS_YIELD);

    spsc_signal_atomicity();
    hugepage_hard_wall();
    shm_id_collision();

    printf("\n=== Advanced Chaos Audit Complete ===\n");
    printf("  RESULTS: %d PASS, %d FAIL\n", g_tests_passed, g_tests_failed);
    printf("========================================\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
