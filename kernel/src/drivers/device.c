/**
 * @file device.c
 * @brief VOS3 Device Subsystem Core
 *
 * @details Device registration and management.
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
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * DEVICE STATE
 * ============================================================================ */

/** @brief Registered devices by major number */
static vos3_device_t* g_devices[VOS3_DEV_MAJOR_MAX][VOS3_DEV_MINOR_MAX];

/** @brief Device list (for iteration) */
static vos3_device_t* g_device_list = NULL;

/** @brief Device lock */
static vos3_spinlock_t g_dev_lock = VOS3_SPINLOCK_INIT;

/** @brief Device subsystem initialized */
static int g_dev_initialized = 0;

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

int vos3_dev_init(void)
{
    if (g_dev_initialized != 0) {
        return VOS3_DEV_ERR_EXIST;
    }

    VOS3_INFO("Initializing device subsystem");

    /* Clear device table */
    for (uint32_t major = 0U; major < VOS3_DEV_MAJOR_MAX; major++) {
        for (uint32_t minor = 0U; minor < VOS3_DEV_MINOR_MAX; minor++) {
            g_devices[major][minor] = NULL;
        }
    }

    g_device_list = NULL;
    g_dev_initialized = 1;

    VOS3_INFO("Device subsystem initialized");
    VOS3_INFO("  Max devices: %u", VOS3_MAX_DEVICES);
    VOS3_INFO("  Major range: 0-%u", VOS3_DEV_MAJOR_MAX - 1U);

    return VOS3_DEV_OK;
}

/* ============================================================================
 * DEVICE REGISTRATION
 * ============================================================================ */

vos3_device_t* vos3_cdev_register(const char* name, vos3_dev_t devno,
                                   const vos3_char_ops_t* ops,
                                   void* private_data)
{
    if (name == NULL || ops == NULL) {
        return NULL;
    }

    uint32_t major = VOS3_MAJOR(devno);
    uint32_t minor = VOS3_MINOR(devno);

    if (major >= VOS3_DEV_MAJOR_MAX || minor >= VOS3_DEV_MINOR_MAX) {
        VOS3_ERROR("Invalid device number %u:%u", major, minor);
        return NULL;
    }

    vos3_spinlock_lock(&g_dev_lock);

    /* Check if already registered */
    if (g_devices[major][minor] != NULL) {
        vos3_spinlock_unlock(&g_dev_lock);
        VOS3_ERROR("Device %u:%u already registered", major, minor);
        return NULL;
    }

    /* Allocate device structure */
    vos3_device_t* dev = (vos3_device_t*)vos3_kzalloc(sizeof(vos3_device_t));
    if (dev == NULL) {
        vos3_spinlock_unlock(&g_dev_lock);
        return NULL;
    }

    /* Initialize device */
    dev->magic = VOS3_DEV_MAGIC;
    dev->devno = devno;
    dev->type = VOS3_DEV_CHAR;
    dev->ops.char_ops = ops;
    dev->ref_count = 0U;
    dev->private_data = private_data;

    size_t len = strlen(name);
    if (len >= VOS3_DEV_NAME_MAX) {
        len = VOS3_DEV_NAME_MAX - 1U;
    }
    memcpy(dev->name, name, len);
    dev->name[len] = '\0';

    vos3_mutex_init(&dev->lock, "cdev");

    /* Add to table */
    g_devices[major][minor] = dev;

    /* Add to list */
    dev->next = g_device_list;
    g_device_list = dev;

    vos3_spinlock_unlock(&g_dev_lock);

    VOS3_DEBUG("Registered char device '%s' (%u:%u)", name, major, minor);

    return dev;
}

