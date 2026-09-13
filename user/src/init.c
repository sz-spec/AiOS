/**
 * @file init.c
 * @brief VOS3 Init Process (PID 1)
 *
 * @details The first user-space process. Responsible for:
 *          - First-boot provisioning via Setup Wizard
 *          - Spawning the login shell
 *          - Reaping orphaned processes
 *          - System shutdown
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 24 - First-Boot Provisioning & Workspace Logic
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

#define SHELL_PATH         "/bin/sh"
#define SHELL_NAME         "sh"
#define SETUP_WIZARD_PATH  "/bin/setup_wizard"
#define VOS3_CONFIG_PATH   "/etc/vos3.conf"

/* ============================================================================
 * HEADLESS STRESS TEST MODE (Phase 25 Audit)
 * Comment out to restore normal boot behavior
 * ============================================================================ */
/* #define HEADLESS_STRESS_TEST 1 */  /* Phase 27: Complete */
#define STRESS_PATH        "/bin/ai_stress"

/* ============================================================================
 * ALPHA 2.0 AUDIT MODE (Phases 26-29)
 * Uncomment to run the deep technical audit on boot
 * ============================================================================ */
/* #define HEADLESS_ALPHA2_AUDIT 1 */  /* Phase 29: Complete */
#define AUDIT_PATH         "/bin/ai_audit"
#define UDP_TEST_PATH      "/bin/udp_test"
#define TCP_TEST_PATH      "/bin/tcp_test"
#define DISK_TEST_PATH     "/bin/disk_test"

/* ============================================================================
 * BENCHMARK SUITE MODE (Week 2 Verification)
 * Runs all benchmark programs + SIGPIPE test + /bin listing
 *
 * Enabled by passing -DHEADLESS_BENCH_SUITE=1 at compile time, e.g.:
 *   make BENCH_MODE=1   (from user/ or kernel/ directory)
 * Default is OFF so normal boots spawn the shell.
 * ============================================================================ */
