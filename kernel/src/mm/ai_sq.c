/**
 * @file ai_sq.c
 * @brief VOS3 AI Guard — Submission Queue, Completion Ring, Doorbell Matrix
 *
 * Task 4.1: Split from ai_guard.c. Contains:
 *   - Completion Ring (cring_init, cring_post)
 *   - Submission Queue (sq_init, sq_post, sq_post_warp, sq_poll, sq_status)
 *   - Kinetic fill work
 *   - AVX2 vectorized sentinel validation
 *   - Doorbell matrix (init, ring, clear, check, drain)
 *   - SIMD memcpy dispatch (AVX-512, AVX2, ERMS)
 *   - Memcpy benchmark
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "ai_guard_internal.h"

/* ============================================================================
 * SQ Globals — defined here, declared extern in ai_guard_internal.h
 * ============================================================================ */

volatile vos3_sq_header_t *g_sq_hdr = NULL;
volatile vos3_sq_entry_t  *g_sq_entries = NULL;
uint64_t g_sq_processed = 0;
uint64_t g_sq_sentinel_rejects = 0;
volatile uint32_t g_validation_bitmask = 0;
uint32_t g_sntl_entropy_salt = 0;

/* CQ Globals */
volatile vos3_cring_header_t *g_cring_hdr = NULL;
volatile vos3_cring_entry_t  *g_cring_entries = NULL;
uint16_t g_cring_frame_seq = 0;

/* Doorbell */
volatile vos3_doorbell_matrix_t *g_doorbell = NULL;

/* ============================================================================
 * SIMD Feature Globals — defined here, declared extern in ai_guard_internal.h
 * ============================================================================ */

int g_cpu_has_avx2    = 0;
int g_cpu_has_avx512f = 0;
int g_memcpy_path     = 0;  /* 0=ERMS, 1=AVX2, 2=AVX-512 */

/* ============================================================================
 * PHASE 4.4: UNIVERSAL HIGH-SPEED PIPELINE — CPUID-DISPATCHED MEMCPY
 * ============================================================================ */

/**
 * @brief Detect extended SIMD features for memcpy dispatch (Phase 4.4)
 */
void ai_detect_simd_features(void)
{
    uint32_t eax, ebx, ecx, edx;

    /* CPUID leaf 7, sub 0: AVX2 (EBX bit 5), AVX-512F (EBX bit 16) */
    vos3_cpuid(VOS3_CPUID_EXTENDED_FEAT, 0, &eax, &ebx, &ecx, &edx);
    g_cpu_has_avx2    = (ebx & VOS3_CPU_EXT7_AVX2)    ? 1 : 0;
    g_cpu_has_avx512f = (ebx & VOS3_CPU_EXT7_AVX512F) ? 1 : 0;

    /* Also need OS XSAVE support (CR0.TS=0, XCR0 bits 1-2 set for AVX) */
    vos3_cpuid(VOS3_CPUID_FEATURES, 0, &eax, &ebx, &ecx, &edx);
    int os_xsave = (ecx & (1U << 27)) ? 1 : 0;  /* OSXSAVE */

    if (!os_xsave) {
        g_cpu_has_avx2 = 0;
        g_cpu_has_avx512f = 0;
    }

    if (g_cpu_has_avx512f) {
        g_memcpy_path = 2;
    } else if (g_cpu_has_avx2) {
        g_memcpy_path = 1;
    } else {
        g_memcpy_path = 0;
    }

    const char *tier = g_memcpy_path == 2 ? "ELITE TIER" :
                       g_memcpy_path == 1 ? "PRO TIER"   : "LEGACY TIER";

    VOS3_INFO("[AI-GUARD] ****************************************************");
    VOS3_INFO("[AI-GUARD] Processor Tier Detected: %s", tier);
    VOS3_INFO("[AI-GUARD] ****************************************************");
    VOS3_INFO("[AI-GUARD] SIMD: AVX2=%d AVX-512F=%d OSXSAVE=%d path=%s",
              g_cpu_has_avx2, g_cpu_has_avx512f, os_xsave,
              vos3_memcpy_path_name());
}

