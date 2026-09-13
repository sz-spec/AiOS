/**
 * @file ai_pte.c
 * @brief VOS3 AI Guard — PTE Operations, CRC32C, XXH3 Hash
 *
 * Task 4.1: Split from ai_guard.c. Contains:
 *   - CPU feature detection (ai_detect_cpu_features)
 *   - XXH3-64 rolling hash
 *   - CRC32C (hardware SSE4.2 + software fallback)
 *   - PTE inversion key init
 *   - PTE invert / uninvert (CAS loops)
 *   - PTE-gated DMA protect/unprotect
 */

#include "ai_guard_internal.h"

/* ============================================================================
 * CPU Feature Flags — defined here, declared extern in ai_guard_internal.h
 * ============================================================================ */

int g_cpu_has_sse42      = 0;
int g_cpu_has_cldemote   = 0;
int g_cpu_has_invpcid    = 0;
int g_cpu_has_avx        = 0;
int g_cpu_has_ibpb       = 0;
int g_cpu_has_l3cat      = 0;
int g_cpu_has_clflushopt = 0;
int g_cpu_has_flush_l1d  = 0;  /* Cyber overlay (Stage 2): MSR_IA32_FLUSH_CMD hardware L1D flush; CPUID detection wired in Stage 3 */

/**
 * @brief Detect CPU features for Phase 4.2 hardening (called once from model_start)
 */
