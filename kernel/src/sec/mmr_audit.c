/**
 * @file mmr_audit.c
 * @brief VOS3 Merkle Mountain Range — Append-Only Audit Ledger
 *
 * Implementation of an MMR over SHA-256 hashes. Each syscall (or
 * boot measurement) is an irreversible leaf node. The MMR root hash
 * is exported via the VBus MMR_ROOT command so the Python backend
 * can pin it to an external transparency log.
 *
 * Security property: once a leaf is appended, no code path in this
 * kernel can remove or reorder it without recomputing the entire tree,
 * which is detected by comparing the root against the previous pinned
 * value. The monotone leaf counter provides a second independent witness.
 *
 * @version 1.0.0
 * @date 2026-04-24
 */

#include "mmr_audit.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/console.h"
#include <stdint.h>
#include <stddef.h>
#include "tpm2.h"  /* [OLYMPUS-FIX APEX-HOME RFC-21.0.7] PCR sealing */

/* ============================================================================
 * rdtsc helper (freestanding — no libc)
 * ============================================================================ */

static inline uint64_t mmr_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

/* ============================================================================
 * Static state — no heap allocation
 * ============================================================================ */

/* MMR peak array: peak[k] holds the root of the complete binary tree
 * of height k+1, or is zeroed if that peak does not exist. */
static uint8_t  g_peaks[MMR_MAX_PEAKS][MMR_HASH_SIZE];
static uint8_t  g_peak_valid[MMR_MAX_PEAKS];   /* 1 if peak[k] is set */
static uint64_t g_leaf_count;
static uint8_t  g_initialized;

/* OLYMPUS Tier-A S2: append-after-finalize gate. When an external
 * transparency log pins the MMR root, mmr_finalize() flips this flag
 * and any subsequent append is rejected. Prevents post-pin tampering
 * by Ring-0 code that has already obtained an "audited" attestation. */
static volatile uint8_t g_finalized;

/* Scratch buffers for merge operations — avoid stack VLAs */
static uint8_t  g_merge_buf[MMR_HASH_SIZE * 2 + 8]; /* left || right || height */
static uint8_t  g_merge_out[MMR_HASH_SIZE];

/* ============================================================================
 * Internal helpers
 * ============================================================================ */

static void mmr_memzero(void *dst, size_t n)
{
    uint8_t *p = (uint8_t *)dst;
    while (n--) *p++ = 0;
}

static void mmr_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
}

/* Hash two child nodes into a parent:
 * parent = SHA-256( left[32] || right[32] || height_byte )
 * The height byte domain-separates levels, preventing second-preimage
 * attacks where an attacker crafts a node that is valid at two heights. */
static void mmr_merge(const uint8_t left[MMR_HASH_SIZE],
                      const uint8_t right[MMR_HASH_SIZE],
                      uint8_t       height,
                      uint8_t       out[MMR_HASH_SIZE])
{
    vos3_sha256_ctx_t ctx;
    mmr_memcpy(g_merge_buf,                    left,  MMR_HASH_SIZE);
    mmr_memcpy(g_merge_buf + MMR_HASH_SIZE,    right, MMR_HASH_SIZE);
    g_merge_buf[MMR_HASH_SIZE * 2] = height;
    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, g_merge_buf, MMR_HASH_SIZE * 2 + 1);
    vos3_sha256_final(&ctx, out);
}

/* ============================================================================
 * Public API
 * ============================================================================ */

void mmr_init(void)
{
    if (g_initialized) return;
    mmr_memzero(g_peaks,      sizeof(g_peaks));
    mmr_memzero(g_peak_valid, sizeof(g_peak_valid));
    g_leaf_count  = 0;
    g_initialized = 1;
    VOS3_INFO("[MMR] Merkle Mountain Range audit ledger initialized");
}

