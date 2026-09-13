/**
 * @file file.c
 * @brief VOS3 File Operations
 *
 * @details High-level file operations using VFS.
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
#include "../../include/vos/heap.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/task.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/device.h"

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/* From ramfs.c */
extern const vos3_file_ops_t g_ramfs_file_ops;

/* ============================================================================
 * KERNEL FD TABLE (for early boot)
 * ============================================================================ */

static vos3_fd_table_t g_kernel_fd_table;
static int g_kernel_fd_table_initialized = 0;

/**
 * @brief Initialize kernel FD table
 */
static void init_kernel_fd_table(void)
{
    if (g_kernel_fd_table_initialized == 0) {
        (void)vos3_fd_table_init(&g_kernel_fd_table);
        g_kernel_fd_table_initialized = 1;
    }
}

/**
 * @brief Get kernel FD table (exported for dir.c)
 */
vos3_fd_table_t* vos3_get_kernel_fd_table(void)
{
    init_kernel_fd_table();
    return &g_kernel_fd_table;
}

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get current task's FD table or kernel FD table
 */
static vos3_fd_table_t* get_fd_table(void)
{
    vos3_task_t* task = vos3_sched_current();
    if (task != NULL && task->fd_table != NULL) {
        return task->fd_table;
    }

    /* Use kernel FD table during early boot */
    init_kernel_fd_table();
    return &g_kernel_fd_table;
}

/**
 * @brief Allocate file structure
 */
static vos3_file_t* file_alloc(vos3_dentry_t* dentry, uint32_t flags)
{
    vos3_file_t* file = (vos3_file_t*)vos3_kzalloc(sizeof(vos3_file_t));
    if (file == NULL) {
        return NULL;
    }

    file->dentry = dentry;
    file->inode = dentry->inode;
    file->flags = flags;
    file->pos = 0;
    /* ref_count starts at 0 (kzalloc zeroed); vos3_fd_alloc() will increment to 1.
     * This matches pipe.c and epoll.c which also use ref_count=0 before fd_alloc. */

    /* Set file operations based on file type */
    if (file->inode != NULL) {
        uint32_t mode = file->inode->mode;
        if (VOS3_S_ISCHR(mode) || VOS3_S_ISBLK(mode)) {
            /* Device file - use devfs operations */
            file->ops = vos3_devfs_get_ops();
        } else if (file->inode->default_fops != NULL) {
            /* FS-specific file ops set by the filesystem */
            file->ops = file->inode->default_fops;
        } else {
            /* Default - use ramfs operations */
            file->ops = &g_ramfs_file_ops;
        }
    } else {
        file->ops = &g_ramfs_file_ops;
    }

    vos3_mutex_init(&file->lock, "file");

    /* Increment inode reference */
    if (file->inode != NULL) {
        file->inode->ref_count++;
    }

    return file;
}

/* ============================================================================
 * FILE OPERATIONS
 * ============================================================================ */

