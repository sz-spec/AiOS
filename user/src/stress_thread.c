/**
 * @file stress_thread.c
 * @brief VOS3 Task 1.4 Threading Stress & Validation Test
 *
 * @details Rigorous validation of the clone()/TLS/fd-table threading
 *          subsystem across four distinct tests:
 *
 *   1. stress_counter    — 10 threads x 10,000 atomic increments.
 *                          Final value MUST equal 100,000 exactly.
 *   2. tls_isolation     — Each thread sets a unique %fs base via
 *                          arch_prctl(ARCH_SET_FS), then reads %%fs:0
 *                          directly (hardware segment access) during
 *                          concurrent work to prove MSR_FS_BASE is
 *                          correctly restored on every context switch.
 *   3. file_table_stress — Each thread opens/writes/closes a unique
 *                          file through the shared fd_table to verify
 *                          CLONE_FILES stability under concurrency.
 *   4. repeat_stability  — Runs tests 1+2+3 five more times, reading
 *                          /proc/meminfo MemFree before and after to
 *                          bound memory growth (< 512 kB threshold).
 *
 * @version 1.0.0
 * @date 2026-03-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define NUM_THREADS         10
#define INCREMENTS_EACH     10000
#define EXPECTED_TOTAL      ((long)(NUM_THREADS * INCREMENTS_EACH))   /* 100,000 */
#define STABILITY_ITERS     5
#define STACK_SIZE          65536U
#define SPIN_LIMIT          300000000U   /* ~3 s on QEMU before timeout */
#define MEM_LEAK_THRESH_KB  512          /* max acceptable MemFree drop over 5 runs */

/* ============================================================================
 * SYSCALL NUMBERS  (Linux x86-64, matched to VOS3 dispatch table)
 * ============================================================================ */

#define SYS_CLONE           56
#define SYS_ARCH_PRCTL      158
#define SYS_MKDIR_NR        83    /* avoid shadowing SYS_MKDIR if defined */

/* arch_prctl codes */
#define ARCH_SET_FS         0x1002
#define ARCH_GET_FS         0x1003

/* ============================================================================
 * CLONE FLAGS  (Linux POSIX-thread subset)
 * ============================================================================ */

#define CLONE_VM            0x00000100UL
#define CLONE_FS            0x00000200UL
#define CLONE_FILES         0x00000400UL
#define CLONE_SIGHAND       0x00000800UL
#define CLONE_THREAD        0x00010000UL

/* ============================================================================
 * TEST FRAMEWORK
 * ============================================================================ */

static int g_tests_passed = 0;
static int g_tests_failed = 0;

#define TEST_PASS(name) do { \
    printf("  [PASS] %s\n", (name)); \
    g_tests_passed++; \
} while (0)

#define TEST_FAIL(name, reason) do { \
    printf("  [FAIL] %s — %s\n", (name), (reason)); \
    g_tests_failed++; \
} while (0)

/* ============================================================================
 * TLS AREA  (pre-allocated, one slot per thread)
 *
 * The first field MUST be at offset 0 so that reading %%fs:0 returns
 * self_magic directly.  This is the field we verify via inline asm.
 * ============================================================================ */

typedef struct {
    unsigned long long self_magic;  /**< offset 0: 0xFACE0000 | slot */
    int                slot;        /**< offset 8 */
    int                pad;         /**< offset 12: alignment */
} tls_area_t;

/* ============================================================================
 * SHARED STATE  (all threads share via CLONE_VM)
 * ============================================================================ */

/* Per-thread stacks — reused across stability iterations */
static char g_stacks[NUM_THREADS][STACK_SIZE] __attribute__((aligned(16)));

/* Pre-allocated TLS areas, one per slot */
static tls_area_t g_tls_areas[NUM_THREADS];

/* Atomic slot allocator — each clone()d thread grabs one slot */
static volatile int  g_next_slot;

/* Count of threads that have completed all work */
static volatile int  g_done_count;

/* Shared counter incremented atomically by every thread */
static volatile long g_counter;

/* Per-slot results: -1 = not yet set, 0 = fail, 1 = pass */
static volatile int  g_tls_ok[NUM_THREADS];
static volatile int  g_file_ok[NUM_THREADS];

/* ============================================================================
 * ATOMIC / INLINE HELPERS
 * ============================================================================ */

/** Atomic fetch-and-add on int; returns old value. */
static inline int atomic_add_i(volatile int* ptr, int val)
{
    return __sync_fetch_and_add(ptr, val);
}

/** Atomic fetch-and-add on long; returns old value. */
static inline long atomic_add_l(volatile long* ptr, long val)
{
    return __sync_fetch_and_add(ptr, val);
}

