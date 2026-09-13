/**
 * @file vbus_ai_cmds.c
 * @brief VBus bridge AI/model command handlers.
 *
 * Extracted from virtio_bridge.c during the bridge split refactor.
 * Contains: MODEL_START/SYNC/REWIND/DONE/CHECK/TAMPER,
 * SLOT_START/FINISH/SYNC/REWIND/SUSPEND/RESUME/STATUS/CHECK/SNAPSHOT/
 * TIMER/RESET/SWAP, VDEV_READ, ISC_SEND/RECV, SLOT_DORMANT/WAKE/QUOTA/
 * CAPS/AFFINITY/CHECKPOINT/ROLLBACK/IO_MASK/ENUMERATE,
 * Context Persistence, Event Pub/Sub, Autonomous Agentic,
 * WARP_POST/WARP_STATUS, HP_STATS.
 */

#include "vbus_bridge_internal.h"
#include "../../include/vos/ai_orch.h"
#include "../../include/vos/vbus.h"  /* [APEX-HOME] vos3_vbus_check_pro_license */
#include "../../include/vos/kv_compressor.h"  /* M4 efficiency-stats helper */
#include "../../include/vos/tee.h"           /* Cyber overlay (Stage 4): vos3_intent_validate + VOS3_INTENT_* codes */
#include "../../include/vos/sha384.h"        /* Cyber overlay (Stage 5): vos3_sha384 for slot weights digest */
#include "../../include/vos/hcs.h"           /* Cyber overlay (Stage 6): vos3_hcs_flush manual-source command */
#include "../../include/vos/audit_ring.h"    /* Stage 10.1: kernel-side compliance failure ring */
#include "../../include/vos/action_bridge.h" /* Stage 10.2: hallucination guardrail (per-slot min_confidence_score) */
#include "../../include/vos/atomic.h"        /* Resilience-Matrix F1: per-slot spinlocks */
#include "../../include/vos/ai_guard.h"      /* VOS3_MODEL_SLOT_MAX */
#include "../../include/vos/vvfs.h"          /* M3: vvfs_verify_model_slot + VOS3_VVFS_REQUIRE_MODEL_SIG */
#include "../../include/vos/sha256.h"        /* M3: SHA-256 over loaded model bytes */

/* ============================================================================
 * Resilience-Matrix F1 — per-slot binding lock.
 *
 * The Stage-5 INTENT_SUBMIT path takes a manifest, reads the slot's
 * weight bytes, computes h_M, and calls vos3_tee_slot_activate_bound()
 * which extends RTMR[1]. If two INTENT_SUBMIT frames arrive for the
 * same slot, both reads of info.base/info.size could race, and the
 * RTMR ladder could be extended out-of-order.
 *
 * The vbus dispatcher is single-threaded today, but this lock is
 * insurance against any future move to a multi-dispatcher design and
 * also future-proofs the path for SMP delivery via virtio-serial
 * multiqueue. The lock is held only for the duration of the bind —
 * the validate path (Stage 4) is not gated by it.
 * ============================================================================ */
static vos3_spinlock_t g_slot_bind_locks[VOS3_MODEL_SLOT_MAX];

/* ============================================================================
 * [OLYMPUS-FIX APEX-HOME — Commercial Seal v21.2.2]
 * Architectural PRO-license gate at the AI command-dispatch boundary.
 * ============================================================================
 *
 * Today every cmd_* handler in this file is CORE — no profile_flags
 * mechanism reaches them, and no caller emits a PRO request. The
 * helper below is the *seam*: future PRO-gated commands declare a
 * non-zero profile_flags input and call this gate at the top of
 * their handler. Returning a non-zero reject code means "send the
 * matching VBus REJECT response and abort"; zero means "proceed".
 *
 * Why a seam, not a per-command rewrite: charter rule 1
 * (kernel/pro/README.md) keeps every CORE primitive ungated. The
 * 10 GiB hugepage ceiling already gates the only PRO-runtime
 * feature. Adding the seam here means future PRO commands have a
 * one-line check pattern — `if (vos3_ai_pro_gate(flags)) return;` —
 * instead of inventing the architecture per-command.
 *
 * The architecture is now in place. No call site exists today
 * because no PRO command exists; that is correct behavior, not a
 * gap.
 */
static int vos3_ai_pro_gate(uint32_t profile_flags)
{
    uint32_t reject = vos3_vbus_check_pro_license(profile_flags);
    if (reject != 0u) {
        char err_buf[64];
        const char *base = "EPRO: ";
        int wi = 0;
        while (base[wi] && wi < 60) { err_buf[wi] = base[wi]; wi++; }
        char num_buf[12];
        uint_to_str(reject, num_buf, sizeof(num_buf));
        for (int i = 0; num_buf[i] && wi < 62; i++) err_buf[wi++] = num_buf[i];
        err_buf[wi] = '\0';
        send_err((int)reject, err_buf);
        return 1; /* reject — caller MUST return immediately */
    }
    return 0;
}

/* ============================================================================
 * PHASE 4.2: AI MODEL COMMANDS
 * ============================================================================ */

/* MODEL_START|model_id|size */
void cmd_model_start(const char *id_str, const char *size_str)
{
    /* [OLYMPUS-FIX APEX-HOME] Commercial seam — today profile_flags=0
     * (CORE caller), gate returns 0, behavior unchanged. When a
     * future PRO model variant emits VOS3_VBUS_PROFILE_PRO_FEATURE,
     * the gate fires and rejects without entering the handler. */
    if (vos3_ai_pro_gate(0u)) return;

    uint32_t model_id = (uint32_t)parse_u64(id_str);
    size_t size = (size_t)parse_u64(size_str);

    if (size == 0 || size > 256 * 1024 * 1024) {
        send_err(22, "EINVAL: size 0 or >256MB");
        return;
    }

    uintptr_t base = vos3_ai_model_start(model_id, size);
    if (base == 0) {
        send_err(12, "ENOMEM: HugePage alloc failed");
        return;
    }

    char resp[80];
    char hex_buf[20], id_buf[12];
    u64_to_hex(base, hex_buf, sizeof(hex_buf));
    uint_to_str(model_id, id_buf, sizeof(id_buf));
    int ri = 0;
    const char *s;
    s = hex_buf; while (*s && ri < 78) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = id_buf; while (*s && ri < 78) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* MODEL_SYNC */
void cmd_model_sync(void)
{
    size_t offset = vos3_ai_model_get_offset();
    char buf[20];
    uint_to_str((uint64_t)offset, buf, sizeof(buf));
    send_ok(buf);
}

/* MODEL_REWIND|offset */
void cmd_model_rewind(const char *offset_str)
{
    uint64_t target = parse_u64(offset_str);

    int rc = vos3_ai_model_rewind((size_t)target);
    if (rc != 0) {
        send_err(22, "EINVAL: invalid rewind target");
        return;
    }

    char buf[20];
    uint_to_str(target, buf, sizeof(buf));
    send_ok(buf);
}

/* MODEL_DONE */
void cmd_model_done(void)
{
    vos3_ai_model_info_t info;
    int rc = vos3_ai_model_finish(&info);
    if (rc != 0) {
        send_err(-rc, "model_finish failed");
        return;
    }

    char resp[120];
    char addr_hex[20], size_buf[20], cksum_hex[20];
    u64_to_hex(info.base, addr_hex, sizeof(addr_hex));
    uint_to_str(info.size, size_buf, sizeof(size_buf));
    u64_to_hex(info.checksum, cksum_hex, sizeof(cksum_hex));

    int ri = 0;
    const char *s;
    s = addr_hex; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = size_buf; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = cksum_hex; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* MODEL_CHECK|addr */
void cmd_model_check(const char *addr_str)
{
    uintptr_t addr = (uintptr_t)parse_u64(addr_str);
    uint64_t pte = vos3_ai_model_check_pte(addr);

    if (pte == 0) {
        send_err(14, "EFAULT: unmapped address");
        return;
    }

    char hex_buf[20];
    u64_to_hex(pte, hex_buf, sizeof(hex_buf));
    send_ok(hex_buf);
}

/* MODEL_TAMPER|addr */
void cmd_model_tamper(const char *addr_str)
{
    uintptr_t addr = (uintptr_t)parse_u64(addr_str);

    volatile int is_protected = 0;
    for (uint32_t i = 0; i < 64U; i++) {
        vos3_pte_t pte;
        int rc = vos3_vmm_get_pte(addr + (uintptr_t)i * 4096, &pte);
        if (rc == 0 && (pte & VOS3_PTE_AI_PROTECTED) && !(pte & VOS3_PTE_WRITABLE)) {
            is_protected = 1;
        }
    }
    /* Normalize latency */
    for (volatile uint32_t d = 0; d < 100000U; d++) {
        __asm__ volatile("" ::: "memory");
    }

    if (is_protected) {
        vos3_ai_slot_inc_access_violations(0);
    }
    send_err(13, is_protected ? "PROTECTED" : "UNPROTECTED");
}

/* ============================================================================
 * PHASE 4.2.5: MULTI-SLOT BRIDGE COMMANDS
 * ============================================================================ */

static const char *slot_status_name(vos3_model_slot_status_t s)
{
    switch (s) {
        case VOS3_SLOT_FREE:      return "free";
        case VOS3_SLOT_STREAMING: return "streaming";
        case VOS3_SLOT_ACTIVE:    return "active";
        case VOS3_SLOT_WARM:      return "warm";
        case VOS3_SLOT_SUSPENDED: return "suspended";
        case VOS3_SLOT_CORRUPT:   return "corrupt";
        case VOS3_SLOT_DORMANT:   return "dormant";
        case VOS3_SLOT_STUCK:              return "stuck";
        case VOS3_SLOT_SUSPENDED_PENDING:  return "suspended_pending";
        default:                           return "unknown";
    }
}

/* SLOT_START|slot_id|model_id|size|label[|model_version|model_epoch] */
void cmd_slot_start(const char *slot_str, const char *id_str,
                    const char *size_str, const char *label_str,
                    const char *version_str, const char *epoch_str)
{
    uint8_t slot_id  = (uint8_t)parse_u64(slot_str);
    uint32_t model_id = (uint32_t)parse_u64(id_str);
    size_t size       = (size_t)parse_u64(size_str);
    const char *label = label_str ? label_str : "";

    if (size == 0 || size > 256 * 1024 * 1024) {
        send_err(22, "EINVAL: size 0 or >256MB");
        return;
    }

    /* Phase 4.6: Reset SQ write offset for this slot */
    if (slot_id < 4) g_sq_write_off[slot_id] = 0;

    /* Phase 6.4.1-U: Reset token chain MAC on new slot session */
    vos3_vbus_reset_token_chain(slot_id);

    uintptr_t base = vos3_ai_model_slot_start(slot_id, model_id, size, label, 128);
    if (base == 0) {
        if (vos3_ai_model_streaming_slot() >= 0) {
            send_err(16, "EBUSY");
        } else {
            send_err(12, "ENOMEM: HugePage alloc failed");
        }
        return;
    }

    /* Phase 5 — Tier 2: Set ownership — bridge task owns the slot during loading */
    {
        vos3_task_t *cur = vos3_sched_current();
        vos3_ai_set_slot_owner(slot_id, cur ? cur->tid : 0);
    }

    /* Prefetch: Notify prediction engine of slot load */
    {
        extern void vos3_prefetch_on_slot_load(uint8_t slot_id);
        vos3_prefetch_on_slot_load(slot_id);
    }

    /* Phase 4.2.7: Set semantic registry fields */
    {
        vos3_ai_model_slot_t info;
        vos3_ai_model_slot_get_info(slot_id, &info);
        uint32_t version = version_str ? (uint32_t)parse_u64(version_str) : 0;
        uint32_t epoch   = epoch_str   ? (uint32_t)parse_u64(epoch_str)   : 0;
        if (version_str || epoch_str) {
            extern void vos3_ai_slot_set_version(uint8_t slot, uint32_t ver, uint32_t ep);
            vos3_ai_slot_set_version(slot_id, version, epoch);
        }
    }

    char resp[120];
    char hex_buf[20], slot_buf[4];
    u64_to_hex(base, hex_buf, sizeof(hex_buf));
    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));

    int ri = 0;
    const char *s;
    s = hex_buf; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = slot_buf; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = label; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_FINISH|slot_id */
void cmd_slot_finish(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    /* Phase 4.6: Drain SQ before finalizing checksums */
    vos3_sq_poll();

    vos3_ai_model_info_t info;
    int rc = vos3_ai_model_slot_finish(slot_id, &info);
    if (rc != 0) {
        send_err(-rc, "slot_finish failed");
        return;
    }

#if VOS3_VVFS_REQUIRE_MODEL_SIG
    /* M3 SecureBoot (fail-closed; default-off): compute SHA-256 over the loaded
     * model bytes and verify the slot's registered OMS Ed25519 signature
     * against the provisioned trusted key. Refuse activation on any failure.
     * The OMS bundle/signature is delivered by the Phase-3 transport via
     * vvfs_register_model_signature(); until then this rejects (fail-closed). */
    {
        uint8_t mdg[VOS3_SHA256_DIGEST_SIZE];
        vos3_sha256_ctx_t sctx;
        vos3_sha256_init(&sctx);
        vos3_sha256_update(&sctx, (const void *)(uintptr_t)info.base, info.size);
        vos3_sha256_final(&sctx, mdg);
        int vrc = vvfs_verify_model_slot(slot_id, mdg);
        if (vrc != 0) {
            send_err(-vrc, "M3: model signature verification failed");
            return;
        }
    }
#endif

    char resp[160];
    char addr_hex[20], size_buf[20], xxh3_hex[20], crc_hex[20];
    u64_to_hex(info.base, addr_hex, sizeof(addr_hex));
    uint_to_str(info.size, size_buf, sizeof(size_buf));
    u64_to_hex(info.checksum, xxh3_hex, sizeof(xxh3_hex));
    u64_to_hex((uint64_t)info.crc32c, crc_hex, sizeof(crc_hex));

    int ri = 0;
    const char *s;
    s = addr_hex; while (*s && ri < 158) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = size_buf; while (*s && ri < 158) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = xxh3_hex; while (*s && ri < 158) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = crc_hex; while (*s && ri < 158) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* --- M3 OMS-signature transport --------------------------------------------
 * MODEL_SIG|slot_id|digest_hex(64)|sig_hex(128)|signer_hex(64)
 *
 * Registers a slot's OMS signature so SLOT_FINISH can verify it against the
 * BUILD-embedded trusted key. Delivering a signature over VBus is NOT a trust
 * escalation — it is checked against the immutable anchor (vvfs_trusted_key.h),
 * which is deliberately never settable over VBus. Bad/short hex => fail-closed
 * (no registration => SLOT_FINISH rejects under enforcement). */
static int m3_nib(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int m3_hex2bin(const char *h, uint8_t *out, int n)
{
    int i, hi, lo;
    if (h == NULL) return -1;
    for (i = 0; i < n; i++) {
        hi = m3_nib(h[2 * i]);
        if (hi < 0) return -1;          /* non-hex or premature '\0' */
        lo = m3_nib(h[2 * i + 1]);
        if (lo < 0) return -1;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return 0;
}

void cmd_model_sig_register(const char *slot_str, const char *digest_hex,
                            const char *sig_hex, const char *signer_hex)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint8_t digest[32], sig[64], signer[32];

    if (m3_hex2bin(digest_hex, digest, 32) != 0 ||
        m3_hex2bin(sig_hex, sig, 64) != 0 ||
        m3_hex2bin(signer_hex, signer, 32) != 0) {
        send_err(22, "MODEL_SIG: malformed hex");
        return;
    }
    int rc = vvfs_register_model_signature(slot_id, digest, sig, signer);
    if (rc != 0) {
        send_err(-rc, "MODEL_SIG: register failed");
        return;
    }
    send_ok("MODEL_SIG_OK");
}

/* SLOT_SYNC|slot_id */
void cmd_slot_sync(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    vos3_sq_poll();
    size_t offset = vos3_ai_slot_get_offset(slot_id);
    char buf[20];
    uint_to_str((uint64_t)offset, buf, sizeof(buf));
    send_ok(buf);
}

/* SLOT_REWIND|slot_id|offset */
void cmd_slot_rewind(const char *slot_str, const char *offset_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint64_t target = parse_u64(offset_str);

    int rc = vos3_ai_model_slot_rewind(slot_id, (size_t)target);
    if (rc != 0) {
        send_err(22, "EINVAL: invalid rewind target");
        return;
    }
    char buf[20];
    uint_to_str(target, buf, sizeof(buf));
    send_ok(buf);
}

/* SLOT_SUSPEND|slot_id */
void cmd_slot_suspend(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_model_slot_suspend(slot_id);
    if (rc == -1) {
        send_err(1, "EPERM");
        return;
    }
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    char resp[40];
    char slot_buf[4];
    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));
    int ri = 0;
    const char *s;
    s = slot_buf; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "suspended"; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_RESUME|slot_id */
void cmd_slot_resume(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_model_slot_resume(slot_id);
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    char resp[40];
    char slot_buf[4];
    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));
    int ri = 0;
    const char *s;
    s = slot_buf; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "active"; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_STATUS|slot_id */
void cmd_slot_status(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: bad slot");
        return;
    }

    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    char resp[256];
    char slot_buf[4], pri_buf[4], mid_buf[12], size_buf[20], cksum_hex[20];
    char cyc_buf[20], viol_buf[20];

    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));
    uint_to_str(info.priority, pri_buf, sizeof(pri_buf));
    uint_to_str(info.model_id, mid_buf, sizeof(mid_buf));
    uint_to_str(info.size, size_buf, sizeof(size_buf));
    u64_to_hex(info.checksum, cksum_hex, sizeof(cksum_hex));
    uint_to_str(info.cycle_count, cyc_buf, sizeof(cyc_buf));
    uint_to_str(info.access_violations, viol_buf, sizeof(viol_buf));

    int ri = 0;
    const char *s;
    s = slot_buf; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = slot_status_name(info.status); while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = info.label; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = pri_buf; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = mid_buf; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = size_buf; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = cksum_hex; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = cyc_buf; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = viol_buf; while (*s && ri < 250) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_CHECK|slot_id|addr */
