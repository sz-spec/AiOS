/**
 * @file vfs.c
 * @brief VOS3 Virtual File System Core
 *
 * @details VFS layer implementation.
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
#include "../../include/vos/vos3fs.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/task.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * VFS STATE
 * ============================================================================ */

/** @brief Registered file systems */
static vos3_fs_type_t* g_fs_types = NULL;

/** @brief Mount list */
static vos3_mount_t* g_mounts = NULL;

/** @brief Root dentry */
static vos3_dentry_t* g_root_dentry = NULL;

/** @brief VFS lock */
static vos3_spinlock_t g_vfs_lock = VOS3_SPINLOCK_INIT;

/** @brief VFS initialized flag */
static int g_vfs_initialized = 0;

/* Forward declarations for ramfs */
extern vos3_fs_type_t g_ramfs_type;

/* Forward declarations for procfs */
extern int vos3_procfs_init(void);
extern int vos3_procfs_mount(void);

/* ============================================================================
 * PATH HELPERS
 * ============================================================================ */

/**
 * @brief Skip leading slashes
 */
static const char* skip_slashes(const char* path)
{
    while (*path == '/') {
        path++;
    }
    return path;
}

/**
 * @brief Get next path component
 */
static const char* next_component(const char* path, char* name, size_t max_len)
{
    size_t i = 0U;

    path = skip_slashes(path);

    while (*path != '\0' && *path != '/' && i < max_len - 1U) {
        name[i++] = *path++;
    }
    name[i] = '\0';

    return path;
}

/* ============================================================================
 * DENTRY OPERATIONS
 * ============================================================================ */

/**
 * @brief Allocate dentry
 */
static vos3_dentry_t* dentry_alloc(const char* name)
{
    vos3_dentry_t* dentry = (vos3_dentry_t*)vos3_kzalloc(sizeof(vos3_dentry_t));
    if (dentry == NULL) {
        return NULL;
    }

    if (name != NULL) {
        size_t len = strlen(name);
        if (len >= VOS3_NAME_MAX) {
            len = VOS3_NAME_MAX - 1U;
        }
        memcpy(dentry->name, name, len);
        dentry->name[len] = '\0';
    }

    dentry->ref_count = 1U;
    vos3_mutex_init(&dentry->lock, "dentry");

    return dentry;
}

/**
 * @brief Get dentry reference
 */
static void dentry_get(vos3_dentry_t* dentry)
{
    if (dentry != NULL) {
        dentry->ref_count++;
    }
}

/**
 * @brief Put dentry reference
 */
static void dentry_put(vos3_dentry_t* dentry)
{
    if (dentry == NULL) {
        return;
    }

    if (dentry->ref_count > 0U) {
        dentry->ref_count--;
    }

    if (dentry->ref_count == 0U) {
        vos3_mutex_destroy(&dentry->lock);
        vos3_kfree(dentry);
    }
}

/**
 * @brief Add child to directory dentry
 */
static void dentry_add_child(vos3_dentry_t* parent, vos3_dentry_t* child)
{
    if (parent == NULL || child == NULL) {
        return;
    }

    vos3_mutex_lock(&parent->lock);

    child->parent = parent;
    child->next = parent->children;
    parent->children = child;
    dentry_get(child);

    vos3_mutex_unlock(&parent->lock);
}

/**
 * @brief Remove child from directory dentry
 */
static void dentry_remove_child(vos3_dentry_t* parent, vos3_dentry_t* child)
{
    if (parent == NULL || child == NULL) {
        return;
    }

    vos3_mutex_lock(&parent->lock);

    vos3_dentry_t** pp = &parent->children;
    while (*pp != NULL) {
        if (*pp == child) {
            *pp = child->next;
            child->next = NULL;
            child->parent = NULL;
            dentry_put(child);
            break;
        }
        pp = &(*pp)->next;
    }

    vos3_mutex_unlock(&parent->lock);
}

