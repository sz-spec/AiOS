/**
 * @file vfs.h
 * @brief VOS3 Virtual File System Interface
 *
 * @details Abstraction layer for file system operations.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_VFS_H
#define VOS3_VFS_H

#include <stdint.h>
#include <stddef.h>
#include "sync.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum path length */
#define VOS3_PATH_MAX           256U

/** @brief Maximum filename length */
#define VOS3_NAME_MAX           64U

/** @brief Maximum open files per task */
#define VOS3_MAX_FD             1024U

/** @brief Maximum mounted file systems */
#define VOS3_MAX_MOUNTS         16U

/** @brief Maximum inodes */
#define VOS3_MAX_INODES         1024U

/** @brief Maximum directory entries per directory */
#define VOS3_MAX_DIRENTS        128U

/** @brief File descriptor invalid value */
#define VOS3_FD_INVALID         (-1)

/** @brief Root inode number */
#define VOS3_ROOT_INODE         1U

/* ============================================================================
 * FILE TYPES
 * ============================================================================ */

/** @brief File type enumeration */
typedef enum vos3_file_type {
    VOS3_FT_NONE      = 0,    /**< No type */
    VOS3_FT_REG       = 1,    /**< Regular file */
    VOS3_FT_DIR       = 2,    /**< Directory */
    VOS3_FT_CHR       = 3,    /**< Character device */
    VOS3_FT_BLK       = 4,    /**< Block device */
    VOS3_FT_FIFO      = 5,    /**< FIFO/pipe */
    VOS3_FT_SOCK      = 6,    /**< Socket */
    VOS3_FT_LNK       = 7,    /**< Symbolic link */
} vos3_file_type_t;

/* ============================================================================
 * FILE MODE FLAGS
 * ============================================================================ */

/** @brief File mode bits */
#define VOS3_S_IFMT     0170000U  /**< File type mask */
#define VOS3_S_IFREG    0100000U  /**< Regular file */
#define VOS3_S_IFDIR    0040000U  /**< Directory */
#define VOS3_S_IFCHR    0020000U  /**< Character device */
#define VOS3_S_IFBLK    0060000U  /**< Block device */
#define VOS3_S_IFIFO    0010000U  /**< FIFO */
#define VOS3_S_IFLNK    0120000U  /**< Symbolic link */
#define VOS3_S_IFSOCK   0140000U  /**< Socket */

/** @brief File type test macros */
#define VOS3_S_ISREG(m)  (((m) & VOS3_S_IFMT) == VOS3_S_IFREG)
#define VOS3_S_ISDIR(m)  (((m) & VOS3_S_IFMT) == VOS3_S_IFDIR)
#define VOS3_S_ISCHR(m)  (((m) & VOS3_S_IFMT) == VOS3_S_IFCHR)
#define VOS3_S_ISBLK(m)  (((m) & VOS3_S_IFMT) == VOS3_S_IFBLK)
#define VOS3_S_ISFIFO(m) (((m) & VOS3_S_IFMT) == VOS3_S_IFIFO)
#define VOS3_S_ISLNK(m)  (((m) & VOS3_S_IFMT) == VOS3_S_IFLNK)
#define VOS3_S_ISSOCK(m) (((m) & VOS3_S_IFMT) == VOS3_S_IFSOCK)

/** @brief Permission bits */
#define VOS3_S_IRWXU    0700U     /**< Owner: rwx */
#define VOS3_S_IRUSR    0400U     /**< Owner: read */
#define VOS3_S_IWUSR    0200U     /**< Owner: write */
#define VOS3_S_IXUSR    0100U     /**< Owner: execute */
#define VOS3_S_IRWXG    0070U     /**< Group: rwx */
#define VOS3_S_IRGRP    0040U     /**< Group: read */
#define VOS3_S_IWGRP    0020U     /**< Group: write */
#define VOS3_S_IXGRP    0010U     /**< Group: execute */
#define VOS3_S_IRWXO    0007U     /**< Others: rwx */
#define VOS3_S_IROTH    0004U     /**< Others: read */
#define VOS3_S_IWOTH    0002U     /**< Others: write */
#define VOS3_S_IXOTH    0001U     /**< Others: execute */

/* ============================================================================
 * OPEN FLAGS
 * ============================================================================ */

