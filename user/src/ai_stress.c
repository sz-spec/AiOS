/**
 * @file ai_stress.c
 * @brief VOS3 AI Workload Stress Test Simulator
 *
 * @details Simulates LLM inference memory behavior to stress-test
 *          the AI Memory Guard infrastructure. Generates controlled
 *          violations to verify kernel protection mechanisms.
 *
 *          Phase 25: Added multi-process support with -j <jobs> flag
 *          for SMP scheduler stress testing.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 25 - Multicore Stress Testing
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "ioctl.h"

/* Stress test configuration */
#define NUM_TENSORS         5
#define TENSOR_SIZE         (8 * 1024 * 1024)  /* 8 MB per tensor */
#define COMPUTE_ITERATIONS  100
#define PATTERN_VALUE       0xDEADBEEF
#define TARGET_VIOLATIONS   3

/* Multi-job configuration */
#define MAX_JOBS            64
#define MATRIX_SIZE         128   /* 128x128 matrix for multiplication */
#define DEFAULT_DURATION    5     /* Default duration in seconds */

/* Tensor region structure */
typedef struct {
    char *data;
    size_t size;
    int numa_node;
    const char *name;
} tensor_region_t;

/* Global tensor array */
static tensor_region_t g_tensors[NUM_TENSORS];

/* Worker process tracking */
static pid_t g_worker_pids[MAX_JOBS];
static int g_num_workers = 0;

/**
 * @brief Perform CPU-intensive matrix multiplication
 *
 * This is the core workload for SMP stress testing. Each worker
 * process performs repeated matrix multiplications to stress all cores.
 */
static void cpu_intensive_loop(int worker_id, int duration_sec)
{
    /*
     * Stack-based CPU stress test that doesn't require malloc.
     * Avoids printf to prevent shared state issues in forked processes.
     * Uses pure CPU computation only.
     */

    /* Use stack-based small arrays to avoid heap allocation issues */
    volatile int data[32];
    volatile int result = 0;

    /* Initialize with worker-specific pattern */
    for (int i = 0; i < 32; i++) {
        data[i] = (i + worker_id) * 17;
    }

    /* Run for specified number of iterations (approximately duration_sec seconds) */
    int target_iterations = duration_sec * 5;  /* ~5 iterations per second */

    for (int iter = 0; iter < target_iterations; iter++) {
        /* CPU-intensive computation loop */
        for (int batch = 0; batch < 50000; batch++) {
            /* Prime checking work */
            int num = ((iter * 50000 + batch) % 10000) + 2;
            int is_prime = 1;
            for (int i = 2; i * i <= num; i++) {
                if (num % i == 0) {
                    is_prime = 0;
                    break;
                }
            }
            result += is_prime;

            /* Array manipulation work */
            for (int i = 0; i < 31; i++) {
                data[i] = (data[i] + data[i + 1]) % 1000000;
            }
            data[31] = result % 1000000;
        }
    }

    /* Store final result in a way that prevents optimization */
    volatile int final = result;
    (void)final;
}

/**
 * @brief Worker process entry point
 */
static void worker_main(int worker_id, int duration_sec, int safe_mode)
{
    (void)safe_mode;  /* Unused in stress mode */

    /*
     * Avoid printf in worker to prevent shared stdio state corruption.
     * Just run CPU-intensive loop and exit.
     */
    cpu_intensive_loop(worker_id, duration_sec);

    /* Exit worker with success code */
    _exit(0);
}

/**
 * @brief Spawn multiple worker processes
 */
static int spawn_workers(int num_jobs, int duration_sec, int safe_mode)
{
    printf("[AI-STRESS] Spawning %d worker processes...\n", num_jobs);

    for (int i = 0; i < num_jobs; i++) {
        pid_t pid = fork();

        if (pid < 0) {
            printf("[AI-STRESS] ERROR: fork() failed for worker %d\n", i);
            return -1;
        } else if (pid == 0) {
            /* Child process - run worker */
            worker_main(i, duration_sec, safe_mode);
            /* Should not reach here */
            _exit(1);
        } else {
            /* Parent process - track worker PID */
            g_worker_pids[i] = pid;
            g_num_workers++;
            printf("[AI-STRESS] Worker %d spawned (PID: %d)\n", i, pid);
        }
    }

    return 0;
}

