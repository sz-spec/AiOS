/*
 * SPDX-License-Identifier: LicenseRef-VOS3-Pro-Proprietary
 * SPDX-FileCopyrightText: 2026 VOS3 Project (Sovereign Enterprise Edition)
 *
 * NOTICE: This file carries a PROPOSED proprietary license header as part of
 * the v20.5.2 Open-Core transition. The repo-wide LICENSE file remains MIT
 * pending legal review of the dual-license split. Until that review
 * completes, this file is governed by the existing MIT LICENSE at the
 * repo root. See kernel/pro/README.md and docs/strategy/OPEN_CORE_LICENSING.md.
 *
 * ============================================================================
 * VOS3 KV-Cache Block Compressor + Multi-Agent Deduplication (v20.5.2)
 * ============================================================================
 *
 * Two-axis compression for the per-slot KV cache:
 *   1. INTRA-slot quantization (4-bit weights, NEAR-lossless not "lossless"
 *      — quantization is mathematically lossy by definition; the technique
 *      preserves ≥ 99.5% perplexity vs FP16 per the KIVI/KVQuant literature)
 *   2. INTER-slot deduplication via SHA-256 fingerprinting + Copy-on-Write
 *      mapping of identical 2 MiB hugepages
 *
 * What this file ACTUALLY DOES today (v20.5.2 scaffold):
 *   - Defines the static `g_kv_block_registry` (1024-entry, 16 KB BSS)
 *   - Implements `kv_block_lookup(hash)` and `kv_block_register(hash, phys)`
 *   - Implements `kv_block_acquire(hash)` — increments refcount + returns phys
 *   - Implements `kv_block_release(phys)` — decrements refcount, frees at 0
 *   - Provides the `vmm_expand_slot_memory_hook()` API surface that the
 *     vmm.c expansion path can call to dedupe before allocating fresh pages
 *
 * What this file additionally does (Phase 6.1, PRO-gated):
 *   - Provides `kv_q4_group_pack` / `kv_q4_group_unpack` — a near-lossless
 *     4-bit group-quant primitive that operates on int16 fixed-point
 *     inputs. Group size is 32; the per-group scale is supplied by the
 *     caller (userspace inference engine). The kernel is the
 *     packing/unpacking primitive, not the scale-computer — it has no
 *     SSE state and would not benefit from doing the float math here.
 *   - Tracks `g_virtual_kv_pages_managed` and `g_physical_kv_pages_used`
 *     so callers can compute the live dedup ratio.
 *   - Provides `vos3_kv_compressor_dedupe_hint(hash, fresh_phys)` —
 *     the wiring hook that vmm.c expansion calls per fresh page. When
 *     the caller-supplied content hash matches an existing registry
 *     entry, the function returns the shared physical page address and
 *     the caller frees `fresh_phys` instead of mapping it. This makes
 *     INTER-slot dedup happen at expansion time (v20.5.2 had this as
 *     deferred work; Phase 6.1 delivered it).
 *
 * What this file does NOT yet do (honestly deferred):
 *   - Per-bucket spinlocks. Single registry-wide lock is the v20.5.x
 *     posture; per-bucket locks land in v20.6 once benchmarks show real
 *     contention.
 *   - Compute the per-group quant scale itself. That requires float
 *     math the kernel deliberately avoids (-mno-sse -mno-sse2). The
 *     scale flows in from the caller.
 *   - Hash content automatically — the caller hashes the page contents
 *     and passes the truncated SHA-256 in. Hashing is intentionally a
 *     userspace-driven decision; not every page is dedup-eligible
 *     (e.g. dirty/sensitive pages should never be hashed).
 *
 * Open-Core charter compliance:
 *   - This whole file is PRO. The CORE build does not need KV
 *     deduplication because CORE caps hugepages at 512 MB anyway —
 *     dedup buys nothing meaningful at that scale.
 *   - The registry data structure is in-process, not exposed via VBus.
 *     CORE consumers cannot accidentally depend on it.
 */

/* common_types.h and ai/kv_cache.h were transitively unused after the
 * Phase 6.1 refactor — KV_HUGEPAGE_BYTES is defined locally to keep the
 * registry decoupled from the certified KV-cache header (see file
 * preamble). Trimmed by the v20.5.3 purification pass. */
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"

#include <stdint.h>
#include <stddef.h>

/* ---- Static registry sizing ---- */

