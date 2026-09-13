/**
 * @file procfs.c
 * @brief VOS3 /proc Virtual Filesystem
 *
 * @details Virtual filesystem providing process and system information.
 *          Generates content on-the-fly from kernel data structures.
 *          Implements /proc/<pid>/status, /proc/<pid>/cmdline,
 *          /proc/meminfo, /proc/uptime, /proc/self/status.
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/procfs.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/pmm.h"

/* ============================================================================
 * PROCFS INTERNAL TYPES
 * ============================================================================ */

/** @brief Type of procfs node */
typedef enum procfs_node_type {
    PROCFS_DIR      = 0,    /**< Directory */
    PROCFS_STATUS   = 1,    /**< /proc/<pid>/status */
    PROCFS_CMDLINE  = 2,    /**< /proc/<pid>/cmdline */
    PROCFS_MEMINFO  = 3,    /**< /proc/meminfo */
    PROCFS_UPTIME   = 4,    /**< /proc/uptime */
    PROCFS_SELF     = 5,    /**< /proc/self (symlink-like to current pid) */
    PROCFS_EXE      = 6,    /**< /proc/<pid>/exe */
    PROCFS_MAPS     = 7,    /**< /proc/<pid>/maps */
} procfs_node_type_t;

/** @brief Procfs inode private data */
typedef struct procfs_priv {
    procfs_node_type_t  type;
    vos3_pid_t          pid;        /**< PID for per-process nodes */
} procfs_priv_t;

/* ============================================================================
 * FORWARD DECLARATIONS
 * ============================================================================ */

static vos3_dentry_t* procfs_lookup(vos3_inode_t* dir, const char* name);
static int procfs_readlink(vos3_inode_t* inode, char* buf, size_t size);
static int procfs_inode_rename(vos3_inode_t* old_dir, const char* old_name,
                                vos3_inode_t* new_dir, const char* new_name);
static int64_t procfs_file_read(vos3_file_t* file, void* buf, size_t count);
static int procfs_file_open(vos3_file_t* file);
static int procfs_file_close(vos3_file_t* file);
static int procfs_readdir(vos3_file_t* file, vos3_dirent_t* dirents,
                           size_t count, size_t* out_count);
static vos3_superblock_t* procfs_mount_fn(vos3_fs_type_t* fs, const char* source,
                                            uint32_t flags, void* data);
static int procfs_unmount(vos3_superblock_t* sb);

/* ============================================================================
 * OPERATIONS
 * ============================================================================ */

static const vos3_inode_ops_t g_procfs_inode_ops = {
    .create   = NULL,
    .lookup   = procfs_lookup,
    .mkdir    = NULL,
    .rmdir    = NULL,
    .unlink   = NULL,
    .rename   = procfs_inode_rename,
    .readlink = procfs_readlink,
    .truncate = NULL,
};

static const vos3_file_ops_t g_procfs_file_ops = {
    .open    = procfs_file_open,
    .close   = procfs_file_close,
    .read    = procfs_file_read,
    .write   = NULL,
    .lseek   = NULL,
    .readdir = procfs_readdir,
    .fsync   = NULL,
    .ioctl   = NULL,
};

static const vos3_sb_ops_t g_procfs_sb_ops = {
    .alloc_inode = NULL,
    .free_inode  = NULL,
    .sync        = NULL,
    .unmount     = procfs_unmount,
};

/**
 * @brief Procfs does not support rename — return ENOTSUP
 */
static int procfs_inode_rename(vos3_inode_t* old_dir, const char* old_name,
                                vos3_inode_t* new_dir, const char* new_name)
{
    (void)old_dir;
    (void)old_name;
    (void)new_dir;
    (void)new_name;
    return VOS3_FS_ERR_NOTSUP;
}

/* ============================================================================
 * STATE
 * ============================================================================ */

static vos3_superblock_t* g_procfs_sb = NULL;
static uint32_t g_procfs_next_ino = 0x50000;

static uint32_t procfs_alloc_ino(void)
{
    return g_procfs_next_ino++;
}

/* ============================================================================
 * HELPERS — Integer to String
 * ============================================================================ */

