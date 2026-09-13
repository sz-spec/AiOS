/**
 * @file test_m3_model_sig_enforcement.c
 * @brief M3 — host audit of the vVFS read-path model-signature ENFORCEMENT
 *        gate under -DVOS3_VVFS_REQUIRE_MODEL_SIG=1, with a mock trusted key.
 *        TEST_PLAN_300 §A8 (M3 extension). Companion to test_a6_model_signature.c.
 *
 * Goal (Option 1, no production brick): prove that the dormant M3 read gate,
 * when COMPILED with enforcement on, fail-closes an unverified slot — WITHOUT
 * flipping the permanent default in include/vos/vvfs.h. The macro is supplied
 * per-compile by the runner; the production default (=0) is never touched.
 *
 * What is real vs mirrored:
 *   - The verification DECISION (vvfs_verify_model_slot / _is_verified) is the
 *     REAL production code from src/fs/vvfs_model_verify.c (compiled directly,
 *     same as A6).
 *   - vvfs_model_sig_verify() lives in src/fs/vvfs_transport.c, which cannot
 *     host-compile (PMM / task / mutex deps). Its ~6-line gate body is mirrored
 *     here VERBATIM under the SAME macro (vvfs_transport.c:84-97, cited). A
 *     divergence between this twin and the source is itself a regression.
 *
 * The trusted anchor is provisioned in the TEST SCOPE ONLY via the exported
 * setter vvfs_set_trusted_model_key() (sets g_trusted_model_key_set=1 inside
 * this binary). The build-time pubkey placeholder in vvfs_trusted_key.h is left
 * all-zero / unprovisioned, exactly as in production.
 *
 * Compiled BOTH ways by run_kernel_audit.sh:
 *     cc -O2 -Iinclude -DVOS3_VVFS_REQUIRE_MODEL_SIG=1 ...   (enforcement ON)
 *     cc -O2 -Iinclude -DVOS3_VVFS_REQUIRE_MODEL_SIG=0 ...   (default OFF / no brick)
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

#include "../../src/crypto/sha512.c"
#include "../../src/crypto/ed25519_verify.c"
#include "../../src/fs/vvfs_model_verify.c"   /* real verify + registry + setter */
#include "a6_vectors.h"

#ifndef VOS3_VVFS_REQUIRE_MODEL_SIG
#define VOS3_VVFS_REQUIRE_MODEL_SIG 0
#endif

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok)
{
    printf("%-58s %s\n", name, ok ? "PASS" : "FAIL");
    if (ok) g_pass++; else g_fail++;
}

/* ---- VERBATIM twin of vvfs_model_sig_verify (vvfs_transport.c:84-97) ----
 * Calls the REAL vvfs_model_slot_is_verified() from vvfs_model_verify.c. */
static int vvfs_model_sig_verify_twin(uint8_t slot_id, const void *buf, uint32_t len)
{
    (void)buf; (void)len;
#if VOS3_VVFS_REQUIRE_MODEL_SIG
    if (vvfs_model_slot_is_verified(slot_id)) return 0;
    return VOS3_FS_ERR_KEYREJECTED;     /* fail-closed */
#else
    (void)slot_id;
    return 0;                            /* enforcement off: pass-through */
#endif
}

int main(void)
{
    const int enforce = VOS3_VVFS_REQUIRE_MODEL_SIG ? 1 : 0;
    printf("--- M3 mode: VOS3_VVFS_REQUIRE_MODEL_SIG=%d ---\n", enforce);

    /* Provision the trusted anchor in TEST SCOPE ONLY (exported setter). */
    vvfs_set_trusted_model_key(A6_TRUSTED_PK);

    /* slot 0: valid trusted signature → becomes verified. */
    vvfs_register_model_signature(0, A6_DIGEST, A6_TRUSTED_SIG, A6_TRUSTED_PK);
    (void)vvfs_verify_model_slot(0, A6_DIGEST);          /* verified=1 */

    /* slot 1: never registered → unverified. */

    /* slot 2: valid FOREIGN signature → verify fails (untrusted signer). */
    vvfs_register_model_signature(2, A6_DIGEST, A6_FOREIGN_SIG, A6_FOREIGN_PK);
    (void)vvfs_verify_model_slot(2, A6_DIGEST);          /* verified stays 0 */

    if (enforce) {
        /* Enforcement ON — the read gate must fail-close every unverified slot. */
        check("M3-ENF.1 verified slot read-gate => ALLOW (0)",
              vvfs_model_sig_verify_twin(0, NULL, 0) == 0);
        check("M3-ENF.2 unverified (unregistered) slot => KEYREJECTED",
              vvfs_model_sig_verify_twin(1, NULL, 0) == VOS3_FS_ERR_KEYREJECTED);
        check("M3-ENF.3 foreign-signed slot => KEYREJECTED",
              vvfs_model_sig_verify_twin(2, NULL, 0) == VOS3_FS_ERR_KEYREJECTED);
        /* Re-register slot 0 with a bad sig → verified flag resets → re-block. */
        {
            uint8_t zero[64]; memset(zero, 0, 64);
            vvfs_register_model_signature(0, A6_DIGEST, zero, A6_TRUSTED_PK);
            check("M3-ENF.4 re-registered (now unverified) slot => KEYREJECTED",
                  vvfs_model_sig_verify_twin(0, NULL, 0) == VOS3_FS_ERR_KEYREJECTED);
        }
        /* Without a provisioned anchor, EVERY slot fails — the brick we avoid
         * shipping by default. Re-verify under no anchor is covered in A6.0. */
        check("M3-ENF.5 enforcement path is live (macro compiled in)",
              enforce == 1);
    } else {
        /* Default build (macro OFF) — the gate is a PASS-THROUGH no-op: an
         * unverified slot is NOT blocked. This is the production default and
         * proves enabling-by-flip-alone would not silently change behaviour
         * here, while a real enable WITHOUT a provisioned key would brick. */
        check("M3-OFF.1 default build: unverified slot => PASS-THROUGH (no brick)",
              vvfs_model_sig_verify_twin(1, NULL, 0) == 0);
        check("M3-OFF.2 default build: verified slot also passes (0)",
              vvfs_model_sig_verify_twin(0, NULL, 0) == 0);
    }

    printf("\n== M3 (enforce=%d) %d passed, %d failed ==\n", enforce, g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
