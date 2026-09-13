/**
 * @file test_a6_model_signature.c
 * @brief A6 — adversarial host audit of the kernel model/driver SecureBoot
 *        gate (vvfs_model_verify.c). TEST_PLAN_300 §A6.
 *
 * The `vvfs_verify_model_slot()` gate is vOS's "reject any binary that lacks a
 * valid corporate signature" primitive for model slots. It MUST fail closed on
 * every adversarial input: unsigned, partially-signed, foreign-signed (even
 * with a cryptographically VALID foreign signature), signer-spoofed,
 * digest-mismatched, or out-of-range. m3_e2e_test.c covers the happy path +
 * a few negatives; THIS file pushes the adversarial boundary the catalog asks
 * for.
 *
 * Host-runnable (the gate is pure: ed25519 + sha512, no kernel I/O):
 *     cc -O2 -Ikernel/include -o /tmp/a6 kernel/tests/audit/test_a6_model_signature.c
 *     /tmp/a6   # exit 0 iff every case passes
 *
 * Vectors (a6_vectors.h) are real Ed25519 signatures over a 32-byte digest,
 * produced with Python `cryptography` — the same OMS signer scheme as M3.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

/* Compile the real freestanding primitives + the gate directly. */
#include "../../src/crypto/sha512.c"
#include "../../src/crypto/ed25519_verify.c"
#include "../../src/fs/vvfs_model_verify.c"

#include "a6_vectors.h"

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok)
{
    printf("%-52s %s\n", name, ok ? "PASS" : "FAIL");
    if (ok) g_pass++; else g_fail++;
}

/* The gate keeps a file-scope registry + a build-time trust anchor that is
 * UNPROVISIONED by default. We provision it to the trusted vector key for the
 * cases that need an anchor, and clear it for the no-anchor case. There is no
 * public "clear" API (the anchor is build-time), so the no-anchor case runs
 * FIRST, before any vvfs_set_trusted_model_key() call mutates the static. */

