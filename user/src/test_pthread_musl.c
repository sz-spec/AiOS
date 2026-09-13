/*
 * test_pthread_musl.c — POSIX threads tests through musl libc
 *
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>

/* ---------- Test 1: create + join basic ---------- */

static void* thread_return_42(void* arg)
{
    (void)arg;
    return (void*)42;
}

/* ---------- Test 2: mutex counter ---------- */

static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;
static int g_counter = 0;

static void* thread_increment(void* arg)
{
    int n = (int)(long)arg;
    for (int i = 0; i < n; i++) {
        pthread_mutex_lock(&g_mtx);
        g_counter++;
        pthread_mutex_unlock(&g_mtx);
    }
    return NULL;
}

/* ---------- Test 3: TLS per thread ---------- */

static __thread int tls_val = 0;

static void* thread_set_tls(void* arg)
{
    tls_val = (int)(long)arg;
    /* Yield to let other thread run */
    for (volatile int i = 0; i < 1000; i++) {}
    return (void*)(long)tls_val;
}

/* ---------- Test 4: multiple threads ---------- */

static void* thread_square(void* arg)
{
    long v = (long)arg;
    return (void*)(v * v);
}

/* ---------- Test 5: getpid from thread ---------- */

static pid_t g_main_pid;

static void* thread_getpid(void* arg)
{
    (void)arg;
    return (void*)(long)getpid();
}

int main(void)
{
    g_main_pid = getpid();

    /* Test 1: create + join, verify return value */
    {
        pthread_t t;
        int rc = pthread_create(&t, NULL, thread_return_42, NULL);
        if (rc != 0) {
            printf("[FAIL] test_pthread: create_join_basic (create rc=%d)\n", rc);
        } else {
            void* retval = NULL;
            rc = pthread_join(t, &retval);
            if (rc == 0 && (long)retval == 42) {
                printf("[PASS] test_pthread: create_join_basic\n");
            } else {
                printf("[FAIL] test_pthread: create_join_basic (join rc=%d, retval=%ld)\n",
                       rc, (long)retval);
            }
        }
    }

    /* Test 2: mutex-protected shared counter (2 threads x 1000 increments) */
    {
        g_counter = 0;
        pthread_t t1, t2;
        int rc1 = pthread_create(&t1, NULL, thread_increment, (void*)1000);
        int rc2 = pthread_create(&t2, NULL, thread_increment, (void*)1000);
        if (rc1 != 0 || rc2 != 0) {
            printf("[FAIL] test_pthread: mutex_counter (create rc1=%d rc2=%d)\n", rc1, rc2);
        } else {
            pthread_join(t1, NULL);
            pthread_join(t2, NULL);
            if (g_counter == 2000) {
                printf("[PASS] test_pthread: mutex_counter\n");
            } else {
                printf("[FAIL] test_pthread: mutex_counter (got %d, expected 2000)\n", g_counter);
            }
        }
    }

    /* Test 3: __thread variable isolated between threads */
    {
        pthread_t t1, t2;
        int rc1 = pthread_create(&t1, NULL, thread_set_tls, (void*)111);
        int rc2 = pthread_create(&t2, NULL, thread_set_tls, (void*)222);
        if (rc1 != 0 || rc2 != 0) {
            printf("[FAIL] test_pthread: tls_per_thread (create failed)\n");
        } else {
            void *r1 = NULL, *r2 = NULL;
            pthread_join(t1, &r1);
            pthread_join(t2, &r2);
            if ((long)r1 == 111 && (long)r2 == 222) {
                printf("[PASS] test_pthread: tls_per_thread\n");
            } else {
                printf("[FAIL] test_pthread: tls_per_thread (r1=%ld, r2=%ld)\n",
                       (long)r1, (long)r2);
            }
        }
    }

    /* Test 4: create 4 threads, join all, verify results */
    {
        pthread_t threads[4];
        int ok = 1;
        for (int i = 0; i < 4; i++) {
            int rc = pthread_create(&threads[i], NULL, thread_square, (void*)(long)(i + 1));
            if (rc != 0) {
                printf("[FAIL] test_pthread: multiple_threads (create[%d] rc=%d)\n", i, rc);
                ok = 0;
                break;
            }
        }
        if (ok) {
            long expected[] = {1, 4, 9, 16};
            for (int i = 0; i < 4; i++) {
                void* retval = NULL;
                pthread_join(threads[i], &retval);
                if ((long)retval != expected[i]) {
                    printf("[FAIL] test_pthread: multiple_threads (thread[%d] got %ld, expected %ld)\n",
                           i, (long)retval, expected[i]);
                    ok = 0;
                }
            }
            if (ok) {
                printf("[PASS] test_pthread: multiple_threads\n");
            }
        }
    }

    /* Test 5: getpid() from thread matches main */
    {
        pthread_t t;
        int rc = pthread_create(&t, NULL, thread_getpid, NULL);
        if (rc != 0) {
            printf("[FAIL] test_pthread: thread_getpid (create rc=%d)\n", rc);
        } else {
            void* retval = NULL;
            pthread_join(t, &retval);
            pid_t thread_pid = (pid_t)(long)retval;
            if (thread_pid == g_main_pid) {
                printf("[PASS] test_pthread: thread_getpid\n");
            } else {
                printf("[FAIL] test_pthread: thread_getpid (main=%d, thread=%d)\n",
                       g_main_pid, thread_pid);
            }
        }
    }

    return 0;
}
