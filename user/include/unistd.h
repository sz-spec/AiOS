/**
 * @file unistd.h
 * @brief VOS3 User-Space POSIX-like Definitions
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_UNISTD_H
#define VOS3_USER_UNISTD_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TYPE DEFINITIONS
 * ============================================================================ */

typedef int32_t pid_t;
typedef int32_t ssize_t;
typedef uint32_t uid_t;
typedef uint32_t gid_t;
typedef int64_t off_t;

/* ============================================================================
 * STANDARD FILE DESCRIPTORS
 * ============================================================================ */

#define STDIN_FILENO    0
#define STDOUT_FILENO   1
#define STDERR_FILENO   2

/* ============================================================================
 * SEEK CONSTANTS
 * ============================================================================ */

#define SEEK_SET    0
#define SEEK_CUR    1
#define SEEK_END    2

/* ============================================================================
 * ACCESS MODE FLAGS
 * ============================================================================ */

#define F_OK    0   /* File exists */
#define X_OK    1   /* Execute permission */
#define W_OK    2   /* Write permission */
#define R_OK    4   /* Read permission */

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/* Process control */
pid_t fork(void);
int execve(const char *path, char *const argv[], char *const envp[]);
int execv(const char *path, char *const argv[]);
int execvp(const char *file, char *const argv[]);
void _exit(int status) __attribute__((noreturn));
pid_t getpid(void);
pid_t getppid(void);
pid_t wait(int *status);
pid_t waitpid(pid_t pid, int *status, int options);

/* Open flags */
#define O_RDONLY    0
#define O_WRONLY    1
#define O_RDWR      2
#define O_CREAT     0100
#define O_TRUNC     01000
#define O_APPEND    02000

/* File operations */
int open(const char *pathname, int flags, unsigned int mode);
ssize_t read(int fd, void *buf, size_t count);
ssize_t write(int fd, const void *buf, size_t count);
int close(int fd);
int dup(int oldfd);
int dup2(int oldfd, int newfd);
off_t lseek(int fd, off_t offset, int whence);
int unlink(const char *pathname);
int pipe(int pipefd[2]);

/* Directory operations */
int chdir(const char *path);
char *getcwd(char *buf, size_t size);
int mkdir(const char *pathname, unsigned int mode);
int rmdir(const char *pathname);

/* Sleep */
unsigned int sleep(unsigned int seconds);
int usleep(unsigned int usec);

/* User/Group */
uid_t getuid(void);
gid_t getgid(void);
int setuid(uid_t uid);
int setgid(gid_t gid);

/* Process Groups and Sessions */
pid_t getpgid(pid_t pid);
int setpgid(pid_t pid, pid_t pgid);
pid_t getpgrp(void);
int setpgrp(void);
pid_t getsid(pid_t pid);
pid_t setsid(void);

/* Terminal Control */
pid_t tcgetpgrp(int fd);
int tcsetpgrp(int fd, pid_t pgrp);

/* Misc */
int isatty(int fd);

#endif /* VOS3_USER_UNISTD_H */
