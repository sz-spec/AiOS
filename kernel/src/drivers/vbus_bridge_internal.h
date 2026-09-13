/**
 * @file vbus_bridge_internal.h
 * @brief Shared internal header for the VBus bridge split modules.
 *
 * Exposes globals, utility functions, and response helpers from the
 * bridge core (virtio_bridge.c / vbus_transport.c) that are needed
 * by the split-out command handler translation units.
 *
 * This header must NOT be included by any code outside kernel/src/drivers/.
 */

#ifndef VOS3_VBUS_BRIDGE_INTERNAL_H
#define VOS3_VBUS_BRIDGE_INTERNAL_H

/* ---- Common includes needed by all bridge modules ---- */

#include "../../include/vos/virtio_bridge.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/console.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/elf.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/virtio_vbus.h"
#include "../../include/vos/string.h"
#include "../../include/vos/ivshmem.h"
#include "../../include/vos/pci.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/dma_warp.h"
#include "../../include/vos/entropy.h"
/* Q2-2026 Hardening: Action Bridge for EXEC/APPLOAD redirection */
#include "../../include/vos/action_bridge.h"
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Shared Globals (defined in vbus_transport.c)
 * ============================================================================ */

extern int g_use_vbus;                  /* 1 = binary VBus transport */
extern uint16_t g_current_tag;          /* VBus v2 tag echo state */
extern uint8_t  g_current_slot_id;      /* VBus v2 slot echo state */
extern size_t g_sq_write_off[4];        /* Per-slot SQ write offset */
extern uint32_t g_vbus_protocol_version;
extern uint64_t g_vbus_hmac_seq[8];     /* Phase 7: Per-slot HMAC sequence counter (64-bit) */

/* Phase 7: Per-dispatch frame context (replaces global tag/slot state) */
typedef struct vbus_dispatch_ctx {
    uint16_t tag;
    uint8_t  slot_id;
    uint8_t  frame_type;
} vbus_dispatch_ctx_t;

/* ============================================================================
 * Response Helpers (defined in vbus_transport.c)
 * ============================================================================ */

void send_ok(const char* data);
void send_err(int code, const char* msg);

/* Phase 7: Context-aware response helpers */
void send_ok_ctx(const vbus_dispatch_ctx_t *ctx, const char *data);
void send_err_ctx(const vbus_dispatch_ctx_t *ctx, int code, const char *msg);

/* ============================================================================
 * Utility Functions (defined in vbus_transport.c)
 * ============================================================================ */

void uint_to_str(uint64_t val, char* buf, int bufsz);
void int_to_str(int32_t val, char* buf, int bufsz);
const char* errno_str(int code);
size_t bridge_strlen(const char* s);
void bridge_strcpy(char* dst, const char* src, size_t max);
void hex_encode(const uint8_t* data, size_t len, char* out);
int hex_decode(const char* hex, uint8_t* out, size_t max, size_t* out_len);
int hex_digit(char c);
uint32_t parse_uint(const char* s);
uint64_t parse_u64(const char *s);
void u64_to_hex(uint64_t val, char *out, int bufsz);
uint32_t vos3_bridge_bound_window(void);

/* Congestion control — token bucket rate limiter */
void congestion_enter(void);
void congestion_leave(void);
extern volatile uint32_t g_congestion;
extern vos3_token_bucket_t g_rate_limiter;

/* Per-CMD HMAC verification (Batch F: Instruction Integrity Tags).
 *
 * A3-1 three-valued return contract (kept as int-compatible macros so the
 * existing `extern int` call sites stay warning-free):
 *   VOS3_HMAC_VALID      — trailer present AND MAC authentic.
 *   VOS3_HMAC_INVALID    — trailer present but MAC failed (tamper/forgery);
 *                          a structural alert (records an HMAC violation).
 *   VOS3_HMAC_NO_TRAILER — no 32-byte trailer present, or no session key.
 *                          This is NOT "valid" — a caller that requires HMAC
 *                          MUST fail-closed on this (it used to be conflated
 *                          with VALID, the A3-1 strip path). The split lets the
 *                          call site refuse a stripped frame explicitly.
 */
