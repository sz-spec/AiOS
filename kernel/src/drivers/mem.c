/**
 * @file mem.c
 * @brief VOS3 Memory Device Drivers
 *
 * @details Implementation of /dev/null, /dev/zero, /dev/random, /dev/urandom.
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
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/entropy.h"

/* ============================================================================
 * DEVICE MINORS
 * ============================================================================ */

#define MEM_NULL_MINOR      3U
#define MEM_ZERO_MINOR      5U
#define MEM_RANDOM_MINOR    8U
#define MEM_URANDOM_MINOR   9U

/* ============================================================================
 * RANDOM NUMBER GENERATION (via entropy subsystem)
 * ============================================================================ */

/* ============================================================================
 * /dev/null - Discards all writes, returns EOF on read
 * ============================================================================ */

static int null_open(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return VOS3_DEV_OK;
}

static int null_close(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return VOS3_DEV_OK;
}

static int64_t null_read(vos3_device_t* dev, vos3_file_t* file,
                          void* buf, size_t count)
{
    (void)dev;
    (void)file;
    (void)buf;
    (void)count;
    return 0;  /* EOF */
}

static int64_t null_write(vos3_device_t* dev, vos3_file_t* file,
                           const void* buf, size_t count)
{
    (void)dev;
    (void)file;
    (void)buf;
    return (int64_t)count;  /* Discard all data */
}

static const vos3_char_ops_t g_null_ops = {
    .open  = null_open,
    .close = null_close,
    .read  = null_read,
    .write = null_write,
    .ioctl = NULL,
    .poll  = NULL,
};

/* ============================================================================
 * /dev/zero - Returns zeros on read, discards writes
 * ============================================================================ */

static int64_t zero_read(vos3_device_t* dev, vos3_file_t* file,
                          void* buf, size_t count)
{
    (void)dev;
    (void)file;
    memset(buf, 0, count);
    return (int64_t)count;
}

static const vos3_char_ops_t g_zero_ops = {
    .open  = null_open,
    .close = null_close,
    .read  = zero_read,
    .write = null_write,
    .ioctl = NULL,
    .poll  = NULL,
};

/* ============================================================================
 * /dev/random, /dev/urandom - Returns random bytes
 * ============================================================================ */

static int64_t random_read(vos3_device_t* dev, vos3_file_t* file,
                            void* buf, size_t count)
{
    (void)dev;
    (void)file;

    int ret = vos3_entropy_extract(buf, count);
    if (ret != 0) {
        return -5;  /* EIO */
    }

    return (int64_t)count;
}

static int64_t random_write(vos3_device_t* dev, vos3_file_t* file,
                             const void* buf, size_t count)
{
    (void)dev;
    (void)file;
    (void)buf;

    /* Writing to /dev/random triggers a reseed from hardware entropy */
    vos3_entropy_reseed();

    return (int64_t)count;
}

static const vos3_char_ops_t g_random_ops = {
    .open  = null_open,
    .close = null_close,
    .read  = random_read,
    .write = random_write,
    .ioctl = NULL,
    .poll  = NULL,
};

/* ============================================================================
 * REGISTRATION
 * ============================================================================ */

int vos3_mem_devices_init(void)
{
    vos3_device_t* dev;

    VOS3_INFO("Registering memory devices");

    /* /dev/null */
    dev = vos3_cdev_register("null",
                              VOS3_MKDEV(VOS3_MEM_MAJOR, MEM_NULL_MINOR),
                              &g_null_ops, NULL);
    if (dev != NULL) {
        (void)vos3_devfs_create("null", dev);
    }

    /* /dev/zero */
    dev = vos3_cdev_register("zero",
                              VOS3_MKDEV(VOS3_MEM_MAJOR, MEM_ZERO_MINOR),
                              &g_zero_ops, NULL);
    if (dev != NULL) {
        (void)vos3_devfs_create("zero", dev);
    }

    /* /dev/random */
    dev = vos3_cdev_register("random",
                              VOS3_MKDEV(VOS3_MEM_MAJOR, MEM_RANDOM_MINOR),
                              &g_random_ops, NULL);
    if (dev != NULL) {
        (void)vos3_devfs_create("random", dev);
    }

    /* /dev/urandom */
    dev = vos3_cdev_register("urandom",
                              VOS3_MKDEV(VOS3_MEM_MAJOR, MEM_URANDOM_MINOR),
                              &g_random_ops, NULL);
    if (dev != NULL) {
        (void)vos3_devfs_create("urandom", dev);
    }

    VOS3_INFO("Memory devices registered: null, zero, random, urandom");

    return VOS3_DEV_OK;
}
