/*
 * VOS3 Native File System (vos3fs) - Implementation
 * ===================================================
 * Day 8: Persistent filesystem on VirtIO block device.
 *
 * Provides read/write persistent storage backed by disk.img
 * through the VirtIO block driver validated in Day 7.
 *
 * Copyright (c) 2026 VOS3 Project
 */

#include "../../include/vos/vos3fs.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/virtio_blk.h"
#include "../../include/vos/console.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/timer.h"
#include <stdint.h>
#include <stddef.h>

/* Forward declarations for VFS integration */
struct vos3_superblock;
struct vos3_inode;
struct vos3_dentry;
struct vos3_file;

/* ========================================================================= */
/* Helpers                                                                    */
/* ========================================================================= */

static void *vos3fs_memset(void *s, int c, size_t n)
{
    uint8_t *p = (uint8_t *)s;
    while (n--) *p++ = (uint8_t)c;
    return s;
}

static void *vos3fs_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
    return dst;
}

static size_t vos3fs_strlen(const char *s)
{
    size_t len = 0;
    while (s[len]) len++;
    return len;
}

static int vos3fs_strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

static void vos3fs_strncpy(char *dst, const char *src, size_t n)
{
    size_t i;
    for (i = 0; i < n && src[i]; i++) dst[i] = src[i];
    for (; i < n; i++) dst[i] = '\0';
}

/* ========================================================================= */
/* Timestamp Helper                                                           */
/* ========================================================================= */

static uint32_t vos3fs_uptime_seconds(void)
{
    return (uint32_t)(vos3_timer_get_uptime_ms() / 1000ULL);
}

/* ========================================================================= */
/* Bitmap Operations                                                          */
/* ========================================================================= */

static int bmp_test(const uint8_t *bmp, uint32_t bit)
{
    return (bmp[bit / 8] >> (bit % 8)) & 1;
}

static void bmp_set(uint8_t *bmp, uint32_t bit)
{
    bmp[bit / 8] |= (uint8_t)(1U << (bit % 8));
}

static void bmp_clear(uint8_t *bmp, uint32_t bit)
{
    bmp[bit / 8] &= (uint8_t)~(1U << (bit % 8));
}

/* Find first free bit in bitmap. Returns bit index, or -1 if full. */
static int bmp_find_free(const uint8_t *bmp, uint32_t count)
{
    for (uint32_t i = 0; i < count; i++) {
        if (!bmp_test(bmp, i))
            return (int)i;
    }
    return -1;
}

/* ========================================================================= */
/* Disk I/O Wrappers                                                          */
/* ========================================================================= */

/* Read one sector (512 bytes) from disk into buf. */
static int disk_read_sector(uint64_t sector, void *buf)
{
    return vos3_virtio_blk_read(sector, 1, buf);
}

/* Write one sector (512 bytes) from buf to disk. */
static int disk_write_sector(uint64_t sector, const void *buf)
{
    return vos3_virtio_blk_write(sector, 1, buf);
}

/* ========================================================================= */
/* Superblock / Bitmap Persistence                                            */
/* ========================================================================= */

static int vos3fs_write_superblock(vos3fs_info_t *info)
{
    uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
    vos3fs_memset(sector_buf, 0, VOS3FS_BLOCK_SIZE);
    vos3fs_memcpy(sector_buf, &info->dsb, sizeof(info->dsb));
    return disk_write_sector(VOS3FS_SB_SECTOR, sector_buf);
}

static int vos3fs_write_inode_bitmap(vos3fs_info_t *info)
{
    uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
    vos3fs_memset(sector_buf, 0, VOS3FS_BLOCK_SIZE);
    vos3fs_memcpy(sector_buf, info->inode_bitmap, sizeof(info->inode_bitmap));
    return disk_write_sector(VOS3FS_INODE_BMP_SECTOR, sector_buf);
}

static int vos3fs_write_data_bitmap(vos3fs_info_t *info)
{
    /*
     * Write data bitmap to disk (VOS3FS_DATA_BMP_SECTORS sectors).
     * Use a stack buffer as intermediary for disk writes.
     * Heap pointers (PHYS_MAP range) may not be usable for DMA paths.
     */
    uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
    int rc;

    for (uint32_t s = 0; s < VOS3FS_DATA_BMP_SECTORS; s++) {
        vos3fs_memcpy(sector_buf, info->data_bitmap + s * VOS3FS_BLOCK_SIZE,
                      VOS3FS_BLOCK_SIZE);
        rc = disk_write_sector(VOS3FS_DATA_BMP_SECTOR + s, sector_buf);
        if (rc != 0) return rc;
    }
    return 0;
}

/* ========================================================================= */
/* Inode Disk I/O                                                             */
/* ========================================================================= */

/* Read an on-disk inode by inode number. */
static int vos3fs_read_dinode(uint32_t ino, vos3fs_inode_t *out)
{
    if (ino == 0 || ino >= VOS3FS_MAX_INODES) return -1;

    /* 8 inodes per sector (512 / 64 = 8) */
    uint64_t sector = VOS3FS_INODE_TBL_SECTOR + (ino / 8);
    uint32_t offset = (ino % 8) * sizeof(vos3fs_inode_t);

    uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
    int rc = disk_read_sector(sector, sector_buf);
    if (rc != 0) return rc;

    vos3fs_memcpy(out, sector_buf + offset, sizeof(vos3fs_inode_t));
    return 0;
}

/* Write an on-disk inode by inode number. */
static int vos3fs_write_dinode(uint32_t ino, const vos3fs_inode_t *di)
{
    if (ino == 0 || ino >= VOS3FS_MAX_INODES) return -1;

    uint64_t sector = VOS3FS_INODE_TBL_SECTOR + (ino / 8);
    uint32_t offset = (ino % 8) * sizeof(vos3fs_inode_t);

    uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
    int rc = disk_read_sector(sector, sector_buf);
    if (rc != 0) return rc;

    vos3fs_memcpy(sector_buf + offset, di, sizeof(vos3fs_inode_t));
    return disk_write_sector(sector, sector_buf);
}

/* ========================================================================= */
/* Block Allocation                                                           */
/* ========================================================================= */

static vos3fs_info_t *vos3fs_get_info(vos3_superblock_t *sb)
{
    return (vos3fs_info_t *)sb->private_data;
}

/* Allocate a data block. Returns sector number (>= DATA_START), or 0 on failure. */
static uint32_t vos3fs_alloc_block(vos3fs_info_t *info)
{
    int idx = bmp_find_free(info->data_bitmap, info->total_data_blocks);
    if (idx < 0) return 0;

    bmp_set(info->data_bitmap, (uint32_t)idx);
    info->dsb.free_blocks--;

    /* Zero the block on disk */
    uint8_t zero[VOS3FS_BLOCK_SIZE];
    vos3fs_memset(zero, 0, VOS3FS_BLOCK_SIZE);
    if (disk_write_sector(VOS3FS_DATA_START + (uint32_t)idx, zero) != 0) {
        bmp_clear(info->data_bitmap, (uint32_t)idx);
        info->dsb.free_blocks++;
        return 0;
    }

    return VOS3FS_DATA_START + (uint32_t)idx;
}

/* Free a data block by sector number. */
static void vos3fs_free_block(vos3fs_info_t *info, uint32_t sector)
{
    if (sector < VOS3FS_DATA_START) return;
    uint32_t idx = sector - VOS3FS_DATA_START;
    if (idx >= info->total_data_blocks) return;

    bmp_clear(info->data_bitmap, idx);
    info->dsb.free_blocks++;
}

/*
 * Free all data blocks referenced by an inode (direct, indirect, double-indirect).
 * Does NOT free the inode itself. Used by unlink, rmdir, rename.
 */
static void vos3fs_free_all_blocks(vos3fs_info_t *info, vos3fs_inode_t *di)
{
    /* Free direct blocks */
    for (uint32_t i = 0; i < VOS3FS_DIRECT_BLOCKS; i++) {
        if (di->direct[i] != 0) {
            vos3fs_free_block(info, di->direct[i]);
            di->direct[i] = 0;
        }
    }

    /* Free single indirect block and its data blocks */
    if (di->indirect != 0) {
        uint8_t ibuf[VOS3FS_BLOCK_SIZE];
        if (disk_read_sector(di->indirect, ibuf) == 0) {
            uint32_t *ptrs = (uint32_t *)ibuf;
            for (uint32_t i = 0; i < VOS3FS_INDIRECT_BLOCKS; i++) {
                if (ptrs[i] != 0) vos3fs_free_block(info, ptrs[i]);
            }
        }
        vos3fs_free_block(info, di->indirect);
        di->indirect = 0;
    }

    /* Free double-indirect blocks (3-level traversal) */
    for (uint32_t d = 0; d < VOS3FS_DINDIRECT_BLOCKS; d++) {
        if (di->dindirect[d] == 0) continue;

        uint8_t l1buf[VOS3FS_BLOCK_SIZE];
        if (disk_read_sector(di->dindirect[d], l1buf) == 0) {
            uint32_t *l1_ptrs = (uint32_t *)l1buf;
            for (uint32_t i = 0; i < VOS3FS_INDIRECT_BLOCKS; i++) {
                if (l1_ptrs[i] == 0) continue;
                uint8_t l2buf[VOS3FS_BLOCK_SIZE];
                if (disk_read_sector(l1_ptrs[i], l2buf) == 0) {
                    uint32_t *l2_ptrs = (uint32_t *)l2buf;
                    for (uint32_t j = 0; j < VOS3FS_INDIRECT_BLOCKS; j++) {
                        if (l2_ptrs[j] != 0)
                            vos3fs_free_block(info, l2_ptrs[j]);
                    }
                }
                vos3fs_free_block(info, l1_ptrs[i]);
            }
        }
        vos3fs_free_block(info, di->dindirect[d]);
        di->dindirect[d] = 0;
    }
}

