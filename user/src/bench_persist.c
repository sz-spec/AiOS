/**
 * @file bench_persist.c
 * @brief VOS3 Persistence Access Speed Benchmark
 *
 * @details Measures read/write throughput for 64KB data blocks
 *          using the VOS3 filesystem (ramfs or vos3fs).
 *
 * @version 1.0.0
 * @date 2026-02-25
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define BENCH_FILE          "/tmp/bench_persist.dat"
#define BLOCK_SIZE          1024        /* Write in 1K chunks */
#define TOTAL_SIZE          (64 * 1024) /* 64 KB total */
#define NUM_BLOCKS          (TOTAL_SIZE / BLOCK_SIZE)
#define ITERATIONS          5

/* ============================================================================
 * HELPERS
 * ============================================================================ */

static unsigned long long rdtsc(void)
{
    unsigned int lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

/**
 * @brief Fill buffer with deterministic pattern
 */
static void fill_pattern(char* buf, int size, int seed)
{
    for (int i = 0; i < size; i++) {
        buf[i] = (char)((seed + i) & 0x7F);
    }
}

/**
 * @brief Verify buffer matches expected pattern
 */
static int verify_pattern(const char* buf, int size, int seed)
{
    for (int i = 0; i < size; i++) {
        if (buf[i] != (char)((seed + i) & 0x7F)) {
            return -1;
        }
    }
    return 0;
}

/* ============================================================================
 * BENCHMARKS
 * ============================================================================ */

/**
 * @brief Measure sequential write throughput
 */
static unsigned long long bench_write(int iteration)
{
    char block[BLOCK_SIZE];

    int fd = open(BENCH_FILE, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        printf("  [ERROR] Cannot open %s for writing\n", BENCH_FILE);
        return 0;
    }

    unsigned long long start = rdtsc();

    for (int i = 0; i < NUM_BLOCKS; i++) {
        fill_pattern(block, BLOCK_SIZE, iteration * NUM_BLOCKS + i);
        ssize_t written = write(fd, block, BLOCK_SIZE);
        if (written != BLOCK_SIZE) {
            printf("  [ERROR] Short write at block %d: %d\n", i, (int)written);
            close(fd);
            return 0;
        }
    }

    unsigned long long end = rdtsc();
    close(fd);

    return end - start;
}

/**
 * @brief Measure sequential read throughput
 */
static unsigned long long bench_read(int iteration)
{
    char block[BLOCK_SIZE];

    int fd = open(BENCH_FILE, O_RDONLY, 0);
    if (fd < 0) {
        printf("  [ERROR] Cannot open %s for reading\n", BENCH_FILE);
        return 0;
    }

    int integrity_ok = 1;
    unsigned long long start = rdtsc();

    for (int i = 0; i < NUM_BLOCKS; i++) {
        ssize_t bytes_read = read(fd, block, BLOCK_SIZE);
        if (bytes_read != BLOCK_SIZE) {
            printf("  [ERROR] Short read at block %d: %d\n", i, (int)bytes_read);
            close(fd);
            return 0;
        }

        if (verify_pattern(block, BLOCK_SIZE, iteration * NUM_BLOCKS + i) != 0) {
            integrity_ok = 0;
        }
    }

    unsigned long long end = rdtsc();
    close(fd);

    if (!integrity_ok) {
        printf("  [WARN] Data integrity check failed\n");
    }

    return end - start;
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    printf("\n");
    printf("============================================\n");
    printf("  VOS3 Persistence Access Speed Benchmark\n");
    printf("============================================\n");
    printf("\n");
    printf("[INFO] Block size:  %d bytes\n", BLOCK_SIZE);
    printf("[INFO] Total size:  %d KB\n", TOTAL_SIZE / 1024);
    printf("[INFO] Iterations:  %d\n", ITERATIONS);
    printf("[INFO] File:        %s\n", BENCH_FILE);
    printf("\n");

    /* Ensure /tmp exists */
    mkdir("/tmp", 0755);

    unsigned long long write_total = 0;
    unsigned long long read_total = 0;
    unsigned long long write_min = (unsigned long long)-1;
    unsigned long long write_max = 0;
    unsigned long long read_min = (unsigned long long)-1;
    unsigned long long read_max = 0;

    for (int iter = 0; iter < ITERATIONS; iter++) {
        printf("[ITER %d/%d]\n", iter + 1, ITERATIONS);

        /* Write benchmark */
        unsigned long long w_cycles = bench_write(iter);
        if (w_cycles > 0) {
            write_total += w_cycles;
            if (w_cycles < write_min) write_min = w_cycles;
            if (w_cycles > write_max) write_max = w_cycles;
            printf("  Write: %llu cycles\n", w_cycles);
        }

        /* Read benchmark */
        unsigned long long r_cycles = bench_read(iter);
        if (r_cycles > 0) {
            read_total += r_cycles;
            if (r_cycles < read_min) read_min = r_cycles;
            if (r_cycles > read_max) read_max = r_cycles;
            printf("  Read:  %llu cycles\n", r_cycles);
        }
    }

    /* Cleanup */
    unlink(BENCH_FILE);

    /* Results */
    printf("\n");
    printf("[RESULTS] Write 64KB (%d iterations)\n", ITERATIONS);
    if (write_total > 0) {
        printf("  Min:     %llu cycles\n", write_min);
        printf("  Max:     %llu cycles\n", write_max);
        printf("  Average: %llu cycles\n", write_total / ITERATIONS);
    } else {
        printf("  (no data)\n");
    }

    printf("\n");
    printf("[RESULTS] Read 64KB (%d iterations)\n", ITERATIONS);
    if (read_total > 0) {
        printf("  Min:     %llu cycles\n", read_min);
        printf("  Max:     %llu cycles\n", read_max);
        printf("  Average: %llu cycles\n", read_total / ITERATIONS);
    } else {
        printf("  (no data)\n");
    }

    printf("\n");
    printf("============================================\n");
    printf("  Benchmark complete.\n");
    printf("============================================\n");

    return 0;
}
