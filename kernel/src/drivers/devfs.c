/**
 * @file devfs.c
 * @brief VOS3 Device Filesystem
 *
 * @details Virtual filesystem for /dev device nodes.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/device.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * DEVFS STATE
 * ============================================================================ */

/** @brief Devfs inode data */
typedef struct devfs_inode_data {
    vos3_device_t* device;      /**< Associated device */
} devfs_inode_data_t;

/** @brief Devfs initialized flag */
static int g_devfs_initialized = 0;

/** @brief /dev dentry */
static vos3_dentry_t* g_dev_dentry = NULL;

/* ============================================================================
 * DEVFS FILE OPERATIONS
 * ============================================================================ */

static int devfs_file_open(vos3_file_t* file)
{
    if (file == NULL || file->inode == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    devfs_inode_data_t* data = (devfs_inode_data_t*)file->inode->private_data;
    if (data == NULL || data->device == NULL) {
        return VOS3_DEV_ERR_NODEV;
    }

    return vos3_dev_open(data->device, file);
}

static int devfs_file_close(vos3_file_t* file)
{
    if (file == NULL || file->inode == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    devfs_inode_data_t* data = (devfs_inode_data_t*)file->inode->private_data;
    if (data == NULL || data->device == NULL) {
        return VOS3_DEV_OK;  /* Already closed */
    }

    return vos3_dev_close(data->device, file);
}

static int64_t devfs_file_read(vos3_file_t* file, void* buf, size_t count)
{
    if (file == NULL || file->inode == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    devfs_inode_data_t* data = (devfs_inode_data_t*)file->inode->private_data;
    if (data == NULL || data->device == NULL) {
        return VOS3_DEV_ERR_NODEV;
    }

    return vos3_dev_read(data->device, file, buf, count);
}

static int64_t devfs_file_write(vos3_file_t* file, const void* buf, size_t count)
{
    if (file == NULL || file->inode == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    devfs_inode_data_t* data = (devfs_inode_data_t*)file->inode->private_data;
    if (data == NULL || data->device == NULL) {
        return VOS3_DEV_ERR_NODEV;
    }

    return vos3_dev_write(data->device, file, buf, count);
}

static int64_t devfs_file_lseek(vos3_file_t* file, int64_t offset, int whence)
{
    /* Most char devices don't support seeking */
    (void)file;
    (void)offset;
    (void)whence;
    return VOS3_DEV_ERR_INVAL;
}

static int devfs_file_ioctl(vos3_file_t* file, uint32_t cmd, void* arg)
{
    if (file == NULL || file->inode == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    devfs_inode_data_t* data = (devfs_inode_data_t*)file->inode->private_data;
    if (data == NULL || data->device == NULL) {
        return VOS3_DEV_ERR_NODEV;
    }

    return vos3_dev_ioctl(data->device, file, cmd, arg);
}

const vos3_file_ops_t g_devfs_file_ops = {
    .open    = devfs_file_open,
    .close   = devfs_file_close,
    .read    = devfs_file_read,
    .write   = devfs_file_write,
    .lseek   = devfs_file_lseek,
    .readdir = NULL,
    .fsync   = NULL,
    .ioctl   = devfs_file_ioctl,
};

/**
 * @brief Get devfs file operations
 * @return Pointer to devfs file operations structure
 */
const vos3_file_ops_t* vos3_devfs_get_ops(void)
{
    return &g_devfs_file_ops;
}

/* ============================================================================
 * DEVFS FUNCTIONS
 * ============================================================================ */

int vos3_devfs_init(void)
{
    if (g_devfs_initialized != 0) {
        return VOS3_DEV_ERR_EXIST;
    }

    /* Lookup /dev directory */
    int result = vos3_path_lookup("/dev", &g_dev_dentry);
    if (result != VOS3_FS_OK) {
        VOS3_ERROR("Failed to find /dev directory");
        return result;
    }

    g_devfs_initialized = 1;

    VOS3_DEBUG("Devfs initialized at /dev");

    return VOS3_DEV_OK;
}

int vos3_devfs_create(const char* name, vos3_device_t* dev)
{
    if (name == NULL || dev == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (g_dev_dentry == NULL) {
        return VOS3_DEV_ERR_NODEV;
    }

    /* Create inode data */
    devfs_inode_data_t* idata = (devfs_inode_data_t*)vos3_kzalloc(sizeof(devfs_inode_data_t));
    if (idata == NULL) {
        return VOS3_DEV_ERR_NOMEM;
    }
    idata->device = dev;

    /* Create inode */
    vos3_inode_t* inode = (vos3_inode_t*)vos3_kzalloc(sizeof(vos3_inode_t));
    if (inode == NULL) {
        vos3_kfree(idata);
        return VOS3_DEV_ERR_NOMEM;
    }

    static uint32_t devfs_ino = 0x10000U;
    inode->ino = devfs_ino++;
    inode->mode = (dev->type == VOS3_DEV_CHAR) ? VOS3_S_IFCHR : VOS3_S_IFBLK;
    inode->mode |= 0666U;  /* rw-rw-rw- */
    inode->nlink = 1U;
    inode->ref_count = 1U;
    inode->private_data = idata;
    inode->sb = g_dev_dentry->inode->sb;
    vos3_mutex_init(&inode->lock, "devfs_inode");

    /* Create dentry */
    vos3_dentry_t* dentry = (vos3_dentry_t*)vos3_kzalloc(sizeof(vos3_dentry_t));
    if (dentry == NULL) {
        vos3_kfree(inode);
        vos3_kfree(idata);
        return VOS3_DEV_ERR_NOMEM;
    }

    size_t len = strlen(name);
    if (len >= VOS3_NAME_MAX) {
        len = VOS3_NAME_MAX - 1U;
    }
    memcpy(dentry->name, name, len);
    dentry->name[len] = '\0';
    dentry->inode = inode;
    dentry->ref_count = 1U;
    vos3_mutex_init(&dentry->lock, "devfs_dentry");

    /* Add to /dev */
    dentry->parent = g_dev_dentry;
    vos3_mutex_lock(&g_dev_dentry->lock);
    dentry->next = g_dev_dentry->children;
    g_dev_dentry->children = dentry;
    vos3_mutex_unlock(&g_dev_dentry->lock);

    VOS3_DEBUG("Created device node /dev/%s", name);

    return VOS3_DEV_OK;
}

int vos3_devfs_remove(const char* name)
{
    if (name == NULL || g_dev_dentry == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    vos3_mutex_lock(&g_dev_dentry->lock);

    vos3_dentry_t** pp = &g_dev_dentry->children;
    while (*pp != NULL) {
        if (strcmp((*pp)->name, name) == 0) {
            vos3_dentry_t* dentry = *pp;
            *pp = dentry->next;

            vos3_mutex_unlock(&g_dev_dentry->lock);

            /* Free inode data */
            if (dentry->inode != NULL) {
                if (dentry->inode->private_data != NULL) {
                    vos3_kfree(dentry->inode->private_data);
                }
                vos3_mutex_destroy(&dentry->inode->lock);
                vos3_kfree(dentry->inode);
            }

            vos3_mutex_destroy(&dentry->lock);
            vos3_kfree(dentry);

            VOS3_DEBUG("Removed device node /dev/%s", name);
            return VOS3_DEV_OK;
        }
        pp = &(*pp)->next;
    }

    vos3_mutex_unlock(&g_dev_dentry->lock);
    return VOS3_DEV_ERR_NODEV;
}