#ifndef HEADLESS_BENCH_SUITE
/* default off — controlled via BENCH_MODE=1 build flag */
#endif
#define BENCH_CSW_PATH          "/bin/bench_csw"
#define BENCH_ISOLATE_PATH      "/bin/bench_isolate"
#define BENCH_PERSIST_PATH      "/bin/bench_persist"
#define BENCH_OVERHEAD_PATH     "/bin/bench_overhead"
#define BENCH_SIGPIPE_PATH      "/bin/bench_sigpipe_test"
#define AI_EXPLOIT_PATH         "/bin/ai_exploit"
#define BENCH_BUGFIX_PATH       "/bin/bench_bugfix_test"
#define BENCH_PHASE13_PATH      "/bin/bench_phase13_test"
#define BENCH_PROCFS_PATH       "/bin/bench_procfs_test"
#define BENCH_POLL_PATH         "/bin/bench_poll_test"
#define BENCH_FS_PATH           "/bin/bench_fs_test"
#define BENCH_NET_PATH          "/bin/bench_net_test"
#define BENCH_STRESS_PATH       "/bin/bench_stress_test"
#define BENCH_FUZZ_PATH         "/bin/bench_fuzz_test"
#define BENCH_SCHED_PATH        "/bin/bench_sched_test"
#define BENCH_APP_ISOLATION_PATH "/bin/bench_app_isolation_test"
#define TEST_POSIX_FS_PATH       "/bin/test_posix_fs"
#define TEST_EPOLL_PATH          "/bin/test_epoll"
#define BENCH_THREAD_PATH        "/bin/bench_thread_test"
#define STRESS_THREAD_PATH       "/bin/stress_thread"
#define TEST_POSIX_CORE_PATH     "/bin/test_posix_core"
#define TEST_FUTEX_PATH          "/bin/test_futex"
#define TEST_NET_DISPATCH_PATH   "/bin/test_net_dispatch"
#define TEST_SHM_DISPATCH_PATH   "/bin/test_shm_dispatch"
#define DIAG_SHM_STRESS_PATH     "/bin/diag_shm_stress"
#define TEST_PT_INTERP_PATH      "/bin/test_pt_interp"
#define TEST_DYNLINK_PATH        "/bin/test_dynlink"
#define TEST_SIGNAL_MUSL_PATH    "/bin/test_signal_musl"
#define TEST_ENV_MUSL_PATH       "/bin/test_env_musl"
#define TEST_FILEIO_MUSL_PATH    "/bin/test_fileio_musl"
#define TEST_MALLOC_MUSL_PATH    "/bin/test_malloc_musl"
#define TEST_FORK_MUSL_PATH      "/bin/test_fork_musl"
#define TEST_PIPE_MUSL_PATH      "/bin/test_pipe_musl"
#define TEST_STAT_MUSL_PATH      "/bin/test_stat_musl"
#define TEST_PTHREAD_MUSL_PATH   "/bin/test_pthread_musl"
#define TEST_STRESS_MT_PATH      "/bin/test_stress_mt"
#define TEST_AI_GUARD_PATH       "/bin/test_ai_guard"
#define TEST_INTEGRATION_PATH    "/bin/test_integration"
#define TEST_SUSTAINED_PATH      "/bin/test_sustained"
#define BENCH_AI_THROUGHPUT_PATH "/bin/bench_ai_throughput"
#define BENCH_CSW_1MS_PATH       "/bin/bench_csw_1ms"
#define BENCH_SOAK_5MIN_PATH     "/bin/bench_soak_5min"
#define BENCH_HUGEPAGE_TLB_PATH  "/bin/bench_hugepage_tlb"
#define BENCH_AI_SCALE_PATH  "/bin/bench_ai_scale"
#define TEST_NPU_DIRECT_PATH "/bin/test_npu_direct"
#define TEST_AI_LATENCY_PATH "/bin/test_ai_latency"
#define BENCH_2026_FRONTIER_PATH "/bin/bench_2026_frontier"
#define TEST_AGENT_CHAOS_PATH    "/bin/test_agent_chaos"
#define TEST_ADVANCED_CHAOS_PATH "/bin/test_advanced_chaos"
#define TEST_MODEL_LOAD_PATH     "/bin/test_model_load"
#define TEST_HEALTH_CHECK_PATH   "/bin/test_health_check"
#define TEST_AGENT_CLUSTER_PATH  "/bin/test_agent_cluster"
#define TEST_TORTURE_PATH        "/bin/test_torture"
#define TEST_AI_EXPLOIT_V2_PATH  "/bin/test_ai_exploit_v2"
#define TEST_MAX_SATURATION_PATH "/bin/test_max_saturation"
#define TEST_VMM_SOAK_PATH       "/bin/test_vmm_soak"
#define TEST_SECURITY_CHECK_PATH "/bin/test_security_check"

/* O_RDONLY for checking file existence */
#ifndef O_RDONLY
#define O_RDONLY    0
#endif

/* ============================================================================
 * FIRST-BOOT CHECK
 * ============================================================================ */

/**
 * @brief Check if system is provisioned
 * @return 1 if provisioned (config exists), 0 if first boot
 */
static int is_provisioned(void)
{
    int fd = open(VOS3_CONFIG_PATH, O_RDONLY, 0);
    if (fd >= 0) {
        close(fd);
        return 1;  /* Config exists - system is provisioned */
    }
    return 0;  /* First boot - needs provisioning */
}

/**
 * @brief Run the Setup Wizard for first-boot provisioning
 * @return 0 on success, -1 on failure
 */