/**
 * @brief AVX-512 memcpy: 64 bytes per ZMM register, unrolled 4x = 256 bytes/iter
 */
static void memcpy_avx512(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;

    while (len >= 256) {
        __asm__ volatile(
            "vmovdqu64 0x00(%1), %%zmm0\n\t"
            "vmovdqu64 0x40(%1), %%zmm1\n\t"
            "vmovdqu64 0x80(%1), %%zmm2\n\t"
            "vmovdqu64 0xC0(%1), %%zmm3\n\t"
            "vmovdqu64 %%zmm0, 0x00(%0)\n\t"
            "vmovdqu64 %%zmm1, 0x40(%0)\n\t"
            "vmovdqu64 %%zmm2, 0x80(%0)\n\t"
            "vmovdqu64 %%zmm3, 0xC0(%0)\n\t"
            :: "r"(d), "r"(s)
            : "memory"
        );
        d += 256; s += 256; len -= 256;
    }

    if (len > 0) {
        __asm__ volatile(
            "rep movsb"
            : "+D"(d), "+S"(s), "+c"(len)
            :: "memory"
        );
    }
}

/**
 * @brief AVX2 memcpy: 32 bytes per YMM register, unrolled 4x = 128 bytes/iter
 */
static void memcpy_avx2(void *dst, const void *src, size_t len)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;

    while (len >= 128) {
        __asm__ volatile(
            "vmovdqu 0x00(%1), %%ymm0\n\t"
            "vmovdqu 0x20(%1), %%ymm1\n\t"
            "vmovdqu 0x40(%1), %%ymm2\n\t"
            "vmovdqu 0x60(%1), %%ymm3\n\t"
            "vmovdqu %%ymm0, 0x00(%0)\n\t"
            "vmovdqu %%ymm1, 0x20(%0)\n\t"
            "vmovdqu %%ymm2, 0x40(%0)\n\t"
            "vmovdqu %%ymm3, 0x60(%0)\n\t"
            :: "r"(d), "r"(s)
            : "memory"
        );
        d += 128; s += 128; len -= 128;
    }

    if (len > 0) {
        __asm__ volatile(
            "rep movsb"
            : "+D"(d), "+S"(s), "+c"(len)
            :: "memory"
        );
    }
}

/**
 * @brief ERMS fallback: Enhanced REP MOVSB (available on all modern x86-64)
 */
static void memcpy_erms(void *dst, const void *src, size_t len)
{
    __asm__ volatile(
        "rep movsb"
        : "+D"(dst), "+S"(src), "+c"(len)
        :: "memory"
    );
}

void vos3_memcpy_optimized(void *dst, const void *src, size_t len)
{
    if (len == 0 || dst == NULL || src == NULL) return;

    switch (g_memcpy_path) {
    case 2:  memcpy_avx512(dst, src, len); break;
    case 1:  memcpy_avx2(dst, src, len);   break;
    default: memcpy_erms(dst, src, len);   break;
    }
}

const char *vos3_memcpy_path_name(void)
{
    switch (g_memcpy_path) {
    case 2:  return "AVX-512";
    case 1:  return "AVX2";
    default: return "ERMS";
    }
}

/**
 * @brief Phase 4.4: Comparative memcpy benchmark.
 */
void vos3_memcpy_bench(uint64_t *erms_cycles, uint64_t *opt_cycles)
{
    volatile uint8_t *src = g_l1d_flush_buf;
    uint8_t *dst = g_cow_copy_buf;
    const size_t chunk = 4096;
    const uint32_t iters = 16384;

    uint32_t lo, hi;
    uint64_t t0, t1;

    /* Warm cache */
    memcpy_erms(dst, (const void *)src, chunk);

    /* Benchmark ERMS */
    __asm__ volatile("mfence\n\trdtsc" : "=a"(lo), "=d"(hi));
    t0 = ((uint64_t)hi << 32) | lo;
    for (uint32_t i = 0; i < iters; i++) {
        memcpy_erms(dst, (const void *)src, chunk);
    }
    __asm__ volatile("mfence\n\trdtsc" : "=a"(lo), "=d"(hi));
    t1 = ((uint64_t)hi << 32) | lo;
    *erms_cycles = t1 - t0;

    /* Benchmark detected optimal path */
    __asm__ volatile("mfence\n\trdtsc" : "=a"(lo), "=d"(hi));
    t0 = ((uint64_t)hi << 32) | lo;
    for (uint32_t i = 0; i < iters; i++) {
        vos3_memcpy_optimized(dst, (const void *)src, chunk);
    }
    __asm__ volatile("mfence\n\trdtsc" : "=a"(lo), "=d"(hi));
    t1 = ((uint64_t)hi << 32) | lo;
    *opt_cycles = t1 - t0;
}

