/* hello_musl.c — Dynamically-linked program using musl libc
 * Linked against musl's crt1.o + libc.so
 * PT_INTERP = /bin/ld-musl-x86_64.so.1
 */
#include <stdio.h>

int main(int argc, char **argv)
{
    printf("[PASS] test_dynlink: musl_ldso_hello\n");
    printf("[hello_musl] Hello from musl libc on VOS3!\n");
    printf("[hello_musl] argc=%d, argv[0]=%s\n", argc, argv[0] ? argv[0] : "(null)");
    return 0;
}
