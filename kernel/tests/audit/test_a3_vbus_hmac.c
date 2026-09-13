/**
 * @file test_a3_vbus_hmac.c
 * @brief A3 — adversarial host audit of the VBus per-command HMAC trailer
 *        verification contract (vbus_transport.c:vos3_verify_cmd_hmac).
 *        TEST_PLAN_300 §A3.
 *
 * vos3_verify_cmd_hmac() lives in vbus_transport.c, which drags in UART /
 * spinlock / token-bucket / global state that cannot host-compile. So this
 * test exercises:
 *   (1) the REAL HMAC-SHA256 primitive the gate stands on (src/crypto/sha256.c
 *       compiled directly) against the RFC 4231 known-answer vector, and
 *   (2) a FAITHFUL behavioural twin of the verify wrapper — same incremental
 *       HMAC API, same AAD prefix layout (type|slot|tag_lo|tag_hi), same
 *       constant-time 32-byte trailer compare, same early-return contract —
 *       mirroring vbus_transport.c:83-122 line-for-line (cited below).
 *
 * The crypto is 100% production code; only the ~15-line compare/early-return
 * wrapper is mirrored. The in-kernel vbus_replay_test.c remains the authority
 * for the live frame-drop + HMAC-ban behaviour; this is the deterministic
 * host-side boundary audit.
 *
 * Host-runnable:
 *     cc -O2 -DVOS3_STRING_H -Ikernel/include -o /tmp/a3 \
 *        kernel/tests/audit/test_a3_vbus_hmac.c && /tmp/a3
 *   (-DVOS3_STRING_H skips the kernel's x86-only string.h asm; sha256.c then
 *    uses host libc memcpy/memset, which we include first.)
 */
#include <string.h>      /* host memcpy/memset for sha256.c */
#define VOS3_STRING_H    /* neutralise kernel/include/vos/string.h (x86 asm) */
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>

#include "../../src/crypto/sha256.c"   /* real HMAC-SHA256 primitive */

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok)
{
    printf("%-54s %s\n", name, ok ? "PASS" : "FAIL");
    if (ok) g_pass++; else g_fail++;
}

/* A3-1 three-valued return (mirror of vbus_bridge_internal.h). */
#define VOS3_HMAC_INVALID    0
#define VOS3_HMAC_VALID      1
#define VOS3_HMAC_NO_TRAILER 2

/* ------------------------------------------------------------------------
 * Faithful twin of vbus_transport.c:vos3_verify_cmd_hmac (A3-1 refactor).
 *   VOS3_HMAC_NO_TRAILER  payload_len < 32 OR key_len == 0 (stripped/no key)
 *   VOS3_HMAC_INVALID     trailer present, MAC mismatch (tamper/forgery)
 *   VOS3_HMAC_VALID       trailer present, MAC authentic
 * ---------------------------------------------------------------------- */
static int verify_cmd_hmac_twin(uint8_t type, uint8_t slot_id, uint16_t tag,
                                const uint8_t *payload, uint32_t payload_len,
                                const uint8_t *session_key, uint32_t key_len)
{
    if (payload_len < 32 || key_len == 0) {
        return VOS3_HMAC_NO_TRAILER;  /* no trailer / no key (SOURCE l.87) */
    }
    vos3_hmac_ctx_t ctx;
    uint8_t prefix[4];
    prefix[0] = type;
    prefix[1] = slot_id;
    prefix[2] = (uint8_t)(tag & 0xFF);
    prefix[3] = (uint8_t)(tag >> 8);

    vos3_hmac_sha256_init(&ctx, session_key, key_len);
    vos3_hmac_sha256_update(&ctx, prefix, 4);
    vos3_hmac_sha256_update(&ctx, payload, payload_len - 32);

    uint8_t computed[32];
    vos3_hmac_sha256_final(&ctx, computed);

    const uint8_t *trailer = payload + payload_len - 32;
    volatile uint8_t diff = 0;
    for (int i = 0; i < 32; i++) diff |= computed[i] ^ trailer[i];
    return diff != 0 ? VOS3_HMAC_INVALID : VOS3_HMAC_VALID;
}

/* Downstream command-ingestion handler (A3-1 fix demonstration): accepts a
 * frame ONLY on VOS3_HMAC_VALID. Both INVALID (tamper) and NO_TRAILER (strip)
 * fail-closed — a stripped frame can no longer pass through as "valid". */
static int ingest_frame_accepts(int hmac_result)
{
    return hmac_result == VOS3_HMAC_VALID;  /* fail-closed on INVALID + NO_TRAILER */
}

/* Build a frame: [body(body_len)] + [32-byte HMAC trailer] over
 * prefix(type,slot,tag) + body, keyed by `key`. */
static uint32_t build_frame(uint8_t type, uint8_t slot, uint16_t tag,
                            const uint8_t *body, uint32_t body_len,
                            const uint8_t *key, uint32_t key_len,
                            uint8_t *out)
{
    if (body_len) memcpy(out, body, body_len);
    uint8_t prefix[4] = { type, slot, (uint8_t)(tag & 0xFF), (uint8_t)(tag >> 8) };
    vos3_hmac_ctx_t ctx;
    vos3_hmac_sha256_init(&ctx, key, key_len);
    vos3_hmac_sha256_update(&ctx, prefix, 4);
    vos3_hmac_sha256_update(&ctx, body, body_len);
    vos3_hmac_sha256_final(&ctx, out + body_len);
    return body_len + 32;
}

static int hexeq(const uint8_t *a, const char *hex, int n)
{
    for (int i = 0; i < n; i++) {
        unsigned v; sscanf(hex + i * 2, "%2x", &v);
        if (a[i] != (uint8_t)v) return 0;
    }
    return 1;
}

