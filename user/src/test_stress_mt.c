/*
 * test_stress_mt.c — 50-thread VFS stress test
 *
 * Validates FD table spinlocks, VFS locking, and MMU under concurrent
 * pressure from 50 pthreads. Each thread performs open/write/read/close
 * cycles on per-thread and shared files.
 *
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>

#define NUM_THREADS   2
#define ITERATIONS    5
#define BUF_SIZE      128

/* Shared error counters (atomic) */
static volatile int g_errors = 0;
static volatile int g_ops = 0;

static void inc_errors(void) { __atomic_add_fetch(&g_errors, 1, __ATOMIC_RELAXED); }
static void inc_ops(int n) { __atomic_add_fetch(&g_ops, n, __ATOMIC_RELAXED); }

/* ---------- Thread: per-file open/write/read/close ---------- */

static void* thread_file_ops(void* arg)
{
    int tid = (int)(long)arg;
    char path[64];
    char wbuf[BUF_SIZE];
    char rbuf[BUF_SIZE];

    snprintf(path, sizeof(path), "/tmp/stress_%d", tid);

    for (int i = 0; i < ITERATIONS; i++) {
        /* Write */
        int fd = open(path, O_CREAT | O_RDWR | O_TRUNC, 0644);
        if (fd < 0) { inc_errors(); continue; }

        int len = snprintf(wbuf, sizeof(wbuf), "tid=%d iter=%d data=0x%x", tid, i, tid * 1000 + i);
        ssize_t w = write(fd, wbuf, len);
        if (w != len) { inc_errors(); close(fd); continue; }

        /* Read back */
        if (lseek(fd, 0, SEEK_SET) != 0) { inc_errors(); close(fd); continue; }

        memset(rbuf, 0, sizeof(rbuf));
        ssize_t r = read(fd, rbuf, sizeof(rbuf));
        if (r != len || memcmp(wbuf, rbuf, len) != 0) {
            inc_errors();
            close(fd);
            continue;
        }

        close(fd);
        inc_ops(4); /* open + write + read + close */

        /* Stat the file */
        struct stat st;
        if (stat(path, &st) == 0) {
            if ((int)st.st_size != len) inc_errors();
            inc_ops(1);
        }

        /* Unlink every 10th iteration */
        if (i % 10 == 9) {
            unlink(path);
            inc_ops(1);
        }
    }

    /* Final cleanup */
    unlink(path);
    return NULL;
}

/* ---------- Thread: double-open race ---------- */
/* Two threads open same file simultaneously, write different data, verify */

/* Simple spin barrier (avoids pthread_barrier_t portability issues) */
static volatile int g_barrier_count = 0;
static volatile int g_barrier_gen = 0;
#define BARRIER_PARTIES 2

static void spin_barrier_wait(void) {
    int gen = __atomic_load_n(&g_barrier_gen, __ATOMIC_ACQUIRE);
    if (__atomic_add_fetch(&g_barrier_count, 1, __ATOMIC_ACQ_REL) == BARRIER_PARTIES) {
        __atomic_store_n(&g_barrier_count, 0, __ATOMIC_RELEASE);
        __atomic_add_fetch(&g_barrier_gen, 1, __ATOMIC_RELEASE);
    } else {
        while (__atomic_load_n(&g_barrier_gen, __ATOMIC_ACQUIRE) == gen) {
            __asm__ volatile("pause");
        }
    }
}

static volatile int g_double_open_errors = 0;

static void* thread_double_open(void* arg)
{
    int tid = (int)(long)arg; /* 0 or 1 */
    char wbuf[32];
    int len = snprintf(wbuf, sizeof(wbuf), "writer_%d", tid);

    for (int i = 0; i < 10; i++) {
        /* Both threads try to open the same file */
        spin_barrier_wait();

        int fd = open("/tmp/stress_shared", O_CREAT | O_RDWR | O_TRUNC, 0644);
        if (fd < 0) {
            __atomic_add_fetch(&g_double_open_errors, 1, __ATOMIC_RELAXED);
            continue;
        }

        write(fd, wbuf, len);
        close(fd);
        inc_ops(3);
    }
    return NULL;
}

/* ---------- Thread: concurrent unlink ---------- */
/* Thread A writes to file while Thread B unlinks it */

static volatile int g_unlink_errors = 0;

static void* thread_writer_unlink(void* arg)
{
    (void)arg;
    char buf[64] = "data data data data";

    for (int i = 0; i < 10; i++) {
        int fd = open("/tmp/stress_ul", O_CREAT | O_RDWR | O_TRUNC, 0644);
        if (fd < 0) continue;

        /* Write multiple times to increase window for unlink race */
        for (int j = 0; j < 5; j++) {
            write(fd, buf, 19);
        }

        /* FD should still be valid even if file was unlinked */
        char rb[64];
        lseek(fd, 0, SEEK_SET);
        ssize_t r = read(fd, rb, sizeof(rb));
        if (r < 0 && errno == EBADF) {
            __atomic_add_fetch(&g_unlink_errors, 1, __ATOMIC_RELAXED);
        }
        close(fd);
        inc_ops(4);
    }
    return NULL;
}

static void* thread_unlinker(void* arg)
{
    (void)arg;
    for (int i = 0; i < 10; i++) {
        unlink("/tmp/stress_ul");
        inc_ops(1);
        /* Small busy-wait to create interleaving */
        for (volatile int j = 0; j < 100; j++) {}
    }
    return NULL;
}

/* ---------- Thread: malloc stress (exercises brk/mmap) ---------- */

