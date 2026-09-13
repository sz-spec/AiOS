/**
 * @file hello_dyn.c
 * @brief Minimal binary with PT_INTERP for VOS3 dynamic linker test
 *
 * Built with a custom linker script (hello_dyn.ld) that creates a
 * PT_INTERP program header pointing to /bin/fake_interp.
 * When executed, the kernel sees PT_INTERP, loads fake_interp as
 * the interpreter, and enters fake_interp first.  fake_interp
 * validates the auxiliary vector, then jumps back to this program's
 * entry (_start via crt0).
 */

#include "stdio.h"
#include "stdlib.h"

/* This section is mapped to a PT_INTERP program header by hello_dyn.ld */
const char __attribute__((section(".interp")))
    __interp[] = "/bin/fake_interp";

int main(int argc, char *argv[], char *envp[])
{
    (void)argc;
    (void)argv;
    (void)envp;

    printf("[PASS] test_pt_interp: hello_dyn_executed\n");
    return 0;
}
