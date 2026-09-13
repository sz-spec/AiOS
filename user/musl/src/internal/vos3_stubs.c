/* vos3_stubs.c — Stubs for functions not available on VOS3
 *
 * These provide minimal implementations for symbols referenced by musl's
 * internal code that depend on features VOS3 doesn't support.
 *
 * Task 2.7: Most pthread stubs removed — real musl pthread is now compiled.
 * Remaining stubs are for functions with no real implementation yet.
 */

#define hidden __attribute__((__visibility__("hidden")))
#define weak __attribute__((__weak__))

typedef unsigned long size_t;
typedef long ssize_t;

/* ============================================================================
 * Internal stubs still needed
 * ============================================================================ */

/* __setxid: set uid/gid across all threads — just do the raw syscall */
hidden int __setxid(int nr, int a, int b, int c)
{
    long ret;
    register long r10 __asm__("r10") = 0;
    register long r8  __asm__("r8")  = 0;
    __asm__ volatile (
        "syscall"
        : "=a"(ret)
        : "a"((long)nr), "D"((long)a), "S"((long)b), "d"((long)c), "r"(r10), "r"(r8)
        : "rcx", "r11", "memory"
    );
    return (int)ret;
}

/* __lsysinfo: vDSO entry point (not available on VOS3) */
hidden long __lsysinfo = 0;

/* __membarrier: memory barrier (no-op on single-core) */
hidden void __membarrier(int cmd, unsigned flags)
{
    (void)cmd; (void)flags;
    __asm__ volatile("" ::: "memory");
}

/* __mkostemps: temporary file creation (stub) */
int __mkostemps(char *template, int sfxlen, int flags)
{
    (void)template; (void)sfxlen; (void)flags;
    return -38; /* ENOSYS */
}

/* ============================================================================
 * posix_spawn stubs — referenced by musl's internal code
 * ============================================================================ */

typedef struct { int __flags; } posix_spawnattr_t;
typedef struct { int __pad0[2]; void *__actions; int __pad[16]; } posix_spawn_file_actions_t;

weak int posix_spawn(void *pid, const char *path, const void *fa,
                     const void *sa, char *const *argv, char *const *envp)
{
    (void)pid; (void)path; (void)fa; (void)sa; (void)argv; (void)envp;
    return 38; /* ENOSYS (posix_spawn returns positive errno) */
}

weak int posix_spawn_file_actions_init(posix_spawn_file_actions_t *fa)
{
    (void)fa;
    return 0;
}

weak int posix_spawn_file_actions_destroy(posix_spawn_file_actions_t *fa)
{
    (void)fa;
    return 0;
}

weak int posix_spawn_file_actions_addclose(posix_spawn_file_actions_t *fa, int fd)
{
    (void)fa; (void)fd;
    return 0;
}

weak int posix_spawn_file_actions_adddup2(posix_spawn_file_actions_t *fa, int fd, int newfd)
{
    (void)fa; (void)fd; (void)newfd;
    return 0;
}

weak int posix_spawn_file_actions_addopen(posix_spawn_file_actions_t *fa, int fd,
                                          const char *path, int flags, unsigned mode)
{
    (void)fa; (void)fd; (void)path; (void)flags; (void)mode;
    return 0;
}

weak int posix_spawnattr_init(posix_spawnattr_t *sa)
{
    (void)sa;
    return 0;
}

weak int posix_spawnattr_destroy(posix_spawnattr_t *sa)
{
    (void)sa;
    return 0;
}

weak int posix_spawnattr_setflags(posix_spawnattr_t *sa, short flags)
{
    (void)sa; (void)flags;
    return 0;
}

weak int posix_spawnattr_setsigdefault(posix_spawnattr_t *sa, const void *set)
{
    (void)sa; (void)set;
    return 0;
}

weak int posix_spawnattr_setsigmask(posix_spawnattr_t *sa, const void *set)
{
    (void)sa; (void)set;
    return 0;
}

/* ============================================================================
 * Misc stubs — referenced by musl internals
 * ============================================================================ */

weak int prctl(int option, ...)
{
    (void)option;
    return -38; /* ENOSYS */
}

struct sysinfo_s { long uptime; unsigned long loads[3]; unsigned long totalram; };
weak int sysinfo(struct sysinfo_s *info)
{
    (void)info;
    return -38; /* ENOSYS */
}

weak int nftw(const char *path, void *fn, int fd_limit, int flags)
{
    (void)path; (void)fn; (void)fd_limit; (void)flags;
    return -38; /* ENOSYS */
}

weak int getpwuid_r(unsigned uid, void *pw, char *buf, size_t sz, void **res)
{
    (void)uid; (void)pw; (void)buf; (void)sz;
    if (res) *(void **)res = (void *)0;
    return 0; /* not found */
}

weak int getpwnam_r(const char *name, void *pw, char *buf, size_t sz, void **res)
{
    (void)name; (void)pw; (void)buf; (void)sz;
    if (res) *(void **)res = (void *)0;
    return 0; /* not found */
}

weak ssize_t getrandom(void *buf, size_t len, unsigned flags)
{
    (void)flags;
    /* Fill with simple PRNG — better than nothing for AT_RANDOM seed */
    unsigned char *p = (unsigned char *)buf;
    static unsigned long state = 0x12345678UL;
    for (size_t i = 0; i < len; i++) {
        state = state * 6364136223846793005ULL + 1442695040888963407ULL;
        p[i] = (unsigned char)(state >> 33);
    }
    return (ssize_t)len;
}
