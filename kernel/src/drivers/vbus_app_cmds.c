/**
 * @file vbus_app_cmds.c
 * @brief VBus bridge application management command handlers.
 *
 * Extracted from virtio_bridge.c during the bridge split refactor.
 * Contains: APPLOAD, APPSTAT, APPKILL, APPLOGS, APPLIST.
 */

#include "vbus_bridge_internal.h"

/* External: AI guard per-app context management */
extern int vos3_ai_guard_create_app_ctx(uint8_t app_id);
extern int vos3_ai_guard_destroy_app_ctx(uint8_t app_id);
extern int vos3_ai_guard_get_app_status(uint8_t app_id, uint64_t* mem_used,
                                         size_t* region_count);
extern uint8_t vos3_ai_guard_get_active_app_id(void);
extern vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_app_ctx(uint8_t app_id);

/* ============================================================================
 * APPLOAD — App Isolation (Phase N)
 * ============================================================================ */

/** APPLOAD is unavailable until startup ownership is implemented.
 * The former task factory published a READY task without enqueueing it and
 * passed a parent's stack request to the child. Reopening requires an
 * unpublished owned-task factory, explicit enqueue, safe startup/reaping and
 * authenticated administrator authorization. No partial launch is attempted.
 */
void cmd_appload(const char* app_id_str, const char* binary_path)
{
    (void)app_id_str;
    (void)binary_path;
    send_err(95, "APPLOAD unsupported: safe owned task startup unavailable");
}

/**
 * @brief APPSTAT|app_id — Return app status with memory, CPU ticks, task states
 */
void cmd_appstat(const char* app_id_str)
{
    if (!app_id_str) { send_err(22, "missing app_id"); return; }

    uint8_t app_id = (uint8_t)parse_uint(app_id_str);
    if (app_id >= 8) { send_err(22, "app_id must be 0-7"); return; }

    uint64_t mem_used = 0;
    size_t region_count = 0;

    int rc = vos3_ai_guard_get_app_status(app_id, &mem_used, &region_count);
    if (rc != 0) { send_err(-rc, "app not found"); return; }

    uint64_t total_cpu_ticks = 0;
    uint32_t task_count = 0;

    char states_buf[256]; int si = 0;
    char pids_buf[256]; int pi = 0;
    int first_task = 1;

    for (uint32_t i = 0; i < VOS3_MAX_TASKS; i++) {
        vos3_task_t* t = vos3_task_get(i);
        if (t != NULL && (t->flags & VOS3_TASK_FLAG_APP) &&
            t->app_id == app_id) {
            total_cpu_ticks += t->cpu_ticks_used;
            task_count++;

            if (!first_task) {
                if (si < 254) states_buf[si++] = '+';
                if (pi < 254) pids_buf[pi++] = '+';
            }
            first_task = 0;

            const char* sn = vos3_task_state_name(t->state);
            while (*sn && si < 254) states_buf[si++] = *sn++;

            char nb_pid[12];
            uint_to_str(t->pid, nb_pid, 12);
            const char* pp = nb_pid;
            while (*pp && pi < 254) pids_buf[pi++] = *pp++;
        }
    }
    states_buf[si] = '\0';
    pids_buf[pi] = '\0';

    /* Read stdout tail */
    char stdout_path[32];
    bridge_strcpy(stdout_path, "/tmp/app_", sizeof(stdout_path));
    {
        char aid_buf[4];
        uint_to_str((uint64_t)app_id, aid_buf, 4);
        size_t slen = bridge_strlen(stdout_path);
        size_t nlen = bridge_strlen(aid_buf);
        if (slen + nlen < sizeof(stdout_path) - 8) {
            bridge_strcpy(stdout_path + slen, aid_buf, sizeof(stdout_path) - slen);
            slen += nlen;
            bridge_strcpy(stdout_path + slen, "_stdout", sizeof(stdout_path) - slen);
        }
    }

    static uint8_t tail_buf[256];
    static char tail_hex[513];
    int64_t tail_len = 0;

    int sfd = vos3_open(stdout_path, VOS3_O_RDONLY, 0U);
    if (sfd >= 0) {
        vos3_inode_t fst;
        if (vos3_stat(stdout_path, &fst) == 0 && fst.size > 256) {
            vos3_lseek(sfd, (int64_t)(fst.size - 256), 0);
        }
        tail_len = vos3_read(sfd, tail_buf, 256);
        vos3_close(sfd);
        if (tail_len < 0) tail_len = 0;
    }
    hex_encode(tail_buf, (size_t)tail_len, tail_hex);

    /* Build response */
    static char resp[1280]; int ri = 0;
    const char* k; const char* p; char nb[20];

    k = "app_id="; while (*k && ri < 1250) resp[ri++] = *k++;
    uint_to_str(app_id, nb, 20); p = nb; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "mem_used="; while (*k && ri < 1250) resp[ri++] = *k++;
    uint_to_str(mem_used, nb, 20); p = nb; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "regions="; while (*k && ri < 1250) resp[ri++] = *k++;
    uint_to_str(region_count, nb, 20); p = nb; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "cpu_ticks="; while (*k && ri < 1250) resp[ri++] = *k++;
    uint_to_str(total_cpu_ticks, nb, 20); p = nb; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "tasks="; while (*k && ri < 1250) resp[ri++] = *k++;
    uint_to_str(task_count, nb, 20); p = nb; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "active="; while (*k && ri < 1250) resp[ri++] = *k++;
    uint_to_str(vos3_ai_guard_get_active_app_id() == app_id ? 1 : 0, nb, 20);
    p = nb; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "states="; while (*k && ri < 1250) resp[ri++] = *k++;
    p = states_buf; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "pids="; while (*k && ri < 1250) resp[ri++] = *k++;
    p = pids_buf; while (*p && ri < 1250) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "stdout_tail="; while (*k && ri < 1250) resp[ri++] = *k++;
    p = tail_hex; while (*p && ri < 1270) resp[ri++] = *p++;

    resp[ri] = '\0';
    send_ok(resp);
}