void ai_detect_cpu_features(void)
{
    static volatile int detected = 0;
    int expected = 0;
    if (!__atomic_compare_exchange_n(&detected, &expected, 1, 0,
                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        return;

    uint32_t eax, ebx, ecx, edx;

    /* CPUID leaf 1: SSE4.2 */
    vos3_cpuid(VOS3_CPUID_FEATURES, 0, &eax, &ebx, &ecx, &edx);
    g_cpu_has_sse42 = (ecx & VOS3_CPU_FEAT_SSE42) ? 1 : 0;

    /* CPUID leaf 1: AVX (ECX bit 28) */
    g_cpu_has_avx = (ecx & (1U << 28)) ? 1 : 0;

    /* CPUID leaf 7 sub 0: INVPCID (EBX bit 10), CLDEMOTE (ECX bit 25), IBPB (EDX bit 26) */
    vos3_cpuid(VOS3_CPUID_EXTENDED_FEAT, 0, &eax, &ebx, &ecx, &edx);
    g_cpu_has_invpcid    = (ebx & VOS3_CPU_EXT7_INVPCID)   ? 1 : 0;
    g_cpu_has_clflushopt = (ebx & (1U << 23))              ? 1 : 0;
    g_cpu_has_cldemote   = (ecx & VOS3_CPU_EXT7C_CLDEMOTE) ? 1 : 0;
    g_cpu_has_ibpb       = (edx & (1U << 26))              ? 1 : 0;

    /* CPUID leaf 0x10 sub 0: Intel RDT — L3 Cache Allocation Technology (CAT)
     * EBX bit 1 = L3 CAT supported */
    vos3_cpuid(0x10, 0, &eax, &ebx, &ecx, &edx);
    g_cpu_has_l3cat = (ebx & (1U << 1)) ? 1 : 0;

    VOS3_INFO("[AI-GUARD] CPU features: SSE4.2=%d AVX=%d INVPCID=%d CLFLUSHOPT=%d CLDEMOTE=%d IBPB=%d L3CAT=%d",
              g_cpu_has_sse42, g_cpu_has_avx, g_cpu_has_invpcid, g_cpu_has_clflushopt,
              g_cpu_has_cldemote, g_cpu_has_ibpb, g_cpu_has_l3cat);
}

/* ============================================================================
 * XXH3-64 Rolling Hash
 * ============================================================================ */

#define VOS3_XXH3_SEED        0x9E3779B97F4A7C15ULL
#define XXH3_PRIME64_1        0x9E3779B185EBCA87ULL
#define XXH3_PRIME64_2        0xC2B2AE3D27D4EB4FULL
#define XXH3_PRIME64_3        0x165667B19E3779F9ULL
#define XXH3_PRIME64_4        0x85EBCA77C2B2AE63ULL
#define XXH3_PRIME64_5        0x27D4EB2F165B7D1ULL

static inline uint64_t xxh3_mul128_fold64(uint64_t a, uint64_t b)
{
    __uint128_t result = (__uint128_t)a * b;
    return (uint64_t)result ^ (uint64_t)(result >> 64);
}

uint64_t vos3_xxh3_update(uint64_t state, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    const uint8_t *end = p + len;

    while (p + 16 <= end) {
        uint64_t lo, hi;
        __builtin_memcpy(&lo, p, 8);
        __builtin_memcpy(&hi, p + 8, 8);
        state += xxh3_mul128_fold64(lo ^ (XXH3_PRIME64_2 + state),
                                     hi ^ XXH3_PRIME64_3);
        p += 16;
    }
    if (p + 8 <= end) {
        uint64_t k;
        __builtin_memcpy(&k, p, 8);
        state ^= xxh3_mul128_fold64(k ^ XXH3_PRIME64_1,
                                     state ^ XXH3_PRIME64_4);
        p += 8;
    }
    while (p < end) {
        state ^= (*p++) * XXH3_PRIME64_5;
        state = ((state << 11) | (state >> 53)) * XXH3_PRIME64_1;
    }
    return state;
}

uint64_t vos3_xxh3_finalize(uint64_t h)
{
    h ^= h >> 37;
    h *= XXH3_PRIME64_3;
    h ^= h >> 32;
    return h;
}

/* ============================================================================
 * CRC32C (Castagnoli) -- Hardware SSE4.2 + Software Fallback
 * ============================================================================ */

static uint32_t g_crc32c_table[256];
static int g_crc32c_table_ready = 0;

void crc32c_init_table(void)
{
    if (g_crc32c_table_ready) return;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (int j = 0; j < 8; j++) {
            if (crc & 1)
                crc = (crc >> 1) ^ 0x82F63B78U;
            else
                crc = crc >> 1;
        }
        g_crc32c_table[i] = crc;
    }
    g_crc32c_table_ready = 1;
}

uint32_t vos3_crc32c_sw(uint32_t crc, const void *data, size_t len)
{
    if (!g_crc32c_table_ready) {
        crc32c_init_table();
    }
    const uint8_t *p = (const uint8_t *)data;
    crc = ~crc;
    for (size_t i = 0; i < len; i++) {
        crc = g_crc32c_table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    }
    return ~crc;
}

uint32_t vos3_crc32c_hw(uint32_t crc, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint64_t crc64 = (uint64_t)(~crc);

    while (len >= 8) {
        uint64_t val;
        __builtin_memcpy(&val, p, 8);
        __asm__ volatile("crc32q %1, %0" : "+r"(crc64) : "r"(val));
        p += 8;
        len -= 8;
    }
    while (len > 0) {
        __asm__ volatile("crc32b %1, %0" : "+r"(crc64) : "m"(*p));
        p++;
        len--;
    }
    return (uint32_t)(~crc64);
}

uint32_t vos3_crc32c(uint32_t crc, const void *data, size_t len)
{
    if (g_cpu_has_sse42) {
        return vos3_crc32c_hw(crc, data, len);
    }
    return vos3_crc32c_sw(crc, data, len);
}

/* ============================================================================
 * PTE Inversion Boot Key
 * ============================================================================ */

uint64_t g_pte_invert_key = 0;

void ai_init_invert_key(void)
{
    if (g_pte_invert_key != 0) {
        return;
    }
    uint64_t tsc = vos3_rdtsc();

    uint64_t hp = vos3_pmm_alloc_huge();
    uint64_t entropy = tsc ^ hp;
    if (hp != 0) {
        vos3_pmm_free_huge(hp);
    }

    g_pte_invert_key = xxh3_mul128_fold64(entropy, XXH3_PRIME64_1);
    g_pte_invert_key &= vos3_vmm_large_addr_mask();

    if (g_pte_invert_key == 0) {
        g_pte_invert_key = 0x000DEADBEEF00000ULL;
    }

    VOS3_INFO("[AI-GUARD] PTE inversion key: 0x%llx",
              (unsigned long long)g_pte_invert_key);
}

/* ============================================================================
 * PTE Inversion (Suspend / Resume)
 * ============================================================================ */

int vos3_ai_pte_invert(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -1;
    if (slot_id == 0) return -1;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    uint64_t xor_mask = g_pte_invert_key & vos3_vmm_large_addr_mask();
    VOS3_ASSERT((xor_mask & 0x1FF000ULL) == 0,
                "PTE invert: XOR mask touches reserved bits [12:20]");

    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;

        vos3_pte_t old_pte;
        if (vos3_vmm_get_pte(vaddr, &old_pte) != 0) continue;

        if (g_model_slots[0].base != 0 && g_model_slots[0].hp_count > 0) {
            uintptr_t s0_end = g_model_slots[0].base +
                               (uintptr_t)g_model_slots[0].hp_count * VOS3_PAGE_SIZE_2M;
            VOS3_ASSERT(vaddr < g_model_slots[0].base || vaddr >= s0_end,
                        "PTE invert: vaddr 0x%lx inside Slot 0 range!");
        }

        for (;;) {
            /* SMP guard: if another CPU already inverted this PTE, skip */
            if (old_pte & VOS3_PTE_IS_INVERTED) break;

            vos3_pte_t new_pte = old_pte;
            new_pte ^= xor_mask;
            VOS3_ASSERT((new_pte & 0x1FF000ULL) == 0,
                        "PTE invert: reserved bits [12:20] non-zero after XOR");
            new_pte &= ~VOS3_PTE_PRESENT;
            new_pte |= VOS3_PTE_IS_INVERTED;

            int rc = vos3_vmm_cas_pte(vaddr, &old_pte, new_pte);
            if (rc == 0) break;
            if (rc != -1) break;
            /* CAS failed (-1): old_pte updated by CAS, re-check at loop top */
        }
        vos3_vmm_invlpg(vaddr);
    }
    __asm__ volatile("lfence" ::: "memory");
    {
        uint32_t a = 0, b, c, d;
        vos3_cpuid(0, 0, &a, &b, &c, &d);
    }
    if (g_cpu_has_ibpb) {
        vos3_write_msr(0x49, 1);
    } else {
        for (int i = 0; i < 32; i++) {
            __asm__ volatile("pause");
        }
    }
    return 0;
}

