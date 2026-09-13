/**
 * @file virtio_bridge.c
 * @brief VOS3 Serial Bridge Driver — Main dispatch, init, and poll loop.
 *
 * This file was split into focused modules:
 *   vbus_transport.c   — Transport layer, response helpers, utility functions
 *   vbus_fs_cmds.c     — Filesystem command handlers
 *   vbus_ai_cmds.c     — AI/model command handlers
 *   vbus_diag_cmds.c   — Diagnostic/system command handlers
 *   vbus_app_cmds.c    — Application management command handlers
 *
 * What remains here:
 *   - Command dispatch table (string-match dispatch)
 *   - Bridge initialization (vos3_bridge_init)
 *   - Bridge poll loop (vos3_bridge_poll)
 *   - Bridge task entry (vos3_bridge_task)
 *
 * Critical config (confirmed working):
 *   - FIFOs DISABLED (FCR=0x00) — every byte triggers DR immediately
 *   - Busy-poll loop — no vos3_task_sleep_ms (scheduler bug workaround)
 *   - Host must keep socket connection open (persistent)
 */

#include "vbus_bridge_internal.h"

/* ---- Port I/O (needed by legacy serial poll path) ---- */

static inline uint8_t port_inb(uint16_t port)
{
    uint8_t val;
    __asm__ volatile ("inb %1, %0" : "=a"(val) : "Nd"(port));
    return val;
}

/* ---- UART Constants (needed by legacy serial poll path) ---- */

#define COM2        0x2F8U
#define UART_DATA   0U
#define UART_LSR    5U
#define LSR_DR      (1U << 0)

/* ---- State ---- */

static volatile uint32_t g_initialized = 0U;
static uint32_t g_poll_count = 0U;
static uint32_t g_rx_total = 0U;

/* Hardening limits */
#define MAX_BYTES_PER_POLL  256U
#define LINE_TIMEOUT        100000U

/* ---- Line buffer (legacy serial path) ---- */

#define LINE_MAX 9216U

static char     g_line_buf[LINE_MAX];
static uint32_t g_line_pos = 0U;
static uint32_t g_line_age = 0U;

/* ============================================================================
 * DISPATCHER
 * ============================================================================ */

static int streq(const char* a, const char* b)
{
    while (*a && *b) { if (*a != *b) return 0; a++; b++; }
    return (*a == '\0' && *b == '\0');
}

