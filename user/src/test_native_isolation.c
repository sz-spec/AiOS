/* Native diagnostic ELF: four direct CPU accesses, never a hosted simulation. */
#include "unistd.h"
#include "stdio.h"
#include "stdlib.h"
#include "stdint.h"
#include "poll.h"
#include "syscall.h"
#include "string.h"

#define VICTIM_ADDRESS 0x7000000000ULL
#define PAGE_BYTES 4096U
#define FAULT_STATUS (139 << 8) /* Current vOS wait4 ABI, not WIFSIGNALED. */

static unsigned cpl(void)
{
    unsigned short cs;
    __asm__ volatile("mov %%cs, %0" : "=r"(cs));
    return cs & 3U;
}

static void fail(const char* reason)
{
    printf("NATIVE_ISOLATION FAIL reason=%s pid=%d\n", reason, getpid());
    _exit(90);
}

static unsigned char receive(int fd)
{
    struct pollfd p = {fd, POLLIN, 0};
    unsigned char value = 0;
    if (syscall3(SYS_POLL, (long)&p, 1, 5000) != 1 || !(p.revents & POLLIN)) fail("pipe_timeout");
    if (read(fd, &value, 1) != 1) fail("pipe_read");
    return value;
}

static void send_byte(int fd, unsigned char value)
{
    if (write(fd, &value, 1) != 1) fail("pipe_write");
}

static int collect(pid_t child)
{
    for (unsigned i = 0; i < 5000; ++i) {
        int status = -1;
        pid_t got = waitpid(child, &status, 1 /* WNOHANG */);
        if (got == child) return status;
        if (got != 0) fail("waitpid");
        if (usleep(1000) != 0) fail("wait_sleep");
    }
    fail("wait_timeout");
    return -1;
}

static unsigned char expected(unsigned offset)
{
    return (unsigned char)(0xA5U ^ (offset * 37U));
}

static void victim(int commands, int replies)
{
    if (cpl() != 3) fail("victim_privilege");
    long result = syscall6(9 /* mmap */, VICTIM_ADDRESS, PAGE_BYTES,
                          3 /* PROT_READ|PROT_WRITE */,
                          0x32 /* PRIVATE|FIXED|ANONYMOUS */, -1, 0);
    if ((uintptr_t)result != VICTIM_ADDRESS) fail("victim_mmap");
    volatile unsigned char* page = (volatile unsigned char*)VICTIM_ADDRESS;
    for (unsigned i = 0; i < PAGE_BYTES; ++i) page[i] = expected(i);
    pid_t self = getpid();
    printf("NATIVE_ISOLATION role=victim pid=%d cpl=%u address=0x%llx ready=1\n",
           self, cpl(), VICTIM_ADDRESS);
    send_byte(replies, 0x55);
    for (unsigned char challenge = 1; challenge <= 4; ++challenge) {
        if (receive(commands) != challenge) fail("victim_challenge");
        for (unsigned i = 0; i < PAGE_BYTES; ++i)
            if (page[i] != expected(i)) fail("victim_canary");
        if (getpid() != self || cpl() != 3) fail("victim_progress");
        send_byte(replies, challenge);
    }
    _exit(0);
}

int main(int argc, char** argv, char** envp)
{
    (void)argc;
    (void)argv;
    if (cpl() != 3) fail("parent_privilege");
    /* This small native CRT passes envp but does not initialize getenv(). */
    static const char prefix[] = "VOS_NATIVE_KERNEL_TARGET=";
    const char* text = NULL;
    for (unsigned i = 0; envp && i < 64 && envp[i]; ++i)
        if (strncmp(envp[i], prefix, sizeof(prefix) - 1) == 0)
            text = envp[i] + sizeof(prefix) - 1;
    if (!text) fail("missing_kernel_target");
    char* end;
    uintptr_t kernel = (uintptr_t)strtoull(text, &end, 16);
    if (*end || kernel < 0xFFFF800000000000ULL || (kernel & 4095)) fail("kernel_target");
    pid_t parent = getpid();
    printf("NATIVE_ISOLATION role=parent pid=%d cpl=%u\n", parent, cpl());
    int command[2], reply[2];
    if (pipe(command) != 0 || pipe(reply) != 0) fail("pipe_setup");
    pid_t witness = fork();
    if (witness < 0) fail("victim_fork");
    if (witness == 0) {
        close(command[1]); close(reply[0]);
        victim(command[0], reply[1]);
    }
    close(command[0]); close(reply[1]);
    if (receive(reply[0]) != 0x55) fail("victim_ready");
    /* Only the victim mapped the page. The coordinator never has that VMA,
       so attackers forked from it cannot inherit the victim mapping. */
    static const char* names[] = {"foreign_read", "foreign_write", "kernel_read", "kernel_write"};
    for (unsigned i = 0; i < 4; ++i) {
        uintptr_t address = i < 2 ? VICTIM_ADDRESS : kernel;
        pid_t child = fork();
        if (child < 0) fail("attacker_fork");
        if (child == 0) {
            close(command[1]); close(reply[0]);
            if (cpl() != 3) fail("attacker_privilege");
            printf("NATIVE_ISOLATION case=%s pid=%d address=0x%llx operation=%s cpl=%u attempt=1\n",
                   names[i], getpid(), (unsigned long long)address, i & 1 ? "write" : "read", cpl());
            volatile unsigned char* target = (volatile unsigned char*)address;
            if (i & 1) *target = 0x3C;
            else { volatile unsigned char observed = *target; (void)observed; }
            fail("access_returned");
        }
        int status = collect(child);
        if (status != FAULT_STATUS) fail("unexpected_child_status");
        printf("NATIVE_ISOLATION case=%s pid=%d wait_status=%d contained=1\n", names[i], child, status);
        send_byte(command[1], (unsigned char)(i + 1));
        if (receive(reply[0]) != i + 1) fail("witness_ack");
        printf("NATIVE_ISOLATION case=%s victim_pid=%d ack=1 canary=1 challenge=%u\n", names[i], witness, i + 1);
    }
    int status = collect(witness);
    if (status != 0) fail("victim_exit");
    close(command[1]); close(reply[0]);
    printf("NATIVE_ISOLATION complete=1 cases=4 parent_pid=%d victim_status=%d\n", parent, status);
    if (usleep(10000) != 0 || getpid() != parent || cpl() != 3) fail("final_progress");
    printf("NATIVE_ISOLATION progress=1 parent_pid=%d\n", parent);
    for (;;) usleep(100000);
}
