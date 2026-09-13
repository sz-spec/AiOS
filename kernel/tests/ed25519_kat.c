/**
 * @file ed25519_kat.c
 * @brief Known-Answer Tests for the M3 Ed25519 verifier + SHA-512.
 *
 * Host-runnable, self-contained correctness gate for the freestanding
 * verify-only Ed25519 (RFC 8032) and SHA-512 (FIPS 180-4) primitives that back
 * the M3 model-file SecureBoot gate. Run on the build host:
 *
 *     cc -O2 -o /tmp/ed25519_kat kernel/tests/ed25519_kat.c && /tmp/ed25519_kat
 *
 * Covers:
 *   - SHA-512 FIPS 180-4 vectors ("abc", empty).
 *   - Ed25519 RFC 8032 §7.1 TEST 1/2/3 valid signatures (must accept).
 *   - Negative cases per vector: forged signature, tampered message, wrong
 *     public key (all must reject) — proving the gate fails closed.
 *
 * Exit code 0 iff every case passes. This is the Phase-1 verification gate;
 * the kernel-integration (QEMU accept/forged/missing matrix) is a later phase.
 */

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

/* Compile the primitives directly so the KAT is self-contained on the host. */
#include "../src/crypto/sha512.c"
#include "../src/crypto/ed25519_verify.c"

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

static int eq_hex(const uint8_t *b, int n, const char *exp)
{
    char got[256];
    for (int i = 0; i < n; i++) sprintf(got + 2 * i, "%02x", b[i]);
    return strcmp(got, exp) == 0;
}