/* Phase 9.1: Serialized RDTSC for command watchdog — lfence prevents OoO reorder */
static inline uint64_t rdtsc_fence(void)
{
    uint32_t lo, hi;
    __asm__ volatile("lfence; rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

/* Phase 9.1: VBus Command Watchdog — 1 billion cycles max per command */
#define VBUS_CMD_CYCLE_LIMIT  1000000000ULL
static volatile uint64_t g_dispatch_start_tsc = 0;
volatile uint32_t        g_dispatch_timeouts  = 0;

static void dispatch(char* line)
{
    /* Phase 9.1: Start watchdog TSC (lfence-serialized) */
    g_dispatch_start_tsc = rdtsc_fence();

    /* Tokenize up to 8 fields (CMD|arg1|...|arg7) — Phase 4.2.7 */
    char* tok[8] = { NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL }; int nt = 0;
    tok[nt++] = line;
    for (char* p = line; *p && nt < 8; p++)
        if (*p == '|') { *p = '\0'; tok[nt++] = p + 1; }

    VOS3_INFO("[VBUS] CMD: %s (slot=%s)", tok[0], tok[1] ? tok[1] : "N/A");

    /* Original 5 commands */
    if      (streq(tok[0], "PING"))    cmd_ping();
    else if (streq(tok[0], "STAT"))    cmd_stat(tok[1]);
    else if (streq(tok[0], "WRITE"))   cmd_write(tok[1], tok[2]);
    else if (streq(tok[0], "READ"))    cmd_read(tok[1]);
    else if (streq(tok[0], "LS"))      cmd_ls(tok[1]);
    /* Chunked I/O + Enhanced Ops (Phase B) */
    else if (streq(tok[0], "READC"))   cmd_readc(tok[1], tok[2], tok[3]);
    else if (streq(tok[0], "APPEND"))  cmd_append(tok[1], tok[2]);
    else if (streq(tok[0], "LSM"))     cmd_lsm(tok[1]);
    else if (streq(tok[0], "RMDIR"))   cmd_rmdir(tok[1]);
    /* Commands from Day 13-14 — EXEC redirected through Action Bridge (Q2-2026) */
    else if (streq(tok[0], "EXEC")) {
        action_desc_t ad;
        {
            uint8_t *p = (uint8_t *)&ad;
            for (size_t i = 0; i < sizeof(ad); i++) p[i] = 0;
        }
        ad.type = ACTION_TYPE_EXEC;
        if (tok[1]) bridge_strcpy(ad.command, tok[1], ACTION_CMD_MAX_LEN);
        if (tok[2]) bridge_strcpy(ad.args, tok[2], ACTION_CMD_MAX_LEN);
        int rc = action_submit(g_current_slot_id, &ad);
        if (rc == 0 && ad.state == ACTION_STATE_APPROVED) {
            cmd_exec(tok[1], tok[2]);
        } else if (rc == 0) {
            send_ok("EXEC_PENDING");
        } else {
            send_err(-rc, "EXEC denied by Action Bridge");
        }
    }
    else if (streq(tok[0], "MKDIR"))   cmd_mkdir(tok[1]);
    else if (streq(tok[0], "UNLINK"))  cmd_unlink(tok[1]);
    else if (streq(tok[0], "RENAME"))  cmd_rename(tok[1], tok[2]);
    else if (streq(tok[0], "SYSINFO")) cmd_sysinfo();
    else if (streq(tok[0], "BUILD_UUID")) cmd_build_uuid();
    /* v21.3.1 (Track A) — KV-cache efficiency monitoring. */
    else if (streq(tok[0], "EFFICIENCY_STATS")) cmd_efficiency_stats();
    else if (streq(tok[0], "KV_BENCH_PROBE"))   cmd_kv_bench_probe();
    else if (streq(tok[0], "PROCS"))   cmd_procs();
    else if (streq(tok[0], "HTTPGET")) cmd_httpget(tok[1]);
    /* App isolation commands (Phase N) — APPLOAD redirected through Action Bridge (Q2-2026) */
    else if (streq(tok[0], "APPLOAD")) {
        action_desc_t ad;
        {
            uint8_t *p = (uint8_t *)&ad;
            for (size_t i = 0; i < sizeof(ad); i++) p[i] = 0;
        }
        ad.type = ACTION_TYPE_APPLOAD;
        if (tok[1]) bridge_strcpy(ad.command, tok[1], ACTION_CMD_MAX_LEN);
        if (tok[2]) bridge_strcpy(ad.args, tok[2], ACTION_CMD_MAX_LEN);
        int rc = action_submit(g_current_slot_id, &ad);
        if (rc == 0 && ad.state == ACTION_STATE_APPROVED) {
            cmd_appload(tok[1], tok[2]);
        } else if (rc == 0) {
            send_ok("APPLOAD_PENDING");
        } else {
            send_err(-rc, "APPLOAD denied by Action Bridge");
        }
    }
    else if (streq(tok[0], "APPSTAT")) cmd_appstat(tok[1]);
    else if (streq(tok[0], "APPKILL")) cmd_appkill(tok[1], tok[2]);
    /* Phase 4.0: Additional app commands */
    else if (streq(tok[0], "APPLIST")) cmd_applist();
    else if (streq(tok[0], "AGENT_KILL_ALL")) cmd_agent_kill_all();
    else if (streq(tok[0], "APPLOGS")) cmd_applogs(tok[1], tok[2]);
    /* AI Model commands (Phase 4.2 — backward compat) */
    else if (streq(tok[0], "MODEL_START"))  cmd_model_start(tok[1], tok[2]);
    else if (streq(tok[0], "MODEL_SYNC"))   cmd_model_sync();
    else if (streq(tok[0], "MODEL_REWIND")) cmd_model_rewind(tok[1]);
    else if (streq(tok[0], "MODEL_DONE"))   cmd_model_done();
    else if (streq(tok[0], "MODEL_CHECK"))  cmd_model_check(tok[1]);
    else if (streq(tok[0], "MODEL_TAMPER")) cmd_model_tamper(tok[1]);
    /* Cyber overlay (Stages 4–5): IntentManifest submission bridge.
     * tok[2] is optional slot_id; if present, performs validate + bind. */
    else if (streq(tok[0], "INTENT_SUBMIT")) cmd_intent_submit(tok[1], tok[2]);
    /* Cyber overlay (Stage 6): diagnostic / telemetry commands */
    else if (streq(tok[0], "TEE_ENV"))             cmd_tee_env();
    else if (streq(tok[0], "TEE_QUOTE"))           cmd_tee_quote();
    else if (streq(tok[0], "SCHED_COOKIE_STATS"))  cmd_sched_cookie_stats();
    else if (streq(tok[0], "HCS_FLUSH"))           cmd_hcs_flush();
    else if (streq(tok[0], "SCHED_SIBLING_CHECK")) cmd_sched_sibling_check(tok[1]);
    /* Stage 10.1: kernel-side compliance failure ring (Active Compliance Logging foundation) */
    else if (streq(tok[0], "AUDIT_FAIL_QUOTE"))    cmd_audit_fail_quote();
    /* Stage 10.2: hallucination guardrail (per-slot min_confidence_score from manifest v2) */
    else if (streq(tok[0], "ACTION_CHECK_CONFIDENCE")) cmd_action_check_confidence(tok[1], tok[2]);
    /* Stage 10.2.2: management override / Safe-Rollout */
    else if (streq(tok[0], "POLICY_OVERRIDE"))     cmd_policy_override(tok[1], tok[2]);
    else if (streq(tok[0], "POLICY_FORCE_PERMIT")) cmd_policy_force_permit(tok[1]);
    else if (streq(tok[0], "POLICY_STATUS"))       cmd_policy_status();
    /* Phase 4.2.5: Multi-slot commands */
    else if (streq(tok[0], "SLOT_START"))    cmd_slot_start(tok[1], tok[2], tok[3], tok[4], tok[5], tok[6]);
    else if (streq(tok[0], "SLOT_FINISH"))   cmd_slot_finish(tok[1]);
    else if (streq(tok[0], "MODEL_SIG"))     cmd_model_sig_register(tok[1], tok[2], tok[3], tok[4]);
    else if (streq(tok[0], "SLOT_SYNC"))     cmd_slot_sync(tok[1]);
    else if (streq(tok[0], "SLOT_REWIND"))   cmd_slot_rewind(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_SUSPEND"))  cmd_slot_suspend(tok[1]);
    else if (streq(tok[0], "SLOT_RESUME"))   cmd_slot_resume(tok[1]);
    else if (streq(tok[0], "SLOT_STATUS"))   cmd_slot_status(tok[1]);
    else if (streq(tok[0], "SLOT_CHECK"))    cmd_slot_check(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_SNAPSHOT")) cmd_slot_snapshot(tok[1]);
    else if (streq(tok[0], "SLOT_TIMER"))    cmd_slot_timer(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_RESET"))    cmd_slot_reset(tok[1]);
    else if (streq(tok[0], "SLOT_SWAP"))     cmd_slot_swap(tok[1], tok[2]);
    else if (streq(tok[0], "VDEV_READ"))     cmd_vdev_read(tok[1], tok[2], tok[3]);
    /* Phase 4.2.7: Agentic Fabric commands */
    else if (streq(tok[0], "ISC_SEND"))        cmd_isc_send(tok[1], tok[2], tok[3], tok[4]);
    else if (streq(tok[0], "ISC_RECV"))        cmd_isc_recv(tok[1]);
    else if (streq(tok[0], "SLOT_DORMANT"))    cmd_slot_dormant(tok[1]);
    else if (streq(tok[0], "SLOT_WAKE"))       cmd_slot_wake(tok[1]);
    else if (streq(tok[0], "SLOT_QUOTA"))      cmd_slot_quota(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_CAPS"))       cmd_slot_caps(tok[1], tok[2], tok[3]);
    else if (streq(tok[0], "SLOT_AFFINITY"))   cmd_slot_affinity(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_CHECKPOINT")) cmd_slot_checkpoint(tok[1]);
    else if (streq(tok[0], "SLOT_ROLLBACK"))   cmd_slot_rollback(tok[1]);
    else if (streq(tok[0], "SLOT_IO_MASK"))    cmd_slot_io_mask(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_ENUMERATE"))  cmd_slot_enumerate();
    /* Phase 4.2.7+: Context Persistence & Event Pub/Sub */
    else if (streq(tok[0], "SLOT_CONTEXT_CONFIG")) cmd_slot_context_config(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_WARM_RESET"))     cmd_slot_warm_reset(tok[1]);
    else if (streq(tok[0], "EVENT_SUBSCRIBE"))      cmd_event_subscribe(tok[1], tok[2]);
    else if (streq(tok[0], "EVENT_UNSUBSCRIBE"))    cmd_event_unsubscribe(tok[1]);
    /* Phase 4.2.8: Swarm Governance */
    else if (streq(tok[0], "SLOT_SHARED_MAP"))      cmd_slot_shared_map(tok[1], tok[2]);
    else if (streq(tok[0], "SLOT_BARRIER_WAIT"))    cmd_slot_barrier_wait(tok[1], tok[2]);
    /* Phase 4.2.9: Autonomous Agentic */
    else if (streq(tok[0], "YIELD_EX"))             cmd_yield_ex(tok[1], tok[2], tok[3]);
    else if (streq(tok[0], "COMMIT_SLOT"))          cmd_commit_slot(tok[1]);
    else if (streq(tok[0], "SLOT_GATE"))            cmd_slot_gate(tok[1], tok[2]);
    else if (streq(tok[0], "CONTEXT_SHARE"))        cmd_context_share(tok[1], tok[2]);
    /* Phase 4.2.15: Heartbeat page address */
    else if (streq(tok[0], "HEARTBEAT_ADDR"))  cmd_heartbeat_addr();
    /* Phase 4.2.16: Backpressure status flag */
    else if (streq(tok[0], "HEARTBEAT_STATUS"))  cmd_heartbeat_status();
    /* Phase 4.2.20: CR4 register audit */
    else if (streq(tok[0], "QUERY_CR4"))  cmd_query_cr4();
    /* Phase 4.4: Universal Shard diagnostics */
    else if (streq(tok[0], "KASLR_BASE")) cmd_kaslr_base();
    else if (streq(tok[0], "CPU_PATH"))   cmd_cpu_path();
    else if (streq(tok[0], "CRING_STATUS")) cmd_cring_status();
    else if (streq(tok[0], "MEMCPY_BENCH")) cmd_memcpy_bench();
    else if (streq(tok[0], "VECTOR_CHECK")) cmd_vector_check();
    /* Phase 4.5: Swarm Fabric */
    else if (streq(tok[0], "CORE_PIN"))    cmd_core_pin(tok[1]);
    else if (streq(tok[0], "SMP_STATUS"))  cmd_smp_status();
    /* Phase 4.5.1: Swarm Deep Audit */
    else if (streq(tok[0], "CRING_FLOOD")) cmd_cring_flood(tok[1]);
    else if (streq(tok[0], "TLB_AUDIT"))   cmd_tlb_audit();
    /* Phase 4.6: Infinity Loop */
    else if (streq(tok[0], "SQ_STATUS"))   cmd_sq_status();
    /* Phase 4.7: Hyperscale Protocol */
    else if (streq(tok[0], "DOORBELL_STATUS")) cmd_doorbell_status();
    /* Phase 4.2.11: Deterministic Clock */
    else if (streq(tok[0], "SLOT_TIME")) {
        uint8_t sid = tok[1] ? (uint8_t)parse_u64(tok[1]) : 0xFF;
        uint64_t t = vos3_ai_slot_get_time(sid);
        char tbuf[24];
        uint_to_str(t, tbuf, sizeof(tbuf));
        send_ok(tbuf);
    }
    /* Phase 4.9b: Warp Drive */
    else if (streq(tok[0], "WARP_POST"))   cmd_warp_post(tok[1], tok[2], tok[3]);
    else if (streq(tok[0], "WARP_STATUS")) cmd_warp_status();
    else if (streq(tok[0], "HP_STATS"))   cmd_hp_stats();
    else if (streq(tok[0], "DRIVER_PRESSURE")) cmd_driver_pressure();
    /* Phase 5: Context Management (Freeze/Thaw) — April 2026 Hardened */
    else if (streq(tok[0], "CTX_FREEZE"))    cmd_ctx_freeze(tok[1]);
    else if (streq(tok[0], "CTX_SCRUB"))     cmd_ctx_scrub(tok[1]);
    else if (streq(tok[0], "CTX_READ"))      cmd_ctx_read(tok[1], tok[2]);
    else if (streq(tok[0], "CTX_THAW"))      cmd_ctx_thaw(tok[1], tok[2], tok[3], tok[4]);
    else if (streq(tok[0], "CTX_THAW_COLD")) cmd_ctx_thaw_cold(tok[1], tok[2], tok[3], tok[4]);
    else if (streq(tok[0], "CTX_WRITE"))     cmd_ctx_write(tok[1], tok[2], tok[3]);
    else if (streq(tok[0], "CTX_THAW_DONE")) cmd_ctx_thaw_done(tok[1], tok[2]);
    else if (streq(tok[0], "CTX_LATENCY"))   cmd_ctx_latency(tok[1]);
    /* Phase v20.0: HAL + Integrity commands */
    else if (streq(tok[0], "PCI_LIST"))     cmd_pci_list();
    else if (streq(tok[0], "KTEXT_HASH"))   cmd_ktext_hash();
    else if (streq(tok[0], "MMR_ROOT"))     cmd_mmr_root();
    else if (streq(tok[0], "CTX_STATS"))    cmd_ctx_stats(tok[1]);
    /* Phase 9: Storage HAL */
    else if (streq(tok[0], "STORAGE_INFO")) cmd_storage_info();
    /* Batch F: Inference-Aware Scheduling */
    else if (streq(tok[0], "INFERENCE_HINT")) cmd_inference_hint(tok[1], tok[2]);
    /* Phase 6: Kernel Inference Manager */
    else if (streq(tok[0], "KIM_STATS"))     cmd_kim_stats();
    else if (streq(tok[0], "KIM_GENERATE"))  cmd_kim_generate(tok[1], tok[2], tok[3]);
    /* Phase 7: Multi-Channel Dispatch + Lazy-Thaw + Model Update */
    else if (streq(tok[0], "LAZY_THAW"))           cmd_lazy_thaw(tok[1], tok[2]);
    else if (streq(tok[0], "MODEL_LOAD_UPDATE"))   cmd_model_load_update(tok[1], tok[2]);
    else if (streq(tok[0], "KIM_GENERATE_CTX")) {
        vbus_dispatch_ctx_t ctx;
        ctx.tag        = g_current_tag;
        ctx.slot_id    = g_current_slot_id;
        ctx.frame_type = VBUS_TYPE_CMD;
        cmd_kim_generate_ctx(&ctx, tok[1], tok[2], tok[3]);
    }
    /* Phase 8: V-Palace + V-AAAK */
    else if (streq(tok[0], "LOCK_PREFIX"))     cmd_lock_prefix(tok[1], tok[2] ? tok[2] : "4");
    else if (streq(tok[0], "UNLOCK_PREFIX"))   cmd_unlock_prefix(tok[1]);
    else if (streq(tok[0], "PALACE_ASSIGN"))   cmd_palace_assign(tok[1], tok[2], tok[3] ? tok[3] : "0");
    else if (streq(tok[0], "PALACE_QUERY"))    cmd_palace_query(tok[1], tok[2] ? tok[2] : "0");
    else if (streq(tok[0], "PALACE_STATS"))    cmd_palace_stats(tok[1]);
    else if (streq(tok[0], "PALACE_TOUCH"))    cmd_palace_touch(tok[1], tok[2] ? tok[2] : "0");
    else if (streq(tok[0], "V_AAAK_ENCODE"))   cmd_v_aaak_encode(tok[1]);
    else if (streq(tok[0], "V_AAAK_DECODE"))   cmd_v_aaak_decode(tok[1]);
    /* Phase 9: DMA Warp commands */
    else if (streq(tok[0], "DMA_WARP_XFER"))
        cmd_dma_warp_xfer(tok[1], tok[2], tok[3] ? tok[3] : "1", tok[4] ? tok[4] : "2097152");
    else if (streq(tok[0], "DMA_WARP_STATS")) cmd_dma_warp_stats();
    /* Phase 9: V-AAAK Native Mode */
    else if (streq(tok[0], "AAAK_MODE")) cmd_aaak_mode(tok[1] ? tok[1] : "0");
    /* v23.14: HMAC Statistics */
    else if (streq(tok[0], "HMAC_STATS")) cmd_hmac_stats();
    /* Cryptographic Provenance: kernel entropy for HMAC signing keys */
    else if (streq(tok[0], "GET_ENTROPY")) cmd_get_entropy();
    /* Phase 2.1: Managed KV Cache + vVFS commands */
    else if (streq(tok[0], "KV_TIER_STATUS")) cmd_kv_tier_status(tok[1]);
    else if (streq(tok[0], "KV_EVICT"))       cmd_kv_evict(tok[1], tok[2]);
    else if (streq(tok[0], "KV_PROMOTE"))     cmd_kv_promote(tok[1], tok[2]);
    else if (streq(tok[0], "VVFS_STAT"))      cmd_vvfs_stat(tok[1]);
    /* Phase 2.2: Speculative Decoding + Context Window */
    else if (streq(tok[0], "SPEC_CONFIGURE"))
        cmd_spec_configure(tok[1], tok[2], tok[3] ? tok[3] : "4");
    else if (streq(tok[0], "SPEC_GENERATE"))
        cmd_spec_generate(tok[1], tok[2]);
    else if (streq(tok[0], "SPEC_STATS"))    cmd_spec_stats(tok[1]);
    else if (streq(tok[0], "SPEC_SET_K"))    cmd_spec_set_k(tok[1], tok[2]);
    else if (streq(tok[0], "SPEC_RESET"))    cmd_spec_reset(tok[1]);
    else if (streq(tok[0], "CTX_WINDOW_INIT"))
        cmd_ctx_window_init(tok[1], tok[2]);
    else if (streq(tok[0], "CTX_WINDOW_FREEZE"))
        cmd_ctx_window_freeze(tok[1], tok[2]);
    else if (streq(tok[0], "CTX_WINDOW_STATUS"))
        cmd_ctx_window_status(tok[1]);
    /* Phase 2.3: Hybrid Orchestration */
    else if (streq(tok[0], "ORCH_START"))
        cmd_orch_start(tok[1], tok[2]);
    else if (streq(tok[0], "ORCH_DISPATCH"))
        cmd_orch_dispatch(tok[1], tok[2]);
    else if (streq(tok[0], "ORCH_STOP"))      cmd_orch_stop(tok[1]);
    else if (streq(tok[0], "ORCH_STATS"))     cmd_orch_stats();
    else if (streq(tok[0], "ORCH_GHOST"))
        cmd_orch_ghost(tok[1], tok[2]);
    /* Phase 4.1: vScreen Perception */
    else if (streq(tok[0], "VSCREEN_START"))
        cmd_vscreen_start(tok[1], tok[2], tok[3], tok[4], tok[5],
                          tok[6], tok[7]);
    else if (streq(tok[0], "VSCREEN_CAPTURE"))
        cmd_vscreen_capture(tok[1]);
    else if (streq(tok[0], "VSCREEN_STOP"))
        cmd_vscreen_stop(tok[1]);
    else if (streq(tok[0], "VSCREEN_STATUS"))
        cmd_vscreen_status(tok[1]);
    /* Phase 4.1: Agentic Mesh */
    else if (streq(tok[0], "MESH_DISPATCH"))
        cmd_mesh_dispatch(tok[1], tok[2], tok[3], tok[4]);
    else if (streq(tok[0], "MESH_RESULT"))
        cmd_mesh_result(tok[1], tok[2]);
    else if (streq(tok[0], "MESH_TRUST"))
        cmd_mesh_trust(tok[1]);
    /* Phase 4.1: Action Bridge */
    else if (streq(tok[0], "ACTION_SUBMIT"))
        cmd_action_submit(tok[1], tok[2], tok[3]);
    else if (streq(tok[0], "ACTION_APPROVE"))
        cmd_action_approve(tok[1], tok[2]);
    else if (streq(tok[0], "ACTION_EXECUTE"))
        cmd_action_execute(tok[1]);
    else if (streq(tok[0], "ACTION_AUDIT"))
        cmd_action_audit();
    else send_err(22, "unknown command");

    /* Phase 9.1: VBus Watchdog — log slow commands */
    {
        uint64_t elapsed = rdtsc_fence() - g_dispatch_start_tsc;
        if (elapsed > VBUS_CMD_CYCLE_LIMIT) {
            g_dispatch_timeouts++;
            VOS3_WARN("[VBUS-WATCHDOG] CMD '%s' took %llu cycles (limit %llu), timeout_count=%u",
                      tok[0], (unsigned long long)elapsed,
                      (unsigned long long)VBUS_CMD_CYCLE_LIMIT, g_dispatch_timeouts);
        }
    }
}

/* ---- Public API ---- */

/* Defined in vbus_transport.c */
extern void vos3_bridge_uart_reset(void);

int vos3_bridge_init(void)
{
    /* Initialize token bucket rate limiter */
    vos3_token_bucket_init(&g_rate_limiter);

    /* Try binary VBus transport first (virtio-serial-pci) */
    if (vos3_vbus_init() == 0) {
        g_use_vbus = 1;
        VOS3_INFO("[BRIDGE] VBus binary transport active — 41-command protocol (Phase 4.2.5)");
    } else {
        /* Fallback to legacy COM2 serial */
        VOS3_INFO("[BRIDGE] COM2 (0x2F8), no FIFO, 22-command protocol, bound-window CVE-2026-23086");
        vos3_bridge_uart_reset();
    }
    g_initialized = 1U;
    return 0;
}

void vos3_bridge_poll(void)
{
    if (!g_initialized) return;
    g_poll_count++;

    /* Phase 5: Periodic soft-scrub of context pages */
    static uint32_t g_ctx_scrub_poll = 0;
    if (++g_ctx_scrub_poll >= 10000) {
        vos3_ai_ctx_soft_scrub_tick();
        g_ctx_scrub_poll = 0;
    }

    if (g_use_vbus) {
        /* Binary VBus transport: receive CMD frames and dispatch */
        static uint8_t vbus_payload[VBUS_MAX_PAYLOAD];
        uint8_t frame_type;
        uint8_t slot_id;
        uint16_t tag;
        uint32_t frame_len;
        uint32_t frames_read = 0U;

        /* Dynamic poll limit: higher during streaming for throughput */
        uint32_t max_frames = vos3_ai_any_slot_streaming() ? 256U : 16U;

        /* Phase 4.5: Streaming path — per-slot DATA routing for concurrent loads */
        if (vos3_ai_any_slot_streaming()) {
            static uint32_t stream_dbg = 0;
            if (stream_dbg < 5) {
                VOS3_INFO("[BRIDGE] Streaming poll #%u (max=%u)", stream_dbg, max_frames);
                stream_dbg++;
            }
            while (frames_read < max_frames) {
                int rc = vos3_vbus_recv_frame(&frame_type, &slot_id, &tag,
                                               vbus_payload,
                                               sizeof(vbus_payload) - 1, &frame_len);
                if (rc == -1) break;
                if (rc == -3) {
                    /* v23.14: HMAC auth failure — distinct from CRC */
                    vos3_token_bucket_bad_crc(&g_rate_limiter);
                    continue;
                }
                if (rc == -2) {
                    /* CRC error — record for flood detection */
                    vos3_token_bucket_bad_crc(&g_rate_limiter);
                    continue;
                }
                frames_read++;

                /* Token bucket rate limit: cost = payload_size / 50 + 1 */
                {
                    uint32_t cost = (frame_len / 50U) + 1U;
                    if (!vos3_token_bucket_consume(&g_rate_limiter, cost)) {
                        continue;  /* Depleted or banned — drop frame */
                    }
                }

                /* Phase v17: Full serializing barrier after CRC validation */
                {
                    uint32_t _a, _b, _c, _d;
                    __asm__ volatile("mfence; cpuid"
                        : "=a"(_a), "=b"(_b), "=c"(_c), "=d"(_d)
                        : "a"(0) : "memory");
                }

                g_current_tag = tag;
                g_current_slot_id = slot_id;

                if (frame_type == VBUS_TYPE_DATA && vos3_ai_slot_is_streaming(slot_id)) {
                    /* Phase 4.6: Infinity Loop — memcpy hot path + SQ deferred hash */
                    size_t offset = g_sq_write_off[slot_id & 3];
                    void *dst = vos3_ai_slot_write_addr(slot_id, offset);
                    vos3_memcpy_optimized(dst, vbus_payload, frame_len);

                    /* Phase 4.8-H: sfence ensures HugePage payload is committed */
                    __asm__ volatile("sfence" ::: "memory");

                    /* Post SQ entry — hash/CRC deferred to sq_poll batch */
                    static uint64_t sq_tag = 0;
                    int sq_rc = vos3_sq_post(slot_id, VOS3_SQ_CMD_DATA,
                                              (uint16_t)(frame_len & 0xFFFF),
                                              (uint64_t)offset, sq_tag++);
                    if (sq_rc != 0) {
                        vos3_sq_poll();
                        sq_rc = vos3_sq_post(slot_id, VOS3_SQ_CMD_DATA,
                                              (uint16_t)(frame_len & 0xFFFF),
                                              (uint64_t)offset, sq_tag++);
                        if (sq_rc != 0) {
                            vos3_ai_slot_stream_write(slot_id, dst, frame_len);
                            vos3_cring_post(slot_id, 0x01, (uint16_t)(sq_tag & 0xFFFF));
                        }
                    }
                    g_sq_write_off[slot_id & 3] += frame_len;
                    static uint32_t data_cnt = 0;
                    data_cnt++;
                    if (data_cnt <= 3 || data_cnt % 100 == 0) {
                        VOS3_DEBUG("[BRIDGE] DATA #%u: slot=%u len=%u offset=%lu",
                                   data_cnt, slot_id, frame_len, (unsigned long)offset);
                    }
                } else if (frame_type == VBUS_TYPE_CMD && frame_len > 0 &&
                           frame_len < LINE_MAX) {
                    vbus_payload[frame_len] = '\0';
                    dispatch((char *)vbus_payload);
                } else if (frame_type == VBUS_TYPE_BATCH && frame_len > 0 &&
                           frame_len < LINE_MAX) {
                    vbus_payload[frame_len] = '\0';
                    char *start = (char *)vbus_payload;
                    for (char *p = start; ; p++) {
                        if (*p == '\n' || *p == '\0') {
                            char saved = *p;
                            *p = '\0';
                            if (p > start) {
                                VOS3_DEBUG("[BRIDGE] Executing batch command: %s", start);
                                dispatch(start);
                            }
                            if (saved == '\0') break;
                            start = p + 1;
                        }
                    }
                }
            }
            /* Phase 4.6: Batch-process SQ entries */
            vos3_sq_poll();
            vos3_vbus_event_ring_drain();
            return;
        }

        /* Normal (non-streaming) path */
        while (frames_read < max_frames) {
            int rc = vos3_vbus_recv_frame(&frame_type, &slot_id, &tag,
                                          vbus_payload,
                                          sizeof(vbus_payload) - 1, &frame_len);
            if (rc == -1) break;
            if (rc == -3) {
                /* v23.14: HMAC auth failure — distinct from CRC */
                vos3_token_bucket_bad_crc(&g_rate_limiter);
                continue;
            }
            if (rc == -2) {
                /* CRC error — record for flood detection */
                vos3_token_bucket_bad_crc(&g_rate_limiter);
                continue;
            }
            frames_read++;

            /* Token bucket rate limit: cost = payload_size / 50 + 1 */
            {
                uint32_t cost = (frame_len / 50U) + 1U;
                if (!vos3_token_bucket_consume(&g_rate_limiter, cost)) {
                    continue;  /* Depleted or banned — drop frame */
                }
            }

            /* Phase v17: Full serializing barrier (non-streaming path) */
            {
                uint32_t _a, _b, _c, _d;
                __asm__ volatile("mfence; cpuid"
                    : "=a"(_a), "=b"(_b), "=c"(_c), "=d"(_d)
                    : "a"(0) : "memory");
            }

            g_current_tag = tag;
            g_current_slot_id = slot_id;

            if (frame_type == VBUS_TYPE_CMD && frame_len > 0 && frame_len < LINE_MAX) {
                vbus_payload[frame_len] = '\0';
                dispatch((char*)vbus_payload);
            } else if (frame_type == VBUS_TYPE_BATCH && frame_len > 0 && frame_len < LINE_MAX) {
                vbus_payload[frame_len] = '\0';
                char *start = (char *)vbus_payload;
                for (char *p = start; ; p++) {
                    if (*p == '\n' || *p == '\0') {
                        char saved = *p;
                        *p = '\0';
                        if (p > start) {
                            VOS3_DEBUG("[BRIDGE] Executing batch command: %s", start);
                            dispatch(start);
                        }
                        if (saved == '\0') break;
                        start = p + 1;
                    }
                }
            } else if (frame_type == VBUS_TYPE_PING) {
                vos3_vbus_send_frame(VBUS_TYPE_PING, slot_id, tag, "PONG", 4);
            } else if (frame_type == VBUS_TYPE_HANDSHAKE) {
                /* VBus handshake: reset bridge state, negotiate version.
                 * Phase 6.6: v3 handshake enables AAAK semantic pipeline. */
                vos3_ai_force_stop_streaming();
                vos3_vbus_reset_rx();
                /* Phase 4.2.11: Full VirtIO hardware reset */
                vos3_vbus_hardware_reset();

                extern void vos3_vbus_set_hmac_key(const uint8_t *key, size_t len);
                extern int vos3_vbus_hmac_is_enabled(void);

                /* Phase 6.6-U: v3.1 handshake with session salt ("VBUS3" magic).
                 * Generate 16-byte AAAK salt from CSPRNG, append to response.
                 * Backend parses salt from response suffix to sync codec. */
                extern void vos3_vbus_set_aaak_salt(const uint8_t *salt);

                if (frame_len >= 5 && vbus_payload[0] == 'V' &&
                    vbus_payload[1] == 'B' && vbus_payload[2] == 'U' &&
                    vbus_payload[3] == 'S' && vbus_payload[4] == '3') {
                    g_vbus_protocol_version = 3;
                    /* v3 auto-enables AAAK semantic compression */
                    g_vbus_aaak_native = 1;

                    /* Generate session salt: 4 × u32 = 16 bytes from ChaCha20 CSPRNG */
                    uint8_t salt[16];
                    {
                        uint32_t *s32 = (uint32_t *)salt;
                        s32[0] = vos3_entropy_get_u32();
                        s32[1] = vos3_entropy_get_u32();
                        s32[2] = vos3_entropy_get_u32();
                        s32[3] = vos3_entropy_get_u32();
                    }
                    vos3_vbus_set_aaak_salt(salt);

                    if (frame_len >= 37) {
                        /* HMAC key provided — authenticated v3.1 with salt */
                        vos3_vbus_set_hmac_key(
                            (const uint8_t *)(vbus_payload + 5), 32);
                        /* Response: "VOS3-VBUS31-AAAK-HMAC-SALT" + 16-byte salt */
                        uint8_t resp[27 + 16];
                        const char *tag_str = "VOS3-VBUS31-AAAK-HMAC-SALT\x00";
                        for (int i = 0; i < 27; i++) resp[i] = (uint8_t)tag_str[i];
                        for (int i = 0; i < 16; i++) resp[27 + i] = salt[i];
                        vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                              resp, 43);
                    } else if (vos3_vbus_hmac_is_enabled()) {
                        /* Reject v3 without key when HMAC already active */
                        VOS3_WARN("[VBUS] v3 HMAC downgrade REJECTED");
                        g_vbus_protocol_version = 2;
                        g_vbus_aaak_native = 0;
                        vos3_vbus_set_aaak_salt(NULL); /* clear salt */
                        vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                              "ERR|HMAC_REQUIRED", 17);
                    } else {
                        /* Unauthenticated v3.1 with salt */
                        uint8_t resp[24 + 16];
                        const char *tag_str = "VOS3-VBUS31-AAAK-SALT\x00\x00\x00";
                        for (int i = 0; i < 24; i++) resp[i] = (uint8_t)tag_str[i];
                        for (int i = 0; i < 16; i++) resp[24 + i] = salt[i];
                        vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                              resp, 40);
                    }
                    VOS3_INFO("[VBUS] v3.1 handshake: AAAK=%s HMAC=%s SALT=ON",
                              g_vbus_aaak_native ? "ON" : "OFF",
                              vos3_vbus_hmac_is_enabled() ? "ON" : "OFF");
                }
                /* Fallback: v2 handshake ("VBUS2" magic) */
                else if (frame_len >= 5 && vbus_payload[0] == 'V' &&
                    vbus_payload[1] == 'B' && vbus_payload[2] == 'U' &&
                    vbus_payload[3] == 'S' && vbus_payload[4] == '2') {
                    g_vbus_protocol_version = 2;
                    g_vbus_aaak_native = 0;  /* v2 clients don't speak AAAK */
                    if (frame_len >= 37) {
                        /* HMAC key provided — enable authenticated frames */
                        vos3_vbus_set_hmac_key(
                            (const uint8_t *)(vbus_payload + 5), 32);
                        vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                              "VOS3-VBUS2-HMAC", 15);
                    } else {
                        /* v23.14: HMAC Mandatory Mode (D-CRIT1) — if HMAC is
                         * already enabled, reject handshake without a 32-byte
                         * key. Prevents downgrade to unauthenticated transport. */
                        if (vos3_vbus_hmac_is_enabled()) {
                            VOS3_WARN("[VBUS] HMAC downgrade REJECTED: "
                                      "handshake without key while HMAC active");
                            vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                                  "ERR|HMAC_REQUIRED", 17);
                        } else {
                            vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                                  "VOS3-VBUS2-OK", 13);
                        }
                    }
                } else {
                    vos3_vbus_send_frame(VBUS_TYPE_HANDSHAKE, 0xFF, 0,
                                          "VOS3-VBUS2-OK", 13);
                }
            } else if (frame_type == VBUS_TYPE_V_AAAK) {
                /* Phase 6.6: V-AAAK compressed command frame.
                 * Decode AAAK payload → raw text → dispatch as CMD.
                 * HMAC has already been verified on the compressed bytes
                 * (sign-what-you-send principle). */
                if (frame_len > 0 && frame_len < LINE_MAX) {
                    extern uint32_t vos3_v_aaak_decode(const uint8_t *in, uint32_t in_len,
                                                        uint8_t *out, uint32_t out_max);
                    /* Decode into a stack buffer (max LINE_MAX) */
                    uint8_t aaak_decoded[4096];
                    uint32_t decoded_len = vos3_v_aaak_decode(
                        vbus_payload, frame_len,
                        aaak_decoded, (uint32_t)(sizeof(aaak_decoded) - 1));
                    if (decoded_len > 0 && decoded_len < sizeof(aaak_decoded)) {
                        aaak_decoded[decoded_len] = '\0';
                        dispatch((char *)aaak_decoded);
                    } else {
                        VOS3_WARN("[VBUS] V-AAAK decode failed: len=%u rc=%d",
                                  frame_len, decoded_len);
                        vos3_vbus_send_frame(VBUS_TYPE_RESP, slot_id, tag,
                                              "ERR|AAAK_DECODE_FAIL", 20);
                    }
                }
            } else if (frame_type == VBUS_TYPE_INTERRUPT) {
                if (frame_len == 1) {
                    vos3_ai_model_slot_wake(vbus_payload[0]);
                    vos3_vbus_send_frame(VBUS_TYPE_RESP, vbus_payload[0], tag,
                                          "OK|woken", 8);
                } else if (frame_len >= 2) {
                    uint8_t evt = vbus_payload[0];
                    int rc2 = vos3_ai_event_deliver(evt, vbus_payload + 1,
                                                    (uint16_t)(frame_len - 1));
                    if (rc2 == 0)
                        vos3_vbus_send_frame(VBUS_TYPE_RESP, 0xFF, tag,
                                              "OK|delivered", 12);
                    else
                        vos3_vbus_send_frame(VBUS_TYPE_RESP, 0xFF, tag,
                                              "ERR|no_subscriber", 17);
                }
            } else if (frame_type == VBUS_TYPE_SUBSCRIBE) {
                if (frame_len >= 3 && vbus_payload[0] == 1) {
                    vos3_ai_event_subscribe(vbus_payload[1], vbus_payload[2]);
                    vos3_vbus_send_frame(VBUS_TYPE_RESP, vbus_payload[2], tag,
                                          "OK|subscribed", 13);
                } else if (frame_len >= 2 && vbus_payload[0] == 0) {
                    vos3_ai_event_unsubscribe(vbus_payload[1]);
                    vos3_vbus_send_frame(VBUS_TYPE_RESP, 0xFF, tag,
                                          "OK|unsubscribed", 15);
                } else {
                    vos3_vbus_send_frame(VBUS_TYPE_RESP, 0xFF, tag,
                                          "ERR|bad_subscribe", 17);
                }
            }
        }
        vos3_vbus_event_ring_drain();
        return;
    }

    /* Legacy serial transport */

    /* Timeout guard: flush stale partial line after ~1 second */
    if (g_line_pos > 0U) {
        g_line_age++;
        if (g_line_age >= LINE_TIMEOUT) {
            VOS3_WARN("[BRIDGE] Line timeout (%u bytes), flushing", g_line_pos);
            g_line_pos = 0U;
            g_line_age = 0U;
        }
    }

    /* Read available bytes — capped at 256 per poll to prevent lockup */
    uint32_t bytes_read = 0U;
    while ((port_inb(COM2 + UART_LSR) & LSR_DR) && bytes_read < MAX_BYTES_PER_POLL) {
        uint8_t c = port_inb(COM2 + UART_DATA);
        g_rx_total++;
        bytes_read++;

        if (c == '\n' || c == '\r') {
            if (g_line_pos > 0U) {
                g_line_buf[g_line_pos] = '\0';
                dispatch(g_line_buf);
                g_line_pos = 0U;
                g_line_age = 0U;
            }
        } else if (g_line_pos < LINE_MAX - 1U) {
            g_line_buf[g_line_pos++] = (char)c;
        }
    }
}

void vos3_bridge_task(void* arg)
{
    (void)arg;

    /* Enable interrupts so timer-based preemption works */
    __asm__ volatile ("sti" ::: "memory");

    VOS3_INFO("[BRIDGE] Task started — hybrid burst-poll, Phase 4.2.18");

    for (;;) {
        vos3_bridge_poll();

        /* Phase 4.2.18: Hybrid Burst Polling */
        if (vos3_ai_model_streaming_slot() >= 0) {
            /* Burst mode: 64 rapid re-polls with only 100 PAUSE each */
            for (int burst = 0; burst < 64; burst++) {
                for (volatile int d = 0; d < 100; d++)
                    __asm__ volatile ("pause" ::: "memory");
                vos3_bridge_poll();
            }
            /* Brief yield so timer ticks aren't starved */
            vos3_task_yield();
        } else {
            /* Idle mode: yield + standard pause */
            vos3_task_yield();
            for (volatile int d = 0; d < 10000; d++)
                __asm__ volatile ("pause" ::: "memory");
            /* Phase 4.7: Doorbell-driven SQ drain */
            uint32_t bells = vos3_doorbell_check();
            if (bells != 0) {
                vos3_sq_poll();
                vos3_doorbell_drain();
            }
        }
    }
}
