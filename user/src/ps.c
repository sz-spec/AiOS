/**
 * @file ps.c
 * @brief VOS3 Process Status — reads /proc to display running processes
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

#define SYS_OPEN    2
#define SYS_CLOSE   3
#define SYS_READ    0
#define SYS_GETDENTS 78

/* Dirent matching kernel's vos3_dirent_t */
struct dirent {
    unsigned int    ino;
    unsigned short  reclen;
    unsigned char   type;
    char            name[64];
};

static int open_read_close(const char* path, char* buf, int bufsz)
{
    long fd = syscall3(SYS_OPEN, (long)path, 0, 0);
    if (fd < 0) return -1;
    long nr = syscall3(SYS_READ, fd, (long)buf, bufsz - 1);
    syscall1(SYS_CLOSE, fd);
    if (nr < 0) return -1;
    buf[nr] = '\0';
    return (int)nr;
}

static int is_digit(char c) { return c >= '0' && c <= '9'; }

/* Extract value after "Key:\t" from status text */
static int extract_field(const char* text, const char* key, char* out, int outsz)
{
    const char* p = text;
    int klen = 0;
    const char* k = key;
    while (*k) { klen++; k++; }

    while (*p) {
        /* Check if line starts with key */
        int match = 1;
        for (int i = 0; i < klen; i++) {
            if (p[i] != key[i]) { match = 0; break; }
        }
        if (match) {
            p += klen;
            /* Skip tab */
            if (*p == '\t') p++;
            int i = 0;
            while (*p && *p != '\n' && i < outsz - 1) {
                out[i++] = *p++;
            }
            out[i] = '\0';
            return i;
        }
        /* Skip to next line */
        while (*p && *p != '\n') p++;
        if (*p == '\n') p++;
    }
    out[0] = '\0';
    return 0;
}

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    printf("  PID  PPID  STATE       NAME\n");
    printf("-----  ----  ----------  ----------------\n");

    /* Read /proc directory to find PID entries */
    long dfd = syscall3(SYS_OPEN, (long)"/proc", 0x10000 /* O_DIRECTORY */, 0);
    if (dfd < 0) {
        printf("ps: cannot open /proc\n");
        return 1;
    }

    struct dirent entries[64];
    long out_count = 0;
    long rc = syscall4(SYS_GETDENTS, dfd, (long)entries, 64, (long)&out_count);
    syscall1(SYS_CLOSE, dfd);

    if (rc < 0) {
        printf("ps: cannot read /proc\n");
        return 1;
    }

    for (long i = 0; i < out_count; i++) {
        const char* name = entries[i].name;
        /* Only process numeric entries (PIDs) */
        if (!is_digit(name[0])) continue;

        /* Build path: /proc/<pid>/status */
        char path[128];
        int pi = 0;
        const char* pre = "/proc/";
        while (*pre) path[pi++] = *pre++;
        const char* n = name;
        while (*n) path[pi++] = *n++;
        const char* suf = "/status";
        while (*suf) path[pi++] = *suf++;
        path[pi] = '\0';

        char status_buf[512];
        if (open_read_close(path, status_buf, sizeof(status_buf)) <= 0)
            continue;

        char pid_str[16], ppid_str[16], state_str[16], name_str[32];
        extract_field(status_buf, "Pid:", pid_str, sizeof(pid_str));
        extract_field(status_buf, "PPid:", ppid_str, sizeof(ppid_str));
        extract_field(status_buf, "State:", state_str, sizeof(state_str));
        extract_field(status_buf, "Name:", name_str, sizeof(name_str));

        printf("%5s  %4s  %-10s  %s\n", pid_str, ppid_str, state_str, name_str);
    }

    return 0;
}
