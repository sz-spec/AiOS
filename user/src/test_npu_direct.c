/**
 * @file test_npu_direct.c
 * @brief Phase 1.5 NPU-Direct Validation Test
 *
 * Tests device MMIO mapping via SHM with PAT/cache policy:
 * 1. npu_bar_shm_create: Create device SHM at 0xFD000000 (NPU BAR)
 * 2. npu_bar_shm_map: Map into user space with Write-Combine
 * 3. npu_wc_write_performance: Write 1MB sequential data
 * 4. npu_shm_cleanup: Unmap + destroy device SHM
 * 5. npu_ram_reject: Verify managed RAM is rejected as device
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* Syscall numbers */
#define SYS_EXIT             60
#define SYS_SHM_CREATE       410
#define SYS_SHM_DESTROY      411
#define SYS_SHM_MAP          412
#define SYS_SHM_UNMAP        413
#define SYS_SHM_CREATE_DEVICE 415
#define SYS_APP_CTX_CREATE   460
#define SYS_APP_CTX_DESTROY  461
#define SYS_APP_CTX_SWITCH   462

/* SHM flags */
#define VOS3_SHM_FLAG_DEVICE  (1U << 5)

/* NPU BAR simulation address (outside QEMU managed RAM at 0x0-0x80000000) */
#define NPU_BAR_PHYS         0xFD000000ULL
#define NPU_BAR_SIZE         (2U * 1024U * 1024U)  /* 2MB */

/* Test framework */
static int g_pass = 0;
static int g_fail = 0;
#define TEST_PASS(name) do { printf("[PASS] %s\n", name); g_pass++; } while(0)
#define TEST_FAIL(name, msg) do { printf("[FAIL] %s: %s\n", name, msg); g_fail++; } while(0)

static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

/* Global state for cross-test sharing */
static long g_shm_id = -1;
static void* g_mapped_addr = NULL;
static long g_app_ctx_created = 0;

/* ============================================================================
 * TEST 1: NPU BAR SHM Create
 * ============================================================================ */

static void test_npu_bar_shm_create(void)
{
    printf("\n--- Test 1: npu_bar_shm_create ---\n");

    /* First, create an AI Guard app context (required for hardware access) */
    long ret = syscall1(SYS_APP_CTX_CREATE, 7);
    if (ret < 0) {
        TEST_FAIL("npu_bar_shm_create", "failed to create AI guard context");
        return;
    }
    g_app_ctx_created = 1;

    /* Switch to app context 7 */
    ret = syscall1(SYS_APP_CTX_SWITCH, 7);
    if (ret < 0) {
        TEST_FAIL("npu_bar_shm_create", "failed to switch AI guard context");
        return;
    }

    /* Create device SHM: syscall4(415, name, phys_addr, size, flags) */
    long id = syscall4(SYS_SHM_CREATE_DEVICE,
                       (long)"npu_bar",
                       (long)NPU_BAR_PHYS,
                       (long)NPU_BAR_SIZE,
                       (long)VOS3_SHM_FLAG_DEVICE);

    if (id < 0 || id == (long)0xFFFFFFFFU) {
        TEST_FAIL("npu_bar_shm_create", "SHM device create returned invalid ID");
        return;
    }

    g_shm_id = id;
    printf("  Device SHM created: id=%ld, phys=0x%llx, size=%u\n",
           id, (unsigned long long)NPU_BAR_PHYS, NPU_BAR_SIZE);
    TEST_PASS("npu_bar_shm_create");
}

/* ============================================================================
 * TEST 2: NPU BAR SHM Map
 * ============================================================================ */

static void test_npu_bar_shm_map(void)
{
    printf("\n--- Test 2: npu_bar_shm_map ---\n");

    if (g_shm_id < 0) {
        TEST_FAIL("npu_bar_shm_map", "skipped (no SHM ID from test 1)");
        return;
    }

    /* Map into user space */
    long addr = syscall2(SYS_SHM_MAP, g_shm_id, 0);
    if (addr == 0 || addr < 0) {
        TEST_FAIL("npu_bar_shm_map", "SHM map returned NULL/error");
        return;
    }

    g_mapped_addr = (void*)addr;
    printf("  Device SHM mapped at user addr 0x%lx\n", addr);
    TEST_PASS("npu_bar_shm_map");
}

/* ============================================================================
 * TEST 3: Write-Combine Write Performance
 * ============================================================================ */

