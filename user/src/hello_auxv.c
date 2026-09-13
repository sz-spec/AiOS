/**
 * @file hello_auxv.c
 * @brief Minimal binary with PT_INTERP pointing to /bin/auxv_interp
 *
 * Built with hello_auxv.ld to create a PT_INTERP program header.
 * When executed, the kernel loads auxv_interp as the interpreter,
 * which deeply validates the auxiliary vector, then jumps to this
 * program's entry point.
 */

#include "stdio.h"
#include "stdlib.h"

/* This section is mapped to a PT_INTERP program header by hello_auxv.ld */
const char __attribute__((section(".interp")))
    __interp[] = "/bin/auxv_interp";

int main(int argc, char *argv[], char *envp[])
{
    (void)argc;
    (void)argv;
    (void)envp;

    printf("[PASS] test_dynlink: hello_auxv_executed\n");
    return 0;
}
