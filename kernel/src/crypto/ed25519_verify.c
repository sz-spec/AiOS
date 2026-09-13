/**
 * @file ed25519_verify.c
 * @brief Ed25519 detached signature VERIFY (RFC 8032) — verify-only.
 *
 * Faithful port of the public-domain TweetNaCl `crypto_sign_open` verify path
 * (D. J. Bernstein, Bernard van Gabsteren, Peter Schwabe, Sjaak Smetsers,
 * cryptojedi.org/papers/tweetnacl). The internal SHA-512 is provided by
 * vos3_sha512() (sha512.c). No signing, no secret keys, no libc, GPR-only.
 *
 * Validated against RFC 8032 §7.1 known-answer vectors (TEST 1/2/3) plus
 * negative cases (bit-flipped signature/message/pubkey) — see
 * kernel/tests/ed25519_kat.c.
 *
 * Verification operates on public data only (signature, public key, message);
 * it has no secret-dependent branches.
 *
 * @version 1.0.0
 * @date 2026-06-07
 * @copyright TweetNaCl is public domain; this port (c) 2026 VOS3 Project, MIT.
 *
 * @note MISRA deviations are inherited from the audited TweetNaCl reference and
 *       intentionally preserved so the code can be diffed against it.
 */

#include "../../include/vos/ed25519.h"
#include "../../include/vos/sha512.h"

typedef uint8_t u8;
typedef uint64_t u64;
typedef int64_t i64;
typedef i64 gf[16];

static const gf gf0;
static const gf gf1 = {1};
static const gf D = {0x78a3, 0x1359, 0x4dca, 0x75eb, 0xd8ab, 0x4141, 0x0a4d, 0x0070,
                     0xe898, 0x7779, 0x4079, 0x8cc7, 0xfe73, 0x2b6f, 0x6cee, 0x5203};
static const gf D2 = {0xf159, 0x26b2, 0x9b94, 0xebd6, 0xb156, 0x8283, 0x149a, 0x00e0,
                      0xd130, 0xeef3, 0x80f2, 0x198e, 0xfce7, 0x56df, 0xd9dc, 0x2406};
static const gf X = {0xd51a, 0x8f25, 0x2d60, 0xc956, 0xa7b2, 0x9525, 0xc760, 0x692c,
                     0xdc5c, 0xfdd6, 0xe231, 0xc0a4, 0x53fe, 0xcd6e, 0x36d3, 0x2169};
static const gf Y = {0x6658, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666,
                     0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666};
static const gf I = {0xa0b0, 0x4a0e, 0x1b27, 0xc4ee, 0xe478, 0xad2f, 0x1806, 0x2f43,
                     0xd7a7, 0x3dfb, 0x0099, 0x2b4d, 0xdf0b, 0x4fc1, 0x2480, 0x2b83};

static int crypto_verify_32(const u8 *x, const u8 *y)
{
    unsigned int d = 0U;
    int i;
    for (i = 0; i < 32; i++) d |= (unsigned int)(x[i] ^ y[i]);
    return (int)((1U & ((d - 1U) >> 8)) - 1U); /* 0 if equal, -1 if differ */
}

static void set25519(gf r, const gf a)
{
    int i;
    for (i = 0; i < 16; i++) r[i] = a[i];
}

static void car25519(gf o)
{
    int i;
    i64 c;
    for (i = 0; i < 16; i++) {
        o[i] += (1LL << 16);
        c = o[i] >> 16;
        o[(i + 1) * (i < 15)] += c - 1 + 37 * (c - 1) * (i == 15);
        o[i] -= c << 16;
    }
}

static void sel25519(gf p, gf q, int b)
{
    i64 t, i, c = ~(b - 1);
    for (i = 0; i < 16; i++) {
        t = c & (p[i] ^ q[i]);
        p[i] ^= t;
        q[i] ^= t;
    }
}

static void pack25519(u8 *o, const gf n)
{
    int i, j, b;
    gf m, t;
    for (i = 0; i < 16; i++) t[i] = n[i];
    car25519(t);
    car25519(t);
    car25519(t);
    for (j = 0; j < 2; j++) {
        m[0] = t[0] - 0xffed;
        for (i = 1; i < 15; i++) {
            m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1);
            m[i - 1] &= 0xffff;
        }
        m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
        b = (int)((m[15] >> 16) & 1);
        m[14] &= 0xffff;
        sel25519(t, m, 1 - b);
    }
    for (i = 0; i < 16; i++) {
        o[2 * i] = t[i] & 0xff;
        o[2 * i + 1] = t[i] >> 8;
    }
}

static int neq25519(const gf a, const gf b)
{
    u8 c[32], d[32];
    pack25519(c, a);
    pack25519(d, b);
    return crypto_verify_32(c, d);
}

static u8 par25519(const gf a)
{
    u8 d[32];
    pack25519(d, a);
    return d[0] & 1;
}

static void unpack25519(gf o, const u8 *n)
{
    int i;
    for (i = 0; i < 16; i++) o[i] = n[2 * i] + ((i64)n[2 * i + 1] << 8);
    o[15] &= 0x7fff;
}

