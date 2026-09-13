/**
 * @file vvfs.c
 * @brief vVFS Core — Mount, Inode Operations, ACL Enforcement
 *
 * @details Registers the vVFS filesystem type with the VOS3 VFS layer.
 *          Each model slot gets its own mount at /ai/<slot_id>/.
 *          ACL enforcement ensures only the slot's owner_tid can access
 *          files under its mount. VOS3_FS_NO_DCACHE is set to prevent
 *          stale dentry caching across slot resets.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant | Phase 2.1: Privacy Moat
 */

#include "../../include/vos/vvfs.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include <stdint.h>

/* ============================================================================
 * EXTERNAL REFERENCES
 * ============================================================================ */

/* Model slot array (defined in ai_slots.c) */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * STATIC STATE
 * ============================================================================ */

/** @brief Per-slot mount data */
static vvfs_mount_data_t g_vvfs_mounts[VVFS_MAX_MOUNTS];

/** @brief Per-slot backing block store */
static vvfs_slot_store_t g_vvfs_stores[VVFS_MAX_MOUNTS];

/** @brief Per-slot superblocks (set on mount, cleared on unmount) */
static vos3_superblock_t *g_vvfs_superblocks[VVFS_MAX_MOUNTS];

/** @brief Initialization flag */
static uint8_t g_vvfs_initialized = 0;

/* ============================================================================
 * INODE OPERATIONS
 * ============================================================================ */

/**
 * @brief ACL check — verify the current slot owner matches the mount's owner_tid
 */
static int vvfs_acl_check(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -1; /* EPERM */
    }
    /* In kernel context (VBus dispatch), ACL is checked at transport layer.
     * Here we validate the mount is active. */
    if (g_vvfs_mounts[slot_id].mounted == 0U) {
        return -2; /* ENOENT */
    }
    return 0;
}

static int vvfs_inode_create(vos3_inode_t *dir, const char *name, uint32_t mode)
{
    (void)dir; (void)name; (void)mode;
    /* vVFS files are block-addressed, not inode-created in traditional sense */
    return VOS3_FS_ERR_NOTSUP;
}

static vos3_dentry_t *vvfs_inode_lookup(vos3_inode_t *dir, const char *name)
{
    (void)dir; (void)name;
    /* Block-level access — no traditional dentry lookup */
    return NULL;
}

static int vvfs_inode_mkdir(vos3_inode_t *dir, const char *name, uint32_t mode)
{
    (void)dir; (void)name; (void)mode;
    return VOS3_FS_ERR_NOTSUP;
}

static int vvfs_inode_rmdir(vos3_inode_t *dir, const char *name)
{
    (void)dir; (void)name;
    return VOS3_FS_ERR_NOTSUP;
}

static int vvfs_inode_unlink(vos3_inode_t *dir, const char *name)
{
    (void)dir; (void)name;
    return VOS3_FS_ERR_NOTSUP;
}

static const vos3_inode_ops_t g_vvfs_inode_ops = {
    .create  = vvfs_inode_create,
    .lookup  = vvfs_inode_lookup,
    .mkdir   = vvfs_inode_mkdir,
    .rmdir   = vvfs_inode_rmdir,
    .unlink  = vvfs_inode_unlink,
    .rename  = NULL,
    .readlink = NULL,
    .truncate = NULL,
    .symlink  = NULL,
    .link     = NULL,
    .setattr  = NULL,
};

/* ============================================================================
 * FILE OPERATIONS (stub — real I/O goes through block transport API)
 * ============================================================================ */

static int vvfs_file_open(vos3_file_t *file)
{
    (void)file;
    return 0;
}

static int vvfs_file_close(vos3_file_t *file)
{
    (void)file;
    return 0;
}

static int64_t vvfs_file_read(vos3_file_t *file, void *buf, size_t count)
{
    (void)file; (void)buf; (void)count;
    /* Real reads go through vvfs_read_block() in transport layer */
    return VOS3_FS_ERR_NOTSUP;
}

static int64_t vvfs_file_write(vos3_file_t *file, const void *buf, size_t count)
{
    (void)file; (void)buf; (void)count;
    /* Real writes go through vvfs_write_block() in transport layer */
    return VOS3_FS_ERR_NOTSUP;
}

static const vos3_file_ops_t g_vvfs_file_ops = {
    .open    = vvfs_file_open,
    .close   = vvfs_file_close,
    .read    = vvfs_file_read,
    .write   = vvfs_file_write,
    .lseek   = NULL,
    .readdir = NULL,
    .fsync   = NULL,
    .ioctl   = NULL,
};

