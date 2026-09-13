/**
 * @file cet.c
 * @brief VOS3 Control-flow Enforcement Technology (CET) Implementation
 *
 * @details CPUID-gated IBT (Indirect Branch Tracking) and Shadow Stack
 *          infrastructure. Compiled with -fcf-protection=branch so every
 *          function begins with ENDBR64. At runtime, if hardware supports
 *          CET, we flip CR4.CET + MSR bits to enforce ENDBR at all
 *          indirect branch targets — ROP gadgets that jump into function
 *          midpoints trigger #CP (vector 21).
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/cet.h"
#include "../../include/vos/console.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * INTERNAL STATE
 * ============================================================================ */

static vos3_cet_caps_t g_cet_caps = {0};

/* ============================================================================
 * MSR HELPERS
 * ============================================================================ */

static inline uint64_t rdmsr(uint32_t msr)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr));
    return ((uint64_t)hi << 32) | lo;
}

static inline void wrmsr(uint32_t msr, uint64_t val)
{
    uint32_t lo = (uint32_t)val;
    uint32_t hi = (uint32_t)(val >> 32);
    __asm__ volatile ("wrmsr" :: "c"(msr), "a"(lo), "d"(hi));
}

static inline uint64_t read_cr4(void)
{
    uint64_t cr4;
    __asm__ volatile ("mov %%cr4, %0" : "=r"(cr4));
    return cr4;
}

static inline void write_cr4(uint64_t cr4)
{
    __asm__ volatile ("mov %0, %%cr4" :: "r"(cr4) : "memory");
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

void vos3_cet_init(void)
{
    uint32_t eax, ebx, ecx, edx;

    /* Check CPUID leaf 7, sub 0 for CET features */
    vos3_cpuid(7, 0, &eax, &ebx, &ecx, &edx);

    g_cet_caps.ibt_supported   = (edx & VOS3_CET_CPUID_IBT)   ? 1 : 0;
    g_cet_caps.shstk_supported = (ecx & VOS3_CET_CPUID_SHSTK) ? 1 : 0;

    VOS3_INFO("[CET] CPUID leaf 7: IBT=%d SHSTK=%d",
              g_cet_caps.ibt_supported, g_cet_caps.shstk_supported);

    if (!g_cet_caps.ibt_supported) {
        VOS3_INFO("[CET] IBT not available — ENDBR64 present but unenforced");
        return;
    }

    /* Enable CET in CR4 */
    uint64_t cr4 = read_cr4();
    cr4 |= VOS3_CR4_CET;
    write_cr4(cr4);

    /* Configure MSR_IA32_S_CET: enable ENDBR enforcement */
    uint64_t s_cet = rdmsr((uint32_t)VOS3_MSR_IA32_S_CET);
    s_cet |= VOS3_S_CET_ENDBR_EN;

    /* Enable shadow stacks if supported */
    if (g_cet_caps.shstk_supported) {
        s_cet |= VOS3_S_CET_SHSTK_EN;
        g_cet_caps.shstk_enabled = 1;
        VOS3_INFO("[CET] Shadow Stack enabled (kernel-only)");
    }

    wrmsr((uint32_t)VOS3_MSR_IA32_S_CET, s_cet);
    g_cet_caps.ibt_enabled = 1;

    VOS3_INFO("[CET] IBT ACTIVE — ENDBR64 enforced on all indirect branches");
    VOS3_INFO("[CET] CR4=0x%llx S_CET=0x%llx",
              (unsigned long long)(cr4 | VOS3_CR4_CET),
              (unsigned long long)s_cet);
}

const vos3_cet_caps_t* vos3_cet_get_caps(void)
{
    return &g_cet_caps;
}

uint64_t vos3_cet_shstk_alloc(size_t size)
{
    if (!g_cet_caps.shstk_supported) {
        return 0;
    }

    if (size == 0) {
        size = 4 * VOS3_PAGE_SIZE;  /* Default: 16KB shadow stack */
    }

    size_t pages = (size + VOS3_PAGE_SIZE - 1) / VOS3_PAGE_SIZE;
    uintptr_t phys = vos3_pmm_alloc_pages(pages, 0);
    if (phys == 0) {
        VOS3_WARN("[CET] Shadow stack alloc failed (%zu pages)", pages);
        return 0;
    }

    /* Map shadow stack pages into kernel virtual space */
    for (size_t i = 0; i < pages; i++) {
        uintptr_t va = phys + (i * VOS3_PAGE_SIZE);
        vos3_vmm_map(va, phys + (i * VOS3_PAGE_SIZE),
                     (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE));
    }

    return (uint64_t)phys;
}

void vos3_cet_shstk_free(uint64_t base, size_t size)
{
    if (base == 0 || size == 0) {
        return;
    }

    size_t pages = (size + VOS3_PAGE_SIZE - 1) / VOS3_PAGE_SIZE;

    for (size_t i = 0; i < pages; i++) {
        uintptr_t va = (uintptr_t)base + (i * VOS3_PAGE_SIZE);
        vos3_vmm_unmap(va);
    }

    vos3_pmm_free_pages((uintptr_t)base, pages);
}