static int procfs_itoa(int64_t val, char* buf, int bufsz)
{
    int neg = 0;
    if (val < 0) { neg = 1; val = -val; }

    char tmp[20];
    int ti = 0;
    if (val == 0) { tmp[ti++] = '0'; }
    else {
        while (val && ti < 20) {
            tmp[ti++] = '0' + (char)(val % 10);
            val /= 10;
        }
    }

    int idx = 0;
    if (neg && idx < bufsz - 1) buf[idx++] = '-';
    while (ti-- > 0 && idx < bufsz - 1) buf[idx++] = tmp[ti];
    buf[idx] = '\0';
    return idx;
}

static int procfs_utoa(uint64_t val, char* buf, int bufsz)
{
    char tmp[20];
    int ti = 0;
    if (val == 0) { tmp[ti++] = '0'; }
    else {
        while (val && ti < 20) {
            tmp[ti++] = '0' + (char)(val % 10);
            val /= 10;
        }
    }

    int idx = 0;
    while (ti-- > 0 && idx < bufsz - 1) buf[idx++] = tmp[ti];
    buf[idx] = '\0';
    return idx;
}

/* ============================================================================
 * CONTENT GENERATORS
 * ============================================================================ */

/**
 * @brief Generate /proc/<pid>/status content
 */
static int generate_status(vos3_pid_t pid, char* buf, size_t bufsz)
{
    vos3_task_t* task = vos3_task_find_by_pid(pid);
    if (task == NULL) return 0;

    int len = 0;
    char nb[20];

    /* Name */
    const char* k = "Name:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    const char* n = task->name;
    while (*n && len < (int)bufsz - 1) buf[len++] = *n++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* State */
    k = "State:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    const char* st = vos3_task_state_name(task->state);
    while (*st && len < (int)bufsz - 1) buf[len++] = *st++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* Pid */
    k = "Pid:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa(task->pid, nb, 20);
    const char* p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* PPid */
    k = "PPid:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa(task->parent ? task->parent->pid : 0, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* Uid */
    k = "Uid:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa(task->uid, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* Flags */
    k = "Flags:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    if (task->flags & VOS3_TASK_FLAG_KERNEL) {
        const char* f = "kernel";
        while (*f && len < (int)bufsz - 1) buf[len++] = *f++;
    } else {
        const char* f = "user";
        while (*f && len < (int)bufsz - 1) buf[len++] = *f++;
    }
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* Context switches */
    k = "CtxSwitches:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa(task->context_switches, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    buf[len] = '\0';
    return len;
}

/**
 * @brief Generate /proc/<pid>/cmdline content
 */
static int generate_cmdline(vos3_pid_t pid, char* buf, size_t bufsz)
{
    vos3_task_t* task = vos3_task_find_by_pid(pid);
    if (task == NULL) return 0;

    size_t nlen = strlen(task->name);
    if (nlen >= bufsz) nlen = bufsz - 1;
    memcpy(buf, task->name, nlen);
    buf[nlen] = '\n';
    return (int)(nlen + 1);
}

/**
 * @brief Generate /proc/meminfo content
 */
