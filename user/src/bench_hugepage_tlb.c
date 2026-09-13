/**
 * @file bench_hugepage_tlb.c
 * @brief HugePage TLB Performance Benchmark
 *
 * Tests 2MB HugePage support via SHM:
 * 1. hugepage_shm_create: Create 4MB SHM with SHM_HUGETLB flag
 * 2. hugepage_shm_map_write: Map, write pattern, read back, verify
 * 3. hugepage_throughput: Sequential write 4MB (4KB vs 2MB pages)
 * 4. hugepage_no_leak: Create+destroy, verify free pages return to baseline
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
#define SYS_SHM_UNMAP   413
#define SYS_SHM_SIZE    414  /* was 74, collides with SYS_FSYNC */

/* SHM flags */
#define VOS3_SHM_FLAG_HUGETLB  (1U << 4)

/* Configuration */
#define SHM_SIZE_4MB    (4U * 1024U * 1024U)  /* 4MB */
#define LARGE_PAGE_SIZE (2U * 1024U * 1024U)  /* 2MB */

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

/* ============================================================================
 * TEST 1: HugePage SHM Create
 * ============================================================================ */

static void test_hugepage_shm_create(void)
{
    printf("\n--- Test 1: hugepage_shm_create ---\n");

    /* Create 4MB SHM with HUGETLB flag */
    long id = syscall3(SYS_SHM_CREATE, (long)"bench_huge", SHM_SIZE_4MB,
                       VOS3_SHM_FLAG_HUGETLB);

    if (id < 0 || id == 0xFFFFFFFF) {
        TEST_FAIL("hugepage_shm_create", "SHM create with HUGETLB failed");
        return;
    }

    printf("  Created HugePage SHM id=%ld, requested size=%u\n",
           id, SHM_SIZE_4MB);

    /* Verify size (should be rounded up to 2MB boundary = 4MB) */
    long sz = syscall1(SYS_SHM_SIZE, id);
    if (sz >= (long)SHM_SIZE_4MB) {
        printf("  Actual size=%ld (expected >= %u)\n", sz, SHM_SIZE_4MB);
        TEST_PASS("hugepage_shm_create");
    } else {
        printf("  Actual size=%ld (expected >= %u)\n", sz, SHM_SIZE_4MB);
        TEST_FAIL("hugepage_shm_create", "SHM size smaller than requested");
    }

    /* Cleanup */
    syscall1(SYS_SHM_DESTROY, id);
}

/* ============================================================================
 * TEST 2: HugePage SHM Map + Write + Verify
 * ============================================================================ */

static void test_hugepage_shm_map_write(void)
{
    printf("\n--- Test 2: hugepage_shm_map_write ---\n");

    /* Create 4MB HugePage SHM */
    long id = syscall3(SYS_SHM_CREATE, (long)"bench_huge_rw", SHM_SIZE_4MB,
                       VOS3_SHM_FLAG_HUGETLB);

    if (id < 0 || id == 0xFFFFFFFF) {
        TEST_FAIL("hugepage_shm_map_write", "SHM create failed");
        return;
    }

    /* Map into our address space */
    long addr = syscall2(SYS_SHM_MAP, id, 0);
    if (addr <= 0) {
        TEST_FAIL("hugepage_shm_map_write", "SHM map failed");
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }

    volatile unsigned char* ptr = (volatile unsigned char*)addr;
    printf("  Mapped HugePage SHM at 0x%lx\n", (unsigned long)addr);

    /* Write pattern: byte[i] = (i * 7 + 0xAB) & 0xFF */
    for (unsigned long i = 0; i < SHM_SIZE_4MB; i += 8) {
        volatile unsigned long long* p = (volatile unsigned long long*)(ptr + i);
        unsigned char b = (unsigned char)((i * 7 + 0xAB) & 0xFF);
        unsigned long long val = b;
        val |= (val << 8) | (val << 16) | (val << 24);
        val |= (val << 32);
        *p = val;
    }

    /* Read back and verify (sample every 4KB) */
    int errors = 0;
    for (unsigned long i = 0; i < SHM_SIZE_4MB; i += 4096) {
        unsigned char expected = (unsigned char)((i * 7 + 0xAB) & 0xFF);
        if (ptr[i] != expected) {
            errors++;
            if (errors <= 3) {
                printf("  MISMATCH at offset 0x%lx: got 0x%02x expected 0x%02x\n",
                       i, ptr[i], expected);
            }
        }
    }

    if (errors == 0) {
        TEST_PASS("hugepage_shm_map_write");
    } else {
        printf("  %d verification errors\n", errors);
        TEST_FAIL("hugepage_shm_map_write", "data verification failed");
    }

    /* Cleanup */
    syscall2(SYS_SHM_UNMAP, id, addr);
    syscall1(SYS_SHM_DESTROY, id);
}

