/*
 * test_pipe_musl.c — Pipe + fork tests through musl libc
 *
 * Linked with musl CRT + libc.so
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <sys/wait.h>

int main(void)
{
    /* Test 1: pipe creation */
    int pipefd[2];
    if (pipe(pipefd) < 0) {
        printf("[FAIL] test_pipe: create\n");
        return 1;
    }
    printf("[PASS] test_pipe: create\n");

    /* Test 2: pipe read/write */
    const char *msg = "Hello pipe!";
    write(pipefd[1], msg, strlen(msg));
    close(pipefd[1]);
    char buf[64];
    ssize_t n = read(pipefd[0], buf, sizeof(buf) - 1);
    close(pipefd[0]);
    if (n > 0 && strncmp(buf, "Hello pipe!", 11) == 0)
        printf("[PASS] test_pipe: read_write\n");
    else
        printf("[FAIL] test_pipe: read_write\n");

    /* Test 3: pipe + fork */
    if (pipe(pipefd) < 0) {
        printf("[FAIL] test_pipe: fork_pipe\n");
        return 1;
    }
    pid_t pid = fork();
    if (pid == 0) {
        /* Child: write to pipe */
        close(pipefd[0]);
        const char *child_msg = "from child";
        write(pipefd[1], child_msg, strlen(child_msg));
        close(pipefd[1]);
        _exit(0);
    }
    /* Parent: read from pipe */
    close(pipefd[1]);
    n = read(pipefd[0], buf, sizeof(buf) - 1);
    close(pipefd[0]);
    waitpid(pid, NULL, 0);
    if (n > 0) {
        buf[n] = '\0';
        if (strcmp(buf, "from child") == 0)
            printf("[PASS] test_pipe: fork_pipe\n");
        else
            printf("[FAIL] test_pipe: fork_pipe (got '%s')\n", buf);
    } else {
        printf("[FAIL] test_pipe: fork_pipe (n=%zd)\n", n);
    }

    /* Test 4: fprintf to stderr */
    fprintf(stderr, "[PASS] test_pipe: fprintf_stderr\n");

    return 0;
}