#define VOS3_HMAC_INVALID    0
#define VOS3_HMAC_VALID      1
#define VOS3_HMAC_NO_TRAILER 2
int vos3_check_hmac_ban(void);
int vos3_verify_cmd_hmac(uint8_t type, uint8_t slot_id, uint16_t tag,
                         const uint8_t *payload, uint32_t payload_len,
                         const uint8_t *session_key, uint32_t key_len);

/* Hardening limits */
#define MAX_CHUNK_SIZE 8192U
#define LINE_MAX_BRIDGE 9216U

/* ============================================================================
 * Command Handler Declarations — FS Commands (vbus_fs_cmds.c)
 * ============================================================================ */

void cmd_stat(const char* path);
void cmd_write(const char* path, const char* hex_data);
void cmd_read(const char* path);
void cmd_ls(const char* path);
void cmd_mkdir(const char* path);
void cmd_unlink(const char* path);
void cmd_rename(const char* oldpath, const char* newpath);
void cmd_readc(const char* path, const char* offset_str, const char* len_str);
void cmd_append(const char* path, const char* hex_data);
void cmd_lsm(const char* path);
void cmd_rmdir(const char* path);
void cmd_exec(const char* path, const char* args);
void cmd_httpget(const char* url);

/* ============================================================================
 * Command Handler Declarations — AI Commands (vbus_ai_cmds.c)
 * ============================================================================ */

void cmd_model_start(const char *id_str, const char *size_str);
void cmd_model_sync(void);
void cmd_model_rewind(const char *offset_str);
void cmd_model_done(void);
void cmd_model_check(const char *addr_str);
void cmd_model_tamper(const char *addr_str);

void cmd_slot_start(const char *slot_str, const char *id_str,
                    const char *size_str, const char *label_str,
                    const char *version_str, const char *epoch_str);
void cmd_slot_finish(const char *slot_str);
void cmd_model_sig_register(const char *slot_str, const char *digest_hex,
                            const char *sig_hex, const char *signer_hex);
void cmd_slot_sync(const char *slot_str);
void cmd_slot_rewind(const char *slot_str, const char *offset_str);
void cmd_slot_suspend(const char *slot_str);
void cmd_slot_resume(const char *slot_str);
void cmd_slot_status(const char *slot_str);
void cmd_slot_check(const char *slot_str, const char *addr_str);
void cmd_slot_snapshot(const char *slot_str);
void cmd_slot_timer(const char *slot_str, const char *ms_str);
void cmd_slot_reset(const char *slot_str);
void cmd_slot_swap(const char *a_str, const char *b_str);
void cmd_vdev_read(const char *slot_str, const char *off_str, const char *len_str);

void cmd_isc_send(const char *from_str, const char *to_str,
                  const char *ctx_str, const char *hex_data);
void cmd_isc_recv(const char *slot_str);
void cmd_slot_dormant(const char *slot_str);
void cmd_slot_wake(const char *slot_str);
void cmd_slot_quota(const char *slot_str, const char *cycles_str);
void cmd_slot_caps(const char *slot_str, const char *caps_str, const char *type_str);
void cmd_slot_affinity(const char *slot_str, const char *cpu_str);
void cmd_slot_checkpoint(const char *slot_str);
void cmd_slot_rollback(const char *slot_str);
void cmd_slot_io_mask(const char *slot_str, const char *mask_str);
void cmd_slot_enumerate(void);

void cmd_slot_context_config(const char *slot_str, const char *count_str);
void cmd_slot_warm_reset(const char *slot_str);
void cmd_event_subscribe(const char *event_str, const char *slot_str);
void cmd_event_unsubscribe(const char *event_str);
void cmd_slot_shared_map(const char *slot_str, const char *phys_str);
void cmd_slot_barrier_wait(const char *slot_str, const char *mask_str);
void cmd_yield_ex(const char *slot_str, const char *timeout_str, const char *mask_str);
void cmd_commit_slot(const char *slot_str);
void cmd_slot_gate(const char *slot_str, const char *enable_str);
void cmd_context_share(const char *src_str, const char *dst_str);

void cmd_warp_post(const char *s_slot, const char *s_off, const char *s_len);
void cmd_warp_status(void);
void cmd_hp_stats(void);
void cmd_driver_pressure(void);