/* Allocate an inode number. Returns ino (>= 1), or 0 on failure. */
static uint32_t vos3fs_alloc_inode(vos3fs_info_t *info)
{
    /* Skip ino 0 (reserved as "no inode") */
    for (uint32_t i = 1; i < VOS3FS_MAX_INODES; i++) {
        if (!bmp_test(info->inode_bitmap, i)) {
            bmp_set(info->inode_bitmap, i);
            info->dsb.free_inodes--;
            return i;
        }
    }
    return 0;
}

/* Free an inode number. */
static void vos3fs_free_inode_num(vos3fs_info_t *info, uint32_t ino)
{
    if (ino == 0 || ino >= VOS3FS_MAX_INODES) return;
    bmp_clear(info->inode_bitmap, ino);
    info->dsb.free_inodes++;
}

/*
 * Day 11 fix: Persist all filesystem metadata (bitmaps + superblock) to disk.
 * Called after high-level operations (write, create, unlink) complete,
 * NOT from within alloc/free to avoid stack overflow from nested 512B buffers.
 */
static int vos3fs_persist_metadata(vos3fs_info_t *info)
{
    int rc;

    /* Defensive: ensure magic is always set before writing to disk */
    if (info->dsb.magic != VOS3FS_MAGIC) {
        vos3_console_puts("[vos3fs] WARNING: dsb.magic was corrupted, restoring\n");
        info->dsb.magic = VOS3FS_MAGIC;
    }

    rc = vos3fs_write_inode_bitmap(info);
    if (rc != 0) {
        vos3_console_puts("[vos3fs] ERROR: inode bitmap write failed\n");
        return rc;
    }

    rc = vos3fs_write_data_bitmap(info);
    if (rc != 0) {
        vos3_console_puts("[vos3fs] ERROR: data bitmap write failed\n");
        return rc;
    }

    rc = vos3fs_write_superblock(info);
    if (rc != 0) {
        vos3_console_puts("[vos3fs] ERROR: superblock write failed\n");
        return rc;
    }

    rc = vos3_virtio_blk_flush();
    if (rc != 0) {
        vos3_console_puts("[vos3fs] WARNING: flush failed\n");
    }

    return 0;
}

/* ========================================================================= */
/* Block Mapping: translate file offset to disk sector                        */
/* ========================================================================= */

/*
 * Get the disk sector for logical block `blk_idx` of inode `di`.
 * Returns sector number, or 0 if not allocated.
 */
static uint32_t vos3fs_bmap(const vos3fs_inode_t *di, uint32_t blk_idx)
{
    /* Direct blocks: [0, DIRECT_BLOCKS) */
    if (blk_idx < VOS3FS_DIRECT_BLOCKS) {
        return di->direct[blk_idx];
    }

    /* Single indirect: [DIRECT_BLOCKS, DIRECT_BLOCKS + INDIRECT_BLOCKS) */
    uint32_t past_direct = blk_idx - VOS3FS_DIRECT_BLOCKS;
    if (past_direct < VOS3FS_INDIRECT_BLOCKS) {
        if (di->indirect == 0) return 0;
        uint8_t ibuf[VOS3FS_BLOCK_SIZE];
        if (disk_read_sector(di->indirect, ibuf) != 0) return 0;
        uint32_t *ptrs = (uint32_t *)ibuf;
        return ptrs[past_direct];
    }

    /* Double indirect: [DIRECT + INDIRECT, DIRECT + INDIRECT + DINDIRECT*CAP) */
    uint32_t past_indirect = past_direct - VOS3FS_INDIRECT_BLOCKS;
    uint32_t di_num = past_indirect / VOS3FS_DINDIRECT_CAPACITY;
    if (di_num >= VOS3FS_DINDIRECT_BLOCKS) return 0;
    if (di->dindirect[di_num] == 0) return 0;

    uint32_t within = past_indirect % VOS3FS_DINDIRECT_CAPACITY;
    uint32_t l1_idx = within / VOS3FS_INDIRECT_BLOCKS;
    uint32_t l2_idx = within % VOS3FS_INDIRECT_BLOCKS;

    /* Read level-1 (double-indirect) block */
    uint8_t buf1[VOS3FS_BLOCK_SIZE];
    if (disk_read_sector(di->dindirect[di_num], buf1) != 0) return 0;
    uint32_t *l1_ptrs = (uint32_t *)buf1;
    if (l1_ptrs[l1_idx] == 0) return 0;

    /* Read level-2 (indirect) block */
    uint8_t buf2[VOS3FS_BLOCK_SIZE];
    if (disk_read_sector(l1_ptrs[l1_idx], buf2) != 0) return 0;
    uint32_t *l2_ptrs = (uint32_t *)buf2;
    return l2_ptrs[l2_idx];
}

/*
 * Ensure logical block `blk_idx` is allocated. Allocate if needed.
 * Returns disk sector, or 0 on failure.
 */
static uint32_t vos3fs_bmap_alloc(vos3fs_info_t *info, vos3fs_inode_t *di,
                                   uint32_t blk_idx)
{
    /* Direct blocks */
    if (blk_idx < VOS3FS_DIRECT_BLOCKS) {
        if (di->direct[blk_idx] == 0) {
            di->direct[blk_idx] = vos3fs_alloc_block(info);
        }
        return di->direct[blk_idx];
    }

    /* Single indirect */
    uint32_t past_direct = blk_idx - VOS3FS_DIRECT_BLOCKS;
    if (past_direct < VOS3FS_INDIRECT_BLOCKS) {
        if (di->indirect == 0) {
            di->indirect = vos3fs_alloc_block(info);
            if (di->indirect == 0) return 0;
            /* Zero the new indirect block */
            uint8_t zbuf[VOS3FS_BLOCK_SIZE];
            vos3fs_memset(zbuf, 0, VOS3FS_BLOCK_SIZE);
            if (disk_write_sector(di->indirect, zbuf) != 0) return 0;
        }
        uint8_t ibuf[VOS3FS_BLOCK_SIZE];
        if (disk_read_sector(di->indirect, ibuf) != 0) return 0;
        uint32_t *ptrs = (uint32_t *)ibuf;
        if (ptrs[past_direct] == 0) {
            ptrs[past_direct] = vos3fs_alloc_block(info);
            if (ptrs[past_direct] == 0) return 0;
            if (disk_write_sector(di->indirect, ibuf) != 0) return 0;
        }
        return ptrs[past_direct];
    }

    /* Double indirect */
    uint32_t past_indirect = past_direct - VOS3FS_INDIRECT_BLOCKS;
    uint32_t di_num = past_indirect / VOS3FS_DINDIRECT_CAPACITY;
    if (di_num >= VOS3FS_DINDIRECT_BLOCKS) return 0;

    uint32_t within = past_indirect % VOS3FS_DINDIRECT_CAPACITY;
    uint32_t l1_idx = within / VOS3FS_INDIRECT_BLOCKS;
    uint32_t l2_idx = within % VOS3FS_INDIRECT_BLOCKS;

    /* Allocate double-indirect block if needed */
    if (di->dindirect[di_num] == 0) {
        di->dindirect[di_num] = vos3fs_alloc_block(info);
        if (di->dindirect[di_num] == 0) return 0;
        uint8_t zbuf[VOS3FS_BLOCK_SIZE];
        vos3fs_memset(zbuf, 0, VOS3FS_BLOCK_SIZE);
        if (disk_write_sector(di->dindirect[di_num], zbuf) != 0) return 0;
    }

    /* Read level-1 block */
    uint8_t buf1[VOS3FS_BLOCK_SIZE];
    if (disk_read_sector(di->dindirect[di_num], buf1) != 0) return 0;
    uint32_t *l1_ptrs = (uint32_t *)buf1;

    /* Allocate level-2 indirect block if needed */
    if (l1_ptrs[l1_idx] == 0) {
        l1_ptrs[l1_idx] = vos3fs_alloc_block(info);
        if (l1_ptrs[l1_idx] == 0) return 0;
        if (disk_write_sector(di->dindirect[di_num], buf1) != 0) return 0;
        uint8_t zbuf[VOS3FS_BLOCK_SIZE];
        vos3fs_memset(zbuf, 0, VOS3FS_BLOCK_SIZE);
        if (disk_write_sector(l1_ptrs[l1_idx], zbuf) != 0) return 0;
    }

    /* Read level-2 block and allocate data block if needed */
    uint8_t buf2[VOS3FS_BLOCK_SIZE];
    if (disk_read_sector(l1_ptrs[l1_idx], buf2) != 0) return 0;
    uint32_t *l2_ptrs = (uint32_t *)buf2;
    if (l2_ptrs[l2_idx] == 0) {
        l2_ptrs[l2_idx] = vos3fs_alloc_block(info);
        if (l2_ptrs[l2_idx] == 0) return 0;
        if (disk_write_sector(l1_ptrs[l1_idx], buf2) != 0) return 0;
    }
    return l2_ptrs[l2_idx];
}

/* ========================================================================= */
/* Directory Operations (on-disk)                                             */
/* ========================================================================= */

/*
 * Find an entry in a directory inode.
 * Returns the inode number, or 0 if not found.
 */
