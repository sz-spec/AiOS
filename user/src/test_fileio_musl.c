/*
 * test_fileio_musl.c — File I/O tests through musl libc
 *
 * Tests: fopen, fprintf, fgets, snprintf, remove
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>

int main(void)
{
    /* Test 1: fopen write */
    FILE *f = fopen("/tmp/vos3_test", "w");
    if (!f) {
        printf("[FAIL] test_fileio: fopen_write\n");
        return 1;
    }
    printf("[PASS] test_fileio: fopen_write\n");

    /* Test 2: fprintf */
    int n = fprintf(f, "Hello VOS3 file I/O\n");
    if (n > 0)
        printf("[PASS] test_fileio: fprintf (%d bytes)\n", n);
    else
        printf("[FAIL] test_fileio: fprintf\n");
    fclose(f);

    /* Test 3: fopen read + fgets */
    f = fopen("/tmp/vos3_test", "r");
    if (!f) {
        printf("[FAIL] test_fileio: fopen_read\n");
        return 1;
    }
    char buf[64];
    if (fgets(buf, sizeof(buf), f) && strstr(buf, "Hello VOS3"))
        printf("[PASS] test_fileio: fgets_readback\n");
    else
        printf("[FAIL] test_fileio: fgets_readback\n");
    fclose(f);

    /* Test 4: snprintf */
    char sbuf[16];
    int sn = snprintf(sbuf, sizeof(sbuf), "%d+%d=%d", 2, 3, 5);
    if (sn == 5 && strcmp(sbuf, "2+3=5") == 0)
        printf("[PASS] test_fileio: snprintf\n");
    else
        printf("[FAIL] test_fileio: snprintf\n");

    /* Test 5: remove */
    if (remove("/tmp/vos3_test") == 0)
        printf("[PASS] test_fileio: remove\n");
    else
        printf("[FAIL] test_fileio: remove\n");

    return 0;
}