vos3_device_t* vos3_bdev_register(const char* name, vos3_dev_t devno,
                                   const vos3_block_ops_t* ops,
                                   uint64_t sectors, uint32_t sector_size,
                                   void* private_data)
{
    if (name == NULL || ops == NULL) {
        return NULL;
    }

    uint32_t major = VOS3_MAJOR(devno);
    uint32_t minor = VOS3_MINOR(devno);

    if (major >= VOS3_DEV_MAJOR_MAX || minor >= VOS3_DEV_MINOR_MAX) {
        VOS3_ERROR("Invalid device number %u:%u", major, minor);
        return NULL;
    }

    vos3_spinlock_lock(&g_dev_lock);

    /* Check if already registered */
    if (g_devices[major][minor] != NULL) {
        vos3_spinlock_unlock(&g_dev_lock);
        VOS3_ERROR("Device %u:%u already registered", major, minor);
        return NULL;
    }

    /* Allocate block device structure */
    vos3_block_device_t* bdev = (vos3_block_device_t*)vos3_kzalloc(sizeof(vos3_block_device_t));
    if (bdev == NULL) {
        vos3_spinlock_unlock(&g_dev_lock);
        return NULL;
    }

    vos3_device_t* dev = &bdev->base;

    /* Initialize device */
    dev->magic = VOS3_DEV_MAGIC;
    dev->devno = devno;
    dev->type = VOS3_DEV_BLOCK;
    dev->ops.block_ops = ops;
    dev->ref_count = 0U;
    dev->private_data = private_data;

    size_t len = strlen(name);
    if (len >= VOS3_DEV_NAME_MAX) {
        len = VOS3_DEV_NAME_MAX - 1U;
    }
    memcpy(dev->name, name, len);
    dev->name[len] = '\0';

    vos3_mutex_init(&dev->lock, "bdev");

    /* Initialize block device fields */
    bdev->sectors = sectors;
    bdev->sector_size = sector_size;
    bdev->max_sectors = 256U;  /* Default max */
    bdev->request_queue = NULL;
    vos3_mutex_init(&bdev->queue_lock, "bdev_queue");

    /* Add to table */
    g_devices[major][minor] = dev;

    /* Add to list */
    dev->next = g_device_list;
    g_device_list = dev;

    vos3_spinlock_unlock(&g_dev_lock);

    VOS3_DEBUG("Registered block device '%s' (%u:%u, %llu sectors)",
               name, major, minor, (unsigned long long)sectors);

    return dev;
}

int vos3_dev_unregister(vos3_device_t* dev)
{
    if (dev == NULL || dev->magic != VOS3_DEV_MAGIC) {
        return VOS3_DEV_ERR_INVAL;
    }

    uint32_t major = VOS3_MAJOR(dev->devno);
    uint32_t minor = VOS3_MINOR(dev->devno);

    vos3_spinlock_lock(&g_dev_lock);

    /* Check reference count */
    if (dev->ref_count > 0U) {
        vos3_spinlock_unlock(&g_dev_lock);
        return VOS3_DEV_ERR_BUSY;
    }

    /* Remove from table */
    g_devices[major][minor] = NULL;

    /* Remove from list */
    vos3_device_t** pp = &g_device_list;
    while (*pp != NULL) {
        if (*pp == dev) {
            *pp = dev->next;
            break;
        }
        pp = &(*pp)->next;
    }

    vos3_spinlock_unlock(&g_dev_lock);

    /* Cleanup */
    vos3_mutex_destroy(&dev->lock);

    if (dev->type == VOS3_DEV_BLOCK) {
        vos3_block_device_t* bdev = (vos3_block_device_t*)dev;
        vos3_mutex_destroy(&bdev->queue_lock);
    }

    VOS3_DEBUG("Unregistered device '%s' (%u:%u)", dev->name, major, minor);

    dev->magic = 0U;
    vos3_kfree(dev);

    return VOS3_DEV_OK;
}

vos3_device_t* vos3_dev_get(vos3_dev_t devno)
{
    uint32_t major = VOS3_MAJOR(devno);
    uint32_t minor = VOS3_MINOR(devno);

    if (major >= VOS3_DEV_MAJOR_MAX || minor >= VOS3_DEV_MINOR_MAX) {
        return NULL;
    }

    vos3_spinlock_lock(&g_dev_lock);
    vos3_device_t* dev = g_devices[major][minor];
    vos3_spinlock_unlock(&g_dev_lock);

    return dev;
}

vos3_device_t* vos3_dev_find(const char* name)
{
    if (name == NULL) {
        return NULL;
    }

    vos3_spinlock_lock(&g_dev_lock);

    vos3_device_t* dev = g_device_list;
    while (dev != NULL) {
        if (strcmp(dev->name, name) == 0) {
            vos3_spinlock_unlock(&g_dev_lock);
            return dev;
        }
        dev = dev->next;
    }

    vos3_spinlock_unlock(&g_dev_lock);
    return NULL;
}

/* ============================================================================
 * DEVICE FILE OPERATIONS
 * ============================================================================ */

int vos3_dev_open(vos3_device_t* dev, vos3_file_t* file)
{
    if (dev == NULL || dev->magic != VOS3_DEV_MAGIC) {
        return VOS3_DEV_ERR_NODEV;
    }

    vos3_mutex_lock(&dev->lock);

    int result = VOS3_DEV_OK;

    if (dev->type == VOS3_DEV_CHAR) {
        if (dev->ops.char_ops != NULL && dev->ops.char_ops->open != NULL) {
            result = dev->ops.char_ops->open(dev, file);
        }
    } else {
        if (dev->ops.block_ops != NULL && dev->ops.block_ops->open != NULL) {
            result = dev->ops.block_ops->open(dev);
        }
    }

    if (result == VOS3_DEV_OK) {
        dev->ref_count++;
    }

    vos3_mutex_unlock(&dev->lock);

    return result;
}