int vos3_open(const char* path, uint32_t flags, uint32_t mode)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Try to lookup existing file */
    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);

    if (result == VOS3_FS_ERR_NOENT) {
        /* File doesn't exist */
        if ((flags & VOS3_O_CREAT) == 0U) {
            return VOS3_FS_ERR_NOENT;
        }

        /* Create new file */
        vos3_dentry_t* parent = NULL;
        char name[VOS3_NAME_MAX];

        result = vos3_path_parent(path, &parent, name);
        if (result != VOS3_FS_OK) {
            return result;
        }

        if (parent->inode == NULL) {
            return VOS3_FS_ERR_NOENT;
        }

        /*
         * Call the parent inode's create() operation first.
         * For on-disk filesystems (vos3fs), this allocates the disk inode,
         * writes it, and adds the directory entry to the parent on disk.
         */
        if (parent->inode->ops != NULL && parent->inode->ops->create != NULL) {
            result = parent->inode->ops->create(parent->inode, name,
                                                VOS3_S_IFREG | (mode & 0777U));
            if (result != VOS3_FS_OK) {
                return result;
            }
        }

        /*
         * Now look up the newly created file via the filesystem's lookup.
         * This returns a dentry+inode with the correct on-disk ino and
         * filesystem-specific file operations attached.
         */
        if (parent->inode->ops != NULL && parent->inode->ops->lookup != NULL) {
            dentry = parent->inode->ops->lookup(parent->inode, name);
        }

        if (dentry == NULL) {
            /* Fallback for ramfs: create in-memory inode + dentry */
            vos3_inode_t* inode = NULL;
            if (parent->inode->sb != NULL && parent->inode->sb->ops != NULL &&
                parent->inode->sb->ops->alloc_inode != NULL) {
                inode = parent->inode->sb->ops->alloc_inode(parent->inode->sb);
            }

            if (inode == NULL) {
                return VOS3_FS_ERR_NOMEM;
            }

            inode->mode = VOS3_S_IFREG | (mode & 0777U);

            dentry = (vos3_dentry_t*)vos3_kzalloc(sizeof(vos3_dentry_t));
            if (dentry == NULL) {
                if (parent->inode->sb->ops->free_inode != NULL) {
                    parent->inode->sb->ops->free_inode(inode);
                }
                return VOS3_FS_ERR_NOMEM;
            }

            size_t len = strlen(name);
            if (len >= VOS3_NAME_MAX) {
                len = VOS3_NAME_MAX - 1U;
            }
            memcpy(dentry->name, name, len);
            dentry->name[len] = '\0';
            dentry->inode = inode;
            dentry->ref_count = 1U;
            vos3_mutex_init(&dentry->lock, "dentry");

            dentry->parent = parent;
            vos3_mutex_lock(&parent->lock);
            dentry->next = parent->children;
            parent->children = dentry;
            vos3_mutex_unlock(&parent->lock);
        }

        VOS3_DEBUG("Created file '%s'", path);

    } else if (result != VOS3_FS_OK) {
        return result;
    } else {
        /* File exists */
        if ((flags & VOS3_O_EXCL) != 0U && (flags & VOS3_O_CREAT) != 0U) {
            return VOS3_FS_ERR_EXIST;
        }

        /* Check if directory when expecting file */
        if (dentry->inode != NULL &&
            (dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
            if ((flags & VOS3_O_DIRECTORY) == 0U &&
                (flags & VOS3_O_WRONLY) != 0U) {
                return VOS3_FS_ERR_ISDIR;
            }
        }
    }

    /* Truncate if requested */
    if ((flags & VOS3_O_TRUNC) != 0U && dentry->inode != NULL) {
        if (dentry->inode->ops != NULL && dentry->inode->ops->truncate != NULL) {
            (void)dentry->inode->ops->truncate(dentry->inode, 0U);
        }
    }

    /* Allocate file structure */
    vos3_file_t* file = file_alloc(dentry, flags);
    if (file == NULL) {
        return VOS3_FS_ERR_NOMEM;
    }

    /* Call open handler */
    if (file->ops != NULL && file->ops->open != NULL) {
        result = file->ops->open(file);
        if (result != VOS3_FS_OK) {
            vos3_kfree(file);
            return result;
        }
    }

    /* Allocate file descriptor */
    int fd = vos3_fd_alloc(fd_table, file);
    if (fd < 0) {
        if (file->ops != NULL && file->ops->close != NULL) {
            (void)file->ops->close(file);
        } else {
            vos3_kfree(file);
        }
        return fd;
    }

    return fd;
}

int vos3_close(int fd)
{
    /* Socket FDs live in separate pool (fd >= SOCKET_FD_BASE=100) */
    if (fd >= 100) {
        extern int vos3_sys_closesocket(int fd);
        return vos3_sys_closesocket(fd);
    }

    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    return vos3_fd_free(fd_table, fd);
}