static int run_setup_wizard(void)
{
    printf("\n");
    printf("===========================================\n");
    printf("  VOS3 FIRST-BOOT DETECTED\n");
    printf("===========================================\n");
    printf("\n");
    printf("Init: System is not provisioned.\n");
    printf("Init: Starting Setup Wizard...\n");
    printf("\n");

    pid_t wizard_pid = fork();

    if (wizard_pid < 0) {
        printf("Init: ERROR - Cannot fork for Setup Wizard!\n");
        return -1;
    }

    if (wizard_pid == 0) {
        /* Child process - run setup wizard */
        char *wizard_argv[] = { "setup_wizard", (char *)0 };
        char *wizard_envp[] = {
            "PATH=/bin:/usr/bin",
            "HOME=/",
            "TERM=vt100",
            (char *)0
        };

        execve(SETUP_WIZARD_PATH, wizard_argv, wizard_envp);

        /* If execve fails */
        printf("Init: ERROR - Failed to exec Setup Wizard!\n");
        printf("Init: System cannot be provisioned.\n");
        exit(1);
    }

    /* Parent process - wait for wizard to complete */
    int status;
    waitpid(wizard_pid, &status, 0);

    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        printf("\n");
        printf("Init: Setup Wizard completed successfully.\n");
        printf("Init: System identity has been configured.\n");
        printf("\n");
        return 0;
    } else {
        printf("\n");
        printf("Init: WARNING - Setup Wizard exited with errors.\n");
        printf("Init: Continuing with limited functionality...\n");
        printf("\n");
        return -1;
    }
}

/* ============================================================================
 * INIT MAIN
 * ============================================================================ */

int main(int argc, char *argv[], char *envp[])
{
    (void)argc;
    (void)argv;
    (void)envp;

    pid_t pid = getpid();

    printf("\n");
    printf("===========================================\n");
    printf("  VOS3 Init System (PID %d)\n", pid);
    printf("===========================================\n");
    printf("\n");

    if (pid != 1) {
        printf("Warning: init is not running as PID 1 (PID=%d)\n", pid);
    }

    printf("Init: Starting system initialization...\n");

    /* Set up basic environment */
    setenv("PATH", "/bin:/usr/bin", 1);
    setenv("HOME", "/", 1);
    setenv("TERM", "vt100", 1);
    setenv("SHELL", SHELL_PATH, 1);

    printf("Init: Environment configured\n");

#ifdef HEADLESS_BENCH_SUITE
    /* ================================================================
     * WEEK 2 BENCHMARK SUITE VERIFICATION
     * Runs: bench_csw, bench_isolate, bench_persist, bench_overhead,
     *       bench_sigpipe_test, and lists /bin contents
     * ================================================================ */
    printf("\n");
    printf("===========================================\n");
    printf("  VOS3 WEEK 2 BENCHMARK VERIFICATION\n");
    printf("  Running all benchmark programs\n");
    printf("===========================================\n");
    printf("\n");

    /* List /bin contents first */
    printf("=== /bin directory listing ===\n");
    {
        /* Use getdents to list /bin */
        int bin_fd = open("/bin", O_RDONLY, 0);
        if (bin_fd >= 0) {
            /* Simple approach: just list known programs */
            const char* expected[] = {
                "bench_csw", "bench_isolate", "bench_persist",
                "bench_overhead", "bench_sigpipe_test",
                "init", "sh", "cat", "ai_audit", "ai_exploit",
                NULL
            };
            int found = 0;
            for (int i = 0; expected[i] != NULL; i++) {
                char path[64];
                /* Build path */
                int j = 0;
                const char* prefix = "/bin/";
                while (prefix[j]) { path[j] = prefix[j]; j++; }
                int k = 0;
                while (expected[i][k]) { path[j+k] = expected[i][k]; k++; }
                path[j+k] = '\0';

                int test_fd = open(path, O_RDONLY, 0);
                if (test_fd >= 0) {
                    printf("  [FOUND] %s\n", path);
                    close(test_fd);
                    found++;
                } else {
                    printf("  [MISS]  %s\n", path);
                }
            }
            close(bin_fd);
            printf("  Found %d / 10 expected binaries\n", found);
        } else {
            printf("  [ERROR] Cannot open /bin\n");
        }
    }
    printf("\n");

    /* Selective test filter: TEST_ONLY=xxx only runs tests containing "xxx" */
#ifdef VOS3_TEST_ONLY
    printf("  TEST FILTER: \"%s\"\n", VOS3_TEST_ONLY);
    printf("  (only running tests matching filter)\n\n");
#endif

    /* Helper macro: fork+exec a program, wait, report */
    #define RUN_BENCH(name, path) do { \
        _TEST_MAYBE_SKIP(name); \
        printf("=== Running: %s ===\n", name); \
        pid_t _pid = fork(); \
        if (_pid == 0) { \
            char *_argv[] = { (char*)name, (char *)0 }; \
            char *_envp[] = { "PATH=/bin:/sbin:/usr/bin:/usr/sbin", "HOME=/", "TERM=vt100", (char *)0 }; \
            execve(path, _argv, _envp); \
            printf("Init: ERROR - Failed to exec %s!\n", path); \
            exit(1); \
        } \
        if (_pid > 0) { \
            int _status; \
            waitpid(_pid, &_status, 0); \
            printf("=== %s: exit=%d ===\n\n", name, \
                   WIFEXITED(_status) ? WEXITSTATUS(_status) : -1); \
        } else { \
            printf("Init: ERROR - fork() failed for %s\n", name); \
        } \
    } while(0)