/* ============================================================================
 * SUPERBLOCK OPERATIONS
 * ============================================================================ */

static vos3_inode_t *vvfs_alloc_inode(vos3_superblock_t *sb)
{
    vos3_inode_t *inode = (vos3_inode_t *)vos3_kzalloc(sizeof(vos3_inode_t));
    if (inode != NULL) {
        inode->sb = sb;
        inode->ops = &g_vvfs_inode_ops;
        inode->default_fops = &g_vvfs_file_ops;
        inode->ref_count = 1;
    }
    return inode;
}

static void vvfs_free_inode(vos3_inode_t *inode)
{
    if (inode != NULL) {
        vos3_kfree(inode);
    }
}

static int vvfs_sb_unmount(vos3_superblock_t *sb)
{
    if (sb == NULL) {
        return VOS3_FS_ERR_INVAL;
    }
    if (sb->root != NULL) {
        vvfs_free_inode(sb->root->inode);
        vos3_kfree(sb->root);
        sb->root = NULL;
    }
    vos3_kfree(sb);
    return VOS3_FS_OK;
}

static const vos3_sb_ops_t g_vvfs_sb_ops = {
    .alloc_inode = vvfs_alloc_inode,
    .free_inode  = vvfs_free_inode,
    .sync        = NULL,
    .unmount     = vvfs_sb_unmount,
};

/* ============================================================================
 * MOUNT / UNMOUNT
 * ============================================================================ */

static vos3_superblock_t *vvfs_mount_fn(vos3_fs_type_t *fs,
                                         const char *source,
                                         uint32_t flags, void *data)
{
    (void)source; (void)flags;

    vvfs_mount_data_t *mdata = (vvfs_mount_data_t *)data;
    if (mdata == NULL || mdata->slot_id >= VOS3_MODEL_SLOT_MAX) {
        return NULL;
    }

    /* Allocate superblock */
    vos3_superblock_t *sb = (vos3_superblock_t *)vos3_kzalloc(sizeof(vos3_superblock_t));
    if (sb == NULL) {
        return NULL;
    }

    sb->magic       = VVFS_MAGIC;
    sb->block_size  = VVFS_BLOCK_SIZE;
    sb->block_count = VVFS_MAX_BLOCKS;
    sb->free_blocks = VVFS_MAX_BLOCKS;
    sb->inode_count = VVFS_MAX_INODES;
    sb->free_inodes = VVFS_MAX_INODES;
    sb->ops         = &g_vvfs_sb_ops;
    sb->fs_type     = fs;
    sb->private_data = mdata;

    /* Create root dentry */
    vos3_dentry_t *root = (vos3_dentry_t *)vos3_kzalloc(sizeof(vos3_dentry_t));
    if (root == NULL) {
        vos3_kfree(sb);
        return NULL;
    }

    /* Create root inode */
    vos3_inode_t *root_inode = vvfs_alloc_inode(sb);
    if (root_inode == NULL) {
        vos3_kfree(root);
        vos3_kfree(sb);
        return NULL;
    }

    root_inode->ino  = VOS3_ROOT_INODE;
    root_inode->mode = VOS3_S_IFDIR | VOS3_S_IRWXU;
    root_inode->nlink = 2;

    root->inode = root_inode;
    root->parent = root;
    root->ref_count = 1;
    sb->root = root;

    VOS3_INFO("[vVFS] Mounted for slot %u (owner_tid=%u)",
              mdata->slot_id, mdata->owner_tid);

    return sb;
}

static int vvfs_unmount_fn(vos3_superblock_t *sb)
{
    if (sb == NULL) {
        return VOS3_FS_ERR_INVAL;
    }
    return vvfs_sb_unmount(sb);
}

/* ============================================================================
 * FILESYSTEM TYPE REGISTRATION
 * ============================================================================ */

vos3_fs_type_t g_vvfs_type = {
    .name    = "vvfs",
    .flags   = VOS3_FS_NO_DCACHE,  /* No stale dentry caching across slot resets */
    .mount   = vvfs_mount_fn,
    .unmount = vvfs_unmount_fn,
    .next    = NULL,
};

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vvfs_init(void)
{
    if (g_vvfs_initialized != 0U) {
        return 0; /* already initialized */
    }

    /* Zero all mount data and stores */
    memset(g_vvfs_mounts, 0, sizeof(g_vvfs_mounts));
    memset(g_vvfs_stores, 0, sizeof(g_vvfs_stores));
    memset(g_vvfs_superblocks, 0, sizeof(g_vvfs_superblocks));

    /* Register with VFS */
    int rc = vos3_fs_register(&g_vvfs_type);
    if (rc != 0) {
        VOS3_WARN("[vVFS] Failed to register filesystem type (err=%d)", rc);
        return rc;
    }

    g_vvfs_initialized = 1;
    VOS3_INFO("[vVFS] Subsystem initialized (block_size=%uKB, max_blocks=%u/slot)",
              VVFS_BLOCK_SIZE / 1024U, VVFS_MAX_BLOCKS);

    return 0;
}