/* ============================================================================
 * PHASE 4.4: COMPLETION RING — Asynchronous Notification via Heartbeat Page
 * ============================================================================ */

void cring_init(void)
{
    if (g_heartbeat_ptr == NULL) return;

    uint8_t *page = (uint8_t *)g_heartbeat_ptr;
    g_cring_hdr     = (volatile vos3_cring_header_t *)(page + VOS3_CRING_OFFSET);
    g_cring_entries  = (volatile vos3_cring_entry_t *)(page + VOS3_CRING_OFFSET +
                                                        sizeof(vos3_cring_header_t));
    g_cring_hdr->head = 0;
    g_cring_hdr->tail = 0;
    g_cring_frame_seq = 0;

    VOS3_INFO("[AI-GUARD] Completion Ring: %u entries at heartbeat+%u",
              VOS3_CRING_ENTRIES, VOS3_CRING_OFFSET);
}

void vos3_cring_post(uint8_t slot_id, uint8_t flags, uint16_t frame_seq)
{
    if (g_cring_hdr == NULL) return;

    volatile uint32_t *head_ptr = (volatile uint32_t *)((char *)g_cring_hdr + __builtin_offsetof(vos3_cring_header_t, head));
    uint32_t head = __atomic_fetch_add(head_ptr, 1, __ATOMIC_ACQ_REL);
    uint32_t idx = head % VOS3_CRING_ENTRIES;

    if ((head - g_cring_hdr->tail) >= VOS3_CRING_ENTRIES) {
        flags |= 0x80;
    }

    volatile vos3_cring_entry_t *e = &g_cring_entries[idx];
    e->slot_id   = slot_id;
    e->flags     = flags;
    e->frame_seq = frame_seq;
    e->tick_lo   = (uint32_t)vos3_timer_get_ticks();

    __asm__ volatile("sfence" ::: "memory");
}

/* ============================================================================
 * PHASE 4.6: INFINITY LOOP — SUBMISSION QUEUE
 * ============================================================================ */

void vos3_sq_init(void)
{
    if (g_heartbeat_ptr == NULL) return;

    uint8_t *page = (uint8_t *)g_heartbeat_ptr;
    g_sq_hdr     = (volatile vos3_sq_header_t *)(page + VOS3_SQ_OFFSET);
    g_sq_entries = (volatile vos3_sq_entry_t *)(page + VOS3_SQ_OFFSET +
                                                 sizeof(vos3_sq_header_t));
    g_sq_hdr->head     = 0;
    g_sq_hdr->tail     = 0;
    g_sq_hdr->sentinel = VOS3_SNTL;
    g_sq_hdr->reserved = 0;
    g_sq_processed     = 0;

    VOS3_INFO("[AI-GUARD] Submission Queue: %u entries at heartbeat+%u (SNTL=0x%08X)",
              VOS3_SQ_ENTRIES, VOS3_SQ_OFFSET, VOS3_SNTL);
}