/* Phase 5: Context Management (Freeze/Thaw) */
void cmd_ctx_freeze(const char *slot_str);
void cmd_ctx_scrub(const char *slot_str);
void cmd_ctx_read(const char *slot_str, const char *page_str);
void cmd_ctx_thaw(const char *slot_str, const char *count_str,
                  const char *fid_str, const char *epoch_str);
void cmd_ctx_thaw_cold(const char *slot_str, const char *count_str,
                       const char *fid_str, const char *epoch_str);
void cmd_ctx_write(const char *slot_str, const char *page_str,
                   const char *hex_data);
void cmd_ctx_thaw_done(const char *slot_str, const char *crc_str);
void cmd_ctx_latency(const char *slot_str);

/* Batch F: Inference-Aware Scheduling */
void cmd_inference_hint(const char *slot_str, const char *state_str);

/* Phase 6: Kernel Inference Manager */
void cmd_kim_generate(const char *slot_str, const char *max_str,
                      const char *temp_str);

/* Phase 6: KIM Stats */
void cmd_kim_stats(void);

/* Phase 7: Lazy-Thaw + Model Update */
void cmd_lazy_thaw(const char *slot_str, const char *enable_str);
void cmd_model_load_update(const char *active_slot_str, const char *shadow_slot_str);
void cmd_kim_generate_ctx(const vbus_dispatch_ctx_t *ctx,
                          const char *slot_str, const char *max_str,
                          const char *temp_str);

/* Phase 8: V-AAAK + V-Palace Bridge Commands */
void cmd_v_aaak_encode(const char *hex_data);
void cmd_v_aaak_decode(const char *hex_data);
void cmd_lock_prefix(const char *slot_str, const char *count_str);
void cmd_unlock_prefix(const char *slot_str);
void cmd_palace_assign(const char *slot_str, const char *hp_str, const char *hall_str);
void cmd_palace_query(const char *slot_str, const char *hp_str);
void cmd_palace_stats(const char *slot_str);
void cmd_palace_touch(const char *slot_str, const char *hp_str);

/* Phase 8: V-AAAK encode/decode (defined in vbus_transport.c) */
uint32_t vos3_v_aaak_encode(const uint8_t *in, uint32_t in_len,
                             uint8_t *out, uint32_t out_max);
uint32_t vos3_v_aaak_decode(const uint8_t *in, uint32_t in_len,
                             uint8_t *out, uint32_t out_max);

/* Phase 9: V-AAAK Native Mode */
extern int g_vbus_aaak_native;  /* 1 = auto-compress all text responses */
void cmd_aaak_mode(const char *enable_str);

/* Phase 6.4.1-U: Chained-HMAC Token Provenance (defined in virtio_vbus.c) */
void vos3_vbus_chain_token_mac(uint8_t slot_id,
                                const uint8_t *payload, uint32_t payload_len,
                                uint8_t out_mac[32]);
void vos3_vbus_reset_token_chain(uint8_t slot_id);

/* ============================================================================
 * Command Handler Declarations — Diagnostic Commands (vbus_diag_cmds.c)
 * ============================================================================ */

void cmd_ping(void);
void cmd_sysinfo(void);
void cmd_build_uuid(void);
void cmd_procs(void);
void cmd_kaslr_base(void);
void cmd_cpu_path(void);
void cmd_cring_status(void);
void cmd_vector_check(void);
void cmd_memcpy_bench(void);
void cmd_core_pin(const char *slot_str);
void cmd_smp_status(void);
void cmd_sq_status(void);
void cmd_doorbell_status(void);
void cmd_cring_flood(const char *count_str);
void cmd_tlb_audit(void);
void cmd_query_cr4(void);
void cmd_heartbeat_addr(void);
void cmd_heartbeat_status(void);
void cmd_pci_list(void);
void cmd_ktext_hash(void);
void cmd_ctx_stats(const char *slot_str);
void cmd_hmac_stats(void);
void cmd_get_entropy(void);

/* Phase 9: Storage HAL command */
void cmd_storage_info(void);

/* v20.2: MMR Audit Ledger */
void cmd_mmr_root(void);

/* v21.3.1 (Track A) — KV-cache efficiency monitoring + benchmark probe. */
void cmd_efficiency_stats(void);
void cmd_kv_bench_probe(void);

/* ============================================================================
 * Command Handler Declarations — App Commands (vbus_app_cmds.c)
 * ============================================================================ */