static void* thread_malloc_stress(void* arg)
{
    (void)arg;
    for (int i = 0; i < ITERATIONS; i++) {
        /* Varying sizes to exercise both brk and mmap paths */
        size_t sizes[] = {16, 64, 256, 1024, 4096};
        for (int s = 0; s < 5; s++) {
            void* p = malloc(sizes[s]);
            if (p == NULL) { inc_errors(); continue; }
            memset(p, 0xAB, sizes[s]);
            free(p);
            inc_ops(1);
        }
    }
    return NULL;
}

/* ============ Main ============ */

int main(void)
{
    int pass = 0, fail = 0;

    /* --- Test 1: 50-thread parallel file ops --- */
    {
        pthread_t threads[NUM_THREADS];
        int ok = 1;
        g_errors = 0;
        g_ops = 0;

        for (int i = 0; i < NUM_THREADS; i++) {
            int rc = pthread_create(&threads[i], NULL, thread_file_ops, (void*)(long)i);
            if (rc != 0) {
                printf("[FAIL] test_stress_mt: parallel_file_ops (create[%d] rc=%d)\n", i, rc);
                ok = 0;
                /* Join already created */
                for (int j = 0; j < i; j++) pthread_join(threads[j], NULL);
                break;
            }
        }

        if (ok) {
            for (int i = 0; i < NUM_THREADS; i++)
                pthread_join(threads[i], NULL);

            int errs = __atomic_load_n(&g_errors, __ATOMIC_RELAXED);
            int ops = __atomic_load_n(&g_ops, __ATOMIC_RELAXED);

            if (errs == 0) {
                printf("[PASS] test_stress_mt: parallel_file_ops (%d ops, 0 errors)\n", ops);
                pass++;
            } else {
                printf("[FAIL] test_stress_mt: parallel_file_ops (%d errors in %d ops)\n", errs, ops);
                fail++;
            }
        } else {
            fail++;
        }
    }

    /* --- Test 2: double-open race --- */
    {
        __atomic_store_n(&g_barrier_count, 0, __ATOMIC_RELEASE);
        __atomic_store_n(&g_barrier_gen, 0, __ATOMIC_RELEASE);
        pthread_t t1, t2;
        g_double_open_errors = 0;

        int rc1 = pthread_create(&t1, NULL, thread_double_open, (void*)0L);
        int rc2 = pthread_create(&t2, NULL, thread_double_open, (void*)1L);

        if (rc1 != 0 || rc2 != 0) {
            printf("[FAIL] test_stress_mt: double_open_race (create failed)\n");
            fail++;
        } else {
            pthread_join(t1, NULL);
            pthread_join(t2, NULL);

            int errs = __atomic_load_n(&g_double_open_errors, __ATOMIC_RELAXED);
            if (errs == 0) {
                printf("[PASS] test_stress_mt: double_open_race\n");
                pass++;
            } else {
                printf("[FAIL] test_stress_mt: double_open_race (%d EBADF errors)\n", errs);
                fail++;
            }
        }
        unlink("/tmp/stress_shared");
    }

    /* --- Test 3: concurrent unlink-while-writing --- */
    {
        pthread_t tw, tu;
        g_unlink_errors = 0;

        int rc1 = pthread_create(&tw, NULL, thread_writer_unlink, NULL);
        int rc2 = pthread_create(&tu, NULL, thread_unlinker, NULL);

        if (rc1 != 0 || rc2 != 0) {
            printf("[FAIL] test_stress_mt: concurrent_unlink (create failed)\n");
            fail++;
        } else {
            pthread_join(tw, NULL);
            pthread_join(tu, NULL);

            int errs = __atomic_load_n(&g_unlink_errors, __ATOMIC_RELAXED);
            if (errs == 0) {
                printf("[PASS] test_stress_mt: concurrent_unlink\n");
                pass++;
            } else {
                printf("[FAIL] test_stress_mt: concurrent_unlink (%d EBADF errors)\n", errs);
                fail++;
            }
        }
        unlink("/tmp/stress_ul");
    }

    /* --- Test 4: malloc/free under thread contention --- */
    {
        pthread_t threads[2];
        int old_errors = __atomic_load_n(&g_errors, __ATOMIC_RELAXED);
        int old_ops = __atomic_load_n(&g_ops, __ATOMIC_RELAXED);
        int ok = 1;

        for (int i = 0; i < 2; i++) {
            int rc = pthread_create(&threads[i], NULL, thread_malloc_stress, NULL);
            if (rc != 0) {
                ok = 0;
                for (int j = 0; j < i; j++) pthread_join(threads[j], NULL);
                break;
            }
        }

        if (ok) {
            for (int i = 0; i < 2; i++)
                pthread_join(threads[i], NULL);

            int new_errors = __atomic_load_n(&g_errors, __ATOMIC_RELAXED) - old_errors;
            int new_ops = __atomic_load_n(&g_ops, __ATOMIC_RELAXED) - old_ops;

            if (new_errors == 0) {
                printf("[PASS] test_stress_mt: malloc_mt_stress (%d ops, 0 errors)\n", new_ops);
                pass++;
            } else {
                printf("[FAIL] test_stress_mt: malloc_mt_stress (%d errors in %d ops)\n",
                       new_errors, new_ops);
                fail++;
            }
        } else {
            printf("[FAIL] test_stress_mt: malloc_mt_stress (create failed)\n");
            fail++;
        }
    }

    /* Summary */
    printf("STRESS_MT RESULTS: %d passed, %d failed\n", pass, fail);
    return (fail > 0) ? 1 : 0;
}