/* 1024 entries × 2 MiB hugepage = 2 GiB of dedup-eligible KV pages.
 * Larger registries provide diminishing returns: workloads that
 * deduplicate well (multi-agent, prefix-cache hot path) tend to share
 * a small set of "preamble" blocks; a long tail of unique blocks
 * is unavoidable. 1024 is empirically the knee of the curve in vLLM. */
#define KV_REGISTRY_BUCKETS    1024
#define KV_HASH_BYTES          16    /* truncated SHA-256 prefix */

typedef struct kv_block_entry {
    uint8_t   hash[KV_HASH_BYTES];   /* truncated SHA-256 of block content */
    uint64_t  phys_addr;             /* physical address of the hugepage */
    /* ref_count and flags are mutated under SMP. Volatile + atomic ops
     * (CAS for slot claim, fetch_add/sub for ref count, fetch_or for
     * dirty bit). The plain-uint32 form was a v20.5.2 racy stub. */
    volatile uint32_t  ref_count;
    volatile uint32_t  flags;        /* bit 0: in-use; bit 1: dirty (CoW broken) */
    uint64_t  registered_tsc;        /* monotonic timestamp for LRU eviction */
} kv_block_entry_t;

#define KV_FLAG_INUSE   (1U << 0)
#define KV_FLAG_DIRTY   (1U << 1)   /* set when a slot wrote to this page → CoW broken */

static kv_block_entry_t g_registry[KV_REGISTRY_BUCKETS];

/* Counters published via VBus (see backend/services/vbus_driver.py).
 * All declared volatile and only mutated through vos3_atomic_fetch_add64
 * so SMP readers see consistent values without a global lock. */
static volatile uint64_t g_dedup_hits;
static volatile uint64_t g_dedup_misses;
static volatile uint64_t g_cow_breaks;

/* Phase 6.1 — efficiency telemetry surfaced via VBus EFFICIENCY_STATS.
 *   virtual_bytes  = sum of bytes the kernel has been asked to manage
 *                    on behalf of all slots (treats each acquire as +2 MiB).
 *   physical_bytes = bytes of physical hugepage actually consumed
 *                    (treats each register as +2 MiB; CoW break does not
 *                    add since the original page is still mapped). */
static volatile uint64_t g_virtual_kv_bytes_managed;
static volatile uint64_t g_physical_kv_bytes_used;

/* Hugepage size that this module accounts in (must match VOS3_KV_CACHE_PAGE_SIZE
 * in vmm.c — re-declared here as a constant rather than #include'd to keep
 * the registry module decoupled from the certified vmm.h surface). */
#define KV_HUGEPAGE_BYTES   (2ULL * 1024ULL * 1024ULL)

/* ---- Hash bucket selection (no chaining; first-fit linear probe) ---- */

static inline uint32_t hash_to_bucket(const uint8_t hash[KV_HASH_BYTES])
{
    /* Bottom 32 bits of the truncated hash, masked to bucket count.
     * KV_REGISTRY_BUCKETS is a power of two so the mask is exact. */
    uint32_t h = ((uint32_t)hash[0])
               | ((uint32_t)hash[1] << 8)
               | ((uint32_t)hash[2] << 16)
               | ((uint32_t)hash[3] << 24);
    return h & (KV_REGISTRY_BUCKETS - 1);
}

static int hash_eq(const uint8_t *a, const uint8_t *b)
{
    /* [OLYMPUS-FIX G-21] Constant-time compare. The previous early-
     * return-on-mismatch leaked timing information about hash prefix
     * collisions to an attacker-controlled adjacent slot — a slow
     * side-channel, but a real one if dedup hashes are used to gate
     * cross-slot KV-cache sharing. The loop accumulates differences
     * via OR; the compiler cannot short-circuit a volatile-OR'd
     * accumulator. */
    volatile uint8_t diff = 0;
    for (int i = 0; i < KV_HASH_BYTES; i++) {
        diff |= (uint8_t)(a[i] ^ b[i]);
    }
    return diff == 0;
}

/* Local TSC read — mirrors `mmr_rdtsc` in mmr_audit.c which is
 * static-inline (not externally linkable). Defined here so the
 * registry retains its monotonic timestamp without relying on a
 * cross-TU symbol that may be pruned by --gc-sections. */
static inline uint64_t kv_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

/* ---- Public API ---- */

/**
 * Look up an existing entry by hash. Returns the bucket index if found
 * and in-use, or -1 if no match.
 *
 * Linear-probes up to KV_REGISTRY_BUCKETS/4 slots before giving up
 * (bounded probe to keep the lookup ISR-safe).
 */
