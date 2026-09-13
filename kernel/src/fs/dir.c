/**
 * @file dir.c
 * @brief VOS3 Directory Operations
 *
 * @details Directory manipulation using VFS.
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

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/* From ramfs.c */
extern const vos3_file_ops_t g_ramfs_file_ops;

/* Kernel FD table (defined in file.c) */
extern vos3_fd_table_t* vos3_get_kernel_fd_table(void);

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
    return vos3_get_kernel_fd_table();
}

/* ============================================================================
 * DIRECTORY OPERATIONS
 * ============================================================================ */

int vos3_mkdir(const char* path, uint32_t mode)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Check if already exists */
    vos3_dentry_t* existing = NULL;
    int result = vos3_path_lookup(path, &existing);
    if (result == VOS3_FS_OK) {
        return VOS3_FS_ERR_EXIST;
    }

    /* Get parent directory */
    vos3_dentry_t* parent = NULL;
    char name[VOS3_NAME_MAX];

    result = vos3_path_parent(path, &parent, name);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (parent->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Check parent is a directory */
    if ((parent->inode->mode & VOS3_S_IFMT) != VOS3_S_IFDIR) {
        return VOS3_FS_ERR_NOTDIR;
    }

    /* Create inode for new directory */
    vos3_inode_t* inode = NULL;
    if (parent->inode->sb != NULL && parent->inode->sb->ops != NULL &&
        parent->inode->sb->ops->alloc_inode != NULL) {
        inode = parent->inode->sb->ops->alloc_inode(parent->inode->sb);
    }

    if (inode == NULL) {
        return VOS3_FS_ERR_NOMEM;
    }

    vos3_dentry_t* dentry = (vos3_dentry_t*)vos3_kzalloc(sizeof(vos3_dentry_t));
    if (dentry == NULL) {
        if (parent->inode->sb->ops->free_inode != NULL) {
            parent->inode->sb->ops->free_inode(inode);
        }
        return VOS3_FS_ERR_NOMEM;
    }

    inode->mode = VOS3_S_IFDIR | (mode & 0777U);
    inode->nlink = 2U;  /* . and parent */

    size_t len = strlen(name);
    if (len >= VOS3_NAME_MAX) {
        len = VOS3_NAME_MAX - 1U;
    }
    memcpy(dentry->name, name, len);
    dentry->name[len] = '\0';
    dentry->inode = inode;
    dentry->ref_count = 1U;
    vos3_mutex_init(&dentry->lock, "dentry");

    /* Add to parent */
    dentry->parent = parent;
    vos3_mutex_lock(&parent->lock);
    dentry->next = parent->children;
    parent->children = dentry;
    parent->inode->nlink++;  /* New subdirectory */
    vos3_mutex_unlock(&parent->lock);

    VOS3_DEBUG("Created directory '%s'", path);

    return VOS3_FS_OK;
}

int vos3_rmdir(const char* path)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Lookup directory */
    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Check it's a directory */
    if ((dentry->inode->mode & VOS3_S_IFMT) != VOS3_S_IFDIR) {
        return VOS3_FS_ERR_NOTDIR;
    }

    /* Check if empty */
    if (dentry->children != NULL) {
        return VOS3_FS_ERR_NOTEMPTY;
    }

    /* Cannot remove root */
    if (dentry->parent == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* parent = dentry->parent;

    /* Remove from parent */
    vos3_mutex_lock(&parent->lock);

    vos3_dentry_t** pp = &parent->children;
    while (*pp != NULL) {
        if (*pp == dentry) {
            *pp = dentry->next;
            parent->inode->nlink--;
            break;
        }
        pp = &(*pp)->next;
    }

    vos3_mutex_unlock(&parent->lock);

    /* Free inode and dentry */
    if (dentry->inode->sb != NULL && dentry->inode->sb->ops != NULL &&
        dentry->inode->sb->ops->free_inode != NULL) {
        dentry->inode->sb->ops->free_inode(dentry->inode);
    }

    vos3_mutex_destroy(&dentry->lock);
    vos3_kfree(dentry);

    VOS3_DEBUG("Removed directory '%s'", path);

    return VOS3_FS_OK;
}

