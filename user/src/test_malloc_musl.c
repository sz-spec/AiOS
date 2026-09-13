/*
 * test_malloc_musl.c — Malloc/free/realloc/calloc tests through musl libc
 *
 * Exercises both brk (small) and mmap (large) allocation paths.
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    /* Test 1: basic malloc */
    char *p = malloc(256);
    if (!p) {
        printf("[FAIL] test_malloc: basic\n");
        return 1;
    }
    memset(p, 'A', 255);
    p[255] = '\0';
    printf("[PASS] test_malloc: basic_256\n");
    free(p);

    /* Test 2: stress — 1000 alloc/free cycles */
    int ok = 1;
    for (int i = 0; i < 1000; i++) {
        p = malloc(64);
        if (!p) { ok = 0; break; }
        *(int *)p = i;
        free(p);
    }
    printf("[%s] test_malloc: stress_1000\n", ok ? "PASS" : "FAIL");

    /* Test 3: large allocation (mmap path) */
    p = malloc(1024 * 1024);
    if (p) {
        memset(p, 0xBB, 1024 * 1024);
        printf("[PASS] test_malloc: large_1mb\n");
        free(p);
    } else {
        printf("[FAIL] test_malloc: large_1mb\n");
    }

    /* Test 4: calloc (zeroed memory) */
    int *arr = calloc(100, sizeof(int));
    ok = 1;
    if (arr) {
        for (int i = 0; i < 100; i++) {
            if (arr[i] != 0) { ok = 0; break; }
        }
        printf("[%s] test_malloc: calloc_zeroed\n", ok ? "PASS" : "FAIL");
        free(arr);
    } else {
        printf("[FAIL] test_malloc: calloc_zeroed\n");
    }

    /* Test 5: realloc preserves content */
    p = malloc(32);
    if (p) {
        strcpy(p, "Hello");
        p = realloc(p, 256);
        if (p && strcmp(p, "Hello") == 0)
            printf("[PASS] test_malloc: realloc_preserve\n");
        else
            printf("[FAIL] test_malloc: realloc_preserve\n");
        free(p);
    } else {
        printf("[FAIL] test_malloc: realloc_preserve\n");
    }

    return 0;
}