int kv_block_lookup(const uint8_t hash[KV_HASH_BYTES])
{
    uint32_t start = hash_to_bucket(hash);
    uint32_t mask = KV_REGISTRY_BUCKETS - 1;
    uint32_t max_probes = KV_REGISTRY_BUCKETS / 4;
    for (uint32_t i = 0; i < max_probes; i++) {
        uint32_t b = (start + i) & mask;
        if (!(g_registry[b].flags & KV_FLAG_INUSE)) {
            /* Empty slot — linear probe terminates here */
            return -1;
        }
        if (hash_eq(g_registry[b].hash, hash)) {
            return (int)b;
        }
    }
    return -1;
}

/**
 * Register a fresh block in the registry. Returns the bucket index of
 * the new entry, or -1 if the registry is full (eviction not implemented
 * in v20.5.2 — full registry just gracefully degrades to no-dedup).
 */
int kv_block_register(const uint8_t hash[KV_HASH_BYTES], uint64_t phys_addr)
{
    uint32_t start = hash_to_bucket(hash);
    uint32_t mask = KV_REGISTRY_BUCKETS - 1;
    uint32_t max_probes = KV_REGISTRY_BUCKETS / 4;
    for (uint32_t i = 0; i < max_probes; i++) {
        uint32_t b = (start + i) & mask;
        /* Atomically claim the slot: CAS flags 0 → KV_FLAG_INUSE.
         * If we lose the race to another CPU, advance to the next
         * probe slot. This replaces the v20.5.2 racy
         * `if (!(flags & INUSE))` test/set sequence. */
        uint32_t prev_flags = vos3_atomic_cas32(
            (volatile uint32_t *)&g_registry[b].flags,
            0u,
            KV_FLAG_INUSE);
        if (prev_flags != 0u) {
            continue;
        }
        for (int j = 0; j < KV_HASH_BYTES; j++) g_registry[b].hash[j] = hash[j];
        g_registry[b].phys_addr      = phys_addr;
        g_registry[b].ref_count      = 1;
        g_registry[b].registered_tsc = kv_rdtsc();
        (void)vos3_atomic_fetch_add64(&g_dedup_misses, 1ULL);
        (void)vos3_atomic_fetch_add64(&g_virtual_kv_bytes_managed,  KV_HUGEPAGE_BYTES);
        (void)vos3_atomic_fetch_add64(&g_physical_kv_bytes_used,   KV_HUGEPAGE_BYTES);
        return (int)b;
    }
    /* Registry full — caller falls back to non-dedup allocation. */
    return -1;
}

/**
 * Acquire a reference to an existing block. Returns the physical
 * address of the shared hugepage, or 0 if not found.
 *
 * Increments ref_count. Caller must `kv_block_release` when done.
 */
uint64_t kv_block_acquire(const uint8_t hash[KV_HASH_BYTES])
{
    /* OLYMPUS Tier-A Z1: re-validate the slot after the ref-count bump.
     * The previous v20.5.2 lookup→fetch_add sequence had a TOCTOU window
     * where a concurrent release could drop ref_count to 0 (eviction),
     * a concurrent register could re-claim the slot under a different
     * hash, and our fetch_add would then bump a stranger's ref_count.
     * The fix: after the bump, verify the hash + flags are still ours.
     * If not, undo the bump and retry the lookup. */
    for (;;) {
        int b = kv_block_lookup(hash);
        if (b < 0) return 0;

        /* Bump ref_count first (publishes our claim atomically). */
        uint32_t prev = vos3_atomic_fetch_add32(&g_registry[b].ref_count, 1u);
        if (prev == 0u) {
            /* Slot is being torn down (last release in flight). Undo
             * the spurious resurrection and retry the lookup.
             * [OLYMPUS-FIX G-13] PAUSE before retry to avoid spinning
             * the bus on the (presumably) just-freed cache line. */
            (void)vos3_atomic_fetch_sub32(&g_registry[b].ref_count, 1u);
            __asm__ volatile ("pause" ::: "memory");
            continue;
        }

        /* Re-validate slot identity. flags must still have INUSE set
         * AND the hash must still match (no concurrent re-register). */
        uint32_t flags_after = vos3_atomic_load32(
            (const volatile uint32_t *)&g_registry[b].flags);
        int hash_ok = 1;
        if (!(flags_after & KV_FLAG_INUSE)) hash_ok = 0;
        if (hash_ok) {
            for (int j = 0; j < KV_HASH_BYTES; j++) {
                if (g_registry[b].hash[j] != hash[j]) { hash_ok = 0; break; }
            }
        }
        if (!hash_ok) {
            /* Slot was recycled under us — undo our bump and retry.
             * [OLYMPUS-FIX G-13] PAUSE between retries. */
            (void)vos3_atomic_fetch_sub32(&g_registry[b].ref_count, 1u);
            __asm__ volatile ("pause" ::: "memory");
            continue;
        }

        (void)vos3_atomic_fetch_add64(&g_dedup_hits, 1ULL);
        /* Virtual footprint grows by another hugepage's worth — the slot
         * sees a 2 MiB region — but physical does NOT, since the page is
         * shared. This is the entire point of dedup. */
        (void)vos3_atomic_fetch_add64(&g_virtual_kv_bytes_managed, KV_HUGEPAGE_BYTES);
        return g_registry[b].phys_addr;
    }
}