/**
 * @brief Find child by name
 */
static vos3_dentry_t* dentry_find_child(vos3_dentry_t* parent, const char* name)
{
    if (parent == NULL || name == NULL) {
        return NULL;
    }

    vos3_mutex_lock(&parent->lock);

    vos3_dentry_t* child = parent->children;
    while (child != NULL) {
        if (strcmp(child->name, name) == 0) {
            dentry_get(child);
            vos3_mutex_unlock(&parent->lock);
            return child;
        }
        child = child->next;
    }

    vos3_mutex_unlock(&parent->lock);
    return NULL;
}

/**
 * @brief Check if a dentry is a mount point and cross into the mounted FS.
 *
 * If a mount exists whose mountpoint == dentry, returns the root dentry
 * of the mounted filesystem (with ref incremented). Otherwise returns NULL.
 */
static vos3_dentry_t* check_mount_crossing(vos3_dentry_t* dentry)
{
    vos3_spinlock_lock(&g_vfs_lock);

    vos3_mount_t* mnt = g_mounts;
    while (mnt != NULL) {
        if (mnt->mountpoint == dentry && mnt->sb != NULL && mnt->sb->root != NULL) {
            vos3_dentry_t* root = mnt->sb->root;
            dentry_get(root);
            vos3_spinlock_unlock(&g_vfs_lock);
            return root;
        }
        mnt = mnt->next;
    }

    vos3_spinlock_unlock(&g_vfs_lock);
    return NULL;
}

/* ============================================================================
 * FILE SYSTEM REGISTRATION
 * ============================================================================ */