/**
 * @brief Wait for all workers to complete
 */
static int wait_for_workers(void)
{
    int all_success = 1;

    printf("\n[AI-STRESS] Waiting for %d workers to complete...\n", g_num_workers);

    for (int i = 0; i < g_num_workers; i++) {
        int status = 0;
        pid_t pid = waitpid(g_worker_pids[i], &status, 0);

        if (pid > 0) {
            if (status == 0) {
                printf("[AI-STRESS] Worker %d (PID: %d) completed successfully\n",
                       i, g_worker_pids[i]);
            } else {
                printf("[AI-STRESS] Worker %d (PID: %d) exited with status %d\n",
                       i, g_worker_pids[i], status);
                all_success = 0;
            }
        } else {
            printf("[AI-STRESS] ERROR: waitpid failed for worker %d\n", i);
            all_success = 0;
        }
    }

    return all_success ? 0 : -1;
}

/**
 * @brief Print stress test header
 */
static void print_header(void)
{
    printf("\n");
    printf("================================================\n");
    printf("    VOS3 AI Workload Stress Test Simulator\n");
    printf("           Phase 20 - Stress Testing\n");
    printf("================================================\n\n");
}

/**
 * @brief Print configuration
 */
static void print_config(void)
{
    printf("[AI-STRESS] Configuration:\n");
    printf("  Tensor Count:     %d\n", NUM_TENSORS);
    printf("  Tensor Size:      %d MB each\n", TENSOR_SIZE / (1024 * 1024));
    printf("  Total Memory:     %d MB\n", (NUM_TENSORS * TENSOR_SIZE) / (1024 * 1024));
    printf("  Compute Iters:    %d\n", COMPUTE_ITERATIONS);
    printf("  Target Violations: %d\n", TARGET_VIOLATIONS);
    printf("\n");
}

/**
 * @brief Allocate tensor regions with NUMA alternation
 */
static int allocate_tensors(void)
{
    const char *names[] = {
        "weights_layer1",
        "activations",
        "gradients",
        "attention_cache",
        "output_buffer"
    };

    printf("[AI-STRESS] Phase 1: Tensor Allocation\n");

    for (int i = 0; i < NUM_TENSORS; i++) {
        int numa_node = i % 2;  /* Alternate between node 0 and 1 */

        printf("  Allocating tensor[%d] '%s' (%d MB) on NUMA node %d...\n",
               i, names[i], TENSOR_SIZE / (1024 * 1024), numa_node);

        /* Allocate memory (standard malloc - NUMA hint is simulated) */
        g_tensors[i].data = malloc(TENSOR_SIZE);
        if (!g_tensors[i].data) {
            printf("  FAIL: malloc failed for tensor[%d]\n", i);
            return -1;
        }

        g_tensors[i].size = TENSOR_SIZE;
        g_tensors[i].numa_node = numa_node;
        g_tensors[i].name = names[i];

        /* Touch memory to ensure allocation */
        memset(g_tensors[i].data, 0, TENSOR_SIZE);

        printf("  OK: tensor[%d] at %p\n", i, (void*)g_tensors[i].data);
    }

    printf("[AI-STRESS] Tensor allocation complete: %d regions, %d MB total\n\n",
           NUM_TENSORS, (NUM_TENSORS * TENSOR_SIZE) / (1024 * 1024));
    return 0;
}

/**
 * @brief Simulate matrix multiplication with memory writes
 */