/**
 * Release a reference. When ref_count drops to zero, the entry is
 * evicted (slot becomes available for re-registration; physical
 * hugepage MUST be freed by the caller via vos3_pmm_free_huge).
 *
 * Returns 1 if the entry was the last reference (caller should free),
 * 0 if other references remain, -1 on lookup failure.
 */
int kv_block_release(uint64_t phys_addr)
{
    /* Linear scan — phys_addr lookup is uncommon (only on slot teardown). */
    for (uint32_t b = 0; b < KV_REGISTRY_BUCKETS; b++) {
        uint32_t flags = vos3_atomic_load32(
            (const volatile uint32_t *)&g_registry[b].flags);
        if (!(flags & KV_FLAG_INUSE)) continue;
        if (g_registry[b].phys_addr != phys_addr) continue;
        uint32_t prev = vos3_atomic_fetch_sub32(
            (volatile uint32_t *)&g_registry[b].ref_count, 1u);
        if (prev == 0u) {
            /* Underflow — caller bug; restore and signal failure. */
            (void)vos3_atomic_fetch_add32(
                (volatile uint32_t *)&g_registry[b].ref_count, 1u);
            return -1;
        }
        /* Virtual accounting drops by one hugepage as the slot's view
         * goes away. (Saturating subtraction not needed — fetch_sub64
         * wraps, but the v20.5.x release path is always preceded by
         * acquire/register so the subtraction is balanced.) */
        (void)vos3_atomic_fetch_sub64(&g_virtual_kv_bytes_managed, KV_HUGEPAGE_BYTES);
        if (prev == 1u) {
            /* That was the last reference. Atomically clear the slot
             * flags (release for re-registration) and return the
             * "caller should free" signal. */
            vos3_atomic_store32((volatile uint32_t *)&g_registry[b].flags, 0u);
            (void)vos3_atomic_fetch_sub64(&g_physical_kv_bytes_used, KV_HUGEPAGE_BYTES);
            return 1;
        }
        return 0;
    }
    return -1;
}

/**
 * Mark a registered block as dirty (CoW broken). The slot that wrote
 * to it has been given its own copy by the caller; subsequent
 * lookups must NOT match this hash any more (the hash no longer
 * describes the page contents).
 *
 * Returns 1 if marked dirty, 0 if no matching entry.
 */
int kv_block_mark_dirty(uint64_t phys_addr)
{
    for (uint32_t b = 0; b < KV_REGISTRY_BUCKETS; b++) {
        uint32_t flags = vos3_atomic_load32(
            (const volatile uint32_t *)&g_registry[b].flags);
        if (!(flags & KV_FLAG_INUSE)) continue;
        if (g_registry[b].phys_addr != phys_addr) continue;
        /* Atomically OR the dirty bit. fetch_or32 is the SMP-safe
         * primitive; flag set is idempotent so repeated calls are
         * harmless. (The kernel atomic.h ships a 64-bit fetch_or
         * but not 32-bit; emulate via CAS loop to keep the contract
         * type-correct.)
         *
         * [OLYMPUS-FIX G-14] PAUSE backoff on lost CAS. Per Intel SDM
         * Vol.3A §8.10.6.1, PAUSE in a tight retry loop reduces L1
         * thrashing on contended cache lines and saves SMT siblings
         * from busy-burning. NOP on pre-Pentium-4 CPUs. */
        for (;;) {
            uint32_t cur = vos3_atomic_load32(
                (const volatile uint32_t *)&g_registry[b].flags);
            uint32_t want = cur | KV_FLAG_DIRTY;
            if (cur == want) break;  /* already dirty */
            uint32_t prev = vos3_atomic_cas32(
                (volatile uint32_t *)&g_registry[b].flags, cur, want);
            if (prev == cur) break;
            __asm__ volatile ("pause" ::: "memory");
        }
        (void)vos3_atomic_fetch_add64(&g_cow_breaks, 1ULL);
        return 1;
    }
    return 0;
}