void cmd_slot_check(const char *slot_str, const char *addr_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    (void)slot_id;
    uintptr_t addr = (uintptr_t)parse_u64(addr_str);
    uint64_t pte = vos3_ai_model_check_pte(addr);
    if (pte == 0) {
        send_err(14, "EFAULT: unmapped address");
        return;
    }

    const char *status_str = "unknown";
    if (pte & VOS3_PTE_IS_INVERTED) {
        status_str = "inverted";
    } else if (pte & VOS3_PTE_PRESENT) {
        if (pte & VOS3_PTE_AI_PROTECTED) {
            status_str = "protected";
        } else {
            status_str = "mapped";
        }
    } else {
        status_str = "not_present";
    }

    char hex_buf[20];
    u64_to_hex(pte, hex_buf, sizeof(hex_buf));

    char resp[60];
    int ri = 0;
    const char *s;
    s = hex_buf; while (*s && ri < 58) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = status_str; while (*s && ri < 58) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_SNAPSHOT|slot_id */
void cmd_slot_snapshot(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_model_snapshot(slot_id);
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    char resp[40];
    char slot_buf[4];
    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));
    int ri = 0;
    const char *s;
    s = slot_buf; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "warm"; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    /* cldemote flag */
    resp[ri++] = 'c'; resp[ri++] = 'l'; resp[ri++] = 'd';
    resp[ri++] = 'e'; resp[ri++] = 'm'; resp[ri++] = 'o';
    resp[ri++] = 't'; resp[ri++] = 'e'; resp[ri++] = '=';
    resp[ri++] = vos3_ai_has_cldemote() ? '1' : '0';
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_TIMER|slot_id|ms */
void cmd_slot_timer(const char *slot_str, const char *ms_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint64_t ms = parse_u64(ms_str);

    int rc = vos3_ai_set_timer(slot_id, ms);
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    char resp[40];
    char slot_buf[4], ms_buf[20];
    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));
    uint_to_str(ms, ms_buf, sizeof(ms_buf));
    int ri = 0;
    const char *s;
    s = slot_buf; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = ms_buf; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_RESET|slot_id */
