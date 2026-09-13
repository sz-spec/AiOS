/**
 * @file test_shm_dispatch.c
 * @brief Task 1.7 — AI-Agent IPC Foundation validation tests
 *
 * Tests:
 *   Group 1: SHM zero-copy dispatch (VOS3 custom syscalls)
 *   Group 2: Cross-process context sharing (fork + SHM)
 *   Group 3: Linux ABI aliases (shmget=29, shmat=30, shmdt=67, shmctl=31)
 *   Group 4: Large context window (64KB SHM region)
 *   Group 5: Multi-agent concurrent access (2 children sharing SHM)
 *   Group 6: Cleanup validation (destroy + rebind)
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* VOS3 custom SHM syscall numbers */
#define SYS_SHM_CREATE  410
#define SYS_SHM_DESTROY 411
#define SYS_SHM_MAP     412
#define VOS3_SHM_FLAG_PUBLIC (1U << 6)
#define SYS_SHM_UNMAP   413

/* Linux x86-64 ABI SHM syscall numbers */
#define LINUX_SHMGET    29
#define LINUX_SHMAT     30
#define LINUX_SHMCTL    31
#define LINUX_SHMDT     67

/* Fork/wait/exit */
#define SYS_FORK        57
#define SYS_WAIT4       61
#define SYS_EXIT        60
#define SYS_SLEEP       35   /* nanosleep */
#define SYS_YIELD       24   /* sched_yield */

/* VOS3 custom yield (matches VOS3_SYS_YIELD=24, Linux sched_yield) */
#define VOS3_SYS_YIELD  24
#define VOS3_SYS_SLEEP  481

/* Test helper macros */
static int g_pass = 0;
static int g_fail = 0;

#define TEST_PASS(name) do { \
    printf("[PASS] shm_dispatch: %s\n", name); \
    g_pass++; \
} while(0)

#define TEST_FAIL(name) do { \
    printf("[FAIL] shm_dispatch: %s\n", name); \
    g_fail++; \
} while(0)

/* ============================================================================
 * GROUP 1: SHM zero-copy dispatch (VOS3 custom syscalls)
 * ============================================================================ */

static void test_shm_create_map_readwrite(void)
{
    /* Create a 4KB SHM region */
    long id = syscall3(SYS_SHM_CREATE, (long)"agent_ctx_1", 4096, 0);
    if (id < 0) {
        TEST_FAIL("shm_create_map_readwrite: create");
        return;
    }
    TEST_PASS("shm_create_map_readwrite: create");

    /* Map it into our address space */
    long addr = syscall2(SYS_SHM_MAP, id, 0);
    if (addr < 0x10000) {
        TEST_FAIL("shm_create_map_readwrite: map");
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }
    TEST_PASS("shm_create_map_readwrite: map");

    /* Write AI context data */
    volatile char* ptr = (volatile char*)(uintptr_t)addr;
    const char* context = "AI_CONTEXT_V1:model=claude-4,tokens=8192";
    int i;
    for (i = 0; context[i]; i++) {
        ptr[i] = context[i];
    }
    ptr[i] = '\0';

    /* Read it back and verify */
    int match = 1;
    for (i = 0; context[i]; i++) {
        if (ptr[i] != context[i]) {
            match = 0;
            break;
        }
    }

    if (match && ptr[i] == '\0') {
        TEST_PASS("shm_create_map_readwrite: zero_copy_verify");
    } else {
        TEST_FAIL("shm_create_map_readwrite: zero_copy_verify");
    }

    /* Cleanup */
    syscall2(SYS_SHM_UNMAP, id, addr);
    syscall1(SYS_SHM_DESTROY, id);
}

/* ============================================================================
 * GROUP 2: Cross-process context sharing (fork + SHM)
 * ============================================================================ */

