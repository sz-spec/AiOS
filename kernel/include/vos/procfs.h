/**
 * @file procfs.h
 * @brief VOS3 /proc Virtual Filesystem
 *
 * @details Virtual filesystem providing process and system information.
 *          Generates content on-the-fly from kernel data structures.
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_PROCFS_H
#define VOS3_PROCFS_H

#include "vfs.h"

/** @brief procfs magic number */
#define PROCFS_MAGIC    0x50524F43U  /* "PROC" */

/**
 * @brief Initialize procfs and register with VFS
 * @return 0 on success, negative on error
 */
int vos3_procfs_init(void);

/**
 * @brief Mount procfs at /proc
 * @return 0 on success, negative on error
 */
int vos3_procfs_mount(void);

/** @brief External procfs filesystem type (for VFS registration) */
extern vos3_fs_type_t g_procfs_type;

#endif /* VOS3_PROCFS_H */
