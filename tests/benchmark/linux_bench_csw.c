/**
 * @file linux_bench_csw.c
 * @brief Linux Context Switch Latency Baseline
 *
 * @details Pipe ping-pong benchmark for Linux, matching the VOS3
 *          bench_csw program. Compile and run on the host to get
 *          a baseline for comparison.
 *
 * Compile: gcc -O2 -o linux_bench_csw linux_bench_csw.c
 * Run:     ./linux_bench_csw
 *
 * @version 1.0.0
 * @date 2026-02-25
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <time.h>
#include <stdint.h>

#define ITERATIONS      10000
#define WARMUP          1000
#define PIPE_TOKEN      0x42

/**
 * @brief Read TSC
 */
static inline uint64_t rdtsc(void)
{
#if defined(__x86_64__) || defined(__i386__)
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
#elif defined(__aarch64__)
    uint64_t val;
    __asm__ volatile ("mrs %0, cntvct_el0" : "=r"(val));
    return val;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
#endif
}

/**
 * @brief Get timer frequency
 */
static uint64_t get_freq_khz(void)
{
#if defined(__x86_64__) || defined(__i386__)
    /* Calibrate TSC with clock_gettime */
    struct timespec start_ts, end_ts;
    clock_gettime(CLOCK_MONOTONIC, &start_ts);
    uint64_t start_tsc = rdtsc();

    /* Busy-wait ~50ms */
    volatile int i;
    for (i = 0; i < 50000000; i++) {}

    uint64_t end_tsc = rdtsc();
    clock_gettime(CLOCK_MONOTONIC, &end_ts);

    uint64_t ns = (uint64_t)(end_ts.tv_sec - start_ts.tv_sec) * 1000000000ULL +
                  (uint64_t)(end_ts.tv_nsec - start_ts.tv_nsec);
    if (ns == 0) ns = 1;

    return (end_tsc - start_tsc) * 1000000ULL / ns;  /* kHz */
#elif defined(__aarch64__)
    uint64_t freq;
    __asm__ volatile ("mrs %0, cntfrq_el0" : "=r"(freq));
    return freq / 1000;
#else
    return 1000000;  /* 1 GHz assumed */
#endif
}

int main(void)
{
    printf("\n");
    printf("============================================\n");
    printf("  Linux Context Switch Latency Baseline\n");
    printf("============================================\n");
    printf("\n");

    uint64_t freq_khz = get_freq_khz();
    printf("[INFO] Timer frequency: ~%llu MHz\n", (unsigned long long)(freq_khz / 1000));
    printf("[INFO] Iterations: %d (+ %d warmup)\n", ITERATIONS, WARMUP);
    printf("\n");

    int pipe_a[2], pipe_b[2];
    if (pipe(pipe_a) < 0 || pipe(pipe_b) < 0) {
        perror("pipe");
        return 1;
    }

    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return 1;
    }

    if (pid == 0) {
        /* Child: echo back */
        close(pipe_a[1]);
        close(pipe_b[0]);

        char buf;
        for (int i = 0; i < WARMUP + ITERATIONS; i++) {
            if (read(pipe_a[0], &buf, 1) != 1) break;
            buf++;
            if (write(pipe_b[1], &buf, 1) != 1) break;
        }

        close(pipe_a[0]);
        close(pipe_b[1]);
        _exit(0);
    }

    /* Parent: measure */
    close(pipe_a[0]);
    close(pipe_b[1]);

    /* Warmup */
    for (int i = 0; i < WARMUP; i++) {
        char token = PIPE_TOKEN;
        char reply;
        write(pipe_a[1], &token, 1);
        read(pipe_b[0], &reply, 1);
    }

    /* Measurement */
    uint64_t total = 0;
    uint64_t min_val = UINT64_MAX;
    uint64_t max_val = 0;
    int success = 0;

    for (int i = 0; i < ITERATIONS; i++) {
        char token = PIPE_TOKEN;
        char reply;

        uint64_t start = rdtsc();
        write(pipe_a[1], &token, 1);
        read(pipe_b[0], &reply, 1);
        uint64_t end = rdtsc();

        uint64_t elapsed = end - start;
        total += elapsed;
        success++;

        if (elapsed < min_val) min_val = elapsed;
        if (elapsed > max_val) max_val = elapsed;
    }

    close(pipe_a[1]);
    close(pipe_b[0]);

    int status;
    waitpid(pid, &status, 0);

    /* Results */
    printf("[RESULTS] Linux Pipe Ping-Pong\n");
    printf("  Iterations:     %d\n", success);

    if (success > 0) {
        uint64_t avg = total / (uint64_t)success;
        printf("  Round-trip min: %llu cycles\n", (unsigned long long)min_val);
        printf("  Round-trip max: %llu cycles\n", (unsigned long long)max_val);
        printf("  Round-trip avg: %llu cycles\n", (unsigned long long)avg);
        printf("  Per-switch avg: ~%llu cycles (round-trip / 2)\n",
               (unsigned long long)(avg / 2));

        if (freq_khz > 0) {
            uint64_t avg_us = (avg * 1000ULL) / freq_khz;
            printf("  Round-trip avg: ~%llu us\n", (unsigned long long)avg_us);
            printf("  Per-switch avg: ~%llu us\n", (unsigned long long)(avg_us / 2));
        }
    }

    printf("\n");
    printf("============================================\n");
    printf("  Baseline complete.\n");
    printf("============================================\n");

    return 0;
}