int vos3_ai_pte_uninvert(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -1;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    uint64_t xor_mask = g_pte_invert_key & vos3_vmm_large_addr_mask();

    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;

        vos3_pte_t old_pte;
        if (vos3_vmm_get_pte(vaddr, &old_pte) != 0) continue;

        for (;;) {
            /* SMP guard: if another CPU already uninverted this PTE, skip */
            if (!(old_pte & VOS3_PTE_IS_INVERTED)) break;

            vos3_pte_t new_pte = old_pte;
            new_pte ^= xor_mask;
            new_pte |= VOS3_PTE_PRESENT;
            new_pte &= ~VOS3_PTE_IS_INVERTED;
            /* v23.16 (D3): Restore COGNITIVE bit alongside AI_PROTECTED.
             * Prevents suspend/resume from stripping cognitive identity. */
            new_pte |= VOS3_PTE_AI_PROTECTED | VOS3_PTE_NO_EXECUTE | VOS3_PTE_COGNITIVE;
            new_pte &= ~(VOS3_PTE_WRITABLE | VOS3_PTE_GLOBAL);

            int rc = vos3_vmm_cas_pte(vaddr, &old_pte, new_pte);
            if (rc == 0) break;
            if (rc != -1) break;
            /* CAS failed (-1): old_pte updated by CAS, re-check at loop top */
        }
        vos3_vmm_invlpg(vaddr);
    }
    __asm__ volatile("lfence" ::: "memory");
    return 0;
}

/* ============================================================================
 * PTE-Gated DMA
 * ============================================================================ */

int vos3_ai_pte_protect_region(uint8_t slot_id, uintptr_t base, size_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (base == 0 || len == 0) return -22;

    size_t pages = (len + VOS3_PAGE_SIZE - 1) / VOS3_PAGE_SIZE;
    for (size_t i = 0; i < pages; i++) {
        uintptr_t vaddr = base + i * VOS3_PAGE_SIZE;
        /* CAS loop: race-safe against hardware A/D bit updates */
        for (;;) {
            vos3_pte_t old_pte;
            if (vos3_vmm_get_pte(vaddr, &old_pte) != 0 || !(old_pte & VOS3_PTE_PRESENT))
                break;
            vos3_pte_t new_pte = (old_pte & ~VOS3_PTE_PRESENT) | VOS3_PTE_AI_GUARD_PAGE;
            if (vos3_vmm_cas_pte(vaddr, &old_pte, new_pte) == 0)
                break;  /* CAS succeeded */
            /* CAS failed (-1): old_pte updated, retry */
        }
        vos3_vmm_invlpg(vaddr);
    }
    __asm__ volatile("sfence" ::: "memory");
    return 0;
}

int vos3_ai_pte_unprotect_region(uint8_t slot_id, uintptr_t base, size_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (base == 0 || len == 0) return -22;

    size_t pages = (len + VOS3_PAGE_SIZE - 1) / VOS3_PAGE_SIZE;
    for (size_t i = 0; i < pages; i++) {
        uintptr_t vaddr = base + i * VOS3_PAGE_SIZE;
        /* CAS loop: race-safe against hardware A/D bit updates */
        for (;;) {
            vos3_pte_t old_pte;
            if (vos3_vmm_get_pte(vaddr, &old_pte) != 0)
                break;
            vos3_pte_t new_pte = old_pte | VOS3_PTE_PRESENT;
            new_pte &= ~VOS3_PTE_AI_GUARD_PAGE;
            if (vos3_vmm_cas_pte(vaddr, &old_pte, new_pte) == 0)
                break;  /* CAS succeeded */
            /* CAS failed (-1): old_pte updated, retry */
        }
        vos3_vmm_invlpg(vaddr);
    }
    __asm__ volatile("lfence" ::: "memory");
    return 0;
}