void mmr_append(const mmr_leaf_t *leaf)
{
    if (!g_initialized) mmr_init();

    /* OLYMPUS Tier-A S2: refuse to append after the ledger has been
     * finalized for external attestation. Without this gate, a Ring-0
     * attacker could post-pin tampering of the audit trail. */
    if (__atomic_load_n(&g_finalized, __ATOMIC_ACQUIRE)) {
        VOS3_ERROR("[MMR] append rejected: ledger is finalized");
        return;
    }

    /* Build the leaf hash:
     * SHA-256( timestamp[8] || syscall_nr[8] || entropy[8] || data[32] )
     * XOR the first 8 bytes with entropy to bind to hardware RNG state. */
    uint8_t leaf_hash[MMR_HASH_SIZE];
    {
        vos3_sha256_ctx_t ctx;
        vos3_sha256_init(&ctx);
        vos3_sha256_update(&ctx, &leaf->timestamp,  sizeof(leaf->timestamp));
        vos3_sha256_update(&ctx, &leaf->syscall_nr, sizeof(leaf->syscall_nr));
        vos3_sha256_update(&ctx, leaf->entropy,     sizeof(leaf->entropy));
        vos3_sha256_update(&ctx, leaf->data,        sizeof(leaf->data));
        vos3_sha256_final(&ctx, leaf_hash);
    }

    /* XOR entropy into first 8 bytes of leaf_hash (additional binding) */
    for (int i = 0; i < 8; i++)
        leaf_hash[i] ^= leaf->entropy[i];

    /* MMR append algorithm:
     * The new leaf starts at height 0. Repeatedly merge with existing
     * peak at the same height (if any) to produce a peak one level up.
     * Stop when no existing peak exists at the current height. */
    uint8_t carry[MMR_HASH_SIZE];
    mmr_memcpy(carry, leaf_hash, MMR_HASH_SIZE);

    for (uint8_t h = 0; h < MMR_MAX_PEAKS; h++) {
        if (!g_peak_valid[h]) {
            /* No peak at height h — plant carry here and stop */
            mmr_memcpy(g_peaks[h], carry, MMR_HASH_SIZE);
            g_peak_valid[h] = 1;
            break;
        }
        /* Merge existing peak[h] (left) with carry (right) → new carry */
        mmr_merge(g_peaks[h], carry, h, g_merge_out);
        mmr_memcpy(carry, g_merge_out, MMR_HASH_SIZE);
        /* Consume peak[h] */
        mmr_memzero(g_peaks[h], MMR_HASH_SIZE);
        g_peak_valid[h] = 0;
    }

    g_leaf_count++;

    /* [OLYMPUS-FIX APEX-HOME RFC-21.0.7] MMR → TPM PCR seal bridge.
     *
     * Every Nth append (N=1000), seal the current root hash into TPM
     * PCR[8]. This converts the in-memory g_finalized atomic flag from
     * "SMP-coherent" into "tamper-evident on hardware that has a TPM"
     * — once a PCR is extended with the root, un-doing the append
     * requires a TPM clear (physical presence).
     *
     * Today's reality (honest):
     *   - tpm2_extend_pcr() in kernel/src/sec/tpm2.c returns -1 when
     *     no TPM is detected (the typical home PC) OR when CRB MMIO
     *     is not yet mapped (today's stub state). Either way the
     *     attempt is logged and we continue — the append succeeds
     *     even if sealing did not.
     *   - No additional latency added on the failure path: tpm2_extend_pcr
     *     short-circuits on !g_tpm2_present.
     *   - When tpm2.c grows ACPI TPM2-table parsing + CRB MMIO map
     *     in v21.x, this same call site begins issuing real PCR
     *     extends with no further code change here.
     */
#define VOS3_MMR_PCR_SEAL_INTERVAL  1000ull
    if ((g_leaf_count % VOS3_MMR_PCR_SEAL_INTERVAL) == 0ull) {
        if (tpm2_is_present()) {
            uint8_t root[MMR_HASH_SIZE];
            mmr_root(root);
            int rc = tpm2_extend_pcr(VOS3_TPM2_PCR_MMR, root);
            if (rc != 0) {
                VOS3_WARN("[MMR] tpm2_extend_pcr(PCR[8]) returned %d at "
                          "leaf=%llu — continuing (no TPM or CRB pending)",
                          rc, (unsigned long long)g_leaf_count);
            }
        }
        /* If !tpm2_is_present the call is silently skipped; that is
         * the home-PC happy path, not a fault. */
    }
}