/* ============================================================================
 * TEST 3: HugePage Throughput Comparison
 * ============================================================================ */

static void write_sequential(volatile unsigned char* ptr, unsigned long size)
{
    for (unsigned long i = 0; i < size; i += 8) {
        volatile unsigned long long* p = (volatile unsigned long long*)(ptr + i);
        *p = 0xDEADBEEFCAFEBABEULL;
    }
}

static void test_hugepage_throughput(void)
{
    printf("\n--- Test 3: hugepage_throughput ---\n");

    /* Phase A: 4KB SHM throughput */
    long id_4k = syscall3(SYS_SHM_CREATE, (long)"bench_4k", SHM_SIZE_4MB, 0);
    if (id_4k < 0 || id_4k == 0xFFFFFFFF) {
        TEST_FAIL("hugepage_throughput", "4KB SHM create failed");
        return;
    }

    long addr_4k = syscall2(SYS_SHM_MAP, id_4k, 0);
    if (addr_4k <= 0) {
        TEST_FAIL("hugepage_throughput", "4KB SHM map failed");
        syscall1(SYS_SHM_DESTROY, id_4k);
        return;
    }

    unsigned long t0 = get_uptime_ms();
    unsigned long long c0 = rdtsc();
    write_sequential((volatile unsigned char*)addr_4k, SHM_SIZE_4MB);
    unsigned long long c1 = rdtsc();
    unsigned long t1 = get_uptime_ms();

    unsigned long ms_4k = (t1 > t0) ? (t1 - t0) : 1;
    unsigned long long cycles_4k = c1 - c0;
    unsigned long mbs_4k = (ms_4k > 0) ? (SHM_SIZE_4MB / 1024 / 1024 * 1000 / ms_4k) : 0;

    printf("  4KB pages: %llu cycles, %lu ms, ~%lu MB/s\n",
           cycles_4k, ms_4k, mbs_4k);

    syscall2(SYS_SHM_UNMAP, id_4k, addr_4k);
    syscall1(SYS_SHM_DESTROY, id_4k);

    /* Phase B: 2MB HugePage SHM throughput */
    long id_2m = syscall3(SYS_SHM_CREATE, (long)"bench_2m", SHM_SIZE_4MB,
                          VOS3_SHM_FLAG_HUGETLB);
    if (id_2m < 0 || id_2m == 0xFFFFFFFF) {
        TEST_FAIL("hugepage_throughput", "HugePage SHM create failed");
        return;
    }

    long addr_2m = syscall2(SYS_SHM_MAP, id_2m, 0);
    if (addr_2m <= 0) {
        TEST_FAIL("hugepage_throughput", "HugePage SHM map failed");
        syscall1(SYS_SHM_DESTROY, id_2m);
        return;
    }

    unsigned long t2 = get_uptime_ms();
    unsigned long long c2 = rdtsc();
    write_sequential((volatile unsigned char*)addr_2m, SHM_SIZE_4MB);
    unsigned long long c3 = rdtsc();
    unsigned long t3 = get_uptime_ms();

    unsigned long ms_2m = (t3 > t2) ? (t3 - t2) : 1;
    unsigned long long cycles_2m = c3 - c2;
    unsigned long mbs_2m = (ms_2m > 0) ? (SHM_SIZE_4MB / 1024 / 1024 * 1000 / ms_2m) : 0;

    printf("  2MB pages: %llu cycles, %lu ms, ~%lu MB/s\n",
           cycles_2m, ms_2m, mbs_2m);

    syscall2(SYS_SHM_UNMAP, id_2m, addr_2m);
    syscall1(SYS_SHM_DESTROY, id_2m);

    /* Verdict: HugePage should not be significantly slower */
    printf("  Cycle ratio (4KB/2MB): %llu/%llu\n", cycles_4k, cycles_2m);

    /* On QEMU single-CPU, the difference may be small, but HugePages
       should never be significantly slower than 4KB pages */
    if (cycles_2m <= cycles_4k * 2) {
        TEST_PASS("hugepage_throughput");
    } else {
        TEST_FAIL("hugepage_throughput", "HugePage significantly slower than 4KB");
    }
}