int main(void)
{
    /* ---- A6.0  no trust anchor provisioned => fail closed (run first) ---- */
    /* Default build sets VOS3_TRUSTED_MODEL_KEY_PROVISIONED=0 → unprovisioned.*/
    vvfs_register_model_signature(0, A6_DIGEST, A6_TRUSTED_SIG, A6_TRUSTED_PK);
    check("A6.0 unprovisioned anchor => reject",
          vvfs_verify_model_slot(0, A6_DIGEST) != 0 &&
          vvfs_model_slot_is_verified(0) == 0);

    /* Provision the trusted anchor for the remaining cases. */
    vvfs_set_trusted_model_key(A6_TRUSTED_PK);

    /* ---- A6.1  valid trusted signature => accept + activate ---- */
    vvfs_register_model_signature(0, A6_DIGEST, A6_TRUSTED_SIG, A6_TRUSTED_PK);
    check("A6.1 valid trusted sig => accept",
          vvfs_verify_model_slot(0, A6_DIGEST) == 0 &&
          vvfs_model_slot_is_verified(0) == 1);

    /* ---- A6.2  unsigned slot (never registered) => reject ---- */
    check("A6.2 unsigned slot (slot 1) => reject",
          vvfs_verify_model_slot(1, A6_DIGEST) != 0 &&
          vvfs_model_slot_is_verified(1) == 0);

    /* ---- A6.3  foreign-signed with a VALID foreign signature => reject ----
     * The signature verifies under the FOREIGN key, but the signer key is not
     * the trusted anchor — the anchor check must reject it before/again at
     * verify. This is the "valid corporate signature from the WRONG corp". */
    vvfs_register_model_signature(2, A6_DIGEST, A6_FOREIGN_SIG, A6_FOREIGN_PK);
    check("A6.3 valid FOREIGN sig (untrusted signer) => reject",
          vvfs_verify_model_slot(2, A6_DIGEST) != 0);

    /* ---- A6.4  signer-spoof: claim the trusted pubkey but present a sig that
     * was NOT made by its private key => reject at ed25519_verify ---- */
    vvfs_register_model_signature(3, A6_DIGEST, A6_FOREIGN_SIG, A6_TRUSTED_PK);
    check("A6.4 signer-spoof (trusted pk + foreign sig) => reject",
          vvfs_verify_model_slot(3, A6_DIGEST) != 0);

    /* ---- A6.5  partially-signed: zero out half the signature => reject ---- */
    {
        uint8_t half[64];
        memcpy(half, A6_TRUSTED_SIG, 64);
        memset(half + 32, 0, 32);          /* clobber the S half */
        vvfs_register_model_signature(0, A6_DIGEST, half, A6_TRUSTED_PK);
        check("A6.5 partial/truncated signature => reject",
              vvfs_verify_model_slot(0, A6_DIGEST) != 0);
    }

    /* ---- A6.6  all-zero signature => reject ---- */
    {
        uint8_t zero[64];
        memset(zero, 0, 64);
        vvfs_register_model_signature(0, A6_DIGEST, zero, A6_TRUSTED_PK);
        check("A6.6 all-zero signature => reject",
              vvfs_verify_model_slot(0, A6_DIGEST) != 0);
    }

    /* ---- A6.7  digest mismatch: valid sig over A6_DIGEST, but a DIFFERENT
     * computed digest presented at verify (tampered model bytes) => reject -- */
    {
        uint8_t other[32];
        memcpy(other, A6_DIGEST, 32);
        other[0] ^= 0xFF;
        vvfs_register_model_signature(0, A6_DIGEST, A6_TRUSTED_SIG, A6_TRUSTED_PK);
        check("A6.7 digest mismatch (tampered model) => reject",
              vvfs_verify_model_slot(0, other) != 0 &&
              vvfs_model_slot_is_verified(0) == 0);
    }

    /* ---- A6.8  out-of-range slot id => INVAL on both register and verify -- */
    check("A6.8 register out-of-range slot => INVAL",
          vvfs_register_model_signature(VVFS_MAX_MOUNTS, A6_DIGEST,
                                        A6_TRUSTED_SIG, A6_TRUSTED_PK) != 0);
    check("A6.8 verify out-of-range slot => INVAL",
          vvfs_verify_model_slot(VVFS_MAX_MOUNTS, A6_DIGEST) != 0);

    /* ---- A6.9  re-register clears a prior verified flag (anti-TOCTOU) ----
     * Verify a good slot (verified=1), then re-register a bad sig over it:
     * the verified flag must reset to 0 immediately, and re-verify reject. */
    vvfs_register_model_signature(0, A6_DIGEST, A6_TRUSTED_SIG, A6_TRUSTED_PK);
    (void)vvfs_verify_model_slot(0, A6_DIGEST);          /* verified=1 */
    {
        uint8_t zero[64]; memset(zero, 0, 64);
        vvfs_register_model_signature(0, A6_DIGEST, zero, A6_TRUSTED_PK);
        check("A6.9 re-register resets verified flag (anti-TOCTOU)",
              vvfs_model_slot_is_verified(0) == 0 &&
              vvfs_verify_model_slot(0, A6_DIGEST) != 0);
    }

    /* ---- A6.10 cross-slot confusion: valid in slot 0, verify slot 3 ---- */
    vvfs_register_model_signature(0, A6_DIGEST, A6_TRUSTED_SIG, A6_TRUSTED_PK);
    /* slot 3 currently holds the A6.4 spoof registration → must reject, and a
     * valid slot-0 verify must NOT bleed activation into slot 3. */
    (void)vvfs_verify_model_slot(0, A6_DIGEST);
    check("A6.10 cross-slot: slot-0 verify does not activate slot 3",
          vvfs_model_slot_is_verified(3) == 0);

    printf("\n== A6 %d passed, %d failed ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