#define VOS3_O_RDONLY   0x0000U   /**< Read only */
#define VOS3_O_WRONLY   0x0001U   /**< Write only */
#define VOS3_O_RDWR     0x0002U   /**< Read/write */
#define VOS3_O_ACCMODE  0x0003U   /**< Access mode mask */

#define VOS3_O_CREAT    0x0040U   /**< Create if not exists */
#define VOS3_O_EXCL     0x0080U   /**< Exclusive create */
#define VOS3_O_TRUNC    0x0200U   /**< Truncate to zero */
#define VOS3_O_APPEND   0x0400U   /**< Append mode */
#define VOS3_O_NONBLOCK 0x0800U   /**< Non-blocking */
#define VOS3_O_DIRECTORY 0x10000U /**< Must be directory */

/* ============================================================================
 * SEEK WHENCE
 * ============================================================================ */

#define VOS3_SEEK_SET   0         /**< Seek from start */
#define VOS3_SEEK_CUR   1         /**< Seek from current */
#define VOS3_SEEK_END   2         /**< Seek from end */

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_FS_OK              0
#define VOS3_FS_ERR_NOENT      (-2)   /**< No such file or directory */
#define VOS3_FS_ERR_IO         (-5)   /**< I/O error */
#define VOS3_FS_ERR_BADF       (-9)   /**< Bad file descriptor */
#define VOS3_FS_ERR_NOMEM     (-12)   /**< Out of memory */
#define VOS3_FS_ERR_EXIST     (-17)   /**< File exists */
#define VOS3_FS_ERR_NOTDIR    (-20)   /**< Not a directory */
#define VOS3_FS_ERR_ISDIR     (-21)   /**< Is a directory */
#define VOS3_FS_ERR_INVAL     (-22)   /**< Invalid argument */
#define VOS3_FS_ERR_MFILE     (-24)   /**< Too many open files */
#define VOS3_FS_ERR_NOSPC     (-28)   /**< No space left */
#define VOS3_FS_ERR_NAMETOOLONG (-36) /**< Name too long */
#define VOS3_FS_ERR_NOTEMPTY  (-39)   /**< Directory not empty */
#define VOS3_FS_ERR_LOOP      (-40)   /**< Too many symlink levels */
#define VOS3_FS_ERR_NOSYS     (-38)   /**< Function not implemented */
#define VOS3_FS_ERR_PERM      (-1)    /**< Operation not permitted */
#define VOS3_FS_ERR_KEYREJECTED (-129) /**< Key/signature was rejected (EKEYREJECTED) — model SecureBoot gate (M3) */
#define VOS3_FS_ERR_NOTSUP   (-95)   /**< Operation not supported */

/* ============================================================================
 * FORWARD DECLARATIONS
 * ============================================================================ */

struct vos3_inode;
struct vos3_dentry;
struct vos3_file;
struct vos3_superblock;
struct vos3_fs_type;

/* ============================================================================
 * INODE STRUCTURE
 * ============================================================================ */

/** @brief Inode operations */
typedef struct vos3_inode_ops {
    /** Create file in directory */
    int (*create)(struct vos3_inode* dir, const char* name, uint32_t mode);
    /** Lookup entry in directory */
    struct vos3_dentry* (*lookup)(struct vos3_inode* dir, const char* name);
    /** Create directory */
    int (*mkdir)(struct vos3_inode* dir, const char* name, uint32_t mode);
    /** Remove directory */
    int (*rmdir)(struct vos3_inode* dir, const char* name);
    /** Unlink file */
    int (*unlink)(struct vos3_inode* dir, const char* name);
    /** Rename */
    int (*rename)(struct vos3_inode* old_dir, const char* old_name,
                  struct vos3_inode* new_dir, const char* new_name);
    /** Read symbolic link */
    int (*readlink)(struct vos3_inode* inode, char* buf, size_t size);
    /** Truncate file */
    int (*truncate)(struct vos3_inode* inode, size_t size);
    /** Create symbolic link (Task 1.2) */
    int (*symlink)(struct vos3_inode* dir, const char* name, const char* target);
    /** Create hard link (Task 1.2) */
    int (*link)(struct vos3_inode* old_inode, struct vos3_inode* dir, const char* name);
    /** Set file attributes — mode/uid/gid (Task 1.2) */
    int (*setattr)(struct vos3_inode* inode, uint32_t mode, uint32_t uid, uint32_t gid, int which);
} vos3_inode_ops_t;