static void test_cross_process_context(void)
{
    /* Create SHM region for AI context exchange */
    long id = syscall3(SYS_SHM_CREATE, (long)"agent_xproc", 4096,
                       (long)VOS3_SHM_FLAG_PUBLIC);
    if (id < 0) {
        TEST_FAIL("cross_process_context: create");
        return;
    }

    /* Map in parent */
    long addr = syscall2(SYS_SHM_MAP, id, 0);
    if (addr < 0x10000) {
        TEST_FAIL("cross_process_context: parent_map");
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }

    /* Parent writes AI context window */
    volatile uint64_t* ctx = (volatile uint64_t*)(uintptr_t)addr;
    ctx[0] = 0xA1C0DEAF01ULL;    /* Magic marker */
    ctx[1] = 8192;               /* Token count */
    ctx[2] = 0xDEADBEEFCAFEULL; /* Checksum placeholder */

    pid_t child = fork();
    if (child < 0) {
        TEST_FAIL("cross_process_context: fork");
        syscall2(SYS_SHM_UNMAP, id, addr);
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }

    if (child == 0) {
        /* Child: map the same SHM and read parent's context */
        long caddr = syscall2(SYS_SHM_MAP, id, 0);
        if (caddr < 0x10000) {
            printf("[FAIL] shm_dispatch: cross_process_context: child_map\n");
            syscall1(SYS_EXIT, 1);
        }

        volatile uint64_t* cctx = (volatile uint64_t*)(uintptr_t)caddr;

        /* Verify parent's data is visible (zero-copy) */
        if (cctx[0] == 0xA1C0DEAF01ULL && cctx[1] == 8192 &&
            cctx[2] == 0xDEADBEEFCAFEULL) {
            /* Write response back to parent */
            cctx[3] = 0xC41DACULL;
            printf("[PASS] shm_dispatch: cross_process_context: child_read\n");
        } else {
            printf("[FAIL] shm_dispatch: cross_process_context: child_read\n");
        }

        syscall2(SYS_SHM_UNMAP, id, caddr);
        syscall1(SYS_EXIT, 0);
    }

    /* Parent: wait for child */
    int status;
    waitpid(child, &status, 0);

    /* Verify child wrote back */
    if (ctx[3] == 0xC41DACULL) {
        TEST_PASS("cross_process_context: parent_verify_child");
    } else {
        TEST_FAIL("cross_process_context: parent_verify_child");
    }

    syscall2(SYS_SHM_UNMAP, id, addr);
    syscall1(SYS_SHM_DESTROY, id);
}

/* ============================================================================
 * GROUP 3: Linux ABI aliases (shmget=29, shmat=30, shmdt=67, shmctl=31)
 * ============================================================================ */

static void test_linux_abi_shmget(void)
{
    /* Use Linux shmget(29) to create SHM — same handler as VOS3 SHM_CREATE(70) */
    long id = syscall3(LINUX_SHMGET, (long)"linux_abi_1", 4096, 0);
    if (id < 0) {
        TEST_FAIL("linux_abi_shmget: create_via_29");
        return;
    }
    TEST_PASS("linux_abi_shmget: create_via_29");

    /* Use Linux shmctl(31) to destroy — same handler as VOS3 SHM_DESTROY(71) */
    long ret = syscall1(LINUX_SHMCTL, id);
    if (ret < 0) {
        TEST_FAIL("linux_abi_shmget: destroy_via_31");
    } else {
        TEST_PASS("linux_abi_shmget: destroy_via_31");
    }
}

static void test_linux_abi_shmat_shmdt(void)
{
    /* Create via VOS3 number, map/unmap via Linux numbers */
    long id = syscall3(SYS_SHM_CREATE, (long)"linux_abi_2", 4096, 0);
    if (id < 0) {
        TEST_FAIL("linux_abi_shmat_shmdt: create");
        return;
    }

    /* shmat(30) → map */
    long addr = syscall2(LINUX_SHMAT, id, 0);
    if (addr < 0x10000) {
        TEST_FAIL("linux_abi_shmat_shmdt: map_via_30");
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }
    TEST_PASS("linux_abi_shmat_shmdt: map_via_30");

    /* Write through Linux-mapped address */
    volatile uint32_t* p = (volatile uint32_t*)(uintptr_t)addr;
    *p = 0x4C494E55;  /* "LINU" */

    /* Verify read */
    if (*p == 0x4C494E55) {
        TEST_PASS("linux_abi_shmat_shmdt: readwrite_via_30");
    } else {
        TEST_FAIL("linux_abi_shmat_shmdt: readwrite_via_30");
    }

    /* shmdt(67) → unmap */
    long ret = syscall2(LINUX_SHMDT, id, addr);
    if (ret < 0) {
        TEST_FAIL("linux_abi_shmat_shmdt: unmap_via_67");
    } else {
        TEST_PASS("linux_abi_shmat_shmdt: unmap_via_67");
    }

    syscall1(SYS_SHM_DESTROY, id);
}

