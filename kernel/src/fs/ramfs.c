/**
 * @file ramfs.c
 * @brief VOS3 RAM File System
 *
 * @details Simple in-memory file system.
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
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * RAMFS STRUCTURES
 * ============================================================================ */

/** @brief RAMFS magic number */
#define RAMFS_MAGIC     0x52414D46U  /* "RAMF" */

/** @brief Maximum file size (1 MB) */
#define RAMFS_MAX_SIZE  (1024U * 1024U)

/** @brief RAMFS inode data */
typedef struct ramfs_inode {
    uint8_t*    data;       /**< File data (for regular files) */
    size_t      capacity;   /**< Allocated capacity */
} ramfs_inode_t;

/* ============================================================================
 * FORWARD DECLARATIONS
 * ============================================================================ */

static int ramfs_inode_create(vos3_inode_t* dir, const char* name, uint32_t mode);
static vos3_dentry_t* ramfs_inode_lookup(vos3_inode_t* dir, const char* name);
static int ramfs_inode_mkdir(vos3_inode_t* dir, const char* name, uint32_t mode);
static int ramfs_inode_rmdir(vos3_inode_t* dir, const char* name);
static int ramfs_inode_unlink(vos3_inode_t* dir, const char* name);
static int ramfs_inode_rename(vos3_inode_t* old_dir, const char* old_name,
                               vos3_inode_t* new_dir, const char* new_name);
static int ramfs_inode_truncate(vos3_inode_t* inode, size_t size);
static int ramfs_inode_readlink(vos3_inode_t* inode, char* buf, size_t size);
static int ramfs_inode_symlink(vos3_inode_t* dir, const char* name, const char* target);
static int ramfs_inode_link(vos3_inode_t* old_inode, vos3_inode_t* dir, const char* name);
static int ramfs_inode_setattr(vos3_inode_t* inode, uint32_t mode, uint32_t uid, uint32_t gid, int which);

static int ramfs_file_open(vos3_file_t* file);
static int ramfs_file_close(vos3_file_t* file);
static int64_t ramfs_file_read(vos3_file_t* file, void* buf, size_t count);
static int64_t ramfs_file_write(vos3_file_t* file, const void* buf, size_t count);
static int64_t ramfs_file_lseek(vos3_file_t* file, int64_t offset, int whence);
static int ramfs_file_readdir(vos3_file_t* file, vos3_dirent_t* dirents,
                               size_t count, size_t* out_count);

static vos3_inode_t* ramfs_sb_alloc_inode(vos3_superblock_t* sb);
static void ramfs_sb_free_inode(vos3_inode_t* inode);

/* ============================================================================
 * OPERATIONS STRUCTURES
 * ============================================================================ */

static const vos3_inode_ops_t g_ramfs_inode_ops = {
    .create   = ramfs_inode_create,
    .lookup   = ramfs_inode_lookup,
    .mkdir    = ramfs_inode_mkdir,
    .rmdir    = ramfs_inode_rmdir,
    .unlink   = ramfs_inode_unlink,
    .rename   = ramfs_inode_rename,
    .readlink = ramfs_inode_readlink,
    .truncate = ramfs_inode_truncate,
    .symlink  = ramfs_inode_symlink,
    .link     = ramfs_inode_link,
    .setattr  = ramfs_inode_setattr,
};

const vos3_file_ops_t g_ramfs_file_ops = {
    .open    = ramfs_file_open,
    .close   = ramfs_file_close,
    .read    = ramfs_file_read,
    .write   = ramfs_file_write,
    .lseek   = ramfs_file_lseek,
    .readdir = ramfs_file_readdir,
    .fsync   = NULL,
    .ioctl   = NULL,
};

static const vos3_sb_ops_t g_ramfs_sb_ops = {
    .alloc_inode = ramfs_sb_alloc_inode,
    .free_inode  = ramfs_sb_free_inode,
    .sync        = NULL,
    .unmount     = NULL,
};

/* ============================================================================
 * SUPERBLOCK STATE
 * ============================================================================ */

static uint32_t g_ramfs_next_ino = VOS3_ROOT_INODE;
static vos3_spinlock_t g_ramfs_lock = VOS3_SPINLOCK_INIT;

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Allocate inode number
 */
static uint32_t ramfs_alloc_ino(void)
{
    vos3_spinlock_lock(&g_ramfs_lock);
    uint32_t ino = g_ramfs_next_ino++;
    vos3_spinlock_unlock(&g_ramfs_lock);
    return ino;
}