/** @brief Flags for setattr 'which' parameter */
#define SETATTR_MODE    1
#define SETATTR_UID     2
#define SETATTR_GID     4

/** @brief Inode structure */
typedef struct vos3_inode {
    uint32_t            ino;          /**< Inode number */
    uint32_t            mode;         /**< File mode (type + permissions) */
    uint32_t            uid;          /**< Owner user ID */
    uint32_t            gid;          /**< Owner group ID */
    uint32_t            nlink;        /**< Link count */
    size_t              size;         /**< File size */
    uint64_t            atime;        /**< Access time */
    uint64_t            mtime;        /**< Modification time */
    uint64_t            ctime;        /**< Creation time */
    uint32_t            ref_count;    /**< Reference count */
    vos3_mutex_t        lock;         /**< Inode lock */
    struct vos3_superblock* sb;       /**< Superblock */
    const vos3_inode_ops_t* ops;      /**< Inode operations */
    const struct vos3_file_ops* default_fops; /**< Default file operations for this FS */
    void*               private_data; /**< FS-specific data */
} vos3_inode_t;

/* ============================================================================
 * DIRECTORY ENTRY
 * ============================================================================ */

/** @brief Directory entry (dentry) */
typedef struct vos3_dentry {
    char                name[VOS3_NAME_MAX];  /**< Entry name */
    vos3_inode_t*       inode;                /**< Associated inode */
    struct vos3_dentry* parent;               /**< Parent directory */
    struct vos3_dentry* children;             /**< First child (for dirs) */
    struct vos3_dentry* next;                 /**< Next sibling */
    uint32_t            ref_count;            /**< Reference count */
    vos3_mutex_t        lock;                 /**< Dentry lock */
} vos3_dentry_t;

/** @brief Directory entry for readdir */
typedef struct vos3_dirent {
    uint32_t            ino;                  /**< Inode number */
    uint16_t            reclen;               /**< Record length */
    uint8_t             type;                 /**< File type */
    char                name[VOS3_NAME_MAX];  /**< File name */
} vos3_dirent_t;

/* ============================================================================
 * FILE STRUCTURE
 * ============================================================================ */

/** @brief File operations */
typedef struct vos3_file_ops {
    /** Open file */
    int (*open)(struct vos3_file* file);
    /** Close file */
    int (*close)(struct vos3_file* file);
    /** Read from file */
    int64_t (*read)(struct vos3_file* file, void* buf, size_t count);
    /** Write to file */
    int64_t (*write)(struct vos3_file* file, const void* buf, size_t count);
    /** Seek in file */
    int64_t (*lseek)(struct vos3_file* file, int64_t offset, int whence);
    /** Read directory entries */
    int (*readdir)(struct vos3_file* file, vos3_dirent_t* dirents,
                   size_t count, size_t* out_count);
    /** Sync file */
    int (*fsync)(struct vos3_file* file);
    /** IO control */
    int (*ioctl)(struct vos3_file* file, uint32_t cmd, void* arg);
} vos3_file_ops_t;

/** @brief Open file structure */
typedef struct vos3_file {
    vos3_dentry_t*          dentry;       /**< Directory entry */
    vos3_inode_t*           inode;        /**< Associated inode */
    uint32_t                flags;        /**< Open flags */
    uint32_t                mode;         /**< Open mode */
    int64_t                 pos;          /**< Current position */
    volatile uint32_t       ref_count;    /**< Reference count -- use __atomic ops */
    vos3_mutex_t            lock;         /**< File lock */
    const vos3_file_ops_t*  ops;          /**< File operations */
    void*                   private_data; /**< FS-specific data */
} vos3_file_t;

/* ============================================================================
 * SUPERBLOCK
 * ============================================================================ */

/** @brief Superblock operations */
typedef struct vos3_sb_ops {
    /** Allocate inode */
    vos3_inode_t* (*alloc_inode)(struct vos3_superblock* sb);
    /** Free inode */
    void (*free_inode)(vos3_inode_t* inode);
    /** Sync superblock */
    int (*sync)(struct vos3_superblock* sb);
    /** Unmount */
    int (*unmount)(struct vos3_superblock* sb);
} vos3_sb_ops_t;