/**
 * @brief Read the qword at %%fs:0 via x86-64 segment override.
 *
 * This directly tests MSR_FS_BASE (0xC0000100) at the hardware level:
 * the CPU fetches the physical address as (MSR_FS_BASE + 0).  If VOS3
 * wrote the wrong thread's tls_base to the MSR during a context switch,
 * this read returns the wrong magic value and TLS isolation fails.
 *
 * @warning  Requires arch_prctl(ARCH_SET_FS, valid_ptr) to have been
 *           called first; otherwise %%fs:0 resolves to VA 0 → #PF.
 */
static inline unsigned long long read_fs_zero(void)
{
    unsigned long long val;
    __asm__ volatile("movq %%fs:0, %0" : "=r"(val) : : "memory");
    return val;
}

/* ============================================================================
 * /proc/meminfo READER
 * ============================================================================ */

/** Parse a decimal integer starting at `s`, stopping at non-digit. */
static long parse_long(const char* s)
{
    long v = 0;
    while (*s >= '0' && *s <= '9')
        v = v * 10 + (*s++ - '0');
    return v;
}

/**
 * @brief Read MemFree from /proc/meminfo.
 * @return Free physical memory in kB, or -1 on error.
 */
static long read_memfree_kb(void)
{
    char buf[256];
    int fd = open("/proc/meminfo", O_RDONLY, 0);
    if (fd < 0) return -1;
    int n = (int)read(fd, buf, (int)sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';

    /* Scan for "MemFree:\t" */
    for (int i = 0; i + 8 < n; i++) {
        if (buf[i]   == 'M' && buf[i+1] == 'e' && buf[i+2] == 'm' &&
            buf[i+3] == 'F' && buf[i+4] == 'r' && buf[i+5] == 'e' &&
            buf[i+6] == 'e' && buf[i+7] == ':') {
            const char* p = &buf[i + 8];
            while (*p == '\t' || *p == ' ') p++;
            return parse_long(p);
        }
    }
    return -1;
}

/* ============================================================================
 * FILE PATH BUILDER
 * ============================================================================ */

/**
 * @brief Write "/tmp/vst<slot>" into buf (must be >= 14 bytes).
 *
 * Uses a "vst" prefix (volatile stress thread) to avoid collisions
 * with other benchmark files in /tmp.
 */
static void make_tmp_path(char* buf, int slot)
{
    const char* prefix = "/tmp/vst";
    int i = 0;
    while (prefix[i]) { buf[i] = prefix[i]; i++; }
    if (slot >= 10) buf[i++] = (char)('0' + (slot / 10));
    buf[i++] = (char)('0' + (slot % 10));
    buf[i]   = '\0';
}

/* ============================================================================
 * THREAD BODY
 *
 * Entered when clone() returns 0 (child path).  The thread:
 *   1. Grabs a unique slot atomically.
 *   2. Sets up its TLS area and calls arch_prctl(ARCH_SET_FS).
 *   3. Runs INCREMENTS_EACH atomic increments, verifying %%fs:0 every
 *      1024 iterations (after pausing to invite context switches).
 *   4. Does a final %%fs:0 verification.
 *   5. Opens, writes, and closes /tmp/vstN to test shared fd_table.
 *   6. Signals completion and calls sys_exit.
 * ============================================================================ */

static void __attribute__((noreturn)) thread_body(void)
{
    /* ---- 1. Claim unique slot ---- */
    int my_slot   = atomic_add_i(&g_next_slot, 1);
    unsigned long long my_magic = 0xFACE0000ULL | (unsigned long long)my_slot;

    /* ---- 2. Set up TLS and write to %%fs base MSR ---- */
    g_tls_areas[my_slot].self_magic = my_magic;
    g_tls_areas[my_slot].slot       = my_slot;
    __asm__ volatile("" ::: "memory");   /* ensure store before wrmsr */
    syscall2(SYS_ARCH_PRCTL, (long)ARCH_SET_FS,
             (long)&g_tls_areas[my_slot]);

    /* ---- 3. Counter loop + periodic TLS hardware verification ---- */
    int tls_ok = 1;
    for (int i = 0; i < INCREMENTS_EACH; i++) {
        atomic_add_l(&g_counter, 1L);

        /*
         * Every 1024 iterations: pause (invites timer-driven context
         * switches) then read %%fs:0 through the hardware segment
         * override.  If MSR_FS_BASE was corrupted by a buggy context
         * switch, this read returns a foreign magic value.
         */
        if ((i & 1023) == 0) {
            __asm__ volatile("pause" ::: "memory");
            unsigned long long seen = read_fs_zero();
            if (seen != my_magic) {
                tls_ok = 0;
            }
        }
    }

    /* ---- 4. Final TLS check after all work ---- */
    {
        unsigned long long seen = read_fs_zero();
        if (seen != my_magic) tls_ok = 0;
    }
    g_tls_ok[my_slot] = tls_ok;

    /* ---- 5. File table test via shared fd_table ---- */
    char path[16];
    make_tmp_path(path, my_slot);
    int file_ok = 0;
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        /* 4-byte payload unique to this thread slot */
        char payload[4] = { 'V', 'S', 'T', (char)('0' + my_slot) };
        int nw = (int)write(fd, payload, 4);
        close(fd);
        file_ok = (nw == 4) ? 1 : 0;
    }
    g_file_ok[my_slot] = file_ok;

    /* ---- 6. Signal done and exit ---- */
    __asm__ volatile("" ::: "memory");
    atomic_add_i(&g_done_count, 1);
    syscall1(SYS_EXIT, 0);
    __builtin_unreachable();
}

