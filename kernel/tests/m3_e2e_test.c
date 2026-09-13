/**
 * @file m3_e2e_test.c
 * @brief M3 end-to-end host test — provision key -> register OMS bundle ->
 *        SLOT_FINISH verify -> activation state, plus fail-closed negatives.
 *
 * Exercises the real Phase-3/4 pipeline (vvfs_model_verify.c) against a REAL
 * vector signed by Python `cryptography` Ed25519 over SHA-256(model) — the
 * infra/security/model_signer.py / OMS scheme. Run on the host:
 *
 *     cc -O2 -Ikernel/include -o /tmp/m3_e2e kernel/tests/m3_e2e_test.c && /tmp/m3_e2e
 *
 * Exit 0 iff every case passes.
 */

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

#include "../src/crypto/sha512.c"
#include "../src/crypto/ed25519_verify.c"
#include "../src/fs/vvfs_model_verify.c"

static int unhex(const char *h, uint8_t *o, int max)
{
    int n = 0;
    for (; h[0] && h[1] && n < max; h += 2, n++) {
        unsigned v;
        sscanf(h, "%2x", &v);
        o[n] = (uint8_t)v;
    }
    return n;
}

int main(void)
{
    int pass = 0, fail = 0, ok;
    uint8_t pk[32], dg[32], sig[64];
    /* Real OMS interop vector (Ed25519 over SHA-256, model_signer.py scheme). */
    unhex("03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8", pk, 32);
    unhex("d80e9b40ea1e1265fcaa3f5ef36dfe3a1c3e5269c99f141ec97cd65a9e4be3ca", dg, 32);
    unhex("fc7b1799db30c19ddb85d19fab448dceb1705f6bd8b3fca02ea261ae70d2cbe8"
          "abb01fbf10ba20dec1e01d44e29c60e82deb8aa6be8880b856aa9e4aaf36260e", sig, 64);

    /* 1. Ingest the OMS signature for slot 0 (Phase-3 transport). */
    ok = (vvfs_register_model_signature(0, dg, sig, pk) == 0);
    printf("register OMS signature (slot 0):        %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    /* 2. UNPROVISIONED trust anchor => SLOT_FINISH verify must FAIL CLOSED. */
    ok = (vvfs_verify_model_slot(0, dg) != 0) && (vvfs_model_slot_is_verified(0) == 0);
    printf("no trusted key => reject (fail-closed):  %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    /* 3. Provision the build trust anchor (here via the setter; prod = baked). */
    vvfs_set_trusted_model_key(pk);

    /* 4. SLOT_FINISH verification with matching digest => ACCEPT + activate. */
    ok = (vvfs_verify_model_slot(0, dg) == 0);
    printf("SLOT_FINISH verify (valid) => accept:    %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;
    ok = (vvfs_model_slot_is_verified(0) == 1);
    printf("read-gate activation state = verified:   %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    /* 5. Tampered model (different computed digest) => reject + de-activate. */
    uint8_t dg2[32];
    memcpy(dg2, dg, 32);
    dg2[0] ^= 0x01;
    ok = (vvfs_verify_model_slot(0, dg2) != 0) && (vvfs_model_slot_is_verified(0) == 0);
    printf("tampered model digest => reject:         %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    /* 6. Unregistered slot => reject (fail-closed). */
    ok = (vvfs_verify_model_slot(1, dg) != 0);
    printf("unregistered slot => reject:             %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    /* 7. Untrusted signer (signer != build trust anchor) => reject. */
    uint8_t other[32];
    memcpy(other, pk, 32);
    other[0] ^= 0x01;
    vvfs_register_model_signature(2, dg, sig, other);
    ok = (vvfs_verify_model_slot(2, dg) != 0);
    printf("untrusted signer key => reject:          %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    printf("\n== %d passed, %d failed ==\n", pass, fail);
    return fail ? 1 : 0;
}
