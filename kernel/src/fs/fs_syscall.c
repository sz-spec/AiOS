/**
 * @file fs_syscall.c
 * @brief VOS3 File System System Calls
 *
 * @details System call handlers for file system operations.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/vfs.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/console.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/user.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/epoll.h"
#include "../../include/vos/entropy.h"

/* ============================================================================
 * SYSCALL NUMBERS (Linux x86_64 compatible)
 * ============================================================================ */

#define SYS_READ        0
#define SYS_WRITE       1
#define SYS_OPEN        2
#define SYS_CLOSE       3
#define SYS_STAT        4
#define SYS_FSTAT       5
#define SYS_POLL        7
#define SYS_LSEEK       8
#define SYS_PIPE        22
#define SYS_DUP         32
#define SYS_DUP2        33
#define SYS_FCNTL       72
#define SYS_FSYNC       74
#define SYS_TRUNCATE    76
#define SYS_FTRUNCATE   77
#define SYS_GETDENTS    78
#define SYS_GETCWD      79
#define SYS_CHDIR       80
#define SYS_RENAME      82
#define SYS_MKDIR       83
#define SYS_RMDIR       84
#define SYS_UNLINK      87
#define SYS_STATFS      137
#define SYS_MOUNT       165
#define SYS_UMOUNT      166

/* Task 1.2: New POSIX syscall numbers (Linux x86-64 ABI) */
#define SYS_LSTAT       6
#define SYS_PREAD64     17
#define SYS_PWRITE64    18
#define SYS_READLINK    89
#define SYS_LINK        86
#define SYS_SYMLINK     88
#define SYS_CHMOD       90
#define SYS_FCHMOD      91
#define SYS_CHOWN       92
#define SYS_FCHOWN      93
#define SYS_GETDENTS64  217
#define SYS_IOCTL_LINUX 16
#define SYS_READV       19
#define SYS_WRITEV      20
#define SYS_ACCESS      21
#define SYS_DUP3        292
#define SYS_PIPE2       293

/* Task 2.7: New syscalls for musl stat/openat/getrandom */
#define SYS_OPENAT      257
#define SYS_NEWFSTATAT  262
#define SYS_GETRANDOM   318
#define SYS_STATX       332

/* Task 2.7: openat/fstatat constants */
#define AT_FDCWD            (-100)
#define AT_SYMLINK_NOFOLLOW 0x100
#define AT_NO_AUTOMOUNT     0x800
#define O_LARGEFILE         0x8000

/* Phase C: utimensat constants */
#define SYS_UTIMENSAT   280
#define UTIME_NOW       0x3FFFFFFF
#define UTIME_OMIT      0x3FFFFFFE

/* Task 1.2: errno values for new handlers */
#define EINVAL          22
#define EBADF           9
#define ENOENT          2

/* ============================================================================
 * Linux kstat structure — must match musl's arch/x86_64/kstat.h exactly
 * ============================================================================ */

typedef struct {
    uint64_t st_dev;        /* offset 0 */
    uint64_t st_ino;        /* offset 8 */
    uint64_t st_nlink;      /* offset 16 */
    uint32_t st_mode;       /* offset 24 */
    uint32_t st_uid;        /* offset 28 */
    uint32_t st_gid;        /* offset 32 */
    uint32_t __pad0;        /* offset 36 */
    uint64_t st_rdev;       /* offset 40 */
    int64_t  st_size;       /* offset 48 */
    int64_t  st_blksize;    /* offset 56 */
    int64_t  st_blocks;     /* offset 64 */
    int64_t  st_atime_sec;  /* offset 72 */
    int64_t  st_atime_nsec; /* offset 80 */
    int64_t  st_mtime_sec;  /* offset 88 */
    int64_t  st_mtime_nsec; /* offset 96 */
    int64_t  st_ctime_sec;  /* offset 104 */
    int64_t  st_ctime_nsec; /* offset 112 */
    int64_t  __reserved[3]; /* offset 120 */
} linux_kstat_t;            /* Total: 144 bytes */

_Static_assert(sizeof(linux_kstat_t) == 144, "linux_kstat_t must be 144 bytes");

static void inode_to_kstat(const vos3_inode_t* inode, linux_kstat_t* kst)
{
    memset(kst, 0, sizeof(*kst));
    kst->st_dev     = 1;  /* single device */
    kst->st_ino     = (uint64_t)inode->ino;
    kst->st_nlink   = (uint64_t)inode->nlink;
    kst->st_mode    = inode->mode;
    kst->st_uid     = inode->uid;
    kst->st_gid     = inode->gid;
    kst->st_rdev    = 0;
    kst->st_size    = (int64_t)inode->size;
    kst->st_blksize = 4096;
    kst->st_blocks  = ((int64_t)inode->size + 511) / 512;
    kst->st_atime_sec  = (int64_t)inode->atime;
    kst->st_mtime_sec  = (int64_t)inode->mtime;
    kst->st_ctime_sec  = (int64_t)inode->ctime;
}

/* Task 1.2: O_CLOEXEC / FD_CLOEXEC */
#define O_CLOEXEC       0x80000    /* Linux O_CLOEXEC */
#define FD_CLOEXEC      1          /* Close-on-exec flag bit in fd_entry.flags */

/* ============================================================================
 * POLL Constants (matches user-space poll.h)
 * ============================================================================ */

#define POLLIN      0x0001
#define POLLPRI     0x0002
#define POLLOUT     0x0004
#define POLLERR     0x0008
#define POLLHUP     0x0010
#define POLLNVAL    0x0020

struct pollfd {
    int     fd;
    short   events;
    short   revents;
};

/* ============================================================================
 * fcntl constants
 * ============================================================================ */

#define F_GETFD     1
#define F_SETFD     2
#define F_GETFL     3
#define F_SETFL     4
#define F_DUPFD     0

/* ============================================================================
 * Phase 29: User Pointer Validation Helpers
 * ============================================================================ */

/* vfs.h's VOS3_PATH_MAX (256) is intentionally small — it sizes VFS
 * mount-point path fields in structs.  Syscall path copy buffers need
 * POSIX PATH_MAX (4096) to handle arbitrary user paths.  Undefine the
 * structural value and redefine locally for this purpose. */
#undef  VOS3_PATH_MAX
#define VOS3_PATH_MAX   4096

/**
 * @brief Safely copy a path from user space
 * @param[out] kpath  Kernel buffer (must be at least VOS3_PATH_MAX)
 * @param[in]  upath  User space path pointer
 * @return 0 on success, -EFAULT on invalid pointer
 */
static int copy_path_from_user(char* kpath, const char* upath)
{
    int64_t len = strncpy_from_user(kpath, upath, VOS3_PATH_MAX);
    if (len < 0) {
        return -EFAULT;
    }
    return 0;
}

/* ============================================================================
 * SYSCALL HANDLERS
 * ============================================================================ */

/**
 * @brief sys_open - Open file (Phase 29: Hardened)
 */
static int64_t sys_open(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    uint32_t flags = (uint32_t)frame->rsi;
    uint32_t mode = (uint32_t)frame->rdx;
    char kpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_open(kpath, flags, mode);
}

/**
 * @brief sys_close - Close file
 */
static int64_t sys_close(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    return (int64_t)vos3_close(fd);
}

/** @brief Maximum read buffer for kernel bounce (64 KB) */
#define SYS_READ_MAX_BOUNCE     ((size_t)65536U)

/**
 * @brief sys_read - Read from file (secure implementation)
 *
 * Reads data from file descriptor into a kernel buffer, then
 * securely copies to user space using copy_to_user().
 */
