/*
 * VOS3 Native File System (vos3fs)
 * =================================
 * Day 8: Persistent filesystem on VirtIO block device.
 *
 * Disk Layout (v2 — expanded for 32MB file support):
 *   Sector 0       : Superblock
 *   Sector 1       : Inode bitmap (1 sector = 4096 inodes max, we use 512)
 *   Sectors 2-33   : Data block bitmap (32 sectors = 131,072 blocks tracked)
 *   Sectors 34-97  : Inode table (64 sectors = 512 inodes * 64 bytes each)
 *   Sectors 98+    : Data blocks (one sector per block)
 *
 * Design:
 *   - Block size = sector size = 512 bytes
 *   - Max 512 inodes
 *   - 6 direct block pointers per inode
 *   - 1 indirect block pointer (512/4 = 128 extra blocks)
 *   - 4 double-indirect block pointers (128*128 = 16,384 blocks each)
 *   - Max file size = (6 + 128 + 4*16384) * 512 = 33,623,040 bytes (~32 MB)
 *   - Directory entries: 8 per block (64 bytes each)
 *
 * Copyright (c) 2026 VOS3 Project
 */

#ifndef VOS3_VOS3FS_H
#define VOS3_VOS3FS_H

#include <stdint.h>
#include <stddef.h>
#include "sync.h"

/* ========================================================================= */
/* Constants                                                                  */
/* ========================================================================= */

#define VOS3FS_MAGIC            0x56335346U  /* "V3SF" */
#define VOS3FS_VERSION          2U           /* v2: double-indirect support */
#define VOS3FS_BLOCK_SIZE       512U
#define VOS3FS_NAME_MAX         59U          /* Max filename in dirent */

/* Disk layout sectors (v2) */
#define VOS3FS_SB_SECTOR        0U           /* Superblock */
#define VOS3FS_INODE_BMP_SECTOR 1U           /* Inode bitmap */
#define VOS3FS_DATA_BMP_SECTOR  2U           /* Data block bitmap start */
#define VOS3FS_DATA_BMP_SECTORS 32U          /* Sectors for data bitmap */
#define VOS3FS_INODE_TBL_SECTOR 34U          /* Inode table start (2 + 32) */
#define VOS3FS_INODE_TBL_COUNT  64U          /* Sectors for inode table */
#define VOS3FS_DATA_START       98U          /* First data block sector (34 + 64) */

/* Block pointer limits */
#define VOS3FS_DIRECT_BLOCKS    6U           /* Direct block pointers per inode */
#define VOS3FS_INDIRECT_BLOCKS  128U         /* 512 / sizeof(uint32_t) */
#define VOS3FS_DINDIRECT_BLOCKS 4U           /* Double-indirect pointers per inode */
#define VOS3FS_DINDIRECT_CAPACITY (VOS3FS_INDIRECT_BLOCKS * VOS3FS_INDIRECT_BLOCKS) /* 16384 */

/* Limits */
#define VOS3FS_MAX_INODES       512U
#define VOS3FS_MAX_DATA_BLOCKS  131072U      /* Data bitmap capacity (32 sectors × 4096 bits) */
#define VOS3FS_MAX_FILE_BLOCKS  (VOS3FS_DIRECT_BLOCKS + VOS3FS_INDIRECT_BLOCKS + \
                                 (uint32_t)VOS3FS_DINDIRECT_BLOCKS * VOS3FS_DINDIRECT_CAPACITY)
#define VOS3FS_MAX_FILE_SIZE    ((uint64_t)VOS3FS_MAX_FILE_BLOCKS * VOS3FS_BLOCK_SIZE) /* ~32 MB */
#define VOS3FS_DIRENTS_PER_BLK  8U           /* 512 / 64 = 8 entries per block */

/* Inode number for root directory */
#define VOS3FS_ROOT_INO         1U

/* File type bits stored in on-disk inode mode (POSIX compatible) */
#define VOS3FS_S_IFMT           0170000U
#define VOS3FS_S_IFREG          0100000U
#define VOS3FS_S_IFDIR          0040000U
#define VOS3FS_S_IFLNK          0120000U     /* Symbolic link (= 0xA000) */

/* Inline symlink target storage (fits in union with block pointers: 44 bytes) */
#define VOS3FS_SYMLINK_MAX      44U

/* ========================================================================= */
/* On-Disk Structures                                                         */
/* ========================================================================= */

/*
 * On-disk superblock (512 bytes, occupies sector 0).
 * Written to disk on mkfs and updated on sync/umount.
 */