int vos3_dev_close(vos3_device_t* dev, vos3_file_t* file)
{
    if (dev == NULL || dev->magic != VOS3_DEV_MAGIC) {
        return VOS3_DEV_ERR_NODEV;
    }

    vos3_mutex_lock(&dev->lock);

    int result = VOS3_DEV_OK;

    if (dev->ref_count > 0U) {
        dev->ref_count--;

        if (dev->ref_count == 0U) {
            if (dev->type == VOS3_DEV_CHAR) {
                if (dev->ops.char_ops != NULL && dev->ops.char_ops->close != NULL) {
                    result = dev->ops.char_ops->close(dev, file);
                }
            } else {
                if (dev->ops.block_ops != NULL && dev->ops.block_ops->close != NULL) {
                    result = dev->ops.block_ops->close(dev);
                }
            }
        }
    }

    vos3_mutex_unlock(&dev->lock);

    return result;
}

int64_t vos3_dev_read(vos3_device_t* dev, vos3_file_t* file,
                       void* buf, size_t count)
{
    if (dev == NULL || dev->magic != VOS3_DEV_MAGIC) {
        return VOS3_DEV_ERR_NODEV;
    }

    if (dev->type != VOS3_DEV_CHAR) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->ops.char_ops == NULL || dev->ops.char_ops->read == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    return dev->ops.char_ops->read(dev, file, buf, count);
}

int64_t vos3_dev_write(vos3_device_t* dev, vos3_file_t* file,
                        const void* buf, size_t count)
{
    if (dev == NULL || dev->magic != VOS3_DEV_MAGIC) {
        return VOS3_DEV_ERR_NODEV;
    }

    if (dev->type != VOS3_DEV_CHAR) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->ops.char_ops == NULL || dev->ops.char_ops->write == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    return dev->ops.char_ops->write(dev, file, buf, count);
}

int vos3_dev_ioctl(vos3_device_t* dev, vos3_file_t* file,
                    uint32_t cmd, void* arg)
{
    if (dev == NULL || dev->magic != VOS3_DEV_MAGIC) {
        return VOS3_DEV_ERR_NODEV;
    }

    if (dev->type == VOS3_DEV_CHAR) {
        if (dev->ops.char_ops == NULL || dev->ops.char_ops->ioctl == NULL) {
            return VOS3_DEV_ERR_INVAL;
        }
        return dev->ops.char_ops->ioctl(dev, file, cmd, arg);
    } else {
        if (dev->ops.block_ops == NULL || dev->ops.block_ops->ioctl == NULL) {
            return VOS3_DEV_ERR_INVAL;
        }
        return dev->ops.block_ops->ioctl(dev, cmd, arg);
    }
}

/* ============================================================================
 * BLOCK DEVICE HELPERS
 * ============================================================================ */

int vos3_bdev_read(vos3_block_device_t* dev, uint64_t sector,
                    uint32_t count, void* buf)
{
    if (dev == NULL || buf == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->base.magic != VOS3_DEV_MAGIC || dev->base.type != VOS3_DEV_BLOCK) {
        return VOS3_DEV_ERR_NODEV;
    }

    if (sector + count > dev->sectors) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->base.ops.block_ops == NULL || dev->base.ops.block_ops->submit == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    /* Create request */
    vos3_block_request_t req = {
        .type = VOS3_BLK_READ,
        .sector = sector,
        .count = count,
        .buffer = buf,
        .status = 0,
        .next = NULL
    };

    return dev->base.ops.block_ops->submit(&dev->base, &req);
}

int vos3_bdev_write(vos3_block_device_t* dev, uint64_t sector,
                     uint32_t count, const void* buf)
{
    if (dev == NULL || buf == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->base.magic != VOS3_DEV_MAGIC || dev->base.type != VOS3_DEV_BLOCK) {
        return VOS3_DEV_ERR_NODEV;
    }

    if (sector + count > dev->sectors) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->base.ops.block_ops == NULL || dev->base.ops.block_ops->submit == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    /* Create request */
    vos3_block_request_t req = {
        .type = VOS3_BLK_WRITE,
        .sector = sector,
        .count = count,
        .buffer = (void*)buf,
        .status = 0,
        .next = NULL
    };

    return dev->base.ops.block_ops->submit(&dev->base, &req);
}

int vos3_bdev_flush(vos3_block_device_t* dev)
{
    if (dev == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    if (dev->base.magic != VOS3_DEV_MAGIC || dev->base.type != VOS3_DEV_BLOCK) {
        return VOS3_DEV_ERR_NODEV;
    }

    if (dev->base.ops.block_ops == NULL || dev->base.ops.block_ops->submit == NULL) {
        return VOS3_DEV_OK;  /* No submit, nothing to flush */
    }

    /* Create flush request */
    vos3_block_request_t req = {
        .type = VOS3_BLK_FLUSH,
        .sector = 0,
        .count = 0,
        .buffer = NULL,
        .status = 0,
        .next = NULL
    };

    return dev->base.ops.block_ops->submit(&dev->base, &req);
}