static void A(gf o, const gf a, const gf b)
{
    int i;
    for (i = 0; i < 16; i++) o[i] = a[i] + b[i];
}

static void Z(gf o, const gf a, const gf b)
{
    int i;
    for (i = 0; i < 16; i++) o[i] = a[i] - b[i];
}

static void M(gf o, const gf a, const gf b)
{
    i64 i, j, t[31];
    for (i = 0; i < 31; i++) t[i] = 0;
    for (i = 0; i < 16; i++)
        for (j = 0; j < 16; j++) t[i + j] += a[i] * b[j];
    for (i = 0; i < 15; i++) t[i] += 38 * t[i + 16];
    for (i = 0; i < 16; i++) o[i] = t[i];
    car25519(o);
    car25519(o);
}

static void S(gf o, const gf a)
{
    M(o, a, a);
}

static void inv25519(gf o, const gf i)
{
    gf c;
    int a;
    for (a = 0; a < 16; a++) c[a] = i[a];
    for (a = 253; a >= 0; a--) {
        S(c, c);
        if (a != 2 && a != 4) M(c, c, i);
    }
    for (a = 0; a < 16; a++) o[a] = c[a];
}

static void pow2523(gf o, const gf i)
{
    gf c;
    int a;
    for (a = 0; a < 16; a++) c[a] = i[a];
    for (a = 250; a >= 0; a--) {
        S(c, c);
        if (a != 1) M(c, c, i);
    }
    for (a = 0; a < 16; a++) o[a] = c[a];
}

static void add(gf p[4], gf q[4])
{
    gf a, b, c, d, t, e, f, g, h;
    Z(a, p[1], p[0]);
    Z(t, q[1], q[0]);
    M(a, a, t);
    A(b, p[0], p[1]);
    A(t, q[0], q[1]);
    M(b, b, t);
    M(c, p[3], q[3]);
    M(c, c, D2);
    M(d, p[2], q[2]);
    A(d, d, d);
    Z(e, b, a);
    Z(f, d, c);
    A(g, d, c);
    A(h, b, a);
    M(p[0], e, f);
    M(p[1], h, g);
    M(p[2], g, f);
    M(p[3], e, h);
}

static void cswap(gf p[4], gf q[4], u8 b)
{
    int i;
    for (i = 0; i < 4; i++) sel25519(p[i], q[i], (int)b);
}

static void pack(u8 *r, gf p[4])
{
    gf tx, ty, zi;
    inv25519(zi, p[2]);
    M(tx, p[0], zi);
    M(ty, p[1], zi);
    pack25519(r, ty);
    r[31] ^= par25519(tx) << 7;
}

static void scalarmult(gf p[4], gf q[4], const u8 *s)
{
    int i;
    set25519(p[0], gf0);
    set25519(p[1], gf1);
    set25519(p[2], gf1);
    set25519(p[3], gf0);
    for (i = 255; i >= 0; --i) {
        u8 b = (s[i / 8] >> (i & 7)) & 1;
        cswap(p, q, b);
        add(q, p);
        add(p, p);
        cswap(p, q, b);
    }
}

static void scalarbase(gf p[4], const u8 *s)
{
    gf q[4];
    set25519(q[0], X);
    set25519(q[1], Y);
    set25519(q[2], gf1);
    M(q[3], X, Y);
    scalarmult(p, q, s);
}

static const u64 L[32] = {0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58,
                          0xd6, 0x9c, 0xf7, 0xa2, 0xde, 0xf9, 0xde, 0x14,
                          0,    0,    0,    0,    0,    0,    0,    0,
                          0,    0,    0,    0,    0,    0,    0,    0x10};

static void modL(u8 *r, i64 x[64])
{
    i64 carry, i, j;
    for (i = 63; i >= 32; --i) {
        carry = 0;
        for (j = i - 32; j < i - 12; ++j) {
            x[j] += carry - 16 * x[i] * (i64)L[j - (i - 32)];
            carry = (x[j] + 128) >> 8;
            x[j] -= carry << 8;
        }
        x[j] += carry;
        x[i] = 0;
    }
    carry = 0;
    for (j = 0; j < 32; j++) {
        x[j] += carry - (x[31] >> 4) * (i64)L[j];
        carry = x[j] >> 8;
        x[j] &= 255;
    }
    for (j = 0; j < 32; j++) x[j] -= carry * (i64)L[j];
    for (i = 0; i < 32; i++) {
        x[i + 1] += x[i] >> 8;
        r[i] = x[i] & 255;
    }
}

static void reduce(u8 *r)
{
    i64 x[64], i;
    for (i = 0; i < 64; i++) x[i] = (i64)(u64)r[i];
    for (i = 0; i < 64; i++) r[i] = 0;
    modL(r, x);
}