void mmr_root(uint8_t out[MMR_HASH_SIZE])
{
    if (!g_initialized || g_leaf_count == 0) {
        mmr_memzero(out, MMR_HASH_SIZE);
        return;
    }

    /* Bagging-the-Peaks: fold peaks from highest to lowest.
     * root = SHA-256( peak[63] || peak[62] || ... || peak[0] || leaf_count[8] )
     * Only include valid peaks. */
    vos3_sha256_ctx_t ctx;
    vos3_sha256_init(&ctx);

    int found_first = 0;
    uint8_t running[MMR_HASH_SIZE];
    mmr_memzero(running, MMR_HASH_SIZE);

    for (int h = MMR_MAX_PEAKS - 1; h >= 0; h--) {
        if (!g_peak_valid[h]) continue;
        if (!found_first) {
            mmr_memcpy(running, g_peaks[h], MMR_HASH_SIZE);
            found_first = 1;
        } else {
            /* running = SHA-256(running || peaks[h]) */
            uint8_t tmp[MMR_HASH_SIZE];
            vos3_sha256_ctx_t c2;
            vos3_sha256_init(&c2);
            vos3_sha256_update(&c2, running,     MMR_HASH_SIZE);
            vos3_sha256_update(&c2, g_peaks[h],  MMR_HASH_SIZE);
            vos3_sha256_final(&c2, tmp);
            mmr_memcpy(running, tmp, MMR_HASH_SIZE);
        }
    }

    /* Final: SHA-256(running || leaf_count) */
    vos3_sha256_init(&ctx);
    vos3_sha256_update(&ctx, running,       MMR_HASH_SIZE);
    vos3_sha256_update(&ctx, &g_leaf_count, sizeof(g_leaf_count));
    vos3_sha256_final(&ctx, out);
}

uint64_t mmr_leaf_count(void)
{
    return g_leaf_count;
}

/* OLYMPUS Tier-A S2: pin the ledger as finalized. Subsequent
 * mmr_append() calls return without modifying state. Idempotent. */
void mmr_finalize(void)
{
    __atomic_store_n(&g_finalized, 1u, __ATOMIC_RELEASE);
    VOS3_INFO("[MMR] ledger finalized at leaf_count=%llu",
              (unsigned long long)g_leaf_count);
}

int mmr_is_finalized(void)
{
    return (int)__atomic_load_n(&g_finalized, __ATOMIC_ACQUIRE);
}

void mmr_record_syscall(uint64_t syscall_nr, uint64_t arg0)
{
    mmr_leaf_t leaf;
    mmr_memzero(&leaf, sizeof(leaf));
    leaf.timestamp  = mmr_rdtsc();
    leaf.syscall_nr = syscall_nr;

    /* Bind to hardware entropy */
    uint64_t e = vos3_entropy_get_u64();
    for (int i = 0; i < 8; i++)
        leaf.entropy[i] = (uint8_t)(e >> (i * 8));

    /* Encode arg0 into data[0..7] */
    for (int i = 0; i < 8; i++)
        leaf.data[i] = (uint8_t)(arg0 >> (i * 8));

    mmr_append(&leaf);
}

void mmr_record_event(const uint8_t label_hash[MMR_HASH_SIZE])
{
    mmr_leaf_t leaf;
    mmr_memzero(&leaf, sizeof(leaf));
    leaf.timestamp  = mmr_rdtsc();
    leaf.syscall_nr = 0;  /* 0 = non-syscall event */

    uint64_t e = vos3_entropy_get_u64();
    for (int i = 0; i < 8; i++)
        leaf.entropy[i] = (uint8_t)(e >> (i * 8));

    mmr_memcpy(leaf.data, label_hash, MMR_HASH_SIZE);
    mmr_append(&leaf);
}