/**
 * @brief Create inode
 */
static vos3_inode_t* ramfs_create_inode(vos3_superblock_t* sb, uint32_t mode)
{
    vos3_inode_t* inode = (vos3_inode_t*)vos3_kzalloc(sizeof(vos3_inode_t));
    if (inode == NULL) {
        return NULL;
    }

    ramfs_inode_t* ri = (ramfs_inode_t*)vos3_kzalloc(sizeof(ramfs_inode_t));
    if (ri == NULL) {
        vos3_kfree(inode);
        return NULL;
    }

    inode->ino = ramfs_alloc_ino();
    inode->mode = mode;
    inode->uid = 0U;
    inode->gid = 0U;
    inode->nlink = 1U;
    inode->size = 0U;
    inode->ref_count = 1U;
    inode->sb = sb;
    inode->ops = &g_ramfs_inode_ops;
    inode->default_fops = &g_ramfs_file_ops;
    inode->private_data = ri;

    vos3_mutex_init(&inode->lock, "ramfs_inode");

    return inode;
}

/**
 * @brief Create dentry and attach to parent
 */
static vos3_dentry_t* ramfs_create_dentry(vos3_dentry_t* parent,
                                           const char* name,
                                           vos3_inode_t* inode)
{
    vos3_dentry_t* dentry = (vos3_dentry_t*)vos3_kzalloc(sizeof(vos3_dentry_t));
    if (dentry == NULL) {
        return NULL;
    }

    size_t len = strlen(name);
    if (len >= VOS3_NAME_MAX) {
        len = VOS3_NAME_MAX - 1U;
    }
    memcpy(dentry->name, name, len);
    dentry->name[len] = '\0';

    dentry->inode = inode;
    dentry->ref_count = 1U;
    vos3_mutex_init(&dentry->lock, "ramfs_dentry");

    /* Add to parent */
    if (parent != NULL) {
        dentry->parent = parent;
        vos3_mutex_lock(&parent->lock);
        dentry->next = parent->children;
        parent->children = dentry;
        vos3_mutex_unlock(&parent->lock);
    }

    return dentry;
}

/* ============================================================================
 * INODE OPERATIONS
 * ============================================================================ */