static int unpackneg(gf r[4], const u8 p[32])
{
    gf t, chk, num, den, den2, den4, den6;
    set25519(r[2], gf1);
    unpack25519(r[1], p);
    S(num, r[1]);
    M(den, num, D);
    Z(num, num, r[2]);
    A(den, r[2], den);

    S(den2, den);
    S(den4, den2);
    M(den6, den4, den2);
    M(t, den6, num);
    M(t, t, den);

    pow2523(t, t);
    M(t, t, num);
    M(t, t, den);
    M(t, t, den);
    M(r[0], t, den);

    S(chk, r[0]);
    M(chk, chk, den);
    if (neq25519(chk, num)) M(r[0], r[0], I);

    S(chk, r[0]);
    M(chk, chk, den);
    if (neq25519(chk, num)) return -1;

    if (par25519(r[0]) == (p[31] >> 7)) Z(r[0], gf0, r[0]);

    M(r[3], r[0], r[1]);
    return 0;
}

/* ---------------------------------------------------------------------------
 * Phase 2 strict-verify gates (malleability + small-order hardening).
 *
 * The base TweetNaCl verify path is RFC-8032-KAT-correct for honest inputs but,
 * like upstream TweetNaCl, omits the canonical/order checks — which makes it
 * malleable (accepts S' = S + L, the CVE-2026-4115 class, demonstrated) and
 * able to admit non-canonical / small-order points (CVE-2025-15444 class). The
 * three gates below close those, per "Taming the many EdDSAs" (eprint
 * 2020/1244). They operate on public data only (constant-time form retained
 * from the libsodium reference routines).
 * ------------------------------------------------------------------------- */

/* Returns 1 iff the 32-byte little-endian scalar s is canonical (s < L). */
static int sc_is_canonical(const u8 s[32])
{
    u8 c = 0, n = 1;
    unsigned int i = 32U;
    do {
        i--;
        c |= (u8)(((unsigned int)((unsigned int)s[i] - (unsigned int)(u8)L[i]) >> 8) & n);
        n &= (u8)(((unsigned int)((unsigned int)(s[i] ^ (u8)L[i]) - 1U)) >> 8);
    } while (i != 0U);
    return c != 0;
}

/* Returns 1 iff the 32-byte point encoding has a canonical y (y < p),
 * i.e. it is NOT one of the non-canonical encodings y in [2^255-19, 2^255-1]. */
static int point_is_canonical(const u8 s[32])
{
    u8 c, d;
    unsigned int i;
    c = (u8)((s[31] & 0x7f) ^ 0x7f);
    for (i = 30U; i > 0U; i--) c |= (u8)(s[i] ^ 0xff);
    c = (u8)((((unsigned int)c) - 1U) >> 8);
    d = (u8)((0xedU - 1U - (unsigned int)s[0]) >> 8);
    return (int)(1U - ((unsigned int)(c & d) & 1U));
}

/* Returns 1 iff the decoded point q has small order (i.e. [8]q == identity). */
static int point_is_small_order(gf q[4])
{
    gf e[4];
    u8 enc[32];
    int i, id;
    for (i = 0; i < 4; i++) set25519(e[i], q[i]);
    add(e, e); /* [2]q */
    add(e, e); /* [4]q */
    add(e, e); /* [8]q */
    pack(enc, e);
    id = (enc[0] == 1);
    for (i = 1; i < 32; i++) id &= (enc[i] == 0);
    return id; /* identity encoding => q was small-order */
}

int vos3_ed25519_verify(const uint8_t sig[VOS3_ED25519_SIG_SIZE],
                        const uint8_t *msg, size_t msg_len,
                        const uint8_t pubkey[VOS3_ED25519_PUBKEY_SIZE])
{
    u8 t[32], h[64];
    gf p[4], q[4];

    /* Phase 2 gates (fail-closed, public-data only): reject non-canonical
     * public key A, non-canonical R, and over-large S (S >= L malleability). */
    if (!point_is_canonical(pubkey)) return -1;
    if (!point_is_canonical(sig)) return -1;     /* R = sig[0..32) */
    if (!sc_is_canonical(sig + 32)) return -1;   /* S = sig[32..64) ; CVE-2026-4115 */

    if (unpackneg(q, pubkey)) return -1; /* q = -A; rejects malformed pubkey */

    /* Reject small-order public keys (small-subgroup; CVE-2025-15444 class). */
    if (point_is_small_order(q)) return -1;

    /* h = SHA-512(R || A || M), streamed to avoid a large scratch buffer. */
    {
        vos3_sha512_ctx_t ctx;
        vos3_sha512_init(&ctx);
        vos3_sha512_update(&ctx, sig, 32);          /* R */
        vos3_sha512_update(&ctx, pubkey, 32);       /* A */
        vos3_sha512_update(&ctx, msg, msg_len);     /* M */
        vos3_sha512_final(&ctx, h);
    }
    reduce(h);

    scalarmult(p, q, h);     /* p = h * (-A)        */
    scalarbase(q, sig + 32); /* q = S * B           */
    add(p, q);               /* p = S*B - h*A       */
    pack(t, p);

    /* Valid iff S*B - h*A == R, i.e. packed point equals R = sig[0..32). */
    if (crypto_verify_32(sig, t)) return -1;
    return 0;
}