typedef struct vos3fs_superblock {
    uint32_t    magic;              /* VOS3FS_MAGIC */
    uint32_t    version;            /* Filesystem version */
    uint32_t    block_size;         /* Block size (always 512) */
    uint32_t    total_blocks;       /* Total data blocks */
    uint32_t    free_blocks;        /* Free data blocks */
    uint32_t    total_inodes;       /* Total inodes (512) */
    uint32_t    free_inodes;        /* Free inodes */
    uint32_t    root_ino;           /* Root directory inode (1) */
    uint32_t    data_start;         /* First data block sector */
    uint32_t    mount_count;        /* Times mounted */
    uint64_t    last_mount_time;    /* Last mount timestamp */
    uint8_t     _reserved[512 - 48]; /* Pad to 512 bytes (48 = 10*u32 + 1*u64) */
} __attribute__((packed)) vos3fs_superblock_t;

/* Compile-time assertion: superblock must be exactly 512 bytes (one sector) */
_Static_assert(sizeof(vos3fs_superblock_t) == 512, "vos3fs_superblock must be 512 bytes");

/*
 * On-disk inode (64 bytes, 8 per sector).
 *   512 inodes total -> 64 sectors for the inode table.
 *
 * v2 layout: 6 direct + 1 indirect + 4 double-indirect = 44 bytes (unchanged).
 */
typedef struct vos3fs_inode {
    uint32_t    mode;               /* File type + permissions */
    uint32_t    size;               /* File size in bytes */
    uint32_t    nlink;              /* Hard link count */
    uint32_t    ctime;              /* Creation time (ticks, 32-bit) */
    uint32_t    mtime;              /* Modification time (ticks, 32-bit) */
    /* 44-byte union: block pointers for regular files/dirs,
     * inline target string for symlinks. */
    union {
        struct {
            uint32_t direct[VOS3FS_DIRECT_BLOCKS]; /* 6 direct block pointers  (24 B) */
            uint32_t indirect;                      /* Single indirect pointer  ( 4 B) */
            uint32_t dindirect[VOS3FS_DINDIRECT_BLOCKS]; /* 4 double-indirect  (16 B) */
        };
        char symlink_target[VOS3FS_SYMLINK_MAX];   /* Inline symlink target (44 B) */
    };
} __attribute__((packed)) vos3fs_inode_t;

/* Compile-time assertion: inode must be exactly 64 bytes */
_Static_assert(sizeof(vos3fs_inode_t) == 64, "vos3fs_inode must be 64 bytes");

/*
 * On-disk directory entry (64 bytes, 8 per block).
 *   ino == 0 means entry is free.
 */
typedef struct vos3fs_dirent {
    uint32_t    ino;                /* Inode number (0 = free) */
    uint8_t     type;               /* File type (VOS3_FT_REG, VOS3_FT_DIR) */
    char        name[VOS3FS_NAME_MAX]; /* Null-terminated filename */
} __attribute__((packed)) vos3fs_dirent_t;

/* Compile-time assertion: dirent must be exactly 64 bytes */
_Static_assert(sizeof(vos3fs_dirent_t) == 64, "vos3fs_dirent must be 64 bytes");

/* ========================================================================= */
/* In-Memory State                                                            */
/* ========================================================================= */

/*
 * In-memory vos3fs state, attached to superblock->private_data.
 */
typedef struct vos3fs_info {
    vos3fs_superblock_t dsb;        /* Cached on-disk superblock */
    uint8_t inode_bitmap[VOS3FS_MAX_INODES / 8]; /* 64 bytes */
    uint8_t data_bitmap[VOS3FS_MAX_DATA_BLOCKS / 8]; /* 16384 bytes = 131072 bits */
    uint32_t total_data_blocks;     /* Computed from disk capacity */
    vos3_spinlock_t lock;           /* Protects bitmaps, superblock, and inode ops */
} vos3fs_info_t;

/* ========================================================================= */
/* Public API                                                                 */
/* ========================================================================= */

/*
 * Format a VirtIO block device with vos3fs.
 * Creates superblock, bitmaps, root directory.
 * Returns 0 on success, negative on error.
 */
int vos3fs_mkfs(void);

/*
 * Register vos3fs filesystem type with the VFS.
 * Called once at boot. After this, mount -t vos3fs works.
 */
int vos3fs_init(void);

/*
 * Phase 36.5: Storage stress & integrity test.
 * Writes 10 sectors, reads back in reverse, verifies cache coherency
 * and superblock persistence. Call after vos3fs is mounted.
 * Returns 0 on success, -1 on failure.
 */
int vos3fs_stress_test(void);

#endif /* VOS3_VOS3FS_H */
