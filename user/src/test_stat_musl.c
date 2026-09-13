/*
 * test_stat_musl.c — stat/openat tests through musl libc
 *
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>

int main(void)
{
    /* Test 1: fopen + fwrite (exercises openat through musl) */
    {
        FILE* f = fopen("/tmp/stat_test.txt", "w");
        if (f) {
            fprintf(f, "hello stat");
            fclose(f);
            printf("[PASS] test_stat: fopen_openat\n");
        } else {
            printf("[FAIL] test_stat: fopen_openat (errno=%d)\n", errno);
        }
    }

    /* Test 2: stat() on existing file, check st_size */
    {
        struct stat st;
        int rc = stat("/tmp/stat_test.txt", &st);
        if (rc == 0 && st.st_size == 10) {
            printf("[PASS] test_stat: stat_existing (size=%ld)\n", (long)st.st_size);
        } else if (rc == 0) {
            printf("[FAIL] test_stat: stat_existing (size=%ld, expected 10)\n", (long)st.st_size);
        } else {
            printf("[FAIL] test_stat: stat_existing (rc=%d, errno=%d)\n", rc, errno);
        }
    }

    /* Test 3: stat() on non-existent file returns error */
    {
        struct stat st;
        int rc = stat("/tmp/no_such_file_xyz", &st);
        if (rc != 0) {
            printf("[PASS] test_stat: stat_noent\n");
        } else {
            printf("[FAIL] test_stat: stat_noent (should have failed)\n");
        }
    }

    /* Test 4: fopen(read) + fread, verify content matches */
    {
        FILE* f = fopen("/tmp/stat_test.txt", "r");
        if (f) {
            char buf[64] = {0};
            size_t n = fread(buf, 1, sizeof(buf) - 1, f);
            fclose(f);
            if (n == 10 && strcmp(buf, "hello stat") == 0) {
                printf("[PASS] test_stat: fread_verify\n");
            } else {
                printf("[FAIL] test_stat: fread_verify (n=%zu, buf='%s')\n", n, buf);
            }
        } else {
            printf("[FAIL] test_stat: fread_verify (fopen failed)\n");
        }
    }

    /* Test 5: mkdir + stat + rmdir lifecycle */
    {
        int rc = mkdir("/tmp/stat_test_dir", 0755);
        if (rc != 0) {
            printf("[FAIL] test_stat: mkdir_stat_rmdir (mkdir failed)\n");
        } else {
            struct stat st;
            rc = stat("/tmp/stat_test_dir", &st);
            if (rc == 0 && (st.st_mode & 0170000) == 0040000) {
                rc = rmdir("/tmp/stat_test_dir");
                if (rc == 0) {
                    printf("[PASS] test_stat: mkdir_stat_rmdir\n");
                } else {
                    printf("[FAIL] test_stat: mkdir_stat_rmdir (rmdir failed)\n");
                }
            } else {
                printf("[FAIL] test_stat: mkdir_stat_rmdir (stat failed or not dir, mode=0%o)\n",
                       st.st_mode);
                rmdir("/tmp/stat_test_dir");
            }
        }
    }

    /* Cleanup */
    unlink("/tmp/stat_test.txt");

    return 0;
}