/** @brief Superblock structure */
typedef struct vos3_superblock {
    uint32_t                magic;        /**< FS magic number */
    uint32_t                block_size;   /**< Block size */
    uint64_t                block_count;  /**< Total blocks */
    uint64_t                free_blocks;  /**< Free blocks */
    uint32_t                inode_count;  /**< Total inodes */
    uint32_t                free_inodes;  /**< Free inodes */
    vos3_dentry_t*          root;         /**< Root dentry */
    const vos3_sb_ops_t*    ops;          /**< Superblock operations */
    struct vos3_fs_type*    fs_type;      /**< File system type */
    vos3_mutex_t            lock;         /**< Superblock lock */
    void*                   private_data; /**< FS-specific data */
} vos3_superblock_t;

/* ============================================================================
 * FILE SYSTEM TYPE
 * ============================================================================ */

/** @brief File system type */
#define VOS3_FS_NO_DCACHE  (1U << 0)  /**< Don't cache dentries (for virtual FS like procfs) */

typedef struct vos3_fs_type {
    const char*     name;                   /**< FS name (e.g., "ramfs") */
    uint32_t        flags;                  /**< FS flags */
    /** Mount file system */
    vos3_superblock_t* (*mount)(struct vos3_fs_type* fs, const char* source,
                                 uint32_t flags, void* data);
    /** Unmount file system */
    int (*unmount)(vos3_superblock_t* sb);
    struct vos3_fs_type* next;              /**< Next in list */
} vos3_fs_type_t;

/* ============================================================================
 * MOUNT POINT
 * ============================================================================ */

/** @brief Mount point */
typedef struct vos3_mount {
    vos3_superblock_t*  sb;               /**< Superblock */
    vos3_dentry_t*      mountpoint;       /**< Mount point dentry */
    char                path[VOS3_PATH_MAX]; /**< Mount path */
    uint32_t            flags;            /**< Mount flags */
    struct vos3_mount*  next;             /**< Next mount */
} vos3_mount_t;

/* ============================================================================
 * FILE DESCRIPTOR
 * ============================================================================ */

/** @brief File descriptor entry */
typedef struct vos3_fd_entry {
    vos3_file_t*    file;     /**< Open file */
    uint32_t        flags;    /**< FD flags (e.g., close-on-exec) */
} vos3_fd_entry_t;

/** @brief File descriptor table */
typedef struct vos3_fd_table {
    vos3_fd_entry_t entries[VOS3_MAX_FD];  /**< FD entries */
    vos3_spinlock_t spinlock;               /**< IRQ-safe spinlock for all FD ops */
    volatile uint32_t ref_count;            /**< Sharing count (CLONE_FILES) -- use __atomic ops */
} vos3_fd_table_t;

/* ============================================================================
 * VFS FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize VFS
 * @return 0 on success, negative on error
 */
int vos3_vfs_init(void);

/**
 * @brief Register file system type
 * @param[in] fs File system type
 * @return 0 on success, negative on error
 */
int vos3_fs_register(vos3_fs_type_t* fs);

/**
 * @brief Unregister file system type
 * @param[in] fs File system type
 * @return 0 on success, negative on error
 */
int vos3_fs_unregister(vos3_fs_type_t* fs);

/**
 * @brief Mount file system
 * @param[in] source Source device/path
 * @param[in] target Mount point
 * @param[in] fstype File system type name
 * @param[in] flags Mount flags
 * @param[in] data FS-specific data
 * @return 0 on success, negative on error
 */
int vos3_mount(const char* source, const char* target,
               const char* fstype, uint32_t flags, void* data);

/**
 * @brief Unmount file system
 * @param[in] target Mount point
 * @return 0 on success, negative on error
 */
int vos3_umount(const char* target);

/* ============================================================================
 * PATH OPERATIONS
 * ============================================================================ */

/**
 * @brief Resolve path to dentry
 * @param[in] path Path to resolve
 * @param[out] dentry Resolved dentry
 * @return 0 on success, negative on error
 */
int vos3_path_lookup(const char* path, vos3_dentry_t** dentry);

/**
 * @brief Get parent directory
 * @param[in] path Path
 * @param[out] parent Parent dentry
 * @param[out] name Last component name
 * @return 0 on success, negative on error
 */