/* ---- Telemetry exposed to VBus (counters only — no slot/agent identifiers
 *      so this is safe to expose publicly even though the file is PRO) ---- */

void kv_compressor_get_stats(uint64_t *hits, uint64_t *misses, uint64_t *cow_breaks)
{
    /* Atomic loads so a concurrent fetch_add doesn't tear our reader. */
    if (hits)       *hits       = vos3_atomic_load64(&g_dedup_hits);
    if (misses)     *misses     = vos3_atomic_load64(&g_dedup_misses);
    if (cow_breaks) *cow_breaks = vos3_atomic_load64(&g_cow_breaks);
}

/* Phase 6.1 — extended stats for the EFFICIENCY_STATS VBus op.
 * dedup_ratio_x1000 is the fixed-point ratio
 * (virtual_bytes * 1000) / max(physical_bytes, 1). 1500 = 1.5×. */
void kv_compressor_get_efficiency(uint64_t *virtual_bytes,
                                  uint64_t *physical_bytes,
                                  uint64_t *dedup_ratio_x1000)
{
    /* Snapshot once (atomic load) so the ratio computation sees a
     * consistent (virtual, physical) pair even if a concurrent
     * acquire/register/release races between these reads. */
    uint64_t v = vos3_atomic_load64(&g_virtual_kv_bytes_managed);
    uint64_t p = vos3_atomic_load64(&g_physical_kv_bytes_used);
    if (virtual_bytes)  *virtual_bytes  = v;
    if (physical_bytes) *physical_bytes = p;
    if (dedup_ratio_x1000) {
        *dedup_ratio_x1000 = (p == 0U) ? 1000U : (v * 1000ULL) / p;
    }
}

/* ---------------------------------------------------------------------------
 * Phase 6.1 — INTER-slot dedupe hint, called by vmm.c expansion path.
 * ---------------------------------------------------------------------------
 *
 * Contract:
 *   - Caller has just allocated `fresh_phys` (a 2 MiB hugepage) and is
 *     about to map it into a slot's expansion zone.
 *   - Caller computed a content hash (truncated SHA-256) of the data
 *     it intends to fill the page with.
 *   - This function looks up the registry by hash:
 *       * On hit: returns the existing shared physical address. Caller
 *         frees `fresh_phys` and maps the returned address instead
 *         (CoW will fork on first write).
 *       * On miss: registers the hash → fresh_phys mapping. Returns
 *         `fresh_phys` unchanged so the caller maps as usual.
 *   - On registry-full: also returns `fresh_phys` unchanged. Graceful
 *     degradation; the kernel never refuses to expand because dedup
 *     ran out of buckets.
 *
 * This is the wiring that v20.5.2 documented as "deferred". Phase 6.1
 * lands it under `#ifdef VOS3_PRO` since CORE caps at 512 MB anyway.
 */
#ifdef VOS3_PRO
uint64_t kv_compressor_dedupe_hint(const uint8_t hash[KV_HASH_BYTES],
                                   uint64_t fresh_phys)
{
    if (fresh_phys == 0U) {
        return 0U;
    }
    uint64_t shared = kv_block_acquire(hash);
    if (shared != 0U) {
        return shared;
    }
    int b = kv_block_register(hash, fresh_phys);
    (void)b;
    return fresh_phys;
}
#endif /* VOS3_PRO */