static int ramfs_inode_create(vos3_inode_t* dir, const char* name, uint32_t mode)
{
    if (dir == NULL || name == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    if ((dir->mode & VOS3_S_IFMT) != VOS3_S_IFDIR) {
        return VOS3_FS_ERR_NOTDIR;
    }

    /* No-op for ramfs: the VFS fallback path in vos3_open() handles
     * in-memory inode + dentry creation after this returns OK. */
    (void)mode;

    return VOS3_FS_OK;
}

static vos3_dentry_t* ramfs_inode_lookup(vos3_inode_t* dir, const char* name)
{
    /* Lookup is handled by dentry cache in vfs.c */
    /* This is called when cache misses - for ramfs, that means not found */
    (void)dir;
    (void)name;
    return NULL;
}

static int ramfs_inode_mkdir(vos3_inode_t* dir, const char* name, uint32_t mode)
{
    (void)dir;
    (void)name;
    (void)mode;
    /* Handled by vos3_mkdir using dentry operations */
    return VOS3_FS_OK;
}

static int ramfs_inode_rmdir(vos3_inode_t* dir, const char* name)
{
    (void)dir;
    (void)name;
    /* Handled by vos3_rmdir */
    return VOS3_FS_OK;
}

static int ramfs_inode_unlink(vos3_inode_t* dir, const char* name)
{
    (void)dir;
    (void)name;
    /* Handled by vos3_unlink */
    return VOS3_FS_OK;
}

/* ============================================================================
 * RAMFS RENAME HELPERS
 * ============================================================================ */

/**
 * @brief Find the dentry for a given inode by searching from root
 */
static vos3_dentry_t* ramfs_find_dentry_by_inode(vos3_dentry_t* root,
                                                   vos3_inode_t* inode)
{
    if (root == NULL || inode == NULL) {
        return NULL;
    }

    if (root->inode == inode) {
        return root;
    }

    vos3_dentry_t* child = root->children;
    while (child != NULL) {
        vos3_dentry_t* found = ramfs_find_dentry_by_inode(child, inode);
        if (found != NULL) {
            return found;
        }
        child = child->next;
    }

    return NULL;
}

/**
 * @brief Find a child dentry by name under a parent dentry
 */
static vos3_dentry_t* ramfs_find_child(vos3_dentry_t* parent, const char* name)
{
    vos3_dentry_t* child = parent->children;
    while (child != NULL) {
        if (strcmp(child->name, name) == 0) {
            return child;
        }
        child = child->next;
    }
    return NULL;
}

/**
 * @brief Remove a child dentry from parent's children list (caller holds lock)
 */
static void ramfs_detach_child(vos3_dentry_t* parent, vos3_dentry_t* child)
{
    vos3_dentry_t** pp = &parent->children;
    while (*pp != NULL) {
        if (*pp == child) {
            *pp = child->next;
            child->next = NULL;
            return;
        }
        pp = &(*pp)->next;
    }
}

/**
 * @brief RAMFS rename — move/rename within the same ramfs mount
 *
 * Handles same-directory rename, cross-directory rename, and
 * overwrite of existing destination (POSIX semantics).
 */
static int ramfs_inode_rename(vos3_inode_t* old_dir, const char* old_name,
                               vos3_inode_t* new_dir, const char* new_name)
{
    if (old_dir == NULL || new_dir == NULL ||
        old_name == NULL || new_name == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    if (old_dir->sb == NULL || old_dir->sb->root == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Find parent dentries from inodes */
    vos3_dentry_t* root = old_dir->sb->root;
    vos3_dentry_t* old_parent = ramfs_find_dentry_by_inode(root, old_dir);
    vos3_dentry_t* new_parent = ramfs_find_dentry_by_inode(root, new_dir);

    if (old_parent == NULL || new_parent == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Find source dentry */
    vos3_dentry_t* old_dentry = ramfs_find_child(old_parent, old_name);
    if (old_dentry == NULL || old_dentry->inode == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Check if destination already exists */
    vos3_dentry_t* existing = ramfs_find_child(new_parent, new_name);
    if (existing != NULL) {
        /* Self-rename is a no-op (POSIX: rename(path, path) succeeds) */
        if (existing == old_dentry) {
            return VOS3_FS_OK;
        }

        if (existing->inode != NULL) {
            int src_is_dir = ((old_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR);
            int dst_is_dir = ((existing->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR);

            /* Cannot overwrite directory with file or vice versa */
            if (src_is_dir != dst_is_dir) {
                return src_is_dir ? VOS3_FS_ERR_NOTDIR : VOS3_FS_ERR_ISDIR;
            }

            /* Cannot overwrite non-empty directory */
            if (dst_is_dir && existing->children != NULL) {
                return VOS3_FS_ERR_NOTEMPTY;
            }

            /* Remove destination from parent */
            vos3_mutex_lock(&new_parent->lock);
            ramfs_detach_child(new_parent, existing);
            if (dst_is_dir) {
                new_parent->inode->nlink--;
            }
            vos3_mutex_unlock(&new_parent->lock);

            /* Free destination inode and dentry */
            if (existing->inode->nlink > 0U) {
                existing->inode->nlink--;
            }
            if (existing->inode->nlink == 0U &&
                existing->inode->ref_count <= 1U) {
                if (existing->inode->sb != NULL &&
                    existing->inode->sb->ops != NULL &&
                    existing->inode->sb->ops->free_inode != NULL) {
                    existing->inode->sb->ops->free_inode(existing->inode);
                }
                existing->inode = NULL;
            }
            vos3_mutex_destroy(&existing->lock);
            vos3_kfree(existing);
        }
    }

    /* Remove source from old parent */
    vos3_mutex_lock(&old_parent->lock);
    ramfs_detach_child(old_parent, old_dentry);
    if ((old_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
        old_parent->inode->nlink--;
    }
    vos3_mutex_unlock(&old_parent->lock);

    /* Update name */
    size_t len = strlen(new_name);
    if (len >= VOS3_NAME_MAX) {
        len = VOS3_NAME_MAX - 1U;
    }
    memcpy(old_dentry->name, new_name, len);
    old_dentry->name[len] = '\0';

    /* Add to new parent */
    vos3_mutex_lock(&new_parent->lock);
    old_dentry->parent = new_parent;
    old_dentry->next = new_parent->children;
    new_parent->children = old_dentry;
    if ((old_dentry->inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
        new_parent->inode->nlink++;
    }
    vos3_mutex_unlock(&new_parent->lock);

    return VOS3_FS_OK;
}

static int ramfs_inode_truncate(vos3_inode_t* inode, size_t size)
{
    if (inode == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    ramfs_inode_t* ri = (ramfs_inode_t*)inode->private_data;
    if (ri == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_mutex_lock(&inode->lock);

    if (size == 0U) {
        /* Free data */
        if (ri->data != NULL) {
            vos3_kfree(ri->data);
            ri->data = NULL;
            ri->capacity = 0U;
        }
        inode->size = 0U;
    } else if (size < inode->size) {
        /* Shrink */
        inode->size = size;
    } else if (size > inode->size) {
        /* Extend with zeros */
        if (size > ri->capacity) {
            size_t new_cap = size;
            uint8_t* new_data = (uint8_t*)vos3_kmalloc(new_cap);
            if (new_data == NULL) {
                vos3_mutex_unlock(&inode->lock);
                return VOS3_FS_ERR_NOMEM;
            }

            if (ri->data != NULL) {
                memcpy(new_data, ri->data, inode->size);
                vos3_kfree(ri->data);
            }

            memset(new_data + inode->size, 0, size - inode->size);
            ri->data = new_data;
            ri->capacity = new_cap;
        } else {
            memset(ri->data + inode->size, 0, size - inode->size);
        }
        inode->size = size;
    }

    vos3_mutex_unlock(&inode->lock);

    return VOS3_FS_OK;
}

/**
 * @brief setattr — update mode/uid/gid on in-memory inode
 */
static int ramfs_inode_setattr(vos3_inode_t* inode, uint32_t mode,
                                uint32_t uid, uint32_t gid, int which)
{
    if (inode == NULL)
        return VOS3_FS_ERR_INVAL;

    if (which & SETATTR_MODE)
        inode->mode = (inode->mode & VOS3_S_IFMT) | (mode & 07777U);
    if (which & SETATTR_UID)
        inode->uid = uid;
    if (which & SETATTR_GID)
        inode->gid = gid;

    return VOS3_FS_OK;
}

/**
 * @brief readlink — return symlink target stored in inode data buffer
 */
static int ramfs_inode_readlink(vos3_inode_t* inode, char* buf, size_t size)
{
    if (inode == NULL || buf == NULL || size == 0U)
        return VOS3_FS_ERR_INVAL;

    if ((inode->mode & VOS3_S_IFMT) != VOS3_S_IFLNK)
        return -22; /* EINVAL: not a symlink */

    ramfs_inode_t* ri = (ramfs_inode_t*)inode->private_data;
    if (ri == NULL || ri->data == NULL)
        return VOS3_FS_ERR_INVAL;

    /* readlink: no null terminator in result buffer */
    size_t copy_len = (inode->size < size) ? inode->size : size;
    memcpy(buf, ri->data, copy_len);
    return (int)copy_len;
}

/**
 * @brief symlink — create a symbolic link entry under dir
 */
static int ramfs_inode_symlink(vos3_inode_t* dir, const char* name,
                                const char* target)
{
    if (dir == NULL || name == NULL || target == NULL)
        return VOS3_FS_ERR_INVAL;

    if (dir->sb == NULL || dir->sb->root == NULL)
        return VOS3_FS_ERR_INVAL;

    size_t target_len = strlen(target);
    if (target_len == 0U)
        return VOS3_FS_ERR_INVAL;

    /* Find parent dentry from dir inode */
    vos3_dentry_t* parent = ramfs_find_dentry_by_inode(dir->sb->root, dir);
    if (parent == NULL)
        return VOS3_FS_ERR_NOENT;

    /* Check name not already taken */
    if (ramfs_find_child(parent, name) != NULL)
        return VOS3_FS_ERR_EXIST;

    /* Create symlink inode; store target in data buffer */
    vos3_inode_t* lnk = ramfs_create_inode(dir->sb, VOS3_S_IFLNK | 0777U);
    if (lnk == NULL)
        return VOS3_FS_ERR_NOMEM;

    ramfs_inode_t* ri = (ramfs_inode_t*)lnk->private_data;
    ri->data = (uint8_t*)vos3_kmalloc(target_len + 1U);
    if (ri->data == NULL) {
        ramfs_sb_free_inode(lnk);
        return VOS3_FS_ERR_NOMEM;
    }
    memcpy(ri->data, target, target_len);
    ri->data[target_len] = '\0';
    ri->capacity = target_len + 1U;
    lnk->size = target_len;

    /* Attach dentry */
    vos3_dentry_t* dentry = ramfs_create_dentry(parent, name, lnk);
    if (dentry == NULL) {
        ramfs_sb_free_inode(lnk);
        return VOS3_FS_ERR_NOMEM;
    }

    return VOS3_FS_OK;
}

/**
 * @brief link — create a hard link (new dentry pointing to existing inode)
 */
static int ramfs_inode_link(vos3_inode_t* old_inode, vos3_inode_t* dir,
                             const char* name)
{
    if (old_inode == NULL || dir == NULL || name == NULL)
        return VOS3_FS_ERR_INVAL;

    /* Directories cannot be hard-linked (POSIX) */
    if ((old_inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR)
        return VOS3_FS_ERR_ISDIR;

    if (dir->sb == NULL || dir->sb->root == NULL)
        return VOS3_FS_ERR_INVAL;

    /* Find parent dentry from dir inode */
    vos3_dentry_t* parent = ramfs_find_dentry_by_inode(dir->sb->root, dir);
    if (parent == NULL)
        return VOS3_FS_ERR_NOENT;

    /* Check name not already taken */
    if (ramfs_find_child(parent, name) != NULL)
        return VOS3_FS_ERR_EXIST;

    /* Create new dentry pointing to existing inode */
    vos3_dentry_t* dentry = ramfs_create_dentry(parent, name, old_inode);
    if (dentry == NULL)
        return VOS3_FS_ERR_NOMEM;

    /* Increment link count on the inode */
    old_inode->nlink++;
    old_inode->ref_count++;

    return VOS3_FS_OK;
}

/* ============================================================================
 * FILE OPERATIONS
 * ============================================================================ */

static int ramfs_file_open(vos3_file_t* file)
{
    (void)file;
    return VOS3_FS_OK;
}

static int ramfs_file_close(vos3_file_t* file)
{
    if (file == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Decrement inode reference (atomic for SMP safety) */
    if (file->inode != NULL) {
        __atomic_sub_fetch(&file->inode->ref_count, 1U, __ATOMIC_ACQ_REL);
    }

    /* Free file structure */
    vos3_mutex_destroy(&file->lock);
    vos3_kfree(file);

    return VOS3_FS_OK;
}

static int64_t ramfs_file_read(vos3_file_t* file, void* buf, size_t count)
{
    if (file == NULL || buf == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_inode_t* inode = file->inode;
    if (inode == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Check if directory */
    if ((inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
        return VOS3_FS_ERR_ISDIR;
    }

    ramfs_inode_t* ri = (ramfs_inode_t*)inode->private_data;
    if (ri == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_mutex_lock(&file->lock);
    vos3_mutex_lock(&inode->lock);

    /* Calculate bytes to read */
    size_t avail = 0U;
    if ((size_t)file->pos < inode->size) {
        avail = inode->size - (size_t)file->pos;
    }

    if (count > avail) {
        count = avail;
    }

    if (count > 0U && ri->data != NULL) {
        memcpy(buf, ri->data + file->pos, count);
        file->pos += (int64_t)count;
    }

    vos3_mutex_unlock(&inode->lock);
    vos3_mutex_unlock(&file->lock);

    return (int64_t)count;
}

static int64_t ramfs_file_write(vos3_file_t* file, const void* buf, size_t count)
{
    if (file == NULL || buf == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_inode_t* inode = file->inode;
    if (inode == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Check if directory */
    if ((inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR) {
        return VOS3_FS_ERR_ISDIR;
    }

    ramfs_inode_t* ri = (ramfs_inode_t*)inode->private_data;
    if (ri == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_mutex_lock(&file->lock);
    vos3_mutex_lock(&inode->lock);

    /* Handle append mode */
    if ((file->flags & VOS3_O_APPEND) != 0U) {
        file->pos = (int64_t)inode->size;
    }

    /* Calculate required size */
    size_t end_pos = (size_t)file->pos + count;

    if (end_pos > RAMFS_MAX_SIZE) {
        vos3_mutex_unlock(&inode->lock);
        vos3_mutex_unlock(&file->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Grow buffer if needed */
    if (end_pos > ri->capacity) {
        size_t new_cap = end_pos;
        /* Round up to reasonable chunk size */
        if (new_cap < 4096U) {
            new_cap = 4096U;
        } else {
            new_cap = (new_cap + 4095U) & ~4095ULL;
        }

        uint8_t* new_data = (uint8_t*)vos3_kmalloc(new_cap);
        if (new_data == NULL) {
            vos3_mutex_unlock(&inode->lock);
            vos3_mutex_unlock(&file->lock);
            return VOS3_FS_ERR_NOMEM;
        }

        if (ri->data != NULL) {
            memcpy(new_data, ri->data, inode->size);
            vos3_kfree(ri->data);
        }

        ri->data = new_data;
        ri->capacity = new_cap;
    }

    /* Zero-fill gap if writing past end */
    if ((size_t)file->pos > inode->size) {
        memset(ri->data + inode->size, 0, (size_t)file->pos - inode->size);
    }

    /* Write data */
    memcpy(ri->data + file->pos, buf, count);
    file->pos += (int64_t)count;

    if ((size_t)file->pos > inode->size) {
        inode->size = (size_t)file->pos;
    }

    vos3_mutex_unlock(&inode->lock);
    vos3_mutex_unlock(&file->lock);

    return (int64_t)count;
}

static int64_t ramfs_file_lseek(vos3_file_t* file, int64_t offset, int whence)
{
    if (file == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_mutex_lock(&file->lock);

    int64_t new_pos;
    switch (whence) {
        case VOS3_SEEK_SET:
            new_pos = offset;
            break;

        case VOS3_SEEK_CUR:
            new_pos = file->pos + offset;
            break;

        case VOS3_SEEK_END:
            if (file->inode != NULL) {
                new_pos = (int64_t)file->inode->size + offset;
            } else {
                new_pos = offset;
            }
            break;

        default:
            vos3_mutex_unlock(&file->lock);
            return VOS3_FS_ERR_INVAL;
    }

    if (new_pos < 0) {
        vos3_mutex_unlock(&file->lock);
        return VOS3_FS_ERR_INVAL;
    }

    file->pos = new_pos;

    vos3_mutex_unlock(&file->lock);

    return new_pos;
}

static int ramfs_file_readdir(vos3_file_t* file, vos3_dirent_t* dirents,
                               size_t count, size_t* out_count)
{
    if (file == NULL || dirents == NULL || out_count == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    if (file->dentry == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_mutex_lock(&file->lock);
    vos3_mutex_lock(&file->dentry->lock);

    size_t read_count = 0U;
    size_t cur_pos = (size_t)file->pos;

    /* Positions 0 and 1 are synthetic "." and ".." entries */
    if (cur_pos == 0U && read_count < count) {
        dirents[read_count].ino    = file->dentry->inode ? file->dentry->inode->ino : 0U;
        dirents[read_count].reclen = (uint16_t)sizeof(vos3_dirent_t);
        dirents[read_count].type   = VOS3_FT_DIR;
        memcpy(dirents[read_count].name, ".", 2);
        read_count++;
        cur_pos++;
        file->pos++;
    }
    if (cur_pos == 1U && read_count < count) {
        vos3_dentry_t* par = file->dentry->parent;
        dirents[read_count].ino    = (par && par->inode) ? par->inode->ino : 0U;
        dirents[read_count].reclen = (uint16_t)sizeof(vos3_dirent_t);
        dirents[read_count].type   = VOS3_FT_DIR;
        memcpy(dirents[read_count].name, "..", 3);
        read_count++;
        cur_pos++;
        file->pos++;
    }

    /* Skip to current real-child position (offset 2 = first real child) */
    vos3_dentry_t* child = file->dentry->children;
    size_t child_idx = 0U;
    while (child != NULL && (child_idx + 2U) < cur_pos) {
        child = child->next;
        child_idx++;
    }

    /* Read real entries */
    while (child != NULL && read_count < count) {
        dirents[read_count].ino = child->inode ? child->inode->ino : 0U;
        dirents[read_count].reclen = (uint16_t)sizeof(vos3_dirent_t);

        if (child->inode != NULL) {
            uint32_t mode = child->inode->mode & VOS3_S_IFMT;
            if (mode == VOS3_S_IFDIR) {
                dirents[read_count].type = VOS3_FT_DIR;
            } else if (mode == VOS3_S_IFLNK) {
                dirents[read_count].type = VOS3_FT_LNK;
            } else if (mode == VOS3_S_IFREG) {
                dirents[read_count].type = VOS3_FT_REG;
            } else {
                dirents[read_count].type = VOS3_FT_NONE;
            }
        } else {
            dirents[read_count].type = VOS3_FT_NONE;
        }

        size_t len = strlen(child->name);
        if (len >= VOS3_NAME_MAX) {
            len = VOS3_NAME_MAX - 1U;
        }
        memcpy(dirents[read_count].name, child->name, len);
        dirents[read_count].name[len] = '\0';

        child = child->next;
        read_count++;
        file->pos++;
    }

    *out_count = read_count;

    vos3_mutex_unlock(&file->dentry->lock);
    vos3_mutex_unlock(&file->lock);

    return VOS3_FS_OK;
}

/* ============================================================================
 * SUPERBLOCK OPERATIONS
 * ============================================================================ */

static vos3_inode_t* ramfs_sb_alloc_inode(vos3_superblock_t* sb)
{
    return ramfs_create_inode(sb, VOS3_S_IFREG | 0644U);
}

static void ramfs_sb_free_inode(vos3_inode_t* inode)
{
    if (inode == NULL) {
        return;
    }

    ramfs_inode_t* ri = (ramfs_inode_t*)inode->private_data;
    if (ri != NULL) {
        if (ri->data != NULL) {
            vos3_kfree(ri->data);
        }
        vos3_kfree(ri);
    }

    vos3_mutex_destroy(&inode->lock);
    vos3_kfree(inode);
}

/* ============================================================================
 * MOUNT/UNMOUNT
 * ============================================================================ */

static vos3_superblock_t* ramfs_mount(vos3_fs_type_t* fs, const char* source,
                                       uint32_t flags, void* data)
{
    (void)source;
    (void)flags;
    (void)data;

    /* Allocate superblock */
    vos3_superblock_t* sb = (vos3_superblock_t*)vos3_kzalloc(sizeof(vos3_superblock_t));
    if (sb == NULL) {
        return NULL;
    }

    sb->magic = RAMFS_MAGIC;
    sb->block_size = 4096U;
    sb->ops = &g_ramfs_sb_ops;
    sb->fs_type = fs;
    vos3_mutex_init(&sb->lock, "ramfs_sb");

    /* Create root inode */
    vos3_inode_t* root_inode = ramfs_create_inode(sb, VOS3_S_IFDIR | 0755U);
    if (root_inode == NULL) {
        vos3_kfree(sb);
        return NULL;
    }

    /* Create root dentry */
    vos3_dentry_t* root_dentry = ramfs_create_dentry(NULL, "", root_inode);
    if (root_dentry == NULL) {
        ramfs_sb_free_inode(root_inode);
        vos3_kfree(sb);
        return NULL;
    }

    sb->root = root_dentry;

    VOS3_DEBUG("RAMFS mounted (sb=%p, root=%p)", (void*)sb, (void*)root_dentry);

    return sb;
}

/**
 * @brief Recursively free all inodes and dentries in the ramfs tree
 *
 * Walks the dentry tree depth-first: for each dentry, recurse into children
 * first, then free the inode (including its ramfs_inode_t data), then free the
 * dentry itself. This ensures no memory is leaked on unmount.
 */
static void ramfs_free_dentry_tree(vos3_dentry_t* dentry)
{
    if (dentry == NULL) {
        return;
    }

    /* Recurse into children first (depth-first) */
    vos3_dentry_t* child = dentry->children;
    while (child != NULL) {
        vos3_dentry_t* next_child = child->next;
        ramfs_free_dentry_tree(child);
        child = next_child;
    }
    dentry->children = NULL;

    /* Free the inode attached to this dentry */
    if (dentry->inode != NULL) {
        ramfs_sb_free_inode(dentry->inode);
        dentry->inode = NULL;
    }

    /* Free the dentry itself */
    vos3_mutex_destroy(&dentry->lock);
    vos3_kfree(dentry);
}

static int ramfs_unmount(vos3_superblock_t* sb)
{
    if (sb == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Recursively free all inodes and dentries starting from root */
    if (sb->root != NULL) {
        ramfs_free_dentry_tree(sb->root);
        sb->root = NULL;
    }

    vos3_mutex_destroy(&sb->lock);
    vos3_kfree(sb);

    return VOS3_FS_OK;
}

/* ============================================================================
 * FILE SYSTEM TYPE
 * ============================================================================ */

vos3_fs_type_t g_ramfs_type = {
    .name    = "ramfs",
    .flags   = 0U,
    .mount   = ramfs_mount,
    .unmount = ramfs_unmount,
    .next    = NULL,
};