int main(void)
{
    int pass = 0, fail = 0;
    uint8_t out[64];

    /* ---- SHA-512 FIPS 180-4 ---- */
    vos3_sha512("abc", 3, out);
    int ok = eq_hex(out, 64,
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f");
    printf("SHA-512(\"abc\"): %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    vos3_sha512("", 0, out);
    ok = eq_hex(out, 64,
        "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e");
    printf("SHA-512(\"\"):    %s\n", ok ? "PASS" : "FAIL");
    ok ? pass++ : fail++;

    /* ---- Ed25519 RFC 8032 §7.1 ---- */
    struct {
        const char *pk;
        const char *msg;
        const char *sig;
    } v[] = {
        {"d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e0652249015"
         "55fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"},
        {"3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69d"
         "a085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"},
        {"fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
         "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3a"
         "c18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"},
    };

    for (int i = 0; i < 3; i++) {
        uint8_t pk[32], sig[64], msg[64];
        unhex(v[i].pk, pk, 32);
        unhex(v[i].sig, sig, 64);
        int ml = unhex(v[i].msg, msg, 64);

        ok = (vos3_ed25519_verify(sig, msg, ml, pk) == 0);
        printf("RFC8032 TEST %d valid-sig:       %s\n", i + 1, ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        uint8_t s2[64];
        memcpy(s2, sig, 64);
        s2[10] ^= 0x01;
        ok = (vos3_ed25519_verify(s2, msg, ml, pk) != 0);
        printf("RFC8032 TEST %d forged-sig reject: %s\n", i + 1, ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        if (ml > 0) {
            uint8_t m2[64];
            memcpy(m2, msg, ml);
            m2[0] ^= 0x01;
            ok = (vos3_ed25519_verify(sig, m2, ml, pk) != 0);
            printf("RFC8032 TEST %d tampered-msg rej: %s\n", i + 1, ok ? "PASS" : "FAIL");
            ok ? pass++ : fail++;
        }

        uint8_t pk2[32];
        memcpy(pk2, pk, 32);
        pk2[0] ^= 0x01;
        ok = (vos3_ed25519_verify(sig, msg, ml, pk2) != 0);
        printf("RFC8032 TEST %d wrong-key reject:  %s\n", i + 1, ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;
    }

    /* ---- Phase 2: malleability + canonical / small-order rejection ---- */
    {
        uint8_t pk[32], sig[64], msg[8];
        unhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", pk, 32);
        unhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69d"
              "a085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00", sig, 64);
        int ml = unhex("72", msg, 8);
        static const uint8_t Lq[32] = {0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58,
                                       0xd6, 0x9c, 0xf7, 0xa2, 0xde, 0xf9, 0xde, 0x14,
                                       0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x10};

        /* (a) S' = S + L must be REJECTED (CVE-2026-4115 malleability). */
        uint8_t m1[64];
        memcpy(m1, sig, 64);
        unsigned carry = 0;
        for (int i = 0; i < 32; i++) { unsigned s = m1[32 + i] + Lq[i] + carry; m1[32 + i] = s & 0xff; carry = s >> 8; }
        ok = (vos3_ed25519_verify(m1, msg, ml, pk) != 0);
        printf("Phase2 S+L malleated rejected:    %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        /* (b) S == L exactly must be REJECTED (non-canonical scalar). */
        uint8_t m2[64];
        memcpy(m2, sig, 64);
        memcpy(m2 + 32, Lq, 32);
        ok = (vos3_ed25519_verify(m2, msg, ml, pk) != 0);
        printf("Phase2 S==L rejected:             %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        /* (c) Non-canonical R (y = p encoding) must be REJECTED. */
        uint8_t m3[64];
        memcpy(m3, sig, 64);
        m3[0] = 0xed;
        for (int i = 1; i < 31; i++) m3[i] = 0xff;
        m3[31] = 0x7f;
        ok = (vos3_ed25519_verify(m3, msg, ml, pk) != 0);
        printf("Phase2 non-canonical R rejected:  %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        /* (d) Small-order public key (all-zero, order 4) must be REJECTED. */
        uint8_t z[32];
        memset(z, 0, 32);
        ok = (vos3_ed25519_verify(sig, msg, ml, z) != 0);
        printf("Phase2 small-order pk(0) rejected:%s\n", ok ? " PASS" : " FAIL");
        ok ? pass++ : fail++;

        /* (e) Identity public key {1,0,...} (order 1) must be REJECTED. */
        uint8_t one[32];
        memset(one, 0, 32);
        one[0] = 1;
        ok = (vos3_ed25519_verify(sig, msg, ml, one) != 0);
        printf("Phase2 identity pk rejected:      %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;
    }

    /* ---- Phase 4: real Python(cryptography)->kernel interop, OMS scheme ----
     * Vector generated by Ed25519 over SHA-256(model) (infra/security/
     * model_signer.py scheme): pk/digest/sig below come from the host signer.
     * This is exactly what vvfs_verify_model_slot() checks: digest-match +
     * vos3_ed25519_verify over the 32-byte SHA-256 digest. */
    {
        uint8_t pk[32], dg[32], sg[64];
        unhex("03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8", pk, 32);
        unhex("d80e9b40ea1e1265fcaa3f5ef36dfe3a1c3e5269c99f141ec97cd65a9e4be3ca", dg, 32);
        unhex("fc7b1799db30c19ddb85d19fab448dceb1705f6bd8b3fca02ea261ae70d2cbe8"
              "abb01fbf10ba20dec1e01d44e29c60e82deb8aa6be8880b856aa9e4aaf36260e", sg, 64);

        ok = (vos3_ed25519_verify(sg, dg, 32, pk) == 0);
        printf("Phase4 OMS interop (py->kernel):  %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        uint8_t dg2[32];
        memcpy(dg2, dg, 32);
        dg2[0] ^= 0x01; /* tampered model -> different digest -> reject */
        ok = (vos3_ed25519_verify(sg, dg2, 32, pk) != 0);
        printf("Phase4 tampered-digest rejected:  %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;

        uint8_t pk2[32];
        memcpy(pk2, pk, 32);
        pk2[1] ^= 0x01; /* untrusted/wrong signer key -> reject */
        ok = (vos3_ed25519_verify(sg, dg, 32, pk2) != 0);
        printf("Phase4 wrong-signer rejected:     %s\n", ok ? "PASS" : "FAIL");
        ok ? pass++ : fail++;
    }

    printf("\n== %d passed, %d failed ==\n", pass, fail);
    return fail ? 1 : 0;
}
