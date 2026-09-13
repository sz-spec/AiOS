/**
 * @file vbus_fs_cmds.c
 * @brief VBus bridge filesystem command handlers.
 *
 * Extracted from virtio_bridge.c during the bridge split refactor.
 * Contains: STAT, WRITE, READ, LS, MKDIR, UNLINK, RENAME,
 * READC, APPEND, LSM, RMDIR, EXEC, HTTPGET.
 */

#include "vbus_bridge_internal.h"

/* ============================================================================
 * COMMAND HANDLERS — Original 5
 * ============================================================================ */

void cmd_stat(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    vos3_statfs_t st;
    if (vos3_statfs(path, &st) != 0) { send_err(5, "statfs failed"); return; }

    char resp[256]; int ri = 0; char nb[20]; const char* p;
    const char* k;
    k = "mount_count="; while (*k) resp[ri++] = *k++;
    uint_to_str(st.f_mount_count, nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';
    k = "free_blocks="; while (*k) resp[ri++] = *k++;
    uint_to_str(st.f_bfree, nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';
    k = "total_blocks="; while (*k) resp[ri++] = *k++;
    uint_to_str(st.f_blocks, nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';
    k = "free_inodes="; while (*k) resp[ri++] = *k++;
    uint_to_str(st.f_ffree, nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri] = '\0';
    send_ok(resp);
}

void cmd_write(const char* path, const char* hex_data)
{
    if (!path || !hex_data) { send_err(22, "missing args"); return; }
    /* CVE-2026-23086: reject payloads exceeding bound window */
    if (g_congestion) { send_err(16, "BUSY"); return; }
    size_t hex_len = bridge_strlen(hex_data);
    uint32_t window = vos3_bridge_bound_window();
    if (hex_len > MAX_CHUNK_SIZE || hex_len > window - 64U) {
        send_err(27, "chunk exceeds bound window"); return;
    }
    static uint8_t wbuf[4096]; size_t dlen = 0;
    if (hex_decode(hex_data, wbuf, sizeof(wbuf), &dlen) != 0) {
        send_err(22, "bad hex"); return;
    }
    int fd = vos3_open(path, VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC, 0644);
    if (fd < 0) { send_err(-fd, "open failed"); return; }
    int64_t nw = vos3_write(fd, wbuf, dlen);
    vos3_close(fd);
    if (nw < 0) { send_err((int)(-nw), "write failed"); return; }
    extern void vos3_virtio_blk_flush(void);
    vos3_virtio_blk_flush();
    char nb[20]; uint_to_str((uint64_t)nw, nb, 20);
    send_ok(nb);
}

void cmd_read(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    int fd = vos3_open(path, VOS3_O_RDONLY, 0);
    if (fd < 0) { send_err(-fd, "open failed"); return; }
    static uint8_t rbuf[4096];
    int64_t nr = vos3_read(fd, rbuf, sizeof(rbuf));
    vos3_close(fd);
    if (nr < 0) { send_err((int)(-nr), "read failed"); return; }
    static char hout[8193];
    hex_encode(rbuf, (size_t)nr, hout);
    send_ok(hout);
}

void cmd_ls(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    int fd = vos3_open(path, VOS3_O_RDONLY | VOS3_O_DIRECTORY, 0);
    if (fd < 0) { send_err(-fd, "open failed"); return; }
    vos3_dirent_t de[64]; size_t count = 0;
    int rc = vos3_readdir(fd, de, 64, &count);
    vos3_close(fd);
    if (rc != 0) { send_err(-rc, "readdir failed"); return; }

    static char lbuf[4096]; int li = 0; int first = 1;
    for (size_t i = 0; i < count; i++) {
        const char* n = de[i].name;
        if (n[0] == '.' && (n[1] == '\0' || (n[1] == '.' && n[2] == '\0')))
            continue;
        if (!first && li < 4095) lbuf[li++] = ',';
        first = 0;
        while (*n && li < 4095) lbuf[li++] = *n++;
    }
    lbuf[li] = '\0';
    send_ok(lbuf);
}

/* ============================================================================
 * COMMAND HANDLERS — New 7 (Day 13-14)
 * ============================================================================ */

void cmd_mkdir(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    int rc = vos3_mkdir(path, 0755);
    if (rc != 0) { send_err(-rc, "mkdir failed"); return; }
    send_ok("created");
}

void cmd_unlink(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    int rc = vos3_unlink(path);
    if (rc != 0) { send_err(-rc, "unlink failed"); return; }
    send_ok("deleted");
}

void cmd_rename(const char* oldpath, const char* newpath)
{
    if (!oldpath || !newpath) { send_err(22, "missing args"); return; }
    int rc = vos3_rename(oldpath, newpath);
    if (rc != 0) { send_err(-rc, "rename failed"); return; }
    send_ok("renamed");
}

/* ============================================================================
 * COMMAND HANDLERS — Chunked I/O + Enhanced Ops (Phase B)
 * ============================================================================ */

/**
 * @brief READC|path|offset|len — Chunked file read with offset
 */
void cmd_readc(const char* path, const char* offset_str, const char* len_str)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    if (!offset_str || !len_str) { send_err(22, "missing offset or len"); return; }

    uint64_t offset = 0;
    for (const char* s = offset_str; *s >= '0' && *s <= '9'; s++)
        offset = offset * 10 + (uint64_t)(*s - '0');

    uint64_t len = 0;
    for (const char* s = len_str; *s >= '0' && *s <= '9'; s++)
        len = len * 10 + (uint64_t)(*s - '0');

    if (len > 4096) len = 4096;

    int fd = vos3_open(path, VOS3_O_RDONLY, 0);
    if (fd < 0) { send_err(-fd, "open failed"); return; }

    int64_t seekrc = vos3_lseek(fd, (int64_t)offset, 0 /* SEEK_SET */);
    if (seekrc < 0) { vos3_close(fd); send_err((int)(-seekrc), "seek failed"); return; }

    static uint8_t rbuf[4096];
    int64_t nr = vos3_read(fd, rbuf, (size_t)len);
    vos3_close(fd);

    if (nr < 0) { send_err((int)(-nr), "read failed"); return; }

    static char hout[8193];
    hex_encode(rbuf, (size_t)nr, hout);
    send_ok(hout);
}

/**
 * @brief APPEND|path|hex_data — Append data to file
 */
void cmd_append(const char* path, const char* hex_data)
{
    if (!path || !hex_data) { send_err(22, "missing args"); return; }
    /* CVE-2026-23086: reject payloads exceeding bound window */
    if (g_congestion) { send_err(16, "BUSY"); return; }
    size_t hex_len = bridge_strlen(hex_data);
    uint32_t window = vos3_bridge_bound_window();
    if (hex_len > MAX_CHUNK_SIZE || hex_len > window - 64U) {
        send_err(27, "chunk exceeds bound window"); return;
    }
    static uint8_t wbuf[4096]; size_t dlen = 0;
    if (hex_decode(hex_data, wbuf, sizeof(wbuf), &dlen) != 0) {
        send_err(22, "bad hex"); return;
    }
    int fd = vos3_open(path, VOS3_O_WRONLY | VOS3_O_APPEND | VOS3_O_CREAT, 0644);
    if (fd < 0) { send_err(-fd, "open failed"); return; }
    int64_t nw = vos3_write(fd, wbuf, dlen);
    vos3_close(fd);
    if (nw < 0) { send_err((int)(-nw), "write failed"); return; }
    extern void vos3_virtio_blk_flush(void);
    vos3_virtio_blk_flush();
    char nb[20]; uint_to_str((uint64_t)nw, nb, 20);
    send_ok(nb);
}

/**
 * @brief LSM|path — List directory with metadata
 *
 * Wire format per entry: `name:size:type:mtime`, entries `,`-separated.
 * `mtime` is the inode's modification time as a uint64 (seconds since
 * uptime epoch on RAMFS / Unix epoch on vos3fs — opaque to the host;
 * the host treats it as a sortable number). Older kernels emitted
 * three fields (`name:size:type`); the host parser tolerates that and
 * defaults missing mtime to 0.
 */
void cmd_lsm(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    int fd = vos3_open(path, VOS3_O_RDONLY | VOS3_O_DIRECTORY, 0);
    if (fd < 0) { send_err(-fd, "open failed"); return; }
    vos3_dirent_t de[64]; size_t count = 0;
    int rc = vos3_readdir(fd, de, 64, &count);
    vos3_close(fd);
    if (rc != 0) { send_err(-rc, "readdir failed"); return; }

    static char lbuf[8192]; int li = 0; int first = 1;

    for (size_t i = 0; i < count && li < 8100; i++) {
        const char* n = de[i].name;
        /* Skip . and .. */
        if (n[0] == '.' && (n[1] == '\0' || (n[1] == '.' && n[2] == '\0')))
            continue;

        if (!first && li < 8191) lbuf[li++] = ',';
        first = 0;

        /* name */
        while (*n && li < 8100) lbuf[li++] = *n++;
        if (li < 8191) lbuf[li++] = ':';

        /* Get stat for size and type */
        /* Build full path: path + "/" + name */
        char fullpath[512];
        size_t plen = bridge_strlen(path);
        size_t nlen = bridge_strlen(de[i].name);
        if (plen + 1 + nlen < sizeof(fullpath)) {
            bridge_strcpy(fullpath, path, sizeof(fullpath));
            if (plen > 0 && fullpath[plen - 1] != '/') {
                fullpath[plen] = '/';
                plen++;
            }
            bridge_strcpy(fullpath + plen, de[i].name, sizeof(fullpath) - plen);

            vos3_inode_t st;
            if (vos3_stat(fullpath, &st) == 0) {
                /* size */
                char nb[20]; uint_to_str(st.size, nb, 20);
                const char* p = nb;
                while (*p && li < 8100) lbuf[li++] = *p++;
                if (li < 8191) lbuf[li++] = ':';

                /* type */
                if (VOS3_S_ISDIR(st.mode))      { if (li < 8191) lbuf[li++] = 'd'; }
                else if (VOS3_S_ISLNK(st.mode)) { if (li < 8191) lbuf[li++] = 'l'; }
                else                             { if (li < 8191) lbuf[li++] = 'f'; }

                /* mtime — emitted as a 4th colon-separated field. uint64
                 * fits in at most 20 ASCII digits; uint_to_str handles
                 * the full width. Zero is a valid value and means
                 * "unknown" on filesystems that don't track it. */
                if (li < 8191) lbuf[li++] = ':';
                char mb[24]; uint_to_str(st.mtime, mb, 24);
                const char* mp = mb;
                while (*mp && li < 8100) lbuf[li++] = *mp++;
            } else {
                /* stat failed, use defaults: size=0, type=f, mtime=0 */
                if (li < 8191) lbuf[li++] = '0';
                if (li < 8191) lbuf[li++] = ':';
                if (li < 8191) lbuf[li++] = 'f';
                if (li < 8191) lbuf[li++] = ':';
                if (li < 8191) lbuf[li++] = '0';
            }
        }
    }
    lbuf[li] = '\0';
    send_ok(lbuf);
}

/**
 * @brief RMDIR|path — Remove directory
 */
void cmd_rmdir(const char* path)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }
    int rc = vos3_rmdir(path);
    if (rc != 0) { send_err(-rc, "rmdir failed"); return; }
    send_ok("removed");
}