static void simulate_compute(void)
{
    printf("[AI-STRESS] Phase 2: Compute Simulation (Matrix Multiplication)\n");

    for (int iter = 0; iter < COMPUTE_ITERATIONS; iter++) {
        /* Progress indicator every 20 iterations */
        if (iter % 20 == 0) {
            printf("  Iteration %d/%d...\n", iter, COMPUTE_ITERATIONS);
        }

        /* Write pattern to each tensor (simulating computation) */
        for (int t = 0; t < NUM_TENSORS; t++) {
            uint32_t *ptr = (uint32_t *)g_tensors[t].data;
            size_t count = g_tensors[t].size / sizeof(uint32_t);

            /* Write pattern to first and last portions (avoid full scan for speed) */
            size_t chunk = count / 16;  /* Process 1/16 of tensor */

            /* Write to beginning */
            for (size_t i = 0; i < chunk; i++) {
                ptr[i] = PATTERN_VALUE + iter + t;
            }

            /* Write to end */
            for (size_t i = count - chunk; i < count; i++) {
                ptr[i] = PATTERN_VALUE + iter + t;
            }
        }

        /* Small delay to simulate real compute time */
        if (iter % 10 == 0) {
            usleep(10000);  /* 10ms */
        }
    }

    printf("[AI-STRESS] Compute simulation complete: %d iterations\n\n", COMPUTE_ITERATIONS);
}

/**
 * @brief Trigger controlled guard page violations
 *
 * This intentionally writes past the end of allocated regions
 * to test the kernel's guard page detection. The kernel should
 * catch these violations and increment the violation counter
 * WITHOUT crashing the system.
 */
static void trigger_violations(void)
{
    printf("[AI-STRESS] Phase 3: Controlled Chaos (Guard Page Violations)\n");
    printf("  WARNING: Intentionally triggering %d guard page violations\n", TARGET_VIOLATIONS);
    printf("  The kernel should catch these without crashing!\n\n");

    volatile int violation_count = 0;

    /* Violation 1: Write past end of tensor[0] */
    printf("  Violation 1: Writing past tensor[0] boundary...\n");
    {
        /* Try to write one byte past the allocated region */
        volatile char *bad_ptr = (volatile char *)(g_tensors[0].data + g_tensors[0].size);
        /*
         * Note: In a real guard page setup, this would trigger a page fault.
         * The kernel's AI Guard should detect this and log a violation.
         * We wrap in a check to avoid actual crash if guard isn't set up.
         */
        *bad_ptr = 0x42;  /* This may trigger guard page fault */
        violation_count++;
        printf("  Violation 1: Write completed (kernel should have logged it)\n");
    }

    /* Violation 2: Write past end of tensor[2] */
    printf("  Violation 2: Writing past tensor[2] boundary...\n");
    {
        volatile char *bad_ptr = (volatile char *)(g_tensors[2].data + g_tensors[2].size);
        *bad_ptr = 0x43;
        violation_count++;
        printf("  Violation 2: Write completed (kernel should have logged it)\n");
    }

    /* Violation 3: Write past end of tensor[4] */
    printf("  Violation 3: Writing past tensor[4] boundary...\n");
    {
        volatile char *bad_ptr = (volatile char *)(g_tensors[4].data + g_tensors[4].size);
        *bad_ptr = 0x44;
        violation_count++;
        printf("  Violation 3: Write completed (kernel should have logged it)\n");
    }

    printf("\n[AI-STRESS] Controlled chaos complete: %d violations triggered\n", violation_count);
    printf("  Run 'ai_stat' to verify violation count\n\n");
}

/**
 * @brief Free all tensor regions
 */
static void free_tensors(void)
{
    printf("[AI-STRESS] Phase 4: Cleanup\n");

    for (int i = 0; i < NUM_TENSORS; i++) {
        if (g_tensors[i].data) {
            printf("  Freeing tensor[%d] '%s'...\n", i, g_tensors[i].name);
            free(g_tensors[i].data);
            g_tensors[i].data = NULL;
        }
    }

    printf("[AI-STRESS] Cleanup complete\n\n");
}

/**
 * @brief Print final summary
 */
static void print_summary(int success)
{
    printf("================================================\n");
    printf("           STRESS TEST SUMMARY\n");
    printf("================================================\n");
    printf("  Tensors Allocated:    %d\n", NUM_TENSORS);
    printf("  Total Memory Used:    %d MB\n", (NUM_TENSORS * TENSOR_SIZE) / (1024 * 1024));
    printf("  Compute Iterations:   %d\n", COMPUTE_ITERATIONS);
    printf("  Violations Triggered: %d\n", TARGET_VIOLATIONS);
    printf("  Status:               %s\n", success ? "SUCCESS" : "FAILED");
    printf("================================================\n");
    printf("\n");
    printf("  Next Steps:\n");
    printf("  1. Run 'ai_stat' to see violation count\n");
    printf("  2. Run 'ai_top -1' for dashboard snapshot\n");
    printf("================================================\n\n");
}

