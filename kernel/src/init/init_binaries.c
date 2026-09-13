/**
 * @file init_binaries.c
 * @brief Initialize ramfs with embedded user-space binaries
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/vfs.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* Include generated embedded binaries */
#include "embedded_bins.h"

/**
 * @brief Create a file in ramfs with the given content
 */
static int create_ramfs_file(const char* path, const uint8_t* data, size_t size)
{
    /* Open file with create and truncate flags */
    int fd = vos3_open(path, VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC,
                       VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                       VOS3_S_IROTH | VOS3_S_IXOTH);
    if (fd < 0) {
        return fd;
    }

    /* Write data */
    size_t written = 0;
    while (written < size) {
        int64_t w = vos3_write(fd, data + written, size - written);
        if (w < 0) {
            vos3_close(fd);
            return (int)w;
        }
        written += (size_t)w;
    }

    vos3_close(fd);
    return VOS3_FS_OK;
}

/**
 * @brief Build path from prefix and name
 */
static void build_path(char* dest, size_t dest_size, const char* prefix, const char* name)
{
    size_t prefix_len = strlen(prefix);
    size_t name_len = strlen(name);

    if (prefix_len + name_len + 1 >= dest_size) {
        dest[0] = '\0';
        return;
    }

    memcpy(dest, prefix, prefix_len);
    memcpy(dest + prefix_len, name, name_len);
    dest[prefix_len + name_len] = '\0';
}

/**
 * @brief Initialize ramfs with embedded binaries
 *
 * Called after VFS is initialized to populate /bin with
 * embedded user-space programs.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_init_embedded_binaries(void)
{
    VOS3_INFO("Loading embedded binaries...");

    /* Create /bin and /sbin directories */
    (void)vos3_mkdir("/bin", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                             VOS3_S_IROTH | VOS3_S_IXOTH);
    (void)vos3_mkdir("/sbin", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                              VOS3_S_IROTH | VOS3_S_IXOTH);
    (void)vos3_mkdir("/etc", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                             VOS3_S_IROTH | VOS3_S_IXOTH);

    /* Load each embedded binary */
    size_t loaded = 0;
    for (size_t i = 0; i < g_embedded_binaries_count; i++) {
        const embedded_binary_t* bin = &g_embedded_binaries[i];

        if (bin->name == NULL || bin->data == NULL) {
            continue;
        }

        /* Create path /bin/<name> */
        char path[VOS3_PATH_MAX];
        build_path(path, sizeof(path), "/bin/", bin->name);

        int result = create_ramfs_file(path, bin->data, bin->size);
        if (result == VOS3_FS_OK) {
            VOS3_INFO("  Loaded: %s (%u bytes)", path, (unsigned)bin->size);
            loaded++;
        } else {
            VOS3_ERROR("  Failed to load %s (error %d)", path, result);
        }
    }

    VOS3_INFO("Loaded %u embedded binaries", (unsigned)loaded);

    return 0;
}