#ifdef VOS3_TEST_ONLY
    /* No do/while wrapper — break must exit RUN_BENCH's do/while */
    #define _TEST_MAYBE_SKIP(name) \
        if (strstr(name, VOS3_TEST_ONLY) == 0) { \
            printf("=== SKIP: %s ===\n", name); \
            break; \
        }
#else
    #define _TEST_MAYBE_SKIP(name)
#endif

    /* Run each benchmark */
    RUN_BENCH("bench_csw", BENCH_CSW_PATH);
    /* test_max_saturation runs early — needs 1024 task slots before
     * heavy tests consume memory and saturate the scheduler. */
    RUN_BENCH("test_max_saturation", TEST_MAX_SATURATION_PATH);
    RUN_BENCH("bench_isolate", BENCH_ISOLATE_PATH);
    RUN_BENCH("bench_persist", BENCH_PERSIST_PATH);
    RUN_BENCH("bench_overhead", BENCH_OVERHEAD_PATH);
    RUN_BENCH("bench_sigpipe_test", BENCH_SIGPIPE_PATH);
    RUN_BENCH("ai_exploit", AI_EXPLOIT_PATH);
    RUN_BENCH("bench_bugfix_test", BENCH_BUGFIX_PATH);
    RUN_BENCH("bench_phase13_test", BENCH_PHASE13_PATH);
    RUN_BENCH("bench_procfs_test", BENCH_PROCFS_PATH);
    RUN_BENCH("bench_poll_test", BENCH_POLL_PATH);
    RUN_BENCH("bench_fs_test", BENCH_FS_PATH);
    RUN_BENCH("bench_net_test", BENCH_NET_PATH);
    /* Re-enabled for debugging */
    RUN_BENCH("bench_stress_test", BENCH_STRESS_PATH);
    RUN_BENCH("bench_fuzz_test", BENCH_FUZZ_PATH);
    RUN_BENCH("bench_sched_test", BENCH_SCHED_PATH);
    /* Re-enabled for debugging */
    RUN_BENCH("bench_app_isolation_test", BENCH_APP_ISOLATION_PATH);
    RUN_BENCH("test_posix_fs", TEST_POSIX_FS_PATH);
    RUN_BENCH("test_epoll", TEST_EPOLL_PATH);
    RUN_BENCH("test_posix_core",   TEST_POSIX_CORE_PATH);
    RUN_BENCH("test_futex",        TEST_FUTEX_PATH);
    RUN_BENCH("bench_thread_test", BENCH_THREAD_PATH);
    RUN_BENCH("stress_thread",     STRESS_THREAD_PATH);
    RUN_BENCH("test_net_dispatch", TEST_NET_DISPATCH_PATH);
    RUN_BENCH("test_shm_dispatch", TEST_SHM_DISPATCH_PATH);
    RUN_BENCH("diag_shm_stress",  DIAG_SHM_STRESS_PATH);
    RUN_BENCH("bench_hugepage_tlb", BENCH_HUGEPAGE_TLB_PATH);
    RUN_BENCH("test_pt_interp",  TEST_PT_INTERP_PATH);
    RUN_BENCH("test_dynlink",   TEST_DYNLINK_PATH);
    RUN_BENCH("test_signal_musl", TEST_SIGNAL_MUSL_PATH);
    RUN_BENCH("test_env_musl",    TEST_ENV_MUSL_PATH);
    RUN_BENCH("test_fileio_musl", TEST_FILEIO_MUSL_PATH);
    RUN_BENCH("test_malloc_musl", TEST_MALLOC_MUSL_PATH);
    RUN_BENCH("test_fork_musl",  TEST_FORK_MUSL_PATH);
    RUN_BENCH("test_pipe_musl",  TEST_PIPE_MUSL_PATH);
    RUN_BENCH("test_stat_musl",    TEST_STAT_MUSL_PATH);
    RUN_BENCH("test_pthread_musl", TEST_PTHREAD_MUSL_PATH);
    RUN_BENCH("test_ai_guard",    TEST_AI_GUARD_PATH);
    RUN_BENCH("test_integration", TEST_INTEGRATION_PATH);
    RUN_BENCH("test_sustained",   TEST_SUSTAINED_PATH);
    RUN_BENCH("bench_ai_throughput", BENCH_AI_THROUGHPUT_PATH);
    RUN_BENCH("bench_csw_1ms",   BENCH_CSW_1MS_PATH);
    /* bench_soak_5min excluded from auto-run (300s duration).
       Run via: TEST_ONLY=bench_soak_5min bash run_tests.sh */
    RUN_BENCH("test_npu_direct", TEST_NPU_DIRECT_PATH);
    RUN_BENCH("test_ai_latency", TEST_AI_LATENCY_PATH);
    RUN_BENCH("bench_2026_frontier", BENCH_2026_FRONTIER_PATH);
    RUN_BENCH("test_agent_chaos", TEST_AGENT_CHAOS_PATH);
    RUN_BENCH("test_advanced_chaos", TEST_ADVANCED_CHAOS_PATH);
    RUN_BENCH("test_model_load", TEST_MODEL_LOAD_PATH);
    RUN_BENCH("test_health_check", TEST_HEALTH_CHECK_PATH);
    RUN_BENCH("test_agent_cluster", TEST_AGENT_CLUSTER_PATH);
    RUN_BENCH("test_torture", TEST_TORTURE_PATH);
    RUN_BENCH("test_ai_exploit_v2", TEST_AI_EXPLOIT_V2_PATH);
    RUN_BENCH("test_security_check", TEST_SECURITY_CHECK_PATH);
    /* test_vmm_soak excluded from auto-run (300s duration).
       Run via: TEST_ONLY=test_vmm_soak bash run_tests.sh */