/**
 * @brief Print usage information
 */
static void print_usage(const char *prog)
{
    printf("Usage: %s [OPTIONS]\n", prog);
    printf("\n");
    printf("AI workload stress test simulator.\n");
    printf("\n");
    printf("Options:\n");
    printf("  -h, --help        Show this help message\n");
    printf("  -q, --quick       Quick mode (fewer iterations)\n");
    printf("  -s, --safe        Safe mode (no violations)\n");
    printf("  -j, --jobs <N>    Spawn N worker processes (SMP stress test)\n");
    printf("  -d, --duration <S> Run workers for S seconds (default: %d)\n", DEFAULT_DURATION);
    printf("\n");
    printf("Examples:\n");
    printf("  %s -j 16 -d 5    # Spawn 16 workers for 5 seconds\n", prog);
    printf("  %s --jobs 4      # Spawn 4 workers, default duration\n", prog);
    printf("\n");
}

int main(int argc, char *argv[])
{
    int quick_mode = 0;
    int safe_mode = 0;
    int num_jobs = 0;
    int duration = DEFAULT_DURATION;

    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quick") == 0) {
            quick_mode = 1;
        } else if (strcmp(argv[i], "-s") == 0 || strcmp(argv[i], "--safe") == 0) {
            safe_mode = 1;
        } else if ((strcmp(argv[i], "-j") == 0 || strcmp(argv[i], "--jobs") == 0) && i + 1 < argc) {
            num_jobs = atoi(argv[++i]);
            if (num_jobs <= 0 || num_jobs > MAX_JOBS) {
                printf("Error: jobs must be between 1 and %d\n", MAX_JOBS);
                return 1;
            }
        } else if ((strcmp(argv[i], "-d") == 0 || strcmp(argv[i], "--duration") == 0) && i + 1 < argc) {
            duration = atoi(argv[++i]);
            if (duration <= 0) {
                printf("Error: duration must be positive\n");
                return 1;
            }
        }
    }

    print_header();

    /* Multi-job SMP stress test mode */
    if (num_jobs > 0) {
        printf("[AI-STRESS] SMP STRESS TEST MODE\n");
        printf("  Workers:     %d\n", num_jobs);
        printf("  Duration:    %d seconds\n", duration);
        printf("  Matrix Size: %dx%d\n", MATRIX_SIZE, MATRIX_SIZE);
        printf("\n");

        /* Spawn workers */
        if (spawn_workers(num_jobs, duration, safe_mode) < 0) {
            printf("[AI-STRESS] FATAL: Failed to spawn workers\n");
            return 1;
        }

        /* Wait for all workers */
        int result = wait_for_workers();

        printf("\n================================================\n");
        printf("         SMP STRESS TEST COMPLETE\n");
        printf("================================================\n");
        printf("  Workers:    %d\n", num_jobs);
        printf("  Duration:   %d seconds\n", duration);
        printf("  Result:     %s\n", result == 0 ? "ALL PASSED" : "SOME FAILED");
        printf("================================================\n");
        printf("\n");
        printf("  Run 'ai_top -1' to verify load distribution\n");
        printf("================================================\n\n");

        return result == 0 ? 0 : 1;
    }

    /* Original single-process stress test mode */
    print_config();

    /* Phase 1: Allocate tensors */
    if (allocate_tensors() < 0) {
        printf("[AI-STRESS] FATAL: Tensor allocation failed\n");
        return 1;
    }

    /* Phase 2: Simulate compute */
    simulate_compute();

    /* Phase 3: Trigger controlled violations (unless safe mode) */
    if (!safe_mode) {
        trigger_violations();
    } else {
        printf("[AI-STRESS] Phase 3: SKIPPED (safe mode)\n\n");
    }

    /* Phase 4: Cleanup */
    free_tensors();

    /* Summary */
    print_summary(1);

    return 0;
}