int vos3_sq_post(uint8_t slot_id, uint8_t cmd, uint16_t length,
                  uint64_t offset, uint64_t tag)
{
    if (g_sq_hdr == NULL) return -1;

    uint32_t tail = g_sq_hdr->tail;
    uint32_t head = g_sq_hdr->head;

    if ((tail - head) >= VOS3_SQ_ENTRIES) {
        return -12;
    }

    uint32_t idx = tail % VOS3_SQ_ENTRIES;
    volatile vos3_sq_entry_t *e = &g_sq_entries[idx];

    /* Triple-Fence Seal */
    e->slot_id      = slot_id;
    e->cmd          = cmd;
    e->length       = length;
    e->offset       = offset;
    e->payload_addr = 0;
    e->tag          = tag;
    __asm__ volatile("sfence" ::: "memory");
    e->sentinel     = VOS3_SNTL ^ (uint32_t)(tag) ^ g_sntl_entropy_salt;
    __asm__ volatile("sfence" ::: "memory");

    if (g_cpu_has_clflushopt) {
        __asm__ volatile("clflushopt (%0)" :: "r"((uintptr_t)e) : "memory");
    } else {
        __asm__ volatile("clflush (%0)" :: "r"((uintptr_t)e) : "memory");
    }

    g_sq_hdr->tail = tail + 1;
    __asm__ volatile("sfence" ::: "memory");

    vos3_doorbell_ring(slot_id);

    return 0;
}

int vos3_sq_post_warp(uint8_t slot_id, uint8_t cmd, uint16_t length,
                       uint64_t offset, uint64_t tag, uint64_t payload_addr)
{
    if (g_sq_hdr == NULL) return -1;

    uint32_t tail = g_sq_hdr->tail;
    uint32_t head = g_sq_hdr->head;

    if ((tail - head) >= VOS3_SQ_ENTRIES) {
        return -12;
    }

    uint32_t idx = tail % VOS3_SQ_ENTRIES;
    volatile vos3_sq_entry_t *e = &g_sq_entries[idx];

    e->slot_id      = slot_id;
    e->cmd          = cmd;
    e->length       = length;
    e->offset       = offset;
    e->payload_addr = payload_addr;
    e->tag          = tag;
    __asm__ volatile("sfence" ::: "memory");
    e->sentinel     = VOS3_SNTL ^ (uint32_t)(tag) ^ g_sntl_entropy_salt;
    __asm__ volatile("sfence" ::: "memory");

    if (g_cpu_has_clflushopt) {
        __asm__ volatile("clflushopt (%0)" :: "r"((uintptr_t)e) : "memory");
    } else {
        __asm__ volatile("clflush (%0)" :: "r"((uintptr_t)e) : "memory");
    }

    g_sq_hdr->tail = tail + 1;
    __asm__ volatile("sfence" ::: "memory");

    vos3_doorbell_ring(slot_id);

    return 0;
}