/**
 * @brief APPKILL|app_id|retention — Kill app with retention policy.
 */
void cmd_appkill(const char* app_id_str, const char* retention_str)
{
    if (!app_id_str) { send_err(22, "missing app_id"); return; }

    uint8_t app_id = (uint8_t)parse_uint(app_id_str);
    if (app_id >= 8) { send_err(22, "app_id must be 0-7"); return; }

    uint8_t retention = retention_str ? (uint8_t)parse_uint(retention_str) : 0U;

    uint32_t killed = 0;
    for (uint32_t i = 0; i < VOS3_MAX_TASKS; i++) {
        vos3_task_t* t = vos3_task_get(i);
        if (t != NULL && (t->flags & VOS3_TASK_FLAG_APP) &&
            t->app_id == app_id && t->state != VOS3_TASK_DEAD &&
            t->state != VOS3_TASK_ZOMBIE) {
            vos3_task_kill(t, 9);
            killed++;
        }
    }

    for (int i = 0; i < 50; i++) {
        vos3_task_yield();
    }

    for (uint32_t i = 0; i < VOS3_MAX_TASKS; i++) {
        vos3_task_t* t = vos3_task_get(i);
        if (t != NULL && (t->flags & VOS3_TASK_FLAG_APP) &&
            t->app_id == app_id &&
            (t->state == VOS3_TASK_ZOMBIE || t->state == VOS3_TASK_DEAD)) {
            vos3_task_defer_destroy(t);
        }
    }

    /* Phase 4.0: Data retention policy */
    if (retention == 0U) {
        uint64_t scrubbed = vos3_ai_guard_scrub_model_regions(app_id);
        VOS3_INFO("[BRIDGE] APPKILL %u: scrubbed %llu bytes of MODEL regions",
                  app_id, (unsigned long long)scrubbed);
    } else if (retention == 1U) {
        VOS3_INFO("[BRIDGE] APPKILL %u: persist (MODEL regions kept RO)", app_id);
    } else if (retention == 2U) {
        VOS3_INFO("[BRIDGE] APPKILL %u: snapshot (Phase 4.1 stub)", app_id);
    }

    (void)vos3_ai_guard_destroy_app_ctx(app_id);

    char resp[80]; int ri = 0;
    const char* k; const char* p; char nb[20];

    k = "killed="; while (*k) resp[ri++] = *k++;
    uint_to_str(killed, nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';
    k = "retention="; while (*k) resp[ri++] = *k++;
    uint_to_str(retention, nb, 20); p = nb; while (*p) resp[ri++] = *p++;

    resp[ri] = '\0';
    send_ok(resp);
}

/**
 * @brief APPLOGS|app_id|offset — Read app stdout from offset.
 */
void cmd_applogs(const char* id_str, const char* offset_str)
{
    if (!id_str) { send_err(22, "missing app_id"); return; }
    uint8_t app_id = (uint8_t)parse_uint(id_str);
    uint64_t offset = offset_str ? (uint64_t)parse_uint(offset_str) : 0ULL;

    char stdout_path[32];
    bridge_strcpy(stdout_path, "/tmp/app_", sizeof(stdout_path));
    {
        char aid_buf[4];
        uint_to_str((uint64_t)app_id, aid_buf, 4);
        size_t slen = bridge_strlen(stdout_path);
        size_t nlen = bridge_strlen(aid_buf);
        if (slen + nlen < sizeof(stdout_path) - 8) {
            bridge_strcpy(stdout_path + slen, aid_buf, sizeof(stdout_path) - slen);
            slen += nlen;
            bridge_strcpy(stdout_path + slen, "_stdout", sizeof(stdout_path) - slen);
        }
    }

    int sfd = vos3_open(stdout_path, VOS3_O_RDONLY, 0U);
    if (sfd < 0) {
        char nb[20]; uint_to_str(offset, nb, 20);
        static char resp[40]; int ri = 0;
        const char* p = nb; while (*p && ri < 30) resp[ri++] = *p++;
        resp[ri++] = '|';
        resp[ri] = '\0';
        send_ok(resp);
        return;
    }

    vos3_inode_t fst;
    if (vos3_stat(stdout_path, &fst) == 0 && offset >= (uint64_t)fst.size) {
        vos3_close(sfd);
        char nb[20]; uint_to_str(offset, nb, 20);
        static char resp[40]; int ri = 0;
        const char* p = nb; while (*p && ri < 30) resp[ri++] = *p++;
        resp[ri++] = '|';
        resp[ri] = '\0';
        send_ok(resp);
        return;
    }

    if (offset > 0) {
        vos3_lseek(sfd, (int64_t)offset, 0);
    }

    static uint8_t log_buf[2048];
    int64_t nr = vos3_read(sfd, log_buf, sizeof(log_buf));
    vos3_close(sfd);
    if (nr < 0) nr = 0;

    uint64_t new_offset = offset + (uint64_t)nr;
    static char hex_out[4097];
    hex_encode(log_buf, (size_t)nr, hex_out);

    static char resp[4200]; int ri = 0;
    char nb[20];
    uint_to_str(new_offset, nb, 20);
    const char* p = nb; while (*p && ri < 4100) resp[ri++] = *p++;
    resp[ri++] = '|';
    p = hex_out; while (*p && ri < 4190) resp[ri++] = *p++;
    resp[ri] = '\0';
    send_ok(resp);
}

/**
 * @brief APPLIST — List all running app contexts.
 */
void cmd_applist(void)
{
    static char resp[512]; int ri = 0;
    int first = 1;

    for (uint8_t id = 0; id < 8; id++) {
        vos3_ai_guard_ctx_t* ctx = vos3_ai_guard_acquire_app_ctx(id);
        if (!ctx) continue;

        if (!first && ri < 500) resp[ri++] = ';';
        first = 0;

        char nb[20];
        uint_to_str(id, nb, 20);
        const char* p = nb; while (*p && ri < 500) resp[ri++] = *p++;
        if (ri < 500) resp[ri++] = ':';

        uint32_t alive = 0;
        for (uint32_t i = 0; i < VOS3_MAX_TASKS; i++) {
            vos3_task_t* t = vos3_task_get(i);
            if (t != NULL && (t->flags & VOS3_TASK_FLAG_APP) &&
                t->app_id == id && t->state != VOS3_TASK_DEAD) {
                alive++;
            }
        }
        const char* sn = alive > 0 ? "running" : "stopped";
        while (*sn && ri < 500) resp[ri++] = *sn++;
        if (ri < 500) resp[ri++] = ':';

        uint_to_str(ctx->quota_used, nb, 20);
        vos3_ai_guard_ctx_put(ctx);
        p = nb; while (*p && ri < 500) resp[ri++] = *p++;
    }

    resp[ri] = '\0';
    send_ok(resp);
}

/* ============================================================================
 * AGENT_KILL_ALL — Kill all registered dispatcher agents
 * ============================================================================ */

/**
 * @brief Kill all registered agents via the dispatcher kill-all mechanism.
 * No arguments. Returns killed count.
 */
void cmd_agent_kill_all(void)
{
    extern int vos3_dispatcher_kill_all(void);

    int killed = vos3_dispatcher_kill_all();

    char resp[64];
    int ri = 0;
    const char* k = "killed=";
    while (*k) resp[ri++] = *k++;

    char nb[20];
    uint_to_str((uint64_t)(killed >= 0 ? killed : 0), nb, 20);
    const char* p = nb;
    while (*p) resp[ri++] = *p++;

    resp[ri] = '\0';
    send_ok(resp);
}