static int generate_meminfo(char* buf, size_t bufsz)
{
    size_t total = vos3_pmm_total_pages_count();
    size_t free_p = vos3_pmm_free_pages_count();
    size_t used = total - free_p;

    int len = 0;
    char nb[20];
    const char* k;
    const char* p;

    /* Total (in KB) */
    k = "MemTotal:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa((uint64_t)(total * 4), nb, 20);  /* 4KB pages */
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    k = " kB\n";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;

    /* Free */
    k = "MemFree:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa((uint64_t)(free_p * 4), nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    k = " kB\n";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;

    /* Used */
    k = "MemUsed:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa((uint64_t)(used * 4), nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    k = " kB\n";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;

    /* Pages */
    k = "TotalPages:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa((uint64_t)total, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    k = "FreePages:\t";
    while (*k && len < (int)bufsz - 1) buf[len++] = *k++;
    procfs_utoa((uint64_t)free_p, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    buf[len] = '\0';
    return len;
}

/**
 * @brief Generate /proc/uptime content
 */
static int generate_uptime(char* buf, size_t bufsz)
{
    uint64_t ms = vos3_sched_get_uptime_ms();
    uint64_t secs = ms / 1000;
    uint64_t frac = (ms % 1000) / 10;  /* centiseconds */

    int len = 0;
    char nb[20];
    const char* p;

    procfs_utoa(secs, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '.';
    /* two-digit fraction */
    if (frac < 10 && len < (int)bufsz - 1) buf[len++] = '0';
    procfs_utoa(frac, nb, 20);
    p = nb;
    while (*p && len < (int)bufsz - 1) buf[len++] = *p++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    buf[len] = '\0';
    return len;
}

/**
 * @brief procfs readlink — handles /proc/<pid>/exe symlink
 */
static int procfs_readlink(vos3_inode_t* inode, char* buf, size_t size)
{
    if (inode == NULL || buf == NULL || size == 0) return -22; /* EINVAL */

    procfs_priv_t* priv = (procfs_priv_t*)inode->private_data;
    if (priv == NULL) return -22;

    if (priv->type == PROCFS_EXE) {
        vos3_task_t* task = vos3_task_find_by_pid(priv->pid);
        if (task == NULL) return -3; /* ESRCH */

        const char* p = task->exe_path;
        if (p[0] == '\0') return -22;

        size_t plen = strlen(p);
        if (plen > size) plen = size;
        memcpy(buf, p, plen);
        return (int)plen;
    }

    return -22; /* EINVAL — not a symlink node */
}

/**
 * @brief Generate /proc/<pid>/exe content — returns the executable path
 */
static int generate_exe(vos3_pid_t pid, char* buf, size_t bufsz)
{
    vos3_task_t* task = vos3_task_find_by_pid(pid);
    if (task == NULL) return 0;

    const char* p = task->exe_path;
    if (p[0] == '\0') p = "(none)";

    size_t plen = strlen(p);
    if (plen >= bufsz) plen = bufsz - 1;
    memcpy(buf, p, plen);
    buf[plen] = '\0';
    return (int)plen;
}

/**
 * @brief Generate /proc/<pid>/maps content (simplified)
 *
 * Outputs minimal Linux-format /proc/pid/maps lines.
 * Real implementation would iterate VMAs; we output the known
 * fixed memory regions from VOS3's memory map.
 */
static int generate_maps(vos3_pid_t pid, char* buf, size_t bufsz)
{
    vos3_task_t* task = vos3_task_find_by_pid(pid);
    if (task == NULL) return 0;

    int len = 0;
    const char* exe = task->exe_path[0] ? task->exe_path : "[anon]";

    /* Text segment (approximate) */
    const char* line1 = "00400000-00402000 r-xp 00000000 00:00 0 ";
    while (*line1 && len < (int)bufsz - 1) buf[len++] = *line1++;
    while (*exe && len < (int)bufsz - 1) buf[len++] = *exe++;
    if (len < (int)bufsz - 1) buf[len++] = '\n';

    /* Data segment */
    const char* line2 = "00402000-00404000 rw-p 00000000 00:00 0 [heap]\n";
    while (*line2 && len < (int)bufsz - 1) buf[len++] = *line2++;

    /* Stack */
    const char* line3 = "7ffffff00000-7ffffff10000 rw-p 00000000 00:00 0 [stack]\n";
    while (*line3 && len < (int)bufsz - 1) buf[len++] = *line3++;

    buf[len] = '\0';
    return len;
}

/* ============================================================================
 * INODE / DENTRY HELPERS
 * ============================================================================ */

static vos3_inode_t* procfs_create_inode(uint32_t mode, procfs_node_type_t type,
                                          vos3_pid_t pid)
{
    vos3_inode_t* inode = (vos3_inode_t*)vos3_kzalloc(sizeof(vos3_inode_t));
    if (inode == NULL) return NULL;

    procfs_priv_t* priv = (procfs_priv_t*)vos3_kzalloc(sizeof(procfs_priv_t));
    if (priv == NULL) { vos3_kfree(inode); return NULL; }

    priv->type = type;
    priv->pid = pid;

    inode->ino = procfs_alloc_ino();
    inode->mode = mode;
    inode->nlink = 1;
    inode->ref_count = 1;
    inode->sb = g_procfs_sb;
    inode->ops = &g_procfs_inode_ops;
    inode->default_fops = &g_procfs_file_ops;
    inode->private_data = priv;
    vos3_mutex_init(&inode->lock, "procfs_ino");

    return inode;
}

static vos3_dentry_t* procfs_create_dentry(const char* name, vos3_inode_t* inode)
{
    vos3_dentry_t* dentry = (vos3_dentry_t*)vos3_kzalloc(sizeof(vos3_dentry_t));
    if (dentry == NULL) return NULL;

    size_t len = strlen(name);
    if (len >= VOS3_NAME_MAX) len = VOS3_NAME_MAX - 1;
    memcpy(dentry->name, name, len);
    dentry->name[len] = '\0';
    dentry->inode = inode;
    dentry->ref_count = 1;
    vos3_mutex_init(&dentry->lock, "procfs_de");

    return dentry;
}

/* ============================================================================
 * LOOKUP — The core of procfs
 *
 * /proc/meminfo, /proc/uptime → static files
 * /proc/<pid>                  → directory for that PID
 * /proc/<pid>/status           → process status
 * /proc/<pid>/cmdline          → command line
 * /proc/self                   → alias for current PID
 * ============================================================================ */

static int is_number(const char* s)
{
    if (!s || !*s) return 0;
    while (*s) {
        if (*s < '0' || *s > '9') return 0;
        s++;
    }
    return 1;
}

static uint32_t parse_uint(const char* s)
{
    uint32_t v = 0;
    while (*s >= '0' && *s <= '9') {
        v = v * 10 + (uint32_t)(*s - '0');
        s++;
    }
    return v;
}

static vos3_dentry_t* procfs_lookup(vos3_inode_t* dir, const char* name)
{
    if (dir == NULL || name == NULL) return NULL;

    procfs_priv_t* dpriv = (procfs_priv_t*)dir->private_data;
    if (dpriv == NULL) return NULL;

    /* Top-level /proc/ directory */
    if (dpriv->type == PROCFS_DIR && dpriv->pid == 0) {
        /* /proc/meminfo */
        if (strcmp(name, "meminfo") == 0) {
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFREG | 0444, PROCFS_MEMINFO, 0);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("meminfo", ino);
        }

        /* /proc/uptime */
        if (strcmp(name, "uptime") == 0) {
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFREG | 0444, PROCFS_UPTIME, 0);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("uptime", ino);
        }

        /* /proc/self → maps to current pid (no dentry caching on procfs) */
        if (strcmp(name, "self") == 0) {
            vos3_task_t* cur = vos3_sched_current();
            vos3_pid_t pid = cur ? cur->pid : 1;
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFDIR | 0555, PROCFS_DIR, pid);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("self", ino);
        }

        /* /proc/<pid> — numeric directory */
        if (is_number(name)) {
            uint32_t pid = parse_uint(name);
            vos3_task_t* task = vos3_task_find_by_pid(pid);
            if (task == NULL) return NULL;
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFDIR | 0555, PROCFS_DIR, pid);
            if (ino == NULL) return NULL;
            return procfs_create_dentry(name, ino);
        }

        return NULL;
    }

    /* Per-process directory /proc/<pid>/ */
    if (dpriv->type == PROCFS_DIR && dpriv->pid != 0) {
        vos3_pid_t pid = dpriv->pid;

        if (strcmp(name, "status") == 0) {
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFREG | 0444, PROCFS_STATUS, pid);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("status", ino);
        }

        if (strcmp(name, "cmdline") == 0) {
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFREG | 0444, PROCFS_CMDLINE, pid);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("cmdline", ino);
        }

        if (strcmp(name, "exe") == 0) {
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFREG | 0444, PROCFS_EXE, pid);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("exe", ino);
        }

        if (strcmp(name, "maps") == 0) {
            vos3_inode_t* ino = procfs_create_inode(VOS3_S_IFREG | 0444, PROCFS_MAPS, pid);
            if (ino == NULL) return NULL;
            return procfs_create_dentry("maps", ino);
        }

        return NULL;
    }

    return NULL;
}

