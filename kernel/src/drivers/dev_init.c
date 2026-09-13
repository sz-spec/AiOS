/**
 * @file dev_init.c
 * @brief VOS3 Device Subsystem Initialization
 *
 * @details Initialize device subsystem and register syscalls.
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
#include "../../include/vos/syscall.h"
#include "../../include/vos/console.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/uaccess.h"

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define VOS3_SYS_IOCTL      444

/* ============================================================================
 * EXTERNAL DRIVER INIT FUNCTIONS
 * ============================================================================ */

extern int vos3_mem_devices_init(void);
extern int vos3_tty_init(void);
extern int vos3_keyboard_init(void);
extern void vos3_tty_input_char(char c);
extern void vos3_tty_set_foreground(uint32_t pgid);

/* ============================================================================
 * SYSCALL HANDLERS
 * ============================================================================ */

/**
 * @brief sys_ioctl - Device IO control (Phase 29: Hardened)
 *
 * The arg parameter is command-specific. We validate it as a user
 * pointer when non-NULL. Individual ioctl handlers must validate
 * the arg contents using copy_from_user/copy_to_user.
 */
static int64_t sys_ioctl(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    uint32_t cmd = (uint32_t)frame->rsi;
    void* arg = (void*)frame->rdx;

    /* Phase 29: Reject kernel-space arg pointers from user */
    if (arg != NULL && !access_ok(arg, 1)) {
        return -EFAULT;
    }

    /* Get file from fd */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

    vos3_file_t* file = vos3_fd_get(task->fd_table, fd);
    if (file == NULL) {
        return VOS3_FS_ERR_BADF;
    }

    /* Check if this is a device file */
    if (file->inode == NULL) {
        vos3_fd_put(file);
        return VOS3_DEV_ERR_NODEV;
    }

    /* Get device from inode private data */
    void* private_data = file->inode->private_data;
    if (private_data == NULL) {
        vos3_fd_put(file);
        return VOS3_DEV_ERR_NODEV;
    }

    /* Assume private_data contains device pointer for devfs inodes */
    /* This is a simplification - in real code we'd have proper type checking */
    typedef struct { vos3_device_t* device; } devfs_inode_data_t;
    devfs_inode_data_t* idata = (devfs_inode_data_t*)private_data;

    if (idata->device == NULL) {
        vos3_fd_put(file);
        return VOS3_DEV_ERR_NODEV;
    }

    int64_t result = (int64_t)vos3_dev_ioctl(idata->device, file, cmd, arg);
    vos3_fd_put(file);
    return result;
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

int vos3_devices_init(void)
{
    int result;

    VOS3_INFO("Initializing device subsystem");

    /* Initialize device core */
    result = vos3_dev_init();
    if (result != VOS3_DEV_OK) {
        VOS3_ERROR("Failed to initialize device core (error %d)", result);
        return result;
    }

    /* Initialize devfs */
    result = vos3_devfs_init();
    if (result != VOS3_DEV_OK) {
        VOS3_ERROR("Failed to initialize devfs (error %d)", result);
        return result;
    }

    /* Register device syscalls */
    VOS3_INFO("Registering device syscalls");
    vos3_syscall_register(VOS3_SYS_IOCTL, sys_ioctl);

    /* Initialize drivers */
    VOS3_INFO("Initializing device drivers");

    result = vos3_mem_devices_init();
    if (result != VOS3_DEV_OK) {
        VOS3_ERROR("Failed to initialize memory devices (error %d)", result);
        /* Continue anyway - not fatal */
    }

    result = vos3_tty_init();
    if (result != VOS3_DEV_OK) {
        VOS3_ERROR("Failed to initialize TTY devices (error %d)", result);
        /* Continue anyway - not fatal */
    }

    /* Initialize keyboard driver (Phase 26) */
    result = vos3_keyboard_init();
    if (result != 0) {
        VOS3_WARN("Failed to initialize keyboard driver (error %d)", result);
        /* Continue anyway - not fatal */
    }

    /* Initialize AI telemetry device (/dev/ai_telemetry) */
    result = vos3_ai_telemetry_init();
    if (result != 0) {
        VOS3_WARN("Failed to initialize AI telemetry (error %d)", result);
        /* Continue anyway - not fatal */
    }

    VOS3_INFO("Device subsystem initialized");

    return VOS3_DEV_OK;
}