/* ============================================================================
 * EXEC Command
 * ============================================================================ */

/** @brief Temporary stdout capture file for exec'd programs */
#define EXEC_STDOUT_PATH "/tmp/exec_stdout"

/**
 * @brief Exec request structure — shared between bridge task and child
 */
typedef struct exec_request {
    char            path[256];
    char            args[256];
    volatile int32_t exit_code;
    volatile int    done;
} exec_request_t;

/**
 * @brief Entry point for the exec child task
 */
static void exec_child_entry(void* arg)
{
    exec_request_t* req = (exec_request_t*)arg;

    /* Enable interrupts so the scheduler can preempt us */
    __asm__ volatile ("sti" ::: "memory");

    int fd0 = vos3_open("/dev/console", VOS3_O_RDONLY, 0U);
    if (fd0 < 0) {
        VOS3_WARN("[BRIDGE-EXEC] Failed to open /dev/console for stdin: %d", fd0);
    }

    int fd1 = vos3_open(EXEC_STDOUT_PATH,
                         VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC, 0644U);
    if (fd1 < 0) {
        VOS3_ERROR("[BRIDGE-EXEC] Failed to open stdout capture file: %d", fd1);
        req->exit_code = -1;
        req->done = 1;
        vos3_task_exit(-1);
    }

    int fd2 = vos3_open(EXEC_STDOUT_PATH, VOS3_O_WRONLY | VOS3_O_APPEND, 0U);
    if (fd2 < 0) {
        VOS3_WARN("[BRIDGE-EXEC] Failed to open stderr capture file: %d", fd2);
    }

    if (fd0 != 0 || fd1 != 1) {
        VOS3_WARN("[BRIDGE-EXEC] Unexpected fds: stdin=%d stdout=%d stderr=%d",
                   fd0, fd1, fd2);
    }

    char* arg_buf = (char*)vos3_kmalloc(256);
    const char* argv[16];
    int argc = 0;

    argv[argc++] = req->path;

    if (arg_buf && req->args[0] != '\0') {
        bridge_strcpy(arg_buf, req->args, 256);

        char* p = arg_buf;
        while (*p && argc < 15) {
            while (*p == ' ') p++;
            if (*p == '\0') break;
            argv[argc++] = p;
            while (*p && *p != ' ') p++;
            if (*p) *p++ = '\0';
        }
    }
    argv[argc] = NULL;

    const char* envp[] = {
        "PATH=/bin:/sbin:/usr/bin:/usr/sbin",
        "HOME=/",
        "TERM=vt100",
        NULL
    };

    VOS3_INFO("[BRIDGE-EXEC] Executing '%s' with %d args", req->path, argc);

    int result = vos3_exec(req->path, argv, envp);

    if (arg_buf) vos3_kfree(arg_buf);
    VOS3_ERROR("[BRIDGE-EXEC] exec failed: %d", result);
    req->exit_code = result;
    req->done = 1;
    vos3_task_exit(result);
}