int vos3_fs_register(vos3_fs_type_t* fs)
{
    if (fs == NULL || fs->name == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_spinlock_lock(&g_vfs_lock);

    /* Check for duplicate */
    vos3_fs_type_t* p = g_fs_types;
    while (p != NULL) {
        if (strcmp(p->name, fs->name) == 0) {
            vos3_spinlock_unlock(&g_vfs_lock);
            return VOS3_FS_ERR_EXIST;
        }
        p = p->next;
    }

    /* Add to list */
    fs->next = g_fs_types;
    g_fs_types = fs;

    vos3_spinlock_unlock(&g_vfs_lock);

    VOS3_DEBUG("Registered file system '%s'", fs->name);

    return VOS3_FS_OK;
}

int vos3_fs_unregister(vos3_fs_type_t* fs)
{
    if (fs == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_spinlock_lock(&g_vfs_lock);

    vos3_fs_type_t** pp = &g_fs_types;
    while (*pp != NULL) {
        if (*pp == fs) {
            *pp = fs->next;
            fs->next = NULL;
            vos3_spinlock_unlock(&g_vfs_lock);
            VOS3_DEBUG("Unregistered file system '%s'", fs->name);
            return VOS3_FS_OK;
        }
        pp = &(*pp)->next;
    }

    vos3_spinlock_unlock(&g_vfs_lock);
    return VOS3_FS_ERR_NOENT;
}

/**
 * @brief Find file system type by name
 */
static vos3_fs_type_t* fs_find(const char* name)
{
    vos3_spinlock_lock(&g_vfs_lock);

    vos3_fs_type_t* fs = g_fs_types;
    while (fs != NULL) {
        if (strcmp(fs->name, name) == 0) {
            vos3_spinlock_unlock(&g_vfs_lock);
            return fs;
        }
        fs = fs->next;
    }

    vos3_spinlock_unlock(&g_vfs_lock);
    return NULL;
}

/* ============================================================================
 * MOUNT OPERATIONS
 * ============================================================================ */

int vos3_mount(const char* source, const char* target,
               const char* fstype, uint32_t flags, void* data)
{
    if (target == NULL || fstype == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Find file system type */
    vos3_fs_type_t* fs = fs_find(fstype);
    if (fs == NULL) {
        VOS3_ERROR("Unknown file system type '%s'", fstype);
        return VOS3_FS_ERR_NOENT;
    }

    /* Mount */
    if (fs->mount == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_superblock_t* sb = fs->mount(fs, source, flags, data);
    if (sb == NULL) {
        return VOS3_FS_ERR_IO;
    }

    /* Create mount structure */
    vos3_mount_t* mnt = (vos3_mount_t*)vos3_kzalloc(sizeof(vos3_mount_t));
    if (mnt == NULL) {
        if (fs->unmount != NULL) {
            (void)fs->unmount(sb);
        }
        return VOS3_FS_ERR_NOMEM;
    }

    mnt->sb = sb;
    mnt->flags = flags;

    size_t len = strlen(target);
    if (len >= VOS3_PATH_MAX) {
        len = VOS3_PATH_MAX - 1U;
    }
    memcpy(mnt->path, target, len);
    mnt->path[len] = '\0';

    /* Lookup mount point BEFORE acquiring g_vfs_lock to avoid deadlock
     * (path_lookup -> check_mount_crossing also acquires g_vfs_lock) */
    vos3_dentry_t* mountpoint = NULL;
    int is_root = (strcmp(target, "/") == 0);
    if (!is_root) {
        int result = vos3_path_lookup(target, &mountpoint);
        if (result != VOS3_FS_OK) {
            vos3_kfree(mnt);
            if (fs->unmount != NULL) {
                (void)fs->unmount(sb);
            }
            return result;
        }
    }

    /* Add to mount list */
    vos3_spinlock_lock(&g_vfs_lock);

    if (is_root && g_root_dentry == NULL) {
        g_root_dentry = sb->root;
        dentry_get(g_root_dentry);
    } else {
        mnt->mountpoint = mountpoint;
    }

    mnt->next = g_mounts;
    g_mounts = mnt;

    vos3_spinlock_unlock(&g_vfs_lock);

    VOS3_INFO("Mounted '%s' on '%s' (type=%s)", source ? source : "none",
              target, fstype);

    return VOS3_FS_OK;
}

int vos3_umount(const char* target)
{
    if (target == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_spinlock_lock(&g_vfs_lock);

    vos3_mount_t** pp = &g_mounts;
    while (*pp != NULL) {
        if (strcmp((*pp)->path, target) == 0) {
            vos3_mount_t* mnt = *pp;
            *pp = mnt->next;

            vos3_spinlock_unlock(&g_vfs_lock);

            /* Unmount */
            if (mnt->sb->fs_type->unmount != NULL) {
                (void)mnt->sb->fs_type->unmount(mnt->sb);
            }

            if (mnt->mountpoint != NULL) {
                dentry_put(mnt->mountpoint);
            }

            vos3_kfree(mnt);

            VOS3_INFO("Unmounted '%s'", target);
            return VOS3_FS_OK;
        }
        pp = &(*pp)->next;
    }

    vos3_spinlock_unlock(&g_vfs_lock);
    return VOS3_FS_ERR_NOENT;
}

/* ============================================================================
 * PATH LOOKUP
 * ============================================================================ */

int vos3_path_lookup(const char* path, vos3_dentry_t** out_dentry)
{
    if (path == NULL || out_dentry == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    if (g_root_dentry == NULL) {
        return VOS3_FS_ERR_NOENT;
    }

    /* Start at root or current directory */
    vos3_dentry_t* dentry;
    if (path[0] == '/') {
        dentry = g_root_dentry;
        path++;
    } else {
        /* TODO: Get current working directory from task */
        dentry = g_root_dentry;
    }

    dentry_get(dentry);

    /* Traverse path */
    char name[VOS3_NAME_MAX];
    while (*path != '\0') {
        path = next_component(path, name, sizeof(name));

        if (name[0] == '\0') {
            continue;
        }

        /* Handle . and .. */
        if (strcmp(name, ".") == 0) {
            continue;
        }

        if (strcmp(name, "..") == 0) {
            if (dentry->parent != NULL) {
                vos3_dentry_t* parent = dentry->parent;
                dentry_get(parent);
                dentry_put(dentry);
                dentry = parent;
            }
            continue;
        }

        /* Look up in inode */
        if (dentry->inode == NULL || dentry->inode->ops == NULL ||
            dentry->inode->ops->lookup == NULL) {
            dentry_put(dentry);
            return VOS3_FS_ERR_NOTDIR;
        }

        /* Check in-memory dentry cache first (avoids disk I/O) */
        int no_dcache = (dentry->inode->sb != NULL &&
                         dentry->inode->sb->fs_type != NULL &&
                         (dentry->inode->sb->fs_type->flags & VOS3_FS_NO_DCACHE));
        vos3_dentry_t* child = no_dcache ? NULL : dentry_find_child(dentry, name);
        if (child == NULL) {
            /* Cache miss — go to filesystem (disk I/O) */
            child = dentry->inode->ops->lookup(dentry->inode, name);
            if (child == NULL) {
                dentry_put(dentry);
                return VOS3_FS_ERR_NOENT;
            }
            /* Populate cache for future lookups (skip virtual FS) */
            if (!no_dcache) {
                dentry_add_child(dentry, child);
            }
        }

        dentry_put(dentry);
        dentry = child;

        /* Check if this dentry is a mount point and cross into it */
        vos3_dentry_t* mounted_root = check_mount_crossing(dentry);
        if (mounted_root != NULL) {
            dentry_put(dentry);
            dentry = mounted_root;
        }
    }

    *out_dentry = dentry;
    return VOS3_FS_OK;
}

int vos3_path_parent(const char* path, vos3_dentry_t** out_parent, char* name)
{
    if (path == NULL || out_parent == NULL || name == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Find last component */
    const char* last_slash = NULL;
    const char* p = path;
    while (*p != '\0') {
        if (*p == '/') {
            last_slash = p;
        }
        p++;
    }

    if (last_slash == NULL) {
        /* No slash - parent is cwd, name is whole path */
        *out_parent = g_root_dentry;
        dentry_get(*out_parent);
        size_t len = strlen(path);
        if (len >= VOS3_NAME_MAX) {
            return VOS3_FS_ERR_NAMETOOLONG;
        }
        memcpy(name, path, len + 1U);
        return VOS3_FS_OK;
    }

    /* Copy last component to name */
    const char* basename = last_slash + 1;
    size_t len = strlen(basename);
    if (len >= VOS3_NAME_MAX) {
        return VOS3_FS_ERR_NAMETOOLONG;
    }
    memcpy(name, basename, len + 1U);

    /* Lookup parent */
    if (last_slash == path) {
        /* Parent is root */
        *out_parent = g_root_dentry;
        dentry_get(*out_parent);
        return VOS3_FS_OK;
    }

    /* Create temporary path for parent */
    size_t parent_len = (size_t)(last_slash - path);
    char parent_path[VOS3_PATH_MAX];
    if (parent_len >= VOS3_PATH_MAX) {
        return VOS3_FS_ERR_NAMETOOLONG;
    }
    memcpy(parent_path, path, parent_len);
    parent_path[parent_len] = '\0';

    return vos3_path_lookup(parent_path, out_parent);
}

/* ============================================================================
 * VFS INITIALIZATION
 * ============================================================================ */

int vos3_vfs_init(void)
{
    if (g_vfs_initialized != 0) {
        return VOS3_FS_ERR_EXIST;
    }

    VOS3_INFO("Initializing Virtual File System");

    /* Register ramfs */
    int result = vos3_fs_register(&g_ramfs_type);
    if (result != VOS3_FS_OK) {
        VOS3_ERROR("Failed to register ramfs (error %d)", result);
        return result;
    }

    /* Mount root filesystem */
    result = vos3_mount(NULL, "/", "ramfs", 0U, NULL);
    if (result != VOS3_FS_OK) {
        VOS3_ERROR("Failed to mount root filesystem (error %d)", result);
        return result;
    }

    /* Create standard directories */
    (void)vos3_mkdir("/dev", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                             VOS3_S_IROTH | VOS3_S_IXOTH);
    (void)vos3_mkdir("/tmp", VOS3_S_IRWXU | VOS3_S_IRWXG | VOS3_S_IRWXO);
    (void)vos3_mkdir("/proc", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                              VOS3_S_IROTH | VOS3_S_IXOTH);

    /* Register and mount procfs */
    result = vos3_procfs_init();
    if (result == VOS3_FS_OK) {
        result = vos3_procfs_mount();
        if (result != VOS3_FS_OK) {
            VOS3_WARN("Failed to mount procfs (error %d)", result);
        }
    } else {
        VOS3_WARN("Failed to register procfs (error %d)", result);
    }

    /* Sprint 15 / Item K4 — initialize the vBus FS provenance side table.
     * This is the kernel-side counterpart to backend's MAIF format; every
     * artifact crossing a vOS filesystem boundary can be tagged with
     * SHA-256 + signer fingerprint + producer slot. */
    {
        extern int vos3_vbus_fs_init(void);
        int prov_rc = vos3_vbus_fs_init();
        if (prov_rc != 0) {
            VOS3_WARN("vBus FS provenance table init failed (rc=%d)", prov_rc);
        }
    }

    g_vfs_initialized = 1;

    VOS3_INFO("VFS initialized");
    VOS3_INFO("  Root filesystem: ramfs");
    VOS3_INFO("  Max open files per task: %u", VOS3_MAX_FD);

    return VOS3_FS_OK;
}

/* ============================================================================
 * STATFS
 * ============================================================================ */

int vos3_statfs(const char* path, vos3_statfs_t* buf)
{
    if (path == NULL || buf == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    memset(buf, 0, sizeof(*buf));

    vos3_spinlock_lock(&g_vfs_lock);

    /* Walk mount list to find matching mount point */
    vos3_mount_t* mnt = g_mounts;
    vos3_mount_t* best = NULL;
    size_t best_len = 0;

    while (mnt != NULL) {
        size_t mlen = strlen(mnt->path);
        if (mlen <= strlen(path) && memcmp(mnt->path, path, mlen) == 0) {
            if (mlen > best_len) {
                best = mnt;
                best_len = mlen;
            }
        }
        mnt = mnt->next;
    }

    if (best == NULL || best->sb == NULL) {
        vos3_spinlock_unlock(&g_vfs_lock);
        return VOS3_FS_ERR_NOENT;
    }

    vos3_superblock_t* sb = best->sb;

    buf->f_type = sb->magic;
    buf->f_bsize = sb->block_size;
    buf->f_blocks = sb->block_count;
    buf->f_bfree = sb->free_blocks;
    buf->f_files = sb->inode_count;
    buf->f_ffree = sb->free_inodes;

    /* Copy FS type name */
    if (best->sb->fs_type != NULL && best->sb->fs_type->name != NULL) {
        size_t nlen = strlen(best->sb->fs_type->name);
        if (nlen >= sizeof(buf->f_fstype)) nlen = sizeof(buf->f_fstype) - 1;
        memcpy(buf->f_fstype, best->sb->fs_type->name, nlen);
    }

    /* Read live data from vos3fs in-memory superblock (the VFS sb->free_blocks
     * is only set at mount time and may be stale after file operations) */
    if (sb->magic == VOS3FS_MAGIC && sb->private_data != NULL) {
        vos3fs_info_t* info = (vos3fs_info_t*)sb->private_data;
        buf->f_bfree = info->dsb.free_blocks;
        buf->f_ffree = info->dsb.free_inodes;
        buf->f_mount_count = info->dsb.mount_count;
    }

    vos3_spinlock_unlock(&g_vfs_lock);
    return VOS3_FS_OK;
}