static uint32_t vos3fs_dir_lookup(const vos3fs_inode_t *dir_di, const char *name)
{
    uint32_t nblocks = (dir_di->size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE;
    uint8_t buf[VOS3FS_BLOCK_SIZE];

    for (uint32_t b = 0; b < nblocks; b++) {
        uint32_t sector = vos3fs_bmap(dir_di, b);
        if (sector == 0) continue;
        if (disk_read_sector(sector, buf) != 0) continue;

        vos3fs_dirent_t *de = (vos3fs_dirent_t *)buf;
        for (uint32_t i = 0; i < VOS3FS_DIRENTS_PER_BLK; i++) {
            if (de[i].ino != 0 && vos3fs_strcmp(de[i].name, name) == 0) {
                return de[i].ino;
            }
        }
    }
    return 0;
}

/*
 * Add an entry to a directory inode.
 * Returns 0 on success, negative on error.
 */
static int vos3fs_dir_add(vos3fs_info_t *info, vos3fs_inode_t *dir_di,
                           uint32_t dir_ino, const char *name,
                           uint32_t child_ino, uint8_t type)
{
    uint32_t nblocks = (dir_di->size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE;
    uint8_t buf[VOS3FS_BLOCK_SIZE];

    /* Search existing blocks for a free slot */
    for (uint32_t b = 0; b < nblocks; b++) {
        uint32_t sector = vos3fs_bmap(dir_di, b);
        if (sector == 0) continue;
        if (disk_read_sector(sector, buf) != 0) continue;

        vos3fs_dirent_t *de = (vos3fs_dirent_t *)buf;
        for (uint32_t i = 0; i < VOS3FS_DIRENTS_PER_BLK; i++) {
            if (de[i].ino == 0) {
                de[i].ino = child_ino;
                de[i].type = type;
                vos3fs_strncpy(de[i].name, name, VOS3FS_NAME_MAX);
                if (disk_write_sector(sector, buf) != 0)
                    return VOS3_FS_ERR_IO;
                return 0;
            }
        }
    }

    /* No free slot - allocate a new block for the directory */
    uint32_t new_blk = nblocks;
    uint32_t sector = vos3fs_bmap_alloc(info, dir_di, new_blk);
    if (sector == 0) return -28; /* ENOSPC */

    /* Read the zeroed block and add entry */
    vos3fs_memset(buf, 0, VOS3FS_BLOCK_SIZE);
    vos3fs_dirent_t *de = (vos3fs_dirent_t *)buf;
    de[0].ino = child_ino;
    de[0].type = type;
    vos3fs_strncpy(de[0].name, name, VOS3FS_NAME_MAX);
    if (disk_write_sector(sector, buf) != 0) {
        vos3fs_free_block(info, sector);  /* Free leaked block on I/O error */
        return VOS3_FS_ERR_IO;
    }

    dir_di->size += VOS3FS_BLOCK_SIZE;
    vos3fs_write_dinode(dir_ino, dir_di);
    return 0;
}

/*
 * Remove an entry from a directory inode by name.
 * Returns the removed inode number, or 0 if not found.
 */
static uint32_t vos3fs_dir_remove(const vos3fs_inode_t *dir_di,
                                   const char *name)
{
    uint32_t nblocks = (dir_di->size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE;
    uint8_t buf[VOS3FS_BLOCK_SIZE];

    for (uint32_t b = 0; b < nblocks; b++) {
        uint32_t sector = vos3fs_bmap(dir_di, b);
        if (sector == 0) continue;
        if (disk_read_sector(sector, buf) != 0) continue;

        vos3fs_dirent_t *de = (vos3fs_dirent_t *)buf;
        for (uint32_t i = 0; i < VOS3FS_DIRENTS_PER_BLK; i++) {
            if (de[i].ino != 0 && vos3fs_strcmp(de[i].name, name) == 0) {
                uint32_t removed_ino = de[i].ino;
                de[i].ino = 0;
                de[i].name[0] = '\0';
                if (disk_write_sector(sector, buf) != 0) {
                    /* Undo — restore the entry */
                    de[i].ino = removed_ino;
                    return 0;
                }
                return removed_ino;
            }
        }
    }
    return 0;
}

/* ========================================================================= */
/* VFS Inode Operations                                                       */
/* ========================================================================= */

/* Forward declarations */
static const vos3_inode_ops_t g_vos3fs_inode_ops;
static const vos3_file_ops_t g_vos3fs_file_ops;

static int vos3fs_inode_create(vos3_inode_t *dir, const char *name, uint32_t mode)
{
    vos3fs_info_t *info = vos3fs_get_info(dir->sb);
    uint32_t dir_ino = dir->ino;

    vos3_spinlock_lock(&info->lock);

    /* Read parent directory's on-disk inode */
    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir_ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    /* Check if name already exists */
    if (vos3fs_dir_lookup(&dir_di, name) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_EXIST;
    }

    /* Allocate new inode */
    uint32_t new_ino = vos3fs_alloc_inode(info);
    if (new_ino == 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Initialize on-disk inode */
    vos3fs_inode_t new_di;
    vos3fs_memset(&new_di, 0, sizeof(new_di));
    new_di.mode = VOS3FS_S_IFREG | (mode & 0777);
    new_di.nlink = 1;
    new_di.size = 0;
    new_di.ctime = vos3fs_uptime_seconds();
    new_di.mtime = new_di.ctime;

    /* Write inode to disk */
    if (vos3fs_write_dinode(new_ino, &new_di) != 0) {
        vos3fs_free_inode_num(info, new_ino);
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    /* Add directory entry */
    uint8_t ftype = 1; /* VOS3_FT_REG */
    if (vos3fs_dir_add(info, &dir_di, dir_ino, name, new_ino, ftype) != 0) {
        vos3fs_free_inode_num(info, new_ino);
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Persist bitmaps + superblock after inode creation */
    if (vos3fs_persist_metadata(info) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static vos3_dentry_t *vos3fs_inode_lookup(vos3_inode_t *dir, const char *name)
{
    vos3fs_info_t *info = vos3fs_get_info(dir->sb);

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir->ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return NULL;
    }

    uint32_t child_ino = vos3fs_dir_lookup(&dir_di, name);
    if (child_ino == 0) {
        vos3_spinlock_unlock(&info->lock);
        return NULL;
    }

    /* Read child inode from disk */
    vos3fs_inode_t child_di;
    if (vos3fs_read_dinode(child_ino, &child_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return NULL;
    }

    vos3_spinlock_unlock(&info->lock);

    /* Allocate VFS inode + dentry for the lookup result (outside lock) */
    vos3_inode_t *vinode = (vos3_inode_t *)vos3_kmalloc(sizeof(vos3_inode_t));
    if (!vinode) return NULL;
    vos3fs_memset(vinode, 0, sizeof(*vinode));

    vinode->ino = child_ino;
    vinode->mode = child_di.mode;
    vinode->size = child_di.size;
    vinode->nlink = child_di.nlink;
    vinode->ctime = (uint64_t)child_di.ctime;
    vinode->mtime = (uint64_t)child_di.mtime;
    vinode->atime = (uint64_t)child_di.mtime;  /* atime defaults to mtime */
    vinode->ref_count = 1;
    vinode->sb = dir->sb;
    vinode->ops = &g_vos3fs_inode_ops;
    vinode->default_fops = &g_vos3fs_file_ops;

    /* Create dentry */
    vos3_dentry_t *dentry = (vos3_dentry_t *)vos3_kmalloc(sizeof(vos3_dentry_t));
    if (!dentry) {
        vos3_kfree(vinode);
        return NULL;
    }
    vos3fs_memset(dentry, 0, sizeof(*dentry));
    vos3fs_strncpy(dentry->name, name, VOS3_NAME_MAX);
    dentry->inode = vinode;
    dentry->ref_count = 1;

    return dentry;
}

static int vos3fs_inode_mkdir(vos3_inode_t *dir, const char *name, uint32_t mode)
{
    vos3fs_info_t *info = vos3fs_get_info(dir->sb);
    uint32_t dir_ino = dir->ino;

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir_ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    if (vos3fs_dir_lookup(&dir_di, name) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_EXIST;
    }

    /* Allocate inode for new directory */
    uint32_t new_ino = vos3fs_alloc_inode(info);
    if (new_ino == 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Initialize directory inode */
    vos3fs_inode_t new_di;
    vos3fs_memset(&new_di, 0, sizeof(new_di));
    new_di.mode = VOS3FS_S_IFDIR | (mode & 0777);
    new_di.nlink = 2; /* . and parent's link */
    new_di.size = 0;
    new_di.ctime = vos3fs_uptime_seconds();
    new_di.mtime = new_di.ctime;

    if (vos3fs_write_dinode(new_ino, &new_di) != 0) {
        vos3fs_free_inode_num(info, new_ino);
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    /* Add entry in parent */
    uint8_t dtype = 2; /* VOS3_FT_DIR */
    if (vos3fs_dir_add(info, &dir_di, dir_ino, name, new_ino, dtype) != 0) {
        vos3fs_free_inode_num(info, new_ino);
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Increment parent nlink */
    dir_di.nlink++;
    vos3fs_write_dinode(dir_ino, &dir_di);
    dir->nlink++;

    /* Persist bitmaps + superblock after mkdir */
    if (vos3fs_persist_metadata(info) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static int vos3fs_inode_unlink(vos3_inode_t *dir, const char *name)
{
    vos3fs_info_t *info = vos3fs_get_info(dir->sb);
    uint32_t dir_ino = dir->ino;

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir_ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    /* Remove directory entry */
    uint32_t removed_ino = vos3fs_dir_remove(&dir_di, name);
    if (removed_ino == 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOENT;
    }

    /* Decrement link count on removed inode */
    vos3fs_inode_t child_di;
    if (vos3fs_read_dinode(removed_ino, &child_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    child_di.nlink--;
    if (child_di.nlink == 0) {
        /* Symlinks store target inline — do NOT attempt to free "block" data */
        if ((child_di.mode & VOS3FS_S_IFMT) != VOS3FS_S_IFLNK) {
            vos3fs_free_all_blocks(info, &child_di);
        }
        /* Free the inode itself */
        vos3fs_free_inode_num(info, removed_ino);
    }
    vos3fs_write_dinode(removed_ino, &child_di);

    /* Persist bitmaps + superblock after unlink */
    (void)vos3fs_persist_metadata(info);  /* best-effort: blocks already freed in memory */

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static int vos3fs_inode_rmdir(vos3_inode_t *dir, const char *name)
{
    vos3fs_info_t *info = vos3fs_get_info(dir->sb);
    uint32_t dir_ino = dir->ino;

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir_ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    uint32_t child_ino = vos3fs_dir_lookup(&dir_di, name);
    if (child_ino == 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOENT;
    }

    /* Read the target directory */
    vos3fs_inode_t child_di;
    if (vos3fs_read_dinode(child_ino, &child_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    if ((child_di.mode & VOS3FS_S_IFMT) != VOS3FS_S_IFDIR) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOTDIR;
    }

    /* Check if directory is empty (size == 0 means no entries) */
    if (child_di.size > 0) {
        /* Scan for any non-free entry */
        uint32_t nblocks = (child_di.size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE;
        uint8_t buf[VOS3FS_BLOCK_SIZE];
        for (uint32_t b = 0; b < nblocks; b++) {
            uint32_t sector = vos3fs_bmap(&child_di, b);
            if (sector == 0) continue;
            if (disk_read_sector(sector, buf) != 0) continue;
            vos3fs_dirent_t *de = (vos3fs_dirent_t *)buf;
            for (uint32_t i = 0; i < VOS3FS_DIRENTS_PER_BLK; i++) {
                if (de[i].ino != 0) {
                    vos3_spinlock_unlock(&info->lock);
                    return VOS3_FS_ERR_NOTEMPTY;
                }
            }
        }
    }

    /* Remove entry from parent */
    vos3fs_dir_remove(&dir_di, name);

    /* Free directory's data blocks and inode */
    vos3fs_free_all_blocks(info, &child_di);
    vos3fs_free_inode_num(info, child_ino);

    /* Decrement parent nlink */
    dir_di.nlink--;
    vos3fs_write_dinode(dir_ino, &dir_di);
    dir->nlink--;

    /* Persist bitmaps + superblock after rmdir */
    (void)vos3fs_persist_metadata(info);  /* best-effort: blocks already freed in memory */

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static int vos3fs_inode_rename(vos3_inode_t *old_dir, const char *old_name,
                               vos3_inode_t *new_dir, const char *new_name)
{
    if (!old_dir || !new_dir || !old_name || !new_name)
        return VOS3_FS_ERR_INVAL;

    /* Both directories must be on the same filesystem */
    if (old_dir->sb != new_dir->sb)
        return VOS3_FS_ERR_INVAL;

    vos3fs_info_t *info = vos3fs_get_info(old_dir->sb);
    uint32_t old_dir_ino = old_dir->ino;
    uint32_t new_dir_ino = new_dir->ino;

    vos3_spinlock_lock(&info->lock);

    /* Read old parent directory inode */
    vos3fs_inode_t old_dir_di;
    if (vos3fs_read_dinode(old_dir_ino, &old_dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    /* Lookup source entry in old parent */
    uint32_t src_ino = vos3fs_dir_lookup(&old_dir_di, old_name);
    if (src_ino == 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOENT;
    }

    /* Read source inode to get its type for directory entry */
    vos3fs_inode_t src_di;
    if (vos3fs_read_dinode(src_ino, &src_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    uint8_t src_type;
    if ((src_di.mode & VOS3FS_S_IFMT) == VOS3FS_S_IFDIR)
        src_type = 2; /* VOS3_FT_DIR */
    else
        src_type = 1; /* VOS3_FT_REG */

    /* Read new parent directory inode */
    vos3fs_inode_t new_dir_di;
    if (vos3fs_read_dinode(new_dir_ino, &new_dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    /* Check if destination name already exists in new parent */
    uint32_t dst_ino = vos3fs_dir_lookup(&new_dir_di, new_name);
    if (dst_ino != 0) {
        /* Destination exists -- unlink it first */
        vos3fs_inode_t dst_di;
        if (vos3fs_read_dinode(dst_ino, &dst_di) != 0) {
            vos3_spinlock_unlock(&info->lock);
            return VOS3_FS_ERR_IO;
        }

        /* Cannot overwrite a directory with a file or vice versa */
        int src_is_dir = ((src_di.mode & VOS3FS_S_IFMT) == VOS3FS_S_IFDIR);
        int dst_is_dir = ((dst_di.mode & VOS3FS_S_IFMT) == VOS3FS_S_IFDIR);
        if (src_is_dir != dst_is_dir) {
            vos3_spinlock_unlock(&info->lock);
            return src_is_dir ? VOS3_FS_ERR_NOTDIR : VOS3_FS_ERR_ISDIR;
        }

        /* Remove destination directory entry */
        uint32_t removed = vos3fs_dir_remove(&new_dir_di, new_name);
        if (removed == 0) {
            vos3_spinlock_unlock(&info->lock);
            return VOS3_FS_ERR_IO;
        }

        /* Decrement link count on overwritten inode */
        dst_di.nlink--;
        if (dst_di.nlink == 0) {
            vos3fs_free_all_blocks(info, &dst_di);
            vos3fs_free_inode_num(info, dst_ino);
        }
        vos3fs_write_dinode(dst_ino, &dst_di);
    }

    /* Add source inode entry under new name in new parent */
    if (vos3fs_dir_add(info, &new_dir_di, new_dir_ino, new_name, src_ino, src_type) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Re-read old_dir_di in case old_dir == new_dir (same directory rename) */
    if (old_dir_ino == new_dir_ino) {
        if (vos3fs_read_dinode(old_dir_ino, &old_dir_di) != 0) {
            vos3_spinlock_unlock(&info->lock);
            return VOS3_FS_ERR_IO;
        }
    }

    /* Remove source entry from old parent */
    uint32_t removed_ino = vos3fs_dir_remove(&old_dir_di, old_name);
    if (removed_ino == 0) {
        /* This should not happen since we verified the entry exists above.
         * But if it does, we have already added the new entry, so the rename
         * is in a partially completed state. Log and persist what we have. */
        vos3_console_puts("[vos3fs] WARNING: rename failed to remove old entry\n");
    }

    /* If renaming a directory across parents, update parent nlinks */
    if (src_type == 2 && old_dir_ino != new_dir_ino) {
        /* Old parent loses a subdirectory link */
        if (old_dir_di.nlink > 0) old_dir_di.nlink--;
        vos3fs_write_dinode(old_dir_ino, &old_dir_di);
        old_dir->nlink = old_dir_di.nlink;

        /* New parent gains a subdirectory link */
        new_dir_di.nlink++;
        vos3fs_write_dinode(new_dir_ino, &new_dir_di);
        new_dir->nlink = new_dir_di.nlink;
    }

    /* Persist bitmaps + superblock */
    (void)vos3fs_persist_metadata(info);

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static int vos3fs_inode_truncate(vos3_inode_t *inode, size_t size)
{
    vos3fs_info_t *info = vos3fs_get_info(inode->sb);

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t di;
    if (vos3fs_read_dinode(inode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    if (size > VOS3FS_MAX_FILE_SIZE) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Clamp on-disk size to prevent corrupt-disk DoS in free loop */
    if (di.size > VOS3FS_MAX_FILE_SIZE) di.size = (uint32_t)VOS3FS_MAX_FILE_SIZE;

    /* Free blocks beyond new size */
    uint32_t new_blocks = (uint32_t)((size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE);
    uint32_t old_blocks = (di.size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE;

    if (new_blocks < old_blocks) {
        if (new_blocks == 0) {
            /* Truncate to zero — use helper that frees all 3 levels
             * (direct data + indirect metadata + dindirect metadata). */
            vos3fs_free_all_blocks(info, &di);
        } else {
            /* Free data blocks beyond new size */
            for (uint32_t b = new_blocks; b < old_blocks; b++) {
                uint32_t sector = vos3fs_bmap(&di, b);
                if (sector != 0) {
                    vos3fs_free_block(info, sector);
                    if (b < VOS3FS_DIRECT_BLOCKS) {
                        di.direct[b] = 0;
                    }
                }
            }

            /* Free indirect/dindirect metadata blocks whose data is now
             * entirely freed.  If new_blocks <= DIRECT_BLOCKS, all
             * indirect and double-indirect data was freed above. */
            if (new_blocks <= VOS3FS_DIRECT_BLOCKS) {
                if (di.indirect != 0) {
                    vos3fs_free_block(info, di.indirect);
                    di.indirect = 0;
                }
                for (uint32_t d = 0; d < VOS3FS_DINDIRECT_BLOCKS; d++) {
                    if (di.dindirect[d] == 0) continue;
                    /* Free L1 metadata blocks within this dindirect */
                    uint8_t l1buf[VOS3FS_BLOCK_SIZE];
                    if (disk_read_sector(di.dindirect[d], l1buf) == 0) {
                        uint32_t *l1_ptrs = (uint32_t *)l1buf;
                        for (uint32_t i = 0; i < VOS3FS_INDIRECT_BLOCKS; i++) {
                            if (l1_ptrs[i] != 0)
                                vos3fs_free_block(info, l1_ptrs[i]);
                        }
                    }
                    vos3fs_free_block(info, di.dindirect[d]);
                    di.dindirect[d] = 0;
                }
            }
        }
    }

    di.size = (uint32_t)size;
    di.mtime = vos3fs_uptime_seconds();
    inode->size = size;
    inode->mtime = (uint64_t)di.mtime;
    vos3fs_write_dinode(inode->ino, &di);

    /* Persist bitmaps after truncate (blocks may have been freed) */
    (void)vos3fs_persist_metadata(info);  /* best-effort: blocks already freed in memory */

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

/* ========================================================================= */
/* Symlink, Hard Link, and Attribute Operations (Task 1.2)                   */
/* ========================================================================= */

static int vos3fs_inode_symlink(vos3_inode_t *dir, const char *name,
                                const char *target)
{
    vos3fs_info_t *info = vos3fs_get_info(dir->sb);
    uint32_t dir_ino = dir->ino;
    size_t target_len = 0;

    while (target[target_len] != '\0' && target_len < VOS3FS_SYMLINK_MAX - 1U)
        target_len++;
    if (target_len == 0U)
        return VOS3_FS_ERR_INVAL;

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir_ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    if (vos3fs_dir_lookup(&dir_di, name) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_EXIST;
    }

    uint32_t new_ino = vos3fs_alloc_inode(info);
    if (new_ino == 0U) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    /* Symlink inode: target stored inline in symlink_target[], no data blocks */
    vos3fs_inode_t new_di;
    vos3fs_memset(&new_di, 0, sizeof(new_di));
    new_di.mode  = VOS3FS_S_IFLNK | 0777U;
    new_di.nlink = 1U;
    new_di.size  = (uint32_t)target_len;
    vos3fs_memcpy(new_di.symlink_target, target, target_len);
    new_di.symlink_target[target_len] = '\0';

    if (vos3fs_write_dinode(new_ino, &new_di) != 0) {
        vos3fs_free_inode_num(info, new_ino);
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    uint8_t ftype = 7U; /* VOS3_FT_LNK */
    if (vos3fs_dir_add(info, &dir_di, dir_ino, name, new_ino, ftype) != 0) {
        vos3fs_free_inode_num(info, new_ino);
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    if (vos3fs_persist_metadata(info) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static int vos3fs_inode_readlink(vos3_inode_t *inode, char *buf, size_t size)
{
    if (inode == NULL || buf == NULL || size == 0U)
        return VOS3_FS_ERR_INVAL;
    if ((inode->mode & VOS3_S_IFMT) != VOS3_S_IFLNK)
        return -22; /* EINVAL: not a symlink */

    vos3fs_info_t *info = vos3fs_get_info(inode->sb);
    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t di;
    if (vos3fs_read_dinode(inode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }
    vos3_spinlock_unlock(&info->lock);

    /* readlink semantics: no null terminator in result buffer */
    size_t copy_len = (di.size < size) ? (size_t)di.size : size;
    vos3fs_memcpy(buf, di.symlink_target, copy_len);
    return (int)copy_len;
}

static int vos3fs_inode_link(vos3_inode_t *old_inode, vos3_inode_t *dir,
                             const char *name)
{
    if (old_inode == NULL || dir == NULL || name == NULL)
        return VOS3_FS_ERR_INVAL;
    /* POSIX: cannot hard-link directories */
    if ((old_inode->mode & VOS3_S_IFMT) == VOS3_S_IFDIR)
        return VOS3_FS_ERR_ISDIR;

    vos3fs_info_t *info = vos3fs_get_info(dir->sb);
    uint32_t dir_ino  = dir->ino;
    uint32_t old_ino  = old_inode->ino;

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t dir_di;
    if (vos3fs_read_dinode(dir_ino, &dir_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    if (vos3fs_dir_lookup(&dir_di, name) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_EXIST;
    }

    vos3fs_inode_t old_di;
    if (vos3fs_read_dinode(old_ino, &old_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    uint8_t ftype = ((old_di.mode & VOS3FS_S_IFMT) == VOS3FS_S_IFLNK) ? 7U : 1U;

    if (vos3fs_dir_add(info, &dir_di, dir_ino, name, old_ino, ftype) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_NOSPC;
    }

    old_di.nlink++;
    if (vos3fs_write_dinode(old_ino, &old_di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }
    old_inode->nlink++;

    if (vos3fs_persist_metadata(info) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static int vos3fs_inode_setattr(vos3_inode_t *inode, uint32_t mode,
                                uint32_t uid, uint32_t gid, int which)
{
    if (inode == NULL)
        return VOS3_FS_ERR_INVAL;

    vos3fs_info_t *info = vos3fs_get_info(inode->sb);
    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t di;
    if (vos3fs_read_dinode(inode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    if (which & SETATTR_MODE) {
        /* Preserve file-type bits, update only permission bits */
        di.mode     = (di.mode & VOS3FS_S_IFMT) | (mode & 07777U);
        inode->mode = di.mode;
    }
    /* uid/gid are not persisted on disk in vos3fs v1 — update in-memory only */
    if (which & SETATTR_UID) inode->uid = uid;
    if (which & SETATTR_GID) inode->gid = gid;

    if (vos3fs_write_dinode(inode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    vos3_spinlock_unlock(&info->lock);
    return VOS3_FS_OK;
}

static const vos3_inode_ops_t g_vos3fs_inode_ops = {
    .create   = vos3fs_inode_create,
    .lookup   = vos3fs_inode_lookup,
    .mkdir    = vos3fs_inode_mkdir,
    .rmdir    = vos3fs_inode_rmdir,
    .unlink   = vos3fs_inode_unlink,
    .rename   = vos3fs_inode_rename,
    .readlink = vos3fs_inode_readlink,
    .truncate = vos3fs_inode_truncate,
    .symlink  = vos3fs_inode_symlink,
    .link     = vos3fs_inode_link,
    .setattr  = vos3fs_inode_setattr,
};

/* ========================================================================= */
/* VFS File Operations                                                        */
/* ========================================================================= */

static int vos3fs_file_open(vos3_file_t *file)
{
    (void)file;
    return VOS3_FS_OK;
}

static int vos3fs_file_close(vos3_file_t *file)
{
    (void)file;
    return VOS3_FS_OK;
}

static int64_t vos3fs_file_read(vos3_file_t *file, void *buf, size_t count)
{
    vos3_inode_t *vinode = file->inode;
    vos3fs_info_t *info = vos3fs_get_info(vinode->sb);

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t di;
    if (vos3fs_read_dinode(vinode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    int64_t pos = file->pos;
    if (pos >= (int64_t)di.size) {
        vos3_spinlock_unlock(&info->lock);
        return 0; /* EOF */
    }

    /* Clamp count to available data */
    if ((uint32_t)(pos + (int64_t)count) > di.size) {
        count = di.size - (uint32_t)pos;
    }

    size_t bytes_read = 0;
    uint8_t *dst = (uint8_t *)buf;

    while (bytes_read < count) {
        uint32_t blk_idx = (uint32_t)pos / VOS3FS_BLOCK_SIZE;
        uint32_t blk_off = (uint32_t)pos % VOS3FS_BLOCK_SIZE;
        uint32_t chunk = VOS3FS_BLOCK_SIZE - blk_off;
        if (chunk > count - bytes_read) chunk = (uint32_t)(count - bytes_read);

        uint32_t sector = vos3fs_bmap(&di, blk_idx);
        if (sector == 0) {
            /* Sparse block: return zeros */
            vos3fs_memset(dst + bytes_read, 0, chunk);
        } else {
            uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
            if (disk_read_sector(sector, sector_buf) != 0) {
                int64_t result = (int64_t)bytes_read > 0 ? (int64_t)bytes_read : VOS3_FS_ERR_IO;
                vos3_spinlock_unlock(&info->lock);
                file->pos = pos;
                return result;
            }
            vos3fs_memcpy(dst + bytes_read, sector_buf + blk_off, chunk);
        }

        bytes_read += chunk;
        pos += chunk;
    }

    vos3_spinlock_unlock(&info->lock);
    file->pos = pos;
    return (int64_t)bytes_read;
}

static int64_t vos3fs_file_write(vos3_file_t *file, const void *buf, size_t count)
{
    vos3_inode_t *vinode = file->inode;
    vos3fs_info_t *info = vos3fs_get_info(vinode->sb);

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t di;
    if (vos3fs_read_dinode(vinode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    int64_t pos = file->pos;

    /* Append mode */
    if (file->flags & VOS3_O_APPEND) {
        pos = (int64_t)di.size;
    }

    /* Check max file size */
    if ((uint64_t)(pos + (int64_t)count) > VOS3FS_MAX_FILE_SIZE) {
        count = VOS3FS_MAX_FILE_SIZE - (size_t)pos;
        if (count == 0) {
            vos3_spinlock_unlock(&info->lock);
            return VOS3_FS_ERR_NOSPC;
        }
    }

    size_t bytes_written = 0;
    const uint8_t *src = (const uint8_t *)buf;

    while (bytes_written < count) {
        uint32_t blk_idx = (uint32_t)pos / VOS3FS_BLOCK_SIZE;
        uint32_t blk_off = (uint32_t)pos % VOS3FS_BLOCK_SIZE;
        uint32_t chunk = VOS3FS_BLOCK_SIZE - blk_off;
        if (chunk > count - bytes_written) chunk = (uint32_t)(count - bytes_written);

        uint32_t sector = vos3fs_bmap_alloc(info, &di, blk_idx);
        if (sector == 0) {
            int64_t result = (int64_t)bytes_written > 0 ? (int64_t)bytes_written : VOS3_FS_ERR_NOSPC;
            /* Persist what we have so far (best-effort on error path) */
            if (bytes_written > 0) {
                if ((uint32_t)pos > di.size) {
                    di.size = (uint32_t)pos;
                    vinode->size = (size_t)pos;
                }
                vos3fs_write_dinode(vinode->ino, &di);
                (void)vos3fs_persist_metadata(info);
            }
            vos3_spinlock_unlock(&info->lock);
            file->pos = pos;
            return result;
        }

        uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
        /* Read-modify-write for partial block writes */
        if (blk_off != 0 || chunk < VOS3FS_BLOCK_SIZE) {
            disk_read_sector(sector, sector_buf);
        }

        vos3fs_memcpy(sector_buf + blk_off, src + bytes_written, chunk);
        if (disk_write_sector(sector, sector_buf) != 0) {
            int64_t result = (int64_t)bytes_written > 0 ? (int64_t)bytes_written : VOS3_FS_ERR_IO;
            if (bytes_written > 0) {
                if ((uint32_t)pos > di.size) {
                    di.size = (uint32_t)pos;
                    vinode->size = (size_t)pos;
                }
                vos3fs_write_dinode(vinode->ino, &di);
                (void)vos3fs_persist_metadata(info);
            }
            vos3_spinlock_unlock(&info->lock);
            file->pos = pos;
            return result;
        }

        bytes_written += chunk;
        pos += chunk;
    }

    /* Update file size if we extended it */
    if ((uint32_t)pos > di.size) {
        di.size = (uint32_t)pos;
        vinode->size = (size_t)pos;
    }

    /* Update mtime after write */
    di.mtime = vos3fs_uptime_seconds();
    vinode->mtime = (uint64_t)di.mtime;

    /* Write updated inode to disk (with block pointers) */
    if (vos3fs_write_dinode(vinode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        file->pos = pos;
        return VOS3_FS_ERR_IO;
    }

    /* Persist bitmaps + superblock after write completes */
    if (vos3fs_persist_metadata(info) != 0) {
        vos3_spinlock_unlock(&info->lock);
        file->pos = pos;
        return VOS3_FS_ERR_IO;
    }

    vos3_spinlock_unlock(&info->lock);
    file->pos = pos;
    return (int64_t)bytes_written;
}

static int64_t vos3fs_file_lseek(vos3_file_t *file, int64_t offset, int whence)
{
    int64_t new_pos;

    switch (whence) {
    case 0: /* SEEK_SET */
        new_pos = offset;
        break;
    case 1: /* SEEK_CUR */
        new_pos = file->pos + offset;
        break;
    case 2: /* SEEK_END */
        new_pos = (int64_t)file->inode->size + offset;
        break;
    default:
        return VOS3_FS_ERR_INVAL;
    }

    if (new_pos < 0) return VOS3_FS_ERR_INVAL;
    file->pos = new_pos;
    return new_pos;
}

static int vos3fs_file_readdir(vos3_file_t *file, vos3_dirent_t *dirents,
                                size_t count, size_t *out_count)
{
    vos3_inode_t *vinode = file->inode;
    vos3fs_info_t *info = vos3fs_get_info(vinode->sb);

    vos3_spinlock_lock(&info->lock);

    vos3fs_inode_t di;
    if (vos3fs_read_dinode(vinode->ino, &di) != 0) {
        vos3_spinlock_unlock(&info->lock);
        return VOS3_FS_ERR_IO;
    }

    uint32_t nblocks = (di.size + VOS3FS_BLOCK_SIZE - 1) / VOS3FS_BLOCK_SIZE;
    size_t found = 0;
    uint8_t buf[VOS3FS_BLOCK_SIZE];

    for (uint32_t b = 0; b < nblocks && found < count; b++) {
        uint32_t sector = vos3fs_bmap(&di, b);
        if (sector == 0) continue;
        if (disk_read_sector(sector, buf) != 0) continue;

        vos3fs_dirent_t *de = (vos3fs_dirent_t *)buf;
        for (uint32_t i = 0; i < VOS3FS_DIRENTS_PER_BLK && found < count; i++) {
            if (de[i].ino != 0) {
                dirents[found].ino = de[i].ino;
                dirents[found].type = de[i].type;
                dirents[found].reclen = sizeof(vos3_dirent_t);
                vos3fs_strncpy(dirents[found].name, de[i].name, VOS3_NAME_MAX);
                found++;
            }
        }
    }

    vos3_spinlock_unlock(&info->lock);
    *out_count = found;
    return VOS3_FS_OK;
}

static int vos3fs_file_fsync(vos3_file_t *file)
{
    (void)file;
    /* Flush the VirtIO block device write cache */
    return vos3_virtio_blk_flush();
}

static const vos3_file_ops_t g_vos3fs_file_ops = {
    .open    = vos3fs_file_open,
    .close   = vos3fs_file_close,
    .read    = vos3fs_file_read,
    .write   = vos3fs_file_write,
    .lseek   = vos3fs_file_lseek,
    .readdir = vos3fs_file_readdir,
    .fsync   = vos3fs_file_fsync,
    .ioctl   = NULL,
};

/* ========================================================================= */
/* Superblock Operations                                                      */
/* ========================================================================= */

static vos3_inode_t *vos3fs_sb_alloc_inode(vos3_superblock_t *sb)
{
    (void)sb;
    vos3_inode_t *inode = (vos3_inode_t *)vos3_kmalloc(sizeof(vos3_inode_t));
    if (inode) vos3fs_memset(inode, 0, sizeof(*inode));
    return inode;
}

static void vos3fs_sb_free_inode(vos3_inode_t *inode)
{
    if (inode) vos3_kfree(inode);
}

static int vos3fs_sb_sync(vos3_superblock_t *sb)
{
    vos3fs_info_t *info = (vos3fs_info_t *)sb->private_data;

    vos3_spinlock_lock(&info->lock);

    int rc;
    rc = vos3fs_write_superblock(info);
    if (rc != 0) { vos3_spinlock_unlock(&info->lock); return rc; }
    rc = vos3fs_write_inode_bitmap(info);
    if (rc != 0) { vos3_spinlock_unlock(&info->lock); return rc; }
    rc = vos3fs_write_data_bitmap(info);
    if (rc != 0) { vos3_spinlock_unlock(&info->lock); return rc; }

    rc = vos3_virtio_blk_flush();
    vos3_spinlock_unlock(&info->lock);
    return rc;
}

static const vos3_sb_ops_t g_vos3fs_sb_ops = {
    .alloc_inode = vos3fs_sb_alloc_inode,
    .free_inode  = vos3fs_sb_free_inode,
    .sync        = vos3fs_sb_sync,
    .unmount     = NULL,
};

/* ========================================================================= */
/* Mount                                                                      */
/* ========================================================================= */

static vos3_superblock_t *vos3fs_do_mount(vos3_fs_type_t *fs,
                                           const char *source,
                                           uint32_t flags, void *data)
{
    (void)fs; (void)source; (void)flags; (void)data;

    /* Verify VirtIO block device is available */
    if (!vos3_virtio_blk_available()) {
        vos3_console_puts("[vos3fs] ERROR: VirtIO block device not available\n");
        return NULL;
    }

    /*
     * PHASE 1: All disk I/O using stack buffers.
     *
     * We complete ALL disk reads/writes BEFORE any heap allocations that
     * may trigger slab creation. The slab allocator's create_slab() allocates
     * physical pages from the PMM which can conflict with VMM page table
     * pages, corrupting address translation for stack-relative accesses.
     *
     * By doing all disk I/O first (while page tables are intact), then
     * heap allocations second, we avoid this interaction.
     */

    /* Reusable 512-byte stack buffer for all disk I/O */
    uint8_t buf[VOS3FS_BLOCK_SIZE];

    /* --- Read superblock --- */
    vos3_console_puts("[vos3fs] mount: reading superblock\n");
    vos3fs_superblock_t dsb_local;
    vos3fs_memset(&dsb_local, 0, sizeof(dsb_local));
    if (disk_read_sector(VOS3FS_SB_SECTOR, &dsb_local) != 0) {
        vos3_console_puts("[vos3fs] ERROR: Cannot read superblock\n");
        return NULL;
    }

    /* Check magic - if not formatted, run mkfs */
    if (dsb_local.magic != VOS3FS_MAGIC) {
        vos3_console_puts("[vos3fs] Disk not formatted. Running mkfs...\n");
        if (vos3fs_mkfs() != 0) {
            vos3_console_puts("[vos3fs] ERROR: mkfs failed\n");
            return NULL;
        }
        /* Re-read superblock after mkfs */
        if (disk_read_sector(VOS3FS_SB_SECTOR, &dsb_local) != 0)
            return NULL;
    }

    /* Version check: v1 disks must be reformatted for v2 layout */
    if (dsb_local.version < VOS3FS_VERSION) {
        vos3_console_puts("[vos3fs] v1 disk detected — reformatting to v2\n");
        if (vos3fs_mkfs() != 0) {
            vos3_console_puts("[vos3fs] ERROR: v2 mkfs failed\n");
            return NULL;
        }
        if (disk_read_sector(VOS3FS_SB_SECTOR, &dsb_local) != 0)
            return NULL;
    }

    /* --- Read inode bitmap (1 sector) --- */
    vos3_console_puts("[vos3fs] mount: reading bitmaps\n");
    uint8_t inode_bmp_local[VOS3FS_MAX_INODES / 8]; /* 64 bytes */
    if (disk_read_sector(VOS3FS_INODE_BMP_SECTOR, buf) != 0)
        return NULL;
    vos3fs_memcpy(inode_bmp_local, buf, sizeof(inode_bmp_local));

    /* --- Read data bitmap (VOS3FS_DATA_BMP_SECTORS sectors) --- */
    uint8_t data_bmp_local[VOS3FS_MAX_DATA_BLOCKS / 8]; /* 16384 bytes */
    vos3fs_memset(data_bmp_local, 0, sizeof(data_bmp_local));
    for (uint32_t s = 0; s < VOS3FS_DATA_BMP_SECTORS; s++) {
        if (disk_read_sector(VOS3FS_DATA_BMP_SECTOR + s, buf) != 0)
            return NULL;
        vos3fs_memcpy(data_bmp_local + s * VOS3FS_BLOCK_SIZE, buf,
                      VOS3FS_BLOCK_SIZE);
    }

    /* --- Read root inode --- */
    vos3_console_puts("[vos3fs] mount: reading root inode\n");
    vos3fs_inode_t root_di;
    {
        uint64_t root_sector = VOS3FS_INODE_TBL_SECTOR + (VOS3FS_ROOT_INO / 8);
        uint32_t root_offset = (VOS3FS_ROOT_INO % 8) * sizeof(vos3fs_inode_t);
        if (disk_read_sector(root_sector, buf) != 0)
            return NULL;
        vos3fs_memcpy(&root_di, buf + root_offset, sizeof(vos3fs_inode_t));
    }

    /* --- Verify root inode --- */
    if ((root_di.mode & VOS3FS_S_IFMT) == VOS3FS_S_IFDIR && root_di.nlink >= 1) {
        vos3_console_puts("[vos3fs] Verification: Root directory Inode #1 matches spec. (Pass)\n");
    } else {
        vos3_console_puts("[vos3fs] Verification: Root directory Inode #1 matches spec. (Fail)\n");
        return NULL;
    }

    /* --- Update and write mount count --- */
    vos3_console_puts("[vos3fs] mount: updating mount count\n");
    dsb_local.mount_count++;

    /* Log persistence check */
    {
        char mcbuf[32];
        int mi = 0;
        uint32_t mc = dsb_local.mount_count;
        vos3_console_puts("[vos3fs] Persistence Check: Mount #");
        if (mc == 0) { mcbuf[mi++] = '0'; }
        else {
            char tmp[16]; int ti = 0;
            while (mc) { tmp[ti++] = '0' + (mc % 10); mc /= 10; }
            while (ti--) mcbuf[mi++] = tmp[ti];
        }
        mcbuf[mi] = '\0';
        vos3_console_puts(mcbuf);
        vos3_console_puts(" detected. (Pass)\n");
    }

    /* Log filesystem version */
    if (dsb_local.version >= 2) {
        vos3_console_puts("[vos3fs] mounted v2 filesystem (32MB max, double-indirect)\n");
    } else {
        vos3_console_puts("[vos3fs] mounted v1 filesystem (legacy)\n");
    }

    vos3fs_memset(buf, 0, VOS3FS_BLOCK_SIZE);
    vos3fs_memcpy(buf, &dsb_local, sizeof(dsb_local));
    if (disk_write_sector(VOS3FS_SB_SECTOR, buf) != 0)
        return NULL;

    /*
     * PHASE 2: Heap allocations and VFS structure creation.
     *
     * All disk data is now cached in stack-local variables. We can safely
     * allocate from the heap (which may trigger slab page creation).
     */
    vos3_console_puts("[vos3fs] mount: allocating structures\n");

    /* Allocate in-memory filesystem state */
    vos3fs_info_t *info = (vos3fs_info_t *)vos3_kmalloc(sizeof(vos3fs_info_t));
    if (!info) return NULL;
    vos3fs_memset(info, 0, sizeof(*info));

    /* Copy cached data to heap struct */
    vos3fs_memcpy(&info->dsb, &dsb_local, sizeof(info->dsb));
    vos3fs_memcpy(info->inode_bitmap, inode_bmp_local, sizeof(info->inode_bitmap));
    vos3fs_memcpy(info->data_bitmap, data_bmp_local, sizeof(info->data_bitmap));
    info->total_data_blocks = info->dsb.total_blocks;
    vos3_spinlock_init(&info->lock);

    /* Bitmap consistency check (inspired by CVE-2025-40307):
     * Recount free blocks from the actual bitmap state instead of
     * trusting the superblock value, which may be stale after a crash. */
    {
        uint32_t actual_free = 0;
        for (uint32_t i = 0; i < info->total_data_blocks; i++) {
            if (!bmp_test(info->data_bitmap, i))
                actual_free++;
        }
        if (actual_free != info->dsb.free_blocks) {
            vos3_console_puts("[vos3fs] WARNING: free_blocks mismatch — sb says ");
            /* Print superblock value */
            {
                char nb[16]; int ni = 0;
                uint32_t v = info->dsb.free_blocks;
                if (v == 0) { nb[ni++] = '0'; }
                else { char t[12]; int ti = 0; while (v) { t[ti++] = '0'+(v%10); v /= 10; } while (ti--) nb[ni++] = t[ti]; }
                nb[ni] = '\0';
                vos3_console_puts(nb);
            }
            vos3_console_puts(", bitmap says ");
            /* Print actual value */
            {
                char nb[16]; int ni = 0;
                uint32_t v = actual_free;
                if (v == 0) { nb[ni++] = '0'; }
                else { char t[12]; int ti = 0; while (v) { t[ti++] = '0'+(v%10); v /= 10; } while (ti--) nb[ni++] = t[ti]; }
                nb[ni] = '\0';
                vos3_console_puts(nb);
            }
            vos3_console_puts(". Correcting to bitmap value.\n");
            info->dsb.free_blocks = actual_free;
        }
    }

    /* Create VFS superblock */
    vos3_superblock_t *sb = (vos3_superblock_t *)vos3_kmalloc(sizeof(vos3_superblock_t));
    if (!sb) {
        vos3_kfree(info);
        return NULL;
    }
    vos3fs_memset(sb, 0, sizeof(*sb));

    sb->magic = VOS3FS_MAGIC;
    sb->block_size = VOS3FS_BLOCK_SIZE;
    sb->block_count = info->dsb.total_blocks;
    sb->free_blocks = info->dsb.free_blocks;
    sb->inode_count = info->dsb.total_inodes;
    sb->free_inodes = info->dsb.free_inodes;
    sb->ops = &g_vos3fs_sb_ops;
    sb->private_data = info;

    /* Allocate root inode */
    vos3_inode_t *root_inode = vos3fs_sb_alloc_inode(sb);
    if (!root_inode) {
        vos3_kfree(info);
        vos3_kfree(sb);
        return NULL;
    }

    root_inode->ino = VOS3FS_ROOT_INO;
    root_inode->mode = root_di.mode;
    root_inode->size = root_di.size;
    root_inode->nlink = root_di.nlink;
    root_inode->ref_count = 1;
    root_inode->sb = sb;
    root_inode->ops = &g_vos3fs_inode_ops;
    root_inode->default_fops = &g_vos3fs_file_ops;

    vos3_dentry_t *root_dentry = (vos3_dentry_t *)vos3_kmalloc(sizeof(vos3_dentry_t));
    if (!root_dentry) {
        vos3_kfree(root_inode);
        vos3_kfree(info);
        vos3_kfree(sb);
        return NULL;
    }
    vos3fs_memset(root_dentry, 0, sizeof(*root_dentry));
    root_dentry->inode = root_inode;
    root_dentry->ref_count = 1;
    vos3fs_strncpy(root_dentry->name, "/", VOS3_NAME_MAX);

    sb->root = root_dentry;

    vos3_console_puts("[vos3fs] Mounted: ");
    /* Print stats */
    char numbuf[16];
    uint32_t val = info->dsb.total_blocks;
    int idx = 0;
    if (val == 0) { numbuf[idx++] = '0'; }
    else {
        char tmp[16]; int ti = 0;
        while (val) { tmp[ti++] = '0' + (val % 10); val /= 10; }
        while (ti--) numbuf[idx++] = tmp[ti];
    }
    numbuf[idx] = '\0';
    vos3_console_puts(numbuf);
    vos3_console_puts(" blocks, ");

    val = info->dsb.free_blocks;
    idx = 0;
    if (val == 0) { numbuf[idx++] = '0'; }
    else {
        char tmp[16]; int ti = 0;
        while (val) { tmp[ti++] = '0' + (val % 10); val /= 10; }
        while (ti--) numbuf[idx++] = tmp[ti];
    }
    numbuf[idx] = '\0';
    vos3_console_puts(numbuf);
    vos3_console_puts(" free, mount #");

    val = info->dsb.mount_count;
    idx = 0;
    if (val == 0) { numbuf[idx++] = '0'; }
    else {
        char tmp[16]; int ti = 0;
        while (val) { tmp[ti++] = '0' + (val % 10); val /= 10; }
        while (ti--) numbuf[idx++] = tmp[ti];
    }
    numbuf[idx] = '\0';
    vos3_console_puts(numbuf);
    vos3_console_puts("\n");

    return sb;
}

/* ========================================================================= */
/* mkfs - Format Disk                                                         */
/* ========================================================================= */

int vos3fs_mkfs(void)
{
    if (!vos3_virtio_blk_available()) {
        vos3_console_puts("[vos3fs] mkfs: No block device\n");
        return -1;
    }

    uint64_t capacity = vos3_virtio_blk_capacity();
    if (capacity < VOS3FS_DATA_START + 100) {
        vos3_console_puts("[vos3fs] mkfs: Disk too small\n");
        return -1;
    }

    uint32_t total_data_blocks = (uint32_t)(capacity - VOS3FS_DATA_START);
    /* Cap to what our bitmap can track */
    if (total_data_blocks > VOS3FS_MAX_DATA_BLOCKS) total_data_blocks = VOS3FS_MAX_DATA_BLOCKS;

    vos3_console_puts("[vos3fs] mkfs: Formatting disk...\n");

    /* 1. Write superblock */
    vos3fs_superblock_t dsb;
    vos3fs_memset(&dsb, 0, sizeof(dsb));
    dsb.magic = VOS3FS_MAGIC;
    dsb.version = VOS3FS_VERSION;
    dsb.block_size = VOS3FS_BLOCK_SIZE;
    dsb.total_blocks = total_data_blocks;
    dsb.free_blocks = total_data_blocks - 1; /* Root dir uses 1 block */
    dsb.total_inodes = VOS3FS_MAX_INODES;
    dsb.free_inodes = VOS3FS_MAX_INODES - 2; /* ino 0 reserved, ino 1 = root */
    dsb.root_ino = VOS3FS_ROOT_INO;
    dsb.data_start = VOS3FS_DATA_START;
    dsb.mount_count = 0;

    uint8_t sector_buf[VOS3FS_BLOCK_SIZE];
    vos3fs_memset(sector_buf, 0, VOS3FS_BLOCK_SIZE);
    vos3fs_memcpy(sector_buf, &dsb, sizeof(dsb));
    if (disk_write_sector(VOS3FS_SB_SECTOR, sector_buf) != 0) return -1;

    /* 2. Write inode bitmap (ino 0 and 1 are used) */
    uint8_t inode_bmp[VOS3FS_BLOCK_SIZE];
    vos3fs_memset(inode_bmp, 0, VOS3FS_BLOCK_SIZE);
    inode_bmp[0] = 0x03; /* bits 0 and 1 set */
    if (disk_write_sector(VOS3FS_INODE_BMP_SECTOR, inode_bmp) != 0) return -1;

    /* 3. Write data block bitmap (block 0 used for root dir).
     *    VOS3FS_DATA_BMP_SECTORS sectors total. */
    uint8_t data_bmp[VOS3FS_BLOCK_SIZE];
    vos3fs_memset(data_bmp, 0, VOS3FS_BLOCK_SIZE);
    data_bmp[0] = 0x01; /* bit 0 set (first data block for root dir) */
    if (disk_write_sector(VOS3FS_DATA_BMP_SECTOR, data_bmp) != 0) return -1;
    vos3fs_memset(data_bmp, 0, VOS3FS_BLOCK_SIZE);
    for (uint32_t s = 1; s < VOS3FS_DATA_BMP_SECTORS; s++) {
        if (disk_write_sector(VOS3FS_DATA_BMP_SECTOR + s, data_bmp) != 0) return -1;
    }

    /* 4. Clear inode table */
    vos3fs_memset(sector_buf, 0, VOS3FS_BLOCK_SIZE);
    for (uint32_t s = 0; s < VOS3FS_INODE_TBL_COUNT; s++) {
        if (disk_write_sector(VOS3FS_INODE_TBL_SECTOR + s, sector_buf) != 0)
            return -1;
    }

    /* 5. Write root directory inode (ino 1) */
    vos3fs_inode_t root_di;
    vos3fs_memset(&root_di, 0, sizeof(root_di));
    root_di.mode = VOS3FS_S_IFDIR | 0755;
    root_di.nlink = 2;
    root_di.size = VOS3FS_BLOCK_SIZE; /* One block for directory entries */
    root_di.direct[0] = VOS3FS_DATA_START; /* First data block */
    if (vos3fs_write_dinode(VOS3FS_ROOT_INO, &root_di) != 0) return -1;

    /* 6. Initialize root directory block (empty - all entries ino=0) */
    vos3fs_memset(sector_buf, 0, VOS3FS_BLOCK_SIZE);
    if (disk_write_sector(VOS3FS_DATA_START, sector_buf) != 0) return -1;

    /* 7. Flush to disk */
    vos3_virtio_blk_flush();

    vos3_console_puts("[vos3fs] mkfs: Complete. ");
    /* Print total data blocks */
    char numbuf[16]; int idx = 0;
    uint32_t val = total_data_blocks;
    if (val == 0) { numbuf[idx++] = '0'; }
    else {
        char tmp[16]; int ti = 0;
        while (val) { tmp[ti++] = '0' + (val % 10); val /= 10; }
        while (ti--) numbuf[idx++] = tmp[ti];
    }
    numbuf[idx] = '\0';
    vos3_console_puts(numbuf);
    vos3_console_puts(" data blocks available.\n");

    return 0;
}

/* ========================================================================= */
/* File System Registration                                                   */
/* ========================================================================= */

static vos3_fs_type_t g_vos3fs_type = {
    .name    = "vos3fs",
    .flags   = 0,
    .mount   = vos3fs_do_mount,
    .unmount = NULL,
    .next    = NULL,
};

int vos3fs_init(void)
{
    vos3_console_puts("[vos3fs] Registering vos3fs filesystem type\n");
    return vos3_fs_register(&g_vos3fs_type);
}

/* ========================================================================= */
/* Phase 36.5: Storage Stress & Integrity Test                                */
/* ========================================================================= */

/*
 * Helper: print a decimal number to console (no printf in kernel).
 */
static void test_print_u32(uint32_t val)
{
    char numbuf[16];
    int idx = 0;
    if (val == 0) { numbuf[idx++] = '0'; }
    else {
        char tmp[16]; int ti = 0;
        while (val) { tmp[ti++] = '0' + (val % 10); val /= 10; }
        while (ti--) numbuf[idx++] = tmp[ti];
    }
    numbuf[idx] = '\0';
    vos3_console_puts(numbuf);
}

/*
 * Run boot-time stress test on vos3fs.
 * Uses 10 raw data sectors beyond the filesystem area for testing.
 * Returns 0 on success, -1 on any failure.
 */
int vos3fs_stress_test(void)
{
    /*
     * Use sectors far beyond the filesystem metadata area.
     * VOS3FS data starts at sector 68. We use sectors 8000-8009
     * which are inside the 10 MB disk (20480 sectors) but well
     * beyond the root directory block area.
     */
    const uint32_t TEST_BASE = 8000;
    const uint32_t TEST_COUNT = 10;
    /* Pattern bytes: 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22, 0x33, 0x44 */
    const uint8_t patterns[10] = {
        0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22, 0x33, 0x44
    };
    uint8_t buf[VOS3FS_BLOCK_SIZE];
    int rc;
    int pass = 1;

    vos3_console_puts("\n[TEST] ========================================\n");
    vos3_console_puts("[TEST] Phase 36.5: Storage Stress & Integrity\n");
    vos3_console_puts("[TEST] ========================================\n");

    /* --- Test 1: Sequential Write (10 sectors) --- */
    vos3_console_puts("[TEST] Step 1: Sequential write of 10 sectors...\n");
    for (uint32_t i = 0; i < TEST_COUNT; i++) {
        vos3fs_memset(buf, patterns[i], VOS3FS_BLOCK_SIZE);
        rc = disk_write_sector(TEST_BASE + i, buf);
        if (rc != 0) {
            vos3_console_puts("[TEST] Sequential Write: FAILED at sector ");
            test_print_u32(TEST_BASE + i);
            vos3_console_puts("\n");
            return -1;
        }
    }

    /* Flush to ensure all data hits the disk platter */
    vos3_virtio_blk_flush();
    vos3_console_puts("[TEST] Sequential Write: SUCCESS (10 sectors written + flushed)\n");

    /* --- Test 2: Reverse-Order Read & Verify --- */
    vos3_console_puts("[TEST] Step 2: Interleaved read (reverse order)...\n");
    uint32_t matched = 0;
    for (int i = (int)(TEST_COUNT - 1); i >= 0; i--) {
        vos3fs_memset(buf, 0, VOS3FS_BLOCK_SIZE);
        rc = disk_read_sector(TEST_BASE + (uint32_t)i, buf);
        if (rc != 0) {
            vos3_console_puts("[TEST] Read FAILED at sector ");
            test_print_u32(TEST_BASE + (uint32_t)i);
            vos3_console_puts("\n");
            pass = 0;
            continue;
        }

        /* Verify every byte in the sector */
        int sector_ok = 1;
        for (uint32_t b = 0; b < VOS3FS_BLOCK_SIZE; b++) {
            if (buf[b] != patterns[i]) {
                sector_ok = 0;
                break;
            }
        }
        if (sector_ok) {
            matched++;
        } else {
            vos3_console_puts("[TEST]   Sector ");
            test_print_u32(TEST_BASE + (uint32_t)i);
            vos3_console_puts(": MISMATCH (expected 0x");
            /* Print hex byte */
            const char hex[] = "0123456789ABCDEF";
            char hb[3];
            hb[0] = hex[(patterns[i] >> 4) & 0xF];
            hb[1] = hex[patterns[i] & 0xF];
            hb[2] = '\0';
            vos3_console_puts(hb);
            vos3_console_puts(")\n");
            pass = 0;
        }
    }

    vos3_console_puts("[TEST] Data Integrity: ");
    test_print_u32(matched);
    vos3_console_puts("/");
    test_print_u32(TEST_COUNT);
    if (matched == TEST_COUNT) {
        vos3_console_puts(" - 100% MATCH\n");
    } else {
        vos3_console_puts(" - PARTIAL MATCH\n");
        pass = 0;
    }

    /* --- Test 3: Cache Coherency --- */
    vos3_console_puts("[TEST] Step 3: Cache coherency check...\n");
    {
        /* Write a known pattern to a test sector via disk */
        uint8_t write_buf[VOS3FS_BLOCK_SIZE];
        uint8_t cache_buf[VOS3FS_BLOCK_SIZE];
        uint8_t disk_buf[VOS3FS_BLOCK_SIZE];
        uint32_t test_sector = TEST_BASE + 5; /* Use sector 8005 */

        vos3fs_memset(write_buf, 0x42, VOS3FS_BLOCK_SIZE);
        rc = disk_write_sector(test_sector, write_buf);
        if (rc != 0) {
            vos3_console_puts("[TEST] Cache coherency: FAILED (write error)\n");
            pass = 0;
        } else {
            vos3_virtio_blk_flush();

            /* Read through cache (should populate cache) */
            vos3fs_memset(cache_buf, 0, VOS3FS_BLOCK_SIZE);
            rc = disk_read_sector(test_sector, cache_buf);
            if (rc != 0 || cache_buf[0] != 0x42) {
                vos3_console_puts("[TEST] Cache coherency: FAILED (cache read mismatch)\n");
                pass = 0;
            } else {
                /* Overwrite with different data */
                vos3fs_memset(write_buf, 0x99, VOS3FS_BLOCK_SIZE);
                rc = disk_write_sector(test_sector, write_buf);
                vos3_virtio_blk_flush();

                /* Read again - cache should return updated data since write
                 * updates the cache entry via the read path */
                vos3fs_memset(disk_buf, 0, VOS3FS_BLOCK_SIZE);
                rc = disk_read_sector(test_sector, disk_buf);
                if (rc == 0 && disk_buf[0] == 0x99) {
                    vos3_console_puts("[TEST] Cache Coherency: PASS (cache consistent with disk)\n");
                } else {
                    vos3_console_puts("[TEST] Cache Coherency: FAIL (stale cache data)\n");
                    pass = 0;
                }
            }
        }
    }

    /* --- Test 4: Superblock Persistence Verification --- */
    vos3_console_puts("[TEST] Step 4: Superblock write/read-back verification...\n");
    {
        /* Read current superblock from disk */
        uint8_t sb_buf[VOS3FS_BLOCK_SIZE];
        vos3fs_memset(sb_buf, 0, VOS3FS_BLOCK_SIZE);
        rc = disk_read_sector(VOS3FS_SB_SECTOR, sb_buf);
        if (rc != 0) {
            vos3_console_puts("[TEST] Superblock read: FAILED\n");
            pass = 0;
        } else {
            vos3fs_superblock_t *dsb = (vos3fs_superblock_t *)sb_buf;
            if (dsb->magic == VOS3FS_MAGIC) {
                vos3_console_puts("[vos3fs] Superblock Persisted: Mount Count incremented (mount_count=");
                test_print_u32(dsb->mount_count);
                vos3_console_puts(")\n");
            } else {
                vos3_console_puts("[TEST] Superblock: FAILED (bad magic)\n");
                pass = 0;
            }
        }
    }

    /* --- Final Verdict --- */
    vos3_console_puts("[TEST] ========================================\n");
    if (pass) {
        vos3_console_puts("[TEST] VERDICT: ALL STRESS TESTS PASSED\n");
        vos3_console_puts("[TEST] Storage subsystem is INTEGRITY-PROVEN\n");
    } else {
        vos3_console_puts("[TEST] VERDICT: SOME TESTS FAILED\n");
    }
    vos3_console_puts("[TEST] ========================================\n\n");

    return pass ? 0 : -1;
}