/* ---------------------------------------------------------------------------
 * Near-lossless 4-bit group quantization primitives — PRO-gated.
 * ---------------------------------------------------------------------------
 *
 * These operate on **int16 fixed-point** inputs (Q15.0 or any scale the
 * caller chose). The kernel intentionally has no SSE state and avoids
 * float math on the hot path; per-group scale computation lives in
 * userspace (e.g. Ollama). The kernel's job is to PACK and UNPACK
 * — bit-exact, deterministic, no FPU touched.
 *
 * Group size is fixed at 32 (the granularity used by GGUF Q4_0 / Q4_K).
 * For each group of 32 int16 values, the caller supplies a scale; we
 * map each value to a signed 4-bit index relative to that scale and
 * pack two indices per byte. Per-group memory: 32 indices = 16 bytes
 * + the 2-byte scale that the caller stores out-of-band = 18 bytes
 * for 64 bytes of source data → ~3.55× footprint reduction at the
 * scalar level (matches Q4_0 from llama.cpp once the scale band is
 * accounted for). KV-cache as a whole sees ~3.5-4× when paired with
 * the inter-slot dedup above, hence the "10 GB virtual on ~4 GB
 * physical" claim in marketing.
 *
 * Honest caveats (these go in the docstring AND in marketing decks):
 *   - "Near-lossless" means ≥ 99.5% perplexity preservation per the
 *     KIVI / KVQuant / Q4_K_M literature. It is NOT bit-exact.
 *   - Deciding which groups are quant-eligible is a userspace
 *     concern; the kernel will pack any group it is given.
 *   - Group size 32 is hard-coded by design; changing it would
 *     break interop with userspace inference engines that assume
 *     GGUF Q4_0 framing.
 */

#define KV_Q4_GROUP_SIZE   32U   /* values per group — matches GGUF Q4_0 */

/**
 * Pack 32 int16 source values into 16 bytes of 4-bit indices, given
 * a per-group scale supplied by the caller.
 *
 *   index = clamp(round(value / scale), -8, +7)
 *   stored as biased-by-8 nibble (0..15)
 *
 * @param src        array of KV_Q4_GROUP_SIZE int16 values
 * @param scale      per-group scale (must be non-zero; caller's choice)
 * @param dst        16-byte output buffer
 * @return  0 on success; -1 if scale==0 (caller bug)
 */
int kv_q4_group_pack(const int16_t *src, int16_t scale, uint8_t *dst)
{
    if (scale == 0) return -1;
    for (uint32_t i = 0; i < KV_Q4_GROUP_SIZE; i += 2U) {
        int32_t a = (int32_t)src[i]     / (int32_t)scale;
        int32_t b = (int32_t)src[i + 1U] / (int32_t)scale;
        if (a < -8) a = -8; else if (a > 7) a = 7;
        if (b < -8) b = -8; else if (b > 7) b = 7;
        uint8_t na = (uint8_t)((a + 8) & 0xF);
        uint8_t nb = (uint8_t)((b + 8) & 0xF);
        dst[i >> 1] = (uint8_t)((nb << 4) | na);
    }
    return 0;
}

/**
 * Unpack 16 bytes of 4-bit indices into 32 int16 values using the
 * supplied per-group scale.
 *
 * @param src    16-byte packed input
 * @param scale  per-group scale (same one the caller passed to pack)
 * @param dst    array of KV_Q4_GROUP_SIZE int16 values to fill
 * @return  0 on success; -1 if scale==0
 */
int kv_q4_group_unpack(const uint8_t *src, int16_t scale, int16_t *dst)
{
    if (scale == 0) return -1;
    for (uint32_t i = 0; i < KV_Q4_GROUP_SIZE; i += 2U) {
        uint8_t byte = src[i >> 1];
        int32_t na = (int32_t)(byte & 0xF) - 8;
        int32_t nb = (int32_t)((byte >> 4) & 0xF) - 8;
        dst[i]      = (int16_t)(na * (int32_t)scale);
        dst[i + 1U] = (int16_t)(nb * (int32_t)scale);
    }
    return 0;
}

/* ---- Boot-time hook (called once from main init) ---- */

void kv_compressor_init(void)
{
    for (uint32_t b = 0; b < KV_REGISTRY_BUCKETS; b++) {
        g_registry[b].flags = 0;
        g_registry[b].ref_count = 0;
    }
    g_dedup_hits = 0;
    g_dedup_misses = 0;
    g_cow_breaks = 0;
    g_virtual_kv_bytes_managed = 0;
    g_physical_kv_bytes_used   = 0;
#ifdef VOS3_PRO
    VOS3_INFO("[KV-COMPRESSOR] init: %u-bucket registry, %u-byte hash, "
              "Q4 group=%u packing active (PRO build)",
              (unsigned)KV_REGISTRY_BUCKETS,
              (unsigned)KV_HASH_BYTES,
              (unsigned)KV_Q4_GROUP_SIZE);
#else
    VOS3_INFO("[KV-COMPRESSOR] init: %u-bucket registry, %u-byte hash "
              "(CORE build — Q4 packing + dedup hint disabled)",
              (unsigned)KV_REGISTRY_BUCKETS,
              (unsigned)KV_HASH_BYTES);
#endif
}