int main(void)
{
    const uint8_t key[16] = "session-key-0123";
    const uint32_t klen = 16;

    /* ---- A3.0  RFC 4231 HMAC-SHA256 KAT (test case 2) — the real primitive */
    {
        uint8_t mac[32];
        vos3_hmac_sha256((const uint8_t *)"Jefe", 4,
                         (const uint8_t *)"what do ya want for nothing?", 28, mac);
        check("A3.0 HMAC-SHA256 RFC 4231 TC2 KAT",
              hexeq(mac,
                    "5bdcc146bf60754e6a042426089575c7"
                    "5a003f089d2739839dec58b964ec3843", 32));
    }

    uint8_t body[64];
    for (int i = 0; i < 64; i++) body[i] = (uint8_t)(0xA0 + i);
    uint8_t frame[64 + 32];

    /* ---- A3.1  valid authenticated frame => VALID + accepted ---- */
    uint32_t flen = build_frame(0x07, 2, 0x1234, body, 64, key, klen, frame);
    int r1 = verify_cmd_hmac_twin(0x07, 2, 0x1234, frame, flen, key, klen);
    check("A3.1 valid HMAC trailer => VOS3_HMAC_VALID",
          r1 == VOS3_HMAC_VALID && ingest_frame_accepts(r1));

    /* ---- A3.2  one corrupted body byte => INVALID + rejected ---- */
    {
        uint8_t f2[64 + 32]; memcpy(f2, frame, flen);
        f2[10] ^= 0x01;
        int r = verify_cmd_hmac_twin(0x07, 2, 0x1234, f2, flen, key, klen);
        check("A3.2 corrupted body byte => INVALID (rejected)",
              r == VOS3_HMAC_INVALID && !ingest_frame_accepts(r));
    }

    /* ---- A3.3  forged MAC under a WRONG key => INVALID ---- */
    {
        const uint8_t wrong[16] = "session-key-XXXX";
        check("A3.3 forged MAC (wrong session key) => INVALID",
              verify_cmd_hmac_twin(0x07, 2, 0x1234, frame, flen, wrong, klen)
                  == VOS3_HMAC_INVALID);
    }

    /* ---- A3.4  AAD binding: flip type / slot / tag => INVALID ---- */
    check("A3.4a wrong frame type => INVALID (AAD binding)",
          verify_cmd_hmac_twin(0x08, 2, 0x1234, frame, flen, key, klen)
              == VOS3_HMAC_INVALID);
    check("A3.4b wrong slot id => INVALID (AAD binding)",
          verify_cmd_hmac_twin(0x07, 3, 0x1234, frame, flen, key, klen)
              == VOS3_HMAC_INVALID);
    check("A3.4c wrong tag => INVALID (AAD binding)",
          verify_cmd_hmac_twin(0x07, 2, 0x9999, frame, flen, key, klen)
              == VOS3_HMAC_INVALID);

    /* ---- A3.5  empty body (payload_len == 32, body_len 0) handled ---- */
    {
        uint8_t f0[32];
        uint32_t f0len = build_frame(0x07, 2, 0x1234, body, 0, key, klen, f0);
        check("A3.5 empty-body frame (len==32) => VALID",
              f0len == 32 &&
              verify_cmd_hmac_twin(0x07, 2, 0x1234, f0, f0len, key, klen)
                  == VOS3_HMAC_VALID);
        f0[5] ^= 0x80;
        check("A3.5 empty-body frame tampered trailer => INVALID",
              verify_cmd_hmac_twin(0x07, 2, 0x1234, f0, 32, key, klen)
                  == VOS3_HMAC_INVALID);
    }

    /* ==== A3-1 FIX: three-valued return — NO_TRAILER is distinct + fail-closed ====
     * Previously payload_len<32 / key_len==0 both returned 1 (conflated with
     * VALID), an HMAC-STRIP pass-through. The refactor returns the explicit
     * VOS3_HMAC_NO_TRAILER state, and the downstream ingestion handler
     * fail-closes on it (accepts ONLY VOS3_HMAC_VALID). */
    {
        int r_trunc = verify_cmd_hmac_twin(0x07, 2, 0x1234, frame, 31, key, klen);
        check("A3-1.1 len==31 (dropped trailer) => NO_TRAILER (was VALID)",
              r_trunc == VOS3_HMAC_NO_TRAILER);
        check("A3-1.2 stripped frame is REFUSED by ingestion (not pass-through)",
              !ingest_frame_accepts(r_trunc));

        int r_nokey = verify_cmd_hmac_twin(0x07, 2, 0x1234, frame, flen, key, 0);
        check("A3-1.3 key_len==0 => NO_TRAILER",
              r_nokey == VOS3_HMAC_NO_TRAILER);
        check("A3-1.4 no-key frame REFUSED by ingestion (fail-closed)",
              !ingest_frame_accepts(r_nokey));

        check("A3-1.5 payload_len<32 => NO_TRAILER",
              verify_cmd_hmac_twin(0x07, 2, 0x1234, frame, 16, key, klen)
                  == VOS3_HMAC_NO_TRAILER);
        /* The three states are mutually distinct. */
        check("A3-1.6 VALID / INVALID / NO_TRAILER are three distinct values",
              VOS3_HMAC_VALID != VOS3_HMAC_INVALID &&
              VOS3_HMAC_VALID != VOS3_HMAC_NO_TRAILER &&
              VOS3_HMAC_INVALID != VOS3_HMAC_NO_TRAILER);
    }

    printf("\n== A3 %d passed, %d failed ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