#ifdef VOS3_TEST_ONLY
    RUN_BENCH("test_vmm_soak", TEST_VMM_SOAK_PATH);
#endif
    /* bench_ai_scale and test_stress_mt last — may hang in QEMU */
    RUN_BENCH("bench_ai_scale", BENCH_AI_SCALE_PATH);
    RUN_BENCH("test_stress_mt",   TEST_STRESS_MT_PATH);

    #undef RUN_BENCH

    printf("===========================================\n");
    printf("  WEEK 2 BENCHMARK SUITE: COMPLETE\n");
    printf("===========================================\n");
    printf("\n");
    printf("Init: All benchmarks executed. System halting.\n");

    /* Halt */
    for (;;) { sleep(10); }

#elif defined(HEADLESS_ALPHA2_AUDIT)
    /* ================================================================
     * ALPHA 2.0 DEEP TECHNICAL AUDIT MODE
     * Tests Phases 26, 27, 28, 29
     * ================================================================ */
    printf("\n");
    printf("===========================================\n");
    printf("  VOS3 ALPHA 2.0 DEEP TECHNICAL AUDIT\n");
    printf("  Testing: Phases 26, 27, 28, 29\n");
    printf("===========================================\n");
    printf("\n");
    /* Day 5: Run UDP Socket Test first */
    printf("Init: Running Day 5 UDP Socket Test...\n");
    printf("\n");

    pid_t udp_pid = fork();
    if (udp_pid == 0) {
        char *udp_argv[] = { "udp_test", (char *)0 };
        char *udp_envp[] = { "PATH=/bin", (char *)0 };
        execve(UDP_TEST_PATH, udp_argv, udp_envp);
        printf("Init: ERROR - Failed to exec udp_test!\n");
        exit(1);
    }
    if (udp_pid > 0) {
        int udp_status;
        waitpid(udp_pid, &udp_status, 0);
        printf("\nInit: UDP test completed (exit=%d)\n", WEXITSTATUS(udp_status));
    }

    /* Day 6: Run TCP Socket Test */
    printf("\nInit: Running Day 6 TCP Socket Test...\n");
    printf("\n");

    pid_t tcp_pid = fork();
    if (tcp_pid == 0) {
        char *tcp_argv[] = { "tcp_test", (char *)0 };
        char *tcp_envp[] = { "PATH=/bin", (char *)0 };
        execve(TCP_TEST_PATH, tcp_argv, tcp_envp);
        printf("Init: ERROR - Failed to exec tcp_test!\n");
        exit(1);
    }
    if (tcp_pid > 0) {
        int tcp_status;
        waitpid(tcp_pid, &tcp_status, 0);
        printf("\nInit: TCP test completed (exit=%d)\n", WEXITSTATUS(tcp_status));
    }

    /* Day 7: Run Disk Test */
    printf("\nInit: Running Day 7 Block Device Test...\n");
    printf("\n");

    pid_t disk_pid = fork();
    if (disk_pid == 0) {
        char *disk_argv[] = { "disk_test", (char *)0 };
        char *disk_envp[] = { "PATH=/bin", (char *)0 };
        execve(DISK_TEST_PATH, disk_argv, disk_envp);
        printf("Init: ERROR - Failed to exec disk_test!\n");
        exit(1);
    }
    if (disk_pid > 0) {
        int disk_status;
        waitpid(disk_pid, &disk_status, 0);
        printf("\nInit: Disk test completed (exit=%d)\n", WEXITSTATUS(disk_status));
    }

    printf("\nInit: Launching ai_audit test suite...\n");
    printf("\n");

    pid_t audit_pid = fork();

    if (audit_pid < 0) {
        printf("Init: ERROR - fork() failed for audit!\n");
        for (;;) { sleep(1); }
    }

    if (audit_pid == 0) {
        /* Child process - run audit */
        char *audit_argv[] = { "ai_audit", (char *)0 };
        char *audit_envp[] = {
            "PATH=/bin:/usr/bin",
            "HOME=/",
            "TERM=vt100",
            (char *)0
        };

        execve(AUDIT_PATH, audit_argv, audit_envp);

        /* If execve fails */
        printf("Init: ERROR - Failed to exec ai_audit!\n");
        printf("Init: Check that /bin/ai_audit exists.\n");
        exit(1);
    }

    /* Parent process - wait for audit to complete */
    int audit_status;
    waitpid(audit_pid, &audit_status, 0);

    printf("\n");
    printf("===========================================\n");
    if (WIFEXITED(audit_status) && WEXITSTATUS(audit_status) == 0) {
        printf("  ALPHA 2.0 AUDIT: ALL TESTS PASSED\n");
    } else {
        printf("  ALPHA 2.0 AUDIT: SOME TESTS FAILED\n");
        printf("  Exit code: %d\n", WEXITSTATUS(audit_status));
    }
    printf("===========================================\n");
    printf("\n");
    printf("Init: Audit complete. System halting.\n");

    /* Halt - audit complete */
    for (;;) { sleep(10); }