/**
 * @brief EXEC|path|args — Execute a binary with stdout capture
 */
void cmd_exec(const char* path, const char* args)
{
    if (!path || !path[0]) { send_err(22, "missing path"); return; }

    vos3_inode_t st;
    int rc = vos3_stat(path, &st);
    if (rc != 0) {
        send_err(-rc, "file not found");
        return;
    }
    if (!VOS3_S_ISREG(st.mode)) {
        send_err(13, "not a regular file");
        return;
    }

    exec_request_t req;
    bridge_strcpy(req.path, path, sizeof(req.path));
    if (args && args[0]) {
        bridge_strcpy(req.args, args, sizeof(req.args));
    } else {
        req.args[0] = '\0';
    }
    req.exit_code = 0;
    req.done = 0;

    vos3_task_t* child = vos3_task_create("exec_child",
                                           exec_child_entry,
                                           &req,
                                           VOS3_PRIORITY_NORMAL);
    if (child == NULL) {
        send_err(12, "task create failed");
        return;
    }

    vos3_task_t* bridge_task = vos3_sched_current();
    if (bridge_task != NULL) {
        child->parent = bridge_task;
    }

    VOS3_INFO("[BRIDGE] EXEC: spawned child pid=%u for '%s'", child->pid, path);

    uint32_t polls = 0;
    const uint32_t max_polls = 5000U;

    while (polls < max_polls) {
        if (req.done) break;
        if (child->state == VOS3_TASK_ZOMBIE ||
            child->state == VOS3_TASK_DEAD) {
            break;
        }
        vos3_task_yield();
        polls++;
    }

    int32_t exit_code;
    if (polls >= max_polls) {
        VOS3_WARN("[BRIDGE] EXEC: child pid=%u timed out after %u polls, killing",
                   child->pid, polls);
        child->exit_code = -9;
        child->state = VOS3_TASK_ZOMBIE;
        exit_code = -9;
    } else if (req.done) {
        exit_code = req.exit_code;
    } else {
        exit_code = child->exit_code;
    }

    VOS3_INFO("[BRIDGE] EXEC: child pid=%u finished, exit_code=%d",
               child->pid, exit_code);

    static uint8_t stdout_buf[4096];
    static char    stdout_hex[8193];
    int64_t stdout_len = 0;

    int stdout_fd = vos3_open(EXEC_STDOUT_PATH, VOS3_O_RDONLY, 0U);
    if (stdout_fd >= 0) {
        stdout_len = vos3_read(stdout_fd, stdout_buf, sizeof(stdout_buf));
        vos3_close(stdout_fd);
        if (stdout_len < 0) stdout_len = 0;
    }

    hex_encode(stdout_buf, (size_t)stdout_len, stdout_hex);

    static char resp[8320];
    int ri = 0;
    const char* k;
    char nb[20];

    k = "exit_code="; while (*k && ri < 8300) resp[ri++] = *k++;
    int_to_str(exit_code, nb, 20);
    const char* p = nb; while (*p && ri < 8300) resp[ri++] = *p++;

    resp[ri++] = ',';

    k = "stdout="; while (*k && ri < 8300) resp[ri++] = *k++;
    p = stdout_hex; while (*p && ri < 8300) resp[ri++] = *p++;

    resp[ri] = '\0';
    send_ok(resp);

    vos3_task_defer_destroy(child);
    vos3_unlink(EXEC_STDOUT_PATH);
}

/**
 * @brief HTTPGET|url — Host-side HTTP proxy placeholder
 */
void cmd_httpget(const char* url)
{
    (void)url;
    send_err(38, "HTTPGET must be handled by host bridge proxy");
}