int vos3_path_parent(const char* path, vos3_dentry_t** parent, char* name);

/* ============================================================================
 * FILE OPERATIONS
 * ============================================================================ */

/**
 * @brief Open file
 * @param[in] path File path
 * @param[in] flags Open flags
 * @param[in] mode Creation mode (if O_CREAT)
 * @return File descriptor or negative error
 */
int vos3_open(const char* path, uint32_t flags, uint32_t mode);

/**
 * @brief Close file
 * @param[in] fd File descriptor
 * @return 0 on success, negative on error
 */
int vos3_close(int fd);

/**
 * @brief Read from file
 * @param[in] fd File descriptor
 * @param[out] buf Buffer
 * @param[in] count Bytes to read
 * @return Bytes read or negative error
 */
int64_t vos3_read(int fd, void* buf, size_t count);

/**
 * @brief Write to file
 * @param[in] fd File descriptor
 * @param[in] buf Buffer
 * @param[in] count Bytes to write
 * @return Bytes written or negative error
 */
int64_t vos3_write(int fd, const void* buf, size_t count);

/**
 * @brief Seek in file
 * @param[in] fd File descriptor
 * @param[in] offset Offset
 * @param[in] whence SEEK_SET, SEEK_CUR, or SEEK_END
 * @return New position or negative error
 */
int64_t vos3_lseek(int fd, int64_t offset, int whence);

/**
 * @brief Get file status
 * @param[in] path File path
 * @param[out] st Status buffer
 * @return 0 on success, negative on error
 */
int vos3_stat(const char* path, vos3_inode_t* st);

/**
 * @brief Get file status by fd
 * @param[in] fd File descriptor
 * @param[out] st Status buffer
 * @return 0 on success, negative on error
 */
int vos3_fstat(int fd, vos3_inode_t* st);

/**
 * @brief Truncate file
 * @param[in] path File path
 * @param[in] length New length
 * @return 0 on success, negative on error
 */
int vos3_truncate(const char* path, size_t length);

/**
 * @brief Sync file
 * @param[in] fd File descriptor
 * @return 0 on success, negative on error
 */
int vos3_fsync(int fd);

/* ============================================================================
 * DIRECTORY OPERATIONS
 * ============================================================================ */

/**
 * @brief Create directory
 * @param[in] path Directory path
 * @param[in] mode Permissions
 * @return 0 on success, negative on error
 */
int vos3_mkdir(const char* path, uint32_t mode);

/**
 * @brief Remove directory
 * @param[in] path Directory path
 * @return 0 on success, negative on error
 */
int vos3_rmdir(const char* path);

/**
 * @brief Read directory entries
 * @param[in] fd Directory file descriptor
 * @param[out] dirents Directory entries buffer
 * @param[in] count Maximum entries
 * @param[out] out_count Actual entries read
 * @return 0 on success, negative on error
 */
int vos3_readdir(int fd, vos3_dirent_t* dirents, size_t count, size_t* out_count);

/**
 * @brief Unlink file
 * @param[in] path File path
 * @return 0 on success, negative on error
 */
int vos3_unlink(const char* path);

/**
 * @brief Rename file
 * @param[in] oldpath Old path
 * @param[in] newpath New path
 * @return 0 on success, negative on error
 */
int vos3_rename(const char* oldpath, const char* newpath);

/**
 * @brief Get current working directory
 * @param[out] buf Buffer for path
 * @param[in] size Buffer size
 * @return Buffer on success, NULL on error
 */
char* vos3_getcwd(char* buf, size_t size);

/**
 * @brief Change current directory
 * @param[in] path New directory
 * @return 0 on success, negative on error
 */
int vos3_chdir(const char* path);

/* ============================================================================
 * SYMLINK / HARD LINK / ATTRIBUTE OPERATIONS (Task 1.2)
 * ============================================================================ */