void cmd_slot_reset(const char *slot_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    /* Prefetch: Notify prediction engine of slot reset */
    {
        extern void vos3_prefetch_on_slot_reset(uint8_t slot_id);
        vos3_prefetch_on_slot_reset(slot_id);
    }

    /* Phase 6.4.1-U: Reset token chain MAC on slot reset */
    vos3_vbus_reset_token_chain(slot_id);

    int rc = vos3_ai_slot_reset(slot_id);
    if (rc == -1) {
        send_err(1, "EPERM");
        return;
    }
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    char resp[30];
    char slot_buf[4];
    uint_to_str(slot_id, slot_buf, sizeof(slot_buf));
    int ri = 0;
    const char *s;
    s = slot_buf; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "free"; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_SWAP|slot_a|slot_b */
void cmd_slot_swap(const char *a_str, const char *b_str)
{
    uint8_t slot_a = (uint8_t)parse_u64(a_str);
    uint8_t slot_b = (uint8_t)parse_u64(b_str);

    int rc = vos3_ai_slot_swap(slot_a, slot_b);
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    char resp[30];
    char a_buf[4], b_buf[4];
    uint_to_str(slot_a, a_buf, sizeof(a_buf));
    uint_to_str(slot_b, b_buf, sizeof(b_buf));
    int ri = 0;
    const char *s;
    s = a_buf; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = b_buf; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "swapped"; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* VDEV_READ|slot_id|offset|len */
void cmd_vdev_read(const char *slot_str, const char *off_str,
                   const char *len_str)
{
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    size_t offset = (size_t)parse_u64(off_str);
    size_t len = (size_t)parse_u64(len_str);

    if (len > 4096) {
        len = 4096;
    }

    static uint8_t vdev_buf[4096];
    int rc = vos3_ai_vdev_read(slot_id, vdev_buf, offset, len);
    if (rc == -515) {
        send_err(515, "EALIGN: offset must be 64-byte aligned");
        return;
    }
    if (rc != 0) {
        send_err(22, "EINVAL");
        return;
    }

    static const char hex[] = "0123456789abcdef";
    static char hex_resp[8200];
    size_t pos = 0;
    for (size_t i = 0; i < len && pos < 8192; i++) {
        hex_resp[pos++] = hex[(vdev_buf[i] >> 4) & 0xF];
        hex_resp[pos++] = hex[vdev_buf[i] & 0xF];
    }
    hex_resp[pos] = '\0';
    send_ok(hex_resp);
}

/* ============================================================================
 * PHASE 4.2.7: AGENTIC FABRIC BRIDGE COMMANDS
 * ============================================================================ */

/* ISC_SEND|from|to|context_id|hex_data */
void cmd_isc_send(const char *from_str, const char *to_str,
                  const char *ctx_str, const char *hex_data)
{
    if (!from_str || !to_str) { send_err(22, "missing args"); return; }
    uint8_t from_slot = (uint8_t)parse_u64(from_str);
    uint8_t to_slot   = (uint8_t)parse_u64(to_str);

    uint32_t context_id = 0;
    const char *actual_hex = ctx_str;

    if (hex_data != NULL && hex_data[0] != '\0') {
        context_id = (uint32_t)parse_u64(ctx_str);
        actual_hex = hex_data;
    }

    if (!actual_hex || !actual_hex[0]) { send_err(22, "missing hex data"); return; }

    static uint8_t isc_buf[2048];
    size_t dlen = 0;
    if (hex_decode(actual_hex, isc_buf, sizeof(isc_buf), &dlen) != 0) {
        send_err(22, "bad hex"); return;
    }

    int rc = vos3_ai_isc_send(from_slot, to_slot, context_id,
                               isc_buf, (uint16_t)dlen);
    if (rc == -1) { send_err(1, "EPERM"); return; }
    if (rc == -28) { send_err(28, "ENOSPC"); return; }
    if (rc != 0) { send_err(22, "EINVAL"); return; }

    char nb[20]; uint_to_str((uint64_t)dlen, nb, 20);
    VOS3_INFO("[BRIDGE] ISC_SEND: from=%u to=%u ctx=%u dlen=%zu rc=%d",
              from_slot, to_slot, context_id, dlen, rc);
    send_ok(nb);
}

/* ISC_RECV|slot_id */
void cmd_isc_recv(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    static uint8_t isc_buf[2048];
    uint16_t out_len = 0;
    uint32_t context_id = 0;
    int rc = vos3_ai_isc_recv(slot_id, isc_buf, sizeof(isc_buf),
                               &out_len, &context_id);
    if (rc == -11) { send_err(11, "EAGAIN"); return; }
    if (rc != 0) { send_err(22, "EINVAL"); return; }

    static char hex_out[4097];
    hex_encode(isc_buf, (size_t)out_len, hex_out);
    static char resp_buf[4200];
    int ri = 0;
    char cb[12];
    uint_to_str((uint64_t)context_id, cb, sizeof(cb));
    const char *s = cb;
    while (*s && ri < 4198) resp_buf[ri++] = *s++;
    resp_buf[ri++] = '|';
    s = hex_out;
    while (*s && ri < 4198) resp_buf[ri++] = *s++;
    resp_buf[ri] = '\0';
    VOS3_INFO("[BRIDGE] ISC_RECV: slot=%u ctx=%u rc=%d out_len=%u",
              slot_id, context_id, rc, out_len);
    send_ok(resp_buf);
}

/* SLOT_DORMANT|slot_id */
void cmd_slot_dormant(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_model_slot_dormant(slot_id);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[30]; int ri = 0; char sb[4]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    s = sb; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "dormant"; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_WAKE|slot_id */
void cmd_slot_wake(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_model_slot_wake(slot_id);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[30]; int ri = 0; char sb[4]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    s = sb; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "active"; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_QUOTA|slot_id|max_cycles */
void cmd_slot_quota(const char *slot_str, const char *cycles_str)
{
    if (!slot_str || !cycles_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint64_t max_cycles = parse_u64(cycles_str);
    int rc = vos3_ai_slot_set_quota(slot_id, max_cycles);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[40]; int ri = 0; char sb[4], cb[20]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    uint_to_str(max_cycles, cb, sizeof(cb));
    s = sb; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = cb; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_CAPS|slot_id|capabilities|agent_type */
void cmd_slot_caps(const char *slot_str, const char *caps_str, const char *type_str)
{
    if (!slot_str || !caps_str || !type_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint64_t caps   = parse_u64(caps_str);
    uint8_t atype   = (uint8_t)parse_u64(type_str);
    int rc = vos3_ai_slot_set_caps(slot_id, caps, atype);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[40]; int ri = 0; char sb[4], cb[20], tb[4]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    uint_to_str(caps, cb, sizeof(cb));
    uint_to_str(atype, tb, sizeof(tb));
    s = sb; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = cb; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = tb; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_AFFINITY|slot_id|cpu_id */
void cmd_slot_affinity(const char *slot_str, const char *cpu_str)
{
    if (!slot_str || !cpu_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int8_t cpu_id = -1;
    if (cpu_str[0] == '-') {
        cpu_id = -1;
    } else {
        cpu_id = (int8_t)parse_u64(cpu_str);
    }
    int rc = vos3_ai_set_affinity(slot_id, cpu_id);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[30]; int ri = 0; char sb[4]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    s = sb; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = cpu_str; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_CHECKPOINT|slot_id */
void cmd_slot_checkpoint(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_checkpoint(slot_id);
    if (rc != 0) { send_err(22, "EINVAL"); return; }

    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    char resp[120]; int ri = 0;
    char sb[4], hash_hex[20], crc_hex[20], ep_buf[20]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    u64_to_hex(info.checkpoint_hash, hash_hex, sizeof(hash_hex));
    u64_to_hex((uint64_t)info.checkpoint_crc32c, crc_hex, sizeof(crc_hex));
    uint_to_str(info.model_epoch, ep_buf, sizeof(ep_buf));
    s = sb; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = hash_hex; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = crc_hex; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = ep_buf; while (*s && ri < 118) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_ROLLBACK|slot_id */
void cmd_slot_rollback(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_rollback(slot_id);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[30]; int ri = 0; char sb[4]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    s = sb; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = "restored"; while (*s && ri < 28) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_IO_MASK|slot_id|hex_mask */
void cmd_slot_io_mask(const char *slot_str, const char *mask_str)
{
    if (!slot_str || !mask_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint64_t mask   = parse_u64(mask_str);
    int rc = vos3_ai_slot_set_io_mask(slot_id, mask);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    char resp[40]; int ri = 0; char sb[4], mh[20]; const char *s;
    uint_to_str(slot_id, sb, sizeof(sb));
    u64_to_hex(mask, mh, sizeof(mh));
    s = sb; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri++] = '|';
    s = mh; while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_ENUMERATE */
void cmd_slot_enumerate(void)
{
    static char resp[4096];
    int ri = 0;

    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (i > 0 && ri < 4088) resp[ri++] = '|';

        vos3_ai_model_slot_t info;
        vos3_ai_model_slot_get_info(i, &info);

        char nb[20]; const char *s;

        /* slot_id */
        uint_to_str(i, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* status */
        s = slot_status_name(info.status);
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* label */
        s = info.label;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* agent_type */
        uint_to_str(info.agent_type, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* caps hex */
        char caps_hex[20];
        u64_to_hex(info.capabilities, caps_hex, sizeof(caps_hex)); s = caps_hex;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* priority */
        uint_to_str(info.priority, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* affinity */
        if (info.affinity_cpu < 0) {
            resp[ri++] = '-'; resp[ri++] = '1';
        } else {
            uint_to_str((uint64_t)info.affinity_cpu, nb, sizeof(nb)); s = nb;
            while (*s && ri < 4088) resp[ri++] = *s++;
        }
        resp[ri++] = ':';

        /* model_id */
        uint_to_str(info.model_id, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* version */
        uint_to_str(info.model_version, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* epoch */
        uint_to_str(info.model_epoch, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* size */
        uint_to_str(info.size, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* cycles */
        uint_to_str(info.cycle_count, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* violations */
        uint_to_str(info.access_violations, nb, sizeof(nb)); s = nb;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* io_mask hex */
        char mask_hex[20];
        u64_to_hex(info.io_perm_mask, mask_hex, sizeof(mask_hex)); s = mask_hex;
        while (*s && ri < 4088) resp[ri++] = *s++;
        resp[ri++] = ':';

        /* has_checkpoint */
        resp[ri++] = info.has_checkpoint ? '1' : '0';
    }
    resp[ri] = '\0';
    send_ok(resp);
}

/* ============================================================================
 * PHASE 4.2.7+: CONTEXT PERSISTENCE & EVENT PUB/SUB COMMANDS
 * ============================================================================ */

/* SLOT_CONTEXT_CONFIG|slot_id|page_count */
void cmd_slot_context_config(const char *slot_str, const char *count_str)
{
    if (!slot_str || !count_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t page_count = (uint32_t)parse_u64(count_str);
    int rc = vos3_ai_slot_context_config(slot_id, page_count);
    if (rc != 0) { send_err(-rc, "context_config failed"); return; }
    char resp[40]; int ri = 0; char nb[12]; const char *s;
    s = "configured|"; while (*s && ri < 38) resp[ri++] = *s++;
    uint_to_str(page_count, nb, sizeof(nb)); s = nb;
    while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_WARM_RESET|slot_id */
void cmd_slot_warm_reset(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_slot_warm_reset(slot_id);
    if (rc != 0) { send_err(-rc, "warm_reset failed"); return; }
    send_ok("warm_reset");
}

/* EVENT_SUBSCRIBE|event_type|slot_id */
void cmd_event_subscribe(const char *event_str, const char *slot_str)
{
    if (!event_str || !slot_str) { send_err(22, "missing args"); return; }
    uint8_t event_type = (uint8_t)parse_u64(event_str);
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_event_subscribe(event_type, slot_id);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    send_ok("subscribed");
}

/* EVENT_UNSUBSCRIBE|event_type */
void cmd_event_unsubscribe(const char *event_str)
{
    if (!event_str) { send_err(22, "missing args"); return; }
    uint8_t event_type = (uint8_t)parse_u64(event_str);
    int rc = vos3_ai_event_unsubscribe(event_type);
    if (rc != 0) { send_err(22, "EINVAL"); return; }
    send_ok("unsubscribed");
}

/* SLOT_SHARED_MAP|target_slot|source_phys_hex */
void cmd_slot_shared_map(const char *slot_str, const char *phys_str)
{
    if (!slot_str || !phys_str) { send_err(22, "missing args"); return; }
    uint8_t target_slot = (uint8_t)parse_u64(slot_str);
    uintptr_t source_phys = (uintptr_t)parse_u64(phys_str);
    int rc = vos3_ai_map_shared_hugepage(target_slot, source_phys);
    if (rc != 0) { send_err(-rc, "shared_map failed"); return; }
    char resp[40]; int ri = 0; char sb[4]; const char *s;
    s = "mapped|"; while (*s && ri < 38) resp[ri++] = *s++;
    uint_to_str(target_slot, sb, sizeof(sb)); s = sb;
    while (*s && ri < 38) resp[ri++] = *s++;
    resp[ri] = '\0';
    send_ok(resp);
}

/* SLOT_BARRIER_WAIT|slot_id|mask */
void cmd_slot_barrier_wait(const char *slot_str, const char *mask_str)
{
    if (!slot_str || !mask_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint8_t mask = (uint8_t)parse_u64(mask_str);
    int rc = vos3_ai_slot_barrier_wait(slot_id, mask);
    if (rc != 0) { send_err(-rc, "barrier_wait failed"); return; }
    send_ok("barrier_set");
}

/* ---- Phase 4.2.9: Autonomous Agentic Commands ---- */

void cmd_yield_ex(const char *slot_str, const char *timeout_str, const char *mask_str)
{
    if (!slot_str || !timeout_str || !mask_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint64_t timeout_ms = parse_u64(timeout_str);
    uint64_t event_mask = parse_u64(mask_str);
    int rc = vos3_ai_yield_ex(slot_id, timeout_ms, event_mask);
    if (rc != 0) { send_err(-rc, "yield_ex failed"); return; }
    send_ok("yielded");
}

void cmd_commit_slot(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_consensus_commit(slot_id);
    if (rc != 0) { send_err(-rc, "commit failed"); return; }
    __asm__ volatile("mfence" ::: "memory");
    send_ok("committed");
}

void cmd_slot_gate(const char *slot_str, const char *enable_str)
{
    if (!slot_str || !enable_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint8_t enable = (uint8_t)parse_u64(enable_str);
    int rc = vos3_ai_slot_set_consensus_gate(slot_id, enable);
    if (rc != 0) { send_err(-rc, "gate failed"); return; }
    send_ok(enable ? "gate_on" : "gate_off");
}

void cmd_context_share(const char *src_str, const char *dst_str)
{
    if (!src_str || !dst_str) { send_err(22, "missing args"); return; }
    uint8_t src_slot = (uint8_t)parse_u64(src_str);
    uint8_t dst_slot = (uint8_t)parse_u64(dst_str);
    int rc = vos3_ai_context_share(src_slot, dst_slot);
    if (rc != 0) { send_err(-rc, "context_share failed"); return; }
    send_ok("shared");
}

/* ============================================================================
 * PHASE 4.9b: WARP DRIVE COMMANDS
 * ============================================================================ */

/* WARP_POST <slot_id> <zone_offset> <length> */
void cmd_warp_post(const char *s_slot, const char *s_off, const char *s_len)
{
    if (!s_slot || !s_off || !s_len) { send_err(22, "usage: WARP_POST slot off len"); return; }
    uint8_t  sid = (uint8_t)parse_u64(s_slot);
    uint64_t off = parse_u64(s_off);
    uint64_t len = parse_u64(s_len);
    if (sid >= 4 || len == 0 || len > 65535) { send_err(22, "bad params"); return; }
    if (!vos3_ivshmem_available()) { send_err(19, "no warp drive"); return; }

    size_t slot_off = g_sq_write_off[sid & 3];
    static uint64_t warp_tag = 0;

    int rc = vos3_sq_post_warp(sid, VOS3_SQ_CMD_WARP_DATA,
                                (uint16_t)(len & 0xFFFF),
                                (uint64_t)slot_off, warp_tag, off);
    if (rc != 0) {
        vos3_sq_poll();
        rc = vos3_sq_post_warp(sid, VOS3_SQ_CMD_WARP_DATA,
                                (uint16_t)(len & 0xFFFF),
                                (uint64_t)slot_off, warp_tag, off);
    }
    if (rc != 0) { send_err(12, "SQ full"); return; }

    warp_tag++;
    g_sq_write_off[sid & 3] += len;
    send_ok("WARP_OK");
}

void cmd_warp_status(void)
{
    if (!vos3_ivshmem_available()) { send_ok("WARP_OFF"); return; }
    char buf[64];
    size_t sz = vos3_ivshmem_size();
    int pos = 0;
    const char *prefix = "WARP_ON ";
    while (prefix[pos]) { buf[pos] = prefix[pos]; pos++; }
    char nbuf[20];
    uint_to_str(sz, nbuf, sizeof(nbuf));
    int ni = 0;
    while (nbuf[ni] && pos < 62) { buf[pos++] = nbuf[ni++]; }
    buf[pos] = '\0';
    send_ok(buf);
}

/* HP_STATS */
void cmd_hp_stats(void)
{
    uint32_t total = 0, used = 0;
    vos3_pmm_hugepage_stats(&total, &used);
    char buf[48];
    char t_buf[12], u_buf[12];
    uint_to_str(total, t_buf, sizeof(t_buf));
    uint_to_str(used, u_buf, sizeof(u_buf));
    int pos = 0, i;
    for (i = 0; t_buf[i] && pos < 44; i++) buf[pos++] = t_buf[i];
    buf[pos++] = '|';
    for (i = 0; u_buf[i] && pos < 46; i++) buf[pos++] = u_buf[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * PHASE 5: CONTEXT MANAGEMENT (Freeze/Thaw) — April 2026 Hardened
 * ============================================================================ */

static uint32_t popcount64(uint64_t v)
{
    uint32_t c = 0;
    while (v) { c++; v &= v - 1; }
    return c;
}

/* CTX_FREEZE|slot_id */
void cmd_ctx_freeze(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing slot"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    uint64_t freeze_id = 0;
    int rc = vos3_ai_ctx_freeze(slot_id, &freeze_id);
    if (rc != 0) {
        send_err(-rc, "CTX_FREEZE failed");
        return;
    }

    /* Read slot metadata for response */
    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    uint32_t dirty_count = popcount64(info.ctx_dirty_bitmap[0])
                         + popcount64(info.ctx_dirty_bitmap[1]);

    /* Format: freeze_id|page_count|dirty_count|bitmap_hex|hash|crc|epoch|offset|session_epoch */
    char resp[256];
    int pos = 0;
    char nbuf[24];
    const char *s;

    /* freeze_id */
    u64_to_hex(freeze_id, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* page_count */
    uint_to_str(info.context_page_count, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* dirty_count */
    uint_to_str(dirty_count, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* dirty_bitmap as hex (bitmap[1]:bitmap[0], 32 hex chars) */
    u64_to_hex(info.ctx_dirty_bitmap[1], nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    u64_to_hex(info.ctx_dirty_bitmap[0], nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* hash (checkpoint_hash) */
    u64_to_hex(info.checkpoint_hash, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* crc (checkpoint_crc32c) */
    u64_to_hex(info.checkpoint_crc32c, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* epoch (checkpoint_epoch) */
    uint_to_str(info.checkpoint_epoch, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* offset */
    uint_to_str((uint64_t)info.checkpoint_offset, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    /* session_epoch */
    uint_to_str(info.session_epoch, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 250; ) resp[pos++] = *s++;

    resp[pos] = '\0';
    send_ok(resp);
}

/* CTX_SCRUB|slot_id */
void cmd_ctx_scrub(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing slot"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    uint32_t pages_cleaned = 0, effective_bytes = 0;
    int rc = vos3_ai_ctx_scrub(slot_id, &pages_cleaned, &effective_bytes);
    if (rc != 0) {
        send_err(-rc, "CTX_SCRUB failed");
        return;
    }

    /* Format: scrubbed|pages_cleaned|effective_bytes */
    char resp[64];
    int pos = 0;
    const char *s;
    char nbuf[12];

    s = "scrubbed"; while (*s && pos < 58) resp[pos++] = *s++;
    resp[pos++] = '|';

    uint_to_str(pages_cleaned, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 58; ) resp[pos++] = *s++;
    resp[pos++] = '|';

    uint_to_str(effective_bytes, nbuf, sizeof(nbuf));
    for (s = nbuf; *s && pos < 62; ) resp[pos++] = *s++;

    resp[pos] = '\0';
    send_ok(resp);
}

/* CTX_READ|slot_id|page_idx */
void cmd_ctx_read(const char *slot_str, const char *page_str)
{
    if (!slot_str || !page_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t page_idx = (uint32_t)parse_u64(page_str);

    static uint8_t page_buf[4096];
    int rc = vos3_ai_ctx_read_page(slot_id, page_idx, page_buf);
    if (rc == -61) {
        send_err(61, "ENODATA: page not dirty");
        return;
    }
    if (rc != 0) {
        send_err(-rc, "CTX_READ failed");
        return;
    }

    /* Hex-encode 4KB → 8192 hex chars */
    static const char hex[] = "0123456789abcdef";
    static char hex_resp[8200];
    size_t pos = 0;
    for (size_t i = 0; i < 4096; i++) {
        hex_resp[pos++] = hex[(page_buf[i] >> 4) & 0xF];
        hex_resp[pos++] = hex[page_buf[i] & 0xF];
    }
    hex_resp[pos] = '\0';
    send_ok(hex_resp);
}

/* CTX_THAW|slot_id|page_count|freeze_id|session_epoch */
void cmd_ctx_thaw(const char *slot_str, const char *count_str,
                  const char *fid_str, const char *epoch_str)
{
    if (!slot_str || !count_str || !fid_str || !epoch_str) {
        send_err(22, "missing args"); return;
    }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t page_count = (uint32_t)parse_u64(count_str);
    uint64_t freeze_id = parse_u64(fid_str);
    uint32_t session_epoch = (uint32_t)parse_u64(epoch_str);

    int rc = vos3_ai_ctx_thaw(slot_id, page_count, freeze_id, session_epoch, 0);
    if (rc != 0) {
        send_err(-rc, "CTX_THAW failed");
        return;
    }
    send_ok("ready");
}

/* CTX_THAW_COLD|slot_id|page_count|freeze_id|session_epoch */
void cmd_ctx_thaw_cold(const char *slot_str, const char *count_str,
                       const char *fid_str, const char *epoch_str)
{
    if (!slot_str || !count_str || !fid_str || !epoch_str) {
        send_err(22, "missing args"); return;
    }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t page_count = (uint32_t)parse_u64(count_str);
    uint64_t freeze_id = parse_u64(fid_str);
    uint32_t session_epoch = (uint32_t)parse_u64(epoch_str);

    int rc = vos3_ai_ctx_thaw(slot_id, page_count, freeze_id, session_epoch, 1);
    if (rc != 0) {
        send_err(-rc, "CTX_THAW_COLD failed");
        return;
    }
    send_ok("ready");
}

/* CTX_WRITE|slot_id|page_idx|hex_data */
void cmd_ctx_write(const char *slot_str, const char *page_str,
                   const char *hex_data)
{
    if (!slot_str || !page_str || !hex_data) {
        send_err(22, "missing args"); return;
    }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t page_idx = (uint32_t)parse_u64(page_str);

    static uint8_t page_buf[4096];
    size_t decoded_len = 0;
    int drc = hex_decode(hex_data, page_buf, 4096, &decoded_len);
    if (drc != 0) {
        send_err(22, "hex decode error");
        return;
    }

    int rc = vos3_ai_ctx_write_page(slot_id, page_idx, page_buf, decoded_len);
    if (rc != 0) {
        send_err(-rc, "CTX_WRITE failed");
        return;
    }
    send_ok("written");
}

/* CTX_THAW_DONE|slot_id|expected_crc */
void cmd_ctx_thaw_done(const char *slot_str, const char *crc_str)
{
    if (!slot_str || !crc_str) { send_err(22, "missing args"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t expected_crc = (uint32_t)parse_u64(crc_str);

    int rc = vos3_ai_ctx_thaw_done(slot_id, expected_crc);
    if (rc == -5) {
        send_err(5, "thaw_commit_failed");
        return;
    }
    if (rc != 0) {
        send_err(-rc, "CTX_THAW_DONE failed");
        return;
    }
    send_ok("active");
}

/* CTX_LATENCY|slot_id */
void cmd_ctx_latency(const char *slot_str)
{
    if (!slot_str) { send_err(22, "missing slot"); return; }
    uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    if (slot_id >= VOS3_MODEL_SLOT_MAX) { send_err(22, "EINVAL"); return; }

    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    char nbuf[24];
    uint_to_str(info.ctx_access_latency, nbuf, sizeof(nbuf));
    send_ok(nbuf);
}

/* ============================================================================
 * BATCH F: INFERENCE HINT (Inference-Aware Scheduling)
 * ============================================================================ */

/* INFERENCE_HINT|slot_id|state */
void cmd_inference_hint(const char *slot_str, const char *state_str)
{
    if (!slot_str || !state_str) {
        send_err(22, "EINVAL: missing slot or state");
        return;
    }

    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t state = (uint32_t)parse_u64(state_str);

    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: bad slot");
        return;
    }

    if (state > VOS3_INFERENCE_YIELDING) {
        send_err(22, "EINVAL: bad state");
        return;
    }

    /* Get the AI model slot info to find the owner TID */
    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    if (info.owner_tid == 0) {
        send_err(1, "EPERM: slot has no owner");
        return;
    }

    /* Find the task and set its inference state */
    vos3_task_t *task = vos3_task_get((vos3_tid_t)info.owner_tid);
    if (task == NULL) {
        send_err(2, "ENOENT: owner task not found");
        return;
    }

    uint8_t old_state = task->inference_state;
    task->inference_state = (uint8_t)state;

    /* Phase 6.1: Set/clear AI_INFERENCE flag for scheduler anti-preemption */
    if (state == VOS3_INFERENCE_INFERRING) {
        task->flags |= VOS3_TASK_FLAG_AI_INFERENCE;
        task->inference_grants = 0;
    } else {
        task->flags &= ~VOS3_TASK_FLAG_AI_INFERENCE;
        task->inference_grants = 0;
    }

    if (old_state != (uint8_t)state) {
        VOS3_DEBUG("[KIM] VBus: slot %u tid=%u inference: %u -> %u",
                   slot_id, info.owner_tid, old_state, (uint8_t)state);
    }

    send_ok("inference_state_set");
}

/* ============================================================================
 * PHASE 6: KERNEL INFERENCE MANAGER (KIM) — TOKEN STREAM
 * ============================================================================ */

/**
 * @brief Send a single token via VBUS_TYPE_TOKEN_STREAM frame.
 *
 * Payload format: [u8:slot_id][u32:token_id LE][u16:seq LE][u8:flags]
 *                 [variable:token_text NUL-terminated]
 * Total: 8 + strlen(text) + 1
 *
 * Flags: 0x01 = FIRST_TOKEN, 0x02 = LAST_TOKEN (EOS), 0x04 = ERROR
 *
 * Frame is HMAC-SHA256 signed via vos3_vbus_send_frame().
 */
#define KIM_TOKEN_FLAG_FIRST   0x01U
#define KIM_TOKEN_FLAG_LAST    0x02U
#define KIM_TOKEN_FLAG_ERROR   0x04U
#define KIM_TOKEN_FLAG_BATCHED 0x40U  /* Phase 6.4.1-U: frame contains batched text */
#define KIM_TOKEN_FLAG_AAAK    0x80U  /* Phase 6.4: token text is V-AAAK compressed */

/* Phase 6.4: Minimum text length to attempt V-AAAK compression.
 * Below 4 bytes the 0xFF-escape overhead negates any savings. */
#define KIM_TOKEN_AAAK_MIN_LEN  4U

/* ============================================================================
 * Phase 6.4.1-U: ADAPTIVE AAAK BATCHING
 *
 * Small tokens (< 3 bytes, e.g. punctuation, spaces) are buffered per-slot.
 * Flush triggers: 4 accumulated tokens, ~50ms timeout, LAST/ERROR flag,
 * or next non-small token arrival. Reduces VBus interrupt frequency ~30%
 * during high-speed prose generation.
 *
 * Phase 6.4.2-H: Jitter injection on flush — entropy-seeded pause loop
 * masks AAAK encoder timing from power-analysis side-channels.
 * ============================================================================ */

#define KIM_BATCH_MAX_TOKENS    4U   /* flush after this many small tokens */
#define KIM_BATCH_MAX_TEXT      64U  /* max accumulated text bytes */
#define KIM_BATCH_TIMEOUT_TICKS 5U   /* ~50ms at 100 Hz tick rate (5 × 10ms) */
#define KIM_BATCH_SMALL_THRESH  3U   /* tokens <= this size are "small" */

/* Phase 6.4.2-H: Time-locked sequence — staleness and monotonicity bounds */
#define KIM_TOKEN_TS_STALE_MS   500U /* reject frames older than 500ms */

typedef struct kim_token_batch {
    uint32_t  first_token_id;   /* token_id of first buffered token */
    uint16_t  first_seq;        /* seq of first buffered token */
    uint8_t   count;            /* number of buffered tokens */
    uint8_t   flags;            /* accumulated flags (OR of all tokens) */
    char      text[KIM_BATCH_MAX_TEXT];
    uint32_t  text_len;
    uint64_t  first_tick;       /* tick when first token entered buffer */
} kim_token_batch_t;

static kim_token_batch_t g_token_batch[8]; /* per-slot, zeroed at boot */

/* Forward declaration — the actual send path */
static int kim_send_token_immediate(uint8_t slot_id, uint32_t token_id,
                                     uint16_t seq, uint8_t flags,
                                     const char *text, uint32_t text_len);

/**
 * @brief Flush the token batch buffer for a slot.
 *
 * Sends the accumulated text as a single frame with KIM_TOKEN_FLAG_BATCHED
 * set. Uses the first_token_id/first_seq from the batch.
 *
 * Phase 6.4.2-H: Jitter injection — entropy-seeded pause loop (0-500µs)
 * before VBus commit masks AAAK encoder timing from power-analysis attacks.
 * Entropy source: vos3_entropy_get_u32() (RDRAND primary, RDSEED fallback).
 *
 * @return 0 on success, <0 on error, 1 if buffer was empty (nothing to flush)
 */
static int kim_flush_batch(uint8_t slot_id)
{
    /* IDOR fix (2026-04): bound against the real slot array size
     * (VOS3_MODEL_SLOT_MAX = 4). Previously this guard was a hardcoded `>= 8`
     * while g_model_slots[] is 4 entries, so slot_id=4..7 passed the check and
     * indexed past the array end, corrupting adjacent BSS. OOB write via VBus.
     */
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -1;
    kim_token_batch_t *b = &g_token_batch[slot_id];
    if (b->count == 0)
        return 1; /* nothing to flush */

    /* Phase 6.4.2-H: Side-channel jitter injection.
     * Random pause (0-500µs) masks the deterministic timing signature of
     * AAAK compression + batch accumulation. Each `pause` instruction is
     * ~10-40 cycles (~5-20ns at 2GHz). 500µs ≈ 2000 pauses at ~250ns avg.
     * Factor of 4 loop iterations per µs gives adequate coverage. */
    {
        uint32_t jitter_us = vos3_entropy_get_u32() % 500U;
        volatile uint32_t j;
        for (j = 0; j < jitter_us * 4U; j++)
            __asm__ volatile("pause");
    }

    uint8_t flush_flags = b->flags | KIM_TOKEN_FLAG_BATCHED;
    int rc = kim_send_token_immediate(slot_id, b->first_token_id,
                                       b->first_seq, flush_flags,
                                       b->text, b->text_len);
    /* Reset batch state */
    b->count    = 0;
    b->text_len = 0;
    b->flags    = 0;
    b->first_tick = 0;
    return rc;
}

int vos3_kim_send_token(uint8_t slot_id, uint32_t token_id,
                                uint16_t seq, uint8_t flags,
                                const char *text, uint32_t text_len)
{
    /* IDOR fix (2026-04): bound against the real slot array size
     * (VOS3_MODEL_SLOT_MAX = 4). Previously this guard was a hardcoded `>= 8`
     * while g_model_slots[] is 4 entries, so slot_id=4..7 passed the check and
     * indexed past the array end, corrupting adjacent BSS. OOB write via VBus.
     */
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -1;
    if (text_len > 255U) text_len = 255U;

    kim_token_batch_t *b = &g_token_batch[slot_id];
    uint64_t now = vos3_sched_get_ticks();

    /* Check for batch timeout flush (existing buffered tokens expired) */
    if (b->count > 0 && b->first_tick > 0 &&
        (now - b->first_tick) >= KIM_BATCH_TIMEOUT_TICKS) {
        kim_flush_batch(slot_id);
    }

    /* Determine if this token is "small" and eligible for batching.
     * LAST, ERROR, or FIRST tokens always flush + send immediately. */
    int is_small = (text_len <= KIM_BATCH_SMALL_THRESH) &&
                   !(flags & (KIM_TOKEN_FLAG_LAST | KIM_TOKEN_FLAG_ERROR |
                              KIM_TOKEN_FLAG_FIRST));

    if (is_small) {
        /* Buffer this small token */
        if (b->count == 0) {
            b->first_token_id = token_id;
            b->first_seq      = seq;
            b->first_tick     = now;
        }
        /* Append text to batch buffer (bounds check) */
        uint32_t avail = KIM_BATCH_MAX_TEXT - b->text_len;
        uint32_t copy_len = (text_len < avail) ? text_len : avail;
        for (uint32_t i = 0; i < copy_len; i++)
            b->text[b->text_len + i] = text[i];
        b->text_len += copy_len;
        b->flags    |= flags;
        b->count++;

        /* Flush if batch is full */
        if (b->count >= KIM_BATCH_MAX_TOKENS)
            return kim_flush_batch(slot_id);

        return 0; /* Buffered — no VBus interrupt yet */
    }

    /* Non-small token: flush any pending batch first, then send immediately */
    if (b->count > 0)
        kim_flush_batch(slot_id);

    return kim_send_token_immediate(slot_id, token_id, seq, flags,
                                     text, text_len);
}

/**
 * @brief Build and transmit a single TOKEN_STREAM frame.
 *
 * Phase 6.4: V-AAAK selective compression.
 * Phase 6.4.1-U: Chained-HMAC provenance.
 * Phase 6.4.2-H: Time-locked timestamp + clflushopt cache-purity.
 *
 * Payload format (v6.4.2-H):
 *   [u8:slot][u32:token_id LE][u16:seq LE][u8:flags][u32:timestamp_ms LE]
 *   [variable:text NUL-terminated][u8:chain_mac[32]]
 * Total: 12 + text_len + 1 + 32 = 45 + text_len
 *
 * The 32-bit timestamp (ms since boot) enables the backend to reject frames
 * older than 500ms (replay) or out of monotonic order (injection).
 * The chained HMAC now covers the timestamp, binding it cryptographically.
 */

/* Phase 6.6: Forward declaration — defined below kim_send_token_immediate */
void vos3_vbus_purity_flush(const void *buf, uint32_t len);

static int kim_send_token_immediate(uint8_t slot_id, uint32_t token_id,
                                     uint16_t seq, uint8_t flags,
                                     const char *text, uint32_t text_len)
{
    /* Max payload: 12 header + 512 compressed + 1 NUL + 32 chain MAC = 557 */
    uint8_t buf[564];
    if (text_len > 255U) text_len = 255U;

    /* Phase 6.4.2-H: Capture timestamp BEFORE any compression work.
     * This binds the time-lock to the moment of token generation,
     * not the post-compression commit. */
    uint32_t ts_ms = (uint32_t)vos3_sched_get_uptime_ms();

    buf[0] = slot_id;
    buf[1] = (uint8_t)(token_id & 0xFF);
    buf[2] = (uint8_t)((token_id >> 8) & 0xFF);
    buf[3] = (uint8_t)((token_id >> 16) & 0xFF);
    buf[4] = (uint8_t)((token_id >> 24) & 0xFF);
    buf[5] = (uint8_t)(seq & 0xFF);
    buf[6] = (uint8_t)((seq >> 8) & 0xFF);
    buf[7] = 0; /* flags written after compression decision */
    /* Phase 6.4.2-H: 32-bit LE timestamp at offset 8-11 */
    buf[8]  = (uint8_t)(ts_ms & 0xFF);
    buf[9]  = (uint8_t)((ts_ms >> 8) & 0xFF);
    buf[10] = (uint8_t)((ts_ms >> 16) & 0xFF);
    buf[11] = (uint8_t)((ts_ms >> 24) & 0xFF);

    /* Text region now starts at offset 12 (was 8 before timestamp) */
    uint32_t payload_len = text_len;

    /* Phase 6.4: V-AAAK selective compression for token text.
     * Only compress when AAAK native mode is on and text is long enough.
     * If compression doesn't shrink the output, fall back to raw text. */
    if (g_vbus_aaak_native && text_len >= KIM_TOKEN_AAAK_MIN_LEN) {
        uint8_t comp[512]; /* max compressed output */
        uint32_t comp_len = vos3_v_aaak_encode(
            (const uint8_t *)text, text_len, comp, sizeof(comp));

        /* Phase 6.4.2-H: Selective cache flush (replaces lfence).
         * clflushopt evicts cache lines containing the raw (pre-compression)
         * token text, preventing data shadows from persisting in L1/L2/L3.
         * More targeted than a full pipeline stall (lfence): flushes only
         * the specific cache lines touched by the plaintext pointer.
         * sfence after flush ensures completion before TX commit. */
        {
            extern int g_cpu_has_clflushopt; /* ai_pte.c */
            const uint8_t *flush_ptr = (const uint8_t *)text;
            uint32_t flush_end = text_len;
            for (uint32_t off = 0; off < flush_end; off += 64U) {
                uintptr_t addr = (uintptr_t)(flush_ptr + off);
                if (g_cpu_has_clflushopt)
                    __asm__ volatile("clflushopt (%0)" :: "r"(addr) : "memory");
                else
                    __asm__ volatile("clflush (%0)" :: "r"(addr) : "memory");
            }
            __asm__ volatile("sfence" ::: "memory");
        }

        if (comp_len > 0 && comp_len < text_len) {
            /* Compression saved bytes — use it */
            flags |= KIM_TOKEN_FLAG_AAAK;
            for (uint32_t i = 0; i < comp_len; i++)
                buf[12 + i] = comp[i];
            buf[12 + comp_len] = 0; /* NUL terminator */
            payload_len = comp_len;
        } else {
            /* Compression didn't help — send raw */
            for (uint32_t i = 0; i < text_len; i++)
                buf[12 + i] = (uint8_t)text[i];
            buf[12 + text_len] = 0;
        }
    } else {
        for (uint32_t i = 0; i < text_len; i++)
            buf[12 + i] = (uint8_t)text[i];
        buf[12 + text_len] = 0;
    }

    buf[7] = flags;
    uint32_t core_len = 12U + payload_len + 1U;

    /* Phase 6.4.1-U → 6.4.2-H: Chained-HMAC provenance.
     * chain_mac[n] = HMAC(session_key, (payload+timestamp) || chain_mac[n-1]).
     * The timestamp is now inside the HMAC input, so replayed frames
     * with altered timestamps break the chain. */
    uint8_t chain_mac[32];
    vos3_vbus_chain_token_mac(slot_id, buf, core_len, chain_mac);
    for (int i = 0; i < 32; i++)
        buf[core_len + (uint32_t)i] = chain_mac[i];
    uint32_t total = core_len + 32U;

    /* Phase 7: Increment per-slot HMAC sequence counter for token frames */
    if (slot_id < 8U) {
        extern uint64_t g_vbus_hmac_seq[8]; /* defined in vbus_transport.c */
        g_vbus_hmac_seq[slot_id]++;
    }

    /* Use trylock to avoid blocking inference loop on TX contention */
    int send_rc = vos3_vbus_send_frame_trylock(VBUS_TYPE_TOKEN_STREAM,
                                                slot_id, 0 /* tag=0 async */,
                                                buf, total);

    /* Phase 6.6: Purity flush — scrub TX buffer from cache after send.
     * Eliminates shadow traces of raw token text in L1/L2/L3. */
    vos3_vbus_purity_flush(buf, total);

    return send_rc;
}

/* ============================================================================
 * Phase 6.6: Shadow Trace Mitigation — vos3_vbus_purity_flush()
 * ============================================================================
 * After every token transmission, flush the TX buffer cache lines using
 * clflushopt (or clflush on older CPUs) followed by sfence.  This prevents
 * speculative "shadow traces" of raw token text from remaining in L1/L2/L3
 * after the data has been sent to the backend.
 *
 * Call this after any sensitive TX operation to eliminate residual cache state.
 * ============================================================================ */

void vos3_vbus_purity_flush(const void *buf, uint32_t len)
{
    if (!buf || len == 0)
        return;

    extern int g_cpu_has_clflushopt; /* ai_pte.c */
    const uint8_t *ptr = (const uint8_t *)buf;

    /* Flush every 64-byte cache line covering the buffer */
    for (uint32_t off = 0; off < len; off += 64U) {
        uintptr_t addr = (uintptr_t)(ptr + off);
        if (g_cpu_has_clflushopt)
            __asm__ volatile("clflushopt (%0)" :: "r"(addr) : "memory");
        else
            __asm__ volatile("clflush (%0)" :: "r"(addr) : "memory");
    }
    /* sfence ensures all flushes complete before returning */
    __asm__ volatile("sfence" ::: "memory");
}

/* Forward declaration — implemented in ai_kim.c */
extern int vos3_kim_generate(uint8_t slot_id, uint32_t max_tokens,
                              uint32_t temperature_fp);

/* KIM_GENERATE|slot_id|max_tokens|temperature */
void cmd_kim_generate(const char *slot_str, const char *max_str,
                       const char *temp_str)
{
    if (!slot_str || !max_str) {
        send_err(22, "EINVAL: KIM_GENERATE slot|max_tokens[|temp]");
        return;
    }

    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t max_tokens = (uint32_t)parse_u64(max_str);
    uint32_t temp_fp = 100; /* default temperature 1.0 in fixed-point ×100 */

    if (temp_str) {
        temp_fp = (uint32_t)parse_u64(temp_str);
    }

    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: bad slot");
        return;
    }

    if (max_tokens == 0U || max_tokens > 4096U) {
        send_err(22, "EINVAL: max_tokens must be 1-4096");
        return;
    }

    int rc = vos3_kim_generate(slot_id, max_tokens, temp_fp);
    if (rc < 0) {
        send_err(-rc, "KIM_GENERATE failed");
        return;
    }

    /* Format: OK|tokens_generated */
    char nbuf[16];
    uint_to_str((uint64_t)(uint32_t)rc, nbuf, sizeof(nbuf));
    send_ok(nbuf);
}

/* ============================================================================
 * PHASE 7: MULTI-CHANNEL DISPATCH + LAZY-THAW + MODEL LOAD UPDATE
 * ============================================================================ */

/* Phase 7: KIM_GENERATE with dispatch context (multi-channel) */
void cmd_kim_generate_ctx(const vbus_dispatch_ctx_t *ctx,
                          const char *slot_str, const char *max_str,
                          const char *temp_str)
{
    if (!ctx || !slot_str || !max_str) {
        if (ctx)
            send_err_ctx(ctx, 22, "EINVAL: KIM_GENERATE_CTX slot|max_tokens[|temp]");
        else
            send_err(22, "EINVAL: KIM_GENERATE_CTX slot|max_tokens[|temp]");
        return;
    }

    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint32_t max_tokens = (uint32_t)parse_u64(max_str);
    uint32_t temp_fp = 100; /* default temperature 1.0 in fixed-point x100 */

    if (temp_str) {
        temp_fp = (uint32_t)parse_u64(temp_str);
    }

    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        send_err_ctx(ctx, 22, "EINVAL: bad slot");
        return;
    }

    if (max_tokens == 0U || max_tokens > 4096U) {
        send_err_ctx(ctx, 22, "EINVAL: max_tokens must be 1-4096");
        return;
    }

    /* Forward declaration — implemented in ai_kim.c */
    extern int vos3_kim_generate(uint8_t slot_id, uint32_t max_tokens,
                                  uint32_t temperature_fp);

    int rc = vos3_kim_generate(slot_id, max_tokens, temp_fp);
    if (rc < 0) {
        send_err_ctx(ctx, -rc, "KIM_GENERATE_CTX failed");
        return;
    }

    /* Format: OK|tokens_generated */
    char nbuf[16];
    uint_to_str((uint64_t)(uint32_t)rc, nbuf, sizeof(nbuf));
    send_ok_ctx(ctx, nbuf);
}

/* Phase 7: Enable/disable lazy-thaw demand paging */
void cmd_lazy_thaw(const char *slot_str, const char *enable_str)
{
    if (!slot_str || !enable_str) {
        send_err(22, "EINVAL: LAZY_THAW slot|enable");
        return;
    }

    uint8_t slot_id = (uint8_t)parse_u64(slot_str);
    uint8_t enable = (uint8_t)parse_u64(enable_str);

    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: bad slot");
        return;
    }

    int rc;
    if (enable)
        rc = vos3_ai_lazy_thaw_enable(slot_id);
    else
        rc = vos3_ai_lazy_thaw_disable(slot_id);

    if (rc == 0)
        send_ok(enable ? "LAZY_THAW_ON" : "LAZY_THAW_OFF");
    else
        send_err(rc < 0 ? -rc : rc, "lazy_thaw failed");
}

/* Phase 7: Live model weight update (shadow-load + atomic swap) */
void cmd_model_load_update(const char *active_slot_str, const char *shadow_slot_str)
{
    if (!active_slot_str || !shadow_slot_str) {
        send_err(22, "EINVAL: MODEL_LOAD_UPDATE active_slot|shadow_slot");
        return;
    }

    uint8_t active = (uint8_t)parse_u64(active_slot_str);
    uint8_t shadow = (uint8_t)parse_u64(shadow_slot_str);

    if (active >= 8U || shadow >= 8U) {
        send_err(22, "EINVAL: bad slot");
        return;
    }

    if (active == shadow) {
        send_err(22, "EINVAL: active and shadow must differ");
        return;
    }

    /* Perform atomic swap via vos3_ai_slot_swap(active, shadow) */
    int rc = vos3_ai_slot_swap(active, shadow);
    if (rc == 0)
        send_ok("SWAP_OK");
    else
        send_err(-rc, "swap failed");
}

/* ============================================================================
 * Phase 8: V-Palace + V-AAAK Bridge Commands
 * ============================================================================ */

/* LOCK_PREFIX|<slot>|<count> — Lock first N HugePages as immutable prefix */
void cmd_lock_prefix(const char *slot_str, const char *count_str)
{
    uint32_t slot_id = parse_uint(slot_str);
    uint32_t count   = parse_uint(count_str);
    int rc = vos3_ai_slot_lock_prefix(slot_id, count);
    if (rc < 0) {
        send_err(rc, "LOCK_PREFIX failed");
        return;
    }
    send_ok("PREFIX_LOCKED");
}

/* UNLOCK_PREFIX|<slot> — Unlock prefix HugePages */
void cmd_unlock_prefix(const char *slot_str)
{
    uint32_t slot_id = parse_uint(slot_str);
    int rc = vos3_ai_slot_unlock_prefix(slot_id);
    if (rc < 0) {
        send_err(rc, "UNLOCK_PREFIX failed");
        return;
    }
    send_ok("PREFIX_UNLOCKED");
}

/* PALACE_ASSIGN|<slot>|<hp_index>|<hall> — Assign room to hall (0=WEIGHT,1=PERSISTENT,2=TRANSIENT) */
void cmd_palace_assign(const char *slot_str, const char *hp_str, const char *hall_str)
{
    uint32_t slot_id  = parse_uint(slot_str);
    uint32_t hp_index = parse_uint(hp_str);
    uint32_t hall     = parse_uint(hall_str);
    int rc = vos3_ai_palace_assign_room(slot_id, hp_index, (vos3_hall_type_t)hall);
    if (rc < 0) {
        send_err(rc, "PALACE_ASSIGN failed");
        return;
    }
    send_ok("ROOM_ASSIGNED");
}

/* PALACE_QUERY|<slot>|<hp_index> — Query room metadata */
void cmd_palace_query(const char *slot_str, const char *hp_str)
{
    uint32_t slot_id  = parse_uint(slot_str);
    uint32_t hp_index = parse_uint(hp_str);
    vos3_room_meta_t meta;
    int rc = vos3_ai_query_room_id(slot_id, hp_index, &meta);
    if (rc < 0) {
        send_err(rc, "PALACE_QUERY failed");
        return;
    }
    /* Format: hall|locked|access_count|last_tick */
    char buf[64];
    char tmp[12];
    uint_to_str(meta.hall, buf, sizeof(buf));
    bridge_strcpy(buf + bridge_strlen(buf), "|", sizeof(buf) - bridge_strlen(buf));
    uint_to_str(meta.locked, tmp, sizeof(tmp));
    bridge_strcpy(buf + bridge_strlen(buf), tmp, sizeof(buf) - bridge_strlen(buf));
    bridge_strcpy(buf + bridge_strlen(buf), "|", sizeof(buf) - bridge_strlen(buf));
    uint_to_str(meta.access_count, tmp, sizeof(tmp));
    bridge_strcpy(buf + bridge_strlen(buf), tmp, sizeof(buf) - bridge_strlen(buf));
    bridge_strcpy(buf + bridge_strlen(buf), "|", sizeof(buf) - bridge_strlen(buf));
    uint_to_str(meta.last_access_tick, tmp, sizeof(tmp));
    bridge_strcpy(buf + bridge_strlen(buf), tmp, sizeof(buf) - bridge_strlen(buf));
    send_ok(buf);
}

/* PALACE_STATS|<slot> — Query per-hall counts */
void cmd_palace_stats(const char *slot_str)
{
    uint32_t slot_id = parse_uint(slot_str);
    uint32_t wc = 0, pc = 0, tc = 0;
    int rc = vos3_ai_palace_stats(slot_id, &wc, &pc, &tc);
    if (rc < 0) {
        send_err(rc, "PALACE_STATS failed");
        return;
    }
    char buf[48];
    char tmp[12];
    uint_to_str(wc, buf, sizeof(buf));
    bridge_strcpy(buf + bridge_strlen(buf), "|", sizeof(buf) - bridge_strlen(buf));
    uint_to_str(pc, tmp, sizeof(tmp));
    bridge_strcpy(buf + bridge_strlen(buf), tmp, sizeof(buf) - bridge_strlen(buf));
    bridge_strcpy(buf + bridge_strlen(buf), "|", sizeof(buf) - bridge_strlen(buf));
    uint_to_str(tc, tmp, sizeof(tmp));
    bridge_strcpy(buf + bridge_strlen(buf), tmp, sizeof(buf) - bridge_strlen(buf));
    send_ok(buf);
}

/* PALACE_TOUCH|<slot>|<hp_index> — Touch room (update access recency) */
void cmd_palace_touch(const char *slot_str, const char *hp_str)
{
    uint32_t slot_id  = parse_uint(slot_str);
    uint32_t hp_index = parse_uint(hp_str);
    int rc = vos3_ai_palace_touch_room(slot_id, hp_index);
    if (rc < 0) {
        send_err(rc, "PALACE_TOUCH failed");
        return;
    }
    send_ok("TOUCHED");
}

/* V_AAAK_ENCODE|<hex_data> — Encode text using V-AAAK compression */
void cmd_v_aaak_encode(const char *hex_data)
{
    uint8_t raw[4096];
    size_t raw_len = 0;
    if (hex_decode(hex_data, raw, sizeof(raw), &raw_len) != 0) {
        send_err(-22, "V_AAAK_ENCODE: bad hex");
        return;
    }

    uint8_t encoded[8192];
    uint32_t enc_len = vos3_v_aaak_encode(raw, (uint32_t)raw_len,
                                           encoded, sizeof(encoded));
    if (enc_len == 0) {
        send_err(-12, "V_AAAK_ENCODE: overflow");
        return;
    }

    /* Return hex-encoded compressed data */
    char hex_out[16384 + 1];
    hex_encode(encoded, enc_len, hex_out);
    send_ok(hex_out);
}

/* V_AAAK_DECODE|<hex_data> — Decode V-AAAK compressed data */
void cmd_v_aaak_decode(const char *hex_data)
{
    uint8_t encoded[8192];
    size_t enc_len = 0;
    if (hex_decode(hex_data, encoded, sizeof(encoded), &enc_len) != 0) {
        send_err(-22, "V_AAAK_DECODE: bad hex");
        return;
    }

    uint8_t decoded[16384];
    uint32_t dec_len = vos3_v_aaak_decode(encoded, (uint32_t)enc_len,
                                           decoded, sizeof(decoded));
    if (dec_len == 0) {
        send_err(-12, "V_AAAK_DECODE: overflow");
        return;
    }

    char hex_out[32768 + 1];
    hex_encode(decoded, dec_len, hex_out);
    send_ok(hex_out);
}

/* ============================================================================
 * KIM_STATS — Kernel Inference Manager statistics
 * ============================================================================ */

#include "../../include/vos/ai_kim.h"

/* KIM_STATS */
void cmd_kim_stats(void)
{
    vos3_kim_stats_t stats;
    int rc = vos3_kim_get_stats(&stats);
    if (rc != 0) {
        send_err(22, "KIM_STATS failed");
        return;
    }

    char buf[256];
    int pos = 0;
    char tmp[24];
    const char *k;

    /* active=N */
    k = "active=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.active_slots, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* tokens=N */
    k = "tokens=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.total_tokens, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* dispatches=N */
    k = "dispatches=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.total_dispatches, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* avg_latency=N */
    k = "avg_latency=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.avg_latency_us, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* bsp=N */
    k = "bsp=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.bsp_tokens, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* ap=N */
    k = "ap=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.ap_tokens, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* steals=N */
    k = "steals=";
    while (*k && pos < 240) buf[pos++] = *k++;
    uint_to_str(stats.total_steals, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 240; i++) buf[pos++] = tmp[i];

    buf[pos++] = '|';

    /* silicon_faults=N */
    k = "silicon_faults=";
    while (*k && pos < 250) buf[pos++] = *k++;
    uint_to_str(stats.silicon_faults, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 254; i++) buf[pos++] = tmp[i];

    buf[pos] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * PHASE 9: DMA WARP COMMANDS
 * ============================================================================ */

/* DMA_WARP_XFER|<slot>|<hp_start>|<hp_count>|<length>
 *
 * Transfers data from the ivshmem warp zone into the AI slot's
 * HugePage range using non-temporal (cache-bypassing) stores.
 * The ivshmem zone offset is implicitly 0 (host writes data
 * starting at zone base before issuing this command).
 */
void cmd_dma_warp_xfer(const char *slot_str, const char *hp_str,
                        const char *count_str, const char *len_str)
{
    uint32_t slot_id  = parse_uint(slot_str);
    uint32_t hp_start = parse_uint(hp_str);
    uint32_t hp_count = parse_uint(count_str);
    uint64_t length   = parse_u64(len_str);

    int rc = vos3_dma_warp_ivshmem_to_slot(slot_id, hp_start, hp_count,
                                            0, (size_t)length);
    if (rc < 0) {
        send_err(rc, "DMA_WARP_XFER failed");
        return;
    }
    send_ok("WARP_DMA_COMPLETE");
}

/* DMA_WARP_STATS — Return DMA engine statistics.
 *
 * Response format: total_xfers|total_bytes|peak_throughput|active_xfers|errors
 */
void cmd_dma_warp_stats(void)
{
    vos3_dma_stats_t stats;
    int rc = vos3_dma_warp_stats(&stats);
    if (rc < 0) {
        send_err(rc, "DMA not initialized");
        return;
    }

    char buf[160];
    char tmp[24];
    size_t off = 0;

    /* total_xfers */
    uint_to_str(stats.total_xfers, buf, sizeof(buf));
    off = bridge_strlen(buf);

    /* |total_bytes */
    buf[off++] = '|';
    uint_to_str(stats.total_bytes, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - off);
    off += bridge_strlen(tmp);

    /* |peak_throughput */
    buf[off++] = '|';
    uint_to_str(stats.peak_throughput, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - off);
    off += bridge_strlen(tmp);

    /* |active_xfers */
    buf[off++] = '|';
    uint_to_str((uint64_t)stats.active_xfers, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - off);
    off += bridge_strlen(tmp);

    /* |errors */
    buf[off++] = '|';
    uint_to_str((uint64_t)stats.errors, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - off);

    send_ok(buf);
}

/* ============================================================================
 * PHASE 2.1: MANAGED KV CACHE + vVFS COMMANDS
 * ============================================================================ */

#include "../../include/vos/ai_kv_managed.h"
#include "../../include/vos/vvfs.h"

/* --- Model slot array (defined in ai_slots.c) --- */
extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];

/* KV_TIER_STATUS|slot_id — Query tier distribution for a slot */
void cmd_kv_tier_status(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    if (sid >= VOS3_MODEL_SLOT_MAX) { send_err(22, "EINVAL: bad slot"); return; }

    vos3_ai_model_slot_t *slot = &g_model_slots[sid];
    vos3_kv_managed_t *mgd = &slot->kv_managed;

    char buf[256];
    char tmp[24];
    int off = 0;

    /* Format: t0_hp=<count>|t1_pages=<count>|t2_blocks=<count>|evict_warm=<n>|evict_cold=<n>|promotions=<n> */
    bridge_strcpy(buf, "t0_hp=", sizeof(buf));
    off = 6;
    uint_to_str((uint64_t)slot->kv_hp_count, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off); off += (int)bridge_strlen(tmp);

    buf[off++] = '|'; bridge_strcpy(buf + off, "t1_pages=", sizeof(buf) - (size_t)off); off += 9;
    uint_to_str((uint64_t)mgd->tier1_count, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off); off += (int)bridge_strlen(tmp);

    buf[off++] = '|'; bridge_strcpy(buf + off, "t2_blocks=", sizeof(buf) - (size_t)off); off += 10;
    uint_to_str((uint64_t)mgd->tier2_count, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off); off += (int)bridge_strlen(tmp);

    buf[off++] = '|'; bridge_strcpy(buf + off, "evict_warm=", sizeof(buf) - (size_t)off); off += 11;
    uint_to_str(mgd->evictions_to_warm, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off); off += (int)bridge_strlen(tmp);

    buf[off++] = '|'; bridge_strcpy(buf + off, "evict_cold=", sizeof(buf) - (size_t)off); off += 11;
    uint_to_str(mgd->evictions_to_cold, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off); off += (int)bridge_strlen(tmp);

    buf[off++] = '|'; bridge_strcpy(buf + off, "promotions=", sizeof(buf) - (size_t)off); off += 11;
    uint_to_str(mgd->promotions, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off);

    send_ok(buf);
}

/* KV_EVICT|slot_id|tier — Force eviction from tier N to tier N+1 */
void cmd_kv_evict(const char *slot_str, const char *tier_str)
{
    if (slot_str == NULL || tier_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    if (sid >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot out of range");
        return;
    }
    uint32_t tier = (uint32_t)parse_u64(tier_str);

    int rc;
    if (tier == 0U) {
        /* Evict from T0 (hot) to T1 (warm) — evict HP[0] */
        rc = vos3_kv_evict_to_warm(sid, 0);
    } else if (tier == 1U) {
        /* Evict from T1 (warm) to T2 (cold) — evict T1[0] */
        rc = vos3_kv_evict_to_cold(sid, 0);
    } else {
        send_err(22, "EINVAL: tier must be 0 or 1");
        return;
    }

    if (rc != 0) {
        char emsg[48];
        bridge_strcpy(emsg, "evict failed: ", sizeof(emsg));
        char ebuf[12];
        int_to_str(rc, ebuf, sizeof(ebuf));
        bridge_strcpy(emsg + 14, ebuf, sizeof(emsg) - 14);
        send_err(-rc, emsg);
    } else {
        send_ok("evicted");
    }
}

/* KV_PROMOTE|slot_id|seq_pos — Force promotion from tier N to tier N-1 */
void cmd_kv_promote(const char *slot_str, const char *seq_str)
{
    if (slot_str == NULL || seq_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    if (sid >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot out of range");
        return;
    }
    uint32_t seq = (uint32_t)parse_u64(seq_str);

    int rc = vos3_kv_promote(sid, seq);
    if (rc != 0) {
        char emsg[48];
        bridge_strcpy(emsg, "promote failed: ", sizeof(emsg));
        char ebuf[12];
        int_to_str(rc, ebuf, sizeof(ebuf));
        bridge_strcpy(emsg + 16, ebuf, sizeof(emsg) - 16);
        send_err(-rc, emsg);
    } else {
        send_ok("promoted");
    }
}

/* ============================================================================
 * PHASE 2.2: SPECULATIVE DECODING + CONTEXT WINDOW COMMANDS
 * ============================================================================ */

/* SPEC_CONFIGURE|draft_slot|target_slot|k */
void cmd_spec_configure(const char *draft_str, const char *target_str,
                        const char *k_str)
{
    if (draft_str == NULL || target_str == NULL) {
        send_err(22, "EINVAL: missing draft/target slot");
        return;
    }
    uint8_t draft = (uint8_t)parse_u64(draft_str);
    uint8_t target = (uint8_t)parse_u64(target_str);
    uint32_t k = k_str ? (uint32_t)parse_u64(k_str) : VOS3_SPEC_DEFAULT_K;

    int rc = vos3_spec_configure(draft, target, k);
    if (rc != 0) {
        char emsg[64];
        bridge_strcpy(emsg, "spec_configure failed: ", sizeof(emsg));
        char ebuf[12];
        int_to_str(rc, ebuf, sizeof(ebuf));
        bridge_strcpy(emsg + 23, ebuf, sizeof(emsg) - 23);
        send_err(-rc, emsg);
    } else {
        char resp[64];
        char d_buf[4], t_buf[4], k_buf[4];
        uint_to_str(draft, d_buf, sizeof(d_buf));
        uint_to_str(target, t_buf, sizeof(t_buf));
        uint_to_str(k, k_buf, sizeof(k_buf));
        int off = 0;
        const char *s;
        s = "draft="; while (*s && off < 60) resp[off++] = *s++;
        s = d_buf; while (*s && off < 60) resp[off++] = *s++;
        resp[off++] = '|';
        s = "target="; while (*s && off < 60) resp[off++] = *s++;
        s = t_buf; while (*s && off < 60) resp[off++] = *s++;
        resp[off++] = '|';
        s = "k="; while (*s && off < 60) resp[off++] = *s++;
        s = k_buf; while (*s && off < 60) resp[off++] = *s++;
        resp[off] = '\0';
        send_ok(resp);
    }
}

/* SPEC_GENERATE|slot|token */
void cmd_spec_generate(const char *slot_str, const char *token_str)
{
    if (slot_str == NULL || token_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    uint32_t token = (uint32_t)parse_u64(token_str);

    int rc = vos3_spec_generate(sid, token);
    if (rc != 0) {
        char emsg[48];
        bridge_strcpy(emsg, "spec_generate failed: ", sizeof(emsg));
        char ebuf[12];
        int_to_str(rc, ebuf, sizeof(ebuf));
        bridge_strcpy(emsg + 22, ebuf, sizeof(emsg) - 22);
        send_err(-rc, emsg);
    } else {
        send_ok("drafted");
    }
}

/* SPEC_STATS|slot */
void cmd_spec_stats(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    if (sid >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot out of range");
        return;
    }

    uint32_t drafted = 0, accepted = 0, rejected = 0;
    vos3_spec_get_stats(sid, &drafted, &accepted, &rejected);

    char buf[128];
    char tmp[16];
    int off = 0;
    const char *s;

    s = "drafted="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(drafted, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "accepted="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(accepted, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "rejected="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(rejected, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* SPEC_SET_K|slot|k */
void cmd_spec_set_k(const char *slot_str, const char *k_str)
{
    if (slot_str == NULL || k_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    if (sid >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot out of range");
        return;
    }

    uint32_t k = (uint32_t)parse_u64(k_str);
    if (k == 0U || k > VOS3_SPEC_MAX_K) {
        send_err(22, "EINVAL: k must be 1..8");
        return;
    }

    extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];
    g_model_slots[sid].spec_k = (uint8_t)k;

    char resp[32];
    char kbuf[4];
    uint_to_str(k, kbuf, sizeof(kbuf));
    bridge_strcpy(resp, "k=", sizeof(resp));
    bridge_strcpy(resp + 2, kbuf, sizeof(resp) - 2);
    send_ok(resp);
}

/* SPEC_RESET|slot */
void cmd_spec_reset(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);

    int rc = vos3_spec_reset(sid);
    if (rc != 0) {
        send_err(-rc, "spec_reset failed");
    } else {
        send_ok("reset");
    }
}

/* CTX_WINDOW_INIT|slot|max_active */
void cmd_ctx_window_init(const char *slot_str, const char *max_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    uint32_t max_active = max_str ? (uint32_t)parse_u64(max_str) : 4096U;

    int rc = vos3_ctx_window_init(sid, max_active);
    if (rc != 0) {
        send_err(-rc, "ctx_window_init failed");
    } else {
        char resp[32];
        char mbuf[8];
        uint_to_str(max_active, mbuf, sizeof(mbuf));
        bridge_strcpy(resp, "max=", sizeof(resp));
        bridge_strcpy(resp + 4, mbuf, sizeof(resp) - 4);
        send_ok(resp);
    }
}

/* CTX_WINDOW_FREEZE|slot|prefix_len */
void cmd_ctx_window_freeze(const char *slot_str, const char *prefix_str)
{
    if (slot_str == NULL || prefix_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    uint32_t prefix_len = (uint32_t)parse_u64(prefix_str);

    int rc = vos3_ctx_window_freeze(sid, prefix_len);
    if (rc != 0) {
        char emsg[48];
        bridge_strcpy(emsg, "ctx_freeze failed: ", sizeof(emsg));
        char ebuf[12];
        int_to_str(rc, ebuf, sizeof(ebuf));
        bridge_strcpy(emsg + 19, ebuf, sizeof(emsg) - 19);
        send_err(-rc, emsg);
    } else {
        send_ok("frozen");
    }
}

/* CTX_WINDOW_STATUS|slot */
void cmd_ctx_window_status(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);

    vos3_context_window_t win;
    int rc = vos3_ctx_window_status(sid, &win);
    if (rc != 0) {
        send_err(-rc, "ctx_window_status failed");
        return;
    }

    char buf[128];
    char tmp[16];
    int off = 0;
    const char *s;

    s = "frozen="; while (*s && off < 120) buf[off++] = *s++;
    buf[off++] = win.frozen ? '1' : '0';

    buf[off++] = '|';
    s = "frozen_len="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(win.frozen_len, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "active="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(win.active_len, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "max="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(win.max_active, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "total="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str(win.total_generated, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* VVFS_STAT|slot_id — Query vVFS mount status for a slot */
void cmd_vvfs_stat(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    if (sid >= VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot out of range");
        return;
    }

    uint8_t mounted = 0;
    uint32_t blocks_used = 0, blocks_total = 0;
    int rc = vvfs_stat(sid, &mounted, &blocks_used, &blocks_total);
    if (rc != 0) {
        send_err(-rc, "vvfs_stat failed");
        return;
    }

    char buf[128];
    char tmp[24];
    int off = 0;

    bridge_strcpy(buf, "mounted=", sizeof(buf)); off = 8;
    buf[off++] = mounted ? '1' : '0';

    buf[off++] = '|'; bridge_strcpy(buf + off, "used=", sizeof(buf) - (size_t)off); off += 5;
    uint_to_str((uint64_t)blocks_used, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off); off += (int)bridge_strlen(tmp);

    buf[off++] = '|'; bridge_strcpy(buf + off, "total=", sizeof(buf) - (size_t)off); off += 6;
    uint_to_str((uint64_t)blocks_total, tmp, sizeof(tmp));
    bridge_strcpy(buf + off, tmp, sizeof(buf) - (size_t)off);

    send_ok(buf);
}

/* ============================================================================
 * PHASE 2.3: HYBRID ORCHESTRATION COMMANDS
 * ============================================================================ */

/* ORCH_START|draft_slot|target_slot — Start hybrid orchestration session */
void cmd_orch_start(const char *draft_str, const char *target_str)
{
    if (draft_str == NULL || target_str == NULL) {
        send_err(22, "EINVAL: missing draft/target slot");
        return;
    }
    uint8_t draft  = (uint8_t)parse_u64(draft_str);
    uint8_t target = (uint8_t)parse_u64(target_str);

    int rc = vos3_orch_start(draft, target);
    if (rc < 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }

    char buf[64];
    char tmp[12];
    int off = 0;
    const char *s;

    s = "session="; while (*s && off < 60) buf[off++] = *s++;
    uint_to_str((uint64_t)(uint32_t)rc, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 60) buf[off++] = *s++;
    buf[off] = '\0';
    send_ok(buf);
}

/* ORCH_DISPATCH|session_id|token — Dispatch one orchestration round */
void cmd_orch_dispatch(const char *session_str, const char *token_str)
{
    if (session_str == NULL || token_str == NULL) {
        send_err(22, "EINVAL: missing session/token");
        return;
    }
    uint8_t  sid   = (uint8_t)parse_u64(session_str);
    uint32_t token = (uint32_t)parse_u64(token_str);

    int rc = vos3_orch_dispatch(sid, token);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("dispatched");
}

/* ORCH_STOP|session_id — Stop orchestration session */
void cmd_orch_stop(const char *session_str)
{
    if (session_str == NULL) {
        send_err(22, "EINVAL: missing session");
        return;
    }
    uint8_t sid = (uint8_t)parse_u64(session_str);

    int rc = vos3_orch_stop(sid);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("stopped");
}

/* ORCH_STATS — Query global orchestration statistics */
void cmd_orch_stats(void)
{
    vos3_orch_stats_t st;
    vos3_orch_get_stats(&st);

    char buf[256];
    char tmp[24];
    int off = 0;
    const char *s;

    s = "started="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.sessions_started, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off++] = '|';
    s = "completed="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.sessions_completed, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off++] = '|';
    s = "npu="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.npu_dispatches, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off++] = '|';
    s = "gpu="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.gpu_dispatches, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off++] = '|';
    s = "cpu="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.cpu_fallbacks, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off++] = '|';
    s = "ghost_sent="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.ghost_total_sent, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off++] = '|';
    s = "ghost_replaced="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str((uint64_t)st.ghost_total_replaced, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* ORCH_GHOST|session_id|enable — Toggle ghost token streaming */
void cmd_orch_ghost(const char *session_str, const char *enable_str)
{
    if (session_str == NULL || enable_str == NULL) {
        send_err(22, "EINVAL: missing session/enable");
        return;
    }
    uint8_t sid    = (uint8_t)parse_u64(session_str);
    uint8_t enable = (uint8_t)parse_u64(enable_str);

    int rc = vos3_orch_set_ghost(sid, enable);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok(enable ? "ghost=on" : "ghost=off");
}

/* ============================================================================
 * PHASE 2.3: GHOST TOKEN VBUS HELPERS
 *
 * Build VBUS_TYPE_STREAM_SPEC frames for ghost (unverified) and verified
 * (replacement) token streaming.
 *
 * Payload layout:
 *   [u8 flags][u8 slot_id][u16 count LE][u32 batch_id LE][u32 tokens[count] LE]
 * ============================================================================ */

void vos3_vbus_send_ghost_tokens(uint8_t slot_id, const uint32_t *tokens,
                                  uint32_t count, uint32_t batch_id)
{
    if (tokens == NULL || count == 0U) {
        return;
    }

    /* Cap to VOS3_SPEC_MAX_K tokens per frame */
    if (count > VOS3_SPEC_MAX_K) {
        count = VOS3_SPEC_MAX_K;
    }

    /* Build payload: flags(1) + slot(1) + count(2) + batch_id(4) + tokens(count*4) */
    uint32_t payload_len = 8U + (count * 4U);
    uint8_t payload[128]; /* Max: 8 + 8*4 = 40 bytes — well within 128 */

    if (payload_len > sizeof(payload)) {
        return;
    }

    uint32_t off = 0;
    payload[off++] = VOS3_SPEC_GHOST_FLAG;   /* flags: ghost */
    payload[off++] = slot_id;

    /* count LE */
    payload[off++] = (uint8_t)(count & 0xFFU);
    payload[off++] = (uint8_t)((count >> 8) & 0xFFU);

    /* batch_id LE */
    payload[off++] = (uint8_t)(batch_id & 0xFFU);
    payload[off++] = (uint8_t)((batch_id >> 8) & 0xFFU);
    payload[off++] = (uint8_t)((batch_id >> 16) & 0xFFU);
    payload[off++] = (uint8_t)((batch_id >> 24) & 0xFFU);

    /* tokens LE */
    for (uint32_t i = 0; i < count; i++) {
        uint32_t t = tokens[i];
        payload[off++] = (uint8_t)(t & 0xFFU);
        payload[off++] = (uint8_t)((t >> 8) & 0xFFU);
        payload[off++] = (uint8_t)((t >> 16) & 0xFFU);
        payload[off++] = (uint8_t)((t >> 24) & 0xFFU);
    }

    /* Set FINAL flag on last token's flags byte */
    payload[0] |= VOS3_SPEC_FINAL_FLAG;

    vos3_vbus_send_frame(VBUS_TYPE_STREAM_SPEC, slot_id,
                          VBUS_TAG_ASYNC, payload, payload_len);
}

void vos3_vbus_send_verified_tokens(uint8_t slot_id, const uint32_t *tokens,
                                     uint32_t count, uint32_t batch_id)
{
    if (tokens == NULL || count == 0U) {
        return;
    }

    if (count > (VOS3_SPEC_MAX_K + 1U)) {
        count = VOS3_SPEC_MAX_K + 1U;
    }

    uint32_t payload_len = 8U + (count * 4U);
    uint8_t payload[128];

    if (payload_len > sizeof(payload)) {
        return;
    }

    uint32_t off = 0;
    payload[off++] = VOS3_SPEC_VERIFY_FLAG;   /* flags: verified */
    payload[off++] = slot_id;

    /* count LE */
    payload[off++] = (uint8_t)(count & 0xFFU);
    payload[off++] = (uint8_t)((count >> 8) & 0xFFU);

    /* batch_id LE */
    payload[off++] = (uint8_t)(batch_id & 0xFFU);
    payload[off++] = (uint8_t)((batch_id >> 8) & 0xFFU);
    payload[off++] = (uint8_t)((batch_id >> 16) & 0xFFU);
    payload[off++] = (uint8_t)((batch_id >> 24) & 0xFFU);

    /* tokens LE */
    for (uint32_t i = 0; i < count; i++) {
        uint32_t t = tokens[i];
        payload[off++] = (uint8_t)(t & 0xFFU);
        payload[off++] = (uint8_t)((t >> 8) & 0xFFU);
        payload[off++] = (uint8_t)((t >> 16) & 0xFFU);
        payload[off++] = (uint8_t)((t >> 24) & 0xFFU);
    }

    payload[0] |= VOS3_SPEC_FINAL_FLAG;

    vos3_vbus_send_frame(VBUS_TYPE_STREAM_SPEC, slot_id,
                          VBUS_TAG_ASYNC, payload, payload_len);
}

/* ============================================================================
 * PHASE 4.1: vScreen + Agentic Mesh + Action Bridge COMMANDS
 * ============================================================================ */

#include "../../include/vos/vscreen.h"
#include "../../include/vos/agent_mesh.h"
#include "../../include/vos/action_bridge.h"

/* VSCREEN_START|slot|scanout|phys|width|height|patch|flags */
void cmd_vscreen_start(const char *slot_str, const char *scanout_str,
                       const char *phys_str, const char *width_str,
                       const char *height_str, const char *patch_str,
                       const char *flags_str)
{
    if (slot_str == NULL || scanout_str == NULL || phys_str == NULL ||
        width_str == NULL || height_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t sid      = (uint8_t)parse_u64(slot_str);
    uint32_t scan    = (uint32_t)parse_u64(scanout_str);
    uintptr_t phys   = (uintptr_t)parse_u64(phys_str);
    uint32_t w       = (uint32_t)parse_u64(width_str);
    uint32_t h       = (uint32_t)parse_u64(height_str);
    uint8_t ps       = patch_str ? (uint8_t)parse_u64(patch_str) : 224;
    uint8_t fl       = flags_str ? (uint8_t)parse_u64(flags_str) : 0x03;

    int rc = vscreen_start(sid, scan, phys, w, h, ps, fl);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("vscreen_started");
}

/* VSCREEN_CAPTURE|slot */
void cmd_vscreen_capture(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    int rc = vscreen_capture(sid);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("captured");
}

/* VSCREEN_STOP|slot */
void cmd_vscreen_stop(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    int rc = vscreen_stop(sid);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("stopped");
}

/* VSCREEN_STATUS|slot */
void cmd_vscreen_status(const char *slot_str)
{
    (void)slot_str;
    vscreen_stats_t stats;
    vscreen_get_stats(&stats);

    char buf[128];
    char tmp[24];
    int off = 0;
    const char *s;

    s = "captures="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str((uint64_t)stats.total_captures, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "patches="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str((uint64_t)stats.total_patches, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off++] = '|';
    s = "dispatches="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str((uint64_t)stats.total_dispatches, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* MESH_DISPATCH|src_slot|target_slot|type|priority */
void cmd_mesh_dispatch(const char *src_str, const char *target_str,
                       const char *type_str, const char *prio_str)
{
    if (src_str == NULL || type_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    mesh_task_t task;
    /* Zero-initialize */
    {
        uint8_t *p = (uint8_t *)&task;
        for (size_t i = 0; i < sizeof(task); i++) p[i] = 0;
    }
    task.source_slot = (uint8_t)parse_u64(src_str);
    task.target_slot = target_str ? (uint8_t)parse_u64(target_str) : 0xFF;
    task.type        = (uint8_t)parse_u64(type_str);
    task.priority    = prio_str ? (uint8_t)parse_u64(prio_str) : 128;

    int rc = mesh_dispatch(&task);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("dispatched");
}

/* MESH_RESULT|task_id|result_code */
void cmd_mesh_result(const char *task_str, const char *code_str)
{
    if (task_str == NULL || code_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    mesh_result_t result;
    {
        uint8_t *p = (uint8_t *)&result;
        for (size_t i = 0; i < sizeof(result); i++) p[i] = 0;
    }
    result.task_id     = (uint32_t)parse_u64(task_str);
    result.result_code = (uint8_t)parse_u64(code_str);

    int rc = mesh_result(result.task_id, &result);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("result_recorded");
}

/* MESH_TRUST|slot */
void cmd_mesh_trust(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    uint32_t score = 0;
    int rc = mesh_trust_score(sid, &score);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    char buf[32];
    char tmp[12];
    int off = 0;
    const char *s;
    s = "trust="; while (*s && off < 28) buf[off++] = *s++;
    uint_to_str((uint64_t)score, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 28) buf[off++] = *s++;
    buf[off] = '\0';
    send_ok(buf);
}

/* ACTION_SUBMIT|slot|type|command */
void cmd_action_submit(const char *slot_str, const char *type_str,
                       const char *cmd_str)
{
    if (slot_str == NULL || type_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    action_desc_t action;
    {
        uint8_t *p = (uint8_t *)&action;
        for (size_t i = 0; i < sizeof(action); i++) p[i] = 0;
    }
    action.type = (uint8_t)parse_u64(type_str);
    if (cmd_str != NULL) {
        size_t i;
        for (i = 0; i < ACTION_CMD_MAX_LEN - 1 && cmd_str[i]; i++) {
            action.command[i] = cmd_str[i];
        }
        action.command[i] = '\0';
    }

    uint8_t sid = (uint8_t)parse_u64(slot_str);
    int rc = action_submit(sid, &action);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("submitted");
}

/* ACTION_APPROVE|approver_slot|action_id */
void cmd_action_approve(const char *approver_str, const char *id_str)
{
    if (approver_str == NULL || id_str == NULL) {
        send_err(22, "EINVAL: missing args");
        return;
    }
    uint8_t approver = (uint8_t)parse_u64(approver_str);
    uint32_t action_id = (uint32_t)parse_u64(id_str);

    int rc = action_approve(approver, action_id);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("approved");
}

/* ACTION_EXECUTE|action_id */
void cmd_action_execute(const char *id_str)
{
    if (id_str == NULL) { send_err(22, "EINVAL: missing id"); return; }
    uint32_t action_id = (uint32_t)parse_u64(id_str);

    int rc = action_execute(action_id);
    if (rc != 0) {
        send_err(-rc, errno_str(-rc));
        return;
    }
    send_ok("executed");
}

/* ACTION_AUDIT */
void cmd_action_audit(void)
{
    action_stats_t stats;
    action_get_stats(&stats);

    char buf[180];
    char tmp[24];
    int off = 0;
    const char *s;

    s = "submitted="; while (*s && off < 170) buf[off++] = *s++;
    uint_to_str((uint64_t)stats.submitted, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 170) buf[off++] = *s++;

    buf[off++] = '|';
    s = "approved="; while (*s && off < 170) buf[off++] = *s++;
    uint_to_str((uint64_t)stats.approved, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 170) buf[off++] = *s++;

    buf[off++] = '|';
    s = "denied="; while (*s && off < 170) buf[off++] = *s++;
    uint_to_str((uint64_t)(stats.denied_trust + stats.denied_caps +
                            stats.denied_allowlist + stats.denied_consensus),
                tmp, sizeof(tmp));
    s = tmp; while (*s && off < 170) buf[off++] = *s++;

    buf[off++] = '|';
    s = "executed="; while (*s && off < 170) buf[off++] = *s++;
    uint_to_str((uint64_t)stats.executed, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 170) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* DRIVER_PRESSURE — Reports AI hardware throttle state to the Python API layer.
 *
 * Response format: "congested=N|hp_used=U|hp_total=T|timeouts=X"
 *   congested: 1 if token bucket is empty or CRC ban active, else 0
 *   hp_used:   hugepages currently allocated
 *   hp_total:  total hugepage capacity
 *   timeouts:  cumulative dispatch timeouts (Phase 9.1 watchdog counter)
 *
 * Python reads this via VBusDriver.send_command("DRIVER_PRESSURE") and
 * auto-downgrades the LLM model selection to claude-haiku-4-5 when
 * congested=1 or hp_used/hp_total > 0.85.
 */
void cmd_driver_pressure(void)
{
    uint32_t hp_total = 0, hp_used = 0;
    vos3_pmm_hugepage_stats(&hp_total, &hp_used);

    /* Read congestion state from token bucket sentinel */
    extern volatile uint32_t g_congestion;
    uint32_t congested = g_congestion ? 1U : 0U;

    /* Read watchdog timeout counter */
    extern volatile uint32_t g_dispatch_timeouts;
    uint32_t timeouts = g_dispatch_timeouts;

    char buf[128];
    char tmp[24];
    int off = 0;
    const char *s;

    s = "congested="; while (*s && off < 120) buf[off++] = *s++;
    buf[off++] = congested ? '1' : '0';
    buf[off++] = '|';

    s = "hp_used="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str((uint64_t)hp_used, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;
    buf[off++] = '|';

    s = "hp_total="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str((uint64_t)hp_total, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;
    buf[off++] = '|';

    s = "timeouts="; while (*s && off < 120) buf[off++] = *s++;
    uint_to_str((uint64_t)timeouts, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 120) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * v20.2: MMR Audit Ledger — MMR_ROOT VBus command
 * ============================================================================ */

#include "../sec/mmr_audit.h"

/**
 * MMR_ROOT — return the current Merkle Mountain Range root hash.
 *
 * Response format: "root=<64-hex-chars>|leaves=<decimal>"
 *
 * The Python backend calls this periodically and pins the root to an
 * external transparency log, providing a tamper-evident audit trail of
 * every syscall recorded since boot.
 */
void cmd_mmr_root(void)
{
    uint8_t root[MMR_HASH_SIZE];
    mmr_root(root);
    uint64_t leaves = mmr_leaf_count();

    /* Encode as "root=<64 hex chars>|leaves=<decimal>" */
    char buf[160];
    int  off = 0;
    const char *s;

    s = "root="; while (*s && off < 150) buf[off++] = *s++;
    hex_encode(root, MMR_HASH_SIZE, buf + off);
    off += MMR_HASH_SIZE * 2;  /* 32 bytes = 64 hex chars */

    if (off < (int)sizeof(buf) - 1) buf[off++] = '|';
    s = "leaves="; while (*s && off < 150) buf[off++] = *s++;

    char tmp[24];
    uint_to_str(leaves, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 150) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * v21.3.1 (Track A) — KV-cache efficiency stats + DIAGNOSTIC BENCHMARK PROBE
 * ============================================================================
 *
 * Restored 2026-05-02 from TRUTH-BRIDGE forensic protocol per
 * OMEGA_RESUMPTION_PROTOCOL.md §2.4.
 *
 * cmd_efficiency_stats — emits the canonical "OK|hits=N|misses=N|..." line
 * parsed by api/analytics_routes.py::_query_kernel_efficiency. Wire format
 * pinned by test_round_b12_parser_matches_kernel_emit_format.
 *
 * DIAGNOSTIC NOTE on cmd_kv_bench_probe (defined immediately below):
 *
 *   cmd_kv_bench_probe is a BENCHMARK PROBE / DIAGNOSTIC — NOT a production
 *   primitive. Useful for benchmark tooling (tools/vos3_swarm_bench.py)
 *   to measure KV-cache compression ratio under controlled load. The
 *   response carries an explicit "diagnostic=1" tag so an operator who
 *   finds it in production logs sees it is not an automatic monitoring
 *   path. For ordinary monitoring use cmd_efficiency_stats instead.
 */

/* DIAGNOSTIC: BENCHMARK PROBE — NOT a production primitive.
 * Operators reading this code: cmd_kv_bench_probe is the back-channel
 * the benchmark harness uses to inject controlled allocation patterns
 * into the kv_compressor and read out the resulting efficiency
 * counters in a single round-trip. It is gated behind PRO and is
 * disabled in CORE builds. Use cmd_efficiency_stats for ordinary
 * monitoring; this probe is for laboratory measurement only. */
void cmd_kv_bench_probe(void)
{
    uint64_t virtual_b = 0, physical_b = 0, ratio_x1000 = 1000;
    kv_compressor_get_efficiency(&virtual_b, &physical_b, &ratio_x1000);

    /* Format: "OK|diagnostic=1|virtual_bytes=N|physical_bytes=N|ratio_x1000=N" */
    char buf[160];
    int  off = 0;
    const char *s;
    char tmp[24];

    s = "diagnostic=1|virtual_bytes="; while (*s && off < 150) buf[off++] = *s++;
    uint_to_str(virtual_b, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 150) buf[off++] = *s++;

    s = "|physical_bytes="; while (*s && off < 150) buf[off++] = *s++;
    uint_to_str(physical_b, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 150) buf[off++] = *s++;

    s = "|ratio_x1000="; while (*s && off < 150) buf[off++] = *s++;
    uint_to_str(ratio_x1000, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 150) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

void cmd_efficiency_stats(void)
{
    uint64_t hits = 0, misses = 0, cow_breaks = 0;
    uint64_t virtual_b = 0, physical_b = 0, ratio_x1000 = 1000;

    kv_compressor_get_stats(&hits, &misses, &cow_breaks);
    kv_compressor_get_efficiency(&virtual_b, &physical_b, &ratio_x1000);

    /* Format pinned by test_round_b12_parser_matches_kernel_emit_format:
     *   OK|hits=N|misses=N|cow_breaks=N|virtual_bytes=N|
     *      physical_bytes=N|dedup_ratio_x1000=N
     */
    char buf[256];
    int  off = 0;
    const char *s;
    char tmp[24];

    s = "hits="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str(hits, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    s = "|misses="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str(misses, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    s = "|cow_breaks="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str(cow_breaks, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    s = "|virtual_bytes="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str(virtual_b, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    s = "|physical_bytes="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str(physical_b, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    s = "|dedup_ratio_x1000="; while (*s && off < 240) buf[off++] = *s++;
    uint_to_str(ratio_x1000, tmp, sizeof(tmp));
    s = tmp; while (*s && off < 240) buf[off++] = *s++;

    buf[off] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * Cyber overlay (Stages 4–5) — INTENT_SUBMIT
 *
 * Bridges userspace to the in-kernel IntentManifest validator
 * (vos3_intent_validate, kernel/src/mm/intent_validator.c).
 *
 * Frame format (text, pipe-separated; matches the rest of the VBus ABI):
 *
 *     INTENT_SUBMIT|<hex-bytes-of-manifest>                    (Stage 4: validate only)
 *     INTENT_SUBMIT|<hex-bytes-of-manifest>|<slot_id_decimal>  (Stage 5: validate + bind)
 *
 * Where <hex-bytes-of-manifest> is the IntentManifest binary envelope
 * (≥ VOS3_INTENT_HDR_SIZE, ≤ 4096 bytes raw — capped here so the line
 *  fits comfortably under LINE_MAX = 9216 even after hex doubling).
 *
 * Replies:
 *   Stage-4 path (no slot_id):
 *     OK  →  INTENT_OK|<96-hex-of-intent-sha384>
 *     err →  ERR 22 INTENT_VALIDATION_FAILED|rc=<n>
 *
 *   Stage-5 path (slot_id provided):
 *     OK  →  INTENT_BOUND|<96-hex-of-composed-commitment-sha384>
 *            commitment = SHA-384( h_M || h_I || h_P )
 *            where h_M = SHA-384(slot weights), h_I = intent digest,
 *            h_P = NULL (treated as all-zero by tee.c).
 *            The composed digest is what the kernel extended into RTMR[1].
 *     err →  ERR 22 INTENT_BIND_FAILED|<reason>
 *
 * Trust property:
 *   The Stage-5 path is the production-grade hardware-rooted bind.
 *   After it returns, the platform's TDX RTMR[1] reflects the composed
 *   commitment of (this slot's weights, this manifest, this policy),
 *   which any external attestation verifier can re-derive and check.
 * ============================================================================ */

#define INTENT_SUBMIT_MAX_RAW_BYTES  4096U  /* well under LINE_MAX/2 */

void cmd_intent_submit(const char *hex_str, const char *slot_str)
{
    if (!hex_str) {
        send_err(22, "EINVAL: missing manifest hex");
        return;
    }

    static uint8_t s_manifest_buf[INTENT_SUBMIT_MAX_RAW_BYTES];
    size_t  raw_len = 0U;

    if (hex_decode(hex_str, s_manifest_buf, sizeof(s_manifest_buf), &raw_len) != 0) {
        send_err(22, "EINVAL: bad hex or oversized manifest");
        return;
    }

    uint8_t intent_digest[48];
    const int rc = vos3_intent_validate(s_manifest_buf, raw_len, intent_digest);

    if (rc != VOS3_INTENT_OK) {
        /* Stage 10.1 — Active Compliance Logging:
         * The validator does NOT populate intent_digest on failure paths
         * (it bails before the SHA-384 step). Compute SHA-384 of whatever
         * bytes we received so the audit-ring entry carries a stable
         * "what bytes did we reject" prefix. raw_len may be 0; sha384 of
         * empty input is a well-defined constant. */
        uint8_t reject_digest[48];
        vos3_sha384(s_manifest_buf, raw_len, reject_digest);
        vos3_audit_emit_failure((uint16_t)VOS3_AUDIT_CAT_INTENT_REJECT,
                                /*slot_id=*/0xFFU,
                                (int16_t)rc,
                                reject_digest);

        char msg[64];
        const char *prefix = "INTENT_VALIDATION_FAILED|rc=";
        char rc_str[12];
        int_to_str((int32_t)rc, rc_str, sizeof(rc_str));
        int oi = 0;
        const char *s;
        for (s = prefix; *s && oi < 60; s++) msg[oi++] = *s;
        for (s = rc_str; *s && oi < 60; s++) msg[oi++] = *s;
        msg[oi] = '\0';
        send_err(22, msg);
        return;
    }

    /* Stage 4 path: validate only — no slot binding requested. */
    if (!slot_str) {
        char resp[128];
        const char *p = "INTENT_OK|";
        int oi = 0;
        while (*p && oi < 12) resp[oi++] = *p++;
        hex_encode(intent_digest, sizeof(intent_digest), &resp[oi]);
        send_ok(resp);
        return;
    }

    /* Stage 5 path: validate + bind. */
    const uint8_t slot_id = (uint8_t)parse_u64(slot_str);

    /* Resilience-Matrix F1 — bound slot_id BEFORE indexing the lock array. */
    if ((unsigned)slot_id >= (unsigned)VOS3_MODEL_SLOT_MAX) {
        vos3_audit_emit_failure((uint16_t)VOS3_AUDIT_CAT_TEE_BIND_FAIL,
                                slot_id,
                                /*rc=*/-22,
                                intent_digest);
        send_err(22, "INTENT_BIND_FAILED|slot_id_out_of_range");
        return;
    }

    /* Per-slot binding critical section. Take the lock for the entire
     * read-info → compute-h_M → activate_bound sequence so two
     * concurrent submits on the same slot cannot interleave reads of
     * the slot's bytes around an in-flight RTMR extend. */
    vos3_spinlock_acquire(&g_slot_bind_locks[slot_id]);

    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    if (info.base == 0 || info.size == 0) {
        vos3_spinlock_release(&g_slot_bind_locks[slot_id]);
        /* Stage 10.1 — slot-not-loaded is an attest-relevant failure too.
         * Use intent_digest as the prefix because the manifest itself
         * was structurally fine — the failure is at the bind layer. */
        vos3_audit_emit_failure((uint16_t)VOS3_AUDIT_CAT_TEE_BIND_FAIL,
                                slot_id,
                                /*rc=*/-22,
                                intent_digest);
        send_err(22, "INTENT_BIND_FAILED|slot_not_loaded");
        return;
    }

    /* Compute SHA-384 of the slot's weight bytes for h_M. */
    uint8_t model_digest[48];
    vos3_sha384((const void *)(uintptr_t)info.base, info.size, model_digest);

    /* Composed-commitment activate: RTMR[1] += SHA-384(h_M || h_I || h_P).
     * policy_digest = NULL → treated as all-zero by tee.c (no policy). */
    uint8_t commitment[48];
    const int brc = vos3_tee_slot_activate_bound(slot_id,
                                                 model_digest,
                                                 intent_digest,
                                                 (const uint8_t *)0,
                                                 commitment);

    /* Release lock as soon as the binding is complete (or failed) —
     * subsequent message construction does not need it. */
    vos3_spinlock_release(&g_slot_bind_locks[slot_id]);

    if (brc != 0) {
        /* Stage 10.1 — TEE bind failure path. */
        vos3_audit_emit_failure((uint16_t)VOS3_AUDIT_CAT_TEE_BIND_FAIL,
                                slot_id,
                                (int16_t)brc,
                                intent_digest);

        char msg[64];
        const char *prefix = "INTENT_BIND_FAILED|tee_rc=";
        char rc_str[12];
        int_to_str((int32_t)brc, rc_str, sizeof(rc_str));
        int oi = 0;
        const char *s;
        for (s = prefix; *s && oi < 60; s++) msg[oi++] = *s;
        for (s = rc_str; *s && oi < 60; s++) msg[oi++] = *s;
        msg[oi] = '\0';
        send_err(22, msg);
        return;
    }

    /* Stage 10.2 — bind the hallucination guardrail to the slot.
     *
     * The validator already accepted the manifest (rc == VOS3_INTENT_OK)
     * AND the tee bind succeeded, so the buffer is structurally sound and
     * the slot is now active. Read the version + score directly from the
     * raw bytes (offsets 8 and 18 — see schema in vos/tee.h) instead of
     * extending the validator's signature.
     *
     * v1 manifest → score = 0 (no guardrail; behaviour unchanged from 10.1).
     * v2 manifest → score = the validated 0..1000 value at offset 18. */
    {
        const uint16_t mver = (uint16_t)((uint16_t)s_manifest_buf[8]
                                       | ((uint16_t)s_manifest_buf[9] << 8));
        uint16_t mscore = 0U;
        if (mver == (uint16_t)VOS3_INTENT_VERSION_V2) {
            mscore = (uint16_t)((uint16_t)s_manifest_buf[18]
                              | ((uint16_t)s_manifest_buf[19] << 8));
        }
        (void)vos3_action_bridge_set_min_confidence(slot_id, mscore);
    }

    /* Reply: "INTENT_BOUND|<96-hex-chars>" — 13 + 96 + 1 = 110 bytes. */
    char resp[128];
    const char *p = "INTENT_BOUND|";
    int oi = 0;
    while (*p && oi < 14) resp[oi++] = *p++;
    hex_encode(commitment, sizeof(commitment), &resp[oi]);
    send_ok(resp);
}

/* ============================================================================
 * Cyber overlay (Stage 6) — diagnostic / telemetry VBus commands.
 *
 * Each handler exists for two reasons:
 *   (1) Provide a real, userspace-reachable read path for one of the
 *       Cyber security primitives that has no organic call site in the
 *       vos4 baseline (boot init, attestation read, scheduler telemetry,
 *       manual L1D flush, sibling-compatibility query).
 *   (2) Defeat linker --gc-sections by giving each Cyber symbol at
 *       least one outgoing edge from a TU referenced by the dispatcher.
 *
 * All commands are diag/telemetry — they read state or perform an
 * explicitly-requested operation. None of them modify scheduling or
 * security policy beyond what a privileged userspace caller already
 * had via existing VBus commands.
 * ============================================================================ */

/* TEE_ENV → reports the current Trusted Execution Environment. */
void cmd_tee_env(void)
{
    const vos3_tee_env_t env = vos3_tee_env();
    const char *name;
    switch (env) {
        case VOS3_TEE_BAREMETAL:    name = "BAREMETAL";    break;
        case VOS3_TEE_STANDARD_VM:  name = "STANDARD_VM";  break;
        case VOS3_TEE_INTEL_TDX:    name = "INTEL_TDX";    break;
        case VOS3_TEE_AMD_SEV_SNP:  name = "AMD_SEV_SNP";  break;
        default:                    name = "UNKNOWN";      break;
    }
    char resp[64];
    const char *p = "TEE_ENV|";
    int oi = 0;
    while (*p && oi < 12)        resp[oi++] = *p++;
    while (*name && oi < 60)     resp[oi++] = *name++;
    resp[oi++] = '|';
    char num[12];
    int_to_str((int32_t)env, num, sizeof(num));
    const char *q = num;
    while (*q && oi < 62)        resp[oi++] = *q++;
    resp[oi] = '\0';
    send_ok(resp);
}

/* TEE_QUOTE → snapshot of the per-slot TEE measurement ring as text. */
#define TEE_QUOTE_MAX_ENTRIES  8U
void cmd_tee_quote(void)
{
    static vos3_tee_measurement_t s_snap[TEE_QUOTE_MAX_ENTRIES];
    const uint32_t n = vos3_tee_measurements_snapshot(s_snap, TEE_QUOTE_MAX_ENTRIES);

    /* Response: "TEE_QUOTE|<count>|rtmr<i>:slot=<s>:tick=<t>:dig=<8hex>...|..."
     * Capped to first 4 entries so we stay under the response buffer. */
    char resp[256];
    int oi = 0;
    const char *p = "TEE_QUOTE|";
    while (*p && oi < 12) resp[oi++] = *p++;
    char num[12];
    int_to_str((int32_t)n, num, sizeof(num));
    for (const char *q = num; *q && oi < 254; q++) resp[oi++] = *q;

    const uint32_t emit = (n < 4U) ? n : 4U;
    for (uint32_t i = 0U; i < emit; i++) {
        if (oi < 250) resp[oi++] = '|';
        int_to_str((int32_t)s_snap[i].rtmr_index, num, sizeof(num));
        for (const char *q = num; *q && oi < 250; q++) resp[oi++] = *q;
        if (oi < 250) resp[oi++] = ':';
        int_to_str((int32_t)s_snap[i].slot_id, num, sizeof(num));
        for (const char *q = num; *q && oi < 250; q++) resp[oi++] = *q;
        if (oi < 250) resp[oi++] = ':';
        /* First 4 bytes (8 hex chars) of digest for compactness. */
        char hex8[9];
        hex_encode(s_snap[i].digest, 4U, hex8);
        for (const char *q = hex8; *q && oi < 252; q++) resp[oi++] = *q;
    }
    resp[oi] = '\0';
    send_ok(resp);
}

/* SCHED_COOKIE_STATS → cookie-rejection counter + current task's cookie. */
void cmd_sched_cookie_stats(void)
{
    const uint64_t rejections = vos3_sched_cookie_rejections();
    const vos3_task_t *cur    = vos3_sched_current();
    const uint64_t my_cookie  = (cur != (vos3_task_t *)0)
                                ? vos3_sched_get_cookie(cur)
                                : 0ULL;

    char resp[96];
    const char *p = "SCHED_COOKIE_STATS|rejections=";
    int oi = 0;
    while (*p && oi < 32) resp[oi++] = *p++;
    char num[24];
    uint_to_str(rejections, num, sizeof(num));
    for (const char *q = num; *q && oi < 90; q++) resp[oi++] = *q;
    p = "|cur_cookie=";
    while (*p && oi < 90) resp[oi++] = *p++;
    /* hex form for cookie (it's an opaque 64-bit value) */
    char hex_cookie[18];
    u64_to_hex(my_cookie, hex_cookie, sizeof(hex_cookie));
    for (const char *q = hex_cookie; *q && oi < 94; q++) resp[oi++] = *q;
    resp[oi] = '\0';
    send_ok(resp);
}

/* HCS_FLUSH → operator-initiated hardware-context-switch L1D flush.
 * Uses VOS3_HCS_SRC_MANUAL exactly as designed by hcs.h:54. */
void cmd_hcs_flush(void)
{
    vos3_hcs_flush(VOS3_HCS_SRC_MANUAL);
    send_ok("HCS_FLUSHED|src=MANUAL");
}

/* SCHED_SIBLING_CHECK|<cpu_id> → reports whether current task is allowed
 * to run on cpu_id given the cookie of whatever is on the SMT sibling.
 * Diagnostic / admission-test path; does not actually migrate the task. */
void cmd_sched_sibling_check(const char *cpu_str)
{
    if (!cpu_str) {
        send_err(22, "EINVAL: missing cpu_id");
        return;
    }
    const uint32_t cpu_id  = (uint32_t)parse_u64(cpu_str);
    const vos3_task_t *cur = vos3_sched_current();
    if (cur == (vos3_task_t *)0) {
        send_err(22, "EINVAL: no current task");
        return;
    }
    const int compat = vos3_sched_sibling_compatible(cpu_id, cur);

    char resp[64];
    const char *p = "SCHED_SIBLING_CHECK|cpu=";
    int oi = 0;
    while (*p && oi < 28) resp[oi++] = *p++;
    char num[12];
    uint_to_str((uint64_t)cpu_id, num, sizeof(num));
    for (const char *q = num; *q && oi < 56; q++) resp[oi++] = *q;
    p = "|compat=";
    while (*p && oi < 56) resp[oi++] = *p++;
    p = compat ? "1" : "0";
    while (*p && oi < 60) resp[oi++] = *p++;
    resp[oi] = '\0';
    send_ok(resp);
}

/* ============================================================================
 * Stage 10.1 — AUDIT_FAIL_QUOTE
 *
 * Reads the kernel compliance-failure ring (vos3_audit_snapshot) and emits
 * a one-line summary the userspace compliance endpoint will re-wrap as
 * signed JSON-LD in Stage 10.3.
 *
 * Frame: AUDIT_FAIL_QUOTE
 * Reply: AUDIT_FAIL|total=<n>|fill=<m>[|<seq>:<cat>:<rc>:<slot>:<digest_prefix_hex16>]…
 *
 * The reply is bounded — at most VOS3_AUDIT_QUOTE_MAX entries are inlined
 * to keep the line under LINE_MAX. Userspace asks for the snapshot, parses
 * the entries, and (in Stage 10.3) re-wraps each one in JSON-LD with a
 * hybrid-signature attestation cover.
 *
 * This command also keeps vos3_audit_snapshot / vos3_audit_total_emitted
 * LIVE in the linked image (linker --gc-sections would otherwise strip
 * them — they have no other call site until the userspace bridge lands).
 * ============================================================================ */

#define VOS3_AUDIT_QUOTE_MAX 16U  /* fits in LINE_MAX even with worst-case widths */

void cmd_audit_fail_quote(void)
{
    vos3_audit_event_t s_quote_buf[VOS3_AUDIT_QUOTE_MAX];
    const uint32_t total = vos3_audit_total_emitted();
    const uint32_t got   = vos3_audit_snapshot(s_quote_buf, VOS3_AUDIT_QUOTE_MAX);

    /* Pre-format header. resp is sized for header + 16 entries × ~32 chars. */
    char resp[1024];
    int  oi = 0;

    const char *p = "AUDIT_FAIL|total=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;

    char num[12];
    uint_to_str((uint64_t)total, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

    p = "|fill=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    uint_to_str((uint64_t)got, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

    /* Per-entry: |<seq>:<cat>:<rc>:<slot>:<digest_prefix_hex16> */
    for (uint32_t i = 0U; i < got && oi < (int)sizeof(resp) - 64; i++) {
        const vos3_audit_event_t *e = &s_quote_buf[i];

        if (oi < (int)sizeof(resp) - 1) resp[oi++] = '|';

        uint_to_str((uint64_t)e->seq, num, sizeof(num));
        for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

        if (oi < (int)sizeof(resp) - 1) resp[oi++] = ':';
        uint_to_str((uint64_t)e->category, num, sizeof(num));
        for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

        if (oi < (int)sizeof(resp) - 1) resp[oi++] = ':';
        int_to_str((int32_t)e->rc, num, sizeof(num));
        for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

        if (oi < (int)sizeof(resp) - 1) resp[oi++] = ':';
        uint_to_str((uint64_t)e->slot_id, num, sizeof(num));
        for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

        if (oi < (int)sizeof(resp) - 1) resp[oi++] = ':';
        /* digest_prefix as 16-hex (uint64 BE). Reuse hex_encode for 8 raw bytes. */
        uint8_t prefix_bytes[8];
        prefix_bytes[0] = (uint8_t)(e->digest_prefix >> 56);
        prefix_bytes[1] = (uint8_t)(e->digest_prefix >> 48);
        prefix_bytes[2] = (uint8_t)(e->digest_prefix >> 40);
        prefix_bytes[3] = (uint8_t)(e->digest_prefix >> 32);
        prefix_bytes[4] = (uint8_t)(e->digest_prefix >> 24);
        prefix_bytes[5] = (uint8_t)(e->digest_prefix >> 16);
        prefix_bytes[6] = (uint8_t)(e->digest_prefix >>  8);
        prefix_bytes[7] = (uint8_t)(e->digest_prefix);
        if (oi <= (int)sizeof(resp) - 17) {
            hex_encode(prefix_bytes, sizeof(prefix_bytes), &resp[oi]);
            oi += 16;
        }
    }
    resp[oi] = '\0';
    send_ok(resp);
}

/* ============================================================================
 * Stage 10.2 — ACTION_CHECK_CONFIDENCE
 *
 * Frame:  ACTION_CHECK_CONFIDENCE|<slot_id>|<observed_score 0..1000>
 * Reply:
 *   permit  → CONF_OK|slot=<n>|gate=<g>|observed=<o>
 *   block   → ERR 13 CONF_BLOCK|slot=<n>|gate=<g>|observed=<o>
 *
 * Userspace exercise of the hallucination guardrail. In the production
 * path the gate runs inline alongside LLM token emission (Stage 10's
 * Asynchronous Integrity Streaming sub-task — backend integration); this
 * VBus command exists so:
 *   (a) The userspace test harness can assert end-to-end that a v2
 *       manifest bound at INTENT_SUBMIT enforces the score later.
 *   (b) Linker --gc-sections cannot strip vos3_action_bridge_check_confidence
 *       / vos3_action_bridge_get_min_confidence — the same activation
 *       pattern used for the Stage-6 Cyber primitives.
 *
 * The block path emits a HALLUCINATION_BLOCK event into the Stage-10.1
 * audit ring as a side effect of vos3_action_bridge_check_confidence().
 * ============================================================================ */

void cmd_action_check_confidence(const char *slot_str, const char *score_str)
{
    if (!slot_str || !score_str) {
        send_err(22, "EINVAL: missing slot_id or observed_score");
        return;
    }
    const uint64_t slot64  = parse_u64(slot_str);
    const uint64_t score64 = parse_u64(score_str);

    if (slot64 >= (uint64_t)VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot_id out of range");
        return;
    }
    if (score64 > (uint64_t)VOS3_INTENT_MAX_CONFIDENCE_SCORE) {
        send_err(22, "EINVAL: observed_score > 1000");
        return;
    }
    const uint8_t  slot_id  = (uint8_t)slot64;
    const uint16_t observed = (uint16_t)score64;

    const uint16_t gate = vos3_action_bridge_get_min_confidence(slot_id);
    const int      rc   = vos3_action_bridge_check_confidence(slot_id, observed);

    char resp[96];
    int  oi = 0;
    const char *p = (rc == 0) ? "CONF_OK|slot=" : "CONF_BLOCK|slot=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;

    char num[12];
    uint_to_str((uint64_t)slot_id, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

    p = "|gate=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    uint_to_str((uint64_t)gate, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

    p = "|observed=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    uint_to_str((uint64_t)observed, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;

    resp[oi] = '\0';

    if (rc == 0) {
        send_ok(resp);
    } else {
        send_err(13, resp);  /* EACCES — block */
    }
}

/* ============================================================================
 * Stage 10.2.2 — Management override / Safe-Rollout VBus surface
 *
 * Three commands. Each is a single frame, single reply, no persistent
 * kernel state across reboots. The backend re-asserts whatever the env
 * config says on startup; the kernel never remembers an override
 * between QEMU runs.
 *
 *   POLICY_OVERRIDE|<slot_id>|<score>     → direct per-slot confidence
 *                                            override (bypasses INTENT_SUBMIT)
 *     OK reply: POLICY_OK|slot=<n>|gate=<g>
 *
 *   POLICY_FORCE_PERMIT|<0|1>             → toggle global Safe-Rollout
 *     OK reply: POLICY_OK|force_permit=<0|1>
 *
 *   POLICY_STATUS                         → read-back for management dashboard
 *     OK reply: POLICY|force_permit=<0|1>|gates=<g0>,<g1>,…,<g_{N-1}>
 *
 * Why a separate POLICY_OVERRIDE (vs. just re-issuing INTENT_SUBMIT with
 * a new manifest): the CEO may want to dial a single agent up/down without
 * re-binding the slot's measurement chain. INTENT_SUBMIT performs a TEE
 * extend on RTMR[1]; that's the right path for a real policy change but
 * heavyweight for a quick threshold tweak. POLICY_OVERRIDE is the light
 * path — it changes the gate value without touching the measurement
 * chain, and the existing audit-ring telemetry still records every
 * subsequent block / permit decision so the policy change is visible
 * downstream.
 * ============================================================================ */

void cmd_policy_override(const char *slot_str, const char *score_str)
{
    if (!slot_str || !score_str) {
        send_err(22, "EINVAL: missing slot_id or score");
        return;
    }
    const uint64_t slot64  = parse_u64(slot_str);
    const uint64_t score64 = parse_u64(score_str);

    if (slot64 >= (uint64_t)VOS3_MODEL_SLOT_MAX) {
        send_err(22, "EINVAL: slot_id out of range");
        return;
    }
    if (score64 > (uint64_t)VOS3_INTENT_MAX_CONFIDENCE_SCORE) {
        send_err(22, "EINVAL: score > 1000");
        return;
    }
    const uint8_t  slot_id = (uint8_t)slot64;
    const uint16_t score   = (uint16_t)score64;

    if (vos3_action_bridge_set_min_confidence(slot_id, score) != 0) {
        send_err(22, "EINVAL: set_min_confidence failed");
        return;
    }

    char resp[64];
    int  oi = 0;
    const char *p = "POLICY_OK|slot=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    char num[12];
    uint_to_str((uint64_t)slot_id, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;
    p = "|gate=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    uint_to_str((uint64_t)score, num, sizeof(num));
    for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;
    resp[oi] = '\0';
    send_ok(resp);
}

void cmd_policy_force_permit(const char *enable_str)
{
    if (!enable_str) {
        send_err(22, "EINVAL: missing enable flag");
        return;
    }
    const uint64_t enable64 = parse_u64(enable_str);
    if (enable64 > 1ULL) {
        send_err(22, "EINVAL: enable must be 0 or 1");
        return;
    }
    vos3_action_bridge_set_force_permit((int)enable64);

    char resp[48];
    int  oi = 0;
    const char *p = "POLICY_OK|force_permit=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    resp[oi++] = (enable64 != 0ULL) ? '1' : '0';
    resp[oi]   = '\0';
    send_ok(resp);
}

void cmd_policy_status(void)
{
    char resp[256];
    int  oi = 0;

    const char *p = "POLICY|force_permit=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;
    resp[oi++] = vos3_action_bridge_get_force_permit() ? '1' : '0';

    p = "|gates=";
    while (*p && oi < (int)sizeof(resp) - 1) resp[oi++] = *p++;

    char num[12];
    for (uint32_t s = 0U; s < (uint32_t)VOS3_MODEL_SLOT_MAX; s++) {
        if (s > 0U && oi < (int)sizeof(resp) - 1) resp[oi++] = ',';
        const uint16_t g = vos3_action_bridge_get_min_confidence((uint8_t)s);
        uint_to_str((uint64_t)g, num, sizeof(num));
        for (const char *q = num; *q && oi < (int)sizeof(resp) - 1; q++) resp[oi++] = *q;
    }
    resp[oi] = '\0';
    send_ok(resp);
}