static void test_linux_abi_cross_compat(void)
{
    /* Create via Linux(29), map via VOS3(75), unmap via Linux(67), destroy via VOS3(71) */
    long id = syscall3(LINUX_SHMGET, (long)"cross_compat", 4096, 0);
    if (id < 0) {
        TEST_FAIL("linux_abi_cross_compat: create_linux");
        return;
    }

    long addr = syscall2(SYS_SHM_MAP, id, 0);  /* VOS3 map */
    if (addr < 0x10000) {
        TEST_FAIL("linux_abi_cross_compat: map_vos3");
        syscall1(LINUX_SHMCTL, id);
        return;
    }

    volatile uint64_t* p = (volatile uint64_t*)(uintptr_t)addr;
    *p = 0xC0055C0AULL;

    if (*p == 0xC0055C0AULL) {
        TEST_PASS("linux_abi_cross_compat: cross_compat_readwrite");
    } else {
        TEST_FAIL("linux_abi_cross_compat: cross_compat_readwrite");
    }

    syscall2(LINUX_SHMDT, id, addr);      /* Linux unmap */
    syscall1(SYS_SHM_DESTROY, id);        /* VOS3 destroy */
}

/* ============================================================================
 * GROUP 4: Large context window (64KB SHM region)
 * ============================================================================ */

static void test_large_context_window(void)
{
    /* 64KB = 16 pages — simulates a large AI context window */
    long id = syscall3(SYS_SHM_CREATE, (long)"large_ctx", 65536, 0);
    if (id < 0) {
        TEST_FAIL("large_context_window: create_64kb");
        return;
    }
    TEST_PASS("large_context_window: create_64kb");

    long addr = syscall2(SYS_SHM_MAP, id, 0);
    if (addr < 0x10000) {
        TEST_FAIL("large_context_window: map_64kb");
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }

    /* Write a pattern across all 16 pages (4KB each) */
    volatile uint8_t* p = (volatile uint8_t*)(uintptr_t)addr;
    int pages_ok = 1;
    for (int page = 0; page < 16; page++) {
        int offset = page * 4096;
        /* Write pattern: page number at start of each page */
        p[offset] = (uint8_t)(page & 0xFF);
        p[offset + 1] = (uint8_t)((page >> 8) & 0xFF);
        /* Write at end of page */
        p[offset + 4095] = (uint8_t)(0xFF - page);
    }

    /* Verify all pages */
    for (int page = 0; page < 16; page++) {
        int offset = page * 4096;
        if (p[offset] != (uint8_t)(page & 0xFF) ||
            p[offset + 4095] != (uint8_t)(0xFF - page)) {
            pages_ok = 0;
            printf("    page %d: mismatch\n", page);
            break;
        }
    }

    if (pages_ok) {
        TEST_PASS("large_context_window: all_16_pages_accessible");
    } else {
        TEST_FAIL("large_context_window: all_16_pages_accessible");
    }

    syscall2(SYS_SHM_UNMAP, id, addr);
    syscall1(SYS_SHM_DESTROY, id);
}

/* ============================================================================
 * GROUP 5: Multi-agent concurrent access (2 children sharing SHM)
 * ============================================================================ */

