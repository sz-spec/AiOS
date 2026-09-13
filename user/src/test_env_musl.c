/**
 * @file test_env_musl.c
 * @brief Environment, isatty, and procfs test — dynamically linked via musl libc.
 *
 * Tests: getenv, isatty, /proc/self/exe, /proc/self/maps.
 */
#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
    /* Test 1: getenv("PATH") */
    char *path = getenv("PATH");
    if (path && strstr(path, "/bin"))
        printf("[PASS] test_env: getenv_PATH\n");
    else
        printf("[FAIL] test_env: getenv_PATH (%s)\n", path ? path : "null");

    /* Test 2: getenv("HOME") */
    char *home = getenv("HOME");
    if (home && strcmp(home, "/") == 0)
        printf("[PASS] test_env: getenv_HOME\n");
    else
        printf("[FAIL] test_env: getenv_HOME (%s)\n", home ? home : "null");

    /* Test 3: getenv returns NULL for missing key */
    if (getenv("NONEXISTENT_KEY_XYZ") == NULL)
        printf("[PASS] test_env: getenv_missing\n");
    else
        printf("[FAIL] test_env: getenv_missing\n");

    /* Test 4: isatty(stdout) — requires TIOCGWINSZ ioctl to work */
    if (isatty(STDOUT_FILENO))
        printf("[PASS] test_env: isatty_stdout\n");
    else
        printf("[FAIL] test_env: isatty_stdout (errno)\n");

    /* Test 5: readlink(/proc/self/exe) */
    char buf[256];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = '\0';
        printf("[PASS] test_env: proc_self_exe (%s)\n", buf);
    } else {
        printf("[FAIL] test_env: proc_self_exe (readlink=%zd)\n", n);
    }

    /* Test 6: read /proc/self/maps */
    FILE *f = fopen("/proc/self/maps", "r");
    if (f) {
        int lines = 0;
        while (fgets(buf, sizeof(buf), f)) lines++;
        fclose(f);
        if (lines > 0)
            printf("[PASS] test_env: proc_self_maps (%d entries)\n", lines);
        else
            printf("[FAIL] test_env: proc_self_maps (empty)\n");
    } else {
        printf("[FAIL] test_env: proc_self_maps (fopen failed)\n");
    }

    return 0;
}
