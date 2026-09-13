#ifndef VOS3_BOOT_FS_H
#define VOS3_BOOT_FS_H

/**
 * @brief Initialize filesystem subsystems.
 *
 * VirtIO block device, vos3fs mount on /disk, Day 8/9 persistence
 * tests, VFS I/O integrity checks, serial bridge, and embedded
 * user binary loading.  Panics on fatal failure.
 *
 * @return 0 on success (never returns on fatal error).
 */
int boot_fs_init(void);

#endif /* VOS3_BOOT_FS_H */
