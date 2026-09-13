/**
 * @file syscalls.c
 * @brief VOS3 User-Space System Call Wrappers
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "syscall.h"
#include "unistd.h"
#include "stdlib.h"

/* ============================================================================
 * PROCESS CONTROL
 * ============================================================================ */

pid_t fork(void)
{
    return (pid_t)syscall0(SYS_FORK);
}

int execve(const char *path, char *const argv[], char *const envp[])
{
    return (int)syscall3(SYS_EXECVE, (long)path, (long)argv, (long)envp);
}

int execv(const char *path, char *const argv[])
{
    return execve(path, argv, (char *const *)0);
}

int execvp(const char *file, char *const argv[])
{
    /* Simple implementation - just try the file directly */
    /* A full implementation would search PATH */
    return execve(file, argv, (char *const *)0);
}

void _exit(int status)
{
    syscall1(SYS_EXIT, status);
    /* Should never return */
    for (;;) {
        __asm__ volatile ("hlt");
    }
}

pid_t getpid(void)
{
    return (pid_t)syscall0(SYS_GETPID);
}

pid_t getppid(void)
{
    return (pid_t)syscall0(SYS_GETPPID);
}

pid_t wait(int *status)
{
    return waitpid(-1, status, 0);
}

pid_t waitpid(pid_t pid, int *status, int options)
{
    return (pid_t)syscall3(SYS_WAIT4, pid, (long)status, options);
}

uid_t getuid(void)
{
    return (uid_t)syscall0(102);  /* SYS_GETUID */
}

gid_t getgid(void)
{
    return (gid_t)syscall0(104);  /* SYS_GETGID */
}

int setuid(uid_t uid)
{
    return (int)syscall1(105, uid);  /* SYS_SETUID */
}

int setgid(gid_t gid)
{
    return (int)syscall1(106, gid);  /* SYS_SETGID */
}

/* ============================================================================
 * PROCESS GROUPS AND SESSIONS
 * ============================================================================ */

pid_t getpgid(pid_t pid)
{
    return (pid_t)syscall1(121, pid);  /* SYS_GETPGID */
}

int setpgid(pid_t pid, pid_t pgid)
{
    return (int)syscall2(109, pid, pgid);  /* SYS_SETPGID */
}

pid_t getpgrp(void)
{
    return getpgid(0);
}

int setpgrp(void)
{
    return setpgid(0, 0);
}

pid_t getsid(pid_t pid)
{
    /* SYS_GETSID = 124 */
    return (pid_t)syscall1(124, pid);
}

pid_t setsid(void)
{
    return (pid_t)syscall0(112);  /* SYS_SETSID */
}

/* ============================================================================
 * TERMINAL CONTROL
 * ============================================================================ */

/* IOCTL numbers - must match kernel */
#define SYS_IOCTL           16
#define TIOCGPGRP           0x2006
#define TIOCSPGRP           0x2007

pid_t tcgetpgrp(int fd)
{
    pid_t pgrp;
    int ret = (int)syscall3(SYS_IOCTL, fd, TIOCGPGRP, (long)&pgrp);
    if (ret < 0) {
        return (pid_t)-1;
    }
    return pgrp;
}

int tcsetpgrp(int fd, pid_t pgrp)
{
    return (int)syscall3(SYS_IOCTL, fd, TIOCSPGRP, (long)&pgrp);
}

/* ============================================================================
 * FILE OPERATIONS
 * ============================================================================ */

ssize_t read(int fd, void *buf, size_t count)
{
    return (ssize_t)syscall3(SYS_READ, fd, (long)buf, count);
}

ssize_t write(int fd, const void *buf, size_t count)
{
    return (ssize_t)syscall3(SYS_WRITE, fd, (long)buf, count);
}

int open(const char *pathname, int flags, unsigned int mode)
{
    return (int)syscall3(SYS_OPEN, (long)pathname, flags, mode);
}

int close(int fd)
{
    return (int)syscall1(SYS_CLOSE, fd);
}

int dup(int oldfd)
{
    return (int)syscall1(SYS_DUP, oldfd);
}

int dup2(int oldfd, int newfd)
{
    return (int)syscall2(SYS_DUP2, oldfd, newfd);
}

off_t lseek(int fd, off_t offset, int whence)
{
    return (off_t)syscall3(SYS_LSEEK, fd, offset, whence);
}

int unlink(const char *pathname)
{
    return (int)syscall1(SYS_UNLINK, (long)pathname);
}

int pipe(int pipefd[2])
{
    return (int)syscall1(SYS_PIPE, (long)pipefd);
}

/* ============================================================================
 * DIRECTORY OPERATIONS
 * ============================================================================ */

int chdir(const char *path)
{
    return (int)syscall1(SYS_CHDIR, (long)path);
}

char *getcwd(char *buf, size_t size)
{
    long ret = syscall2(SYS_GETCWD, (long)buf, size);
    if (ret < 0) {
        return (char *)0;
    }
    return buf;
}

int mkdir(const char *pathname, unsigned int mode)
{
    return (int)syscall2(SYS_MKDIR, (long)pathname, mode);
}

int rmdir(const char *pathname)
{
    return (int)syscall1(SYS_RMDIR, (long)pathname);
}

/* ============================================================================
 * SLEEP
 * ============================================================================ */

struct timespec {
    long tv_sec;
    long tv_nsec;
};

unsigned int sleep(unsigned int seconds)
{
    struct timespec req = { .tv_sec = seconds, .tv_nsec = 0 };
    struct timespec rem = { 0, 0 };

    long ret = syscall2(SYS_NANOSLEEP, (long)&req, (long)&rem);
    if (ret < 0) {
        return (unsigned int)rem.tv_sec;
    }
    return 0;
}

int usleep(unsigned int usec)
{
    struct timespec req = {
        .tv_sec = usec / 1000000,
        .tv_nsec = (usec % 1000000) * 1000
    };

    return (int)syscall2(SYS_NANOSLEEP, (long)&req, (long)0);
}

/* ============================================================================
 * MISC
 * ============================================================================ */

int isatty(int fd)
{
    /* Simple check - just see if we can read terminal attributes */
    /* For now, just check if fd is 0, 1, or 2 */
    return (fd >= 0 && fd <= 2) ? 1 : 0;
}

/* ============================================================================
 * DEVICE CONTROL (Phase 18)
 * ============================================================================ */

/**
 * @brief Perform device control operation
 * @param fd File descriptor
 * @param cmd IOCTL command
 * @param arg Command argument
 * @return 0 on success, negative on error
 */
int ioctl(int fd, unsigned int cmd, void* arg)
{
    return (int)syscall3(140, fd, cmd, (long)arg);  /* SYS_IOCTL = 140 in dev_init.c */
}