/** @brief lstat: stat without following final symlink */
int vos3_lstat(const char* path, vos3_inode_t* st);
/** @brief Read symbolic link target (no null terminator in buf, returns length) */
int vos3_readlink(const char* path, char* buf, size_t size);
/** @brief Create symbolic link: linkpath -> target */
int vos3_symlink(const char* target, const char* linkpath);
/** @brief Create hard link: newpath -> same inode as oldpath */
int vos3_link(const char* oldpath, const char* newpath);
/** @brief Change file mode bits */
int vos3_chmod(const char* path, uint32_t mode);
/** @brief Change file mode bits by fd */
int vos3_fchmod(int fd, uint32_t mode);
/** @brief Change file owner/group */
int vos3_chown(const char* path, uint32_t uid, uint32_t gid);
/** @brief Change file owner/group by fd */
int vos3_fchown(int fd, uint32_t uid, uint32_t gid);

/* ============================================================================
 * FILE DESCRIPTOR OPERATIONS
 * ============================================================================ */

/**
 * @brief Initialize FD table for task
 * @param[out] table FD table
 * @return 0 on success, negative on error
 */
int vos3_fd_table_init(vos3_fd_table_t* table);

/**
 * @brief Destroy FD table
 * @param[in] table FD table
 */
void vos3_fd_table_destroy(vos3_fd_table_t* table);
vos3_fd_table_t* vos3_fd_table_clone(vos3_fd_table_t* table);

/**
 * @brief Allocate file descriptor
 * @param[in] table FD table
 * @param[in] file Open file
 * @return FD or negative error
 */
int vos3_fd_alloc(vos3_fd_table_t* table, vos3_file_t* file);

/**
 * @brief Get file from FD
 * @param[in] table FD table
 * @param[in] fd File descriptor
 * @return File or NULL
 */
vos3_file_t* vos3_fd_get(vos3_fd_table_t* table, int fd);

/**
 * @brief Release a reference obtained from vos3_fd_get()
 *
 * Atomically decrements ref_count. If it reaches zero, the file's
 * close handler is invoked and the file structure may be freed.
 * Every successful vos3_fd_get() MUST be paired with exactly one
 * vos3_fd_put().
 *
 * @param[in] file File pointer previously returned by vos3_fd_get()
 */
void vos3_fd_put(vos3_file_t* file);
int vos3_file_retain(vos3_file_t* file);

/**
 * @brief Free file descriptor
 * @param[in] table FD table
 * @param[in] fd File descriptor
 * @return 0 on success, negative on error
 */
int vos3_fd_free(vos3_fd_table_t* table, int fd);

/**
 * @brief Duplicate file descriptor
 * @param[in] table FD table
 * @param[in] oldfd Old FD
 * @return New FD or negative error
 */
int vos3_dup(vos3_fd_table_t* table, int oldfd);

/**
 * @brief Duplicate FD to specific number
 * @param[in] table FD table
 * @param[in] oldfd Old FD
 * @param[in] newfd New FD number
 * @return New FD or negative error
 */
int vos3_dup2(vos3_fd_table_t* table, int oldfd, int newfd);

/**
 * @brief Initialize file system syscalls
 */
void vos3_fs_syscalls_init(void);

/* ============================================================================
 * STATFS
 * ============================================================================ */

/** @brief Filesystem statistics (returned by vos3_statfs) */
typedef struct vos3_statfs {
    uint32_t    f_type;         /**< Filesystem type magic */
    uint32_t    f_bsize;        /**< Block size */
    uint64_t    f_blocks;       /**< Total blocks */
    uint64_t    f_bfree;        /**< Free blocks */
    uint32_t    f_files;        /**< Total inodes */
    uint32_t    f_ffree;        /**< Free inodes */
    uint32_t    f_mount_count;  /**< Mount count (vos3fs-specific) */
    char        f_fstype[16];   /**< FS type name */
} vos3_statfs_t;

/**
 * @brief Get filesystem statistics for a path
 * @param[in]  path  Mount point path (e.g., "/disk")
 * @param[out] buf   Statistics buffer
 * @return 0 on success, negative on error
 */
int vos3_statfs(const char* path, vos3_statfs_t* buf);

/* ============================================================================
 * PIPE OPERATIONS
 * ============================================================================ */

/**
 * @brief Initialize pipe subsystem
 * @return 0 on success, negative on error
 */
int vos3_pipe_init(void);

/**
 * @brief Create a pipe
 * @param[out] pipefd Array of two file descriptors [read_fd, write_fd]
 * @return 0 on success, negative on error
 */
int vos3_pipe(int pipefd[2]);

#endif /* VOS3_VFS_H */