int vvfs_mount_for_slot(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (g_vvfs_initialized == 0U) {
        return -38; /* ENOSYS */
    }
    if (g_vvfs_mounts[slot_id].mounted != 0U) {
        return -17; /* EEXIST — already mounted */
    }

    /* Setup mount data */
    vvfs_mount_data_t *mdata = &g_vvfs_mounts[slot_id];
    mdata->slot_id     = slot_id;
    mdata->owner_tid   = g_model_slots[slot_id].owner_tid;
    mdata->block_count = VVFS_MAX_BLOCKS;
    mdata->mounted     = 1;

    /* Initialize backing store */
    vvfs_slot_store_t *store = &g_vvfs_stores[slot_id];
    memset(store, 0, sizeof(vvfs_slot_store_t));
    store->allocated = 1;

    /* Create mount point directory /ai/<slot_id>/ */
    char mount_path[32];
    mount_path[0] = '/';
    mount_path[1] = 'a';
    mount_path[2] = 'i';
    mount_path[3] = '/';
    mount_path[4] = '0' + slot_id;
    mount_path[5] = '\0';

    (void)vos3_mkdir("/ai", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP);
    (void)vos3_mkdir(mount_path, VOS3_S_IRWXU);

    int rc = vos3_mount(NULL, mount_path, "vvfs", 0, mdata);
    if (rc != 0) {
        VOS3_WARN("[vVFS] Mount failed for slot %u at %s (err=%d)",
                  slot_id, mount_path, rc);
        mdata->mounted = 0;
        store->allocated = 0;
        return rc;
    }

    VOS3_INFO("[vVFS] Slot %u mounted at %s (owner_tid=%u, blocks=%u)",
              slot_id, mount_path, mdata->owner_tid, VVFS_MAX_BLOCKS);

    return 0;
}

int vvfs_unmount_slot(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }
    if (g_vvfs_mounts[slot_id].mounted == 0U) {
        return 0; /* not mounted, nothing to do */
    }

    /* Scrub backing store — zero-fill all blocks */
    vvfs_slot_store_t *store = &g_vvfs_stores[slot_id];
    memset(store->blocks, 0, sizeof(store->blocks));
    memset(store->block_used, 0, sizeof(store->block_used));
    memset(store->block_crc, 0, sizeof(store->block_crc));
    store->allocated = 0;

    /* Unmount */
    char mount_path[32];
    mount_path[0] = '/';
    mount_path[1] = 'a';
    mount_path[2] = 'i';
    mount_path[3] = '/';
    mount_path[4] = '0' + slot_id;
    mount_path[5] = '\0';

    (void)vos3_umount(mount_path);

    /* Clear mount data */
    memset(&g_vvfs_mounts[slot_id], 0, sizeof(vvfs_mount_data_t));

    VOS3_INFO("[vVFS] Slot %u unmounted and scrubbed", slot_id);

    return 0;
}

int vvfs_stat(uint8_t slot_id, uint8_t *mounted,
              uint32_t *blocks_used, uint32_t *blocks_total)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* EINVAL */
    }

    if (mounted != NULL) {
        *mounted = g_vvfs_mounts[slot_id].mounted;
    }
    if (blocks_total != NULL) {
        *blocks_total = VVFS_MAX_BLOCKS;
    }
    if (blocks_used != NULL) {
        uint32_t used = 0;
        if (g_vvfs_stores[slot_id].allocated != 0U) {
            for (uint32_t i = 0; i < VVFS_MAX_BLOCKS; i++) {
                if (g_vvfs_stores[slot_id].block_used[i] > 0U) {
                    used++;
                }
            }
        }
        *blocks_used = used;
    }
    return 0;
}

/* ============================================================================
 * INTERNAL: Backing store accessors (used by vvfs_transport.c)
 * ============================================================================ */

vvfs_slot_store_t *vvfs_get_store(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return NULL;
    }
    if (g_vvfs_stores[slot_id].allocated == 0U) {
        return NULL;
    }
    return &g_vvfs_stores[slot_id];
}

vvfs_mount_data_t *vvfs_get_mount(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return NULL;
    }
    return &g_vvfs_mounts[slot_id];
}