static int64_t sys_read(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    void* user_buf = (void*)frame->rsi;
    size_t count = (size_t)frame->rdx;
    void* kernel_buf;
    int64_t bytes_read;
    size_t chunk_size;
    size_t total_read = 0;

    /* Validate user buffer pointer */
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Handle zero-length read */
    if (count == 0U) {
        return 0;
    }

    /* For small reads, use stack buffer to avoid heap allocation */
    if (count <= 256U) {
        uint8_t stack_buf[256];
        bytes_read = vos3_read(fd, stack_buf, count);
        if (bytes_read <= 0) {
            return bytes_read;
        }
        if (copy_to_user(user_buf, stack_buf, (size_t)bytes_read) != 0) {
            return -EFAULT;
        }
        return bytes_read;
    }

    /* For larger reads, use heap bounce buffer with chunking */
    chunk_size = (count < SYS_READ_MAX_BOUNCE) ? count : SYS_READ_MAX_BOUNCE;
    kernel_buf = vos3_kmalloc(chunk_size);
    if (kernel_buf == NULL) {
        return -EFAULT;  /* No memory */
    }

    /* Read in chunks and copy to user space */
    while (total_read < count) {
        size_t to_read = count - total_read;
        if (to_read > chunk_size) {
            to_read = chunk_size;
        }

        bytes_read = vos3_read(fd, kernel_buf, to_read);
        if (bytes_read <= 0) {
            /* EOF or error */
            if (total_read == 0) {
                vos3_kfree(kernel_buf);
                return bytes_read;
            }
            break;  /* Return what we have so far */
        }

        /* Copy chunk to user space */
        if (copy_to_user((uint8_t*)user_buf + total_read,
                         kernel_buf, (size_t)bytes_read) != 0) {
            vos3_kfree(kernel_buf);
            return -EFAULT;
        }

        total_read += (size_t)bytes_read;

        /* Short read indicates EOF */
        if ((size_t)bytes_read < to_read) {
            break;
        }
    }

    vos3_kfree(kernel_buf);
    return (int64_t)total_read;
}

/** @brief Maximum write buffer for kernel bounce (64 KB) */
#define SYS_WRITE_MAX_BOUNCE    ((size_t)65536U)

/**
 * @brief sys_write - Write to file (secure implementation)
 *
 * Copies data from user space using copy_from_user(), then
 * writes to file descriptor from kernel buffer.
 */
static int64_t sys_write(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    const void* user_buf = (const void*)frame->rsi;
    size_t count = (size_t)frame->rdx;
    void* kernel_buf;
    int64_t bytes_written;
    size_t chunk_size;
    size_t total_written = 0;

    /* Validate user buffer pointer */
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Handle zero-length write */
    if (count == 0U) {
        return 0;
    }

    /* For small writes, use stack buffer to avoid heap allocation */
    if (count <= 256U) {
        uint8_t stack_buf[256];
        if (copy_from_user(stack_buf, user_buf, count) != 0) {
            return -EFAULT;
        }
        return vos3_write(fd, stack_buf, count);
    }

    /* For larger writes, use heap bounce buffer with chunking */
    chunk_size = (count < SYS_WRITE_MAX_BOUNCE) ? count : SYS_WRITE_MAX_BOUNCE;
    kernel_buf = vos3_kmalloc(chunk_size);
    if (kernel_buf == NULL) {
        return -EFAULT;  /* No memory */
    }

    /* Copy from user and write in chunks */
    while (total_written < count) {
        size_t to_write = count - total_written;
        if (to_write > chunk_size) {
            to_write = chunk_size;
        }

        /* Copy chunk from user space */
        if (copy_from_user(kernel_buf,
                           (const uint8_t*)user_buf + total_written,
                           to_write) != 0) {
            vos3_kfree(kernel_buf);
            return -EFAULT;
        }

        bytes_written = vos3_write(fd, kernel_buf, to_write);
        if (bytes_written <= 0) {
            /* Error */
            if (total_written == 0) {
                vos3_kfree(kernel_buf);
                return bytes_written;
            }
            break;  /* Return what we wrote so far */
        }

        total_written += (size_t)bytes_written;

        /* Short write indicates issue */
        if ((size_t)bytes_written < to_write) {
            break;
        }
    }

    vos3_kfree(kernel_buf);
    return (int64_t)total_written;
}

/**
 * @brief sys_lseek - Seek in file
 */
static int64_t sys_lseek(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    int64_t offset = (int64_t)frame->rsi;
    int whence = (int)frame->rdx;

    return vos3_lseek(fd, offset, whence);
}

/**
 * @brief sys_stat - Get file status (Phase 29: Hardened)
 */
