/*
 * test_fork_musl.c — Fork/waitpid/getpid tests through musl libc
 *
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>

int main(void)
{
    /* Test 1: getpid */
    pid_t my_pid = getpid();
    if (my_pid > 0)
        printf("[PASS] test_fork: getpid (%d)\n", my_pid);
    else
        printf("[FAIL] test_fork: getpid\n");

    /* Test 2: fork + child exit */
    pid_t pid = fork();
    if (pid < 0) {
        printf("[FAIL] test_fork: fork\n");
        return 1;
    }
    if (pid == 0) {
        /* Child */
        printf("[PASS] test_fork: child_runs (pid=%d)\n", getpid());
        _exit(42);
    }
    /* Parent */
    int status;
    waitpid(pid, &status, 0);
    if (WIFEXITED(status) && WEXITSTATUS(status) == 42)
        printf("[PASS] test_fork: waitpid_exit_code\n");
    else
        printf("[FAIL] test_fork: waitpid_exit_code (status=0x%x)\n", status);

    /* Test 3: getenv through musl */
    char *path = getenv("PATH");
    if (path && strstr(path, "/bin"))
        printf("[PASS] test_fork: getenv_PATH\n");
    else
        printf("[FAIL] test_fork: getenv_PATH (%s)\n", path ? path : "null");

    return 0;
}