/* ============================================================================
 * FILE OPERATIONS
 * ============================================================================ */

static int procfs_file_open(vos3_file_t* file)
{
    (void)file;
    return VOS3_FS_OK;
}

static int procfs_file_close(vos3_file_t* file)
{
    if (file == NULL) return VOS3_FS_ERR_INVAL;

    if (file->inode != NULL) {
        /* Free the procfs private data */
        if (file->inode->private_data != NULL) {
            vos3_kfree(file->inode->private_data);
            file->inode->private_data = NULL;
        }
        if (file->inode->ref_count > 0) file->inode->ref_count--;
        if (file->inode->ref_count == 0) {
            vos3_mutex_destroy(&file->inode->lock);
            vos3_kfree(file->inode);
        }
    }

    if (file->dentry != NULL) {
        if (file->dentry->ref_count > 0) file->dentry->ref_count--;
        if (file->dentry->ref_count == 0) {
            vos3_mutex_destroy(&file->dentry->lock);
            vos3_kfree(file->dentry);
        }
    }

    vos3_mutex_destroy(&file->lock);
    vos3_kfree(file);
    return VOS3_FS_OK;
}

static int64_t procfs_file_read(vos3_file_t* file, void* buf, size_t count)
{
    if (file == NULL || buf == NULL || file->inode == NULL) return VOS3_FS_ERR_INVAL;

    procfs_priv_t* priv = (procfs_priv_t*)file->inode->private_data;
    if (priv == NULL) return VOS3_FS_ERR_INVAL;

    /* Generate content into temporary buffer */
    char content[1024];
    int content_len = 0;

    switch (priv->type) {
        case PROCFS_STATUS:
            content_len = generate_status(priv->pid, content, sizeof(content));
            break;
        case PROCFS_CMDLINE:
            content_len = generate_cmdline(priv->pid, content, sizeof(content));
            break;
        case PROCFS_MEMINFO:
            content_len = generate_meminfo(content, sizeof(content));
            break;
        case PROCFS_UPTIME:
            content_len = generate_uptime(content, sizeof(content));
            break;
        case PROCFS_EXE:
            content_len = generate_exe(priv->pid, content, sizeof(content));
            break;
        case PROCFS_MAPS:
            content_len = generate_maps(priv->pid, content, sizeof(content));
            break;
        default:
            return 0;
    }

    if (content_len <= 0) return 0;

    /* Apply file position */
    if (file->pos >= content_len) return 0;

    size_t avail = (size_t)(content_len - (int)file->pos);
    if (count > avail) count = avail;

    memcpy(buf, content + file->pos, count);
    file->pos += (int64_t)count;

    return (int64_t)count;
}