int64_t vos3_read(int fd, void* buf, size_t count)
{
    if (buf == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    /* Check access mode */
    if ((file->flags & VOS3_O_ACCMODE) == VOS3_O_WRONLY) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_BADF;
    }

    if (file->ops == NULL || file->ops->read == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    int64_t result = file->ops->read(file, buf, count);
    vos3_fd_put(file);
    return result;
}

int64_t vos3_write(int fd, const void* buf, size_t count)
{
    if (buf == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    /* Check access mode */
    if ((file->flags & VOS3_O_ACCMODE) == VOS3_O_RDONLY) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_BADF;
    }

    if (file->ops == NULL || file->ops->write == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    int64_t result = file->ops->write(file, buf, count);
    vos3_fd_put(file);
    return result;
}

int64_t vos3_lseek(int fd, int64_t offset, int whence)
{
    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    if (file->ops == NULL || file->ops->lseek == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    int64_t result = file->ops->lseek(file, offset, whence);
    vos3_fd_put(file);
    return result;
}

int vos3_stat(const char* path, vos3_inode_t* st)
{
    if (path == NULL || st == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Copy inode info */
    *st = *(dentry->inode);

    return VOS3_FS_OK;
}

int vos3_fstat(int fd, vos3_inode_t* st)
{
    if (st == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    if (file->inode == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    /* Copy inode info */
    *st = *(file->inode);

    vos3_fd_put(file);
    return VOS3_FS_OK;
}

int vos3_truncate(const char* path, size_t length)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    if (dentry->inode->ops == NULL || dentry->inode->ops->truncate == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    return dentry->inode->ops->truncate(dentry->inode, length);
}

int vos3_fsync(int fd)
{
    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    if (file->ops != NULL && file->ops->fsync != NULL) {
        int result = file->ops->fsync(file);
        vos3_fd_put(file);
        return result;
    }

    /* No fsync handler - success (ramfs doesn't need sync) */
    vos3_fd_put(file);
    return VOS3_FS_OK;
}

/* ============================================================================
 * TASK 1.2: POSIX FILE OPERATIONS (lstat, readlink, symlink, link, chmod...)
 * ============================================================================ */

int vos3_lstat(const char* path, vos3_inode_t* st)
{
    /* lstat is identical to stat in VOS3: path lookup never follows symlinks */
    return vos3_stat(path, st);
}

int vos3_readlink(const char* path, char* buf, size_t size)
{
    if (path == NULL || buf == NULL || size == 0) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    if (dentry->inode->ops == NULL || dentry->inode->ops->readlink == NULL) {
        return VOS3_FS_ERR_INVAL;  /* Not a symlink */
    }

    return dentry->inode->ops->readlink(dentry->inode, buf, size);
}

int vos3_symlink(const char* target, const char* linkpath)
{
    if (target == NULL || linkpath == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    char name[VOS3_NAME_MAX + 1];
    vos3_dentry_t* parent = NULL;
    int result = vos3_path_parent(linkpath, &parent, name);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (parent->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    if (parent->inode->ops == NULL || parent->inode->ops->symlink == NULL) {
        return VOS3_FS_ERR_NOSYS;
    }

    return parent->inode->ops->symlink(parent->inode, name, target);
}

int vos3_link(const char* oldpath, const char* newpath)
{
    if (oldpath == NULL || newpath == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Look up the existing inode */
    vos3_dentry_t* old_dentry = NULL;
    int result = vos3_path_lookup(oldpath, &old_dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (old_dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Find the new parent directory */
    char name[VOS3_NAME_MAX + 1];
    vos3_dentry_t* parent = NULL;
    result = vos3_path_parent(newpath, &parent, name);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (parent->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    if (parent->inode->ops == NULL || parent->inode->ops->link == NULL) {
        return VOS3_FS_ERR_NOSYS;
    }

    return parent->inode->ops->link(old_dentry->inode, parent->inode, name);
}

int vos3_chmod(const char* path, uint32_t mode)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    if (dentry->inode->ops == NULL || dentry->inode->ops->setattr == NULL) {
        return VOS3_FS_ERR_NOSYS;
    }

    return dentry->inode->ops->setattr(dentry->inode, mode, 0, 0, SETATTR_MODE);
}

int vos3_fchmod(int fd, uint32_t mode)
{
    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    if (file->inode == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    if (file->inode->ops == NULL || file->inode->ops->setattr == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_NOSYS;
    }

    int result = file->inode->ops->setattr(file->inode, mode, 0, 0, SETATTR_MODE);
    vos3_fd_put(file);
    return result;
}

int vos3_chown(const char* path, uint32_t uid, uint32_t gid)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    if (dentry->inode->ops == NULL || dentry->inode->ops->setattr == NULL) {
        return VOS3_FS_ERR_NOSYS;
    }

    return dentry->inode->ops->setattr(dentry->inode, 0, uid, gid, SETATTR_UID | SETATTR_GID);
}

int vos3_fchown(int fd, uint32_t uid, uint32_t gid)
{
    vos3_fd_table_t* fd_table = get_fd_table();
    if (fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    if (file->inode == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    if (file->inode->ops == NULL || file->inode->ops->setattr == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_NOSYS;
    }

    int result = file->inode->ops->setattr(file->inode, 0, uid, gid, SETATTR_UID | SETATTR_GID);
    vos3_fd_put(file);
    return result;
}