static void test_multi_agent_concurrent(void)
{
    /* Create SHM shared between 3 processes (parent + 2 children = 3 agents) */
    long id = syscall3(SYS_SHM_CREATE, (long)"multi_agent", 4096,
                       (long)VOS3_SHM_FLAG_PUBLIC);
    if (id < 0) {
        TEST_FAIL("multi_agent_concurrent: create");
        return;
    }

    long addr = syscall2(SYS_SHM_MAP, id, 0);
    if (addr < 0x10000) {
        TEST_FAIL("multi_agent_concurrent: parent_map");
        syscall1(SYS_SHM_DESTROY, id);
        return;
    }

    volatile uint64_t* slots = (volatile uint64_t*)(uintptr_t)addr;
    /* slot[0] = agent 0 (parent) marker
     * slot[1] = agent 1 (child 1) marker
     * slot[2] = agent 2 (child 2) marker
     */
    slots[0] = 0xA6E4700;  /* parent marker */

    /* Fork child 1 */
    pid_t child1 = fork();
    if (child1 == 0) {
        long c1addr = syscall2(SYS_SHM_MAP, id, 0);
        if (c1addr < 0x10000) {
            syscall1(SYS_EXIT, 1);
        }
        volatile uint64_t* c1 = (volatile uint64_t*)(uintptr_t)c1addr;
        c1[1] = 0xA6E4701;  /* child 1 marker */
        syscall2(SYS_SHM_UNMAP, id, c1addr);
        syscall1(SYS_EXIT, 0);
    }

    /* Fork child 2 */
    pid_t child2 = fork();
    if (child2 == 0) {
        long c2addr = syscall2(SYS_SHM_MAP, id, 0);
        if (c2addr < 0x10000) {
            syscall1(SYS_EXIT, 1);
        }
        volatile uint64_t* c2 = (volatile uint64_t*)(uintptr_t)c2addr;
        c2[2] = 0xA6E4702;  /* child 2 marker */
        syscall2(SYS_SHM_UNMAP, id, c2addr);
        syscall1(SYS_EXIT, 0);
    }

    /* Wait for both children */
    int s1, s2;
    waitpid(child1, &s1, 0);
    waitpid(child2, &s2, 0);

    /* Verify all 3 agents wrote to their slots */
    if (slots[0] == 0xA6E4700 && slots[1] == 0xA6E4701 && slots[2] == 0xA6E4702) {
        TEST_PASS("multi_agent_concurrent: three_agents_shared");
    } else {
        printf("    slots: [0]=0x%lx [1]=0x%lx [2]=0x%lx\n",
               (unsigned long)slots[0], (unsigned long)slots[1],
               (unsigned long)slots[2]);
        TEST_FAIL("multi_agent_concurrent: three_agents_shared");
    }

    syscall2(SYS_SHM_UNMAP, id, addr);
    syscall1(SYS_SHM_DESTROY, id);
}

/* ============================================================================
 * GROUP 6: Cleanup validation (destroy + rebind)
 * ============================================================================ */

static void test_cleanup_rebind(void)
{
    /* Create, map, unmap, destroy — then create again with same name */
    long id1 = syscall3(SYS_SHM_CREATE, (long)"rebind_test", 4096, 0);
    if (id1 < 0) {
        TEST_FAIL("cleanup_rebind: first_create");
        return;
    }

    long addr1 = syscall2(SYS_SHM_MAP, id1, 0);
    if (addr1 < 0x10000) {
        TEST_FAIL("cleanup_rebind: first_map");
        syscall1(SYS_SHM_DESTROY, id1);
        return;
    }

    /* Write data */
    volatile uint64_t* p1 = (volatile uint64_t*)(uintptr_t)addr1;
    *p1 = 0xF1A57ULL;

    /* Unmap and destroy */
    syscall2(SYS_SHM_UNMAP, id1, addr1);
    syscall1(SYS_SHM_DESTROY, id1);

    /* Recreate with same name — should succeed (slot freed) */
    long id2 = syscall3(SYS_SHM_CREATE, (long)"rebind_test", 4096, 0);
    if (id2 < 0) {
        TEST_FAIL("cleanup_rebind: rebind_after_destroy");
        return;
    }

    long addr2 = syscall2(SYS_SHM_MAP, id2, 0);
    if (addr2 < 0x10000) {
        TEST_FAIL("cleanup_rebind: rebind_map");
        syscall1(SYS_SHM_DESTROY, id2);
        return;
    }

    /* New region should be fresh (zeroed or different from old data) */
    TEST_PASS("cleanup_rebind: rebind_after_destroy");

    syscall2(SYS_SHM_UNMAP, id2, addr2);
    syscall1(SYS_SHM_DESTROY, id2);
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[], char *envp[])
{
    (void)argc; (void)argv; (void)envp;

    printf("\n=== test_shm_dispatch: AI-Agent IPC Foundation ===\n\n");

    printf("--- Group 1: SHM zero-copy dispatch ---\n");
    test_shm_create_map_readwrite();

    printf("\n--- Group 2: Cross-process context sharing ---\n");
    test_cross_process_context();

    printf("\n--- Group 3: Linux ABI aliases ---\n");
    test_linux_abi_shmget();
    test_linux_abi_shmat_shmdt();
    test_linux_abi_cross_compat();

    printf("\n--- Group 4: Large context window (64KB) ---\n");
    test_large_context_window();

    printf("\n--- Group 5: Multi-agent concurrent access ---\n");
    test_multi_agent_concurrent();

    printf("\n--- Group 6: Cleanup validation ---\n");
    test_cleanup_rebind();

    printf("\n=== test_shm_dispatch: %d PASS, %d FAIL ===\n\n",
           g_pass, g_fail);

    return g_fail > 0 ? 1 : 0;
}