void cmd_appload(const char* app_id_str, const char* binary_path);
void cmd_appstat(const char* app_id_str);
void cmd_appkill(const char* app_id_str, const char* retention_str);
void cmd_applogs(const char* id_str, const char* offset_str);
void cmd_applist(void);
void cmd_agent_kill_all(void);

/* ============================================================================
 * Command Handler Declarations — Phase 2.2: Speculative Decoding (vbus_ai_cmds.c)
 * ============================================================================ */

void cmd_spec_configure(const char *draft_str, const char *target_str,
                        const char *k_str);
void cmd_spec_generate(const char *slot_str, const char *token_str);
void cmd_spec_stats(const char *slot_str);
void cmd_spec_set_k(const char *slot_str, const char *k_str);
void cmd_spec_reset(const char *slot_str);
void cmd_ctx_window_init(const char *slot_str, const char *max_str);
void cmd_ctx_window_freeze(const char *slot_str, const char *prefix_str);
void cmd_ctx_window_status(const char *slot_str);

/* ============================================================================
 * Command Handler Declarations — Phase 2.3: Hybrid Orchestration (vbus_ai_cmds.c)
 * ============================================================================ */

void cmd_orch_start(const char *draft_str, const char *target_str);
void cmd_orch_dispatch(const char *session_str, const char *token_str);
void cmd_orch_stop(const char *session_str);
void cmd_orch_stats(void);
void cmd_orch_ghost(const char *session_str, const char *enable_str);

/* ============================================================================
 * Command Handler Declarations — DMA Warp Commands (vbus_ai_cmds.c)
 * ============================================================================ */

void cmd_dma_warp_xfer(const char *slot_str, const char *hp_str,
                        const char *count_str, const char *len_str);
void cmd_dma_warp_stats(void);

/* ============================================================================
 * Command Handler Declarations — Phase 2.1: Managed KV + vVFS (vbus_ai_cmds.c)
 * ============================================================================ */

void cmd_kv_tier_status(const char *slot_str);
void cmd_kv_evict(const char *slot_str, const char *tier_str);
void cmd_kv_promote(const char *slot_str, const char *seq_str);
void cmd_vvfs_stat(const char *slot_str);

/* ============================================================================
 * Command Handler Declarations — Phase 4.1: vScreen + Mesh + Action (vbus_ai_cmds.c)
 * ============================================================================ */

void cmd_vscreen_start(const char *slot_str, const char *scanout_str,
                       const char *phys_str, const char *width_str,
                       const char *height_str, const char *patch_str,
                       const char *flags_str);
void cmd_vscreen_capture(const char *slot_str);
void cmd_vscreen_stop(const char *slot_str);
void cmd_vscreen_status(const char *slot_str);
void cmd_mesh_dispatch(const char *src_str, const char *target_str,
                       const char *type_str, const char *prio_str);
void cmd_mesh_result(const char *task_str, const char *code_str);
void cmd_mesh_trust(const char *slot_str);
void cmd_action_submit(const char *slot_str, const char *type_str,
                       const char *cmd_str);
void cmd_action_approve(const char *approver_str, const char *id_str);
void cmd_action_execute(const char *id_str);
void cmd_action_audit(void);

/* Cyber overlay (Stages 4–5): IntentManifest submission bridge.
 * slot_str is optional (NULL = validate-only; non-NULL = validate + bind to slot). */
void cmd_intent_submit(const char *hex_str, const char *slot_str);

/* Cyber overlay (Stage 6): diagnostic / telemetry commands. */
void cmd_tee_env(void);
void cmd_tee_quote(void);
void cmd_sched_cookie_stats(void);
void cmd_hcs_flush(void);
void cmd_sched_sibling_check(const char *cpu_str);

/* Stage 10.1: kernel-side compliance failure ring snapshot. */
void cmd_audit_fail_quote(void);

/* Stage 10.2: hallucination guardrail check (per-slot min_confidence_score). */
void cmd_action_check_confidence(const char *slot_str, const char *score_str);

/* Stage 10.2.2: management override / Safe-Rollout VBus surface. */
void cmd_policy_override(const char *slot_str, const char *score_str);
void cmd_policy_force_permit(const char *enable_str);
void cmd_policy_status(void);

#endif /* VOS3_VBUS_BRIDGE_INTERNAL_H */