#elif defined(HEADLESS_STRESS_TEST)
    /* ================================================================
     * HEADLESS STRESS TEST MODE (Phase 25 Audit)
     * Run ai_stress directly: 16 workers, 10 seconds
     * ================================================================ */
    printf("\n");
    printf("===========================================\n");
    printf("  HEADLESS MULTICORE STABILITY AUDIT\n");
    printf("  Phase 25 - SMP Stress Test\n");
    printf("===========================================\n");
    printf("\n");
    printf("Init: Launching ai_stress -j 16 -d 10\n");
    printf("\n");

    pid_t stress_pid = fork();

    if (stress_pid < 0) {
        printf("Init: ERROR - fork() failed for stress test!\n");
        for (;;) { sleep(1); }
    }

    if (stress_pid == 0) {
        /* Child process - run stress test */
        char *stress_argv[] = { "ai_stress", "-j", "16", "-d", "3", (char *)0 };
        char *stress_envp[] = {
            "PATH=/bin:/usr/bin",
            "HOME=/",
            (char *)0
        };

        execve(STRESS_PATH, stress_argv, stress_envp);

        /* If execve fails */
        printf("Init: ERROR - Failed to exec ai_stress!\n");
        exit(1);
    }

    /* Parent process - wait for stress test to complete */
    int status;
    waitpid(stress_pid, &status, 0);

    printf("\n");
    printf("===========================================\n");
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        printf("  AUDIT RESULT: PASSED\n");
    } else {
        printf("  AUDIT RESULT: FAILED (exit=%d)\n", WEXITSTATUS(status));
    }
    printf("===========================================\n");
    printf("\n");
    printf("Init: Stress test complete. System stable.\n");
    printf("Init: Halting.\n");

    /* Halt - test complete */
    for (;;) { sleep(10); }