/* ============================================================================
 * TEST 4: HugePage No Leak
 * ============================================================================ */

static void test_hugepage_no_leak(void)
{
    printf("\n--- Test 4: hugepage_no_leak ---\n");

    vos3_sysinfo_t info_before, info_after;

    if (get_sysinfo(&info_before) != 0) {
        TEST_FAIL("hugepage_no_leak", "sysinfo failed (before)");
        return;
    }

    printf("  Free pages before: %lu\n", info_before.free_pages);

    /* Create and destroy 3 HugePage SHM regions */
    for (int i = 0; i < 3; i++) {
        long id = syscall3(SYS_SHM_CREATE, (long)"bench_leak", SHM_SIZE_4MB,
                           VOS3_SHM_FLAG_HUGETLB);
        if (id < 0 || id == 0xFFFFFFFF) {
            printf("  Warning: HugePage SHM create #%d failed\n", i);
            continue;
        }

        /* Map, write, unmap, destroy */
        long addr = syscall2(SYS_SHM_MAP, id, 0);
        if (addr > 0) {
            volatile unsigned char* ptr = (volatile unsigned char*)addr;
            /* Touch every page */
            for (unsigned long off = 0; off < SHM_SIZE_4MB; off += 4096)
                ptr[off] = 0x42;
            syscall2(SYS_SHM_UNMAP, id, addr);
        }
        syscall1(SYS_SHM_DESTROY, id);
    }

    if (get_sysinfo(&info_after) != 0) {
        TEST_FAIL("hugepage_no_leak", "sysinfo failed (after)");
        return;
    }

    printf("  Free pages after:  %lu\n", info_after.free_pages);

    /* Allow small delta for page tables created during mapping */
    long delta = (long)info_before.free_pages - (long)info_after.free_pages;
    printf("  Delta: %ld pages (%ld KB)\n", delta, delta * 4);

    /* Tolerate up to 64 pages (256KB) of page table overhead */
    if (delta < 64) {
        TEST_PASS("hugepage_no_leak");
    } else {
        printf("  LEAK: %ld pages not returned!\n", delta);
        TEST_FAIL("hugepage_no_leak", "memory leak detected");
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[], char *envp[])
{
    (void)argc; (void)argv; (void)envp;

    printf("===========================================\n");
    printf("  VOS3 HugePage TLB Benchmark\n");
    printf("  Testing 2MB page support via SHM\n");
    printf("===========================================\n");

    test_hugepage_shm_create();
    test_hugepage_shm_map_write();
    test_hugepage_throughput();
    test_hugepage_no_leak();

    printf("\n===========================================\n");
    printf("  Results: %d PASS, %d FAIL\n", g_pass, g_fail);
    printf("===========================================\n");

    return (g_fail > 0) ? 1 : 0;
}