uint32_t vos3_sq_poll(void)
{
    if (g_sq_hdr == NULL) return 0;

    uint32_t processed = 0;
    uint32_t head = g_sq_hdr->head;
    uint32_t tail;

    tail = __atomic_load_n((volatile uint32_t *)((uintptr_t)&g_sq_hdr->tail),
                           __ATOMIC_ACQUIRE);

    uint32_t depth = tail - head;
    int silent_cq = (depth > 24U) ? 1 : 0;

    while (head != tail) {
        uint32_t idx = head % VOS3_SQ_ENTRIES;
        volatile vos3_sq_entry_t *ve = &g_sq_entries[idx];

        /* Atomic SQ Shadowing (Anti-TOCTOU) */
        vos3_sq_entry_t local_entry __attribute__((aligned(32)));
        if (g_cpu_has_avx2) {
            __asm__ volatile(
                "vmovdqu (%1), %%ymm0\n\t"
                "vmovdqa %%ymm0, (%0)\n\t"
                "vzeroupper\n\t"
                : : "r"(&local_entry), "r"(ve) : "memory"
            );
        } else {
            local_entry.sentinel    = ve->sentinel;
            local_entry.slot_id     = ve->slot_id;
            local_entry.cmd         = ve->cmd;
            local_entry.length      = ve->length;
            local_entry.offset      = ve->offset;
            local_entry.payload_addr = ve->payload_addr;
            local_entry.tag         = ve->tag;
        }
        __asm__ volatile("lfence" ::: "memory");
        vos3_sq_entry_t *e = &local_entry;

        /* Phase 4.9b-U: Parallel Dispatch Handover */
        int pre_validated = 0;
        {
            uint32_t vmask = __atomic_load_n(&g_validation_bitmask, __ATOMIC_ACQUIRE);
            if (vmask & (1U << idx)) {
                __atomic_fetch_and(&g_validation_bitmask, ~(1U << idx), __ATOMIC_RELEASE);
                pre_validated = 1;
            }
        }

        if (!pre_validated) {
            uint32_t expected_sntl = VOS3_SNTL ^ (uint32_t)(e->tag) ^ g_sntl_entropy_salt;
            if (e->sentinel != expected_sntl) {
                VOS3_WARN("[SQ] Sentinel mismatch at idx=%u: 0x%08X (expected 0x%08X, tag=0x%llX)",
                          idx, e->sentinel, expected_sntl, (unsigned long long)e->tag);
                g_sq_sentinel_rejects++;
                head++;
                continue;
            }
        }

        /* Phase Omega-Ghost: cpuid Seal */
        {
            uint32_t _a, _b, _c, _d;
            __asm__ volatile("cpuid"
                : "=a"(_a), "=b"(_b), "=c"(_c), "=d"(_d)
                : "a"(0) : "memory");
        }

        /* Hardware Entropy Refresh */
        if ((processed & 0x7FU) == 0x7FU) {
            g_sntl_entropy_salt = vos3_entropy_get_u32();
        }

        /* Dispatch SQ command */
        switch (e->cmd) {
        case VOS3_SQ_CMD_DATA: {
            uint8_t sid = e->slot_id;
            if (sid < VOS3_MODEL_SLOT_MAX && (__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << sid))) {
                vos3_ai_model_slot_t *slot = &g_model_slots[sid];
                if (slot->status == VOS3_SLOT_STREAMING && e->length > 0) {
                    void *addr = vos3_ai_slot_write_addr(sid, (size_t)e->offset);
                    if (addr != NULL) {
                        slot->rolling_hash = vos3_xxh3_update(
                            slot->rolling_hash, addr, e->length);
                        slot->crc32c = vos3_crc32c(
                            slot->crc32c, addr, e->length);
                        slot->offset += e->length;
                    }
                }
            }
            if (!silent_cq && (processed & 3U) == 3U) {
                vos3_cring_post(e->slot_id, 0x01, (uint16_t)(e->tag & 0xFFFF));
            }
            break;
        }
        case VOS3_SQ_CMD_WARP_DATA: {
            uint8_t sid = e->slot_id;
            if (sid < VOS3_MODEL_SLOT_MAX && (__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << sid))) {
                vos3_ai_model_slot_t *slot = &g_model_slots[sid];
                if (slot->status == VOS3_SLOT_STREAMING && e->length > 0) {
                    void *src = vos3_ivshmem_zone_base_unchecked(sid);
                    if (src != NULL) {
                        src = (void *)((uintptr_t)src + (size_t)e->payload_addr);
                        void *dst = vos3_ai_slot_write_addr(sid, (size_t)e->offset);
                        if (dst != NULL) {
                            __asm__ volatile("prefetchw (%0)" :: "r"(dst));

                            /* Predictive Prefetch Engine */
                            {
                                size_t next_off = (size_t)e->offset + (size_t)e->length;
                                size_t next_src_off = (size_t)e->payload_addr + (size_t)e->length;
                                void *next_dst = vos3_ai_slot_write_addr(sid, next_off);
                                void *zone_base = vos3_ivshmem_zone_base_unchecked(sid);
                                if (next_dst != NULL) {
                                    __builtin_prefetch(next_dst, 1, 1);
                                    __builtin_prefetch((char *)next_dst + 64, 1, 1);
                                    __builtin_prefetch((char *)next_dst + 128, 1, 1);
                                    __builtin_prefetch((char *)next_dst + 192, 1, 1);
                                }
                                if (zone_base != NULL) {
                                    void *next_src = (void *)((uintptr_t)zone_base + next_src_off);
                                    __builtin_prefetch(next_src, 0, 1);
                                    __builtin_prefetch((char *)next_src + 64, 0, 1);
                                    __builtin_prefetch((char *)next_src + 128, 0, 1);
                                    __builtin_prefetch((char *)next_src + 192, 0, 1);
                                }
                            }

                            vos3_memcpy_optimized(dst, src, e->length);
                            __asm__ volatile("sfence" ::: "memory");
                            slot->rolling_hash = vos3_xxh3_update(
                                slot->rolling_hash, dst, e->length);
                            slot->crc32c = vos3_crc32c(
                                slot->crc32c, dst, e->length);
                            slot->offset += e->length;
                            if (g_cpu_has_clflushopt) {
                                for (size_t cl = 0; cl < e->length; cl += 64) {
                                    __asm__ volatile("clflushopt (%0)" :: "r"((uintptr_t)src + cl) : "memory");
                                }
                            }
                        }
                    }
                }
            }
            if (!silent_cq && (processed & 3U) == 3U) {
                vos3_cring_post(e->slot_id, 0x04, (uint16_t)(e->tag & 0xFFFF));
            }
            break;
        }
        case VOS3_SQ_CMD_SYNC:
        case VOS3_SQ_CMD_FINISH:
            break;
        case VOS3_SQ_CMD_NOP:
            break;
        default:
            break;
        }

        processed++;
        head++;
    }

    if (processed > 0 && (processed & 3U) != 0) {
        vos3_cring_post(0xFF, 0x01, (uint16_t)(processed & 0xFFFF));
    }

    if (processed > 0) {
        __asm__ volatile("sfence" ::: "memory");
        g_sq_hdr->head = head;
        g_sq_processed += processed;
    }

    return processed;
}