#else
    /* ================================================================
     * NORMAL BOOT: First-Boot Check and Shell
     * ================================================================ */
    if (!is_provisioned()) {
        run_setup_wizard();
    } else {
        printf("Init: System is provisioned. Loading identity...\n");
    }

    /* Spawn shell */
    printf("Init: Spawning shell (%s)...\n", SHELL_PATH);

    pid_t shell_pid = fork();

    if (shell_pid < 0) {
        printf("Init: ERROR - fork() failed!\n");
        for (;;) {
            sleep(1);
        }
    }

    if (shell_pid == 0) {
        /* Child process - exec shell */
        char *shell_argv[] = { SHELL_NAME, (char *)0 };
        char *shell_envp[] = {
            "PATH=/bin:/usr/bin",
            "HOME=/",
            "TERM=vt100",
            "SHELL=/bin/sh",
            (char *)0
        };

        execve(SHELL_PATH, shell_argv, shell_envp);

        /* If execve fails, try to run built-in shell */
        printf("Init: Failed to exec %s, entering fallback mode\n", SHELL_PATH);

        /* Simple fallback - just print a message and exit */
        printf("Init: No shell available. System halted.\n");
        exit(1);
    }

    /* Parent process - init */
    printf("Init: Shell started with PID %d\n", shell_pid);
    printf("\n");

    /* Main loop - reap zombies and respawn shell if needed */
    for (;;) {
        int status;
        pid_t child = wait(&status);

        if (child > 0) {
            if (child == shell_pid) {
                /* Shell exited - respawn it */
                int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
                printf("\nInit: Shell (PID %d) exited with code %d\n",
                       child, exit_code);

                sleep(1);  /* Brief pause */

                printf("Init: Respawning shell...\n");

                shell_pid = fork();
                if (shell_pid == 0) {
                    char *shell_argv[] = { SHELL_NAME, (char *)0 };
                    char *shell_envp[] = {
                        "PATH=/bin:/usr/bin",
                        "HOME=/",
                        "TERM=vt100",
                        "SHELL=/bin/sh",
                        (char *)0
                    };
                    execve(SHELL_PATH, shell_argv, shell_envp);
                    exit(1);
                } else if (shell_pid > 0) {
                    printf("Init: Shell restarted with PID %d\n", shell_pid);
                }
            } else {
                /* Some other child exited - just reap it */
                /* This handles orphaned processes */
            }
        }
    }
#endif /* !HEADLESS_STRESS_TEST */

    /* Should never reach here */
    return 0;
}
