/**
 * @file test_pt_interp.c
 * @brief PT_INTERP / dynamic linker support test (Task 2.1)
 *
 * Orchestrates the PT_INTERP test chain:
 *   1. Fork a child process
 *   2. Child calls execve("/bin/hello_dyn")
 *   3. Kernel detects PT_INTERP -> loads /bin/fake_interp at 0x40000000
 *   4. fake_interp validates auxv, prints PASS, jumps to hello_dyn _start
 *   5. hello_dyn prints PASS, exits 0
 *   6. Parent waits, verifies exit code == 0
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"

#define HELLO_DYN_PATH  "/bin/hello_dyn"

static int test_pt_interp_exec(void)
{
    pid_t pid = fork();

    if (pid < 0) {
        printf("[FAIL] test_pt_interp: fork failed\n");
        return 1;
    }

    if (pid == 0) {
        /* Child: exec hello_dyn which has PT_INTERP */
        char *argv[] = { "hello_dyn", (char *)0 };
        char *envp[] = { "PATH=/bin", (char *)0 };
        execve(HELLO_DYN_PATH, argv, envp);
        /* If we get here, execve failed */
        printf("[FAIL] test_pt_interp: execve failed\n");
        exit(1);
    }

    /* Parent: wait for child */
    int status;
    waitpid(pid, &status, 0);

    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        printf("[PASS] test_pt_interp: child_exit_ok\n");
        return 0;
    } else {
        printf("[FAIL] test_pt_interp: child exit=%d\n",
               WIFEXITED(status) ? WEXITSTATUS(status) : -1);
        return 1;
    }
}

int main(int argc, char *argv[], char *envp[])
{
    (void)argc;
    (void)argv;
    (void)envp;

    printf("=== PT_INTERP / Dynamic Linker Test (Task 2.1) ===\n");

    int rc = test_pt_interp_exec();

    printf("=== PT_INTERP test %s ===\n", rc == 0 ? "PASSED" : "FAILED");
    return rc;
}