void vos3_sq_status(uint32_t *head_out, uint32_t *tail_out)
{
    if (g_sq_hdr == NULL) {
        if (head_out) *head_out = 0;
        if (tail_out) *tail_out = 0;
        return;
    }
    if (head_out) *head_out = g_sq_hdr->head;
    if (tail_out) *tail_out = g_sq_hdr->tail;
}

void vos3_sq_security_stats(uint64_t *processed_out, uint64_t *sentinel_rejects_out)
{
    if (processed_out) *processed_out = g_sq_processed;
    if (sentinel_rejects_out) *sentinel_rejects_out = g_sq_sentinel_rejects;
}

/* ============================================================================
 * PHASE 4.9b-K: KINETIC FILL WORK — Productive Jitter-Work Engine
 * ============================================================================ */

uint32_t vos3_kinetic_fill_work(uint64_t target_cycles)
{
    if (target_cycles == 0) return 0;

    uint64_t start_tsc = vos3_rdtsc();
    uint32_t total_scrubbed = 0;
    uint8_t  slot_round = 0;

    while ((vos3_rdtsc() - start_tsc) < target_cycles) {
        uint8_t sid = slot_round & (VOS3_MODEL_SLOT_MAX - 1);
        slot_round++;

        uint32_t scrubbed = vos3_ivshmem_cold_scrub_partial(sid, 16U);
        total_scrubbed += scrubbed;

        if (sid < VOS3_MODEL_SLOT_MAX) {
            vos3_ai_model_slot_t *slot = &g_model_slots[sid];
            if (slot->status == VOS3_SLOT_STREAMING) {
                void *zone = vos3_ivshmem_zone_base_unchecked(sid);
                if (zone != NULL && slot->offset > 0) {
                    void *next_src = (void *)((uintptr_t)zone + slot->offset);
                    __builtin_prefetch(next_src, 0, 1);
                    __builtin_prefetch((char *)next_src + 64, 0, 1);
                    __builtin_prefetch((char *)next_src + 128, 0, 1);
                    __builtin_prefetch((char *)next_src + 192, 0, 1);
                }
            }
        }

        if (g_cpu_has_avx512f) {
            __asm__ volatile(
                "vpxord %%zmm0, %%zmm0, %%zmm0\n\t"
                "vpxord %%zmm1, %%zmm1, %%zmm1\n\t"
                "vpxord %%zmm2, %%zmm2, %%zmm2\n\t"
                "vpxord %%zmm3, %%zmm3, %%zmm3\n\t"
                "vpxord %%zmm4, %%zmm4, %%zmm4\n\t"
                "vpxord %%zmm5, %%zmm5, %%zmm5\n\t"
                "vpxord %%zmm6, %%zmm6, %%zmm6\n\t"
                "vpxord %%zmm7, %%zmm7, %%zmm7\n\t"
                "vpxord %%zmm8, %%zmm8, %%zmm8\n\t"
                "vpxord %%zmm9, %%zmm9, %%zmm9\n\t"
                ::: "memory"
            );
        }

        if (scrubbed == 0) {
            __asm__ volatile("pause" ::: "memory");
        }
    }

    return total_scrubbed;
}