int vos3_readdir(int fd, vos3_dirent_t* dirents, size_t count, size_t* out_count)
{
    if (dirents == NULL || out_count == NULL) {
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

    /* Check it's a directory */
    if (file->inode == NULL ||
        (file->inode->mode & VOS3_S_IFMT) != VOS3_S_IFDIR) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_NOTDIR;
    }

    if (file->ops == NULL || file->ops->readdir == NULL) {
        vos3_fd_put(file);
        return VOS3_FS_ERR_INVAL;
    }

    int result = file->ops->readdir(file, dirents, count, out_count);
    vos3_fd_put(file);
    return result;
}

int vos3_unlink(const char* path)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Lookup file */
    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Check it's not a directory */
    if ((dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
        return VOS3_FS_ERR_ISDIR;
    }

    /* Cannot unlink without parent */
    if (dentry->parent == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_dentry_t* parent = dentry->parent;

    /* Remove from parent */
    vos3_mutex_lock(&parent->lock);

    vos3_dentry_t** pp = &parent->children;
    while (*pp != NULL) {
        if (*pp == dentry) {
            *pp = dentry->next;
            break;
        }
        pp = &(*pp)->next;
    }

    vos3_mutex_unlock(&parent->lock);

    /* Decrement link count */
    if (dentry->inode->nlink > 0U) {
        dentry->inode->nlink--;
    }

    /* Free if no more links and no references */
    if (dentry->inode->nlink == 0U && dentry->inode->ref_count <= 1U) {
        if (dentry->inode->sb != NULL && dentry->inode->sb->ops != NULL &&
            dentry->inode->sb->ops->free_inode != NULL) {
            dentry->inode->sb->ops->free_inode(dentry->inode);
        }
        dentry->inode = NULL;
    }

    vos3_mutex_destroy(&dentry->lock);
    vos3_kfree(dentry);

    VOS3_DEBUG("Unlinked '%s'", path);

    return VOS3_FS_OK;
}

int vos3_rename(const char* oldpath, const char* newpath)
{
    if (oldpath == NULL || newpath == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Lookup old path */
    vos3_dentry_t* old_dentry = NULL;
    int result = vos3_path_lookup(oldpath, &old_dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (old_dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Get old parent and old name */
    vos3_dentry_t* old_parent_dentry = old_dentry->parent;
    if (old_parent_dentry == NULL || old_parent_dentry->inode == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Get new parent and name */
    vos3_dentry_t* new_parent_dentry = NULL;
    char new_name[VOS3_NAME_MAX];

    result = vos3_path_parent(newpath, &new_parent_dentry, new_name);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (new_parent_dentry == NULL || new_parent_dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /*
     * If the old parent inode has an ops->rename, delegate to the filesystem
     * implementation (e.g., vos3fs on-disk rename).
     */
    if (old_parent_dentry->inode->ops != NULL &&
        old_parent_dentry->inode->ops->rename != NULL) {
        return old_parent_dentry->inode->ops->rename(
            old_parent_dentry->inode, old_dentry->name,
            new_parent_dentry->inode, new_name);
    }

    /* In-memory (ramfs) rename fallback */

    /* Check if destination already exists */
    vos3_dentry_t* new_dentry = NULL;
    result = vos3_path_lookup(newpath, &new_dentry);
    if (result == VOS3_FS_OK && new_dentry != NULL) {
        /* Self-rename is a no-op (POSIX: rename(path, path) succeeds) */
        if (new_dentry == old_dentry) {
            return VOS3_FS_OK;
        }
        /* Destination exists -- unlink it first (POSIX rename semantics) */
        if (new_dentry->inode != NULL) {
            int src_is_dir = ((old_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR);
            int dst_is_dir = ((new_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR);

            /* Cannot overwrite directory with non-directory or vice versa */
            if (src_is_dir != dst_is_dir) {
                return src_is_dir ? VOS3_FS_ERR_NOTDIR : VOS3_FS_ERR_ISDIR;
            }

            /* If destination is a non-empty directory, fail */
            if (dst_is_dir && new_dentry->children != NULL) {
                return VOS3_FS_ERR_NOTEMPTY;
            }

            /* Remove destination from its parent */
            vos3_dentry_t* dst_parent = new_dentry->parent;
            if (dst_parent != NULL) {
                vos3_mutex_lock(&dst_parent->lock);
                vos3_dentry_t** pp = &dst_parent->children;
                while (*pp != NULL) {
                    if (*pp == new_dentry) {
                        *pp = new_dentry->next;
                        if (dst_is_dir) {
                            dst_parent->inode->nlink--;
                        }
                        break;
                    }
                    pp = &(*pp)->next;
                }
                vos3_mutex_unlock(&dst_parent->lock);
            }

            /* Decrement link count and free if needed */
            if (new_dentry->inode->nlink > 0U) {
                new_dentry->inode->nlink--;
            }
            if (new_dentry->inode->nlink == 0U && new_dentry->inode->ref_count <= 1U) {
                if (new_dentry->inode->sb != NULL && new_dentry->inode->sb->ops != NULL &&
                    new_dentry->inode->sb->ops->free_inode != NULL) {
                    new_dentry->inode->sb->ops->free_inode(new_dentry->inode);
                }
                new_dentry->inode = NULL;
            }
            vos3_mutex_destroy(&new_dentry->lock);
            vos3_kfree(new_dentry);
        }
    }

    /* Remove from old parent */
    if (old_parent_dentry != NULL) {
        vos3_mutex_lock(&old_parent_dentry->lock);

        vos3_dentry_t** pp = &old_parent_dentry->children;
        while (*pp != NULL) {
            if (*pp == old_dentry) {
                *pp = old_dentry->next;
                if ((old_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
                    old_parent_dentry->inode->nlink--;
                }
                break;
            }
            pp = &(*pp)->next;
        }

        vos3_mutex_unlock(&old_parent_dentry->lock);
    }

    /* Update name */
    size_t len = strlen(new_name);
    if (len >= VOS3_NAME_MAX) {
        len = VOS3_NAME_MAX - 1U;
    }
    memcpy(old_dentry->name, new_name, len);
    old_dentry->name[len] = '\0';

    /* Add to new parent */
    vos3_mutex_lock(&new_parent_dentry->lock);

    old_dentry->parent = new_parent_dentry;
    old_dentry->next = new_parent_dentry->children;
    new_parent_dentry->children = old_dentry;
    if ((old_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
        new_parent_dentry->inode->nlink++;
    }

    vos3_mutex_unlock(&new_parent_dentry->lock);

    VOS3_DEBUG("Renamed '%s' to '%s'", oldpath, newpath);

    return VOS3_FS_OK;
}

char* vos3_getcwd(char* buf, size_t size)
{
    if (buf == NULL || size == 0U) {
        return NULL;
    }

    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->cwd == NULL) {
        /* Return root */
        if (size >= 2U) {
            buf[0] = '/';
            buf[1] = '\0';
            return buf;
        }
        return NULL;
    }

    /* Build path by traversing up to root */
    char temp[VOS3_PATH_MAX];
    size_t pos = VOS3_PATH_MAX - 1U;
    temp[pos] = '\0';

    vos3_dentry_t* dentry = task->cwd;
    while (dentry != NULL && dentry->parent != NULL) {
        size_t len = strlen(dentry->name);
        if (pos < len + 1U) {
            return NULL;  /* Path too long */
        }

        pos -= len;
        memcpy(temp + pos, dentry->name, len);
        pos--;
        temp[pos] = '/';

        dentry = dentry->parent;
    }

    if (temp[pos] != '/') {
        pos--;
        temp[pos] = '/';
    }

    size_t path_len = VOS3_PATH_MAX - 1U - pos;
    if (path_len >= size) {
        return NULL;  /* Buffer too small */
    }

    memcpy(buf, temp + pos, path_len + 1U);

    return buf;
}

int vos3_chdir(const char* path)
{
    if (path == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Lookup directory */
    vos3_dentry_t* dentry = NULL;
    int result = vos3_path_lookup(path, &dentry);
    if (result != VOS3_FS_OK) {
        return result;
    }

    if (dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Check it's a directory */
    if ((dentry->inode->mode & VOS3_S_IFMT) != VOS3_S_IFDIR) {
        return VOS3_FS_ERR_NOTDIR;
    }

    /* Update task's cwd */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    task->cwd = dentry;

    VOS3_DEBUG("Changed directory to '%s'", path);

    return VOS3_FS_OK;
}