static int64_t sys_stat(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    void* user_st = (void*)frame->rsi;
    char kpath[VOS3_PATH_MAX];
    vos3_inode_t kst;
    linux_kstat_t lkst;
    int result;

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    if (!access_ok(user_st, sizeof(linux_kstat_t))) {
        return -EFAULT;
    }

    result = vos3_stat(kpath, &kst);
    if (result != 0) {
        return (int64_t)result;
    }

    /* Return Linux kstat layout for musl compatibility */
    inode_to_kstat(&kst, &lkst);

    if (copy_to_user(user_st, &lkst, sizeof(linux_kstat_t)) != 0) {
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_fstat - Get file status by fd (Phase 29: Hardened)
 */
static int64_t sys_fstat(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    void* user_st = (void*)frame->rsi;
    vos3_inode_t kst;
    linux_kstat_t lkst;
    int result;

    if (!access_ok(user_st, sizeof(linux_kstat_t))) {
        return -EFAULT;
    }

    result = vos3_fstat(fd, &kst);
    if (result != 0) {
        return (int64_t)result;
    }

    /* Return Linux kstat layout for musl compatibility */
    inode_to_kstat(&kst, &lkst);

    if (copy_to_user(user_st, &lkst, sizeof(linux_kstat_t)) != 0) {
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_truncate - Truncate file (Phase 29: Hardened)
 */
static int64_t sys_truncate(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    size_t length = (size_t)frame->rsi;
    char kpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_truncate(kpath, length);
}

/**
 * @brief sys_ftruncate - Truncate file by fd (Linux ABI 77)
 *
 * Args: RDI = fd, RSI = length
 */
static int64_t sys_ftruncate(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    size_t length = (size_t)frame->rsi;

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL || current->fd_table == NULL) {
        return -EBADF;
    }

    vos3_file_t* file = vos3_fd_get(current->fd_table, fd);
    if (file == NULL) {
        return -EBADF;
    }

    if (file->inode == NULL) {
        vos3_fd_put(file);
        return -EINVAL;
    }

    if (file->inode->ops == NULL || file->inode->ops->truncate == NULL) {
        vos3_fd_put(file);
        return -EINVAL;
    }

    int64_t result = (int64_t)file->inode->ops->truncate(file->inode, length);
    vos3_fd_put(file);
    return result;
}

/**
 * @brief sys_fsync - Sync file
 */
static int64_t sys_fsync(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    return (int64_t)vos3_fsync(fd);
}

/**
 * @brief sys_mkdir - Create directory (Phase 29: Hardened)
 */
static int64_t sys_mkdir(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    uint32_t mode = (uint32_t)frame->rsi;
    char kpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_mkdir(kpath, mode);
}

/**
 * @brief sys_rmdir - Remove directory (Phase 29: Hardened)
 */
static int64_t sys_rmdir(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    char kpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_rmdir(kpath);
}

/** @brief Maximum readdir entries per call for bounce buffer */
#define SYS_READDIR_MAX_ENTRIES 128U

/**
 * @brief sys_readdir - Read directory entries (Phase 29: Hardened)
 */
static int64_t sys_readdir(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    vos3_dirent_t* user_dirents = (vos3_dirent_t*)frame->rsi;
    size_t count = (size_t)frame->rdx;
    size_t* user_out_count = (size_t*)frame->r10;
    size_t kout_count = 0;
    int result;

    /* Phase 29: Validate user buffers */
    if (count > 0 && !access_ok(user_dirents, count * sizeof(vos3_dirent_t))) {
        return -EFAULT;
    }
    if (user_out_count != NULL && !access_ok(user_out_count, sizeof(size_t))) {
        return -EFAULT;
    }

    if (count == 0) {
        if (user_out_count != NULL) {
            if (copy_to_user(user_out_count, &kout_count, sizeof(size_t)) != 0) {
                return -EFAULT;
            }
        }
        return 0;
    }

    /* Clamp to max to limit bounce buffer size */
    if (count > SYS_READDIR_MAX_ENTRIES) {
        count = SYS_READDIR_MAX_ENTRIES;
    }

    /* Allocate kernel bounce buffer */
    size_t buf_size = count * sizeof(vos3_dirent_t);
    vos3_dirent_t* kbuf = (vos3_dirent_t*)vos3_kmalloc(buf_size);
    if (kbuf == NULL) {
        return -EFAULT;
    }

    /* Read into kernel buffer */
    result = vos3_readdir(fd, kbuf, count, &kout_count);

    /* Copy entries to user space on success */
    if (result >= 0 && kout_count > 0) {
        if (copy_to_user(user_dirents, kbuf, kout_count * sizeof(vos3_dirent_t)) != 0) {
            vos3_kfree(kbuf);
            return -EFAULT;
        }
    }

    vos3_kfree(kbuf);

    /* Copy out_count to user space */
    if (user_out_count != NULL) {
        if (copy_to_user(user_out_count, &kout_count, sizeof(size_t)) != 0) {
            return -EFAULT;
        }
    }

    /* Return entry count on success (Linux getdents convention) */
    if (result >= 0) {
        return (int64_t)kout_count;
    }
    return (int64_t)result;
}

/**
 * @brief sys_unlink - Unlink file (Phase 29: Hardened)
 */
static int64_t sys_unlink(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    char kpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_unlink(kpath);
}

/**
 * @brief sys_rename - Rename file (Phase 29: Hardened)
 */
static int64_t sys_rename(vos3_syscall_frame_t* frame)
{
    const char* user_oldpath = (const char*)frame->rdi;
    const char* user_newpath = (const char*)frame->rsi;
    char koldpath[VOS3_PATH_MAX];
    char knewpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user paths */
    if (copy_path_from_user(koldpath, user_oldpath) != 0) {
        return -EFAULT;
    }
    if (copy_path_from_user(knewpath, user_newpath) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_rename(koldpath, knewpath);
}

/**
 * @brief sys_getcwd - Get current directory (Phase 29: Hardened)
 */
static int64_t sys_getcwd(vos3_syscall_frame_t* frame)
{
    char* user_buf = (char*)frame->rdi;
    size_t size = (size_t)frame->rsi;
    char kbuf[VOS3_PATH_MAX];
    char* result;

    /* Phase 29: Validate user buffer */
    if (size == 0 || !access_ok(user_buf, size)) {
        return -EFAULT;
    }

    /* Limit size to our buffer */
    if (size > VOS3_PATH_MAX) {
        size = VOS3_PATH_MAX;
    }

    /* Get cwd into kernel buffer */
    result = vos3_getcwd(kbuf, size);
    if (result == NULL) {
        return -EFAULT;
    }

    /* Copy to user space */
    size_t len = strlen(kbuf) + 1;
    if (copy_to_user(user_buf, kbuf, len) != 0) {
        return -EFAULT;
    }

    return (int64_t)(uintptr_t)user_buf;
}

/**
 * @brief sys_chdir - Change directory (Phase 29: Hardened)
 */
static int64_t sys_chdir(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    char kpath[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_chdir(kpath);
}

/**
 * @brief sys_dup - Duplicate fd
 */
static int64_t sys_dup(vos3_syscall_frame_t* frame)
{
    int oldfd = (int)frame->rdi;

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    return (int64_t)vos3_dup(task->fd_table, oldfd);
}

/**
 * @brief sys_dup2 - Duplicate fd to specific number
 */
static int64_t sys_dup2(vos3_syscall_frame_t* frame)
{
    int oldfd = (int)frame->rdi;
    int newfd = (int)frame->rsi;

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    return (int64_t)vos3_dup2(task->fd_table, oldfd, newfd);
}

/**
 * @brief sys_pipe - Create a pipe (Phase 29: Hardened)
 */
static int64_t sys_pipe(vos3_syscall_frame_t* frame)
{
    int* user_pipefd = (int*)frame->rdi;
    int kpipefd[2];
    int result;

    /* Phase 29: Validate user buffer */
    if (!access_ok(user_pipefd, sizeof(int) * 2)) {
        return -EFAULT;
    }

    /* Create pipe with kernel buffer */
    result = vos3_pipe(kpipefd);
    if (result != 0) {
        return (int64_t)result;
    }

    /* Copy fds to user space */
    if (copy_to_user(user_pipefd, kpipefd, sizeof(int) * 2) != 0) {
        /* Cleanup: close the created pipes */
        vos3_close(kpipefd[0]);
        vos3_close(kpipefd[1]);
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_mount - Mount file system (Phase 29: Hardened)
 */
static int64_t sys_mount(vos3_syscall_frame_t* frame)
{
    const char* user_source = (const char*)frame->rdi;
    const char* user_target = (const char*)frame->rsi;
    const char* user_fstype = (const char*)frame->rdx;
    uint32_t flags = (uint32_t)frame->r10;
    void* data = (void*)frame->r8;
    char ksource[VOS3_PATH_MAX];
    char ktarget[VOS3_PATH_MAX];
    char kfstype[64];

    (void)data;  /* TODO: Handle mount data */

    /* Phase 29: Validate and copy user paths */
    if (copy_path_from_user(ksource, user_source) != 0) {
        return -EFAULT;
    }
    if (copy_path_from_user(ktarget, user_target) != 0) {
        return -EFAULT;
    }
    if (strncpy_from_user(kfstype, user_fstype, sizeof(kfstype)) < 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_mount(ksource, ktarget, kfstype, flags, NULL);
}

/**
 * @brief sys_umount - Unmount file system (Phase 29: Hardened)
 */
static int64_t sys_umount(vos3_syscall_frame_t* frame)
{
    const char* user_target = (const char*)frame->rdi;
    char ktarget[VOS3_PATH_MAX];

    /* Phase 29: Validate and copy user path */
    if (copy_path_from_user(ktarget, user_target) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_umount(ktarget);
}

/**
 * @brief sys_statfs - Get filesystem statistics (Phase 36)
 */
static int64_t sys_statfs(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    vos3_statfs_t* user_buf = (vos3_statfs_t*)frame->rsi;
    char kpath[VOS3_PATH_MAX];
    vos3_statfs_t kbuf;

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    if (!access_ok(user_buf, sizeof(vos3_statfs_t))) {
        return -EFAULT;
    }

    int result = vos3_statfs(kpath, &kbuf);
    if (result != 0) {
        return (int64_t)result;
    }

    if (copy_to_user(user_buf, &kbuf, sizeof(vos3_statfs_t)) != 0) {
        return -EFAULT;
    }

    return 0;
}

/* ============================================================================
 * POLL SYSCALL
 * ============================================================================ */

/** @brief Socket FD base — must match socket.c SOCKET_FD_BASE */
#define POLL_SOCKET_FD_BASE     100

/** @brief Maximum pollfd entries per call */
#define SYS_POLL_MAX_FDS        64U

/**
 * @brief Check readiness for a single fd
 *
 * Checks VFS files (including pipes) and sockets for POLLIN/POLLOUT events.
 * Socket check uses extern function to avoid coupling to socket.c internals.
 */

/* Extern: check socket readability (rx_count > 0) */
extern int vos3_socket_poll_check(int fd, int *readable, int *writable);

static short poll_check_fd(int fd, short events)
{
    short revents = 0;

    /* Socket FDs are >= 100 */
    if (fd >= POLL_SOCKET_FD_BASE) {
        int readable = 0, writable = 0;
        if (vos3_socket_poll_check(fd, &readable, &writable) == 0) {
            if ((events & POLLIN) && readable)
                revents |= POLLIN;
            if ((events & POLLOUT) && writable)
                revents |= POLLOUT;
        } else {
            revents |= POLLNVAL;
        }
        return revents;
    }

    /* VFS file descriptor */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return POLLNVAL;
    }

    vos3_file_t* file = vos3_fd_get(task->fd_table, fd);
    if (file == NULL) {
        return POLLNVAL;
    }

    /* Check if this is a pipe, eventfd, or epoll instance
     * (all have private_data set and inode == NULL).
     * Distinguish by the magic value at the start of private_data. */
    if (file->private_data != NULL && file->inode == NULL) {
        uint32_t type_magic = *(const uint32_t*)file->private_data;

        /* eventfd: readable if counter > 0 */
        if (type_magic == EVENTFD_MAGIC) {
            /* Overlay: magic(4), flags(4), count(8) */
            const vos3_eventfd_t* efd = (const vos3_eventfd_t*)file->private_data;
            if ((events & POLLIN) && efd->count > 0)
                revents |= POLLIN;
            if (events & POLLOUT)
                revents |= POLLOUT;  /* eventfd write never blocks in our impl */
            vos3_fd_put(file);
            return revents;
        }

        /* epoll fd: not directly pollable (return POLLNVAL) */
        if (type_magic == EPOLL_MAGIC) {
            vos3_fd_put(file);
            return POLLNVAL;
        }

        /* Pipe fd — private_data is vos3_pipe_t* */
        /* We can't include pipe.c's struct directly, but the first
         * field after the buffer (at offset VOS3_PIPE_BUFSIZE) is head/tail/count.
         * Instead, use a minimal struct overlay matching pipe.c layout. */
        struct pipe_peek {
            char   buffer[4096]; /* VOS3_PIPE_BUFSIZE */
            size_t head;
            size_t tail;
            size_t count;
        };
        struct pipe_peek* pp = (struct pipe_peek*)file->private_data;

        if (events & POLLIN) {
            /* Readable if data in buffer */
            if (pp->count > 0)
                revents |= POLLIN;
        }
        if (events & POLLOUT) {
            /* Writable if buffer not full */
            if (pp->count < 4096)
                revents |= POLLOUT;
        }
        vos3_fd_put(file);
        return revents;
    }

    /* Regular file */
    if (file->inode != NULL) {
        if (events & POLLIN) {
            /* Dynamic-content files (e.g. procfs) have size==0 but generate
             * content in their read op — always report readable; read()
             * returns 0 at EOF per POSIX */
            if (file->ops != NULL && file->ops->read != NULL &&
                file->inode->size == 0) {
                revents |= POLLIN;
            } else if ((size_t)file->pos < file->inode->size) {
                revents |= POLLIN;
            }
        }
        if (events & POLLOUT) {
            /* Regular files are always writable */
            revents |= POLLOUT;
        }
        vos3_fd_put(file);
        return revents;
    }

    /* Unknown fd type */
    vos3_fd_put(file);
    return POLLNVAL;
}

/**
 * @brief vos3_poll_check_fd — public wrapper for poll_check_fd
 *
 * Used by epoll.c and other subsystems that need to check FD readiness
 * without being coupled to the static-only poll_check_fd.
 */
short vos3_poll_check_fd(int fd, short events)
{
    return poll_check_fd(fd, events);
}

/**
 * @brief sys_poll - Wait for events on file descriptors
 *
 * Args: RDI = user pollfd array, RSI = nfds, RDX = timeout_ms
 * Returns: number of fds with events, 0 on timeout, negative on error
 */
static int64_t sys_poll(vos3_syscall_frame_t* frame)
{
    struct pollfd* user_fds = (struct pollfd*)frame->rdi;
    unsigned int nfds = (unsigned int)frame->rsi;
    int timeout_ms = (int)frame->rdx;

    /* Validate */
    if (nfds > SYS_POLL_MAX_FDS) {
        return -22; /* EINVAL */
    }
    if (nfds == 0) {
        if (timeout_ms > 0) {
            vos3_task_sleep_ms((uint64_t)timeout_ms);
        }
        return 0;
    }

    /* Validate user buffer */
    size_t buf_size = nfds * sizeof(struct pollfd);
    if (!access_ok(user_fds, buf_size)) {
        return -EFAULT;
    }

    /* Copy pollfd array from user space */
    struct pollfd kfds[SYS_POLL_MAX_FDS];
    if (copy_from_user(kfds, user_fds, buf_size) != 0) {
        return -EFAULT;
    }

    /* Poll loop */
    uint64_t deadline = 0;
    if (timeout_ms > 0) {
        deadline = vos3_sched_get_uptime_ms() + (uint64_t)timeout_ms;
    }

    for (;;) {
        int ready = 0;

        /* Check all fds */
        for (unsigned int i = 0; i < nfds; i++) {
            kfds[i].revents = 0;
            if (kfds[i].fd < 0) {
                continue; /* Skip negative fds (POSIX: ignore) */
            }
            kfds[i].revents = poll_check_fd(kfds[i].fd, kfds[i].events);
            if (kfds[i].revents != 0) {
                ready++;
            }
        }

        /* If any fd is ready, or timeout is immediate (0), return */
        if (ready > 0 || timeout_ms == 0) {
            /* Copy results back to user space */
            if (copy_to_user(user_fds, kfds, buf_size) != 0) {
                return -EFAULT;
            }
            return (int64_t)ready;
        }

        /* Infinite wait: timeout_ms == -1 */
        if (timeout_ms < 0) {
            vos3_sys_yield(NULL);
            continue;
        }

        /* Timed wait: check deadline */
        if (vos3_sched_get_uptime_ms() >= deadline) {
            /* Timeout — copy back (all revents = 0) and return 0 */
            if (copy_to_user(user_fds, kfds, buf_size) != 0) {
                return -EFAULT;
            }
            return 0;
        }

        /* Yield and retry */
        vos3_sys_yield(NULL);
    }
}

/* ============================================================================
 * FCNTL SYSCALL
 * ============================================================================ */

/**
 * @brief sys_fcntl - File control
 *
 * Args: RDI = fd, RSI = cmd, RDX = arg
 * Supports: F_GETFD, F_SETFD, F_GETFL, F_SETFL, F_DUPFD
 */
static int64_t sys_fcntl(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    int cmd = (int)frame->rsi;
    long arg = (long)frame->rdx;

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return -9; /* EBADF */
    }

    vos3_file_t* file = vos3_fd_get(task->fd_table, fd);
    if (file == NULL) {
        return -9; /* EBADF */
    }

    int64_t result;
    switch (cmd) {
        case F_GETFD:
            result = (int64_t)(task->fd_table->entries[fd].flags);
            break;

        case F_SETFD:
            task->fd_table->entries[fd].flags = (uint32_t)arg;
            result = 0;
            break;

        case F_GETFL:
            result = (int64_t)file->flags;
            break;

        case F_SETFL:
            /* Only allow changing O_NONBLOCK and O_APPEND */
            file->flags = (file->flags & ~(0x0C00U)) | ((uint32_t)arg & 0x0C00U);
            result = 0;
            break;

        case F_DUPFD:
            result = (int64_t)vos3_dup(task->fd_table, fd);
            break;

        default:
            result = -22; /* EINVAL */
            break;
    }

    vos3_fd_put(file);
    return result;
}

/* ============================================================================
 * TASK 1.2: NEW POSIX SYSCALL HANDLERS
 * ============================================================================ */

/** @brief Linux linux_dirent64 structure for getdents64 */
typedef struct {
    uint64_t d_ino;
    int64_t  d_off;
    uint16_t d_reclen;
    uint8_t  d_type;
    char     d_name[1];  /* variable length, starts here */
} __attribute__((packed)) linux_dirent64_t;

/** Map VOS3_FT_ type to Linux DT_ type */
static uint8_t vos3ft_to_dt(uint8_t ft)
{
    switch (ft) {
        case 1:  return 8;   /* VOS3_FT_REG  → DT_REG  */
        case 2:  return 4;   /* VOS3_FT_DIR  → DT_DIR  */
        case 3:  return 2;   /* VOS3_FT_CHR  → DT_CHR  */
        case 4:  return 6;   /* VOS3_FT_BLK  → DT_BLK  */
        case 5:  return 1;   /* VOS3_FT_FIFO → DT_FIFO */
        case 6:  return 12;  /* VOS3_FT_SOCK → DT_SOCK */
        case 7:  return 10;  /* VOS3_FT_LNK  → DT_LNK  */
        default: return 0;   /* DT_UNKNOWN */
    }
}

/**
 * @brief sys_lstat - stat without following the final symlink (Task 1.2)
 */
static int64_t sys_lstat(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    void* user_st = (void*)frame->rsi;
    vos3_inode_t kst;
    linux_kstat_t lkst;
    char kpath[VOS3_PATH_MAX];

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }
    if (!access_ok(user_st, sizeof(linux_kstat_t))) {
        return -EFAULT;
    }

    int result = vos3_lstat(kpath, &kst);
    if (result != 0) {
        return (int64_t)result;
    }

    inode_to_kstat(&kst, &lkst);

    if (copy_to_user(user_st, &lkst, sizeof(linux_kstat_t)) != 0) {
        return -EFAULT;
    }
    return 0;
}

/**
 * @brief sys_pread64 - Read from fd at given offset without changing position (Task 1.2)
 */
static int64_t sys_pread64(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    void* user_buf = (void*)frame->rsi;
    size_t count = (size_t)frame->rdx;
    int64_t offset = (int64_t)frame->r10;

    if (count == 0) {
        return 0;
    }
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Save position */
    int64_t saved = vos3_lseek(fd, 0, VOS3_SEEK_CUR);
    if (saved < 0) {
        return saved;
    }

    /* Seek to requested offset */
    if (vos3_lseek(fd, offset, VOS3_SEEK_SET) < 0) {
        return -(int64_t)EINVAL;
    }

    /* Read via bounce buffer */
    size_t cap = (count < SYS_READ_MAX_BOUNCE) ? count : SYS_READ_MAX_BOUNCE;
    uint8_t* kbuf = (uint8_t*)vos3_kmalloc(cap);
    if (kbuf == NULL) {
        vos3_lseek(fd, saved, VOS3_SEEK_SET);
        return -EFAULT;
    }

    int64_t n = vos3_read(fd, kbuf, cap);
    if (n > 0) {
        if (copy_to_user(user_buf, kbuf, (size_t)n) != 0) {
            n = -EFAULT;
        }
    }
    vos3_kfree(kbuf);

    /* Restore original position */
    vos3_lseek(fd, saved, VOS3_SEEK_SET);

    return n;
}

/**
 * @brief sys_pwrite64 - Write to fd at given offset without changing position (Task 1.2)
 */
static int64_t sys_pwrite64(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    const void* user_buf = (const void*)frame->rsi;
    size_t count = (size_t)frame->rdx;
    int64_t offset = (int64_t)frame->r10;

    if (count == 0) {
        return 0;
    }
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Save position */
    int64_t saved = vos3_lseek(fd, 0, VOS3_SEEK_CUR);
    if (saved < 0) {
        return saved;
    }

    /* Seek to requested offset */
    if (vos3_lseek(fd, offset, VOS3_SEEK_SET) < 0) {
        return -(int64_t)EINVAL;
    }

    /* Write via bounce buffer */
    size_t cap = (count < SYS_WRITE_MAX_BOUNCE) ? count : SYS_WRITE_MAX_BOUNCE;
    uint8_t* kbuf = (uint8_t*)vos3_kmalloc(cap);
    if (kbuf == NULL) {
        vos3_lseek(fd, saved, VOS3_SEEK_SET);
        return -EFAULT;
    }

    int64_t n = -EFAULT;
    if (copy_from_user(kbuf, user_buf, cap) == 0) {
        n = vos3_write(fd, kbuf, cap);
    }
    vos3_kfree(kbuf);

    /* Restore original position */
    vos3_lseek(fd, saved, VOS3_SEEK_SET);

    return n;
}

/**
 * @brief sys_readlink - Read the target of a symbolic link (Task 1.2)
 */
static int64_t sys_readlink(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    char* user_buf = (char*)frame->rsi;
    size_t bufsiz = (size_t)frame->rdx;
    char kpath[VOS3_PATH_MAX];
    char kbuf[VOS3_PATH_MAX];

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }
    if (bufsiz == 0 || !access_ok(user_buf, bufsiz)) {
        return -EFAULT;
    }

    /* Fast path for /proc/self/exe — return current task's exe_path directly */
    if (strcmp(kpath, "/proc/self/exe") == 0) {
        vos3_task_t* cur = vos3_sched_current();
        if (cur != NULL && cur->exe_path[0] != '\0') {
            size_t plen = strlen(cur->exe_path);
            if (plen > bufsiz) plen = bufsiz;
            if (copy_to_user(user_buf, cur->exe_path, plen) != 0)
                return -EFAULT;
            return (int64_t)plen;
        }
        return -2; /* ENOENT */
    }

    size_t kbufsiz = (bufsiz < VOS3_PATH_MAX) ? bufsiz : (size_t)VOS3_PATH_MAX;
    int64_t len = (int64_t)vos3_readlink(kpath, kbuf, kbufsiz);
    if (len < 0) {
        return len;
    }

    size_t copy_len = ((size_t)len < bufsiz) ? (size_t)len : bufsiz;
    if (copy_to_user(user_buf, kbuf, copy_len) != 0) {
        return -EFAULT;
    }
    return (int64_t)copy_len;
}

/**
 * @brief sys_symlink - Create a symbolic link (Task 1.2)
 * Linux: rdi=target, rsi=linkpath
 */
static int64_t sys_symlink(vos3_syscall_frame_t* frame)
{
    const char* user_target = (const char*)frame->rdi;
    const char* user_linkpath = (const char*)frame->rsi;
    char ktarget[VOS3_PATH_MAX];
    char klinkpath[VOS3_PATH_MAX];

    if (copy_path_from_user(ktarget, user_target) != 0) {
        return -EFAULT;
    }
    if (copy_path_from_user(klinkpath, user_linkpath) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_symlink(ktarget, klinkpath);
}

/**
 * @brief sys_link - Create a hard link (Task 1.2)
 */
static int64_t sys_link(vos3_syscall_frame_t* frame)
{
    const char* user_oldpath = (const char*)frame->rdi;
    const char* user_newpath = (const char*)frame->rsi;
    char koldpath[VOS3_PATH_MAX];
    char knewpath[VOS3_PATH_MAX];

    if (copy_path_from_user(koldpath, user_oldpath) != 0) {
        return -EFAULT;
    }
    if (copy_path_from_user(knewpath, user_newpath) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_link(koldpath, knewpath);
}

/**
 * @brief sys_chmod - Change file permissions (Task 1.2)
 */
static int64_t sys_chmod(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    uint32_t mode = (uint32_t)frame->rsi;
    char kpath[VOS3_PATH_MAX];

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_chmod(kpath, mode);
}

/**
 * @brief sys_fchmod - Change file permissions by fd (Task 1.2)
 */
static int64_t sys_fchmod(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    uint32_t mode = (uint32_t)frame->rsi;

    return (int64_t)vos3_fchmod(fd, mode);
}

/**
 * @brief sys_chown - Change file owner (Task 1.2)
 */
static int64_t sys_chown(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    uint32_t uid = (uint32_t)frame->rsi;
    uint32_t gid = (uint32_t)frame->rdx;
    char kpath[VOS3_PATH_MAX];

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_chown(kpath, uid, gid);
}

/**
 * @brief sys_fchown - Change file owner by fd (Task 1.2)
 */
static int64_t sys_fchown(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    uint32_t uid = (uint32_t)frame->rsi;
    uint32_t gid = (uint32_t)frame->rdx;

    return (int64_t)vos3_fchown(fd, uid, gid);
}

/** @brief Max VOS3 dirents to read per getdents64 call */
#define GETDENTS64_MAX_ENTRIES  64U
/** @brief Max bytes per linux_dirent64 entry (19 fixed + 64 name + 1 null, aligned) */
#define GETDENTS64_ENTRY_MAX    88U

/**
 * @brief sys_getdents64 - Read directory entries in Linux dirent64 format (Task 1.2)
 * Returns number of bytes written to user buffer (Linux convention).
 */
static int64_t sys_getdents64(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    void* user_buf = (void*)frame->rsi;
    size_t count = (size_t)frame->rdx;
    size_t kout_count = 0;

    if (count == 0) {
        return 0;
    }
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Read VOS3 dirents into kernel buffer */
    size_t max_ents = GETDENTS64_MAX_ENTRIES;
    if (max_ents * GETDENTS64_ENTRY_MAX > count) {
        max_ents = count / GETDENTS64_ENTRY_MAX;
    }
    if (max_ents == 0) {
        max_ents = 1;
    }

    vos3_dirent_t* kents = (vos3_dirent_t*)vos3_kmalloc(max_ents * sizeof(vos3_dirent_t));
    if (kents == NULL) {
        return -EFAULT;
    }

    int result = vos3_readdir(fd, kents, max_ents, &kout_count);
    if (result < 0 || kout_count == 0) {
        vos3_kfree(kents);
        return (result < 0) ? (int64_t)result : 0;
    }

    /* Build output buffer with linux_dirent64 entries */
    uint8_t* outbuf = (uint8_t*)vos3_kmalloc(kout_count * GETDENTS64_ENTRY_MAX);
    if (outbuf == NULL) {
        vos3_kfree(kents);
        return -EFAULT;
    }

    size_t out_pos = 0;
    for (size_t i = 0; i < kout_count; i++) {
        vos3_dirent_t* ent = &kents[i];
        size_t namelen = strlen(ent->name);
        /* Fixed header: 8(ino)+8(off)+2(reclen)+1(type) = 19 bytes */
        size_t reclen = 19 + namelen + 1;  /* +1 for null terminator */
        /* Align to 8 bytes */
        reclen = (reclen + 7U) & ~(size_t)7U;

        if (out_pos + reclen > count) {
            break;
        }

        uint8_t* p = outbuf + out_pos;
        /* d_ino: 8 bytes */
        *((uint64_t*)p) = (uint64_t)ent->ino;
        p += 8;
        /* d_off: 8 bytes (position of next entry) */
        *((int64_t*)p) = (int64_t)(out_pos + reclen);
        p += 8;
        /* d_reclen: 2 bytes */
        *((uint16_t*)p) = (uint16_t)reclen;
        p += 2;
        /* d_type: 1 byte */
        *p = vos3ft_to_dt(ent->type);
        p += 1;
        /* d_name: namelen+1 bytes (null terminated) */
        memcpy(p, ent->name, namelen + 1);
        /* Zero the alignment padding */
        size_t used = 19 + namelen + 1;
        if (reclen > used) {
            memset(outbuf + out_pos + used, 0, reclen - used);
        }

        out_pos += reclen;
    }

    vos3_kfree(kents);

    int64_t ret = -EFAULT;
    if (out_pos > 0) {
        if (copy_to_user(user_buf, outbuf, out_pos) == 0) {
            ret = (int64_t)out_pos;
        }
    } else {
        ret = 0;
    }
    vos3_kfree(outbuf);
    return ret;
}

/**
 * @brief sys_dup3 - Duplicate fd to specific fd with optional O_CLOEXEC (Task 1.2)
 */
static int64_t sys_dup3(vos3_syscall_frame_t* frame)
{
    int oldfd = (int)frame->rdi;
    int newfd = (int)frame->rsi;
    int flags = (int)frame->rdx;

    if (oldfd == newfd) {
        return -(int64_t)EINVAL;  /* Linux: dup3 requires oldfd != newfd */
    }

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return -(int64_t)EBADF;
    }

    int result = vos3_dup2(task->fd_table, oldfd, newfd);
    if (result < 0) {
        return (int64_t)result;
    }

    /* Apply O_CLOEXEC if requested */
    if (flags & O_CLOEXEC) {
        task->fd_table->entries[newfd].flags |= (uint32_t)FD_CLOEXEC;
    }

    return (int64_t)result;
}

/**
 * @brief sys_pipe2 - Create pipe with optional O_CLOEXEC / O_NONBLOCK (Task 1.2)
 */
static int64_t sys_pipe2(vos3_syscall_frame_t* frame)
{
    int* user_pipefd = (int*)frame->rdi;
    int flags = (int)frame->rsi;
    int kpipefd[2];

    if (!access_ok(user_pipefd, sizeof(int) * 2)) {
        return -EFAULT;
    }

    int result = vos3_pipe(kpipefd);
    if (result != 0) {
        return (int64_t)result;
    }

    /* Apply O_CLOEXEC to both ends */
    if (flags & O_CLOEXEC) {
        vos3_task_t* task = vos3_sched_current();
        if (task != NULL && task->fd_table != NULL) {
            task->fd_table->entries[kpipefd[0]].flags |= (uint32_t)FD_CLOEXEC;
            task->fd_table->entries[kpipefd[1]].flags |= (uint32_t)FD_CLOEXEC;
        }
    }

    if (copy_to_user(user_pipefd, kpipefd, sizeof(int) * 2) != 0) {
        vos3_close(kpipefd[0]);
        vos3_close(kpipefd[1]);
        return -EFAULT;
    }

    return 0;
}

/* ============================================================================
 * WRITEV / ACCESS (musl libc support)
 * ============================================================================ */

/**
 * @brief iovec structure for writev
 */
struct kernel_iovec {
    void  *iov_base;
    size_t iov_len;
};

/**
 * @brief sys_readv - Vectored read (musl fread uses this for buffered I/O)
 */
static int64_t sys_readv(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    struct kernel_iovec *user_iov = (struct kernel_iovec *)frame->rsi;
    int iovcnt = (int)frame->rdx;

    if (iovcnt <= 0 || iovcnt > 1024) {
        return -EINVAL;
    }

    /* Copy iovec array from user space */
    struct kernel_iovec iov[1024];
    size_t iov_size = (size_t)iovcnt * sizeof(struct kernel_iovec);
    if (!access_ok(user_iov, iov_size)) {
        return -EFAULT;
    }
    if (copy_from_user(iov, user_iov, iov_size) != 0) {
        return -EFAULT;
    }

    int64_t total = 0;
    for (int i = 0; i < iovcnt; i++) {
        if (iov[i].iov_len == 0) continue;
        if (!access_ok(iov[i].iov_base, iov[i].iov_len)) {
            return total > 0 ? total : -EFAULT;
        }

        size_t len = iov[i].iov_len;
        if (len > 65536U) len = 65536U;

        void *kbuf = vos3_kmalloc(len);
        if (kbuf == NULL) {
            return total > 0 ? total : -12; /* ENOMEM */
        }

        int64_t n = vos3_read(fd, kbuf, len);
        if (n < 0) {
            vos3_kfree(kbuf);
            return total > 0 ? total : n;
        }
        if (n > 0) {
            if (copy_to_user(iov[i].iov_base, kbuf, (size_t)n) != 0) {
                vos3_kfree(kbuf);
                return total > 0 ? total : -EFAULT;
            }
        }
        vos3_kfree(kbuf);

        total += n;
        if ((size_t)n < len) break;  /* short read */
    }
    return total;
}

/**
 * @brief sys_writev - Vectored write (musl printf uses this exclusively)
 */
static int64_t sys_writev(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    struct kernel_iovec *user_iov = (struct kernel_iovec *)frame->rsi;
    int iovcnt = (int)frame->rdx;

    if (iovcnt <= 0 || iovcnt > 1024) {
        return -EINVAL;
    }

    /* Copy iovec array from user space */
    struct kernel_iovec iov[1024];
    size_t iov_size = (size_t)iovcnt * sizeof(struct kernel_iovec);
    if (!access_ok(user_iov, iov_size)) {
        return -EFAULT;
    }
    if (copy_from_user(iov, user_iov, iov_size) != 0) {
        return -EFAULT;
    }

    int64_t total = 0;
    for (int i = 0; i < iovcnt; i++) {
        if (iov[i].iov_len == 0) continue;
        if (!access_ok(iov[i].iov_base, iov[i].iov_len)) {
            return total > 0 ? total : -EFAULT;
        }

        /* Use a bounce buffer to write each iov entry */
        size_t len = iov[i].iov_len;
        if (len > 65536U) len = 65536U;

        void *kbuf = vos3_kmalloc(len);
        if (kbuf == NULL) {
            return total > 0 ? total : -EFAULT;
        }

        if (copy_from_user(kbuf, iov[i].iov_base, len) != 0) {
            vos3_kfree(kbuf);
            return total > 0 ? total : -EFAULT;
        }

        int64_t n = vos3_write(fd, kbuf, len);
        vos3_kfree(kbuf);

        if (n < 0) {
            return total > 0 ? total : n;
        }
        total += n;
        if ((size_t)n < len) break;  /* short write */
    }
    return total;
}

/**
 * @brief sys_access - Check file accessibility
 *
 * Minimal implementation: try open, if success → close and return 0.
 */
static int64_t sys_access(vos3_syscall_frame_t* frame)
{
    const char* user_path = (const char*)frame->rdi;
    /* mode in rsi (F_OK=0, R_OK=4, W_OK=2, X_OK=1) - we just check existence */
    char kpath[VOS3_PATH_MAX];

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    int fd = vos3_open(kpath, 0 /* O_RDONLY */, 0);
    if (fd < 0) {
        return (int64_t)fd;  /* -ENOENT, -EACCES, etc. */
    }
    vos3_close(fd);
    return 0;
}

/**
 * @brief sys_ioctl_linux - ioctl at Linux slot 16
 *
 * musl calls ioctl(fd, TIOCGWINSZ) on stdout's first write.
 * Translates Linux ioctl numbers to VOS3 internal numbers and dispatches
 * to the device's ioctl handler via the VFS file ops.
 */
static int64_t sys_ioctl_linux(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    uint32_t cmd = (uint32_t)frame->rsi;
    uint64_t arg = frame->rdx;

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) return -9; /* EBADF */

    vos3_file_t* file = vos3_fd_get(task->fd_table, fd);
    if (file == NULL) return -9; /* EBADF */

    int64_t result;

    /* Linux ioctl number translation */
    switch (cmd) {
        case 0x5413U: { /* TIOCGWINSZ */
            /* Return a fixed 80x25 window size */
            struct { uint16_t ws_row; uint16_t ws_col;
                     uint16_t ws_xpixel; uint16_t ws_ypixel; } ws;
            ws.ws_row = 25; ws.ws_col = 80;
            ws.ws_xpixel = 0; ws.ws_ypixel = 0;
            result = (arg != 0 && vos3_copy_to_user((void*)arg, &ws, sizeof(ws)) == 0)
                     ? 0 : -14; /* EFAULT */
            vos3_fd_put(file);
            return result;
        }
        case 0x5401U: { /* TCGETS — musl's isatty() checks this */
            /* Return a minimal termios struct (all zeros = raw mode) */
            uint8_t termios[60];
            memset(termios, 0, sizeof(termios));
            result = (arg != 0 && vos3_copy_to_user((void*)arg, termios, sizeof(termios)) == 0)
                     ? 0 : -14; /* EFAULT */
            vos3_fd_put(file);
            return result;
        }
        default:
            break;
    }

    /* Try dispatching through VFS file ops ioctl */
    if (file->ops != NULL && file->ops->ioctl != NULL) {
        result = (int64_t)file->ops->ioctl(file, cmd, (void*)arg);
        vos3_fd_put(file);
        return result;
    }

    vos3_fd_put(file);
    return -25;  /* ENOTTY */
}

/* ============================================================================
 * TASK 2.7: OPENAT / NEWFSTATAT / GETRANDOM / STATX
 * ============================================================================ */

/**
 * @brief sys_openat - Open file relative to directory fd
 *
 * Args: rdi=dirfd, rsi=path, rdx=flags, r10=mode
 * Only AT_FDCWD (-100) supported for dirfd.
 */
static int64_t sys_openat(vos3_syscall_frame_t* frame)
{
    int dirfd = (int)frame->rdi;
    const char* user_path = (const char*)frame->rsi;
    uint32_t flags = (uint32_t)frame->rdx;
    uint32_t mode = (uint32_t)frame->r10;
    char kpath[VOS3_PATH_MAX];

    /* Only AT_FDCWD supported */
    if (dirfd != AT_FDCWD) {
        return -EBADF;
    }

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    /* Strip O_LARGEFILE — musl always sets it, VOS3 ignores it */
    flags &= ~((uint32_t)O_LARGEFILE);

    return (int64_t)vos3_open(kpath, flags, mode);
}

/**
 * @brief sys_newfstatat - Get file status relative to directory fd
 *
 * Args: rdi=dirfd, rsi=path, rdx=statbuf, r10=flags
 * Only AT_FDCWD (-100) supported.
 * Translates VOS3 inode → Linux kstat for musl compatibility.
 */
static int64_t sys_newfstatat(vos3_syscall_frame_t* frame)
{
    int dirfd = (int)frame->rdi;
    const char* user_path = (const char*)frame->rsi;
    linux_kstat_t* user_buf = (linux_kstat_t*)frame->rdx;
    int at_flags = (int)frame->r10;
    char kpath[VOS3_PATH_MAX];
    vos3_inode_t kst;
    linux_kstat_t lkst;
    int result;

    if (dirfd != AT_FDCWD) {
        return -EBADF;
    }

    if (copy_path_from_user(kpath, user_path) != 0) {
        return -EFAULT;
    }

    if (!access_ok(user_buf, sizeof(linux_kstat_t))) {
        return -EFAULT;
    }

    /* Strip AT_NO_AUTOMOUNT — musl always sets it */
    at_flags &= ~AT_NO_AUTOMOUNT;

    /* AT_SYMLINK_NOFOLLOW → lstat, else stat */
    if (at_flags & AT_SYMLINK_NOFOLLOW) {
        result = vos3_lstat(kpath, &kst);
    } else {
        result = vos3_stat(kpath, &kst);
    }

    if (result != 0) {
        return (int64_t)result;
    }

    /* Translate to Linux kstat layout */
    inode_to_kstat(&kst, &lkst);

    if (copy_to_user(user_buf, &lkst, sizeof(linux_kstat_t)) != 0) {
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_statx - stub returning ENOSYS
 *
 * musl tries statx(332) first, falls back to fstatat(262) on ENOSYS.
 */
static int64_t sys_statx_stub(vos3_syscall_frame_t* frame)
{
    (void)frame;
    return -38;  /* ENOSYS */
}

/**
 * @brief sys_getrandom - Fill buffer with random bytes
 *
 * Args: rdi=buf, rsi=count, rdx=flags
 * Uses ChaCha20 CSPRNG via entropy subsystem.
 */
static int64_t sys_getrandom(vos3_syscall_frame_t* frame)
{
    void* user_buf = (void*)frame->rdi;
    size_t count   = (size_t)frame->rsi;

    if (user_buf == NULL || !access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Generate in 256-byte chunks, copy to user */
    size_t done = 0;
    while (done < count) {
        uint8_t tmp[256];
        size_t chunk = count - done;
        if (chunk > sizeof(tmp)) chunk = sizeof(tmp);

        if (vos3_entropy_extract(tmp, chunk) != 0) {
            return -5;  /* EIO */
        }

        if (copy_to_user((uint8_t*)user_buf + done, tmp, chunk) != 0) {
            return -EFAULT;
        }
        done += chunk;
    }

    return (int64_t)done;
}

/* ============================================================================
 * PHASE C: UTIMENSAT (Linux ABI 280)
 * ============================================================================ */

/* Timer function for uptime */
extern uint64_t vos3_timer_get_uptime_ms(void);

/**
 * @brief sys_utimensat - Set file timestamps
 *
 * Args: rdi=dirfd, rsi=path, rdx=times (struct timespec[2]), r10=flags
 * If times==NULL: set atime+mtime to current time
 * tv_nsec==UTIME_NOW(0x3FFFFFFF): use current time
 * tv_nsec==UTIME_OMIT(0x3FFFFFFE): leave unchanged
 */
static int64_t sys_utimensat(vos3_syscall_frame_t* frame)
{
    int dirfd = (int)frame->rdi;
    const char* user_path = (const char*)frame->rsi;
    const void* user_times = (const void*)frame->rdx;
    /* r10 = flags (AT_SYMLINK_NOFOLLOW, etc.) — ignored for now */

    /* Only AT_FDCWD supported for dirfd */
    if (dirfd != AT_FDCWD && dirfd != -100) {
        return -EBADF;
    }

    char kpath[VOS3_PATH_MAX];
    if (copy_path_from_user(kpath, user_path) != 0) {
        return -14; /* EFAULT */
    }

    /* Resolve path to inode */
    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(kpath, &dentry);
    if (result != VOS3_FS_OK) {
        return (int64_t)result;
    }
    if (dentry->inode == NULL) {
        return -ENOENT;
    }

    /* Get current uptime in seconds */
    uint64_t now_sec = vos3_timer_get_uptime_ms() / 1000ULL;

    if (user_times == NULL) {
        /* NULL times = set both to current time */
        dentry->inode->atime = now_sec;
        dentry->inode->mtime = now_sec;
    } else {
        /* Copy timespec[2] from user space */
        /* struct timespec { int64_t tv_sec; int64_t tv_nsec; } = 16 bytes each, 32 total */
        struct { int64_t tv_sec; int64_t tv_nsec; } ktimes[2];
        if (!access_ok(user_times, 32)) {
            return -14; /* EFAULT */
        }
        if (copy_from_user(&ktimes, user_times, 32) != 0) {
            return -14; /* EFAULT */
        }

        /* atime (ktimes[0]) */
        if (ktimes[0].tv_nsec == UTIME_NOW) {
            dentry->inode->atime = now_sec;
        } else if (ktimes[0].tv_nsec != UTIME_OMIT) {
            dentry->inode->atime = (uint64_t)ktimes[0].tv_sec;
        }

        /* mtime (ktimes[1]) */
        if (ktimes[1].tv_nsec == UTIME_NOW) {
            dentry->inode->mtime = now_sec;
        } else if (ktimes[1].tv_nsec != UTIME_OMIT) {
            dentry->inode->mtime = (uint64_t)ktimes[1].tv_sec;
        }
    }

    return 0;
}

/* ============================================================================
 * REGISTRATION
 * ============================================================================ */

void vos3_fs_syscalls_init(void)
{
    VOS3_INFO("Registering file system syscalls");

    /* File operations */
    vos3_syscall_register(SYS_OPEN, sys_open);
    vos3_syscall_register(SYS_CLOSE, sys_close);
    vos3_syscall_register(SYS_READ, sys_read);
    vos3_syscall_register(SYS_WRITE, sys_write);
    vos3_syscall_register(SYS_LSEEK, sys_lseek);
    vos3_syscall_register(SYS_STAT, sys_stat);
    vos3_syscall_register(SYS_FSTAT, sys_fstat);
    vos3_syscall_register(SYS_TRUNCATE, sys_truncate);
    vos3_syscall_register(SYS_FTRUNCATE, sys_ftruncate);
    vos3_syscall_register(SYS_FSYNC, sys_fsync);

    /* Directory operations */
    vos3_syscall_register(SYS_MKDIR, sys_mkdir);
    vos3_syscall_register(SYS_RMDIR, sys_rmdir);
    vos3_syscall_register(SYS_GETDENTS, sys_readdir);
    vos3_syscall_register(SYS_UNLINK, sys_unlink);
    vos3_syscall_register(SYS_RENAME, sys_rename);
    vos3_syscall_register(SYS_GETCWD, sys_getcwd);
    vos3_syscall_register(SYS_CHDIR, sys_chdir);

    /* File descriptor operations */
    vos3_syscall_register(SYS_DUP, sys_dup);
    vos3_syscall_register(SYS_DUP2, sys_dup2);
    vos3_syscall_register(SYS_PIPE, sys_pipe);

    /* Mount operations */
    vos3_syscall_register(SYS_MOUNT, sys_mount);
    vos3_syscall_register(SYS_UMOUNT, sys_umount);

    /* Filesystem statistics (Phase 36) */
    vos3_syscall_register(SYS_STATFS, sys_statfs);

    /* poll() and fcntl() (Phase B) */
    vos3_syscall_register(SYS_POLL, sys_poll);
    vos3_syscall_register(SYS_FCNTL, sys_fcntl);

    /* Task 1.2: New POSIX file syscalls */
    vos3_syscall_register(SYS_LSTAT,      sys_lstat);
    vos3_syscall_register(SYS_PREAD64,    sys_pread64);
    vos3_syscall_register(SYS_PWRITE64,   sys_pwrite64);
    vos3_syscall_register(SYS_READLINK,   sys_readlink);
    vos3_syscall_register(SYS_SYMLINK,    sys_symlink);
    vos3_syscall_register(SYS_LINK,       sys_link);
    vos3_syscall_register(SYS_CHMOD,      sys_chmod);
    vos3_syscall_register(SYS_FCHMOD,     sys_fchmod);
    vos3_syscall_register(SYS_CHOWN,      sys_chown);
    vos3_syscall_register(SYS_FCHOWN,     sys_fchown);
    vos3_syscall_register(SYS_GETDENTS64, sys_getdents64);
    vos3_syscall_register(SYS_DUP3,       sys_dup3);
    vos3_syscall_register(SYS_PIPE2,      sys_pipe2);

    /* Vectored I/O and access */
    vos3_syscall_register(SYS_IOCTL_LINUX, sys_ioctl_linux);
    vos3_syscall_register(SYS_READV, sys_readv);
    vos3_syscall_register(SYS_WRITEV, sys_writev);
    vos3_syscall_register(SYS_ACCESS, sys_access);

    /* Task 2.7: openat, newfstatat, getrandom, statx */
    vos3_syscall_register(SYS_OPENAT,     sys_openat);
    vos3_syscall_register(SYS_NEWFSTATAT, sys_newfstatat);
    vos3_syscall_register(SYS_GETRANDOM,  sys_getrandom);
    vos3_syscall_register(SYS_STATX,      sys_statx_stub);

    /* Phase C: utimensat */
    vos3_syscall_register(SYS_UTIMENSAT,  sys_utimensat);

    /* Initialize pipe subsystem */
    vos3_pipe_init();

    /* Task 1.3: epoll, select, eventfd */
    vos3_epoll_init();
}