/* ============================================================================
 * SPAWN / WAIT HELPERS
 * ============================================================================ */

/**
 * @brief Spawn NUM_THREADS threads, returning count of successful clones.
 *
 * After a successful clone(), the child path immediately calls thread_body()
 * and never returns (it calls sys_exit).  The parent increments `spawned`
 * and continues the loop.
 */
static int spawn_all_threads(void)
{
    long flags = (long)(CLONE_VM | CLONE_FS | CLONE_FILES |
                        CLONE_SIGHAND | CLONE_THREAD);
    int spawned = 0;

    for (int i = 0; i < NUM_THREADS; i++) {
        /* Stack grows down; pre-decrement and push dummy return address. */
        unsigned long long* sp =
            (unsigned long long*)(g_stacks[i] + STACK_SIZE);
        *(--sp) = 0ULL;

        long ret = syscall5(SYS_CLONE, flags, (long)sp, 0L, 0L, 0L);

        if (ret == 0) {
            /* === CHILD: run thread body, never returns === */
            thread_body();
        }

        /* === PARENT: continue spawning === */
        if (ret > 0) {
            spawned++;
        } else {
            printf("    clone() failed for slot %d: %ld\n", i, ret);
        }
    }
    return spawned;
}

/**
 * @brief Busy-wait until g_done_count reaches `expected`.
 * @return 1 on success, 0 on timeout.
 */
static int wait_all_threads(void)
{
    unsigned int spin = 0;
    while (g_done_count < NUM_THREADS) {
        __asm__ volatile("pause" ::: "memory");
        if (++spin > SPIN_LIMIT) {
            printf("    TIMEOUT — done=%d / %d\n", g_done_count, NUM_THREADS);
            return 0;
        }
    }
    return 1;
}

/**
 * @brief Reset shared counters and result arrays for a fresh run.
 *
 * Stacks are NOT cleared — their content is irrelevant; each thread
 * pushes its own frame before use.
 */
static void reset_state(void)
{
    g_next_slot  = 0;
    g_done_count = 0;
    g_counter    = 0;
    for (int i = 0; i < NUM_THREADS; i++) {
        g_tls_ok[i]  = -1;
        g_file_ok[i] = -1;
    }
    __asm__ volatile("" ::: "memory");
}

/* ============================================================================
 * RESULT CHECKERS  (read per-slot arrays, return pass/fail)
 * ============================================================================ */

static int check_tls_results(void)
{
    int ok = 1;
    for (int i = 0; i < NUM_THREADS; i++) {
        if (g_tls_ok[i] != 1) {
            printf("    slot %d: TLS result = %d (expected 1)\n",
                   i, g_tls_ok[i]);
            ok = 0;
        }
    }
    return ok;
}

static int check_file_results(void)
{
    int ok = 1;
    for (int i = 0; i < NUM_THREADS; i++) {
        if (g_file_ok[i] != 1) {
            printf("    slot %d: file result = %d (expected 1)\n",
                   i, g_file_ok[i]);
            ok = 0;
        }
    }
    return ok;
}

/* ============================================================================
 * TEST 1 + 2 + 3: stress_counter / tls_isolation / file_table_stress
 *
 * All three tests run on the same batch of 10 threads.  After the threads
 * finish, each result array (g_counter, g_tls_ok[], g_file_ok[]) is checked
 * independently so failures are attributed to the correct subsystem.
 * ============================================================================ */