/* ============================================================================
 * PHASE 4.9b-U: AVX2 VECTORIZED SENTINEL VALIDATION
 * ============================================================================ */

uint8_t vos3_sq_validate_vectorized(uint32_t start_idx, uint32_t count, uint32_t salt)
{
    if (g_sq_entries == NULL || count == 0) return 0;
    if (count > 8) count = 8;

    uint32_t actual[8]   __attribute__((aligned(32))) = {0,0,0,0,0,0,0,0};
    uint32_t expected[8]  __attribute__((aligned(32))) = {0,0,0,0,0,0,0,0};

    for (uint32_t i = 0; i < count; i++) {
        uint32_t idx = (start_idx + i) % VOS3_SQ_ENTRIES;
        volatile vos3_sq_entry_t *ve = &g_sq_entries[idx];
        actual[i]   = ve->sentinel;
        expected[i] = VOS3_SNTL ^ (uint32_t)(ve->tag) ^ salt;
    }

    __asm__ volatile("lfence" ::: "memory");

    uint8_t valid_mask = 0;

    if (g_cpu_has_avx2 && count == 8) {
        uint64_t result_mask = 0;
        __asm__ volatile(
            "vmovdqa   (%1), %%ymm0\n\t"
            "vmovdqa   (%2), %%ymm1\n\t"
            "vpcmpeqd  %%ymm1, %%ymm0, %%ymm2\n\t"
            "vmovmskps %%ymm2, %0\n\t"
            "vzeroupper\n\t"
            : "=r"(result_mask)
            : "r"(actual), "r"(expected)
            : "memory"
        );
        valid_mask = (uint8_t)(result_mask & 0xFF);
    } else {
        for (uint32_t i = 0; i < count; i++) {
            if (actual[i] == expected[i]) {
                valid_mask |= (1U << i);
            }
        }
    }

    return valid_mask;
}

/* ============================================================================
 * PHASE 4.7: HYPERSCALE PROTOCOL — Atomic Doorbell Matrix
 * ============================================================================ */

void vos3_doorbell_init(void)
{
    if (g_heartbeat_ptr == NULL) return;

    g_doorbell = (volatile vos3_doorbell_matrix_t *)
                 ((uintptr_t)g_heartbeat_ptr + VOS3_DOORBELL_OFFSET);
    __atomic_store_n(&g_doorbell->bits, 0U, __ATOMIC_RELEASE);

    VOS3_INFO("[AI-GUARD] Doorbell matrix at heartbeat+%u", VOS3_DOORBELL_OFFSET);
}

void vos3_doorbell_ring(uint8_t slot_id)
{
    if (g_doorbell == NULL || slot_id >= 32) return;

    __atomic_fetch_or(&g_doorbell->bits, (1U << slot_id), __ATOMIC_ACQ_REL);

    __asm__ volatile("sfence" ::: "memory");
    if (g_cpu_has_clflushopt) {
        __asm__ volatile("clflushopt (%0)" :: "r"((uintptr_t)g_doorbell) : "memory");
    } else {
        __asm__ volatile("clflush (%0)" :: "r"((uintptr_t)g_doorbell) : "memory");
    }
}

void vos3_doorbell_clear(uint8_t slot_id)
{
    if (g_doorbell == NULL || slot_id >= 32) return;

    __atomic_fetch_and(&g_doorbell->bits, ~(1U << slot_id), __ATOMIC_ACQ_REL);
}

uint32_t vos3_doorbell_check(void)
{
    if (g_doorbell == NULL) return 0;

    return __atomic_load_n(&g_doorbell->bits, __ATOMIC_ACQUIRE);
}

uint32_t vos3_doorbell_drain(void)
{
    if (g_doorbell == NULL) return 0;

    return __atomic_exchange_n(&g_doorbell->bits, 0U, __ATOMIC_ACQ_REL);
}