static int procfs_readdir(vos3_file_t* file, vos3_dirent_t* dirents,
                           size_t count, size_t* out_count)
{
    if (file == NULL || dirents == NULL || out_count == NULL)
        return VOS3_FS_ERR_INVAL;
    if (file->inode == NULL) return VOS3_FS_ERR_INVAL;

    procfs_priv_t* priv = (procfs_priv_t*)file->inode->private_data;
    if (priv == NULL || priv->type != PROCFS_DIR) return VOS3_FS_ERR_NOTDIR;

    size_t idx = 0;
    size_t pos = 0;
    size_t start = (size_t)file->pos;

    if (priv->pid == 0) {
        /* Top-level /proc/ — list PIDs + special files */

        /* meminfo */
        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_REG;
            memcpy(dirents[idx].name, "meminfo", 8);
            idx++;
        }
        pos++;

        /* uptime */
        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_REG;
            memcpy(dirents[idx].name, "uptime", 7);
            idx++;
        }
        pos++;

        /* self */
        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_DIR;
            memcpy(dirents[idx].name, "self", 5);
            idx++;
        }
        pos++;

        /* Enumerate running tasks as PID directories */
        for (uint32_t pid = 0; pid < VOS3_MAX_TASKS && idx < count; pid++) {
            vos3_task_t* task = vos3_task_get(pid);
            if (task == NULL) continue;
            if (task->state == VOS3_TASK_DEAD) continue;

            if (pos >= start) {
                dirents[idx].ino = task->pid;
                dirents[idx].type = VOS3_FT_DIR;
                /* Convert pid to string */
                char nb[12];
                procfs_utoa(task->pid, nb, 12);
                size_t nlen = strlen(nb);
                if (nlen >= VOS3_NAME_MAX) nlen = VOS3_NAME_MAX - 1;
                memcpy(dirents[idx].name, nb, nlen);
                dirents[idx].name[nlen] = '\0';
                idx++;
            }
            pos++;
        }
    } else {
        /* Per-process /proc/<pid>/ directory */
        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_REG;
            memcpy(dirents[idx].name, "status", 7);
            idx++;
        }
        pos++;

        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_REG;
            memcpy(dirents[idx].name, "cmdline", 8);
            idx++;
        }
        pos++;

        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_REG;
            memcpy(dirents[idx].name, "exe", 4);
            idx++;
        }
        pos++;

        if (pos >= start && idx < count) {
            dirents[idx].ino = 0;
            dirents[idx].type = VOS3_FT_REG;
            memcpy(dirents[idx].name, "maps", 5);
            idx++;
        }
        pos++;
    }

    file->pos = (int64_t)(start + idx);
    *out_count = idx;
    return VOS3_FS_OK;
}