static void run_main_tests(void)
{
    printf("\n--- Test: stress_counter ---\n");
    reset_state();

    int spawned = spawn_all_threads();
    if (spawned < NUM_THREADS) {
        printf("  Only %d/%d threads spawned\n", spawned, NUM_THREADS);
        TEST_FAIL("stress_counter",    "spawn failure");
        TEST_FAIL("tls_isolation",     "spawn failure");
        TEST_FAIL("file_table_stress", "spawn failure");
        return;
    }
    if (!wait_all_threads()) {
        TEST_FAIL("stress_counter",    "timeout");
        TEST_FAIL("tls_isolation",     "timeout");
        TEST_FAIL("file_table_stress", "timeout");
        return;
    }

    /* ---- Test 1: counter ---- */
    printf("  counter = %ld  (expected %ld)\n", g_counter, EXPECTED_TOTAL);
    if (g_counter == EXPECTED_TOTAL)
        TEST_PASS("stress_counter");
    else
        TEST_FAIL("stress_counter", "wrong counter value — likely a lost update");

    /* ---- Test 2: TLS isolation ---- */
    printf("\n--- Test: tls_isolation ---\n");
    if (check_tls_results()) {
        printf("  All %d threads: %%%%fs:0 == own magic on every check "
               "(MSR_FS_BASE correctly isolated across context switches)\n",
               NUM_THREADS);
        TEST_PASS("tls_isolation");
    } else {
        TEST_FAIL("tls_isolation",
                  "one or more threads saw a foreign %%fs base "
                  "(MSR_FS_BASE not restored correctly on context switch)");
    }

    /* ---- Test 3: shared fd_table file I/O ---- */
    printf("\n--- Test: file_table_stress ---\n");
    if (check_file_results()) {
        printf("  All %d threads: open/write/close succeeded via shared fd_table\n",
               NUM_THREADS);
        TEST_PASS("file_table_stress");
    } else {
        TEST_FAIL("file_table_stress",
                  "one or more threads failed file I/O through shared fd_table");
    }
}

/* ============================================================================
 * TEST 4: repeat_stability  (5 iterations + MemFree leak check)
 *
 * If thread_destroy leaks kernel stacks, task structs, or any other
 * per-thread allocation, MemFree will fall more than MEM_LEAK_THRESH_KB
 * over 5*10 = 50 thread lifetimes.
 * ============================================================================ */

static void test_repeat_stability(void)
{
    printf("\n--- Test: repeat_stability (%d iterations) ---\n",
           STABILITY_ITERS);

    long mem_before = read_memfree_kb();
    if (mem_before > 0)
        printf("  MemFree before: %ld kB\n", mem_before);
    else
        printf("  MemFree: /proc/meminfo unavailable — skipping leak bound\n");

    int all_pass = 1;

    for (int iter = 0; iter < STABILITY_ITERS; iter++) {
        reset_state();

        int spawned = spawn_all_threads();
        if (spawned < NUM_THREADS || !wait_all_threads()) {
            printf("  Iter %d: spawn/wait failed (spawned=%d)\n",
                   iter + 1, spawned);
            all_pass = 0;
            continue;
        }

        long cnt = g_counter;
        int  tls = check_tls_results();
        int  cnt_ok = (cnt == EXPECTED_TOTAL);

        printf("  Iter %d: counter=%-7ld %s  tls=%s\n",
               iter + 1, cnt,
               cnt_ok ? "OK " : "ERR",
               tls     ? "OK " : "ERR");

        if (!cnt_ok || !tls)
            all_pass = 0;
    }

    /* Memory leak bound */
    long mem_after = read_memfree_kb();
    if (mem_before > 0 && mem_after > 0) {
        long delta = mem_before - mem_after;
        printf("  MemFree after:  %ld kB  (delta = %ld kB)\n",
               mem_after, delta);
        if (delta > MEM_LEAK_THRESH_KB) {
            printf("  LEAK: delta %ld kB exceeds %d kB threshold\n",
                   delta, MEM_LEAK_THRESH_KB);
            all_pass = 0;
        } else {
            printf("  Memory delta within bound (< %d kB) — no leak detected\n",
                   MEM_LEAK_THRESH_KB);
        }
    }

    if (all_pass)
        TEST_PASS("repeat_stability");
    else
        TEST_FAIL("repeat_stability",
                  "counter mismatch, TLS error, or memory leak across iterations");
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("\n=== stress_thread ===\n");
    printf("  %d threads × %d increments = %ld expected\n",
           NUM_THREADS, INCREMENTS_EACH, EXPECTED_TOTAL);

    /* Ensure /tmp exists (ignore EEXIST) */
    syscall2(SYS_MKDIR_NR, (long)"/tmp", (long)0755);

    /* Tests 1 + 2 + 3: run on a single batch of 10 threads */
    run_main_tests();

    /* Test 4: 5-iteration stability + memory leak check */
    test_repeat_stability();

    printf("\nResults: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);

    if (g_tests_failed == 0)
        printf("[stress_thread] ALL PASS\n");
    else
        printf("[stress_thread] FAILURES DETECTED\n");

    return (g_tests_failed == 0) ? 0 : 1;
}