static void test_npu_wc_write_performance(void)
{
    printf("\n--- Test 3: npu_wc_write_performance ---\n");

    if (g_mapped_addr == NULL) {
        TEST_FAIL("npu_wc_write_performance", "skipped (no mapping from test 2)");
        return;
    }

    /*
     * Write 1MB of sequential 64-bit stores.
     * In QEMU this is emulated RAM at the NPU BAR address,
     * but validates the WC mapping path works.
     */
    volatile uint64_t* ptr = (volatile uint64_t*)g_mapped_addr;
    size_t count = (1024U * 1024U) / sizeof(uint64_t);  /* 1MB = 131072 qwords */

    unsigned long long start = rdtsc();

    for (size_t i = 0; i < count; i++) {
        ptr[i] = (uint64_t)(i ^ 0xDEADBEEFULL);
    }

    unsigned long long end = rdtsc();
    unsigned long long cycles = end - start;
    unsigned long long bytes = count * sizeof(uint64_t);

    /* Verify a few written values */
    int ok = 1;
    if (ptr[0] != (0ULL ^ 0xDEADBEEFULL)) ok = 0;
    if (ptr[1] != (1ULL ^ 0xDEADBEEFULL)) ok = 0;
    if (ptr[count - 1] != ((uint64_t)(count - 1) ^ 0xDEADBEEFULL)) ok = 0;

    if (!ok) {
        TEST_FAIL("npu_wc_write_performance", "data verification failed");
        return;
    }

    printf("  Wrote %llu bytes in %llu cycles (%llu cycles/byte)\n",
           bytes, cycles, cycles / bytes);
    TEST_PASS("npu_wc_write_performance");
}

/* ============================================================================
 * TEST 4: SHM Cleanup
 * ============================================================================ */

static void test_npu_shm_cleanup(void)
{
    printf("\n--- Test 4: npu_shm_cleanup ---\n");

    if (g_shm_id < 0) {
        TEST_FAIL("npu_shm_cleanup", "skipped (no SHM ID)");
        return;
    }

    /* Unmap */
    if (g_mapped_addr != NULL) {
        long ret = syscall2(SYS_SHM_UNMAP, g_shm_id, (long)g_mapped_addr);
        if (ret < 0) {
            TEST_FAIL("npu_shm_cleanup", "unmap failed");
            return;
        }
        g_mapped_addr = NULL;
    }

    /* Destroy */
    long ret = syscall1(SYS_SHM_DESTROY, g_shm_id);
    if (ret < 0) {
        TEST_FAIL("npu_shm_cleanup", "destroy failed");
        return;
    }

    g_shm_id = -1;
    printf("  Device SHM unmap + destroy succeeded\n");
    TEST_PASS("npu_shm_cleanup");
}

/* ============================================================================
 * TEST 5: RAM Rejection
 * ============================================================================ */

static void test_npu_ram_reject(void)
{
    printf("\n--- Test 5: npu_ram_reject ---\n");

    /*
     * Try to create device SHM with phys=0x1000 (managed RAM).
     * vos3_pmm_is_device_range() should reject this.
     */
    long id = syscall4(SYS_SHM_CREATE_DEVICE,
                       (long)"bad_dev",
                       (long)0x1000,          /* managed RAM, not device */
                       (long)(4096),
                       (long)VOS3_SHM_FLAG_DEVICE);

    if (id == (long)0xFFFFFFFFU || id < 0) {
        printf("  Correctly rejected managed RAM as device (ret=%ld)\n", id);
        TEST_PASS("npu_ram_reject");
    } else {
        /* Should not reach here — clean up if it did */
        syscall1(SYS_SHM_DESTROY, id);
        TEST_FAIL("npu_ram_reject", "managed RAM was NOT rejected");
    }
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("\n========================================\n");
    printf("  NPU-Direct Validation Test (Phase 1.5)\n");
    printf("========================================\n");

    test_npu_bar_shm_create();
    test_npu_bar_shm_map();
    test_npu_wc_write_performance();
    test_npu_shm_cleanup();
    test_npu_ram_reject();

    /* Cleanup AI Guard context */
    if (g_app_ctx_created) {
        syscall1(SYS_APP_CTX_SWITCH, 0);   /* switch away */
        syscall1(SYS_APP_CTX_DESTROY, 7);  /* destroy context */
    }

    printf("\n========================================\n");
    printf("  NPU-DIRECT RESULTS: %d passed, %d failed\n", g_pass, g_fail);
    printf("========================================\n\n");

    return g_fail > 0 ? 1 : 0;
}