/* ============================================================================
 * MOUNT / UNMOUNT
 * ============================================================================ */

static vos3_superblock_t* procfs_mount_fn(vos3_fs_type_t* fs, const char* source,
                                            uint32_t flags, void* data)
{
    (void)source;
    (void)flags;
    (void)data;

    vos3_superblock_t* sb = (vos3_superblock_t*)vos3_kzalloc(sizeof(vos3_superblock_t));
    if (sb == NULL) return NULL;

    sb->magic = PROCFS_MAGIC;
    sb->block_size = 0;
    sb->ops = &g_procfs_sb_ops;
    sb->fs_type = fs;
    vos3_mutex_init(&sb->lock, "procfs_sb");

    /* Create root inode (the /proc directory itself) */
    vos3_inode_t* root_inode = procfs_create_inode(VOS3_S_IFDIR | 0555, PROCFS_DIR, 0);
    if (root_inode == NULL) {
        vos3_kfree(sb);
        return NULL;
    }

    /* Create root dentry */
    vos3_dentry_t* root_dentry = procfs_create_dentry("", root_inode);
    if (root_dentry == NULL) {
        vos3_kfree(root_inode->private_data);
        vos3_kfree(root_inode);
        vos3_kfree(sb);
        return NULL;
    }

    /* Set g_procfs_sb BEFORE creating root so root inode gets correct sb */
    g_procfs_sb = sb;
    root_inode->sb = sb;  /* Fix: root inode was created before g_procfs_sb was set */
    sb->root = root_dentry;

    VOS3_INFO("procfs mounted");
    return sb;
}

static int procfs_unmount(vos3_superblock_t* sb)
{
    if (sb == NULL) return VOS3_FS_ERR_INVAL;
    g_procfs_sb = NULL;
    vos3_mutex_destroy(&sb->lock);
    vos3_kfree(sb);
    return VOS3_FS_OK;
}

/* ============================================================================
 * FS TYPE & INIT
 * ============================================================================ */

vos3_fs_type_t g_procfs_type = {
    .name    = "procfs",
    .flags   = VOS3_FS_NO_DCACHE,
    .mount   = procfs_mount_fn,
    .unmount = procfs_unmount,
    .next    = NULL,
};

int vos3_procfs_init(void)
{
    int rc = vos3_fs_register(&g_procfs_type);
    if (rc != VOS3_FS_OK) {
        VOS3_ERROR("Failed to register procfs (error %d)", rc);
        return rc;
    }
    VOS3_INFO("procfs registered");
    return VOS3_FS_OK;
}

int vos3_procfs_mount(void)
{
    return vos3_mount(NULL, "/proc", "procfs", 0, NULL);
}
